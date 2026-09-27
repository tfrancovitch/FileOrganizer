r"""The Plan Builder (Phase 3, Build 8): proposed logical intent, previewed
against project evidence, frozen, fingerprinted -- and nothing more.

P3.R7 is the source, scoped by P3.R10 §15. A Plan is a versioned snapshot of
proposed future intent derived from current Decisions and Policies (P3-A85).
It is never an action log and never a guarantee: locking freezes reviewed
intent, not the filesystem (P3-A87). Phase 3 stores the logical desired
effect -- remove_location, "this location should no longer exist" -- and
never an execution method (P3-A88). Phase 4, which does not exist, owns how.

Everything in this module is a pure function of the model (evidence,
decisions, policies, projections, routes), like `resolve.py` and `bulk.py`.
It reads no source file and writes nothing; `store.py` persists what it
returns.

  build(ev, proj, routing)      the draft: one Plan Item per logical action,
                                with its decision, policy and evidence links,
                                its declared preconditions and postconditions,
                                its dependencies, and -- the Phase 3 Evidence
                                Preview -- each evidence-stage precondition's
                                result and the item's state.
  fingerprint(items)            SHA-256 over the canonical payload (P3-A96).
  obsolescence(...)             which dependencies of a locked revision have
                                changed (P3-A97): its own, never the project's.
  diff(old, new)                what changed between two revisions (R7 §34).

What the Plan Builder does NOT decide (P3-A98). Its population is every
exact-duplicate group and visual pair whose DECISIONS already resolve it
ready for a plan -- the Ready-for-Plan route's own criterion; it never
re-decides eligibility, never chooses a keeper where none is decided, never
resolves a disagreement between decisions. Where decisions disagree about
an image -- one pair prefers another image over it, while a second pair (or
a group, or an explicit Keep) keeps it: the question B13 carried forward --
the removal is generated and BLOCKED on a hard retention precondition,
`no_retaining_decision`, naming every decision that keeps the image. It is
neither dropped nor silently allowed.

The Evidence Preview checks only what project evidence can answer: the
decisions are current, no conflict stands, the evidence is fresh, a keeper is
decided and present, protection and every other decision permit removal,
the minimum location and physical-copy counts hold. For a hard precondition,
`unknown` blocks exactly like `fail` (P3-A91). Everything only the live
filesystem could answer -- the file still exists as observed, its bytes still
match, permissions, a free action lock, a rollback route -- is DECLARED for
Phase 4's Live Preflight and not evaluated here; every preview says so.

Build 9 adds Update Source Metadata items: one per metadata question with an
active desired set or clear, after the retention items. Each is previewed
for a certified writer (the capability record's status; none is certified,
so every such item stays blocked) and for the two integrity blockers
metadata.py answers with no plan at all. Project metadata and Leave
unchanged propose no source action and are never items.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict

from . import resolve as R
from . import routing as RT
from . import metadata as MD
from .model import Evidence, active_decisions, active_policies
from .registry import (LOCATION, GROUP, VISUAL_PAIR, ACTION_REMOVE, ACTION_MOVE, ACTION_RENAME, ACTION_METADATA,
                       ACTION_PRESERVE, ACTION_KINDS, ACTION_IMPACT, ACTION_PRECONDITIONS, ACTION_POSTCONDITIONS,
                       PRECONDITIONS, STAGE_EVIDENCE, STAGE_LIVE, ITEM_READY, ITEM_BLOCKED,
                       ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE, PREVIEW_DISCLOSURE, ACTION_WORDS,
                       METADATA_QUESTION, METADATA_FIELDS, blocked_reason)
from . import EVIDENCE_BINDING_SCHEMA

PLAN_PAYLOAD_SCHEMA = "fileorganizer.p3.plan-payload/1"


@dataclass
class Precondition:
    key: str
    klass: str
    severity: str
    stage: str
    params: dict = field(default_factory=dict)
    result: str | None = None               # None: a live-stage condition, not evaluated in Phase 3
    detail: str = ""


@dataclass
class PlanItem:
    item_no: int
    action_kind: str
    target_kind: str
    target_ref: str
    sources: tuple                          # ((source_kind, source_ref), ...) that produced it
    desired_effect: dict
    decision_ids: tuple = ()                # decisions that justify it
    retaining_ids: tuple = ()               # decisions that keep its target -- they block a removal
    policy_version_ids: tuple = ()
    evidence_binding: dict = field(default_factory=dict)
    preconditions: list = field(default_factory=list)
    postconditions: tuple = ()
    depends_on: tuple = ()                  # item_nos
    estimated_effect: dict = field(default_factory=dict)
    impact_class: str = "low"
    state: str = ITEM_READY
    blocked_reason: str | None = None

    @property
    def source_kind(self):
        return self.sources[0][0] if self.sources else None

    @property
    def source_ref(self):
        return self.sources[0][1] if self.sources else None

    def condition(self, key, stage=STAGE_EVIDENCE):
        return next((c for c in self.preconditions if c.key == key and c.stage == stage), None)

    @property
    def blocking(self):
        """The precondition whose result settled a blocked item (its
        blocked_reason is that key, or -- Build 9 -- the reason it names)."""
        if not self.blocked_reason:
            return None
        return next((c for c in self.preconditions if c.stage == STAGE_EVIDENCE and c.result
                     and blocked_reason(c.key, c.result) == self.blocked_reason), None)

    @property
    def warnings(self):
        return [c for c in self.preconditions if c.stage == STAGE_EVIDENCE and c.severity == "warning"
                and c.result not in ("pass", "not_applicable")]


@dataclass
class PlanDraft:
    items: list
    summary: dict
    excluded: dict

    def lines(self):
        return summary_lines(self.summary)


# -- helpers --------------------------------------------------------------------------------

def _name(ev: Evidence, lid):
    loc = ev.locations.get(str(lid))
    return loc.path.replace("/", "\\").rsplit("\\", 1)[-1] if loc else f"location {lid}"


def _id_order(x):
    return (0, int(x), "") if str(x).isdigit() else (1, 0, str(x))


def _ids(xs):
    return tuple(sorted({str(x) for x in xs if x is not None}, key=_id_order))


def _group(proj, group_id):
    """proj.group, indexed once per group list (proj.group scans: once per
    planned removal, that was quadratic at the real collection's size)."""
    cache = getattr(proj, "_plan_group_index", None)
    if cache is None or cache[0] is not proj.groups:
        cache = (proj.groups, {g.group_id: g for g in proj.groups})
        proj._plan_group_index = cache
    return cache[1].get(str(group_id))


def _group_of(ev: Evidence, proj, lid):
    loc = ev.locations.get(str(lid))
    if loc is None or not loc.in_current_group or loc.content_id is None:
        return None
    return _group(proj, loc.content_id)


def retaining_decisions(ev: Evidence, proj, index, pairs_by_image, lid) -> list:
    """Every current decision, anywhere, that keeps this image: an explicit
    Keep on it; its exact-duplicate group keeping it (Keep all, its canonical,
    an explicit Keep); any visual pair keeping it (Keep both, its canonical,
    a related variant's default Keep both). [(decision_id, why)] -- the
    no_retaining_decision precondition's evidence (synthesis §6)."""
    lid = str(lid)
    out = []
    for d in index.get((LOCATION, lid), {}).get("must_keep_location", []):
        out.append((d.decision_id, "an explicit Keep on this copy"))
    gp = _group_of(ev, proj, lid)
    if gp is not None:
        # The group's own rows keep every member (Keep all) or this one (its
        # canonical); the resolver's verdict names only the copy's own rows.
        gdec = index.get((GROUP, gp.group_id), {})
        for d in gdec.get("keep_all_group", []):
            out.append((d.decision_id, "its exact-duplicate group keeps all its copies (Keep all)"))
        for d in gdec.get("canonical_location", []):
            if str(d.value) == lid:
                out.append((d.decision_id, "it is its exact-duplicate group's canonical"))
        v = gp.verdict(lid)
        if v is not None and v.status == R.KEEPER:
            for did in v.decision_ids:
                out.append((did, f"its exact-duplicate group keeps it ({v.rule or 'keeper'})"))
    for pp in pairs_by_image.get(lid, ()):
        if lid not in pp.keeper_set or pp.exact_content:
            continue
        other = pp.b_id if pp.a_id == lid else pp.a_id
        if pp.canonical == lid and pp.canonical_decision_id:
            out.append((pp.canonical_decision_id, f"pair #{pp.pair_id} prefers it over {_name(ev, other)}"))
        if pp.keep_both and pp.keep_both_decision_id:
            out.append((pp.keep_both_decision_id, f"pair #{pp.pair_id} keeps both it and {_name(ev, other)}"))
        elif pp.default_retention and pp.relationship_decision_id:
            out.append((pp.relationship_decision_id, f"pair #{pp.pair_id}: a related variant of {_name(ev, other)} "
                                                     "(both kept by default)"))
    seen, unique = set(), []
    for did, why in out:
        if did not in seen:
            seen.add(did)
            unique.append((did, why))
    return sorted(unique, key=lambda x: _id_order(x[0]))


def _location_binding(ev: Evidence, lid, keepers=(), source=None):
    """The evidence an item rests on, ids only -- the p3_decision binding
    shape (`routing.binding_drift` reads it): the target's observation and
    content identity, each keeper's observation, and the source (group or
    pair) with its members' observations."""
    loc = ev.locations.get(str(lid))

    def obs(x):
        o = ev.locations[x].observation_id if x in ev.locations else None
        return int(o) if o is not None and str(o).isdigit() else o
    b = {"schema": EVIDENCE_BINDING_SCHEMA, "target_kind": LOCATION,
         "file_path_id": int(lid) if str(lid).isdigit() else lid, "observation_id": obs(str(lid)),
         "content_id": (int(loc.content_id) if loc is not None and str(loc.content_id).isdigit()
                        else (loc.content_id if loc is not None else None)),
         "members": [{"file_path_id": int(k) if str(k).isdigit() else k, "observation_id": obs(str(k))}
                     for k in sorted({str(k) for k in keepers}, key=_id_order)]}
    if source is not None:
        kind, ref, members = source
        b["source"] = {"target_kind": kind, "target_ref": str(ref),
                       "members": [{"file_path_id": int(m) if str(m).isdigit() else m, "observation_id": obs(str(m))}
                                   for m in sorted({str(x) for x in members}, key=_id_order)]}
    return b


# -- build ----------------------------------------------------------------------------------

def population(proj, routing):
    """What the Plan Builder reads: every group and pair whose decisions
    resolve it ready for a plan (the Ready-for-Plan route's criterion), and
    what it leaves out and why. A target a person deferred stays out (their
    "not yet"); one the evidence blocks or whose decisions need revalidation
    comes in, and the item says so -- visible and blocked, not silently
    missing."""
    groups, pairs, excluded = [], [], {"deferred": 0, "conflict": 0}
    for gp in proj.groups:
        if not gp.ready_for_plan:
            continue
        route = routing.groups.get(gp.group_id)
        primary = route.primary if route is not None else RT.READY_FOR_PLAN
        if primary == RT.DEFERRED:
            excluded["deferred"] += 1
        elif primary == RT.CONFLICT:
            excluded["conflict"] += 1
        else:
            groups.append(gp)
    for pp in getattr(proj, "pairs", ()):
        if not pp.ready_for_plan:
            continue
        route = routing.pairs.get(pp.pair_id) if getattr(routing, "pairs", None) else None
        primary = route.primary if route is not None else RT.READY_FOR_PLAN
        if primary == RT.DEFERRED:
            excluded["deferred"] += 1
        elif primary == RT.CONFLICT:
            excluded["conflict"] += 1
        else:
            pairs.append(pp)
    return groups, pairs, excluded


def build(ev: Evidence, proj, routing) -> PlanDraft:
    """The draft revision from the current state: items, links, preconditions,
    the evidence preview's results, dependencies and estimated effects."""
    active = active_decisions(ev.decisions)
    index = R._index_decisions(active)
    policies = active_policies(ev.policies)
    pairs_by_image = {}
    for pp in getattr(proj, "pairs", ()):
        pairs_by_image.setdefault(pp.a_id, []).append(pp)
        pairs_by_image.setdefault(pp.b_id, []).append(pp)
    groups, pairs, excluded = population(proj, routing)

    removes, preserves = {}, {}          # location id -> dict of what we know

    def want_remove(lid, source, keepers, decisions, route):
        r = removes.setdefault(lid, {"sources": [], "keepers": set(), "decisions": set(), "routes": []})
        r["sources"].append(source)
        r["keepers"].update(keepers)
        r["decisions"].update(decisions)
        r["routes"].append(route)

    def want_preserve(lid, source, why, decisions, policies_):
        p = preserves.setdefault(lid, {"sources": [], "why": [], "decisions": set(), "policies": set()})
        p["sources"].append(source)
        if why not in p["why"]:
            p["why"].append(why)
        p["decisions"].update(decisions)
        p["policies"].update(policies_)

    for gp in groups:
        src = (GROUP, gp.group_id, tuple(v.location_id for v in gp.members))
        route = routing.groups.get(gp.group_id)
        group_decisions = set(gp.canonical_decision_id and [gp.canonical_decision_id] or [])
        for v in gp.members:
            if v.status == R.REDUNDANT:
                want_remove(v.location_id, src, gp.keeper_set, set(v.decision_ids) | group_decisions, route)
            elif v.status in (R.KEEPER, R.PROTECTED):
                why = ("protected" if v.status == R.PROTECTED else
                       "the group's canonical" if gp.canonical == v.location_id else "a keeper of its group")
                want_preserve(v.location_id, src, why, v.decision_ids, v.protected_by)
    for pp in pairs:
        src = (VISUAL_PAIR, pp.pair_id, (pp.a_id, pp.b_id))
        route = routing.pairs.get(pp.pair_id) if getattr(routing, "pairs", None) else None
        decisions = {d for d in (pp.relationship_decision_id, pp.canonical_decision_id) if d}
        want_remove(pp.redundant_candidate, src, (pp.canonical,), decisions, route)
        want_preserve(pp.canonical, src, f"preferred by pair #{pp.pair_id}", decisions, ())

    def display(lid):
        loc = ev.locations.get(str(lid))
        return (loc.sort_key if loc is not None else (), _id_order(lid))

    items = []
    order = sorted(set(removes) | set(preserves), key=display)
    for lid in order:
        if lid in preserves:
            p = preserves[lid]
            items.append(PlanItem(0, ACTION_PRESERVE, LOCATION, lid, tuple((s[0], s[1]) for s in p["sources"]),
                                  {"effect": "retain", "path": ev.locations[lid].path if lid in ev.locations else None,
                                   "why": p["why"]},
                                  decision_ids=_ids(p["decisions"]), policy_version_ids=_ids(p["policies"]),
                                  evidence_binding=_location_binding(ev, lid),
                                  impact_class=ACTION_IMPACT[ACTION_PRESERVE], state=ITEM_PRESERVE))
        if lid in removes:
            r = removes[lid]
            items.append(PlanItem(0, ACTION_REMOVE, LOCATION, lid, tuple((s[0], s[1]) for s in r["sources"]),
                                  {"effect": "location_absent", "path": ev.locations[lid].path if lid in ev.locations else None},
                                  decision_ids=_ids(r["decisions"]),
                                  evidence_binding=_location_binding(ev, lid, r["keepers"], r["sources"][0]),
                                  postconditions=ACTION_POSTCONDITIONS[ACTION_REMOVE],
                                  impact_class=ACTION_IMPACT[ACTION_REMOVE]))
    for n, it in enumerate(items, 1):
        it.item_no = n
    by_target = {(it.action_kind, it.target_ref): it for it in items}

    for it in items:
        if it.action_kind != ACTION_REMOVE:
            continue
        r = removes[it.target_ref]
        keepers = sorted({str(k) for k in r["keepers"] if k is not None and str(k) != it.target_ref}, key=_id_order)
        it.depends_on = tuple(sorted(by_target[(ACTION_PRESERVE, k)].item_no for k in keepers
                                     if (ACTION_PRESERVE, k) in by_target))
        _declare(it)
        _preview_remove(it, ev, proj, index, policies, pairs_by_image, r, keepers, by_target)
        _settle(it)
        loc = ev.locations.get(it.target_ref)
        it.estimated_effect = {"logical_bytes": loc.size_bytes if loc else None,
                               "physical_id": loc.physical_id if loc else None,
                               "root_key": loc.root_key if loc else None}
    # Build 9: an Update Source Metadata item for every active desired set or
    # clear -- after the retention items, so a revision with none numbers and
    # fingerprints exactly as Build 8's did. Project metadata and Leave
    # unchanged propose no source action and are never items (counted only).
    items.extend(metadata_items(ev, len(items) + 1))
    _metadata_notes(items, ev)
    left = sum(1 for d in MD.current_decisions(MD.active_metadata(ev)).values() if d.kind == "leave_source_metadata_unchanged")
    project = sum(1 for d in MD.active_metadata(ev) if d.kind == "project_metadata_set")
    if left:
        excluded["left_unchanged"] = left
    if project:
        excluded["project_metadata"] = project
    summary = summarize(items, ev, proj, excluded)
    return PlanDraft(items, summary, excluded)


def metadata_items(ev: Evidence, first_no=1) -> list:
    """One Update Source Metadata item per question with an active desired set
    or clear, in location display order then field order. Logical intent only
    ("this field should say X" / "should have no value"); never how."""
    order = {k: i for i, k in enumerate(METADATA_FIELDS)}

    def display(qd):
        q = qd[0]
        loc = ev.locations.get(q.location_id)
        return (loc.sort_key if loc is not None else (), _id_order(q.location_id), order.get(q.semantic_field, 99))
    items = []
    for n, (q, d) in enumerate(sorted(MD.planned(ev), key=display), first_no):
        loc = ev.locations.get(q.location_id)
        effect = {"effect": "metadata_value", "field": q.semantic_field,
                  "operation": "set" if d.kind == "desired_source_metadata_set" else "clear",
                  "path": loc.path if loc is not None else None}
        if d.kind == "desired_source_metadata_set":
            effect["value"] = d.value
        binding = _location_binding(ev, q.location_id)
        binding["question"] = {"question_id": q.question_id, "semantic_field": q.semantic_field,
                               "analyzer_result_ids": list(q.analyzer_result_ids), "observed_state": q.observed_state}
        it = PlanItem(n, ACTION_METADATA, LOCATION, q.location_id, ((METADATA_QUESTION, q.question_id),), effect,
                      decision_ids=_ids([d.decision_id]), evidence_binding=binding)
        _declare(it)
        _preview_metadata(it, ev, q, d)
        _settle(it)
        it.estimated_effect = {"field": q.semantic_field, "observed_state": q.observed_state,
                               "observed_value": q.observed_value, "observed_values": list(q.observed_values),
                               "file_family": q.file_family, "logical_bytes": 0}
        items.append(it)
    return items


OVERLAP_KEY = "no_removal_metadata_overlap"


def _metadata_notes(items, ev: Evidence):
    """Two R8 consequences a person should see before locking (B17).

    S1 (R8 §47): a metadata write changes the file's bytes, so a file in a
    current exact-duplicate group would leave it. An informational note on
    the item (in its estimated effect), never a precondition, never a block.

    S2 (R8 §48): the same revision removes a location AND updates its
    metadata. A WARNING on both items (warnings never block), naming the
    other; neither is reordered or dropped. Attached only where it applies,
    so a revision without metadata items is exactly Build 8's."""
    removals = {it.target_ref: it for it in items if it.action_kind == ACTION_REMOVE}
    for it in items:
        if it.action_kind != ACTION_METADATA:
            continue
        loc = ev.locations.get(it.target_ref)
        if loc is not None and loc.in_current_group and loc.content_id is not None:
            it.estimated_effect.setdefault("notes", []).append(
                f"If written, this file will leave exact-duplicate group {loc.content_id}.")
        rm = removals.get(it.target_ref)
        if rm is not None:
            for mine, other, words in ((it, rm, "removes this location"), (rm, it, "updates this location's metadata")):
                c = Precondition(OVERLAP_KEY, PRECONDITIONS[OVERLAP_KEY], "warning", STAGE_EVIDENCE,
                                 {"other_item": other.item_no})
                c.result, c.detail = "fail", (f"item {other.item_no} of this revision {words} -- a metadata update on a "
                                              "location the plan removes (P3.R8 §48); neither item is reordered or dropped")
                mine.preconditions.append(c)


def _preview_metadata(it, ev, q, d):
    """The Evidence Preview of a metadata item: the target as last observed,
    the decision still resting on that evidence, the writer (the capability
    record's status), and the two integrity blockers -- the same checks
    metadata.blocked_by_evidence answers with no plan at all."""
    loc = ev.locations.get(q.location_id)
    if loc is None:
        _set(it, "target_exists", "unknown", "not in the current evidence")
    else:
        _set(it, "target_exists", "pass" if loc.present else "fail",
             "present at the last scan" if loc.present else "not present at the last scan", observation_id=loc.observation_id)
    drift = MD.question_drift(q, d)
    _set(it, "decision_current", "fail" if drift else "pass",
         ("needs revalidation: " + drift[0]) if drift else "the decision rests on the evidence as it is now")
    result, detail = MD.writer_check(q)
    cap = q.capability
    _set(it, "writer_supported", result, detail, file_family=q.file_family, semantic_field=q.semantic_field,
         capability_status=cap.status if cap is not None else "unknown")
    for key, (res, why) in MD.integrity(q).items():
        _set(it, key, res, why)
    if loc is not None and loc.cloud_only:
        _set(it, "source_local_if_required", "fail", "cloud-only at the last scan: Phase 4 would need it local")
    else:
        _set(it, "source_local_if_required", "pass", "local at the last scan")


def _declare(it: PlanItem):
    it.preconditions = [Precondition(key, PRECONDITIONS[key], severity, stage)
                        for key, severity, stage in ACTION_PRECONDITIONS[it.action_kind]]
    it.postconditions = ACTION_POSTCONDITIONS[it.action_kind]
    it.impact_class = ACTION_IMPACT[it.action_kind]


def _set(it, key, result, detail="", **params):
    c = it.condition(key)
    c.result, c.detail = result, detail
    if params:
        c.params.update({k: v for k, v in params.items() if v is not None})


def _preview_remove(it, ev, proj, index, policies, pairs_by_image, r, keepers, by_target):
    lid = it.target_ref
    loc = ev.locations.get(lid)
    routes = [x for x in r["routes"] if x is not None]
    # Identity: the target as the project last observed it (live existence is Phase 4's).
    if loc is None:
        _set(it, "target_exists", "unknown", "not in the current evidence")
    else:
        _set(it, "target_exists", "pass" if loc.present else "fail",
             "present at the last scan" if loc.present else "not present at the last scan", observation_id=loc.observation_id)
    # Evidence: the decisions still rest on current evidence; no conflict; fresh evidence.
    stale = [c for x in routes for c in x.of(RT.NEEDS_REVALIDATION)]
    _set(it, "decision_current", "fail" if stale else "pass",
         ("needs revalidation: " + stale[0].detail) if stale else "the decisions rest on the evidence as it is now")
    conflicts = [c for x in routes for c in x.of(RT.CONFLICT) if c.severity == R.BLOCKING]
    _set(it, "no_current_conflict", "fail" if conflicts else "pass",
         conflicts[0].detail if conflicts else "no conflict stands on its group or pair")
    blocked = [c for x in routes for c in x.of(RT.BLOCKED)]
    stale_kinds = (RT.STALE_CONTENT_HASH, RT.STALE_IMAGE_ANALYSIS)
    hard_stale = [c for c in blocked if c.kind in stale_kinds]
    unknowable = [c for c in blocked if c.kind in (RT.INCOMPLETE_ROOT_COVERAGE, RT.RECORDED_BLOCK)]
    if loc is not None and not loc.hash_current:
        hard_stale.append(RT.Condition(RT.BLOCKED, RT.STALE_CONTENT_HASH, "its fingerprint rests on an older observation"))
    if hard_stale:
        _set(it, "required_coverage_complete", "fail", hard_stale[0].detail)
    elif unknowable:
        _set(it, "required_coverage_complete", "unknown", unknowable[0].detail)
    else:
        _set(it, "required_coverage_complete", "pass", "fingerprints current; the roots were fully scanned")
    # Retention: protection, every other decision, a decided keeper, the counts.
    if loc is None:
        _set(it, "target_not_protected", "unknown", "not in the current evidence")
    else:
        covering, in_force, overridden = R.location_protection(policies, index, loc)
        it.policy_version_ids = _ids(list(it.policy_version_ids) + [p.policy_version_id for p in covering])
        if in_force:
            _set(it, "target_not_protected", "fail", f"protected by {in_force[0].kind.replace('_', ' ')} "
                 f"(policy version {in_force[0].policy_version_id})")
        else:
            _set(it, "target_not_protected", "pass",
                 f"protection overridden by decision #{overridden[0][1]}" if overridden else "no protection covers it")
    retaining = retaining_decisions(ev, proj, index, pairs_by_image, lid)
    it.retaining_ids = _ids(did for did, _why in retaining)
    if retaining:
        _set(it, "no_retaining_decision", "fail",
             "; ".join(f"#{did}: {why}" for did, why in retaining[:4])
             + (f" (and {len(retaining) - 4} more)" if len(retaining) > 4 else "")
             + " -- the decisions disagree about this copy; a person must resolve it (the Plan Builder never picks a side)",
             decisions=list(it.retaining_ids))
    else:
        _set(it, "no_retaining_decision", "pass", "no current decision anywhere keeps this copy")
    present = [k for k in keepers if k in ev.locations and ev.locations[k].present]
    planned_away = [k for k in keepers if (ACTION_REMOVE, k) in by_target]
    if not keepers:
        _set(it, "keeper_exists", "fail", "no keeper is decided -- a person must choose one (the Plan Builder never does)")
    elif planned_away:
        _set(it, "keeper_exists", "fail", f"its keeper {_name(ev, planned_away[0])} is itself planned for removal "
                                          f"(item {by_target[(ACTION_REMOVE, planned_away[0])].item_no})",
             keepers=keepers)
    elif not present:
        _set(it, "keeper_exists", "fail", "no decided keeper is present at the last scan", keepers=keepers)
    else:
        _set(it, "keeper_exists", "pass", "kept: " + ", ".join(_name(ev, k) for k in present[:3]), keepers=present)
    group_sources = [s for s in r["sources"] if s[0] == GROUP]
    if group_sources:
        gp = _group(proj, group_sources[0][1])
        members = [v.location_id for v in gp.members] if gp is not None else []
        remaining = [m for m in members if m != lid and (ACTION_REMOVE, m) not in by_target
                     and m in ev.locations and ev.locations[m].present]
        _set(it, "minimum_location_count_satisfied", "pass" if remaining else "fail",
             f"{len(remaining)} location(s) of this content remain" if remaining else "no location of this content would remain",
             minimum=1)
        if gp is None or gp.physical_copies is None or not gp.physical_identity_complete:
            unknown = [v.location_id for v in (gp.members if gp is not None else []) if not v.physical_id]
            _set(it, "minimum_physical_copy_count_satisfied", "unknown",
                 "physical identity unknown for " + (", ".join(_name(ev, u) for u in unknown[:3]) or "a copy")
                 + ": whether a physical copy would remain cannot be counted from the evidence", minimum=1)
        else:
            objects = {}
            for v in gp.members:
                objects.setdefault(v.physical_id, []).append(v.location_id)
            left = [pid for pid, names in objects.items()
                    if any(n != lid and (ACTION_REMOVE, n) not in by_target for n in names)]
            _set(it, "minimum_physical_copy_count_satisfied", "pass" if left else "fail",
                 f"{len(left)} physical cop{'y' if len(left) == 1 else 'ies'} would remain", minimum=1)
    else:
        _set(it, "minimum_location_count_satisfied", "not_applicable",
             "a visual pair: the two images are different files; the keeper's presence is keeper_exists")
        _set(it, "minimum_physical_copy_count_satisfied", "not_applicable",
             "a visual pair: the two images are different files; the keeper's presence is keeper_exists")
    # Environment, as project evidence records it: a cloud-only copy.
    if loc is not None and loc.cloud_only:
        _set(it, "source_local_if_required", "fail", "cloud-only at the last scan: Phase 4 would need it local to "
                                                      "verify its bytes before acting")
    else:
        _set(it, "source_local_if_required", "pass", "local at the last scan")


def _settle(it: PlanItem):
    """The item's state from its evidence-stage hard results: fail blocks,
    unknown blocks as evidence (never Pass), unsupported is an unsupported
    action. Warnings never block."""
    hard = [c for c in it.preconditions if c.stage == STAGE_EVIDENCE and c.severity == "hard"]
    failed = [c for c in hard if c.result == "fail"]
    unknown = [c for c in hard if c.result == "unknown"]
    unsupported = [c for c in hard if c.result == "unsupported"]
    pending = [c for c in hard if c.result is None]
    if pending:
        raise ValueError(f"item {it.item_no}: evidence-stage precondition {pending[0].key} was not evaluated")
    if failed:
        it.state, it.blocked_reason = ITEM_BLOCKED, blocked_reason(failed[0].key, "fail")
    elif unknown:
        it.state, it.blocked_reason = ITEM_BLOCKED_EVIDENCE, blocked_reason(unknown[0].key, "unknown")
    elif unsupported:
        it.state, it.blocked_reason = ITEM_UNSUPPORTED, blocked_reason(unsupported[0].key, "unsupported")
    else:
        it.state, it.blocked_reason = ITEM_READY, None


# -- items the current decision vocabulary does not produce yet ------------------------------

def logical_item(ev: Evidence, action, lid, desired_effect, decision_ids=(), item_no=1) -> PlanItem:
    """A move or rename item, previewed from project evidence. No decision
    kind asks for one yet, so the builder does not generate them; the
    vocabulary, the preconditions and the preview exist so the schema carries
    all five kinds (R10 §15). (Metadata items are Build 9's, made from
    desired-metadata decisions by build() -- not here.)"""
    if action not in (ACTION_MOVE, ACTION_RENAME):
        raise ValueError(f"{action} is built from decisions by build(), not here")
    loc = ev.locations.get(str(lid))
    it = PlanItem(item_no, action, LOCATION, str(lid), (), dict(desired_effect), decision_ids=_ids(decision_ids),
                  evidence_binding=_location_binding(ev, str(lid)))
    _declare(it)
    if loc is None:
        _set(it, "target_exists", "unknown", "not in the current evidence")
    else:
        _set(it, "target_exists", "pass" if loc.present else "fail",
             "present at the last scan" if loc.present else "not present at the last scan")
    _set(it, "decision_current", "pass" if decision_ids else "fail",
         "rests on current decisions" if decision_ids else "no decision asks for it -- the Plan Builder never invents intent")
    _set(it, "no_current_conflict", "pass", "no conflict stands on the location")
    dest = str(desired_effect.get("destination") or "")
    if not dest:
        _set(it, "destination_collision_absent", "fail", "no destination recorded")
    else:
        taken = [l for l in ev.locations.values() if l.present and l.path.lower() == dest.lower() and l.location_id != str(lid)]
        _set(it, "destination_collision_absent", "fail" if taken else "pass",
             f"the evidence already holds a file there ({taken[0].path})" if taken
             else "no file known to the evidence is at the destination", destination=dest)
    _settle(it)
    return it


# -- effects and the preview ------------------------------------------------------------------

def summarize(items, ev: Evidence, proj, excluded) -> dict:
    """R7 §24-26: counts, blocked and warnings, and the evidence-based effects
    -- the Phase 3 metric (Plan-Eligible Reclaimable Bytes) beside, never in
    place of, Phase 2's (Potentially Reclaimable Exact-Duplicate Bytes)."""
    counts = {a: 0 for a in ACTION_KINDS}
    states = {}
    for it in items:
        counts[it.action_kind] += 1
        states[it.state] = states.get(it.state, 0) + 1
    ready = [it for it in items if it.action_kind == ACTION_REMOVE and it.state == ITEM_READY]
    removed = {it.target_ref for it in ready}
    # Plan-eligible: each physical object whose every name the ready items remove, once.
    by_physical = {}
    for loc in ev.locations.values():
        if loc.physical_id:
            by_physical.setdefault(loc.physical_id, []).append(loc.location_id)
    counted, eligible, unknown = set(), 0, False
    exact_part = pair_part = 0
    for it in ready:
        loc = ev.locations.get(it.target_ref)
        if loc is None or loc.size_bytes is None or not loc.physical_id:
            unknown = True
            continue
        names = by_physical.get(loc.physical_id, [loc.location_id])
        if loc.physical_id in counted or not all(n in removed for n in names):
            continue
        counted.add(loc.physical_id)
        eligible += loc.size_bytes
        if it.source_kind == GROUP:
            exact_part += loc.size_bytes
        else:
            pair_part += loc.size_bytes
    potential = [g.potential_reclaim_bytes for g in proj.groups]
    phase2 = None if any(p is None for p in potential) else sum(potential)
    return {
        "items": len(items), "actions": counts, "states": states,
        "blocked": sum(v for k, v in states.items() if k in (ITEM_BLOCKED, ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED)),
        "warnings": sum(1 for it in items if it.warnings),
        "locations_removed": len(ready),
        "logical_bytes_affected": sum((ev.locations[t].size_bytes or 0) for t in removed if t in ev.locations),
        "plan_eligible_reclaimable_bytes": None if unknown else eligible,
        "plan_eligible_from_exact_duplicates": exact_part, "plan_eligible_from_visual_pairs": pair_part,
        "phase2_potentially_reclaimable_bytes": phase2,
        "source_roots_affected": sorted({ev.locations[t].root_key for t in removed if t in ev.locations}),
        "excluded": dict(excluded),
        "undecided_left": sum(len(g.undecided) for g in proj.groups if g.ready_for_plan),
        **_note_counts(items),
    }


def _note_counts(items):
    """(B17) The S1 / S2 counts -- present only when non-zero, so a summary
    without them reads exactly as before."""
    leave = sum(1 for it in items if it.action_kind == ACTION_METADATA and it.estimated_effect.get("notes"))
    overlap = sum(1 for it in items if it.action_kind == ACTION_METADATA and it.condition(OVERLAP_KEY) is not None)
    out = {}
    if leave:
        out["metadata_leaves_group"] = leave
    if overlap:
        out["metadata_on_removed_location"] = overlap
    return out


def _bytes(n):
    if n is None:
        return "unknown"
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024


def summary_lines(s) -> list:
    """The preview as R7 §26 shapes it, with §27's coverage and §14's disclosure."""
    a = s["actions"]
    lines = ["Proposed logical actions:"]
    for kind in ACTION_KINDS:
        if a.get(kind):
            lines.append(f"  {a[kind]:,} {ACTION_WORDS[kind]}")
    st = s["states"]
    lines.append(f"Ready: {st.get(ITEM_READY, 0):,} · Blocked: {s['blocked']:,}"
                 + (f" ({st.get(ITEM_BLOCKED_EVIDENCE, 0):,} on unknown evidence)" if st.get(ITEM_BLOCKED_EVIDENCE) else "")
                 + f" · With warnings: {s['warnings']:,}")
    lines.append("Evidence-based effects (estimates, not guarantees):")
    lines.append(f"  Plan-Eligible Reclaimable Bytes (Phase 3, after keeper, protection and decisions): "
                 f"{_bytes(s['plan_eligible_reclaimable_bytes'])}"
                 + (f" -- {_bytes(s['plan_eligible_from_exact_duplicates'])} from exact duplicates, "
                    f"{_bytes(s['plan_eligible_from_visual_pairs'])} from equivalent images"
                    if s["plan_eligible_from_visual_pairs"] else ""))
    lines.append(f"  for comparison, Potentially Reclaimable Exact-Duplicate Bytes (Phase 2, before any decision): "
                 f"{_bytes(s['phase2_potentially_reclaimable_bytes'])}")
    lines.append(f"  {s['locations_removed']:,} file location(s) would be removed from "
                 f"{len(s['source_roots_affected'])} source root(s); Verified Reclaimed Bytes exist only after Phase 4.")
    ex = s.get("excluded") or {}
    if ex.get("deferred") or ex.get("conflict") or s.get("undecided_left"):
        lines.append(f"Not in this plan: {ex.get('deferred', 0):,} deferred by you, {ex.get('conflict', 0):,} in conflict; "
                     f"{s.get('undecided_left', 0):,} undecided cop(ies) in planned groups (the Plan Builder never decides them).")
    if ex.get("left_unchanged") or ex.get("project_metadata"):
        lines.append(f"Never plan items: {ex.get('left_unchanged', 0):,} metadata field(s) you chose to leave unchanged; "
                     f"{ex.get('project_metadata', 0):,} project metadata value(s) (stored in the File Organizer project, "
                     "never written to a file).")
    lines.append("Coverage: Remove Location -- evidence preview complete; live existence, content, permissions and "
                 "rollback are deferred to Phase 4.")
    if a.get(ACTION_METADATA):
        lines.append("Coverage: Update Source Metadata -- planned only: no certified source-metadata writer exists "
                     "(R10 §16), so every such item stays blocked; nothing is written in Phase 3.")
    if s.get("metadata_leaves_group"):
        lines.append(f"Note: {s['metadata_leaves_group']:,} metadata update(s) on files in exact-duplicate groups -- if "
                     "written, each file leaves its group (P3.R8 §47).")
    if s.get("metadata_on_removed_location"):
        lines.append(f"Warning: {s['metadata_on_removed_location']:,} metadata update(s) on a location this plan also "
                     "removes (P3.R8 §48); both items are kept and flagged.")
    lines.append(PREVIEW_DISCLOSURE)
    return lines


# -- the fingerprint ---------------------------------------------------------------------------

def payload(items) -> dict:
    """The canonical, normalized payload a fingerprint covers (R7 §6): every
    item's action, target, sources and desired effect; its Decision, Policy
    Version and Evidence Binding references; its declared pre- and
    postconditions with the preview's results; its dependencies and state."""
    out = []
    for it in sorted(items, key=lambda i: i.item_no):
        out.append({
            "n": it.item_no, "action": it.action_kind, "target": [it.target_kind, str(it.target_ref)],
            "sources": [[k, str(r)] for k, r in it.sources], "effect": it.desired_effect,
            "decisions": list(it.decision_ids), "retaining": list(it.retaining_ids), "policies": list(it.policy_version_ids),
            "binding": it.evidence_binding,
            "pre": [[c.key, c.klass, c.severity, c.stage, c.params, c.result] for c in it.preconditions],
            "post": list(it.postconditions), "deps": list(it.depends_on), "state": it.state,
            "blocked": it.blocked_reason, "impact": it.impact_class,
        })
    return {"schema": PLAN_PAYLOAD_SCHEMA, "items": out}


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def fingerprint(items) -> str:
    return hashlib.sha256(canonical(payload(items))).hexdigest()


def acyclic(items) -> bool:
    deps = {it.item_no: set(it.depends_on) for it in items}
    seen, done = set(), set()

    def visit(n):
        if n in done:
            return True
        if n in seen:
            return False
        seen.add(n)
        ok = all(visit(m) for m in deps.get(n, ()))
        done.add(n)
        return ok
    return all(visit(n) for n in deps)


# -- obsolescence: dependency-scoped, never a rewrite -------------------------------------------

REASON_WORDS = {
    "decision_changed": "source decision changed",
    "retention_added": "a decision now keeps a planned removal",
    "policy_changed": "policy version changed",
    "protection_added": "a protection policy now covers a planned removal",
    "evidence_changed": "evidence changed",
    "membership_changed": "duplicate-group membership changed",
    "pair_changed": "candidate pair changed",
    "new_conflict": "new conflict",
    "capability_changed": "metadata capability changed",
    "integrity_changed": "integrity evidence changed",
}


def obsolescence(items, ev: Evidence, proj, routing) -> list:
    """Why a locked revision may no longer speak -- each reason tied to the
    dependency that fired: [(code, words, detail)]. Only the revision's own
    dependencies are read (its decisions, policy versions, evidence bindings,
    sources); a change anywhere else in the project cannot obsolete it. An
    empty list: it still stands."""
    active = active_decisions(ev.decisions)
    active_ids = {d.decision_id for d in active} | {d.decision_id for d in MD.active_metadata(ev)}
    index = R._index_decisions(active)
    policies = active_policies(ev.policies)
    current_policy_versions = {str(p.policy_version_id) for p in policies}
    pairs_by_image = {}
    for pp in getattr(proj, "pairs", ()):
        pairs_by_image.setdefault(pp.a_id, []).append(pp)
        pairs_by_image.setdefault(pp.b_id, []).append(pp)
    reasons = []

    def add(code, detail):
        if not any(r[0] == code and r[2] == detail for r in reasons):
            reasons.append((code, REASON_WORDS[code], detail))
    for it in items:
        linked = [str(d) for d in list(it.decision_ids) + list(it.retaining_ids)]
        gone = [d for d in linked if d not in active_ids]
        if gone:
            add("decision_changed", f"item {it.item_no}: decision(s) " + ", ".join(f"#{d}" for d in gone)
                + " withdrawn or superseded")
        stale_pol = [p for p in it.policy_version_ids if str(p) not in current_policy_versions]
        if stale_pol:
            add("policy_changed", f"item {it.item_no}: policy version(s) {', '.join(stale_pol)} no longer current")
        if it.action_kind == ACTION_METADATA:
            _metadata_obsolescence(it, ev, add)
            continue
        if it.action_kind != ACTION_REMOVE:
            continue
        loc = ev.locations.get(str(it.target_ref))
        if it.state == ITEM_READY:
            now_retaining = {did for did, _w in retaining_decisions(ev, proj, index, pairs_by_image, it.target_ref)}
            new = sorted(now_retaining - set(it.retaining_ids), key=_id_order)
            if new:
                add("retention_added", f"item {it.item_no}: " + ", ".join(f"#{d}" for d in new) + f" now keep {_name(ev, it.target_ref)}")
            if loc is not None:
                _cov, in_force, _ov = R.location_protection(policies, index, loc)
                if in_force:
                    add("protection_added", f"item {it.item_no}: {_name(ev, it.target_ref)} is now protected "
                                            f"(policy version {in_force[0].policy_version_id})")
        b = it.evidence_binding or {}
        if b:
            drift = RT.binding_drift(ev, LOCATION, it.target_ref, {k: v for k, v in b.items() if k != "source"})
            if loc is not None and b.get("content_id") is not None and str(loc.content_id) != str(b.get("content_id")):
                drift = tuple(drift) + (f"{_name(ev, it.target_ref)}: content identity changed",)
            if drift:
                add("evidence_changed", f"item {it.item_no}: " + "; ".join(drift[:3]))
            src = b.get("source") or {}
            if src.get("target_kind") == GROUP:
                gdrift = RT.binding_drift(ev, GROUP, src.get("target_ref"), src)
                if [d for d in gdrift if d.startswith("new member") or d.startswith("member left") or "no longer a current" in d]:
                    add("membership_changed", f"item {it.item_no}: group {src.get('target_ref')}: " + "; ".join(gdrift[:3]))
            elif src.get("target_kind") == VISUAL_PAIR:
                pair = ev.pairs.get(str(src.get("target_ref"))) if getattr(ev, "pairs", None) is not None else None
                if pair is None or not pair.current:
                    add("pair_changed", f"item {it.item_no}: pair #{src.get('target_ref')} is no longer a current candidate pair")
        for kind, ref in it.sources:
            route = (routing.groups.get(str(ref)) if kind == GROUP else
                     (routing.pairs.get(str(ref)) if getattr(routing, "pairs", None) else None))
            if route is not None and any(c.severity == R.BLOCKING for c in route.of(RT.CONFLICT)) and it.state == ITEM_READY:
                add("new_conflict", f"item {it.item_no}: {'group' if kind == GROUP else 'pair #'}{ref} is now in conflict")
    return reasons


def _metadata_obsolescence(it, ev: Evidence, add):
    """A metadata item's own dependencies (Build 9): its file observed again
    or re-read, its question gone, the capability record or the integrity
    evidence no longer what the preview found."""
    b = it.evidence_binding or {}
    qb = b.get("question") or {}
    src = dict(it.sources).get(METADATA_QUESTION)
    q = ev.metadata.questions.get(str(src)) if src is not None else None
    if q is None:
        add("evidence_changed", f"item {it.item_no}: metadata question #{src} is no longer in the evidence")
        return
    drift = RT.binding_drift(ev, LOCATION, it.target_ref, {k: v for k, v in b.items() if k not in ("source", "question")})
    if not drift and qb and tuple(str(x) for x in qb.get("analyzer_result_ids") or ()) != tuple(q.analyzer_result_ids):
        drift = (f"{_name(ev, it.target_ref)}: {q.semantic_field} read again by an analyzer since",)
    if drift:
        add("evidence_changed", f"item {it.item_no}: " + "; ".join(drift[:3]))
    was = it.condition("writer_supported")
    if was is not None and (was.params.get("capability_status") != (q.capability.status if q.capability else "unknown")):
        add("capability_changed", f"item {it.item_no}: {q.semantic_field} in {q.file_family} files is now "
                                  f"'{q.capability.status if q.capability else 'unknown'}' (was '{was.params.get('capability_status')}')")
    for key, (res, _why) in MD.integrity(q).items():
        c = it.condition(key)
        if c is not None and c.result != res:
            add("integrity_changed", f"item {it.item_no}: {key.replace('_', ' ')} was {c.result}, now {res}")


# -- revision diff (R7 §34) --------------------------------------------------------------------

def diff(old_items, new_items, old_summary=None, new_summary=None) -> dict:
    """What changed from one revision to the next: items added and dropped
    (by action and target), items whose state changed, and the Phase 3
    reclaim estimate moving."""
    old = {(i.action_kind, str(i.target_ref)): i for i in old_items}
    new = {(i.action_kind, str(i.target_ref)): i for i in new_items}
    changed = [(k, old[k].state, new[k].state) for k in sorted(set(old) & set(new)) if old[k].state != new[k].state]
    out = {"added": sorted(set(new) - set(old)), "dropped": sorted(set(old) - set(new)), "state_changed": changed,
           "reclaim": ((old_summary or {}).get("plan_eligible_reclaimable_bytes"),
                       (new_summary or {}).get("plan_eligible_reclaimable_bytes"))}
    out["lines"] = diff_lines(out)
    return out


def diff_lines(d) -> list:
    words = []
    for kind in ACTION_KINDS:
        a = sum(1 for k in d["added"] if k[0] == kind)
        r = sum(1 for k in d["dropped"] if k[0] == kind)
        if a:
            words.append(f"{a:,} {ACTION_WORDS[kind].lower()} item(s) added")
        if r:
            words.append(f"{r:,} {ACTION_WORDS[kind].lower()} item(s) dropped")
    blocked = sum(1 for _k, o, n in d["state_changed"] if n != ITEM_READY and o == ITEM_READY)
    freed = sum(1 for _k, o, n in d["state_changed"] if n == ITEM_READY and o != ITEM_READY)
    if blocked:
        words.append(f"{blocked:,} newly blocked")
    if freed:
        words.append(f"{freed:,} no longer blocked")
    o, n = d["reclaim"]
    if o != n:
        words.append(f"Plan-Eligible Reclaim {_bytes(o)} -> {_bytes(n)}")
    return words or ["no change"]


def item_dict(it: PlanItem) -> dict:
    d = asdict(it)
    d["preconditions"] = [asdict(c) for c in it.preconditions]
    return d
