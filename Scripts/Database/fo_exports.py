#!/usr/bin/env python3
r"""
fo_exports.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
Requires schema 7 for the complete current-state contract.
===================================================================

Renders derived CSV/report artifacts FROM the project SQLite database.
SQLite is authoritative; exports are outputs and are never an IPC or
persistence input to the supported runtime.

B6.1 has two deliberately separate export surfaces:

1. CANONICAL CURRENT EXPORTS
   Machine-readable current-state artifacts for users/downstream tools.
   They are deterministic, streamed, use the current `file_state`
   projection, and use canonical ISO-8601 timestamps.

2. ALPHA-EQUIVALENCE EXPORTS
   Labelled compatibility artifacts used only when comparing historical
   Alpha/Beta output dialects. They preserve the old PowerShell/Python CSV
   formatting rules where useful for evidence. They are not authority and
   do not drive another stage.

Run-folder scoping remains because one operator-visible run folder may contain
inventory, hashing and analyzer run records. A folder-scoped export with an
unbound run is an error: returning an empty artifact would be false success.

Exports stream rows rather than materialising the entire artifact. Paths stored
or written to outputs are ordinary user paths; Windows extended-path prefixes
exist only at file-open boundaries.
"""

import csv
import datetime
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import win_meta  # noqa: E402


#: Equivalence levels an artifact can target.
DATA = "data"
SCHEMA = "schema"
BYTE = "byte"

#: Report classification, per the R6 brief.
CLASS_CANONICAL = "A-canonical"       # R7 must reproduce faithfully
CLASS_DERIVED = "B-derived"           # regenerable presentation
CLASS_DIAGNOSTIC = "C-diagnostic"     # belongs to the run, not the project


class ExportError(Exception):
    """Raised only when an export cannot be produced at all."""


# ---------------------------------------------------------------------------
# Dialects
# ---------------------------------------------------------------------------

class Dialect(object):
    """How one producer writes a CSV."""

    def __init__(self, key, bom, newline, style):
        self.key = key
        self.bom = bom
        self.newline = newline
        self.style = style          # "quote_all_except_null" | "minimal"

    def write(self, path, columns, rows):
        r"""Stream rows to path in this dialect. Returns (path, row_count).

        B6: STREAMED. THE FIX FOR B5-E.F003.

        B4.5 built the entire artifact in a StringIO, called
        `getvalue()` to get one enormous string, called `.encode()` to
        get an equally enormous bytes object, and then wrote it. At
        1,000,000 files `PreliminaryInventory.csv` reached roughly
        1,922 MB peak RSS, because the query result, the rendered cell
        lists, the joined string and the encoded bytes were all alive
        at the same moment.

        None of that was necessary. A CSV is a sequence of independent
        lines; nothing about writing line 900,000 requires line 1 to
        still be in memory.

        This writes directly to an incrementally-flushed binary handle.
        Peak memory is now one row plus the buffer, whatever the
        artifact's size. `rows` may be any iterable -- and the callers
        in B6 pass generators over a streaming cursor, so the database
        rows are not materialised either.

        `rows` are sequences of values. None means SQL NULL and is
        rendered per the dialect's own rule -- which is the whole reason
        this is not one shared writer.
        """
        newline = self.newline
        count = 0
        with open(path, "wb", buffering=1 << 20) as handle:
            if self.bom:
                handle.write(b"\xef\xbb\xbf")

            if self.style == "minimal":
                # csv.writer needs a text sink. A tiny StringIO that is
                # truncated after every row gives us the module's
                # quoting rules without its output accumulating.
                buffer = io.StringIO()
                writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL,
                                    lineterminator=newline)

                def emit(values):
                    buffer.seek(0)
                    buffer.truncate(0)
                    writer.writerow(values)
                    handle.write(buffer.getvalue().encode("utf-8"))

                emit(list(columns))
                for row in rows:
                    emit(["" if v is None else v for v in row])
                    count += 1
            else:
                handle.write((",".join(
                    '"%s"' % str(c).replace('"', '""') for c in columns)
                    + newline).encode("utf-8"))
                for row in rows:
                    cells = []
                    for value in row:
                        if value is None:
                            cells.append("")            # PowerShell $null
                        else:
                            cells.append('"%s"' % str(value).replace('"', '""'))
                    handle.write((",".join(cells) + newline).encode("utf-8"))
                    count += 1
        return path, count


POWERSHELL = Dialect("powershell", bom=True, newline="\r\n",
                     style="quote_all_except_null")
PYTHON = Dialect("python", bom=False, newline="\r\n", style="minimal")
PLAINTEXT = Dialect("plaintext", bom=False, newline="\r\n", style="minimal")


# ---------------------------------------------------------------------------
# Value rendering
# ---------------------------------------------------------------------------

def render_bool(value):
    """PowerShell renders .NET booleans as True/False."""
    if value is None:
        return None
    return "True" if value else "False"


def render_canonical_timestamp(utc_iso, local_naive=None):
    r"""The canonical, machine-readable timestamp for a CSV cell.

    B6. THE FIX FOR B5-F.F004.

    B4.5 rendered exported timestamps through the operator's Windows
    regional settings. Across eleven tested date patterns, ONE stored
    timestamp produced month-first output, day-first output, or -- under
    common ISO-style locale patterns -- an EMPTY CELL. A machine-readable
    export whose contents depend on a display preference is not
    machine-readable, and one that silently empties is worse than one
    that is merely ambiguous.

    B6's rule, stated once and applied everywhere:

        DATA EXPORTS CARRY ISO-8601 UTC. ALWAYS. NO EXCEPTIONS.
        LOCALE FORMATTING HAPPENS AT THE DISPLAY BOUNDARY ONLY.

    ISO-8601 with an explicit 'Z' is unambiguous in every locale, sorts
    lexicographically in chronological order, and is what every
    spreadsheet, database and analysis tool will accept without being
    told the convention.

    THE PRE-B6 FALLBACK. Rows written before B6 have no UTC value --
    see migration 006's note on why they cannot be reconstructed. For
    those, this emits the local wall-clock value it does have, with a
    ' (local)' suffix. The suffix is deliberate and slightly ugly: a
    consumer must not be able to mistake a value whose zone is unknown
    for one whose zone is UTC, and a cell that looks different is the
    cheapest possible way to say so.
    """
    if utc_iso:
        return utc_iso
    if local_naive:
        return "%s (local)" % local_naive
    return ""


def render_locale_timestamp(iso, timestamp_format="MDY"):
    r"""Alpha's locale string. DISPLAY ONLY -- never a data export.

    RETAINED, DEMOTED. B5-F.F004 is not a complaint about this function;
    it is a complaint about where B4.5 called it. Reproducing Alpha's
    display convention is a legitimate thing to want when the point is
    to compare against an Alpha artifact byte-for-byte.

    What B6 changes is the blast radius. This is now reachable only
    from the legacy-equivalence exports, which exist to be compared
    against Alpha's own output and are labelled as such. Every export a
    user or a downstream tool actually consumes goes through
    `render_canonical_timestamp()` above.

    The MDY form Alpha writes -- 8/14/2026 3:26:16 PM -- has no leading
    zeros on the month, day or hour, and a 12-hour clock with a
    meridiem. Every one of those is a formatting decision that has to be
    reproduced exactly, and none is recoverable from the ISO string
    alone.

    If the stored format is anything this function does not know how to
    render, it returns None rather than guessing. An absent value that
    the comparison engine reports is worth more than a plausible wrong
    one that it does not.
    """
    if not iso:
        return ""
    try:
        stamp = datetime.datetime.strptime(iso[:19].rstrip("Z"),
                                           "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    hour = stamp.hour % 12 or 12
    meridiem = "AM" if stamp.hour < 12 else "PM"
    if timestamp_format == "MDY":
        return "%d/%d/%d %d:%02d:%02d %s" % (
            stamp.month, stamp.day, stamp.year, hour, stamp.minute,
            stamp.second, meridiem)
    if timestamp_format == "DMY":
        return "%d/%d/%d %d:%02d:%02d %s" % (
            stamp.day, stamp.month, stamp.year, hour, stamp.minute,
            stamp.second, meridiem)
    return None


def render_duration(seconds):
    r"""Render a duration the way ffprobe wrote it.

    A DOCUMENTED RECONSTRUCTION BY CONVENTION, NOT A RECOVERY.

    Alpha passes ffprobe's `format.duration` string through untouched,
    and ffprobe emits six decimal places: "1.000000". R5 promoted this
    field to a typed REAL column, so the string became the number 1.0 and
    the six zeros are gone.

    Applying ffprobe's convention reproduces the observed value, and on
    the controlled suite it matches exactly. But it is an assumption
    about a third-party tool's formatting, not something read back from
    the database, and it is labelled as such in the output map and in the
    equivalence report. If ffprobe ever emitted a different precision,
    this would differ silently -- which is the honest reason it is called
    out rather than quietly relied upon.

    The value's MEANING is fully intact either way, which is why this is
    classified formatting-only and not a persistence gap.
    """
    if seconds is None:
        return ""
    return "%.6f" % float(seconds)


def render_int(value):
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# The output map
# ---------------------------------------------------------------------------

class Artifact(object):
    def __init__(self, key, filename, producer, purpose, dialect, columns,
                 target, classification, scope, notes=""):
        self.key = key
        self.filename = filename
        self.producer = producer
        self.purpose = purpose
        self.dialect = dialect
        self.columns = columns
        self.target = target
        self.classification = classification
        self.scope = scope
        self.notes = notes


#: THE B6 CANONICAL INVENTORY. This is the artifact a user or a
#: downstream tool should read, and it is the one that fixes
#: B5-F.F003 and B5-F.F004 at the same time:
#:
#:   * timestamps are ISO-8601 UTC, in every locale, always;
#:   * UtcOffsetMinutes makes the local rendering reproducible without
#:     going back to the machine that produced it;
#:   * TimestampModel says which model the row was written under, so a
#:     pre-B6 row is visibly a pre-B6 row rather than a silent one;
#:   * SortKey is the deterministic PRESENTATION order (B5-F.F001), and
#:     is explicitly not an identity;
#:   * FilePathId IS the durable identity, and unlike DB_ID it does not
#:     change when a file is added earlier in the tree.
#:
#: The legacy INVENTORY_COLUMNS artifact is still produced, unchanged,
#: for equivalence comparison against Alpha. The two coexist on purpose:
#: one proves the past, the other is the contract going forward.
CANONICAL_INVENTORY_COLUMNS = [
    "FilePathId", "SortKey", "RootOrdinal", "RootPath", "RelativePath",
    "FileName", "Extension", "SizeBytes", "AllocatedSizeBytes",
    "CreatedUtc", "CreatedTimeState", "ModifiedUtc", "ModifiedTimeState",
    "AccessedUtc", "AccessedTimeState", "UtcOffsetMinutes", "TimestampModel",
    "Attributes", "IsReparsePoint", "ReparseTag", "IsOfflineOrCloud",
    "Depth", "PathLength", "State", "VolumeSerial", "FileIndex",
    "HardLinkCount", "HashStatus", "HashMeasurementMode", "HashIsCurrent",
    "ContentSha256", "ContentIsCurrent",
]


INVENTORY_COLUMNS = ["DB_ID", "FileName", "Extension", "Directory", "Path",
                     "Length", "CreationTime", "LastWriteTime",
                     "LastAccessTime", "Attributes", "IsReparsePoint",
                     "IsOfflineOrCloud", "Depth", "PathLength"]

ARTIFACTS = [
    Artifact("preliminary_inventory", "PreliminaryInventory.csv",
             "PreliminaryInventory.ps1",
             "Every file location observed during the inventory scan.",
             POWERSHELL, INVENTORY_COLUMNS, BYTE, CLASS_CANONICAL, "run"),

    Artifact("potential_duplicates", "PotentialDuplicates.csv",
             "PotentialDuplicates.ps1",
             "Files sharing a size with at least one other file.",
             POWERSHELL,
             ["DB_ID", "FileName", "Directory", "Path", "Length", "SizeGroupID"],
             BYTE, CLASS_CANONICAL, "duplicate_run"),

    Artifact("partial_hash_candidates", "PartialHashCandidates.csv",
             "PartialHash.ps1",
             "Candidate state after partial hashing, before full hashing.",
             POWERSHELL,
             ["DB_ID", "FileName", "Directory", "Path", "Length", "SizeGroupID",
              "PartialHash", "PartialHashGroupID", "Status", "IsOfflineOrCloud"],
             BYTE, CLASS_CANONICAL, "duplicate_run",
             "Status is the state at the END of the partial-hash stage, "
             "not the final verdict -- see status_at_partial_hash()."),

    Artifact("full_hash_inventory", "FullHashInventory.csv",
             "FullHashInventory.ps1",
             "Every inventoried file with a complete SHA-256 (Full Run).",
             POWERSHELL,
             INVENTORY_COLUMNS + ["FullHash", "FinalStatus",
                                  "DuplicateGroupID"],
             BYTE, CLASS_CANONICAL, "duplicate_run",
             "Full Run hashes every file; FinalStatus is UniqueByHash / "
             "ConfirmedDuplicate / SkippedCloudOnly / Error."),

    Artifact("duplicate_hash_inventory", "DuplicateHashInventory.csv",
             "FullHash.ps1",
             "The full inventory plus each file's final duplicate verdict.",
             POWERSHELL,
             INVENTORY_COLUMNS + ["PartialHash", "FullHash", "FinalStatus",
                                  "DuplicateGroupID"],
             BYTE, CLASS_CANONICAL, "duplicate_run"),

    Artifact("image", "ImageHashes.csv", "ImageAnalysis.ps1 / ImageHash.py",
             "Perceptual hashes and dimensions for images.", PYTHON,
             ["DB_ID", "FileName", "Path", "pHash", "aHash", "dHash",
              "Width", "Height", "Format", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run"),

    Artifact("pdf", "PDFInventory.csv", "PDFAnalysis.ps1 / PDFAnalysis.py",
             "PDF structure and document metadata.", PYTHON,
             ["DB_ID", "FileName", "Path", "PageCount", "IsEncrypted",
              "HasExtractableText", "Title", "Author", "Producer",
              "CreationDate", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run"),

    Artifact("office", "OfficeInventory.csv",
             "OfficeAnalysis.ps1 / OfficeAnalysis.py",
             "Office document type and metadata.", PYTHON,
             ["DB_ID", "FileName", "Path", "OfficeType", "ExtractionMode",
              "Title", "Author", "Created", "Modified", "ContentCount", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run"),

    Artifact("raw_image", "RawImageInventory.csv",
             "RawImageAnalysis.ps1 / RawImageAnalysis.py",
             "Camera EXIF for RAW images.", PYTHON,
             ["DB_ID", "FileName", "Path", "CameraMake", "CameraModel",
              "ExposureTime", "FNumber", "ISO", "FocalLength",
              "DateTimeOriginal", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run",
             "Zero applicable files on the controlled suite: Alpha writes "
             "NO artifact, and neither does this exporter."),

    Artifact("audio", "AudioInventory.csv",
             "AudioAnalysis.ps1 / AudioAnalysis.py",
             "Audio stream properties and tags.", PYTHON,
             ["DB_ID", "FileName", "Path", "DurationSeconds", "Bitrate",
              "Codec", "SampleRate", "Channels", "Title", "Artist", "Album",
              "Year", "TrackNumber", "Genre", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run",
             "DurationSeconds is rendered by ffprobe convention -- see "
             "render_duration()."),

    Artifact("video", "VideoInventory.csv",
             "VideoAnalysis.ps1 / VideoAnalysis.py",
             "Video stream properties.", PYTHON,
             ["DB_ID", "FileName", "Path", "DurationSeconds", "Bitrate",
              "VideoCodec", "Width", "Height", "FrameRate", "AudioCodec",
              "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run",
             "Zero applicable files on the controlled suite."),

    Artifact("text", "TextFileInventory.csv",
             "TextFileAnalysis.ps1 / TextFileAnalysis.py",
             "Text and Markdown structure.", PYTHON,
             ["DB_ID", "FileName", "Path", "LineCount", "WordCount",
              "CharCount", "Encoding", "HasFrontmatter", "Title",
              "HeadingCount", "WikilinkCount", "TagCount", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run"),

    Artifact("archive", "ArchiveInventory.csv",
             "ArchiveAnalysis.ps1 / ArchiveAnalysis.py",
             "Archive-level statistics.", PYTHON,
             ["DB_ID", "FileName", "Path", "EntryCount",
              "TotalUncompressedSize", "TotalCompressedSize",
              "CompressionRatioPercent", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run"),

    Artifact("archive_contents", "ArchiveContents.csv",
             "ArchiveAnalysis.ps1 / ArchiveAnalysis.py",
             "Entries listed inside each analyzed archive.", PYTHON,
             ["ArchiveDB_ID", "ArchivePath", "EntryPath", "EntrySize",
              "EntryCompressedSize"],
             BYTE, CLASS_CANONICAL, "analyzer_run",
             "Rebuilt from archive_member and its PARENT analyzer result. "
             "No archive entry becomes a file_path record."),

    Artifact("content_index", "ContentIndex.csv",
             "ContentExtraction.ps1 / ContentExtraction.py",
             "Extraction status and reference per source document.", PYTHON,
             ["DB_ID", "FileName", "Path", "SourceType", "ExtractedTextFile",
              "CharCount", "WordCount", "Error"],
             BYTE, CLASS_CANONICAL, "analyzer_run",
             "The extracted TEXT BODIES are not exported and not stored. "
             "That is the R9 boundary, and R6 does not cross it."),

    # ---------------------------------------------------------------
    # The B6 artifacts.
    #
    # These are NOT Alpha reconstructions and do not target BYTE
    # equivalence -- there is no Alpha artifact to be equivalent to.
    # They are the exports B6 asks consumers to actually read, and they
    # are the ones that are locale-independent, deterministically
    # ordered and scoped to current state.
    #
    # The legacy artifacts above continue to be produced, unchanged.
    # Keeping both is deliberate: B5 established that B4.5's outputs
    # were untrustworthy in specific ways, not that the equivalence
    # evidence should be discarded.
    # ---------------------------------------------------------------

    Artifact("canonical_inventory", "Inventory-Canonical.csv",
             "fo_exports.canonical_inventory (B6)",
             "Current state of every known location, with UTC timestamps.",
             PYTHON, CANONICAL_INVENTORY_COLUMNS,
             DATA, CLASS_CANONICAL, "project",
             "Scoped to CURRENT STATE, not to a run's observation history. "
             "Timestamps are ISO-8601 UTC in every locale (B5-F.F004); "
             "row order is derived from the path, not the walk (B5-F.F001)."),

    Artifact("current_duplicates", "Duplicates-Current.csv",
             "fo_state.current_duplicate_sets (B6)",
             "Duplicate groups as they stand now, one row per location.",
             PYTHON,
             ["Sha256", "SizeBytes", "LocationCount", "RootCount",
              "PhysicalCopyCount", "HardLinkAliasCount",
              "PhysicalIdentityComplete", "ReclaimableBytes",
              "MemberRank", "RootPath", "RelativePath", "FilePathId"],
             DATA, CLASS_CANONICAL, "project",
             "Reads file_state only, so its cost does not grow with run "
             "history (B5-E.F002). Excludes stale content identities: a "
             "hash measured against a superseded observation cannot appear."),
]

ARTIFACT_BY_KEY = {a.key: a for a in ARTIFACTS}

#: Analyzer artifacts, keyed by the analyzer_key R5 registered.
ANALYZER_ARTIFACTS = {
    "image": "image", "pdf": "pdf", "office": "office",
    "raw_image": "raw_image", "audio": "audio", "video": "video",
    "text": "text", "archive": "archive",
    "content_extraction": "content_index",
}

#: How each analyzer CSV column is recovered. A column is either
#: PROMOTED (a real column on analyzer_result), DETAIL (a key in
#: detail_json), or DERIVED (computed from the relational identity).
#:
#: This table is the analyzer half of the output map, and it is the thing
#: to edit when an analyzer gains a column -- not the export code.
ANALYZER_COLUMN_SOURCES = {
    "image": {"Width": ("promoted", "width_px"), "Height": ("promoted", "height_px")},
    "pdf": {"Title": ("promoted", "title"), "Author": ("promoted", "author"),
            "CreationDate": ("promoted", "content_created_reported")},
    "office": {"Title": ("promoted", "title"), "Author": ("promoted", "author"),
               "Created": ("promoted", "content_created_reported")},
    "raw_image": {"DateTimeOriginal": ("promoted", "content_created_reported")},
    "audio": {"DurationSeconds": ("duration", "duration_seconds"),
              "Title": ("promoted", "title")},
    "video": {"DurationSeconds": ("duration", "duration_seconds"),
              "Width": ("promoted", "width_px"), "Height": ("promoted", "height_px")},
    "text": {"Title": ("promoted", "title"), "WordCount": ("promoted", "word_count"),
             "CharCount": ("promoted", "char_count")},
    "archive": {},
    "content_extraction": {"WordCount": ("promoted", "word_count"),
                           "CharCount": ("promoted", "char_count")},
}


def status_at_partial_hash(needed_full_hash, alpha_final_status):
    r"""Alpha's PartialHashCandidates Status, rebuilt from the final state.

    This column is the one place in the hash artifacts where the CSV
    records a MOMENT rather than a verdict. PartialHash.ps1 writes it
    before FullHash.ps1 has run, so its vocabulary is deliberately
    different from the final one:

        NeedsFullHash       partial hashes collided and the file is
                            larger than the partial window
        ConfirmedDuplicate  the partial hash covered the whole file, so
                            the partial hash IS the full hash
        RuledOut            no other file shares this partial hash

    All three are recoverable, because R4 persisted `needed_full_hash`
    alongside the final verdict rather than only the verdict. On the
    controlled suite this reproduces 73 / 66 / 4215 exactly.

    Had R4 stored only alpha_final_status, this column would have been
    unreconstructable -- the 115 confirmed duplicates could not have been
    split back into the 66 settled at partial-hash time and the 49
    settled later. It is worth recording that as the reason the column
    survives.
    """
    if needed_full_hash:
        return "NeedsFullHash"
    if alpha_final_status == "ConfirmedDuplicate":
        return "ConfirmedDuplicate"
    return "RuledOut"


# ---------------------------------------------------------------------------
# The single inventory-row renderer (Beta B2)
# ---------------------------------------------------------------------------

def canonical_inventory_cells(row):
    r"""One canonical inventory row, from a file_state-joined query row.

    ContentIsCurrent is the staleness invariant, surfaced to the user.
    A row whose content hash was measured against a superseded
    observation reports False here rather than presenting a stale
    digest as if it described the file on disk today. B5-I asks for a
    clear trust boundary after partial work; this is that boundary, in
    a column.
    """
    file_name = row["file_name"] or ""
    extension = win_meta.dotnet_extension(file_name)
    current = (row["content_observation_id"] is not None
               and row["content_observation_id"] == row["current_observation_id"])
    hash_current = (row["hash_observation_id"] is not None
                    and row["hash_observation_id"] == row["current_observation_id"])
    return [
        row["file_path_id"], row["path_sort_key"], row["root_ordinal"],
        row["root_path"], row["relative_path"], file_name, extension,
        row["size_bytes"], row["allocated_size_bytes"],
        render_canonical_timestamp(row["created_utc"], row["created_local_naive"]),
        row["created_time_state"],
        render_canonical_timestamp(row["modified_utc"], row["modified_local_naive"]),
        row["modified_time_state"],
        render_canonical_timestamp(row["accessed_utc"], row["accessed_local_naive"]),
        row["accessed_time_state"], row["utc_offset_minutes"],
        row["timestamp_model"], row["attributes"],
        render_bool(row["is_reparse_point"]), row["reparse_tag"],
        render_bool(row["is_offline_or_cloud"]), row["depth"], row["path_length"],
        row["state"], row["volume_serial"], row["file_index"],
        row["hard_link_count"], row["hash_status"], row["hash_measurement_mode"],
        render_bool(hash_current) if row["hash_status"] else None,
        row["sha256"], render_bool(current) if row["sha256"] else None,
    ]


def inventory_cells(legacy_db_id, file_name, full_path, size_bytes, created,
                    modified, accessed, attributes, is_reparse_point,
                    is_offline_or_cloud, depth, path_length,
                    timestamp_format="MDY"):
    r"""One PreliminaryInventory.csv row, from primitive values.

    THE ONLY PLACE an inventory row is rendered. B2 has two callers --
    the database export, and the degraded path used when a project has
    no database to export from -- and two renderers that agreed on the
    day they were written would not stay agreed. The B2 verifier proves
    both callers produce identical bytes; this function is what makes
    that provable rather than coincidental.

    Extension comes from the FILE NAME, not from extension_key.
    extension_key is lowercased for comparison; Alpha writes the
    extension with its original case, so a file named REPORT.PDF must
    come back as .PDF.
    """
    extension = win_meta.dotnet_extension(file_name)
    return [
        legacy_db_id, file_name, extension,
        full_path.rsplit("\\", 1)[0], full_path, size_bytes,
        render_locale_timestamp(created, timestamp_format),
        render_locale_timestamp(modified, timestamp_format),
        render_locale_timestamp(accessed, timestamp_format),
        attributes, render_bool(is_reparse_point),
        render_bool(is_offline_or_cloud), depth, path_length,
    ]


def inventory_cells_from_record(record, timestamp_format="MDY"):
    """The same row, rendered straight from an engine record."""
    return inventory_cells(
        legacy_db_id=record.legacy_db_id, file_name=record.file_name,
        full_path=record.path, size_bytes=record.size_bytes,
        created=record.created, modified=record.modified,
        accessed=record.accessed, attributes=record.attributes,
        is_reparse_point=1 if record.is_reparse_point else 0,
        is_offline_or_cloud=1 if record.is_offline_or_cloud else 0,
        depth=record.depth, path_length=record.path_length,
        timestamp_format=timestamp_format)


def write_inventory_csv(path, rows):
    r"""Write PreliminaryInventory.csv in the PowerShell dialect.

    UTF-8 WITH BOM, CRLF, every field quoted -- and a genuine NULL
    written as bare emptiness rather than as "". That is Export-Csv's
    behaviour and the reason this dialect is hand-rolled instead of
    using csv.QUOTE_ALL.

    Note what this function is NOT. It is not a transport: nothing
    reads it back to populate the database. It is the run's export, and
    it is also what the PowerShell hash stages still consume, which is
    exactly the arrangement B2 asks for -- the CSV is an output of the
    inventory, not a channel into it.
    """
    return POWERSHELL.write(str(path), INVENTORY_COLUMNS, rows)


def export_run_inventory(conn, run_id, csv_path):
    r"""Render one run's inventory CSV FROM the database.

    The B2 flow in one call: the scan persisted, the database is
    authoritative, and the artifact is reconstructed from what was
    persisted. If a column could not survive the round trip this is
    where it would show up, on every single run, instead of only when
    somebody runs an equivalence proof.

    Deliberately NOT routed through Exporter.export_all(), which
    refuses to write inside a project's Runs folder. That refusal is
    R6's -- a proof must not overwrite its own evidence -- and it stays
    exactly as it is. This is a different operation with a different
    purpose: producing the run's own output, in the run's own folder.
    """
    exporter = Exporter(conn, run_id=run_id)
    rows = exporter.preliminary_inventory()
    # B6.2 Fix 2 -- do NOT call len() here.
    #
    # preliminary_inventory() returns a GENERATOR on purpose (B6 made
    # this export stream instead of materialising ~1.9 GB at a million
    # files). len() on a generator is a TypeError, and the generator is
    # already consumed by write_inventory_csv above, so it cannot be
    # re-counted either. The writer already counts rows as it streams
    # them and returns (path, row_count); use that. This function was
    # raising on every Windows run *after* writing a correct, complete
    # CSV -- which is why the run said "inventory recorded but its CSV
    # export failed" next to a full PreliminaryInventory.csv.
    _written_path, row_count = write_inventory_csv(csv_path, rows)
    return {"rows": row_count, "path": str(csv_path),
            "timestamp_format": exporter.timestamp_format()}


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class Exporter(object):
    r"""Reconstructs one run's legacy artifacts from the database.

    SQLite is authoritative. These are deterministic derived artifacts.
    A caller may write the supported run outputs into the run's Inventory
    folder; `_reject_alpha_destination()` is used only by explicit verifier
    calls that supply a protected project destination.
    """

    def __init__(self, conn, run_id=None):
        self.conn = conn
        self.conn.row_factory = __import__("sqlite3").Row
        self.run_id = run_id or self._latest_run()
        self.notes = []
        self._scan_ids = None
        self._duplicate_run_ids = {}

    # -- selection ----------------------------------------------------

    def _latest_run(self):
        row = self.conn.execute(
            "SELECT run_id FROM run ORDER BY started_utc DESC, run_id DESC "
            "LIMIT 1").fetchone()
        if row is None:
            raise ExportError("This project contains no runs to export.")
        return row["run_id"]

    def _run_folder(self):
        row = self.conn.execute("SELECT run_folder FROM run WHERE run_id = ?",
                                (self.run_id,)).fetchone()
        if row is None:
            raise ExportError("Run %s does not exist." % self.run_id)
        folder = row["run_folder"]
        if folder is None or not str(folder).strip():
            # B5-A.F004: folder-scoped exports must never silently turn an
            # unbound run into an apparently successful empty export.
            raise ExportError(
                "Run %s has no run_folder binding; exports cannot determine "
                "which operator-visible session they belong to." % self.run_id)
        return folder

    def inventory_scan_ids(self):
        r"""Every inventory scan belonging to this run's run folder.

        Scoped by RUN FOLDER rather than run_id. The Alpha pipeline
        splits one operator-visible session across several runs -- the
        controlled suite's artifacts come from run 1 (inventory), run 2
        (hashing) and run 3 (analysis), all writing into one folder. An
        export scoped to run_id alone would find no inventory when asked
        for the analysis run's artifacts, which is not what "this run's
        outputs" means to anyone looking at the folder.
        """
        if self._scan_ids is None:
            folder = self._run_folder()
            rows = self.conn.execute(
                "SELECT s.inventory_scan_id FROM inventory_scan s "
                "JOIN run r ON r.run_id = s.run_id "
                "WHERE r.run_folder = ? AND s.status IN "
                "  ('completed', 'completed_with_warnings') "
                "ORDER BY s.inventory_scan_id", (folder,)).fetchall()
            self._scan_ids = [r["inventory_scan_id"] for r in rows]
        return self._scan_ids

    def duplicate_run_id(self, mode=None):
        r"""The duplicate_run these artifacts belong to.

        Beta B3: `mode` narrows the answer to 'selective' or
        'exhaustive'. It defaults to None, which is the pre-B3
        behaviour -- the newest duplicate_run for this run folder --
        so every existing caller is unaffected.

        The parameter is needed because a run folder can now hold both
        kinds. Before B3 it could not in practice: Choose Run Type
        offered Duplicate Run OR Full Run, and only the selective path
        was ever exported. Selecting by "newest" would then let a Full
        Run silently retarget the Duplicate Run's own three artifacts,
        which is the sort of cross-wiring that shows up as four
        mysteriously empty CSVs rather than as an error.
        """
        cache = self._duplicate_run_ids
        key = mode or "_any"
        if key not in cache:
            folder = self._run_folder()
            clause = " AND d.mode = ?" if mode else ""
            parameters = [folder] + ([mode] if mode else [])
            row = self.conn.execute(
                "SELECT d.duplicate_run_id FROM duplicate_run d "
                "JOIN run r ON r.run_id = d.run_id "
                "WHERE r.run_folder = ?%s ORDER BY d.duplicate_run_id DESC "
                "LIMIT 1" % clause, parameters).fetchone()
            cache[key] = row["duplicate_run_id"] if row else 0
        return cache[key] or None

    def timestamp_format(self):
        scans = self.inventory_scan_ids()
        if not scans:
            return "MDY"
        row = self.conn.execute(
            "SELECT timestamp_format_detected FROM inventory_scan "
            "WHERE inventory_scan_id = ?", (scans[0],)).fetchone()
        return (row["timestamp_format_detected"] or "MDY") if row else "MDY"

    # -- shared row source --------------------------------------------

    def _observation_rows(self):
        r"""The observations Alpha's inventory CSV actually lists.

        B6: STREAMS. `execute()` returns a cursor and the cursor is
        returned as-is, so callers iterate it lazily. B4.5 called
        `.fetchall()` here, which is the first of the four
        simultaneously-live copies B5-E.F003 measured. Removing it is
        the difference between peak memory scaling with the project and
        peak memory scaling with a row.

        Restricted to status='observed', and that restriction is
        load-bearing. A file the scan could not enumerate goes to the
        inaccessible set instead. Exporting the table unfiltered would
        invent inventory rows for files that were never listed,
        silently inflating every count derived from them.

        B6 ORDERING. `ORDER BY fp.path_sort_key` replaces B4.5's
        `ORDER BY o.legacy_db_id`, and the change is the point of
        B5-F.F001. legacy_db_id is a per-scan ordinal handed out in
        traversal order; ordering an export by it made the export's row
        order a function of the filesystem's mood. path_sort_key is
        derived from the path itself, so two machines produce the same
        order from the same tree. file_path_id is the final tiebreaker
        so the sort can never be ambiguous -- see B5-F.F002.
        """
        scans = self.inventory_scan_ids()
        if not scans:
            return iter(())
        placeholders = ",".join("?" * len(scans))
        return self.conn.execute(
            "SELECT o.file_observation_id, o.legacy_db_id, o.size_bytes, "
            "       o.created_utc, o.modified_utc, o.accessed_utc, "
            "       o.created_local_naive, o.modified_local_naive, "
            "       o.accessed_local_naive, o.utc_offset_minutes, "
            "       o.timestamp_model, "
            "       o.attributes, o.is_reparse_point, o.is_offline_or_cloud, "
            "       o.path_length, o.status, o.error_message, "
            "       fp.file_name, fp.relative_path, fp.depth, "
            "       fp.path_sort_key, fp.file_path_id, "
            "       sr.root_path, sr.root_ordinal "
            "FROM file_observation o "
            "JOIN file_path fp ON fp.file_path_id = o.file_path_id "
            "JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
            "WHERE o.inventory_scan_id IN (%s) AND o.status = 'observed' "
            "ORDER BY sr.root_ordinal, fp.path_sort_key, fp.file_path_id"
            % placeholders, scans)

    def canonical_inventory_rows(self):
        r"""Canonical CURRENT inventory, streamed from file_state.

        No historical-observation join is needed: B6.1 makes file_state the full
        current projection, including the metadata of unchanged files reverified
        by the latest scan. This keeps history append-only without making current
        exports stale.
        """
        return self.conn.execute(
            "SELECT fs.file_path_id,fp.path_sort_key,sr.root_ordinal,sr.root_path,"
            " fp.relative_path,fp.file_name,fs.depth,fs.size_bytes,fs.allocated_size_bytes,"
            " fs.state,fs.current_observation_id,fs.content_observation_id,"
            " fs.hash_observation_id,fs.hash_status,fs.hash_measurement_mode,"
            " fs.created_utc,fs.modified_utc,fs.accessed_utc,"
            " fs.created_local_naive,fs.modified_local_naive,fs.accessed_local_naive,"
            " fs.created_time_state,fs.modified_time_state,fs.accessed_time_state,"
            " fs.utc_offset_minutes,fs.timestamp_model,fs.attributes,"
            " fs.is_reparse_point,fs.reparse_tag,fs.is_offline_or_cloud,fs.path_length,"
            " fs.volume_serial,fs.file_index,fs.hard_link_count,c.sha256 "
            "FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
            "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
            "LEFT JOIN content c ON c.content_id=fs.content_id "
            "WHERE fs.project_id=1 AND fs.state<>'missing' "
            "ORDER BY sr.root_ordinal,fp.path_sort_key,fp.file_path_id")

    def inaccessible_rows(self):
        r"""The locations Alpha recorded in Logs\errors.txt.

        Kept separate and exposed separately, so the distinction survives
        the export rather than being flattened into it. R6 proves these
        records are still there and still distinguishable; reconstructing
        the errors.txt file itself is not required, because R2 already
        holds the same failures as run events.
        """
        scans = self.inventory_scan_ids()
        if not scans:
            return []
        placeholders = ",".join("?" * len(scans))
        return self.conn.execute(
            "SELECT o.status, o.error_kind, o.error_message, "
            "       fp.relative_path, sr.root_path "
            "FROM file_observation o "
            "JOIN file_path fp ON fp.file_path_id = o.file_path_id "
            "JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
            "WHERE o.inventory_scan_id IN (%s) AND o.status <> 'observed' "
            "ORDER BY o.file_observation_id" % placeholders, scans).fetchall()

    @staticmethod
    def _full_path(row):
        # Rebuilt from the root plus the relative path, which is what
        # keeps two identical relative paths under two roots distinct.
        return row["root_path"].rstrip("\\") + "\\" + row["relative_path"]

    def _inventory_cells(self, row, timestamp_format):
        r"""One LEGACY-dialect inventory row.

        Feeds the Alpha-equivalence artifact only. It reads the
        *_local_naive columns, because Alpha's CSV contained local wall
        clock and reproducing it is this artifact's entire purpose --
        and because after migration 006 those columns are finally
        NAMED for what they hold. Consumers who want a timestamp they
        can compute with read the canonical export instead.
        """
        return inventory_cells(
            legacy_db_id=row["legacy_db_id"], file_name=row["file_name"],
            full_path=self._full_path(row), size_bytes=row["size_bytes"],
            created=row["created_local_naive"],
            modified=row["modified_local_naive"],
            accessed=row["accessed_local_naive"], attributes=row["attributes"],
            is_reparse_point=row["is_reparse_point"],
            is_offline_or_cloud=row["is_offline_or_cloud"],
            depth=row["depth"], path_length=row["path_length"],
            timestamp_format=timestamp_format)

    # -- individual exports -------------------------------------------

    def preliminary_inventory(self):
        """Streams. A generator expression, not a list comprehension."""
        fmt = self.timestamp_format()
        return (self._inventory_cells(r, fmt)
                for r in self._observation_rows())

    def canonical_inventory(self):
        """The B6 canonical inventory. Streams from current state."""
        return (canonical_inventory_cells(r)
                for r in self.canonical_inventory_rows())

    def current_duplicates(self):
        r"""Duplicate groups as they stand NOW, one row per location.

        Reads fo_state's current-state query, so this export does not
        get slower as run history accumulates -- which is the export
        half of B5-E.F002.
        """
        import fo_state
        for group in fo_state.iter_current_duplicate_sets(self.conn):
            for rank, place in enumerate(
                    fo_state.current_locations_for_content(
                        self.conn, group["content_id"]), start=1):
                yield [group["sha256"], group["size_bytes"],
                       group["location_count"], group["root_count"],
                       group["physical_copy_count"], group["hard_link_alias_count"],
                       group["physical_identity_complete"],
                       group["reclaimable_bytes"], rank,
                       place["root_path"],
                       place["relative_path"],
                       place["file_path_id"]]

    def _hash_rows(self, candidates_only, mode="selective"):
        duplicate_run = self.duplicate_run_id(mode)
        if duplicate_run is None:
            return iter(())
        scans = self.inventory_scan_ids()
        placeholders = ",".join("?" * len(scans))
        clause = "AND h.size_group_id IS NOT NULL" if candidates_only else ""
        # B6: every ORDER BY here now ends in a column that cannot tie
        # (file_path_id), and orders on path_sort_key rather than on
        # traversal-order legacy_db_id. B5-F.F002 found report membership
        # changing when tied rows arrived in a different order; a sort
        # that terminates in a unique key cannot do that.
        order = ("ORDER BY h.size_group_id, fp.path_sort_key, fp.file_path_id"
                 if candidates_only
                 else "ORDER BY sr.root_ordinal, fp.path_sort_key, fp.file_path_id")
        return self.conn.execute(
            "SELECT o.legacy_db_id, o.size_bytes, "
            "       o.created_local_naive, o.modified_local_naive, "
            "       o.accessed_local_naive, "
            "       o.attributes, o.is_reparse_point, "
            "       o.is_offline_or_cloud, o.path_length, "
            "       fp.file_name, fp.relative_path, fp.depth, "
            "       fp.path_sort_key, fp.file_path_id, "
            "       sr.root_path, sr.root_ordinal, "
            "       h.size_group_id, h.partial_hash, h.partial_group_id, "
            "       h.full_hash, h.alpha_final_status, h.needed_full_hash, "
            "       g.legacy_group_id "
            "FROM hash_measurement h "
            "JOIN file_observation o ON o.file_observation_id = h.file_observation_id "
            "JOIN file_path fp ON fp.file_path_id = o.file_path_id "
            "JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
            "LEFT JOIN duplicate_member m ON m.hash_measurement_id = h.hash_measurement_id "
            "LEFT JOIN duplicate_group g ON g.duplicate_group_id = m.duplicate_group_id "
            "WHERE h.duplicate_run_id = ? AND o.inventory_scan_id IN (%s) %s %s"
            % (placeholders, clause, order),
            [duplicate_run] + scans)

    def potential_duplicates(self):
        for row in self._hash_rows(candidates_only=True):
            full = self._full_path(row)
            yield [row["legacy_db_id"], row["file_name"],
                   full.rsplit("\\", 1)[0], full, row["size_bytes"],
                   row["size_group_id"]]

    def partial_hash_candidates(self):
        for row in self._hash_rows(candidates_only=True):
            full = self._full_path(row)
            yield [
                row["legacy_db_id"], row["file_name"], full.rsplit("\\", 1)[0],
                full, row["size_bytes"], row["size_group_id"],
                row["partial_hash"], row["partial_group_id"],
                status_at_partial_hash(row["needed_full_hash"],
                                       row["alpha_final_status"]),
                render_bool(row["is_offline_or_cloud"]),
            ]

    def duplicate_hash_inventory(self):
        fmt = self.timestamp_format()
        for row in self._hash_rows(candidates_only=False):
            yield self._inventory_cells(row, fmt) + [
                row["partial_hash"], row["full_hash"],
                row["alpha_final_status"], row["legacy_group_id"]]

    def full_hash_inventory(self):
        r"""FullHashInventory.csv -- the Full Run's artifact.

        Every inventoried file with its complete SHA-256. Deliberately
        NOT duplicate_hash_inventory() with a column dropped: the two
        workflows answer different questions and their FinalStatus
        vocabularies differ, so a shared renderer would have to be told
        which vocabulary to use and would then be two renderers wearing
        one name.
        """
        fmt = self.timestamp_format()
        for row in self._hash_rows(candidates_only=False, mode="exhaustive"):
            yield self._inventory_cells(row, fmt) + [
                row["full_hash"], row["alpha_final_status"],
                row["legacy_group_id"]]

    # -- analyzers ----------------------------------------------------

    def _analyzer_run_id(self, analyzer_key):
        folder = self._run_folder()
        row = self.conn.execute(
            "SELECT ar.analyzer_run_id, ar.analysis_status FROM analyzer_run ar "
            "JOIN analyzer a ON a.analyzer_id = ar.analyzer_id "
            "JOIN run r ON r.run_id = ar.run_id "
            "WHERE r.run_folder = ? AND a.analyzer_key = ? "
            "ORDER BY ar.analyzer_run_id DESC LIMIT 1",
            (folder, analyzer_key)).fetchone()
        if row is None:
            return None, None
        return row["analyzer_run_id"], row["analysis_status"]

    def analyzer_rows(self, analyzer_key):
        r"""Rebuild one analyzer's CSV rows.

        Returns (rows, status). A status of 'no_applicable_files' comes
        back with an empty row list AND is reported, because Alpha writes
        no file at all in that case. Emitting a header-only CSV would be
        a different artifact than the one Alpha produced, and would turn
        "this analyzer found nothing" into "this analyzer produced an
        empty table" -- a distinction R5 went to some trouble to keep.
        """
        analyzer_run_id, status = self._analyzer_run_id(analyzer_key)
        if analyzer_run_id is None:
            return [], None
        if status == "no_applicable_files":
            return [], status

        artifact = ARTIFACT_BY_KEY[ANALYZER_ARTIFACTS[analyzer_key]]
        sources = ANALYZER_COLUMN_SOURCES[analyzer_key]
        rows = self.conn.execute(
            "SELECT r.*, fp.file_name, fp.relative_path, sr.root_path "
            "FROM analyzer_result r "
            "LEFT JOIN file_observation o "
            "  ON o.file_observation_id = r.file_observation_id "
            "LEFT JOIN file_path fp ON fp.file_path_id = o.file_path_id "
            "LEFT JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
            # B6: ordered by the deterministic presentation key with a
            # unique tiebreaker, not by traversal-order legacy_db_id.
            # Unmatched rows have no file_path and sort last, together,
            # by their own reported path -- they are still deterministic,
            # just not locatable in the tree.
            "WHERE r.analyzer_run_id = ? "
            "ORDER BY sr.root_ordinal IS NULL, sr.root_ordinal, "
            "         fp.path_sort_key, r.unmatched_path, r.analyzer_result_id",
            (analyzer_run_id,))

        def stream():
            for row in rows:
                detail = json.loads(row["detail_json"]) if row["detail_json"] else {}
                full = (self._full_path(row) if row["root_path"]
                        else (row["unmatched_path"] or ""))
                cells = []
                for column in artifact.columns:
                    if column == "DB_ID":
                        cells.append(row["legacy_db_id"])
                    elif column == "FileName":
                        cells.append(row["file_name"] or os.path.basename(full))
                    elif column == "Path":
                        cells.append(full)
                    elif column == "Error":
                        # The per-file error text Alpha wrote, preserved as
                        # written. Errors are NOT normalised away just
                        # because the schema is tidier than the CSV.
                        cells.append(row["error_message"] or "")
                    elif column in sources:
                        kind, field = sources[column]
                        value = row[field]
                        if kind == "duration":
                            cells.append(render_duration(value))
                        else:
                            cells.append("" if value is None else value)
                    else:
                        cells.append(detail.get(column, ""))
                yield cells
        return stream(), status

    def archive_contents(self):
        analyzer_run_id, status = self._analyzer_run_id("archive")
        if analyzer_run_id is None or status == "no_applicable_files":
            return [], status
        cursor = self.conn.execute(
            "SELECT m.entry_path, m.entry_size_bytes, "
            "       m.entry_compressed_size_bytes, r.legacy_db_id, "
            "       fp.relative_path, sr.root_path "
            "FROM archive_member m "
            "JOIN analyzer_result r ON r.analyzer_result_id = m.analyzer_result_id "
            "JOIN file_observation o ON o.file_observation_id = r.file_observation_id "
            "JOIN file_path fp ON fp.file_path_id = o.file_path_id "
            "JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
            "WHERE r.analyzer_run_id = ? "
            "ORDER BY sr.root_ordinal, fp.path_sort_key, m.sequence, "
            "         m.archive_member_id", (analyzer_run_id,))

        # Streams. An archive analyzer run can legitimately produce
        # hundreds of thousands of member rows (B5-E.F006), so this is
        # precisely the export that must not build a list.
        def stream():
            for row in cursor:
                yield [row["legacy_db_id"], self._full_path(row),
                       row["entry_path"], row["entry_size_bytes"],
                       row["entry_compressed_size_bytes"]]
        return stream(), status

    def analyzer_errors(self, analyzer_key):
        r"""Rebuild the `<Artifact>.errors.txt` sidecar.

        These are CLASS C diagnostics: the same per-file failures the CSV
        already records in its Error column, gathered into a convenience
        list for a human reading the run folder. They carry no
        information the CSV lacks, which is exactly why they are
        classified as run diagnostics rather than canonical project data.
        """
        analyzer_run_id, status = self._analyzer_run_id(analyzer_key)
        if analyzer_run_id is None or status == "no_applicable_files":
            return [], status
        lines = []
        for row in self.conn.execute(
                "SELECT r.error_message, r.unmatched_path, fp.relative_path, "
                "       sr.root_path FROM analyzer_result r "
                "LEFT JOIN file_observation o "
                "  ON o.file_observation_id = r.file_observation_id "
                "LEFT JOIN file_path fp ON fp.file_path_id = o.file_path_id "
                "LEFT JOIN source_root sr ON sr.source_root_id = fp.source_root_id "
                "WHERE r.analyzer_run_id = ? AND r.status = 'error' "
                "ORDER BY sr.root_ordinal IS NULL, sr.root_ordinal, "
                "         fp.path_sort_key, r.analyzer_result_id",
                (analyzer_run_id,)):
            path = (self._full_path(row) if row["root_path"]
                    else (row["unmatched_path"] or ""))
            lines.append("%s -- %s" % (path, row["error_message"] or ""))
        return lines, status

    # -- driving ------------------------------------------------------

    @staticmethod
    def _reject_alpha_destination(output_dir, project_dir):
        r"""Refuse to write anywhere Alpha's own artifacts live.

        Acceptance criterion 20 says the original artifacts are never
        overwritten. Enforcing that with a check rather than a convention
        is the difference between a property and a hope.
        """
        resolved = os.path.abspath(str(output_dir))
        runs = os.path.abspath(os.path.join(str(project_dir), "Runs"))
        if resolved == runs or resolved.startswith(runs + os.sep):
            raise ExportError(
                "R6 exports must not be written inside the project's Runs "
                "folder, which holds the original Alpha artifacts this "
                "export exists to be compared against. Choose a separate "
                "output directory. (Refused: %s)" % resolved)

    def _write_streamed(self, artifact, output_dir, rows):
        r"""Write one artifact from a streaming row source.

        RETURNS (path, count), OR (None, 0) IF THERE WERE NO ROWS.

        B4.5 decided emptiness with `if not rows:` before writing.
        That worked because every row source returned a list. After
        B5-E.F003 they return GENERATORS, and a generator is always
        truthy -- so the same test would have silently written a
        header-only CSV for every artifact that had nothing in it, and
        Alpha's "no file at all" contract would have quietly become
        "an empty table". Different claim, same filename.

        Emptiness is therefore decided by what the writer actually
        wrote, and a zero-row artifact is removed again. Deciding after
        the fact is the only way to decide it without draining the
        generator first, which is the thing we are avoiding.
        """
        path = os.path.join(str(output_dir), artifact.filename)
        _written, count = artifact.dialect.write(path, artifact.columns, rows)
        if count == 0:
            try:
                os.remove(path)
            except OSError:
                pass
            return None, 0
        return path, count

    def export_hash_stage(self, output_dir, include_canonical=True):
        """Write hash-derived artifacts only; do not regenerate inventory/analyzers."""
        os.makedirs(str(output_dir), exist_ok=True)
        written, skipped, counts = {}, {}, {}
        for key, method in (("potential_duplicates", self.potential_duplicates),
                            ("partial_hash_candidates", self.partial_hash_candidates),
                            ("duplicate_hash_inventory", self.duplicate_hash_inventory),
                            ("full_hash_inventory", self.full_hash_inventory)):
            artifact=ARTIFACT_BY_KEY[key]
            path,count=self._write_streamed(artifact,output_dir,method())
            if path is None: skipped[key]="no rows for this run"
            else: written[key]=path; counts[key]=count
        if include_canonical:
            artifact=ARTIFACT_BY_KEY["current_duplicates"]
            path,count=self._write_streamed(artifact,output_dir,self.current_duplicates())
            if path is None: skipped["current_duplicates"]="no rows"
            else: written["current_duplicates"]=path; counts["current_duplicates"]=count
        return {"written":written,"skipped":skipped,"row_counts":counts}

    def export_analyzer_stage(self, output_dir, analyzer_keys=None):
        """Write analyzer-derived artifacts only."""
        os.makedirs(str(output_dir), exist_ok=True)
        wanted=set(analyzer_keys or ANALYZER_ARTIFACTS.keys())
        written, skipped, counts = {}, {}, {}
        for analyzer_key,artifact_key in ANALYZER_ARTIFACTS.items():
            if analyzer_key not in wanted: continue
            artifact=ARTIFACT_BY_KEY[artifact_key]
            rows,status=self.analyzer_rows(analyzer_key)
            if status == "no_applicable_files": skipped[artifact_key]="no_applicable_files"; continue
            if status is None: skipped[artifact_key]="analyzer did not run in this run"; continue
            path=os.path.join(str(output_dir),artifact.filename)
            _p,count=artifact.dialect.write(path,artifact.columns,rows)
            written[artifact_key]=path; counts[artifact_key]=count
            lines,_=self.analyzer_errors(analyzer_key)
            if lines:
                ep=os.path.join(str(output_dir),artifact.filename.rsplit(".",1)[0]+".errors.txt")
                with open(ep,"wb") as h: h.write(("\r\n".join(lines)+"\r\n").encode("utf-8"))
                written[artifact_key+"_errors"]=ep
        if "archive" in wanted:
            rows,status=self.archive_contents(); artifact=ARTIFACT_BY_KEY["archive_contents"]
            path,count=self._write_streamed(artifact,output_dir,rows)
            if path is None: skipped["archive_contents"]=status or "no archive members"
            else: written["archive_contents"]=path; counts["archive_contents"]=count
        return {"written":written,"skipped":skipped,"row_counts":counts}

    def export_all(self, output_dir, project_dir=None, include_canonical=True):
        """Write every reconstructable artifact for this run."""
        if project_dir:
            self._reject_alpha_destination(output_dir, project_dir)
        os.makedirs(str(output_dir), exist_ok=True)
        written = {}
        skipped = {}
        counts = {}

        for key, method in (("preliminary_inventory", self.preliminary_inventory),
                            ("potential_duplicates", self.potential_duplicates),
                            ("partial_hash_candidates", self.partial_hash_candidates),
                            ("duplicate_hash_inventory", self.duplicate_hash_inventory),
                            ("full_hash_inventory", self.full_hash_inventory)):
            artifact = ARTIFACT_BY_KEY[key]
            path, count = self._write_streamed(artifact, output_dir, method())
            if path is None:
                skipped[key] = "no rows for this run"
                continue
            written[key] = path
            counts[key] = count

        for analyzer_key, artifact_key in ANALYZER_ARTIFACTS.items():
            artifact = ARTIFACT_BY_KEY[artifact_key]
            rows, status = self.analyzer_rows(analyzer_key)
            if status == "no_applicable_files":
                # Matching Alpha exactly: no applicable files means no
                # file on disk, not an empty one.
                skipped[artifact_key] = "no_applicable_files"
                continue
            if status is None:
                skipped[artifact_key] = "analyzer did not run in this run"
                continue
            path = os.path.join(str(output_dir), artifact.filename)
            _p, count = artifact.dialect.write(path, artifact.columns, rows)
            written[artifact_key] = path
            counts[artifact_key] = count

            lines, _ = self.analyzer_errors(analyzer_key)
            if lines:
                errors_path = os.path.join(
                    str(output_dir), artifact.filename.rsplit(".", 1)[0] + ".errors.txt")
                with open(errors_path, "wb") as handle:
                    handle.write(("\r\n".join(lines) + "\r\n").encode("utf-8"))
                written[artifact_key + "_errors"] = errors_path

        rows, status = self.archive_contents()
        artifact = ARTIFACT_BY_KEY["archive_contents"]
        path, count = self._write_streamed(artifact, output_dir, rows)
        if path is None:
            skipped["archive_contents"] = status or "no archive members"
        else:
            written["archive_contents"] = path
            counts["archive_contents"] = count

        # The two B6 artifacts. Written last so a failure here cannot
        # cost the legacy-equivalence set, which is what an Alpha
        # comparison depends on.
        if include_canonical:
            for key, method in (
                    ("canonical_inventory", self.canonical_inventory),
                    ("current_duplicates", self.current_duplicates)):
                artifact = ARTIFACT_BY_KEY[key]
                path, count = self._write_streamed(artifact, output_dir, method())
                if path is None:
                    skipped[key] = "no rows"
                    continue
                written[key] = path
                counts[key] = count

        return {"run_id": self.run_id, "run_folder": self._run_folder(),
                "written": written, "skipped": skipped, "row_counts": counts,
                "output_dir": str(output_dir)}


def output_map():
    """The legacy-artifact map, as data rather than prose."""
    return [{
        "artifact": a.filename, "producer": a.producer, "purpose": a.purpose,
        "dialect": a.dialect.key, "columns": len(a.columns),
        "equivalence_target": a.target, "classification": a.classification,
        "scope": a.scope, "notes": a.notes,
    } for a in ARTIFACTS]
