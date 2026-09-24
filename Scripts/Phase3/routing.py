r"""Review routing -- where a group's attention goes, and why (Build 3).

Queues route attention; they do not create truth. Nothing here changes a
decision or a policy. A route is a pure function of the model: Phase 2
evidence, the decision record, the review events, and the moment it is
evaluated at (`Evidence.now`). The same input gives the same routes; a copy
of the database gives the same routes.

The routes, and the order that decides a target's ONE primary route when
several conditions apply (synthesis §6 -- the handoff's recommendation,
adopted; every condition that applies is still reported on the target):

    1 conflict              contradictory intent -- needs a decision now
    2 needs_revalidation    a decision exists but its evidence moved -- needs a look
    3 blocked               nothing can be decided safely yet -- quiescent
    4 deferred              a person chose not to decide yet -- quiescent
    5 ready_for_plan        resolved, something to plan, no attention needed
    6 resolved              resolved, nothing to plan (every copy kept)
    7 unresolved            the ordinary queue

What each condition rests on:

  conflict            the resolver's conflicts (protected vs redundant, the
                      canonical no longer a keeper, a same-tier policy tie, the
                      last-copy floors)
  needs_revalidation  an active decision whose evidence binding no longer
                      matches current evidence: a bound observation is not the
                      location's current one; a group decision's bound
                      membership is not the current membership
  blocked             evidence: physical identity unknown; a bound copy's
                      fingerprint is stale; a root's latest walk is unusable
                      (interrupted, failed, unavailable, never scanned); or an
                      open system-recorded blocked event of a kind this build
                      cannot compute (a preview blocker is Build 5's)
  deferred            an open deferral event whose return trigger has not
                      fired; a B8 `defer_review` decision counts as an open,
                      manual deferral

Deferred and Blocked differ in origin (a person's choice; the evidence's
condition) and in return: a deferral returns on its own trigger -- a date,
an evidence change, the source coming back, fingerprints becoming current
-- evaluated here, live; the detector (`reconcile`) writes the provenance
rows for what it finds and never decides anything.

Skip is not Defer: a skip is an audit row and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from . import evidence as ev_mod
from . import resolve as R
from .model import Evidence, ReviewEvent, active_decisions
from .registry import (LOCATION, GROUP, EVENT_DEFERRED, EVENT_BLOCKED, EVENT_NEEDS_REVALIDATION, EVENT_RESTORED,
                       RETURN_TIME, RETURN_EVIDENCE_CHANGE, RETURN_SOURCE_AVAILABLE, RETURN_HASH_CURRENT, RETURN_MANUAL)

# -- routes ---------------------------------------------------------------------

CONFLICT = "conflict"
NEEDS_REVALIDATION = "needs_revalidation"
BLOCKED = "blocked"
DEFERRED = "deferred"
READY_FOR_PLAN = "ready_for_plan"
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
ROUTE_ORDER = (CONFLICT, NEEDS_REVALIDATION, BLOCKED, DEFERRED, READY_FOR_PLAN, RESOLVED, UNRESOLVED)

ROUTE_WORDS = {
    CONFLICT: "Conflicts & Exceptions", NEEDS_REVALIDATION: "Needs Revalidation", BLOCKED: "Blocked by Evidence",
    DEFERRED: "Deferred / Snoozed", READY_FOR_PLAN: "Ready for Plan", RESOLVED: "Resolved", UNRESOLVED: "Queue",
}
STATUS_WORDS = {
    CONFLICT: "Conflict", NEEDS_REVALIDATION: "Needs revalidation", BLOCKED: "Blocked", DEFERRED: "Deferred",
    READY_FOR_PLAN: "Ready for plan", RESOLVED: "Resolved",
}

# -- blocker kinds ---------------------------------------------------------------

PHYSICAL_IDENTITY_UNKNOWN = "physical_identity_unknown"
STALE_CONTENT_HASH = "stale_content_hash"
INCOMPLETE_ROOT_COVERAGE = "incomplete_root_coverage"
RECORDED_BLOCK = "recorded"          # an open blocked event of a kind this build cannot compute

#: Which return kind a computed blocker clears on (what the detector records).
BLOCKER_RETURN = {
    PHYSICAL_IDENTITY_UNKNOWN: RETURN_EVIDENCE_CHANGE,
    STALE_CONTENT_HASH: RETURN_HASH_CURRENT,
    INCOMPLETE_ROOT_COVERAGE: RETURN_SOURCE_AVAILABLE,
}
COMPUTED_BLOCKERS = tuple(BLOCKER_RETURN)
_BLOCKER_BY_RETURN = {rk: k for k, rk in BLOCKER_RETURN.items()}

ROUTING_VERSION = "fileorganizer.p3.routing/1"


@dataclass(frozen=True)
class Condition:
    route: str                  # the route this condition argues for
    kind: str                   # conflict kind | blocker kind | deferral return kind | 'drift' | 'coverage_warning' | ...
    detail: str
    event_id: str | None = None
    decision_ids: tuple = ()
    severity: str = ""          # for conflicts: blocking | advisory


@dataclass(frozen=True)
class DecisionDrift:
    decision_id: str
    kind: str
    changes: tuple              # empty: the decision still rests on current evidence

    @property
    def applicable(self):
        return not self.changes


@dataclass
class Route:
    target_kind: str
    target_ref: str
    primary: str
    conditions: tuple           # every Condition that applies, in route order
    drift: tuple = ()           # DecisionDrift per active decision on the target itself
    deferral: ReviewEvent | None = None     # the open deferral, fired or not
    deferral_fired: bool = False
    deferral_fired_why: str = ""
    open_events: dict = field(default_factory=dict)   # kind -> [ReviewEvent] still open on the target

    def has(self, route):
        return any(c.route == route for c in self.conditions)

    def of(self, route):
        return tuple(c for c in self.conditions if c.route == route)

    @property
    def word(self):
        if self.primary == UNRESOLVED:
            return "In progress" if any(c.kind == "in_progress" for c in self.conditions) else "Unreviewed"
        return STATUS_WORDS[self.primary]

    def as_dict(self):
        return {"target_kind": self.target_kind, "target_ref": self.target_ref, "primary": self.primary,
                "conditions": [asdict(c) for c in self.conditions], "drift": [asdict(x) for x in self.drift],
                "deferral": self.deferral.review_event_id if self.deferral else None,
                "deferral_fired": self.deferral_fired}


@dataclass
class RoutingProjection:
    groups: dict                # group_id -> Route
    locations: dict             # location_id -> Route (only locations that carry decisions or events)
    counts: dict
    now: str

    def route(self, group_id):
        return self.groups.get(group_id)


# -- helpers ----------------------------------------------------------------------

def _name(ev: Evidence, location_id):
    loc = ev.locations.get(str(location_id))
    return loc.path.replace("/", "\\").rsplit("\\", 1)[-1] if loc else f"location {location_id}"


def _iso(value):
    """A comparable ISO-UTC prefix (second precision); a bare date is midnight."""
    s = str(value or "").strip()
    if not s:
        return None
    if len(s) == 10:
        s += "T00:00:00"
    return s.replace("Z", "")[:19]


def open_events(events):
    """(target_kind, target_ref) -> kind -> [open ReviewEvent].

    A `restored` event closes the event it refers to, or -- with no
    reference -- every open deferral, blocker and revalidation flag on its
    target. A new deferral replaces an open one (the latest speaks); a new
    blocker replaces an open one of the same return kind. Skips are never
    open: they are audit rows.
    """
    opened = {}
    by_id = {}
    for e in events:
        key = (e.target_kind, str(e.target_ref))
        slot = opened.setdefault(key, {EVENT_DEFERRED: [], EVENT_BLOCKED: [], EVENT_NEEDS_REVALIDATION: []})
        by_id[e.review_event_id] = e
        if e.kind == EVENT_RESTORED:
            if e.refers_to and e.refers_to in by_id:
                target = by_id[e.refers_to]
                tslot = opened.get((target.target_kind, str(target.target_ref)))
                if tslot is not None:
                    for kind in tslot:
                        tslot[kind] = [x for x in tslot[kind] if x.review_event_id != e.refers_to]
            else:
                for kind in slot:
                    slot[kind] = []
        elif e.kind == EVENT_DEFERRED:
            slot[EVENT_DEFERRED] = [e]
        elif e.kind == EVENT_BLOCKED:
            slot[EVENT_BLOCKED] = [x for x in slot[EVENT_BLOCKED] if x.return_kind != e.return_kind] + [e]
        elif e.kind == EVENT_NEEDS_REVALIDATION:
            slot[EVENT_NEEDS_REVALIDATION] = [e]
    return opened


def _pairs_from_binding(binding):
    """[(file_path_id, observation_id)] the binding recorded, ids as text."""
    pairs = []
    b = binding or {}
    if b.get("file_path_id") is not None:
        pairs.append((str(b["file_path_id"]), str(b["observation_id"]) if b.get("observation_id") is not None else None))
    for m in b.get("members") or []:
        if m.get("file_path_id") is not None:
            pairs.append((str(m["file_path_id"]), str(m["observation_id"]) if m.get("observation_id") is not None else None))
    return pairs


def _members_from_binding(binding):
    b = binding or {}
    return {str(m["file_path_id"]) for m in (b.get("members") or []) if m.get("file_path_id") is not None}


def binding_drift(ev: Evidence, target_kind, target_ref, binding) -> tuple:
    """What has changed since a binding was recorded: each bound location's
    current observation against the bound one, and -- for a group target --
    the bound membership against the current membership. Empty: nothing."""
    changes = []
    seen = set()
    for fid, obs in _pairs_from_binding(binding):
        if fid in seen:
            continue
        seen.add(fid)
        loc = ev.locations.get(fid)
        if loc is None:
            changes.append(f"{_name(ev, fid)}: no longer in the current evidence")
        elif obs is not None and loc.observation_id != obs:
            changes.append(f"{_name(ev, fid)}: observed again since (observation {obs} -> {loc.observation_id})")
        elif not loc.present:
            changes.append(f"{_name(ev, fid)}: no longer present")
    if target_kind == GROUP:
        bound = _members_from_binding(binding)
        current = ev.groups.get(str(target_ref))
        if current is None:
            if bound:
                changes.append("the group is no longer a current exact-duplicate group")
        elif bound:
            now = set(current.member_ids)
            for fid in sorted(now - bound, key=lambda x: (ev.locations[x].sort_key if x in ev.locations else (), x)):
                changes.append(f"new member: {_name(ev, fid)}")
            for fid in sorted(bound - now):
                changes.append(f"member left: {_name(ev, fid)}")
    return tuple(changes)


def decision_drift(ev: Evidence, decision) -> DecisionDrift:
    return DecisionDrift(decision.decision_id, decision.kind,
                         binding_drift(ev, decision.target_kind, decision.target_ref, decision.binding))


def _target_locations(ev: Evidence, target_kind, target_ref):
    if target_kind == GROUP:
        g = ev.groups.get(str(target_ref))
        return [ev.locations[i] for i in g.member_ids if i in ev.locations] if g else []
    loc = ev.locations.get(str(target_ref))
    return [loc] if loc else []


def deferral_fired(ev: Evidence, e: ReviewEvent, now=None):
    """Has the deferral's return trigger fired as of `now`? -> (fired, why)."""
    now = _iso(now or ev.now)
    kind = e.return_kind or RETURN_MANUAL
    if kind == RETURN_TIME:
        when = _iso(e.return_on_utc or e.return_condition)
        if when and now and now >= when:
            return True, f"the snooze until {when[:10]} has elapsed"
        return False, ""
    if kind == RETURN_EVIDENCE_CHANGE:
        changes = binding_drift(ev, e.target_kind, e.target_ref, e.detail)
        if changes:
            return True, "the evidence changed: " + "; ".join(changes[:3])
        return False, ""
    locs = _target_locations(ev, e.target_kind, e.target_ref)
    if kind == RETURN_SOURCE_AVAILABLE:
        roots = {l.root_key for l in locs}
        roots_ok = all(ev.roots.get(r) is None or ev.roots[r].available for r in roots)
        if locs and roots_ok and not any(l.cloud_only for l in locs):
            return True, "the source is available again"
        return False, ""
    if kind == RETURN_HASH_CURRENT:
        if locs and all(l.hash_current for l in locs):
            return True, "every fingerprint is current again"
        return False, ""
    return False, ""                                              # manual, preview_available, unknown


def blockers_for(ev: Evidence, target_kind, target_ref, group_proj=None):
    """The computed evidence blockers on a target, plus coverage warnings
    (a completed walk with inaccessible folders warns; it does not block --
    one denied folder must not park every group in the project)."""
    out = []
    locs = _target_locations(ev, target_kind, target_ref)
    if target_kind == GROUP and group_proj is not None and group_proj.location_count and not group_proj.physical_identity_complete:
        missing = [v.location_id for v in group_proj.members if not v.physical_id]
        out.append(Condition(BLOCKED, PHYSICAL_IDENTITY_UNKNOWN,
                             f"physical identity unknown for {len(missing)} cop{'y' if len(missing) == 1 else 'ies'}: "
                             "copies and reclaim cannot be counted"))
    elif target_kind == LOCATION and locs and not locs[0].physical_id:
        out.append(Condition(BLOCKED, PHYSICAL_IDENTITY_UNKNOWN, "physical identity unknown"))
    stale = [l for l in locs if not l.hash_current]
    if stale:
        out.append(Condition(BLOCKED, STALE_CONTENT_HASH,
                             "fingerprint stale for " + ", ".join(_name(ev, l.location_id) for l in stale[:3])
                             + (f" and {len(stale) - 3} more" if len(stale) > 3 else "")))
    hard, warn = [], []
    for root in sorted({l.root_key for l in locs}):
        rc = ev.roots.get(root)
        if rc is None:
            continue
        if not rc.available or rc.detail == "never scanned" or "latest scan" in rc.detail:
            hard.append(f"{root}: {rc.detail}")
        elif not rc.complete:
            warn.append(f"{root}: {rc.detail}")
    if hard:
        out.append(Condition(BLOCKED, INCOMPLETE_ROOT_COVERAGE, "root coverage unusable -- " + "; ".join(hard)))
    if warn:
        out.append(Condition(UNRESOLVED, "coverage_warning", "coverage warnings -- " + "; ".join(warn)))
    return out


# -- the router ---------------------------------------------------------------------

def _primary(conditions):
    present = {c.route for c in conditions}
    for route in ROUTE_ORDER:
        if route in present:
            return route
    return UNRESOLVED


def _deferral_text(e: ReviewEvent):
    kind = e.return_kind or RETURN_MANUAL
    if kind == RETURN_TIME:
        return f"deferred until {(_iso(e.return_on_utc or e.return_condition) or '?')[:10]}"
    return {RETURN_EVIDENCE_CHANGE: "deferred until the evidence changes",
            RETURN_SOURCE_AVAILABLE: "deferred until the source is available",
            RETURN_HASH_CURRENT: "deferred until fingerprints are current",
            "preview_available": "deferred until a preview is available (a later build)",
            RETURN_MANUAL: "deferred indefinitely"}.get(kind, f"deferred ({kind})")


def _by_target(active):
    """(target_kind, target_ref) -> the target's active decisions, in recording order."""
    out = {}
    for d in sorted(active, key=lambda d: (d.sequence, d.decision_id)):
        out.setdefault((d.target_kind, str(d.target_ref)), []).append(d)
    return out


def route_target(ev: Evidence, target_kind, target_ref, group_proj=None, opened=None, active=None, now=None,
                 inherited=(), by_target=None):
    """One target's route: every condition that applies, in route order, and
    the primary among them. `inherited` are a group's members' conditions.
    `by_target` is the active decisions indexed by target (route_all builds
    it once: after a bulk batch there are thousands, and every target asks)."""
    opened = opened if opened is not None else open_events(ev.events)
    if by_target is None:
        by_target = _by_target(active if active is not None else active_decisions(ev.decisions))
    ref = str(target_ref)
    own = by_target.get((target_kind, ref), [])
    conditions = list(inherited)
    slot = opened.get((target_kind, ref), {EVENT_DEFERRED: [], EVENT_BLOCKED: [], EVENT_NEEDS_REVALIDATION: []})

    # 1 conflicts -- the resolver's, for the group or for a member they name
    if group_proj is not None:
        for c in group_proj.conflicts:
            if target_kind == GROUP or ref in c.location_ids:
                conditions.append(Condition(CONFLICT, c.kind, c.message, decision_ids=c.decision_ids, severity=c.severity))

    # 2 needs revalidation -- drift on the target's own active decisions. The
    # live comparison decides; an open flag the detector recorded is carried
    # as provenance and never routes by itself (the detector restores it).
    drift = tuple(decision_drift(ev, d) for d in own)
    flag = slot[EVENT_NEEDS_REVALIDATION][0].review_event_id if slot[EVENT_NEEDS_REVALIDATION] else None
    for x in drift:
        if not x.applicable:
            conditions.append(Condition(NEEDS_REVALIDATION, "drift", f"{x.kind}: " + "; ".join(x.changes),
                                        event_id=flag, decision_ids=(x.decision_id,)))

    # 3 blocked -- computed live; plus open recorded blockers of kinds this build cannot compute
    computed = blockers_for(ev, target_kind, ref, group_proj)
    conditions.extend(computed)
    for e in slot[EVENT_BLOCKED]:
        if _BLOCKER_BY_RETURN.get(e.return_kind) is not None:
            continue                     # a computable kind: the live computation speaks (present above, or cleared)
        conditions.append(Condition(BLOCKED, RECORDED_BLOCK, e.return_condition or (e.return_kind or "blocked"),
                                    event_id=e.review_event_id))

    # 4 deferred -- the open deferral, unless its trigger has fired; a B8 defer_review decision counts
    deferral = slot[EVENT_DEFERRED][0] if slot[EVENT_DEFERRED] else None
    fired, why = deferral_fired(ev, deferral, now) if deferral else (False, "")
    if deferral and not fired:
        conditions.append(Condition(DEFERRED, deferral.return_kind or RETURN_MANUAL, _deferral_text(deferral),
                                    event_id=deferral.review_event_id))
    legacy = [d for d in own if d.kind == "defer_review"]
    if legacy and deferral is None:
        conditions.append(Condition(DEFERRED, RETURN_MANUAL, "deferred indefinitely (a B8 record)",
                                    decision_ids=tuple(d.decision_id for d in legacy)))

    # 5-7 from the resolver, for a group
    if group_proj is not None and target_kind == GROUP:
        if group_proj.ready_for_plan:
            n = len(group_proj.plan_eligible_locations)
            conditions.append(Condition(READY_FOR_PLAN, "ready", f"{n} plan-eligible cop{'y' if n == 1 else 'ies'}"))
        elif group_proj.review_state == R.RESOLVED:
            conditions.append(Condition(RESOLVED, "kept", "every copy is kept; nothing to plan"))
        elif group_proj.review_state == R.IN_PROGRESS:
            conditions.append(Condition(UNRESOLVED, "in_progress", f"{len(group_proj.undecided)} undecided"))
        elif group_proj.review_state == R.UNREVIEWED:
            conditions.append(Condition(UNRESOLVED, "unreviewed", "no decision yet"))

    # One condition once, in route order.
    unique, seen = [], set()
    for c in sorted(conditions, key=lambda c: (ROUTE_ORDER.index(c.route) if c.route in ROUTE_ORDER else 99)):
        key = (c.route, c.kind, c.detail)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    ordered = tuple(unique)
    return Route(target_kind, ref, _primary(ordered), ordered, drift, deferral, fired, why, slot)


def route_all(ev: Evidence, proj: R.ProjectProjection, now=None) -> RoutingProjection:
    """Every group's route, with its members' conditions folded in, plus a
    route for every location that carries a decision or an event of its own.

    A member's revalidation flag or blocker is the group's too. A member's
    deferral parks the copy, not the group -- unless every undecided copy is
    parked. Conflicts are the group's own (the resolver reports them there)."""
    now = now or ev.now
    opened = open_events(ev.events)
    active = active_decisions(ev.decisions)
    by_target = _by_target(active)
    targets = {str(d.target_ref) for d in active if d.target_kind == LOCATION}
    targets |= {str(e.target_ref) for e in ev.events if e.target_kind == LOCATION}
    by_member = {v.location_id: gp for gp in proj.groups for v in gp.members}
    locations = {}
    for lid in sorted(targets, key=lambda x: (ev.locations[x].sort_key if x in ev.locations else (), x)):
        locations[lid] = route_target(ev, LOCATION, lid, by_member.get(lid), opened, active, now, by_target=by_target)
    groups = {}
    for gp in proj.groups:
        inherited = []
        for v in gp.members:
            lr = locations.get(v.location_id)
            if lr is not None:
                inherited.extend(c for c in lr.conditions if c.route in (NEEDS_REVALIDATION, BLOCKED))
        undecided = list(gp.undecided)
        if undecided and all(locations.get(l) is not None and locations[l].has(DEFERRED) for l in undecided):
            inherited.extend(c for l in undecided for c in locations[l].of(DEFERRED))
        groups[gp.group_id] = route_target(ev, GROUP, gp.group_id, gp, opened, active, now, inherited=tuple(inherited), by_target=by_target)
    counts = {route: 0 for route in ROUTE_ORDER}
    for r in groups.values():
        counts[r.primary] += 1
    counts["groups"] = len(groups)
    conditions = {}
    for r in groups.values():
        for c in r.conditions:
            conditions[f"{c.route}:{c.kind}"] = conditions.get(f"{c.route}:{c.kind}", 0) + 1
    counts["conditions"] = conditions
    return RoutingProjection(groups, locations, counts, now)


def ordered(proj: R.ProjectProjection, routing: RoutingProjection, route):
    """The groups whose primary route this is, in the route's own order.

    Conflicts: the most reclaim at stake first, then the oldest unresolved
    (the earliest active decision) -- the stand-in the synthesis asks for
    until a Plan exists. Every other route: display order.
    """
    groups = [g for g in proj.groups if routing.groups[g.group_id].primary == route]
    if route == CONFLICT:
        def key(g):
            r = routing.groups[g.group_id]
            oldest = min((int(d) for c in r.conditions for d in c.decision_ids if str(d).isdigit()), default=10 ** 12)
            return (-(g.potential_reclaim_bytes or 0), oldest, g.group_id)
        groups.sort(key=key)
    return groups


# -- the detector ---------------------------------------------------------------------

def reconcile(conn, store, now=None):
    """Bring the review-event record in line with what the evidence says.

    Runs when the review surface opens and after a collection run. Writes,
    under one system operation:

      needs_revalidation   for a target whose own decisions drifted and that
                           has no open flag
      restored             for an open flag whose drift has cleared (a new
                           decision, a withdrawal); for an open computed
                           blocker that has cleared; for a deferral whose
                           trigger has fired
      blocked              for a computed blocker on a target a person has
                           engaged with -- a decision on it or on one of its
                           copies, or an open deferral -- and no open row for
                           that blocker. A route needs no row to say
                           "blocked"; the row explains why a decision is not
                           moving, without one row per untouched group.

    Never touches a decision. Idempotent: a second run writes nothing.
    Returns the counts written.
    """
    ev = ev_mod.load_evidence(conn)
    if now:
        ev.now = now
    proj = R.resolve_all(ev)
    routing = route_all(ev, proj, ev.now)
    active = active_decisions(ev.decisions)
    engaged = {(d.target_kind, str(d.target_ref)) for d in active}
    for gp in proj.groups:
        if any((LOCATION, v.location_id) in engaged for v in gp.members):
            engaged.add((GROUP, gp.group_id))
    writes = []
    targets = [(GROUP, gid, r) for gid, r in routing.groups.items()] + [(LOCATION, lid, r) for lid, r in routing.locations.items()]
    for target_kind, ref, route in targets:
        slot = route.open_events
        own_drift = [x for x in route.drift if not x.applicable]
        if own_drift and not slot[EVENT_NEEDS_REVALIDATION]:
            changes = [c for x in own_drift for c in x.changes]
            writes.append({"event_kind": EVENT_NEEDS_REVALIDATION, "target_kind": target_kind, "target_ref": ref,
                           "return_kind": RETURN_EVIDENCE_CHANGE, "return_condition": "; ".join(changes[:6]),
                           "detail": {"decision_ids": [x.decision_id for x in own_drift], "changes": changes,
                                      "routing_version": ROUTING_VERSION}})
        elif not own_drift:
            for e in slot[EVENT_NEEDS_REVALIDATION]:
                writes.append({"event_kind": EVENT_RESTORED, "target_kind": target_kind, "target_ref": ref,
                               "refers_to": e.review_event_id,
                               "return_condition": "the decisions rest on current evidence again"})
        computed = {c.kind: c for c in route.conditions if c.route == BLOCKED and c.kind in COMPUTED_BLOCKERS}
        recorded = {_BLOCKER_BY_RETURN.get(e.return_kind): e for e in slot[EVENT_BLOCKED]}
        if (target_kind, ref) in engaged or route.deferral is not None:
            for kind in sorted(set(computed) - set(recorded)):
                writes.append({"event_kind": EVENT_BLOCKED, "target_kind": target_kind, "target_ref": ref,
                               "return_kind": BLOCKER_RETURN[kind], "return_condition": computed[kind].detail,
                               "detail": {"blocker": kind, "routing_version": ROUTING_VERSION}})
        for kind, e in recorded.items():
            if kind is not None and kind not in computed:
                writes.append({"event_kind": EVENT_RESTORED, "target_kind": target_kind, "target_ref": ref,
                               "refers_to": e.review_event_id, "return_condition": f"{kind} has cleared"})
        if route.deferral is not None and route.deferral_fired:
            writes.append({"event_kind": EVENT_RESTORED, "target_kind": target_kind, "target_ref": ref,
                           "refers_to": route.deferral.review_event_id, "return_kind": route.deferral.return_kind,
                           "return_condition": route.deferral_fired_why})
    if writes:
        store.system_events(writes, note=f"routing detector {ROUTING_VERSION}")
    counts = {}
    for w in writes:
        counts[w["event_kind"]] = counts.get(w["event_kind"], 0) + 1
    counts["written"] = len(writes)
    return counts
