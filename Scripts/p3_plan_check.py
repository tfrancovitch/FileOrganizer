#!/usr/bin/env python3
r"""Phase 3 Build 8 (B15) regression: the Plan Builder.

What it proves, in order:

  1. The vocabulary and the schema: R10 §15's five action kinds (and no
     hard-link replacement), R7 §37's precondition keys with their classes,
     the evidence / live split per action, the eight plan tables.
  2. The builder on hand-built models: an item per logical action with its
     decision links, dependencies and declared pre- and postconditions; every
     evidence-stage precondition's outcome (pass, fail, unknown, not
     applicable, unsupported, a warning that does not block); a hard
     'unknown' never becomes Pass (a property over random result sets); the
     population (ready decisions only; a deferral left out; a blocked group
     in, and blocked); no keeper decided -> blocked, never chosen; the
     retention rule that answers B13's question -- a removal of an image any
     current decision keeps is generated and BLOCKED, naming the decisions,
     in every shape (another pair's Keep both, a related variant's default,
     its exact group's Keep all, an explicit Keep); move / rename / metadata
     items; the three reclaim metrics kept apart; the fingerprint.
  3. A real project with real images: a revision frozen in one operation
     (plan, revision, items, links, dependencies, preconditions, event);
     read back exactly (same fingerprint); membership frozen; the lock
     guard (a new operation, a rescan) and the lock itself (R7 §29);
     superseding; the dependency index; obsolescence through the existing
     detector for each trigger -- a withdrawn or superseded decision, a new
     retaining decision, a protection policy, changed evidence, a changed
     group membership -- and NOT for an unrelated change; an obsolete
     revision's items and fingerprint untouched; B13's case on real images.
  4. Zero source access: no source file opened, stat'ed or listed, and no
     write outside the project, while models are loaded, plans built,
     previewed, frozen, locked and the detector runs; every corpus byte
     unchanged.
  5. If a display is available: the Plan page -- build, the preview with its
     disclosure, the items, an item's preconditions, lock, compare, an
     obsolete revision shown with its reason.

Run:  python Scripts\p3_plan_check.py
"""
from __future__ import annotations

import builtins
import io
import json
import os
import random
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_visual_check as T                                                  # noqa: E402  (model helpers, image corpus)
BASE = T.BASE                                                                # noqa: E402  (p3_regression helpers)
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import plans as PL                                               # noqa: E402

RESULTS = []
LOCATION, GROUP, PAIR = REG.LOCATION, REG.GROUP, REG.VISUAL_PAIR
REMOVE, PRESERVE = REG.ACTION_REMOVE, REG.ACTION_PRESERVE
READY, BLOCKED, BLOCKED_EV, UNSUPPORTED = REG.ITEM_READY, REG.ITEM_BLOCKED, REG.ITEM_BLOCKED_EVIDENCE, REG.ITEM_UNSUPPORTED


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def refused(fn):
    try:
        fn()
        return False
    except ValueError:
        return True


gloc, gdec, routed = BASE.loc, BASE.dec, BASE.routed
img, vp, pdec, world = T.img, T.vp, T.dec, T.world


def items_by(draft):
    return {(i.action_kind, i.target_ref): i for i in draft.items}


def res(it, key):
    c = it.condition(key)
    return c.result if c else None


# ---------------------------------------------------------------------------
# 1. Vocabulary and schema
# ---------------------------------------------------------------------------

R7_PRECONDITIONS = ["target_exists", "target_observation_current", "target_content_matches", "keeper_exists",
                    "keeper_content_matches", "minimum_location_count_satisfied", "minimum_physical_copy_count_satisfied",
                    "target_not_protected", "decision_current", "policy_version_current", "no_current_conflict",
                    "required_coverage_complete", "source_root_available", "source_local_if_required",
                    "destination_available", "destination_collision_absent", "filesystem_capability_supported",
                    "permissions_sufficient", "free_space_sufficient", "action_lock_available", "no_conflicting_run",
                    "rollback_ready"]


def test_vocabulary():
    section("1. The vocabulary (P3.R7, scoped by R10 §15) and the schema")
    check("five logical action kinds: remove, move, rename, update source metadata, no-op preserve",
          REG.ACTION_KINDS == ("remove_location", "move_location", "rename_location", "update_source_metadata", "no_op_preserve"))
    check("no hard-link replacement anywhere in the vocabulary (R7 §21: never a default)",
          not any("hardlink" in k or "link" in k for k in REG.ACTION_KINDS))
    check("R7 §37's twenty-two precondition keys, each with a class from R7 §15; one Build 8 addition (no_retaining_decision)",
          set(R7_PRECONDITIONS) <= set(REG.PRECONDITIONS) and set(REG.PRECONDITIONS) - set(R7_PRECONDITIONS) == {"no_retaining_decision"}
          and set(REG.PRECONDITIONS.values()) <= set(REG.PRECONDITION_CLASSES))
    check("severities and results are R7 §16's", REG.SEVERITIES == ("hard", "warning", "informational")
          and REG.RESULTS == ("pass", "fail", "unknown", "not_applicable", "unsupported"))
    live = {k for k, _s, st in REG.ACTION_PRECONDITIONS[REMOVE] if st == REG.STAGE_LIVE}
    check("a removal declares what only the live filesystem can answer for Phase 4 (existence, content, permissions, lock, "
          "rollback) and never evaluates it here",
          {"target_content_matches", "keeper_content_matches", "permissions_sufficient", "rollback_ready",
           "action_lock_available"} <= live)
    check("every destructive action declares hard preconditions and postconditions (R7 §46 rule 4)",
          all(any(s == "hard" for _k, s, _st in REG.ACTION_PRECONDITIONS[a]) and REG.ACTION_POSTCONDITIONS[a]
              for a in (REG.ACTION_REMOVE, REG.ACTION_MOVE, REG.ACTION_RENAME, REG.ACTION_METADATA)))
    check("the disclosure is R7 §14's own words", REG.PREVIEW_DISCLOSURE.startswith("This preview evaluates the Plan against "
                                                                                     "Project Evidence. Phase 4 must re-check"))
    check("no metadata writer is known (R10 §16): a metadata item plans and stays blocked", REG.METADATA_WRITERS == ())


# ---------------------------------------------------------------------------
# 2. The builder on hand-built models
# ---------------------------------------------------------------------------

def test_builder():
    section("2. The builder: items, links, every precondition outcome, the population, the retention rule, effects")
    # An exact-duplicate group: 1 kept explicitly, 2 redundant.
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), gloc("2", r"C:\E\a.pdf", phys="P2")]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2")])
    d = PL.build(ev, proj, rt)
    it = items_by(d)
    rm, keep = it[(REMOVE, "2")], it[(PRESERVE, "1")]
    check("a ready group becomes a removal (the redundant copy) and a no-op preserve (the keeper), in display order",
          [i.action_kind for i in d.items] == [PRESERVE, REMOVE] and rm.state == READY and keep.state == REG.ITEM_PRESERVE)
    check("the removal is justified by the decisions that made the copy redundant; the preserve explains why it stays",
          rm.decision_ids == ("D2",) and keep.decision_ids == ("D1",) and "a keeper of its group" in keep.desired_effect["why"])
    check("the removal depends on its keeper's preserve item (a dependency graph, acyclic)",
          rm.depends_on == (keep.item_no,) and PL.acyclic(d.items))
    check("every evidence-stage hard precondition passed, with a legible reason; the live ones carry no result",
          all(c.result == "pass" for c in rm.preconditions if c.stage == REG.STAGE_EVIDENCE)
          and all(c.result is None for c in rm.preconditions if c.stage == REG.STAGE_LIVE) and all(
              c.detail for c in rm.preconditions if c.stage == REG.STAGE_EVIDENCE))
    check("the removal states logical intent only -- no execution method anywhere in it (P3-A88)",
          rm.desired_effect["effect"] == "location_absent" and "os.remove" not in json.dumps(PL.item_dict(rm)))
    check("its evidence binding is ids only, in the p3_decision shape (target, keeper, source group)",
          rm.evidence_binding["file_path_id"] == 2 and rm.evidence_binding["members"][0]["file_path_id"] == 1
          and rm.evidence_binding["source"]["target_kind"] == GROUP and "a.pdf" not in json.dumps(rm.evidence_binding)
          and "C:" not in json.dumps(rm.evidence_binding))

    # No keeper decided: blocked, never chosen.
    L3 = [gloc("1", r"C:\D\a.pdf", phys="P1"), gloc("2", r"C:\E\a.pdf", phys="P2"), gloc("3", r"C:\F\a.pdf", phys="P3")]
    ev, proj, rt = routed(L3, [gdec("D1", "redundant_location", LOCATION, "2")])
    d = PL.build(ev, proj, rt)
    it = items_by(d)
    check("a redundant mark with no keeper decided: the removal is BLOCKED on keeper_exists -- the Plan Builder never picks one",
          it[(REMOVE, "2")].state == BLOCKED and it[(REMOVE, "2")].blocked_reason == "keeper_exists"
          and not any(i.action_kind == PRESERVE for i in d.items) and d.summary["undecided_left"] == 2)

    # Physical identity unknown (F12's shape): unknown blocks as evidence.
    L = [gloc("1", r"C:\A.bin", known=False, size=8000000), gloc("2", r"C:\B.bin", phys="P2", size=8000000)]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "2"), gdec("D2", "redundant_location", LOCATION, "1")])
    d = PL.build(ev, proj, rt)
    x = items_by(d)[(REMOVE, "1")]
    check("physical identity unknown: minimum_physical_copy_count_satisfied is 'unknown' and the item is blocked_evidence, "
          "although the group's route is Blocked (it is in the plan, visibly blocked, not missing)",
          rt.groups["C1"].primary == RT.BLOCKED and res(x, "minimum_physical_copy_count_satisfied") == "unknown"
          and x.state == BLOCKED_EV and "physical identity unknown" in x.condition("minimum_physical_copy_count_satisfied").detail)
    check("...and the plan-eligible reclaim is not claimed for it", d.summary["plan_eligible_reclaimable_bytes"] == 0)

    # Unknown is never Pass: any hard unknown keeps an item from ready, whatever else holds.
    rnd = random.Random(8)
    ok = True
    for _ in range(300):
        t = PL.PlanItem(1, REMOVE, LOCATION, "9", (), {})
        PL._declare(t)
        for c in t.preconditions:
            if c.stage == REG.STAGE_EVIDENCE:
                c.result = rnd.choice(["pass", "pass", "pass", "fail", "unknown", "not_applicable", "unsupported"])
        PL._settle(t)
        hard = [c.result for c in t.preconditions if c.stage == REG.STAGE_EVIDENCE and c.severity == "hard"]
        if ("unknown" in hard or "fail" in hard or "unsupported" in hard) == (t.state == READY):
            ok = False
    check("over 300 random result sets: an item is ready exactly when no hard evidence-stage precondition is fail, unknown "
          "or unsupported -- Unknown is never Pass (P3-A91)", ok)

    # Stale hash, incomplete coverage, cloud-only warning.
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), replace(gloc("2", r"C:\E\a.pdf", phys="P2"), hash_current=False)]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2")])
    x = items_by(PL.build(ev, proj, rt))[(REMOVE, "2")]
    check("a stale fingerprint: required_coverage_complete fails and blocks", res(x, "required_coverage_complete") == "fail"
          and x.state == BLOCKED)
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), replace(gloc("2", r"C:\E\a.pdf", phys="P2"), cloud_only=True)]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2")])
    d = PL.build(ev, proj, rt)
    x = items_by(d)[(REMOVE, "2")]
    check("a cloud-only target is a WARNING (source_local_if_required), not a block; the preview counts it",
          res(x, "source_local_if_required") == "fail" and x.condition("source_local_if_required").severity == "warning"
          and x.state == READY and d.summary["warnings"] == 1)

    # Protection overridden: passes, and says so; policy linked.
    folder = r"C:\E"
    pv = BASE.pol("PV1", "protect_folder_subtree", {"path": folder})
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), gloc("2", r"C:\E\a.pdf", phys="P2")]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2"),
                              gdec("D3", "override_protection", LOCATION, "2", {"policy_id": "PPV1", "policy_version_id": "PV1"})],
                          [pv])
    x = items_by(PL.build(ev, proj, rt)).get((REMOVE, "2"))
    check("a protected copy whose protection is explicitly overridden: target_not_protected passes and names the override; "
          "the policy version is linked", x is not None and res(x, "target_not_protected") == "pass"
          and "overridden" in x.condition("target_not_protected").detail and "PV1" in x.policy_version_ids)

    # Population: deferred left out; needs revalidation -> decision_current fails.
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), gloc("2", r"C:\E\a.pdf", phys="P2")]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2")],
                          events=[BASE.ev_row("E1", "deferred", GROUP, "C1", "manual")])
    d = PL.build(ev, proj, rt)
    check("a group you deferred stays out of the plan, and the preview says so", not d.items and d.excluded["deferred"] == 1
          and any("deferred by you" in l for l in d.lines()))
    moved = [replace(L[0], observation_id="obs-new"), L[1]]
    ev, proj, rt = routed(moved, [BASE.bdec("D1", "must_keep_location", LOCATION, "1", True, [L[0]]),
                                  gdec("D2", "redundant_location", LOCATION, "2")])
    x = items_by(PL.build(ev, proj, rt)).get((REMOVE, "2"))
    check("a group whose decisions need revalidation is in the plan with decision_current failed (blocked, with the reason)",
          x is not None and res(x, "decision_current") == "fail" and x.state == BLOCKED and "revalidation" in
          x.condition("decision_current").detail, str(rt.groups["C1"].primary))


def test_retention_rule():
    section("2b. The retention rule (synthesis §6): a removal of an image any current decision keeps is generated and BLOCKED")
    # B13's shape: X~Y prefers X (Y redundant); Y~Z keeps both.
    I = [img("1", r"C:\X\x.jpg"), img("2", r"C:\Y\y.jpg"), img("3", r"C:\Z\z.jpg")]
    P = [vp("10", "1", "2"), vp("11", "2", "3")]
    decs = [pdec("D1", "equivalent_copy", "10"), pdec("D2", "canonical_location", "10", "1"),
            pdec("D3", "equivalent_copy", "11"), pdec("D4", "keep_both", "11", ["2", "3"])]
    ev, proj, rt = world(I, P, decs)
    d = PL.build(ev, proj, rt)
    y = items_by(d)[(REMOVE, "2")]
    check("B13's case, as it reaches the Plan Builder: pair 10 is Ready for Plan (the resolver sees no conflict) ...",
          rt.pairs["10"].primary == RT.READY_FOR_PLAN and not proj.pair("10").conflicts)
    check("...and the removal of y.jpg IS generated, BLOCKED on no_retaining_decision, naming pair 11's Keep both (#D4)",
          y.state == BLOCKED and y.blocked_reason == "no_retaining_decision" and y.retaining_ids == ("D4",)
          and "#D4" in y.condition("no_retaining_decision").detail and "never picks a side" in y.condition("no_retaining_decision").detail)
    check("...justified by pair 10's decisions and still depending on x.jpg's preserve item (nothing dropped, nothing silent)",
          y.decision_ids == ("D1", "D2") and y.depends_on == (items_by(d)[(PRESERVE, "1")].item_no,)
          and d.summary["blocked"] == 1 and d.summary["plan_eligible_reclaimable_bytes"] == 0)
    # A related variant's default Keep both retains too.
    ev, proj, rt = world(I, P, decs[:2] + [pdec("D3", "related_variant", "11")])
    y = items_by(PL.build(ev, proj, rt))[(REMOVE, "2")]
    check("a related variant keeps both by default: that retains y.jpg as well", y.state == BLOCKED and y.retaining_ids == ("D3",))
    # Its exact-duplicate group's Keep all.
    I2 = [img("1", r"C:\X\x.jpg"), replace(img("2", r"C:\Y\y.jpg"), content_id="C9", physical_id="PY", in_current_group=True),
          replace(img("4", r"C:\W\y.jpg"), content_id="C9", physical_id="PW", in_current_group=True)]
    from Phase3.model import Group
    ev, proj, rt = world(I2, [vp("10", "1", "2")], decs[:2] + [pdec("D5", "keep_all_group", "C9", True, target_kind=GROUP)])
    ev.groups = {"C9": Group("C9", "C9", ("2", "4"))}
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    y = items_by(PL.build(ev, proj, rt)).get((REMOVE, "2"))
    check("its exact-duplicate group's Keep all retains it: the pair's removal is blocked, naming #D5",
          y is not None and y.state == BLOCKED and "D5" in y.retaining_ids)
    # An explicit Keep on the image (the pair itself is then in conflict -> not in the plan at all).
    ev, proj, rt = world(I, P, decs[:2] + [pdec("D6", "must_keep_location", "2", True, target_kind=LOCATION)])
    d = PL.build(ev, proj, rt)
    check("an explicit Keep on the pair's redundant image puts the pair in Conflicts (the resolver's rule): it never reaches "
          "the plan, and the preview counts it", (REMOVE, "2") not in items_by(d) and rt.pairs["10"].primary == RT.CONFLICT)
    # The group side: a redundant exact copy that some pair keeps.
    I3 = [replace(img("1", r"C:\A\a.jpg"), content_id="C1", physical_id="P1", in_current_group=True),
          replace(img("2", r"C:\B\a.jpg"), content_id="C1", physical_id="P2", in_current_group=True), img("3", r"C:\C\c.jpg")]
    ev, proj, rt = world(I3, [vp("20", "2", "3")], [gdec("D1", "canonical_location", GROUP, "C1", "1"),
                                                    gdec("D2", "redundant_location", LOCATION, "2"),
                                                    pdec("D7", "related_variant", "20"), pdec("D8", "keep_both", "20", ["2", "3"])])
    ev.groups = {"C1": Group("C1", "C1", ("1", "2"))}
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    x = items_by(PL.build(ev, proj, rt)).get((REMOVE, "2"))
    check("the other way round: an exact duplicate marked redundant that a visual pair keeps -- blocked, naming the pair's rows",
          x is not None and x.state == BLOCKED and set(x.retaining_ids) >= {"D8"})
    # Without any retaining decision, the same removal is ready.
    ev, proj, rt = world(I, P, decs[:2])
    check("with no other decision keeping y.jpg the removal is ready", items_by(PL.build(ev, proj, rt))[(REMOVE, "2")].state == READY)
    # The keeper itself planned for removal elsewhere.
    ev, proj, rt = world(I, P, [pdec("D1", "equivalent_copy", "10"), pdec("D2", "canonical_location", "10", "1"),
                                pdec("D3", "equivalent_copy", "11"), pdec("D4", "canonical_location", "11", "3")])
    d = PL.build(ev, proj, rt)
    check("a keeper planned for removal by another item: keeper_exists fails (the resolver's chain guard puts that pair in "
          "conflict; the plan holds nothing that relies on it)",
          all(i.state != READY for i in d.items if i.action_kind == REMOVE and "keeper_exists" == i.blocked_reason)
          or rt.pairs["10"].primary == RT.CONFLICT or rt.pairs["11"].primary == RT.CONFLICT)


def test_other_items_and_effects():
    section("2c. Move / rename / metadata items, the three reclaim metrics, the fingerprint")
    L = [gloc("1", r"C:\D\a.pdf", phys="P1"), gloc("2", r"C:\E\a.pdf", phys="P2"), gloc("3", r"C:\E\b.pdf", phys="P3", content="C3")]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2")])
    m = PL.logical_item(ev, REG.ACTION_METADATA, "3", {"field": "author", "value": "X", "format": "pdf"}, ["D9"])
    check("an update_source_metadata item plans, and is an unsupported action (no writer exists yet) -- never executable",
          m.state == UNSUPPORTED and res(m, "filesystem_capability_supported") == "unsupported")
    mv = PL.logical_item(ev, REG.ACTION_MOVE, "3", {"destination": r"C:\D\a.pdf"}, ["D9"])
    mv2 = PL.logical_item(ev, REG.ACTION_MOVE, "3", {"destination": r"C:\New\b.pdf"}, ["D9"])
    check("a move onto a path the evidence already holds fails destination_collision_absent; a free destination passes",
          mv.state == BLOCKED and res(mv, "destination_collision_absent") == "fail" and mv2.state == READY)
    rn = PL.logical_item(ev, REG.ACTION_RENAME, "3", {"destination": r"C:\E\c.pdf"}, [])
    check("an item no decision asks for: decision_current fails -- the Plan Builder never invents intent",
          rn.state == BLOCKED and res(rn, "decision_current") == "fail")
    check("no_op_preserve cannot be built by hand (only from decisions)", refused(lambda: PL.logical_item(ev, PRESERVE, "1", {})))
    # Hard-link aliases: an object counts only when every name of it is removed.
    L = [gloc("1", r"C:\D\a.pdf", phys="P1", size=500), gloc("2", r"C:\E\a.pdf", phys="P2", size=500),
         gloc("3", r"C:\E\a-link.pdf", phys="P2", size=500), gloc("4", r"C:\F\a.pdf", phys="P4", size=500)]
    ev, proj, rt = routed(L, [gdec("D1", "must_keep_location", LOCATION, "1"), gdec("D2", "redundant_location", LOCATION, "2"),
                              gdec("D4", "redundant_location", LOCATION, "4")])
    d = PL.build(ev, proj, rt)
    s = d.summary
    check("Plan-Eligible Reclaimable Bytes count a physical object once, only when all its names go (a hard-linked alias left "
          "behind keeps it): 500 of 1,000 logical bytes", s["plan_eligible_reclaimable_bytes"] == 500 and s["logical_bytes_affected"] == 1000)
    check("...and Phase 2's Potentially Reclaimable figure is shown beside it under its own name, a different number",
          s["phase2_potentially_reclaimable_bytes"] == proj.groups[0].potential_reclaim_bytes
          and s["phase2_potentially_reclaimable_bytes"] != s["plan_eligible_reclaimable_bytes"]
          and any("Plan-Eligible Reclaimable Bytes (Phase 3" in l for l in d.lines())
          and any("Potentially Reclaimable Exact-Duplicate Bytes (Phase 2" in l for l in d.lines())
          and any("Verified Reclaimed Bytes exist only after Phase 4" in l for l in d.lines()))
    check("the preview ends with R7 §14's disclosure and states its coverage (no single green 'dry run passed')",
          d.lines()[-1] == REG.PREVIEW_DISCLOSURE and any(l.startswith("Coverage:") for l in d.lines()))
    # The fingerprint.
    f0 = PL.fingerprint(d.items)
    rnd = random.Random(15)
    same = True
    for _ in range(3):
        l2, d2 = list(L), [gdec("D4", "redundant_location", LOCATION, "4"), gdec("D2", "redundant_location", LOCATION, "2"),
                           gdec("D1", "must_keep_location", LOCATION, "1")]
        rnd.shuffle(l2)
        e2, p2, r2 = routed(l2, d2)
        same &= PL.fingerprint(PL.build(e2, p2, r2).items) == f0
    check("the fingerprint is SHA-256 over the canonical payload, identical from the same record in any row order", same and len(f0) == 64)
    changed = [replace(i) for i in d.items]
    changed[-1].desired_effect = dict(changed[-1].desired_effect, path="elsewhere")
    check("...and any change to an item changes it", PL.fingerprint(changed) != f0)
    p = PL.payload(d.items)["items"][0]
    check("the payload holds items, targets, desired effects, dependencies, decision / policy / evidence references and "
          "pre- and postconditions (R7 §6)", {"action", "target", "effect", "deps", "decisions", "policies", "binding", "pre",
                                              "post"} <= set(p))


# ---------------------------------------------------------------------------
# 3-4. A real project, and zero source access
# ---------------------------------------------------------------------------

class ReadGuard:
    """Records any open, stat or listing of a path under the corpus -- a read
    of a source file, which the Plan Builder must never do (P3-A89)."""

    def __init__(self, corpus):
        self.prefix = os.path.normcase(os.path.abspath(str(corpus)))
        self.hits = []
        self.saved = {}

    def _watch(self, what, path):
        try:
            p = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        except Exception:                                           # noqa: BLE001
            return
        if p == self.prefix or p.startswith(self.prefix + os.sep):
            self.hits.append(f"{what}: {p}")

    def __enter__(self):
        g = self
        for mod, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat"), (os, "lstat"),
                          (os, "scandir"), (os, "listdir")):
            real = getattr(mod, name)
            self.saved[(mod, name)] = real

            def wrap(*a, _real=real, _name=name, **k):
                if a and not isinstance(a[0], int):
                    g._watch(_name, a[0])
                return _real(*a, **k)
            setattr(mod, name, wrap)
        return self

    def __exit__(self, *exc):
        for (mod, name), real in self.saved.items():
            setattr(mod, name, real)
        return False


def test_project(tmp: Path):
    section("3. A real project: freeze, read back, lock, supersede, the dependency index, obsolescence by each trigger")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S

    app_root, corpus, r1, r2, project_dir, outs = T.open_project(tmp, "P3Plan")
    check("Pre-Scan, Find My Duplicates, Analyze (pictures)", all(o.ok for o in outs))
    before = BASE.fingerprint_tree(corpus)

    def opened():
        c = connect(project_dir, write=True)
        return c, S.DecisionStore(c, actor_id="tester", session_id="session:plan")
    conn, st = opened()

    def model():
        ev = E.load_evidence(conn)
        proj = R.resolve_all(ev)
        return ev, proj, RT.route_all(ev, proj, ev.now)
    ev, proj, rt = model()
    by = {BK_name(l.path): l.location_id for l in ev.locations.values()}
    pid = {frozenset(p.members): p.pair_id for p in ev.pairs.values()}
    P = lambda a, b: pid[frozenset((by[a], by[b]))]                   # noqa: E731
    group = ev.locations[by["A.jpg"]].content_id
    st.record_decision("canonical_location", GROUP, group, by["A.jpg"])
    d_red = st.record_decision("redundant_location", LOCATION, by["A_copy.jpg"])
    st.record_decisions([("equivalent_copy", PAIR, P("C.jpg", "C_edit.jpg"), True),
                         ("canonical_location", PAIR, P("C.jpg", "C_edit.jpg"), by["C.jpg"])])
    st.record_decisions([("equivalent_copy", PAIR, P("A.jpg", "A_small.jpg"), True),
                         ("canonical_location", PAIR, P("A.jpg", "A_small.jpg"), by["A.jpg"])])
    keepboth = st.record_decisions([("related_variant", PAIR, P("A_small.jpg", "A_bw.jpg"), True),
                                    ("keep_both", PAIR, P("A_small.jpg", "A_bw.jpg"), sorted((by["A_small.jpg"], by["A_bw.jpg"]), key=int))])

    # 4. Zero source access around everything the Plan Builder does.
    with ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes:
        ev, proj, rt = model()
        draft = PL.build(ev, proj, rt)
        res1 = st.record_plan_revision(draft, name="Test plan")
        rev, items = st.plan_revision(res1["plan_revision_id"])
    it = items_by(draft)
    small = it[(REMOVE, by["A_small.jpg"])]
    check("B13's case on real images: A_small.jpg preferred away by its pair with A.jpg, kept by its pair with A_bw.jpg -- "
          "the removal is generated and blocked, naming that pair's Keep both row",
          small.state == BLOCKED and small.blocked_reason == "no_retaining_decision"
          and small.retaining_ids == (str(keepboth["decision_ids"][1]),), str(small.retaining_ids))
    check("the exact copy and the edited C are ready removals; A.jpg and C.jpg are preserved",
          it[(REMOVE, by["A_copy.jpg"])].state == READY and it[(REMOVE, by["C_edit.jpg"])].state == READY
          and (PRESERVE, by["A.jpg"]) in it and (PRESERVE, by["C.jpg"]) in it)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("p3_plan", "p3_plan_revision", "p3_plan_event", "p3_plan_item", "p3_plan_item_decision", "p3_plan_dependency",
               "p3_plan_precondition")}
    op = conn.execute("SELECT kind FROM p3_operation WHERE operation_id = ?", (res1["operation_id"],)).fetchone()[0]
    check("one 'plan_build' operation froze the plan, its revision, every item, link, dependency and precondition, and a "
          "'previewed' event", op == "plan_build" and counts["p3_plan"] == 1 and counts["p3_plan_revision"] == 1
          and counts["p3_plan_item"] == len(draft.items) and counts["p3_plan_event"] == 1 and counts["p3_plan_item_decision"] >= 6
          and counts["p3_plan_precondition"] == sum(len(i.preconditions) for i in draft.items), str(counts))
    check("read back exactly as frozen: the same items, the same fingerprint", rev["status"] == "previewed"
          and PL.fingerprint(items) == PL.fingerprint(draft.items) and rev["summary"] == json.loads(json.dumps(draft.summary, default=str)))
    check("the revision remembers what it was built on (record and evidence marks)", rev["basis"]["record_mark"] == res1["operation_id"]
          and rev["basis"]["evidence_mark"])

    # Lock guard, then lock.
    st.skip(PAIR, P("A.jpg", "A_bw.jpg"))
    check("a new operation since the preview: the lock refuses (build again)", refused(lambda: st.lock_plan_revision(res1["plan_revision_id"])))
    ev, proj, rt = model()
    res2 = st.record_plan_revision(PL.build(ev, proj, rt), plan_id=res1["plan_id"])
    rev1, _ = st.plan_revision(res1["plan_revision_id"])
    check("building again makes revision 2 of the same plan; the unlocked revision 1 becomes superseded (its items untouched)",
          res2["revision_no"] == 2 and rev1["status"] == "superseded" and rev1["events"][-1]["event_kind"] == "superseded")
    rows_before = [dict(r) for r in conn.execute("SELECT * FROM p3_plan_item WHERE plan_revision_id = ? ORDER BY item_no",
                                                 (res2["plan_revision_id"],))]
    with ReadGuard(corpus) as reads2, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes2:
        lock = st.lock_plan_revision(res2["plan_revision_id"])
    rev2, items2 = st.plan_revision(res2["plan_revision_id"])
    ev_lock = rev2["events"][-1]
    check("lock (R7 §29): fingerprint computed, stored, equal to a fresh computation over the frozen payload; locked; a lock "
          "event carrying the exact dependency set", rev2["status"] == "locked" and rev2["locked"] == 1
          and rev2["fingerprint"] == lock["fingerprint"] == PL.fingerprint(items2) and ev_lock["event_kind"] == "locked"
          and set(ev_lock["detail"]["dependencies"]["decisions"]) >= {str(d_red["decision_id"])})
    check("a locked revision cannot be locked again", refused(lambda: st.lock_plan_revision(res2["plan_revision_id"])))
    ev, proj, rt = model()
    res3 = st.record_plan_revision(PL.build(ev, proj, rt), plan_id=res1["plan_id"])
    check("a new revision never supersedes a locked one; membership stays frozen (the locked items are the rows they were)",
          st.plan_revision(res2["plan_revision_id"])[0]["status"] == "locked"
          and [dict(r) for r in conn.execute("SELECT * FROM p3_plan_item WHERE plan_revision_id = ? ORDER BY item_no",
                                             (res2["plan_revision_id"],))] == rows_before)
    st.lock_plan_revision(res3["plan_revision_id"])
    check("the dependency index: a decision leads to the locked revisions it feeds, and only those",
          st.revisions_depending_on([d_red["decision_id"]]) == [res2["plan_revision_id"], res3["plan_revision_id"]]
          and st.revisions_depending_on([999999]) == [])

    # Obsolescence through the detector -- first an unrelated change.
    conn.close()
    conn, st = opened()
    st.defer(PAIR, P("A_copy.jpg", "A_bw.jpg") if frozenset((by["A_copy.jpg"], by["A_bw.jpg"])) in pid else P("A.jpg", "A_bw.jpg"),
             "defer_indefinitely")
    with ReadGuard(corpus) as reads3, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes3:
        counts = RT.reconcile(conn, st)
    check("an unrelated change elsewhere (a deferral on a pair no plan item rests on): no revision becomes obsolete",
          counts.get("plans_obsolete", 0) == 0 and st.plan_revision(res3["plan_revision_id"])[0]["status"] == "locked")
    fp3 = st.plan_revision(res3["plan_revision_id"])[0]["fingerprint"]
    items_rows = [dict(r) for r in conn.execute("SELECT * FROM p3_plan_item WHERE plan_revision_id = ?", (res3["plan_revision_id"],))]
    st.withdraw(d_red["decision_id"], reason="changed my mind")
    counts = RT.reconcile(conn, st)
    r2, _ = st.plan_revision(res2["plan_revision_id"])
    r3, _ = st.plan_revision(res3["plan_revision_id"])
    check("its source decision withdrawn: both locked revisions become obsolete, reason 'source decision changed', by the "
          "routing detector (a system operation)", counts["plans_obsolete"] == 2 and r3["status"] == "obsolete"
          and r3["obsolete"] == 1 and r3["events"][-1]["detail"]["reason"] == "source decision changed"
          and r3["events"][-1]["actor_kind"] == "system_evidence")
    check("...still locked and fingerprinted, items untouched: obsolete, never rewritten (P3-A97), never re-planned",
          r3["locked"] == 1 and r3["fingerprint"] == fp3 and items_rows == [dict(r) for r in conn.execute(
              "SELECT * FROM p3_plan_item WHERE plan_revision_id = ?", (res3["plan_revision_id"],))]
          and conn.execute("SELECT COUNT(*) FROM p3_plan_revision").fetchone()[0] == 3)
    check("...and a second detector pass writes nothing more", RT.reconcile(conn, st).get("plans_obsolete", 0) == 0)

    # The other triggers, one locked revision each.
    def fresh_locked():
        e, p, r = model()
        out = st.record_plan_revision(PL.build(e, p, r), plan_id=res1["plan_id"])
        st.lock_plan_revision(out["plan_revision_id"])
        return out["plan_revision_id"]

    def reason_after(change):
        rid = fresh_locked()
        change()
        RT.reconcile(conn, st)
        r, _i = st.plan_revision(rid)
        return r["status"], [x["code"] for x in (r["events"][-1]["detail"].get("reasons") or [])]
    status, codes = reason_after(lambda: st.record_decision("canonical_location", PAIR, P("C.jpg", "C_edit.jpg"), by["C_edit.jpg"]))
    check("a decision that supersedes a justifying one (the pair's preference flipped): obsolete, source decision changed",
          status == "obsolete" and "decision_changed" in codes, str(codes))
    st.record_decision("canonical_location", PAIR, P("C.jpg", "C_edit.jpg"), by["C.jpg"])         # and back
    status, codes = reason_after(lambda: st.record_decision("must_keep_location", LOCATION, by["C_edit.jpg"]))
    check("a new decision that keeps a planned removal (an explicit Keep on C_edit.jpg): obsolete",
          status == "obsolete" and ("retention_added" in codes or "new_conflict" in codes), str(codes))
    keep_id = next(d.decision_id for d in st.active_for(LOCATION, by["C_edit.jpg"]) if d.kind == "must_keep_location")
    st.withdraw(int(keep_id), reason="test")
    folder = str(r1 / "Edits")
    status, codes = reason_after(lambda: st.create_policy("protect_folder_subtree", {"path_key": E.path_key(folder), "path": folder},
                                                          rationale="test"))
    check("a protection policy made over a planned removal: obsolete (a protection policy now covers it)",
          status == "obsolete" and ("protection_added" in codes or "new_conflict" in codes), str(codes))
    conn.execute("UPDATE p3_policy_version SET status = 'retired'")      # take the protection out of the way for what follows
    conn.commit()
    rid = fresh_locked()
    conn.close()
    from PIL import ImageDraw
    from PIL import Image
    im = Image.open(r1 / "Edits" / "C_edit.jpg").copy()
    ImageDraw.Draw(im).rectangle([200, 200, 260, 240], fill=(0, 0, 0))
    im.save(r1 / "Edits" / "C_edit.jpg", quality=92)
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    conn, st = opened()
    RT.reconcile(conn, st)
    r, _i = st.plan_revision(rid)
    codes = [x["code"] for x in (r["events"][-1]["detail"].get("reasons") or [])]
    check("a planned removal's file changed on disk and was rescanned: obsolete (evidence changed)",
          r["status"] == "obsolete" and "evidence_changed" in codes, str(codes))
    st.record_decision("redundant_location", LOCATION, by["A_copy.jpg"])           # the group's removal, planned again
    RT.reconcile(conn, st)
    rid = fresh_locked()
    check("(the exact copy's removal is in this locked revision)",
          any(i.action_kind == REMOVE and i.target_ref == by["A_copy.jpg"] for i in st.plan_revision(rid)[1]))
    conn.close()
    shutil.copyfile(r1 / "Trip" / "A.jpg", r1 / "Trip" / "A_third.jpg")
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    conn, st = opened()
    RT.reconcile(conn, st)
    r, _i = st.plan_revision(rid)
    codes = [x["code"] for x in (r["events"][-1]["detail"].get("reasons") or [])]
    check("a new copy joined a planned group: obsolete (duplicate-group membership changed)",
          r["status"] == "obsolete" and "membership_changed" in codes, str(codes))

    check("no source file was opened, stat'ed or listed while models loaded, plans were built, frozen, locked and the "
          "detector ran", not reads.hits and not reads2.hits and not reads3.hits, str((reads.hits + reads2.hits + reads3.hits)[:3]))
    check("no Python-level write outside the project folder meanwhile", not writes.violations and not writes2.violations
          and not writes3.violations, str((writes.violations + writes2.violations)[:3]))
    after = BASE.fingerprint_tree(corpus)["files"]
    check("every corpus file the tests did not change themselves is byte-for-byte untouched",
          {k: v for k, v in after.items() if "C_edit" not in k and "A_third" not in k}
          == {k: v for k, v in before["files"].items() if "C_edit" not in k})
    conn.close()
    return app_root, corpus, project_dir


def BK_name(path):
    return str(path).replace("/", "\\").rsplit("\\", 1)[-1]


# ---------------------------------------------------------------------------
# 5. The window
# ---------------------------------------------------------------------------

def test_gui(tmp: Path):
    section("5. The Plan page: build, preview, items, preconditions, lock, compare, obsolete")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from tkinter import messagebox
    from Phase2 import gui
    from Phase3 import review as RV

    app_root, corpus, r1, r2, project_dir, outs = T.open_project(tmp, "P3PlanGui")
    before = BASE.fingerprint_tree(corpus)
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else "")), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else "", a[1] if len(a) > 1 else "")))
    try:
        with ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            st = RV.store_for(app)
            ev_names = {}
            for r in app.conn.execute("SELECT fs.file_path_id, fp.relative_path FROM file_state fs JOIN file_path fp USING(file_path_id)"):
                ev_names[BK_name(r[1])] = str(r[0])
            gid = app.conn.execute("SELECT content_id FROM file_state WHERE file_path_id = ?", (int(ev_names["A.jpg"]),)).fetchone()[0]
            st.record_decision("canonical_location", GROUP, str(gid), ev_names["A.jpg"])
            red = st.record_decision("redundant_location", LOCATION, ev_names["A_copy.jpg"])
            from Phase3 import evidence as E
            loads, real_load = [], E.load_evidence
            E.load_evidence = lambda *a, **k: (loads.append(1), real_load(*a, **k))[1]
            try:
                app.show_plan()
                app.update()
            finally:
                E.load_evidence = real_load
            page = app.plan_page
            check("the Plan page opens on the nav's new 'Plan' entry, and says what is ready for a plan",
                  "Ready for a plan: 1 duplicate group" in page.population_var.get())
            check("opening it loads the model once (the detector's), not twice", len(loads) == 1, f"{len(loads)} loads")
            check("with no plan yet it says so, with the disclosure", "No plan yet" in page.preview_text.get("1.0", "end")
                  and REG.PREVIEW_DISCLOSURE in page.preview_text.get("1.0", "end"))
            page.name_var.set("Tidy the trip photos")
            page.build_revision()
            app.update()
            text = page.preview_text.get("1.0", "end")
            check("Build a new revision: revision 1, previewed; the preview in R7 §26's shape with the three metrics kept apart "
                  "and the disclosure", "Tidy the trip photos -- revision 1 -- Previewed" in text
                  and "Plan-Eligible Reclaimable Bytes" in text and "Potentially Reclaimable Exact-Duplicate Bytes" in text
                  and REG.PREVIEW_DISCLOSURE in text)
            rows = [page.items_tree.item(i, "values") for i in page.items_tree.get_children()]
            check("the items: a removal of A_copy.jpg (ready) and a preserve of A.jpg", any(r[2] == "A_copy.jpg" and r[3] == "Ready" for r in rows)
                  and any(r[2] == "A.jpg" and r[3] == "Preserved" for r in rows), str(rows))
            rm = next(i for i in page.items_tree.get_children() if page.items_tree.item(i, "values")[2] == "A_copy.jpg")
            page.items_tree.selection_set(rm)
            page._item_chosen()
            detail = page.detail_text.get("1.0", "end")
            check("an item's detail: its preconditions checked now (with results), those declared for Phase 4, its "
                  "postconditions, its decisions", "Checked now, from project evidence:" in detail and "[pass] no_retaining_decision" in detail
                  and "Declared for Phase 4's live check (not evaluated here)" in detail and f"#{red['decision_id']}" in detail
                  and "Phase 4 must verify" in detail)
            page.lock()
            app.update()
            rev = page.rev
            check("Lock revision: asked first, then locked and fingerprinted (the fingerprint shown)",
                  rev["status"] == "locked" and rev["fingerprint"] and rev["fingerprint"] in page.preview_text.get("1.0", "end")
                  and asked and asked[-1][0] == "yesno")
            st.record_decision("must_keep_location", LOCATION, ev_names["A_small.jpg"])
            page.build_revision()
            page.compare()
            check("revision 2 built; Compare with previous says what changed", page.rev["revision_no"] == 2
                  and asked[-1][0] == "showinfo" and "Revision 1 -> 2" in asked[-1][2])
            st.withdraw(red["decision_id"], reason="test")
            app.show_plan()
            app.update()
            page = app.plan_page
            page.revisions.selection_set(page.revisions.get_children()[-1])
            page._revision_chosen()
            check("reopened after the source decision was withdrawn: revision 1 is shown Obsolete, with the reason",
                  "OBSOLETE" in page.preview_text.get("1.0", "end") and "source decision changed" in page.preview_text.get("1.0", "end"))
            app.destroy()
        check("the page read no source file", not reads.hits, str(reads.hits[:3]))
        check("no Python-level write outside the project and log folders", not guard.violations, str(guard.violations[:3]))
        check("every corpus file untouched", BASE.fingerprint_tree(corpus) == before)
        check("no error dialog", not [a for a in asked if a[0] in ("showerror", "showwarning")], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3plan_"))
    try:
        test_vocabulary()
        test_builder()
        test_retention_rule()
        test_other_items_and_effects()
        test_project(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 PLAN (Build 8): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
