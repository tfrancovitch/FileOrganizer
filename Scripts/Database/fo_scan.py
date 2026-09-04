#!/usr/bin/env python3
r"""
fo_scan.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, B2 (Python Inventory Engine)
Module version: 1.0.0   Requires Python: 3.11
===================================================================

The Python replacement for PreliminaryInventory.ps1's SCAN. It walks a
source root with os.scandir(), yields one structured record per file,
and produces the same side effects the accepted scan produces: the run
folder, Logs\errors.txt, Reports\PreliminaryReport.txt and the
settings.json fields the dashboard and TimeEstimates.ps1 read back.

WHAT REPLACED WHAT

R6's scanner is not really PowerShell. Windows PowerShell 5.1 cannot
enumerate past MAX_PATH through its FileSystem provider, so the script
compiles ~110 lines of C# at every run and P/Invokes
FindFirstFileW/FindNextFileW against \\?\ paths. os.scandir() calls
those same two functions, returns a stat_result populated from the
WIN32_FIND_DATA the walk already produced, and accepts \\?\ paths --
so this is a port onto the same Win32 primitives, not a different
technique with similar results.

WHAT IS DELIBERATELY IDENTICAL, NOT MERELY EQUIVALENT

  * The walk is a LIFO stack, not os.walk. DB_ID is a legacy identity
    allocated in encounter order, so the traversal order IS part of the
    output. R6 pushes the root, pops, enumerates, allocates ids to
    files in enumeration order and pushes subdirectories to be popped
    in reverse. os.walk visits directories in a different order and
    would renumber all 4,385 rows.

  * Each directory is materialised into a list inside the try, exactly
    as the C# Enumerate() does. It matters for failure, not for speed:
    R6 fails a directory WHOLE -- a FindNextFileW error part-way
    through produces one DIRECTORY ACCESS ERROR and no rows at all.
    Iterating the scandir handle lazily would emit the rows found
    before the failure, quietly turning one error into a partial
    inventory.

  * An empty directory is counted and skipped, junctions and symlinks
    are not recursed into, and cloud placeholder folders ARE recursed
    into. That last one is a real R6 decision (only the mount-point and
    symlink reparse tags block recursion), and it is preserved.

WHAT THIS MODULE DOES NOT DO

It does not open, read, rename, move, delete or re-stamp a single
source file. os.scandir() reads directory metadata, which is why the
inventory stage cannot change a last-access time -- the same property
R6 has and the same reason. There is no code path here that writes
anything outside the project's own run folder and settings.json.

It also does not touch the database. Persistence is fo_inventory's
job, and keeping the walk ignorant of it is what lets the engine be
tested without a project.
"""

import os
import sys
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import win_meta  # noqa: E402

MINIMUM_PYTHON = (3, 11)

#: Progress callbacks are time-based, never per-file. R6 throttles
#: Write-Progress to one update every 200ms for the same reason: at
#: 50,000 files a per-file update costs more than the scan.
PROGRESS_INTERVAL_SECONDS = 0.2

#: Largest-file and largest-folder tables in the preliminary report.
REPORT_TOP_N = 20

DIRECTORY_ACCESS_ERROR = "DIRECTORY ACCESS ERROR"
FILE_ERROR = "FILE ERROR"


def _path_order_key(path):
    r"""Deterministic ordering key for a path, for report tie-breaking.

    Case-folded so that ordering does not depend on how a name happens
    to be capitalised, with the raw path appended so two names
    differing only in case still order deterministically instead of
    tying. Mirrors fo_state.sort_key_for; kept local because fo_scan
    deliberately does not import the database layer.
    """
    text = (path or "")
    return (text.casefold(), text)


def _largest_sort_key(item):
    """Descending by size, then ascending by path. No ties possible."""
    size, path = item
    return (-(size or 0),) + _path_order_key(path)


class InventoryRecord(object):
    r"""One observed file, in the accepted fourteen fields.

    Timestamps arrive already rendered as ISO strings with whole-second
    precision, because that is what .NET's DateTime.ToString() shows
    and therefore all the accepted artifact ever contained. Carrying
    more precision than the accepted output has would invite a later
    stage to render it and diverge.

    B6 CARRIES BOTH REPRESENTATIONS, AND SAYS WHICH IS WHICH.

    `created` / `modified` / `accessed` remain the LOCAL wall-clock
    strings, because the report and the legacy-compatible export are
    written from them. `created_utc` / `modified_utc` / `accessed_utc`
    are the true UTC instants, and `utc_offset_minutes` records the
    offset that relates the two.

    B4.5 carried only the first set and stored it in columns named for
    the second. Carrying both, under honest names, is what closes
    B5-F.F003 without losing the display form the operator reads.
    """

    __slots__ = ("legacy_db_id", "file_name", "extension", "directory",
                 "path", "size_bytes", "created", "modified", "accessed",
                 "created_utc", "modified_utc", "accessed_utc",
                 "utc_offset_minutes",
                 "attributes", "is_reparse_point", "reparse_tag",
                 "is_offline_or_cloud", "depth", "path_length",
                 "volume_serial", "file_index", "hard_link_count",
                 "allocated_size_bytes")

    def __init__(self, legacy_db_id, file_name, extension, directory, path,
                 size_bytes, created, modified, accessed, attributes,
                 is_reparse_point, is_offline_or_cloud, depth, path_length,
                 created_utc=None, modified_utc=None, accessed_utc=None,
                 utc_offset_minutes=None, volume_serial=None,
                 file_index=None, hard_link_count=None, reparse_tag=None,
                 allocated_size_bytes=None):
        self.legacy_db_id = legacy_db_id
        self.file_name = file_name
        self.extension = extension
        self.directory = directory
        self.path = path
        self.size_bytes = size_bytes
        self.created = created
        self.modified = modified
        self.accessed = accessed
        self.created_utc = created_utc
        self.modified_utc = modified_utc
        self.accessed_utc = accessed_utc
        self.utc_offset_minutes = utc_offset_minutes
        self.attributes = attributes
        self.is_reparse_point = is_reparse_point
        self.reparse_tag = reparse_tag
        self.is_offline_or_cloud = is_offline_or_cloud
        self.depth = depth
        self.path_length = path_length
        # NULL means "not collected", never "no hard links".
        self.volume_serial = volume_serial
        self.file_index = file_index
        self.hard_link_count = hard_link_count
        self.allocated_size_bytes = allocated_size_bytes

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


class ScanError(object):
    """One inaccessible location, captured at the point of failure.

    Kept as its own type rather than as a record with empty fields.
    R6 found a real defect from blurring observed and inaccessible
    rows, and R3 persists them with status='inaccessible' precisely so
    "could not be read" stays distinguishable from "not there". The
    separation starts here, at capture, because that is the only place
    that still knows which one happened.
    """

    __slots__ = ("kind", "path", "message")

    def __init__(self, kind, path, message):
        self.kind = kind
        self.path = path
        self.message = message

    def as_line(self):
        r"""The Logs\errors.txt line shape R6 writes and R2/R3 parse."""
        return "%s: %s -- %s" % (self.kind, self.path, self.message)


class ScanStatistics(object):
    r"""Everything the preliminary report needs, accumulated as we go.

    R6 keeps every row in memory and queries the list afterwards. This
    keeps the same answers in bounded structures instead: the report
    needs per-extension totals, a depth histogram, the twenty largest
    files, the twenty largest top-level folders, a filename-collision
    count and a median. Only the median genuinely needs every value,
    and an array('q') holds a million of them in 8 MB.

    Grouping is case-insensitive and reports the FIRST spelling seen,
    which is what PowerShell's Group-Object does by default. Getting
    that wrong would split .JPG from .jpg in the report.
    """

    def __init__(self):
        self.file_count = 0
        self.total_bytes = 0
        self.empty_folder_count = 0
        self.empty_file_count = 0
        self.true_link_skipped_count = 0
        self.cloud_placeholder_folder_count = 0
        self.long_path_count = 0
        self.hidden_count = 0
        self.system_count = 0
        self.offline_count = 0
        self.max_depth = 0
        self.sizes = array("q")
        self.by_extension = {}       # key -> [display, count, bytes, order]
        self.by_depth = {}           # depth -> count
        self.by_top_level = {}       # key -> [display, bytes, count, order]
        self.by_file_name = {}       # key -> count
        self.largest = []            # list of (size, path)
        self.oldest = None           # (modified_iso, path)
        self.newest = None
        self.errors = []
        # Coverage facts that cannot be reconstructed from file rows.
        # Tuples are (event_kind, absolute_path, reparse_tag).
        self.path_events = []
        self._order = 0

    # -- accumulation --------------------------------------------------

    def add(self, record, root_path):
        self.file_count += 1
        size = record.size_bytes or 0
        self.total_bytes += size
        self.sizes.append(size)
        if size == 0:
            self.empty_file_count += 1
        if record.path_length > 260:
            self.long_path_count += 1

        attributes = record.attributes or ""
        # R6 uses -match, a substring regex over the rendered string.
        # Reproduced as a substring test so the two agree on every
        # combination, including "NotContentIndexed" (which contains
        # neither token) and "System" inside no other flag name.
        if "Hidden" in attributes:
            self.hidden_count += 1
        if "System" in attributes:
            self.system_count += 1
        if record.is_offline_or_cloud:
            self.offline_count += 1

        if record.depth > self.max_depth:
            self.max_depth = record.depth
        self.by_depth[record.depth] = self.by_depth.get(record.depth, 0) + 1

        self._bump(self.by_extension, record.extension or "", size)
        self._bump(self.by_top_level,
                   win_meta.top_level_folder(record.path, root_path), size)

        name_key = (record.file_name or "").lower()
        self.by_file_name[name_key] = self.by_file_name.get(name_key, 0) + 1

        self._offer_largest(size, record.path)
        self._offer_dates(record)

    def _bump(self, table, value, size):
        key = value.lower()
        entry = table.get(key)
        if entry is None:
            self._order += 1
            table[key] = [value, 1, size, self._order]
        else:
            entry[1] += 1
            entry[2] += size

    def _offer_largest(self, size, path):
        r"""The twenty largest files, kept without sorting the whole set.

        B6: TIES BROKEN BY PATH. THE FIX FOR B5-F.F002.

        B4.5's comment said ties keep "the earlier-encountered file".
        That is a statement about the WALK, and B5-F.F002 found the
        consequence: when equally-sized files arrived in a different
        order, top-N membership changed. Two machines inventorying the
        same disk could print different tables and both be following
        the rule.

        The tie-break is now the path -- a property of the data, not of
        the traversal. Because `sort_key_for` is a total order over
        distinct paths, and no two files in a project share a path, the
        ordering has NO remaining ties and cannot depend on arrival
        order at all.

        `_offer_dates` below takes the same treatment for the same
        reason: oldest and newest were strict comparisons, so a tie
        silently kept whichever file the walk reached first.
        """
        candidate = (size, path)
        if len(self.largest) < REPORT_TOP_N:
            self.largest.append(candidate)
            self.largest.sort(key=_largest_sort_key)
            return
        if _largest_sort_key(candidate) < _largest_sort_key(self.largest[-1]):
            self.largest.append(candidate)
            self.largest.sort(key=_largest_sort_key)
            del self.largest[REPORT_TOP_N:]

    def _offer_dates(self, record):
        stamp = record.modified
        if not stamp:
            return
        # Ties broken by path, so "the oldest file" is one answer rather
        # than whichever equally-old file the walk happened to reach
        # first. See _offer_largest.
        key = (stamp, _path_order_key(record.path))
        if self.oldest is None or key < (self.oldest[0],
                                         _path_order_key(self.oldest[1])):
            self.oldest = (stamp, record.path)
        newest_key = (stamp, _path_order_key(record.path))
        if self.newest is None or newest_key > (self.newest[0],
                                                _path_order_key(self.newest[1])):
            self.newest = (stamp, record.path)

    # -- derived -------------------------------------------------------

    def median_size(self):
        if not self.sizes:
            return 0
        ordered = sorted(self.sizes)
        middle = len(ordered) // 2
        if len(ordered) % 2 == 0:
            return (ordered[middle - 1] + ordered[middle]) / 2
        return ordered[middle]

    def average_size(self):
        if not self.sizes:
            return 0
        return sum(self.sizes) / len(self.sizes)

    def duplicate_name_groups(self):
        groups = [count for count in self.by_file_name.values() if count > 1]
        return len(groups), sum(groups)


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def scan(root_path, next_db_id=1, statistics=None, progress=None,
         should_continue=None):
    r"""Walk one source root, yielding an InventoryRecord per file.

    A GENERATOR ON PURPOSE. B2 does not implement pause/resume, but it
    is required not to make it harder, and a generator is what keeps
    that promise cheap: the caller already decides how many records to
    persist before committing, so a future pause is a check between two
    batches rather than a protocol between two processes. The optional
    should_continue hook is the seam that check will use; nothing in B2
    passes it, and when it is absent the loop is a plain loop.

    Yields records; records the inaccessible in `statistics.errors`.
    Never raises for one bad directory or one bad file -- that is the
    whole point of the two try blocks.
    """
    statistics = statistics if statistics is not None else ScanStatistics()
    root = win_meta.normalize_root(root_path)
    db_id = int(next_db_id) if int(next_db_id or 0) >= 1 else 1

    stack = [root]
    last_progress = time.monotonic()

    while stack:
        current = stack.pop()
        try:
            # Materialised inside the try, exactly as R6's Enumerate()
            # does. A failure part-way through a directory must fail the
            # DIRECTORY, not silently yield the entries found so far.
            entries = _list_directory(current)
        except OSError as exc:
            statistics.errors.append(
                ScanError(DIRECTORY_ACCESS_ERROR, current, _message(exc)))
            continue

        if not entries:
            statistics.empty_folder_count += 1
            statistics.path_events.append(("empty_directory", current, None))
            continue

        # B6: subdirectories are collected and pushed in REVERSE, so the
        # LIFO stack pops them in ascending order. B4.5 pushed them as
        # encountered, which -- once _list_directory started sorting --
        # would have produced a deterministic but backwards traversal.
        # Deterministic was the requirement; comprehensible as well
        # costs one list.
        subdirectories = []
        for entry, entry_stat in entries:
            try:
                attributes = win_meta.stat_attributes(entry_stat)
                is_reparse = win_meta.has_attribute(
                    attributes, win_meta.FILE_ATTRIBUTE_REPARSE_POINT)
                full_path = win_meta.join_child(current, entry.name)

                if win_meta.is_directory(attributes):
                    reparse_tag = win_meta.stat_reparse_tag(entry_stat)
                    if win_meta.is_true_link(attributes, reparse_tag):
                        statistics.true_link_skipped_count += 1
                        statistics.path_events.append(
                            ("skipped_reparse_directory", full_path, reparse_tag))
                    else:
                        if is_reparse:
                            statistics.cloud_placeholder_folder_count += 1
                        subdirectories.append(full_path)
                    continue

                # B6.2 P1 -- resolve physical identity from a full stat of
                # the path. The DirEntry stat is WIN32_FIND_DATA-backed on
                # Windows and carries no file index or link count; see
                # win_meta.physical_identity_for_path.
                volume_serial, file_index, hard_link_count = \
                    win_meta.physical_identity_for_path(full_path, entry_stat)
                reparse_tag = win_meta.stat_reparse_tag(entry_stat)
                allocated = win_meta.allocated_size_bytes(full_path, entry_stat)

                record = InventoryRecord(
                    legacy_db_id=db_id,
                    file_name=entry.name,
                    extension=win_meta.dotnet_extension(entry.name),
                    directory=win_meta.parent_of(full_path),
                    path=full_path,
                    size_bytes=int(entry_stat.st_size),
                    created=win_meta.local_iso_seconds(entry_stat.st_ctime_ns),
                    modified=win_meta.local_iso_seconds(entry_stat.st_mtime_ns),
                    accessed=win_meta.local_iso_seconds(entry_stat.st_atime_ns),
                    # B6: the canonical, machine-independent values.
                    # These are what get persisted; the three above are
                    # the display form. See win_meta.utc_iso_seconds.
                    created_utc=win_meta.utc_iso_seconds(entry_stat.st_ctime_ns),
                    modified_utc=win_meta.utc_iso_seconds(entry_stat.st_mtime_ns),
                    accessed_utc=win_meta.utc_iso_seconds(entry_stat.st_atime_ns),
                    utc_offset_minutes=win_meta.utc_offset_minutes(
                        entry_stat.st_mtime_ns),
                    attributes=win_meta.format_file_attributes(attributes),
                    is_reparse_point=is_reparse,
                    reparse_tag=reparse_tag,
                    # B6.2 P4b -- test ALL the cloud/offline attributes, not
                    # only the legacy FILE_ATTRIBUTE_OFFLINE. Modern OneDrive
                    # online-only placeholders carry RECALL_ON_DATA_ACCESS and
                    # do NOT set OFFLINE, so the old check flagged none of them
                    # and the hash pass would hydrate (download) them.
                    is_offline_or_cloud=win_meta.is_cloud_placeholder(attributes),
                    depth=win_meta.relative_depth(full_path, root),
                    path_length=win_meta.utf16_length(full_path),
                    volume_serial=volume_serial, file_index=file_index,
                    hard_link_count=hard_link_count,
                    allocated_size_bytes=allocated,
                )
                statistics.add(record, root)
                db_id += 1
                yield record

                if progress is not None:
                    now = time.monotonic()
                    if now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                        progress(statistics.file_count)
                        last_progress = now
            except Exception as exc:            # noqa: BLE001 -- see below
                # R6 catches everything at this point and records a FILE
                # ERROR rather than losing the rest of the directory.
                # Narrowing it to OSError would change which failures
                # abandon a scan, which is a behaviour change B2 is not
                # authorised to make.
                statistics.errors.append(
                    ScanError(FILE_ERROR,
                              win_meta.join_child(current, entry.name),
                              _message(exc)))

        # Reversed, so the LIFO pop order is ascending. See the note
        # where `subdirectories` is declared.
        stack.extend(reversed(subdirectories))

        if should_continue is not None and not should_continue():
            return


def _list_directory(directory):
    r"""One directory's entries, with their stat results, as a list.

    The \\?\ prefix is applied HERE and nowhere else: it is what lets
    the walk cross MAX_PATH, and it never reaches a record, because
    every path is rebuilt from `directory`, which is an ordinary path.
    That is the same boundary discipline R6 states in its own comment.

    entry.stat(follow_symlinks=False) does not cost a second syscall on
    Windows -- the values come from the WIN32_FIND_DATA the walk
    returned -- which is the same avoidance R6's C# helper was written
    to achieve.
    """
    target = directory
    if sys.platform == "win32":
        target = win_meta.to_extended_path(directory)
    entries = []
    with os.scandir(target) as handle:
        for entry in handle:
            entries.append((entry, entry.stat(follow_symlinks=False)))

    # B6: SORTED. THE FIX FOR HALF OF B5-F.F001.
    #
    # B4.5 took whatever order the filesystem handed back. On NTFS that
    # is usually B-tree order and looks stable; across filesystems,
    # across a copy, or after enough churn, it is not. B5-F measured
    # the consequence: group ids and export row order changed between
    # runs on identical content.
    #
    # Sorting here removes the volatility at its source, so the walk
    # itself is reproducible.
    #
    # IT IS NOT, BY ITSELF, THE FIX. A sorted walk still renumbers
    # everything after an inserted file, so `legacy_db_id` remains a
    # per-scan ordinal and B6 keeps it firmly out of durable identity
    # (see fo_state.sort_key_for and migration 006). Sorting makes one
    # scan reproducible; separating identity from order is what makes
    # the ids safe. Both are needed, and neither substitutes.
    #
    # The key is casefold()ed for ordering ONLY -- never for identity,
    # where migration 003's note about 'ß' -> 'ss' still applies. The
    # name itself is the tiebreaker, so two names differing only in
    # case still order deterministically rather than by arrival.
    entries.sort(key=lambda pair: (pair[0].name.casefold(), pair[0].name))
    return entries


def _message(exc):
    """A single-line message, so one error stays one errors.txt line."""
    text = getattr(exc, "strerror", None) or str(exc)
    return " ".join(str(text).split())


# ---------------------------------------------------------------------------
# Rendering helpers shared with the report
# ---------------------------------------------------------------------------

def format_bytes(value):
    r"""R6's Format-Bytes: 1024-based, two decimals, thousands separators.

    .NET's "N2" rounds halves away from zero; Python's format() rounds
    halves to even. The difference shows up on exactly the values a
    file size can land on, so the rounding is done explicitly rather
    than left to the default.
    """
    from decimal import Decimal, ROUND_HALF_UP

    units = ("B", "KB", "MB", "GB", "TB")
    amount = Decimal(str(float(value or 0)))
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount = amount / 1024
        index += 1
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return "{:,.2f} {}".format(quantized, units[index])


def format_count(value):
    """.NET's "N0" -- a thousands-separated integer."""
    return "{:,.0f}".format(value or 0)


def format_duration(seconds):
    """The 'hh\\:mm\\:ss' form Stopwatch.Elapsed.ToString() produces."""
    total = int(seconds)
    return "%02d:%02d:%02d" % (total // 3600, (total % 3600) // 60, total % 60)


def format_number(value):
    """A .NET double as PowerShell interpolates it: 4385.0 prints '4385'."""
    if value is None:
        return "0"
    if float(value) == int(float(value)):
        return str(int(float(value)))
    return repr(float(value))


# ---------------------------------------------------------------------------
# The preliminary report
# ---------------------------------------------------------------------------

def build_report(project_name, target_path, run_timestamp, generated,
                 statistics, elapsed_seconds, drive_type, timestamp_format,
                 disk_usage=None):
    r"""Reproduce PreliminaryReport.txt line for line.

    NOT byte-comparable to the accepted artifact, and honestly so: it
    carries the generation time and the scan duration, which differ on
    every run by construction. Everything else -- the section order,
    the column widths, the 1024-based sizes, the twenty-row tables, the
    grouping rules -- is reproduced, because the report is what the
    operator actually reads after a scan.

    Ordering note. PowerShell 5.1's Sort-Object is not a stable sort,
    so R6's tie order among equal-sized extensions or folders is not
    defined run-to-run. The sorts here are stable on first-encounter
    order, which is deterministic. That is a difference from R6, and it
    is a difference in favour of reproducibility in a table R6 itself
    could not reproduce twice.
    """
    lines = []
    rule = "=" * 70
    lines.append(rule)
    lines.append(" THE FILE ORGANIZER -- PRELIMINARY REPORT")
    lines.append(" Project   : %s" % (project_name or ""))
    lines.append(" Target    : %s" % target_path)
    lines.append(" Run       : %s" % run_timestamp)
    lines.append(" Generated : %s" % generated)
    lines.append(rule)
    lines.append("")

    if elapsed_seconds > 0:
        per_second = round(statistics.file_count / elapsed_seconds, 1)
    else:
        per_second = statistics.file_count

    lines.append("SCAN SUMMARY")
    lines.append("  Total files scanned      : %s" % format_count(statistics.file_count))
    lines.append("  Total size                : %s (%d bytes)"
                 % (format_bytes(statistics.total_bytes), statistics.total_bytes))
    lines.append("  Scan duration             : %s" % format_duration(elapsed_seconds))
    lines.append("  Files per second          : %s" % format_number(per_second))
    lines.append("")

    lines.append("FILE TYPE BREAKDOWN (by extension)")
    for display, count, total, _order in _sorted_by_size(statistics.by_extension):
        lines.append("  %-14s %8s files   %s"
                     % (display or "(none)", format_count(count),
                        format_bytes(total)))
    lines.append("")

    lines.append("FOLDER STRUCTURE")
    lines.append("  Maximum folder depth      : %d" % statistics.max_depth)
    lines.append("  Depth distribution:")
    for depth in sorted(statistics.by_depth):
        lines.append("    Depth %-4s: %s files"
                     % (depth, format_count(statistics.by_depth[depth])))
    lines.append("  Empty folders found       : %d" % statistics.empty_folder_count)
    lines.append("  Empty files found         : %d" % statistics.empty_file_count)
    lines.append("")

    lines.append("SIZE STATISTICS")
    lines.append("  Average file size         : %s" % format_bytes(statistics.average_size()))
    lines.append("  Median file size          : %s" % format_bytes(statistics.median_size()))
    lines.append("  Largest files:")
    for index, (size, path) in enumerate(statistics.largest, start=1):
        lines.append("    %2d. %s  (%s)" % (index, path, format_bytes(size)))
    lines.append("  Largest top-level folders:")
    for index, (display, count, total, _order) in enumerate(
            _sorted_by_size(statistics.by_top_level)[:REPORT_TOP_N], start=1):
        lines.append("    %2d. %s  (%s, %d files)"
                     % (index, display, format_bytes(total), count))
    lines.append("")

    group_count, file_count = statistics.duplicate_name_groups()
    lines.append("POTENTIAL DUPLICATE INDICATORS")
    lines.append("  Files sharing a filename with >=1 other file : %d "
                 "(across %d distinct names)" % (file_count, group_count))
    lines.append("")

    lines.append("DATES (by Last Write Time)")
    if statistics.oldest:
        lines.append("  Oldest file  : %s  (%s)"
                     % (statistics.oldest[1],
                        _render_stamp(statistics.oldest[0], timestamp_format)))
    if statistics.newest:
        lines.append("  Newest file  : %s  (%s)"
                     % (statistics.newest[1],
                        _render_stamp(statistics.newest[0], timestamp_format)))
    lines.append("")

    lines.append("SPECIAL CONDITIONS")
    lines.append("  Hidden files                              : %d" % statistics.hidden_count)
    lines.append("  System files                              : %d" % statistics.system_count)
    lines.append("  Symlinks / junctions (not recursed)       : %d"
                 % statistics.true_link_skipped_count)
    lines.append("  Cloud-sync placeholder folders (recursed) : %d"
                 % statistics.cloud_placeholder_folder_count)
    lines.append("  Cloud-only / not locally available files  : %d" % statistics.offline_count)
    lines.append("  Files with path length > 260 characters   : %d" % statistics.long_path_count)
    lines.append("")

    lines.append("ERRORS")
    lines.append("  Folders/files that could not be accessed  : %d" % len(statistics.errors))
    if statistics.errors:
        lines.append("  (see Logs\\errors.txt in this run folder for details)")
    lines.append("")

    lines.append("DISK SPACE")
    lines.append(_disk_space_block(target_path, statistics.total_bytes, disk_usage))
    lines.append("  Target drive type (detected)              : %s" % drive_type)
    lines.append(rule)
    return lines


def _sorted_by_size(table):
    """Descending by total bytes, ties in first-encounter order."""
    return sorted(table.values(), key=lambda entry: (-entry[2], entry[3]))


def _render_stamp(iso, timestamp_format):
    """The one renderer. Shared with the CSV export, deliberately."""
    import fo_exports
    return fo_exports.render_locale_timestamp(iso, timestamp_format) or ""


def _disk_space_block(target_path, total_bytes, disk_usage=None):
    r"""R6's four-line drive block, or its one-line failure text.

    Best-effort in R6 -- a network path that cannot answer produces the
    fallback line and the scan continues. Preserved, including the
    exact fallback wording, because it is what the operator sees.
    """
    fallback = "  Not available (could not determine drive information)"
    try:
        drive_root = os.path.splitdrive(target_path)[0]
        drive_root = (drive_root + "\\") if drive_root else target_path
        if disk_usage is None:
            import shutil
            usage = shutil.disk_usage(target_path)
            total, free = usage.total, usage.free
        else:
            total, free = disk_usage
        used_percent = round((total_bytes / total) * 100, 2) if total > 0 else 0
        return "\r\n".join([
            "  Drive                       : %s" % drive_root,
            "  Total capacity              : %s" % format_bytes(total),
            "  Free space remaining        : %s" % format_bytes(free),
            "  This inventory's size       : %s (%s%% of drive)"
            % (format_bytes(total_bytes), format_number(used_percent)),
        ])
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# The accepted side effects
# ---------------------------------------------------------------------------

#: Windows PowerShell 5.1's `Set-Content -Encoding UTF8` writes a BOM
#: and CRLF line endings and terminates the final line. PowerShell 7
#: does not write the BOM, which is exactly the kind of difference that
#: makes "it's just a text file" wrong: fo_inventory reads errors.txt
#: with utf-8-sig because R6 puts a BOM there.
_PS_BOM = b"\xef\xbb\xbf"


def write_text_lines(path, lines):
    """Write a text artifact exactly as Set-Content -Encoding UTF8 does."""
    payload = "\r\n".join(str(line) for line in lines) + "\r\n"
    with open(path, "wb") as handle:
        handle.write(_PS_BOM)
        handle.write(payload.encode("utf-8"))
    return path


def write_errors_file(path, errors):
    r"""Logs\errors.txt, written only when there is something to say.

    R6 writes no file at all when nothing failed, and the accepted run
    has no errors.txt. Writing an empty one would look like a scan that
    recorded zero errors rather than one that had none, and R2's event
    ingestion would open it on every clean run.
    """
    if not errors:
        return None
    return write_text_lines(path, [error.as_line() for error in errors])


def console_summary(target_path, run_folder, statistics, elapsed_seconds):
    r"""The text R6 printed to the host, rebuilt for the stage capture.

    RunCoordinator derives events from a stage's captured output and
    the dashboard shows it, so a stage that produced no output at all
    would be a visible regression in the run log even though the scan
    itself succeeded.
    """
    return "\n".join([
        "Scanning: %s" % target_path,
        "Run folder: %s" % run_folder,
        "",
        "Preliminary inventory complete.",
        "  Files scanned : %d" % statistics.file_count,
        "  Total size    : %s" % format_bytes(statistics.total_bytes),
        "  Duration      : %s" % format_duration(elapsed_seconds),
        "  Run folder    : %s" % run_folder,
        "",
    ])


#: The settings.json fields the inventory stage owns, in the order R6
#: adds them when they are absent. Order is preserved so a project's
#: settings.json does not reshuffle the first time the Python engine
#: touches it.
SETTINGS_FIELDS = (
    "LastPreliminaryScan", "TargetDriveType", "LastPreliminaryFileCount",
    "LastPreliminaryErrorCount", "LastPreliminaryTotalBytes",
    "LastPreliminaryLongPathCount",
)


def dotnet_round_trip_now():
    r"""(Get-Date).ToString("o") -- seven fractional digits and an offset.

    Nothing in the application parses this field, but it is written
    into a file the PowerShell stages read back, and a shape they have
    never seen is a needless difference. Python gives six digits; .NET
    gives seven.
    """
    from datetime import datetime
    stamp = datetime.now().astimezone()
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + \
        "%06d0" % stamp.microsecond + stamp.strftime("%z")[:3] + ":" + \
        stamp.strftime("%z")[3:]


def update_settings(settings_path, run_timestamp, next_db_id, statistics,
                    drive_type, now=None):
    r"""Write back exactly the fields PreliminaryInventory.ps1 writes.

    Key ORDER is preserved and new keys are appended, because
    ConvertTo-Json emits properties in their existing order and a
    reshuffled settings.json is a diff nobody asked for.

    The file is written the way PowerShell writes it -- UTF-8 with BOM,
    CRLF, two-space indent, non-ASCII escaped -- so that a project's
    settings.json has the same on-disk shape after a Python scan as
    after a PowerShell one. Dashboard.py already reads it with
    utf-8-sig for this reason.
    """
    import json
    from collections import OrderedDict

    with open(settings_path, "r", encoding="utf-8-sig") as handle:
        settings = json.load(handle, object_pairs_hook=OrderedDict)

    settings["NextDBID"] = next_db_id
    settings["CurrentRun"] = run_timestamp
    history = settings.get("RunHistory") or []
    if not isinstance(history, list):
        history = [history]
    settings["RunHistory"] = list(history) + [run_timestamp]

    for field in SETTINGS_FIELDS:
        if field not in settings:
            settings[field] = None

    settings["LastPreliminaryScan"] = now or dotnet_round_trip_now()
    settings["TargetDriveType"] = drive_type
    settings["LastPreliminaryFileCount"] = statistics.file_count
    settings["LastPreliminaryErrorCount"] = len(statistics.errors)
    settings["LastPreliminaryTotalBytes"] = statistics.total_bytes
    settings["LastPreliminaryLongPathCount"] = statistics.long_path_count

    text = json.dumps(settings, indent=2, ensure_ascii=True)
    payload = text.replace("\n", "\r\n") + "\r\n"
    with open(settings_path, "wb") as handle:
        handle.write(_PS_BOM)
        handle.write(payload.encode("utf-8"))
    return settings


def create_run_folder(project_dir, run_timestamp):
    r"""Mint Runs\<stamp>\{Inventory,Reports,Logs}, as the scan does today.

    The run folder is the inventory stage's to create -- R6 mints it
    part-way through the scan, which is why RunCoordinator.bind_run_folder
    exists at all. Creating it up front instead means the coordinator can
    bind before the walk starts, so a directory error in the first
    second lands in the run log rather than in a memory buffer.
    """
    run_folder = os.path.join(str(project_dir), "Runs", run_timestamp)
    for name in ("Inventory", "Reports", "Logs"):
        os.makedirs(os.path.join(run_folder, name), exist_ok=True)
    return run_folder


def run_timestamp_now():
    """The Runs\\<stamp> folder name: Get-Date -Format 'yyyy-MM-dd_HHmmss'."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def generated_stamp_now():
    """The report's 'Generated' line: 'yyyy-MM-dd HH:mm:ss'."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
