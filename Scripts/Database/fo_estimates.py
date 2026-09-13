#!/usr/bin/env python3
r"""
fo_estimates.py
===================================================================
PRODUCTION CODE
The File Organizer -- Phase 1 Release Candidate
Module version: 1.0.0   Requires schema version: 5
===================================================================

Calibrates the Duplicate Run and Full Run time estimates shown on the
Choose Run Type screen.

WHAT IT DOES

Reads a small real sample of this run's actual files, measures how
fast they hash, applies a deliberately pessimistic safety factor, and
divides the work still to be done by that. The estimate is meant to be
an over-estimate: a run that finishes early is a pleasant surprise, a
run that overruns its estimate is a broken promise.

WHY THIS IS PYTHON

TimeEstimates.ps1 was never replaced by B2-B4 because it is not an
engine -- but its content is arithmetic, a bounded read loop, and a
settings.json write. None of that is PowerShell-specific, and it was
the last runtime script keeping Common.ps1 alive.

It also took its file list from PreliminaryInventory.csv. The database
is the authoritative inventory. B6.1 reads the CURRENT file_state projection,
not only file_observation rows written by the latest scan. In history.mode=changes
an unchanged file intentionally gets no new observation row, so using scan-local
history here would silently undercount repeat-run work.

BEHAVIOUR PRESERVED EXACTLY

  * up to 15 sample files, capped at 50 MB of reading;
  * cloud-only and zero-byte files are skipped as sample candidates --
    a cloud file would measure the network and might trigger a
    download, and a 0-byte file tells you nothing about throughput;
  * an unreadable sample file is skipped, not fatal;
  * no usable sample at all falls back to a flat conservative
    5 MB/s rather than dividing by zero;
  * safety factor 0.4 on a network target, 0.6 otherwise;
  * the same five settings.json fields are written.

SOURCE SAFETY

Sampling READS file contents. That is a read and nothing else -- no
write, rename, move, delete or attribute change. Reading may cause
Windows to update LastAccessTime; that is the operating system's
doing, and this module does not attempt to restore it, because writing
a timestamp back would itself be the source mutation Phase 1 forbids.
"""

import hashlib
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import win_meta                                                 # noqa: E402


SAMPLE_FILE_COUNT = 15
SAMPLE_BYTE_CAP = 50 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

#: Assume real throughput will be slower than the sample suggested.
#: Network targets get the harsher factor because a quiet moment on a
#: share is far less representative than a quiet moment on a local disk.
SAFETY_FACTOR_NETWORK = 0.4
SAFETY_FACTOR_LOCAL = 0.6

#: Used only when no file could be sampled at all.
FALLBACK_BYTES_PER_SEC = 5 * 1024 * 1024

SETTINGS_FIELDS = ("DuplicateRunEstimateText", "FullRunEstimateText",
                   "DuplicateRunEstimateSeconds", "FullRunEstimateSeconds",
                   "CalibrationThroughputBytesPerSec",
                   "CalibrationSafetyFactor", "CalibrationSampleFileCount")


def format_duration(seconds):
    r"""TimeEstimates.ps1's Format-Duration, reproduced exactly.

    Taken from the script rather than guessed: it CEILS to whole
    minutes, and its wording is "under a minute" / "~N min" /
    "~N hr" / "~N hr M min". These strings go straight onto the Choose
    Run Type screen, so a paraphrase would be a visible UI change.
    """
    seconds = float(seconds or 0)
    if seconds < 60:
        return "under a minute"
    minutes = int(math.ceil(seconds / 60.0))
    if minutes < 60:
        return "~%d min" % minutes
    hours = minutes // 60
    remainder = minutes % 60
    if remainder == 0:
        return "~%d hr" % hours
    return "~%d hr %d min" % (hours, remainder)


def _open_path(path):
    r"""The path handed to the OS. \\?\ applied only at this boundary."""
    if sys.platform == "win32":
        return win_meta.to_extended_path(path)
    text = str(path)
    if "\\" in text and not os.path.exists(text):
        return text.replace("\\", "/")
    return text


def measure_throughput(candidates, sample_file_count=SAMPLE_FILE_COUNT,
                       byte_cap=SAMPLE_BYTE_CAP):
    r"""Hash a bounded sample and return (bytes/sec, files used, bytes read).

    `candidates` is an iterable of (path, size) already filtered to
    local, non-empty files. Reading stops at whichever limit comes
    first, so calibration costs the same whether the project holds
    thumbnails or disk images.
    """
    bytes_read = 0
    files_used = 0
    started = time.perf_counter()

    for path, _size in candidates:
        if bytes_read >= byte_cap or files_used >= sample_file_count:
            break
        try:
            digest = hashlib.sha256()
            with open(_open_path(path), "rb") as handle:
                while True:
                    block = handle.read(CHUNK_BYTES)
                    if not block:
                        break
                    digest.update(block)
                    bytes_read += len(block)
        except Exception:                                       # noqa: BLE001
            # One unreadable sample file must not break calibration.
            continue
        files_used += 1

    elapsed = time.perf_counter() - started
    if files_used == 0 or bytes_read == 0 or elapsed <= 0:
        return None, 0, 0
    return bytes_read / elapsed, files_used, bytes_read


def sample_candidates(conn, inventory_scan_ids, limit=200):
    r"""Local, non-empty CURRENT files verified by the selected scans.

    B6.1 reads file_state because unchanged files do not receive duplicate
    history observations. current_scan_id proves this scan reverified them;
    current_legacy_db_id provides deterministic within-scan ordering.
    """
    if not inventory_scan_ids:
        return []
    placeholders = ",".join("?" * len(inventory_scan_ids))
    rows = conn.execute(
        "SELECT fs.size_bytes, fp.relative_path, sr.root_path "
        "FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
        "WHERE fs.current_scan_id IN (%s) AND fs.state='present' "
        " AND fs.size_bytes>0 AND COALESCE(fs.is_offline_or_cloud,0)=0 "
        "ORDER BY sr.root_ordinal, fs.current_legacy_db_id, fp.path_sort_key "
        "LIMIT ?" % placeholders,
        list(inventory_scan_ids) + [int(limit)]).fetchall()
    out=[]
    for row in rows:
        root=(row["root_path"] or "").rstrip("\\")
        relative=(row["relative_path"] or "").lstrip("\\")
        if os.name == "nt":
            full=(root+"\\"+relative) if relative else root
        else:
            full=os.path.join(root, relative.replace("\\", os.sep)) if relative else root
        out.append((full, row["size_bytes"] or 0))
    return out


def _candidate_size_predicate(placeholders):
    return (
        "fs.size_bytes IN (SELECT fs2.size_bytes FROM file_state fs2 "
        "WHERE fs2.current_scan_id IN (%s) AND fs2.state='present' "
        "GROUP BY fs2.size_bytes HAVING COUNT(*)>1)" % placeholders)


def inventory_totals(conn, inventory_scan_ids):
    """(total bytes, size-candidate bytes) for the CURRENT scan scope.

    Candidate bytes are derived directly from the current size distribution,
    which is exactly the Duplicate Run's first screening rule. This works both
    before hashing and on repeat unchanged scans; stale historical hash rows are
    deliberately irrelevant to an estimate of present work.
    """
    if not inventory_scan_ids:
        return 0,0
    placeholders=",".join("?"*len(inventory_scan_ids))
    params=list(inventory_scan_ids)
    total=conn.execute(
        "SELECT COALESCE(SUM(size_bytes),0) FROM file_state "
        "WHERE current_scan_id IN (%s) AND state='present'" % placeholders,
        params).fetchone()[0]
    predicate=_candidate_size_predicate(placeholders)
    candidate=conn.execute(
        "SELECT COALESCE(SUM(fs.size_bytes),0) FROM file_state fs "
        "WHERE fs.current_scan_id IN (%s) AND fs.state='present' AND %s"
        % (placeholders,predicate), params+params).fetchone()[0]
    return int(total or 0), int(candidate or 0)


def inventory_file_counts(conn, inventory_scan_ids):
    """(total files, size-candidate files) for the CURRENT scan scope."""
    if not inventory_scan_ids:
        return 0,0
    placeholders=",".join("?"*len(inventory_scan_ids))
    params=list(inventory_scan_ids)
    total=conn.execute(
        "SELECT COUNT(*) FROM file_state WHERE current_scan_id IN (%s) "
        "AND state='present'" % placeholders, params).fetchone()[0]
    predicate=_candidate_size_predicate(placeholders)
    candidate=conn.execute(
        "SELECT COUNT(*) FROM file_state fs WHERE fs.current_scan_id IN (%s) "
        "AND fs.state='present' AND %s" % (placeholders,predicate),
        params+params).fetchone()[0]
    return int(total or 0), int(candidate or 0)


#: Per-file overhead, in seconds, independent of file size.
#:
#: B6. PART OF THE B5-E.F013 FIX.
#:
#: B4.5 modelled work as bytes divided by throughput. B5-E measured
#: real throughput varying ~3.2x BY FILE SIZE on the same SSD, which a
#: bytes-only model cannot express: 10,000 files of 1 KB and one file
#: of 10 MB are the same number of bytes and nothing like the same
#: amount of work. The difference is per-file cost -- open, stat, seek,
#: close -- and it dominates on small files.
#:
#: Calibrated from the sample rather than assumed where possible; this
#: is the floor used when the sample cannot separate the two.
DEFAULT_PER_FILE_SECONDS = 0.0004

#: Rough per-file cost of the stages B4.5's estimate simply omitted.
#: B5-E.F013's complaint is not that these numbers are imprecise -- an
#: estimate is allowed to be imprecise. It is that the estimate
#: EXCLUDED whole stages while presenting itself as the cost of the
#: run, which makes it wrong in a direction the user cannot see.
#: Naming them makes the estimate's scope inspectable.
PERSIST_SECONDS_PER_FILE = 0.00006
EXPORT_SECONDS_PER_FILE = 0.00004


def stage_costs(file_count, candidate_count, analyzer_count=0):
    r"""Per-file costs of the stages beyond hashing, in seconds.

    Returned as a breakdown rather than folded into one number, so the
    estimate can say what it is made of. An estimate the user cannot
    take apart is one they cannot tell is wrong.
    """
    persist = file_count * PERSIST_SECONDS_PER_FILE
    export = file_count * EXPORT_SECONDS_PER_FILE
    # Analyzers run over applicable files, which is not known until
    # selection; file_count is the upper bound and is used as such.
    analyzers = file_count * analyzer_count * PERSIST_SECONDS_PER_FILE
    return {"persist_seconds": persist, "export_seconds": export,
            "analyzer_seconds": analyzers,
            "total_seconds": persist + export + analyzers}


def calibrate(conn, inventory_scan_ids, drive_type=None, analyzer_count=0):
    r"""Measure and compute. Returns a dict of settings.json values.

    B6 MODELS FILES AS WELL AS BYTES, AND NAMES WHAT IT EXCLUDES.

    The estimate is now

        max(bytes / rate, files * per_file) + persistence + export

    rather than `bytes / rate` alone. `max` rather than a sum because
    the two costs overlap -- reading a small file is mostly per-file
    cost, reading a large one is mostly bytes -- and adding them would
    double-count the common case.

    The over-estimate discipline from B4.5 is unchanged: the safety
    factor still applies, because a run that finishes early is a
    pleasant surprise and a run that overruns is a broken promise.
    """
    measured, files_used, bytes_read = measure_throughput(
        sample_candidates(conn, inventory_scan_ids))

    fell_back = measured is None
    if fell_back:
        measured = FALLBACK_BYTES_PER_SEC

    safety = (SAFETY_FACTOR_NETWORK if drive_type == "Network"
              else SAFETY_FACTOR_LOCAL)
    safe_rate = measured * safety

    total_bytes, candidate_bytes = inventory_totals(conn, inventory_scan_ids)
    total_files, candidate_files = inventory_file_counts(conn, inventory_scan_ids)

    per_file = DEFAULT_PER_FILE_SECONDS / safety if safety > 0 else DEFAULT_PER_FILE_SECONDS

    def hashing_seconds(byte_count, file_count):
        by_bytes = byte_count / safe_rate if safe_rate > 0 else 0
        by_files = file_count * per_file
        return max(by_bytes, by_files)

    duplicate_stages = stage_costs(candidate_files, candidate_files, analyzer_count)
    full_stages = stage_costs(total_files, total_files, analyzer_count)

    duplicate_seconds = (hashing_seconds(candidate_bytes, candidate_files)
                         + duplicate_stages["total_seconds"])
    full_seconds = (hashing_seconds(total_bytes, total_files)
                    + full_stages["total_seconds"])

    return {
        "DuplicateRunEstimateText": format_duration(duplicate_seconds),
        "FullRunEstimateText": format_duration(full_seconds),
        # The seconds behind the texts, so a caller can decide whether a run
        # is long enough to warrant a caution before it starts.
        "DuplicateRunEstimateSeconds": int(round(duplicate_seconds)),
        "FullRunEstimateSeconds": int(round(full_seconds)),
        "CalibrationThroughputBytesPerSec": int(measured),
        "CalibrationSafetyFactor": safety,
        "CalibrationSampleFileCount": files_used,
        # Not written to settings.json -- returned for the console line.
        "_bytes_read": bytes_read,
        "_fell_back": fell_back,
        "_safe_rate": safe_rate,
        "_total_bytes": total_bytes,
        "_candidate_bytes": candidate_bytes,
        "_total_files": total_files,
        "_candidate_files": candidate_files,
        "_per_file_seconds": per_file,
        "_duplicate_stages": duplicate_stages,
        "_full_stages": full_stages,
    }


# ---------------------------------------------------------------------------
# Analysis, extraction and indexing -- Phase 2 build item 4
#
# The placeholder above (file_count * analyzer_count * PERSIST_SECONDS_PER_FILE)
# priced an analyzer run as a database write. It did not model opening a
# PDF, parsing a DOCX, hashing an image perceptually or building an index --
# the stages that actually take the time -- and extraction and indexing had
# no estimate at all. With blocking work, an estimate that leaves out whole
# stages is wrong in a direction the user cannot see (B5-E.F013 again).
#
# The approach mirrors calibrate(): count the applicable files EXACTLY from
# the inventory, run the REAL analyzer on a bounded per-type sample spread
# across the size range, model files as well as bytes, keep the pessimistic
# factor, and return a breakdown a person can take apart.
#
# Sampling reads source files. That is a read and nothing else, and cloud-
# only files are never sampled (axiom 14). Extraction sampling writes its
# artifacts to a temporary folder that is deleted afterwards -- never into
# the project's Runs folder, which is evidence.
# ---------------------------------------------------------------------------

#: Files sampled per analyzer, and the most bytes a sample may read.
ANALYZER_SAMPLE_FILES = 6
ANALYZER_SAMPLE_BYTE_CAP = 24 * 1024 * 1024

#: Floors for an analyzer whose sample could not be measured at all --
#: deliberately pessimistic, and labelled as guesses in the breakdown.
FALLBACK_ANALYZER_SECONDS_PER_FILE = 0.05
FALLBACK_ANALYZER_BYTES_PER_SEC = 2 * 1024 * 1024

#: Indexing tracks text volume, not file count. Measured 2026-09-10: index
#: build ~1.6 ms per text on short documents; the per-byte figure covers
#: the FTS5 tokeniser on long ones. Both pessimistic by the local factor.
INDEX_SECONDS_PER_TEXT = 0.0016
INDEX_BYTES_PER_SEC = 20 * 1024 * 1024


def present_files(conn):
    r"""Every CURRENT present file as (path, size, cloud, extension), by size.

    Read once and filtered per analyzer, so estimating nine analyzers costs
    one pass over the inventory rather than nine.
    """
    out = []
    for row in conn.execute(
            "SELECT fs.size_bytes, COALESCE(fs.is_offline_or_cloud,0) AS cloud, "
            "       LOWER(COALESCE(fp.extension_key,'')) AS ext, fp.relative_path, sr.root_path "
            "FROM file_state fs "
            "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
            "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
            "WHERE fs.state='present' "
            "ORDER BY fs.size_bytes"):
        ext = row["ext"]
        if ext and not ext.startswith("."):
            ext = "." + ext
        root = (row["root_path"] or "").rstrip("\\")
        relative = (row["relative_path"] or "").lstrip("\\")
        if os.name == "nt":
            full = (root + "\\" + relative) if relative else root
        else:
            full = os.path.join(root, relative.replace("\\", os.sep)) if relative else root
        out.append((full, int(row["size_bytes"] or 0), bool(row["cloud"]), ext))
    return out


def applicable_files(conn, key, files=None):
    r"""Every CURRENT present file an analyzer would process, plus totals.

    Returns (rows, count, bytes). Applicability comes from the engine's own
    adapter -- its declared extension set and exclusions -- so this cannot
    drift from what a run would do. The rows carry (path, size, cloud) so a
    sample can be drawn without a second query.
    """
    import fo_analyzer_engine
    adapter = fo_analyzer_engine.ADAPTER_BY_KEY.get(key)
    if adapter is None:
        return [], 0, 0
    extensions = adapter.extensions()
    exclusions = adapter.exclusions()
    if files is None:
        files = present_files(conn)
    rows = []
    total_bytes = 0
    for full, size, cloud, ext in files:
        if ext in exclusions or ext not in extensions:
            continue
        rows.append((full, size, cloud))
        total_bytes += size
    return rows, len(rows), total_bytes


def spread_sample(rows, count=ANALYZER_SAMPLE_FILES):
    r"""Up to `count` local, non-empty files at evenly spaced size ranks.

    The rows arrive ordered by size. Taking the first N would sample the
    smallest files only, and per-file cost dominates there; spreading the
    picks across the size range lets the two costs be separated the way
    calibrate() separates them for hashing.
    """
    eligible = [r for r in rows if r[1] > 0 and not r[2]]
    if not eligible:
        return []
    if len(eligible) <= count:
        return list(eligible)
    step = len(eligible) / float(count)
    picks = []
    for i in range(count):
        picks.append(eligible[int(i * step + step / 2.0)])
    return picks


def measure_analyzer(key, sample, byte_cap=ANALYZER_SAMPLE_BYTE_CAP):
    r"""Run the real analyzer over the sample. Returns a measurement dict.

    {files_used, bytes_read, elapsed, text_bytes, error} -- error carries a
    reason when the analyzer cannot run at all (missing package, import
    failure), which is itself a fact the estimate must report: a run that
    would fail at once takes no time and does no work.
    """
    import shutil
    import tempfile
    import fo_analyzer_engine

    out = {"files_used": 0, "bytes_read": 0, "elapsed": 0.0, "text_bytes": 0,
           "pages_read": 0, "error": None}
    adapter = fo_analyzer_engine.ADAPTER_BY_KEY.get(key)
    if adapter is None:
        out["error"] = "unknown analyzer"
        return out
    missing = fo_analyzer_engine.missing_dependencies(key)
    if missing:
        out["error"] = ("missing Python package(s): %s. Install with: pip install %s"
                        % (", ".join(missing), fo_analyzer_engine.dependency_hint(key)))
        return out
    if not sample:
        return out

    scratch = None
    context = {"hash_size": fo_analyzer_engine.DEFAULT_IMAGE_HASH_SIZE}
    if key == "content_extraction":
        # The extraction closure writes artifacts. They go to a folder that
        # is deleted below -- an estimate must not leave evidence behind.
        scratch = tempfile.mkdtemp(prefix="fo_estimate_")
        context["extract_folder"] = scratch
    try:
        try:
            analyze = adapter.analyze_fn(context)
        except Exception as exc:                                # noqa: BLE001
            out["error"] = "%s: %s" % (type(exc).__name__, exc)
            return out
        # Warm up, untimed. The first SUCCESSFUL call pays for lazy library
        # setup -- Pillow's plugin registration, imagehash importing scipy on
        # its first perceptual hash -- which is a once-per-process cost, not
        # a per-file one. Measured here at ~540 ms on the first image and
        # ~1.5 ms on every later one; left in the timing it would inflate a
        # 10,000-image estimate by hours. A file that fails to open never
        # reaches the lazy paths, so the warm-up keeps going until one works.
        for path, _size, _cloud in sample[:4]:
            try:
                analyze(fo_analyzer_engine.openable_path(path))
                break
            except Exception:                                   # noqa: BLE001
                continue
        started = time.perf_counter()
        for path, size, _cloud in sample:
            if out["bytes_read"] >= byte_cap:
                break
            try:
                payload = analyze(fo_analyzer_engine.openable_path(path))
            except Exception:                                   # noqa: BLE001
                # An unreadable or malformed sample file is a fact about that
                # file, not about throughput. It costs the same time as a
                # readable one, which is why it still counts as used.
                payload = None
            out["files_used"] += 1
            out["bytes_read"] += size
            if key == "content_extraction" and isinstance(payload, dict):
                try:
                    out["text_bytes"] += int(payload.get("CharCount") or 0)
                except (TypeError, ValueError):
                    pass
            if key == "content_extraction" and path.lower().endswith(".pdf"):
                out["pages_read"] += _pdf_page_count(path)
        out["elapsed"] = time.perf_counter() - started
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)
    return out


def _pessimistic_seconds(count, total_bytes, measurement, safety):
    r"""max(bytes / rate, files * per_file), both from the sample, then the
    safety factor -- the same shape calibrate() uses for hashing, and for
    the same reason: a small file is mostly per-file cost, a large one is
    mostly bytes, and adding the two double-counts the common case."""
    files_used = measurement["files_used"]
    elapsed = measurement["elapsed"]
    if files_used and elapsed > 0:
        per_file = elapsed / files_used
        rate = (measurement["bytes_read"] / elapsed) if measurement["bytes_read"] else None
        guessed = False
    else:
        per_file = FALLBACK_ANALYZER_SECONDS_PER_FILE
        rate = FALLBACK_ANALYZER_BYTES_PER_SEC
        guessed = True
    by_files = count * per_file
    by_bytes = (total_bytes / rate) if rate else 0.0
    work = max(by_files, by_bytes) / safety if safety > 0 else max(by_files, by_bytes)
    overhead = count * (PERSIST_SECONDS_PER_FILE + EXPORT_SECONDS_PER_FILE)
    return {"per_file_seconds": per_file, "bytes_per_sec": rate, "guessed": guessed,
            "by_files_seconds": by_files, "by_bytes_seconds": by_bytes,
            "overhead_seconds": overhead, "seconds": work + overhead}


def _pdf_page_count(path):
    r"""Pages in one PDF, counted in milliseconds; 0 when it cannot be opened."""
    import fo_analyzer_engine
    long_path = fo_analyzer_engine.openable_path(path)
    try:
        import pypdfium2 as pdfium
    except ImportError:
        pdfium = None
    if pdfium is not None:
        try:
            doc = pdfium.PdfDocument(long_path)
            try:
                return len(doc)
            finally:
                doc.close()
        except Exception:                                       # noqa: BLE001
            return 0            # a PDF PDFium cannot open has no countable pages
    try:
        from pypdf import PdfReader
        return len(PdfReader(long_path).pages)
    except Exception:                                           # noqa: BLE001
        return 0


def _pdf_page_totals(conn):
    r"""(PDF files with a page count recorded by the PDF analyzer, their pages).

    The count is of CURRENT, present, local PDFs whose newest PDF-analysis
    result carries PageCount -- what the summary would call analysed.
    """
    try:
        row = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(pages), 0) FROM (
                SELECT (SELECT CAST(json_extract(ar.detail_json, '$.PageCount') AS INTEGER)
                          FROM analyzer_result ar
                          JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id
                          JOIN analyzer a ON a.analyzer_id = rr.analyzer_id
                         WHERE ar.file_observation_id = fs.current_observation_id
                           AND a.analyzer_key = 'pdf' AND ar.status = 'analyzed'
                         ORDER BY ar.analyzed_utc DESC, ar.analyzer_result_id DESC LIMIT 1) AS pages
                  FROM file_state fs
                  JOIN file_path fp ON fp.file_path_id = fs.file_path_id
                 WHERE fs.state = 'present' AND COALESCE(fs.is_offline_or_cloud, 0) = 0
                   AND LOWER(COALESCE(fp.extension_key, '')) IN ('.pdf', 'pdf'))
             WHERE pages IS NOT NULL""").fetchone()
        return int(row[0] or 0), int(row[1] or 0)
    except Exception:                                           # noqa: BLE001
        return 0, 0


def _estimate_extraction(conn, rows, safety, sample_files):
    r"""Text extraction, in two parts: PDFs by PAGE, everything else by file
    and by byte.

    A PDF's extraction time follows its page count, not its size: a 150 MB
    art book and a 1 MB brief with the same number of pages take about the
    same time, because the reader never decodes the pictures. Extrapolating
    the first real corpus by bytes put a ~2 h run at ~40 h. So the PDF half
    samples PDFs, measures seconds per page, and multiplies by the pages the
    PDF analyzer has already counted (a PDF it has not seen is given the
    sample's mean). The other half is the usual max(files x per-file,
    bytes / rate) over the non-PDF documents.
    """
    pdf_rows = [r for r in rows if r[0].lower().endswith(".pdf")]
    other_rows = [r for r in rows if not r[0].lower().endswith(".pdf")]
    empty = {"files_used": 0, "bytes_read": 0, "elapsed": 0.0, "text_bytes": 0,
             "pages_read": 0, "error": None}
    m_pdf = measure_analyzer("content_extraction", spread_sample(pdf_rows, sample_files)) if pdf_rows else dict(empty)
    if m_pdf["error"]:
        return {"error": m_pdf["error"]}
    m_other = measure_analyzer("content_extraction", spread_sample(other_rows, sample_files)) if other_rows else dict(empty)
    if m_other["error"]:
        return {"error": m_other["error"]}

    pdf_local = [r for r in pdf_rows if not r[2]]
    pdf_count = len(pdf_local)
    pdf_bytes = sum(r[1] for r in pdf_local)
    pdf = None
    pdf_seconds = 0.0
    if pdf_count:
        known_files, known_pages = _pdf_page_totals(conn)
        if m_pdf["pages_read"] and m_pdf["elapsed"] > 0 and m_pdf["files_used"]:
            per_page = m_pdf["elapsed"] / float(m_pdf["pages_read"])
            mean_pages = m_pdf["pages_read"] / float(m_pdf["files_used"])
            unknown = max(0, pdf_count - known_files)
            pages = known_pages + unknown * mean_pages
            work = pages * per_page
            work = work / safety if safety > 0 else work
            overhead = pdf_count * (PERSIST_SECONDS_PER_FILE + EXPORT_SECONDS_PER_FILE)
            pdf_seconds = work + overhead
            pdf = {"files": pdf_count, "bytes": pdf_bytes, "pages": int(round(pages)),
                   "pages_counted_files": min(known_files, pdf_count), "seconds_per_page": per_page,
                   "sample": m_pdf, "seconds": pdf_seconds, "guessed": False}
        else:
            model = _pessimistic_seconds(pdf_count, pdf_bytes, m_pdf, safety)
            pdf_seconds = model["seconds"]
            pdf = {"files": pdf_count, "bytes": pdf_bytes, "pages": None, "pages_counted_files": 0,
                   "seconds_per_page": None, "sample": m_pdf, "seconds": pdf_seconds,
                   "guessed": model["guessed"]}

    other_local = [r for r in other_rows if not r[2]]
    other_model = _pessimistic_seconds(len(other_local), sum(r[1] for r in other_local), m_other, safety) \
        if other_local else {"per_file_seconds": None, "bytes_per_sec": None, "guessed": False,
                             "by_files_seconds": 0.0, "by_bytes_seconds": 0.0, "overhead_seconds": 0.0, "seconds": 0.0}

    merged = dict(empty)
    for m in (m_pdf, m_other):
        for k in ("files_used", "bytes_read", "text_bytes", "pages_read"):
            merged[k] += m[k]
        merged["elapsed"] += m["elapsed"]
    return {"error": None, "sample": merged, "pdf": pdf,
            "other": {"files": len(other_local), "bytes": sum(r[1] for r in other_local),
                      "sample": m_other, **other_model},
            "per_file_seconds": other_model["per_file_seconds"],
            "bytes_per_sec": other_model["bytes_per_sec"],
            "guessed": bool(other_model["guessed"] and (pdf is None or pdf["guessed"])),
            "by_files_seconds": other_model["by_files_seconds"],
            "by_bytes_seconds": other_model["by_bytes_seconds"],
            "overhead_seconds": other_model["overhead_seconds"],
            "seconds": pdf_seconds + other_model["seconds"]}


def estimate_analysis(conn, keys, drive_type=None, sample_files=ANALYZER_SAMPLE_FILES):
    r"""Estimate one analysis run, analyzer by analyzer. Returns a breakdown.

    {
      "analyzers": [ {key, label, applicable, bytes, cloud_skipped, sample:{...},
                      per_file_seconds, bytes_per_sec, guessed, seconds, error} ... ],
      "total_seconds", "text", "safety"
    }
    Every count is measured from the inventory; every rate from a sample of
    this project's own files, run through the real analyzer.
    """
    import fo_analyzer_engine
    safety = SAFETY_FACTOR_NETWORK if drive_type == "Network" else SAFETY_FACTOR_LOCAL
    out = {"analyzers": [], "total_seconds": 0.0, "safety": safety}
    files = present_files(conn)
    for key in keys:
        adapter = fo_analyzer_engine.ADAPTER_BY_KEY.get(key)
        label = adapter.label if adapter else key
        rows, count, total_bytes = applicable_files(conn, key, files)
        cloud = sum(1 for r in rows if r[2])
        item = {"key": key, "label": label, "applicable": count, "bytes": total_bytes,
                "cloud_skipped": cloud, "sample": None, "seconds": 0.0, "error": None,
                "guessed": False, "per_file_seconds": None, "bytes_per_sec": None}
        if count == 0:
            out["analyzers"].append(item)
            continue
        if key == "content_extraction":
            model = _estimate_extraction(conn, rows, safety, sample_files)
            if model["error"]:
                item["error"] = model["error"]
                out["analyzers"].append(item)
                continue
            item.update(model)
            out["total_seconds"] += model["seconds"]
            out["analyzers"].append(item)
            continue
        measurement = measure_analyzer(key, spread_sample(rows, sample_files))
        item["sample"] = measurement
        if measurement["error"]:
            item["error"] = measurement["error"]
            out["analyzers"].append(item)
            continue
        model = _pessimistic_seconds(count - cloud, total_bytes, measurement, safety)
        item.update(model)
        out["total_seconds"] += model["seconds"]
        out["analyzers"].append(item)
    out["text"] = format_duration(out["total_seconds"])
    return out


def estimate_indexing(conn, drive_type=None):
    r"""Estimate a text-index build from what extraction actually produced.

    Indexing reads extracted-text artifacts, never source files, so the
    inputs are the extracted_content rows: how many distinct texts, and how
    many bytes of text. Cost is modelled per text and per byte, whichever
    dominates, and made pessimistic.
    """
    safety = SAFETY_FACTOR_NETWORK if drive_type == "Network" else SAFETY_FACTOR_LOCAL
    row = conn.execute(
        "SELECT COUNT(DISTINCT text_sha256), COALESCE(SUM(artifact_bytes),0) "
        "FROM extracted_content WHERE status='extracted' AND artifact_exists=1 "
        "AND text_sha256 IS NOT NULL").fetchone()
    texts = int(row[0] or 0)
    text_bytes = int(row[1] or 0)
    by_texts = texts * INDEX_SECONDS_PER_TEXT
    by_bytes = text_bytes / float(INDEX_BYTES_PER_SEC)
    seconds = max(by_texts, by_bytes) / safety if safety > 0 else max(by_texts, by_bytes)
    return {"texts": texts, "text_bytes": text_bytes, "by_texts_seconds": by_texts,
            "by_bytes_seconds": by_bytes, "seconds": seconds, "safety": safety,
            "text": format_duration(seconds)}


def _mb(value):
    return "%.1f MB" % (float(value or 0) / (1024.0 * 1024.0))


def describe_analysis(breakdown):
    """The estimate, taken apart, for the screen shown before a run starts."""
    lines = []
    runnable = [a for a in breakdown["analyzers"] if a["applicable"] and not a["error"]]
    for a in breakdown["analyzers"]:
        if a["applicable"] == 0:
            lines.append("%-26s no applicable files -- nothing to do" % a["label"])
            continue
        head = "%-26s %s files, %s" % (a["label"], "{:,}".format(a["applicable"]), _mb(a["bytes"]))
        if a["error"]:
            lines.append(head)
            lines.append("    CANNOT RUN: %s" % a["error"])
            continue
        lines.append(head + "   ->  %s" % format_duration(a["seconds"]))
        sample = a["sample"] or {}
        if a.get("pdf") is not None or a.get("other") is not None:
            lines.extend(_describe_extraction(a))
        elif a["guessed"]:
            lines.append("    no file could be sampled; this is a flat guess, not a measurement")
        else:
            lines.append("    sampled %d file(s), %s in %.2f s: %.1f ms/file, %s/s"
                         % (sample["files_used"], _mb(sample["bytes_read"]), sample["elapsed"],
                            a["per_file_seconds"] * 1000.0,
                            _mb(a["bytes_per_sec"]) if a["bytes_per_sec"] else "n/a"))
        if a["cloud_skipped"]:
            lines.append("    %d cloud-only file(s) will be skipped, never opened"
                         % a["cloud_skipped"])
    lines.append("")
    if runnable:
        lines.append("Estimated time: %s" % breakdown["text"])
        lines.append("Measured on this project's own files with the real analyzers, then made")
        lines.append("deliberately pessimistic (factor %s). Includes analysis, database persistence"
                     % breakdown["safety"])
        lines.append("and export. A run that finishes early is a pleasant surprise.")
    else:
        lines.append("Nothing here can run: no applicable files, or a missing dependency.")
    return "\n".join(lines)


def _describe_extraction(a):
    """Extraction's two halves: PDFs by page, other documents by file."""
    lines = []
    pdf = a.get("pdf")
    if pdf:
        if pdf["pages"] is not None:
            s = pdf["sample"]
            counted = ("every one counted by the PDF analyzer" if pdf["pages_counted_files"] >= pdf["files"]
                       else "%s counted by the PDF analyzer, the rest given the sample's mean"
                       % "{:,}".format(pdf["pages_counted_files"]))
            lines.append("    PDFs: %s files, %s pages (%s)   ->  %s"
                         % ("{:,}".format(pdf["files"]), "{:,}".format(pdf["pages"]), counted,
                            format_duration(pdf["seconds"])))
            lines.append("        sampled %d file(s), %s pages in %.2f s: %.1f ms/page"
                         % (s["files_used"], "{:,}".format(s["pages_read"]), s["elapsed"],
                            pdf["seconds_per_page"] * 1000.0))
        else:
            lines.append("    PDFs: %s files   ->  %s (pages could not be counted; estimated by size)"
                         % ("{:,}".format(pdf["files"]), format_duration(pdf["seconds"])))
    other = a.get("other")
    if other and other["files"]:
        s = other["sample"]
        lines.append("    other documents: %s files, %s   ->  %s"
                     % ("{:,}".format(other["files"]), _mb(other["bytes"]), format_duration(other["seconds"])))
        if other["guessed"]:
            lines.append("        no file could be sampled; this is a flat guess, not a measurement")
        else:
            lines.append("        sampled %d file(s), %s in %.2f s: %.1f ms/file, %s/s"
                         % (s["files_used"], _mb(s["bytes_read"]), s["elapsed"],
                            other["per_file_seconds"] * 1000.0,
                            _mb(other["bytes_per_sec"]) if other["bytes_per_sec"] else "n/a"))
    return lines


def describe_indexing(breakdown):
    lines = ["Index text: builds a literal full-text index from the extracted-text"
             " artifacts already in the project. No source file is opened.", ""]
    lines.append("Distinct extracted texts : %s" % "{:,}".format(breakdown["texts"]))
    lines.append("Text to index            : %s" % _mb(breakdown["text_bytes"]))
    lines.append("")
    if breakdown["texts"] == 0:
        lines.append("Nothing has been extracted yet, so there is nothing to index.")
    else:
        lines.append("Estimated time: %s" % breakdown["text"])
        lines.append("Modelled per text and per byte of text (whichever dominates), made")
        lines.append("deliberately pessimistic (factor %s). The index is all-or-nothing: a"
                     % breakdown["safety"])
        lines.append("stopped build leaves nothing behind.")
    return "\n".join(lines)


def console_summary(values):
    """The lines TimeEstimates.ps1 printed, reproduced."""
    megabyte = 1024.0 * 1024.0
    lines = ["", "Calibration complete."]
    if values["_fell_back"]:
        lines.insert(1, "WARNING: No usable local sample for calibration -- "
                        "using a conservative flat estimate.")
    lines.append("  Sample: %d file(s), %.1f MB"
                 % (values["CalibrationSampleFileCount"],
                    values["_bytes_read"] / megabyte))
    lines.append("  Measured throughput : %.1f MB/s"
                 % (values["CalibrationThroughputBytesPerSec"] / megabyte))
    lines.append("  Safety-adjusted     : %.1f MB/s (factor: %s)"
                 % (values["_safe_rate"] / megabyte,
                    values["CalibrationSafetyFactor"]))
    lines.append("  Per-file overhead   : %.2f ms/file"
                 % (values.get("_per_file_seconds", 0) * 1000))
    lines.append("  Files: %s total, %s size-candidates"
                 % (values.get("_total_files", 0),
                    values.get("_candidate_files", 0)))
    lines.append("  Duplicate Run estimate : %s"
                 % values["DuplicateRunEstimateText"])
    lines.append("  Full Run estimate      : %s" % values["FullRunEstimateText"])
    # B5-E.F013: state what the estimate covers. An estimate that
    # silently omits stages is wrong in a direction the user cannot see.
    stages = values.get("_full_stages") or {}
    if stages:
        lines.append("  Estimate includes hashing, database persistence "
                     "and export.")
        lines.append("  It excludes analyzer execution time, which depends "
                     "on which analyzers you select.")
    lines.append("")
    return "\n".join(lines)
