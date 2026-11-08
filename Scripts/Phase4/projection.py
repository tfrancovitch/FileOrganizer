r"""The projection of journal events into the catalog (B21 unit 5; gate 1 G1; synthesis 12 "Journal and catalog").

The journal is the single authority for physical actions. Every catalog change that records one is a PROJECTION of
one journal event: a function of that event alone, keyed by the event's `action_id`, and idempotent (running it twice
changes nothing). Each projecting event carries `projection_state` PENDING -> PROJECTED (NOT_APPLICABLE when it
projects nothing).

ORDER (jobs.JobRunner drives it inside a run):
    C3 (verification, terminal item state, the projection payload, event PENDING)  -- a journal commit
    P  the projector writes the catalog rows (its own catalog commit)
       then the event is marked PROJECTED                                           -- a journal commit (fenced)
    C4 closes a job only when every event is PROJECTED or NOT_APPLICABLE.

ON EVERY OPEN (open_session): the projection is reconciled by table E (reconcile.py) BEFORE anything else is written:
    E1 event PENDING, no catalog row          -> replay (apply, then mark PROJECTED)
    E2 event PENDING, catalog row present     -> record (mark PROJECTED; the row is already there)
    E3 event PROJECTED, catalog row missing   -> replay (a restored, older catalog is repaired this way)
    E4 catalog row the journal has no projecting event for -> ASK: everything stops (JOB_GLOBAL)
    E5 event PROJECTED, catalog row present   -> none
    EX anything else (the row differs from the event, or no projector can read it) -> ASK: everything stops
If any row says "ask", NOTHING is replayed, recorded or marked, and ProjectionAsk is raised.

This module touches the catalog and the journal only. It never touches a file. Table E's replay is the one
reconciliation next action the product carries out by itself, because the handoff (unit 5) and gate 1 (G1) require
start-up to replay: it re-derives catalog rows from the journal, which is the authority. Every other row of every
table is shown and never acted on (reconcile.py).

The registry. A projector serves one or more item kinds and has three methods:
    apply(catalog_conn, event)      write the rows for the event, idempotently, in one catalog transaction
    observe(catalog_conn, event)    'absent' | 'match' | 'differ'  (is the event's row in the catalog, and is it the same)
    action_ids(catalog_conn)        {action_id, ...} of every row it has ever written
PRODUCT_PROJECTORS is empty in B21: the real projectors (p4_path_succession, file_state epochs, executor
observations) are B24's. Tests register the fake projector from p4_fakes.py, which writes a sandbox-only table.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import jobs as JB
from . import journal as J
from . import locks as L
from . import paths as P
from . import reconcile as R

#: Item kind -> projector. B24 registers the real ones. Nothing is registered in B21.
PRODUCT_PROJECTORS: dict = {}


class ProjectionError(J.JournalError):
    """A projection could not be written. Inside a run it is JOB_GLOBAL (jobs.py treats every JournalError so)."""
    code = "projection_error"
    scope = "JOB_GLOBAL"


class ProjectionConflict(ProjectionError):
    """The catalog already holds a row for this action_id and it is not this event's row. Never overwritten."""
    code = "projection_conflict"


class ProjectionAsk(ProjectionError):
    """Table E found a row that needs the person (E4, EX). Nothing was replayed; nothing may run (JOB_GLOBAL)."""
    code = "projection_ask"

    def __init__(self, message, findings=()):
        super().__init__(message)
        self.findings = list(findings)


class Projection:
    """The catalog side of one open project: a catalog connection (autocommit, `isolation_level=None`) and the
    registry. A read-only catalog connection is enough for `observe` and `catalog_action_ids` (reconcile uses that)."""

    def __init__(self, catalog_conn, projectors=None):
        if catalog_conn.isolation_level is not None:
            raise ProjectionError("the catalog connection must be in autocommit mode (isolation_level=None); "
                                  "each projector manages its own transaction")
        self.cat = catalog_conn
        self.projectors = dict(PRODUCT_PROJECTORS if projectors is None else projectors)

    def handles(self, kind):
        return kind in self.projectors

    @staticmethod
    def _kind(event):
        kind = event.get("item_kind")
        if not kind:
            raise ProjectionError("a projecting event without an item kind (event %s)" % event.get("event_id"))
        return kind

    def apply(self, event):
        """Write the event's catalog rows (idempotent). A conflict or a catalog failure is a ProjectionError."""
        kind = self._kind(event)
        if kind not in self.projectors:
            raise ProjectionError("no projector is registered for item kind %r" % kind)
        try:
            self.projectors[kind].apply(self.cat, event)
        except ProjectionError:
            raise
        except sqlite3.Error as exc:
            raise ProjectionError("the catalog write for action %s failed: %s" % (event.get("action_id"), exc))

    def observe(self, event):
        """'absent' | 'match' | 'differ' | 'unknown' (no projector for this kind, or the catalog cannot be read)."""
        kind = event.get("item_kind")
        if kind not in self.projectors:
            return "unknown"
        try:
            got = self.projectors[kind].observe(self.cat, event)
        except sqlite3.Error:
            return "unknown"
        return got if got in ("absent", "match", "differ") else "unknown"

    def catalog_action_ids(self):
        """Every action_id any registered projector has written into this catalog."""
        out = set()
        seen = set()
        for proj in self.projectors.values():
            if id(proj) in seen:
                continue
            seen.add(id(proj))
            out |= set(proj.action_ids(self.cat))
        return out


# ---------------------------------------------------------------------------------------------------
# Reading the projecting events
# ---------------------------------------------------------------------------------------------------
def projecting_events(conn, states=("PENDING", "PROJECTED")):
    """Every event that carries a projection, in journal order, with its item kind as `item_kind`."""
    marks = ",".join("?" * len(states))
    rows = conn.execute(
        "SELECT e.*, i.kind AS item_kind FROM operation_event e LEFT JOIN operation_item i ON i.item_id = e.item_id "
        "WHERE e.projection_state IN (%s) ORDER BY e.event_id" % marks, tuple(states)).fetchall()
    return [dict(r) for r in rows]


def event_by_id(conn, event_id):
    row = conn.execute(
        "SELECT e.*, i.kind AS item_kind FROM operation_event e LEFT JOIN operation_item i ON i.item_id = e.item_id "
        "WHERE e.event_id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------------------------------
# P: apply, then mark
# ---------------------------------------------------------------------------------------------------
def mark_projected(journal, run_id, event_id):
    """The PROJECTED mark: its own fenced journal commit. Only PENDING -> PROJECTED moves (a journal trigger)."""
    with journal.tx("P", run_id) as t:
        t.execute("UPDATE operation_event SET projection_state = 'PROJECTED', projected_utc = ? "
                  "WHERE event_id = ? AND projection_state = 'PENDING'", (J.utc_now(), event_id))


def project_event(journal, run_id, projection, event_id, hook=None):
    """P for one event: the projector writes the catalog, then the event is marked PROJECTED. `hook(point)` is called
    at 'P:before', 'P:after_apply' and 'P:after_mark' (a test kills the process there)."""
    ev = event_by_id(journal.conn, event_id)
    if ev is None:
        raise ProjectionError("no journal event %s" % event_id)
    if ev["projection_state"] == "NOT_APPLICABLE":
        return
    if hook:
        hook("P:before")
    if ev["projection_state"] == "PENDING":
        projection.apply(ev)
    if hook:
        hook("P:after_apply")
    mark_projected(journal, run_id, event_id)
    if hook:
        hook("P:after_mark")


# ---------------------------------------------------------------------------------------------------
# Replay on open (table E)
# ---------------------------------------------------------------------------------------------------
@dataclass
class ReplayReport:
    findings: list = field(default_factory=list)      # every table E finding (reconcile.Finding)
    replayed: list = field(default_factory=list)      # action_ids whose rows were (re)written
    recorded: list = field(default_factory=list)      # action_ids marked PROJECTED

    def counts(self):
        out = {}
        for f in self.findings:
            out[f.row_id] = out.get(f.row_id, 0) + 1
        return out


def replay_on_open(journal, run_id, projection):
    """Classify the projection by table E, then carry out E's replay and record rows -- but only if NO row says
    ask. Driven by each row's `outcome` (data), not by row ids."""
    findings = R.classify_projection(journal.conn, projection)
    stops = [f for f in findings if f.outcome == "ask"]
    if stops:
        raise ProjectionAsk("the catalog and the journal disagree in a way only the person can settle; nothing was "
                            "replayed and nothing may run (JOB_GLOBAL): " +
                            "; ".join("%s %s" % (f.row_id, f.note) for f in stops[:5]), stops)
    report = ReplayReport(findings=findings)
    for f in findings:
        if f.outcome == "none":
            continue
        ev = event_by_id(journal.conn, f.event_id)
        if f.outcome == "replay":
            projection.apply(ev)
            report.replayed.append(ev["action_id"])
            if ev["projection_state"] == "PENDING":
                mark_projected(journal, run_id, ev["event_id"])
                report.recorded.append(ev["action_id"])
        elif f.outcome == "record":
            mark_projected(journal, run_id, ev["event_id"])
            report.recorded.append(ev["action_id"])
        else:
            raise ProjectionError("table E row %s has an outcome replay does not know: %r" % (f.row_id, f.outcome))
    return report


# ---------------------------------------------------------------------------------------------------
# Opening a project for a mutating run
# ---------------------------------------------------------------------------------------------------
@dataclass
class Session:
    journal: object
    lock: object
    run_id: str
    opened: str                      # 'created' | 'completed_binding' | 'opened' (journal.open_for_project)
    orphans: list                    # job ids found RUNNING / PAUSED and marked INTERRUPTED
    replay: ReplayReport
    _closed: bool = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.lock.release()
        finally:
            self.journal.close()


def open_session(catalog_conn, projection, *, folder=None, machine=None, classifier=None, operation="execute",
                 lock_retry_seconds=L.DEFAULT_RETRY_S):
    """Open a project's journal for an execute, recovery or undo run. In this order:

        1. validate the journal-and-lock folder; take the project lock (busy -> LockBusy, after the retry window)
        2. apply the opening rules (journal.open_for_project) under the lock; write the owner row (the fencing token)
        3. table E: replay the projection, or stop everything if a row says ask (ProjectionAsk, nothing written)
        4. jobs left RUNNING or PAUSED by a process that is gone become INTERRUPTED (R4.3 table D)

    Returns a Session; the caller closes it. On any refusal the lock is released and the journal closed. Nothing here
    classifies a file or acts on one: reconcile.recover_on_start shows that, afterwards."""
    uid = J._catalog_project_uid(catalog_conn)
    store = P.ensure_folder(uid, override=folder, classifier=classifier)
    lock = L.ProjectLock(P.lock_path(store, uid), operation, project_uid=uid, retry_seconds=lock_retry_seconds).acquire()
    j = None
    try:
        j, how = J.open_for_project(catalog_conn, folder=store, project_uid=uid, machine=machine, classifier=classifier)
        info = lock.owner_block()
        j.set_owner(lock.run_id, info["windows_user"], info["machine_name"], info["pid"], operation)
        report = replay_on_open(j, lock.run_id, projection)
        orphans = JB.mark_orphans_interrupted(j, lock.run_id)
        return Session(j, lock, lock.run_id, how, orphans, report)
    except BaseException:
        try:
            lock.release()
        finally:
            if j is not None:
                j.close()
        raise
