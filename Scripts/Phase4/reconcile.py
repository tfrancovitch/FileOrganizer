r"""Reconciliation of an interrupted job (B21 unit 6; R4.3 2.5; synthesis 8.2 and 12; axiom 17).

RECOVERY CLASSIFIES AND SHOWS. IT NEVER ACTS ON ITS OWN. Nothing in this module writes the journal, the catalog or the
disk: it reads the journal (a read-only connection is enough), asks an OBSERVER what the disk shows now (B21: the fake
filesystem; B22: the real one), asks the projection what the catalog holds, and maps each item, job and projecting
event to exactly ONE row of one table. Every "finish" or "roll back" a row names is a new journalled action that the
person chooses (a retry is a new step with its own intent, never a replay). The one exception is table E's
replay/record, which projection.open_session carries out on every open because unit 5 and gate 1 (G1) require it; it
re-derives catalog rows from the journal and never touches a file.

THE TABLES are data (TABLES below):
    A  same-volume rename or move (R4.3 rows A1-A12)        items of kind move, rename, quarantine, restore; fake 'rename'
    C  metadata write (R4.3 rows C1-C4)                    items of kind metadata_write
    D  job level (R4.3 2.5 "D", as rows)                    every job
    E  journal x projection (handoff unit 6; gate 1 G1)     every projecting event, every catalog row
    F  create_folder (handoff unit 6; D8)                   items of kind create_folder; fake 'create_folder'
Table B (cross-volume) is not built (after v1).

Each table has a KEY SPACE (the discrete facts it is decided on) and rows that say which key values they cover. The
classifier returns the one row whose values cover the key; if no explicit row does, the table's X row: R4.3 2.5
"UNKNOWN_AFTER_INTERRUPT is the explicit state for rows where the disk matches none of the expected shapes" -- the
outcome is always "ask". If two explicit rows covered one key the classifier raises AmbiguousRow; the suite proves over
the whole key space that this never happens and prints which keys fall to the X rows.

Vocabulary of outcomes (R4.3 2.5): finish (continue the journalled plan after the person says to resume), record (write
the missing journal row, take no file action), roll_back (undo by the recorded recovery path), ask (no action; show the
evidence), none (nothing to do); table E adds replay (re-derive the catalog rows from the journal event).

The journal state of an item is the highest commit point recorded for its CURRENT action (R4.3 "last durable journal
state"):
    INTENT          C1 recorded, no C2
    RESULT_SUCCESS  C2 recorded and the step said ok; no C3 saying APPLIED_VERIFIED
    RESULT_FAILURE  C2 recorded and the step said anything else (a native error, locked, cloud-only); no verified C3
    VERIFIED        C3 recorded APPLIED_VERIFIED
    INCONSISTENT    the events of the action do not form C1 [-> C2 [-> C3]] (a C2 without C1, two C2s, a C1 without the
                    before-state the commit must carry) -- no explicit row covers it, so it is always the X row (ask)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from itertools import product

from . import jobs as JB

# ---------------------------------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------------------------------
OUTCOMES = ("finish", "record", "roll_back", "ask", "none", "replay")


@dataclass(frozen=True)
class Row:
    table: str
    id: str
    when: dict | None            # key field -> tuple of values this row covers; None = the table's X row
    recovered_state: str
    outcome: str
    next_action: str             # shown to the person; never taken by recovery
    source: str


def _r(table, rid, when, state, outcome, text, source):
    return Row(table, rid, when, state, outcome, text, source)


JOURNAL_STATES = ("INTENT", "RESULT_SUCCESS", "RESULT_FAILURE", "VERIFIED", "INCONSISTENT")
PRESENT = ("match", "differ")

# -- A: same-volume rename or move --------------------------------------------------------------------------------
#   journal  the item's journal state
#   shape    distinct_paths | same_path (a case-only rename: the presence of source and destination cannot tell
#            the shapes apart; it needs its own table, B22)
#   src/dst  absent | match (present and equal to the snapshot's identity) | differ (present, not equal)
#   same     yes | no (both present: the same object or not) | n/a (one of them absent)
KEYSPACE_A = {"journal": JOURNAL_STATES, "shape": ("distinct_paths", "same_path"), "src": ("absent",) + PRESENT,
              "dst": ("absent",) + PRESENT, "same": ("n/a", "yes", "no")}
_D = ("distinct_paths",)
TABLE_A = (
    _r("A", "A1", {"journal": ("INTENT",), "shape": _D, "src": ("match",), "dst": ("absent",)},
       "INTERRUPTED_NOT_APPLIED", "finish",
       "Re-verify the preconditions, then offer 'resume'; the step may be repeated as-is after re-verifying", "R4.3 2.5 A1"),
    _r("A", "A2", {"journal": ("INTENT",), "shape": _D, "src": ("absent",), "dst": ("match",)},
       "APPLIED_UNRECORDED", "record",
       "Record the result as 'recovered, not observed', then run the verification", "R4.3 2.5 A2"),
    _r("A", "A3", {"journal": ("INTENT",), "shape": _D, "src": ("absent",), "dst": ("absent",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. Search the recorded quarantine or staging path and recent-delete evidence; never infer", "R4.3 2.5 A3"),
    _r("A", "A4", {"journal": ("INTENT",), "shape": _D, "src": PRESENT, "dst": PRESENT, "same": ("yes",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. The destination is the same object as the source (a hard link or a duplicate-handling case)", "R4.3 2.5 A4"),
    _r("A", "A5", {"journal": ("INTENT",), "shape": _D, "src": PRESENT, "dst": PRESENT, "same": ("no",)},
       "BLOCKED_COLLISION", "ask",
       "Ask. Never overwrite; the item is not applied (another object holds the destination)", "R4.3 2.5 A5"),
    _r("A", "A6", {"journal": ("INTENT",), "shape": _D, "src": ("differ",), "dst": ("absent",)},
       "PRECONDITION_DRIFT", "ask",
       "Ask. The source changed since the plan: the approved plan no longer describes this object", "R4.3 2.5 A6"),
    _r("A", "A7", {"journal": ("RESULT_SUCCESS",), "shape": _D, "src": ("absent",), "dst": ("match",)},
       "APPLIED_UNVERIFIED", "record",
       "Run the verification and record it", "R4.3 2.5 A7"),
    _r("A", "A8", {"journal": ("RESULT_SUCCESS",), "shape": _D, "src": ("match",), "dst": ("absent",)},
       "RESULT_NOT_ON_DISK", "finish",
       "The rename may have been lost in a power cut. Treat as A1: re-verify, then ask to resume", "R4.3 2.5 A8"),
    _r("A", "A9", {"journal": ("RESULT_SUCCESS",), "shape": _D, "src": ("absent",), "dst": ("absent",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask", "Ask", "R4.3 2.5 A9"),
    _r("A", "A10", {"journal": ("RESULT_FAILURE",), "shape": _D, "src": ("match",), "dst": ("absent",)},
       "FAILED_NOT_APPLIED", "none",
       "Terminal. Show the Win32 code; offer a retry as a NEW action", "R4.3 2.5 A10"),
    _r("A", "A11", {"journal": ("VERIFIED",), "shape": _D, "src": ("absent",), "dst": ("match",)},
       "APPLIED_VERIFIED", "none", "None (terminal, as recorded)", "R4.3 2.5 A11"),
    _r("A", "A12", {"journal": ("VERIFIED",), "shape": _D, "src": PRESENT, "dst": ("absent",)},
       "CHANGED_AFTER_FINAL", "none",
       "Not a crash case. Report the change; undo is no longer possible by record", "R4.3 2.5 A12"),
    _r("A", "AX", None, "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. The journal and the disk match none of the expected shapes; never infer",
       "R4.3 2.5 vocabulary (UNKNOWN_AFTER_INTERRUPT: the disk matches none of the expected shapes)"),
)

# -- C: metadata write ---------------------------------------------------------------------------------------------
#   before   durable (the C1 snapshot carries the before-state) | missing
#   current  before | target | neither | ambiguous (the before-state equals the target: no observation can tell C1
#            from C2)
KEYSPACE_C = {"journal": JOURNAL_STATES, "before": ("durable", "missing"),
              "current": ("before", "target", "neither", "ambiguous")}
TABLE_C = (
    _r("C", "C1", {"journal": ("INTENT",), "before": ("durable",), "current": ("before",)},
       "INTERRUPTED_NOT_APPLIED", "finish", "Re-verify, offer resume", "R4.3 2.5 C1"),
    _r("C", "C2", {"journal": ("INTENT",), "before": ("durable",), "current": ("target",)},
       "APPLIED_UNRECORDED", "record", "Record, then verify", "R4.3 2.5 C2"),
    _r("C", "C3", {"journal": ("INTENT",), "before": ("durable",), "current": ("neither",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask", "Ask (someone changed it, or a partial write)", "R4.3 2.5 C3"),
    _r("C", "C4", {"journal": ("INTENT",), "before": ("missing",)},
       "NO_UNDO_RECORD", "ask",
       "Do not resume. Ask. (Such an INTENT row must never be written: jobs.py refuses it at approval and at C1)",
       "R4.3 2.5 C4"),
    _r("C", "CX", None, "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. The journal and the metadata match none of the expected shapes", "R4.3 2.5 vocabulary"),
)

# -- D: job level ----------------------------------------------------------------------------------------------------
#   job    the job's state in the journal
#   items  needs_decision (an item is INTENT, APPLIED or in a recovery state) | runnable (none needs a decision and
#          some are PLANNED / PREFLIGHT_READY) | all_terminal (every item is terminal)
OPEN_JOB_STATES = ("RUNNING", "PAUSED", "INTERRUPTED")
KEYSPACE_D = {"job": ("APPROVED", "RUNNING", "PAUSED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "ABANDONED"),
              "items": ("needs_decision", "runnable", "all_terminal")}
TABLE_D = (
    _r("D", "D1", {"job": OPEN_JOB_STATES, "items": ("needs_decision",)}, "INTERRUPTED", "ask",
       "The job stays INTERRUPTED until each item is terminal or the person marks it 'accept as unknown'; each item is "
       "listed with its row. Nothing is auto-finished", "R4.3 2.5 D"),
    _r("D", "D2", {"job": OPEN_JOB_STATES, "items": ("runnable",)}, "INTERRUPTED", "finish",
       "Offer 'resume': the revision check and each remaining item's live preflight run again", "R4.3 2.5 D; R4.7"),
    _r("D", "D3", {"job": OPEN_JOB_STATES, "items": ("all_terminal",)}, "INTERRUPTED", "finish",
       "Offer to close the job (C4); every item is terminal", "R4.3 2.5 D; R4.7 'done'"),
    _r("D", "D4", {"job": ("APPROVED",), "items": ("runnable",)}, "APPROVED", "finish",
       "Approved and never started: nothing was done on disk. Offer 'start' or 'abandon'", "R4.7 job states"),
    _r("D", "D5", {"job": ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS"), "items": ("all_terminal",)}, "(as recorded)", "none",
       "None (closed)", "R4.7 'done'"),
    _r("D", "D6", {"job": ("ABANDONED",), "items": ("runnable", "all_terminal")}, "ABANDONED", "none",
       "None (abandoned; items never started stay as they are)", "R4.7 job states"),
    _r("D", "DX", None, "(as recorded)", "ask",
       "Ask. The job's state contradicts its items' states", "R4.3 2.5 vocabulary"),
)

# -- E: journal x projection ----------------------------------------------------------------------------------------
#   projection  the event's projection_state, or NONE (a catalog row for which the journal has no projecting event)
#   catalog     absent | match | differ | unknown (no projector for the kind, or the catalog cannot be read) |
#               present (a catalog row seen from the catalog side)
KEYSPACE_E = {"projection": ("PENDING", "PROJECTED", "NONE"), "catalog": ("absent", "match", "differ", "unknown", "present")}
TABLE_E = (
    _r("E", "E1", {"projection": ("PENDING",), "catalog": ("absent",)}, "PENDING", "replay",
       "Replay: the projector writes the row, then the event is marked PROJECTED", "handoff unit 6 E; G1"),
    _r("E", "E2", {"projection": ("PENDING",), "catalog": ("match",)}, "PENDING", "record",
       "Record: mark the event PROJECTED (the row is already there)", "handoff unit 6 E"),
    _r("E", "E3", {"projection": ("PROJECTED",), "catalog": ("absent",)}, "PROJECTED", "replay",
       "Replay: a restored, older catalog lacks the row; the projector writes it again", "handoff unit 6 E; G1"),
    _r("E", "E4", {"projection": ("NONE",), "catalog": ("present",)}, "(none)", "ask",
       "Ask (JOB_GLOBAL): the catalog holds a row for an action the journal has never recorded; everything stops",
       "handoff unit 5 and 6 E"),
    _r("E", "E5", {"projection": ("PROJECTED",), "catalog": ("match",)}, "PROJECTED", "none",
       "None (consistent)", "derived: the consistent case"),
    _r("E", "EX", None, "(as recorded)", "ask",
       "Ask (JOB_GLOBAL): the catalog row differs from the journal event, or nothing can read it; everything stops",
       "R4.3 2.5 vocabulary"),
)

# -- F: create_folder -------------------------------------------------------------------------------------------------
#   folder  absent | empty | not_empty;  parent  present | absent
KEYSPACE_F = {"journal": JOURNAL_STATES, "folder": ("absent", "empty", "not_empty"), "parent": ("present", "absent")}
TABLE_F = (
    _r("F", "F1", {"journal": ("INTENT",), "folder": ("absent",), "parent": ("present",)},
       "INTERRUPTED_NOT_APPLIED", "finish", "Not applied: re-verify, offer resume", "handoff unit 6 F"),
    _r("F", "F2", {"journal": ("INTENT",), "folder": ("empty",), "parent": ("present",)},
       "APPLIED_UNRECORDED", "record", "Applied, unrecorded: record, then verify", "handoff unit 6 F"),
    _r("F", "F3", {"journal": ("INTENT",), "folder": ("not_empty",), "parent": ("present",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask", "Ask: the folder is there and something is in it", "handoff unit 6 F"),
    _r("F", "F4", {"journal": ("INTENT",), "parent": ("absent",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask", "Ask: the parent folder is missing", "handoff unit 6 F"),
    _r("F", "FX", None, "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. The journal and the disk match none of the expected shapes (table F has rows for the INTENT state only)",
       "R4.3 2.5 vocabulary"),
)

TABLES = {"A": TABLE_A, "C": TABLE_C, "D": TABLE_D, "E": TABLE_E, "F": TABLE_F}
KEYSPACES = {"A": KEYSPACE_A, "C": KEYSPACE_C, "D": KEYSPACE_D, "E": KEYSPACE_E, "F": KEYSPACE_F}

#: Item kind -> table. 'fake' is decided by its params ('op').
TABLE_FOR_KIND = {"move": "A", "rename": "A", "case_only": "A", "quarantine": "A", "restore": "A",
                  "metadata_write": "C", "create_folder": "F"}
TABLE_FOR_FAKE_OP = {"rename": "A", "create_folder": "F", "metadata": "C"}


class ReconcileError(Exception):
    code = "reconcile_error"


class AmbiguousRow(ReconcileError):
    """Two explicit rows cover one key: handoff 8.5. The suite proves it never happens."""
    code = "ambiguous_row"


def all_rows():
    return [r for t in ("A", "C", "D", "E", "F") for r in TABLES[t]]


def row(row_id):
    for r in all_rows():
        if r.id == row_id:
            return r
    raise KeyError(row_id)


def explicit_matches(table, key):
    """Every explicit row of `table` that covers `key` (the X row is never in this list)."""
    return [r for r in TABLES[table] if r.when is not None and all(key[f] in vals for f, vals in r.when.items())]


def classify(table, key):
    """The one row of `table` for `key`. A key that is not in the table's key space is refused."""
    space = KEYSPACES[table]
    if set(key) != set(space) or any(key[f] not in space[f] for f in space):
        raise ReconcileError("key %r is not in the key space of table %s" % (key, table))
    hits = explicit_matches(table, key)
    if len(hits) > 1:
        raise AmbiguousRow("table %s: rows %s all cover %r" % (table, ", ".join(r.id for r in hits), key))
    if hits:
        return hits[0]
    xs = [r for r in TABLES[table] if r.when is None]
    if len(xs) != 1:
        raise ReconcileError("table %s must have exactly one X row" % table)
    return xs[0]


def keyspace(table):
    """Every key of a table's key space (the Cartesian product of its fields' values)."""
    space = KEYSPACES[table]
    fields = list(space)
    return [dict(zip(fields, vals)) for vals in product(*(space[f] for f in fields))]


# ---------------------------------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------------------------------
@dataclass
class Finding:
    table: str
    row_id: str
    key: dict
    recovered_state: str
    outcome: str
    next_action: str
    job_id: int | None = None
    item_id: int | None = None
    position: int | None = None
    action_id: str | None = None
    event_id: int | None = None
    note: str = ""


def _finding(r, key, **kw):
    return Finding(r.table, r.id, dict(key), r.recovered_state, r.outcome, r.next_action, **kw)


def _loads(text, default=None):
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------------------------------
# The item's journal state (read-only)
# ---------------------------------------------------------------------------------------------------
def journal_state(conn, item):
    """(state, before) for the item's CURRENT action, or (None, None) when the item has no action (nothing was ever
    intended, so there is nothing on disk to reconcile). `before` is the C1 evidence snapshot's body (a dict) or None."""
    aid = item["current_action_id"]
    if not aid:
        return None, None
    evs = conn.execute("SELECT kind, detail, evidence_snapshot_id FROM operation_event WHERE item_id = ? AND action_id = ? "
                       "ORDER BY seq", (item["item_id"], aid)).fetchall()
    c1 = [e for e in evs if e[0] == "STEP_INTENT_RECORDED"]
    c2 = [e for e in evs if e[0] == "STEP_RESULT_RECORDED"]
    c3 = [e for e in evs if e[0] == "VERIFICATION_RECORDED"]
    before = None
    if len(c1) == 1 and c1[0][2] is not None:
        snap = conn.execute("SELECT body FROM evidence_snapshot WHERE snapshot_id = ?", (c1[0][2],)).fetchone()
        before = _loads(snap[0]) if snap else None
    if len(c1) != 1 or len(c2) > 1 or len(c3) > 1 or (c3 and not c2):
        return "INCONSISTENT", before
    if c3:
        if (_loads(c3[0][1], {}) or {}).get("state") == "APPLIED_VERIFIED" and (_loads(c2[0][1], {}) or {}).get("status") == "ok":
            return "VERIFIED", before
    if c2:
        return ("RESULT_SUCCESS" if (_loads(c2[0][1], {}) or {}).get("status") == "ok" else "RESULT_FAILURE"), before
    return "INTENT", before


def table_for(item):
    kind = item["kind"]
    if kind == "fake":
        op = (_loads(item["params"], {}) or {}).get("op")
        return TABLE_FOR_FAKE_OP.get(op)
    return TABLE_FOR_KIND.get(kind)


# ---------------------------------------------------------------------------------------------------
# Keys. The observer reports what the disk shows now; the identity rule is here.
# ---------------------------------------------------------------------------------------------------
#: Identity fields that are a display, not identity (the path a file is seen at).
IDENTITY_DISPLAY_FIELDS = ("path",)


def identity_of(ident):
    if ident is None:
        return None
    return {k: v for k, v in ident.items() if k not in IDENTITY_DISPLAY_FIELDS}


def _cmp(observed, snapshot_identity):
    if observed is None:
        return "absent"
    return "match" if identity_of(observed) == identity_of(snapshot_identity) else "differ"


def key_a(jstate, obs, before):
    """obs: {'src': identity or None, 'dst': identity or None, 'same_object': bool or None, 'same_path': bool}."""
    snap = (before or {}).get("identity")
    if jstate != "INCONSISTENT" and not snap:
        jstate = "INCONSISTENT"           # a C1 without the before-identity it must carry
    src, dst = _cmp(obs.get("src"), snap), _cmp(obs.get("dst"), snap)
    if src == "absent" or dst == "absent":
        same = "n/a"
    else:
        same = "yes" if obs.get("same_object") else "no"
    return {"journal": jstate, "shape": "same_path" if obs.get("same_path") else "distinct_paths",
            "src": src, "dst": dst, "same": same}


def key_c(jstate, obs, before, params):
    """obs: {'current': value}. The before-state is the C1 snapshot's 'before_state'; the target is params['target']."""
    b = (before or {}).get("before_state")
    target = (params or {}).get("target")
    cur = obs.get("current")
    if not b:
        return {"journal": jstate, "before": "missing", "current": "target" if cur == target else "neither"}
    if b == target:
        current = "ambiguous"
    elif cur == b:
        current = "before"
    elif cur == target:
        current = "target"
    else:
        current = "neither"
    return {"journal": jstate, "before": "durable", "current": current}


def key_f(jstate, obs):
    """obs: {'folder': 'absent' | 'empty' | 'not_empty', 'parent_present': bool}."""
    return {"journal": jstate, "folder": obs.get("folder"), "parent": "present" if obs.get("parent_present") else "absent"}


def items_summary(states):
    if any(s in JB.NEEDS_DECISION_STATES for s in states):
        return "needs_decision"
    if any(s in ("PLANNED", "PREFLIGHT_READY") for s in states):
        return "runnable"
    return "all_terminal"


# ---------------------------------------------------------------------------------------------------
# Classifiers (all read-only)
# ---------------------------------------------------------------------------------------------------
def classify_item(conn, item, observer):
    """The finding for one item that has an action, or None when it has none (never intended: nothing to reconcile).
    `observer.observe(item, table, params, before)` reports the disk; it must not change it."""
    item = dict(item)
    jstate, before = journal_state(conn, item)
    if jstate is None:
        return None
    table = table_for(item)
    params = _loads(item["params"], {}) or {}
    common = {"job_id": item["job_id"], "item_id": item["item_id"], "position": item["position"],
              "action_id": item["current_action_id"]}
    if table is None:
        k = {"journal": jstate, "shape": "same_path", "src": "absent", "dst": "absent", "same": "n/a"}
        return _finding(row("AX"), k, note="no reconciliation table for item kind %r" % item["kind"], **common)
    obs = observer.observe(item, table, params, before)
    if table == "A":
        k = key_a(jstate, obs, before)
    elif table == "C":
        k = key_c(jstate, obs, before, params)
    else:
        k = key_f(jstate, obs)
    r = classify(table, k)
    return _finding(r, k, note="item #%d (%s)" % (item["position"], item["state"]), **common)


def classify_job(conn, job):
    job = dict(job)
    states = [r[0] for r in conn.execute("SELECT state FROM operation_item WHERE job_id = ?", (job["job_id"],))]
    k = {"job": job["state"], "items": items_summary(states)}
    return _finding(classify("D", k), k, job_id=job["job_id"], note="job %s (%d items)" % (job["job_id"], len(states)))


def classify_projection(conn, projection):
    """Table E over every projecting event and every catalog row. `projection` is a projection.Projection (a
    read-only catalog connection is enough)."""
    if projection is None:
        return []
    out = []
    events = conn.execute(
        "SELECT e.event_id, e.action_id, e.job_id, e.item_id, e.projection_state, e.projection_payload, i.kind AS item_kind, "
        "i.position FROM operation_event e LEFT JOIN operation_item i ON i.item_id = e.item_id "
        "WHERE e.projection_state <> 'NOT_APPLICABLE' ORDER BY e.event_id").fetchall()
    journal_ids = set()
    for ev in events:
        ev = dict(ev)
        journal_ids.add(ev["action_id"])
        k = {"projection": ev["projection_state"], "catalog": projection.observe(ev)}
        out.append(_finding(classify("E", k), k, job_id=ev["job_id"], item_id=ev["item_id"], position=ev["position"],
                            action_id=ev["action_id"], event_id=ev["event_id"], note="action %s" % ev["action_id"]))
    for aid in sorted(projection.catalog_action_ids() - journal_ids):
        k = {"projection": "NONE", "catalog": "present"}
        out.append(_finding(classify("E", k), k, action_id=aid, note="catalog row for unknown action %s" % aid))
    return out


# ---------------------------------------------------------------------------------------------------
# The start-up report: shows, takes no action
# ---------------------------------------------------------------------------------------------------
@dataclass
class Report:
    jobs: list = field(default_factory=list)
    items: list = field(default_factory=list)
    projection: list = field(default_factory=list)

    def all(self):
        return self.jobs + self.items + self.projection

    def needs_person(self):
        return [f for f in self.all() if f.outcome not in ("none",)]

    def lines(self):
        out = []
        for f in self.jobs:
            out.append("job %s [%s] %s -- %s: %s" % (f.job_id, f.row_id, f.recovered_state, f.outcome, f.next_action))
            for g in self.items:
                if g.job_id == f.job_id:
                    out.append("  item #%s [%s] %s -- %s: %s" % (g.position, g.row_id, g.recovered_state, g.outcome, g.next_action))
        for f in self.projection:
            if f.outcome != "none":
                out.append("projection %s [%s] -- %s: %s" % (f.action_id, f.row_id, f.outcome, f.next_action))
        return out


def recover_on_start(conn, observer, projection=None):
    """Classify every job (table D), every item that has an action and is not terminal (tables A, C, F; R4.3 D: "any
    item not in a terminal state ... listed with its row"), and the projection (table E). READ-ONLY: `conn` may be a
    read-only journal connection, the observer only observes, the projection only observes. Takes no action."""
    rep = Report()
    for job in conn.execute("SELECT * FROM job ORDER BY job_id").fetchall():
        rep.jobs.append(classify_job(conn, job))
        for item in conn.execute("SELECT * FROM operation_item WHERE job_id = ? AND current_action_id IS NOT NULL "
                                 "ORDER BY position", (job["job_id"],)).fetchall():
            if item["state"] in JB.TERMINAL_ITEM_STATES:
                continue
            f = classify_item(conn, item, observer)
            if f is not None:
                rep.items.append(f)
    rep.projection = classify_projection(conn, projection)
    return rep
