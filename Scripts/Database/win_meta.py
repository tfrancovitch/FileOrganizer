#!/usr/bin/env python3
r"""
win_meta.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, B2 (Python Inventory Engine)
Module version: 1.0.0   Requires Python: 3.11
===================================================================

The ONLY Windows-specific module in the B-series runtime. Everything
that depends on a Win32 detail, a .NET presentation convention or a
Windows path rule lives here, so the inventory engine itself is
ordinary Python that can be read and tested anywhere.

WHAT IS IN HERE AND WHY

  1. FileAttributes rendering. R6 stores the .NET
     `FileAttributes.ToString()` string -- `Archive`,
     `ReadOnly, Archive`. That is a PRESENTATION CONTRACT, not a
     storage format, and R6 export equivalence is byte-exact, so the
     flag names, their ORDER and the separator all have to be
     reproduced exactly. See format_file_attributes().

  2. .NET Path.GetExtension. Differs from os.path.splitext on two real
     inputs (`.gitignore`, `trailing.`), and the difference reaches the
     database through file_path.extension_key.

  3. FILETIME -> local wall-clock seconds. R6 renders whole seconds of
     LOCAL time; Python gives integer nanoseconds since the Unix epoch.
     The conversion has to floor rather than round, and has to be done
     from *_ns rather than the float fields, or a value one microsecond
     short of a second boundary renders one second early.

  4. Extended-length (\\?\) paths, used ONLY at the Win32 API edge.
     Every path this module returns for storage or export is an
     ordinary Windows path, exactly as in R6.

  5. Drive type, for the time-estimate calibration signal.

NON-WINDOWS BEHAVIOUR
Every function here is importable and unit-testable on any platform.
The Windows-only calls are guarded and degrade to the same fallbacks
R6 uses ("Unknown"), because a calibration signal must never be able
to stop a scan -- that is R6's rule and it is preserved.
"""

import os
import stat as stat_module
import sys
from datetime import datetime, timedelta, timezone

MODULE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 1. .NET FileAttributes rendering
# ---------------------------------------------------------------------------

#: The members of System.IO.FileAttributes as it exists in the .NET
#: Framework 4.x that Windows PowerShell 5.1 runs on, which is what
#: produced the accepted R6 artifact.
#:
#: DERIVED, NOT GUESSED. The accepted 4,385-row inventory contains two
#: renderings -- `Archive` and `ReadOnly, Archive` -- and the second one
#: settles the ordering question the audit flagged (B1 3.C-2): ReadOnly
#: is 1, Archive is 32, and ReadOnly is written first, so the order is
#: ASCENDING BY VALUE. That is also what Enum's flags formatter does,
#: which is the corroboration rather than the evidence.
#:
#: Listed low-to-high so the rendered order is the list order.
_FILE_ATTRIBUTE_MEMBERS = (
    (0x00000001, "ReadOnly"),
    (0x00000002, "Hidden"),
    (0x00000004, "System"),
    (0x00000010, "Directory"),
    (0x00000020, "Archive"),
    (0x00000040, "Device"),
    (0x00000080, "Normal"),
    (0x00000100, "Temporary"),
    (0x00000200, "SparseFile"),
    (0x00000400, "ReparsePoint"),
    (0x00000800, "Compressed"),
    (0x00001000, "Offline"),
    (0x00002000, "NotContentIndexed"),
    (0x00004000, "Encrypted"),
    (0x00008000, "IntegrityStream"),
    (0x00020000, "NoScrubData"),
)

#: Individual bit constants, named as the callers want to read them.
FILE_ATTRIBUTE_READONLY = 0x00000001
FILE_ATTRIBUTE_HIDDEN = 0x00000002
FILE_ATTRIBUTE_SYSTEM = 0x00000004
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_ARCHIVE = 0x00000020
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_OFFLINE = 0x00001000
#: B6.2 P4b -- modern "Files On-Demand" placeholders (OneDrive, etc.) are
#: marked with these, NOT with FILE_ATTRIBUTE_OFFLINE, and are usually not
#: reparse points. RECALL_ON_DATA_ACCESS = an online-only file whose bytes
#: are fetched on first read; RECALL_ON_OPEN = a dehydrated file. Reading
#: one triggers a download, so the runtime must be able to recognise them.
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
CLOUD_PLACEHOLDER_ATTRIBUTES = (
    FILE_ATTRIBUTE_OFFLINE
    | FILE_ATTRIBUTE_RECALL_ON_OPEN
    | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

#: Reparse tags R6 treats as a GENUINE link and refuses to recurse into.
#: Cloud placeholder tags are deliberately NOT here: R6 recurses into
#: those, and that behaviour is accepted and preserved.
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
_TRUE_LINK_TAGS = (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK)


def format_file_attributes(value):
    r"""Reproduce .NET `FileAttributes.ToString()` byte-for-byte.

    The algorithm is Enum's flags formatter, and each of its three
    branches matters for a real file somewhere:

      * zero -> the literal string "0". FileAttributes has no member
        whose value is 0, and Enum falls back to the number. A file
        cannot really have no attributes on NTFS, but a provider that
        reports none must not silently render as an empty field.

      * every bit named -> the member names, ASCENDING BY VALUE,
        joined with ", ".

      * any bit left over -> the DECIMAL VALUE OF THE WHOLE INPUT, not
        a partial rendering. This is not a hypothetical: a modern
        OneDrive placeholder carries FILE_ATTRIBUTE_PINNED (0x80000) or
        RECALL_ON_DATA_ACCESS (0x400000), and neither exists in the
        .NET Framework enum, so PowerShell renders the whole number.
        Reproducing that is what keeps a OneDrive project's exports
        matching; "improving" it would be a silent divergence.
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return "0"

    remaining = value
    names = []
    for bit, name in _FILE_ATTRIBUTE_MEMBERS:
        if remaining & bit == bit:
            remaining &= ~bit
            names.append(name)
    if remaining:
        return str(value)
    return ", ".join(names)


def has_attribute(value, flag):
    """Flag test with R6's semantics: every bit of the flag must be set."""
    try:
        return (int(value) & flag) == flag
    except (TypeError, ValueError):
        return False


def is_cloud_placeholder(attributes):
    r"""Is this a cloud-backed file whose bytes may not be on disk?

    B6.2 P4b. True if ANY of the cloud/offline attributes is set --
    FILE_ATTRIBUTE_OFFLINE (older OneDrive, some backup providers),
    RECALL_ON_OPEN (dehydrated) or RECALL_ON_DATA_ACCESS (online-only,
    the shape modern OneDrive "Free up space" produces). Any read of the
    file's content would make the OS fetch it from the cloud, which for a
    read-only inventory tool is a source-side effect it must not cause
    silently. `has_attribute` is the wrong test here: it requires every
    bit of its flag, and this is an OR over three independent bits.
    """
    try:
        return (int(attributes) & CLOUD_PLACEHOLDER_ATTRIBUTES) != 0
    except (TypeError, ValueError):
        return False


def is_true_link(attributes, reparse_tag):
    r"""Is this entry a junction or symlink R6 refuses to recurse into?

    Mirrors NativeFindEntry.IsTrueLink: the tag is only meaningful when
    the reparse-point attribute is set, and only the mount-point and
    symlink tags count. Cloud placeholder folders keep being walked,
    exactly as they are today.
    """
    if not has_attribute(attributes, FILE_ATTRIBUTE_REPARSE_POINT):
        return False
    try:
        return int(reparse_tag) in _TRUE_LINK_TAGS
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 2. .NET Path.GetExtension
# ---------------------------------------------------------------------------

def dotnet_extension(name):
    r"""`System.IO.Path.GetExtension`, which os.path.splitext is not.

    .NET scans backwards for a '.' and returns everything from it,
    UNLESS the dot is the final character. Two consequences that
    os.path.splitext gets differently, and both reach the database
    through file_path.extension_key:

        ".gitignore"  .NET ".gitignore"   splitext ""
        "trailing."   .NET ""             splitext "."

    The accepted controlled suite contains neither shape -- verified,
    all 4,385 rows agree with both implementations -- so this is here
    to keep a real project correct, not to fix a fixture.
    """
    if not name:
        return ""
    index = name.rfind(".")
    if index < 0 or index == len(name) - 1:
        return ""
    return name[index:]


# ---------------------------------------------------------------------------
# 3. Timestamps
# ---------------------------------------------------------------------------

#: Unix-epoch seconds of FILETIME 0 (1601-01-01T00:00:00Z). A find
#: result carrying a zero FILETIME becomes DateTime.MinValue in R6 (see
#: NativeLongPathEnumerator.FileTimeToLocal), so it is recognised here
#: rather than being rendered as a date in 1601.
_FILETIME_ZERO_UNIX_SECONDS = -11644473600

#: What .NET renders for DateTime.MinValue, as an ISO string.
DATETIME_MIN_ISO = "0001-01-01T00:00:00"


#: SYSTEMTIME/FILETIME conversion is done through Windows itself on
#: Windows. Elsewhere the CRT fallback is used, which on Linux and
#: macOS reads the tz database and is already historically correct.
_USE_WINDOWS_CONVERTER = (sys.platform == "win32")

#: Whole-second -> local parts memo. Six Win32 calls per file times
#: three timestamps is real work on a million-file scan, and inventories
#: repeat timestamps heavily (installer payloads, generated sets, files
#: copied in one operation). Bounded and cleared wholesale rather than
#: evicted: an LRU here would be machinery for no benefit, since the
#: only cost of clearing is recomputing values that are still cheap.
_LOCAL_PARTS_CACHE = {}
_LOCAL_PARTS_CACHE_LIMIT = 100000


#: What .NET renders for DateTime.MinValue, as a UTC ISO string.
DATETIME_MIN_ISO_UTC = "0001-01-01T00:00:00Z"


def utc_iso_seconds(ns):
    r"""Integer Unix nanoseconds -> TRUE UTC ISO-8601, whole seconds, with Z.

    ADDED IN B6. THE FIX FOR B5-F.F003.

    B4.5 stored the output of `local_iso_seconds()` in columns named
    `created_utc`, `modified_utc` and `accessed_utc`. Those values were
    machine-local wall-clock time with no offset attached, so the same
    file produced different stored values in different time zones and
    nothing in the row said which zone it had been. B5-F.F003 called
    that a false column contract, and it was one.

    This is the honest value: the instant itself, independent of the
    machine that observed it.

    NO TIME-ZONE DATABASE IS CONSULTED, and that is the point. A
    FILETIME is already UTC; Windows converts it to local time on the
    way out, and B4.5 stored the result of that conversion. Going back
    the other way would require knowing the offset and the historical
    DST rule that applied on that date -- which is exactly the
    machinery `windows_local_parts()` below exists to get right for
    DISPLAY, and exactly the machinery that has no business sitting
    between a stored fact and the disk.

    So this is arithmetic on the epoch value and nothing else. It
    cannot be wrong about a 2003 DST rule change because it never asks.

    FLOORING matches `local_iso_seconds()`, for the same reason given
    there: .NET renders the second COMPONENT, and flooring is also
    correct for the negative nanosecond counts that pre-1970 timestamps
    produce.
    """
    if ns is None:
        return None
    seconds = int(ns) // 1000000000
    if seconds <= _FILETIME_ZERO_UNIX_SECONDS:
        return DATETIME_MIN_ISO_UTC
    try:
        stamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_offset_minutes(ns=None):
    r"""Minutes east of UTC in force locally, at an instant.

    Stored per observation so the local rendering stays reproducible
    from the UTC value without going back to the machine that made it.
    That is what makes B6's timestamps machine-independent in the sense
    B5-H asks for: the fact travels, and the presentation can be
    rebuilt from it anywhere.

    Returns None rather than 0 when the offset cannot be determined.
    Zero is a real offset (it is UTC) and must not double as "unknown".
    """
    try:
        if ns is None:
            local = datetime.now().astimezone()
        else:
            seconds = int(ns) // 1000000000
            if seconds <= _FILETIME_ZERO_UNIX_SECONDS:
                return None
            local = datetime.fromtimestamp(seconds).astimezone()
        offset = local.utcoffset()
        if offset is None:
            return None
        return int(offset.total_seconds() // 60)
    except (OverflowError, OSError, ValueError):
        return None


def local_iso_seconds(ns):
    r"""Integer Unix nanoseconds -> local wall-clock ISO, whole seconds.

    RETAINED IN B6, BUT NO LONGER STORED UNDER A '_utc' NAME.

    This is a PRESENTATION helper now. Its output goes to the
    `*_local_naive` columns, which is an accurate name for it, and to
    the export/report boundary. `utc_iso_seconds()` above produces what
    B6 actually persists as the canonical value.

    Four decisions, each with a reason:

    FROM *_ns, NOT FROM THE FLOAT FIELDS. st_mtime is a double, and a
    FILETIME a microsecond below a second boundary can land on
    ...999999999 after the round trip. Flooring that renders one second
    early, in a field the export compares byte-for-byte.

    FLOOR, NOT ROUND. .NET renders the second COMPONENT of a DateTime;
    it does not round to the nearest second. Flooring is also correct
    for pre-1970 timestamps, where the nanosecond count is negative and
    truncation-toward-zero would move the value forward.

    LOCAL, NOT UTC. R6 converts every FILETIME with ToLocalTime() and
    renders local wall-clock time. Storing it relabelled as UTC would
    be an invented offset that later arithmetic would believe.

    CONVERTED BY WINDOWS, NOT BY THE CRT (B2.5). See
    windows_local_parts() for why this one cost a Windows cycle.
    """
    if ns is None:
        return None
    seconds = int(ns) // 1000000000
    if seconds <= _FILETIME_ZERO_UNIX_SECONDS:
        return DATETIME_MIN_ISO

    if _USE_WINDOWS_CONVERTER:
        parts = _cached_windows_parts(seconds)
        if parts is not None:
            return "%04d-%02d-%02dT%02d:%02d:%02d" % parts

    try:
        return datetime.fromtimestamp(seconds).replace(microsecond=0).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _cached_windows_parts(seconds):
    parts = _LOCAL_PARTS_CACHE.get(seconds)
    if parts is not None:
        return parts
    parts = windows_local_parts(seconds)
    if parts is not None:
        if len(_LOCAL_PARTS_CACHE) >= _LOCAL_PARTS_CACHE_LIMIT:
            _LOCAL_PARTS_CACHE.clear()
        _LOCAL_PARTS_CACHE[seconds] = parts
    return parts


def windows_local_parts(seconds):
    r"""UTC seconds -> local (y, m, d, H, M, S) using DYNAMIC DST rules.

    WHY THIS EXISTS -- THE DEFECT IT FIXES (B2.5)

    B2.4 used datetime.fromtimestamp(), which on Windows goes through
    the CRT's localtime. The CRT applies the CURRENT time zone rule to
    every date, including dates from before that rule existed. The
    United States moved the start of daylight time from the first
    Sunday in April to the second Sunday in March, effective 2007, so
    the CRT believes 5 April 2003 was daylight time. It was not.

    Windows acceptance found this on a disposable NTFS fixture:

        .NET / R6 target   2003-04-05 06:07:08
        B2.4 produced      2003-04-05 07:07:08

    One hour, on a date twenty-three years old, in a field the export
    compares byte-for-byte. It is not confined to LastAccessTime --
    all three timestamps run through this one helper, so all three were
    wrong on any pre-2007 date where the rules disagree.

    WHY THIS API

    R6's oracle is DateTime.FromFileTimeUtc(raw).ToLocalTime(). .NET
    resolves that against the registry's YEAR-SPECIFIC ("dynamic") DST
    entries, so it gets 2003 right. The Win32 pairing that reads the
    same data is:

        GetDynamicTimeZoneInformation   the zone, with its dynamic rules
        SystemTimeToTzSpecificLocalTimeEx   convert honouring the year

    The plain SystemTimeToTzSpecificLocalTime is NOT equivalent: it
    applies the single current rule, which is precisely the behaviour
    being fixed. The Ex variant is the whole point.

    FILETIME rather than a Unix epoch is used deliberately: it is what
    the filesystem stores, what R6 converts, and it is unsigned from
    1601, so a pre-1970 timestamp is an ordinary value here. The CRT
    fallback cannot represent those on Windows at all.

    No dependency is added: ctypes is standard library, and this file
    already exists as the Windows metadata boundary.

    Returns None on any failure, so the caller falls back rather than
    losing a scan to a clock question.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [("wYear", wintypes.WORD), ("wMonth", wintypes.WORD),
                        ("wDayOfWeek", wintypes.WORD), ("wDay", wintypes.WORD),
                        ("wHour", wintypes.WORD), ("wMinute", wintypes.WORD),
                        ("wSecond", wintypes.WORD),
                        ("wMilliseconds", wintypes.WORD)]

        class DYNAMIC_TIME_ZONE_INFORMATION(ctypes.Structure):
            _fields_ = [("Bias", wintypes.LONG),
                        ("StandardName", wintypes.WCHAR * 32),
                        ("StandardDate", SYSTEMTIME),
                        ("StandardBias", wintypes.LONG),
                        ("DaylightName", wintypes.WCHAR * 32),
                        ("DaylightDate", SYSTEMTIME),
                        ("DaylightBias", wintypes.LONG),
                        ("TimeZoneKeyName", wintypes.WCHAR * 128),
                        ("DynamicDaylightTimeDisabled", wintypes.BOOLEAN)]

        kernel32 = ctypes.windll.kernel32

        # FILETIME counts 100-nanosecond intervals from 1601-01-01 UTC.
        # Built from WHOLE SECONDS, so the flooring already applied by
        # the caller is what gets converted -- no fractional part can
        # sneak back in and round the wall clock forward.
        intervals = (int(seconds) - _FILETIME_ZERO_UNIX_SECONDS) * 10000000
        if intervals < 0:
            return None

        file_time = wintypes.FILETIME(intervals & 0xFFFFFFFF,
                                      (intervals >> 32) & 0xFFFFFFFF)
        utc = SYSTEMTIME()
        if not kernel32.FileTimeToSystemTime(ctypes.byref(file_time),
                                             ctypes.byref(utc)):
            return None

        zone = _dynamic_time_zone(DYNAMIC_TIME_ZONE_INFORMATION, kernel32)
        if zone is None:
            return None

        local = SYSTEMTIME()
        if not kernel32.SystemTimeToTzSpecificLocalTimeEx(
                ctypes.byref(zone), ctypes.byref(utc), ctypes.byref(local)):
            return None

        return (local.wYear, local.wMonth, local.wDay,
                local.wHour, local.wMinute, local.wSecond)
    except Exception:
        return None


#: The zone is read once. It is a machine setting, not per-file data,
#: and re-reading it for every timestamp of every file would be three
#: syscalls per file for an answer that does not change during a scan.
_DYNAMIC_ZONE = []


def _dynamic_time_zone(structure_type, kernel32):
    """The current zone WITH its dynamic rules, or None."""
    if _DYNAMIC_ZONE:
        return _DYNAMIC_ZONE[0]
    import ctypes
    zone = structure_type()
    result = kernel32.GetDynamicTimeZoneInformation(ctypes.byref(zone))
    if result == 0xFFFFFFFF:            # TIME_ZONE_ID_INVALID
        return None
    _DYNAMIC_ZONE.append(zone)
    return zone


def reset_time_zone_cache():
    """Drop the cached zone and conversions. For tests and diagnostics."""
    _DYNAMIC_ZONE.clear()
    _LOCAL_PARTS_CACHE.clear()


# ---------------------------------------------------------------------------
# 4. Paths
# ---------------------------------------------------------------------------

def to_extended_path(path):
    r"""Add the \\?\ prefix, for use at the Win32 API edge ONLY.

    Identical to R6's ToExtendedPath. Nothing this returns is ever
    stored or exported: R6's own comment makes the same promise, and
    the accepted 926-character fixture is stored as an ordinary
    C:\... path.
    """
    text = str(path)
    if text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def strip_extended_prefix(path):
    """Undo to_extended_path, so a stored path is never an API path."""
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def normalize_root(path):
    r"""The scan root, normalised the way R6 normalises it.

    R6 does `(Resolve-Path).Path.TrimEnd('\')`, then builds every child
    as `root + '\' + name`. The TrimEnd is load-bearing at a drive
    root: `C:\` becomes `C:`, so children are `C:\file` rather than
    `C:\\file`. Reproduced, including that edge.

    Case is left exactly as supplied. Resolve-Path does not re-case a
    path to its on-disk spelling, and the accepted artifact carries the
    casing the operator's folder picker handed over.
    """
    text = strip_extended_prefix(str(path))
    # An already-absolute Windows path is left exactly as it is. Sending
    # it through abspath() would be a no-op on Windows and would prepend
    # the working directory anywhere else, which is the difference
    # between a testable function and one that only behaves on the
    # machine it cannot be tested on.
    absolute = text.startswith("\\\\") or (len(text) > 1 and text[1] == ":")
    if not absolute:
        text = os.path.abspath(text)
    return text.rstrip("\\/") if len(text.rstrip("\\/")) else text


#: The separator paths are BUILT with. Windows gets a backslash,
#: unconditionally and forever -- every stored path, every export and
#: every comparison key in this product is a Windows path.
#:
#: Off Windows it is the platform separator. B4.5 hard-coded the
#: backslash here too, which meant a walk on any other platform built
#: paths like '/tmp/x\sub' -- strings that no `os.scandir()` can open,
#: so recursion silently stopped one level down and the engine could
#: not be exercised outside Windows at all. B5's "unavailable evidence"
#: sections are largely a list of things that could not be checked
#: without a Windows machine; this is one small piece of that, and it
#: costs nothing on the platform that ships.
PATH_SEPARATOR = "\\" if sys.platform == "win32" else os.sep


def join_child(directory, name):
    r"""`directory.TrimEnd('\') + '\' + name`, as R6's ConvertEntry does.

    On Windows this is byte-for-byte what B4.5 did. Off Windows it uses
    the platform separator -- see PATH_SEPARATOR above for why.
    """
    return str(directory).rstrip("\\/") + PATH_SEPARATOR + str(name)


def utf16_length(text):
    r"""`String.Length` in .NET: UTF-16 CODE UNITS, not code points.

    R6 stores `$Item.FullName.Length`, and a .NET string is UTF-16, so
    a character outside the Basic Multilingual Plane counts as TWO --
    it is stored as a surrogate pair. Python's len() counts code
    points and would say one.

    This is not hypothetical and it is not a rounding error. The
    accepted controlled suite contains `emoji_test_<U+1F60A>.txt`,
    whose accepted PathLength is 77; counting code points gives 76.
    That single byte is the difference between a byte-identical export
    and a failed one, and it was found by comparing against the
    accepted artifact rather than by reasoning about it.
    """
    if text is None:
        return 0
    return len(text.encode("utf-16-le")) // 2


def relative_depth(full_path, root_path):
    r"""R6's Get-RelativeDepth: how many folders sit between root and file.

    R6 splits the parent of the relative path and counts the parts, so
    a file directly in the root is depth 0 and `Manual\x.md` is depth 1.
    Counting separators in the relative path gives the same number
    without a Split-Path.
    """
    relative = full_path[len(root_path):].lstrip("\\/")
    if not relative:
        return 0
    return relative.replace("/", "\\").count("\\")


def top_level_folder(full_path, root_path):
    """R6's Get-TopLevelFolder, used only by the preliminary report."""
    relative = full_path[len(root_path):].lstrip("\\/")
    parts = relative.replace("/", "\\").split("\\")
    if len(parts) <= 1:
        return "(root)"
    return parts[0]


def detect_display_format():
    r"""Which date ordering this machine's .NET rendering would use.

    WHY THIS EXISTS AT ALL. R3 records `timestamp_format_detected` on
    every inventory_scan, and R6's export layer needs it to turn a
    stored ISO timestamp back into the string PowerShell wrote. R3
    detected it by SAMPLING THE CSV, which B2 no longer produces before
    persisting -- so the same question has to be answered from the
    machine instead of from the artifact.

    The method is the same evidence R3 relies on: format a date whose
    day is 14, and see which component comes out first. A component
    over 12 can only be a day, so the answer is proof rather than a
    guess, exactly as detect_timestamp_format() argues.

    Returns 'MDY', 'DMY' or 'ambiguous'. Never guesses: an ordering it
    cannot prove is reported as ambiguous, and R3's existing rule then
    stores NULL timestamps rather than plausible wrong ones.
    """
    probe = _short_date_probe()
    if not probe:
        return "ambiguous"
    digits = ""
    for char in probe:
        if char.isdigit():
            digits += char
        elif digits:
            break
    if not digits:
        return "ambiguous"
    leading = int(digits)
    if leading == 14:
        return "DMY"
    if leading == 8:
        return "MDY"
    return "ambiguous"


def _short_date_probe():
    """2026-08-14 rendered in the user's short-date pattern, or None."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class SYSTEMTIME(ctypes.Structure):
                _fields_ = [("wYear", wintypes.WORD), ("wMonth", wintypes.WORD),
                            ("wDayOfWeek", wintypes.WORD), ("wDay", wintypes.WORD),
                            ("wHour", wintypes.WORD), ("wMinute", wintypes.WORD),
                            ("wSecond", wintypes.WORD),
                            ("wMilliseconds", wintypes.WORD)]

            when = SYSTEMTIME(2026, 8, 5, 14, 0, 0, 0, 0)
            buffer = ctypes.create_unicode_buffer(128)
            written = ctypes.windll.kernel32.GetDateFormatEx(
                None, 0x00000001,           # LOCALE_NAME_USER_DEFAULT, DATE_SHORTDATE
                ctypes.byref(when), None, buffer, len(buffer), None)
            if written:
                return buffer.value
        except Exception:
            pass
    try:
        return datetime(2026, 8, 14).strftime("%x")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 5. Drive type
# ---------------------------------------------------------------------------

_DRIVE_REMOTE = 4


def drive_type(path):
    r"""Network, or Unknown. A calibration signal, never a measurement.

    WHAT THIS DELIBERATELY NO LONGER DOES (B2.1)

    B2 distinguished SSD from HDD with an IOCTL_STORAGE_QUERY_PROPERTY
    seek-penalty query over ctypes -- roughly 50 lines of DeviceIoControl
    marshalling, a volume handle, and two structure definitions, all of
    it unverifiable anywhere except Windows. It has been removed.

    B1 13.5 already authorised exactly this: degrade the estimate rather
    than complicate the runtime for a cosmetic feature. The value did
    not justify the code, and it did not justify the Windows acceptance
    surface -- an untestable path whose only output is a label.

    WHAT SURVIVES, AND WHY IT IS THE PART THAT MATTERS

    Only one consumer BEHAVES differently on drive type:
    TimeEstimates.ps1 lowers its safety factor from 0.6 to 0.4 for a
    network volume. That decision needs "Network or not", which a UNC
    test and GetDriveTypeW answer between them. SSD versus HDD changed
    a displayed word and nothing else.

    So the vocabulary narrows to:

        UNC path or DRIVE_REMOTE   ->  "Network"
        anything else              ->  "Unknown"
        any failure at all         ->  "Unknown"

    "Unknown" is a value R6 already produced whenever its CIM chain
    failed, so no consumer meets a string it has not seen before.

    This never raises, and it must never be allowed to block a scan --
    R6's rule, preserved. Every path out of here returns a string.
    """
    try:
        text = str(path)
        if text.startswith("\\\\"):
            return "Network"
        if sys.platform != "win32":
            return "Unknown"
        if len(text) < 2 or text[1] != ":":
            return "Unknown"

        import ctypes

        # A mapped drive letter can still be a network location, which
        # is the whole reason this call is worth making at all.
        root = "%s:\\" % text[0]
        code = int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))
        return "Network" if code == _DRIVE_REMOTE else "Unknown"
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# 6. Attribute access from a stat result
# ---------------------------------------------------------------------------

def stat_attributes(stat_result):
    r"""The raw Windows attribute word from a stat result.

    os.scandir populates st_file_attributes on Windows from the
    WIN32_FIND_DATA the walk already returned -- the same source R6's
    C# helper reads, which is what makes this a port rather than a
    reimplementation.

    OFF WINDOWS (B6), the directory and symlink bits are DERIVED from
    the POSIX mode instead of returning a flat 0.

    B4.5 returned 0 everywhere off Windows, which meant
    `is_directory()` was False for every directory and the walk emitted
    directories as if they were files. That never mattered in
    production -- the product is Windows-only -- but it made the
    inventory engine untestable anywhere else, and B5's own "unavailable
    evidence" sections are largely a list of things that could not be
    checked without a Windows machine. An engine that can be exercised
    on the build machine gets exercised more often.

    This changes NOTHING on Windows: the branch is not reached when
    st_file_attributes exists. It is a test-surface fix, not a
    behaviour change, and it does not attempt to synthesise attributes
    Windows has and POSIX does not.
    """
    attributes = getattr(stat_result, "st_file_attributes", None)
    if attributes is not None:
        return int(attributes or 0)
    mode = getattr(stat_result, "st_mode", 0) or 0
    derived = 0
    if stat_module.S_ISDIR(mode):
        derived |= FILE_ATTRIBUTE_DIRECTORY
    if stat_module.S_ISLNK(mode):
        derived |= FILE_ATTRIBUTE_REPARSE_POINT
    return derived


def stat_reparse_tag(stat_result):
    """The reparse tag, present from Python 3.8 on Windows."""
    return int(getattr(stat_result, "st_reparse_tag", 0) or 0)



def stat_physical_identity(stat_result):
    r"""Best-effort physical file identity from a stat result.

    Returns ``(volume_serial, file_index, hard_link_count)``. Python exposes
    Windows file-index identity through ``st_dev``/``st_ino`` and link count
    through ``st_nlink``; POSIX exposes the analogous facts, which also makes
    this boundary independently testable off Windows. NULL/zero identity is
    represented as unknown rather than invented.
    """
    dev = getattr(stat_result, "st_dev", None)
    ino = getattr(stat_result, "st_ino", None)
    nlink = getattr(stat_result, "st_nlink", None)
    try:
        # B6.2 Fix 3 -- reject a zero volume serial, exactly as `index`
        # and `links` below already reject zero. os.DirEntry.stat() on
        # Windows returns 0 for st_dev/st_ino/st_nlink; without this guard
        # a Windows scan stored volume_serial = "0" (a meaningless value)
        # next to a NULL file index. Unknown is now unknown in all three.
        volume = str(int(dev)) if dev is not None and int(dev) != 0 else None
    except (TypeError, ValueError):
        volume = None
    try:
        index = str(int(ino)) if ino is not None and int(ino) != 0 else None
    except (TypeError, ValueError):
        index = None
    try:
        links = int(nlink) if nlink is not None and int(nlink) >= 1 else None
    except (TypeError, ValueError):
        links = None
    return volume, index, links


def physical_identity_for_path(full_path, fallback_stat=None):
    r"""(volume_serial, file_index, hard_link_count) resolved from a FULL
    stat of the path -- B6.2 P1, THE FIX FOR FINDING A.

    Why a second stat rather than reading `fallback_stat`: on Windows the
    stat result os.scandir() hands back is filled from the WIN32_FIND_DATA
    the directory walk already returned, and that structure carries NO file
    index and NO link count. Python reports st_ino / st_dev / st_nlink as 0
    for it (documented). Only os.stat() of the file itself -- which opens a
    handle and calls GetFileInformationByHandle -- has them. Without this,
    a hardlink and a real copy are indistinguishable on Windows, which is
    the one thing this metadata exists to decide.

    COST: one extra metadata open per file. Measured ~35 us/file warm on
    NTFS: about 3-4% of the end-to-end inventory step (which is dominated by
    SQLite persistence, not the walk) and well under 1% of a full Pre-Scan +
    Duplicate Run. The alternative designs -- statting only size-collision
    candidates, or folding the stat into the hash pass -- save a fraction of
    a second on a multi-minute run at the cost of a new pipeline stage or a
    change to the hash engine's accepted-behaviour surface. Simplest wins:
    the value flows through the one InventoryRecord write path that already
    exists. See P1-RESULTS.md.

    SOURCE SAFETY: os.stat opens for metadata only -- no content read, no
    write, no timestamp change. follow_symlinks=False, so a reparse point
    reports its own identity rather than its target's.

    NEVER RAISES. On any failure it falls back to `fallback_stat`, which on
    Windows yields the same three unknowns as B6.1 did -- never worse. Off
    Windows the DirEntry stat already carries real identity, so the second
    stat is skipped entirely and there is no added cost on that platform.
    """
    if sys.platform == "win32":
        try:
            full_stat = os.stat(to_extended_path(full_path), follow_symlinks=False)
            return stat_physical_identity(full_stat)
        except OSError:
            pass
    if fallback_stat is not None:
        return stat_physical_identity(fallback_stat)
    return None, None, None


def allocated_size_bytes(path, stat_result=None):
    r"""Best-effort bytes physically allocated to a file.

    POSIX exposes 512-byte allocation blocks directly. On Windows,
    GetCompressedFileSizeW reports the physical byte count used for ordinary,
    compressed and sparse files. Unsupported providers return None: unknown is
    not zero.
    """
    st = stat_result
    blocks = getattr(st, "st_blocks", None) if st is not None else None
    if blocks is not None:
        try:
            return max(0, int(blocks) * 512)
        except (TypeError, ValueError, OverflowError):
            pass
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        target = to_extended_path(path)
        high = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        low = ctypes.windll.kernel32.GetCompressedFileSizeW(
            ctypes.c_wchar_p(target), ctypes.byref(high))
        low = int(low)
        err = ctypes.get_last_error()
        if low == 0xFFFFFFFF and err:
            return None
        return (int(high.value) << 32) | low
    except Exception:
        return None

def is_directory(attributes):
    return has_attribute(attributes, FILE_ATTRIBUTE_DIRECTORY)


__all__ = [
    "windows_local_parts", "reset_time_zone_cache",
    "utf16_length",
    "MODULE_VERSION", "format_file_attributes", "has_attribute",
    "is_true_link", "dotnet_extension", "local_iso_seconds",
    "to_extended_path", "strip_extended_prefix", "normalize_root",
    "join_child", "relative_depth", "top_level_folder", "drive_type",
    "stat_attributes", "stat_reparse_tag", "stat_physical_identity",
    "allocated_size_bytes", "is_directory",
    "FILE_ATTRIBUTE_READONLY", "FILE_ATTRIBUTE_HIDDEN",
    "FILE_ATTRIBUTE_SYSTEM", "FILE_ATTRIBUTE_DIRECTORY",
    "FILE_ATTRIBUTE_ARCHIVE", "FILE_ATTRIBUTE_REPARSE_POINT",
    "FILE_ATTRIBUTE_OFFLINE", "DATETIME_MIN_ISO",
]


def parent_of(full_path):
    r"""The directory component of a path built by join_child().

    B4.5 inlined `full_path[:full_path.rfind("\\")]` at the one call
    site. That is correct on Windows and silently returns the empty
    string anywhere else, which put an empty Directory column on every
    row of an off-Windows test run. Same reasoning as PATH_SEPARATOR:
    identical behaviour where it ships, honest behaviour where it is
    tested.
    """
    index = max(full_path.rfind("\\"), full_path.rfind("/"))
    return full_path[:index] if index > 0 else full_path
