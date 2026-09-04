#!/usr/bin/env python3
r"""
fo_hash_records.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Adapter between the in-process hash/duplicate engine and SQLite persistence.
The supported path is:

    current file_state -> hash engine -> SQLite -> derived exports

No CSV is parsed to persist hash results. Every engine input already carries
the observation/location identifiers resolved from the database, so persistence
does not re-derive identity from path text.

B6.1 keeps selective and exhaustive measurement semantics explicit. A
`size_unique` result means intentionally not hashed in a selective Duplicate
Run; a full content identity is attached only when complete coverage exists.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_hash_engine                                           # noqa: E402
import fo_hashes                                                # noqa: E402


utc_now = fo_hashes.utc_now

#: Recorded in hash_measurement.source_artifact. The column exists to
#: answer "where did this row come from?", and the honest answer is now
#: the engine, not a file. Writing a CSV name here would be a lie that
#: happened to keep a string column stable.
SOURCE_ENGINE = "fo_hash_engine"

#: Engine FinalStatus -> R4's hash_status vocabulary. Reuses R4's own
#: tables so the two paths cannot drift apart.
_SELECTIVE_STATUS = fo_hashes._SELECTIVE_STATUS
_EXHAUSTIVE_STATUS = fo_hashes._EXHAUSTIVE_STATUS


def load_entries(conn, inventory_scan_ids):
    r"""Current present files verified by the given scans.

    B6.1 reads the current projection. An unchanged file has no new history
    observation in history.mode='changes', but current_scan_id proves the
    current scan reverified it. current_observation_id remains the provenance
    key for the bytes/metadata state being hashed.
    """
    if not inventory_scan_ids:
        return []
    placeholders = ",".join("?" * len(inventory_scan_ids))
    rows = conn.execute(
        "SELECT fs.current_observation_id AS file_observation_id, "
        "       fs.current_legacy_db_id AS legacy_db_id, fs.size_bytes, "
        "       fs.is_offline_or_cloud, fp.relative_path, sr.root_path "
        "FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
        "WHERE fs.current_scan_id IN (%s) AND fs.state='present' "
        "  AND fs.current_observation_id IS NOT NULL "
        "ORDER BY sr.root_ordinal, fp.path_sort_key, fp.file_path_id" % placeholders,
        list(inventory_scan_ids)).fetchall()
    return [fo_hash_engine.FileEntry(
        key=row["file_observation_id"], db_id=row["legacy_db_id"],
        path=_join(row["root_path"], row["relative_path"]),
        size=row["size_bytes"] or 0,
        is_offline_or_cloud=bool(row["is_offline_or_cloud"])) for row in rows]

def _join(root_path, relative_path):
    r"""Rebuild the absolute Windows path a record was observed at.

    The same reconstruction fo_exports._full_path does, and it must
    stay the same: this is the path the engine opens and the path the
    exported CSV shows, and a project where those two disagreed would
    hash one file and report another.
    """
    if os.name != "nt":
        return os.path.join(root_path or "", (relative_path or "").replace("\\", os.sep))
    root = (root_path or "").rstrip("\\")
    relative = (relative_path or "").lstrip("\\")
    if not relative:
        return root
    return root + "\\" + relative


class HashRecordIngestor(fo_hashes.HashIngestor):
    """A HashIngestor fed by the engine instead of by CSVs."""

    # -- scan binding --------------------------------------------------

    def bind_scans(self, target_path):
        """Resolve which inventory scans these hashes describe.

        Exposed because the caller needs the scan ids BEFORE the engine
        runs -- it loads the engine's input from them -- whereas R4
        only needed them at ingestion time. Same inherited logic, called
        earlier.
        """
        self.ensure_schema()
        self._bind_inventory_scan(target_path)
        return list(self._scan_ids)

    # -- measurements --------------------------------------------------

    def _entry_from_result(self, result, status_map, now):
        """One HashResult -> the internal entry shape _write_measurements
        expects. The only translation layer in this module."""
        sha, identity_source, covers = fo_hash_engine.content_identity(
            result, self.partial_hash_bytes)

        hash_status = status_map.get(result.final_status)
        if hash_status is None:
            hash_status = "unknown"
            self._warn("Unrecognised engine status %r; recorded as 'unknown'."
                       % (result.final_status,))

        entry = {
            "file_observation_id": result.key,
            "size_bytes": result.size,
            "size_group_id": result.size_group_id,
            "partial_hash": result.partial_hash,
            "partial_hash_bytes": (self.partial_hash_bytes
                                   if result.partial_hash else None),
            "partial_group_id": result.partial_group_id,
            "partial_covers_file": covers,
            "full_hash": result.full_hash,
            "sha256": sha,
            "identity_source": identity_source,
            "content_id": None,
            "hash_status": hash_status,
            "alpha_final_status": result.final_status,
            "needed_full_hash": result.needed_full_hash,
            "measured_utc": now,
            "source_artifact": SOURCE_ENGINE,
            "error_kind": result.error_kind,
            "error_message": result.error_message,
        }
        if result.failed:
            self.counts["errors"] += 1
        return entry

    def _persist_results(self, results, status_map):
        """Write every measurement, in batches, one transaction each.

        Batched for the same reason R4 batched: a million-file project
        must not be a million transactions. Content ids are resolved
        per batch so the content table is touched once per distinct
        digest per batch rather than once per file.
        """
        now = utc_now()
        batch = []
        for result in results:
            batch.append(self._entry_from_result(result, status_map, now))
            if len(batch) >= self.batch_rows:
                self._flush_batch(batch)
                batch = []
        if batch:
            self._flush_batch(batch)
        self._refresh_measurement_count()

    def _flush_batch(self, batch):
        wanted = {}
        for entry in batch:
            if entry["sha256"]:
                wanted[entry["sha256"]] = (entry["size_bytes"],
                                           entry["identity_source"])
        content_ids = self._upsert_content(wanted)
        for entry in batch:
            entry["content_id"] = (content_ids.get(entry["sha256"])
                                   if entry["sha256"] else None)
        self._write_measurements(batch)
        self.conn.commit()

    # -- duplicate groups ----------------------------------------------

    def _persist_groups(self, groups):
        r"""Write the run's duplicate_group / duplicate_member snapshot.

        Groups come from the engine's own numbering, which reproduces
        R6's DuplicateGroupID. They are stored as legacy_group_id for
        the same reason R4 stored them: this table records what THAT
        RUN concluded, while content-derived grouping stays available
        at any time by grouping on content_id. Keeping both is what
        lets the verifier check they agree.
        """
        if not groups:
            return 0

        measurement_ids = {
            row["file_observation_id"]: row["hash_measurement_id"]
            for row in self.conn.execute(
                "SELECT file_observation_id, hash_measurement_id "
                "FROM hash_measurement WHERE run_id = ? AND measurement_mode = ?",
                (self.run_id, self.mode))}

        for group in groups:
            content_id = None
            method = "unknown"
            if group.sha256:
                member = group.members[0]
                sha, source, _covers = fo_hash_engine.content_identity(
                    member, self.partial_hash_bytes)
                if sha:
                    method = source
                    content_id = self._upsert_content(
                        {sha: (group.size, source)}).get(sha)

            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO duplicate_group (project_id, "
                "duplicate_run_id, legacy_group_id, content_id, "
                "confirmation_method, size_bytes, member_count, "
                "redundant_count, reclaimable_bytes) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.duplicate_run_id, group.group_id, content_id, method,
                 group.size, group.count, group.redundant, group.reclaimable))
            group_row_id = cursor.lastrowid
            if not group_row_id:
                row = self.conn.execute(
                    "SELECT duplicate_group_id FROM duplicate_group "
                    "WHERE duplicate_run_id = ? AND legacy_group_id = ?",
                    (self.duplicate_run_id, group.group_id)).fetchone()
                group_row_id = row["duplicate_group_id"] if row else None
            if group_row_id is None:
                continue
            self.counts["groups"] += 1

            member_rows = [(group_row_id, m.key, measurement_ids.get(m.key))
                           for m in group.members]
            if member_rows:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO duplicate_member (project_id, "
                    "duplicate_group_id, file_observation_id, "
                    "hash_measurement_id) VALUES (1, ?, ?, ?)", member_rows)
                self.counts["members"] += len(member_rows)
        self.conn.commit()
        return len(groups)

    # -- public entry points -------------------------------------------

    def ingest_selective_records(self, outcome):
        """Persist a selective (Duplicate Run) engine outcome."""
        return self._ingest(outcome, "selective", _SELECTIVE_STATUS)

    def ingest_exhaustive_records(self, outcome):
        """Persist an exhaustive (Full Run) engine outcome."""
        return self._ingest(outcome, "exhaustive", _EXHAUSTIVE_STATUS)

    def _ingest(self, outcome, mode, status_map):
        if outcome.mode != mode:
            raise fo_hashes.HashIngestError(
                "Engine produced a %r outcome but %r was asked for."
                % (outcome.mode, mode))
        self.ensure_schema()
        if self._resolver is None and not self._scan_ids:
            raise fo_hashes.HashIngestError(
                "bind_scans() must run before results can be persisted.")
        self.begin(mode)
        self.artifacts.append(SOURCE_ENGINE)
        self._persist_results(outcome.results, status_map)
        self._persist_groups(outcome.groups)
        return {"mode": mode, "counts": dict(self.counts),
                "engine": outcome.summary()}
