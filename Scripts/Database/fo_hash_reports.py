#!/usr/bin/env python3
r"""
fo_hash_reports.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, B3 (Python Hash & Duplicate Engine)
Module version: 1.0.0   Requires schema version: 5
===================================================================

Renders the three human-readable reports the R6 hash pipeline wrote:

    PotentialDuplicatesReport.txt      (PotentialDuplicates.ps1)
    PartialHashReport.txt              (PartialHash.ps1)
    DuplicateHashInventoryReport.txt   (FullHash.ps1)
    FullHashInventoryReport.txt        (FullHashInventory.ps1)

WHY THESE ARE REBUILT RATHER THAN EXPORTED

The CSVs are exports: fo_exports renders them from the database and
R5 proves them byte-identical. These reports are not in that table --
PowerShell built them inline, from state it held in memory, and never
persisted the intermediate figures. So B3 has to render them, and
rendering them means reproducing .NET's number formatting rather than
Python's.

.NET NUMBER FORMATTING, REPRODUCED DELIBERATELY

Three separate rules, and they are not the same rule:

  "{0:N2}" / "{0:N0}"  .NET Framework composite formatting rounds
                       AWAY FROM ZERO. Python's format() and round()
                       use banker's rounding, so 2.005 renders "2.01"
                       in PowerShell and "2.00" in Python. Every
                       Format-Bytes value in these reports goes through
                       this path.

  [math]::Round(x, n)  .NET's Math.Round defaults to TO EVEN --
                       the opposite. The two percentages in the
                       Potential Duplicates report use this one.

  double -> string     An integral double prints without a decimal
                       point, which is why the accepted report says
                       "100% of bytes" and not "100.0%".

Getting these wrong produces a report that is right to the eye and
wrong to a byte comparison, which is the failure mode this module
exists to avoid. `dotnet_fixed`, `dotnet_round` and `dotnet_double`
below implement the three rules separately and are used deliberately,
never interchangeably.

ENCODING

Set-Content -Encoding UTF8 on Windows PowerShell 5.1 writes a UTF-8
BOM and CRLF line endings, and the accepted reports have both. So does
this.

VOLATILE FIELDS

A handful of lines cannot be byte-compared across runs and are marked
as such in VOLATILE_LINE_PREFIXES rather than being quietly excluded:
the generation timestamp, elapsed time, throughput, and the
"hashed this run" counter, which legitimately differs when a run
resumes. Everything else is stable and is compared byte-for-byte.
"""

import os
import sys
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


BOM = b"\xef\xbb\xbf"
NEWLINE = "\r\n"
RULE = "=" * 70
MAX_GROUPS_SHOWN = 20
MAX_PATHS_SHOWN = 5

UNITS = ("B", "KB", "MB", "GB", "TB")

#: Lines whose values legitimately change between two correct runs.
#: Named explicitly so stable-field parity stays strict instead of
#: being loosened to accommodate them.
VOLATILE_LINE_PREFIXES = (
    " Generated :",
    "  Processing time",
    "  Throughput",
    "  Hashed this run",
    "  Files fully hashed this run",
    "  Bytes read",
)


# ---------------------------------------------------------------------------
# .NET number formatting
# ---------------------------------------------------------------------------

def dotnet_fixed(value, digits=2, thousands=True):
    """.NET composite formatting: "{0:N2}" / "{0:N0}". Away from zero."""
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(str(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    text = ("{:,.%df}" % digits).format(rounded)
    return text if thousands else text.replace(",", "")


def dotnet_round(value, digits):
    """.NET Math.Round(x, n): to even. Returns a float."""
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(str(float(value))).quantize(
        quantum, rounding=ROUND_HALF_EVEN))


def dotnet_double(value):
    """A double rendered as PowerShell interpolates it: 100.0 -> "100"."""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return repr(number)


def format_bytes(value):
    """Format-Bytes, from Common's copy in every hash script."""
    number = float(value or 0)
    index = 0
    while number >= 1024 and index < len(UNITS) - 1:
        number /= 1024.0
        index += 1
    return "%s %s" % (dotnet_fixed(number, 2), UNITS[index])


def format_count(value):
    """"{0:N0}" -- thousands separated, no decimals."""
    return dotnet_fixed(value or 0, 0)


def format_duration(seconds):
    """PowerShell's TimeSpan 'hh\\:mm\\:ss'."""
    total = int(seconds or 0)
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------

def _header(title, project_name, run_folder, generated):
    return [RULE,
            " THE FILE ORGANIZER -- %s" % title,
            " Project   : %s" % (project_name or ""),
            " Run       : %s" % (run_folder or ""),
            " Generated : %s" % (generated or ""),
            RULE,
            ""]


def _group_listing(lines, groups, max_groups=MAX_GROUPS_SHOWN,
                   suffix_for=None, csv_name=None, id_column=None,
                   qualifier=""):
    r"""The "Group N | X files x SIZE = [up to ]Y reclaimable" block.

    One renderer for all four reports, because they differ only in the
    qualifier, a trailing annotation, and the CSV they point at. Two
    renderers that agreed on the day they were written would not stay
    agreed.

    The qualifier is not decoration. "up to" appears in the two
    pre-confirmation reports because at that point the reclaim figure
    is a ceiling on files that might still turn out to differ; the
    final reports drop it because by then the duplicates are confirmed
    and the number is real.
    """
    for group in groups[:max_groups]:
        suffix = suffix_for(group) if suffix_for else ""
        lines.append("  Group %3d | %d files x %s = %s%s reclaimable%s"
                     % (group.group_id, group.count, format_bytes(group.size),
                        qualifier, format_bytes(group.reclaimable), suffix))
        for member in group.members[:MAX_PATHS_SHOWN]:
            lines.append("      - %s" % member.path)
        if group.count > MAX_PATHS_SHOWN:
            lines.append("      ... and %d more (see %s, %s = %d)"
                         % (group.count - MAX_PATHS_SHOWN, csv_name,
                            id_column, group.group_id))


def _ranked(groups):
    r"""By potential reclaim descending, ties broken by group id ascending.

    R6 sorts this listing with Sort-Object, which is NOT a stable sort
    -- .NET's introsort leaves equal keys in an order that is an
    artefact of the partitioning, not of the data. On the controlled
    suite that shows up as 24 confirmed groups all worth exactly
    256.00 KB being printed in an order R6 itself would not necessarily
    reproduce on a second run.

    B3 will not chase that. Reproducing another runtime's unstable
    partition order is not behaviour worth preserving, and it is the
    one thing in the accepted artifact that is not reproducible even by
    the accepted artifact's own producer. So ties break on group id,
    which is deterministic, and the difference is declared explicitly
    in the B3 report rather than hidden. The SET of groups, their ids,
    sizes, counts and reclaim figures are unaffected.
    """
    return sorted(groups, key=lambda g: (-g.reclaimable, g.group_id))


def write_report(path, lines):
    """UTF-8 with BOM, CRLF, trailing newline -- Set-Content -Encoding UTF8."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    text = NEWLINE.join(lines) + NEWLINE
    with open(path, "wb") as handle:
        handle.write(BOM)
        handle.write(text.encode("utf-8"))
    return path


# ---------------------------------------------------------------------------
# Partial-stage groups, rebuilt from results
# ---------------------------------------------------------------------------

class _PartialGroup(object):
    """A partial-hash bucket, shaped like DuplicateGroup for the renderer."""

    __slots__ = ("group_id", "size", "members")

    def __init__(self, group_id, size, members):
        self.group_id = group_id
        self.size = int(size or 0)
        self.members = members

    @property
    def count(self):
        return len(self.members)

    @property
    def reclaimable(self):
        return self.size * max(self.count - 1, 0)


def partial_groups(results, status):
    """Buckets carrying `status` at the end of the partial-hash stage."""
    buckets = {}
    for result in results:
        if result.partial_status == status and result.partial_group_id:
            buckets.setdefault(result.partial_group_id, []).append(result)
    return [_PartialGroup(gid, members[0].size, members)
            for gid, members in buckets.items()]


# ---------------------------------------------------------------------------
# 1. PotentialDuplicatesReport.txt
# ---------------------------------------------------------------------------

def potential_duplicates_report(project_name, run_folder, generated, outcome,
                                total_bytes, candidate_bytes, elapsed_seconds):
    total_files = len(outcome.results)
    candidates = outcome.candidate_count
    unique_by_size = total_files - candidates

    percent_files = (dotnet_round(candidates / total_files * 100, 1)
                     if total_files else 0)
    percent_bytes = (dotnet_round(candidate_bytes / total_bytes * 100, 1)
                     if total_bytes else 0)

    groups = []
    by_group = {}
    for result in outcome.results:
        if result.size_group_id:
            by_group.setdefault(result.size_group_id, []).append(result)
    for gid in sorted(by_group):
        members = by_group[gid]
        groups.append(_PartialGroup(gid, members[0].size, members))
    # Already numbered by descending reclaim, so this ordering is the
    # numbering -- sorting again would be a no-op that hid a mistake.
    max_reclaim = sum(g.reclaimable for g in groups)

    lines = _header("POTENTIAL DUPLICATES REPORT", project_name, run_folder,
                    generated)
    lines.append("SUMMARY")
    lines.append("  Total files in inventory        : %s" % format_count(total_files))
    lines.append("  Files unique by size (excluded)  : %s" % format_count(unique_by_size))
    lines.append("  Files with a size-match (kept)   : %s (%s%% of files, %s%% of bytes)"
                 % (format_count(candidates), dotnet_double(percent_files),
                    dotnet_double(percent_bytes)))
    lines.append("  Distinct size-groups found       : %s"
                 % format_count(outcome.size_group_count))
    lines.append("  Max theoretical reclaimable space: %s" % format_bytes(max_reclaim))
    lines.append("    (assumes every file in every group turns out to be a true")
    lines.append("     duplicate, keeping only one copy -- Script 3/4 will confirm)")
    lines.append("  Processing time                  : %s" % format_duration(elapsed_seconds))
    lines.append("")
    lines.append("TOP SIZE-GROUPS (by potential reclaimable space)")
    _group_listing(lines, groups, suffix_for=lambda g: "",
                   csv_name="PotentialDuplicates.csv", id_column="SizeGroupID",
                   qualifier="up to ")
    lines.append("")
    lines.append("NEXT STEP")
    lines.append("  These are SIZE matches only -- not yet confirmed duplicates.")
    lines.append("  Run the next script (partial-hash pass) to narrow these down.")
    lines.append(RULE)
    return lines


# ---------------------------------------------------------------------------
# 2. PartialHashReport.txt
# ---------------------------------------------------------------------------

def partial_hash_report(project_name, run_folder, generated, outcome,
                        elapsed_seconds, hashed_this_run=None):
    import fo_hash_engine as engine

    results = outcome.results
    ruled_out = outcome.count_partial_status(engine.STATUS_RULED_OUT)
    confirmed = outcome.count_partial_status(engine.STATUS_CONFIRMED)
    needs_full = outcome.count_partial_status(engine.STATUS_NEEDS_FULL)
    skipped = outcome.count_partial_status(engine.STATUS_SKIPPED_CLOUD)
    errors = sum(1 for r in results
                 if r.partial_status == engine.STATUS_ERROR and r.size_group_id)
    cloud_hashed = sum(1 for r in results
                       if r.is_offline_or_cloud and r.partial_hash)

    confirmed_groups = _ranked(partial_groups(results, engine.STATUS_CONFIRMED))
    needs_groups = _ranked(partial_groups(results, engine.STATUS_NEEDS_FULL))
    confirmed_reclaim = sum(g.reclaimable for g in confirmed_groups)
    needs_reclaim = sum(g.reclaimable for g in needs_groups)

    if hashed_this_run is None:
        hashed_this_run = outcome.partial_hashed
    throughput = (outcome.bytes_read / elapsed_seconds
                  if elapsed_seconds and elapsed_seconds > 0 else 0)

    lines = _header("PARTIAL HASH REPORT", project_name, run_folder, generated)
    lines.append("SUMMARY")
    lines.append("  Candidate files (total)            : %s"
                 % format_count(outcome.candidate_count))
    lines.append("  Hashed this run                    : %s" % format_count(hashed_this_run))
    lines.append("  Ruled out (not actually duplicates): %s" % format_count(ruled_out))
    lines.append("  Confirmed duplicates (fully hashed): %s files, %d groups, %s reclaimable"
                 % (format_count(confirmed), len(confirmed_groups),
                    format_bytes(confirmed_reclaim)))
    lines.append("  Still needs full hash (Script 4)   : %s files, %d groups, up to %s reclaimable"
                 % (format_count(needs_full), len(needs_groups),
                    format_bytes(needs_reclaim)))
    if skipped > 0:
        lines.append("  Skipped (cloud-only, not hashed)   : %s" % format_count(skipped))
    if errors > 0:
        lines.append("  Errors (could not be hashed)       : %s (see Logs\\errors_partialhash.txt)"
                     % format_count(errors))
    lines.append("  Cloud-only files hashed (may have triggered a download): %s"
                 % format_count(cloud_hashed))
    lines.append("")
    lines.append("PERFORMANCE")
    lines.append("  Bytes read      : %s" % format_bytes(outcome.bytes_read))
    lines.append("  Processing time : %s" % format_duration(elapsed_seconds))
    lines.append("  Throughput      : %s/sec" % format_bytes(throughput))
    lines.append("")
    lines.append("CONFIRMED DUPLICATE GROUPS (fully hashed -- no full hash needed)")
    if not confirmed_groups:
        lines.append("  (none)")
    else:
        _group_listing(lines, confirmed_groups,
                       suffix_for=lambda g: "",
                       csv_name="PartialHashCandidates.csv",
                       id_column="PartialHashGroupID",
                       qualifier="up to ")
    lines.append("")
    lines.append("GROUPS STILL NEEDING A FULL HASH (hand off to Script 4)")
    if not needs_groups:
        lines.append("  (none)")
    else:
        _group_listing(lines, needs_groups,
                       suffix_for=lambda g: "",
                       csv_name="PartialHashCandidates.csv",
                       id_column="PartialHashGroupID",
                       qualifier="up to ")
    lines.append("")
    lines.append("NEXT STEP")
    lines.append("  Run the full-hash script next -- it only needs to process files")
    lines.append("  marked Status = NeedsFullHash in PartialHashCandidates.csv.")
    lines.append(RULE)
    return lines


# ---------------------------------------------------------------------------
# 3 / 4. The inventory-shaped reports
# ---------------------------------------------------------------------------

def _inventory_summary_block(lines, meta_rows):
    """The SCAN SUMMARY + FILE TYPE BREAKDOWN block, shared by both.

    Note the deliberately uneven padding on "Total files": R6 writes it
    one space short of the lines beneath it. That is not a typo being
    copied for its own sake -- it is in the accepted artifact, and a
    byte comparison would fail without it.
    """
    total_files = len(meta_rows)
    total_bytes = sum(r["size"] for r in meta_rows)
    max_depth = max((r["depth"] for r in meta_rows), default=0)
    empty = sum(1 for r in meta_rows if r["size"] == 0)
    hidden = sum(1 for r in meta_rows if "Hidden" in (r["attributes"] or ""))
    system = sum(1 for r in meta_rows if "System" in (r["attributes"] or ""))
    long_paths = sum(1 for r in meta_rows if (r["path_length"] or 0) > 260)

    lines.append("  Total files              : %s" % format_count(total_files))
    lines.append("  Total size                : %s (%d bytes)"
                 % (format_bytes(total_bytes), total_bytes))
    lines.append("  Maximum folder depth      : %d" % max_depth)
    lines.append("  Empty files               : %d" % empty)
    lines.append("  Hidden files              : %d" % hidden)
    lines.append("  System files              : %d" % system)
    lines.append("  Paths over 260 characters : %d" % long_paths)
    lines.append("")
    lines.append("FILE TYPE BREAKDOWN (by extension)")

    order, by_ext = [], {}
    for row in meta_rows:
        key = row["extension"] or "(none)"
        if key not in by_ext:
            by_ext[key] = [0, 0]
            order.append(key)
        by_ext[key][0] += 1
        by_ext[key][1] += row["size"]
    for key in sorted(order, key=lambda k: by_ext[k][1], reverse=True):
        count, size = by_ext[key]
        lines.append("  %-14s %8s files   %s"
                     % (key, format_count(count), format_bytes(size)))
    return total_files, total_bytes


def _resolution_summary(lines, meta_rows):
    """Status counts in FIRST-APPEARANCE order, as the Dictionary gave them."""
    order, counts = [], {}
    for row in meta_rows:
        status = row["final_status"]
        if status not in counts:
            counts[status] = 0
            order.append(status)
        counts[status] += 1
    for status in order:
        lines.append("  %-24s: %s" % (status, format_count(counts[status])))


def duplicate_hash_inventory_report(project_name, run_folder, generated,
                                    outcome, meta_rows, elapsed_seconds,
                                    prelim_file_count=None,
                                    prelim_total_bytes=None):
    lines = _header("DUPLICATE HASH INVENTORY REPORT", project_name,
                    run_folder, generated)
    lines.append("SCAN SUMMARY (recomputed independently from DuplicateHashInventory.csv)")
    total_files, total_bytes = _inventory_summary_block(lines, meta_rows)
    lines.append("")
    lines.append("DRIFT CHECK (vs. PreliminaryInventory.csv from this same run)")
    drift_files = total_files - (prelim_file_count
                                 if prelim_file_count is not None else total_files)
    drift_bytes = total_bytes - (prelim_total_bytes
                                 if prelim_total_bytes is not None else total_bytes)
    lines.append("  File count difference : %d" % drift_files)
    lines.append("  Byte count difference : %d" % drift_bytes)
    if drift_files != 0 or drift_bytes != 0:
        lines.append("  ** Non-zero drift -- files may have changed since the Preliminary scan **")
    lines.append("")
    lines.append("DUPLICATE RESOLUTION SUMMARY")
    _resolution_summary(lines, meta_rows)
    lines.append("")

    ranked = _ranked(outcome.groups)
    lines.append("  Confirmed duplicate groups : %s" % format_count(len(ranked)))
    lines.append("  Redundant files (all but one per group): %s"
                 % format_count(outcome.redundant_file_count))
    lines.append("  Confirmed reclaimable space: %s"
                 % format_bytes(outcome.reclaimable_bytes))
    lines.append("")
    lines.append("PERFORMANCE")
    lines.append("  Files fully hashed this run : %d" % outcome.full_hashed)
    lines.append("  Bytes read (full hash pass) : %s" % format_bytes(outcome.bytes_read))
    lines.append("  Processing time             : %s" % format_duration(elapsed_seconds))
    if outcome.error_count:
        lines.append("  Errors (could not be hashed): %d (see Logs\\errors_fullhash.txt)"
                     % outcome.error_count)
    lines.append("")
    lines.append("CONFIRMED DUPLICATE GROUPS (final)")
    if not ranked:
        lines.append("  (none)")
    else:
        _group_listing(lines, ranked,
                       suffix_for=lambda g: "  [confirmed via: %s]" % g.confirmed_at,
                       csv_name="DuplicateHashInventory.csv",
                       id_column="DuplicateGroupID")
        if len(ranked) > MAX_GROUPS_SHOWN:
            lines.append("")
            lines.append("  ... and %d more group(s) not shown here (see "
                         "DuplicateHashInventory.csv for the full list)"
                         % (len(ranked) - MAX_GROUPS_SHOWN))
    lines.append(RULE)
    return lines


def full_hash_inventory_report(project_name, run_folder, generated, outcome,
                               meta_rows, elapsed_seconds,
                               prelim_file_count=None, prelim_total_bytes=None):
    """The Full Run's report. Same shape, different vocabulary -- and the
    difference is the point: every file here was actually hashed."""
    lines = _header("FULL HASH INVENTORY REPORT", project_name, run_folder,
                    generated)
    lines.append("SCAN SUMMARY (recomputed independently from FullHashInventory.csv)")
    total_files, total_bytes = _inventory_summary_block(lines, meta_rows)
    lines.append("")
    lines.append("DRIFT CHECK (vs. PreliminaryInventory.csv from this same run)")
    drift_files = total_files - (prelim_file_count
                                 if prelim_file_count is not None else total_files)
    drift_bytes = total_bytes - (prelim_total_bytes
                                 if prelim_total_bytes is not None else total_bytes)
    lines.append("  File count difference : %d" % drift_files)
    lines.append("  Byte count difference : %d" % drift_bytes)
    lines.append("")
    lines.append("HASH RESOLUTION SUMMARY")
    _resolution_summary(lines, meta_rows)
    lines.append("")

    ranked = _ranked(outcome.groups)
    lines.append("  Confirmed duplicate groups : %s" % format_count(len(ranked)))
    lines.append("  Redundant files (all but one per group): %s"
                 % format_count(outcome.redundant_file_count))
    lines.append("  Confirmed reclaimable space: %s"
                 % format_bytes(outcome.reclaimable_bytes))
    lines.append("")
    lines.append("PERFORMANCE")
    lines.append("  Files fully hashed this run : %d" % outcome.full_hashed)
    lines.append("  Bytes read                  : %s" % format_bytes(outcome.bytes_read))
    lines.append("  Processing time             : %s" % format_duration(elapsed_seconds))
    if outcome.error_count:
        lines.append("  Errors (could not be hashed): %d (see Logs\\errors_fullhashinventory.txt)"
                     % outcome.error_count)
    lines.append("")
    lines.append("CONFIRMED DUPLICATE GROUPS (final)")
    if not ranked:
        lines.append("  (none)")
    else:
        _group_listing(lines, ranked, suffix_for=lambda g: "",
                       csv_name="FullHashInventory.csv",
                       id_column="DuplicateGroupID")
        if len(ranked) > MAX_GROUPS_SHOWN:
            lines.append("")
            lines.append("  ... and %d more group(s) not shown here (see "
                         "FullHashInventory.csv for the full list)"
                         % (len(ranked) - MAX_GROUPS_SHOWN))
    lines.append(RULE)
    return lines


# ---------------------------------------------------------------------------
# Error logs
# ---------------------------------------------------------------------------

def write_error_log(path, errors, prefix="HASH ERROR"):
    r"""One line per failed file, as R6's Add-Content wrote them.

    Written only when there is something to write: R6 creates the file
    lazily via Add-Content, so an empty errors file would be a new
    artifact rather than a reproduced one.
    """
    if not errors:
        return None
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    lines = ["%s: %s -- %s" % (prefix, path_text, message)
             for path_text, _kind, message in errors]
    return write_report(path, lines)


def is_volatile_line(line):
    """True if this line carries run metadata that legitimately varies."""
    return any(line.startswith(prefix) for prefix in VOLATILE_LINE_PREFIXES)
