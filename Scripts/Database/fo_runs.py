#!/usr/bin/env python3
r"""
fo_runs.py
===================================================================
PRODUCTION CODE
The File Organizer -- Version Beta, R2 (Logging + Run/Stage Persistence)
Module version: 1.0.0   Requires schema version: 2
===================================================================

The database half of R2: reading and writing run, run_stage,
run_source_root, environment_snapshot and event rows.

Every function here takes an already-open connection obtained from
fo_db.open_project(). That is deliberate -- fo_db remains the only
module that decides how a database is opened, which pragmas apply, and
whether this folder's database is allowed to be opened at all. This
module never calls sqlite3.connect() itself and never looks at a path.

SCOPE
R2 RECORDS what happened. It does not resume, and it does not ingest
inventory, file, content, duplicate or analyzer data -- there are no
tables for any of that yet, by design.

RECONCILIATION, NOT RESURRECTION
reconcile_stale_runs() answers one question: "was this run's process
still alive?" A run marked 'running' whose process is gone becomes
'interrupted', and its running stage with it. That is the whole
feature. Deciding what work remains, and redoing it, is a later
revision.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

REQUIRED_SCHEMA_VERSION = 2

#: A run whose heartbeat is older than this and whose process cannot be
#: confirmed alive is treated as interrupted. The coordinator beats
#: every 30 seconds, so this is six missed beats -- generous enough to
#: survive a machine that was briefly suspended, short enough that the
#: next launch tells the truth.
STALE_HEARTBEAT_SECONDS = 180

RUN_STATUSES = (
    "created", "running", "completed", "completed_with_warnings",
    "failed", "paused", "interrupted", "cancelled",
)

STAGE_STATUSES = RUN_STATUSES + ("skipped", "no_applicable_files")

#: Stage outcomes that mean "this stage ran and did not go wrong".
STAGE_OK_STATUSES = ("completed", "no_applicable_files", "skipped", "paused")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_utc(text):
    """Parse a timestamp this application wrote. Returns None on anything
    unexpected rather than raising -- a corrupt timestamp must not be
    able to stop a reconciliation pass."""
    if not text:
        return None
    try:
        cleaned = str(text).strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def new_uid():
    return str(uuid.uuid4())


def _json_or_none(value):
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"unserializable": str(type(value))})


def _bool_or_none(value):
    return None if value is None else (1 if value else 0)


def ensure_schema(conn):
    """Confirm this connection's database actually has the R2 tables.

    fo_db.open_project() migrates forward on open, so this should always
    pass. It is checked anyway because every write below assumes it, and
    a clear message beats an OperationalError from four frames deeper.
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version < REQUIRED_SCHEMA_VERSION:
        raise RuntimeError(
            "This project's database is at schema version %d; run/stage "
            "persistence requires version %d." % (version, REQUIRED_SCHEMA_VERSION))
    return version


# ---------------------------------------------------------------------------
# environment_snapshot
# ---------------------------------------------------------------------------

def upsert_environment_snapshot(conn, snapshot, snapshot_hash):
    """Return the id for this snapshot, inserting it only if this exact
    machine state has not been seen before."""
    existing = conn.execute(
        "SELECT environment_snapshot_id FROM environment_snapshot WHERE snapshot_hash = ?",
        (snapshot_hash,)).fetchone()
    if existing:
        return existing["environment_snapshot_id"]

    cur = conn.execute(
        "INSERT INTO environment_snapshot ("
        "  project_id, snapshot_hash, captured_utc, os_caption, os_version, os_build,"
        "  powershell_version, powershell_edition, python_version, sqlite_version,"
        "  app_version, fo_db_module_version, long_paths_enabled, last_access_update,"
        "  details_json"
        ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (snapshot_hash, utc_now(),
         snapshot.get("os_caption"), snapshot.get("os_version"), snapshot.get("os_build"),
         snapshot.get("powershell_version"), snapshot.get("powershell_edition"),
         snapshot.get("python_version"), snapshot.get("sqlite_version"),
         snapshot.get("app_version"), snapshot.get("fo_db_module_version"),
         snapshot.get("long_paths_enabled"), snapshot.get("last_access_update"),
         _json_or_none(snapshot) or "{}"))
    return cur.lastrowid


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def create_run(conn, run_uid, run_kind, app_version, schema_version,
               run_label=None, run_folder=None, host_pid=None,
               process_started_utc=None, environment_snapshot_id=None,
               status="running", started_utc=None):
    started = started_utc or utc_now()
    cur = conn.execute(
        "INSERT INTO run ("
        "  project_id, run_uid, run_folder, run_kind, run_label, status,"
        "  started_utc, heartbeat_utc, host_pid, process_started_utc,"
        "  environment_snapshot_id, app_version, schema_version"
        ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_uid, run_folder, run_kind, run_label, status, started, started,
         host_pid, process_started_utc, environment_snapshot_id,
         app_version, schema_version))
    return cur.lastrowid


def set_run_folder(conn, run_id, run_folder):
    r"""Bind a run to the Runs\<folder> it turned out to write into.

    An initial scan's run record exists before PreliminaryInventory.ps1
    has minted the folder, because the run has to be recorded before the
    thing that might fail is attempted. This closes that gap once the
    folder name is known.
    """
    conn.execute("UPDATE run SET run_folder = ? WHERE run_id = ?", (run_folder, run_id))


def set_run_environment(conn, run_id, environment_snapshot_id):
    conn.execute("UPDATE run SET environment_snapshot_id = ? WHERE run_id = ?",
                 (environment_snapshot_id, run_id))


def heartbeat(conn, run_id, when=None):
    """Prove the process behind this run is still alive.

    A stage can legitimately run for hours, so stage boundaries are far
    too coarse to be the liveness signal. The coordinator ticks this on
    a timer instead; the tick stopping is precisely what a hard
    interruption looks like from the outside.
    """
    conn.execute("UPDATE run SET heartbeat_utc = ? WHERE run_id = ?",
                 (when or utc_now(), run_id))


def finish_run(conn, run_id, status, ended_utc=None, notes=None):
    if status not in RUN_STATUSES:
        raise ValueError("Unknown run status: %r" % (status,))
    ended = ended_utc or utc_now()
    row = conn.execute("SELECT started_utc FROM run WHERE run_id = ?", (run_id,)).fetchone()
    duration_ms = None
    started = parse_utc(row["started_utc"]) if row else None
    finished = parse_utc(ended)
    if started and finished:
        duration_ms = int((finished - started).total_seconds() * 1000)

    counts = conn.execute(
        "SELECT"
        "  (SELECT COUNT(*) FROM run_stage WHERE run_id = ?) AS stages,"
        "  (SELECT COUNT(*) FROM event WHERE run_id = ? AND severity = 'warning') AS warns,"
        "  (SELECT COUNT(*) FROM event WHERE run_id = ? AND severity IN ('error','critical')) AS errs",
        (run_id, run_id, run_id)).fetchone()

    conn.execute(
        "UPDATE run SET status = ?, ended_utc = ?, duration_ms = ?, heartbeat_utc = ?,"
        "  stage_count = ?, warning_count = ?, error_count = ?,"
        "  notes = COALESCE(?, notes) WHERE run_id = ?",
        (status, ended, duration_ms, ended,
         counts["stages"], counts["warns"], counts["errs"], notes, run_id))
    return {"status": status, "duration_ms": duration_ms,
            "stage_count": counts["stages"],
            "warning_count": counts["warns"], "error_count": counts["errs"]}


def get_run(conn, run_id):
    return conn.execute("SELECT * FROM run WHERE run_id = ?", (run_id,)).fetchone()


def list_runs(conn, limit=50):
    return conn.execute(
        "SELECT * FROM run ORDER BY started_utc DESC, run_id DESC LIMIT ?",
        (limit,)).fetchall()


# ---------------------------------------------------------------------------
# run_source_root
# ---------------------------------------------------------------------------

def record_run_source_roots(conn, run_id, roots):
    r"""Record, or CORRECT, this run's association with its source roots.

    roots: iterable of dicts with source_root_id, root_path_at_run,
    was_scanned, was_available, drive_type, filesystem, total_bytes,
    free_bytes.

    B4.4: an UPSERT on the existing UNIQUE(run_id, source_root_id),
    not INSERT OR IGNORE.

    A run associates its roots BEFORE the inventory walks them -- during
    project creation there is not yet anything to have scanned -- so the
    first write necessarily says was_scanned = 0. With INSERT OR IGNORE
    the later write that knows the truth was discarded, and the row said
    "not scanned" forever about roots that had in fact been fully
    inventoried.

    was_scanned only ever moves 0 -> 1, via MAX(). A second write that
    knows less than the first must not be able to un-record coverage;
    an unavailable root simply never gets its 1. The descriptive columns
    take the newest non-NULL value, because those are observations of
    the drive at a moment and the later look is the better one.

    No schema change: this uses the unique constraint introduced in schema 5 and retained by later schemas.
    """
    recorded = 0
    for root in roots:
        conn.execute(
            "INSERT INTO run_source_root ("
            "  run_id, source_root_id, project_id, root_path_at_run,"
            "  was_scanned, was_available, drive_type, filesystem,"
            "  total_bytes, free_bytes"
            ") VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id, source_root_id) DO UPDATE SET "
            "  root_path_at_run = excluded.root_path_at_run,"
            "  was_scanned      = MAX(run_source_root.was_scanned,"
            "                         excluded.was_scanned),"
            "  was_available    = COALESCE(excluded.was_available,"
            "                              run_source_root.was_available),"
            "  drive_type       = COALESCE(excluded.drive_type,"
            "                              run_source_root.drive_type),"
            "  filesystem       = COALESCE(excluded.filesystem,"
            "                              run_source_root.filesystem),"
            "  total_bytes      = COALESCE(excluded.total_bytes,"
            "                              run_source_root.total_bytes),"
            "  free_bytes       = COALESCE(excluded.free_bytes,"
            "                              run_source_root.free_bytes)",
            (run_id, root["source_root_id"], root["root_path_at_run"],
             1 if root.get("was_scanned") else 0,
             _bool_or_none(root.get("was_available")),
             root.get("drive_type"), root.get("filesystem"),
             root.get("total_bytes"), root.get("free_bytes")))
        recorded += 1
    return recorded


def get_run_source_roots(conn, run_id):
    """Return the source-root coverage record for one run.

    B6.1 makes run_source_root an explicit read surface rather than a
    write-only forensic table. The rows preserve what was intended/scanned,
    availability, filesystem and capacity facts for later diagnostics.
    """
    return conn.execute(
        "SELECT rsr.*, sr.root_path AS current_root_path "
        "FROM run_source_root rsr "
        "LEFT JOIN source_root sr ON sr.source_root_id=rsr.source_root_id "
        "WHERE rsr.run_id=? ORDER BY rsr.run_source_root_id", (run_id,)).fetchall()


# ---------------------------------------------------------------------------
# run_stage
# ---------------------------------------------------------------------------

def create_stage(conn, run_id, sequence, stage_key, stage_label=None,
                 stage_role=None, command=None, status="running",
                 started_utc=None):
    if status not in STAGE_STATUSES:
        raise ValueError("Unknown stage status: %r" % (status,))
    cur = conn.execute(
        "INSERT INTO run_stage ("
        "  run_id, project_id, sequence, stage_key, stage_label, stage_role,"
        "  command, status, started_utc"
        ") VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, sequence, stage_key, stage_label, stage_role, command,
         status, started_utc or utc_now()))
    return cur.lastrowid


def finish_stage(conn, run_stage_id, status, exit_code=None, ended_utc=None,
                 stdout_log_path=None, stderr_log_path=None,
                 skip_reason=None, notes=None):
    if status not in STAGE_STATUSES:
        raise ValueError("Unknown stage status: %r" % (status,))
    ended = ended_utc or utc_now()
    row = conn.execute("SELECT started_utc FROM run_stage WHERE run_stage_id = ?",
                       (run_stage_id,)).fetchone()
    duration_ms = None
    started = parse_utc(row["started_utc"]) if row else None
    finished = parse_utc(ended)
    if started and finished:
        duration_ms = int((finished - started).total_seconds() * 1000)

    counts = conn.execute(
        "SELECT"
        "  SUM(CASE WHEN severity = 'warning' THEN 1 ELSE 0 END) AS warns,"
        "  SUM(CASE WHEN severity IN ('error','critical') THEN 1 ELSE 0 END) AS errs"
        " FROM event WHERE run_stage_id = ?", (run_stage_id,)).fetchone()

    conn.execute(
        "UPDATE run_stage SET status = ?, exit_code = ?, ended_utc = ?,"
        "  duration_ms = ?, stdout_log_path = ?, stderr_log_path = ?,"
        "  skip_reason = ?, notes = ?, warning_count = ?, error_count = ?"
        " WHERE run_stage_id = ?",
        (status, exit_code, ended, duration_ms, stdout_log_path, stderr_log_path,
         skip_reason, notes, counts["warns"] or 0, counts["errs"] or 0, run_stage_id))
    return duration_ms


def record_skipped_stage(conn, run_id, sequence, stage_key, stage_label,
                         skip_reason, stage_role=None):
    """A stage that was never launched still gets a row.

    "Not run" and "ran and found nothing" and "ran and failed" are three
    different answers to the same question, and only the first is
    invisible unless it is written down deliberately.
    """
    now = utc_now()
    cur = conn.execute(
        "INSERT INTO run_stage ("
        "  run_id, project_id, sequence, stage_key, stage_label, stage_role,"
        "  status, skip_reason, started_utc, ended_utc, duration_ms"
        ") VALUES (?, 1, ?, ?, ?, ?, 'skipped', ?, ?, ?, 0)",
        (run_id, sequence, stage_key, stage_label, stage_role, skip_reason, now, now))
    return cur.lastrowid


def list_stages(conn, run_id):
    return conn.execute(
        "SELECT * FROM run_stage WHERE run_id = ? ORDER BY sequence",
        (run_id,)).fetchall()


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------

def next_event_seq(conn, run_id):
    if run_id is None:
        return None
    row = conn.execute("SELECT MAX(seq) AS m FROM event WHERE run_id = ?",
                       (run_id,)).fetchone()
    return (row["m"] or 0) + 1


def add_event(conn, severity, category, source, message, run_id=None,
              run_stage_id=None, stage_key=None, file_path=None,
              error_type=None, continued=None, file_skipped=None,
              retryable=None, detail=None, event_utc=None, seq=None):
    """Insert one structured event.

    Returns the new event_id. Callers that are inserting a burst should
    use add_events() instead -- one transaction beats hundreds.
    """
    if seq is None:
        seq = next_event_seq(conn, run_id)
    cur = conn.execute(
        "INSERT INTO event ("
        "  project_id, run_id, run_stage_id, seq, event_utc, severity, category,"
        "  source, stage_key, file_path, message, error_type, continued,"
        "  file_skipped, retryable, detail_json"
        ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, run_stage_id, seq, event_utc or utc_now(), severity, category,
         source, stage_key, file_path, message, error_type,
         _bool_or_none(continued), _bool_or_none(file_skipped),
         _bool_or_none(retryable), _json_or_none(detail)))
    return cur.lastrowid


def add_events(conn, events, run_id=None):
    """Insert a batch of event dicts in one go.

    A single analyzer stage can produce hundreds of per-file errors.
    Inserting them one autocommit statement at a time would turn logging
    into the slowest part of the stage, which is a good way to get
    logging turned off.
    """
    if not events:
        return 0
    seq = next_event_seq(conn, run_id) if run_id is not None else None
    rows = []
    for item in events:
        rows.append((
            item.get("run_id", run_id), item.get("run_stage_id"), seq,
            item.get("event_utc") or utc_now(),
            item.get("severity", "info"), item.get("category", "general"),
            item.get("source", "coordinator"), item.get("stage_key"),
            item.get("file_path"), item.get("message", ""),
            item.get("error_type"),
            _bool_or_none(item.get("continued")),
            _bool_or_none(item.get("file_skipped")),
            _bool_or_none(item.get("retryable")),
            _json_or_none(item.get("detail")),
        ))
        if seq is not None:
            seq += 1
    conn.executemany(
        "INSERT INTO event ("
        "  project_id, run_id, run_stage_id, seq, event_utc, severity, category,"
        "  source, stage_key, file_path, message, error_type, continued,"
        "  file_skipped, retryable, detail_json"
        ") VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)


def count_events(conn, run_id=None, run_stage_id=None, severities=None):
    clauses, params = [], []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    if run_stage_id is not None:
        clauses.append("run_stage_id = ?")
        params.append(run_stage_id)
    if severities:
        clauses.append("severity IN (%s)" % ",".join("?" * len(severities)))
        params.extend(severities)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute("SELECT COUNT(*) AS n FROM event" + where, params).fetchone()["n"]


# ---------------------------------------------------------------------------
# Stale-run reconciliation
# ---------------------------------------------------------------------------

def pid_alive(pid):
    """True / False / None.

    None means "could not determine", which is treated as alive by
    reconcile_stale_runs. On Windows OpenProcess can fail with access
    denied for a process owned by another account; answering "dead"
    there would let one user's launch rewrite another user's live run
    as interrupted. Refusing to guess is the safer failure.
    """
    if pid is None:
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # 87 ERROR_INVALID_PARAMETER == no such process.
                # 5  ERROR_ACCESS_DENIED     == exists, not ours.
                return False if ctypes.get_last_error() == 87 else None
            try:
                code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return None
                return code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None

    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except Exception:
        return None


def reconcile_stale_runs(conn, current_pid=None, stale_seconds=STALE_HEARTBEAT_SECONDS,
                         now_utc=None, pid_alive_fn=None):
    """Mark abandoned runs as interrupted. Returns a list of dicts
    describing what was reconciled.

    A run is left alone if it is this process's own, or if its process
    is confirmed (or cannot be disproved) alive AND its heartbeat is
    fresh. Everything else is a run whose process disappeared while the
    database still said 'running' -- which is exactly what a power cut,
    a Task Manager kill, or a crash looks like afterwards.

    Its 'running' stage is reconciled with it: a stage cannot outlive
    the process that was executing it.

    This RECORDS the interruption. It does not work out what remains to
    be done, and it does not restart anything.
    """
    alive_check = pid_alive_fn or pid_alive
    now = parse_utc(now_utc) if now_utc else datetime.now(timezone.utc)
    current_pid = current_pid if current_pid is not None else os.getpid()

    reconciled = []
    for row in conn.execute(
            "SELECT run_id, run_uid, run_kind, run_folder, host_pid, heartbeat_utc,"
            "       started_utc FROM run WHERE status = 'running'").fetchall():

        if row["host_pid"] is not None and int(row["host_pid"]) == int(current_pid):
            continue  # this process's own live run

        beat = parse_utc(row["heartbeat_utc"]) or parse_utc(row["started_utc"])
        beat_age = (now - beat).total_seconds() if beat else None
        heartbeat_fresh = beat_age is not None and beat_age <= stale_seconds

        alive = alive_check(row["host_pid"])
        # None (undeterminable) counts as alive, so ambiguity never
        # destroys a record that might be genuine.
        probably_alive = (alive is not False) and heartbeat_fresh
        if probably_alive:
            continue

        detected = utc_now()
        reason = "process %s; last heartbeat %s" % (
            {True: "reported alive but heartbeat stale",
             False: "not running",
             None: "state undeterminable"}[alive],
            ("%.0fs ago" % beat_age) if beat_age is not None else "never recorded")

        stages = conn.execute(
            "SELECT run_stage_id, stage_key, stage_label, sequence FROM run_stage"
            " WHERE run_id = ? AND status IN ('running','created') ORDER BY sequence",
            (row["run_id"],)).fetchall()
        for stage in stages:
            finish_stage(conn, stage["run_stage_id"], "interrupted",
                         notes="Reconciled at %s: %s" % (detected, reason))

        add_event(
            conn, "warning", "reconcile", "coordinator",
            "Run %s was still marked running from a previous session and has been "
            "recorded as interrupted (%s)." % (row["run_uid"], reason),
            run_id=row["run_id"],
            detail={"host_pid": row["host_pid"],
                    "heartbeat_utc": row["heartbeat_utc"],
                    "stages_reconciled": [s["stage_key"] for s in stages]},
            continued=False, retryable=True)

        finish_run(conn, row["run_id"], "interrupted",
                   notes="Reconciled on a later launch: %s" % reason)
        conn.execute("UPDATE run SET reconciled_utc = ? WHERE run_id = ?",
                     (detected, row["run_id"]))

        reconciled.append({
            "run_id": row["run_id"], "run_uid": row["run_uid"],
            "run_kind": row["run_kind"], "run_folder": row["run_folder"],
            "reason": reason,
            "stages": [s["stage_key"] for s in stages],
        })

    return reconciled


# ---------------------------------------------------------------------------
# Read-side helpers (used by the R2 verifier; a future dashboard will
# want the same questions answered)
# ---------------------------------------------------------------------------

def run_report(conn, run_id):
    run = get_run(conn, run_id)
    if run is None:
        return None
    stages = [dict(s) for s in list_stages(conn, run_id)]
    events = [dict(e) for e in conn.execute(
        "SELECT * FROM event WHERE run_id = ? ORDER BY seq, event_id",
        (run_id,)).fetchall()]
    return {"run": dict(run), "stages": stages, "events": events}


def project_run_summary(conn, limit=20):
    """The shape a future project dashboard will read. Provided now
    because it costs one query and proves the schema can answer the
    questions the UX direction calls for -- it is NOT wired into any
    UI in R2."""
    rows = list_runs(conn, limit=limit)
    return [{
        "run_uid": r["run_uid"], "run_kind": r["run_kind"],
        "run_folder": r["run_folder"], "status": r["status"],
        "started_utc": r["started_utc"], "ended_utc": r["ended_utc"],
        "duration_ms": r["duration_ms"], "stage_count": r["stage_count"],
        "warning_count": r["warning_count"], "error_count": r["error_count"],
    } for r in rows]
