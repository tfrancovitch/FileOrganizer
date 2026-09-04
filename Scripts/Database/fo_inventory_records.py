#!/usr/bin/env python3
r"""
fo_inventory_records.py
===================================================================
PRODUCTION CODE
The File Organizer -- B6 (Inventory Persistence / State Projection)
Module version: 2.1.0   Requires schema version: 7
===================================================================

Persists the inventory engine's records into the project database,
and maintains `file_state` as it does so.

WHAT B6 CHANGED HERE
--------------------
B4.5 appended one `file_observation` row per file per run, whether or
not anything about the file had changed. B5-E.F001 measured the cost
at ~525 bytes per file per run on an UNCHANGED corpus, and B5-E.F002
measured the consequence that mattered more: the duplicate query got
slower every run, because it scanned all of that history.

B6 writes:

    an OBSERVATION      when the location is new, or when what was
                        observed differs from what is currently
                        recorded;

    a STATE ROW         always -- but `file_state` holds one row per
                        location, so "always" costs a fixed amount.

An unchanged file therefore costs one timestamp UPDATE instead of a
full duplicate row. Ten runs over an unchanged 20,000-file corpus
produce 20,000 observations, not 200,000.

WHAT WAS DELIBERATELY NOT LOST
------------------------------
Every question B4.5's history could answer, B6's history still answers:

    "When was this location first seen?"   file_path.first_seen_utc
    "What did it look like at run 3?"      the observation in force
                                           then, reached by following
                                           supersedes_observation_id
    "Did it change between runs 3 and 7?"  yes iff an observation was
                                           written between them
    "Was it verified on run 7?"            file_state.verified_utc,
                                           plus the scan's counters

What is no longer stored is a byte-identical restatement of an
unchanged file, once per run, forever. B5-E.F001 asks what history
model preserves the facts we need without multiplying the corpus
indefinitely. This is the answer, and `history.mode = 'full'` remains
available for anyone who wants the old behaviour deliberately.

TRANSACTIONAL BEHAVIOUR (B5-G)
------------------------------
Observations and their state rows are written in the SAME transaction,
per batch. A process that dies mid-scan leaves a database in which
every state row has a committed observation behind it, and in which
the scan is visibly not `completed`. It cannot leave a state row
pointing at an observation that was never committed, and it cannot
leave a scan that reads as finished.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_inventory  # noqa: E402
import fo_state      # noqa: E402


utc_now = fo_state.utc_now
path_key = fo_inventory.path_key


class RecordIngestor(fo_inventory.InventoryIngestor):
    """An InventoryIngestor fed by the engine, maintaining file_state."""

    def __init__(self, *args, **kwargs):
        super(RecordIngestor, self).__init__(*args, **kwargs)
        self.projector = None
        self.history_mode = "changes"
        #: B6.2 P4c -- how many WHOLE DIRECTORIES the walk could not list
        #: (fo_scan.DIRECTORY_ACCESS_ERROR). Non-zero means a subtree was
        #: skipped, i.e. this scan's coverage of the root is incomplete --
        #: which is what a share drop or a pulled drive looks like mid-walk.
        self.directory_error_count = 0

    # -- entry point --------------------------------------------------

    def ingest_records(self, records, target_path, timestamp_format,
                       scan_errors=None, root_available=True, path_events=None):
        r"""Persist an iterable of engine records. Returns a summary dict.

        `records` is consumed as an ITERABLE, never materialised, so the
        engine's generator streams into batched inserts and peak memory
        stays bounded by batch_rows rather than by the size of the
        inventory.

        `root_available` is new in B6 and is load-bearing. B5-H requires
        that a missing root not read as an empty root. If the root could
        not be reached, this records the scan with that fact and marks
        NOTHING missing -- because nothing was observed to be missing,
        only unobserved.
        """
        self.ensure_schema()
        started = utc_now()
        primary_root_id = self._ensure_root(target_path)
        fo_state.assign_root_ordinals(self.conn)
        resolver = fo_inventory.RootResolver(self._active_roots(), primary_root_id)
        self._scan_for_root(primary_root_id, started)

        self.history_mode = fo_state.history_mode(self.conn)
        self.projector = fo_state.StateProjector(self.conn, self.run_id)
        self.projector.prime(primary_root_id)

        total_rows = 0
        batch = []
        for record in records:
            total_rows += 1
            entry = self._entry_from_record(record, resolver)
            self._scan_for_root(entry["source_root_id"], started)
            self.projector.prime(entry["source_root_id"])
            self.observed[entry["source_root_id"]] = \
                self.observed.get(entry["source_root_id"], 0) + 1
            batch.append(entry)
            if len(batch) >= self.batch_rows:
                self._flush_with_state(batch)
                batch = []
        if batch:
            self._flush_with_state(batch)

        inaccessible = self.ingest_scan_errors(scan_errors or [], resolver)
        self.ingest_path_events(path_events or [], resolver)

        # Vanished detection. ONLY for a root that was actually
        # available and actually walked. See B5-H: absence of evidence
        # is not evidence of absence, and this is the one place in the
        # product that could confuse the two.
        vanished = 0
        if root_available:
            for root_id, scan_id in list(self.scan_ids.items()):
                vanished += self.projector.mark_vanished(root_id, scan_id)
            self.conn.commit()

        if resolver.unmatched:
            self._warn(
                "%d observed path(s) did not fall under any registered source "
                "root and were attributed to %s with their full path retained."
                % (resolver.unmatched, target_path))

        availability = "available" if root_available else "missing"
        self.conn.execute(
            "UPDATE inventory_scan SET source_csv_rows = ?, "
            "timestamp_format_detected = ?, timestamp_model = 'utc_offset', "
            "history_mode = ?, root_availability = ?, "
            "unchanged_count = ?, changed_count = ?, vanished_count = ?, "
            "new_path_count = ? "
            "WHERE inventory_scan_id = ?",
            (total_rows, timestamp_format, self.history_mode, availability,
             self.projector.unchanged_count, self.projector.changed_count,
             vanished, self.projector.new_count,
             self.scan_ids[primary_root_id]))
        self.conn.commit()

        return {"rows": total_rows, "inaccessible": inaccessible,
                "directory_errors": self.directory_error_count,
                "timestamp_format": timestamp_format,
                "unmatched_paths": resolver.unmatched,
                "new": self.projector.new_count,
                "changed": self.projector.changed_count,
                "unchanged": self.projector.unchanged_count,
                "vanished": vanished,
                "history_mode": self.history_mode,
                "root_availability": availability}

    # -- the batch ----------------------------------------------------

    def _flush_with_state(self, entries):
        r"""Write one batch of observations AND their state rows.

        ONE TRANSACTION, committed at the end, covering both tables. So
        `file_state.current_observation_id` can never reference an
        observation that was not committed alongside it.
        """
        if not entries:
            return
        now = utc_now()
        path_ids = self._resolve_path_ids(
            entries, self.scan_ids[entries[0]["source_root_id"]], now)

        # Give every location its deterministic presentation key. Cheap,
        # idempotent, and it guarantees the key exists before any export
        # can ask for it.
        sort_updates = []
        for entry in entries:
            fid = path_ids.get((entry["source_root_id"], entry["key"]))
            if fid is not None:
                key = fo_state.sort_key_for(entry["relative_path"])
                sort_updates.append((key, fid, key))
        if sort_updates:
            self.conn.executemany(
                "UPDATE file_path SET path_sort_key = ? WHERE file_path_id = ? "
                "AND (path_sort_key IS NULL OR path_sort_key <> ?)",
                sort_updates)

        to_write = []
        unchanged_rows = []

        for entry in entries:
            identity = (entry["source_root_id"], entry["key"])
            file_path_id = path_ids.get(identity)
            if file_path_id is None:
                self._warn("Could not resolve a location id for %s; row skipped."
                           % entry["relative_path"][:120])
                continue

            change_kind = self.projector.classify(file_path_id, entry)
            if change_kind is None and self.history_mode == "full":
                # 'full' reproduces B4.5's behaviour on request, and
                # labels the row for what it is rather than implying
                # the file changed.
                change_kind = fo_state.CHANGE_VERIFIED

            if change_kind is None:
                current = dict(entry)
                current["file_path_id"] = file_path_id
                current["scan_id"] = self.scan_ids[entry["source_root_id"]]
                current["timestamp_model"] = "utc_offset"
                unchanged_rows.append(current)
                continue

            to_write.append((entry, file_path_id, change_kind,
                             self.projector.prior_observation_id(file_path_id)))

        if to_write:
            self._insert_observations(to_write, now)
            written_ids = self._observation_ids(
                [(self.scan_ids[e["source_root_id"]], fid)
                 for e, fid, _k, _p in to_write])
            state_rows = []
            for entry, file_path_id, _kind, _prior in to_write:
                scan_id = self.scan_ids[entry["source_root_id"]]
                state_rows.append({
                    "file_path_id": file_path_id,
                    "source_root_id": entry["source_root_id"],
                    "state": ("inaccessible"
                              if entry["status"] == "inaccessible"
                              else "present"),
                    "observation_id": written_ids.get((scan_id, file_path_id)),
                    "scan_id": scan_id,
                    "legacy_db_id": entry["legacy_db_id"],
                    "size_bytes": entry["size_bytes"],
                    "created_local_naive": entry["created_local_naive"],
                    "modified_local_naive": entry["modified_local_naive"],
                    "accessed_local_naive": entry["accessed_local_naive"],
                    "created_utc": entry["created_utc"],
                    "modified_utc": entry["modified_utc"],
                    "accessed_utc": entry["accessed_utc"],
                    "utc_offset_minutes": entry["utc_offset_minutes"],
                    "timestamp_model": "utc_offset",
                    "attributes": entry["attributes"],
                    "is_reparse_point": entry["is_reparse_point"],
                    "reparse_tag": entry["reparse_tag"],
                    "is_offline_or_cloud": entry["is_offline_or_cloud"],
                    "depth": entry["depth"],
                    "path_length": entry["path_length"],
                    "volume_serial": entry["volume_serial"],
                    "file_index": entry["file_index"],
                    "hard_link_count": entry["hard_link_count"],
                    "allocated_size_bytes": entry["allocated_size_bytes"],
                    "created_time_state": entry["created_time_state"],
                    "modified_time_state": entry["modified_time_state"],
                    "accessed_time_state": entry["accessed_time_state"],
                    "first_seen_utc": now,
                })
            self.projector.upsert(state_rows, now)

        if unchanged_rows:
            self.projector.touch_unchanged(unchanged_rows, now)

        self.conn.commit()

    def _insert_observations(self, to_write, now):
        """Insert this batch's change observations, in one statement."""
        payload = []
        for entry, file_path_id, change_kind, prior in to_write:
            payload.append((
                self.scan_ids[entry["source_root_id"]], file_path_id,
                entry["status"], entry["legacy_db_id"], entry["size_bytes"],
                entry["created_local_naive"], entry["modified_local_naive"],
                entry["accessed_local_naive"],
                entry["created_utc"], entry["modified_utc"],
                entry["accessed_utc"], entry["utc_offset_minutes"],
                entry["timestamps_parsed"], entry["attributes"],
                entry["is_reparse_point"], entry["reparse_tag"],
                entry["is_offline_or_cloud"], entry["path_length"], now,
                entry["error_kind"], entry["error_message"], change_kind, prior,
                entry["volume_serial"], entry["file_index"],
                entry["hard_link_count"], entry["allocated_size_bytes"],
                entry["created_time_state"], entry["modified_time_state"],
                entry["accessed_time_state"]))

        self.conn.executemany(
            "INSERT OR IGNORE INTO file_observation ("
            "  project_id, inventory_scan_id, file_path_id, status,"
            "  legacy_db_id, size_bytes,"
            "  created_local_naive, modified_local_naive, accessed_local_naive,"
            "  created_utc, modified_utc, accessed_utc, utc_offset_minutes,"
            "  timestamps_parsed, attributes, is_reparse_point, reparse_tag,"
            "  is_offline_or_cloud, path_length, observed_utc,"
            "  error_kind, error_message, change_kind,"
            "  supersedes_observation_id, volume_serial, file_index,"
            "  hard_link_count, allocated_size_bytes, created_time_state,"
            "  modified_time_state, accessed_time_state, timestamp_model) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'utc_offset')", payload)

    def _observation_ids(self, pairs):
        r"""Look up the ids just inserted, one query for the whole batch.

        A per-row SELECT here would reintroduce exactly the cost
        `_stamp_last_seen`'s own comment records B4.5 having removed
        from ingestion. The unique index on (inventory_scan_id,
        file_path_id) makes this a batched index probe.
        """
        if not pairs:
            return {}
        found = {}
        chunk = 400
        for start in range(0, len(pairs), chunk):
            slice_ = pairs[start:start + chunk]
            scan_ids = sorted({s for s, _f in slice_})
            path_ids = [f for _s, f in slice_]
            sql = ("SELECT inventory_scan_id, file_path_id, file_observation_id "
                   "FROM file_observation WHERE inventory_scan_id IN (%s) "
                   "AND file_path_id IN (%s)"
                   % (",".join("?" * len(scan_ids)),
                      ",".join("?" * len(path_ids))))
            for row in self.conn.execute(sql, scan_ids + path_ids):
                found[(row[0], row[1])] = row[2]
        return found

    # -- record mapping -----------------------------------------------

    def _entry_from_record(self, record, resolver):
        """One engine record in the internal shape the batch writer uses."""
        absolute = record.path
        source_root_id, relative, _matched = resolver.resolve(absolute)
        parsed = 1 if (record.created or record.modified or record.accessed) else 0
        return {
            "source_root_id": source_root_id,
            "relative_path": relative,
            "key": path_key(relative),
            "file_name": record.file_name,
            # Lowercased for COMPARISON only. The export rebuilds the
            # displayed extension from file_name, so REPORT.PDF still
            # exports as .PDF.
            "extension_key": (record.extension or "").lower(),
            "depth": record.depth,
            "status": "observed",
            "legacy_db_id": record.legacy_db_id,
            "size_bytes": record.size_bytes,
            # B6 stores BOTH representations, under names that say which
            # is which. See migration 006's timestamp note.
            "created_local_naive": record.created,
            "modified_local_naive": record.modified,
            "accessed_local_naive": record.accessed,
            "created_utc": record.created_utc,
            "modified_utc": record.modified_utc,
            "accessed_utc": record.accessed_utc,
            "utc_offset_minutes": record.utc_offset_minutes,
            "timestamps_parsed": parsed,
            "attributes": record.attributes or None,
            "is_reparse_point": 1 if record.is_reparse_point else 0,
            "reparse_tag": getattr(record, "reparse_tag", None),
            "is_offline_or_cloud": 1 if record.is_offline_or_cloud else 0,
            "path_length": record.path_length,
            "volume_serial": record.volume_serial,
            "file_index": record.file_index,
            "hard_link_count": record.hard_link_count,
            "allocated_size_bytes": getattr(record, "allocated_size_bytes", None),
            "created_time_state": "known" if (record.created_utc or record.created) else "unavailable",
            "modified_time_state": "known" if (record.modified_utc or record.modified) else "unavailable",
            "accessed_time_state": "known" if (record.accessed_utc or record.accessed) else "unavailable",
            "error_kind": None, "error_message": None,
        }

    def ingest_path_events(self, path_events, resolver):
        """Persist scan coverage facts absent from the file list."""
        if not path_events:
            return 0
        now = utc_now()
        rows = []
        for event_kind, raw_path, reparse_tag in path_events:
            source_root_id, relative, _matched = resolver.resolve(raw_path)
            self._scan_for_root(source_root_id, now)
            scan_id = self.scan_ids[source_root_id]
            rows.append((scan_id, source_root_id, event_kind, relative,
                         path_key(relative), reparse_tag, now))
        self.conn.executemany(
            "INSERT OR IGNORE INTO scan_path_event (project_id, inventory_scan_id, "
            "source_root_id, event_kind, relative_path, relative_path_key, "
            "reparse_tag, observed_utc) VALUES (1,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    # -- inaccessible locations ---------------------------------------

    def ingest_scan_errors(self, scan_errors, resolver, max_entries=None):
        r"""Record inaccessible locations, and record the CAP HONESTLY.

        THE FIX FOR B5-E.F044.

        B4.5 stored at most 5,000 inaccessible paths and said nothing
        about having stopped. A badly permissioned tree therefore
        produced a database that was quietly an incomplete record of
        itself, and a consumer could not distinguish "4,900 inaccessible
        files" from "at least 5,000, of which 5,000 were kept".

        B6 keeps a cap, because unbounded diagnostics are their own
        scalability problem, and records BOTH numbers plus a truncation
        flag. The bound is still a bound; it is no longer a secret.
        """
        if max_entries is None:
            try:
                max_entries = int(fo_state.get_policy(
                    self.conn, "inaccessible.cap", "5000") or 5000)
            except (TypeError, ValueError):
                max_entries = 5000

        started = utc_now()
        entries, seen, stored = [], 0, 0
        dir_errors = 0
        for error in scan_errors:
            seen += 1
            # fo_scan.DIRECTORY_ACCESS_ERROR -- a whole directory could not
            # be listed, so everything under it was skipped.
            if error.kind == "DIRECTORY ACCESS ERROR":
                dir_errors += 1
            if stored >= max_entries:
                continue          # keep COUNTING; stop STORING
            raw_path = error.path
            source_root_id, relative, _m = resolver.resolve(raw_path)
            self._scan_for_root(source_root_id, started)
            if self.projector is not None:
                self.projector.prime(source_root_id)
            self.inaccessible[source_root_id] = \
                self.inaccessible.get(source_root_id, 0) + 1
            entries.append({
                "source_root_id": source_root_id, "relative_path": relative,
                "key": path_key(relative),
                "file_name": os.path.basename(raw_path.rstrip("\\/")) or raw_path,
                "extension_key": os.path.splitext(raw_path)[1].lower(),
                "depth": None, "status": "inaccessible", "legacy_db_id": None,
                "size_bytes": None,
                "created_local_naive": None, "modified_local_naive": None,
                "accessed_local_naive": None,
                "created_utc": None, "modified_utc": None,
                "accessed_utc": None, "utc_offset_minutes": None,
                "timestamps_parsed": 0, "attributes": None,
                "is_reparse_point": None, "reparse_tag": None,
                "is_offline_or_cloud": None, "path_length": len(raw_path),
                "volume_serial": None, "file_index": None,
                "hard_link_count": None, "allocated_size_bytes": None,
                "created_time_state": "unavailable",
                "modified_time_state": "unavailable",
                "accessed_time_state": "unavailable",
                "error_kind": error.kind,
                "error_message": (error.message or "")[:1000],
            })
            stored += 1
            if len(entries) >= self.batch_rows:
                self._flush_with_state(entries)
                entries = []
        if entries:
            self._flush_with_state(entries)

        truncated = 1 if seen > stored else 0
        for scan_id in set(self.scan_ids.values()):
            self.conn.execute(
                "UPDATE inventory_scan SET inaccessible_seen_count = ?, "
                "inaccessible_cap = ?, inaccessible_truncated = ? "
                "WHERE inventory_scan_id = ?",
                (seen, max_entries, truncated, scan_id))
        self.conn.commit()

        self.directory_error_count = dir_errors

        # B6.2 P4c -- WARN WHENEVER ANYTHING WAS INACCESSIBLE, not only past
        # the cap. Before this, a scan that lost access to part of the tree
        # mid-walk (a share dropping, a USB drive pulled) recorded the
        # skipped directories but still finished 'completed' with zero
        # warnings, because finish() only downgrades to
        # 'completed_with_warnings' when self.warnings is non-empty. A
        # partial scan presented as a clean one is exactly the failure the
        # acceptance rules say must not ship. A single unreadable FILE is
        # normal and non-alarming; a skipped DIRECTORY means a whole
        # subtree is missing from this inventory.
        if dir_errors:
            self._warn(
                "%d folder(s) could not be listed during this scan, so the "
                "files under them are NOT in this inventory. This scan's "
                "coverage of the source is incomplete -- a disconnected "
                "drive or share mid-scan looks like this. (%d path(s) "
                "inaccessible in total.)" % (dir_errors, seen))
        elif seen:
            self._warn(
                "%d file(s) could not be read during this scan and were "
                "recorded as inaccessible rather than inventoried." % seen)

        if truncated:
            self._warn(
                "Only the first %d inaccessible path(s) are stored in the "
                "database; the cap is recorded with them and the remainder "
                "appear in the log file only." % stored)
        return stored
