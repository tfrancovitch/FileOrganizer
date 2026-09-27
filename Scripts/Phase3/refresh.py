r"""The Similar Images page's refresh after an answer, made incremental (B14).

Once a project holds tens of thousands of decisions (B12's folder batches
recorded 73,000), re-reading and re-parsing every decision and re-routing
every decided pair after each keystroke cost ~5 s at real scale (B13's
measurement). P3.R10 §22 (UI-6) left "materialization" open until real
corpus behaviour could decide it; this is the scoped answer for this page.

Two facts make it exact:

  1. The record only grows. A decision row is never changed once written:
     it is withdrawn (a new withdrawal row) or superseded (a new decision
     row); review events are only added. So everything that changed since
     the page last read the record is what lies above four watermarks --
     the newest decision, withdrawal, review event and policy version
     (`evidence.record_marks`).

  2. A change reaches only its neighbourhood. A pair's projection depends
     on its own rows, on decisions about its two images, and on the chain
     guard -- its keeper given up by another pair, which must share that
     image. `pairs.neighbourhood` (Build 7's batch simulation, generalized)
     re-resolves the first ring around what changed exactly. A pair's route
     depends on its projection, its own decisions and events, the evidence
     (unchanged: this is the record-only refresh) and the clock (a deferral
     that returns on a date) -- so the first ring, the changed targets and
     every pair with an open event are re-routed; every other route stands.

What it keeps current is exactly what `refresh_record` always kept current
(the decisions, the events, the pairs' projections and routes); the page's
groups are reloaded where the page reloads them. When something is outside
this reach -- a policy changed, or a decision names a pair or an image the
loaded model does not hold -- `apply` returns None and the page reloads
everything from the database. The suite (`p3_refresh_check.py`) compares
the incremental model with a fresh full load after every step of long
random sessions, and so did the acceptance run on the real collection.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

from Phase2.core import utc_now

from . import evidence as E
from . import pairs as PR
from . import resolve as R
from . import routing as RT
from .model import active_decisions, Decision
from .registry import LOCATION, GROUP, VISUAL_PAIR


def marks(conn) -> dict:
    """Take the watermarks -- BEFORE loading the model they describe (a row
    written in between is then read again by the next `apply`, which skips
    what it already holds, instead of being missed)."""
    return E.record_marks(conn)


def _bounded_decisions(conn, lo, hi):
    return E.load_decisions(conn, "d.decision_id > ? AND d.decision_id <= ?", (int(lo), int(hi)))


def apply(conn, ev, proj, routing, since, now=None):
    """Bring (ev, proj, routing) up to the record as it stands, in place.

    Returns (new marks, stats) -- or None when the change is outside what an
    incremental refresh can prove (then the caller refreshes in full)."""
    now_marks = E.record_marks(conn)
    if (now_marks["policy"] != since["policy"]
            or any(now_marks[k] < since[k] for k in ("decision", "withdrawal", "event"))):
        return None
    new_rows = _bounded_decisions(conn, since["decision"], now_marks["decision"])
    withdrawals = [w for w in E.withdrawals_after(conn, since["withdrawal"]) if w[0] <= now_marks["withdrawal"]]
    new_events = [e for e in E.load_events(conn, after=since["event"]) if int(e.review_event_id) <= now_marks["event"]]

    held = {d.decision_id: i for i, d in enumerate(ev.decisions)}
    decisions = list(ev.decisions)
    for d in new_rows:
        if d.decision_id in held:                        # read already (written while the model was loading)
            decisions[held[d.decision_id]] = d
        else:
            held[d.decision_id] = len(decisions)
            decisions.append(d)
    changed = [d for d in new_rows]
    for _wid, did in withdrawals:
        i = held.get(did)
        if i is None:
            return None                                  # a withdrawal of a row this model never read
        if not decisions[i].withdrawn:
            decisions[i] = replace(decisions[i], withdrawn=True)
        changed.append(decisions[i])
    held_events = {e.review_event_id for e in ev.events}
    events = list(ev.events) + [e for e in new_events if e.review_event_id not in held_events]

    # What changed: pairs directly, and images (a decision on a location, or
    # on an exact-duplicate group's copies, can change a pair's conflicts).
    pair_ids, images = set(), set()
    for x in list(changed) + list(new_events):
        kind, ref = x.target_kind, str(x.target_ref)
        if kind == VISUAL_PAIR:
            if ref not in ev.pairs:
                return None
            pair_ids.add(ref)
        elif kind == LOCATION:
            if ref not in ev.locations:
                return None
            images.add(ref)
        elif kind == GROUP:
            group = ev.groups.get(ref)
            if group is None:
                return None
            images.update(str(m) for m in group.member_ids)
        else:
            return None

    ev.decisions = decisions
    ev.events = events
    ev.now = now or utc_now()
    active = active_decisions(decisions)
    ring1 = set()
    if pair_ids or images:
        sub, ring1 = PR.neighbourhood(ev, pair_ids, images)
        index = R._index_decisions(active)
        fresh = {p.pair_id: p for p in PR.resolve_pairs(sub, index, active) if p.pair_id in ring1}
        proj.pairs = [fresh.get(p.pair_id, p) for p in proj.pairs]
    opened = RT.open_events(events)
    only = set(ring1) | pair_ids | {ref for (kind, ref) in opened if kind == VISUAL_PAIR}
    if any(pid not in routing.pairs for pid in only):
        return None
    by_target = RT._by_target([d for d in active if d.target_kind == VISUAL_PAIR and d.target_ref in only])
    routing.pairs, routing.pair_counts = RT.route_pairs(ev, proj.pairs, ev.now, opened, by_target,
                                                        previous=routing.pairs, only=only)
    return now_marks, {"decisions": len(new_rows), "withdrawals": len(withdrawals), "events": len(new_events),
                       "resolved": len(ring1), "routed": len(only)}


def full(conn, ev, proj, routing, now=None):
    """The refresh as it was through B13 -- every decision and event read
    again, every pair re-resolved (untouched pairs reused), every decided pair
    re-routed. Kept to measure against (the acceptance's "before"). It never
    re-read the policies, so it is NOT the page's fallback: when `apply`
    returns None the page reloads everything from the database."""
    ev.decisions = E.load_decisions(conn)
    ev.events = E.load_events(conn)
    ev.now = now or utc_now()
    active = active_decisions(ev.decisions)
    before = {p.pair_id: p for p in proj.pairs}
    proj.pairs = PR.resolve_pairs(ev, R._index_decisions(active), active, previous=before)
    routing.pairs, routing.pair_counts = RT.route_pairs(ev, proj.pairs, ev.now, previous=routing.pairs)


# -- B18: the Metadata Review queue's in-session cache --------------------------------------
#
# The same two facts, for the metadata slice: the record only grows, so what
# changed since the page last read it lies above the watermarks (plus the
# newest metadata question); and the evidence -- the observations, the
# analyzer results, the capability record -- is one more mark. A change of
# the evidence reloads the slice in full; a change of the record alone is
# caught up by reading only what lies above the marks. The cache is
# REBUILDABLE AND NEVER AUTHORITATIVE (S9): every write goes to the database
# through the store; the cache is only ever read for display, `reset` drops
# it, and a fresh load is always correct. `p3_revalidation_check` compares it
# with a fresh load after every step of random sessions.

def metadata_marks(conn) -> dict:
    """The record watermarks, the newest question, and the metadata evidence
    mark (evidence_mark + the newest analyzer result + the capability record).
    Taken BEFORE a load, like `marks`."""
    m = E.record_marks(conn)
    m["question"] = int(conn.execute("SELECT COALESCE(MAX(question_id), 0) FROM p3_metadata_question").fetchone()[0] or 0)
    caps = conn.execute("SELECT COUNT(*), COALESCE(group_concat(file_family || ':' || semantic_field || ':' || status || ':' "
                        "|| read_support || ':' || write_support, '|'), '') FROM p3_metadata_capability").fetchone()
    m["evidence"] = f"{E.metadata_mark(conn)}; capability {caps[0]}:{hashlib.sha1(caps[1].encode()).hexdigest()[:12]}"
    return m


class MetadataCache:
    """The Metadata Review queue's slice (evidence.load_metadata) and the
    surfaced conflicts (evidence.conflicting_fields), kept for the session.
    `load(conn)` returns (md, conflicts) as a fresh load would, reading as
    little as the watermarks allow. `last` says what the last load did:
    'full', 'hit' or 'catch_up'."""

    def __init__(self):
        self.md = self.conflicts = self.marks = None
        self.last = None

    def reset(self):
        self.md = self.conflicts = self.marks = None

    def load(self, conn):
        now = metadata_marks(conn)
        was = self.marks
        if (self.md is None or was is None or now["evidence"] != was["evidence"]
                or any(now[k] < was[k] for k in ("decision", "withdrawal", "event", "question"))):
            self.md, self.conflicts = E.load_metadata(conn), E.conflicting_fields(conn)
            self.marks, self.last = now, "full"
            return self.md, self.conflicts
        if all(now[k] == was[k] for k in ("decision", "withdrawal", "event", "question")):
            self.marks, self.last = now, "hit"
            return self.md, self.conflicts
        md = self.md
        if now["question"] > was["question"]:
            ids = [r[0] for r in conn.execute("SELECT question_id FROM p3_metadata_question WHERE question_id > ? AND "
                                              "question_id <= ?", (was["question"], now["question"]))]
            md.questions.update(E.load_metadata(conn, ids).questions)
            asked = {(q.location_id, q.semantic_field) for q in md.questions.values()}
            self.conflicts = [q for q in self.conflicts if (q.location_id, q.semantic_field) not in asked]
        new = E.load_decisions(conn, "d.decision_id > ? AND d.decision_id <= ?", (was["decision"], now["decision"]),
                               metadata=True)
        held = {d.decision_id for d in md.decisions}
        added = [d for d in new if d.decision_id not in held]
        gone = {did for wid, did in E.withdrawals_after(conn, was["withdrawal"], metadata=True) if wid <= now["withdrawal"]}
        md.decisions = [replace(d, withdrawn=True) if d.decision_id in gone and not d.withdrawn else d
                        for d in md.decisions] + added
        events = [e for e in E.load_events(conn, after=was["event"], metadata=True) if int(e.review_event_id) <= now["event"]]
        held_e = {e.review_event_id for e in md.events}
        events = [e for e in events if e.review_event_id not in held_e]
        E.resolve_bound_values(conn, added, events)
        md.events = md.events + events
        self.marks, self.last = now, "catch_up"
        return md, self.conflicts


def metadata_fingerprint(md, conflicts) -> dict:
    """The metadata slice in comparable form (the cache's equivalence test)."""
    return {
        "questions": sorted((qid, repr(q)) for qid, q in md.questions.items()),
        "decisions": sorted(_decision_key(d) for d in md.decisions),
        "events": sorted((e.review_event_id, e.target_kind, e.target_ref, e.kind, e.return_kind, e.refers_to,
                          repr(sorted((e.detail or {}).items()))) for e in md.events),
        "conflicts": sorted((q.location_id, q.semantic_field, q.observed_state, repr(q.observed_values)) for q in conflicts),
    }


def _decision_key(d: Decision):
    return (d.decision_id, d.target_kind, d.target_ref, d.kind, repr(d.value), d.origin_kind, d.supersedes,
            d.withdrawn, d.operation_id, d.sequence, repr(sorted((d.binding or {}).items())), d.occurred_utc)


def _route_key(r):
    return (r.primary, tuple((c.route, c.kind, c.detail, tuple(c.decision_ids), c.severity, c.event_id) for c in r.conditions),
            tuple((x.decision_id, x.applicable, tuple(x.changes)) for x in r.drift),
            r.deferral.review_event_id if r.deferral is not None else None, r.deferral_fired, r.deferral_fired_why,
            tuple(sorted((k, tuple(e.review_event_id for e in v)) for k, v in (r.open_events or {}).items())))


def fingerprint(ev, proj, routing) -> dict:
    """What the page shows, in comparable form: the decisions and events it
    holds, every pair's projection and every pair's route, and the counts.
    Two models with equal fingerprints route and draw identically."""
    return {
        "decisions": sorted(_decision_key(d) for d in ev.decisions),
        "events": sorted((e.review_event_id, e.target_kind, e.target_ref, e.kind, e.return_kind, e.refers_to)
                         for e in ev.events),
        "pairs": [repr(sorted(p.as_dict().items())) for p in proj.pairs],
        "routes": {pid: _route_key(r) for pid, r in routing.pairs.items()},
        "counts": dict(routing.pair_counts),
    }
