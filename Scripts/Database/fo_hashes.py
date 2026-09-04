#!/usr/bin/env python3
r"""
fo_hashes.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Shared hash/duplicate persistence primitives used by the in-process engine.

The retired CSV hash-ingestion path has been removed. The supported flow is:

    current file_state -> fo_hash_engine -> fo_hash_records -> SQLite

This module owns the persistence base and shared hash semantics: complete-vs-
partial identity classification, source-root/observation linkage, content
upsert, idempotent measurement UPSERT, duplicate-run lifecycle and read helpers.

A content row is created only from SHA-256 coverage of the entire file. A
partial hash on a larger file remains a screening value. Selective and
exhaustive runs keep distinct status vocabularies so `size_unique` can never be
mistaken for a measured full identity.
"""

from datetime import datetime, timezone

import fo_inventory

import fo_state  # noqa: E402

REQUIRED_SCHEMA_VERSION = 4

BATCH_ROWS = 1000
IN_CHUNK = 900

#: PartialHash.ps1's default read window. Recorded per measurement so a
#: later change to the script cannot silently reinterpret old rows.
DEFAULT_PARTIAL_HASH_BYTES = 65536

#: Alpha FinalStatus -> our hash_status. Kept as an explicit table so an
#: unrecognised Alpha status is visible rather than quietly bucketed.
_SELECTIVE_STATUS = {
    "UniqueBySize": "size_unique",
    "RuledOutByPartialHash": "ruled_out_partial",
    "RuledOutByFullHash": "ruled_out_full",
    "ConfirmedDuplicate": "confirmed_duplicate",
    "SkippedCloudOnly": "skipped_cloud_only",
    "Error": "error",
}
_EXHAUSTIVE_STATUS = {
    "UniqueByHash": "unique_by_hash",
    "ConfirmedDuplicate": "confirmed_duplicate",
    "SkippedCloudOnly": "skipped_cloud_only",
    "Error": "error",
}


class HashIngestError(Exception):
    """Ingestion could not proceed. Never raised for one bad row."""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def classify_hash(full_hash, partial_hash, size_bytes, window):
    """Decide what whole-file SHA-256, if any, this row establishes.

    Returns (sha256_or_None, identity_source_or_None, partial_covers_file).

    This is the single place the partial-vs-complete judgement is made.
    Getting it wrong in either direction matters: treating a genuinely
    partial hash as identity would declare different files identical,
    while refusing a window-spanning partial hash would leave most of
    the confirmed duplicates on the controlled suite unexplained.
    """
    covers = None
    if partial_hash:
        covers = 1 if (size_bytes is not None and window is not None
                       and size_bytes <= window) else 0
    if full_hash:
        return full_hash.strip().upper(), "full_hash", covers
    if partial_hash and covers == 1:
        return partial_hash.strip().upper(), "partial_hash_complete", covers
    return None, None, covers


class HashIngestor(object):
    """Shared hash/duplicate persistence base for the engine adapter."""

    def __init__(self, conn, run_id, run_stage_id=None, logger=None,
                 batch_rows=BATCH_ROWS, partial_hash_bytes=DEFAULT_PARTIAL_HASH_BYTES):
        self.conn = conn
        self.run_id = run_id
        self.run_stage_id = run_stage_id
        self.logger = logger
        self.batch_rows = batch_rows
        self.partial_hash_bytes = partial_hash_bytes

        self.duplicate_run_id = None
        self.mode = None
        self.warnings = []
        self.artifacts = []
        self.counts = {
            "measurements": 0, "content_rows": 0, "groups": 0, "members": 0,
            "unmatched": 0, "db_id_mismatch": 0, "errors": 0,
        }
        self._observation_cache = {}
        self._content_cache = {}
        self._resolver = None
        self._scan_id = None
        self._scan_ids = []

    # -- plumbing -----------------------------------------------------

    def _log(self, severity, message):
        if self.logger is not None:
            try:
                self.logger(severity, message)
            except Exception:
                pass

    def _warn(self, message):
        self.warnings.append(message)
        self._log("WARNING", message)

    def ensure_schema(self):
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version < REQUIRED_SCHEMA_VERSION:
            raise HashIngestError(
                "Hash persistence needs schema version %d; this database is at %d."
                % (REQUIRED_SCHEMA_VERSION, version))
        return version

    # -- R3 linkage ---------------------------------------------------

    def _bind_inventory_scan(self, target_path):
        r"""Find the inventory scan whose observations these hashes describe.

        The duplicate artifacts are produced from the run folder's
        PreliminaryInventory.csv, so the correct scan is the most recent
        completed one for this project -- normally the same run, but a
        Duplicate Run started later reuses the run folder and its
        inventory, which is why this looks up by scan rather than
        assuming run_id.
        """
        row = self.conn.execute(
            "SELECT inventory_scan_id, run_id, source_root_id FROM inventory_scan "
            "WHERE status IN ('completed', 'completed_with_warnings') "
            "ORDER BY started_utc DESC, inventory_scan_id DESC LIMIT 1").fetchone()
        if row is None:
            raise HashIngestError(
                "No completed inventory scan exists in this project, so hash "
                "results cannot be attached to file observations.")

        # A multi-root project has ONE inventory_scan PER SOURCE ROOT for
        # a single inventory run (see R3's fo_inventory._scan_for_root).
        # Binding to just the newest one would make every observation
        # under the project's other roots invisible here -- and a
        # cross-root duplicate would then silently lose half its members,
        # which is the exact case multiple roots exist to support. So
        # bind to every scan of the newest inventory RUN.
        self._scan_ids = [r["inventory_scan_id"] for r in self.conn.execute(
            "SELECT inventory_scan_id FROM inventory_scan "
            "WHERE run_id = ? AND status IN ('completed', 'completed_with_warnings')",
            (row["run_id"],))]
        self._scan_id = row["inventory_scan_id"]

        roots = [(r["source_root_id"], r["root_path"]) for r in self.conn.execute(
            "SELECT source_root_id, root_path FROM source_root "
            "WHERE project_id = 1 AND is_active = 1")]
        primary = row["source_root_id"]
        for root_id, root_path in roots:
            if fo_inventory.path_key(root_path) == fo_inventory.path_key(target_path or ""):
                primary = root_id
                break
        self._resolver = fo_inventory.RootResolver(roots, primary)
        return self._scan_id

    def _lookup_observations(self, absolute_paths):
        """Resolve absolute paths to file_observation ids, in bulk.

        Keyed on (source_root_id, relative_path_key) -- the R3 identity
        -- never on filename and never on a path without its root.
        """
        wanted = {}
        for absolute in absolute_paths:
            if absolute in self._observation_cache:
                continue
            root_id, relative, matched = self._resolver.resolve(absolute)
            key = fo_inventory.path_key(relative)
            wanted.setdefault(root_id, {})[key] = absolute
            if not matched:
                self.counts["unmatched"] += 1

        for root_id, keyed in wanted.items():
            keys = list(keyed)
            for start in range(0, len(keys), IN_CHUNK):
                chunk = keys[start:start + IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                scan_placeholders = ",".join("?" * len(self._scan_ids))
                rows = self.conn.execute(
                    "SELECT fp.relative_path_key, o.file_observation_id, o.legacy_db_id, "
                    "       o.size_bytes "
                    "FROM file_path fp JOIN file_observation o "
                    "  ON o.file_path_id = fp.file_path_id "
                    "WHERE fp.source_root_id = ? AND o.inventory_scan_id IN (%s) "
                    "  AND fp.relative_path_key IN (%s)"
                    % (scan_placeholders, placeholders),
                    [root_id] + self._scan_ids + chunk).fetchall()
                for row in rows:
                    absolute = keyed.get(row["relative_path_key"])
                    if absolute is not None:
                        self._observation_cache[absolute] = {
                            "file_observation_id": row["file_observation_id"],
                            "legacy_db_id": row["legacy_db_id"],
                            "size_bytes": row["size_bytes"],
                        }
        return self._observation_cache

    # -- content ------------------------------------------------------

    def _upsert_content(self, wanted):
        """wanted: {sha256: (size_bytes, identity_source)} -> {sha256: id}."""
        if not wanted:
            return {}
        # Resolved ids persist for the whole ingestion. The three
        # selective artifacts describe the same files, so without this
        # every content hash is looked up again on each pass.
        found = {sha: self._content_cache[sha] for sha in wanted
                 if sha in self._content_cache}
        keys = [sha for sha in wanted if sha not in found]
        if not keys:
            return found
        for start in range(0, len(keys), IN_CHUNK):
            chunk = keys[start:start + IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for row in self.conn.execute(
                    "SELECT content_id, sha256, size_bytes FROM content "
                    "WHERE project_id = 1 AND sha256 IN (%s)" % placeholders, chunk):
                found[row["sha256"]] = row["content_id"]
                expected = wanted[row["sha256"]][0]
                if (expected is not None and row["size_bytes"] is not None
                        and expected != row["size_bytes"]):
                    # Same SHA-256, different size. Either corruption or
                    # something far more interesting. Worth surfacing, not
                    # worth silently overwriting.
                    self._warn(
                        "Content %s was previously recorded at %s bytes but now "
                        "appears at %s bytes." % (row["sha256"][:16],
                                                  row["size_bytes"], expected))

        now = utc_now()
        missing = [(sha, wanted[sha][0], wanted[sha][1], now, self.run_id, now, self.run_id)
                   for sha in keys if sha not in found]
        if missing:
            self.conn.executemany(
                "INSERT OR IGNORE INTO content (project_id, sha256, size_bytes, "
                "identity_source, first_seen_utc, first_seen_run_id, last_seen_utc, "
                "last_seen_run_id) VALUES (1, ?, ?, ?, ?, ?, ?, ?)", missing)
            self.counts["content_rows"] += len(missing)
            remaining = [sha for sha in keys if sha not in found]
            for start in range(0, len(remaining), IN_CHUNK):
                chunk = remaining[start:start + IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                for row in self.conn.execute(
                        "SELECT content_id, sha256 FROM content WHERE project_id = 1 AND sha256 IN (%s)"
                        % placeholders, chunk):
                    found[row["sha256"]] = row["content_id"]

        # last_seen is NOT stamped per batch. Doing so re-wrote the same
        # rows once per batch per artifact and measured as one of the
        # largest costs in the ingestion. It is applied once, in
        # finish(), by a single set-based statement -- see
        # _stamp_content_last_seen.
        self._content_cache.update(found)
        return found

    # -- duplicate_run ------------------------------------------------

    def begin(self, mode):
        if mode not in ("selective", "exhaustive"):
            raise ValueError("mode must be selective or exhaustive")
        self.mode = mode
        existing = self.conn.execute(
            "SELECT duplicate_run_id FROM duplicate_run WHERE run_id = ? AND mode = ?",
            (self.run_id, mode)).fetchone()
        if existing:
            self.duplicate_run_id = existing["duplicate_run_id"]
        else:
            cur = self.conn.execute(
                "INSERT INTO duplicate_run (project_id, run_id, run_stage_id, mode, "
                "status, started_utc, partial_hash_bytes) "
                "VALUES (1, ?, ?, ?, 'running', ?, ?)",
                (self.run_id, self.run_stage_id, mode, utc_now(),
                 self.partial_hash_bytes))
            self.duplicate_run_id = cur.lastrowid
        self.conn.commit()
        return self.duplicate_run_id

    # -- measurements -------------------------------------------------

    def _write_measurements(self, rows):
        r"""Persist/refine hash measurements with one statement per row.

        B6.1 closes B5-D.F001: B4.5/B6 performed INSERT OR IGNORE and then
        UPDATE for every row. ON CONFLICT preserves the same idempotent
        multi-artifact refinement semantics without writing every new row twice.
        """
        if not rows:
            return
        self.conn.executemany(
            "INSERT INTO hash_measurement ("
            " project_id,file_observation_id,content_id,run_id,run_stage_id,"
            " duplicate_run_id,measurement_mode,algorithm,size_bytes,size_group_id,"
            " partial_hash,partial_hash_bytes,partial_group_id,partial_covers_file,"
            " full_hash,hash_status,alpha_final_status,needed_full_hash,"
            " reused_from_previous,measured_utc,source_artifact,error_kind,error_message) "
            "VALUES (1,?,?,?,?,?,?,'SHA256',?,?,?,?,?,?,?,?,?,?,0,?,?,?,?) "
            "ON CONFLICT(run_id,file_observation_id,measurement_mode) DO UPDATE SET "
            " content_id=COALESCE(excluded.content_id,hash_measurement.content_id),"
            " size_bytes=COALESCE(excluded.size_bytes,hash_measurement.size_bytes),"
            " size_group_id=COALESCE(excluded.size_group_id,hash_measurement.size_group_id),"
            " partial_hash=COALESCE(excluded.partial_hash,hash_measurement.partial_hash),"
            " partial_hash_bytes=COALESCE(excluded.partial_hash_bytes,hash_measurement.partial_hash_bytes),"
            " partial_group_id=COALESCE(excluded.partial_group_id,hash_measurement.partial_group_id),"
            " partial_covers_file=COALESCE(excluded.partial_covers_file,hash_measurement.partial_covers_file),"
            " full_hash=COALESCE(excluded.full_hash,hash_measurement.full_hash),"
            " hash_status=excluded.hash_status,"
            " alpha_final_status=COALESCE(excluded.alpha_final_status,hash_measurement.alpha_final_status),"
            " needed_full_hash=COALESCE(excluded.needed_full_hash,hash_measurement.needed_full_hash),"
            " duplicate_run_id=COALESCE(hash_measurement.duplicate_run_id,excluded.duplicate_run_id),"
            " source_artifact=excluded.source_artifact,"
            " error_kind=COALESCE(excluded.error_kind,hash_measurement.error_kind),"
            " error_message=COALESCE(excluded.error_message,hash_measurement.error_message)",
            [(r["file_observation_id"], r["content_id"], self.run_id,
              self.run_stage_id, self.duplicate_run_id, self.mode, r["size_bytes"],
              r["size_group_id"], r["partial_hash"], r["partial_hash_bytes"],
              r["partial_group_id"], r["partial_covers_file"], r["full_hash"],
              r["hash_status"], r["alpha_final_status"], r["needed_full_hash"],
              r["measured_utc"], r["source_artifact"], r["error_kind"],
              r["error_message"]) for r in rows])


    # -- per-artifact handlers ----------------------------------------


    # -- duplicate groups ---------------------------------------------


    # -- public entry points ------------------------------------------

    def _refresh_measurement_count(self):
        """Distinct rows, not the running total across artifacts: the
        three selective CSVs each describe the same observations, so
        adding their row counts would triple-count."""
        self.counts["measurements"] = self.conn.execute(
            "SELECT COUNT(*) FROM hash_measurement WHERE run_id = ? AND measurement_mode = ?",
            (self.run_id, self.mode)).fetchone()[0]
        return self.counts["measurements"]


    def _stamp_content_last_seen(self, when):
        """Point every content this run touched at this run.

        Derived from the measurements just written, in one statement, so
        it states exactly what they state.
        """
        self.conn.execute(
            "UPDATE content SET last_seen_utc = ?, last_seen_run_id = ? "
            "WHERE content_id IN ("
            "  SELECT DISTINCT content_id FROM hash_measurement "
            "  WHERE run_id = ? AND content_id IS NOT NULL)",
            (when, self.run_id, self.run_id))

    def finish(self, status=None):
        """Close the duplicate_run row, recording Alpha's own totals."""
        if self.duplicate_run_id is None:
            return None
        finished = utc_now()
        self._stamp_content_last_seen(finished)

        # B6: promote this run's content identity onto file_state,
        # inside the same close-out as the rest of the run's totals.
        #
        # This is the join that makes the current-state duplicate query
        # possible at all. attach_content() only promotes a measurement
        # whose file_observation_id is the CURRENT observation, so a
        # hash taken against a since-superseded observation is left
        # behind rather than presented as a fact about the disk today.
        # See fo_state.attach_content for the full argument.
        try:
            fo_state.attach_content(self.conn, self.run_id)
        except Exception as exc:                              # noqa: BLE001
            # A projection failure must not lose the measurements, which
            # are the expensive part and are already committed. It is
            # recorded as a warning so the run reports
            # completed_with_warnings rather than silently holding a
            # state table that does not reflect the hashes beside it.
            self._warn("Could not update current state from this run's "
                       "hashes: %s" % exc)
        row = self.conn.execute(
            "SELECT started_utc FROM duplicate_run WHERE duplicate_run_id = ?",
            (self.duplicate_run_id,)).fetchone()
        duration = None
        try:
            start = datetime.fromisoformat(row["started_utc"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration = int((end - start).total_seconds() * 1000)
        except Exception:
            pass

        totals = self.conn.execute(
            "SELECT"
            "  COUNT(size_group_id) AS candidates,"
            "  COUNT(DISTINCT size_group_id) AS size_groups,"
            "  SUM(CASE WHEN needed_full_hash = 1 THEN 1 ELSE 0 END) AS needs_full,"
            "  SUM(CASE WHEN hash_status = 'ruled_out_full' THEN 1 ELSE 0 END) AS ruled_full,"
            "  SUM(CASE WHEN hash_status = 'error' THEN 1 ELSE 0 END) AS errors"
            " FROM hash_measurement WHERE run_id = ? AND measurement_mode = ?",
            (self.run_id, self.mode)).fetchone()
        groups = self.conn.execute(
            "SELECT COUNT(*) AS groups, COALESCE(SUM(member_count), 0) AS members,"
            "       COALESCE(SUM(redundant_count), 0) AS redundant"
            " FROM duplicate_group WHERE duplicate_run_id = ?",
            (self.duplicate_run_id,)).fetchone()

        self._refresh_measurement_count()

        resolved = status or ("completed_with_warnings" if self.warnings else "completed")
        self.conn.execute(
            "UPDATE duplicate_run SET status = ?, completed_utc = ?, duration_ms = ?, "
            "candidate_count = ?, size_group_count = ?, needs_full_hash_count = ?, "
            "ruled_out_full_count = ?, confirmed_group_count = ?, confirmed_file_count = ?, "
            "redundant_file_count = ?, error_count = ?, source_artifacts = ?, notes = ? "
            "WHERE duplicate_run_id = ?",
            (resolved, finished, duration, totals["candidates"] or 0,
             totals["size_groups"] or 0, totals["needs_full"] or 0,
             totals["ruled_full"] or 0, groups["groups"] or 0, groups["members"] or 0,
             groups["redundant"] or 0, totals["errors"] or 0,
             ", ".join(sorted(set(self.artifacts))) or None,
             "; ".join(self.warnings)[:2000] or None, self.duplicate_run_id))
        self.conn.commit()
        return resolved

    def fail(self, reason):
        if self.duplicate_run_id is None:
            return
        self.conn.execute(
            "UPDATE duplicate_run SET status = 'failed', completed_utc = ?, notes = ? "
            "WHERE duplicate_run_id = ?",
            (utc_now(), str(reason)[:2000], self.duplicate_run_id))
        self.conn.commit()


# ---------------------------------------------------------------------------
# Read helpers -- the questions this schema exists to answer
# ---------------------------------------------------------------------------

def duplicate_sets_by_content(conn, min_locations=2):
    r"""Duplicate groups derived from ALL RECORDED HISTORY.

    RETAINED, BUT NO LONGER THE CURRENT-STATE ANSWER (B5-E.F002).

    This spans every hash measurement the project has ever recorded, so
    its cost grows with RUN COUNT rather than with file count. Measured
    on a 4,000-file corpus over ten runs: 3.80 ms rising to 26.33 ms,
    on a corpus that never changed. B4.5 used this to answer "what are
    my duplicates?", which is why that question got slower every time
    the product was used.

    `fo_state.current_duplicate_sets()` is the current-state answer and
    is what the product now asks. It reads one row per location and
    stayed flat at ~3.9 ms across the same ten runs.

    This function survives because "which locations have EVER held
    identical content?" is a real historical question, and answering it
    requires exactly this scan. It is now called only when history is
    intentionally requested -- which is what the finding asked for.
    """
    return conn.execute(
        "SELECT c.content_id, c.sha256, c.size_bytes, c.identity_source,"
        "       COUNT(DISTINCT fp.file_path_id) AS location_count "
        "FROM content c "
        "JOIN hash_measurement h ON h.content_id = c.content_id "
        "JOIN file_observation o ON o.file_observation_id = h.file_observation_id "
        "JOIN file_path fp       ON fp.file_path_id = o.file_path_id "
        "GROUP BY c.content_id HAVING location_count >= ? "
        "ORDER BY c.size_bytes DESC", (min_locations,)).fetchall()


def locations_for_content(conn, content_id):
    return conn.execute(
        "SELECT DISTINCT sr.root_path, fp.relative_path, fp.file_path_id "
        "FROM hash_measurement h "
        "JOIN file_observation o ON o.file_observation_id = h.file_observation_id "
        "JOIN file_path fp       ON fp.file_path_id = o.file_path_id "
        "JOIN source_root sr     ON sr.source_root_id = fp.source_root_id "
        "WHERE h.content_id = ? ORDER BY sr.root_path, fp.relative_path",
        (content_id,)).fetchall()


def cross_root_duplicates(conn):
    """Content found under more than one source root -- a core reason a
    project may contain several roots."""
    return conn.execute(
        "SELECT c.content_id, c.sha256, c.size_bytes,"
        "       COUNT(DISTINCT fp.source_root_id) AS root_count "
        "FROM content c "
        "JOIN hash_measurement h ON h.content_id = c.content_id "
        "JOIN file_observation o ON o.file_observation_id = h.file_observation_id "
        "JOIN file_path fp       ON fp.file_path_id = o.file_path_id "
        "GROUP BY c.content_id HAVING root_count > 1").fetchall()


def hash_counts(conn):
    return {
        "content": conn.execute("SELECT COUNT(*) FROM content").fetchone()[0],
        "measurements": conn.execute("SELECT COUNT(*) FROM hash_measurement").fetchone()[0],
        "with_content": conn.execute(
            "SELECT COUNT(*) FROM hash_measurement WHERE content_id IS NOT NULL").fetchone()[0],
        "partial_only": conn.execute(
            "SELECT COUNT(*) FROM hash_measurement WHERE partial_hash IS NOT NULL "
            "AND full_hash IS NULL AND partial_covers_file = 0").fetchone()[0],
        "duplicate_runs": conn.execute("SELECT COUNT(*) FROM duplicate_run").fetchone()[0],
        "groups": conn.execute("SELECT COUNT(*) FROM duplicate_group").fetchone()[0],
        "members": conn.execute("SELECT COUNT(*) FROM duplicate_member").fetchone()[0],
    }
