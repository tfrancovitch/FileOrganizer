#!/usr/bin/env python3
r"""
fo_analyzers.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Shared analyzer specifications and SQLite persistence primitives for the
supported in-process analyzer engine. SQLite is authoritative; analyzer
results are persisted directly through AnalyzerRecordIngestor.

This module does not execute analyzers and does not ingest CSV artifacts.
It owns the stable AnalyzerSpec contracts, observation/content attribution,
analyzer_run lifecycle, batched result persistence, and read helpers.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import fo_inventory  # noqa: E402  (path_key, RootResolver)

REQUIRED_SCHEMA_VERSION = 7

#: Rows prepared and written per transaction. Matches the R3/R4 value.
BATCH_ROWS = 2000

#: Maximum parameters in one IN (...) clause.
IN_CHUNK = 400

#: Alpha's Error column vocabulary -> analyzer_result.status.
#: Anything not listed is a real per-file analyzer failure.
_STATUS_FROM_ERROR = {
    "": "analyzed",
    "SkippedCloudOnly": "skipped_cloud_only",
    "NotProcessed": "not_processed",
}

_VERSION_LINE = re.compile(r"^\s*Version:\s*(\S+)", re.MULTILINE)


class AnalyzerIngestError(Exception):
    """Raised only for conditions that make ingestion impossible."""


def utc_now():
    return fo_inventory.utc_now()


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text_or_none(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


# ---------------------------------------------------------------------------
# Analyzer specifications
#
# One entry per analyzer. This is the ONLY place that knows the shape of
# an Alpha artifact, so adding a tenth analyzer is a row here plus a row
# in the `analyzer` table -- not a migration and not a schema change.
#
# promoted:  db column -> (csv column, converter)
#            Only fields that TWO OR MORE analyzers populate appear
#            here; that rule is what stops analyzer_result drifting into
#            the wide sparse table the brief forbids. See migration 005.
#
# detail:    csv columns stored in detail_json, in this order.
# ---------------------------------------------------------------------------

class AnalyzerSpec(object):
    def __init__(self, key, label, artifact, promoted, detail,
                 script_name=None, engine_name=None,
                 secondary_artifact=None, kind="simple"):
        self.key = key
        self.label = label
        self.artifact = artifact
        self.promoted = promoted
        self.detail = detail
        self.script_name = script_name
        self.engine_name = engine_name
        self.secondary_artifact = secondary_artifact
        self.kind = kind


ANALYZER_SPECS = [
    AnalyzerSpec(
        "image", "Image Analysis", "ImageHashes.csv",
        promoted={"width_px": ("Width", _int_or_none),
                  "height_px": ("Height", _int_or_none)},
        detail=["pHash", "aHash", "dHash", "Format", "HashSize"],
        script_name="ImageAnalysis.ps1", engine_name="ImageHash.py"),

    AnalyzerSpec(
        "pdf", "PDF Analysis", "PDFInventory.csv",
        promoted={"title": ("Title", _text_or_none),
                  "author": ("Author", _text_or_none),
                  "content_created_reported": ("CreationDate", _text_or_none)},
        detail=["PageCount", "IsEncrypted", "HasExtractableText", "Producer"],
        script_name="PDFAnalysis.ps1", engine_name="PDFAnalysis.py"),

    AnalyzerSpec(
        "office", "Office Analysis", "OfficeInventory.csv",
        promoted={"title": ("Title", _text_or_none),
                  "author": ("Author", _text_or_none),
                  "content_created_reported": ("Created", _text_or_none)},
        detail=["OfficeType", "ExtractionMode", "Modified", "ContentCount"],
        script_name="OfficeAnalysis.ps1", engine_name="OfficeAnalysis.py"),

    AnalyzerSpec(
        "raw_image", "RAW Image Analysis", "RawImageInventory.csv",
        promoted={"content_created_reported": ("DateTimeOriginal", _text_or_none)},
        detail=["CameraMake", "CameraModel", "ExposureTime", "FNumber",
                "ISO", "FocalLength"],
        script_name="RawImageAnalysis.ps1", engine_name="RawImageAnalysis.py"),

    AnalyzerSpec(
        "audio", "Audio Analysis", "AudioInventory.csv",
        promoted={"duration_seconds": ("DurationSeconds", _float_or_none),
                  "title": ("Title", _text_or_none)},
        detail=["Bitrate", "Codec", "SampleRate", "Channels", "Artist",
                "Album", "Year", "TrackNumber", "Genre"],
        script_name="AudioAnalysis.ps1", engine_name="AudioAnalysis.py"),

    AnalyzerSpec(
        "video", "Video Analysis", "VideoInventory.csv",
        promoted={"duration_seconds": ("DurationSeconds", _float_or_none),
                  "width_px": ("Width", _int_or_none),
                  "height_px": ("Height", _int_or_none)},
        detail=["Bitrate", "VideoCodec", "FrameRate", "AudioCodec"],
        script_name="VideoAnalysis.ps1", engine_name="VideoAnalysis.py"),

    AnalyzerSpec(
        "text", "Text / Markdown Analysis", "TextFileInventory.csv",
        promoted={"title": ("Title", _text_or_none),
                  "word_count": ("WordCount", _int_or_none),
                  "char_count": ("CharCount", _int_or_none)},
        detail=["LineCount", "Encoding", "HasFrontmatter", "HeadingCount",
                "WikilinkCount", "TagCount"],
        script_name="TextFileAnalysis.ps1", engine_name="TextFileAnalysis.py"),

    AnalyzerSpec(
        "archive", "Archive Analysis", "ArchiveInventory.csv",
        promoted={},
        detail=["EntryCount", "TotalUncompressedSize", "TotalCompressedSize",
                "CompressionRatioPercent"],
        script_name="ArchiveAnalysis.ps1", engine_name="ArchiveAnalysis.py",
        secondary_artifact="ArchiveContents.csv", kind="archive"),

    AnalyzerSpec(
        "content_extraction", "Content Extraction", "ContentIndex.csv",
        promoted={"word_count": ("WordCount", _int_or_none),
                  "char_count": ("CharCount", _int_or_none)},
        detail=["SourceType", "ExtractedTextFile"],
        script_name="ContentExtraction.ps1", engine_name="ContentExtraction.py",
        secondary_artifact="ExtractedText", kind="extraction"),
]

SPEC_BY_KEY = {spec.key: spec for spec in ANALYZER_SPECS}
SPEC_BY_SCRIPT = {spec.script_name: spec for spec in ANALYZER_SPECS}

#: Stage statuses that mean "this analyzer did not run in this run", so
#: no analyzer_run row is created. R2's run_stage already records the
#: fact; duplicating it here would blur "not selected" together with
#: "ran and found nothing", which is exactly the distinction the brief
#: asks to preserve.
_NON_RUNNING_STAGE_STATUS = {"skipped"}


def read_script_version(scripts_dir, filename):
    r"""Best-effort 'Version: x.y.z' from an Alpha script header.

    Read rather than hard-coded so that "which analyzer version produced
    this result?" survives the script being upgraded. Failure is not an
    error: the column is nullable and an unknown version is a better
    record than a wrong one.
    """
    if not scripts_dir or not filename:
        return None
    path = os.path.join(str(scripts_dir), filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4000)
    except OSError:
        return None
    match = _VERSION_LINE.search(head)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Observation resolution
# ---------------------------------------------------------------------------

class ObservationResolver(object):
    r"""Maps absolute analyzer paths onto R3 file observations.

    Deliberately mirrors the linkage HashIngestor established in R4:
    bind to EVERY inventory_scan of the newest completed inventory run
    (R3 creates one scan per source root, so binding to a single scan
    would make every observation under the project's other roots
    invisible), then resolve each absolute path through the longest-
    prefix root resolver to (source_root_id, relative_path_key).

    It is a separate class rather than a call into fo_hashes because
    The hash subsystem remains independent of analyzer persistence and does not modify
    modules to add a caller. Folding both onto one resolver is a
    worthwhile tidy-up for R6/R7, when those files are being revisited
    anyway.
    """

    def __init__(self, conn):
        self.conn = conn
        self.scan_ids = []
        self.resolver = None
        self.cache = {}
        self.unmatched_root = 0

    def bind(self, target_path):
        row = self.conn.execute(
            "SELECT inventory_scan_id, run_id, source_root_id FROM inventory_scan "
            "WHERE status IN ('completed', 'completed_with_warnings') "
            "ORDER BY started_utc DESC, inventory_scan_id DESC LIMIT 1").fetchone()
        if row is None:
            raise AnalyzerIngestError(
                "No completed inventory scan exists in this project, so analyzer "
                "results cannot be attached to file observations.")

        self.scan_ids = [r["inventory_scan_id"] for r in self.conn.execute(
            "SELECT inventory_scan_id FROM inventory_scan "
            "WHERE run_id = ? AND status IN ('completed', 'completed_with_warnings')",
            (row["run_id"],))]

        roots = [(r["source_root_id"], r["root_path"]) for r in self.conn.execute(
            "SELECT source_root_id, root_path FROM source_root "
            "WHERE project_id = 1 AND is_active = 1")]
        primary = row["source_root_id"]
        for root_id, root_path in roots:
            if fo_inventory.path_key(root_path) == fo_inventory.path_key(target_path or ""):
                primary = root_id
                break
        self.resolver = fo_inventory.RootResolver(roots, primary)
        return self.scan_ids

    def lookup(self, absolute_paths):
        """Resolve a batch of absolute paths. Returns the shared cache."""
        wanted = {}
        for absolute in absolute_paths:
            if not absolute or absolute in self.cache:
                continue
            root_id, relative, matched = self.resolver.resolve(absolute)
            key = fo_inventory.path_key(relative)
            wanted.setdefault(root_id, {})[key] = absolute
            if not matched:
                self.unmatched_root += 1

        if not self.scan_ids:
            return self.cache
        scan_placeholders = ",".join("?" * len(self.scan_ids))
        for root_id, keyed in wanted.items():
            keys = list(keyed)
            for start in range(0, len(keys), IN_CHUNK):
                chunk = keys[start:start + IN_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = self.conn.execute(
                    "SELECT fp.relative_path_key, o.file_observation_id, o.legacy_db_id "
                    "FROM file_path fp JOIN file_observation o "
                    "  ON o.file_path_id = fp.file_path_id "
                    "WHERE fp.source_root_id = ? AND o.inventory_scan_id IN (%s) "
                    "  AND fp.relative_path_key IN (%s)"
                    % (scan_placeholders, placeholders),
                    [root_id] + self.scan_ids + chunk).fetchall()
                for row in rows:
                    absolute = keyed.get(row["relative_path_key"])
                    if absolute is not None:
                        self.cache[absolute] = {
                            "file_observation_id": row["file_observation_id"],
                            "legacy_db_id": row["legacy_db_id"],
                        }
        return self.cache


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

class AnalyzerPersistenceBase(object):
    """Shared persistence primitives for database-backed analyzer results."""

    def __init__(self, conn, run_id, run_stage_id=None, logger=None,
                 batch_rows=BATCH_ROWS, scripts_dir=None):
        self.conn = conn
        self.run_id = run_id
        #: The stage that is doing the PERSISTING. Recorded separately
        #: from the stage that did the analysing -- see migration 005.
        self.ingest_run_stage_id = run_stage_id
        self.logger = logger
        self.batch_rows = batch_rows
        self.scripts_dir = scripts_dir or os.path.dirname(HERE)

        self.warnings = []
        self.resolver = ObservationResolver(conn)
        self._analyzer_ids = {}
        self._version_cache = {}
        self.per_analyzer = {}

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
            raise AnalyzerIngestError(
                "Analyzer persistence needs schema version %d; this database "
                "is at %d." % (REQUIRED_SCHEMA_VERSION, version))
        return version

    def _analyzer_id(self, key):
        if key not in self._analyzer_ids:
            row = self.conn.execute(
                "SELECT analyzer_id FROM analyzer WHERE analyzer_key = ?",
                (key,)).fetchone()
            if row is None:
                raise AnalyzerIngestError(
                    "Analyzer '%s' is not registered in this database." % key)
            self._analyzer_ids[key] = row["analyzer_id"]
        return self._analyzer_ids[key]

    def _version(self, filename):
        if filename not in self._version_cache:
            self._version_cache[filename] = read_script_version(
                self.scripts_dir, filename)
        return self._version_cache[filename]

    def _stage_map(self):
        r"""stage_key -> (run_stage_id, status) for this run.

        The analyzer stages are recorded by the Dashboard through R2's
        coordinator, so the truth about what ran, and with what outcome,
        already exists. Reading it is how analyzer_run.analysis_status
        ends up agreeing with run_stage.status by construction instead
        of by a second, independent guess.
        """
        mapping = {}
        for row in self.conn.execute(
                "SELECT run_stage_id, stage_key, status FROM run_stage "
                "WHERE run_id = ? ORDER BY sequence", (self.run_id,)):
            mapping[row["stage_key"]] = (row["run_stage_id"], row["status"])
        return mapping

    # -- analyzer_run -------------------------------------------------

    def _begin_analyzer_run(self, spec, analysis_status, run_stage_id):
        analyzer_id = self._analyzer_id(spec.key)
        existing = self.conn.execute(
            "SELECT analyzer_run_id FROM analyzer_run WHERE run_id = ? AND analyzer_id = ?",
            (self.run_id, analyzer_id)).fetchone()
        if existing:
            analyzer_run_id = existing["analyzer_run_id"]
            # Re-ingesting THIS run's artifact is a repair, not history.
            # History lives across runs: a later analysis creates a new
            # `run`, hence a new analyzer_run row, and nothing here can
            # reach it. Clearing only this run's rows keeps re-ingestion
            # idempotent without inventing duplicates, and cascades to
            # archive_member and extracted_content.
            self.conn.execute(
                "DELETE FROM analyzer_result WHERE analyzer_run_id = ?",
                (analyzer_run_id,))
            self.conn.execute(
                "UPDATE analyzer_run SET analysis_status = ?, ingest_status = 'running', "
                "run_stage_id = COALESCE(?, run_stage_id), ingest_run_stage_id = ?, "
                "started_utc = ? WHERE analyzer_run_id = ?",
                (analysis_status, run_stage_id, self.ingest_run_stage_id,
                 utc_now(), analyzer_run_id))
        else:
            cur = self.conn.execute(
                "INSERT INTO analyzer_run (project_id, analyzer_id, run_id, "
                "run_stage_id, ingest_run_stage_id, analysis_status, ingest_status, "
                "analyzer_version, engine_version, started_utc) "
                "VALUES (1, ?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (analyzer_id, self.run_id, run_stage_id, self.ingest_run_stage_id,
                 analysis_status, self._version(spec.script_name),
                 self._version(spec.engine_name), utc_now()))
            analyzer_run_id = cur.lastrowid
        self.conn.commit()
        return analyzer_run_id

    def _finish_analyzer_run(self, analyzer_run_id, counts, ingest_status,
                             artifacts, notes=None, started=None):
        duration = None
        if started is not None:
            duration = int((time.monotonic() - started) * 1000)
        self.conn.execute(
            "UPDATE analyzer_run SET ingest_status = ?, completed_utc = ?, "
            "duration_ms = ?, applicable_count = ?, succeeded_count = ?, "
            "failed_count = ?, skipped_count = ?, ingested_count = ?, "
            "unmatched_count = ?, db_id_mismatch_count = ?, source_artifacts = ?, "
            "notes = ? WHERE analyzer_run_id = ?",
            (ingest_status, utc_now(), duration, counts["applicable"],
             counts["succeeded"], counts["failed"], counts["skipped"],
             counts["ingested"], counts["unmatched"], counts["db_id_mismatch"],
             ", ".join(artifacts) or None, (notes or None), analyzer_run_id))
        self.conn.commit()

    # -- row preparation ----------------------------------------------

    def _prepare_rows(self, spec, rows, artifact_name, now):
        """Turn a batch of CSV rows into analyzer_result parameter dicts."""
        paths = [(r.get("Path") or "").strip() for r in rows]
        self.resolver.lookup([p for p in paths if p])
        prepared = []

        for row in rows:
            absolute = (row.get("Path") or "").strip()
            observation = self.resolver.cache.get(absolute)
            db_id = _int_or_none(row.get("DB_ID"))

            error_text = (row.get("Error") or "").strip()
            status = _STATUS_FROM_ERROR.get(error_text, "error")
            error_kind = error_message = None
            if status == "error":
                error_kind = "ANALYZER ERROR"
                error_message = error_text[:2000]

            entry = {
                "file_observation_id": None,
                "content_id": None,
                "status": status,
                "legacy_db_id": db_id,
                "unmatched_path": None,
                "detail_json": None,
                "analyzed_utc": now,
                "source_artifact": artifact_name,
                "error_kind": error_kind,
                "error_message": error_message,
                "_path": absolute,
                "_db_id_mismatch": False,
            }
            for column in ("title", "author", "content_created_reported",
                           "width_px", "height_px", "duration_seconds",
                           "word_count", "char_count"):
                entry[column] = None

            if observation is None:
                # Never silently discarded. The analyzer really produced
                # this result; dropping it would make the database
                # quietly disagree with the CSV, which is the failure
                # mode the brief calls out by name.
                entry["status"] = "unmatched"
                entry["unmatched_path"] = absolute[:1000] or None
                prepared.append(entry)
                continue

            entry["file_observation_id"] = observation["file_observation_id"]
            if (db_id is not None and observation["legacy_db_id"] is not None
                    and db_id != observation["legacy_db_id"]):
                entry["_db_id_mismatch"] = True

            for column, (csv_column, convert) in spec.promoted.items():
                entry[column] = convert(row.get(csv_column))

            detail = {}
            for csv_column in spec.detail:
                value = row.get(csv_column)
                if value not in (None, ""):
                    detail[csv_column] = value
            entry["detail_json"] = json.dumps(
                detail, ensure_ascii=False, sort_keys=True) if detail else None

            prepared.append(entry)

        self._attach_content_ids(prepared)
        return prepared

    def _attach_content_ids(self, prepared):
        r"""Attribute each result to a content identity, where one is known.

        An ATTRIBUTION, not a deduplication key: the result still
        belongs to its own observation, and two identical files in two
        folders still get two rows. This only makes "what did the
        analyzers say about this content?" answerable. Bulk-looked-up
        once per batch rather than per row -- the R4 profiling lesson.
        """
        ids = [e["file_observation_id"] for e in prepared
               if e["file_observation_id"] is not None]
        if not ids:
            return
        found = {}
        unique_ids = list(dict.fromkeys(ids))
        for start in range(0, len(unique_ids), IN_CHUNK):
            chunk = unique_ids[start:start + IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            for row in self.conn.execute(
                    "SELECT file_observation_id, content_id FROM hash_measurement "
                    "WHERE content_id IS NOT NULL AND file_observation_id IN (%s) "
                    "ORDER BY hash_measurement_id" % placeholders, chunk):
                found[row["file_observation_id"]] = row["content_id"]
        for entry in prepared:
            entry["content_id"] = found.get(entry["file_observation_id"])

    def _write_results(self, analyzer_run_id, prepared):
        if not prepared:
            return
        self.conn.executemany(
            "INSERT OR IGNORE INTO analyzer_result ("
            "  project_id, analyzer_run_id, file_observation_id, content_id, status,"
            "  legacy_db_id, unmatched_path, title, author, content_created_reported,"
            "  width_px, height_px, duration_seconds, word_count, char_count,"
            "  detail_json, analyzed_utc, source_artifact, error_kind, error_message"
            ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(analyzer_run_id, e["file_observation_id"], e["content_id"], e["status"],
              e["legacy_db_id"], e["unmatched_path"], e["title"], e["author"],
              e["content_created_reported"], e["width_px"], e["height_px"],
              e["duration_seconds"], e["word_count"], e["char_count"],
              e["detail_json"], e["analyzed_utc"], e["source_artifact"],
              e["error_kind"], e["error_message"]) for e in prepared])

    def _result_ids_by_path(self, analyzer_run_id, prepared_paths):
        r"""absolute path -> analyzer_result_id, in ONE query.

        The child tables (archive_member, extracted_content) need the
        parent id, and executemany does not return them. Re-reading the
        run's rows once is cheap; a SELECT per child row is the exact
        pathological pattern R4's profiling removed.
        """
        by_observation = {}
        by_unmatched = {}
        for row in self.conn.execute(
                "SELECT analyzer_result_id, file_observation_id, unmatched_path "
                "FROM analyzer_result WHERE analyzer_run_id = ?", (analyzer_run_id,)):
            if row["file_observation_id"] is not None:
                by_observation[row["file_observation_id"]] = row["analyzer_result_id"]
            elif row["unmatched_path"]:
                by_unmatched[row["unmatched_path"]] = row["analyzer_result_id"]

        mapping = {}
        for absolute in prepared_paths:
            observation = self.resolver.cache.get(absolute)
            if observation is not None:
                result_id = by_observation.get(observation["file_observation_id"])
            else:
                result_id = by_unmatched.get(absolute[:1000])
            if result_id is not None:
                mapping[absolute] = result_id
        return mapping

    # -- one analyzer -------------------------------------------------

    def _flush(self, spec, analyzer_run_id, rows, counts, now, all_paths):
        prepared = self._prepare_rows(spec, rows, spec.artifact, now)
        self._write_results(analyzer_run_id, prepared)
        self.conn.commit()
        for entry in prepared:
            counts["applicable"] += 1
            if entry["status"] == "analyzed":
                counts["succeeded"] += 1
            elif entry["status"] == "error":
                counts["failed"] += 1
            elif entry["status"] in ("skipped_cloud_only", "not_processed"):
                counts["skipped"] += 1
            elif entry["status"] == "unmatched":
                counts["unmatched"] += 1
            if entry["status"] != "unmatched":
                counts["ingested"] += 1
            if entry["_db_id_mismatch"]:
                counts["db_id_mismatch"] += 1
            if entry["_path"]:
                all_paths.append(entry["_path"])

    # -- archive members ----------------------------------------------



# ---------------------------------------------------------------------------
# Read helpers -- the questions this schema exists to answer
# ---------------------------------------------------------------------------

def analyzer_counts(conn, run_id=None):
    """analyzer_key -> {results, analyzed, errors, skipped, unmatched}."""
    where = "WHERE ar.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    out = {}
    for row in conn.execute(
            "SELECT a.analyzer_key AS k, "
            "  COUNT(r.analyzer_result_id) AS results, "
            "  SUM(CASE WHEN r.status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed, "
            "  SUM(CASE WHEN r.status = 'error' THEN 1 ELSE 0 END) AS errors, "
            "  SUM(CASE WHEN r.status IN ('skipped_cloud_only','not_processed') "
            "           THEN 1 ELSE 0 END) AS skipped, "
            "  SUM(CASE WHEN r.status = 'unmatched' THEN 1 ELSE 0 END) AS unmatched "
            "FROM analyzer a "
            "JOIN analyzer_run ar ON ar.analyzer_id = a.analyzer_id "
            "LEFT JOIN analyzer_result r ON r.analyzer_run_id = ar.analyzer_run_id "
            + where + " GROUP BY a.analyzer_key", params):
        out[row["k"]] = {"results": row["results"] or 0,
                         "analyzed": row["analyzed"] or 0,
                         "errors": row["errors"] or 0,
                         "skipped": row["skipped"] or 0,
                         "unmatched": row["unmatched"] or 0}
    return out


def analyzer_run_summary(conn, run_id=None):
    """One row per analyzer_run, with both statuses kept apart."""
    where = "WHERE ar.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    return [dict(row) for row in conn.execute(
        "SELECT a.analyzer_key, a.label, ar.analyzer_run_id, ar.run_id, "
        "       ar.run_stage_id, ar.ingest_run_stage_id, ar.analysis_status, "
        "       ar.ingest_status, ar.analyzer_version, ar.engine_version, "
        "       ar.applicable_count, ar.succeeded_count, ar.failed_count, "
        "       ar.skipped_count, ar.ingested_count, ar.unmatched_count, "
        "       ar.duration_ms, ar.notes "
        "FROM analyzer_run ar JOIN analyzer a ON a.analyzer_id = ar.analyzer_id "
        + where + " ORDER BY a.sort_order, ar.analyzer_run_id", params)]


def results_for_observation(conn, file_observation_id):
    """Everything every analyzer said about one observed file."""
    return [dict(row) for row in conn.execute(
        "SELECT a.analyzer_key, r.* FROM analyzer_result r "
        "JOIN analyzer_run ar ON ar.analyzer_run_id = r.analyzer_run_id "
        "JOIN analyzer a ON a.analyzer_id = ar.analyzer_id "
        "WHERE r.file_observation_id = ? ORDER BY a.sort_order",
        (file_observation_id,))]


def results_history_for_path(conn, file_path_id, analyzer_key=None):
    r"""Every analyzer result ever recorded for one location, newest first.

    This is the query the "was this file analyzed before, and did its
    analysis change?" requirement exists for. It walks
    file_path -> file_observation -> analyzer_result, so a file analyzed
    across five runs returns five rows rather than one overwritten one.
    """
    clause = "AND a.analyzer_key = ?" if analyzer_key else ""
    params = [file_path_id] + ([analyzer_key] if analyzer_key else [])
    return [dict(row) for row in conn.execute(
        "SELECT a.analyzer_key, ar.run_id, ar.analyzer_version, "
        "       r.analyzer_result_id, r.file_observation_id, r.status, "
        "       r.content_id, r.analyzed_utc, r.detail_json "
        "FROM file_observation o "
        "JOIN analyzer_result r ON r.file_observation_id = o.file_observation_id "
        "JOIN analyzer_run ar ON ar.analyzer_run_id = r.analyzer_run_id "
        "JOIN analyzer a ON a.analyzer_id = ar.analyzer_id "
        "WHERE o.file_path_id = ? " + clause +
        " ORDER BY r.analyzed_utc DESC, r.analyzer_result_id DESC", params)]


def archive_members_for_run(conn, run_id=None):
    """Archive entries, with the archive they came from."""
    where = "WHERE ar.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    return [dict(row) for row in conn.execute(
        "SELECT m.*, r.file_observation_id AS archive_observation_id "
        "FROM archive_member m "
        "JOIN analyzer_result r ON r.analyzer_result_id = m.analyzer_result_id "
        "JOIN analyzer_run ar ON ar.analyzer_run_id = r.analyzer_run_id "
        + where + " ORDER BY m.analyzer_result_id, m.sequence", params)]


def extraction_index(conn, run_id=None):
    """Extracted-text references persisted in SQLite; never the text itself."""
    where = "WHERE ar.run_id = ?" if run_id else ""
    params = (run_id,) if run_id else ()
    return [dict(row) for row in conn.execute(
        "SELECT e.*, r.file_observation_id "
        "FROM extracted_content e "
        "JOIN analyzer_result r ON r.analyzer_result_id = e.analyzer_result_id "
        "JOIN analyzer_run ar ON ar.analyzer_run_id = r.analyzer_run_id "
        + where + " ORDER BY e.extracted_content_id", params)]


def analyzer_totals(conn):
    """Whole-project totals, for logging and the verifier."""
    def count(table):
        return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
    return {"analyzer_runs": count("analyzer_run"),
            "results": count("analyzer_result"),
            "archive_members": count("archive_member"),
            "extracted_content": count("extracted_content")}
