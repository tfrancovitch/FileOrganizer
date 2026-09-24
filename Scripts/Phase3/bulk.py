r"""Bulk decisions and the policy workflow (Build 4).

Two of the research's four bulk mechanisms are new here: the explicit
checked selection and the frozen query-result batch. The other two -- a
decision on a whole group, and a dynamic policy -- exist since Builds 1-2.

Everything in this module is a pure function of the model (evidence,
decisions, policies, events, routes): it says what a bulk action WOULD
record for each target in scope, and sorts every target into one of the
research's preview buckets --

    decided             a decision would be recorded
    already_satisfied   an active decision already says this
    preserved           an explicit, incompatible decision stands; a batch never overwrites it
    conflict            recording it would contradict a hard constraint (protection, the
                        last-copy floor), or the group already needs a person
    blocked             the evidence is not fit to decide on (Build 3's blockers)
    not_applicable      nothing to do (no recommendation to accept, say)

-- by running every target through the SAME resolver Builds 1-3 tested
(`resolve.resolve_group`, with the hypothetical decisions in place). There
is no second evaluation path. The store then commits exactly what the
preview showed (`store.commit_bulk`) and freezes the membership; nothing
re-evaluates a batch after it is recorded. A target that comes to match the
same query later is not a member: a snapshot is not a policy (P3-A41).

The policy side: `policy_preview` says what a policy would cover today and
what it would change today, so the window can show that -- and say, in so
many words, that future matching evidence will be evaluated against it --
before the policy exists. F05 is the check: a protect-folder policy made
before a file existed covers the file when it appears, with no decision
recorded for it (`resolve.protected_locations`).

The Show filter the Decide page draws its list from lives here too, as a
pure function (`filter_groups`), so that "apply to the N current matches"
freezes exactly the list the person was looking at.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from Phase2.core import utc_now

from . import BULK_QUERY_SCHEMA
from . import evidence as ev_mod
from . import resolve as R
from . import routing as RT
from .model import Evidence, Decision, PolicyVersion, active_decisions, under, path_key
from .registry import (LOCATION, GROUP, decision_kind, policy_kind, bulk_action, scope_kind as _scope_kind,
                       SOURCE_ROOT, SCOPE_EXPLICIT_SELECTION, DEFER_DISPOSITIONS,
                       DISP_DECIDED, DISP_SATISFIED, DISP_PRESERVED, DISP_CONFLICT, DISP_BLOCKED, DISP_NOT_APPLICABLE,
                       DISPOSITIONS, MODE_PRESERVE)

# -- the Show filter: routes, then lenses -- filters over the same groups ----------

ROUTE_FILTERS = [("Queue", RT.UNRESOLVED), ("Conflicts & Exceptions", RT.CONFLICT), ("Needs Revalidation", RT.NEEDS_REVALIDATION),
                 ("Blocked by Evidence", RT.BLOCKED), ("Deferred / Snoozed", RT.DEFERRED), ("Ready for Plan", RT.READY_FOR_PLAN),
                 ("Resolved", RT.RESOLVED)]
LENSES = ["High Reclaim", "Cross-Root", "Unknown Physical Identity", "Hard-Link Aliases",
          "Filename Divergence", "Extension Divergence", "Policy Tie"]
FILTERS = [name for name, _ in ROUTE_FILTERS] + ["All"] + [f"Lens: {l}" for l in LENSES]
HIGH_RECLAIM_BYTES = 100 * 1024 * 1024

#: Words for the buckets, on the preview and in the Batches list.
DISPOSITION_WORDS = {
    DISP_DECIDED: "decided", DISP_SATISFIED: "already satisfied", DISP_PRESERVED: "preserved (explicit decision stands)",
    DISP_CONFLICT: "conflict -- not recorded", DISP_BLOCKED: "blocked by evidence -- not recorded", DISP_NOT_APPLICABLE: "not applicable",
}


def file_name(path):
    return str(path).replace("/", "\\").rsplit("\\", 1)[-1]


def folder_of(path):
    s = str(path).replace("/", "\\")
    return s.rsplit("\\", 1)[0] if "\\" in s else ""


def _extension(path):
    name = file_name(path)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def filter_groups(proj: R.ProjectProjection, routing: RT.RoutingProjection, show: str) -> list:
    """The groups a Show filter lists: a route (in the route's own order), a
    lens, or All (display order). Presentation sorting is the page's."""
    routes = dict(ROUTE_FILTERS)
    if show in routes:
        return list(RT.ordered(proj, routing, routes[show]))
    groups = list(proj.groups)
    if not str(show).startswith("Lens: "):
        return groups                                              # "All"
    lens = str(show)[len("Lens: "):]
    if lens == "High Reclaim":
        return [g for g in groups if (g.potential_reclaim_bytes or 0) >= HIGH_RECLAIM_BYTES]
    if lens == "Cross-Root":
        return [g for g in groups if len({m.root_key for m in g.members}) > 1]
    if lens == "Unknown Physical Identity":
        return [g for g in groups if not g.physical_identity_complete]
    if lens == "Hard-Link Aliases":
        return [g for g in groups if g.hardlink_aliases]
    if lens == "Filename Divergence":
        return [g for g in groups if len({file_name(m.path).lower() for m in g.members}) > 1]
    if lens == "Extension Divergence":
        return [g for g in groups if len({_extension(m.path) for m in g.members}) > 1]
    if lens == "Policy Tie":
        return [g for g in groups if len(g.policy_preferred) > 1 or any(c.kind == R.SAME_PRECEDENCE_POLICY_TIE for c in g.conflicts)]
    return groups


# -- queries: what a snapshot freezes ------------------------------------------------

def snapshot_query(show=None, folder=None, targets=GROUP, group_ids=None) -> dict:
    """The frozen query a snapshot batch records: the Show filter that listed
    the groups and, for a folder action, the folder -- enough to say later
    what the batch was over, and to ask what would match now."""
    q = {"schema": BULK_QUERY_SCHEMA, "targets": targets}
    if show:
        q["show"] = str(show)
    if group_ids:
        q["group_ids"] = [str(g) for g in group_ids]
    if folder:
        q["folder"] = str(folder)
        q["folder_key"] = path_key(folder)
    return q


def describe_query(query, parameters=None) -> str:
    q = query or {}
    parts = []
    if q.get("show"):
        parts.append(f"Show: {q['show']}")
    if q.get("group_ids"):
        parts.append(f"{len(q['group_ids'])} groups")
    if q.get("folder"):
        parts.append(f"copies under {q['folder']}")
    if q.get("root"):
        parts.append(f"root {q['root']}")
    if q.get("extension"):
        parts.append(f"extension {q['extension']}")
    if not parts and parameters and parameters.get("selection"):
        parts.append(_n(len(parameters["selection"]), "checked group"))
    return "; ".join(parts) or "every current group"


def _n(count, singular, plural=None):
    """'1 group' / '2 groups' / '1 copy' / '2 copies'."""
    if plural is None:
        plural = singular[:-1] + "ies" if singular.endswith("y") else singular + "s"
    return f"{count:,} {singular if count == 1 else plural}"


def _matches(loc, criteria) -> bool:
    if criteria.get("folder_key") or criteria.get("folder"):
        if not under(loc.path_key, path_key(criteria.get("folder_key") or criteria.get("folder"))):
            return False
    if criteria.get("root") and str(criteria["root"]).lower() != str(loc.root_key).lower():
        return False
    if criteria.get("extension"):
        ext = str(criteria["extension"]).lower().lstrip(".")
        if _extension(loc.path) != ext:
            return False
    return True


def scope_groups(query, ev: Evidence, proj: R.ProjectProjection, routing: RT.RoutingProjection) -> list:
    """The groups a query's group scope names: an explicit list, a Show
    filter, or -- with neither -- every current group."""
    q = query or {}
    if q.get("group_ids"):
        return [proj.group(str(g)) for g in q["group_ids"] if proj.group(str(g)) is not None]
    if q.get("show"):
        return filter_groups(proj, routing, q["show"])
    return list(proj.groups)


def query_targets(query, ev: Evidence, proj: R.ProjectProjection, routing: RT.RoutingProjection):
    """What a query matches NOW -> (target_kind, [refs]). This is the
    question a frozen batch never asks again: its membership is what this
    returned at commit, recorded, and a later match (F04's future.tmp) is
    not a member."""
    q = query or {}
    groups = scope_groups(q, ev, proj, routing)
    criteria = {k: q[k] for k in ("folder", "folder_key", "root", "extension") if q.get(k)}
    if q.get("targets") == LOCATION or criteria:
        if q.get("show") or q.get("group_ids"):
            locs = [ev.locations[v.location_id] for gp in groups for v in gp.members if v.location_id in ev.locations]
        else:
            locs = list(ev.locations.values())      # no group scope at all: every location the model holds
        # Display order, then id: the same answer from the same rows in any order.
        locs = sorted((l for l in locs if _matches(l, criteria)), key=lambda l: (l.sort_key, l.location_id))
        return LOCATION, [l.location_id for l in locs]
    return GROUP, [gp.group_id for gp in groups]


# -- planning: what an action would record ---------------------------------------------

@dataclass
class Candidate:
    """One batch member and what the action would record for it."""
    target_kind: str
    target_ref: str
    decisions: tuple                    # (kind, target_kind, target_ref, value) the action plans
    group_id: str = ""
    label: str = ""
    events: tuple = ()                  # (disposition, until) for a deferral
    disposition: str = ""               # set by classify()
    detail: str = ""
    outcomes: tuple = ()                # per planned decision/event: (disposition, detail)
    bindings: dict = field(default_factory=dict)    # (kind, target_kind, target_ref) -> evidence binding, for the commit

    @property
    def to_record(self):
        """The planned decisions that classify() left as 'decided'."""
        return tuple(d for d, (disp, _why) in zip(self.decisions, self.outcomes[:len(self.decisions)]) if disp == DISP_DECIDED)

    @property
    def events_to_record(self):
        offset = len(self.decisions)
        return tuple(e for e, (disp, _why) in zip(self.events, self.outcomes[offset:]) if disp == DISP_DECIDED)

    def as_dict(self):
        d = asdict(self)
        d.pop("bindings", None)
        return d


def _group_label(gp: R.GroupProjection) -> str:
    names = sorted({file_name(m.path) for m in gp.members}, key=str.lower)
    return names[0] + (f" (+{len(names) - 1} other name{'s' if len(names) > 2 else ''})" if len(names) > 1 else "") if names else gp.group_id


def _location_label(ev: Evidence, lid) -> str:
    loc = ev.locations.get(str(lid))
    return loc.path if loc else f"location {lid}"


def plan(action, params, groups, ev: Evidence, proj: R.ProjectProjection, routing: RT.RoutingProjection) -> list:
    """One Candidate per batch member: what the action would record for it,
    before any of it is judged against what already stands."""
    spec = bulk_action(action)
    params = params or {}
    out = []
    if spec.needs_folder:
        folder_key = path_key(params.get("folder_key") or params.get("folder") or "")
        if not folder_key:
            raise ValueError(f"{spec.label} needs a folder")
        kind = spec.decision_kinds[0]
        for gp in groups:
            for v in gp.members:
                loc = ev.locations.get(v.location_id)
                if loc is None or not under(loc.path_key, folder_key):
                    continue
                out.append(Candidate(LOCATION, v.location_id, ((kind, LOCATION, v.location_id, True),), gp.group_id, loc.path))
        return out
    for gp in groups:
        label = _group_label(gp)
        if action == "keep_all":
            out.append(Candidate(GROUP, gp.group_id, (("keep_all_group", GROUP, gp.group_id, True),), gp.group_id, label))
        elif action == "defer":
            disposition = params.get("disposition") or "defer_indefinitely"
            if disposition not in DEFER_DISPOSITIONS:
                raise ValueError(f"unknown deferral disposition: {disposition!r}")
            out.append(Candidate(GROUP, gp.group_id, (), gp.group_id, label, events=((disposition, params.get("until")),)))
        elif action == "accept_recommendation":
            suggested = gp.suggested_canonical
            if suggested is None:
                out.append(Candidate(GROUP, gp.group_id, (), gp.group_id, label, disposition=DISP_NOT_APPLICABLE,
                                     detail="no unique policy recommendation for this group"))
                continue
            decisions = [("canonical_location", GROUP, gp.group_id, suggested)]
            left = {"protected": 0, "kept": 0, "already redundant": 0}
            if params.get("mark_others", True):
                for v in gp.members:
                    if v.location_id == suggested:
                        continue
                    if v.status == R.UNDECIDED:
                        decisions.append(("redundant_location", LOCATION, v.location_id, True))
                    elif v.status == R.PROTECTED:
                        left["protected"] += 1
                    elif v.status == R.KEEPER:
                        left["kept"] += 1
                    else:
                        left["already redundant"] += 1
            note = ", ".join(f"{n} {what}" for what, n in left.items() if n)
            out.append(Candidate(GROUP, gp.group_id, tuple(decisions), gp.group_id, label, detail=("left alone: " + note) if note else ""))
        else:
            raise ValueError(f"unknown bulk action: {action!r}")
    return out


# -- classification: every target through the same resolver ----------------------------

class _Overlay:
    """The decision index with a few targets' slots replaced -- so a group can
    be resolved with hypothetical decisions in place without copying the
    whole index per group."""
    __slots__ = ("base", "extra")

    def __init__(self, base, extra):
        self.base, self.extra = base, extra

    def get(self, key, default=None):
        if key in self.extra:
            return self.extra[key]
        return self.base.get(key, default)


def _same_value(a, b):
    return str(a) == str(b) if not isinstance(a, dict) else a == b


def _classify_one(kind, tk, ref, value, gp, route, index, routing):
    """One planned decision against what stands -- before the simulation."""
    spec = decision_kind(kind)
    tdec = index.get((tk, ref), {})
    if any(_same_value(d.value, value) for d in tdec.get(kind, [])):
        return DISP_SATISFIED, f"{spec.label} is already recorded"
    domain = spec.domain_key(value)
    for k, ds in tdec.items():
        for d in ds:
            if decision_kind(d.kind).domain_key(d.value) == domain:
                return DISP_PRESERVED, f"{decision_kind(d.kind).label} (#{d.decision_id}) stands"
    # Cross-domain explicit intent the one-click path withdraws after asking; a batch never withdraws.
    if gp is not None:
        gdec = index.get((GROUP, gp.group_id), {})
        if kind == "redundant_location":
            if gp.canonical == ref:
                return DISP_PRESERVED, "it is the group's canonical (decided)"
            if gdec.get("keep_all_group"):
                return DISP_PRESERVED, "Keep all is in force on the group"
        elif kind == "keep_all_group":
            marks = [v.location_id for v in gp.members if index.get((LOCATION, v.location_id), {}).get("redundant_location")]
            if marks:
                return DISP_PRESERVED, (f"{len(marks)} redundant marks stand on the group" if len(marks) != 1
                                        else "a redundant mark stands on the group")
        elif kind == "canonical_location":
            if index.get((LOCATION, str(value)), {}).get("redundant_location"):
                return DISP_PRESERVED, "the recommended copy carries a redundant mark"
    if route is not None:
        blocking = [c for c in route.of(RT.CONFLICT) if c.severity == R.BLOCKING]
        if blocking:
            return DISP_CONFLICT, "the group already needs a person: " + blocking[0].detail
    lr = routing.locations.get(str(ref)) if tk == LOCATION else None
    for r in (route, lr):
        if r is not None and r.has(RT.BLOCKED):
            return DISP_BLOCKED, r.of(RT.BLOCKED)[0].detail
    return DISP_DECIDED, ""


def _simulate(ev: Evidence, gp: R.GroupProjection, index, planned) -> dict:
    """Resolve the group again with the planned decisions in place. Returns
    {planned decision -> message} for each one a NEW blocking conflict names."""
    group = ev.groups.get(gp.group_id)
    if group is None or not planned:
        return {}
    extra = {}
    ids = {}
    for i, (kind, tk, ref, value) in enumerate(planned):
        key = (tk, ref)
        slot = extra.get(key) or {k: list(v) for k, v in index.get(key, {}).items()}
        hid = f"planned-{i}"
        ids[hid] = (kind, tk, ref, value)
        slot.setdefault(kind, []).append(Decision(hid, tk, ref, kind, value, "bulk_explicit_human", None, False, None, 10 ** 9 + i))
        extra[key] = slot
    after = R.resolve_group(ev, group, _Overlay(index, extra), [])
    before = {(c.kind, c.location_ids, c.reason) for c in gp.conflicts if c.severity == R.BLOCKING}
    out = {}
    for c in after.conflicts:
        if c.severity != R.BLOCKING or (c.kind, c.location_ids, c.reason) in before:
            continue
        named = [ids[d] for d in c.decision_ids if d in ids]
        if not named:
            named = [p for p in planned if p[1] == LOCATION and p[2] in c.location_ids]
        for p in named:
            out.setdefault(p, c.message)
    return out


_PRIORITY = (DISP_PRESERVED, DISP_CONFLICT, DISP_BLOCKED, DISP_SATISFIED, DISP_NOT_APPLICABLE)


def classify(candidates, ev: Evidence, proj: R.ProjectProjection, routing: RT.RoutingProjection):
    """Sort every candidate into its bucket, in place. Preserve, never
    overwrite: an explicit incompatible decision on a target stops the
    batch's decision for that target and is counted, not dropped."""
    active = active_decisions(ev.decisions)
    index = R._index_decisions(active)
    by_group = {g.group_id: g for g in proj.groups}
    per_group = {}
    pending = []
    for cand in candidates:
        if cand.disposition:
            cand.outcomes = tuple((cand.disposition, cand.detail) for _ in cand.decisions) or ((cand.disposition, cand.detail),)
            continue
        gp = by_group.get(cand.group_id)
        route = routing.groups.get(cand.group_id)
        outcomes = [_classify_one(kind, tk, ref, value, gp, route, index, routing) for (kind, tk, ref, value) in cand.decisions]
        # A candidate's first decision gates the rest (the canonical gates the
        # marks that go with it): when the person's own canonical stands, the
        # recommendation is not applied around it piecemeal.
        if len(outcomes) > 1 and outcomes[0][0] not in (DISP_DECIDED, DISP_SATISFIED):
            outcomes = [outcomes[0]] + [(outcomes[0][0], f"{outcomes[0][1]} (gates the rest)") for _ in outcomes[1:]]
        for i, (d, (disp, _why)) in enumerate(zip(cand.decisions, outcomes)):
            if disp == DISP_DECIDED and gp is not None:
                per_group.setdefault(gp.group_id, []).append((cand, i, d))
        pending.append((cand, route, outcomes))
    # The simulation is per GROUP, with every decision the batch plans for
    # that group in place at once: the last-copy floor is a group property
    # (marking every copy under a folder redundant must trip it), and so is
    # the canonical.
    for gid, items in per_group.items():
        hits = _simulate(ev, by_group[gid], index, [d for _cand, _i, d in items])
        for cand, i, d in items:
            if d in hits:
                for _c, _r, outcomes in pending:
                    if _c is cand:
                        outcomes[i] = (DISP_CONFLICT, hits[d])
    for cand, route, outcomes in pending:
        for disposition, _until in cand.events:
            if route is not None and route.deferral is not None and not route.deferral_fired:
                outcomes.append((DISP_SATISFIED, "already deferred"))
            elif route is not None and route.has(RT.DEFERRED):
                outcomes.append((DISP_SATISFIED, "already deferred (a B8 record)"))
            else:
                outcomes.append((DISP_DECIDED, DEFER_DISPOSITIONS[disposition][1]))
        cand.outcomes = tuple(outcomes)
        disps = [o[0] for o in outcomes]
        if DISP_DECIDED in disps:
            cand.disposition = DISP_DECIDED
            others = [f"{decision_kind(d[0]).label if i < len(cand.decisions) else 'deferral'}: {why}"
                      for i, (d, (disp, why)) in enumerate(zip(cand.decisions + cand.events, outcomes)) if disp != DISP_DECIDED]
            n = disps.count(DISP_DECIDED)
            head = f"{n} decision{'s' if n != 1 else ''}" if cand.decisions else "deferral"
            cand.detail = "; ".join([head] + others + ([cand.detail] if cand.detail else []))
        else:
            for want in _PRIORITY:
                if want in disps:
                    cand.disposition = want
                    cand.detail = next(why for disp, why in outcomes if disp == want)
                    break


# -- the preview ---------------------------------------------------------------------------

@dataclass
class BulkPreview:
    action: str
    parameters: dict
    scope_kind: str
    query: dict | None
    scope_size: int                 # groups the scope named
    candidates: list
    evaluated_utc: str = ""
    mode: str = MODE_PRESERVE
    record_mark: int | None = None  # the last operation id when previewed: the commit refuses if the record moved

    @property
    def counts(self) -> dict:
        c = {d: 0 for d in DISPOSITIONS}
        for cand in self.candidates:
            c[cand.disposition] = c.get(cand.disposition, 0) + 1
        return c

    @property
    def decision_count(self) -> int:
        return sum(len(c.to_record) for c in self.candidates if c.disposition == DISP_DECIDED)

    @property
    def event_count(self) -> int:
        return sum(len(c.events_to_record) for c in self.candidates if c.disposition == DISP_DECIDED)

    @property
    def member_word(self) -> str:
        return "copies" if bulk_action(self.action).member_kind == LOCATION else "groups"

    def samples(self, disposition, n=3):
        return [(c.label, c.detail) for c in self.candidates if c.disposition == disposition][:n]

    def lines(self) -> list:
        """The research's preview: categorized counts, samples for the buckets
        that hide problems, and the one sentence that is always true."""
        spec = bulk_action(self.action)
        c = self.counts
        n = len(self.candidates)
        out = [f"{_n(n, 'copy' if spec.member_kind == LOCATION else 'group')} in scope ({describe_query(self.query, self.parameters)})"]
        if spec.records_events:
            out.append(f"  -> {c[DISP_DECIDED]:,} would be deferred")
        else:
            kinds = " + ".join(decision_kind(k).label for k in spec.decision_kinds)
            out.append(f"  -> {c[DISP_DECIDED]:,} would receive a decision ({_n(self.decision_count, 'decision')}: {kinds})")
        out.append(f"  -> {c[DISP_SATISFIED]:,} already satisfy it")
        out.append(f"  -> {c[DISP_PRESERVED]:,} have an explicit incompatible decision, preserved (caution)")
        out.append(f"  -> {c[DISP_CONFLICT]:,} would conflict -- not recorded; needs a person (conflict)")
        out.append(f"  -> {c[DISP_BLOCKED]:,} are blocked by evidence -- not recorded (blocked)")
        if c[DISP_NOT_APPLICABLE]:
            out.append(f"  -> {c[DISP_NOT_APPLICABLE]:,} not applicable")
        for disp in (DISP_PRESERVED, DISP_CONFLICT, DISP_BLOCKED, DISP_NOT_APPLICABLE):
            for label, why in self.samples(disp):
                out.append(f"       {DISPOSITION_WORDS[disp]}: {label} -- {why}")
        out.append("No source files will be changed.")
        return out

    def as_dict(self) -> dict:
        return {"action": self.action, "parameters": self.parameters, "scope_kind": self.scope_kind, "query": self.query,
                "scope_size": self.scope_size, "members": len(self.candidates), "counts": self.counts,
                "decisions": self.decision_count, "events": self.event_count, "mode": self.mode,
                "evaluated_utc": self.evaluated_utc,
                "samples": {d: self.samples(d) for d in DISPOSITIONS if self.counts.get(d)}}


def preview(ev: Evidence, proj: R.ProjectProjection, routing: RT.RoutingProjection, action, params, scope_kind,
            query=None, selection=(), conn=None) -> BulkPreview:
    """The categorized preview of one bulk action over one scope. With a
    connection, the evidence binding of every decision that would be
    recorded is captured now, so the commit records what was previewed --
    if the evidence moves between preview and commit, the revalidation
    detector will say so afterwards (synthesis §7)."""
    _scope_kind(scope_kind)
    spec = bulk_action(action)
    params = dict(params or {})
    if spec.needs_folder:
        folder = str(params.get("folder") or "").strip()
        if not folder:
            raise ValueError(f"{spec.label} needs a folder")
        params["folder"], params["folder_key"] = folder, path_key(folder)
    if scope_kind == SCOPE_EXPLICIT_SELECTION:
        ids = [str(g) for g in selection]
        if not ids:
            raise ValueError("nothing is checked")
        params["selection"] = ids
        groups = [proj.group(g) for g in ids if proj.group(g) is not None]
        query = None
    else:
        if not query:
            raise ValueError("a snapshot needs the query that lists its matches")
        query = dict(query)
        query.setdefault("schema", BULK_QUERY_SCHEMA)
        if spec.needs_folder:
            query.update(targets=LOCATION, folder=params["folder"], folder_key=params["folder_key"])
        groups = scope_groups(query, ev, proj, routing)
    candidates = plan(action, params, groups, ev, proj, routing)
    classify(candidates, ev, proj, routing)
    mark = None
    if conn is not None:
        for cand in candidates:
            if cand.disposition != DISP_DECIDED:
                continue
            for kind, tk, ref, _value in cand.to_record:
                cand.bindings[(kind, tk, ref)] = ev_mod.binding_for(conn, tk, ref)
        mark = record_mark(conn)
    return BulkPreview(action, params, scope_kind, query, len(groups), candidates, utc_now(), record_mark=mark)


def record_mark(conn) -> int:
    """Where the decision record stands: the newest operation id. A preview
    carries it; the commit refuses when the record has moved since, so a
    batch is never applied over a record the person did not see."""
    row = conn.execute("SELECT MAX(operation_id) FROM p3_operation").fetchone()
    return int(row[0] or 0)


# -- the policy workflow: what a policy would cover and change today ------------------------

def _covered_file_locations(conn, spec, scope):
    """Every present file location the policy's scope covers, from Phase 2's
    file_state -- group member or not. (count, samples)"""
    rows = conn.execute(
        "SELECT sr.root_path, sr.root_path_key, fp.relative_path FROM file_state fs "
        "  JOIN file_path fp ON fp.file_path_id = fs.file_path_id "
        "  JOIN source_root sr ON sr.source_root_id = fs.source_root_id "
        " WHERE fs.state = 'present' AND sr.is_active = 1").fetchall()
    count, samples = 0, []
    if spec.scope_kind == SOURCE_ROOT:
        want = str(scope.get("root_key", "")).lower()
        for r in rows:
            key = str(r["root_path_key"] or path_key(r["root_path"])).lower()
            if key == want:
                count += 1
                if len(samples) < 3:
                    samples.append(ev_mod.full_path(r["root_path"], r["relative_path"]))
    else:
        folder_key = path_key(scope.get("path_key") or scope.get("path") or "")
        for r in rows:
            full = ev_mod.full_path(r["root_path"], r["relative_path"])
            if under(path_key(full), folder_key):
                count += 1
                if len(samples) < 3:
                    samples.append(full)
    return count, samples


def policy_preview(conn, kind, scope, effect=None) -> dict:
    """What a policy would cover today, what it would change today, and the
    statement the window must make before creating it: future matching
    evidence WILL be evaluated against it. Computed by resolving every group
    with the policy in place and diffing against the projection without it
    -- the same resolver, nothing new."""
    spec = policy_kind(kind)
    scope = spec.validate_scope(scope)
    effect = spec.validate_effect(effect)
    hyp = PolicyVersion("preview", "preview", kind, scope, effect, 1, True)
    ev = ev_mod.load_evidence(conn)
    before = R.resolve_all(ev)
    ev2 = Evidence(ev.locations, ev.groups, ev.decisions, list(ev.policies) + [hyp], ev.events, ev.roots, ev.now)
    after = R.resolve_all(ev2)
    covered_total, covered_samples = _covered_file_locations(conn, spec, scope)
    covered_members = [lid for lid, loc in ev.locations.items() if loc.in_current_group and R._policy_covers(hyp, loc)]
    groups_touched = {ev.locations[lid].content_id for lid in covered_members}
    changes = []
    totals = {"newly_protected": 0, "new_conflicts": 0, "recommendations": 0, "ready_changes": 0}
    for g0 in before.groups:
        g1 = after.group(g0.group_id)
        if g1 is None:
            continue
        diffs = []
        s0 = {v.location_id: v.status for v in g0.members}
        s1 = {v.location_id: v.status for v in g1.members}
        newly = [l for l in s1 if s1[l] == R.PROTECTED and s0.get(l) != R.PROTECTED]
        if newly:
            totals["newly_protected"] += len(newly)
            diffs.append(f"{len(newly)} cop{'y becomes' if len(newly) == 1 else 'ies become'} protected")
        c0 = {(c.kind, c.location_ids) for c in g0.conflicts}
        new_conflicts = [c for c in g1.conflicts if (c.kind, c.location_ids) not in c0]
        if new_conflicts:
            totals["new_conflicts"] += len(new_conflicts)
            diffs.append("; ".join(f"new {'conflict' if c.severity == R.BLOCKING else 'exception'}: {c.kind.replace('_', ' ')}" for c in new_conflicts))
        if g0.effective_canonical != g1.effective_canonical or g0.canonical_origin != g1.canonical_origin:
            totals["recommendations"] += 1
            v0 = g0.verdict(g0.effective_canonical) if g0.effective_canonical else None
            v1 = g1.verdict(g1.effective_canonical) if g1.effective_canonical else None
            diffs.append(f"recommendation: {file_name(v0.path) if v0 else 'none'} -> {file_name(v1.path) if v1 else 'none'}")
        elif g0.policy_preferred != g1.policy_preferred:
            totals["recommendations"] += 1
            diffs.append("the ranking of the copies changes")
        if g0.ready_for_plan != g1.ready_for_plan:
            totals["ready_changes"] += 1
            diffs.append("ready for a plan" if g1.ready_for_plan else "no longer ready for a plan")
        if diffs:
            changes.append((_group_label(g0), "; ".join(diffs)))
    scope_text = scope.get("root_path") or scope.get("path") or ""
    lines = [f"{spec.label}: {scope_text}" + (f" (over {effect['over']})" if effect.get("over") else ""),
             f"Covers {covered_total:,} current file location{'s' if covered_total != 1 else ''} now"
             f" ({len(covered_members):,} of them in {len(groups_touched):,} duplicate group{'s' if len(groups_touched) != 1 else ''})."]
    if changes:
        bits = []
        if totals["newly_protected"]:
            bits.append(f"{totals['newly_protected']:,} cop{'y becomes' if totals['newly_protected'] == 1 else 'ies become'} protected")
        if totals["new_conflicts"]:
            bits.append(f"{totals['new_conflicts']:,} new conflict{'s' if totals['new_conflicts'] != 1 else ''} or exception{'s' if totals['new_conflicts'] != 1 else ''}")
        if totals["recommendations"]:
            bits.append(f"{totals['recommendations']:,} recommendation{'s' if totals['recommendations'] != 1 else ''} change")
        if totals["ready_changes"]:
            bits.append(f"{totals['ready_changes']:,} group{'s' if totals['ready_changes'] != 1 else ''} change plan readiness")
        lines.append(f"Today, {len(changes):,} group{'s' if len(changes) != 1 else ''} change: " + "; ".join(bits) + ".")
        for label, what in changes[:3]:
            lines.append(f"    {label}: {what}")
    else:
        lines.append("Today, no group's resolution changes.")
    lines.append("Future matching evidence WILL be evaluated against this policy: a file that appears under this "
                 f"{'root' if spec.scope_kind == SOURCE_ROOT else 'folder'} later is covered too, with no decision recorded for it. "
                 "This is a policy, not a snapshot -- to affect only what is listed now, use a bulk action instead.")
    return {"kind": kind, "label": spec.label, "scope": scope, "effect": effect, "scope_text": scope_text,
            "covers_locations": covered_total, "covers_samples": covered_samples, "covers_members": len(covered_members),
            "groups_touched": len(groups_touched), "groups_changed": len(changes), "changes": changes, "totals": totals,
            "lines": lines}
