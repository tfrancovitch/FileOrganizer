#!/usr/bin/env python3
r"""
fo_analyzer_records.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Persists results produced by the supported in-process analyzer engine directly
to SQLite. Analyzer inputs come from the current `file_state` projection;
results retain observation/content/run provenance.

B6.1 supports incremental result sinks so large analyzer result populations do
not need to remain in memory. Archive child rows are persisted batch-by-batch,
and persistence failure is a failed analyzer outcome rather than a successful
empty result.
"""

import os
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_analyzer_engine as engine                             # noqa: E402
import fo_analyzers                                             # noqa: E402
import fo_inventory                                             # noqa: E402


utc_now = fo_analyzers.utc_now
_int_or_none = fo_analyzers._int_or_none
_text_or_none = fo_analyzers._text_or_none

#: Recorded in analyzer_result.source_artifact. The column answers
#: "where did this row come from?", and the honest answer is now the
#: runtime, not a file. Writing a CSV name here would be a lie that
#: happened to keep a string column stable.
SOURCE_ENGINE = "fo_analyzer_engine"


def load_entries(conn, inventory_scan_ids):
    r"""Current present files verified by the given scans.

    B6.1 intentionally reads file_state, not this scan's history rows. In
    history.mode='changes' an unchanged file has no new file_observation row;
    current_scan_id/current_legacy_db_id say that the scan reverified it.
    The historical current_observation_id remains the provenance key used by
    analyzer_result.
    """
    if not inventory_scan_ids:
        return []
    placeholders = ",".join("?" * len(inventory_scan_ids))
    rows = conn.execute(
        "SELECT fs.current_observation_id AS file_observation_id, "
        "       fs.current_legacy_db_id AS legacy_db_id, fs.size_bytes, "
        "       fs.is_offline_or_cloud, fp.relative_path, fp.file_name, "
        "       sr.root_path "
        "FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
        "WHERE fs.current_scan_id IN (%s) AND fs.state='present' "
        "  AND fs.current_observation_id IS NOT NULL "
        "ORDER BY sr.root_ordinal, fp.path_sort_key, fp.file_path_id" % placeholders,
        list(inventory_scan_ids)).fetchall()
    return [engine.FileEntry(
        key=row["file_observation_id"], db_id=row["legacy_db_id"],
        path=_join(row["root_path"], row["relative_path"]),
        file_name=row["file_name"], size=row["size_bytes"] or 0,
        is_offline_or_cloud=bool(row["is_offline_or_cloud"])) for row in rows]

def _join(root_path, relative_path):
    r"""Rebuild the absolute Windows path a record was observed at.

    The same reconstruction fo_exports._full_path and
    fo_hash_records._join perform, and it must stay the same: this is
    the path an analyzer opens and the path the exported CSV shows, and
    a project where those disagreed would analyse one file and report
    another.
    """
    if os.name != "nt":
        return os.path.join(root_path or "", (relative_path or "").replace("\\", os.sep))
    root = (root_path or "").rstrip("\\")
    relative = (relative_path or "").lstrip("\\")
    if not relative:
        return root
    return root + "\\" + relative


class AnalyzerRecordIngestor(fo_analyzers.AnalyzerPersistenceBase):
    """Persist results fed directly by the in-process runtime."""

    # -- scan binding --------------------------------------------------

    def bind_scans(self, target_path):
        """Resolve the observation scope before the analyzers run.

        The shared resolver is bound before runtime analysis.
        """
        self.ensure_schema()
        self.resolver.bind(target_path)
        return self.resolver

    # -- one analyzer --------------------------------------------------

    def _row_from_result(self, result):
        r"""One AnalyzerResult -> the CSV-shaped dict _prepare_rows wants.

        The only translation layer in this module. Values are stringified
        because that is what _prepare_rows receives from csv.DictReader
        and what its _int_or_none / _float_or_none helpers are written
        against; handing it native ints here would work today and break
        the first time a promoted column changed type.
        """
        row = {"DB_ID": "" if result.db_id is None else str(result.db_id),
               "FileName": result.file_name or "",
               "Path": result.path,
               "Error": result.error or ""}
        for name, value in result.fields.items():
            row[name] = "" if value is None else str(value)
        return row

    def ingest_outcome(self, outcome, run_stage_id=None):
        """Persist one analyzer's outcome. Returns a per-analyzer summary."""
        import time

        spec = fo_analyzers.SPEC_BY_KEY.get(outcome.key)
        if spec is None:
            raise fo_analyzers.AnalyzerIngestError(
                "No AnalyzerSpec for %r." % outcome.key)

        started = time.monotonic()
        analyzer_run_id = self._begin_analyzer_run(
            spec, outcome.status, run_stage_id)

        counts = {"applicable": 0, "succeeded": 0, "failed": 0, "skipped": 0,
                  "ingested": 0, "unmatched": 0, "db_id_mismatch": 0}
        artifacts = []
        notes = None
        ingest_status = "completed"

        if outcome.status == engine.STATUS_NO_APPLICABLE:
            # Nothing to write, and that IS the result. No placeholder
            # rows: an invented result would be evidence of an analysis
            # that never happened.
            self._finish_analyzer_run(
                analyzer_run_id, counts, "completed", artifacts,
                notes="The analyzer ran and found no applicable files.",
                started=started)
            return {"analyzer": spec.key, "analyzer_run_id": analyzer_run_id,
                    "analysis_status": outcome.status,
                    "ingest_status": "completed", "counts": dict(counts)}

        if outcome.status == engine.STATUS_FAILED and not outcome.results:
            # The analyzer never produced anything -- a missing library,
            # or it raised before its first file. Recorded as failed
            # with the reason, not as "found nothing".
            notes = outcome.failure_reason or "The analyzer failed."
            self._warn("%s: %s" % (spec.label, notes))
            self._finish_analyzer_run(analyzer_run_id, counts, "failed",
                                      artifacts, notes=notes, started=started)
            return {"analyzer": spec.key, "analyzer_run_id": analyzer_run_id,
                    "analysis_status": outcome.status,
                    "ingest_status": "failed", "counts": dict(counts)}

        artifacts.append(spec.artifact)
        now = utc_now()
        all_paths = []

        pending = []
        for result in outcome.results:
            pending.append(self._row_from_result(result))
            if len(pending) >= self.batch_rows:
                self._flush(spec, analyzer_run_id, pending, counts, now,
                            all_paths)
                pending = []
        if pending:
            self._flush(spec, analyzer_run_id, pending, counts, now, all_paths)

        # Child rows, after the parents exist.
        if spec.kind == "archive":
            members, warned = self._write_archive_members(
                analyzer_run_id, outcome, all_paths)
            counts["archive_members"] = members
            if members:
                artifacts.append(spec.secondary_artifact)
            if warned:
                ingest_status = "completed_with_warnings"
        elif spec.kind == "extraction":
            counts["extracted_content"] = self._write_extracted_content(
                analyzer_run_id, outcome, spec, all_paths)

        if counts["unmatched"]:
            ingest_status = "completed_with_warnings"
            notes = ("%d analyzer row(s) could not be matched to a file "
                     "observation and were recorded as unmatched."
                     % counts["unmatched"])

        self._finish_analyzer_run(analyzer_run_id, counts, ingest_status,
                                  artifacts, notes=notes, started=started)
        return {"analyzer": spec.key, "analyzer_run_id": analyzer_run_id,
                "analysis_status": outcome.status,
                "ingest_status": ingest_status, "counts": dict(counts)}

    # -- child tables --------------------------------------------------

    def _write_archive_members(self, analyzer_run_id, outcome, all_paths):
        r"""Archive entries -> archive_member, from the results.

        Nothing here creates a
        file_path or a file_observation: an entry inside a .zip is not
        a location the inventory observed, and pretending otherwise
        would inflate every inventory count derived from those tables.
        """
        result_ids = self._result_ids_by_path(analyzer_run_id, set(all_paths))
        rows = []
        orphaned = 0
        for result in outcome.results:
            entries = result.extra or []
            if not entries:
                continue
            result_id = result_ids.get(result.path)
            if result_id is None:
                orphaned += len(entries)
                continue
            for index, entry in enumerate(entries, start=1):
                entry_path = (entry.get("EntryPath") or "").strip()
                if not entry_path:
                    continue
                name = entry_path.replace("\\", "/").rstrip("/").split("/")[-1]
                extension = ""
                if "." in name:
                    extension = "." + name.rsplit(".", 1)[-1].lower()
                rows.append((
                    result_id, entry_path, fo_inventory.path_key(entry_path),
                    name, extension, _int_or_none(entry.get("EntrySize")),
                    _int_or_none(entry.get("EntryCompressedSize")), index))

        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO archive_member (project_id, "
                "analyzer_result_id, entry_path, entry_path_key, entry_name, "
                "entry_extension_key, entry_size_bytes, "
                "entry_compressed_size_bytes, sequence) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.conn.commit()

        if orphaned:
            self._warn("%d archive content row(s) named an archive with no "
                       "analyzer result and were not persisted." % orphaned)
        return len(rows), bool(orphaned)

    def _write_extracted_content(self, analyzer_run_id, outcome, spec,
                                 all_paths, results=None):
        r"""Extraction metadata -> extracted_content (REFERENCES ONLY).

        Records that extraction happened, which observation it belongs
        to, where the artifact is, and the counts the extractor
        produced. It does NOT read, copy, hash or store the extracted
        text -- that storage architecture is a later revision, and
        pre-empting it here is the one decision B4 is explicitly told
        not to make.
        """
        folder_relpath = "Inventory/" + spec.secondary_artifact
        extract_folder = outcome_extract_folder(outcome)
        result_ids = self._result_ids_by_path(analyzer_run_id, set(all_paths))

        rows = []
        # Explicit list when called from the incremental sink.
        for result in (outcome.results if results is None else results):
            result_id = result_ids.get(result.path)
            if result_id is None:
                continue
            filename = (result.fields.get("ExtractedTextFile") or "").strip()
            error_text = (result.error or "").strip()
            char_count = _int_or_none(result.fields.get("CharCount"))

            if error_text in ("SkippedCloudOnly", "NotProcessed"):
                status = "skipped"
            elif error_text:
                status = "error"
            elif char_count == 0:
                # Ran, succeeded, and the document genuinely had no
                # extractable text -- a scanned PDF, most often.
                # Distinct from an error, and the distinction is the
                # useful part.
                status = "empty"
            else:
                status = "extracted"

            exists = None
            size = None
            if filename and extract_folder:
                candidate = os.path.join(str(extract_folder), filename)
                try:
                    if os.path.isfile(candidate):
                        exists = 1
                        size = os.path.getsize(candidate)
                    else:
                        exists = 0
                except OSError:
                    exists = 0

            rows.append((
                result_id, _text_or_none(result.fields.get("SourceType")),
                folder_relpath,
                (folder_relpath + "/" + filename) if filename else None,
                filename or None, char_count,
                _int_or_none(result.fields.get("WordCount")), exists, size,
                status))

        if rows:
            self.conn.executemany(
                "INSERT OR IGNORE INTO extracted_content (project_id, "
                "analyzer_result_id, source_type, extract_folder_relpath, "
                "extracted_relpath, extracted_filename, char_count, "
                "word_count, artifact_exists, artifact_bytes, status) "
                "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self.conn.commit()
        return len(rows)

    # -- all analyzers -------------------------------------------------

    # -- incremental persistence ---------------------------------------

    def result_sink(self, stage_ids=None):
        r"""A callable the engine feeds each result to as it is produced.

        B6. THE PERSISTENCE HALF OF THE B5-E.F007 FIX.

        `ingest_outcome()` below writes an analyzer's results by
        iterating `outcome.results` after the analyzer has finished --
        which requires that every result was kept. B5-E.F007 measured
        the cost of keeping them: nine analyzers' complete output, all
        alive at once, for the whole pass.

        This returns a sink that batches results into the SAME
        `_flush()` those methods use, as they arrive. The engine then
        retains nothing, and peak memory is one batch rather than one
        corpus.

        WHAT IS DEFERRED, AND WHY. Child rows -- archive members and
        extracted-content references -- still need their parent
        `analyzer_result` rows to exist, so they are written by
        `finalize_outcomes()` after the analyzer completes. Those are
        bounded by their own caps (B5-E.F006 / E.F008) rather than by
        the corpus, which is what makes deferring them safe here.

        Ordering, batching, matching rules and the row shape are all
        `ingest_outcome`'s, unchanged; only WHEN the rows are written
        moves. That matters -- B5-D's finding on duplicated analyzer
        machinery is what happens when a second write path grows its
        own slightly different rules.
        """
        stage_ids = stage_ids or {}
        self._live = {}

        def sink(analyzer_key, result):
            state = self._live.get(analyzer_key)
            if state is None:
                spec = fo_analyzers.SPEC_BY_KEY.get(analyzer_key)
                if spec is None:
                    raise fo_analyzers.AnalyzerIngestError(
                        "No AnalyzerSpec for %r." % analyzer_key)
                state = {
                    "spec": spec,
                    "analyzer_run_id": self._begin_analyzer_run(
                        spec, engine.STATUS_COMPLETED,
                        stage_ids.get(analyzer_key)),
                    "counts": {"applicable": 0, "succeeded": 0, "failed": 0,
                               "skipped": 0, "ingested": 0, "unmatched": 0,
                               "db_id_mismatch": 0},
                    "pending": [], "batch": [], "all_paths": [],
                    "now": utc_now(), "started": time.monotonic(),
                    "children": 0, "orphaned": 0,
                }
                self._live[analyzer_key] = state

            state["pending"].append(self._row_from_result(result))
            # The result objects are kept ONLY for the current batch, so
            # their child rows can be written once _flush has assigned
            # the parent ids. Cleared immediately afterwards -- this is
            # a batch-sized window, not the corpus-sized retention
            # B5-E.F007 measured.
            state["batch"].append(result)
            if len(state["pending"]) >= self.batch_rows:
                self._drain(state, outcome=None)

        return sink

    def _drain(self, state, outcome):
        r"""Flush one batch, then write the child rows it just parented.

        Child rows are written HERE, per batch, rather than deferred to
        the end. `_write_archive_members` needs its parent
        `analyzer_result` rows to exist, and it needs the result
        objects that carry the entries -- so the only way to write them
        without keeping every result alive is to write them while the
        batch is still in hand.

        This is what keeps the archive analyzer bounded. It is the one
        that produced the 358 MB in B5-E.F006, and it is the one whose
        results carry the most weight.
        """
        if not state["pending"]:
            state["batch"] = []
            return
        spec = state["spec"]
        self._flush(spec, state["analyzer_run_id"], state["pending"],
                    state["counts"], state["now"], state["all_paths"])
        state["pending"] = []

        batch = state["batch"]
        state["batch"] = []
        if not batch:
            return

        if spec.kind == "archive":
            members, warned = self._write_archive_members(
                state["analyzer_run_id"], outcome, state["all_paths"],
                results=batch)
            state["children"] += members
            if warned:
                state["orphaned"] += 1
        elif spec.kind == "extraction" and outcome is not None:
            state["children"] += self._write_extracted_content(
                state["analyzer_run_id"], outcome, spec,
                state["all_paths"], results=batch)
        elif spec.kind == "extraction":
            # Extraction child rows need the outcome's extract_folder,
            # which the engine only attaches once the analyzer has
            # finished. Those results are therefore held for the final
            # drain -- bounded by the extraction analyzer's own output,
            # not by the whole pass.
            state.setdefault("deferred", []).extend(batch)

    def finalize_outcomes(self, outcomes, stage_ids=None):
        r"""Close out every analyzer that streamed through `result_sink`.

        Flushes the last partial batch, writes the child rows that
        needed their parents to exist, and closes the analyzer_run row
        with the counts the OUTCOME accumulated -- not counts derived
        from a retained list, which is the thing that is no longer kept.

        An analyzer that produced nothing (no applicable files, or a
        failure before its first file) never reached the sink and so
        has no live state. Those fall through to `ingest_outcome()`,
        which already handles both cases and records them as what they
        were rather than as "found nothing".
        """
        stage_ids = stage_ids or {}
        live = getattr(self, "_live", None) or {}
        summaries = []

        for outcome in outcomes:
            state = live.get(outcome.key)
            if state is None:
                try:
                    summaries.append(self.ingest_outcome(
                        outcome, stage_ids.get(outcome.key)))
                except Exception as exc:                        # noqa: BLE001
                    self._warn("Could not persist %s: %s: %s"
                               % (outcome.key, type(exc).__name__, exc))
                    summaries.append({"analyzer": outcome.key,
                                      "analysis_status": outcome.status,
                                      "ingest_status": "failed", "counts": {}})
                continue

            try:
                summaries.append(self._finalize_one(outcome, state))
            except Exception as exc:                            # noqa: BLE001
                # One analyzer's persistence problem must not stop the
                # others being recorded -- the same rule as
                # ingest_outcomes(), for the same reason.
                self._warn("Could not persist %s: %s: %s"
                           % (outcome.key, type(exc).__name__, exc))
                summaries.append({"analyzer": outcome.key,
                                  "analysis_status": outcome.status,
                                  "ingest_status": "failed", "counts": {}})

        self._live = {}
        return summaries

    def _finalize_one(self, outcome, state):
        spec = state["spec"]
        counts = state["counts"]
        analyzer_run_id = state["analyzer_run_id"]
        artifacts = [spec.artifact]
        notes = None
        ingest_status = "completed"

        # Final batch, plus the child rows it parents.
        self._drain(state, outcome)

        if spec.kind == "archive":
            counts["archive_members"] = state["children"]
            if state["children"]:
                artifacts.append(spec.secondary_artifact)
            if state["orphaned"]:
                ingest_status = "completed_with_warnings"
        elif spec.kind == "extraction":
            deferred = state.get("deferred") or []
            if deferred:
                state["children"] += self._write_extracted_content(
                    analyzer_run_id, outcome, spec, state["all_paths"],
                    results=deferred)
            counts["extracted_content"] = state["children"]
            if state["children"]:
                artifacts.append(spec.secondary_artifact)

        if counts["unmatched"]:
            ingest_status = "completed_with_warnings"
            notes = ("%d analyzer row(s) could not be matched to a file "
                     "observation and were recorded as unmatched."
                     % counts["unmatched"])

        self._finish_analyzer_run(analyzer_run_id, counts, ingest_status,
                                  artifacts, notes=notes,
                                  started=state["started"])
        return {"analyzer": spec.key, "analyzer_run_id": analyzer_run_id,
                "analysis_status": outcome.status,
                "ingest_status": ingest_status, "counts": dict(counts)}

    def ingest_outcomes(self, outcomes, stage_ids=None):
        """Persist every analyzer outcome from one run.

        One analyzer's persistence problem must not stop the others
        being recorded, for the same reason one analyzer's crash must
        not stop the others running.
        """
        stage_ids = stage_ids or {}
        summaries = []
        for outcome in outcomes:
            try:
                summaries.append(self.ingest_outcome(
                    outcome, stage_ids.get(outcome.key)))
            except Exception as exc:                            # noqa: BLE001
                self._warn("Could not persist %s: %s: %s"
                           % (outcome.key, type(exc).__name__, exc))
                summaries.append({"analyzer": outcome.key,
                                  "analysis_status": outcome.status,
                                  "ingest_status": "failed",
                                  "counts": {}})
        return summaries


def outcome_extract_folder(outcome):
    """Where content extraction wrote its .txt files, if it did."""
    return getattr(outcome, "extract_folder", None)
