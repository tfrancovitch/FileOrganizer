#!/usr/bin/env python3
"""
ArchiveAnalysis.py
Part of: The File Organizer
Version: 1.3.1

Catalogs the contents of .zip/.7z archives for the database-backed
analyzer engine. The engine persists aggregate facts and bounded child-member
rows directly to SQLite. The retired standalone CSV/checkpoint pipeline was
removed in B6.1.

Requires:
    .zip is handled by the standard library (no extra package needed)
    .7z requires: pip install py7zr (optional -- .zip works without it;
        if py7zr is missing, any .7z files encountered are logged as
        individual errors rather than blocking the whole run)

"""

import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import to_long_path

try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

EXTENSIONS = {".zip", ".7z"}

#: How many members of ONE archive get their own row.
#:
#: B6. THE BOUND FOR B5-E.F006.
#:
#: A single 75 MB ZIP with 500,000 entries produced 500,000 database
#: rows, ~358 MB of heap and ~26 seconds of work in B4.5, with no cap,
#: no estimate and no progress granularity -- a file that looks small
#: on disk and is not small to process.
#:
#: 10,000 is not a magic number; it is a judgement that a per-entry
#: listing stops being useful long before it stops being expensive.
#: Above it, B6 keeps the AGGREGATES -- which are what the report
#: actually uses -- and records that the listing is capped, so an
#: incomplete listing is visibly incomplete. --archive-member-cap 0
#: restores unbounded behaviour for anyone who wants it deliberately.
DEFAULT_MEMBER_CAP = 10000

MODE_COMPLETE = "complete"
MODE_CAPPED = "capped"
MODE_SUMMARY = "summary_only"

AGG_CHECKPOINT_FIELDS = ["Key", "EntryCount", "TotalUncompressedSize",
                          "TotalCompressedSize", "CompressionRatioPercent",
                          "AnalysisMode", "EntriesRecorded", "Truncated",
                          "Error"]
CONTENTS_FIELDS = ["ArchiveDB_ID", "ArchivePath", "EntryPath", "EntrySize", "EntryCompressedSize"]


#: Above this entry count, an archive is summarised rather than listed.
#:
#: THE SECOND HALF OF THE B5-E.F006 FIX, AND WHY IT IS NEEDED.
#:
#: Capping our own retention was not sufficient. `zipfile.ZipFile`
#: parses the ENTIRE central directory on open and builds one ZipInfo
#: object per entry before any of our code runs. Measured on a
#: 200,000-entry archive: 121.6 MB inside the standard library, versus
#: 20.6 MB of our own retention. Capping only the second one takes the
#: total from 142 MB to 121 MB -- a real improvement to the database
#: row count, and close to nothing for peak memory.
#:
#: So an archive this large is not opened with ZipFile at all. Its
#: entry count is read from the 22-byte End Of Central Directory
#: record, and it is recorded in `summary_only` mode: counted,
#: attributed, flagged, and not enumerated.
#:
#: This is a REAL LOSS OF DETAIL and it is recorded as one. The
#: alternative is a hidden memory ceiling that depends on what happens
#: to be inside a file that looks small on disk, which is what B5-E
#: objected to.
SUMMARY_ONLY_THRESHOLD = 100000

_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_LOCATOR = b"PK\x06\x07"
_ZIP64_EOCD_RECORD = b"PK\x06\x06"


def peek_zip_entry_count(path):
    r"""Entry count from the end-of-central-directory records, cheaply.

    Returns an int, or None if the count could not be read without
    parsing the directory itself -- in which case the caller opens the
    archive normally. Guessing would be worse than not knowing: a wrong
    count would either skip a listing that was affordable or attempt
    one that was not.

    Reads at most ~64 KB from the end of the file, plus one 56-byte
    record if the archive is ZIP64.

    ZIP64 IS THE CASE THAT MATTERS, so it is handled rather than
    declined. The classic EOCD stores the entry count in two bytes, so
    any archive with more than 65,535 entries writes a 0xFFFF
    placeholder there and puts the real count in a ZIP64 record. An
    implementation that reads only the classic record therefore fails
    on precisely the archives large enough to be a problem -- which is
    what a first draft of this function did, and what the 200,000-entry
    fixture caught.
    """
    try:
        size = os.path.getsize(path)
        with open(to_long_path(path), "rb") as handle:
            window = min(size, 65557 + 64)
            handle.seek(size - window)
            tail = handle.read(window)

            index = tail.rfind(_EOCD_SIGNATURE)
            if index < 0 or index + 22 > len(tail):
                return None
            count = int.from_bytes(tail[index + 10:index + 12], "little")
            if count != 0xFFFF:
                return count

            # ZIP64. The locator gives the offset of the real record.
            #
            # 0xFFFF IS AMBIGUOUS. It is the ZIP64 placeholder, and it
            # is also the literal count of an archive holding exactly
            # 65,535 entries. The two are told apart by whether a ZIP64
            # locator is present, not by the value alone -- a fixture at
            # exactly 65,535 caught an earlier version treating the
            # literal as a placeholder and giving up on a count it
            # already had.
            locator = tail.rfind(_ZIP64_EOCD_LOCATOR)
            if locator < 0 or locator + 20 > len(tail):
                return 0xFFFF
            offset = int.from_bytes(tail[locator + 8:locator + 16], "little")
            if offset <= 0 or offset >= size:
                return None
            handle.seek(offset)
            record = handle.read(56)
            if len(record) < 40 or not record.startswith(_ZIP64_EOCD_RECORD):
                return None
            return int.from_bytes(record[32:40], "little")
    except OSError:
        return None


def iter_zip_entries(path):
    """Yield one dict per non-directory ZIP entry. Streams."""
    with zipfile.ZipFile(to_long_path(path)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            yield {
                "EntryPath": info.filename,
                "EntrySize": info.file_size,
                "EntryCompressedSize": info.compress_size,
            }


def iter_7z_entries(path):
    """Yield one dict per non-directory 7z entry. Streams."""
    if not HAS_PY7ZR:
        raise RuntimeError("py7zr not installed -- run: pip install py7zr")
    with py7zr.SevenZipFile(to_long_path(path), mode="r") as z:
        for info in z.list():
            if info.is_directory:
                continue
            yield {
                "EntryPath": info.filename,
                "EntrySize": info.uncompressed or 0,
                "EntryCompressedSize": info.compressed or 0,
            }


def analyze_archive(path, member_cap=DEFAULT_MEMBER_CAP,
                    summary_threshold=SUMMARY_ONLY_THRESHOLD):
    r"""Aggregate an archive, retaining at most `member_cap` member rows.

    B6. THE FIX FOR B5-E.F006.

    Returns (aggregates, retained_entries).

    THE AGGREGATES ARE ALWAYS COMPLETE. Every entry is counted and
    every byte is summed, however many there are, because that is
    cheap -- an integer per entry, not a dict per entry. What the cap
    bounds is the DETAIL: how many members get their own row.

    That distinction is the whole design. B4.5's problem was not that
    it looked at 500,000 entries; it is that it kept a dict for each of
    them and then wrote 500,000 database rows. Counting them costs
    nothing. Remembering them costs 358 MB.

    So a capped archive still reports a truthful EntryCount, truthful
    totals and a truthful compression ratio. It reports fewer member
    rows, and it says so -- `AnalysisMode` and `Truncated` are written
    into the artifact and into `archive_summary`, so a consumer that
    needs a complete listing can detect that it does not have one.

    member_cap=0 means unbounded, which is B4.5's behaviour, available
    on request rather than by default.
    """
    ext = Path(path).suffix.lower()

    # Pre-flight. An archive with more entries than we would ever list
    # is summarised WITHOUT opening it -- see SUMMARY_ONLY_THRESHOLD.
    if ext == ".zip" and summary_threshold > 0:
        peeked = peek_zip_entry_count(path)
        if peeked is not None and peeked > summary_threshold:
            return {
                "EntryCount": str(peeked),
                # Byte totals require the central directory, which is
                # the thing we are declining to parse. Reporting 0
                # would be a false total; empty is the honest answer to
                # a question we did not ask.
                "TotalUncompressedSize": "",
                "TotalCompressedSize": "",
                "CompressionRatioPercent": "",
                "AnalysisMode": MODE_SUMMARY,
                "EntriesRecorded": "0",
                "Truncated": "True",
            }, []

    if ext == ".zip":
        source = iter_zip_entries(path)
    elif ext == ".7z":
        source = iter_7z_entries(path)
    else:
        raise ValueError(f"Unsupported archive type: {ext}")

    retained = []
    total_entries = 0
    total_uncompressed = 0
    total_compressed = 0

    for entry in source:
        total_entries += 1
        total_uncompressed += entry["EntrySize"]
        total_compressed += entry["EntryCompressedSize"]
        if member_cap <= 0 or len(retained) < member_cap:
            retained.append(entry)

    ratio = ((1 - (total_compressed / total_uncompressed)) * 100
             if total_uncompressed > 0 else 0)
    truncated = total_entries > len(retained)
    mode = MODE_COMPLETE if not truncated else MODE_CAPPED

    agg = {
        "EntryCount": str(total_entries),
        "TotalUncompressedSize": str(total_uncompressed),
        "TotalCompressedSize": str(total_compressed),
        "CompressionRatioPercent": f"{ratio:.1f}",
        "AnalysisMode": mode,
        "EntriesRecorded": str(len(retained)),
        "Truncated": "True" if truncated else "False",
    }
    return agg, retained
