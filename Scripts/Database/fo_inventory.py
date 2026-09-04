#!/usr/bin/env python3
r"""
fo_inventory.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Shared inventory persistence primitives used by the database-backed scanner.

The retired CSV inventory-ingestion pipeline has been removed. The supported
path is:

    fo_scan records -> fo_inventory_records.RecordIngestor -> SQLite

This module now owns only the common pieces that the live record ingestor and
other persistence modules share: time/path helpers, source-root resolution,
location identity insertion, scan lifecycle/finalisation, warnings, and small
read helpers.

`file_state` is the current projection and `file_observation` is change history.
Scan finalisation refreshes `file_path.last_seen_*` from current state, not from
new observation rows, because unchanged files intentionally do not receive a
new historical observation on every run.
"""

import os
from datetime import datetime, timezone

REQUIRED_SCHEMA_VERSION = 3

#: Rows per transaction. Large enough that commit overhead is
#: negligible against 50,000 files, small enough that peak memory and
#: the size of a rolled-back batch both stay bounded.
BATCH_ROWS = 1000

#: Chunk size for "WHERE relative_path_key IN (...)" lookups. Kept
#: below the historical SQLITE_MAX_VARIABLE_NUMBER of 999 so the module
#: works on older SQLite builds as well as the 3.50 on the acceptance
#: machine.
IN_CHUNK = 900

class InventoryError(Exception):
    """Ingestion could not proceed. Never raised for a single bad row."""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def path_key(path):
    r"""Invariant-lowercase comparison key, matching fo_db.path_key.

    lower(), not casefold(): casefold maps 'ss' onto 'ß', which would
    merge strasse.txt and straße.txt into one location. Those are
    different files on NTFS.
    """
    return path.rstrip("\\/").lower()


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Source-root resolution
# ---------------------------------------------------------------------------

class RootResolver(object):
    r"""Maps an absolute observed path onto the source root it belongs to.

    Longest prefix wins, so nested roots behave correctly: with both
    C:\Data and C:\Data\Photos registered, a file under the latter is
    attributed to the latter rather than to whichever happens to be
    checked first.

    A path that matches no root is not discarded -- it is attributed to
    the scan's primary root and flagged, because a file the scanner
    genuinely saw is evidence, and dropping it would make the database
    quietly disagree with the scan evidence.
    """

    def __init__(self, roots, primary_root_id):
        # roots: iterable of (source_root_id, root_path)
        self.entries = sorted(
            ((rid, path_key(path), path) for rid, path in roots),
            key=lambda item: len(item[1]), reverse=True)
        self.primary_root_id = primary_root_id
        self.unmatched = 0

    def resolve(self, absolute_path):
        """Return (source_root_id, relative_path, matched)."""
        key = absolute_path.lower()
        for root_id, root_key, root_display in self.entries:
            if key == root_key:
                return root_id, os.path.basename(absolute_path.rstrip("\\/")), True
            if key.startswith(root_key + "\\") or key.startswith(root_key + "/"):
                return root_id, absolute_path[len(root_display):].lstrip("\\/"), True
        self.unmatched += 1
        return self.primary_root_id, absolute_path, False


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class InventoryIngestor(object):
    """Shared scan/location persistence base for RecordIngestor."""

    def __init__(self, conn, run_id, run_stage_id=None, logger=None,
                 batch_rows=BATCH_ROWS):
        self.conn = conn
        self.run_id = run_id
        self.run_stage_id = run_stage_id
        self.logger = logger
        self.batch_rows = batch_rows
        self.scan_ids = {}          # source_root_id -> inventory_scan_id
        self.new_paths = {}         # source_root_id -> count
        self.observed = {}
        self.inaccessible = {}
        self.warnings = []

    # -- logging ------------------------------------------------------

    def _log(self, severity, message):
        if self.logger is not None:
            try:
                self.logger(severity, message)
            except Exception:
                pass

    def _warn(self, message):
        self.warnings.append(message)
        self._log("WARNING", message)

    # -- schema -------------------------------------------------------

    def ensure_schema(self):
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version < REQUIRED_SCHEMA_VERSION:
            raise InventoryError(
                "Inventory ingestion needs schema version %d; this database is at %d."
                % (REQUIRED_SCHEMA_VERSION, version))
        return version

    # -- roots --------------------------------------------------------

    def _active_roots(self):
        return [(row["source_root_id"], row["root_path"]) for row in self.conn.execute(
            "SELECT source_root_id, root_path FROM source_root "
            "WHERE project_id = 1 AND is_active = 1 ORDER BY source_root_id")]

    def _ensure_root(self, target_path):
        r"""Find the source_root row for the path the scan actually walked.

        A project created through the GUI always has one, because
        New-Project.ps1 registers TargetPath at creation. A project
        created before R1, or one whose TargetPath was edited by hand,
        may not. Registering it is better than refusing to ingest: the
        scan really did walk that root, and the alternative is throwing
        away the whole inventory over a bookkeeping gap. It is logged
        rather than done silently.
        """
        key = path_key(target_path)
        for root_id, root_path in self._active_roots():
            if path_key(root_path) == key:
                return root_id
        cur = self.conn.execute(
            "INSERT INTO source_root (project_id, root_path, root_path_key, label, added_utc) "
            "VALUES (1, ?, ?, ?, ?)",
            (target_path.rstrip("\\/"), key, "Registered during inventory ingestion",
             utc_now()))
        self._warn("Source root %s was not registered for this project and has been "
                   "added during ingestion." % target_path)
        return cur.lastrowid

    # -- scans --------------------------------------------------------

    def _scan_for_root(self, source_root_id, started_utc):
        """One inventory_scan row per (run, root), created on first use."""
        if source_root_id in self.scan_ids:
            return self.scan_ids[source_root_id]
        existing = self.conn.execute(
            "SELECT inventory_scan_id FROM inventory_scan WHERE run_id = ? AND source_root_id = ?",
            (self.run_id, source_root_id)).fetchone()
        if existing:
            scan_id = existing["inventory_scan_id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO inventory_scan (project_id, run_id, run_stage_id, "
                "source_root_id, status, started_utc) VALUES (1, ?, ?, ?, 'running', ?)",
                (self.run_id, self.run_stage_id, source_root_id, started_utc))
            scan_id = cur.lastrowid
        self.scan_ids[source_root_id] = scan_id
        self.new_paths.setdefault(source_root_id, 0)
        self.observed.setdefault(source_root_id, 0)
        self.inaccessible.setdefault(source_root_id, 0)
        return scan_id

    # -- path identity -------------------------------------------------

    def _resolve_path_ids(self, entries, scan_id, now):
        """entries: list of dicts with source_root_id / relative_path / ...

        Returns {(source_root_id, relative_path_key): file_path_id},
        inserting locations not seen before. Bounded per batch: the
        lookup dictionary never grows with the size of the inventory.
        """
        by_root = {}
        for entry in entries:
            by_root.setdefault(entry["source_root_id"], set()).add(entry["key"])

        found = {}
        for root_id, keys in by_root.items():
            keys = list(keys)
            for start in range(0, len(keys), IN_CHUNK):
                chunk = keys[start:start + IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                for row in self.conn.execute(
                        "SELECT file_path_id, relative_path_key FROM file_path "
                        "WHERE source_root_id = ? AND relative_path_key IN (%s)" % placeholders,
                        [root_id] + chunk):
                    found[(root_id, row["relative_path_key"])] = row["file_path_id"]

        missing = []
        seen = set()
        for entry in entries:
            identity = (entry["source_root_id"], entry["key"])
            if identity in found or identity in seen:
                continue
            seen.add(identity)
            missing.append((
                entry["source_root_id"], entry["relative_path"], entry["key"],
                entry["file_name"], entry["extension_key"], entry["depth"],
                now, scan_id, now, scan_id))

        if missing:
            self.conn.executemany(
                "INSERT OR IGNORE INTO file_path (project_id, source_root_id, "
                "relative_path, relative_path_key, file_name, extension_key, depth, "
                "first_seen_utc, first_seen_scan_id, last_seen_utc, last_seen_scan_id) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", missing)
            for root_id, keys in by_root.items():
                keys = [k for k in keys if (root_id, k) not in found]
                for start in range(0, len(keys), IN_CHUNK):
                    chunk = keys[start:start + IN_CHUNK]
                    placeholders = ",".join("?" * len(chunk))
                    for row in self.conn.execute(
                            "SELECT file_path_id, relative_path_key FROM file_path "
                            "WHERE source_root_id = ? AND relative_path_key IN (%s)" % placeholders,
                            [root_id] + chunk):
                        found[(root_id, row["relative_path_key"])] = row["file_path_id"]
                        self.new_paths[root_id] = self.new_paths.get(root_id, 0) + 1
        return found

    # -- completion ---------------------------------------------------

    def _stamp_last_seen(self, scan_id, when):
        """Point every location VERIFIED by this scan at this scan.

        B6.1 uses the current projection rather than new history rows. Under
        change-only history an unchanged file receives no new observation, but
        `file_state.current_scan_id` is still refreshed. Updating from
        file_observation would therefore make an unchanged, successfully
        verified location look historically stale after every later scan.
        """
        self.conn.execute(
            "UPDATE file_path SET last_seen_utc = ?, last_seen_scan_id = ? "
            "WHERE file_path_id IN ("
            "  SELECT file_path_id FROM file_state WHERE current_scan_id = ? "
            "  AND state IN ('present','inaccessible'))",
            (when, scan_id, scan_id))

    def finish(self, status=None):
        """Close every scan row this ingestion opened."""
        finished = utc_now()
        resolved = status or ("completed_with_warnings" if self.warnings else "completed")
        for root_id, scan_id in self.scan_ids.items():
            self._stamp_last_seen(scan_id, finished)
            row = self.conn.execute(
                "SELECT started_utc FROM inventory_scan WHERE inventory_scan_id = ?",
                (scan_id,)).fetchone()
            duration = None
            try:
                start = datetime.fromisoformat(row["started_utc"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                duration = int((end - start).total_seconds() * 1000)
            except Exception:
                pass
            self.conn.execute(
                "UPDATE inventory_scan SET status = ?, completed_utc = ?, duration_ms = ?, "
                "observed_count = ?, inaccessible_count = ?, new_path_count = ?, notes = ? "
                "WHERE inventory_scan_id = ?",
                (resolved, finished, duration, self.observed.get(root_id, 0),
                 self.inaccessible.get(root_id, 0), self.new_paths.get(root_id, 0),
                 "; ".join(self.warnings)[:2000] or None, scan_id))
        self.conn.commit()
        return resolved

    def fail(self, reason):
        """Mark every open scan failed. Rows already committed are kept.

        A partial inventory is still evidence of what was seen, and
        deleting it would destroy the only record of how far the scan
        got. The scan's status is what stops a later comparison from
        reading the gap as deletions.
        """
        for scan_id in self.scan_ids.values():
            self.conn.execute(
                "UPDATE inventory_scan SET status = 'failed', completed_utc = ?, "
                "notes = ? WHERE inventory_scan_id = ?",
                (utc_now(), str(reason)[:2000], scan_id))
        self.conn.commit()


# ---------------------------------------------------------------------------
# Read helpers -- the re-scan questions the schema exists to answer
# ---------------------------------------------------------------------------

def latest_completed_scan(conn, source_root_id):
    return conn.execute(
        "SELECT * FROM inventory_scan WHERE source_root_id = ? "
        "AND status IN ('completed', 'completed_with_warnings') "
        "ORDER BY started_utc DESC, inventory_scan_id DESC LIMIT 1",
        (source_root_id,)).fetchone()


def scan_summary(conn, inventory_scan_id):
    return conn.execute("SELECT * FROM inventory_scan WHERE inventory_scan_id = ?",
                        (inventory_scan_id,)).fetchone()


def missing_since(conn, source_root_id, inventory_scan_id):
    """Locations known to this root that the given scan did not observe.

    Only meaningful for a scan that COMPLETED -- a failed or interrupted
    scan's gaps are its own, not the filesystem's. The caller is
    expected to check inventory_scan.status first; latest_completed_scan
    exists so that is easy to do.
    """
    return conn.execute(
        "SELECT fp.* FROM file_path fp WHERE fp.source_root_id = ? AND NOT EXISTS ("
        "  SELECT 1 FROM file_observation o WHERE o.file_path_id = fp.file_path_id "
        "  AND o.inventory_scan_id = ? AND o.status = 'observed')",
        (source_root_id, inventory_scan_id)).fetchall()


def new_in_scan(conn, inventory_scan_id):
    return conn.execute(
        "SELECT fp.* FROM file_path fp WHERE fp.first_seen_scan_id = ?",
        (inventory_scan_id,)).fetchall()


def changed_since(conn, file_path_id, limit=2):
    """The most recent observations of one location, newest first.

    Two rows is enough to answer "did its size or timestamp change?".
    The comparison itself is deliberately left to the caller: R3 records
    history, it does not define what counts as a meaningful change.
    """
    return conn.execute(
        "SELECT o.*, s.started_utc AS scan_started FROM file_observation o "
        "JOIN inventory_scan s ON s.inventory_scan_id = o.inventory_scan_id "
        "WHERE o.file_path_id = ? ORDER BY s.started_utc DESC, o.file_observation_id DESC "
        "LIMIT ?", (file_path_id, limit)).fetchall()


def inventory_counts(conn):
    return {
        "scans": conn.execute("SELECT COUNT(*) FROM inventory_scan").fetchone()[0],
        "paths": conn.execute("SELECT COUNT(*) FROM file_path").fetchone()[0],
        "observations": conn.execute("SELECT COUNT(*) FROM file_observation").fetchone()[0],
        "observed": conn.execute(
            "SELECT COUNT(*) FROM file_observation WHERE status = 'observed'").fetchone()[0],
        "inaccessible": conn.execute(
            "SELECT COUNT(*) FROM file_observation WHERE status = 'inaccessible'").fetchone()[0],
    }
