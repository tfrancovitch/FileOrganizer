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
from . import organize as OG
from . import scopes as SC
from .model import Evidence, Location, active_decisions, active_policies
from .registry import (LOCATION, GROUP, VISUAL_PAIR, ACTION_REMOVE, ACTION_MOVE, ACTION_RENAME, ACTION_METADATA,
                       ACTION_PRESERVE, ACTION_KINDS, ACTION_IMPACT, ACTION_PRECONDITIONS, ACTION_POSTCONDITIONS,
                       PRECONDITIONS, STAGE_EVIDENCE, STAGE_LIVE, ITEM_READY, ITEM_BLOCKED,
                       ITEM_BLOCKED_EVIDENCE, ITEM_UNSUPPORTED, ITEM_PRESERVE, PREVIEW_DISCLOSURE, ACTION_WORDS,
                       METADATA_QUESTION, METADATA_FIELDS, POLICY_KINDS, blocked_reason)
from . import EVIDENCE_BINDING_SCHEMA

PLAN_PAYLOAD_SCHEMA = "fileorganizer.p3.plan-payload/1"
#: (B18, S4) The Evidence Preview's contract version (what _preview_* and
#: _settle evaluate), recorded in every revision's basis. /2 (B19): removals
#: carry no_dangling_protection while a protection policy is dangling.
EVALUATOR_VERSION = "fileorganizer.p3.plan-evaluator/3"
#: /3 (B20): move_location / rename_location items are built from desired_location / desired_name
#: decisions (the preview of a move: occupancy against the walk and its coverage, the chain exception,
#: companions, cross-volume), and the cross-kind checks are attached to removal and metadata items
#: only where an organize item exists for the location.


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
    # B19 (I15): in recording order (the decisions' sequence) -- through B18
    # in id order, so which four the preview's words name moved with the ids.
    # (The item's retaining_ids stay a set, kept in id order in the payload.)
    seq = _sequences(index)
    return sorted(unique, key=lambda x: (seq.get(x[0], float("inf")), _id_order(x[0])))


def _sequences(index) -> dict:
    """decision_id -> sequence for the active decisions an index holds (cached on it)."""
    cache = getattr(_sequences, "_cache", None)
    if cache is not None and cache[0] is index:
        return cache[1]
    seq = {d.decision_id: d.sequence for kinds in index.values() for ds in kinds.values() for d in ds}
    _sequences._cache = (index, seq)
    return seq


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
    dangling = SC.dangling_protection(ev)            # B19: protection whose scope matches nothing scanned

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
        _dangling_protection(it, dangling)
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
    # B20: Move / Rename Location items, after the metadata items -- and none at all (nothing attached to
    # any other item either) unless a person recorded a desired location or name.
    items.extend(organize_items(ev, len(items) + 1, items, removes))
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
        shown = sorted(present, key=lambda k: ev.locations[k].sort_key)          # B19: the words in display order
        _set(it, "keeper_exists", "pass", "kept: " + ", ".join(_name(ev, k) for k in shown[:3]), keepers=present)
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


DANGLING_KEY = "no_dangling_protection"


def _dangling_protection(it: PlanItem, dangling):
    """(B19) While any active protection policy is dangling -- its scope
    matches nothing in the latest scan (renamed, moved, deleted: scopes.py) --
    every removal carries this hard precondition, evaluated UNKNOWN: whether
    this copy was meant to be protected nobody can say, and unknown never
    passes (P3-A91). Attached only then (registry.CONDITIONAL_PRECONDITIONS),
    so a plan with no dangling protection is exactly the earlier builds'. The
    detail names every such policy and how to clear it."""
    if not dangling:
        return
    c = Precondition(DANGLING_KEY, PRECONDITIONS[DANGLING_KEY], "hard", STAGE_EVIDENCE,
                     {"policies": sorted({s.policy_id for s in dangling}, key=_id_order)})
    c.result = "unknown"
    c.detail = (" ".join(s.words() for s in dangling)
                + " Until then no removal can be shown safe: the renamed files would no longer be protected.")
    it.preconditions.append(c)
    # The item depends on those versions: re-pointing or retiring one is a policy change for it.
    it.policy_version_ids = _ids(list(it.policy_version_ids) + [s.policy_version_id for s in dangling])


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


# -- move / rename items: organizing intent (B20, P3.R12) ------------------------------------------
#
# One item per location that carries an active desired_location and / or desired_name: a rename_location
# when only a name is wanted, otherwise a move_location (the name from desired_name if that is also active).
# The builder previews each against PROJECT EVIDENCE alone (the latest walk and what the scans recorded:
# no source file is read) and ends every item blocked, blocked_evidence or unsupported_action -- never
# ready: organize_executor_supported is `unsupported` until Phase 4 certifies an executor (P3-A136).
# Every other precondition is still evaluated, so a person sees the real collision, the stranded
# companion, the protected folder now, not on the day an executor arrives.

@dataclass
class OrgCtx:
    """What the move / rename previews share, computed once per build."""
    facts: object
    policies: list
    index: dict
    dangling: list
    by_id: dict                    # organize decision id -> Decision
    intents: dict                  # location id -> organize.Intent
    geoms: dict                    # location id -> organize.Geometry
    loc_by_key: dict               # path key -> Location (the locations the model holds)


def org_context(ev: Evidence) -> OrgCtx:
    from .scopes import ScopeFacts
    facts = getattr(ev, "scopes", None) or ScopeFacts()
    active = active_decisions(ev.decisions)
    intents = OG.intents(ev)
    geoms = {}
    for lid, intent in intents.items():
        g = OG.geometry(ev, intent, facts.root_ids)
        if g is not None:
            geoms[lid] = g
    return OrgCtx(facts, active_policies(ev.policies), R._index_decisions(active), SC.dangling_protection(ev),
                  {d.decision_id: d for d in getattr(ev.organize, "decisions", [])}, intents, geoms,
                  {l.path_key: l for l in ev.locations.values()})


def _effect_of(g, creates=()) -> dict:
    """The item's desired logical effect (synthesis 3.1): byte-exact strings, never an execution method."""
    return {"from_path": {"root_key": g.from_root_key, "path": g.from_rel},
            "to_path": {"root_key": g.to_root_key, "path": g.to_rel},
            "collision_policy": OG.COLLISION_POLICY, "companions": [], "creates_folders": list(creates)}


def _org_binding(ev: Evidence, lid, ctx: OrgCtx, g):
    b = _location_binding(ev, lid)
    loc = ev.locations.get(str(lid))
    b["organize"] = {"from_path": g.from_rel if g else None, "physical_id": loc.physical_id if loc is not None else None,
                     "destination_root_key": g.to_root_key if g else None,
                     "walk_run": ctx.facts.run_id}          # recorded, never compared: a new walk run alone obsoletes nothing
    return b


def _org_results(ev: Evidence, ctx: OrgCtx, action, lid, effect, decision_ids) -> dict:
    """key -> (result, detail, params) for the per-item evidence-stage preconditions -- everything that
    needs no other item. Shared by logical_item and the obsolescence check, so what a locked
    revision judged is judged again by the very same code."""
    lid = str(lid)
    loc = ev.locations.get(lid)
    facts = ctx.facts
    frm, to = effect["from_path"], effect["to_path"]
    from_name = OG.split_rel(frm["path"])[1]
    to_folder, to_name = OG.split_rel(to["path"])
    out = {}
    # identity
    if loc is None:
        out["target_exists"] = ("unknown", "not in the current evidence", {})
    elif facts.roots and frm["root_key"] not in facts.roots:
        out["target_exists"] = ("unknown", "its source root is not one of the project's roots", {})
    else:
        out["target_exists"] = ("pass" if loc.present else "fail",
                                "present at the last scan" if loc.present else "not present at the last scan",
                                {"observation_id": loc.observation_id} if loc.observation_id is not None else {})
    decs = [ctx.by_id[str(d)] for d in decision_ids if str(d) in ctx.by_id]
    if not decision_ids:
        out["decision_current"] = ("fail", "no decision asks for it -- the Plan Builder never invents intent", {})
    else:
        drift = [x for d in decs for x in OG.binding_drift(ev, d, facts.root_ids)]
        out["decision_current"] = (("fail", "needs revalidation: " + drift[0], {}) if drift
                                   else ("pass", "the decisions rest on the evidence as it is now", {}))
    out["no_current_conflict"] = ("pass", "no conflict stands on the location", {})
    if loc is None:
        for key in ("source_identity_known", "source_local_if_required", "target_not_protected", "source_not_hardlinked"):
            out[key] = ("unknown", "not in the current evidence", {})
    else:
        out["source_identity_known"] = (("pass", "volume serial and file index are recorded", {}) if loc.physical_id
                                        else ("unknown", "its volume serial and file index were not recorded, so the file cannot be "
                                              "told from another one later (succession and verification need them)", {}))
        out["source_local_if_required"] = (
            ("fail", "cloud-only at the last scan: a move or rename would have to leave it a placeholder or hydrate it, "
                     "and the product never hydrates", {}) if loc.cloud_only else
            ("pass", "local at the last scan", {}) if loc.cloud_known else
            ("unknown", "whether it is a cloud placeholder was not recorded", {}))
        _cov, in_force, overridden = R.location_protection(ctx.policies, ctx.index, loc, scope="organize")
        if in_force:
            out["target_not_protected"] = ("fail", f"protected by {in_force[0].kind.replace('_', ' ')} (policy version "
                                                   f"{in_force[0].policy_version_id}); only an override for MOVING it lifts that",
                                           {"policy_versions": [p.policy_version_id for p in in_force]})
        else:
            out["target_not_protected"] = ("pass", (f"protection overridden for moves by decision #{overridden[0][1]}" if overridden
                                                    else "no protection covers it"), {})
        out["source_not_hardlinked"] = (("unknown", "its link count was not recorded", {}) if loc.hard_links is None else
                                        ("fail", f"{loc.hard_links} names share this file: one name moves, the others stay", {})
                                        if loc.hard_links > 1 else ("pass", "one name", {}))
    if ctx.dangling:
        out["no_dangling_protection"] = (
            "unknown", " ".join(s.words() for s in ctx.dangling)
            + " A protection that matches nothing cannot be ruled out for this file, so no move can be shown safe.",
            {"policies": sorted({s.policy_id for s in ctx.dangling}, key=_id_order)})
    else:
        out["no_dangling_protection"] = ("pass", "no protection policy is dangling", {})
    # destination
    if action == ACTION_MOVE:
        root = facts.roots.get(to["root_key"]) if to["root_key"] is not None else None
        if to["root_key"] is None:
            out["destination_within_project_root"] = ("unknown", "the destination root is not a source root of this project "
                                                                  "(removed?)", {})
        elif root is None or root.state != "scanned":
            out["destination_within_project_root"] = ("unknown", (root.detail if root is not None else "that root is unknown")
                                                      or "the destination root was not scanned in the latest scan", {})
        else:
            out["destination_within_project_root"] = ("pass", "inside a scanned source root of this project", {})
    why = OG.name_problem(to_name) or OG.folder_problem(to_folder)
    if not why and facts.roots and to["root_key"] is not None:
        try:
            OG.canonical_folder(facts, to["root_key"], "", to_folder)
        except ValueError as exc:
            why = str(exc)
    out["destination_name_valid"] = (("fail", why, {}) if why else ("pass", "a legal Windows name and folder", {}))
    if OG.extension_of(from_name) != OG.extension_of(to_name):
        confirmed = any(d.kind == OG.KIND_NAME and d.value.get("extension_change_confirmed") is True for d in decs)
        out["extension_change_confirmed"] = (("pass", "the extension change was confirmed", {}) if confirmed else
                                             ("fail", "the extension changes and was not confirmed", {}))
    else:
        out["extension_change_confirmed"] = ("pass", "the extension is unchanged", {})
    # occupancy (the walk alone)
    from_key = OG.full_key(frm["root_key"], frm["path"])
    to_key = OG.full_key(to["root_key"], to["path"]) if to["root_key"] is not None else None
    if to_key is None:
        out["destination_collision_absent"] = ("unknown", "the destination root cannot be resolved", {"judged": "unknown"})
    else:
        occ = OG.occupancy(facts, to_key, own_key=from_key, root_key=to["root_key"], folder=to_folder)
        res = {"occupied": "fail", "unknown": "unknown", "free": "pass"}[occ.state]
        out["destination_collision_absent"] = (res, occ.detail, {"destination": to_key, "judged": occ.state})
    # volume, and moving INTO protection
    if action == ACTION_MOVE:
        a = facts.volumes.get(frm["root_key"])
        b = facts.volumes.get(to["root_key"]) if to["root_key"] else None
        if to["root_key"] is None or not a or not b or len(a) > 1 or len(b) > 1:
            out["same_volume"] = ("unknown", "the volume of a root is not known from the evidence (no files recorded there)", {})
        elif OG.through_link(facts, to["root_key"], to_folder):
            out["same_volume"] = ("unknown", "the path to the destination passes through a link the scan did not follow, which "
                                             "may land on another volume; same_volume compares root volumes and cannot see "
                                             "through it", {})
        elif a != b:
            out["same_volume"] = ("unsupported", "the destination is on another volume: a cross-volume move is not supported "
                                                 "initially (it would copy, then delete)", {})
        else:
            out["same_volume"] = ("pass", "the same volume", {})
        if to["root_key"] is not None and loc is not None:
            root_path = facts.roots[to["root_key"]].root_path if to["root_key"] in facts.roots else to["root_key"]
            probe = Location("destination", OG.join_rel(root_path, to_folder) if to_folder else root_path, to["root_key"], None,
                             None, True, None, None)
            protect = [p for p in ctx.policies if POLICY_KINDS.get(p.kind) and POLICY_KINDS[p.kind].effect == "protect"]
            already = {p.policy_version_id for p in protect if R._policy_covers(p, loc)}
            newly = [p for p in protect if R._policy_covers(p, probe) and p.policy_version_id not in already]
            out["destination_not_protected"] = (
                ("fail", f"the destination is under {newly[0].kind.replace('_', ' ')} (policy version {newly[0].policy_version_id}): "
                         "after it moves, the file is protected there", {"policy_versions": [p.policy_version_id for p in newly]})
                if newly else ("pass", "the destination is not under a protection that does not already cover the file", {}))
    out["organize_executor_supported"] = ("unsupported", "no executor is certified: Phase 4 builds and certifies one", {})
    return out


def logical_item(ev: Evidence, action, lid, desired_effect, decision_ids=(), item_no=1, ctx=None) -> PlanItem:
    """A move or rename item, previewed from project evidence (B20: made by build() from
    desired_location / desired_name decisions). `desired_effect` is {"from_path": {"root_key", "path"},
    "to_path": {"root_key", "path"}, "collision_policy", "companions", "creates_folders"} -- byte-exact
    strings; collisions are judged through `model.path_key`, never `.lower()` (P3-A138). The checks that
    need other items (conflicting intent, the chain exception, cycles, companions) are the builder's
    second pass (`_organize_cross`). Extends the B16 helper; does not replace it."""
    if action not in (ACTION_MOVE, ACTION_RENAME):
        raise ValueError(f"{action} is built from decisions by build(), not here")
    ctx = ctx or org_context(ev)
    lid = str(lid)
    g = ctx.geoms.get(lid)
    it = PlanItem(item_no, action, LOCATION, lid, ((LOCATION, lid),), dict(desired_effect), decision_ids=_ids(decision_ids),
                  evidence_binding=_org_binding(ev, lid, ctx, g))
    _declare(it)
    loc_ = ev.locations.get(lid)
    if loc_ is not None:
        it.policy_version_ids = _ids(p.policy_version_id for p in R.location_protection(ctx.policies, ctx.index, loc_, scope="organize")[0])
    for key, (result, detail, params) in _org_results(ev, ctx, action, lid, it.desired_effect, it.decision_ids).items():
        if it.condition(key) is not None:
            _set(it, key, result, detail, **params)
    # the cross-item checks start as pass: _organize_cross evaluates them over the whole revision
    for key, detail in (("no_conflicting_intent_same_location", "no removal is wanted for this location"),
                        ("no_dependency_cycle", "no cycle of moves"),
                        ("companions_travel_together", "no companion")):
        if it.condition(key) is not None and it.condition(key).result is None:
            _set(it, key, "pass", detail)
    for c in it.preconditions:
        if c.stage == STAGE_EVIDENCE and c.severity == "hard" and c.result is None:
            c.result, c.detail = "unknown", "not evaluated for this item"
    _settle(it)
    return it


def _display_order(ev, lid):
    loc = ev.locations.get(str(lid))
    return (loc.sort_key if loc is not None else (), _id_order(lid))


def organize_items(ev: Evidence, first_no, existing, removes) -> list:
    """The move / rename items for every active organize intent, in location display order, after the
    retention and metadata items (so a revision with no organize decision numbers and fingerprints
    exactly as B19's did). Conditional attachments on the removal and metadata items already in
    `existing` are made here, only when an organize item exists for the location (RC 8)."""
    if not OG.intents(ev):
        return []
    ctx = org_context(ev)
    out = []
    for n, lid in enumerate(sorted(ctx.intents, key=lambda l: _display_order(ev, l)), first_no):
        intent, g = ctx.intents[lid], ctx.geoms.get(lid)
        loc = ev.locations.get(lid)
        if g is None:                                       # the location is not in the evidence at all
            g = OG.Geometry(lid, intent.kind, loc.root_key if loc else "", "", "", None, "", "", "", None)
            ctx.geoms[lid] = g
        creates = OG.new_folders(ctx.facts, g.to_root_key, g.to_folder) if (g.to_root_key and ctx.facts.roots) else []
        effect = _effect_of(g, creates=creates)
        action = ACTION_MOVE if intent.move is not None else ACTION_RENAME
        out.append(logical_item(ev, action, lid, effect, [d.decision_id for d in intent.decisions], n, ctx))
    _companion_entries(ev, ctx, out)
    _organize_cross(out, existing, ev, ctx, removes)
    return out


def _clean(it, skip=("organize_executor_supported", "destination_collision_absent")) -> tuple:
    """(clean, why): every evidence-stage hard precondition passes except those in `skip` -- the
    executor first of all, because every item is unsupported today and a chain test that asked for
    `ready` would never pass (the evidence-clean vacator, synthesis 3.4)."""
    for c in it.preconditions:
        if c.stage == STAGE_EVIDENCE and c.severity == "hard" and c.key not in skip and c.result != "pass":
            return False, f"{c.key} is {c.result}"
    return True, ""


def _set_cond(it, key, result, detail, **params):
    c = it.condition(key)
    changed = (c.result, c.detail) != (result, detail)
    c.result, c.detail = result, detail
    if params:
        c.params = {k: v for k, v in params.items() if v is not None}
    return changed


def _organize_cross(items, existing, ev: Evidence, ctx: OrgCtx, removes):
    """The checks that need the whole revision: removal intent on the same location, the chain
    exception (a destination another item vacates, passing only when that item is evidence-clean),
    cycles, intra-plan collisions, companions travelling together; then the attachments to removal
    and metadata items. Iterated to a fixed point, because a family member's block or a vacator's
    block moves the others (it only ever adds failures, so it terminates)."""
    facts = ctx.facts
    by_lid = {it.target_ref: it for it in items}
    geoms = [ctx.geoms[l] for l in by_lid if l in ctx.geoms]
    edges = OG.chain_graph(geoms, facts)
    cyc = OG.cycle_members(edges)
    clash = OG.intra_plan_collisions(geoms)
    removal_items = {it.target_ref: it for it in existing if it.action_kind == ACTION_REMOVE}
    redundant = {d.target_ref for d in active_decisions(ev.decisions) if d.kind == "redundant_location"}
    meta_items = {}
    for it in existing:
        if it.action_kind == ACTION_METADATA:
            meta_items.setdefault(it.target_ref, []).append(it)
    base = {lid: {k: v for k, v in it.condition("destination_collision_absent").params.items() if k in ("destination", "judged")}
            for lid, it in by_lid.items()}
    for lid, it in by_lid.items():
        if lid in removal_items or lid in redundant:
            who = (f"item {removal_items[lid].item_no} of this revision removes it" if lid in removal_items
                   else "a redundant mark on it asks for its removal")
            _set_cond(it, "no_conflicting_intent_same_location", "fail",
                      f"{who}, and this item moves or renames it: removing and moving one location are contradictory "
                      "actions (P3-A131). Neither is preferred; withdraw one of them.")
        if lid in cyc:
            _set_cond(it, "no_dependency_cycle", "fail",
                      "it is part of a swap or rotation of names (each destination is another item's source): that needs a "
                      "temporary name, which is an execution method and belongs to Phase 4")
    for _round in range(len(items) + 2):
        changed = False
        memo = {}

        def chain_ok(lid, seen=()):
            """(ok, why): can the file standing at this item's destination be vacated?"""
            if lid in memo:
                return memo[lid]
            if lid in seen:
                return False, "the chain is circular"
            if lid in clash:
                r = (False, "it shares its destination with another item")
            elif base[lid].get("judged") == "free":
                r = (True, "")
            elif base[lid].get("judged") == "occupied" and lid in edges and lid not in cyc:
                v = by_lid.get(edges[lid])
                if v is None:
                    r = (False, "the item that vacates it is not in this revision")
                else:
                    ok, why = _clean(v)
                    if not ok:
                        r = (False, f"item {v.item_no} cannot proceed ({why})")
                    else:
                        ok2, why2 = chain_ok(v.target_ref, seen + (lid,))
                        r = (ok2, "" if ok2 else f"item {v.item_no}: {why2}")
            else:
                r = (False, "occupied")
            memo[lid] = r
            return r
        for lid, it in by_lid.items():
            if lid in clash:
                others = ", ".join(f"item {by_lid[x].item_no}" for x in clash[lid] if x in by_lid)
                changed |= _set_cond(it, "destination_collision_absent", "fail",
                                     f"{others} has the same destination (compared through the path rule): neither can go",
                                     **base[lid])
            elif lid in edges and lid not in cyc and base[lid].get("judged") == "occupied":
                v = by_lid.get(edges[lid])
                ok, why = chain_ok(lid)
                if ok:
                    holder = ctx.loc_by_key.get(base[lid].get("destination"))
                    changed |= _set_cond(it, "destination_collision_absent", "pass",
                                         f"occupied by {holder.path if holder is not None else 'a file'}, which item {v.item_no} "
                                         "of this revision vacates first (an evidence-clean vacator)", **base[lid])
                else:
                    changed |= _set_cond(it, "destination_collision_absent", "fail",
                                         f"occupied by a file that item {v.item_no if v is not None else '?'} would vacate, but a "
                                         f"dependent never passes on a vacator that cannot proceed ({why})", **base[lid])
                it.depends_on = (v.item_no,) if v is not None else ()
        for lid, it in by_lid.items():
            changed |= _companions(it, by_lid, ctx, ev, removal_items, redundant)
        if not changed:
            break
    for it in items:
        _settle(it)
        it.estimated_effect = _org_effects(it, ev, ctx, by_lid, ev.locations.get(it.target_ref))
    # attachments on the items already built -- only because an organize item exists for the location
    for lid, rm in removal_items.items():
        mine = by_lid.get(lid)
        keepers = sorted({str(k) for k in (removes.get(lid, {}).get("keepers") or ()) if k is not None and str(k) in by_lid
                          and str(k) != lid}, key=_id_order)
        if mine is not None:
            c = Precondition("no_conflicting_intent_same_location", PRECONDITIONS["no_conflicting_intent_same_location"], "hard",
                             STAGE_EVIDENCE, {"other_item": mine.item_no})
            what = "moves" if mine.action_kind == ACTION_MOVE else "renames"
            c.result, c.detail = "fail", (f"item {mine.item_no} of this revision {what} this location -- removing and moving one "
                                          "location are contradictory actions (P3-A131); neither is preferred")
            rm.preconditions.append(c)
        if keepers:
            k = keepers[0]
            c = Precondition("keeper_not_moving", PRECONDITIONS["keeper_not_moving"], "hard", STAGE_EVIDENCE,
                             {"keepers": keepers, "organize_items": [by_lid[x].item_no for x in keepers]})
            c.result, c.detail = "fail", (f"its keeper {_name(ev, k)} is moved or renamed by item {by_lid[k].item_no} of this revision "
                                          "while this duplicate is removed: which order is safe is Phase 4's to decide, so the "
                                          "removal is blocked")
            rm.preconditions.append(c)
        if mine is not None or keepers:
            _settle(rm)
    for lid, mds in meta_items.items():
        mine = by_lid.get(lid)
        if mine is None:
            continue
        for md in mds:
            for a, b in ((md, mine), (mine, md)):
                c = Precondition("no_organize_metadata_overlap", PRECONDITIONS["no_organize_metadata_overlap"], "warning",
                                 STAGE_EVIDENCE, {"other_item": b.item_no})
                other = "updates this location's metadata" if b.action_kind == ACTION_METADATA else "moves or renames this location"
                c.result, c.detail = "fail", (f"item {b.item_no} of this revision {other} -- a metadata write changes the bytes a "
                                              "move would carry; neither item is reordered or dropped")
                a.preconditions.append(c)


def family_names(links, mk) -> list:
    """The file names (other than this one) the sidecar patterns tie to the file whose path key is `mk`."""
    out = set()
    for l in links:
        involved = {l.sidecar} | ({l.primary} if l.primary else set()) | set(l.candidates)
        if mk in {OG.path_key(n) for n in involved}:
            out |= {n for n in involved if OG.path_key(n) != mk}
    return sorted(out, key=lambda n: (OG.path_key(n), n))


def _companion_entries(ev, ctx, items) -> None:
    """The sidecars that travel with each primary, with the names they will have, in the item's
    desired effect (a companion is its own item too; nothing propagates)."""
    for it in items:
        loc = ev.locations.get(it.target_ref)
        g = ctx.geoms.get(it.target_ref)
        if loc is None or g is None or not ctx.facts.roots:
            continue
        folder, me = OG.split_rel(loc.rel_path)
        idx = OG.folder_index(ctx.facts)
        names = [n for spelled in idx.files(OG.full_key(loc.root_key, folder)).values() for n in spelled]
        out = []
        for l in OG.detect(names):
            if not l.ambiguous and l.primary and OG.path_key(l.primary) == OG.path_key(me):
                s = ctx.loc_by_key.get(OG.full_key(loc.root_key, OG.join_rel(folder, l.sidecar)))
                if s is not None and s.location_id in ctx.geoms:
                    sg = ctx.geoms[s.location_id]
                    out.append({"location_id": s.location_id, "from_path": sg.from_rel, "to_path": sg.to_rel})
        it.desired_effect["companions"] = out


def _companions(it, by_lid, ctx: OrgCtx, ev: Evidence, removal_items, redundant) -> bool:
    """companions_travel_together (P3-A133): every sidecar found by name beside this file moves or
    renames with it, to the same folder and the mapped name -- as its own explicit decision; and the
    other way round for a sidecar. Ambiguous (RAW + JPEG sharing `<stem>.xmp`) is unknown, never a
    guess. A block on any family member blocks the whole action unit, and the detail names it."""
    facts = ctx.facts
    loc = ev.locations.get(it.target_ref)
    g = ctx.geoms.get(it.target_ref)
    if loc is None or g is None:
        return _set_cond(it, "companions_travel_together", "unknown", "the file is not in the evidence")
    folder = OG.split_rel(loc.rel_path)[0]
    gap = OG.coverage_gap(facts, loc.root_key, folder)
    if gap:
        return _set_cond(it, "companions_travel_together", "unknown", f"companions cannot be looked for: {gap}")
    idx = OG.folder_index(facts)
    names = [n for spelled in idx.files(OG.full_key(loc.root_key, folder)).values() for n in spelled]
    links = OG.detect(names)
    me = OG.split_rel(loc.rel_path)[1]
    mk = OG.path_key(me)
    family = family_names(links, mk)

    def other(name):
        return ctx.loc_by_key.get(OG.full_key(loc.root_key, OG.join_rel(folder, name)))

    def agrees(member_geom, expected_name, anchor_geom):
        if member_geom.to_root_key != anchor_geom.to_root_key or OG.path_key(member_geom.to_folder) != OG.path_key(anchor_geom.to_folder):
            return False, f"goes to {member_geom.to_rel} instead of {anchor_geom.to_folder or 'the root'}"
        if member_geom.to_name != expected_name:
            return False, f"is named {member_geom.to_name}, not {expected_name}"
        return True, ""

    problems, unknowns, members = [], [], []
    for l in links:
        if not l.ambiguous and OG.path_key(l.primary) == mk:                    # I am the primary
            s = other(l.sidecar)
            if s is None or s.location_id not in ctx.geoms:
                problems.append(f"{l.sidecar} would be stranded: it has no move or rename of its own")
                continue
            ok, why = agrees(ctx.geoms[s.location_id], OG.mapped_sidecar_name(l, me, g.to_name), g)
            if not ok:
                problems.append(f"{l.sidecar} {why}")
            members.append(s.location_id)
        elif not l.ambiguous and OG.path_key(l.sidecar) == mk:                  # I am the sidecar
            p = other(l.primary)
            if p is None or p.location_id not in ctx.geoms:
                problems.append(f"its primary {l.primary} is not being moved or renamed with it")
                continue
            pg = ctx.geoms[p.location_id]
            ok, why = agrees(g, OG.mapped_sidecar_name(l, l.primary, pg.to_name), pg)
            if not ok:
                problems.append(f"it {why.replace('is named', 'would be named')} where its primary {l.primary} is going")
            members.append(p.location_id)
        elif l.ambiguous and (OG.path_key(l.sidecar) == mk or mk in {OG.path_key(c) for c in l.candidates}):
            s = other(l.sidecar)
            if s is None or s.location_id not in ctx.geoms:
                unknowns.append(f"{l.sidecar} could belong to any of {', '.join(l.candidates)} and has no decision of its own, "
                                "so which file it travels with cannot be said")
                continue
            sg = ctx.geoms[s.location_id]
            for cand in l.candidates:
                c = other(cand)
                cg = ctx.geoms.get(c.location_id) if c is not None else None
                if cg is None:
                    problems.append(f"{cand} shares {l.sidecar} but is not moving with it")
                    continue
                want = OG.mapped_sidecar_name(OG.Link(l.sidecar, "adobe_xmp", cand), cand, cg.to_name)
                ok, why = agrees(sg, want, cg)
                if not ok:
                    problems.append(f"{l.sidecar} and {cand} do not agree: the sidecar {why}")
                members.append(c.location_id)
            members.append(s.location_id)
    # a family member that is removed, or is itself blocked, blocks the whole unit
    for lid in dict.fromkeys(members):
        m = by_lid.get(lid)
        if lid in removal_items or lid in redundant:
            problems.append(f"{_name(ev, lid)} carries removal intent")
        elif m is not None and m is not it:
            ok, why = _clean(m, skip=("organize_executor_supported", "companions_travel_together"))
            if not ok:
                key = why.split(" ")[0]
                (problems if m.condition(key).result == "fail" else unknowns).append(
                    f"{_name(ev, lid)} is blocked ({why}), so the whole family is")
    if problems:
        return _set_cond(it, "companions_travel_together", "fail", "; ".join(problems), family=family)
    if unknowns:
        return _set_cond(it, "companions_travel_together", "unknown", "; ".join(unknowns), family=family)
    return _set_cond(it, "companions_travel_together", "pass",
                     "no companion found beside it" if not members else f"{len(set(members))} companion(s) travel with it",
                     family=family)


def _org_effects(it, ev, ctx, by_lid, loc) -> dict:
    """Estimated effects (R12 9.5), informational: nothing here blocks."""
    leaves_empty = None
    if loc is not None and ctx.facts.roots:
        folder = OG.split_rel(loc.rel_path)[0]
        idx = OG.folder_index(ctx.facts)
        here = idx.files(OG.full_key(loc.root_key, folder))
        moving = {ctx.geoms[l].from_key for l in by_lid if l in ctx.geoms}
        leaves_empty = bool(here) and all(OG.full_key(loc.root_key, OG.join_rel(folder, n)) in moving
                                          for names in here.values() for n in names) \
            and not idx.children(OG.full_key(loc.root_key, folder))
    protect = it.condition("destination_not_protected")
    return {"from_path": it.desired_effect["from_path"], "to_path": it.desired_effect["to_path"],
            "creates_folders": list(it.desired_effect["creates_folders"]), "companions": list(it.desired_effect["companions"]),
            "duplicate_group_effect": "none",          # content unchanged, so group membership is unchanged
            "destination_protection": (protect.params.get("policy_versions") if protect is not None else None) or [],
            "leaves_folder_empty": leaves_empty, "logical_bytes": loc.size_bytes if loc is not None else None,
            "case_only": bool(ctx.geoms.get(it.target_ref) and ctx.geoms[it.target_ref].case_only)}


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
        **_dangling_summary(ev),
        **_organize_summary(items),
    }


def _organize_summary(items):
    """(B20) Present only when there are move / rename items, so a summary without them reads as before."""
    mv = [i for i in items if i.action_kind in (ACTION_MOVE, ACTION_RENAME)]
    if not mv:
        return {}
    return {"organize": {"items": len(mv), "moves": sum(1 for i in mv if i.action_kind == ACTION_MOVE),
                         "renames": sum(1 for i in mv if i.action_kind == ACTION_RENAME),
                         "creates_folders": sum(len(i.desired_effect.get("creates_folders") or []) for i in mv),
                         "cross_volume": sum(1 for i in mv if i.blocked_reason == "cross_volume_move_not_supported_initially")}}


def _dangling_summary(ev):
    """(B19) The dangling policies -- present only when there are any, so a
    summary without them reads exactly as before."""
    found = SC.summary(ev)
    return {"dangling_policies": found} if found else {}


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
    if s.get("organize"):
        o = s["organize"]
        lines.append(f"Coverage: Move / Rename Location -- planned only: {o['moves']:,} move(s) and {o['renames']:,} rename(s); no executor "
                     "is certified, so every such item stays blocked or unsupported and nothing is moved or renamed in Phase 3 "
                     f"({o['creates_folders']:,} folder(s) would be created; {o['cross_volume']:,} cross-volume, not supported yet). "
                     "Stored in the project; never moves the file.")
    if s.get("metadata_leaves_group"):
        lines.append(f"Note: {s['metadata_leaves_group']:,} metadata update(s) on files in exact-duplicate groups -- if "
                     "written, each file leaves its group (P3.R8 §47).")
    if s.get("metadata_on_removed_location"):
        lines.append(f"Warning: {s['metadata_on_removed_location']:,} metadata update(s) on a location this plan also "
                     "removes (P3.R8 §48); both items are kept and flagged.")
    for d in s.get("dangling_policies") or ():
        if d.get("effect") == "protect":
            lines.append("BLOCKED -- every removal (no_dangling_protection): " + d["words"])
        else:
            lines.append("Warning (the recommendation only): " + d["words"])
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


#: (B18) The canonical encoding of a plan payload, written down in
#: Docs\Specifications\PLAN_FINGERPRINT_SPEC.md (with golden test vectors in
#: Resources\Phase3\fingerprint_vectors.json, checked by p3_fingerprint_check):
#:   JSON; object keys sorted by code point, and every key a string; no
#:   whitespace (separators "," and ":"); strings as stored -- no Unicode
#:   normalization -- written as UTF-8 without \u escapes except the ones JSON
#:   requires (control characters, '"', '\'); an unpaired surrogate (a Windows
#:   name that is not valid UTF-16) as its three-byte generalized-UTF-8 form;
#:   integers in decimal; true / false / null; a finite float in its shortest
#:   round-trip form, NaN and the infinities refused; tuples as arrays.
#:   Nothing else is accepted: B15-B17's `default=str` would have written any
#:   other type through Python's str(). The audit (p3_fingerprint_check) shows
#:   no payload ever held one, so every existing fingerprint is unchanged and
#:   the payload schema stays fileorganizer.p3.plan-payload/1. From here on,
#:   any change to this encoding bumps PLAN_PAYLOAD_SCHEMA.
_JSON_SCALARS = (str, int, bool, type(None))


def plain(obj, where="payload"):
    """The payload as JSON-native values, explicitly: anything the encoding
    does not name is refused (TypeError), never converted by str()."""
    if isinstance(obj, _JSON_SCALARS):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            raise TypeError(f"{where}: NaN and the infinities have no canonical encoding")
        return obj
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"{where}: object key {k!r} is not a string")
            out[k] = plain(v, f"{where}.{k}")
        return out
    if isinstance(obj, (list, tuple)):
        return [plain(v, f"{where}[{i}]") for i, v in enumerate(obj)]
    raise TypeError(f"{where}: {type(obj).__name__} has no canonical encoding (plan payload /1 is JSON-native only)")


def canonical(obj) -> bytes:
    return json.dumps(plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8", "surrogatepass")


def fingerprint(items) -> str:
    return hashlib.sha256(canonical(payload(items))).hexdigest()


#: The canonical form each payload schema was locked under -- verification
#: uses the schema RECORDED with the revision (basis "payload_schema", B18),
#: never assumed to be the current one. A revision locked before B18 records
#: none: it is /1, whose encoding for every value it can hold is `canonical`.
CANONICAL_BY_SCHEMA = {"fileorganizer.p3.plan-payload/1": canonical}


def verify(items, fingerprint_, payload_schema=None) -> bool:
    """Does this frozen item set still hash to the fingerprint it was locked
    with, under the payload schema it was locked under?"""
    schema = payload_schema or "fileorganizer.p3.plan-payload/1"
    enc = CANONICAL_BY_SCHEMA.get(schema)
    if enc is None:
        raise ValueError(f"unknown plan payload schema: {schema}")
    body = payload(items)
    body["schema"] = schema
    return hashlib.sha256(enc(body)).hexdigest() == fingerprint_


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
    "protection_dangling": "a protection policy matches nothing in the latest scan",
    "organize_occupancy_changed": "what is at a planned destination changed",
    "organize_vacator_changed": "the item that vacates a planned destination changed",
    "organize_companions_changed": "the sidecars beside a planned move changed",
}


def obsolescence(items, ev: Evidence, proj, routing) -> list:
    """Why a locked revision may no longer speak -- each reason tied to the
    dependency that fired: [(code, words, detail)]. Only the revision's own
    dependencies are read (its decisions, policy versions, evidence bindings,
    sources); a change anywhere else in the project cannot obsolete it. An
    empty list: it still stands."""
    active = active_decisions(ev.decisions)
    active_ids = ({d.decision_id for d in active} | {d.decision_id for d in MD.active_metadata(ev)}
                  | {d.decision_id for d in active_decisions(ev.organize.decisions)})
    org_ctx = None
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
    # B19: every removal's target_not_protected read every protection policy;
    # one that now matches nothing (renamed / moved) leaves a ready removal
    # unprovable -- the same rule as the preview's no_dangling_protection.
    dangling = SC.dangling_protection(ev)
    ready_removals = [it for it in items if it.action_kind == ACTION_REMOVE and it.state == ITEM_READY]
    if dangling and ready_removals:
        add("protection_dangling", f"{len(ready_removals):,} ready removal(s); " + " ".join(s.words() for s in dangling))
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
        if it.action_kind in (ACTION_MOVE, ACTION_RENAME):
            org_ctx = org_ctx or org_context(ev)
            _organize_obsolescence(it, items, ev, org_ctx, add)
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


#: (B20) The moves' obsolescence reasons, beside the removal's. Judged from the FACTS the item judged
#: (the occupant of the name, the coverage of the folder chain, the protection it checked, the
#: vacator, the companions) -- never from a newer walk run (F29).
def _organize_obsolescence(it: PlanItem, items, ev: Evidence, ctx: OrgCtx, add):
    fresh = _org_results(ev, ctx, it.action_kind, it.target_ref, it.desired_effect, it.decision_ids)
    where = f"item {it.item_no}"
    for key, (result, detail, params) in fresh.items():
        c = it.condition(key)
        if c is None or key == "organize_executor_supported":
            continue
        if key == "destination_collision_absent":
            if c.params.get("judged") != params.get("judged"):
                add("organize_occupancy_changed", f"{where}: the destination was judged {c.params.get('judged')}, now {params.get('judged')} "
                                                  f"({detail})")
        elif key == "target_not_protected":
            if c.result != result:
                add("protection_added" if result == "fail" else "policy_changed",
                    f"{where}: protection of {_name(ev, it.target_ref)} was '{c.result}', now '{result}'")
        elif key == "no_dangling_protection":
            if c.result != result:
                add("protection_dangling", f"{where}: " + detail)
        elif c.result != result:
            add("evidence_changed", f"{where}: {key.replace('_', ' ')} was {c.result}, now {result}")
    for dep in it.depends_on:
        v = next((x for x in items if x.item_no == dep), None)
        gone = [] if v is None else [d for d in v.decision_ids if str(d) not in {x.decision_id for x in active_decisions(ev.organize.decisions)}]
        if v is None or gone:
            add("organize_vacator_changed", f"{where}: the item that vacates its destination (item {dep}) changed")
    loc = ev.locations.get(it.target_ref)
    cc = it.condition("companions_travel_together")
    if loc is not None and cc is not None and "family" in cc.params and ctx.facts.roots:
        folder = OG.split_rel(loc.rel_path)[0]
        names = [n for spelled in OG.folder_index(ctx.facts).files(OG.full_key(loc.root_key, folder)).values() for n in spelled]
        now = family_names(OG.detect(names), OG.path_key(OG.split_rel(loc.rel_path)[1]))
        if now != list(cc.params["family"]):
            add("organize_companions_changed", f"{where}: the companions beside {_name(ev, it.target_ref)} changed")


def _metadata_obsolescence(it, ev: Evidence, add):
    """A metadata item's own dependencies (Build 9): its question gone, its
    file observed again or gone, the capability record or the integrity
    evidence no longer what the preview found.

    B18 -- judged by what changed, never by a newer row: a re-read that finds
    the same value and value-state changes no previewed input, so it no
    longer obsoletes the revision (B16 / B17 compared analyzer-result ids).
    The previewed value and state are the item's own estimated effect, as
    frozen; an item without them falls back to the ids. A change in any
    previewed input still obsoletes it -- integrity evidence included."""
    b = it.evidence_binding or {}
    qb = b.get("question") or {}
    src = dict(it.sources).get(METADATA_QUESTION)
    q = ev.metadata.questions.get(str(src)) if src is not None else None
    if q is None:
        add("evidence_changed", f"item {it.item_no}: metadata question #{src} is no longer in the evidence")
        return
    drift = RT.binding_drift(ev, LOCATION, it.target_ref, {k: v for k, v in b.items() if k not in ("source", "question")})
    was = it.estimated_effect or {}
    if not drift and was.get("observed_state") is not None and was.get("observed_state") != q.observed_state:
        drift = (f"{_name(ev, it.target_ref)}: {q.semantic_field}'s observed state changed "
                 f"({was.get('observed_state')} -> {q.observed_state})",)
    if not drift and "observed_values" in was and MD.value_key(was.get("observed_values")) != MD.value_key(q.observed_values):
        drift = (f"{_name(ev, it.target_ref)}: {q.semantic_field}'s observed value changed",)
    if not drift and "observed_values" not in was and qb \
            and tuple(str(x) for x in qb.get("analyzer_result_ids") or ()) != tuple(q.analyzer_result_ids):
        drift = (f"{_name(ev, it.target_ref)}: {q.semantic_field} read again by an analyzer since",)
    local = it.condition("source_local_if_required")
    loc = ev.locations.get(str(it.target_ref))
    if not drift and local is not None and loc is not None and local.result != ("fail" if loc.cloud_only else "pass"):
        drift = (f"{_name(ev, it.target_ref)}: {'now' if loc.cloud_only else 'no longer'} cloud-only",)
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
