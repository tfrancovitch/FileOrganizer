r"""Visual candidate pairs: relationship and retention, resolved (Build 5).

A pure function of the model, like `resolve.py`, and for the same reason:
the product and the research's fixture lab run the same code.

Every visual decision names ONE pair. Two rules from the research govern
everything here:

  1. Relationship and retention are separate decisions. A pair's
     relationship -- equivalent_copy, related_variant, different -- is one
     row; which of its images survive -- canonical_location (the preferred
     one) and/or keep_both -- is another. Either may change without the
     other.

  2. Similarity is not transitive. A~B and B~C say nothing about A~C, so
     nothing is inferred across pairs: no group of "the same image" exists
     anywhere in this module. The one place pairs meet is a guard that
     REFUSES to rely on a chain: if the copy one pair keeps is the copy
     another pair would give up, the first pair is in conflict -- the plan
     could otherwise remove both halves of a chain on the strength of an
     equivalence nobody asserted.

What a pair resolves to:

    relationship     retention            keepers        redundant candidate
    equivalent       canonical X          X              the other image
    equivalent       keep_both (+canon.)  both           --
    equivalent       none yet             --             --  (in progress)
    related_variant  keep_both / none     both           --  never ordinary cleanup
    different        --                   --             --  (resolved: a false positive)

A redundant candidate here is exactly what the exact-duplicate resolver
calls one: a copy a future plan MAY remove. The same hard constraints stand
in front of it: a protection policy in force on it, or an explicit Keep on
it, is a conflict, not an effect.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from .model import Evidence, VisualPair, active_decisions, active_policies
from .registry import LOCATION, VISUAL_PAIR, POLICY_KINDS
from . import resolve as R

EQUIVALENT = "equivalent_copy"
RELATED = "related_variant"
DIFFERENT = "different"

#: Conflict kinds that exist only for pairs.
RETENTION_ON_DIFFERENT = "retention_on_different_pair"
EXPLICIT_KEEP_VS_PAIR = "explicit_keep_vs_pair_preference"
KEEPER_REDUNDANT_ELSEWHERE = "pair_keeper_redundant_elsewhere"


@dataclass
class PairProjection:
    pair_id: str
    a_id: str
    b_id: str
    relationship: str | None
    relationship_decision_id: str | None
    canonical: str | None
    canonical_decision_id: str | None
    keep_both: bool
    keep_both_decision_id: str | None
    keeper_set: tuple
    redundant_candidate: str | None      # the image a plan may remove: equivalent + a preferred other, no keep_both
    default_retention: bool              # a related variant with no retention recorded keeps both by default
    conflicts: tuple
    review_state: str                    # resolve.UNREVIEWED | IN_PROGRESS | RESOLVED | CONFLICT
    ready_for_plan: bool
    decision_ids: tuple
    current: bool
    exact_content: bool
    distance: int | None
    method: str
    notes: tuple = ()

    @property
    def members(self):
        return (self.a_id, self.b_id)

    @property
    def ordinary_redundant_candidate(self):
        """Would this pair feed the ordinary redundant-copy cleanup? Only an
        equivalent copy with a preferred other does -- never a related variant (F07)."""
        return self.redundant_candidate is not None

    def as_dict(self):
        d = asdict(self)
        d["conflicts"] = [asdict(c) for c in self.conflicts]
        return d


def _latest(decisions):
    return max(decisions, key=lambda d: (d.sequence, d.decision_id)) if decisions else None


def _name(ev, lid):
    loc = ev.locations.get(str(lid))
    return loc.path.replace("/", "\\").rsplit("\\", 1)[-1] if loc else f"location {lid}"


def _first_pass(ev: Evidence, pair: VisualPair, index, policies):
    pdec = index.get((VISUAL_PAIR, pair.pair_id), {})
    rel = _latest([d for k in (EQUIVALENT, RELATED, DIFFERENT) for d in pdec.get(k, [])])
    canon = _latest(pdec.get("canonical_location", []))
    both = _latest(pdec.get("keep_both", []))
    relationship = rel.kind if rel is not None else None
    canonical = str(canon.value) if canon is not None and str(canon.value) in pair.members else None
    conflicts, notes = [], []
    if canon is not None and canonical is None:
        notes.append(f"the canonical ({canon.value}) is not one of this pair's images; ignored")
    keep_both = both is not None
    retention_ids = tuple(d.decision_id for d in (canon, both) if d is not None)

    keepers, redundant, default = (), None, False
    if relationship == DIFFERENT:
        if retention_ids:
            conflicts.append(R.Conflict(
                RETENTION_ON_DIFFERENT, pair.members, retention_ids + (rel.decision_id,),
                "This pair is marked Different, yet a retention decision on it still stands. Retention only means "
                "something between related images: undo it, or change the relationship."))
    elif relationship == RELATED:
        keepers = pair.members
        default = not keep_both
        if canonical is not None and not keep_both:
            notes.append("a related variant is not interchangeable: the canonical names the representative, "
                         "and both images stay")
    elif relationship == EQUIVALENT:
        if keep_both:
            keepers = pair.members
        elif canonical is not None:
            keepers = (canonical,)
            redundant = pair.other(canonical)
    elif retention_ids:
        notes.append("retention is recorded but the relationship is not decided yet")

    if redundant is not None:
        loc = ev.locations.get(redundant)
        if loc is not None:
            _covering, in_force, _overridden = R.location_protection(policies, index, loc)
            if in_force:
                conflicts.append(R.Conflict(
                    R.PROTECTED_VS_REDUNDANT, (redundant,), tuple(x for x in (canon.decision_id, rel.decision_id)),
                    f"{loc.path} would be given up in favour of {_name(ev, canonical)}, but it is protected by "
                    f"{POLICY_KINDS[in_force[0].kind].label.lower()} ({in_force[0].scope.get('root_path') or in_force[0].scope.get('path')}). "
                    "Protection is a hard constraint: prefer the other image, keep both, or override the protection."))
        keep = _latest(index.get((LOCATION, redundant), {}).get("must_keep_location", []))
        if keep is not None:
            conflicts.append(R.Conflict(
                EXPLICIT_KEEP_VS_PAIR, (redundant,), (keep.decision_id, canon.decision_id),
                f"{_name(ev, redundant)} carries an explicit Keep, yet this pair prefers {_name(ev, canonical)} over it. "
                "Keep both, or withdraw one of the two decisions."))
    ids = tuple(sorted({d.decision_id for ds in pdec.values() for d in ds}))
    return dict(relationship=relationship, rel=rel, canonical=canonical, canon=canon, keep_both=keep_both, both=both,
                keepers=tuple(sorted(keepers, key=lambda x: (0, int(x), x) if str(x).isdigit() else (1, 0, x))),
                redundant=redundant, default=default, conflicts=conflicts, notes=notes, ids=ids)


def resolve_pairs(ev: Evidence, _index=None, _active=None, previous=None) -> list:
    """Every pair the model holds, resolved, in presentation order (the
    closest measurement first, then the images' display order).

    `previous` (pair_id -> PairProjection, from the same evidence) lets a
    caller that only changed the decision record skip the pairs no decision
    touches, then or now: their projection cannot have changed. The answer
    is the same as without it (the checks compare the two)."""
    if not ev.pairs:
        return []
    active = _active if _active is not None else active_decisions(ev.decisions)
    index = _index if _index is not None else R._index_decisions(active)
    policies = active_policies(ev.policies)
    first = {pid: (_first_pass(ev, p, index, policies) if (VISUAL_PAIR, pid) in index else None)
             for pid, p in ev.pairs.items()}
    # The chain guard: which images would a pair give up, and on whose word.
    given_up = {}
    for pid, f in first.items():
        if f is not None and f["redundant"] is not None:
            given_up.setdefault(f["redundant"], []).append(pid)
    # The order is the evidence's (measurement, then display order): with a
    # previous result over the same pairs, it is that result's order.
    same_pairs = previous is not None and len(previous) == len(ev.pairs) and all(pid in ev.pairs for pid in previous)
    out = []
    for pid in (previous if same_pairs else ev.pairs):
        pair = ev.pairs[pid]
        f = first[pid]
        if f is None:
            # No decision on the pair: nothing to resolve, nothing to guard.
            old = previous.get(pid) if previous else None
            if old is not None and not old.decision_ids:
                out.append(old)
                continue
            out.append(PairProjection(pid, pair.a_id, pair.b_id, None, None, None, None, False, None, (), None, False, (),
                                      R.UNREVIEWED, False, (), pair.current, pair.exact_content, pair.distance, pair.method))
            continue
        conflicts = list(f["conflicts"])
        if f["redundant"] is not None:
            keeper = f["canonical"]
            others = [q for q in given_up.get(keeper, []) if q != pid]
            if others:
                q = sorted(others, key=lambda x: (0, int(x), x) if str(x).isdigit() else (1, 0, x))[0]
                qf = first[q]
                conflicts.append(R.Conflict(
                    KEEPER_REDUNDANT_ELSEWHERE, (keeper,),
                    tuple(sorted({d for d in (f["canon"].decision_id, qf["canon"].decision_id) if d})),
                    f"This pair keeps {_name(ev, keeper)} and gives up {_name(ev, f['redundant'])}, but another pair gives up "
                    f"{_name(ev, keeper)} in favour of {_name(ev, qf['canonical'])}. Equivalence is decided pair by pair and is "
                    f"never inferred along a chain: decide {_name(ev, f['redundant'])} against {_name(ev, qf['canonical'])} "
                    "directly, or keep one of them."))
        conflicts = tuple(sorted(conflicts, key=lambda c: (c.severity != R.BLOCKING, c.kind, c.location_ids)))
        blocking = [c for c in conflicts if c.severity == R.BLOCKING]
        if blocking:
            state = R.CONFLICT
        elif not f["ids"]:
            state = R.UNREVIEWED
        elif f["relationship"] is None or (f["relationship"] == EQUIVALENT and not (f["canonical"] or f["keep_both"])):
            state = R.IN_PROGRESS
        else:
            state = R.RESOLVED
        out.append(PairProjection(
            pair_id=pid, a_id=pair.a_id, b_id=pair.b_id, relationship=f["relationship"],
            relationship_decision_id=f["rel"].decision_id if f["rel"] is not None else None,
            canonical=f["canonical"], canonical_decision_id=f["canon"].decision_id if f["canon"] is not None else None,
            keep_both=f["keep_both"], keep_both_decision_id=f["both"].decision_id if f["both"] is not None else None,
            keeper_set=f["keepers"], redundant_candidate=f["redundant"], default_retention=f["default"],
            conflicts=conflicts, review_state=state,
            ready_for_plan=f["redundant"] is not None and not blocking,
            decision_ids=f["ids"], current=pair.current, exact_content=pair.exact_content,
            distance=pair.distance, method=pair.method, notes=tuple(f["notes"])))

    if same_pairs:
        return out

    def order(pp):
        a, b = ev.locations.get(pp.a_id), ev.locations.get(pp.b_id)
        return (pp.distance if pp.distance is not None else 10 ** 6,
                a.sort_key if a else (), b.sort_key if b else (), pp.pair_id)
    out.sort(key=order)
    return out
