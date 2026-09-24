#!/usr/bin/env python3
r"""Phase 3 Build 5 (Image Game, B11) regression: visual candidate pairs.

What it proves, in order:

  1. The registry: the four new decision kinds target a visual candidate
     pair only; canonical_location now also targets a pair; keep_both names
     exactly two locations; the deferral triggers each target offers.
  2. The pair resolver, on hand-built models: relationship and retention are
     separate rows and resolve separately; equivalent + a preferred image
     makes the other a redundant candidate, keep_both keeps both; a related
     variant keeps both by default and never feeds ordinary cleanup (F07);
     different is a resolved false positive; the conflicts (retention left on
     a Different pair, protection, an explicit Keep, and the chain guard that
     refuses to rely on an equivalence nobody asserted); no transitive
     inference (F06's shape); supersession and withdrawal; determinism.
  3. Routing for pairs: the Visual Similarity Review queue, every route, the
     two pair blockers (preview unavailable, stale image analysis), the
     preview-available trigger, exact copies set aside, a recorded Build 3
     preview row still honoured as recorded.
  4. Candidate generation (Phase2.visual): the pigeonhole search finds exactly
     what brute force finds; the numpy and pure-Python paths agree; the
     neighbour cap bounds a crowd and says so; the measurement is described
     by its method, never as a percentage; the speed at 20,000 images.
  5. A real project with real images (two roots, an exact copy, a resized
     copy, a black-and-white copy, a crop, an edit, unrelated pictures):
     the pairs are made inside the Image Analysis stage; a project analyzed
     before them gets them on demand; re-analysis keeps every pair's
     identity; a fast action is two rows in one operation, undone as one; the
     store's refusals; the chain guard and protection on real files; a pair
     marked Different stays out of the queue after re-analysis; deferral,
     skip, restore; a changed image routes its decided pair to Needs
     Revalidation (never back to unreviewed) and re-recording clears it; a
     cloud-only image is never opened for a preview; zero source mutation.
  6. If a display is available: the window -- the side panel's Similar
     Images, the summary's Similar images line, the page, every key of its
     keymap, linked and unlinked zoom, re-sync, swap, the evidence panel,
     Undo, the exact-copies lens with the group canonical, the Decide page's
     Compare images..., the pair Defer dialog, Skip; headless, no mainloop.

Run:  python Scripts\p3_visual_check.py
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_regression as BASE                                                  # noqa: E402  (helpers only)
from Phase3.model import Evidence, Location, Decision, PolicyVersion, VisualPair, ReviewEvent, path_key   # noqa: E402
from Phase3 import resolve as R                                               # noqa: E402
from Phase3 import routing as RT                                              # noqa: E402
from Phase3 import pairs as PR                                                # noqa: E402
from Phase3 import registry as REG                                            # noqa: E402
from Phase2 import visual as V                                                # noqa: E402

RESULTS = []
LOCATION, GROUP, PAIR = REG.LOCATION, REG.GROUP, REG.VISUAL_PAIR
NOW = "2026-09-23T12:00:00Z"


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


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------

def img(lid, path, root="Primary", present=True, cloud=False, obs=None):
    return Location(lid, path, root, None, f"P{lid}", present, 1000, obs or f"obs-{lid}", (root, path_key(path)),
                    in_current_group=False, cloud_only=cloud)


def vp(pid, a, b, distance=3, current=True, exact=False, a_obs=None, b_obs=None):
    a, b = sorted((a, b))
    return VisualPair(pid, a, b, "phash", distance, 64, None, a_obs or f"obs-{a}", b_obs or f"obs-{b}", current, exact)


def dec(did, kind, ref, value=True, target_kind=PAIR, supersedes=None, withdrawn=False, op=None, binding=None):
    n = int(did[1:])
    return Decision(did, target_kind, ref, kind, value, "explicit_human", supersedes, withdrawn, op or f"op-{did}", n,
                    binding=binding, occurred_utc=f"2026-09-{10 + n % 18:02d}T00:00:00Z")


def pair_binding(p, locs):
    return {"target_kind": PAIR, "pair_id": p.pair_id,
            "members": [{"file_path_id": i, "observation_id": locs[i].observation_id} for i in p.members]}


def world(locs, pairs, decisions=(), policies=(), events=(), roots=None, now=NOW):
    ev = Evidence({l.location_id: l for l in locs}, {}, list(decisions), list(policies), list(events), roots or {}, now)
    ev.pairs = {p.pair_id: p for p in pairs}
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, now)


def pol(pvid, kind, scope):
    if "path" in scope:
        scope = {"path_key": path_key(scope["path"]), "path": scope["path"]}
    return PolicyVersion(pvid, "P" + pvid, kind, scope)


# ---------------------------------------------------------------------------
# 1. Registry
# ---------------------------------------------------------------------------

def test_registry():
    section("1. The registry: pair kinds, targets, values, deferral triggers")
    K = REG.DECISION_KINDS
    for k in ("equivalent_copy", "related_variant", "different", "keep_both"):
        check(f"{k} targets a visual candidate pair only", K[k].target_kinds == frozenset({PAIR}))
    check("the three relationships share ONE conflict domain (a new one supersedes the old)",
          {K[k].conflict_domain for k in REG.PAIR_RELATIONSHIPS} == {"pair_relationship"})
    check("retention is a different domain from the relationship (two rows, each supersedable on its own)",
          K["keep_both"].conflict_domain == "pair_retention" and K["canonical_location"].conflict_domain == "group_canonical"
          and "pair_relationship" not in (K["keep_both"].conflict_domain, K["canonical_location"].conflict_domain))
    check("canonical_location is reused for a pair (Prefer Left / Right) and still targets a group",
          K["canonical_location"].target_kinds == frozenset({GROUP, PAIR}))
    check("relationships change no reclaim by themselves; keep_both does",
          not any(K[k].affects_reclaim for k in REG.PAIR_RELATIONSHIPS) and K["keep_both"].affects_reclaim)
    check("keep_both takes the pair's two locations, stored in id order (the lab's [\"A\",\"B\"])",
          K["keep_both"].validate_value(["12", "3"]) == ["3", "12"] and K["keep_both"].validate_value(("B", "A")) == ["A", "B"])
    check("keep_both refuses one location, three, a repeat, or a blank",
          all(refused(lambda v=v: K["keep_both"].validate_value(v)) for v in (["1"], ["1", "2", "3"], ["1", "1"], ["1", " "], "12")))
    check("a relationship takes the value true and nothing else", refused(lambda: K["different"].validate_value("yes")))
    check("a pair is offered 'until a preview is possible', not 'until fingerprints are current'",
          "snooze_until_preview_available" in REG.DEFER_CHOICES[PAIR] and "snooze_until_hash_current" not in REG.DEFER_CHOICES[PAIR])
    check("a group is not offered the preview trigger (Build 4's choices unchanged)",
          "snooze_until_preview_available" not in REG.DEFER_CHOICES[GROUP] and "snooze_until_hash_current" in REG.DEFER_CHOICES[GROUP])
    check("three target kinds", REG.TARGET_KINDS == (LOCATION, GROUP, PAIR))


# ---------------------------------------------------------------------------
# 2. The pair resolver
# ---------------------------------------------------------------------------

def test_resolver():
    section("2. The pair resolver: relationship and retention, conflicts, no inference")
    A, B, C = img("1", r"C:\P\a.jpg"), img("2", r"C:\P\b.jpg"), img("3", r"C:\P\c.jpg")
    locs = [A, B, C]
    AB, BC, AC = vp("10", "1", "2"), vp("11", "2", "3"), vp("12", "1", "3", distance=7)

    _ev, proj, rt = world(locs, [AB])
    p = proj.pair("10")
    check("no decision: unreviewed, on the Visual Similarity Review (the pairs' queue)",
          p.review_state == R.UNREVIEWED and rt.pairs["10"].primary == RT.UNRESOLVED and rt.pairs["10"].word == "Unreviewed")

    _ev, proj, rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1")])
    p = proj.pair("10")
    check("Equivalent + prefer 1: 1 kept, 2 the redundant candidate, ready for a plan",
          p.relationship == "equivalent_copy" and p.keeper_set == ("1",) and p.redundant_candidate == "2"
          and p.ready_for_plan and rt.pairs["10"].primary == RT.READY_FOR_PLAN and p.ordinary_redundant_candidate)
    check("...the two rows are two decisions on the pair", set(p.decision_ids) == {"D1", "D2"}
          and p.relationship_decision_id == "D1" and p.canonical_decision_id == "D2")

    _ev, proj, rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "keep_both", "10", ["1", "2"]),
                                       dec("D3", "canonical_location", "10", "2")])
    p = proj.pair("10")
    check("Equivalent + keep both (+ a canonical as the representative): both kept, nothing redundant, resolved",
          p.keeper_set == ("1", "2") and p.redundant_candidate is None and p.canonical == "2" and p.review_state == R.RESOLVED
          and rt.pairs["10"].primary == RT.RESOLVED)

    _ev, proj, rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10")])
    check("Equivalent with no retention yet: in progress, still on the queue",
          proj.pair("10").review_state == R.IN_PROGRESS and rt.pairs["10"].primary == RT.UNRESOLVED and rt.pairs["10"].word == "In progress")

    _ev, proj, rt = world(locs, [AB], [dec("D1", "related_variant", "10")])
    p = proj.pair("10")
    check("Related variant alone: both kept by default, resolved, never ordinary cleanup (F07)",
          p.keeper_set == ("1", "2") and p.default_retention and p.redundant_candidate is None
          and not p.ordinary_redundant_candidate and p.review_state == R.RESOLVED)
    _ev, proj, rt = world(locs, [AB], [dec("D1", "related_variant", "10"), dec("D2", "canonical_location", "10", "1")])
    p = proj.pair("10")
    check("Related variant + a canonical: the canonical is the representative; the other is still kept",
          p.keeper_set == ("1", "2") and p.redundant_candidate is None and any("not interchangeable" in n for n in p.notes))

    _ev, proj, rt = world(locs, [AB], [dec("D1", "different", "10")])
    p = proj.pair("10")
    check("Different: resolved, a false positive; neither image is kept or given up by this pair",
          p.review_state == R.RESOLVED and p.keeper_set == () and p.redundant_candidate is None and rt.pairs["10"].primary == RT.RESOLVED)
    _ev, proj, rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1"),
                                       dec("D3", "different", "10", supersedes="D1")])
    p = proj.pair("10")
    check("Different while a retention row still stands: a conflict, not a silent effect",
          p.review_state == R.CONFLICT and [c.kind for c in p.conflicts] == [PR.RETENTION_ON_DIFFERENT]
          and rt.pairs["10"].primary == RT.CONFLICT and not p.ready_for_plan)

    # supersession, withdrawal
    _ev, proj, _rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "related_variant", "10", supersedes="D1")])
    check("a new relationship supersedes the old; the old row stays in the record",
          proj.pair("10").relationship == "related_variant")
    _ev, proj, _rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "related_variant", "10", supersedes="D1", withdrawn=True)])
    check("withdrawing the superseding relationship does not revive the old one (Build 1's rule): nothing speaks",
          proj.pair("10").relationship is None and proj.pair("10").review_state == R.UNREVIEWED)
    _ev, proj, _rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1", withdrawn=True)])
    check("withdrawing the retention row leaves the relationship: in progress again",
          proj.pair("10").relationship == "equivalent_copy" and proj.pair("10").redundant_candidate is None)

    # conflicts from hard constraints
    prot = pol("PV1", "protect_folder_subtree", {"path": r"C:\P"})
    _ev, proj, rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1")], [prot])
    p = proj.pair("10")
    check("the image a pair would give up is protected: PROTECTED_VS_REDUNDANT, not plan-eligible",
          [c.kind for c in p.conflicts] == [R.PROTECTED_VS_REDUNDANT] and not p.ready_for_plan and rt.pairs["10"].primary == RT.CONFLICT)
    _ev, proj, _rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1"),
                                        dec("D3", "must_keep_location", "2", target_kind=LOCATION)])
    check("an explicit Keep on the image a pair would give up: a conflict",
          [c.kind for c in proj.pair("10").conflicts] == [PR.EXPLICIT_KEEP_VS_PAIR])
    _ev, proj, _rt = world(locs, [AB], [dec("D1", "equivalent_copy", "10"), dec("D2", "keep_both", "10", ["1", "2"])], [prot])
    check("keeping both under protection: no conflict (nothing is given up)", proj.pair("10").conflicts == ())

    # the chain guard, and no inference: F06's shape
    chain = [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1"),     # A~B keep A, give up B
             dec("D3", "equivalent_copy", "11"), dec("D4", "canonical_location", "11", "2")]     # B~C keep B, give up C
    _ev, proj, rt = world(locs, [AB, BC, AC], chain)
    pab, pbc, pac = proj.pair("10"), proj.pair("11"), proj.pair("12")
    check("the chain guard: B~C keeps B, which A~B gives up -- B~C is in conflict (nothing asserts A~C)",
          [c.kind for c in pbc.conflicts] == [PR.KEEPER_REDUNDANT_ELSEWHERE] and not pbc.ready_for_plan
          and rt.pairs["11"].primary == RT.CONFLICT and "never inferred along a chain" in pbc.conflicts[0].message)
    check("...A~B itself is not in conflict and stays ready; A~C stays unreviewed -- no inference either way",
          pab.conflicts == () and pab.ready_for_plan and pac.review_state == R.UNREVIEWED and pac.relationship is None
          and rt.pairs["12"].primary == RT.UNRESOLVED)
    _ev, proj, rt = world(locs, [AB, BC, AC], chain + [dec("D5", "different", "12")])
    check("A~C decided Different by the person: every pair holds only what was said about it",
          {p.pair_id: p.relationship for p in proj.pairs} == {"10": "equivalent_copy", "11": "equivalent_copy", "12": "different"}
          and not any(p.relationship == "equivalent_copy" for p in proj.pairs if p.pair_id == "12"))
    _ev, proj, rt = world(locs, [AB, BC, AC], chain + [dec("D5", "keep_both", "11", ["2", "3"])])
    check("keeping both of B~C ends the chain conflict (B~C no longer gives anything up)", proj.pair("11").conflicts == ())
    _ev, proj, _rt = world(locs, [AB, BC], [dec("D1", "equivalent_copy", "10"), dec("D2", "canonical_location", "10", "1"),
                                            dec("D3", "equivalent_copy", "11"), dec("D4", "canonical_location", "11", "3")])
    check("two pairs giving up the same image B for different keepers: no conflict (each keeper is kept)",
          proj.pair("10").conflicts == () and proj.pair("11").conflicts == ())

    # determinism and isolation from the exact-duplicate resolver
    decs = chain + [dec("D5", "different", "12")]
    base = json.dumps([p.as_dict() for p in world(locs, [AB, BC, AC], decs)[1].pairs], sort_keys=True)
    rnd = random.Random(5)
    same = True
    for _ in range(3):
        d2, l2, p2 = list(decs), list(locs), [AB, BC, AC]
        rnd.shuffle(d2), rnd.shuffle(l2), rnd.shuffle(p2)
        same &= json.dumps([p.as_dict() for p in world(l2, p2, d2)[1].pairs], sort_keys=True) == base
    check("the same decisions in any order give the same pair projection (3 shuffles)", same)
    g = img("7", r"C:\P\g.jpg")
    grp = Location("7", g.path, "Primary", "C7", "P7", True, 10, "obs-7", g.sort_key)
    grp2 = Location("8", r"C:\P\h.jpg", "Primary", "C7", "P8", True, 10, "obs-8", ("Primary", "c:\\p\\h.jpg"))
    from Phase3.model import Group
    ev = Evidence({l.location_id: l for l in locs + [grp, grp2]}, {"7": Group("7", "C7", ("7", "8"))},
                  [dec("D1", "canonical_location", "10", "1"), dec("D2", "equivalent_copy", "10")], [], [], {}, NOW)
    ev.pairs = {"10": AB}
    proj = R.resolve_all(ev)
    check("a pair's canonical never reaches an exact group that shares its id text ('10' vs group '7' and pair '10')",
          proj.group("7").canonical is None and proj.pair("10").canonical == "1" and proj.orphaned_decisions == ())

    t0 = time.perf_counter()
    many_locs = [img(str(i), rf"C:\M\{i}.jpg") for i in range(1, 6001)]
    many_pairs = [vp(str(100000 + i), str(i), str(i + 1)) for i in range(1, 6000)]
    many_dec = []
    for i in range(1, 6000, 2):
        many_dec += [dec(f"D{2 * i}", "equivalent_copy", str(100000 + i)), dec(f"D{2 * i + 1}", "canonical_location", str(100000 + i), str(i))]
    ev, proj, rt = world(many_locs, many_pairs, many_dec)
    took = time.perf_counter() - t0
    check(f"6,000 images, 5,999 pairs, 6,000 decisions resolved and routed in {took:.2f} s (< 5 s)", took < 5.0, f"{took:.2f}s")


# ---------------------------------------------------------------------------
# 3. Routing for pairs
# ---------------------------------------------------------------------------

def test_routing():
    section("3. Routing for pairs: queue, blockers, triggers, exact copies, the detector's bookkeeping")
    A, B = img("1", r"C:\P\a.jpg"), img("2", r"C:\P\b.jpg")
    AB = vp("10", "1", "2")
    ev, proj, rt = world([A, B], [replace(AB, exact_content=True)])
    r = rt.pairs["10"]
    check("byte-identical, undecided: Resolved as 'Exact copies', out of the queue",
          r.primary == RT.RESOLVED and r.word == "Exact copies" and rt.pair_counts[RT.EXACT_CONTENT] == 1 and rt.pair_counts[RT.UNRESOLVED] == 0)

    ev, proj, rt = world([A, replace(B, cloud_only=True)], [AB])
    r = rt.pairs["10"]
    check("an image cloud-only: Blocked, preview unavailable (the product never downloads it)",
          r.primary == RT.BLOCKED and any(c.kind == RT.PREVIEW_UNAVAILABLE and "cloud-only" in c.detail for c in r.conditions))
    ev, proj, rt = world([A, replace(B, present=False)], [AB])
    check("an image not present: Blocked, preview unavailable", rt.pairs["10"].primary == RT.BLOCKED
          and any(c.kind == RT.PREVIEW_UNAVAILABLE for c in rt.pairs["10"].conditions))

    d1 = dec("D1", "equivalent_copy", "10", binding=pair_binding(AB, {"1": A, "2": B}))
    moved = replace(B, observation_id="obs-2b")
    ev, proj, rt = world([A, moved], [replace(AB, current=False)], [d1])
    r = rt.pairs["10"]
    check("a decided pair whose image changed: Needs Revalidation first, the stale analysis beside it -- never 'unreviewed'",
          r.primary == RT.NEEDS_REVALIDATION and any(c.kind == RT.STALE_IMAGE_ANALYSIS for c in r.conditions)
          and not any(c.kind == "unreviewed" for c in r.conditions)
          and any("no longer a current candidate pair" in c.detail for c in r.conditions if c.route == RT.NEEDS_REVALIDATION))
    ev, proj, rt = world([A, B], [AB], [d1])
    check("the same decision on unchanged evidence: no drift", rt.pairs["10"].drift[0].applicable
          and rt.pairs["10"].primary == RT.UNRESOLVED)

    def evt(eid, kind, rk=None, cond=None, until=None, detail=None, refers=None):
        return ReviewEvent(eid, PAIR, "10", kind, rk, cond, until, detail or {}, refers, f"2026-09-{10 + int(eid[1:]):02d}T00:00:00Z",
                           "explicit_human", int(eid[1:]))
    ev, proj, rt = world([A, replace(B, cloud_only=True)], [AB], events=[evt("E1", "deferred", "preview_available")])
    check("deferred until a preview is possible: parked while an image is cloud-only (blocked outranks deferred)",
          rt.pairs["10"].primary == RT.BLOCKED and rt.pairs["10"].deferral is not None and not rt.pairs["10"].deferral_fired)
    ev, proj, rt = world([A, B], [AB], events=[evt("E1", "deferred", "preview_available")])
    check("...and the trigger fires once both are local and present",
          rt.pairs["10"].deferral_fired and "can be shown again" in rt.pairs["10"].deferral_fired_why and rt.pairs["10"].primary == RT.UNRESOLVED)
    ev, proj, rt = world([A, B], [AB], events=[evt("E1", "deferred", "time", until="2099-01-01T00:00:00Z")])
    check("snoozed until a date: Deferred / Snoozed", rt.pairs["10"].primary == RT.DEFERRED)
    ev, proj, rt = world([A, B], [AB], events=[evt("E1", "deferred", "manual"), evt("E2", "restored", "manual", refers="E1")])
    check("restored: back on the queue", rt.pairs["10"].primary == RT.UNRESOLVED)
    ev, proj, rt = world([A, B], [AB], events=[evt("E1", "skipped")])
    check("skipped: an audit row, the pair exactly where it was", rt.pairs["10"].primary == RT.UNRESOLVED)
    ev, proj, rt = world([A, B], [AB], events=[evt("E1", "blocked", "preview_available", "no preview",
                                                   detail={"blocker": RT.PREVIEW_UNAVAILABLE})])
    check("a detector row naming a computable pair blocker speaks through the live computation (cleared here)",
          rt.pairs["10"].primary == RT.UNRESOLVED)
    loc_evt = ReviewEvent("E1", LOCATION, "1", "blocked", "preview_available", "no preview yet", None, {}, None, NOW, "system_evidence", 1)
    check("a recorded Build 3 preview row with no blocker named is still honoured as recorded (F08 unchanged)",
          RT.recorded_blocker(loc_evt) is None
          and RT.recorded_blocker(replace(loc_evt, return_kind="evidence_change")) == RT.PHYSICAL_IDENTITY_UNKNOWN)
    ev, proj, rt = world([A, B, img("3", r"C:\P\c.jpg")], [AB, vp("11", "2", "3", distance=1)])
    check("the queue's order is the closest measurement first",
          [p.pair_id for p in RT.ordered_pairs(proj, rt, RT.UNRESOLVED)] == ["11", "10"])
    from Phase3.imagegame import filter_pairs, LENS_EXACT, LENS_STRONG, LENS_CROSS_ROOT, LENS_ACROSS_FOLDERS
    # The Similar Images page re-derives only the pairs after a decision,
    # reusing what no decision touches: the answer must be the fresh one.
    rnd = random.Random(23)
    locs = [img(str(i), rf"C:\R\{i:04d}.jpg") for i in range(1, 301)]
    prs = []
    for i in range(1, 300):
        prs.append(vp(str(5000 + i), str(i), str(i + 1), distance=rnd.randint(0, 8)))
        if i + 2 <= 300 and rnd.random() < 0.5:
            prs.append(vp(str(9000 + i), str(i), str(i + 2), distance=rnd.randint(0, 8), exact=rnd.random() < 0.1))
    decisions, n, same = [], 0, True
    prev_pp = prev_rt = None
    for step in range(25):
        for _ in range(rnd.randint(1, 6)):
            p = rnd.choice(prs)
            n += 1
            kind = rnd.choice(["equivalent_copy", "related_variant", "different", "canonical_location", "keep_both"])
            value = {"canonical_location": rnd.choice(p.members), "keep_both": list(p.members)}.get(kind, True)
            decisions.append(dec(f"D{n}", kind, p.pair_id, value))
        if decisions and rnd.random() < 0.5:
            k = rnd.randrange(len(decisions))
            decisions[k] = replace(decisions[k], withdrawn=True)
        ev = Evidence({l.location_id: l for l in locs}, {}, list(decisions), [], [], {}, NOW)
        ev.pairs = {p.pair_id: p for p in prs}
        act = [d for d in decisions if not d.withdrawn]
        fresh_pp = PR.resolve_pairs(ev)
        fresh_rt, fresh_c = RT.route_pairs(ev, fresh_pp, NOW)
        if prev_pp is not None:
            inc_pp = PR.resolve_pairs(ev, R._index_decisions(act), act, previous={p.pair_id: p for p in prev_pp})
            inc_rt, inc_c = RT.route_pairs(ev, inc_pp, NOW, previous=prev_rt)
            same &= ([p.as_dict() for p in inc_pp] == [p.as_dict() for p in fresh_pp]
                     and {k: r.as_dict() for k, r in inc_rt.items()} == {k: r.as_dict() for k, r in fresh_rt.items()} and inc_c == fresh_c)
            prev_pp, prev_rt = inc_pp, inc_rt
        else:
            prev_pp, prev_rt = fresh_pp, fresh_rt
    check("re-deriving only what a decision touches gives exactly the fresh answer (25 random rounds, order included)", same)
    far = img("4", r"D:\Q\d.jpg", root="Second")
    ev, proj, rt = world([A, B, far], [AB, replace(vp("12", "1", "4", distance=8)), replace(vp("13", "2", "4", distance=0), exact_content=True)])
    check("lenses: strong near-copies (distance <= 5), cross-root pairs, exact copies -- each a filter, not a workflow",
          [p.pair_id for p in filter_pairs(proj, rt, ev, LENS_STRONG)] == ["10"]
          and [p.pair_id for p in filter_pairs(proj, rt, ev, LENS_CROSS_ROOT)] == ["12"]
          and [p.pair_id for p in filter_pairs(proj, rt, ev, LENS_EXACT)] == ["13"]
          and [p.pair_id for p in filter_pairs(proj, rt, ev, RT.PAIR_QUEUE_WORD)] == ["10", "12"])
    check("the Across folders lens: pairs whose images sit in different folders (a crowd of variations in one folder stays out)",
          [p.pair_id for p in filter_pairs(proj, rt, ev, LENS_ACROSS_FOLDERS)] == ["12"])


# ---------------------------------------------------------------------------
# 4. Candidate generation, pure
# ---------------------------------------------------------------------------

def test_generator():
    section("4. Candidate generation: exact against brute force, bounded, described honestly, fast")
    rnd = random.Random(11)

    def flip(h, k):
        for b in rnd.sample(range(64), k):
            h ^= 1 << b
        return h
    items = []
    while len(items) < 1200:
        base = rnd.getrandbits(64)
        items.append((len(items), base, 64))
        for _ in range(rnd.randint(0, 3)):
            items.append((len(items), flip(base, rnd.randint(0, 12)), 64))
    items += [(len(items) + i, 0x8000000000000000, 64) for i in range(60)]           # a crowd of blank frames
    brute = {(i, j) for i in range(len(items)) for j in range(i + 1, len(items))
             if (items[i][1] ^ items[j][1]).bit_count() <= V.MAX_DISTANCE}
    got, _capped = V.find_pairs(items, V.MAX_DISTANCE, cap=10 ** 9)
    check(f"uncapped, the pigeonhole search finds exactly the {len(brute):,} pairs brute force finds", {(i, j) for i, j, _d in got} == brute)
    check("every reported distance is the true Hamming distance",
          all(d == (items[i][1] ^ items[j][1]).bit_count() for i, j, d in got))
    real = V._numpy
    a, ca = V.find_pairs(items)
    V._numpy = lambda: None
    try:
        b, cb = V.find_pairs(items)
    finally:
        V._numpy = real
    check("the numpy path and the pure-Python path give the same pairs", a == b and ca == cb)
    crowd = [p for p in a if items[p[0]][1] == 0x8000000000000000 and items[p[1]][1] == 0x8000000000000000]
    check(f"the neighbour cap bounds the crowd of 60 identical hashes ({len(crowd)} pairs, not 1,770) and counts the images it trimmed",
          len(crowd) <= 60 * V.NEIGHBOR_CAP and ca >= 60)
    check("hashes of different widths are never compared",
          V.find_pairs([(0, 1, 64), (1, 1, 256)])[0] == [])
    text = V.describe("phash", 4, 64, 3, 7)
    check("the measurement is named by its method, never a percentage",
          text.startswith("Perceptual hash (pHash) -- Hamming distance 4 of 64 bits (Strong near-copy)") and "%" not in text
          and "aHash 3/64" in text and "dHash 7/64" in text)
    check("tier words: 0-2 near-identical, 3-5 strong, 6-8 possible",
          [V.tier(d) for d in (0, 2, 3, 5, 6, 8)] == ["Near-identical", "Near-identical", "Strong near-copy", "Strong near-copy",
                                                      "Possible near-copy", "Possible near-copy"])
    check("a method with its own score is shown on its own scale (the lab's sscd)",
          V.describe("sscd", score=0.91) == "Copy-detection descriptor (SSCD) -- score 0.91 (the method's own scale)")
    big = [(i, rnd.getrandbits(64), 64) for i in range(20000)]
    t0 = time.perf_counter()
    V.find_pairs(big)
    took = time.perf_counter() - t0
    check(f"20,000 images compared in {took:.2f} s (< 10 s): indexed, not all against all", took < 10.0, f"{took:.2f}s")


# ---------------------------------------------------------------------------
# 5. A real project
# ---------------------------------------------------------------------------

def _photo(seed, size=(640, 480)):
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    d = ImageDraw.Draw(im)
    w, h = size
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(40 + 180 * t), 95, int(200 - 150 * t)))
    for _ in range(14):
        x0, y0, r = rnd.randrange(w), rnd.randrange(h), rnd.randrange(20, 140)
        d.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return im.filter(ImageFilter.GaussianBlur(3))


def build_images(base: Path):
    """Two roots. The 'A' photograph four ways (a byte copy, a resized
    recompressed copy in the other root, a black-and-white copy), a crop
    too different for the hash, an edited 'C' pair, two unrelated pictures,
    and a text file."""
    from PIL import ImageDraw
    r1, r2 = base / "Photos", base / "Backup"
    (r1 / "Trip").mkdir(parents=True)
    (r1 / "Edits").mkdir(parents=True)
    (r2 / "Old").mkdir(parents=True)
    a = _photo(1)
    a.save(r1 / "Trip" / "A.jpg", quality=95)
    shutil.copyfile(r1 / "Trip" / "A.jpg", r1 / "Trip" / "A_copy.jpg")
    a.resize((320, 240)).save(r2 / "Old" / "A_small.jpg", quality=70)
    a.convert("L").save(r1 / "Edits" / "A_bw.jpg", quality=90)
    a.crop((64, 48, 576, 432)).resize((640, 480)).save(r1 / "Edits" / "A_crop.jpg", quality=90)
    c = _photo(3)
    c.save(r1 / "Trip" / "C.jpg", quality=92)
    c2 = c.copy()
    ImageDraw.Draw(c2).rectangle([20, 20, 120, 60], fill=(255, 255, 255))
    c2.save(r1 / "Edits" / "C_edit.jpg", quality=92)
    _photo(2).save(r1 / "Trip" / "B.png")
    _photo(4).save(r2 / "Old" / "D.jpg", quality=90)
    (r1 / "notes.txt").write_text("not a picture", encoding="utf-8")
    return r1, r2


def open_project(tmp: Path, name):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    app_root = tmp / f"{name}Root"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / f"{name}Corpus"
    r1, r2 = build_images(corpus)
    out1 = RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(r1), str(r2)], project_name=name)).run()
    project_dir = app_root / "Projects" / name
    out2 = RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    out3 = RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze pictures", analyzer_keys=["image"])).run()
    return app_root, corpus, r1, r2, project_dir, (out1, out2, out3)


def test_project(tmp: Path):
    section("5. A real project: pairs made in the Image Analysis stage, stable identity, decisions, revalidation, zero mutation")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, ANALYSIS
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    from Phase3.imagegame import ImageView

    app_root, corpus, r1, r2, project_dir, outs = open_project(tmp, "P3Visual")
    check("Pre-Scan, Find My Duplicates and Analyze (pictures) through the worker", all(o.ok for o in outs), str([o.status for o in outs]))
    conn = connect(project_dir, write=True)
    check("the project is on schema 12 with the two visual-candidate tables",
          conn.execute("PRAGMA user_version").fetchone()[0] == 12
          and {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'visual_candidate%' AND type='table'")}
          == {"visual_candidate", "visual_candidate_generation"})
    gens = conn.execute("SELECT * FROM visual_candidate_generation").fetchall()
    check("the Image Analysis stage compared the hashes: one completed generation tied to the analysis run",
          len(gens) == 1 and gens[0]["status"] == "completed" and gens[0]["run_id"] is not None
          and conn.execute("SELECT run_kind FROM run WHERE run_id=?", (gens[0]["run_id"],)).fetchone()[0] == "content_analysis")
    check("nine images considered (the text file is not an image), seven pairs, none trimmed",
          gens[0]["images_considered"] == 9 and gens[0]["pairs_current"] == 7 and gens[0]["capped_images"] == 0, str(dict(gens[0])))
    note = conn.execute("SELECT message FROM event WHERE message LIKE 'Similar images:%'").fetchone()
    check("the run records what it found, as an event", note is not None and "7 candidate pair(s) among 9 image(s)" in note[0], str(note and note[0]))

    by_path = {}
    for r in conn.execute("SELECT fs.file_path_id, sr.root_path, fp.relative_path FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
                          "JOIN source_root sr ON sr.source_root_id=fs.source_root_id"):
        by_path[Path(r["root_path"]).name + "/" + r["relative_path"].replace("\\", "/")] = str(r["file_path_id"])
    F = by_path
    A, Acopy, Asmall, Abw, Acrop = F["Photos/Trip/A.jpg"], F["Photos/Trip/A_copy.jpg"], F["Backup/Old/A_small.jpg"], F["Photos/Edits/A_bw.jpg"], F["Photos/Edits/A_crop.jpg"]
    C, Cedit, Bp, D = F["Photos/Trip/C.jpg"], F["Photos/Edits/C_edit.jpg"], F["Photos/Trip/B.png"], F["Backup/Old/D.jpg"]
    ev = E.load_evidence(conn)
    members = {frozenset(p.members): p for p in ev.pairs.values()}
    check("the A family: every two of A, its byte copy, its resized copy and its black-and-white copy are a pair (6)",
          all(frozenset((x, y)) in members for i, x in enumerate((A, Acopy, Asmall, Abw)) for y in (A, Acopy, Asmall, Abw)[i + 1:]))
    check("C and its edit are a pair; the crop, B and D are in none (the hash does not see a crop as alike)",
          frozenset((C, Cedit)) in members and not any(x in p.members for p in ev.pairs.values() for x in (Acrop, Bp, D)))
    exact = members[frozenset((A, Acopy))]
    check("the byte copy's pair is marked exact content; the resized copy's is not",
          exact.exact_content and not members[frozenset((A, Asmall))].exact_content)
    check("each pair carries its method, 64-bit measurement and the observations it compared",
          all(p.method == "phash" and p.bits == 64 and p.distance is not None and p.a_observation_id and p.b_observation_id for p in ev.pairs.values()))
    ids_before = {frozenset(p.members): p.pair_id for p in ev.pairs.values()}
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    check("routes: six pairs on the Visual Similarity Review, the exact pair set aside",
          rt.pair_counts[RT.UNRESOLVED] == 6 and rt.pair_counts[RT.EXACT_CONTENT] == 1 and rt.pairs[exact.pair_id].word == "Exact copies")

    # -- a project analyzed before pairs existed ---------------------------------------------
    conn.execute("DELETE FROM visual_candidate")
    conn.execute("DELETE FROM visual_candidate_generation")
    conn.commit()
    st_before = V.status(conn)
    made = V.ensure_candidates(conn)
    check("a project whose images were analyzed but never compared is out of date, and gets its pairs on demand",
          st_before["out_of_date"] and st_before["generation"] is None and made is not None and made["pairs_current"] == 7)
    check("...and once compared, it is not out of date (ensure does nothing twice)",
          not V.status(conn)["out_of_date"] and V.ensure_candidates(conn) is None)
    ev = E.load_evidence(conn)
    ids_before = {frozenset(p.members): p.pair_id for p in ev.pairs.values()}
    exact = [p for p in ev.pairs.values() if p.exact_content][0]
    P = lambda x, y: ids_before[frozenset((x, y))]                  # noqa: E731

    # -- decisions through the store --------------------------------------------------------------------
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visual")
    ops0 = st.counts()["operations"]
    res = st.record_decisions([("equivalent_copy", PAIR, P(A, Asmall), True), ("canonical_location", PAIR, P(A, Asmall), A)])
    rows = conn.execute("SELECT * FROM p3_decision WHERE operation_id=? ORDER BY decision_id", (res["operation_id"],)).fetchall()
    check("a fast action: two decisions (relationship, retention) in ONE operation",
          st.counts()["operations"] == ops0 + 1 and [r["decision_kind"] for r in rows] == ["equivalent_copy", "canonical_location"]
          and all(r["target_kind"] == PAIR and r["target_ref"] == P(A, Asmall) for r in rows))
    b = json.loads(rows[0]["evidence_binding_json"])
    check("each row is bound to the pair's evidence: the pair, its measurement, both images' current observations (ids only)",
          b["target_kind"] == PAIR and str(b["pair_id"]) == P(A, Asmall) and b["method"] == "phash" and b["hash_bits"] == 64
          and sorted(str(m["file_path_id"]) for m in b["members"]) == sorted((A, Asmall)) and all(m["observation_id"] for m in b["members"]))
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    p = proj.pair(P(A, Asmall))
    check("resolved: A kept, the resized copy the redundant candidate, ready for a plan",
          p.keeper_set == (A,) and p.redundant_candidate == Asmall and p.ready_for_plan)
    check("the exact-duplicate resolution is untouched by a pair's decisions",
          all(v.status == R.UNDECIDED for g in proj.groups for v in g.members))
    res2 = st.record_decision("related_variant", PAIR, P(A, Asmall))
    check("a new relationship supersedes only the relationship; the retention row still stands on its own",
          res2["supersedes_decision_id"] == int(rows[0]["decision_id"])
          and {d.kind for d in st.active_for(PAIR, P(A, Asmall))} == {"related_variant", "canonical_location"})
    undo = st.withdraw_many([res2["decision_id"], rows[1]["decision_id"]], reason="undo")
    check("withdraw_many: several withdrawals in one operation; nothing is revived",
          len(undo["withdrawn"]) == 2 and st.active_for(PAIR, P(A, Asmall)) == [])
    check("the store refuses: a preferred image outside the pair", refused(lambda: st.record_decision("canonical_location", PAIR, P(A, Asmall), C)))
    check("...keep_both naming other images", refused(lambda: st.record_decision("keep_both", PAIR, P(A, Asmall), [A, C])))
    check("...a relationship on an exact-duplicate group", refused(lambda: st.record_decision("equivalent_copy", GROUP, exact.a_id)))
    check("...a pair that does not exist", refused(lambda: st.record_decision("different", PAIR, "999999")))
    check("...a fast action with one bad row records neither row",
          refused(lambda: st.record_decisions([("equivalent_copy", PAIR, P(C, Cedit), True), ("canonical_location", PAIR, P(C, Cedit), A)]))
          and st.active_for(PAIR, P(C, Cedit)) == [])
    check("...a group deferral trigger on a pair, and the preview trigger on a group",
          refused(lambda: st.defer(PAIR, P(C, Cedit), "snooze_until_hash_current"))
          and refused(lambda: st.defer(GROUP, exact.a_id, "snooze_until_preview_available")))

    # -- the chain guard and protection on real files ------------------------------------------------
    st.record_decisions([("equivalent_copy", PAIR, P(A, Asmall), True), ("canonical_location", PAIR, P(A, Asmall), A)])
    st.record_decisions([("equivalent_copy", PAIR, P(Asmall, Abw), True), ("canonical_location", PAIR, P(Asmall, Abw), Asmall)])
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    check("real files: A~small keeps A and gives up small; small~bw keeps small -- small~bw is in conflict, A~small is not",
          [c.kind for c in proj.pair(P(Asmall, Abw)).conflicts] == [PR.KEEPER_REDUNDANT_ELSEWHERE] and proj.pair(P(A, Asmall)).conflicts == ())
    st.record_decision("keep_both", PAIR, P(Asmall, Abw), [Asmall, Abw])
    pol_ = st.create_policy("protect_source_root", {"root_key": ev.locations[Asmall].root_key, "root_path": str(r2)})
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    check("keep both clears the chain; protecting the Backup root makes A~small's give-up a protection conflict",
          proj.pair(P(Asmall, Abw)).conflicts == () and [c.kind for c in proj.pair(P(A, Asmall)).conflicts] == [R.PROTECTED_VS_REDUNDANT]
          and rt.pairs[P(A, Asmall)].primary == RT.CONFLICT)
    st.retire_policy(pol_["policy_id"])

    # -- Different, deferral, skip, the detector --------------------------------------------
    st.record_decision("different", PAIR, P(C, Cedit))
    e1 = st.defer(PAIR, P(A, Abw), "snooze_until_preview_available", note="look later")
    st.skip(PAIR, P(Acopy, Abw))
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    check("Different: resolved; the deferral until a preview is possible fires at once (both are local)",
          rt.pairs[P(C, Cedit)].primary == RT.RESOLVED and rt.pairs[P(A, Abw)].deferral_fired)
    counts = RT.reconcile(conn, st)
    check("the detector restores the fired deferral, and a second run writes nothing",
          counts.get("restored", 0) >= 1 and RT.reconcile(conn, st) == {"written": 0}, str(counts))
    e2 = st.defer(PAIR, P(A, Abw), "snooze_until_date", until="2099-01-01")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    check("snoozed until 2099: Deferred; Skip left its pair on the queue", rt.pairs[P(A, Abw)].primary == RT.DEFERRED
          and rt.pairs[P(Acopy, Abw)].primary == RT.UNRESOLVED)
    st.restore(PAIR, P(A, Abw), refers_to=e2["review_event_id"])
    check("pair review events land on the pair", {e["event_kind"] for e in st.review_events_for(PAIR, P(A, Abw))} >= {"deferred", "restored"}
          and e1["return_kind"] == "preview_available")

    # -- stable identity across re-analysis ---------------------------------------------------------------
    out = RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze pictures", analyzer_keys=["image"])).run()
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visual")
    gen = conn.execute("SELECT * FROM visual_candidate_generation ORDER BY generation_id DESC LIMIT 1").fetchone()
    ev = E.load_evidence(conn)
    ids_after = {frozenset(p.members): p.pair_id for p in ev.pairs.values()}
    check("re-analysis: a new generation, no new pairs, every pair keeps its id", out.ok and gen["pairs_new"] == 0 and ids_after == ids_before)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    check("...so a pair marked Different stays out of the queue, and a decided pair stays decided",
          rt.pairs[P(C, Cedit)].primary == RT.RESOLVED and rt.pairs[P(A, Asmall)].primary == RT.READY_FOR_PLAN)

    # -- an image changes: revalidation, not resurfacing ------------------------------------------------
    from PIL import Image
    small = r2 / "Old" / "A_small.jpg"
    with Image.open(small) as im:
        im.load()
        again = im.copy()
    again.save(small, quality=40)                                   # the TEST changes its own corpus, as a person would
    before_mutation_tests = BASE.fingerprint_tree(corpus)
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visual")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    r = rt.pairs[P(A, Asmall)]
    check("the resized copy re-observed: its decided pair routes to Needs Revalidation, with the stale analysis named",
          r.primary == RT.NEEDS_REVALIDATION and any(c.kind == RT.STALE_IMAGE_ANALYSIS for c in r.conditions)
          and not any(c.kind == "unreviewed" for c in r.conditions))
    check("...an undecided pair on the changed image leaves the queue until it is analyzed again (no current evidence)",
          P(Acopy, Asmall) not in rt.pairs)
    counts = RT.reconcile(conn, st)
    check("the detector records the revalidation (and the block on an engaged pair), idempotently",
          counts.get("needs_revalidation", 0) >= 1 and RT.reconcile(conn, st) == {"written": 0}, str(counts))
    RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze pictures", analyzer_keys=["image"])).run()
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visual")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    r = rt.pairs[P(A, Asmall)]
    check("analyzed again: the same pair id, current again, the stale block gone -- the decision still asks to be revalidated",
          ev.pairs[P(A, Asmall)].current and r.primary == RT.NEEDS_REVALIDATION and not any(c.kind == RT.STALE_IMAGE_ANALYSIS for c in r.conditions))
    st.record_decisions([("equivalent_copy", PAIR, P(A, Asmall), True), ("canonical_location", PAIR, P(A, Asmall), A)],
                        rationale="reconfirmed on current evidence")
    counts = RT.reconcile(conn, st)
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    check("re-recorded on the evidence as it is now: Ready for plan, and the detector restores its flag",
          rt.pairs[P(A, Asmall)].primary == RT.READY_FOR_PLAN and counts.get("restored", 0) >= 1, str(counts))

    # -- a cloud-only image is never opened ------------------------------------------------------------
    conn.execute("UPDATE file_state SET is_offline_or_cloud=1 WHERE file_path_id=?", (int(Cedit),))
    conn.commit()
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev), ev.now)
    opened = []
    from PIL import Image as PILImage
    real_open = PILImage.open
    PILImage.open = lambda *a, **k: (opened.append(a[0]), real_open(*a, **k))[1]
    try:
        class _Canvas:                                               # the view needs a canvas; not this one
            def winfo_width(self): return 400
            def winfo_height(self): return 300
            def delete(self, *a): pass
            def create_text(self, *a, **k): pass
            def create_image(self, *a, **k): pass
        view = ImageView(_Canvas())
        why = view.load(V.location_facts(conn, Cedit))
        ok_view = ImageView(_Canvas())
        ok_why = ok_view.load(V.location_facts(conn, C))
    finally:
        PILImage.open = real_open
    check("a cloud-only image: its pair (already marked Different) carries the preview-unavailable blocker",
          any(c.kind == RT.PREVIEW_UNAVAILABLE for c in rt.pairs[P(C, Cedit)].conditions))
    check("...and the preview refuses it without opening the file; the local image opens",
          why is not None and "Cloud-only" in why and len(opened) == 1 and ok_why is None and ok_view.image is not None
          and "C_edit" not in str(opened[0]))
    conn.execute("UPDATE file_state SET is_offline_or_cloud=0 WHERE file_path_id=?", (int(Cedit),))
    conn.commit()
    check("full resolution is available: the preview holds the decoded original, not a thumbnail",
          ok_view.image.size == (640, 480))

    # -- zero mutation ---------------------------------------------------------------------
    (project_dir / "Exports").mkdir(exist_ok=True)
    with BASE.WriteGuard([project_dir, app_root / "Logs"]) as guard:
        V.generate_candidates(conn)
        st.record_decisions([("related_variant", PAIR, P(Acopy, Abw), True), ("keep_both", PAIR, P(Acopy, Abw), [Acopy, Abw])])
        st.defer(PAIR, P(Acopy, Asmall) if P(Acopy, Asmall) in E.load_evidence(conn).pairs else P(A, Abw), "defer_indefinitely")
        RT.reconcile(conn, st)
        for lid in (A, Asmall, Abw, C):
            ImageView(_Canvas()).load(V.location_facts(conn, lid))
        S.export_decision_journal(conn, project_dir / "Exports" / "DecisionJournal.txt")
    check("no Python-level write outside the project folder while pairs were generated, decided, routed and previewed",
          not guard.violations, str(guard.violations[:5]))
    after = BASE.fingerprint_tree(corpus)
    changed = [p for p, v in before_mutation_tests["files"].items() if after["files"].get(p) != v]
    check(f"every one of the {len(after['files'])} corpus files has its bytes, size and modified time", not changed, str(changed[:3]))
    journal = (project_dir / "Exports" / "DecisionJournal.txt").read_text(encoding="utf-8")
    check("the decision journal lists the pair decisions", "on visual_candidate_pair" in journal and "equivalent_copy" in journal)
    conn.close()


# ---------------------------------------------------------------------------
# 6. The window
# ---------------------------------------------------------------------------

def test_gui(tmp: Path):
    section("6. The window: Similar Images, the keymap, the view, Undo, exact copies on request")
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
    from Phase3 import imagegame as IG, review as RV

    app_root, corpus, r1, r2, project_dir, outs = open_project(tmp, "P3VisualGui")
    before = BASE.fingerprint_tree(corpus)
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else k.get("title"))), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else k.get("title"), a[1] if len(a) > 1 else k.get("message"))))
    try:
        with BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            check("the side panel has Similar Images beside Decide",
                  [str(b.cget("text")) for b in app.nav_buttons][2:4] == ["Decide", "Similar Images"])
            app.open_project(project_dir)
            app.update_idletasks()
            t = BASE._texts(app.content)
            check("the summary's Decide section has a Similar images line with Compare images",
                  "Similar images" in t and "Compare images" in t
                  and "0 of 7 candidate pairs decided; 1 byte-identical pair set aside" in t,
                  str([x for x in t if "pairs" in x]))
            app.show_similar()
            app.update()
            page = app.similar_page
            check("the page opens on the Visual Similarity Review: six pairs, the exact pair not among them",
                  page.filter_var.get() == RT.PAIR_QUEUE_WORD and len(page.shown) == 6
                  and not any(page.proj.pair(x).exact_content for x in page.shown))
            first = page.selected
            check("the first pair is shown: both images decoded, the measurement named by its method, no percentage",
                  first == page.shown[0] and all(v.image is not None for v in page.views)
                  and "Hamming distance" in page.measure_label.cget("text") and "of 64 bits" in page.measure_label.cget("text")
                  and "%" not in page.measure_label.cget("text"))
            ev_text = page.evidence[0].cget("text")
            check("the evidence panel: name, folder, resolution, format, size, modified time, capture date line",
                  " x " in ev_text and "px" in ev_text and ("JPEG" in ev_text or "PNG" in ev_text) and "modified" in ev_text
                  and "capture date" in ev_text)
            check("every key of the page's keymap is bound on the list and on both canvases",
                  all(page.table.bind(f"<KeyPress-{k}>") and page.canvases[0].bind(f"<KeyPress-{k}>") for k, _a, _l in IG.KEYMAP))

            # the view
            page.key("fit")
            s0 = [v.scale for v in page.views]
            page.key("zoom_in")
            s1 = [v.scale for v in page.views]
            check("linked: zoom moves both sides", all(b > a for a, b in zip(s0, s1)))
            page.key("toggle_link")
            page.focus_side = 1
            page.key("zoom_in")
            s2 = [v.scale for v in page.views]
            check("unlinked: zoom moves only the side last used", s2[0] == s1[0] and s2[1] > s1[1] and not page.linked.get())
            page.key("resync")
            check("re-sync: the right side takes the left side's region, in its own coordinates",
                  abs(page.views[1].relative_zoom() - page.views[0].relative_zoom()) < 1e-6
                  and (page.views[1].cx, page.views[1].cy) == (page.views[0].cx, page.views[0].cy))
            page.key("toggle_link")
            page.key("actual")
            check("100%: both sides at actual pixels", all(abs(v.scale - 1.0) < 1e-9 for v in page.views) and page.linked.get())
            page._press(0, type("E", (), {"x": 100, "y": 100})())
            page._drag(0, type("E", (), {"x": 60, "y": 80})())
            check("linked drag pans both sides by the same region", page.views[0].cx == page.views[1].cx and page.views[0].cy == page.views[1].cy)
            page.key("fit")
            left, right = page.left_id, page.right_id
            n0 = RV.store_for(app).counts()["decisions"]
            page.key("swap")
            check("swap sides: presentation only -- the sides change, nothing is recorded",
                  (page.left_id, page.right_id) == (right, left) and RV.store_for(app).counts()["decisions"] == n0)
            page.key("swap")

            # decisions by key
            st = RV.store_for(app)
            ops = st.counts()["operations"]
            press(page, "a")
            app.update()
            rows = st.active_for(PAIR, first)
            check("A: Equivalent -> Prefer Left -- two rows in one operation, the left image preferred",
                  {d.kind for d in rows} == {"equivalent_copy", "canonical_location"} and st.counts()["operations"] == ops + 1
                  and [d.value for d in rows if d.kind == "canonical_location"] == [left])
            check("...and the page moves on to the next pair", page.selected != first and page.selected in page.shown)
            press(page, "u")
            app.update()
            check("U: Undo withdraws both rows of that action, and shows the pair again",
                  st.active_for(PAIR, first) == [] and page.selected == first)
            press(page, "l")
            second_choice = [d.value for d in st.active_for(PAIR, first) if d.kind == "canonical_location"]
            check("L: Equivalent -> Prefer Right", second_choice == [right])
            page.show_pair(first)                                   # now Ready for plan: off the queue, still shown on request
            press(page, "d")
            kinds = {d.kind for d in st.active_for(PAIR, first)}
            check("D on that pair: Different, and the retention row is withdrawn in the same operation", kinds == {"different"})
            target = page.selected
            press(page, "e")
            check("E: Equivalent -> Keep Both", {d.kind for d in st.active_for(PAIR, target)} == {"equivalent_copy", "keep_both"})
            target = page.selected
            press(page, "v")
            check("V: Related Variant -> Keep Both", {d.kind for d in st.active_for(PAIR, target)} == {"related_variant", "keep_both"})
            page.show_pair(target)
            press(page, "bracketleft")
            check("[: the left image as canonical, beside Keep Both", {d.kind for d in st.active_for(PAIR, target)} == {"related_variant", "keep_both", "canonical_location"})
            page.filter_var.set(RT.PAIR_QUEUE_WORD)
            page.reload()
            remaining = list(page.shown)
            if remaining:
                cur = remaining[0]
                page.show_pair(cur)
                n_events = len(st.review_events_for(PAIR, cur))
                press(page, "s")
                check("S: Skip records an audit row and moves on; the pair stays on the queue",
                      len(st.review_events_for(PAIR, cur)) == n_events + 1 and cur in page.shown)
                page.show_pair(cur)
                press(page, "w")
                dlg = page.defer_dialog
                offered = [w.cget("value") for w in _radios(dlg.win)]
                check("W: the pair's Defer dialog offers 'until a preview is possible', not 'until fingerprints are current'",
                      "snooze_until_preview_available" in offered and "snooze_until_hash_current" not in offered, str(offered))
                dlg.choice.set("defer_indefinitely")
                dlg._confirm()
                app.update()
                check("...and the deferred pair leaves the queue", cur not in page.shown and page.routing.pairs[cur].primary == RT.DEFERRED)
            check("progress is the record: the summary says how many pairs are reviewed", "reviewed" in page.summary_var.get())

            # exact copies on request
            page.filter_var.set(IG.LENS_EXACT)
            page.refresh_list()
            ex = page.shown[0] if page.shown else None
            check("Show 'Exact copies': the byte-identical pair can be opened", ex is not None and page.proj.pair(ex).exact_content)
            check("...no relationship is asked of it: the pair actions are off, the canonical keys are on",
                  str(page.buttons["equivalent_left"].cget("state")) == "disabled" and str(page.buttons["canonical_left"].cget("state")) == "normal")
            gid = page._group_of_pair().group_id
            press(page, "bracketright")
            check("]: the right image becomes the exact-duplicate GROUP's canonical (where that decision lives)",
                  [d.value for d in st.active_for(GROUP, gid) if d.kind == "canonical_location"] == [page.right_id]
                  and st.active_for(PAIR, ex) == [])
            press(page, "u")
            check("...and U undoes it", st.active_for(GROUP, gid) == [])

            # the Decide page's way in
            app.show_decide(content_id=gid)
            app.update()
            rp = app.review_page
            check("the Decide page offers Compare images... for a group of images", str(rp.buttons["compare"].cget("state")) == "normal")
            rp.compare_images()
            app.update()
            sp = app.similar_page
            check("...which opens the two copies side by side on the Similar Images page (the exact pair)",
                  sp.selected == ex and {sp.left_id, sp.right_id} == set(sp.proj.pair(ex).members) and all(v.image is not None for v in sp.views))
            app.close_project() if hasattr(app, "close_project") else None
            app.destroy()
        check("no Python-level write outside the project and log folders while the window ran", not guard.violations, str(guard.violations[:5]))
        after = BASE.fingerprint_tree(corpus)
        check("every corpus file untouched by the window", after == before)
        check("no unexpected error dialog", not [a for a in asked if a[0] == "showerror"], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)


def press(page, keysym):
    """A keystroke, the way the bindings deliver it: the keymap names the action."""
    from Phase3.imagegame import KEYMAP
    return page.key({k: a for k, a, _l in KEYMAP}[keysym])


def _radios(win):
    from tkinter import ttk
    out = []

    def walk(w):
        for c in w.winfo_children():
            if isinstance(c, ttk.Radiobutton):
                out.append(c)
            walk(c)
    walk(win)
    return out


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3vis_"))
    try:
        test_registry()
        test_resolver()
        test_routing()
        test_generator()
        test_project(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 VISUAL (Build 5): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
