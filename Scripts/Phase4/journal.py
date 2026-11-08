r"""The action journal (B21, decision D4; gate 1 G1).

A SEPARATE SQLite file, the single authority for what the product did to the user's files:

    journal_mode=WAL, synchronous=FULL on EVERY connection that writes it, explicit BEGIN IMMEDIATE / COMMIT,
    foreign_keys=ON, busy_timeout=5000, its own application_id and user_version.

It is backed up only through SQLite's backup API. It lives in a local, never-synced, non-network folder
(paths.py). Call the thing it records "journalled intent and verification", never a transaction.

Commit points (jobs.py drives them; each is its own commit, never merged, never reordered, each checking the
fencing token): C0 job approval -> C1 intent -> [the one step] -> C2 result -> [verify] -> C3 verification and
terminal item state -> P projection into the catalog, then the event marked PROJECTED -> C4 job close.

This module owns the store: schema, pragmas, transactions with the fencing check, the binding between a
catalog and its journal, and the owner row. It decides nothing about files.

Opening rules (open_for_project; the catalog holds migration 017's p4_journal_binding, at most one row):
    no binding, no journal                      -> create the journal, then write the binding
    binding, journal missing                    -> refuse (JournalMissing); NEVER create one
    journal, no binding, no jobs in the journal -> complete the binding
    journal, no binding, jobs in the journal    -> refuse and ask (JournalBindingAsk)
    the journal's project_uid or machine differs from the catalog's, or the journal uid differs from the
    binding's                                   -> refuse (JournalMismatch)
v1 mutation is single-machine: the journal is bound to the machine (MachineGuid).
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import paths as P

APPLICATION_ID = 0x464F4A4C            # 'FOJL'
JOURNAL_SCHEMA_VERSION = 1             # PRAGMA user_version of the journal file
SOFTWARE_VERSION = "B21"

# ---------------------------------------------------------------------------------------------------
# Vocabulary (synthesis 8.3 R4.7 as amended in 12; R4.3 2.5 for the recovery states)
# ---------------------------------------------------------------------------------------------------
ITEM_KINDS = ("move", "rename", "case_only", "quarantine", "restore", "create_folder", "metadata_write", "fake")

#: Item states. PLANNED -> (live preflight) -> PREFLIGHT_READY | STALE | CONFLICT | BLOCKED | UNSUPPORTED |
#: UNKNOWN | the typed blocks -> INTENT -> APPLIED -> APPLIED_VERIFIED | FAILED_NOT_APPLIED; the recovery
#: states of R4.3 2.5; SKIPPED (by the person); BLOCKED_DEPENDENCY.
ITEM_STATES = (
    "PLANNED", "PREFLIGHT_READY", "STALE", "CONFLICT", "BLOCKED", "UNSUPPORTED", "UNKNOWN",
    "BLOCKED_LOCKED", "BLOCKED_CLOUD_ONLY", "BLOCKED_ACCESS", "BLOCKED_DEPENDENCY",
    "INTENT", "APPLIED", "APPLIED_VERIFIED", "FAILED_NOT_APPLIED", "SKIPPED",
    # recovery states
    "INTERRUPTED_NOT_APPLIED", "APPLIED_UNRECORDED", "UNKNOWN_AFTER_INTERRUPT", "BLOCKED_COLLISION",
    "PRECONDITION_DRIFT", "APPLIED_UNVERIFIED", "RESULT_NOT_ON_DISK", "CHANGED_AFTER_FINAL", "NO_UNDO_RECORD",
)

JOB_STATES = ("APPROVED", "RUNNING", "PAUSED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "ABANDONED")

EVENT_KINDS = (
    "JOB_APPROVED",             # C0
    "STEP_INTENT_RECORDED",     # C1
    "STEP_RESULT_RECORDED",     # C2
    "VERIFICATION_RECORDED",    # C3
    "JOB_CLOSED",               # C4
    "ITEM_REFUSED",             # a refusal before any mutation
    "JOB_PAUSED", "JOB_INTERRUPTED", "JOB_RESUMED",
    "ITEM_SKIPPED", "ITEM_PREFLIGHT",
    "RESULT_RECOVERED_NOT_OBSERVED",
)

PROJECTION_STATES = ("NOT_APPLICABLE", "PENDING", "PROJECTED")
REFUSAL_SCOPES = ("ITEM_LOCAL", "JOB_GLOBAL")


def _q(values):
    return ",".join("'%s'" % v for v in values)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------------------------------
# Errors. Every one of them is JOB_GLOBAL when it happens inside a run (jobs.py maps them).
# ---------------------------------------------------------------------------------------------------
class JournalError(Exception):
    """Base class. `code` is machine-readable."""
    code = "journal_error"


class JournalMissing(JournalError):
    code = "journal_missing"


class JournalBindingAsk(JournalError):
    code = "journal_binding_ask"


class JournalMismatch(JournalError):
    code = "journal_mismatch"


class JournalCorrupt(JournalError):
    code = "journal_corrupt"


class FencingLost(JournalError):
    """The run holding this transaction is not the journal's owner any more (lock or fencing-token loss)."""
    code = "fencing_lost"


class JournalCommitFailed(JournalError):
    """A journal commit failed (disk full, I/O error, locked). The effect of the step on disk is unknown to
    the journal, so the job pauses (JOB_GLOBAL)."""
    code = "journal_commit_failed"


# ---------------------------------------------------------------------------------------------------
# The machine identity (v1 mutation is single-machine)
# ---------------------------------------------------------------------------------------------------
def read_machine_guid():
    """HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid, opened read-only (KEY_READ, 64-bit view)."""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                         winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    try:
        value, _type = winreg.QueryValueEx(key, "MachineGuid")
    finally:
        winreg.CloseKey(key)
    return str(value)


def read_machine_name():
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def default_machine_identity():
    """(machine_guid, machine_name). Injectable in tests as `machine=`."""
    return read_machine_guid(), read_machine_name()


# ---------------------------------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------------------------------
SCHEMA = f"""
CREATE TABLE journal_meta (
    meta_id        INTEGER PRIMARY KEY CHECK (meta_id = 1),
    journal_uid    TEXT NOT NULL,
    project_uid    TEXT NOT NULL,
    machine_guid   TEXT NOT NULL,
    machine_name   TEXT NOT NULL,
    app_version    TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_utc    TEXT NOT NULL
);

CREATE TABLE job (
    job_id              INTEGER PRIMARY KEY,
    job_uid             TEXT NOT NULL UNIQUE,
    state               TEXT NOT NULL CHECK (state IN ({_q(JOB_STATES)})),
    revision_ref        TEXT NOT NULL,      -- the locked revision frozen at C0 (never re-read from the job's own writes)
    manifest_hash       TEXT NOT NULL,      -- immutable once approved
    catalog_backup_path TEXT,               -- the SQLite-backup-API copy of the catalog taken at C0
    onedrive_answer     TEXT,               -- the person's answer, stored as given, never inferred
    authorising_record  TEXT NOT NULL,      -- who approved, and the record that says so
    run_id              TEXT,               -- the run (fencing token) that last held the job
    pause_reason        TEXT,
    software_version    TEXT NOT NULL,
    approved_utc        TEXT NOT NULL,
    closed_utc          TEXT
);

CREATE TABLE operation_item (
    item_id             INTEGER PRIMARY KEY,
    job_id              INTEGER NOT NULL REFERENCES job(job_id),
    position            INTEGER NOT NULL,
    kind                TEXT NOT NULL CHECK (kind IN ({_q(ITEM_KINDS)})),
    state               TEXT NOT NULL CHECK (state IN ({_q(ITEM_STATES)})),
    plan_ref            TEXT NOT NULL,      -- the plan item this step came from
    params              TEXT NOT NULL DEFAULT '{{}}',   -- JSON: what the step is (paths, names)
    depends_on_item_id  INTEGER REFERENCES operation_item(item_id),
    current_action_id   TEXT,               -- the action_id of the latest attempt (a retry gets a new one)
    refusal_class       TEXT,
    refusal_scope       TEXT CHECK (refusal_scope IS NULL OR refusal_scope IN ({_q(REFUSAL_SCOPES)})),
    UNIQUE (job_id, position)
);

CREATE TABLE evidence_snapshot (
    snapshot_id  INTEGER PRIMARY KEY,
    item_id      INTEGER NOT NULL REFERENCES operation_item(item_id),
    snapshot_kind TEXT NOT NULL,            -- 'before' (identity, size, times, attributes, link count, reparse tag)
    body         TEXT NOT NULL,             -- JSON
    created_utc  TEXT NOT NULL
);

CREATE TABLE operation_event (
    event_id             INTEGER PRIMARY KEY,
    action_id            TEXT NOT NULL,     -- one per attempt at an item; a retry is a new step with a new action_id
    job_id               INTEGER NOT NULL REFERENCES job(job_id),
    item_id              INTEGER REFERENCES operation_item(item_id),   -- NULL for job-level events
    seq                  INTEGER NOT NULL,
    kind                 TEXT NOT NULL CHECK (kind IN ({_q(EVENT_KINDS)})),
    plan_ref             TEXT,
    evidence_snapshot_id INTEGER REFERENCES evidence_snapshot(snapshot_id),
    before_identity      TEXT,              -- JSON identity tuple before the step
    after_identity       TEXT,              -- JSON identity tuple after the step
    method               TEXT,              -- which call did it
    native_result        TEXT,
    win32_error          INTEGER,
    verification_result  TEXT,
    recovery_token       TEXT,              -- what undo or recovery needs (a path, a manifest entry)
    authorising_record   TEXT,
    software_version     TEXT NOT NULL,
    run_id               TEXT NOT NULL,     -- the fencing token of the run that wrote the event
    detail               TEXT,              -- JSON, free-form facts for this event kind
    projection_payload   TEXT,              -- JSON: what the catalog must record for this event (B24's projectors read it)
    projection_state     TEXT NOT NULL CHECK (projection_state IN ({_q(PROJECTION_STATES)})),
    projected_utc        TEXT,
    created_utc          TEXT NOT NULL,
    UNIQUE (item_id, seq)
);
-- Job-level events (item_id IS NULL) are unique per (job, seq) too.
CREATE UNIQUE INDEX ux_event_job_seq ON operation_event (job_id, seq) WHERE item_id IS NULL;
-- At most one event of an action carries a projection: the catalog row is keyed by action_id.
CREATE UNIQUE INDEX ux_event_projected_action ON operation_event (action_id) WHERE projection_state <> 'NOT_APPLICABLE';
CREATE INDEX ix_event_projection ON operation_event (projection_state);

CREATE TABLE recovery_record (
    recovery_id      INTEGER PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES job(job_id),
    item_id          INTEGER REFERENCES operation_item(item_id),
    table_row        TEXT NOT NULL,         -- 'A2', 'C1', 'E3', ... the reconciliation row that matched
    recovered_state  TEXT NOT NULL,
    next_action      TEXT NOT NULL,         -- shown, never taken by recovery itself
    observed         TEXT,                  -- JSON: what was seen on disk
    shown_utc        TEXT NOT NULL,
    choice           TEXT,                  -- what the person chose, if they have
    chosen_utc       TEXT,
    chosen_action_id TEXT                   -- the new journalled action that carried the choice out
);

CREATE TABLE owner (
    owner_id       INTEGER PRIMARY KEY CHECK (owner_id = 1),
    run_id         TEXT NOT NULL,           -- the fencing token
    windows_user   TEXT NOT NULL,
    machine_name   TEXT NOT NULL,
    pid            INTEGER NOT NULL,
    started_utc    TEXT NOT NULL,
    operation      TEXT NOT NULL,
    heartbeat_utc  TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------------------------------
def connect_writer(path):
    """The ONE way a writing connection to the journal is opened. Reports synchronous=2 and journal_mode=wal."""
    conn = sqlite3.connect(os.fspath(path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise JournalCorrupt("the journal could not be put in WAL mode (got %r); is the folder local?" % (mode,))
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def connect_readonly(path):
    uri = Path(os.path.abspath(os.fspath(path))).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class Tx:
    """One explicit BEGIN IMMEDIATE ... COMMIT. Usage:

        with journal.tx("C1", run_id) as t:
            t.execute(...)

    `run_id` (the fencing token) is checked INSIDE the transaction, after BEGIN IMMEDIATE has taken the write
    lock: if the owner row does not carry it, nothing is written and FencingLost is raised. A commit that
    fails for any reason raises JournalCommitFailed. Test hooks: Journal.before_commit / after_commit are
    called with the label, so a test can raise a disk-full error or kill the process at a named point.
    """

    def __init__(self, journal, label, fence_run_id):
        self.j = journal
        self.label = label
        self.fence = fence_run_id
        self.conn = journal.conn

    def __enter__(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            raise JournalCommitFailed("%s: could not begin a journal transaction: %s" % (self.label, exc))
        if self.fence is not None:
            row = self.conn.execute("SELECT run_id FROM owner WHERE owner_id = 1").fetchone()
            if row is None or row[0] != self.fence:
                self._rollback()
                raise FencingLost("%s: this run (%s) no longer owns the journal (owner is %s)"
                                  % (self.label, self.fence, None if row is None else row[0]))
        return self

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def _rollback(self):
        try:
            self.conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self._rollback()
            return False
        try:
            if self.j.before_commit:
                self.j.before_commit(self.label)
            self.conn.execute("COMMIT")
        except sqlite3.Error as exc2:
            self._rollback()
            raise JournalCommitFailed("%s: the journal commit failed: %s" % (self.label, exc2))
        except BaseException:
            self._rollback()
            raise
        if self.j.after_commit:
            self.j.after_commit(self.label)
        return False


class Journal:
    """An open journal with one writing connection."""

    def __init__(self, path, conn):
        self.path = os.fspath(path)
        self.conn = conn
        self.before_commit = None       # test hook: callable(label) -> may raise or kill
        self.after_commit = None        # test hook: callable(label)

    # -- creating and opening --------------------------------------------------------------------------
    @classmethod
    def create(cls, path, project_uid, machine, app_version=SOFTWARE_VERSION):
        """Create a NEW journal file. Refuses to touch an existing file."""
        path = os.fspath(path)
        if os.path.exists(path):
            raise JournalError("refusing to create a journal over an existing file: %s" % path)
        guid, name = machine
        conn = connect_writer(path)
        try:
            conn.execute("PRAGMA application_id = %d" % APPLICATION_ID)
            conn.execute("BEGIN IMMEDIATE")
            for stmt in _split(SCHEMA):
                conn.execute(stmt)
            for stmt in TRIGGERS:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO journal_meta (meta_id, journal_uid, project_uid, machine_guid, machine_name, "
                "app_version, schema_version, created_utc) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, project_uid, guid, name, app_version, JOURNAL_SCHEMA_VERSION, utc_now()))
            conn.execute("PRAGMA user_version = %d" % JOURNAL_SCHEMA_VERSION)
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            conn.close()
            raise
        return cls(path, conn)

    @classmethod
    def open(cls, path):
        """Open an EXISTING journal for writing; verify it is one of ours."""
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise JournalMissing("no journal at %s" % path)
        conn = connect_writer(path)
        try:
            _verify_identity(conn, path)
        except BaseException:
            conn.close()
            raise
        return cls(path, conn)

    @classmethod
    def open_readonly(cls, path):
        """Read-only view (the Dashboard's). Writes through it fail."""
        path = os.fspath(path)
        if not os.path.isfile(path):
            raise JournalMissing("no journal at %s" % path)
        conn = connect_readonly(path)
        try:
            _verify_identity(conn, path)
        except BaseException:
            conn.close()
            raise
        return cls(path, conn)

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- facts ------------------------------------------------------------------------------------------
    def pragmas(self):
        c = self.conn
        return {
            "journal_mode": str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
            "synchronous": int(c.execute("PRAGMA synchronous").fetchone()[0]),
            "foreign_keys": int(c.execute("PRAGMA foreign_keys").fetchone()[0]),
            "busy_timeout": int(c.execute("PRAGMA busy_timeout").fetchone()[0]),
            "application_id": int(c.execute("PRAGMA application_id").fetchone()[0]),
            "user_version": int(c.execute("PRAGMA user_version").fetchone()[0]),
        }

    def meta(self):
        row = self.conn.execute("SELECT * FROM journal_meta WHERE meta_id = 1").fetchone()
        return dict(row) if row else None

    def has_jobs(self):
        return self.conn.execute("SELECT 1 FROM job LIMIT 1").fetchone() is not None

    # -- transactions -----------------------------------------------------------------------------------
    def tx(self, label, fence_run_id=None):
        return Tx(self, label, fence_run_id)

    # -- the owner row (mirrors the lock file's owner bytes; the run_id is the fencing token) -----------------
    def set_owner(self, run_id, windows_user, machine_name, pid, operation):
        now = utc_now()
        with self.tx("owner") as t:
            t.execute("INSERT INTO owner (owner_id, run_id, windows_user, machine_name, pid, started_utc, operation, "
                      "heartbeat_utc) VALUES (1, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(owner_id) DO UPDATE SET "
                      "run_id = excluded.run_id, windows_user = excluded.windows_user, machine_name = excluded.machine_name, "
                      "pid = excluded.pid, started_utc = excluded.started_utc, operation = excluded.operation, "
                      "heartbeat_utc = excluded.heartbeat_utc",
                      (run_id, windows_user, machine_name, int(pid), now, operation, now))

    def heartbeat(self, run_id):
        with self.tx("heartbeat", run_id) as t:
            t.execute("UPDATE owner SET heartbeat_utc = ? WHERE owner_id = 1", (utc_now(),))

    def owner(self):
        row = self.conn.execute("SELECT * FROM owner WHERE owner_id = 1").fetchone()
        return dict(row) if row else None

    # -- backup: only through SQLite's backup API -------------------------------------------------------------
    def backup_to(self, dest_path):
        dest = sqlite3.connect(os.fspath(dest_path))
        try:
            self.conn.backup(dest)
        finally:
            dest.close()
        return os.fspath(dest_path)


#: Triggers (each one statement). A job freezes its revision and manifest at C0, and what a step IS never
#: changes after approval; events are append-only facts (only the projection columns may change).
TRIGGERS = (
    """CREATE TRIGGER job_frozen_at_c0 BEFORE UPDATE OF job_uid, revision_ref, manifest_hash, authorising_record, approved_utc ON job
       BEGIN SELECT RAISE(ABORT, 'a job is frozen at C0: its revision, manifest hash and authorising record never change'); END""",
    """CREATE TRIGGER item_frozen_at_c0 BEFORE UPDATE OF job_id, position, kind, plan_ref, params ON operation_item
       BEGIN SELECT RAISE(ABORT, 'what a step is never changes after approval'); END""",
    """CREATE TRIGGER event_is_a_fact BEFORE UPDATE OF action_id, job_id, item_id, seq, kind, plan_ref, evidence_snapshot_id,
           before_identity, after_identity, method, native_result, win32_error, verification_result, recovery_token,
           authorising_record, software_version, run_id, detail, projection_payload, created_utc ON operation_event
       BEGIN SELECT RAISE(ABORT, 'a journal event is a recorded fact; only its projection state moves'); END""",
    """CREATE TRIGGER event_never_deleted BEFORE DELETE ON operation_event
       BEGIN SELECT RAISE(ABORT, 'journal events are never deleted'); END""",
    """CREATE TRIGGER projection_moves_forward BEFORE UPDATE OF projection_state ON operation_event
       WHEN NOT (OLD.projection_state = NEW.projection_state OR (OLD.projection_state = 'PENDING' AND NEW.projection_state = 'PROJECTED'))
       BEGIN SELECT RAISE(ABORT, 'a projection moves only from PENDING to PROJECTED'); END""",
)


def _split(script):
    """Split a SQL script into single statements (no semicolons inside the strings used here)."""
    out = []
    buf = []
    for line in script.splitlines():
        stripped = line.split("--", 1)[0].rstrip() if "'" not in line else line.rstrip()
        buf.append(stripped)
        if stripped.endswith(";"):
            out.append("\n".join(buf).strip().rstrip(";"))
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s.strip()]


def _verify_identity(conn, path):
    app = int(conn.execute("PRAGMA application_id").fetchone()[0])
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if app != APPLICATION_ID:
        raise JournalMismatch("%s is not a FileOrganizer journal (application_id %#x)" % (path, app))
    if ver > JOURNAL_SCHEMA_VERSION:
        raise JournalMismatch("%s has journal schema %d; this build understands up to %d" % (path, ver, JOURNAL_SCHEMA_VERSION))
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='journal_meta'").fetchone()
    if row is None or conn.execute("SELECT 1 FROM journal_meta WHERE meta_id = 1").fetchone() is None:
        raise JournalCorrupt("%s has no journal_meta row" % path)


# ---------------------------------------------------------------------------------------------------
# The binding between a catalog and its journal
# ---------------------------------------------------------------------------------------------------
def read_binding(catalog_conn):
    """The catalog's p4_journal_binding row as a dict, or None. (Migration 017 must have run.)"""
    row = catalog_conn.execute(
        "SELECT project_uid, journal_uid, machine_guid, machine_name, created_utc FROM p4_journal_binding").fetchone()
    if row is None:
        return None
    return {"project_uid": row[0], "journal_uid": row[1], "machine_guid": row[2], "machine_name": row[3], "created_utc": row[4]}


def _catalog_project_uid(catalog_conn):
    row = catalog_conn.execute("SELECT project_uid FROM project WHERE project_id = 1").fetchone()
    if row is None:
        raise JournalMismatch("the catalog has no project row; a journal cannot be bound to it")
    return row[0]


def _write_binding(catalog_conn, meta):
    catalog_conn.execute(
        "INSERT INTO p4_journal_binding (binding_id, project_uid, journal_uid, machine_guid, machine_name, created_utc) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (meta["project_uid"], meta["journal_uid"], meta["machine_guid"], meta["machine_name"], utc_now()))
    if not catalog_conn.in_transaction:
        return
    catalog_conn.commit()


def open_for_project(catalog_conn, folder=None, project_uid=None, machine=None, classifier=None):
    """Apply the opening rules. Returns (Journal, action) where action is
    'created', 'completed_binding' or 'opened'. Raises a JournalError subclass on every refusal.

    `folder` is the journal-and-lock folder (every test passes a sandbox folder); `machine` is
    (guid, name) and is injectable. Nothing is created before the path has been validated.
    """
    cat_uid = _catalog_project_uid(catalog_conn)
    if project_uid is not None and project_uid != cat_uid:
        raise JournalMismatch("asked to open the journal of project %s but the catalog is project %s" % (project_uid, cat_uid))
    project_uid = cat_uid
    folder = P.store_folder(project_uid, override=folder, classifier=classifier)
    jpath = P.journal_path(folder, project_uid)
    here_guid, here_name = machine if machine is not None else default_machine_identity()
    binding = read_binding(catalog_conn)
    exists = os.path.isfile(jpath)

    if binding is not None and binding["project_uid"] != project_uid:
        raise JournalMismatch("the catalog's journal binding names project %s, not this project (%s)"
                              % (binding["project_uid"], project_uid))
    if binding is not None and binding["machine_guid"] != here_guid:
        raise JournalMismatch("this project's journal is bound to another machine (%s); v1 mutation is single-machine"
                              % binding["machine_name"])

    if binding is None and not exists:
        P.ensure_folder(project_uid, override=folder, classifier=classifier)
        j = Journal.create(jpath, project_uid, (here_guid, here_name))
        try:
            _write_binding(catalog_conn, j.meta())
        except BaseException:
            j.close()
            raise
        return j, "created"

    if binding is not None and not exists:
        raise JournalMissing("this project has a journal binding but its journal is missing (%s). The product never "
                             "creates a blank journal for a project that already has one; restore it from a backup "
                             "(see ARCHITECTURE.md, the restore ceremony)." % jpath)

    # a journal file exists from here on
    j = Journal.open(jpath)
    try:
        meta = j.meta()
        if meta["project_uid"] != project_uid:
            raise JournalMismatch("the journal belongs to project %s, not %s" % (meta["project_uid"], project_uid))
        if meta["machine_guid"] != here_guid:
            raise JournalMismatch("the journal is bound to another machine (%s); v1 mutation is single-machine"
                                  % meta["machine_name"])
        if binding is None:
            if j.has_jobs():
                raise JournalBindingAsk("a journal with recorded jobs exists for this project but the catalog has no "
                                        "binding to it (a restored or replaced catalog?). The product will not guess; "
                                        "this needs the person's decision (see ARCHITECTURE.md, the restore ceremony).")
            _write_binding(catalog_conn, meta)
            return j, "completed_binding"
        if binding["journal_uid"] != meta["journal_uid"]:
            raise JournalMismatch("the catalog is bound to journal %s but the file at %s is journal %s"
                                  % (binding["journal_uid"], jpath, meta["journal_uid"]))
        return j, "opened"
    except BaseException:
        j.close()
        raise
