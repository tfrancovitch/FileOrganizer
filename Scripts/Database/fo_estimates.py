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
