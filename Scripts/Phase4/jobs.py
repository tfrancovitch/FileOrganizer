r"""The job and item state machine, and the runner (B21; synthesis 8.3 R4.7 as amended in 12; R4.3 5.3).

COMMIT POINTS -- never merged, never reordered, each its own journal commit, each checking the fencing token:

    C0   job approval: the manifest hash, the catalog backup, the person's OneDrive answer, the revision
         reference, every item as PLANNED                                    (approve_job)
         preconditions: the live preflight of the item (no file change)
    C1   intent: evidence snapshot, before-state, recovery token, item INTENT   -- committed BEFORE any file call
         the one step (the action)
    C2   result: what the step reported (native result, Win32 error)            -- never recorded before the step ran
         verify: the action observes the post-state
    C3   verification and the terminal item state (and the projection payload)
    P    projection into the catalog (idempotent, keyed by action_id), THEN the event marked PROJECTED
    C4   job close, only when every item is terminal and every event is PROJECTED or NOT_APPLICABLE

This is "journalled intent and verification". It is not a transaction.

REFUSAL SCOPES. ITEM_LOCAL (the job may continue): CONFLICT, STALE, typed BLOCKED_LOCKED, typed
BLOCKED_CLOUD_ONLY -- each only when it happened before any mutation. Everything else pauses the job (JOB_GLOBAL):
journal unavailable or commit failure, lock or fencing-token loss, catalog/journal identity mismatch, root or
volume identity change, sync-root state unknown, a postcondition mismatch, any native error whose effect on disk
is ambiguous, and any class this module does not name (unknown blocks). CIRCUIT BREAKER (gate 1 G14, with the
Architect's small-job ruling of 2026-11-07 in DECISIONS.md): three consecutive refusals of one class, or refusals
of one class reaching max(3, ceil(10 % of the job's items)), or any `source_root_available` failure pauses the job;
BLOCKED_DEPENDENCY is not counted. A transient lock refusal is retried at 250 ms, 1 s and 3 s, then ends BLOCKED_LOCKED.

The runner never decides what to do with a file: the ACTION does (B21: the fake action over a fake filesystem).
A job freezes its revision at C0; obsolescence is checked at C0 and at every resume, never from the job's own
writes. Recovery of an interrupted job is reconcile.py's: this module only refuses to run past an item whose
state needs a person's decision.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass, field

from . import journal as J
from .journal import ITEM_KINDS, ITEM_STATES, JOB_STATES

# ---------------------------------------------------------------------------------------------------
# The tables (data, so a test can walk every pair)
# ---------------------------------------------------------------------------------------------------
REFUSAL_BEFORE_MUTATION = ("STALE", "CONFLICT", "BLOCKED", "UNSUPPORTED", "UNKNOWN", "BLOCKED_LOCKED",
                           "BLOCKED_CLOUD_ONLY", "BLOCKED_ACCESS")
RECOVERY_STATES = ("INTERRUPTED_NOT_APPLIED", "APPLIED_UNRECORDED", "UNKNOWN_AFTER_INTERRUPT", "BLOCKED_COLLISION",
                   "PRECONDITION_DRIFT", "APPLIED_UNVERIFIED", "RESULT_NOT_ON_DISK", "CHANGED_AFTER_FINAL", "NO_UNDO_RECORD")

#: The ONLY item-state edges. Anything else is refused (IllegalTransition). Edges out of a refusal or a failed
#: item (to PLANNED, SKIPPED) are the person's choices (a retry is a NEW step with its own intent); the runner of
#: B21 does not take them on its own. reconcile.py (unit 6) may add recovery edges, as data, with tests.
ITEM_TRANSITIONS = {
    "PLANNED": {"PREFLIGHT_READY", "STALE", "CONFLICT", "BLOCKED", "UNSUPPORTED", "UNKNOWN", "BLOCKED_LOCKED",
                "BLOCKED_CLOUD_ONLY", "BLOCKED_ACCESS", "BLOCKED_DEPENDENCY", "SKIPPED"},
    "PREFLIGHT_READY": {"INTENT", "STALE", "CONFLICT", "BLOCKED", "UNSUPPORTED", "UNKNOWN", "BLOCKED_LOCKED",
                        "BLOCKED_CLOUD_ONLY", "BLOCKED_ACCESS", "SKIPPED"},
    # a step that ends in a refusal that mutated nothing: the typed item-local refusals
    "INTENT": {"APPLIED", "FAILED_NOT_APPLIED", "STALE", "CONFLICT", "BLOCKED_LOCKED", "BLOCKED_CLOUD_ONLY",
               "INTERRUPTED_NOT_APPLIED", "APPLIED_UNRECORDED", "UNKNOWN_AFTER_INTERRUPT", "BLOCKED_COLLISION",
               "PRECONDITION_DRIFT", "NO_UNDO_RECORD"},
    "APPLIED": {"APPLIED_VERIFIED", "APPLIED_UNVERIFIED", "RESULT_NOT_ON_DISK", "UNKNOWN_AFTER_INTERRUPT"},
    "APPLIED_VERIFIED": {"CHANGED_AFTER_FINAL"},
    "FAILED_NOT_APPLIED": {"PLANNED", "SKIPPED"},
    # refusals before any mutation: terminal for the run; the person may retry (a new step) or skip
    "STALE": {"PLANNED", "SKIPPED"}, "CONFLICT": {"PLANNED", "SKIPPED"}, "BLOCKED": {"PLANNED", "SKIPPED"},
    "UNSUPPORTED": {"SKIPPED"}, "UNKNOWN": {"PLANNED", "SKIPPED"}, "BLOCKED_LOCKED": {"PLANNED", "SKIPPED"},
    "BLOCKED_CLOUD_ONLY": {"PLANNED", "SKIPPED"}, "BLOCKED_ACCESS": {"PLANNED", "SKIPPED"},
    "BLOCKED_DEPENDENCY": {"PLANNED", "SKIPPED"},
    "SKIPPED": set(),
    # recovery states: shown to the person; only the person's chosen, journalled actions leave them
    "INTERRUPTED_NOT_APPLIED": {"PLANNED", "SKIPPED", "FAILED_NOT_APPLIED"},
    "APPLIED_UNRECORDED": {"APPLIED", "SKIPPED"},
    "UNKNOWN_AFTER_INTERRUPT": {"PLANNED", "SKIPPED", "FAILED_NOT_APPLIED"},
    "BLOCKED_COLLISION": {"PLANNED", "SKIPPED", "FAILED_NOT_APPLIED"},
    "PRECONDITION_DRIFT": {"PLANNED", "SKIPPED", "FAILED_NOT_APPLIED"},
    "APPLIED_UNVERIFIED": {"APPLIED_VERIFIED", "UNKNOWN_AFTER_INTERRUPT", "SKIPPED"},
    "RESULT_NOT_ON_DISK": {"PLANNED", "SKIPPED", "FAILED_NOT_APPLIED"},
    "CHANGED_AFTER_FINAL": set(),
    "NO_UNDO_RECORD": {"SKIPPED", "FAILED_NOT_APPLIED"},
}

JOB_TRANSITIONS = {
    "APPROVED": {"RUNNING", "ABANDONED"},
    "RUNNING": {"PAUSED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "ABANDONED"},
    "PAUSED": {"RUNNING", "INTERRUPTED", "ABANDONED"},
    "INTERRUPTED": {"RUNNING", "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "ABANDONED"},
    "COMPLETED": set(), "COMPLETED_WITH_EXCEPTIONS": set(), "ABANDONED": set(),
}

#: Terminal for the purposes of "done" (R4.7): every item in one of these, and the job can close.
TERMINAL_ITEM_STATES = frozenset({"APPLIED_VERIFIED", "FAILED_NOT_APPLIED", "STALE", "CONFLICT", "BLOCKED", "UNSUPPORTED",
                                  "UNKNOWN", "BLOCKED_LOCKED", "BLOCKED_CLOUD_ONLY", "BLOCKED_ACCESS",
                                  "BLOCKED_DEPENDENCY", "SKIPPED", "CHANGED_AFTER_FINAL"})
#: States that need a person's decision before the runner may go past them (reconcile.py shows them).
NEEDS_DECISION_STATES = frozenset({"INTENT", "APPLIED"} | set(RECOVERY_STATES) - {"CHANGED_AFTER_FINAL"})

#: Refusals that may continue the job (only when they happened before any mutation).
ITEM_LOCAL_CLASSES = frozenset({"CONFLICT", "STALE", "BLOCKED_LOCKED", "BLOCKED_CLOUD_ONLY"})

#: The circuit breaker (DECISIONS.md 2026-11-07, B21 J-8 resolved by the Architect).
BREAKER_CONSECUTIVE = 3
BREAKER_PERCENT = 10          # of the job's items refused in one class ...
BREAKER_PERCENT_MIN = 3       # ... but never fewer than this many (a job of 30 items or fewer needs 3)
BREAKER_ROOT_CLASS = "source_root_available"


def breaker_threshold(job_items):
    """How many refusals of one class pause a job of `job_items` items: max(3, ceil(10 % of the items))."""
    return max(BREAKER_PERCENT_MIN, -(-BREAKER_PERCENT * int(job_items) // 100))

#: A transient lock refusal (Win32 error 32) is retried after these delays, then ends BLOCKED_LOCKED.
LOCK_RETRY_DELAYS = (0.25, 1.0, 3.0)


def scope_of(refusal_class):
    """ITEM_LOCAL or JOB_GLOBAL. A class that is not named as item-local is JOB_GLOBAL: unknown blocks."""
    return "ITEM_LOCAL" if refusal_class in ITEM_LOCAL_CLASSES else "JOB_GLOBAL"


def item_transition_allowed(old, new):
    return new in ITEM_TRANSITIONS.get(old, ())


def job_transition_allowed(old, new):
    return new in JOB_TRANSITIONS.get(old, ())


# ---------------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------------
class JobError(Exception):
    code = "job_error"


class IllegalTransition(JobError):
    code = "illegal_transition"


class ApprovalRefused(JobError):
    """C0 did not happen (the revision is obsolete, the backup failed, ...). Nothing was written."""
    code = "approval_refused"


class ResumeBlocked(JobError):
    """The job may not be resumed now (revision obsolete, an item needs a decision, manifest changed)."""
    code = "resume_blocked"

    def __init__(self, message, items=()):
        super().__init__(message)
        self.items = list(items)


class JobStopped(JobError):
    """A JOB_GLOBAL stop inside the runner that could not be recorded (the journal itself failed)."""
    code = "job_stopped"

    def __init__(self, message, scope="JOB_GLOBAL", cause=None):
        super().__init__(message)
        self.scope, self.cause = scope, cause


# ---------------------------------------------------------------------------------------------------
# What a job is
# ---------------------------------------------------------------------------------------------------
@dataclass
class ItemSpec:
    kind: str
    plan_ref: str
    params: dict = field(default_factory=dict)
    depends_on: int | None = None        # the 1-based position of the item this one depends on


@dataclass
class JobSpec:
    revision_ref: str
    items: list


def manifest_hash(revision_ref, items):
    """SHA-256 over the canonical form of the revision reference and every item (kind, plan ref, params,
    dependency), in order. This is what the person approves at C0 and what never changes afterwards."""
    body = {"revision_ref": revision_ref,
            "items": [{"pos": i, "kind": it.kind, "plan_ref": it.plan_ref, "params": it.params, "depends_on": it.depends_on}
                      for i, it in enumerate(items, 1)]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass
class Preflight:
    """The live preflight of one item (no file change). `refusal` is None when the item is ready."""
    refusal: str | None = None            # a class: CONFLICT, STALE, BLOCKED_LOCKED, ..., or a global class
    detail: str = ""
    before: dict = field(default_factory=dict)         # the before-identity / before-state
    recovery_token: str | None = None


@dataclass
class StepResult:
    status: str = "ok"                    # ok | locked | cloud_only | error
    native_result: str = ""
    win32_error: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass
class Verification:
    ok: bool
    outcome: str = "APPLIED_VERIFIED"     # APPLIED_VERIFIED | RESULT_NOT_ON_DISK | APPLIED_UNVERIFIED | NOT_APPLIED
    detail: str = ""
    after: dict = field(default_factory=dict)
    projection_payload: dict | None = None


class SimulatedCrash(BaseException):
    """Raised by a test hook to stop the process at a named point as a crash would (nothing after it runs)."""


@dataclass
class RunOutcome:
    status: str                           # completed | paused | stopped
    job_state: str
    reason: str = ""
    scope: str | None = None
    processed: int = 0


def utc_now():
    return J.utc_now()


def _json(obj):
    return None if obj is None else json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------------------------------
# State changes always go through here: the table decides
# ---------------------------------------------------------------------------------------------------
def set_item_state(t, item_id, new_state, **columns):
    """Change an item's state inside transaction `t`. Raises IllegalTransition for an edge not in the table."""
    row = t.execute("SELECT state FROM operation_item WHERE item_id = ?", (item_id,)).fetchone()
    if row is None:
        raise JobError("no such item %r" % (item_id,))
    old = row[0]
    if new_state not in ITEM_STATES or not item_transition_allowed(old, new_state):
        raise IllegalTransition("item %s: %s -> %s is not a transition of the item state machine" % (item_id, old, new_state))
    sets, vals = ["state = ?"], [new_state]
    for k, v in columns.items():
        sets.append("%s = ?" % k)
        vals.append(v)
    t.execute("UPDATE operation_item SET %s WHERE item_id = ?" % ", ".join(sets), vals + [item_id])
    return old


def set_job_state(t, job_id, new_state, **columns):
    row = t.execute("SELECT state FROM job WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise JobError("no such job %r" % (job_id,))
    old = row[0]
    if new_state not in JOB_STATES or not job_transition_allowed(old, new_state):
        raise IllegalTransition("job %s: %s -> %s is not a transition of the job state machine" % (job_id, old, new_state))
    sets, vals = ["state = ?"], [new_state]
    for k, v in columns.items():
        sets.append("%s = ?" % k)
        vals.append(v)
    t.execute("UPDATE job SET %s WHERE job_id = ?" % ", ".join(sets), vals + [job_id])
    return old


def _next_seq(t, job_id, item_id):
    if item_id is None:
        row = t.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM operation_event WHERE job_id = ? AND item_id IS NULL", (job_id,)).fetchone()
    else:
        row = t.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM operation_event WHERE item_id = ?", (item_id,)).fetchone()
    return int(row[0])


def add_event(t, run_id, job_id, item_id, action_id, kind, projection_state="NOT_APPLICABLE", **cols):
    """Append one event (the next sequence number for the item, or for the job when item_id is None)."""
    names = ["action_id", "job_id", "item_id", "seq", "kind", "software_version", "run_id", "projection_state", "created_utc"]
    vals = [action_id, job_id, item_id, _next_seq(t, job_id, item_id), kind, J.SOFTWARE_VERSION, run_id, projection_state, utc_now()]
    for k, v in cols.items():
        names.append(k)
        vals.append(v)
    t.execute("INSERT INTO operation_event (%s) VALUES (%s)" % (", ".join(names), ", ".join("?" * len(names))), vals)
    return vals[3]


# ---------------------------------------------------------------------------------------------------
# C0 -- approve
# ---------------------------------------------------------------------------------------------------
def approve_job(journal, run_id, spec, *, revision_check, backup_catalog, onedrive_answer, authorising_record, hook=None):
    """C0. Returns the new job id.

    Before the commit (and recorded by it): the revision check (an obsolete revision refuses approval), the catalog
    backup (taken with SQLite's backup API by `backup_catalog()`; a failure refuses approval), the manifest hash.
    `onedrive_answer` is the person's answer to "OneDrive is synced and not paused", stored exactly as given and
    never inferred (None means the question was not asked for this job).
    """
    for it in spec.items:
        if it.kind not in ITEM_KINDS:
            raise ApprovalRefused("unknown item kind %r" % (it.kind,))
        if it.depends_on is not None and not (1 <= it.depends_on <= len(spec.items)):
            raise ApprovalRefused("item depends on a position outside the job: %r" % (it.depends_on,))
        if it.kind == "metadata_write" and not it.params.get("before_state"):
            raise ApprovalRefused("a metadata_write step without a durable before-state (R4.3 table C row C4): refused")
    if not spec.items:
        raise ApprovalRefused("a job with no items")
    ok, why = revision_check(spec.revision_ref)
    if not ok:
        raise ApprovalRefused("the plan revision is obsolete or cannot be verified: %s" % (why,))
    try:
        backup_path = backup_catalog()
    except Exception as exc:                                         # noqa: BLE001
        raise ApprovalRefused("the catalog backup failed: %s" % (exc,))
    if not backup_path:
        raise ApprovalRefused("the catalog backup was not made")
    mh = manifest_hash(spec.revision_ref, spec.items)
    job_uid = uuid.uuid4().hex
    if hook:
        hook("C0:before", None)
    with journal.tx("C0", run_id) as t:
        t.execute("INSERT INTO job (job_uid, state, revision_ref, manifest_hash, catalog_backup_path, onedrive_answer, "
                  "authorising_record, run_id, software_version, approved_utc) VALUES (?, 'APPROVED', ?, ?, ?, ?, ?, ?, ?, ?)",
                  (job_uid, spec.revision_ref, mh, backup_path, onedrive_answer, authorising_record, run_id,
                   J.SOFTWARE_VERSION, utc_now()))
        job_id = t.execute("SELECT job_id FROM job WHERE job_uid = ?", (job_uid,)).fetchone()[0]
        ids = {}
        for pos, it in enumerate(spec.items, 1):
            dep = ids.get(it.depends_on) if it.depends_on else None
            if it.depends_on and dep is None:
                raise ApprovalRefused("item %d depends on item %d, which does not come before it" % (pos, it.depends_on))
            t.execute("INSERT INTO operation_item (job_id, position, kind, state, plan_ref, params, depends_on_item_id) "
                      "VALUES (?, ?, ?, 'PLANNED', ?, ?, ?)", (job_id, pos, it.kind, it.plan_ref, _json(it.params), dep))
            ids[pos] = t.execute("SELECT item_id FROM operation_item WHERE job_id = ? AND position = ?", (job_id, pos)).fetchone()[0]
        add_event(t, run_id, job_id, None, job_uid, "JOB_APPROVED", authorising_record=authorising_record,
                  detail=_json({"manifest_hash": mh, "revision_ref": spec.revision_ref, "items": len(spec.items),
                                "catalog_backup": backup_path, "onedrive_answer": onedrive_answer,
                                "revision_check": "ok"}))
    if hook:
        hook("C0:after", None)
    return job_id


def mark_orphans_interrupted(journal, run_id):
    """On start, with the project lock held: a job left RUNNING or PAUSED by a process that is gone is INTERRUPTED.
    (Nothing is classified, finished or rolled back here; reconcile.py shows what happened.) Returns the job ids."""
    out = []
    rows = journal.conn.execute("SELECT job_id, job_uid FROM job WHERE state IN ('RUNNING', 'PAUSED')").fetchall()
    for job_id, job_uid in rows:
        with journal.tx("interrupt", run_id) as t:
            set_job_state(t, job_id, "INTERRUPTED", pause_reason="the process that was running this job is gone")
            add_event(t, run_id, job_id, None, job_uid, "JOB_INTERRUPTED", detail=_json({"reason": "found at start"}))
        out.append(job_id)
    return out


# ---------------------------------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------------------------------
class JobRunner:
    """Runs one project's jobs while the caller holds the project lock.

    journal          an open journal.Journal (the writing connection)
    run_id           the fencing token of the lock held by this run
    actions          {item kind: action}; an action has preflight(item) / step(item) / verify(item, before)
    revision_check   callable(revision_ref) -> (ok, reason); runs at C0 and at resume, never per item
    projection       a projection.Projection (the catalog connection and the projector registry), or None; an
                     APPLIED_VERIFIED item of a kind it handles gets a PENDING event at C3, projected at P
    hook             callable(point, item_row) called at every named point; a test raises SimulatedCrash from it
    sleep            injectable (the lock retries at 250 ms, 1 s, 3 s)
    """

    def __init__(self, journal, run_id, actions, revision_check, projection=None, hook=None, sleep=time.sleep):
        self.j = journal
        self.run_id = run_id
        self.actions = actions
        self.revision_check = revision_check
        self.projection = projection
        self._hook = hook
        self.sleep = sleep

    def hook(self, point, item=None):
        if self._hook:
            self._hook(point, item)

    # -- reading ---------------------------------------------------------------------------------------
    def job(self, job_id):
        row = self.j.conn.execute("SELECT * FROM job WHERE job_id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def items(self, job_id):
        return [dict(r) for r in self.j.conn.execute("SELECT * FROM operation_item WHERE job_id = ? ORDER BY position", (job_id,))]

    def item(self, item_id):
        row = self.j.conn.execute("SELECT * FROM operation_item WHERE item_id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    # -- start / resume --------------------------------------------------------------------------------
    def start(self, job_id):
        """APPROVED -> RUNNING, then run."""
        job = self.job(job_id)
        if job["state"] != "APPROVED":
            raise JobError("job %s is %s, not APPROVED (use resume)" % (job_id, job["state"]))
        with self.j.tx("start", self.run_id) as t:
            set_job_state(t, job_id, "RUNNING", run_id=self.run_id)
        return self._run(job_id)

    def resume(self, job_id):
        """PAUSED or INTERRUPTED -> RUNNING. The revision check runs again; the manifest must still be the one approved
        at C0; no item may be in a state that needs the person's decision. Each item's preflight is re-run when its
        turn comes."""
        job = self.job(job_id)
        if job["state"] not in ("PAUSED", "INTERRUPTED"):
            raise ResumeBlocked("job %s is %s; only a PAUSED or INTERRUPTED job is resumed" % (job_id, job["state"]))
        ok, why = self.revision_check(job["revision_ref"])
        if not ok:
            raise ResumeBlocked("the plan revision is obsolete or cannot be verified: %s" % (why,))
        items = self.items(job_id)
        now_hash = manifest_hash(job["revision_ref"], [
            ItemSpec(r["kind"], r["plan_ref"], json.loads(r["params"]), self._dep_position(items, r)) for r in items])
        if now_hash != job["manifest_hash"]:
            raise ResumeBlocked("the job's manifest no longer matches what was approved at C0")
        waiting = [r for r in items if r["state"] in NEEDS_DECISION_STATES]
        if waiting:
            raise ResumeBlocked("%d item(s) need a decision before the job can go on (see the reconciliation list): %s"
                                % (len(waiting), ", ".join("#%d %s" % (r["position"], r["state"]) for r in waiting[:5])), waiting)
        with self.j.tx("resume", self.run_id) as t:
            set_job_state(t, job_id, "RUNNING", run_id=self.run_id, pause_reason=None)
            add_event(t, self.run_id, job_id, None, job["job_uid"], "JOB_RESUMED", detail=_json({"revision_check": "ok"}))
        return self._run(job_id)

    @staticmethod
    def _dep_position(items, row):
        dep = row["depends_on_item_id"]
        if dep is None:
            return None
        for r in items:
            if r["item_id"] == dep:
                return r["position"]
        return None

    def shutdown(self, job_id):
        """The process is exiting: a RUNNING or PAUSED job becomes INTERRUPTED (the caller then releases the lock)."""
        job = self.job(job_id)
        if job and job["state"] in ("RUNNING", "PAUSED"):
            with self.j.tx("shutdown", self.run_id) as t:
                set_job_state(t, job_id, "INTERRUPTED", pause_reason=job["pause_reason"] or "the process exited")
                add_event(t, self.run_id, job_id, None, job["job_uid"], "JOB_INTERRUPTED", detail=_json({"reason": "exit"}))

    # -- the loop -------------------------------------------------------------------------------------
    def _run(self, job_id):
        job = self.job(job_id)
        processed = 0
        streak_class, streak = None, 0
        refused_by_class = {}
        total = self.j.conn.execute("SELECT COUNT(*) FROM operation_item WHERE job_id = ?", (job_id,)).fetchone()[0]
        threshold = breaker_threshold(total)
        for state, cls in self.j.conn.execute(
                "SELECT state, refusal_class FROM operation_item WHERE job_id = ? AND refusal_class IS NOT NULL", (job_id,)):
            refused_by_class[cls] = refused_by_class.get(cls, 0) + 1
        try:
            while True:
                row = self.j.conn.execute(
                    "SELECT item_id FROM operation_item WHERE job_id = ? AND state IN ('PLANNED', 'PREFLIGHT_READY') "
                    "ORDER BY position LIMIT 1", (job_id,)).fetchone()
                if row is None:
                    break
                outcome = self._do_item(job, row[0])
                processed += 1
                if outcome["kind"] == "verified":
                    streak_class, streak = None, 0
                    continue
                if outcome["kind"] == "refused":
                    cls = outcome["class"]
                    if cls == "BLOCKED_DEPENDENCY":
                        continue            # a consequence of an earlier item, not a refusal of its own: not counted
                    streak = streak + 1 if cls == streak_class else 1
                    streak_class = cls
                    refused_by_class[cls] = refused_by_class.get(cls, 0) + 1
                    if outcome["scope"] == "JOB_GLOBAL":
                        return self._pause(job, "JOB_GLOBAL: %s (item #%d): %s" % (cls, outcome["position"], outcome["detail"]),
                                           "JOB_GLOBAL", processed)
                    if cls == BREAKER_ROOT_CLASS:
                        return self._pause(job, "circuit breaker: %s" % cls, "JOB_GLOBAL", processed)
                    if streak >= BREAKER_CONSECUTIVE:
                        return self._pause(job, "circuit breaker: %d consecutive %s refusals" % (streak, cls), "BREAKER", processed)
                    if refused_by_class[cls] >= threshold:
                        return self._pause(job, "circuit breaker: %d of %d items refused as %s (the threshold is %d: "
                                           "max(%d, ceil(%d%% of the items)))"
                                           % (refused_by_class[cls], total, cls, threshold, BREAKER_PERCENT_MIN, BREAKER_PERCENT), "BREAKER", processed)
                    continue
                # anything that is neither APPLIED_VERIFIED nor an expected item-local refusal pauses the job
                return self._pause(job, "%s on item #%d: %s" % (outcome["state"], outcome["position"], outcome["detail"]),
                                   "JOB_GLOBAL", processed)
            return self._close(job, processed)
        except J.JournalError as exc:
            # a journal commit failure, or the loss of the lock or fencing token, is JOB_GLOBAL; we cannot rely on
            # the journal to record the pause, so we try once and then stop
            try:
                self._pause(job, "JOB_GLOBAL: %s: %s" % (exc.code, exc), "JOB_GLOBAL", processed)
            except J.JournalError:
                pass
            raise JobStopped("JOB_GLOBAL: %s" % exc, "JOB_GLOBAL", exc)

    def _pause(self, job, reason, scope, processed):
        with self.j.tx("pause", self.run_id) as t:
            set_job_state(t, job["job_id"], "PAUSED", pause_reason=reason)
            add_event(t, self.run_id, job["job_id"], None, job["job_uid"], "JOB_PAUSED", detail=_json({"reason": reason, "scope": scope}))
        return RunOutcome("paused", "PAUSED", reason, scope, processed)

    # -- one item ------------------------------------------------------------------------------------
    def _do_item(self, job, item_id):
        item = self.item(item_id)
        pos = item["position"]
        action = self.actions.get(item["kind"])
        if item["depends_on_item_id"] is not None:
            dep = self.item(item["depends_on_item_id"])
            if dep["state"] != "APPLIED_VERIFIED":
                return self._refuse(job, item, "BLOCKED_DEPENDENCY", "item #%d did not reach APPLIED_VERIFIED (it is %s)"
                                    % (dep["position"], dep["state"]), scope="ITEM_LOCAL")
        if action is None:
            return self._refuse(job, item, "UNSUPPORTED", "no action is certified for item kind %r in this build" % item["kind"])
        # ---- preconditions: the live preflight (no file change)
        self.hook("preflight:before", item)
        pre = action.preflight(item)
        if pre.refusal:
            return self._refuse(job, item, pre.refusal, pre.detail)
        if item["kind"] == "metadata_write" and not pre.before:
            return self._refuse(job, item, "BLOCKED", "no durable before-state: such an INTENT row must never be written (R4.3 row C4)")
        # ---- C1: intent -- committed before any file call
        action_id = uuid.uuid4().hex
        self.hook("C1:before", item)
        with self.j.tx("C1", self.run_id) as t:
            if item["state"] == "PLANNED":
                set_item_state(t, item["item_id"], "PREFLIGHT_READY")
            set_item_state(t, item["item_id"], "INTENT", current_action_id=action_id, refusal_class=None, refusal_scope=None)
            t.execute("INSERT INTO evidence_snapshot (item_id, snapshot_kind, body, created_utc) VALUES (?, 'before', ?, ?)",
                      (item["item_id"], _json(pre.before), utc_now()))
            snap = t.execute("SELECT MAX(snapshot_id) FROM evidence_snapshot WHERE item_id = ?", (item["item_id"],)).fetchone()[0]
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_INTENT_RECORDED",
                      plan_ref=item["plan_ref"], evidence_snapshot_id=snap, before_identity=_json(pre.before),
                      recovery_token=pre.recovery_token, authorising_record=job["authorising_record"],
                      method=getattr(action, "method", "fake"), detail=_json({"kind": item["kind"], "params": json.loads(item["params"])}))
        self.hook("C1:after", item)
        # ---- the one step
        self.hook("step:before", item)
        result, attempts = self._step_with_lock_retries(action, item)
        self.hook("step:after", item)
        # ---- C2: result -- never before the step ran
        self.hook("C2:before", item)
        with self.j.tx("C2", self.run_id) as t:
            if result.status == "ok":
                set_item_state(t, item["item_id"], "APPLIED")
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_RESULT_RECORDED",
                      plan_ref=item["plan_ref"], method=getattr(action, "method", "fake"), native_result=result.native_result,
                      win32_error=result.win32_error, detail=_json({"status": result.status, "attempts": attempts, **result.detail}))
        self.hook("C2:after", item)
        # ---- verify (the action observes; it changes nothing)
        ver = action.verify(item, json.loads(_json(pre.before)), result)
        self.hook("verify:after", item)
        return self._c3(job, item, action, action_id, result, ver)

    @staticmethod
    def _call_step(action, item):
        """The action's one step. An exception is an UNCLASSIFIED native failure: its effect on disk is unknown until
        the verification looks (SimulatedCrash, a BaseException, is not caught: a crash is not an answer)."""
        try:
            return action.step(item)
        except Exception as exc:                                      # noqa: BLE001
            return StepResult("error", "unclassified failure: %s: %s" % (type(exc).__name__, exc), None)

    def _step_with_lock_retries(self, action, item):
        """One step call; a 'locked' answer (Win32 32, nothing mutated) is retried after 250 ms, 1 s, 3 s, with the
        live preflight re-checked before each retry. Returns (final result, list of attempt summaries)."""
        attempts = []
        result = self._call_step(action, item)
        attempts.append({"status": result.status, "win32_error": result.win32_error})
        for delay in LOCK_RETRY_DELAYS:
            if result.status != "locked":
                break
            self.sleep(delay)
            pre = action.preflight(item)
            if pre.refusal:
                result = StepResult("error", "preflight refused before a retry: %s" % pre.refusal, None)
                attempts.append({"status": "preflight_refused", "class": pre.refusal})
                break
            result = self._call_step(action, item)
            attempts.append({"status": result.status, "win32_error": result.win32_error, "after_s": delay})
        return result, attempts

    # -- C3 ----------------------------------------------------------------------------------------------
    def _c3(self, job, item, action, action_id, result, ver):
        """Verification and the terminal item state, then P."""
        self.hook("C3:before", item)
        projection_event = None
        if result.status == "ok" and ver.ok:
            new_state, refusal_cls, scope = "APPLIED_VERIFIED", None, None
        elif result.status == "ok":
            new_state = ver.outcome if ver.outcome in ("RESULT_NOT_ON_DISK", "APPLIED_UNVERIFIED") else "APPLIED_UNVERIFIED"
            refusal_cls, scope = new_state, "JOB_GLOBAL"
        elif result.status in ("locked", "cloud_only") and ver.ok and ver.outcome == "NOT_APPLIED":
            new_state = "BLOCKED_LOCKED" if result.status == "locked" else "BLOCKED_CLOUD_ONLY"
            refusal_cls, scope = new_state, "ITEM_LOCAL"
        elif ver.ok and ver.outcome == "NOT_APPLIED":
            new_state, refusal_cls, scope = "FAILED_NOT_APPLIED", "FAILED_NOT_APPLIED", "JOB_GLOBAL"
        else:
            new_state, refusal_cls, scope = "UNKNOWN_AFTER_INTERRUPT", "UNKNOWN_AFTER_INTERRUPT", "JOB_GLOBAL"
        will_project = (new_state == "APPLIED_VERIFIED" and self.projection is not None and self.projection.handles(item["kind"])
                        and ver.projection_payload is not None)
        with self.j.tx("C3", self.run_id) as t:
            if new_state == "APPLIED_VERIFIED":
                set_item_state(t, item["item_id"], new_state)
            else:
                # from APPLIED (the step reported ok but the disk disagrees) or from INTENT (the step reported a
                # failure and APPLIED was never reached): the table decides whether the edge exists
                set_item_state(t, item["item_id"], new_state, refusal_class=refusal_cls, refusal_scope=scope)
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "VERIFICATION_RECORDED",
                      projection_state="PENDING" if will_project else "NOT_APPLICABLE",
                      plan_ref=item["plan_ref"], after_identity=_json(ver.after), verification_result=ver.outcome,
                      projection_payload=_json(ver.projection_payload) if will_project else None,
                      detail=_json({"ok": ver.ok, "state": new_state, "detail": ver.detail}))
            if will_project:
                projection_event = t.execute("SELECT event_id FROM operation_event WHERE item_id = ? AND kind = 'VERIFICATION_RECORDED' "
                                             "ORDER BY seq DESC LIMIT 1", (item["item_id"],)).fetchone()[0]
        self.hook("C3:after", item)
        if projection_event is not None:
            self._project(item, projection_event)
        if new_state == "APPLIED_VERIFIED":
            return {"kind": "verified", "state": new_state, "position": item["position"]}
        if scope == "ITEM_LOCAL":
            return {"kind": "refused", "class": new_state, "scope": "ITEM_LOCAL", "position": item["position"], "detail": ver.detail or result.native_result}
        return {"kind": "failed", "state": new_state, "position": item["position"], "detail": ver.detail or result.native_result}

    # -- P ---------------------------------------------------------------------------------------------
    def _project(self, item, event_id):
        """P: the projector writes the catalog (idempotent, keyed by action_id), then the event is marked PROJECTED in
        its own fenced commit (projection.project_event). A projection failure is a JournalError: JOB_GLOBAL."""
        from . import projection as PR          # imported here: projection imports this module
        PR.project_event(self.j, self.run_id, self.projection, event_id, hook=lambda point: self.hook(point, item))

    # -- refusals before any mutation ------------------------------------------------------------------------
    def _refuse(self, job, item, cls, detail, scope=None):
        scope = scope or scope_of(cls)
        new_state = cls if cls in ITEM_STATES else "BLOCKED"
        self.hook("refuse:before", item)
        with self.j.tx("REFUSE", self.run_id) as t:
            set_item_state(t, item["item_id"], new_state, refusal_class=cls, refusal_scope=scope)
            add_event(t, self.run_id, job["job_id"], item["item_id"], item["current_action_id"] or job["job_uid"], "ITEM_REFUSED",
                      plan_ref=item["plan_ref"], native_result=cls, detail=_json({"class": cls, "scope": scope, "detail": detail}))
        self.hook("refuse:after", item)
        return {"kind": "refused", "class": cls, "scope": scope, "position": item["position"], "detail": detail,
                "state": new_state}

    # -- C4 --------------------------------------------------------------------------------------------
    def _close(self, job, processed):
        """C4: the job closes only when every item is terminal and every event is PROJECTED or NOT_APPLICABLE."""
        self.hook("C4:before", None)
        with self.j.tx("C4", self.run_id) as t:
            states = [r[0] for r in t.execute("SELECT state FROM operation_item WHERE job_id = ?", (job["job_id"],))]
            open_items = [s for s in states if s not in TERMINAL_ITEM_STATES]
            pending = t.execute("SELECT COUNT(*) FROM operation_event WHERE job_id = ? AND projection_state = 'PENDING'",
                                (job["job_id"],)).fetchone()[0]
            if open_items or pending:
                raise JobError("the job cannot close: %d item(s) not terminal, %d event(s) not projected" % (len(open_items), pending))
            final = "COMPLETED" if all(s == "APPLIED_VERIFIED" for s in states) else "COMPLETED_WITH_EXCEPTIONS"
            set_job_state(t, job["job_id"], final, closed_utc=utc_now())
            add_event(t, self.run_id, job["job_id"], None, job["job_uid"], "JOB_CLOSED",
                      detail=_json({"final": final, "items": len(states), "verified": sum(1 for s in states if s == "APPLIED_VERIFIED"),
                                    "exceptions": sum(1 for s in states if s != "APPLIED_VERIFIED")}))
        self.hook("C4:after", None)
        return RunOutcome("completed", final, "", None, processed)
