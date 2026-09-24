r"""Keeper / canonical / protection resolution -- the deterministic order.

A pure function of the model in `model.py`: the same input always gives the
same projection, and every projection is rebuilt from authoritative history
(decisions, withdrawals, policy versions) plus Phase 2 evidence. Nothing
computed here is stored as truth.

The order, from the P3.R2 keeper-rule catalog; an earlier class outranks
every later one:

    1 hard_constraint            protection policies (minus explicit overrides),
                                 explicit must-keep, the minimum-copies floors
    2 explicit_location_decision canonical_location, redundant_location
    3 explicit_group_decision    keep_all_group
    4 folder_policy              prefer / avoid folder subtree   (recommendation only)
    5 source_root_policy         prefer source root              (recommendation only)
    6 project_policy             placeholder: no kind exists yet
    7 optional_heuristic         none ships; disabled
    8 display_only_tiebreak      presentation order; never intent

What that gives each member of an exact-duplicate group is one of four
statuses -- PROTECTED, KEEPER, REDUNDANT, UNDECIDED -- and each group a
keeper set, an explicit canonical (if any), a policy-suggested canonical
(labelled as such), its conflicts, its physical-copy arithmetic and its
plan-eligible reclaim.

The protection gate (P3.R2, adopted): a `redundant_location` decision has no
effect on a location an active protection policy covers. The location stays
PROTECTED and the group carries a CONFLICT until an `override_protection`
decision naming that exact policy version exists for that location. This is
a gate here, in the resolution, not a dialog.

Words: a retained copy is a Keeper or the Canonical. Nothing here is ever
called the original -- filesystem evidence does not establish which copy
came first, and the research is emphatic on the point.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .model import Evidence, Location, Group, active_decisions, active_policies, under, path_key
from .registry import LOCATION, GROUP, POLICY_KINDS

# -- per-location statuses ------------------------------------------------------

PROTECTED = "protected"
KEEPER = "keeper"
REDUNDANT = "redundant_candidate"
UNDECIDED = "undecided"

# -- per-group review states ----------------------------------------------------

UNREVIEWED = "unreviewed"
IN_PROGRESS = "in_progress"
DEFERRED = "deferred"
RESOLVED = "resolved"
CONFLICT = "conflict"

# -- catalog resolution states (the recommendation's basis) ---------------------

RS_EXPLICIT = "explicit"
RS_POLICY_UNIQUE = "policy_unique"
RS_POLICY_MULTIPLE = "policy_multiple_keepers"
RS_TIE = "tie"
RS_CONFLICT = "conflict"
RS_BLOCKED = "blocked_evidence"

#: The floors a future removal plan must respect (catalog defaults).
MINIMUM_FILE_LOCATIONS = 1
MINIMUM_PHYSICAL_COPIES = 1


#: Conflict kinds (Build 3 names them the research's way). A BLOCKING
#: conflict is contradictory intent -- it withholds plan eligibility until a
#: person resolves it. An ADVISORY one (an exception) routes attention but
#: changes nothing about what is eligible.
PROTECTED_VS_REDUNDANT = "protected_vs_redundant"          # the gate (B8 called it redundant_protected)
CANONICAL_NO_LONGER_KEEPER = "canonical_no_longer_keeper"  # reason: marked_redundant | left_group
SAME_PRECEDENCE_POLICY_TIE = "same_precedence_policy_tie"  # a prefer and an avoid at one tier disagree
MINIMUM_FILE_LOCATIONS_KIND = "minimum_file_locations"
MINIMUM_PHYSICAL_COPIES_KIND = "minimum_physical_copies"
BLOCKING = "blocking"
ADVISORY = "advisory"


@dataclass(frozen=True)
class Conflict:
    kind: str
    location_ids: tuple
    decision_ids: tuple
    message: str
    severity: str = BLOCKING
    reason: str = ""


@dataclass
class LocationVerdict:
    location_id: str
    path: str
    root_key: str
    status: str
    rule: str = ""                      # the catalog rule that decided the status
    resolution_class: str = ""          # which class of the order decided it
    decision_ids: tuple = ()            # every active decision that bears on this location
    protected_by: tuple = ()            # policy version ids that cover it (before overrides)
    overridden: tuple = ()              # (policy_version_id, decision_id) overrides in force
    policy_tier: str = "neutral"        # preferred | neutral | avoided -- a recommendation input only
    deferred: bool = False
    present: bool = True
    physical_id: str | None = None
    size_bytes: int | None = None
    notes: tuple = ()


@dataclass
class GroupProjection:
    group_id: str
    content_id: str
    members: list                        # LocationVerdict, display order
    keeper_set: tuple                    # protected + keepers, sorted by id
    protected: tuple
    redundant_candidates: tuple
    undecided: tuple
    canonical: str | None                # the explicit canonical, when one is active and valid
    canonical_decision_id: str | None
    suggested_canonical: str | None      # what policy alone would recommend (unique), labelled as such
    canonical_origin: str | None         # 'explicit_human' | 'policy' | None
    effective_canonical: str | None
    policy_preferred: tuple              # members in the best policy tier, when a policy applies
    conflicts: tuple
    location_count: int
    physical_copies: int | None
    hardlink_aliases: int | None
    physical_identity_complete: bool
    size_bytes: int | None
    plan_eligible_locations: tuple
    plan_eligible_reclaim_bytes: int | None   # None = unknown (physical identity incomplete)
    potential_reclaim_bytes: int | None       # Phase 2's (copies-1)*size, decision-free
    ready_for_plan: bool
    review_state: str
    resolution_state: str
    deferred: bool
    decision_count: int
    notes: tuple = ()

    def as_dict(self):
        d = asdict(self)
        d["conflicts"] = [asdict(c) for c in self.conflicts]
        return d

    def verdict(self, location_id):
        for v in self.members:
            if v.location_id == location_id:
                return v
        return None


@dataclass
class ProjectProjection:
    groups: list                         # GroupProjection, display order
    orphaned_decisions: tuple            # active decisions whose target is no longer in current evidence
    totals: dict = field(default_factory=dict)

    def group(self, group_id):
        for g in self.groups:
            if g.group_id == group_id:
                return g
        return None


# -- helpers ---------------------------------------------------------------------

def _latest(decisions):
    """Of several active decisions in one domain (only possible in imported
    or hand-built data -- the store supersedes), the latest recorded speaks."""
    return max(decisions, key=lambda d: (d.sequence, d.decision_id)) if decisions else None


def _policy_covers(policy, loc: Location) -> bool:
    kind = POLICY_KINDS.get(policy.kind)
    if kind is None:
        return False
    if kind.scope_kind == "source_root":
        return str(policy.scope.get("root_key", "")).lower() == str(loc.root_key).lower()
    return under(loc.path_key, path_key(policy.scope.get("path_key") or policy.scope.get("path") or ""))


def _prefer_applies(policy, members) -> bool:
    """prefer_folder_subtree may be relative: 'Documents over Downloads' only
    speaks in a group that has a member under Downloads."""
    over = policy.effect.get("over_path_key") or policy.effect.get("over")
    if not over:
        return True
    over_key = path_key(over)
    return any(under(m.path_key, over_key) for m in members)


def _index_decisions(decisions):
    """(target_kind, target_ref) -> kind -> [active decisions]"""
    index = {}
    for d in decisions:
        index.setdefault((d.target_kind, d.target_ref), {}).setdefault(d.kind, []).append(d)
    return index


def location_protection(policies, index, loc: Location):
    """Step 1 for one location: (covering, in_force, overridden) -- the active
    protection policies that cover it, those still in force after the
    location's own override_protection decisions, and the (policy version,
    decision) overrides that lifted the rest. Answers for any location,
    group member or not: a policy's population is every location it covers."""
    protect_policies = [p for p in policies if POLICY_KINDS.get(p.kind) and POLICY_KINDS[p.kind].effect == "protect"]
    covering = [p for p in protect_policies if _policy_covers(p, loc)]
    ldec = index.get((LOCATION, loc.location_id), {})
    overrides = {}
    for od in ldec.get("override_protection", []):
        overrides[str(od.value.get("policy_version_id"))] = od
    in_force = [p for p in covering if p.policy_version_id not in overrides]
    overridden = tuple(sorted((p.policy_version_id, overrides[p.policy_version_id].decision_id)
                              for p in covering if p.policy_version_id in overrides))
    return covering, in_force, overridden


def protected_locations(ev: Evidence) -> dict:
    """Every location the model holds that an active protection policy covers
    and no override lifts: location_id -> the policy version ids in force.
    Protection is a pure effect of policy plus evidence (F05): a location that
    appears under a protected folder after the policy was made is covered with
    no decision recorded for it, and a location outside any duplicate group
    is covered all the same."""
    active = active_decisions(ev.decisions)
    index = _index_decisions(active)
    policies = active_policies(ev.policies)
    out = {}
    for lid in sorted(ev.locations):
        _covering, in_force, _overridden = location_protection(policies, index, ev.locations[lid])
        if in_force:
            out[lid] = tuple(p.policy_version_id for p in in_force)
    return out


# -- the resolution -------------------------------------------------------------

def resolve_group(ev: Evidence, group: Group, _index=None, _active=None) -> GroupProjection:
    """Resolve one exact-duplicate group. Deterministic; reads only `ev`."""
    active = _active if _active is not None else active_decisions(ev.decisions)
    index = _index if _index is not None else _index_decisions(active)
    policies = active_policies(ev.policies)
    members = [ev.locations[i] for i in group.member_ids if i in ev.locations]
    member_ids = [m.location_id for m in members]
    conflicts = []
    notes = []

    group_key = (GROUP, group.group_id)
    gdec = index.get(group_key, {})
    canonical_dec = _latest(gdec.get("canonical_location", []))
    keep_all_dec = _latest(gdec.get("keep_all_group", []))
    group_defer = _latest(gdec.get("defer_review", []))

    # Step 1: hard constraints -- protection first, every time.
    verdicts = {}
    for m in members:
        ldec = index.get((LOCATION, m.location_id), {})
        covering, in_force, overridden = location_protection(policies, index, m)
        must_keep = _latest(ldec.get("must_keep_location", []))
        redundant = _latest(ldec.get("redundant_location", []))
        defer = _latest(ldec.get("defer_review", []))
        bearing = tuple(sorted({d.decision_id for ds in ldec.values() for d in ds}))
        v = LocationVerdict(m.location_id, m.path, m.root_key, UNDECIDED,
                            decision_ids=bearing,
                            protected_by=tuple(sorted(p.policy_version_id for p in covering)),
                            overridden=overridden, deferred=defer is not None,
                            present=m.present, physical_id=m.physical_id, size_bytes=m.size_bytes)
        if in_force:
            v.status, v.rule, v.resolution_class = PROTECTED, in_force[0].kind, "hard_constraint"
            if redundant is not None:
                # The gate: a redundant mark against active protection is a
                # conflict, not an effect. The location stays protected.
                conflicts.append(Conflict(
                    PROTECTED_VS_REDUNDANT, (m.location_id,), (redundant.decision_id,),
                    f"{m.path} is marked a redundant candidate but is protected by "
                    f"{POLICY_KINDS[in_force[0].kind].label.lower()} ({in_force[0].scope.get('root_path') or in_force[0].scope.get('path')}). "
                    "Protection is a hard constraint: override it explicitly, or withdraw the mark."))
        elif must_keep is not None:
            v.status, v.rule, v.resolution_class = KEEPER, "explicit_must_keep_location", "hard_constraint"
            if redundant is not None:
                notes.append(f"{m.location_id}: a redundant mark is outranked by the explicit keep")
        verdicts[m.location_id] = (v, must_keep, redundant)

    # Step 2: explicit location decisions -- canonical, redundant.
    canonical = None
    canonical_id = None
    if canonical_dec is not None:
        cand = str(canonical_dec.value)
        if cand in verdicts:
            canonical, canonical_id = cand, canonical_dec.decision_id
        else:
            conflicts.append(Conflict(
                CANONICAL_NO_LONGER_KEEPER, (cand,), (canonical_dec.decision_id,),
                f"The canonical ({cand}) is no longer a current member of this group.", reason="left_group"))
    for m in members:
        v, must_keep, redundant = verdicts[m.location_id]
        if v.status != UNDECIDED:
            continue
        is_canonical = canonical == m.location_id
        if redundant is not None and is_canonical:
            # Two explicit location decisions of the same class disagree. The
            # safe side wins (the canonical must be a keeper); say so.
            v.status, v.rule, v.resolution_class = KEEPER, "explicit_canonical_location", "explicit_location_decision"
            conflicts.append(Conflict(
                CANONICAL_NO_LONGER_KEEPER, (m.location_id,), (canonical_id, redundant.decision_id),
                f"{m.path} is both the canonical and a redundant candidate. Withdraw one.", reason="marked_redundant"))
        elif redundant is not None:
            v.status, v.rule, v.resolution_class = REDUNDANT, "explicit_redundant_location", "explicit_location_decision"
            if keep_all_dec is not None:
                v.notes = v.notes + ("keep_all_group is outranked by this explicit location decision",)
        elif is_canonical:
            v.status, v.rule, v.resolution_class = KEEPER, "explicit_canonical_location", "explicit_location_decision"

    # Step 3: explicit group decision -- keep all.
    if keep_all_dec is not None:
        for m in members:
            v = verdicts[m.location_id][0]
            if v.status == UNDECIDED:
                v.status, v.rule, v.resolution_class = KEEPER, "explicit_keep_all_group", "explicit_group_decision"

    # Steps 4-5: policies rank candidates for the RECOMMENDATION only. They
    # never change a status (S3: policy never masquerades as a human choice).
    candidates = [m for m in members if verdicts[m.location_id][0].status != REDUNDANT]
    folder_prefer = [p for p in policies if p.kind == "prefer_folder_subtree" and _prefer_applies(p, members)]
    folder_avoid = [p for p in policies if p.kind == "avoid_folder_subtree"]
    root_prefer = [p for p in policies if p.kind == "prefer_source_root"]
    policy_applies = bool(folder_prefer or folder_avoid or root_prefer)
    tier_rank = {}
    for m in members:
        v = verdicts[m.location_id][0]
        preferred = any(_policy_covers(p, m) for p in folder_prefer)
        avoided = any(_policy_covers(p, m) for p in folder_avoid)
        if preferred and avoided:
            v.policy_tier = "neutral"
            v.notes = v.notes + ("a prefer and an avoid folder policy both cover this location",)
            if canonical is None and v.status != REDUNDANT:
                # Two policies at one tier of the order disagree about a copy
                # the recommendation still has to rank: an exception for the
                # Conflicts route, advisory -- it changes no eligibility.
                conflicts.append(Conflict(
                    SAME_PRECEDENCE_POLICY_TIE, (m.location_id,), (),
                    f"A prefer-folder and an avoid-folder policy both cover {m.path}; the recommendation "
                    "cannot rank it. Retire or narrow one of them, or set the canonical explicitly.",
                    severity=ADVISORY))
        elif preferred:
            v.policy_tier = "preferred"
        elif avoided:
            v.policy_tier = "avoided"
        tier_rank[m.location_id] = {"preferred": 0, "neutral": 1, "avoided": 2}[v.policy_tier]
    best = None
    policy_preferred = ()
    if candidates and policy_applies:
        best_rank = min(tier_rank[m.location_id] for m in candidates)
        best = [m for m in candidates if tier_rank[m.location_id] == best_rank]
        if len(best) > 1 and root_prefer:
            in_root = [m for m in best if any(_policy_covers(p, m) for p in root_prefer)]
            if in_root:
                best = in_root
        # Step 6 (project_policy): no kind exists; the slot is here.
        # Step 7 (optional_heuristic): none ships; disabled.
        # Step 8 (display_only_tiebreak): NOT a recommendation -- see below.
        policy_preferred = tuple(m.location_id for m in best)
    # A recommendation exists only when policy singles out ONE of several
    # candidates. A group the policies do not distinguish is a tie, and a
    # tie is not a recommendation: the display order breaks it for the eye
    # only (step 8), never for the record.
    suggested = best[0].location_id if best is not None and len(best) == 1 and len(candidates) > 1 else None
    if canonical is not None:
        effective, origin = canonical, "explicit_human"
    elif suggested is not None:
        effective, origin = suggested, "policy"
    else:
        effective, origin = None, None

    # Physical-copy arithmetic (a hard-link alias is not a separate copy).
    present = [m for m in members if m.present]
    complete = bool(present) and all(m.physical_id for m in present)
    if complete:
        objects = {}
        for m in present:
            objects.setdefault(m.physical_id, []).append(m.location_id)
        physical_copies = len(objects)
        aliases = len(present) - physical_copies
    else:
        objects, physical_copies, aliases = {}, None, None
    size = max((m.size_bytes for m in members if m.size_bytes is not None), default=None)
    potential = (physical_copies - 1) * (size or 0) if complete and physical_copies else (None if not complete else 0)

    # The floors, then plan eligibility.
    statuses = {lid: v.status for lid, (v, _mk, _rd) in verdicts.items()}
    redundant_ids = [lid for lid in member_ids if statuses[lid] == REDUNDANT]
    remaining = len(member_ids) - len(redundant_ids)
    if member_ids and remaining < MINIMUM_FILE_LOCATIONS:
        conflicts.append(Conflict(
            MINIMUM_FILE_LOCATIONS_KIND, tuple(redundant_ids),
            tuple(sorted(d for lid in redundant_ids for d in verdicts[lid][0].decision_ids)),
            "Every location in the group is marked redundant. A plan must leave at least one; the product "
            "never removes the last copy."))
    if complete:
        remaining_objects = sum(1 for lids in objects.values() if any(statuses[l] != REDUNDANT for l in lids if l in statuses))
        if present and remaining_objects < MINIMUM_PHYSICAL_COPIES and remaining >= MINIMUM_FILE_LOCATIONS:
            conflicts.append(Conflict(
                MINIMUM_PHYSICAL_COPIES_KIND, tuple(redundant_ids), (),
                "Every physical copy would be removed."))
    conflicts = tuple(sorted(conflicts, key=lambda c: (c.severity != BLOCKING, c.kind, c.location_ids)))
    blocking = [c for c in conflicts if c.severity == BLOCKING]
    if blocking:
        eligible, reclaim = (), 0
    else:
        eligible = tuple(redundant_ids)
        if complete:
            reclaim = sum((size or 0) for _pid, lids in objects.items() if lids and all(statuses[l] == REDUNDANT for l in lids))
        else:
            reclaim = None if eligible else 0
    ready = bool(eligible) and not blocking

    # Review state.
    decision_ids = {d for v, _mk, _rd in verdicts.values() for d in v.decision_ids}
    decision_ids |= {d.decision_id for ds in gdec.values() for d in ds}
    undecided = [lid for lid in member_ids if statuses[lid] == UNDECIDED]
    blocked = (not group.evidence_current) or any(not m.present for m in members)
    if blocking:
        review_state = CONFLICT
    elif group_defer is not None:
        review_state = DEFERRED
    elif not decision_ids:
        review_state = UNREVIEWED
    elif undecided:
        review_state = IN_PROGRESS
    else:
        review_state = RESOLVED
    if blocked:
        resolution_state = RS_BLOCKED
    elif blocking:
        resolution_state = RS_CONFLICT
    elif canonical is not None or keep_all_dec is not None or any(
            v.rule.startswith("explicit_") for v, _mk, _rd in verdicts.values()):
        resolution_state = RS_EXPLICIT
    elif suggested is not None:
        resolution_state = RS_POLICY_UNIQUE
    elif policy_preferred and len(policy_preferred) < len(candidates):
        resolution_state = RS_POLICY_MULTIPLE
    else:
        resolution_state = RS_TIE

    ordered = [verdicts[lid][0] for lid in member_ids]
    return GroupProjection(
        group_id=group.group_id, content_id=group.content_id, members=ordered,
        keeper_set=tuple(sorted(lid for lid in member_ids if statuses[lid] in (PROTECTED, KEEPER))),
        protected=tuple(sorted(lid for lid in member_ids if statuses[lid] == PROTECTED)),
        redundant_candidates=tuple(sorted(redundant_ids)),
        undecided=tuple(sorted(undecided)),
        canonical=canonical, canonical_decision_id=canonical_id,
        suggested_canonical=suggested, canonical_origin=origin, effective_canonical=effective,
        policy_preferred=policy_preferred, conflicts=conflicts,
        location_count=len(member_ids), physical_copies=physical_copies, hardlink_aliases=aliases,
        physical_identity_complete=complete, size_bytes=size,
        plan_eligible_locations=eligible, plan_eligible_reclaim_bytes=reclaim,
        potential_reclaim_bytes=potential, ready_for_plan=ready,
        review_state=review_state, resolution_state=resolution_state,
        deferred=group_defer is not None, decision_count=len(decision_ids), notes=tuple(notes))


def resolve_all(ev: Evidence) -> ProjectProjection:
    """Every current group, in display order, plus the project totals."""
    active = active_decisions(ev.decisions)
    index = _index_decisions(active)
    groups = sorted(ev.groups.values(), key=lambda g: (
        min((ev.locations[i].sort_key for i in g.member_ids if i in ev.locations), default=()), g.group_id))
    projections = [resolve_group(ev, g, index, active) for g in groups]
    current_locations = {lid for g in ev.groups.values() for lid in g.member_ids}
    orphaned = tuple(sorted(
        d.decision_id for d in active
        if (d.target_kind == LOCATION and d.target_ref not in current_locations)
        or (d.target_kind == GROUP and d.target_ref not in ev.groups)))
    totals = {
        "groups": len(projections),
        "unreviewed": sum(1 for p in projections if p.review_state == UNREVIEWED),
        "in_progress": sum(1 for p in projections if p.review_state == IN_PROGRESS),
        "deferred": sum(1 for p in projections if p.review_state == DEFERRED),
        "resolved": sum(1 for p in projections if p.review_state == RESOLVED),
        "conflicts": sum(1 for p in projections if p.review_state == CONFLICT),
        "ready_for_plan": sum(1 for p in projections if p.ready_for_plan),
        "plan_eligible_locations": sum(len(p.plan_eligible_locations) for p in projections),
        "plan_eligible_reclaim_bytes": sum(p.plan_eligible_reclaim_bytes or 0 for p in projections),
        "reclaim_unknown_groups": sum(1 for p in projections if p.plan_eligible_reclaim_bytes is None),
        "potential_reclaim_bytes": sum(p.potential_reclaim_bytes or 0 for p in projections),
        "protected_locations": sum(len(p.protected) for p in projections),
        "keeper_locations": sum(len(p.keeper_set) for p in projections),
        "redundant_locations": sum(len(p.redundant_candidates) for p in projections),
        "undecided_locations": sum(len(p.undecided) for p in projections),
        "orphaned_decisions": len(orphaned),
        "active_decisions": len(active),
    }
    return ProjectProjection(projections, orphaned, totals)
