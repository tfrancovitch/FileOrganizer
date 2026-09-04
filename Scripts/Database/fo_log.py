#!/usr/bin/env python3
r"""
fo_log.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, R2 (Logging + Run/Stage Persistence)
Module version: 1.0.0
===================================================================

The text-logging half of R2. Writes the two human-readable logs and
knows nothing about SQLite:

    TheFileOrganizer\Logs\app.log
        Application-level. Always available, including before any
        project or run exists, which is exactly when the failures that
        are hardest to explain happen (Python missing, project folder
        unwritable, database refused).

    Projects\<Project>\Runs\<RunFolder>\Logs\run.log
        Per-run-folder master log. Appended to, not overwritten: the
        Runs\<timestamp> folder is written to by several execution
        sessions over its lifetime (pre-scan, then hashing, then
        analyzers), and one file showing everything that happened to
        that folder is more useful than three that each show a third
        of it. Each session writes a delimited block with its own
        header, timeline and summary.

DELIBERATE SEPARATION FROM THE DATABASE
Text logging must survive the database being absent, refused, or
broken -- a legacy Alpha project has no database at all, and the
moment a database problem needs explaining is the moment it cannot be
used to explain itself. Nothing in this module imports fo_db.

NEVER RAISES
Every write is wrapped. A full disk, a locked file or a read-only
folder degrades logging to silence and sets .degraded; it never takes
down a scan that was otherwise going to succeed. Logging is evidence,
not a dependency.

BUFFERING
A run's log has nowhere to go until its run folder exists, and an
initial scan does not have one until PreliminaryInventory.ps1 has
minted it. RunLog therefore accepts lines before it has a path, holds
them in memory, and flushes them in order once bind() is called. If it
is never bound, close() spills the buffer into app.log rather than
dropping it.
"""

import os
import sys
import threading
from datetime import datetime, timezone


#: app.log is rotated at this size, keeping ROTATE_KEEP old copies.
#: Small enough to stay openable in Notepad on a machine that has been
#: running scans for a year.
ROTATE_BYTES = 2 * 1024 * 1024
ROTATE_KEEP = 3

SEVERITIES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def utc_now():
    """ISO-8601 UTC, millisecond precision, explicit Z -- same format
    fo_db.py uses, so timestamps sort and compare across log and
    database without conversion."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _local_now():
    """Local time for the human-facing header. The operator thinks in
    local time; every machine-readable timestamp stays UTC."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def format_line(severity, message, stage=None):
    """One log line. Fixed-width severity and stage columns so the file
    stays scannable by eye, which is the entire point of it existing
    alongside the structured event table."""
    sev = (severity or "INFO").upper()
    if sev not in SEVERITIES:
        sev = "INFO"
    stage_col = ("[%s]" % stage) if stage else ""
    text = "" if message is None else str(message)
    # Multi-line messages (a captured traceback, a stderr block) are
    # indented under their own header rather than being written flush
    # left, where they would read as separate log entries.
    lines = text.splitlines() or [""]
    head = "%s  %-8s %-26s %s" % (utc_now(), sev, stage_col, lines[0])
    if len(lines) == 1:
        return head
    indent = " " * 4
    return "\n".join([head] + [indent + ln for ln in lines[1:]])


class _FileSink(object):
    """Append-only text sink. UTF-8 without BOM, \\n line endings.

    No BOM because these files are read by Python (utf-8-sig would
    tolerate one, but PowerShell 5.1's Get-Content and most editors
    handle a plain UTF-8 file better than a mid-file BOM if the file is
    ever concatenated). Opened and closed per write rather than held
    open: a scan can run for hours, and an open handle on a OneDrive or
    network path across a sync or a sleep is a liability for a file
    whose only job is to be readable afterwards.
    """

    def __init__(self, path):
        self.path = str(path)
        self.degraded = False
        self.degraded_reason = None
        self._lock = threading.Lock()

    def _ensure_parent(self):
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)

    def write(self, text):
        with self._lock:
            try:
                self._ensure_parent()
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(text + "\n")
                return True
            except Exception as exc:  # never fatal -- see module docstring
                if not self.degraded:
                    self.degraded = True
                    self.degraded_reason = "%s: %s" % (type(exc).__name__, exc)
                return False

    def rotate_if_needed(self, max_bytes=ROTATE_BYTES, keep=ROTATE_KEEP):
        with self._lock:
            try:
                if not os.path.isfile(self.path):
                    return
                if os.path.getsize(self.path) < max_bytes:
                    return
                oldest = "%s.%d" % (self.path, keep)
                if os.path.isfile(oldest):
                    os.remove(oldest)
                for index in range(keep - 1, 0, -1):
                    src = "%s.%d" % (self.path, index)
                    if os.path.isfile(src):
                        os.replace(src, "%s.%d" % (self.path, index + 1))
                os.replace(self.path, "%s.1" % self.path)
            except Exception:
                # Rotation is housekeeping. Failing to rotate is not a
                # reason to stop logging.
                pass


class AppLog(object):
    r"""The application-level log at TheFileOrganizer\Logs\app.log.

    Exists so that a failure with no project and no run to attach
    itself to still leaves a trace. Process-wide singleton via
    get_app_log().
    """

    def __init__(self, app_root):
        self.app_root = str(app_root)
        self.log_dir = os.path.join(self.app_root, "Logs")
        self.path = os.path.join(self.log_dir, "app.log")
        self._sink = _FileSink(self.path)
        self._sink.rotate_if_needed()

    @property
    def degraded(self):
        return self._sink.degraded

    @property
    def degraded_reason(self):
        return self._sink.degraded_reason

    def log(self, severity, message, stage=None):
        return self._sink.write(format_line(severity, message, stage))

    def debug(self, message, stage=None):
        return self.log("DEBUG", message, stage)

    def info(self, message, stage=None):
        return self.log("INFO", message, stage)

    def warning(self, message, stage=None):
        return self.log("WARNING", message, stage)

    def error(self, message, stage=None):
        return self.log("ERROR", message, stage)

    def critical(self, message, stage=None):
        return self.log("CRITICAL", message, stage)

    def session_start(self, app_version, extra=None):
        self._sink.write("")
        self._sink.write("=" * 78)
        self.info("The File Organizer %s starting (pid %d, Python %s)"
                  % (app_version, os.getpid(),
                     ".".join(str(n) for n in sys.version_info[:3])))
        if extra:
            for key in sorted(extra):
                self.info("  %-22s %s" % (key, extra[key]))

    def session_end(self, note=""):
        self.info("Application closing.%s" % ((" " + note) if note else ""))


_APP_LOG = None
_APP_LOG_LOCK = threading.Lock()


def get_app_log(app_root=None):
    """Process-wide AppLog. app_root is required on the first call and
    ignored afterwards."""
    global _APP_LOG
    with _APP_LOG_LOCK:
        if _APP_LOG is None:
            if app_root is None:
                raise ValueError("get_app_log() needs app_root on first use")
            _APP_LOG = AppLog(app_root)
        return _APP_LOG


def reset_app_log():
    """Test hook. Production code never calls this."""
    global _APP_LOG
    with _APP_LOG_LOCK:
        _APP_LOG = None


class RunLog(object):
    r"""The per-run-folder master log at Runs\<RunFolder>\Logs\run.log.

    Written as: header, then a timeline of stage transitions and
    notable events, then a summary. Every session produces all three,
    including a session where nothing went wrong -- a successful run
    that leaves no evidence it happened is the specific problem R2
    exists to fix.

    Accepts lines before bind() (see module docstring) and never
    raises.
    """

    def __init__(self, run_uid, run_kind, app_version, app_log=None):
        self.run_uid = run_uid
        self.run_kind = run_kind
        self.app_version = app_version
        self.app_log = app_log
        self.path = None
        self._sink = None
        self._buffer = []
        self._lock = threading.Lock()
        self._header_written = False
        self._closed = False

    # -- plumbing ----------------------------------------------------

    def _emit(self, text):
        """Write now if bound, otherwise hold it. Ordering across the
        bind boundary is preserved because bind() flushes the whole
        buffer before anything new is written."""
        with self._lock:
            if self._sink is None:
                self._buffer.append(text)
                return False
            return self._sink.write(text)

    def bind(self, run_folder_path):
        r"""Point this log at Runs\<RunFolder>\Logs\run.log and flush
        anything buffered. Safe to call once; later calls are ignored."""
        with self._lock:
            if self._sink is not None:
                return self.path
            logs_dir = os.path.join(str(run_folder_path), "Logs")
            self.path = os.path.join(logs_dir, "run.log")
            self._sink = _FileSink(self.path)
            pending, self._buffer = self._buffer, []
        for text in pending:
            self._sink.write(text)
        return self.path

    @property
    def bound(self):
        return self._sink is not None

    @property
    def degraded(self):
        return bool(self._sink and self._sink.degraded)

    # -- structure ---------------------------------------------------

    def header(self, project_name, details=None):
        """Opens this session's block. The delimiter and the run UID are
        what let a reader separate this session from the previous ones
        already in the file."""
        if self._header_written:
            return
        self._header_written = True
        self._emit("")
        self._emit("=" * 78)
        self._emit("  RUN %s" % self.run_uid)
        self._emit("  %-14s %s" % ("Project:", project_name))
        self._emit("  %-14s %s" % ("Kind:", self.run_kind))
        self._emit("  %-14s %s (local)  /  %s" % ("Started:", _local_now(), utc_now()))
        self._emit("  %-14s %s" % ("App version:", self.app_version))
        self._emit("  %-14s %d" % ("Process:", os.getpid()))
        for key in sorted(details or {}):
            self._emit("  %-14s %s" % (key + ":", details[key]))
        self._emit("=" * 78)

    def log(self, severity, message, stage=None):
        return self._emit(format_line(severity, message, stage))

    def debug(self, message, stage=None):
        return self.log("DEBUG", message, stage)

    def info(self, message, stage=None):
        return self.log("INFO", message, stage)

    def warning(self, message, stage=None):
        return self.log("WARNING", message, stage)

    def error(self, message, stage=None):
        return self.log("ERROR", message, stage)

    @staticmethod
    def _stage_number(sequence, total):
        # The coordinator does not know how many stages a run will have
        # -- the dashboard decides that, and for the category path it
        # depends on what the user ticked. "STAGE 3" is honest; "STAGE
        # 3/?" just advertises the gap.
        return "%d of %d" % (sequence, total) if total else "%d" % sequence

    def stage_start(self, sequence, total, stage_key, label):
        self.info("STAGE %s  %s -- starting"
                  % (self._stage_number(sequence, total), label or stage_key),
                  stage=stage_key)

    def stage_end(self, sequence, total, stage_key, label, status, duration_ms,
                  exit_code=None, note=None):
        severity = {
            "completed": "INFO",
            "no_applicable_files": "INFO",
            "skipped": "INFO",
            "paused": "INFO",
            "completed_with_warnings": "WARNING",
            "interrupted": "WARNING",
            "cancelled": "WARNING",
            "failed": "ERROR",
        }.get(status, "INFO")
        seconds = (duration_ms or 0) / 1000.0
        detail = "STAGE %s  %s -- %s (%.1fs" % (
            self._stage_number(sequence, total), label or stage_key,
            status.upper(), seconds)
        detail += ", exit %s)" % exit_code if exit_code is not None else ")"
        if note:
            detail += " -- %s" % note
        self.log(severity, detail, stage=stage_key)

    def summary(self, status, duration_ms, stages, warning_count, error_count,
                extra_lines=None):
        """Closes this session's block. Deliberately verbose about the
        boring case: a clean run states that it was clean, per stage,
        rather than saying nothing."""
        self._emit("-" * 78)
        self._emit("  RUN SUMMARY -- %s" % status.upper())
        self._emit("  %-22s %s" % ("Run UID:", self.run_uid))
        self._emit("  %-22s %.1f seconds" % ("Duration:", (duration_ms or 0) / 1000.0))
        self._emit("  %-22s %d" % ("Stages executed:", len(stages)))
        for item in stages:
            self._emit("      %-34s %s%s" % (
                item.get("label") or item.get("stage_key"),
                (item.get("status") or "?").upper(),
                ("  (%.1fs)" % ((item.get("duration_ms") or 0) / 1000.0))))
        self._emit("  %-22s %d" % ("Warnings:", warning_count))
        self._emit("  %-22s %d" % ("Errors:", error_count))
        for line in (extra_lines or []):
            self._emit("  %s" % line)
        self._emit("  %-22s %s (local)  /  %s" % ("Finished:", _local_now(), utc_now()))
        self._emit("=" * 78)
        self._emit("")

    def close(self):
        """Spill anything still buffered into app.log so a run that
        never got a folder -- project creation failed, say -- is not a
        silent one."""
        if self._closed:
            return
        self._closed = True
        with self._lock:
            pending, self._buffer = self._buffer, []
        if pending and self.app_log is not None:
            self.app_log.warning(
                "Run %s produced no run folder; its log is inlined below."
                % self.run_uid)
            for text in pending:
                self.app_log.log("INFO", text)
