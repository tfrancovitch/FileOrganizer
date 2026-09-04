#!/usr/bin/env python3
r"""
RunCoordinator.py
===================================================================
PRODUCTION CODE -- The File Organizer B6.1
===================================================================

The execution and operational-record boundary used by Dashboard.py.

RunCoordinator launches the supported Python inventory/hash/analyzer engines,
opens/closes run and stage records, persists structured events, maintains the
project-local SQLite authority, renders derived outputs, and coordinates
project/run folders. Windows bootstrap helpers remain external; the retired
CSV-to-database persistence paths were removed in B6.1.

Failures are recorded with stage attribution. A failure to persist required
engine results must not be converted into a successful stage; optional
observability/logging failures may degrade with an explicit warning where the
underlying source operation remains trustworthy.
"""

import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
DATABASE_DIR = SCRIPTS_DIR / "Database"
if str(DATABASE_DIR) not in sys.path:
    sys.path.insert(0, str(DATABASE_DIR))

import fo_analyzer_engine   # noqa: E402
import fo_estimates         # noqa: E402
import fo_project           # noqa: E402
import fo_analyzer_records  # noqa: E402
import fo_db      # noqa: E402  (path set above)
import fo_env     # noqa: E402
import fo_exports    # noqa: E402
import fo_hash_engine   # noqa: E402
import fo_hash_records  # noqa: E402
import fo_hash_reports  # noqa: E402
import fo_hashes     # noqa: E402
import fo_inventory  # noqa: E402
import fo_inventory_records  # noqa: E402
import fo_log     # noqa: E402
import fo_runs    # noqa: E402
import fo_scan    # noqa: E402
import win_meta   # noqa: E402

MODULE_VERSION = "1.3.0"
APP_VERSION = fo_db.APP_VERSION

HEARTBEAT_SECONDS = 30

#: Ceiling on events derived from one captured stream. A pathological
#: stage could emit thousands of ERROR lines; the first hundreds explain
#: it and a truncation notice preserves the fact that there were more.
MAX_DERIVED_EVENTS_PER_STREAM = 200

#: Exit code 2 means "paused at a checkpoint by user request" across
#: this application (see Dashboard.run_powershell and ImageHash.py).
EXIT_CODE_PAUSED = 2

#: Primary output of each stage, relative to the run folder.
#:
#: Stable operator-visible artifact names associated with historical stage keys.
#: Analyzer completion semantics come from persisted analyzer outcomes; absence of
#: a CSV is not used as the authority for success/failure/no-applicable-files.
STAGE_EXPECTED_OUTPUT = {
    "PreliminaryInventory.ps1": "Inventory/PreliminaryInventory.csv",
    "PotentialDuplicates.ps1":  "Inventory/PotentialDuplicates.csv",
    "PartialHash.ps1":          "Inventory/PartialHashCandidates.csv",
    "FullHash.ps1":             "Inventory/DuplicateHashInventory.csv",
    "FullHashInventory.ps1":    "Inventory/FullHashInventory.csv",
    "ImageAnalysis.ps1":        "Inventory/ImageHashes.csv",
    "PDFAnalysis.ps1":          "Inventory/PDFInventory.csv",
    "OfficeAnalysis.ps1":       "Inventory/OfficeInventory.csv",
    "RawImageAnalysis.ps1":     "Inventory/RawImageInventory.csv",
    "AudioAnalysis.ps1":        "Inventory/AudioInventory.csv",
    "VideoAnalysis.ps1":        "Inventory/VideoInventory.csv",
    "TextFileAnalysis.ps1":     "Inventory/TextFileInventory.csv",
    "ArchiveAnalysis.ps1":      "Inventory/ArchiveInventory.csv",
    "ContentExtraction.ps1":    "Inventory/ContentIndex.csv",
}

STAGE_ROLES = {
    "New-Project.ps1":          "project",
    "PreliminaryInventory.ps1": "inventory",
    "PotentialDuplicates.ps1":  "duplicate",
    "PartialHash.ps1":          "identity",
    "FullHash.ps1":             "identity",
    "FullHashInventory.ps1":    "identity",
    "TimeEstimates.ps1":        "support",
}

_ERROR_LINE = re.compile(r"^\s*(ERROR|FATAL|CRITICAL)\b[:\s]", re.IGNORECASE)
_WARNING_LINE = re.compile(r"^\s*(WARNING|WARN)\b[:\s]", re.IGNORECASE)

#: PreliminaryInventory.ps1 writes Logs\errors.txt in this shape.
_ERRORS_TXT_LINE = re.compile(
    r"^(?P<kind>DIRECTORY ACCESS ERROR|FILE ERROR):\s*(?P<path>.*?)\s+--\s+(?P<message>.*)$")


def utc_now():
    return fo_runs.utc_now()


def _process_start_utc():
    """A best-effort process-start marker, stored beside host_pid so a
    recycled PID is less likely to be mistaken for a live run. Falls
    back to 'now', which is close enough for the coordinator's purpose
    since the run is created moments after the process starts."""
    return utc_now()


# ---------------------------------------------------------------------------
# Stage handle
# ---------------------------------------------------------------------------

class StageHandle(object):
    """One stage of a run. Obtained from RunCoordinator.stage().

    Used as a context manager:

        with coordinator.stage("PDFAnalysis.ps1", "PDFs") as stage:
            ok, out, err, code = run_powershell(...)
            stage.record(code, out, err)

    If record() is never called -- because the caller raised -- the
    stage is closed as failed rather than left dangling, so an
    unexpected exception in the dashboard cannot silently produce a
    stage that appears to still be running forever.
    """

    def __init__(self, coordinator, sequence, stage_key, label, role=None, command=None):
        self.coordinator = coordinator
        self.sequence = sequence
        self.stage_key = stage_key
        self.label = label or stage_key
        self.role = role or STAGE_ROLES.get(stage_key, "analyzer")
        self.command = command
        self.run_stage_id = None
        self.status = None
        self.exit_code = None
        self.started_monotonic = time.monotonic()
        self.started_utc = None
        self.ended_utc = None
        self.duration_ms = None
        self.stdout_rel = None
        self.stderr_rel = None
        self.skip_reason = None
        self.note = None
        self._recorded = False
        self._events = []
        self._event_file = None
        self._prior_event_file = None

    # -- lifecycle ---------------------------------------------------

    def _begin(self):
        self.started_utc = utc_now()
        self.coordinator._log_stage_start(self)
        self.run_stage_id = self.coordinator._db_create_stage(self)
        self._event_file = self.coordinator._activate_event_file(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.coordinator._deactivate_event_file(self)
        if self._recorded:
            return False
        if exc_type is not None:
            self.record_failure(
                "%s raised %s: %s" % (self.label, exc_type.__name__, exc_value))
        else:
            # Left the block without recording anything. Say so rather
            # than inventing an outcome.
            self.record_failure(
                "%s finished without recording an outcome." % self.label)
        return False  # never swallow the caller's exception

    # -- recording ---------------------------------------------------

    def add_event(self, severity, message, category="stage", file_path=None,
                  error_type=None, continued=None, file_skipped=None,
                  retryable=None, detail=None, source="coordinator"):
        """Queue a structured event. Flushed to the database when the
        stage closes."""
        self._events.append({
            "event_utc": utc_now(), "severity": severity, "category": category,
            "source": source, "stage_key": self.stage_key, "file_path": file_path,
            "message": message, "error_type": error_type, "continued": continued,
            "file_skipped": file_skipped, "retryable": retryable, "detail": detail,
        })

    def record(self, exit_code=0, stdout="", stderr="", status=None,
               expected_output=None, note=None):
        """Record the outcome of a stage that actually ran.

        status may be forced by the caller; otherwise it is derived from
        the exit code, the captured output, and whether the stage's
        expected output file exists.
        """
        self._recorded = True
        self.exit_code = exit_code
        self.duration_ms = int((time.monotonic() - self.started_monotonic) * 1000)

        stdout_rel, stderr_rel = self.coordinator._write_stage_capture(
            self, stdout, stderr)

        derived = self._derive_events(stdout, stderr)
        self._events.extend(derived)
        self._events.extend(self.coordinator._ingest_event_file(self))

        if status is None:
            status = self._derive_status(exit_code, expected_output)
        self.status = status

        self.coordinator._close_stage(self, status, exit_code, stdout_rel,
                                      stderr_rel, note)
        return status

    def record_skipped(self, reason):
        self._recorded = True
        self.status = "skipped"
        self.duration_ms = int((time.monotonic() - self.started_monotonic) * 1000)
        self.coordinator._close_stage(self, "skipped", None, None, None,
                                      note=reason, skip_reason=reason)
        return "skipped"

    def record_failure(self, message, exit_code=None):
        self._recorded = True
        self.status = "failed"
        self.exit_code = exit_code
        self.duration_ms = int((time.monotonic() - self.started_monotonic) * 1000)
        self.add_event("error", message, category="stage", continued=False)
        self.coordinator._close_stage(self, "failed", exit_code, None, None,
                                      note=message)
        return "failed"

    # -- derivation --------------------------------------------------

    def _derive_status(self, exit_code, expected_output):
        if exit_code == EXIT_CODE_PAUSED:
            return "paused"
        if exit_code != 0:
            return "failed"

        relative = expected_output or STAGE_EXPECTED_OUTPUT.get(self.stage_key)
        if relative and self.coordinator.run_folder_path:
            produced = Path(self.coordinator.run_folder_path) / relative
            if not produced.exists():
                # Exited cleanly and wrote nothing: no files of this
                # kind were present. Distinct from skipped (never ran)
                # and from failed (ran and broke).
                return "no_applicable_files"

        if any(e["severity"] in ("warning", "error", "critical") for e in self._events):
            return "completed_with_warnings"
        return "completed"

    def _derive_events(self, stdout, stderr):
        r"""Recover warnings and errors from a protected script's output.

        The Alpha processing scripts could not be modified in R2, so
        their problems are read back out of the console output they
        already produce, using the ERROR:/WARNING: convention they
        already follow. These events are tagged source='stage_stdout' or
        'stage_stderr' so that they are never confused with an event a
        script deliberately emitted through Write-FoEvent -- one is an
        observation, the other is a statement, and the event table
        should not present a guess with the same authority as a fact.
        """
        events = []
        for stream_name, text, source in (("stdout", stdout, "stage_stdout"),
                                          ("stderr", stderr, "stage_stderr")):
            if not text:
                continue
            captured = 0
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if _ERROR_LINE.match(stripped):
                    severity = "error"
                elif _WARNING_LINE.match(stripped):
                    severity = "warning"
                else:
                    continue
                if captured >= MAX_DERIVED_EVENTS_PER_STREAM:
                    events.append({
                        "event_utc": utc_now(), "severity": "info",
                        "category": "stage", "source": source,
                        "stage_key": self.stage_key,
                        "message": ("More than %d %s lines were produced by %s; "
                                    "the remainder are in the captured %s log."
                                    % (MAX_DERIVED_EVENTS_PER_STREAM, severity,
                                       self.stage_key, stream_name)),
                        "continued": True, "detail": None,
                    })
                    break
                events.append({
                    "event_utc": utc_now(), "severity": severity,
                    "category": "stage", "source": source,
                    "stage_key": self.stage_key, "message": stripped,
                    # The stage kept running unless its exit code says
                    # otherwise, which _derive_status decides separately.
                    "continued": None, "detail": None,
                })
                captured += 1

            # A Python traceback in stderr has no ERROR: prefix and would
            # otherwise be invisible in the structured record.
            if source == "stage_stderr" and "Traceback (most recent call last)" in text:
                events.append({
                    "event_utc": utc_now(), "severity": "error",
                    "category": "stage", "source": source,
                    "stage_key": self.stage_key,
                    "message": "An unhandled Python exception occurred in %s." % self.stage_key,
                    "error_type": "PythonTraceback",
                    "continued": False, "retryable": True,
                    "detail": {"traceback_tail": text[-2000:]},
                })
        return events


# ---------------------------------------------------------------------------
# Run coordinator
# ---------------------------------------------------------------------------

class RunCoordinator(object):
    r"""Records one execution session.

    Lifecycle:

        coordinator = RunCoordinator(app_root, project_name)
        coordinator.begin_run("prescan")
        ... coordinator.stage(...) blocks ...
        coordinator.bind_run_folder("2026-08-15_120000")   # when known
        coordinator.finish()

    Every method is safe to call when the database is unavailable; the
    run still produces a run.log and app.log record.
    """

    def __init__(self, app_root, project_name=None, app_log=None,
                 app_version=APP_VERSION):
        self.app_root = Path(app_root)
        self.project_name = project_name
        self.app_version = app_version
        self.app_log = app_log or fo_log.get_app_log(str(self.app_root))

        self.project_dir = (self.app_root / "Projects" / project_name) if project_name else None
        self.run_uid = None
        self.run_id = None
        self.run_kind = None
        self.run_label = None
        self.run_started_utc = None
        self._environment = None
        self._environment_hash = None
        self.run_folder_name = None
        self.run_folder_path = None
        self._hash_result = None
        self._analyzer_result = None
        #: path_keys of the roots this run actually scanned.
        self._scanned_root_keys = set()
        self.run_log = None

        self.db_available = False
        self.db_reason = None
        self.tolerate_stage_failures = False

        # Stages whose failure is recorded honestly on the stage row but
        # must NOT make the run report as failed, because the run's
        # actual purpose still succeeded. Inventory ingestion is the
        # first: the Alpha scan and its CSV are already complete and
        # correct on disk when it runs, so a database problem costs a
        # convenience, not the scan. Reporting "run failed" there would
        # tell the operator their scan broke when it did not.
        self._non_fatal_stages = set()

        #: What the Beta B2 inventory engine did on this run, so the
        #: persistence outcome can be recorded as its own stage after
        #: the scan stage closes. None until run_inventory() has run.
        self._inventory_result = None

        self._sequence = 0
        self._stages = []
        self._started_monotonic = None
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        self._pending_captures = []
        self._lock = threading.Lock()
        self._finished = False

    # -- database plumbing -------------------------------------------

    @contextmanager
    def _db(self):
        r"""Open the project database, yield it, commit, close.

        Yields None when there is no usable database -- a legacy Alpha
        project, a project whose database creation was skipped because
        Python was missing at the time, or a database the isolation
        guard refused. Callers treat None as "record it in the log
        only".
        """
        if not self.project_dir:
            yield None
            return

        # B6.2 Fix 1 -- setup and body are SEPARATE.
        #
        # A @contextmanager generator must yield exactly once. The old
        # version wrapped setup and body in one try/except whose except
        # clause did `yield None`. When the *body* raised (a caller error
        # -- disk full, an OperationalError, anything), that except caught
        # it and yielded a second time, so Python discarded the real
        # exception and raised "RuntimeError: generator didn't stop after
        # throw()". Every failure inside a `with self._db()` block then
        # reported the wrong cause; on Windows that fired on every run.
        #
        # Setup failure (cannot open the database) still yields None, so a
        # legacy Alpha project or a project whose database creation was
        # skipped keeps running on file logging only, exactly as before.
        # Body failure now rolls back (best effort) and re-raises the
        # original exception untouched.
        conn = None
        try:
            conn, _project = fo_db.open_project(str(self.project_dir),
                                                app_version=self.app_version)
            fo_runs.ensure_schema(conn)
        except Exception as exc:
            reason = "%s: %s" % (type(exc).__name__, exc)
            if reason != self.db_reason:
                # Log the reason once per distinct cause, not once per
                # operation -- a project with no database would
                # otherwise fill app.log with the same line.
                self.db_reason = reason
                self.app_log.warning(
                    "Database unavailable for project '%s'; continuing with file "
                    "logging only. %s" % (self.project_name, reason))
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            yield None
            return

        try:
            yield conn
            conn.commit()
            if not self.db_available:
                self.db_available = True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # -- run lifecycle -----------------------------------------------

    def begin_run(self, run_kind, run_label=None, run_folder=None,
                  tolerate_stage_failures=False, environment=None):
        """Open a run record and start its log. Returns the run UID."""
        self.run_kind = run_kind
        self.run_uid = fo_runs.new_uid()
        self.run_label = run_label
        self.tolerate_stage_failures = tolerate_stage_failures
        self._started_monotonic = time.monotonic()
        self.run_started_utc = utc_now()
        self._environment = None
        self._environment_hash = None

        self.run_log = fo_log.RunLog(self.run_uid, run_kind, self.app_version,
                                     app_log=self.app_log)
        if run_folder:
            self.bind_run_folder(run_folder, write_header=False)

        snapshot = environment if environment is not None else get_environment(
            self.app_version, self._source_root_paths())
        snapshot_hash = fo_env.environment_hash(snapshot)
        self._environment = snapshot
        self._environment_hash = snapshot_hash

        # run_kind already appears on the header's own "Kind:" line.
        self.run_log.header(self.project_name or "(no project yet)")
        for line in fo_env.describe(snapshot):
            self.run_log.info("  " + line)

        self.app_log.info("Run %s started (%s) for project '%s'."
                          % (self.run_uid, run_kind, self.project_name))

        with self._db() as conn:
            if conn is not None:
                environment_snapshot_id = fo_runs.upsert_environment_snapshot(
                    conn, snapshot, snapshot_hash)
                self.run_id = fo_runs.create_run(
                    conn, self.run_uid, run_kind, self.app_version,
                    fo_db.APP_SCHEMA_VERSION, run_label=run_label,
                    run_folder=self.run_folder_name, host_pid=os.getpid(),
                    process_started_utc=_process_start_utc(),
                    environment_snapshot_id=environment_snapshot_id,
                    status="running", started_utc=self.run_started_utc)
                self._record_source_roots(conn, snapshot)
                fo_runs.add_event(
                    conn, "info", "lifecycle", "coordinator",
                    "Run started: %s" % run_kind, run_id=self.run_id)

        if self.run_id is None:
            self.run_log.warning(
                "No project database is available, so this run is recorded in "
                "this log only. %s" % (self.db_reason or ""))

        self._start_heartbeat()
        return self.run_uid

    def bind_run_folder(self, run_folder_name, write_header=True):
        r"""Attach this run to the Runs\<folder> it is writing into.

        An initial scan cannot do this at begin_run(): the folder is
        minted by PreliminaryInventory.ps1, which is a protected Alpha
        file and mints it partway through the run. Everything logged
        before this point is buffered in memory and flushed here in
        order.
        """
        if not run_folder_name or self.run_folder_name:
            return self.run_folder_path
        self.run_folder_name = str(run_folder_name)
        if self.project_dir:
            self.run_folder_path = self.project_dir / "Runs" / self.run_folder_name
        if self.run_log and self.run_folder_path:
            self.run_log.bind(str(self.run_folder_path))
            if write_header:
                self.run_log.info("Run folder resolved: Runs\\%s" % self.run_folder_name)
        self._flush_pending_captures()

        with self._db() as conn:
            if conn is not None and self.run_id is not None:
                fo_runs.set_run_folder(conn, self.run_id, self.run_folder_name)
        return self.run_folder_path

    def attach_database(self, project_name=None):
        """Bind this run to a database that did not exist when it began.

        An initial scan starts before its project does. The run is
        already under way -- and its first stage, creating the project,
        is the one most worth having a record of when it fails -- so the
        coordinator records it in memory and calls this once the project
        folder and its database exist.

        Anything already recorded is replayed in order: the run row is
        written with its original start time, then each completed stage
        with its own timings, statuses and events. The result is
        indistinguishable from a run that had a database all along.

        Returns True if the run is now backed by a database.
        """
        if self.run_id is not None:
            return True
        if project_name:
            self.project_name = project_name
            self.project_dir = self.app_root / "Projects" / project_name
        if not self.project_dir:
            return False

        with self._db() as conn:
            if conn is None:
                return False
            # B4.4: RECOMPUTE the snapshot here rather than reusing the
            # one cached at begin_run().
            #
            # A creation run begins BEFORE its project exists, so at
            # that moment _source_root_paths() has no database to read
            # and no settings.json to fall back on -- the cached
            # snapshot describes zero source roots. Attaching is the
            # first moment the roots are knowable, and persisting the
            # stale snapshot meant a two-root project's environment
            # record showed none.
            #
            # The cached one is kept only if it already describes at
            # least one root, so a run that attaches to an existing
            # project does not pay for a second filesystem probe.
            cached = self._environment or {}
            if cached.get("source_roots"):
                snapshot = cached
                snapshot_hash = self._environment_hash or \
                    fo_env.environment_hash(snapshot)
            else:
                snapshot = get_environment(self.app_version,
                                           self._source_root_paths())
                snapshot_hash = fo_env.environment_hash(snapshot)
                self._environment = snapshot
                self._environment_hash = snapshot_hash
            environment_snapshot_id = fo_runs.upsert_environment_snapshot(
                conn, snapshot, snapshot_hash)

            self.run_id = fo_runs.create_run(
                conn, self.run_uid, self.run_kind, self.app_version,
                fo_db.APP_SCHEMA_VERSION, run_label=getattr(self, "run_label", None),
                run_folder=self.run_folder_name, host_pid=os.getpid(),
                process_started_utc=_process_start_utc(),
                environment_snapshot_id=environment_snapshot_id,
                status="running", started_utc=self.run_started_utc)
            self._record_source_roots(conn, snapshot)
            fo_runs.add_event(
                conn, "info", "lifecycle", "coordinator",
                "Run started: %s (recorded once the project database existed)"
                % self.run_kind, run_id=self.run_id,
                event_utc=self.run_started_utc)

            for handle in self._stages:
                if handle.run_stage_id is not None:
                    continue
                handle.run_stage_id = fo_runs.create_stage(
                    conn, self.run_id, handle.sequence, handle.stage_key,
                    stage_label=handle.label, stage_role=handle.role,
                    command=handle.command, status="running",
                    started_utc=handle.started_utc or self.run_started_utc)
                if handle._events:
                    for item in handle._events:
                        item["run_id"] = self.run_id
                        item["run_stage_id"] = handle.run_stage_id
                    fo_runs.add_events(conn, handle._events, run_id=self.run_id)
                if handle.status:
                    fo_runs.finish_stage(
                        conn, handle.run_stage_id, handle.status,
                        exit_code=handle.exit_code,
                        ended_utc=handle.ended_utc,
                        stdout_log_path=handle.stdout_rel,
                        stderr_log_path=handle.stderr_rel,
                        skip_reason=handle.skip_reason, notes=handle.note)

        if self.run_id is not None:
            self.run_log.info(
                "Project database attached; this run is recorded as run_id %d."
                % self.run_id)
            self._start_heartbeat()
            return True
        return False

    def stage(self, stage_key, label=None, role=None, command=None):
        """Open the next stage. Use as a context manager."""
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        handle = StageHandle(self, sequence, stage_key, label, role=role,
                             command=command)
        handle._begin()
        self._stages.append(handle)
        return handle

    def skip_stage(self, stage_key, label, reason):
        """Record a stage that was deliberately not run."""
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
        handle = StageHandle(self, sequence, stage_key, label)
        handle.status = "skipped"
        handle.duration_ms = 0
        self._stages.append(handle)
        if self.run_log:
            self.run_log.info("STAGE %d  %s -- SKIPPED (%s)"
                              % (sequence, label or stage_key, reason),
                              stage=stage_key)
        with self._db() as conn:
            if conn is not None and self.run_id is not None:
                handle.run_stage_id = fo_runs.record_skipped_stage(
                    conn, self.run_id, sequence, stage_key, label, reason,
                    stage_role=role_for(stage_key))
        return handle

    def note(self, severity, message, category="lifecycle", **kwargs):
        """Record a run-level event that is not tied to a stage."""
        if self.run_log:
            self.run_log.log(severity.upper(), message)
        with self._db() as conn:
            if conn is not None:
                fo_runs.add_event(conn, severity, category, "coordinator",
                                  message, run_id=self.run_id, **kwargs)

    def finish(self, status=None, notes=None):
        """Close the run: roll up a status, write the summary, stop the
        heartbeat."""
        if self._finished:
            return self.run_uid
        self._finished = True
        self._stop_heartbeat()

        if status is None:
            status = self._roll_up_status()

        duration_ms = int((time.monotonic() - (self._started_monotonic or time.monotonic())) * 1000)
        warning_count = error_count = 0
        summary = None

        with self._db() as conn:
            if conn is not None and self.run_id is not None:
                fo_runs.add_event(conn, "info", "lifecycle", "coordinator",
                                  "Run finished: %s" % status, run_id=self.run_id)
                summary = fo_runs.finish_run(conn, self.run_id, status, notes=notes)

        if summary:
            warning_count = summary["warning_count"]
            error_count = summary["error_count"]
            duration_ms = summary["duration_ms"] or duration_ms
        else:
            # No database: count what this session saw in memory so the
            # log summary is still truthful.
            for handle in self._stages:
                for item in handle._events:
                    if item["severity"] == "warning":
                        warning_count += 1
                    elif item["severity"] in ("error", "critical"):
                        error_count += 1

        if self.run_log:
            extra = []
            if self.run_id is None:
                extra.append("Database        : not available (%s)"
                             % (self.db_reason or "no project database"))
            else:
                extra.append("Database        : recorded as run_id %d" % self.run_id)
            if self.run_folder_name:
                extra.append("Run folder      : Runs\\%s" % self.run_folder_name)
            self.run_log.summary(
                status, duration_ms,
                [{"stage_key": h.stage_key, "label": h.label,
                  "status": h.status or "unknown", "duration_ms": h.duration_ms}
                 for h in self._stages],
                warning_count, error_count, extra_lines=extra)
            self.run_log.close()

        self.app_log.info("Run %s finished: %s (%d stage(s), %d warning(s), %d error(s))"
                          % (self.run_uid, status, len(self._stages),
                             warning_count, error_count))
        return status

    def abandon(self, reason="The application closed while this run was in progress."):
        """Best-effort close for a run whose process is going away now.

        The reconciler would catch this on the next launch anyway; doing
        it here means the record is accurate immediately rather than
        after the next start, and costs one small transaction.
        """
        if self._finished:
            return
        self.note("warning", reason, category="lifecycle")
        self.finish(status="interrupted", notes=reason)

    # -- status roll-up ----------------------------------------------

    def _roll_up_status(self):
        statuses = [h.status for h in self._stages if h.status]
        if not statuses:
            return "completed"
        if "interrupted" in statuses:
            return "interrupted"
        if "paused" in statuses:
            return "paused"
        # A failure in a stage registered as non-fatal is downgraded to a
        # warning for the purpose of the RUN's status. The stage row
        # still says 'failed' and its error events are still recorded --
        # nothing is hidden, it is only kept from overstating what broke.
        fatal = [h.status for h in self._stages
                 if h.status and h.stage_key not in self._non_fatal_stages]
        if "failed" in statuses and "failed" not in fatal:
            return "completed_with_warnings"
        if "failed" in statuses:
            # Category stages are independent: one analyzer failing does
            # not stop the others, and the run genuinely did finish. Say
            # completed_with_warnings and let the failed stage row and
            # its error events carry the detail. A run that STOPPED at a
            # failure is a different thing and is reported as failed.
            return "completed_with_warnings" if self.tolerate_stage_failures else "failed"
        if "completed_with_warnings" in statuses:
            return "completed_with_warnings"
        return "completed"

    # -- heartbeat ---------------------------------------------------

    def _start_heartbeat(self):
        r"""Tick run.heartbeat_utc on a timer.

        Stage boundaries are far too coarse to prove liveness: hashing a
        large drive is a single stage that can run for hours. The
        heartbeat stopping is what a power cut, a kill, or a crash looks
        like from the next launch's point of view.
        """
        if self.run_id is None:
            return
        self._heartbeat_stop = threading.Event()

        def beat():
            while not self._heartbeat_stop.wait(HEARTBEAT_SECONDS):
                try:
                    with self._db() as conn:
                        if conn is not None and self.run_id is not None:
                            fo_runs.heartbeat(conn, self.run_id)
                except Exception:
                    pass  # a missed beat is not worth a crash

        self._heartbeat_thread = threading.Thread(
            target=beat, name="fo-run-heartbeat", daemon=True)
        self._heartbeat_thread.start()

    def _stop_heartbeat(self):
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
        thread = self._heartbeat_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    # -- source roots -------------------------------------------------

    def _source_root_paths(self):
        paths = []
        try:
            with self._db() as conn:
                if conn is not None:
                    paths = [row["root_path"] for row in fo_db.list_source_roots(conn)
                             if row["is_active"]]
        except Exception:
            pass
        if not paths:
            # B4.4: fall back to EVERY configured root, not just the
            # first. The old fallback returned one path, so a
            # pre-database snapshot for a two-root project described one
            # root and looked complete.
            paths = list(self._settings_source_roots())
        return paths

    def _settings_target_path(self):
        """The project's FIRST source root.

        Kept because project helpers resolve a project through
        a single path. It is the first root, never "the" root -- callers
        that need the project's actual scope use
        _settings_source_roots().
        """
        roots = self._settings_source_roots()
        return roots[0] if roots else None

    def _settings_source_roots(self):
        r"""Every source root this project covers, in order.

        SourceRoots is authoritative. TargetPath is read only as a
        fallback for a project created before B4.3 wrote the list, so an
        older project keeps working rather than reporting that it has no
        roots at all.
        """
        if not self.project_dir:
            return []
        settings_path = self.project_dir / "settings.json"
        try:
            import json
            with open(settings_path, "r", encoding="utf-8-sig") as handle:
                settings = json.load(handle) or {}
        except Exception:
            return []
        roots = settings.get("SourceRoots")
        if isinstance(roots, list) and roots:
            return [str(r) for r in roots if str(r).strip()]
        legacy = settings.get("TargetPath")
        return [str(legacy)] if legacy else []

    def update_scanned_source_roots(self):
        r"""Re-record run_source_root now that coverage is known.

        B4.4. The roots are first associated when the run attaches to
        its database, which is BEFORE the inventory walks anything -- so
        that first write necessarily says was_scanned = 0. This is the
        second write, made once _scanned_root_keys holds the roots that
        actually produced observations, and record_run_source_roots
        UPSERTs so it corrects the earlier row instead of being ignored.

        Never raises: a bookkeeping update must not fail a completed
        scan. It does not swallow silently either -- a failure is logged
        and noted on the run.
        """
        if self.run_id is None:
            return False
        try:
            with self._db() as conn:
                if conn is None:
                    return False
                snapshot = self._environment or {}
                self._record_source_roots(conn, snapshot)
                return True
        except Exception as exc:                                # noqa: BLE001
            self.app_log.warning(
                "Could not update scanned source roots for run %s: %s"
                % (self.run_uid, exc))
            self.note("warning",
                      "Source-root coverage could not be updated: %s: %s"
                      % (type(exc).__name__, exc), category="inventory")
            return False

    def _record_source_roots(self, conn, snapshot):
        r"""Associate this run with the project's source roots.

        B4.3: was_scanned now reflects what the run ACTUALLY covered.
        The inventory walks every active root, so every active root is
        marked -- and a root the walk could not process is NOT marked,
        because the whole point of the flag is to record coverage rather
        than intent.
        """
        if self.run_id is None:
            return
        try:
            scanned = set(self._scanned_root_keys)
            drive_by_path = {item.get("path"): item
                             for item in snapshot.get("source_roots", [])}
            rows = []
            for root in fo_db.list_source_roots(conn):
                if not root["is_active"]:
                    continue
                info = drive_by_path.get(root["root_path"], {})
                rows.append({
                    "source_root_id": root["source_root_id"],
                    "root_path_at_run": root["root_path"],
                    # ACTUAL coverage, never intent. Before the walk
                    # this set is empty and every root records 0; the
                    # walk then calls this again and the UPSERT lifts
                    # the roots it really covered to 1. A root that was
                    # configured but missing keeps its 0.
                    "was_scanned": fo_db.path_key(root["root_path"]) in scanned,
                    "was_available": info.get("exists"),
                    "drive_type": info.get("drive_type"),
                    "filesystem": info.get("filesystem"),
                    "total_bytes": info.get("total_bytes"),
                    "free_bytes": info.get("free_bytes"),
                })
            if rows:
                fo_runs.record_run_source_roots(conn, self.run_id, rows)
        except Exception as exc:
            self.app_log.warning("Could not record source roots for run %s: %s"
                                 % (self.run_uid, exc))

    # -- stage plumbing (called by StageHandle) -----------------------

    def _log_stage_start(self, handle):
        if self.run_log:
            self.run_log.stage_start(handle.sequence, None, handle.stage_key,
                                     handle.label)

    def _db_create_stage(self, handle):
        with self._db() as conn:
            if conn is None or self.run_id is None:
                return None
            return fo_runs.create_stage(
                conn, self.run_id, handle.sequence, handle.stage_key,
                stage_label=handle.label, stage_role=handle.role,
                command=handle.command, status="running")

    def _close_stage(self, handle, status, exit_code, stdout_rel, stderr_rel,
                     note=None, skip_reason=None):
        # Stashed on the handle so attach_database() can replay a stage
        # that completed before any database existed -- which is exactly
        # what the project-creation stage of an initial scan is.
        handle.status = status
        handle.exit_code = exit_code
        handle.ended_utc = utc_now()
        handle.stdout_rel = stdout_rel
        handle.stderr_rel = stderr_rel
        handle.skip_reason = skip_reason
        handle.note = note

        with self._db() as conn:
            if conn is not None and handle.run_stage_id is not None:
                for item in handle._events:
                    item["run_id"] = self.run_id
                    item["run_stage_id"] = handle.run_stage_id
                fo_runs.add_events(conn, handle._events, run_id=self.run_id)
                fo_runs.finish_stage(
                    conn, handle.run_stage_id, status, exit_code=exit_code,
                    stdout_log_path=stdout_rel, stderr_log_path=stderr_rel,
                    skip_reason=skip_reason, notes=note)

        if self.run_log:
            self.run_log.stage_end(handle.sequence, None, handle.stage_key,
                                   handle.label, status, handle.duration_ms,
                                   exit_code=exit_code, note=note)
            # Warnings and errors are repeated into run.log, not just
            # left in the database, so the log alone answers "what went
            # wrong" without a SQLite client.
            for item in handle._events:
                if item["severity"] in ("warning", "error", "critical"):
                    prefix = "  %s" % (("[%s] " % item["file_path"])
                                       if item.get("file_path") else "")
                    self.run_log.log(item["severity"].upper(),
                                     prefix + item["message"],
                                     stage=handle.stage_key)

    # -- stdout/stderr capture ---------------------------------------

    def _capture_names(self, handle):
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", handle.stage_key)
        base = "%02d_%s" % (handle.sequence, safe)
        return base + ".out.log", base + ".err.log"

    def _write_stage_capture(self, handle, stdout, stderr):
        r"""Persist a stage's captured output under Logs\Stages\.

        Dashboard.py already captures both streams for its own error
        dialogs and then discards them. Writing them down is most of the
        value in R2 for nearly no cost: it is the difference between
        "PDFs failed" and knowing which file and which exception.

        Returns paths relative to the run folder, or None.
        """
        out_name, err_name = self._capture_names(handle)
        entries = []
        if stdout and stdout.strip():
            entries.append((out_name, stdout))
        if stderr and stderr.strip():
            entries.append((err_name, stderr))
        if not entries:
            return None, None

        if not self.run_folder_path:
            # Buffered until the run folder exists -- see bind_run_folder.
            self._pending_captures.extend(entries)
            return None, None

        written = {}
        for name, text in entries:
            written[name] = self._write_capture_file(name, text)
        return (("Logs/Stages/" + out_name) if written.get(out_name) else None,
                ("Logs/Stages/" + err_name) if written.get(err_name) else None)

    def _write_capture_file(self, name, text):
        try:
            target_dir = Path(self.run_folder_path) / "Logs" / "Stages"
            target_dir.mkdir(parents=True, exist_ok=True)
            with open(target_dir / name, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return True
        except Exception as exc:
            self.app_log.warning("Could not write stage capture %s: %s" % (name, exc))
            return False

    def _flush_pending_captures(self):
        if not self._pending_captures or not self.run_folder_path:
            return
        pending, self._pending_captures = self._pending_captures, []
        for name, text in pending:
            self._write_capture_file(name, text)

    # -- PowerShell event-file handoff --------------------------------

    def _activate_event_file(self, handle):
        r"""Point $env:FO_EVENT_FILE at a fresh NDJSON file for this
        stage.

        Child processes inherit the environment, so a PowerShell script
        that dot-sources FoEvents.ps1 starts emitting structured events
        with no new parameter and no database connection. A script that
        does not know about it is unaffected, and a script run by hand
        from a prompt -- where the variable is unset -- behaves exactly
        as it did in Alpha.

        Only one stage runs at a time in this application (Dashboard.py
        runs a single scan thread), so a process-wide environment
        variable is safe here.
        """
        try:
            if self.run_folder_path:
                events_dir = Path(self.run_folder_path) / "Logs" / "Events"
            else:
                events_dir = self.app_root / "Logs" / "PendingEvents" / str(self.run_uid)
            events_dir.mkdir(parents=True, exist_ok=True)
            out_name, _err = self._capture_names(handle)
            path = events_dir / (out_name.replace(".out.log", "") + ".ndjson")
            handle._prior_event_file = os.environ.get("FO_EVENT_FILE")
            os.environ["FO_EVENT_FILE"] = str(path)
            return path
        except Exception as exc:
            self.app_log.warning("Could not prepare the event file for %s: %s"
                                 % (handle.stage_key, exc))
            return None

    def _deactivate_event_file(self, handle):
        try:
            if handle._prior_event_file is None:
                os.environ.pop("FO_EVENT_FILE", None)
            else:
                os.environ["FO_EVENT_FILE"] = handle._prior_event_file
        except Exception:
            pass

    def _ingest_event_file(self, handle):
        """Read the NDJSON a PowerShell stage emitted and turn it into
        event rows.

        A malformed line is reported and skipped, not fatal: the point
        of a line-delimited format is that damage stays local. A
        truncated final line from a hard kill costs that line only.
        """
        events = []
        path = handle._event_file
        if not path:
            return events
        try:
            if not Path(path).exists():
                return events
            import json
            with open(path, "r", encoding="utf-8-sig") as stream:
                for number, line in enumerate(stream, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                    except Exception:
                        events.append({
                            "event_utc": utc_now(), "severity": "warning",
                            "category": "database", "source": "coordinator",
                            "stage_key": handle.stage_key,
                            "message": ("Unreadable event line %d from %s was skipped."
                                        % (number, handle.stage_key)),
                            "continued": True,
                            "detail": {"line": text[:400]},
                        })
                        continue
                    events.append({
                        "event_utc": payload.get("event_utc") or utc_now(),
                        "severity": payload.get("severity", "info"),
                        "category": payload.get("category", "stage"),
                        "source": payload.get("source", "powershell"),
                        "stage_key": payload.get("stage_key") or handle.stage_key,
                        "file_path": payload.get("file_path"),
                        "message": payload.get("message", ""),
                        "error_type": payload.get("error_type"),
                        "continued": payload.get("continued"),
                        "file_skipped": payload.get("file_skipped"),
                        "retryable": payload.get("retryable"),
                        "detail": payload.get("detail"),
                    })
        except Exception as exc:
            self.app_log.warning("Could not ingest events from %s: %s"
                                 % (handle.stage_key, exc))
        return events

    # -- inventory engine (Beta B2) -----------------------------------

    def run_inventory(self, settings_path):
        r"""Run the Python inventory engine for this run.

        Returns the same 4-tuple Dashboard.run_powershell returns --
        (success, stdout, stderr, returncode) -- so the dashboard's
        stage handling, its pause/timeout/failure branches and its
        captured-output logging all keep working unchanged. That shape
        is not laziness: every one of those branches is verified
        behaviour, and B2 is authorised to change how the inventory is
        produced, not how a stage is recorded.

        WHAT REPLACES WHAT. This method is the whole of B2's runtime
        change: no PowerShell process, no C# compiled at every scan, no
        CSV parsed back to reach the database. The walk yields records,
        the records are persisted in batches, and the CSV is rendered
        from what was persisted.

        WHICH PATH RUNS. When the project has a usable database the
        engine persists first and the CSV is exported FROM the
        database, which is the B2 flow and which makes any column that
        failed to survive the round trip visible on every run rather
        than only under an equivalence proof. A project with no usable
        database -- a legacy Alpha project, or one whose database
        creation was skipped -- streams the CSV straight from the
        records instead, because R6 can scan such a project today and
        losing that would be a regression. Both paths render through
        the same fo_exports function, and the B2 verifier proves they
        produce identical bytes.

        Never raises. A failure is returned as a non-zero return code
        with its reason on stderr, exactly as a failed subprocess was.
        """
        self._inventory_result = None
        try:
            return self._run_inventory(settings_path)
        except Exception as exc:                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.error("Inventory engine failed: %s" % reason)
            return False, "", "ERROR: the inventory scan failed. %s" % reason, 1

    def _run_inventory(self, settings_path):
        import json

        settings_path = Path(settings_path)
        if not settings_path.is_file():
            return False, "", ("ERROR: settings.json not found at:\n  %s"
                               % settings_path), 1
        try:
            with open(settings_path, "r", encoding="utf-8-sig") as handle:
                settings = json.load(handle) or {}
        except Exception as exc:
            return False, "", ("ERROR: Could not read/parse settings.json. %s"
                               % exc), 1

        configured = settings.get("SourceRoots")
        if not isinstance(configured, list) or not configured:
            legacy = settings.get("TargetPath")
            configured = [legacy] if legacy else []
        configured = [str(r) for r in configured if str(r).strip()]
        if not configured:
            return False, "", ("ERROR: this project has no source folders in "
                               "settings.json."), 1

        # A root that has gone missing is reported per root rather than
        # failing the whole scan: on a project spanning an internal disk
        # and an external drive, an unplugged drive should not discard
        # the inventory of the disk that is present.
        roots, missing = [], []
        for candidate in configured:
            if os.path.isdir(candidate):
                roots.append(win_meta.normalize_root(candidate))
            else:
                missing.append(candidate)
        if not roots:
            return False, "", ("ERROR: none of this project's source folders "
                               "exist:\\n  %s" % "\\n  ".join(missing)), 1

        project_dir = settings_path.parent
        root = roots[0]

        # Drive type is a calibration signal and is never allowed to
        # stop a scan -- R6's rule, preserved. B2.1 narrowed the answer
        # to Network-or-Unknown (B1 13.5); the guard stays because the
        # requirement is that classification cannot fail the inventory,
        # and a requirement that holds only because the callee happens
        # to be total is a requirement nobody can test.
        try:
            drive = win_meta.drive_type(root)
        except Exception as exc:                # noqa: BLE001
            self.app_log.warning("Drive-type detection failed: %s" % exc)
            drive = "Unknown"

        run_timestamp = fo_scan.run_timestamp_now()
        run_folder = Path(fo_scan.create_run_folder(project_dir, run_timestamp))

        # Bind BEFORE the walk. R6 could not: it mints the run folder
        # part-way through a protected script, so everything logged
        # before that point had to be buffered. Here the folder exists
        # first, so a directory error in the first second lands in the
        # run log where somebody will see it.
        self.bind_run_folder(run_timestamp)

        csv_path = run_folder / "Inventory" / "PreliminaryInventory.csv"
        errors_path = run_folder / "Logs" / "errors.txt"
        report_path = run_folder / "Reports" / "PreliminaryReport.txt"

        next_db_id = settings.get("NextDBID")
        try:
            next_db_id = int(next_db_id)
        except (TypeError, ValueError):
            next_db_id = 1
        if next_db_id < 1:
            next_db_id = 1

        statistics = fo_scan.ScanStatistics()
        timestamp_format = win_meta.detect_display_format()
        started = time.monotonic()

        # ONE run, ONE database, one inventory_scan per root. The scan
        # ids are what the hash and analyzer engines later bind to, and
        # they already bind to every scan of the newest run -- which is
        # what lets a duplicate group span two roots.
        persisted = self._scan_and_persist_roots(
            roots, next_db_id, statistics, timestamp_format, csv_path)
        # Coverage is knowable now and was not before, so correct the
        # run_source_root rows written at attach time.
        self.update_scanned_source_roots()

        # ONE combined export, from what every root persisted.
        exported = self._export_combined_inventory(csv_path)
        if not exported and persisted.get("status") in (
                "completed", "completed_with_warnings"):
            persisted.setdefault("warnings", []).append(
                "The combined inventory CSV could not be written.")
        if missing:
            persisted.setdefault("warnings", []).append(
                "%d source folder(s) could not be found and were not scanned: %s"
                % (len(missing), "; ".join(missing)))

        elapsed = time.monotonic() - started

        fo_scan.write_errors_file(str(errors_path), statistics.errors)

        report = fo_scan.build_report(
            project_name=settings.get("ProjectName"), target_path=root,
            run_timestamp=run_timestamp,
            generated=fo_scan.generated_stamp_now(), statistics=statistics,
            elapsed_seconds=elapsed, drive_type=drive,
            timestamp_format=timestamp_format)
        fo_scan.write_text_lines(str(report_path), report)

        fo_scan.update_settings(
            str(settings_path), run_timestamp,
            next_db_id + statistics.file_count, statistics, drive)

        self._inventory_result = {
            "run_folder": run_timestamp, "rows": statistics.file_count,
            "source_roots": roots, "missing_roots": missing,
            "inaccessible": len(statistics.errors),
            "timestamp_format": timestamp_format, "drive_type": drive,
            "elapsed_sec": elapsed, "csv_path": str(csv_path),
            "persistence": persisted,
        }

        stdout = fo_scan.console_summary(
            root if len(roots) == 1 else "%s (+%d more source folder(s))"
            % (root, len(roots) - 1), str(run_folder), statistics,
                                         elapsed)
        return True, stdout, "", 0

    def _scan_and_persist_roots(self, roots, next_db_id, statistics,
                                timestamp_format, csv_path):
        r"""Walk every root into one run, then export one combined CSV.

        DB_IDs continue across roots rather than restarting, so a
        project's inventory numbering stays unique project-wide -- two
        files with DB_ID 1 in one export would make every downstream
        join ambiguous.

        A root that fails is recorded as a warning and the remaining
        roots still run. The caller reports partial coverage honestly:
        a multi-root scan that lost a root does not get to claim it
        completed.
        """
        combined = {"status": None, "rows": 0, "inaccessible": 0,
                    "reason": None, "elapsed_sec": 0.0, "warnings": [],
                    "roots": []}
        statuses = []
        cursor = next_db_id
        for root in roots:
            try:
                # csv_path is NOT passed: no root exports. See
                # _export_combined_inventory below.
                outcome = self._scan_and_persist(
                    root, cursor, statistics, timestamp_format, root, None)
            except Exception as exc:                            # noqa: BLE001
                combined["warnings"].append(
                    "%s could not be scanned: %s: %s"
                    % (root, type(exc).__name__, exc))
                continue

            combined["roots"].append({"root": root,
                                      "rows": outcome.get("rows", 0),
                                      "status": outcome.get("status"),
                                      # B6.2 P4c -- carried so _scanned_root_keys
                                      # can exclude a root whose walk skipped a
                                      # directory (incomplete coverage).
                                      "directory_errors": outcome.get("directory_errors", 0)})
            combined["rows"] += outcome.get("rows", 0)
            combined["inaccessible"] += outcome.get("inaccessible", 0)
            combined["elapsed_sec"] += outcome.get("elapsed_sec", 0.0)
            combined["warnings"].extend(outcome.get("warnings", []))
            if outcome.get("reason") and not combined["reason"]:
                combined["reason"] = outcome["reason"]
            statuses.append(outcome.get("status"))
            cursor = statistics.file_count + next_db_id

        # B4.4: the aggregate is derived from the statuses that actually
        # came back. It used to start at "skipped" and take the worst of
        # itself and each root -- and "skipped" outranked "completed", so
        # a fully successful run could never climb out of the sentinel it
        # started in. That mislabelled ordinary SINGLE-root runs too.
        combined["status"] = self._aggregate_status(statuses)
        if not statuses:
            # Nothing was processed at all. Say why rather than defaulting.
            combined["reason"] = combined["reason"] or (
                "No source root could be processed.")
            combined["status"] = "failed" if combined["warnings"] else "skipped"

        # Only roots that genuinely produced observations count as
        # covered; that set is what run_source_root.was_scanned uses.
        #
        # B6.2 P4c -- a root whose walk hit a DIRECTORY it could not list
        # is NOT counted as covered: a subtree was skipped, so the
        # inventory is not a complete record of that root and a later
        # phase must not treat it as one. A stray unreadable FILE does not
        # demote coverage (that is normal); only a skipped directory does.
        self._scanned_root_keys = {
            fo_db.path_key(r["root"]) for r in combined["roots"]
            if r.get("status") in ("completed", "completed_with_warnings")
            and not r.get("directory_errors")}
        return combined

    @staticmethod
    def _aggregate_status(statuses):
        r"""One status for a run made of several per-root outcomes.

        The worst REAL outcome wins, so a failed root is never hidden by
        a successful one. Only statuses that actually occurred take
        part -- there is no initial value competing with them.
        """
        order = ("completed", "completed_with_warnings", "skipped", "failed")
        rank = {name: index for index, name in enumerate(order)}
        real = [s for s in statuses if s]
        if not real:
            return None
        return max(real, key=lambda s: rank.get(s, len(order)))

    def _export_combined_inventory(self, csv_path):
        r"""Render ONE PreliminaryInventory.csv covering every root.

        Rendered from SQLite after all roots are persisted, so the
        artifact describes the whole run rather than whichever root
        happened to go last -- and so no intermediate file is ever
        written. Never raises: a failed export must not discard a
        completed scan.
        """
        if csv_path is None:
            return False
        try:
            with self._db() as conn:
                if conn is None:
                    return False
                fo_exports.export_run_inventory(conn, self.run_id, csv_path)
                return True
        except Exception as exc:                                # noqa: BLE001
            self.app_log.error("Could not export the combined inventory: %s"
                               % exc)
            self.note("warning",
                      "The inventory was recorded but its CSV export failed: "
                      "%s: %s" % (type(exc).__name__, exc),
                      category="inventory")
            return False

    def _scan_and_persist(self, root, next_db_id, statistics, timestamp_format,
                          target_path, csv_path):
        r"""Walk once; persist to SQLite and produce the CSV export.

        Returns a dict describing what persistence did, which
        record_inventory_ingest() turns into its own run_stage. Keeping
        that as a separate stage is R3's decision and it still holds:
        the scan can succeed while the database write fails, and one
        status cannot honestly describe both.
        """
        outcome = {"status": "skipped", "rows": 0, "inaccessible": 0,
                   "reason": None, "elapsed_sec": 0.0, "warnings": []}

        if not self.project_dir:
            outcome["reason"] = "This run is not bound to a project folder."
            if csv_path is not None:
                self._scan_to_csv(root, next_db_id, statistics,
                                  timestamp_format, csv_path)
            return outcome

        started = time.monotonic()
        try:
            with self._db() as conn:
                if conn is None:
                    outcome["reason"] = \
                        "No project database is available for this project."
                    self._scan_to_csv(root, next_db_id, statistics,
                                      timestamp_format, csv_path)
                    return outcome

                ingestor = fo_inventory_records.RecordIngestor(
                    conn, self.run_id,
                    logger=lambda severity, message: self.note(
                        severity.lower(), message, category="inventory"))
                # Whether the root was actually reachable. Integrity requires
                # requires that a missing root not read as an empty
                # root -- if this is False, the ingestor records the
                # scan and marks NOTHING missing, because nothing was
                # observed to be missing, only unobserved.
                root_available = os.path.isdir(root)
                if not root_available:
                    self.note("warning",
                              "Source root is not reachable: %s. Its files "
                              "keep their last known state and are NOT "
                              "recorded as deleted." % root,
                              category="inventory")

                try:
                    records = fo_scan.scan(root, next_db_id, statistics)
                    # Errors are handed to ingest_records, which
                    # applies the recorded cap and writes both the SEEN
                    # and the STORED counts (B5-E.F044). B4.5 ingested
                    # them separately and had nowhere to put the fact
                    # that it had stopped storing.
                    summary = ingestor.ingest_records(
                        records, target_path, timestamp_format,
                        scan_errors=statistics.errors,
                        root_available=root_available,
                        path_events=statistics.path_events)
                    inaccessible = summary["inaccessible"]
                    status = ingestor.finish()
                except Exception as exc:
                    ingestor.fail("%s: %s" % (type(exc).__name__, exc))
                    raise

                outcome.update({
                    "status": status, "rows": summary["rows"],
                    "inaccessible": inaccessible,
                    # B6.2 P4c -- whole directories the walk could not list.
                    # Non-zero => this root's coverage is incomplete.
                    "directory_errors": summary.get("directory_errors", 0),
                    "warnings": list(ingestor.warnings),
                    "elapsed_sec": time.monotonic() - started,
                    # What the scan actually changed, rather than
                    # only how many rows it walked. On an unchanged
                    # corpus these read 0 / 0 / N, which is the visible
                    # form of the B5-E.F001 fix.
                    "new": summary.get("new", 0),
                    "changed": summary.get("changed", 0),
                    "unchanged": summary.get("unchanged", 0),
                    "vanished": summary.get("vanished", 0),
                    "root_availability": summary.get("root_availability"),
                })

                # B4.4: NO export here. This method persists one root;
                # the combined CSV is rendered once, after every root,
                # by _export_combined_inventory().
                #
                # It used to export unconditionally, and multi-root
                # passed csv_path=None for every root but the last --
                # which the CSV writer str()'d into a literal file
                # named "None" in the working directory. Suppressing an
                # export with a fake destination was the wrong shape;
                # the export simply does not belong per root.
                return outcome
        except Exception as exc:
            reason = "%s: %s" % (type(exc).__name__, exc)
            outcome.update({"status": "failed", "reason": reason,
                            "elapsed_sec": time.monotonic() - started})
            self.app_log.error("Inventory persistence failed: %s" % reason)
            # R6 produces its CSV whether or not the database is
            # willing, and an operator must not lose a multi-hour scan
            # to a database problem. The walk is repeated WITHOUT the
            # database so the run still yields its inventory, and the
            # failure is recorded rather than smoothed over.
            statistics.__init__()
            if csv_path is not None:
                self._scan_to_csv(root, next_db_id, statistics,
                                  timestamp_format, csv_path)
            return outcome

    @staticmethod
    def _scan_to_csv(root, next_db_id, statistics, timestamp_format, csv_path):
        """Render the CSV straight from the records, for the no-database path.

        Streams: one pass, bounded memory, and the SAME renderer the
        database export uses, so the two cannot drift apart.
        """
        rows = (fo_exports.inventory_cells_from_record(record, timestamp_format)
                for record in fo_scan.scan(root, next_db_id, statistics))
        fo_exports.write_inventory_csv(csv_path, rows)

    def record_inventory_ingest(self, stage_key="InventoryIngest",
                                label="Recording inventory in the database"):
        r"""Record the persistence outcome as its own run_stage.

        R3 made ingestion a separate stage so that a database failure
        stays distinguishable from a scan failure -- the inventory can
        be `completed` while InventoryIngest is `failed`, which is the
        literal truth in that case. B2 moves persistence INTO the walk
        for the obvious reason (there is no longer a second process to
        wait for), and this keeps the distinction the stage existed to
        express.

        Never raises, and never fails a run: a lost database
        convenience is not a lost scan.
        """
        result = getattr(self, "_inventory_result", None)
        if not result:
            return None
        outcome = result.get("persistence") or {}
        handle = self.stage(stage_key, label, role="inventory",
                            command="python: fo_inventory_records.ingest_records")
        self._non_fatal_stages.add(stage_key)
        try:
            status = outcome.get("status")
            if status == "failed":
                handle.add_event(
                    "error",
                    "Inventory persistence failed: %s The scan and its CSV are "
                    "unaffected." % (outcome.get("reason") or ""),
                    category="inventory", continued=True, retryable=True)
                handle.record(None, status="failed",
                              note="The scan and its CSV are unaffected.")
                return outcome
            if status == "skipped":
                handle.record_skipped(outcome.get("reason") or
                                      "No database was available.")
                return outcome

            message = ("Recorded %d inventory row(s) and %d inaccessible "
                       "path(s) in %.2fs." % (outcome.get("rows", 0),
                                              outcome.get("inaccessible", 0),
                                              outcome.get("elapsed_sec", 0.0)))
            handle.add_event("info", message, category="inventory")
            if self.run_log:
                self.run_log.info(message, stage=stage_key)
            handle.record(0, status=("completed_with_warnings"
                                     if outcome.get("warnings") else "completed"))
            return outcome
        except Exception as exc:
            self.app_log.error("Could not record the inventory ingest stage: %s"
                               % exc)
            try:
                handle.record_failure("Inventory ingest recording error: %s" % exc)
            except Exception:
                pass
            return outcome
        finally:
            self._deactivate_event_file(handle)

    # -- inventory ingestion (Beta R3) --------------------------------


    # -- hash & duplicate engine (Beta B3) -----------------------------

    def run_size_candidates(self, settings_path):
        r"""The Pre-Scan's size-grouping stage, in Python.

        Replaces PotentialDuplicates.ps1. Returns the same 4-tuple
        Dashboard.run_powershell returns -- (success, stdout, stderr,
        returncode) -- so every pause / timeout / failure branch in the
        dashboard keeps working unchanged. B3 is authorised to change
        how the work is done, not how a stage is recorded.

        Size grouping alone reads no file contents, so this stage
        cannot fail for an I/O reason and does not write hash rows. It
        exists as its own stage because the accepted workflow has it as
        its own stage, and because the Pre-Scan summary screen reads the
        three settings.json counters it writes.

        Never raises.
        """
        try:
            return self._run_hash_stage(settings_path, "candidates")
        except Exception as exc:                                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.error("Size-candidate stage failed: %s" % reason)
            return False, "", "ERROR: finding potential duplicates failed. %s" % reason, 1

    def run_duplicate_hash(self, settings_path):
        r"""The Duplicate Run: partial hash, escalation, confirmation.

        Replaces PartialHash.ps1 AND FullHash.ps1 with one in-process
        pass. Two PowerShell stages become one Python stage because the
        second only ever existed to consume the first's CSV; with the
        engine in-process there is no artifact to hand over and nothing
        for a second process to re-read.

        Never raises.
        """
        try:
            return self._run_hash_stage(settings_path, "selective")
        except Exception as exc:                                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.error("Hash engine failed: %s" % reason)
            return False, "", "ERROR: the duplicate hash pass failed. %s" % reason, 1

    def run_full_hash_inventory(self, settings_path):
        r"""The Full Run: a complete SHA-256 for every inventoried file.

        Replaces FullHashInventory.ps1. Never raises.
        """
        try:
            return self._run_hash_stage(settings_path, "exhaustive")
        except Exception as exc:                                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.error("Exhaustive hash engine failed: %s" % reason)
            return False, "", "ERROR: the full hash inventory failed. %s" % reason, 1

    # -- the shared driver ---------------------------------------------

    def _run_hash_stage(self, settings_path, mode):
        r"""Load from SQLite, run the engine, persist, then export.

        THE WHOLE OF B3's RUNTIME CHANGE IS THIS ORDER:

            database -> engine -> database -> exports

        Never engine -> CSV -> parse -> database. The engine's input is
        read from file_observation rows, not from
        PreliminaryInventory.csv, and its output is written to
        hash_measurement directly. The CSVs and reports are rendered
        afterwards, FROM what was persisted, which is what makes a
        column that failed to survive the round trip visible on every
        run rather than only under an equivalence proof.

        A project with no usable database falls back to the frozen
        PowerShell scripts rather than reintroducing a CSV channel --
        see the caller in Dashboard.py. R6 can hash such a project
        today and losing that would be a regression.
        """
        import json

        settings_path = Path(settings_path)
        if not settings_path.is_file():
            return False, "", "ERROR: settings.json not found at:\n  %s" % settings_path, 1
        try:
            with open(settings_path, "r", encoding="utf-8-sig") as handle:
                settings = json.load(handle) or {}
        except Exception as exc:                                # noqa: BLE001
            return False, "", "ERROR: Could not read/parse settings.json. %s" % exc, 1

        run_folder_name = settings.get("CurrentRun")
        if not run_folder_name:
            return False, "", ("ERROR: No CurrentRun found in settings.json.\n"
                               "Run the Pre-Scan first."), 1

        target_path = settings.get("TargetPath")
        if not target_path:
            return False, "", "ERROR: TargetPath in settings.json is missing.", 1

        project_dir = settings_path.parent
        run_folder = project_dir / "Runs" / run_folder_name
        inventory_dir = run_folder / "Inventory"
        reports_dir = run_folder / "Reports"
        logs_dir = run_folder / "Logs"
        for directory in (inventory_dir, reports_dir, logs_dir):
            directory.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        with self._db() as conn:
            if conn is None:
                return False, "", ("ERROR: no project database is available, so the "
                                   "Python hash engine cannot run. Set HASH_ENGINE = "
                                   "\"powershell\" in Dashboard.py to use the frozen "
                                   "PowerShell pipeline for this project."), 1

            ingestor = fo_hash_records.HashRecordIngestor(
                conn, self.run_id,
                partial_hash_bytes=fo_hash_engine.DEFAULT_PARTIAL_HASH_BYTES,
                logger=lambda severity, message: self.note(
                    severity.lower(), message, category="hashes"))
            scan_ids = ingestor.bind_scans(str(target_path))
            entries = fo_hash_records.load_entries(conn, scan_ids)
            if not entries:
                return False, "", ("ERROR: this run has no inventory to hash. Run the "
                                   "Pre-Scan first."), 1

            engine = fo_hash_engine.HashEngine(
                progress=lambda stage, done, total: self._hash_progress(
                    stage, done, total),
                logger=lambda severity, message: self.note(
                    severity.lower(), message, category="hashes"),
                # B6.2 P4b -- never open a cloud-only placeholder: reading
                # one triggers a download, and this is a read-only tool.
                # Matches the analyzer engine's default. Recorded as
                # hash_status 'skipped_cloud_only', no digest.
                skip_cloud_only=True)

            if mode == "candidates":
                outcome = self._run_candidates_only(engine, entries)
            elif mode == "exhaustive":
                outcome = engine.run_exhaustive(entries)
            else:
                outcome = engine.run_selective(entries)

            elapsed = time.monotonic() - started

            if mode != "candidates":
                if mode == "exhaustive":
                    ingestor.ingest_exhaustive_records(outcome)
                else:
                    ingestor.ingest_selective_records(outcome)
                ingestor.finish()
                fo_exports.Exporter(conn).export_hash_stage(str(inventory_dir))

            self._write_hash_reports(settings, run_folder_name, outcome, mode,
                                     entries, reports_dir, logs_dir, elapsed)

        self._update_hash_settings(settings_path, settings, outcome, mode)
        self._hash_result = {"mode": mode, "elapsed_sec": elapsed,
                             "summary": outcome.summary()}
        return True, self._hash_console_summary(outcome, mode, elapsed), "", 0

    def _run_candidates_only(self, engine, entries):
        """Size grouping with no hashing at all.

        Reuses the engine's own selector rather than repeating the
        stable-rank rule here, so the SizeGroupID this stage reports and
        the one the hash pass later uses cannot disagree.
        """
        outcome = fo_hash_engine.EngineOutcome("selective",
                                               engine.partial_hash_bytes)
        results = [fo_hash_engine.HashResult(e) for e in entries]
        by_key = {r.key: r for r in results}
        outcome.results = results
        candidates, group_count = fo_hash_engine.select_size_candidates(entries)
        for group_id, entry in candidates:
            by_key[entry.key].size_group_id = group_id
        outcome.size_group_count = group_count
        outcome.candidate_count = len(candidates)
        return outcome

    def _hash_progress(self, stage, done, total):
        """Forward engine progress to the run log.

        Deliberately coarse. The engine reports every 200 files in the
        partial pass and every 25 in the full pass, matching R6's own
        Write-Progress cadence closely enough that the GUI's progress
        behaviour is unchanged, without turning the run log into a
        per-file trace.
        """
        if self.run_log and total:
            self.run_log.info("%s: %d of %d" % (stage, done, total),
                              stage="HashEngine")

    def _write_hash_reports(self, settings, run_folder_name, outcome, mode,
                            entries, reports_dir, logs_dir, elapsed):
        """Render the legacy reports and per-file error logs."""
        project_name = settings.get("ProjectName")
        generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_bytes = sum(e.size for e in entries)

        if mode == "candidates":
            candidate_bytes = sum(r.size for r in outcome.results
                                  if r.size_group_id)
            fo_hash_reports.write_report(
                str(reports_dir / "PotentialDuplicatesReport.txt"),
                fo_hash_reports.potential_duplicates_report(
                    project_name, run_folder_name, generated, outcome,
                    total_bytes, candidate_bytes, elapsed))
            return

        meta_rows = self._hash_meta_rows(outcome)

        if mode == "exhaustive":
            fo_hash_reports.write_report(
                str(reports_dir / "FullHashInventoryReport.txt"),
                fo_hash_reports.full_hash_inventory_report(
                    project_name, run_folder_name, generated, outcome,
                    meta_rows, elapsed, len(entries), total_bytes))
            fo_hash_reports.write_error_log(
                str(logs_dir / "errors_fullhashinventory.txt"), outcome.errors,
                prefix="FULL HASH ERROR")
            return

        fo_hash_reports.write_report(
            str(reports_dir / "PartialHashReport.txt"),
            fo_hash_reports.partial_hash_report(
                project_name, run_folder_name, generated, outcome, elapsed))
        fo_hash_reports.write_report(
            str(reports_dir / "DuplicateHashInventoryReport.txt"),
            fo_hash_reports.duplicate_hash_inventory_report(
                project_name, run_folder_name, generated, outcome, meta_rows,
                elapsed, len(entries), total_bytes))
        partial_errors = [e for e in outcome.errors]
        fo_hash_reports.write_error_log(
            str(logs_dir / "errors_partialhash.txt"), partial_errors)

    def _hash_meta_rows(self, outcome):
        r"""Per-file metadata the inventory-shaped reports need.

        Read back from file_observation rather than carried through the
        engine. The engine is about digests; teaching it to carry
        attribute words and path lengths so a report could print them
        would put presentation concerns inside the algorithm.
        """
        keys = [r.key for r in outcome.results]
        metadata = {}
        with self._db() as conn:
            if conn is None:
                return []
            for start in range(0, len(keys), 900):
                chunk = keys[start:start + 900]
                placeholders = ",".join("?" * len(chunk))
                for row in conn.execute(
                        "SELECT o.file_observation_id, o.size_bytes, o.attributes,"
                        "       o.path_length, fp.depth, fp.file_name "
                        "FROM file_observation o "
                        "JOIN file_path fp ON fp.file_path_id = o.file_path_id "
                        "WHERE o.file_observation_id IN (%s)" % placeholders,
                        chunk):
                    metadata[row["file_observation_id"]] = row

        rows = []
        for result in outcome.results:
            row = metadata.get(result.key)
            if row is None:
                continue
            rows.append({
                "size": row["size_bytes"] or 0,
                "depth": row["depth"] or 0,
                "attributes": row["attributes"] or "",
                "path_length": row["path_length"] or 0,
                "extension": win_meta.dotnet_extension(row["file_name"]),
                "final_status": result.final_status,
            })
        return rows

    def _update_hash_settings(self, settings_path, settings, outcome, mode):
        """Write back the settings.json fields the dashboard reads.

        Same fields, same names, same meanings the PowerShell scripts
        wrote. The Pre-Scan summary screen reads three of them, and a
        stage that produced correct results but left the screen blank
        would be a regression the database would not show.
        """
        import json

        now = datetime.now().astimezone().isoformat()
        if mode == "candidates":
            settings["LastPotentialDuplicatesScan"] = now
            settings["LastPotentialDuplicatesCandidateCount"] = outcome.candidate_count
            settings["LastPotentialDuplicatesGroupCount"] = outcome.size_group_count
            by_group = {}
            for result in outcome.results:
                if result.size_group_id:
                    by_group.setdefault(result.size_group_id, []).append(result)
            settings["LastPotentialDuplicatesMaxReclaim"] = sum(
                members[0].size * (len(members) - 1)
                for members in by_group.values())
        elif mode == "exhaustive":
            settings["LastFullHashInventoryScan"] = now
        else:
            settings["LastPartialHashScan"] = now
            settings["LastFullHashScan"] = now

        try:
            with open(settings_path, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2)
        except Exception as exc:                                # noqa: BLE001
            self.app_log.warning("Could not update settings.json: %s" % exc)

    @staticmethod
    def _hash_console_summary(outcome, mode, elapsed):
        """Console output shaped like the script it replaces."""
        lines = [""]
        if mode == "candidates":
            lines.append("Potential duplicates scan complete.")
            lines.append("  Size-groups found  : %d" % outcome.size_group_count)
            lines.append("  Files involved     : %d of %d"
                         % (outcome.candidate_count, len(outcome.results)))
        elif mode == "exhaustive":
            lines.append("Full Hash Inventory complete.")
            lines.append("  Files hashed               : %d" % outcome.full_hashed)
            lines.append("  Confirmed duplicate groups : %d" % outcome.confirmed_group_count)
            lines.append("  Redundant files            : %d" % outcome.redundant_file_count)
        else:
            lines.append("Duplicate detection complete.")
            lines.append("  Partial hashes             : %d" % outcome.partial_hashed)
            lines.append("  Full hashes                : %d" % outcome.full_hashed)
            lines.append("  Confirmed duplicate groups : %d" % outcome.confirmed_group_count)
            lines.append("  Redundant files            : %d" % outcome.redundant_file_count)
        if outcome.error_count:
            lines.append("  Errors                     : %d" % outcome.error_count)
        lines.append("  Duration                   : %s"
                     % fo_hash_reports.format_duration(elapsed))
        lines.append("")
        return "\n".join(lines)


    # -- project creation (Phase 1 RC) ----------------------------------

    def create_project(self, projects_dir, project_name, target_path):
        r"""Create a project in-process.

        Replaces New-Project.ps1, which created the folder and
        settings.json and then shelled out to python.exe to run
        fo_db -- Python calling PowerShell calling Python, for work
        with no PowerShell-specific content.

        Returns the (success, stdout, stderr, returncode) 4-tuple the
        dashboard's stage recorder expects, so the stage row, its
        status and its logging are unchanged.

        Never raises.
        """
        try:
            result = fo_project.create_project(
                projects_dir, project_name, target_path,
                app_version=getattr(self, "app_version", None))
        except fo_project.ProjectError as exc:
            return False, "", "ERROR: %s" % exc, 1
        except Exception as exc:                                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.error("Project creation failed: %s" % reason)
            return False, "", "ERROR: Could not create the project. %s" % reason, 1

        lines = ["", "Project created.",
                 "  Name           : %s" % result["project_name"],
                 "  Folder         : %s" % result["project_folder"],
                 "  Source root    : %s" % result["target_path"],
                 "  Schema version : %s" % result["schema_version"], ""]
        self._created_project = result
        return True, "\n".join(lines), "", 0

    # -- time estimates (Phase 1 RC) ------------------------------------

    def run_time_estimates(self, settings_path):
        r"""Calibrate the Duplicate Run / Full Run estimates in-process.

        Replaces TimeEstimates.ps1. Deliberately optional: if anything
        here fails the caller records a failed stage inside a run that
        still succeeded, because an unavailable estimate is a missing
        convenience, not a broken pre-scan.

        Never raises.
        """
        import json

        try:
            settings_path = Path(settings_path)
            with open(settings_path, "r", encoding="utf-8-sig") as handle:
                settings = json.load(handle) or {}
        except Exception as exc:                                # noqa: BLE001
            return False, "", "ERROR: Could not read settings.json. %s" % exc, 1

        try:
            with self._db() as conn:
                if conn is None:
                    return False, "", ("ERROR: no project database is "
                                       "available for calibration."), 1
                scan_ids = self._analyzer_scan_ids(conn)
                values = fo_estimates.calibrate(
                    conn, scan_ids, drive_type=settings.get("TargetDriveType"))
        except Exception as exc:                                # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            self.app_log.warning("Time estimate calibration failed: %s" % reason)
            return False, "", "ERROR: calibration failed. %s" % reason, 1

        for field in fo_estimates.SETTINGS_FIELDS:
            settings[field] = values[field]
        try:
            with open(settings_path, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2)
        except Exception as exc:                                # noqa: BLE001
            self.app_log.warning("Could not update settings.json: %s" % exc)

        return True, fo_estimates.console_summary(values), "", 0

    # -- analyzer runtime (Beta B4) -------------------------------------

    def run_analyzers(self, settings_path, analyzer_keys, stage_handles=None,
                      skip_cloud_only=True):
        r"""Run the selected analyzers in-process and persist the results.

        Replaces nine PowerShell wrappers that did nothing but launch
        the analyzers. Returns (success, stdout, stderr, returncode) --
        the same 4-tuple Dashboard.run_powershell returns -- so the
        dashboard's existing failure and pause branches keep working.
        B4 changes how the work is invoked, not how a stage is recorded.

        THE ORDER IS THE POINT:

            database -> analyzers -> database -> exports

        never analyzer -> CSV -> parse -> database. The file list comes
        from file_observation, results are persisted as objects, and
        the CSVs are rendered afterwards FROM what was stored.

        `stage_handles` maps analyzer key -> run_stage_id so each
        analyzer_run still hangs off the stage the dashboard opened for
        it, preserving R2's stage identity.

        Never raises.
        """
        import json

        try:
            settings_path = Path(settings_path)
            with open(settings_path, "r", encoding="utf-8-sig") as handle:
                settings = json.load(handle) or {}
        except Exception as exc:                                # noqa: BLE001
            return False, "", "ERROR: Could not read settings.json. %s" % exc, 1

        run_folder_name = settings.get("CurrentRun")
        target_path = settings.get("TargetPath")
        if not run_folder_name or not target_path:
            return False, "", ("ERROR: settings.json has no CurrentRun or "
                               "TargetPath. Run the Pre-Scan first."), 1

        project_dir = settings_path.parent
        run_folder = project_dir / "Runs" / run_folder_name
        inventory_dir = run_folder / "Inventory"
        reports_dir = run_folder / "Reports"
        for directory in (inventory_dir, reports_dir):
            directory.mkdir(parents=True, exist_ok=True)

        started = time.monotonic()
        outcomes = []
        with self._db() as conn:
            if conn is None:
                return False, "", (
                    "ERROR: no project database is available, so the Python "
                    "analyzer runtime cannot run. Set ANALYZER_ENGINE = "
                    "\"powershell\" in Dashboard.py to use the frozen "
                    "PowerShell wrappers for this project."), 1

            ingestor = fo_analyzer_records.AnalyzerRecordIngestor(
                conn, self.run_id,
                logger=lambda severity, message: self.note(
                    severity.lower(), message, category="analyzers"))
            ingestor.bind_scans(str(target_path))

            scan_ids = self._analyzer_scan_ids(conn)
            entries = fo_analyzer_records.load_entries(conn, scan_ids)
            if not entries:
                return False, "", ("ERROR: this run has no inventory to "
                                   "analyse. Run the Pre-Scan first."), 1

            engine = fo_analyzer_engine.AnalyzerEngine(
                progress=lambda key, done, total: self._analyzer_progress(
                    key, done, total),
                logger=lambda severity, message: self.note(
                    severity.lower(), message, category="analyzers"),
                skip_cloud_only=skip_cloud_only)

            context = {"extract_folder": str(inventory_dir / "ExtractedText")}

            # Results are persisted INCREMENTALLY. This prevents
            # B5-E.F007.
            #
            # B4.5 held every result of every analyzer until the whole
            # pass finished, so peak memory was the sum of nine
            # analyzers' complete output over the project. The sink
            # below takes each result as it is produced; the engine
            # then stops retaining them, and memory is bounded by one
            # batch instead of by the corpus.
            #
            # The outcomes still carry every COUNT the summary and the
            # settings update need -- AnalyzerOutcome accumulates those
            # as results pass through, rather than deriving them from a
            # list it was keeping anyway.
            sink = ingestor.result_sink(stage_ids=stage_handles or {})
            outcomes = engine.run_all(entries, context=context,
                                      only=set(analyzer_keys), sink=sink)
            for outcome in outcomes:
                if outcome.key == "content_extraction":
                    outcome.extract_folder = context["extract_folder"]

            ingestor.finalize_outcomes(outcomes, stage_ids=stage_handles or {})
            fo_exports.Exporter(conn).export_analyzer_stage(str(inventory_dir), analyzer_keys)

        elapsed = time.monotonic() - started
        self._analyzer_result = {"elapsed_sec": elapsed,
                                 "outcomes": [o.summary() for o in outcomes]}
        self._update_analyzer_settings(settings_path, settings, outcomes)

        failed = [o for o in outcomes
                  if o.status == fo_analyzer_engine.STATUS_FAILED]
        summary = self._analyzer_console_summary(outcomes, elapsed)
        if failed:
            # Analyzers are independent, so a failure is reported as a
            # warning on stderr with the run still successful overall.
            # The per-analyzer stage row and analyzer_run carry which
            # one failed and why; collapsing that into one failed run
            # would lose the eight that worked.
            reasons = "; ".join("%s: %s" % (o.label, o.failure_reason)
                                for o in failed)
            return True, summary, "WARNING: %s" % reasons, 0
        return True, summary, "", 0

    def _analyzer_scan_ids(self, conn):
        """Inventory scans whose observations these analyzers cover."""
        rows = conn.execute(
            "SELECT s.inventory_scan_id FROM inventory_scan s "
            "JOIN run r ON r.run_id = s.run_id "
            "WHERE r.run_folder = ? ORDER BY s.inventory_scan_id",
            (self.run_folder_name,)).fetchall()
        if rows:
            return [r["inventory_scan_id"] for r in rows]
        # A content-analysis run reuses the pre-scan's run folder; if the
        # lookup by folder finds nothing, fall back to the project's
        # completed scans rather than analysing nothing at all.
        return [r["inventory_scan_id"] for r in conn.execute(
            "SELECT inventory_scan_id FROM inventory_scan "
            "WHERE status = 'completed' ORDER BY inventory_scan_id")]

    def _analyzer_progress(self, key, done, total):
        """Forward analyzer progress to the run log, coarsely."""
        if self.run_log and total:
            self.run_log.info("%s: %d of %d" % (key, done, total),
                              stage="AnalyzerEngine")

    def _update_analyzer_settings(self, settings_path, settings, outcomes):
        r"""Write back the Last*Scan fields the wrappers stamped.

        Only for analyzers that actually ran. A failed analyzer must not
        leave a timestamp claiming it succeeded -- the dashboard reads
        these to tell the user when a category was last done.
        """
        import json

        now = datetime.now().astimezone().isoformat()
        changed = False
        for outcome in outcomes:
            if outcome.status == fo_analyzer_engine.STATUS_FAILED:
                continue
            field = fo_analyzer_engine.SETTINGS_FIELD.get(outcome.key)
            if field:
                settings[field] = now
                changed = True
        if not changed:
            return
        try:
            with open(settings_path, "w", encoding="utf-8") as handle:
                json.dump(settings, handle, indent=2)
        except Exception as exc:                                # noqa: BLE001
            self.app_log.warning("Could not update settings.json: %s" % exc)

    @staticmethod
    def _analyzer_console_summary(outcomes, elapsed):
        """Console output shaped like the wrappers it replaces."""
        lines = [""]
        for outcome in outcomes:
            if outcome.status == fo_analyzer_engine.STATUS_NO_APPLICABLE:
                lines.append("  %-28s no applicable files" % outcome.label)
            elif outcome.status == fo_analyzer_engine.STATUS_FAILED:
                lines.append("  %-28s FAILED -- %s"
                             % (outcome.label, outcome.failure_reason))
            else:
                lines.append("  %-28s %d succeeded, %d failed, %d skipped"
                             % (outcome.label, outcome.succeeded_count,
                                outcome.error_count, outcome.skipped_count))
        lines.append("")
        lines.append("  Duration : %.1fs" % elapsed)
        lines.append("")
        return "\n".join(lines)

    def ingest_errors_txt(self, stage_handle=None):
        r"""Turn PreliminaryInventory.ps1's Logs\errors.txt into events.

        That script is protected and writes its inaccessible-path list
        as plain text. Reading it here gives per-path structured records
        -- with the file path, the severity, and the fact that
        processing continued -- without touching the script.

        The text file is left exactly as it is. It remains the Alpha
        artefact it always was.
        """
        if not self.run_folder_path:
            return 0
        errors_path = Path(self.run_folder_path) / "Logs" / "errors.txt"
        if not errors_path.exists():
            return 0

        events = []
        try:
            with open(errors_path, "r", encoding="utf-8-sig") as stream:
                for line in stream:
                    text = line.strip()
                    if not text:
                        continue
                    match = _ERRORS_TXT_LINE.match(text)
                    if match:
                        events.append({
                            "severity": "warning", "category": "file",
                            "source": "errors_txt",
                            "stage_key": "PreliminaryInventory.ps1",
                            "file_path": match.group("path"),
                            "message": match.group("message"),
                            "error_type": match.group("kind"),
                            # The inventory records the failure and keeps
                            # walking; the item itself is not inventoried.
                            "continued": True, "file_skipped": True,
                            "retryable": True,
                        })
                    else:
                        events.append({
                            "severity": "warning", "category": "file",
                            "source": "errors_txt",
                            "stage_key": "PreliminaryInventory.ps1",
                            "message": text, "continued": True,
                        })
                    if len(events) >= MAX_DERIVED_EVENTS_PER_STREAM:
                        events.append({
                            "severity": "info", "category": "file",
                            "source": "errors_txt",
                            "stage_key": "PreliminaryInventory.ps1",
                            "message": ("Only the first %d entries from errors.txt were "
                                        "recorded as events; the full list remains in "
                                        "Logs\\errors.txt."
                                        % MAX_DERIVED_EVENTS_PER_STREAM),
                            "continued": True,
                        })
                        break
        except Exception as exc:
            self.app_log.warning("Could not read errors.txt: %s" % exc)
            return 0

        if not events:
            return 0

        stage_id = stage_handle.run_stage_id if stage_handle else None
        for item in events:
            item["run_id"] = self.run_id
            item["run_stage_id"] = stage_id
            item.setdefault("event_utc", utc_now())
        with self._db() as conn:
            if conn is not None and self.run_id is not None:
                fo_runs.add_events(conn, events, run_id=self.run_id)
        if self.run_log:
            self.run_log.warning(
                "%d inaccessible path(s) reported by the inventory were recorded "
                "as events (see Logs\\errors.txt for the full list)." % len(events))
        return len(events)


def role_for(stage_key):
    return STAGE_ROLES.get(stage_key, "analyzer")


# ---------------------------------------------------------------------------
# Environment snapshot cache
# ---------------------------------------------------------------------------

_ENVIRONMENT_CACHE = {}
_ENVIRONMENT_LOCK = threading.Lock()


def get_environment(app_version, source_roots=None):
    """Collect the environment once per (app version, root set) per
    process and reuse it.

    Collection costs a PowerShell subprocess. Paying that on every run
    in a session would be a visible delay in exchange for an answer that
    cannot have changed.
    """
    key = (app_version, tuple(sorted(source_roots or [])))
    with _ENVIRONMENT_LOCK:
        cached = _ENVIRONMENT_CACHE.get(key)
        if cached is not None:
            return cached
    snapshot = fo_env.collect_environment(
        app_version, fo_db_module_version=fo_db.MODULE_VERSION,
        source_roots=source_roots)
    with _ENVIRONMENT_LOCK:
        _ENVIRONMENT_CACHE[key] = snapshot
    return snapshot


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------

def reconcile_all_projects(app_root, app_log=None, app_version=APP_VERSION):
    r"""Find runs left marked 'running' by a session that never came
    back, and record them as interrupted.

    Called at application start. Each project database is opened on its
    own, one at a time, through fo_db.open_project -- so the isolation
    guard applies to every one of them and no project's rows are ever
    visible while another's database is open. A project whose database
    is missing, refused or newer than this build is skipped with a log
    line, not an error.

    Opening each database here also applies the 001 -> 002 migration to
    existing projects on the first R2 launch.

    This RECORDS interruptions. It does not resume anything.
    """
    app_root = Path(app_root)
    log = app_log or fo_log.get_app_log(str(app_root))
    projects_dir = app_root / "Projects"
    results = {"projects_checked": 0, "projects_skipped": 0, "runs_reconciled": []}

    if not projects_dir.is_dir():
        return results

    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        if not (project_dir / "project.json").exists():
            # A legacy Alpha project with no database. Nothing to
            # reconcile, and nothing wrong with that.
            continue
        conn = None
        try:
            conn, _project = fo_db.open_project(str(project_dir), app_version=app_version)
            fo_runs.ensure_schema(conn)
            reconciled = fo_runs.reconcile_stale_runs(conn)
            conn.commit()
            results["projects_checked"] += 1
            for item in reconciled:
                item["project"] = project_dir.name
                results["runs_reconciled"].append(item)
                log.warning(
                    "Project '%s': run %s (%s) was still marked running from an "
                    "earlier session and has been recorded as interrupted -- %s"
                    % (project_dir.name, item["run_uid"], item["run_kind"],
                       item["reason"]))
                _append_interruption_to_run_log(project_dir, item, app_version, log)
        except Exception as exc:
            results["projects_skipped"] += 1
            log.warning("Skipped stale-run check for project '%s': %s: %s"
                        % (project_dir.name, type(exc).__name__, exc))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    if results["runs_reconciled"]:
        log.info("Stale-run reconciliation recorded %d interrupted run(s)."
                 % len(results["runs_reconciled"]))
    return results


def _append_interruption_to_run_log(project_dir, item, app_version, app_log):
    """Close the interrupted session's block in its own run.log.

    Without this, that run's log would end mid-timeline with no
    explanation -- which is the single most confusing thing a log can
    do to whoever reads it next.
    """
    if not item.get("run_folder"):
        return
    try:
        run_folder = Path(project_dir) / "Runs" / item["run_folder"]
        if not run_folder.is_dir():
            return
        log = fo_log.RunLog(item["run_uid"], item["run_kind"], app_version)
        log.bind(str(run_folder))
        log.warning("This run did not close normally. Detected on a later launch: %s"
                    % item["reason"])
        log.summary("interrupted", None,
                    [{"stage_key": key, "label": key, "status": "interrupted",
                      "duration_ms": None} for key in item.get("stages", [])],
                    0, 0,
                    extra_lines=["Recorded by stale-run reconciliation.",
                                 "No work was resumed; R2 records interruptions only."])
    except Exception as exc:
        app_log.warning("Could not append the interruption notice to a run log: %s" % exc)


# ---------------------------------------------------------------------------
# Diagnostic CLI
# ---------------------------------------------------------------------------

def _main(argv=None):
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="RunCoordinator.py",
        description="The File Organizer -- run/stage record inspection (R2).")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("reconcile", help="Record abandoned runs as interrupted")
    p.add_argument("--app-root", required=True)

    p = sub.add_parser("runs", help="List recorded runs for one project")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--limit", type=int, default=20)

    p = sub.add_parser("show", help="Show one run in full")
    p.add_argument("--project-dir", required=True)
    p.add_argument("--run-uid", required=True)

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    if args.command == "reconcile":
        results = reconcile_all_projects(args.app_root)
        print(json.dumps(results, indent=2, default=str))
        return 0

    conn, _project = fo_db.open_project(args.project_dir)
    try:
        if args.command == "runs":
            print(json.dumps(fo_runs.project_run_summary(conn, limit=args.limit),
                             indent=2, default=str))
            return 0
        row = conn.execute("SELECT run_id FROM run WHERE run_uid = ?",
                           (args.run_uid,)).fetchone()
        if row is None:
            print("No run with UID %s in this project." % args.run_uid, file=sys.stderr)
            return 1
        print(json.dumps(fo_runs.run_report(conn, row["run_id"]), indent=2, default=str))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(_main())
