#!/usr/bin/env python3
r"""
fo_env.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, R2 (Logging + Run/Stage Persistence)
Module version: 1.0.0
===================================================================

Collects the machine/toolchain state a run executed under, so that
historical behaviour stays explicable months later: why that scan
missed long paths, why that PDF analyzer failed, why timings changed.

WHAT IS COLLECTED
    Windows caption / version / build
    PowerShell version and edition
    Python version, SQLite library version (sqlite3.sqlite_version)
    LongPathsEnabled                (changes what the engine can see)
    NTFS last-access-time policy    (changes whether reading mutates
                                     source metadata)
    ffprobe presence and version    (audio/video analyzers need it)
    Versions of the Python packages the analyzers depend on
    Per-source-root drive type, filesystem, capacity and free space

WHAT IS DELIBERATELY NOT COLLECTED
    Machine name, user name, domain, SIDs, serial numbers, MAC
    addresses, IP addresses, installed software inventory, or the
    user's profile path. None of it would explain a scan result, and
    this file is written into a project folder the operator may well
    share when asking for help.

COST
    One PowerShell subprocess (~0.5-1.5s) for $PSVersionTable, one
    optional ffprobe call, and some registry reads. Callers collect
    once per application session and reuse -- see RunCoordinator.

NEVER RAISES
    Every probe is individually guarded and degrades to None or
    "unknown". An environment snapshot is context for a scan, never a
    precondition for one.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Mirrors the package list in Install-Dependencies.ps1. Versions are
#: read from installed distribution metadata rather than by importing
#: the packages: importing Pillow or pdfplumber to ask its version
#: would cost far more than the answer is worth, and would fail loudly
#: for a package that is installed but broken -- which is a fact worth
#: recording, not worth crashing on.
DEPENDENCY_PACKAGES = (
    "Pillow", "imagehash", "pypdf", "pdfplumber", "python-docx",
    "openpyxl", "python-pptx", "olefile", "exifread", "mutagen",
    "chardet", "py7zr", "pillow-heif",
)

#: HKLM\SYSTEM\CurrentControlSet\Control\FileSystem
_FS_POLICY_KEY = r"SYSTEM\CurrentControlSet\Control\FileSystem"

_LAST_ACCESS_MEANING = {
    0: "User Managed, Last Access Time Updates Enabled",
    1: "User Managed, Last Access Time Updates Disabled",
    2: "System Managed, Last Access Time Updates Enabled",
    3: "System Managed, Last Access Time Updates Disabled",
}

_DRIVE_TYPES = {
    0: "Unknown", 1: "NoRootDirectory", 2: "Removable",
    3: "Fixed", 4: "Network", 5: "CDROM", 6: "RamDisk",
}


# ---------------------------------------------------------------------------
# Individual probes -- each returns a value or None, never raises
# ---------------------------------------------------------------------------

def _safe(probe, *args, **kwargs):
    """Call a probe and return None if it fails in any way.

    Catches BaseException deliberately, not just Exception. An
    environment probe reaches into ctypes, the registry and subprocesses;
    the point is that NOTHING it does can propagate into a scan that was
    otherwise going to succeed. KeyboardInterrupt and SystemExit are
    re-raised, because swallowing those would make the application
    unkillable during collection.
    """
    try:
        return probe(*args, **kwargs)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return None


def _read_fs_policy_dword(value_name):
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _FS_POLICY_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
            return int(value)
    except Exception:
        # Absent value, no permission, or not Windows. "Could not
        # determine" is a legitimate answer and is stored as NULL.
        return None


def long_paths_enabled():
    r"""1, 0, or None when it could not be read.

    Worth recording because PreliminaryInventory.ps1 walks directories
    through FindFirstFileW with \\?\ prefixes specifically so it does
    NOT depend on this setting -- but other tooling on the machine
    does, and a support question about "files I can see but Explorer
    can't" is answered by this one number.
    """
    value = _read_fs_policy_dword("LongPathsEnabled")
    if value is None:
        return None
    return 1 if value else 0


def last_access_update_policy():
    """Human-readable NTFS last-access-time policy, or None.

    Directly relevant to the Phase 1 source-safety model: this is the
    setting that decides whether merely reading a source file updates
    its last-access timestamp.
    """
    value = _read_fs_policy_dword("NtfsDisableLastAccessUpdate")
    if value is None:
        return None
    meaning = _LAST_ACCESS_MEANING.get(value & 0x3)
    return "%d (%s)" % (value, meaning) if meaning else str(value)


def windows_info():
    info = {"caption": None, "version": None, "build": None}
    try:
        import platform
        info["version"] = platform.version() or None
        release = platform.release()
        system = platform.system()
        if system:
            info["caption"] = ("%s %s" % (system, release)).strip()
    except Exception:
        pass
    if os.name == "nt":
        try:
            vinfo = sys.getwindowsversion()
            info["build"] = str(vinfo.build)
            info["version"] = "%d.%d.%d" % (vinfo.major, vinfo.minor, vinfo.build)
        except Exception:
            pass
    return info


def powershell_info(timeout=20):
    r"""PowerShell version and edition, via one subprocess.

    Windows PowerShell 5.1 Desktop and PowerShell 7 Core differ in ways
    this application cares about -- Set-Content -Encoding UTF8 writes a
    BOM on 5.1 and not on 7, which is exactly why Dashboard.py reads
    settings.json as utf-8-sig. Recording which one ran a scan makes
    that class of problem diagnosable after the fact.
    """
    result = {"version": None, "edition": None, "host": None}
    if os.name != "nt" and not shutil.which("powershell") and not shutil.which("pwsh"):
        return result
    executable = "powershell" if (os.name == "nt" or shutil.which("powershell")) else "pwsh"
    script = (
        "$v=$PSVersionTable; "
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "Write-Output ($v.PSVersion.ToString() + '|' + "
        "  $(if ($v.PSEdition) { $v.PSEdition } else { 'Desktop' }))"
    )
    try:
        completed = subprocess.run(
            [executable, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            parts = completed.stdout.strip().splitlines()[-1].split("|")
            result["version"] = parts[0].strip() or None
            if len(parts) > 1:
                result["edition"] = parts[1].strip() or None
    except Exception:
        pass
    return result


def ffprobe_info(timeout=15):
    """Presence and version of ffprobe -- a hard requirement for the
    audio and video analyzers and a common cause of their failure."""
    path = shutil.which("ffprobe")
    if not path:
        return {"available": False, "version": None}
    version = None
    try:
        completed = subprocess.run(
            [path, "-version"], capture_output=True, text=True,
            timeout=timeout, creationflags=_NO_WINDOW)
        if completed.returncode == 0 and completed.stdout:
            first = completed.stdout.splitlines()[0].split()
            if len(first) >= 3:
                version = first[2]
    except Exception:
        pass
    # The path itself is not recorded: it is frequently under a user
    # profile directory, and its presence is the fact that matters.
    return {"available": True, "version": version}


def dependency_versions():
    versions = {}
    try:
        from importlib import metadata
    except Exception:
        return versions
    for package in DEPENDENCY_PACKAGES:
        try:
            versions[package] = metadata.version(package)
        except Exception:
            versions[package] = None  # not installed, or metadata unreadable
    return versions


def drive_info(path):
    r"""Drive characteristics for one source root.

    Filesystem type matters: exFAT has 2-second timestamp granularity
    and no alternate data streams, which changes what a comparison
    against a previous NTFS-hosted inventory can legitimately conclude.
    """
    info = {
        "path": path, "exists": False, "drive_type": None,
        "filesystem": None, "total_bytes": None, "free_bytes": None,
    }
    try:
        info["exists"] = os.path.exists(path)
    except Exception:
        pass

    if str(path).startswith("\\\\"):
        info["drive_type"] = "Network"
    elif os.name == "nt" and len(str(path)) >= 2 and str(path)[1] == ":":
        root = "%s:\\" % str(path)[0]
        try:
            import ctypes
            code = ctypes.windll.kernel32.GetDriveTypeW(root)
            info["drive_type"] = _DRIVE_TYPES.get(int(code), "Unknown")
        except Exception:
            pass
        try:
            import ctypes
            fs_buffer = ctypes.create_unicode_buffer(64)
            name_buffer = ctypes.create_unicode_buffer(64)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), name_buffer, ctypes.sizeof(name_buffer),
                None, None, None, fs_buffer, ctypes.sizeof(fs_buffer))
            if ok:
                # Only the filesystem type is kept. The volume LABEL is
                # user-chosen text and is not recorded.
                info["filesystem"] = fs_buffer.value or None
        except Exception:
            pass

    if info["exists"]:
        try:
            usage = shutil.disk_usage(path)
            info["total_bytes"] = int(usage.total)
            info["free_bytes"] = int(usage.free)
        except Exception:
            pass
    return info


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_environment(app_version, fo_db_module_version=None,
                        source_roots=None, include_powershell=True):
    """Gather the full snapshot. Returns a plain dict, ready to hash and
    store. Never raises.

    Each field is produced through _safe(), so one probe failing on an
    unanticipated Python or Windows build degrades that single field to
    None instead of taking down the caller. Before Beta R2's Windows
    acceptance this assembly called the probes directly, and a single
    attribute removed in Python 3.14 (sqlite3.version) was enough to
    abort the whole run -- exactly the failure mode the "never raises"
    contract exists to prevent. The contract is now enforced here, not
    just asserted in the docstring.
    """
    windows = _safe(windows_info) or {}
    shell = (_safe(powershell_info) if include_powershell else None) or {}

    snapshot = {
        "os_caption": windows.get("caption"),
        "os_version": windows.get("version"),
        "os_build": windows.get("build"),
        "powershell_version": shell.get("version"),
        "powershell_edition": shell.get("edition"),
        "python_version": _safe(
            lambda: ".".join(str(n) for n in sys.version_info[:3])),
        "python_implementation": _safe(lambda: sys.implementation.name),
        "python_64bit": _safe(lambda: sys.maxsize > 2 ** 32),
        # sqlite3.sqlite_version is the SQLite LIBRARY version, which is
        # what actually explains database behaviour. The old
        # sqlite3.version field was the DB-API module's own version
        # string -- it never varied usefully, it was deprecated in
        # Python 3.12, and it was REMOVED in 3.14. It is not recorded,
        # and nothing reads it: it lived only in details_json, so no
        # schema change is involved in dropping it.
        "sqlite_version": _safe(lambda: sqlite3.sqlite_version),
        "app_version": app_version,
        "fo_db_module_version": fo_db_module_version,
        "long_paths_enabled": _safe(long_paths_enabled),
        "last_access_update": _safe(last_access_update_policy),
        "ffprobe": _safe(ffprobe_info) or {"available": None, "version": None},
        "dependencies": _safe(dependency_versions) or {},
        "source_roots": [_safe(drive_info, root) or {"path": root}
                         for root in (source_roots or [])],
    }
    return snapshot


def environment_hash(snapshot):
    """Stable identity for a snapshot, so an unchanged machine yields
    one row however many runs reference it.

    Volatile fields are excluded from the hash: free space changes
    every minute and would otherwise produce a new snapshot row per
    run, which is the opposite of the point. The free-space value is
    still stored -- just not part of what makes the snapshot distinct.
    """
    material = dict(snapshot)
    material["source_roots"] = [
        {k: v for k, v in root.items() if k not in ("free_bytes", "total_bytes")}
        for root in snapshot.get("source_roots", [])
    ]
    encoded = json.dumps(material, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def describe(snapshot):
    """Compact lines for the run.log header. Only the facts an operator
    reading a log actually scans for."""
    lines = []
    caption = snapshot.get("os_caption") or "unknown OS"
    build = snapshot.get("os_build")
    lines.append("OS              : %s%s" % (caption, (" build %s" % build) if build else ""))
    lines.append("PowerShell      : %s" % (" ".join(
        part for part in (snapshot.get("powershell_version"),
                          snapshot.get("powershell_edition")) if part)
        or "not determined"))
    lines.append("Python / SQLite : %s / %s" % (
        snapshot.get("python_version") or "not determined",
        snapshot.get("sqlite_version") or "not determined"))
    long_paths = snapshot.get("long_paths_enabled")
    lines.append("LongPaths       : %s" % (
        "enabled" if long_paths == 1 else "disabled" if long_paths == 0 else "not determined"))
    lines.append("Last access     : %s" % (snapshot.get("last_access_update") or "not determined"))
    ffprobe = snapshot.get("ffprobe") or {}
    available = ffprobe.get("available")
    if available:
        ffprobe_text = ("present %s" % (ffprobe.get("version") or "")).strip()
    elif available is None:
        # Unknown is not the same as absent. Claiming the audio and video
        # analyzers will fail, when all that happened is that the probe
        # could not run, would send a reader hunting a problem that may
        # not exist.
        ffprobe_text = "not determined"
    else:
        ffprobe_text = "NOT FOUND (audio/video analyzers will fail)"
    lines.append("ffprobe         : %s" % ffprobe_text)
    for root in snapshot.get("source_roots", []):
        lines.append("Source root     : %s [%s %s]%s" % (
            root.get("path"),
            root.get("drive_type") or "?",
            root.get("filesystem") or "?",
            "" if root.get("exists") else "  (NOT AVAILABLE)"))
    return lines


if __name__ == "__main__":
    # Diagnostic entry point: python fo_env.py [source_root ...]
    snap = collect_environment("Beta-R2", source_roots=sys.argv[1:])
    print(json.dumps(snap, indent=2, default=str, ensure_ascii=False))
    print("")
    print("hash: %s" % environment_hash(snap))
