r"""Where the journal and the locks live (B21, decision D4; gate 1 G18).

The journal, its -wal/-shm and the locks live in a LOCAL, NEVER-SYNCED, NON-NETWORK folder keyed by the
project's uid. Production default:

    %LOCALAPPDATA%\FileOrganizer\journals\<project_uid>\

A journal or lock path that is a UNC path, that is on a drive that is not DRIVE_FIXED, or that is under any
OneDrive folder is refused with a clear message. Anything the product cannot classify is refused too:
"unknown" blocks.

The validator is injectable (a `PathClassifier`): tests pass their own, and B22 adds the Cloud Files
(sync-root) check as another classifier. The tests never touch %LOCALAPPDATA%\FileOrganizer\: every test
passes an explicit folder under its own sandbox root as the `override`.

This module only computes and validates paths. It creates nothing (`ensure_folder` does, and only after
validation, for a folder the caller named).
"""
from __future__ import annotations

import ctypes
import os
import re

DRIVE_FIXED = 3
DRIVE_NAMES = {0: "unknown", 1: "no root directory", 2: "removable", 3: "fixed", 4: "network",
               5: "CD-ROM", 6: "RAM disk"}

#: The environment variables whose value is a OneDrive folder (handoff unit 2).
ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")

#: A project uid is used as a folder name, so it must be a plain token (a UUID in practice).
_UID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


class PathRefused(Exception):
    """A journal or lock path that must not be used. `code` is machine-readable; str() is for the person."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------------------------------
# Classification (injectable)
# ---------------------------------------------------------------------------------------------------
class PathClassifier:
    """Answers the three questions the validator asks. Subclass or replace it in tests."""

    def drive_type(self, path):
        """GetDriveTypeW for the drive that holds `path` (an absolute, local-style path). 0 = unknown."""
        drive = os.path.splitdrive(path)[0]
        if not drive:
            return 0
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
            k32.GetDriveTypeW.restype = ctypes.c_uint
            return int(k32.GetDriveTypeW(drive + "\\"))
        except (OSError, AttributeError):
            return 0

    def onedrive_folders(self):
        """The folders the OneDrive environment variables name (those that are set)."""
        out = []
        for name in ONEDRIVE_ENV_VARS:
            val = os.environ.get(name)
            if val:
                out.append(val)
        return out


def _key(path):
    """Case-insensitive comparison key, separators and trailing separators normalised."""
    p = os.path.normcase(os.path.normpath(path))
    return p.rstrip("\\/") if len(p) > 3 else p


def _is_under(path, folder):
    p, f = _key(path), _key(folder)
    if not f:
        return False
    return p == f or p.startswith(f + "\\")


def is_unc(path):
    """True for \\\\server\\share forms and for the \\\\?\\UNC\\ and \\\\.\\UNC\\ device forms."""
    p = os.fspath(path).replace("/", "\\")
    if p.upper().startswith(("\\\\?\\UNC\\", "\\\\.\\UNC\\")):
        return True
    if p.startswith("\\\\?\\") or p.startswith("\\\\.\\"):
        return False                       # a device-path form of a local drive: judged after stripping
    return p.startswith("\\\\")


def _strip_device_prefix(path):
    p = os.fspath(path).replace("/", "\\")
    if p.startswith("\\\\?\\") or p.startswith("\\\\.\\"):
        return p[4:]
    return p


# ---------------------------------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------------------------------
def validate_store_path(path, classifier=None):
    """Return the absolute, normalised path if it may hold a journal or a lock; else raise PathRefused.

    The path is judged as written AND after resolving links (so a junction or symbolic link that leads
    into OneDrive or onto a network share is caught). Nothing is created or written.
    """
    cls = classifier or PathClassifier()
    raw = os.fspath(path)
    if not raw or not raw.strip():
        raise PathRefused("empty", "The journal folder is not set.")

    if is_unc(raw):
        raise PathRefused("unc", "The journal and lock folder must be on this computer, not a network path "
                                 "(UNC path refused): %s" % raw)

    local = _strip_device_prefix(raw)
    if not os.path.isabs(local):
        local = os.path.abspath(local)
    absolute = os.path.normpath(local)

    # Resolve links, then judge both forms.
    try:
        real = os.path.realpath(absolute)
    except OSError as exc:
        raise PathRefused("unresolvable", "The journal folder could not be resolved (%s): %s" % (exc, raw))
    real = _strip_device_prefix(real)
    for form in (absolute, real):
        if is_unc(form):
            raise PathRefused("unc", "The journal and lock folder resolves to a network path (UNC path "
                                     "refused): %s" % raw)

    for form in (absolute, real):
        dtype = cls.drive_type(form)
        if dtype != DRIVE_FIXED:
            raise PathRefused(
                "drive_not_fixed",
                "The journal and lock folder must be on a fixed local drive; %s is %s%s: %s"
                % (os.path.splitdrive(form)[0] or "this path",
                   "of an unknown type" if dtype == 0 else "a %s drive" % DRIVE_NAMES.get(dtype, str(dtype)),
                   " (unknown blocks)" if dtype == 0 else "", raw))

    for folder in cls.onedrive_folders():
        for form in (absolute, real):
            if _is_under(form, folder):
                raise PathRefused(
                    "onedrive",
                    "The journal and lock folder must not be inside a OneDrive folder (%s): a sync client must "
                    "never touch a live journal. Path: %s" % (folder, raw))
    return absolute


def validate_project_uid(project_uid):
    uid = str(project_uid or "")
    if not _UID_OK.match(uid):
        raise PathRefused("bad_project_uid", "The project uid cannot be used as a folder name: %r" % (project_uid,))
    return uid


def default_base_folder():
    """%LOCALAPPDATA%\\FileOrganizer\\journals (computed, never created here)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise PathRefused("no_localappdata", "%LOCALAPPDATA% is not set; the journal folder cannot be chosen.")
    return os.path.join(base, "FileOrganizer", "journals")


def store_folder(project_uid, override=None, classifier=None):
    """The validated journal-and-lock folder for `project_uid`.

    `override` is the folder itself (it is used as given, NOT extended with the uid): every test passes one
    under its own sandbox root. Without an override the production default is used.
    """
    uid = validate_project_uid(project_uid)
    folder = os.fspath(override) if override is not None else os.path.join(default_base_folder(), uid)
    return validate_store_path(folder, classifier)


def ensure_folder(project_uid, override=None, classifier=None):
    """Validate, then create the folder (and only that folder tree). Returns the folder."""
    folder = store_folder(project_uid, override, classifier)
    os.makedirs(folder, exist_ok=True)
    return folder


def journal_path(folder, project_uid):
    return os.path.join(folder, "%s.journal.db" % validate_project_uid(project_uid))


def lock_path(folder, project_uid):
    return os.path.join(folder, "%s.lock" % validate_project_uid(project_uid))


def root_lock_folder(override=None, classifier=None):
    """The folder of the root-level locks. It must be SHARED by every project on this machine (the lock exists
    so that two projects aimed at the same or nested roots cannot mutate at once), so it is not keyed by a
    project uid: production default %LOCALAPPDATA%\\FileOrganizer\\journals\\_root_locks. Tests pass a sandbox."""
    folder = os.fspath(override) if override is not None else os.path.join(default_base_folder(), "_root_locks")
    return validate_store_path(folder, classifier)
