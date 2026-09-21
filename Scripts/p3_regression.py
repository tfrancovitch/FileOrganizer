#!/usr/bin/env python3
r"""Phase 3 (Builds 1-2) regression: persistence, resolution, the gate, the window.

What it proves, in order:

  1. The registry is a gate: unknown kinds, wrong targets and malformed
     values are refused before anything is written; an override needs its
     explicit confirmation and a rationale.
  2. The resolver, on hand-built models: the eight-step order; protection
     before ranking; the override that lifts it; keep-all outranked by an
     explicit location decision; the canonical is a keeper; the last-copy
     floor; hard-link aliases reclaim nothing; unknown physical identity
     means unknown reclaim; policy recommendations (unique, tie, relative
     'over', avoid, root preference) never change a status; withdrawal does
     not revive what was superseded; orphaned decisions are reported; the
     same input in any order gives the same answer; and it is fast.
  3. A real project (a small corpus with two roots, a hard link and two
     duplicate groups): the Pre-Scan and Find My Duplicates through the
     worker; migration 009 on a fresh project AND on a schema-8 database
     (pre-migration backup made, every Phase 2 row untouched); decisions,
     policies, overrides, withdrawals and supersession through the store;
     one operation is one transaction; the projection rebuilt from a copy
     of the database equals the live one; the journal export.
  4. Zero source-file mutation: every file under the corpus has the bytes,
     size and modified time it was built with after everything above, no
     path appeared or vanished, and no Python-level write touched a path
     outside the project folder while Phase 3 ran.
  5. If a display is available: the window's Decide line, the Decide page
     and every action on it, the Override, Policies and Defer dialogs, Skip,
     Restore, Undo, the routes, lenses and sort, the Files-page jump-in, and
     -- after a rescan moved the evidence -- Needs Revalidation and Confirm
     decisions; headless, no mainloop.
  6. Build 3 (B9), on hand-built models: the routing precedence table over
     every combination of conditions; open/closed review events (a skip
     never opens; a restore closes what it names); every deferral trigger;
     drift detection; the three computed blockers and the recorded one;
     coverage warnings versus unusable roots; the B8 defer_review record as
     a deferral; the advisory policy tie; the Conflicts order; determinism.
  7. Build 3 on the real project: the detector is idempotent, writes what
     changed and nothing else; a snooze that elapsed is restored; a rescan
     that changes a copy routes the group to Needs Revalidation with the
     decision untouched; re-recording clears it; Skip versus Defer.

Run:  python Scripts\p3_regression.py
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------

from Phase3.model import Evidence, Location, Group, Decision, PolicyVersion, path_key, active_decisions  # noqa: E402
from Phase3 import resolve as R                                                                            # noqa: E402
from Phase3 import registry as REG                                                                         # noqa: E402
from Phase3 import routing as RT                                                                           # noqa: E402


def loc(lid, path, root="Primary", content="C1", phys=None, size=1000, present=True, known=True):
    return Location(lid, path, root, content, phys if known else None, present, size, f"obs-{lid}", (root, path_key(path)))


def dec(did, kind, target_kind, ref, value=True, supersedes=None, withdrawn=False, seq=None):
    return Decision(did, target_kind, ref, kind, value, "explicit_human", supersedes, withdrawn, f"op-{did}", seq or int(did[1:]))


def pol(pvid, kind, scope, effect=None, active=True, policy_id=None):
    if "path" in scope and "path_key" not in scope:
        scope = {"path_key": path_key(scope["path"]), "path": scope["path"]}
    if effect and "over" in effect and "over_path_key" not in effect:
        effect = {"over_path_key": path_key(effect["over"]), "over": effect["over"]}
    return PolicyVersion(pvid, policy_id or ("P" + pvid), kind, scope, effect or {}, 1, active)


def model(locations, decisions=(), policies=()):
    groups = {}
    for l in locations:
        if l.content_id is None or not l.in_current_group:
            continue                                    # a location that left its group is loaded, not grouped
        groups.setdefault(l.content_id, []).append(l.location_id)
    return Evidence({l.location_id: l for l in locations},
                    {c: Group(c, c, tuple(ids)) for c, ids in groups.items()},
                    list(decisions), list(policies))


LOCATION, GROUP = REG.LOCATION, REG.GROUP
NOW = "2026-09-20T12:00:00Z"


def ev_row(eid, kind, target_kind, ref, return_kind=None, condition=None, until=None, detail=None, refers_to=None, when=None):
    from Phase3.model import ReviewEvent
    return ReviewEvent(eid, target_kind, ref, kind, return_kind, condition, until, detail or {}, refers_to,
                       when or f"2026-09-{10 + int(eid[1:]):02d}T00:00:00Z", "explicit_human", int(eid[1:]))


def bound(locs, target_kind=LOCATION, content=None):
    """An evidence binding the way the store records one: ids only."""
    if target_kind == LOCATION:
        l = locs[0]
        return {"file_path_id": l.location_id, "observation_id": l.observation_id, "target_kind": LOCATION,
                "group": {"members": [{"file_path_id": x.location_id, "observation_id": x.observation_id} for x in locs]}}
    return {"target_kind": GROUP, "content_id": content or locs[0].content_id,
            "members": [{"file_path_id": x.location_id, "observation_id": x.observation_id} for x in locs]}


def bdec(did, kind, target_kind, ref, value, locs, **kw):
    """A decision with a real binding to the given locations."""
    d = dec(did, kind, target_kind, ref, value, **kw)
    from dataclasses import replace
    return replace(d, binding=bound(locs, target_kind, ref if target_kind == GROUP else None))


def routed(locations, decisions=(), policies=(), events=(), roots=None, now=NOW):
    """Resolve and route a hand-built model."""
    ev = model(locations, decisions, policies)
    ev.events = list(events)
    ev.roots = roots or {}
    ev.now = now
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, now)


# ---------------------------------------------------------------------------
# 1. The registry
# ---------------------------------------------------------------------------

def test_registry():
    section("1. The decision-kind registry is a gate")
    kinds = set(REG.DECISION_KINDS)
    check("six decision kinds, exactly the research's", kinds == {
        "must_keep_location", "keep_all_group", "canonical_location", "redundant_location", "defer_review", "override_protection"}, str(kinds))
    check("five policy kinds", set(REG.POLICY_KINDS) == {
        "protect_source_root", "protect_folder_subtree", "prefer_source_root", "prefer_folder_subtree", "avoid_folder_subtree"})
    check("eight-step resolution order as the catalog names it", REG.RESOLUTION_ORDER == (
        "hard_constraint", "explicit_location_decision", "explicit_group_decision", "folder_policy",
        "source_root_policy", "project_policy", "optional_heuristic", "display_only_tiebreak"))
    catalog = json.loads((SCRIPTS.parent / "Resources" / "Phase3" / "keeper_rule_catalog.json").read_text(encoding="utf-8"))
    check("the shipped catalog agrees with the order in code", tuple(catalog["resolution_order"]) == REG.RESOLUTION_ORDER)
    for k in REG.DECISION_KINDS.values():
        check(f"{k.key}: targets, value kind, class, domain, confirmation all declared",
              k.target_kinds and k.value_kind in ("flag", "location_ref", "override")
              and (k.resolution_class in REG.RESOLUTION_ORDER or k.resolution_class == "none")
              and k.conflict_domain and k.confirmation in (REG.CONFIRM_NONE, REG.CONFIRM_EXPLICIT_OVERRIDE)
              and k.requires_evidence)
    check("every kind but override records with one click; override needs its own confirmation",
          all(k.confirmation == REG.CONFIRM_NONE for k in REG.DECISION_KINDS.values() if k.key != "override_protection")
          and REG.DECISION_KINDS["override_protection"].confirmation == REG.CONFIRM_EXPLICIT_OVERRIDE)
    check("defer_review is not a retention decision (class none, no reclaim effect)",
          REG.DECISION_KINDS["defer_review"].resolution_class == "none" and not REG.DECISION_KINDS["defer_review"].affects_reclaim)

    def refused(fn):
        try:
            fn()
            return False
        except ValueError:
            return True
    check("unknown decision kind refused", refused(lambda: REG.decision_kind("delete_file")))
    check("flag kinds take only true", refused(lambda: REG.DECISION_KINDS["must_keep_location"].validate_value("yes")))
    check("canonical needs a location reference", refused(lambda: REG.DECISION_KINDS["canonical_location"].validate_value(True)))
    check("override needs the policy it defeats", refused(lambda: REG.DECISION_KINDS["override_protection"].validate_value({"policy_id": 1})))
    check("override domain is per rule overridden",
          REG.DECISION_KINDS["override_protection"].domain_key({"policy_id": "1", "policy_version_id": "3"}) == "protection_override:1")
    check("must-keep and redundant share one conflict domain (the later supersedes the earlier)",
          REG.DECISION_KINDS["must_keep_location"].conflict_domain == REG.DECISION_KINDS["redundant_location"].conflict_domain)
    check("policy scope validated: a root policy needs root_key", refused(lambda: REG.POLICY_KINDS["protect_source_root"].validate_scope({"path": "C:\\x"})))
    check("policy scope validated: a folder policy needs path_key", refused(lambda: REG.POLICY_KINDS["protect_folder_subtree"].validate_scope({"root_key": "x"})))


# ---------------------------------------------------------------------------
# 2. The resolver on hand-built models
# ---------------------------------------------------------------------------

def test_resolver():
    section("2. The resolver: order, gate, floors, physical copies, policies, history")
    A = loc("A", r"C:\Docs\a.pdf", phys="P1")
    B = loc("B", r"C:\Downloads\a.pdf", phys="P2")
    C = loc("C", r"E:\Backup\a.pdf", root="Backup", phys="P3")
    protect = pol("PV1", "protect_source_root", {"root_key": "Backup", "root_path": "E:\\Backup"})

    # -- the protection gate ------------------------------------------------
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "C")], [protect])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("a redundant mark against a protected root has no effect: the copy stays protected",
          g.verdict("C").status == R.PROTECTED and "C" not in g.redundant_candidates)
    check("...and the group carries a conflict, no reclaim, not ready",
          [c.kind for c in g.conflicts] == [R.PROTECTED_VS_REDUNDANT] and g.plan_eligible_reclaim_bytes == 0 and not g.ready_for_plan
          and g.review_state == R.CONFLICT)
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "C"),
                           dec("D2", "override_protection", LOCATION, "C", {"policy_id": "PPV1", "policy_version_id": "PV1"})], [protect])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("an override naming that policy version lifts it: the copy is a redundant candidate, 1000 bytes eligible",
          g.verdict("C").status == R.REDUNDANT and g.plan_eligible_reclaim_bytes == 1000 and not g.conflicts and g.ready_for_plan
          and g.verdict("C").overridden == (("PV1", "D2"),))
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "C"),
                           dec("D2", "override_protection", LOCATION, "C", {"policy_id": "PPV1", "policy_version_id": "PV0"})], [protect])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("an override of a different policy version does not lift it", g.verdict("C").status == R.PROTECTED)
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "C"),
                           dec("D2", "override_protection", LOCATION, "C", {"policy_id": "PPV1", "policy_version_id": "PV1"}, withdrawn=True)], [protect])
    check("a withdrawn override protects again", R.resolve_group(ev, ev.groups["C1"]).verdict("C").status == R.PROTECTED)
    retired = pol("PV1", "protect_source_root", {"root_key": "Backup", "root_path": "E:\\Backup"}, active=False)
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "C")], [retired])
    check("a retired protection policy no longer protects", R.resolve_group(ev, ev.groups["C1"]).verdict("C").status == R.REDUNDANT)
    folder = pol("PV2", "protect_folder_subtree", {"path": r"C:\Docs"})
    ev = model([A, B, loc("D", r"C:\Docs2\a.pdf", phys="P4")], [], [folder])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("a folder protection covers the folder and its subtree, not a sibling with the same prefix",
          g.verdict("A").status == R.PROTECTED and g.verdict("D").status == R.UNDECIDED)

    # -- explicit decisions and the order -----------------------------------
    ev = model([A, B, C], [dec("D1", "must_keep_location", LOCATION, "A"), dec("D2", "canonical_location", GROUP, "C1", "A"),
                           dec("D3", "redundant_location", LOCATION, "B")], [protect])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("F15 shape: keepers {A, C}, canonical A, protected {C}, redundant {B}, resolved, ready, 1000 eligible",
          g.keeper_set == ("A", "C") and g.canonical == "A" and g.protected == ("C",) and g.redundant_candidates == ("B",)
          and g.review_state == R.RESOLVED and g.ready_for_plan and g.plan_eligible_reclaim_bytes == 1000, g.as_dict())
    ev = model([A, B, C], [dec("D1", "keep_all_group", GROUP, "C1")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("Keep All: every member a keeper, zero reclaim, resolved, not ready -- a valid resolved state",
          g.keeper_set == ("A", "B", "C") and g.plan_eligible_reclaim_bytes == 0 and g.review_state == R.RESOLVED and not g.ready_for_plan)
    ev = model([A, B, C], [dec("D1", "keep_all_group", GROUP, "C1"), dec("D2", "redundant_location", LOCATION, "B")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("an explicit location decision (class 2) outranks Keep All (class 3)",
          g.verdict("B").status == R.REDUNDANT and g.keeper_set == ("A", "C") and not g.conflicts
          and any("outranked" in n for n in g.verdict("B").notes))
    ev = model([A, B, C], [dec("D1", "canonical_location", GROUP, "C1", "B")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("the canonical is a keeper by itself", g.verdict("B").status == R.KEEPER and g.keeper_set == ("B",) and g.effective_canonical == "B"
          and g.canonical_origin == "explicit_human")
    ev = model([A, B, C], [dec("D1", "canonical_location", GROUP, "C1", "B"), dec("D2", "redundant_location", LOCATION, "B")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("canonical AND redundant on one copy: it stays a keeper and the group is in conflict",
          g.verdict("B").status == R.KEEPER and [(c.kind, c.reason) for c in g.conflicts] == [(R.CANONICAL_NO_LONGER_KEEPER, "marked_redundant")]
          and g.plan_eligible_reclaim_bytes == 0)
    ev = model([A, B, C], [dec("D1", "canonical_location", GROUP, "C1", "Z")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("a canonical that is no longer a member is a conflict, not a crash",
          [(c.kind, c.reason) for c in g.conflicts] == [(R.CANONICAL_NO_LONGER_KEEPER, "left_group")] and g.canonical is None)
    ev = model([A, B, C], [dec("D1", "redundant_location", LOCATION, "A"), dec("D2", "redundant_location", LOCATION, "B"),
                           dec("D3", "redundant_location", LOCATION, "C")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("every copy marked redundant trips the last-copy floor: conflict, nothing eligible",
          [c.kind for c in g.conflicts] == ["minimum_file_locations"] and g.plan_eligible_locations == () and not g.ready_for_plan)
    ev = model([A, B, C], [dec("D1", "must_keep_location", LOCATION, "A"), dec("D2", "redundant_location", LOCATION, "A")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("must-keep (hard constraint) outranks a redundant mark on the same copy", g.verdict("A").status == R.KEEPER and not g.conflicts)
    ev = model([A, B, C], [dec("D1", "defer_review", GROUP, "C1")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("defer is a durable 'not yet': deferred state, no status changes", g.review_state == R.DEFERRED and g.deferred
          and all(v.status == R.UNDECIDED for v in g.members))
    ev = model([A, B, C], [dec("D1", "defer_review", LOCATION, "B")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("a location defer marks the member, not the group", g.verdict("B").deferred and g.review_state == R.IN_PROGRESS)
    ev = model([A, B, C])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("no decision, no policy: unreviewed, a tie, potential reclaim 2000 but nothing eligible",
          g.review_state == R.UNREVIEWED and g.resolution_state == R.RS_TIE and g.potential_reclaim_bytes == 2000
          and g.plan_eligible_reclaim_bytes == 0 and g.suggested_canonical is None)

    # -- physical copies and hard-link aliases -----------------------------
    L1 = loc("L1", r"C:\Documents\report.pdf", phys="P1", size=10_000_000)
    L2 = loc("L2", r"C:\Documents\report-copy.pdf", phys="P1", size=10_000_000)   # hard-link alias of L1
    L3 = loc("L3", r"E:\Backup\report.pdf", root="Backup", phys="P2", size=10_000_000)
    L4 = loc("L4", r"C:\Downloads\report.pdf", phys="P3", size=10_000_000)
    ev = model([L1, L2, L3, L4], [dec("D1", "must_keep_location", LOCATION, "L1"), dec("D2", "canonical_location", GROUP, "C1", "L1"),
                                  dec("D3", "redundant_location", LOCATION, "L4")], [protect])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("F01 shape: 4 locations, 3 physical copies, 1 alias; keepers {L1, L3}; L4 eligible for 10 MB",
          g.location_count == 4 and g.physical_copies == 3 and g.hardlink_aliases == 1 and g.keeper_set == ("L1", "L3")
          and g.plan_eligible_reclaim_bytes == 10_000_000 and g.potential_reclaim_bytes == 20_000_000)
    ev = model([L1, L2, L3, L4], [dec("D1", "redundant_location", LOCATION, "L2")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("a redundant hard-link alias of a kept copy reclaims nothing (the object stays)",
          g.plan_eligible_locations == ("L2",) and g.plan_eligible_reclaim_bytes == 0 and g.ready_for_plan)
    ev = model([L1, L2, L3, L4], [dec("D1", "redundant_location", LOCATION, "L1"), dec("D2", "redundant_location", LOCATION, "L2")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("both names of one object redundant: the object's bytes count once", g.plan_eligible_reclaim_bytes == 10_000_000)
    U = loc("U", r"C:\x\a.pdf", phys=None, known=False)
    ev = model([A, B, U], [dec("D1", "redundant_location", LOCATION, "B")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("unknown physical identity: copies unknown, potential unknown, eligible reclaim unknown (None), never guessed",
          g.physical_copies is None and g.potential_reclaim_bytes is None and g.plan_eligible_reclaim_bytes is None
          and g.plan_eligible_locations == ("B",) and not g.physical_identity_complete)

    # -- policies recommend; they never decide --------------------------------
    prefer = pol("PV3", "prefer_folder_subtree", {"path": r"C:\Documents"}, {"over": r"C:\Downloads"})
    G1a, G1b = loc("L1", r"C:\Documents\a.pdf", phys="P1", content="G1"), loc("L2", r"C:\Downloads\a.pdf", phys="P2", content="G1")
    G2a, G2b = loc("L3", r"C:\Documents\b.pdf", phys="P3", content="G2"), loc("L4", r"C:\Downloads\b.pdf", phys="P4", content="G2")
    G3a, G3b = loc("L5", r"C:\Documents\c.pdf", phys="P5", content="G3"), loc("L6", r"E:\Other\c.pdf", root="Backup", phys="P6", content="G3")
    ev = model([G1a, G1b, G2a, G2b, G3a, G3b], [dec("D1", "canonical_location", GROUP, "G2", "L4")], [prefer])
    proj = R.resolve_all(ev)
    g1, g2, g3 = proj.group("G1"), proj.group("G2"), proj.group("G3")
    check("F03 shape: policy suggests L1 for G1 (labelled policy), the explicit L4 wins for G2",
          g1.suggested_canonical == "L1" and g1.effective_canonical == "L1" and g1.canonical_origin == "policy"
          and g2.suggested_canonical == "L3" and g2.effective_canonical == "L4" and g2.canonical_origin == "explicit_human")
    check("a preference never changes a status: every member of G1 is still undecided, nothing eligible",
          all(v.status == R.UNDECIDED for v in g1.members) and g1.plan_eligible_reclaim_bytes == 0 and g1.review_state == R.UNREVIEWED)
    check("'over' is relative: with no Downloads member the preference stays silent (G3 is a tie)",
          g3.suggested_canonical is None and g3.resolution_state == R.RS_TIE)
    plain = pol("PV6", "prefer_folder_subtree", {"path": r"C:\Documents"})
    ev = model([G1a, loc("L9", r"C:\Documents\sub\a.pdf", phys="P9", content="G1"), G1b], [], [plain])
    g = R.resolve_all(ev).group("G1")
    check("two preferred candidates: no suggestion -- a tie is not a recommendation",
          g.suggested_canonical is None and set(g.policy_preferred) == {"L1", "L9"} and g.resolution_state == R.RS_POLICY_MULTIPLE)
    avoid = pol("PV4", "avoid_folder_subtree", {"path": r"C:\Downloads"})
    ev = model([G1a, G1b], [], [avoid])
    g = R.resolve_all(ev).group("G1")
    check("avoid ranks below: the other copy is the unique suggestion", g.suggested_canonical == "L1" and g.verdict("L2").policy_tier == "avoided")
    root_pref = pol("PV5", "prefer_source_root", {"root_key": "Backup", "root_path": "E:\\"})
    ev = model([G3a, G3b], [], [root_pref])
    g = R.resolve_all(ev).group("G3")
    check("a source-root preference singles out the copy in that root", g.suggested_canonical == "L6" and g.resolution_state == R.RS_POLICY_UNIQUE)
    ev = model([G1a, G1b], [], [prefer, avoid])
    g = R.resolve_all(ev).group("G1")
    check("a copy both preferred and avoided is neutral, and says so",
          g.suggested_canonical == "L1" and g.verdict("L2").policy_tier == "avoided")

    # -- history semantics -------------------------------------------------------
    ev = model([A, B], [dec("D1", "canonical_location", GROUP, "C1", "A"), dec("D2", "canonical_location", GROUP, "C1", "B", supersedes="D1", withdrawn=True),
                        dec("D3", "canonical_location", GROUP, "C1", "A", supersedes="D2")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("F09 shape: D3 speaks; D1 stays superseded although D2 was withdrawn", g.canonical == "A" and g.canonical_decision_id == "D3"
          and [d.decision_id for d in active_decisions(ev.decisions)] == ["D3"])
    ev = model([A, B], [dec("D1", "canonical_location", GROUP, "C1", "A"), dec("D2", "canonical_location", GROUP, "C1", "B", supersedes="D1", withdrawn=True)])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("after the withdrawal and before the re-apply there is no canonical (no silent revival)", g.canonical is None)
    ev = model([A, B], [dec("D1", "canonical_location", GROUP, "C1", "A", seq=1), dec("D2", "canonical_location", GROUP, "C1", "B", seq=2)])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("two active decisions in one domain (imported data): the later recorded speaks", g.canonical == "B")
    ev = model([A, B], [dec("D1", "redundant_location", LOCATION, "GONE"), dec("D2", "keep_all_group", GROUP, "C9")])
    proj = R.resolve_all(ev)
    check("decisions on targets no longer in current evidence are reported as orphaned, not applied",
          proj.orphaned_decisions == ("D1", "D2") and proj.totals["orphaned_decisions"] == 2)

    # -- determinism and speed -------------------------------------------------------
    import random
    big = []
    for i in range(4000):
        n = 2 + (i % 4)
        for j in range(n):
            big.append(loc(f"L{i}_{j}", rf"C:\R{j % 3}\f{i}\a{j}.bin", root=f"R{j % 3}", content=f"G{i}", phys=f"P{i}_{j // 2}", size=4096 + i))
    decisions = [dec(f"D{i}", "redundant_location", LOCATION, f"L{i}_1") for i in range(0, 4000, 3)]
    decisions += [dec(f"D{i}", "canonical_location", GROUP, f"G{i}", f"L{i}_0") for i in range(1, 4000, 5)]
    policies = [pol("PV1", "protect_source_root", {"root_key": "R2", "root_path": "C:\\R2"}), pol("PV2", "prefer_folder_subtree", {"path": r"C:\R0"})]
    ev = model(big, decisions, policies)
    t0 = time.perf_counter()
    first = R.resolve_all(ev)
    elapsed = time.perf_counter() - t0
    check(f"4,000 groups / {len(big):,} locations resolve in {elapsed:.2f} s (< 3 s)", elapsed < 3.0)
    rnd = random.Random(7)
    shuffled = Evidence(dict(rnd.sample(list(ev.locations.items()), len(ev.locations))),
                        dict(rnd.sample(list(ev.groups.items()), len(ev.groups))),
                        rnd.sample(ev.decisions, len(ev.decisions)), rnd.sample(ev.policies, len(ev.policies)))
    second = R.resolve_all(shuffled)
    canon = lambda p: json.dumps([g.as_dict() for g in p.groups] + [p.totals], sort_keys=True, default=str)  # noqa: E731
    check("the same model in another row order gives the identical projection", canon(first) == canon(second))
    check("the totals add up", first.totals["groups"] == 4000 and first.totals["redundant_locations"] + first.totals["keeper_locations"]
          + first.totals["undecided_locations"] == len(big))


# ---------------------------------------------------------------------------
# 3. A real project
# ---------------------------------------------------------------------------

def build_corpus(base: Path):
    root1, root2 = base / "Root1", base / "Root2"
    for d in (root1 / "Documents", root1 / "Downloads", root1 / "Archive", root2 / "Backup"):
        d.mkdir(parents=True)
    A, B, U = b"alpha" * 20000, b"beta" * 30000, b"unique" * 1000
    (root1 / "Documents" / "report.pdf").write_bytes(A)
    (root1 / "Downloads" / "report.pdf").write_bytes(A)
    (root2 / "Backup" / "report.pdf").write_bytes(A)
    os.link(root1 / "Documents" / "report.pdf", root1 / "Documents" / "report-link.pdf")   # a hard-link alias
    (root1 / "Documents" / "tax.docx").write_bytes(B)
    (root2 / "Backup" / "tax.docx").write_bytes(B)
    (root1 / "Archive" / "tax.docx").write_bytes(B)
    (root1 / "Documents" / "only.txt").write_bytes(U)
    return root1, root2


def fingerprint_tree(base: Path):
    files, everything = {}, set()
    for dirpath, dirnames, filenames in os.walk(base):
        for d in dirnames:
            everything.add(os.path.join(dirpath, d))
        for f in filenames:
            p = os.path.join(dirpath, f)
            everything.add(p)
            st = os.stat(p)
            with open(p, "rb") as h:
                digest = hashlib.sha256(h.read()).hexdigest()
            files[p] = (st.st_size, st.st_mtime_ns, digest)
    return {"files": files, "everything": everything}


class WriteGuard:
    """Records every Python-level write outside the allowed folders."""

    def __init__(self, allowed):
        self.allowed = [os.path.normcase(os.path.abspath(str(a))) for a in allowed]
        self.violations = []
        self.saved = {}

    def _ok(self, path):
        try:
            p = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        except Exception:                                           # noqa: BLE001
            return True
        return any(p == a or p.startswith(a + os.sep) for a in self.allowed)

    def _note(self, what, path):
        if not self._ok(path):
            self.violations.append(f"{what}: {os.fsdecode(path)}")

    def __enter__(self):
        guard = self
        real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

        def g_open(file, mode="r", *a, **k):
            if isinstance(file, (str, bytes, os.PathLike)) and any(c in str(mode) for c in "wax+"):
                guard._note(f"open({mode})", file)
            return real_open(file, mode, *a, **k)

        def g_io_open(file, mode="r", *a, **k):
            if isinstance(file, (str, bytes, os.PathLike)) and any(c in str(mode) for c in "wax+"):
                guard._note(f"io.open({mode})", file)
            return real_io_open(file, mode, *a, **k)

        def g_os_open(path, flags, *a, **k):
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
                guard._note("os.open", path)
            return real_os_open(path, flags, *a, **k)
        builtins.open, io.open, os.open = g_open, g_io_open, g_os_open
        self.saved = {"open": real_open, "io.open": real_io_open, "os.open": real_os_open}
        for name in ("remove", "unlink", "rename", "replace", "utime", "link", "symlink", "rmdir", "mkdir", "makedirs", "chmod", "truncate"):
            real = getattr(os, name)
            self.saved[name] = real

            def wrapped(path, *a, _real=real, _name=name, **k):
                guard._note(f"os.{_name}", path)
                return _real(path, *a, **k)
            setattr(os, name, wrapped)
        return self

    def __exit__(self, *exc):
        builtins.open, io.open, os.open = self.saved["open"], self.saved["io.open"], self.saved["os.open"]
        for name in ("remove", "unlink", "rename", "replace", "utime", "link", "symlink", "rmdir", "mkdir", "makedirs", "chmod", "truncate"):
            setattr(os, name, self.saved[name])
        return False


def test_project(tmp: Path):
    section("3. A real project: runs, migration, the store, rebuildable projections")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2.core import connect
    import fo_db
    from Phase3 import evidence as E, store as S

    app_root = tmp / "AppRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "Corpus"
    root1, root2 = build_corpus(corpus)
    before = fingerprint_tree(corpus)

    out = RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(root1), str(root2)], project_name="P3Reg")).run()
    check("Pre-Scan through the worker", out.ok, f"{out.status}: {out.message}")
    project_dir = app_root / "Projects" / "P3Reg"
    out = RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    check("Find My Duplicates through the worker", out.ok, f"{out.status}: {out.message}")

    conn = connect(project_dir, write=True)
    check("a fresh project lands on schema 10 with the six p3 tables",
          conn.execute("PRAGMA user_version").fetchone()[0] == 10 and {r[0] for r in conn.execute(
              "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'p3_%'")} == {
              "p3_operation", "p3_decision", "p3_decision_withdrawal", "p3_policy", "p3_policy_version", "p3_review_event"})
    check("app_meta carries the Phase 3 contracts",
          conn.execute("SELECT value FROM app_meta WHERE key='phase3.decision_schema'").fetchone()[0] == "fileorganizer.p3.decisions/1")
    check("one product version: fo_db.APP_VERSION == Phase2.VERSION == 'B9', schema 10 everywhere",
          fo_db.APP_VERSION == "B9" and __import__("Phase2").VERSION == "B9" and fo_db.APP_SCHEMA_VERSION == 10
          and __import__("Phase2.core").core.REQUIRED_SCHEMA_VERSION == 10 and __import__("self_check").REQUIRED_SCHEMA_VERSION == 10)

    # -- migrating a schema-8 database ---------------------------------------
    old_dir = tmp / "Old8"
    old_dir.mkdir()
    mig8 = tmp / "mig8"
    mig8.mkdir()
    for f in sorted((SCRIPTS / "Database" / "migrations").glob("*.sql"))[:8]:
        shutil.copy(f, mig8 / f.name)
    paths = fo_db.ProjectPaths(str(old_dir))
    paths.ensure_dirs()
    c8 = fo_db.connect(paths.database_file, create=True)
    fo_db.apply_migrations(c8, paths, app_version="B7.2", migrations_dir=str(mig8))
    c8.execute("INSERT INTO project(project_id, project_uid, name, created_utc, app_version_created) VALUES(1,'uid-8','Old8','2026-01-01T00:00:00Z','B7.2')")
    c8.execute("INSERT INTO app_meta(key,value,updated_utc) VALUES('database_uid','uid-8','2026-01-01T00:00:00Z')")
    c8.execute("INSERT INTO source_root(project_id, root_path, root_path_key, added_utc, is_active, root_ordinal) VALUES(1,'C:\\x','c:\\x','2026-01-01T00:00:00Z',1,0)")
    c8.execute("INSERT INTO run(project_id, run_uid, run_kind, status, started_utc, app_version, schema_version) VALUES(1,'r1','prescan','completed','2026-01-01T00:00:00Z','B7.2',8)")
    c8.commit()
    snapshot8 = {t: c8.execute(f"SELECT * FROM {t}").fetchall() for t in ("project", "source_root", "run", "schema_migration")}
    version8 = c8.execute("PRAGMA user_version").fetchone()[0]
    c8.close()
    fo_db.write_project_json(paths, "uid-8", "Old8", "2026-01-01T00:00:00Z", "B7.2")
    c9, _ = fo_db.open_project(str(old_dir), app_version=fo_db.APP_VERSION)
    check("a schema-8 project opens and migrates to 10", version8 == 8 and c9.execute("PRAGMA user_version").fetchone()[0] == 10)
    check("migrations 009 and 010 recorded with this build's version",
          [r[0] for r in c9.execute("SELECT app_version FROM schema_migration WHERE version IN (9,10) ORDER BY version")] == ["B9", "B9"])
    same = all(c9.execute(f"SELECT * FROM {t}").fetchall() == rows for t, rows in snapshot8.items() if t != "schema_migration")
    check("every pre-existing row is untouched by the migration", same)
    check("a pre-migration backup was made", any(paths.backup_dir and Path(paths.backup_dir).glob("*")))
    c9.close()

    # -- the store -------------------------------------------------------------
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:test")
    ev = E.load_evidence(conn)
    check("evidence: two groups from the Phase 2 projection, seven members", len(ev.groups) == 2 and len(ev.locations) == 7, str(ev.groups))
    by_path = {l.path: l for l in ev.locations.values()}
    L = lambda *parts: by_path[str(Path(*parts))]                   # noqa: E731
    doc, link, down, back = L(root1, "Documents", "report.pdf"), L(root1, "Documents", "report-link.pdf"), L(root1, "Downloads", "report.pdf"), L(root2, "Backup", "report.pdf")
    tax1, tax2 = L(root1, "Documents", "tax.docx"), L(root2, "Backup", "tax.docx")
    report_group, tax_group = doc.content_id, tax1.content_id
    check("the hard link is one physical object under two names", doc.physical_id == link.physical_id and doc.physical_id != down.physical_id)
    g = R.resolve_all(ev).group(report_group)
    check("before any decision: 4 locations, 3 physical copies, 1 alias, potential 195 KB, nothing eligible",
          g.location_count == 4 and g.physical_copies == 3 and g.hardlink_aliases == 1 and g.potential_reclaim_bytes == 200000
          and g.plan_eligible_reclaim_bytes == 0 and g.review_state == R.UNREVIEWED)

    protect = st.create_policy("protect_source_root", {"root_key": back.root_key, "root_path": str(root2)}, rationale="the backup drive")
    prefer = st.create_policy("prefer_folder_subtree", {"path_key": path_key(root1 / "Documents"), "path": str(root1 / "Documents")},
                              {"over_path_key": path_key(root1 / "Downloads"), "over": str(root1 / "Downloads")})
    check("two policies recorded, each version 1", protect["version_no"] == 1 and prefer["version_no"] == 1 and len(st.policies()) == 2)
    proj = R.resolve_all(E.load_evidence(conn))
    g, t = proj.group(report_group), proj.group(tax_group)
    check("the backup copies are protected in both groups", g.verdict(back.location_id).status == R.PROTECTED and t.verdict(tax2.location_id).status == R.PROTECTED)
    check("preferred folder: both Documents names are preferred, so no unique suggestion (a tie)",
          g.verdict(doc.location_id).policy_tier == "preferred" and g.suggested_canonical is None)

    def refused(fn):
        try:
            fn()
            return False
        except ValueError:
            return True
    check("store refuses an unknown kind", refused(lambda: st.record_decision("delete_location", LOCATION, doc.location_id)))
    check("store refuses a kind on the wrong target", refused(lambda: st.record_decision("keep_all_group", LOCATION, doc.location_id)))
    check("store refuses a canonical that is not a member", refused(lambda: st.record_decision("canonical_location", GROUP, report_group, tax1.location_id)))
    check("store refuses a group decision on content that is not a current group", refused(lambda: st.record_decision("keep_all_group", GROUP, "999999")))
    check("store refuses override without confirmation", refused(lambda: st.record_override(back.location_id, protect["policy_id"], "why", confirmed=False)))
    check("store refuses override without a rationale", refused(lambda: st.record_override(back.location_id, protect["policy_id"], " ", confirmed=True)))
    check("store refuses override of a policy that does not cover the location", refused(lambda: st.record_override(doc.location_id, protect["policy_id"], "why", confirmed=True)))
    check("store refuses override of a preference policy", refused(lambda: st.record_override(doc.location_id, prefer["policy_id"], "why", confirmed=True)))
    check("override cannot be recorded through record_decision", refused(lambda: st.record_decision("override_protection", LOCATION, back.location_id, {"policy_id": "1", "policy_version_id": "1"})))
    check("nothing was written by the refusals", st.counts() == {"operations": 2, "decisions": 0, "withdrawals": 0, "policy_versions": 2, "review_events": 0})

    d1 = st.record_decision("canonical_location", GROUP, report_group, doc.location_id, rationale="the working copy")
    d2 = st.record_decision("redundant_location", LOCATION, down.location_id)
    d3 = st.record_decision("redundant_location", LOCATION, back.location_id)
    binding = json.loads(conn.execute("SELECT evidence_binding_json FROM p3_decision WHERE decision_id=?", (d1["decision_id"],)).fetchone()[0])
    check("a group decision binds the group's identity and each member's current observation, ids only",
          binding["schema"] == "fileorganizer.p3.evidence-binding/1" and binding["content_id"] == int(report_group)
          and len(binding["members"]) == 4 and all(m["observation_id"] for m in binding["members"])
          and set(binding) == {"schema", "target_kind", "content_id", "members"}
          and all(set(m) == {"file_path_id", "observation_id"} for m in binding["members"]))
    binding = json.loads(conn.execute("SELECT evidence_binding_json FROM p3_decision WHERE decision_id=?", (d2["decision_id"],)).fetchone()[0])
    check("a location decision binds file, observation, content, hash run and the group",
          binding["file_path_id"] == int(down.location_id) and binding["observation_id"] and binding["content_id"] == int(report_group)
          and binding["hash_run_id"] and binding["group"]["content_id"] == int(report_group))
    op = conn.execute("SELECT * FROM p3_operation WHERE operation_id=?", (d1["operation_id"],)).fetchone()
    check("the operation carries actor, actor kind, agent kind and version, session and command ids, time, note",
          op["actor_id"] == "tester" and op["actor_kind"] == "explicit_human" and op["agent_kind"] == "dashboard"
          and op["agent_version"] == "B9" and op["session_id"] == "session:test" and op["command_id"].startswith("cmd:")
          and op["occurred_utc"].endswith("Z") and op["note"] == "the working copy")
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("the gate in a real project: the protected backup mark is a conflict; Downloads is eligible only once the conflict clears",
          g.review_state == R.CONFLICT and g.verdict(back.location_id).status == R.PROTECTED and g.plan_eligible_reclaim_bytes == 0)
    o = st.record_override(back.location_id, protect["policy_id"], "the backup drive is being retired", confirmed=True)
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("after the explicit override: Downloads and Backup eligible, 195 KB, keepers {doc}, canonical doc",
          g.plan_eligible_locations == tuple(sorted((down.location_id, back.location_id))) and g.plan_eligible_reclaim_bytes == 200000
          and g.keeper_set == (doc.location_id,) and g.canonical == doc.location_id and g.review_state == R.IN_PROGRESS)
    check("the override operation has its own kind and rationale",
          conn.execute("SELECT kind, note FROM p3_operation WHERE operation_id=?", (o["operation_id"],)).fetchone()[:] == ("override_protection", "the backup drive is being retired"))
    d4 = st.record_decision("redundant_location", LOCATION, link.location_id)
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("the hard-link alias marked redundant adds a location but no bytes", len(g.plan_eligible_locations) == 3 and g.plan_eligible_reclaim_bytes == 200000)

    # supersession: keep the Downloads copy after all
    d5 = st.record_decision("must_keep_location", LOCATION, down.location_id)
    check("a keep on a marked copy supersedes the mark (same domain), the mark's row stays",
          d5["supersedes_decision_id"] == d2["decision_id"]
          and conn.execute("SELECT COUNT(*) FROM p3_decision WHERE decision_id=?", (d2["decision_id"],)).fetchone()[0] == 1)
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("...and the copy is a keeper", g.verdict(down.location_id).status == R.KEEPER and g.plan_eligible_reclaim_bytes == 100000)
    # canonical change and withdrawal (F09 in the product)
    d6 = st.record_decision("canonical_location", GROUP, report_group, down.location_id)
    check("a new canonical supersedes the old", d6["supersedes_decision_id"] == d1["decision_id"])
    st.withdraw(d6["decision_id"], "changed my mind")
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("withdrawing the new canonical leaves none (the old stays superseded); the rows survive",
          g.canonical is None and conn.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0] == 7
          and conn.execute("SELECT reason FROM p3_decision_withdrawal WHERE decision_id=?", (d6["decision_id"],)).fetchone()[0] == "changed my mind")
    d7 = st.record_decision("canonical_location", GROUP, report_group, doc.location_id)
    hist = st.history_for(GROUP, report_group)
    check("the group's history: three canonical rows, only the last active; the re-apply supersedes the withdrawn one (F09)",
          [h["decision_kind"] for h in hist] == ["canonical_location"] * 3 and d7["supersedes_decision_id"] == d6["decision_id"]
          and [(h["active"], h["withdrawn"], h["superseded"]) for h in hist] == [(False, False, True), (False, True, True), (True, False, False)])
    check("withdrawing a withdrawn decision is refused", refused(lambda: st.withdraw(d6["decision_id"])))
    check("withdrawing a superseded decision is refused", refused(lambda: st.withdraw(d1["decision_id"])))
    # keep all withdrawing marks in one operation, atomically
    ka = st.record_decision("keep_all_group", GROUP, report_group, True, withdraw=[d3["decision_id"], d4["decision_id"]])
    g = R.resolve_all(E.load_evidence(conn)).group(report_group)
    check("Keep All with two withdrawals in one operation: every copy a keeper, resolved, zero reclaim",
          sorted(ka["withdrawn"]) == sorted([d3["decision_id"], d4["decision_id"]]) and g.keeper_set == tuple(sorted(l.location_id for l in (doc, link, down, back)))
          and g.review_state == R.RESOLVED and g.plan_eligible_reclaim_bytes == 0)
    check("...and the withdrawals share the decision's operation",
          {r[0] for r in conn.execute("SELECT operation_id FROM p3_decision_withdrawal WHERE decision_id IN (?,?)", (d3["decision_id"], d4["decision_id"]))} == {ka["operation_id"]})
    before_counts = st.counts()
    check("a failing operation writes nothing (one operation, one transaction)",
          refused(lambda: st.record_decision("keep_all_group", GROUP, tax_group, True, withdraw=[999999])) and st.counts() == before_counts)
    # defer and policy retirement
    st.record_decision("defer_review", GROUP, tax_group)
    t = R.resolve_all(E.load_evidence(conn)).group(tax_group)
    check("defer on the tax group", t.review_state == R.DEFERRED)
    st.retire_policy(protect["policy_id"], "retired for the test")
    t = R.resolve_all(E.load_evidence(conn)).group(tax_group)
    check("retiring the protection policy (a new version) unprotects; history has two versions",
          t.verdict(tax2.location_id).status == R.UNDECIDED and len(st.policy_history(protect["policy_id"])) == 2
          and st.policy(protect["policy_id"])["status"] == "retired")
    st.revise_policy(protect["policy_id"], rationale="back on")
    t = R.resolve_all(E.load_evidence(conn)).group(tax_group)
    check("reactivating is a third version, and it protects again", t.verdict(tax2.location_id).status == R.PROTECTED
          and st.policy(protect["policy_id"])["version_no"] == 3)
    check("no p3 row was ever updated or deleted: ids are contiguous and counts only grew",
          [r[0] for r in conn.execute("SELECT decision_id FROM p3_decision ORDER BY decision_id")] == list(range(1, 11))
          and conn.execute("SELECT COUNT(*) FROM p3_policy_version").fetchone()[0] == 4)

    # -- rebuildable projection: a copy of the database gives the same answer ----------
    live = R.resolve_all(E.load_evidence(conn))
    conn.commit()
    copy_dir = tmp / "Copy"
    shutil.copytree(project_dir, copy_dir)
    c2 = connect(copy_dir, write=True)
    rebuilt = R.resolve_all(E.load_evidence(c2))
    canon = lambda p: json.dumps([g.as_dict() for g in p.groups] + [list(p.orphaned_decisions), p.totals], sort_keys=True, default=str)  # noqa: E731
    check("F15 in the product: the projection rebuilt from a copy of the record equals the live one", canon(live) == canon(rebuilt))
    c2.close()
    shutil.rmtree(copy_dir)
    journal = project_dir / "Exports" / "DecisionJournal.txt"
    journal.parent.mkdir(exist_ok=True)
    n = S.export_decision_journal(conn, journal)
    text = journal.read_text(encoding="utf-8")
    check("the journal export lists every operation, with decisions, withdrawals and policy versions",
          n == st.counts()["operations"] and "override_protection" in text and "withdrew decision" in text and "policy #" in text)
    conn.close()
    return project_dir, corpus, before, app_root


# ---------------------------------------------------------------------------
# 4. Nothing outside the project database was written
# ---------------------------------------------------------------------------

def test_no_mutation(project_dir, corpus, before, app_root):
    section("4. Zero source-file mutation")
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester")
    ev = E.load_evidence(conn)
    some = sorted(ev.locations)[0]
    with WriteGuard([project_dir, app_root / "Logs"]) as guard:
        st.record_decision("must_keep_location", LOCATION, some)
        st.record_decision("defer_review", LOCATION, some)
        R.resolve_all(E.load_evidence(conn))
        st.withdraw(st.history_for(LOCATION, some)[-1]["decision_id"])
        S.export_decision_journal(conn, project_dir / "Exports" / "DecisionJournal.txt")
    conn.close()
    check("no Python-level write reached a path outside the project folder while Phase 3 ran", not guard.violations, str(guard.violations[:5]))
    after = fingerprint_tree(corpus)
    changed = [p for p, v in before["files"].items() if after["files"].get(p) != v]
    check(f"every one of the {len(before['files'])} corpus files has the size, modified time and bytes it was built with", not changed, str(changed[:5]))
    check("no path appeared or vanished under the corpus", after["everything"] == before["everything"])


# ---------------------------------------------------------------------------
# 6. Build 3: routing on hand-built models
# ---------------------------------------------------------------------------

def test_routing_model():
    section("6. Routing (Build 3): precedence, events, triggers, drift, blockers")
    from itertools import combinations
    order = RT.ROUTE_ORDER
    check("the routes, in the handoff's recommended precedence", order == (
        RT.CONFLICT, RT.NEEDS_REVALIDATION, RT.BLOCKED, RT.DEFERRED, RT.READY_FOR_PLAN, RT.RESOLVED, RT.UNRESOLVED))
    ok = True
    for n in range(1, len(order) + 1):
        for subset in combinations(order, n):
            conds = [RT.Condition(r, "x", "") for r in reversed(subset)]
            if RT._primary(conds) != min(subset, key=order.index):
                ok = False
    check("every combination of conditions (127) resolves to the earliest route in the table", ok)
    check("no condition at all is the ordinary queue", RT._primary([]) == RT.UNRESOLVED)

    A = loc("A", r"C:\Docs\a.pdf", phys="P1")
    B = loc("B", r"C:\Downloads\a.pdf", phys="P2")
    C = loc("C", r"E:\Backup\a.pdf", root="Backup", phys="P3")
    protect = pol("PV1", "protect_source_root", {"root_key": "Backup", "root_path": "E:\\Backup"})

    # -- end to end: several conditions on one group, peeled off one at a time --
    drifted = bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C])
    from dataclasses import replace
    drifted = replace(drifted, binding={**drifted.binding, "members": [{"file_path_id": "A", "observation_id": "obs-OLD"}] + drifted.binding["members"][1:]})
    redundant_c = bdec("D2", "redundant_location", LOCATION, "C", True, [C, A, B])
    unknown = loc("U", r"C:\u\a.pdf", phys=None, known=False)
    deferral = ev_row("E1", "deferred", GROUP, "C1", "time", "until 2099-01-01", "2099-01-01T00:00:00Z")
    ev, proj, rt = routed([A, B, C, unknown], [drifted, redundant_c], [protect], [deferral])
    r = rt.groups["C1"]
    check("conflict + drift + blocker + deferral on one group: primary is Conflict, every condition still reported",
          r.primary == RT.CONFLICT and r.has(RT.NEEDS_REVALIDATION) and r.has(RT.BLOCKED) and r.has(RT.DEFERRED)
          and [c.kind for c in r.of(RT.CONFLICT)] == [R.PROTECTED_VS_REDUNDANT], r.as_dict())
    ev, proj, rt = routed([A, B, C, unknown], [drifted], [protect], [deferral])
    check("...without the conflict: Needs revalidation", rt.groups["C1"].primary == RT.NEEDS_REVALIDATION)
    fresh = bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C, unknown])
    ev, proj, rt = routed([A, B, C, unknown], [fresh], [protect], [deferral])
    check("...with current evidence behind the decision: Blocked (identity unknown)", rt.groups["C1"].primary == RT.BLOCKED
          and [c.kind for c in rt.groups["C1"].of(RT.BLOCKED)] == [RT.PHYSICAL_IDENTITY_UNKNOWN])
    fresh3 = bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C])
    ev, proj, rt = routed([A, B, C], [fresh3], [protect], [deferral])
    check("...with identity known: Deferred", rt.groups["C1"].primary == RT.DEFERRED and rt.groups["C1"].word == "Deferred")
    ev, proj, rt = routed([A, B, C], [fresh3], [protect], [])
    check("...with nothing parked: the ordinary queue, In progress", rt.groups["C1"].primary == RT.UNRESOLVED and rt.groups["C1"].word == "In progress")
    ev, proj, rt = routed([A, B, C], [fresh3, bdec("D2", "redundant_location", LOCATION, "B", True, [B, A, C])], [protect], [])
    check("...with a redundant mark: Ready for plan", rt.groups["C1"].primary == RT.READY_FOR_PLAN)
    ev, proj, rt = routed([A, B, C], [bdec("D1", "keep_all_group", GROUP, "C1", True, [A, B, C])], [], [])
    check("...Keep all: Resolved (nothing to plan) -- not Ready, not the queue", rt.groups["C1"].primary == RT.RESOLVED)
    ev, proj, rt = routed([A, B, C], [], [], [])
    check("...untouched: Unreviewed on the ordinary queue", rt.groups["C1"].word == "Unreviewed" and rt.counts[RT.UNRESOLVED] == 1)

    # -- events: open and closed ---------------------------------------------------
    events = [ev_row("E1", "skipped", GROUP, "C1"), ev_row("E2", "deferred", GROUP, "C1", "manual", "indefinitely"),
              ev_row("E3", "skipped", GROUP, "C1")]
    opened = RT.open_events(events)[(GROUP, "C1")]
    check("a skip never opens anything; the deferral stays open through later skips",
          [e.review_event_id for e in opened["deferred"]] == ["E2"] and not opened["blocked"] and not opened["needs_revalidation"])
    events += [ev_row("E4", "deferred", GROUP, "C1", "time", "until 2099", "2099-01-01")]
    opened = RT.open_events(events)[(GROUP, "C1")]
    check("a new deferral replaces the open one (the latest speaks)", [e.review_event_id for e in opened["deferred"]] == ["E4"])
    events += [ev_row("E5", "restored", GROUP, "C1", "manual", "by hand", refers_to="E4")]
    opened = RT.open_events(events)[(GROUP, "C1")]
    check("a restore that names the deferral closes it", opened["deferred"] == [])
    events2 = [ev_row("E1", "deferred", GROUP, "C1", "manual"), ev_row("E2", "blocked", GROUP, "C1", "preview_available", "no preview"),
               ev_row("E3", "needs_revalidation", GROUP, "C1", "evidence_change", "x"), ev_row("E4", "restored", GROUP, "C1")]
    opened = RT.open_events(events2)[(GROUP, "C1")]
    check("a restore with no reference closes everything open on the target", all(v == [] for v in opened.values()))
    events3 = [ev_row("E1", "deferred", GROUP, "C1", "manual"), ev_row("E2", "deferred", LOCATION, "A", "manual"),
               ev_row("E3", "restored", LOCATION, "A", refers_to="E2")]
    opened = RT.open_events(events3)
    check("a restore on one target leaves another target's deferral open",
          [e.review_event_id for e in opened[(GROUP, "C1")]["deferred"]] == ["E1"] and opened[(LOCATION, "A")]["deferred"] == [])

    # -- deferral triggers --------------------------------------------------------------
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "time", "until", "2026-09-21T00:00:00Z")])
    check("snooze until a date: parked while the date is ahead", rt.groups["C1"].primary == RT.DEFERRED and not rt.groups["C1"].deferral_fired)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "time", "until", "2026-09-19")], now="2026-09-20T12:00:00Z")
    check("...and back on the queue once it has passed, live, with the reason",
          rt.groups["C1"].primary == RT.UNRESOLVED and rt.groups["C1"].deferral_fired and "elapsed" in rt.groups["C1"].deferral_fired_why)
    snap = {"target_kind": GROUP, "content_id": "C1", "members": [{"file_path_id": "A", "observation_id": "obs-A"}, {"file_path_id": "B", "observation_id": "obs-B"}, {"file_path_id": "C", "observation_id": "obs-C"}]}
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "evidence_change", "until changed", detail=snap)])
    check("snooze until the evidence changes: parked while nothing changed", rt.groups["C1"].primary == RT.DEFERRED)
    A2 = Location("A", A.path, A.root_key, A.content_id, A.physical_id, True, A.size_bytes, "obs-A2", A.sort_key)
    ev, proj, rt = routed([A2, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "evidence_change", "until changed", detail=snap)])
    check("...fires when a copy is observed again", rt.groups["C1"].deferral_fired and "observed again" in rt.groups["C1"].deferral_fired_why)
    D = loc("D", r"C:\new\a.pdf", phys="P4")
    ev, proj, rt = routed([A, B, C, D], [], [], [ev_row("E1", "deferred", GROUP, "C1", "evidence_change", "until changed", detail=snap)])
    check("...fires when a member arrives", rt.groups["C1"].deferral_fired and "new member" in rt.groups["C1"].deferral_fired_why)
    from Phase3.model import RootCoverage
    unavailable = {"Backup": RootCoverage("Backup", complete=False, available=False, detail="root unavailable")}
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "source_available", "until back")], roots=unavailable)
    check("snooze until the source is available: parked while a root is unavailable", not rt.groups["C1"].deferral_fired)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "source_available", "until back")], roots={})
    check("...fires once every root is available", rt.groups["C1"].deferral_fired)
    stale_a = Location("A", A.path, A.root_key, A.content_id, A.physical_id, True, A.size_bytes, A.observation_id, A.sort_key, hash_current=False)
    ev, proj, rt = routed([stale_a, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "hash_current", "until current")])
    check("snooze until fingerprints are current: parked while one is stale", not rt.groups["C1"].deferral_fired)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "hash_current", "until current")])
    check("...fires once every fingerprint is current", rt.groups["C1"].deferral_fired)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "deferred", GROUP, "C1", "manual", "indefinitely")], now="2099-01-01T00:00:00Z")
    check("defer indefinitely never fires by itself", rt.groups["C1"].primary == RT.DEFERRED and not rt.groups["C1"].deferral_fired)
    ev, proj, rt = routed([A, B, C], [dec("D1", "defer_review", GROUP, "C1")], [], [])
    check("a B8 defer_review decision routes as a manual deferral", rt.groups["C1"].primary == RT.DEFERRED
          and rt.groups["C1"].of(RT.DEFERRED)[0].decision_ids == ("D1",))

    # -- drift -------------------------------------------------------------------------
    d = bdec("D1", "redundant_location", LOCATION, "B", True, [B, A, C])
    ev, proj, rt = routed([A, B, C], [d])
    check("a location decision on current evidence has no drift", rt.locations["B"].drift[0].applicable)
    B2 = Location("B", B.path, B.root_key, B.content_id, B.physical_id, True, B.size_bytes, "obs-B2", B.sort_key)
    ev, proj, rt = routed([A, B2, C], [d])
    check("...its observation changed: drift names the copy and both observations",
          not rt.locations["B"].drift[0].applicable and "observed again" in rt.locations["B"].drift[0].changes[0]
          and rt.groups["C1"].primary == RT.NEEDS_REVALIDATION)
    g = bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C])
    ev, proj, rt = routed([A, B], [g])
    check("a group decision: a member left -> drift; the decision itself is untouched and still active",
          any("member left" in c for c in rt.groups["C1"].drift[0].changes) and proj.group("C1").canonical == "A")
    ev, proj, rt = routed([A, B, C, D], [g])
    check("...a member arrived -> drift", any("new member" in c for c in rt.groups["C1"].drift[0].changes))
    nobind = dec("D1", "canonical_location", GROUP, "C1", "A")
    ev, proj, rt = routed([A, B], [nobind])
    check("a decision with no binding (imported data) cannot drift", rt.groups["C1"].drift[0].applicable)
    g3 = bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C])
    ev, proj, rt = routed([A, B, C], [g3], [], [ev_row("E1", "needs_revalidation", GROUP, "C1", "evidence_change", "recorded earlier")])
    check("an open revalidation flag on a target whose decisions rest on current evidence is not a route by itself "
          "(the live comparison speaks; the detector will restore it)",
          rt.groups["C1"].primary == RT.UNRESOLVED and not rt.groups["C1"].has(RT.NEEDS_REVALIDATION))
    ev, proj, rt = routed([A, B2, C], [d], [], [ev_row("E1", "needs_revalidation", LOCATION, "B", "evidence_change", "recorded")])
    check("...and where live drift exists, the open flag is carried on the condition as provenance",
          rt.locations["B"].of(RT.NEEDS_REVALIDATION)[0].event_id == "E1")

    # -- blockers ---------------------------------------------------------------------------
    ev, proj, rt = routed([A, B, unknown], [])
    check("physical identity unknown on a member blocks the group", rt.groups["C1"].primary == RT.BLOCKED
          and rt.groups["C1"].of(RT.BLOCKED)[0].kind == RT.PHYSICAL_IDENTITY_UNKNOWN)
    interrupted = {"Primary": RootCoverage("Primary", complete=False, available=True, detail="latest scan interrupted")}
    ev, proj, rt = routed([A, B, C], [], [], [], roots=interrupted)
    check("an interrupted walk of a root blocks its groups", rt.groups["C1"].primary == RT.BLOCKED
          and rt.groups["C1"].of(RT.BLOCKED)[0].kind == RT.INCOMPLETE_ROOT_COVERAGE)
    warnings = {"Primary": RootCoverage("Primary", complete=False, available=True, detail="3 inaccessible")}
    ev, proj, rt = routed([A, B, C], [], [], [], roots=warnings)
    check("a completed walk with inaccessible folders warns but does not block (one denied folder must not park every group)",
          rt.groups["C1"].primary == RT.UNRESOLVED and any(c.kind == "coverage_warning" for c in rt.groups["C1"].conditions))
    ev, proj, rt = routed([A, B, C], [bdec("D1", "redundant_location", LOCATION, "B", True, [B, A, C])], [], [],
                          roots={"Primary": RootCoverage("Primary", False, False, "root unavailable")})
    check("an unavailable root blocks", rt.groups["C1"].primary == RT.BLOCKED)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "blocked", GROUP, "C1", "preview_available", "no preview yet")])
    check("a recorded blocker of a kind this build cannot compute (a preview) is honoured", rt.groups["C1"].primary == RT.BLOCKED
          and rt.groups["C1"].of(RT.BLOCKED)[0].kind == RT.RECORDED_BLOCK)
    ev, proj, rt = routed([A, B, C], [], [], [ev_row("E1", "blocked", GROUP, "C1", "evidence_change", "identity unknown")])
    check("a recorded blocker of a computable kind is ignored once the live evidence has cleared it", rt.groups["C1"].primary == RT.UNRESOLVED)
    off = Location("Z", r"C:\gone\a.pdf", "Primary", None, "P9", True, 10, "obs-Z", (), hash_current=False, in_current_group=False)
    ev, proj, rt = routed([A, B, C, off], [bdec("D1", "redundant_location", LOCATION, "Z", True, [off])], [], [])
    check("a stale fingerprint on a decided copy that left its group: blocked (stale) and orphaned, never guessed",
          rt.locations["Z"].primary == RT.BLOCKED and rt.locations["Z"].of(RT.BLOCKED)[0].kind == RT.STALE_CONTENT_HASH
          and proj.orphaned_decisions == ("D1",))

    # -- conflicts: kinds, severity, order -----------------------------------------------
    prefer = pol("PV3", "prefer_folder_subtree", {"path": r"C:\Docs"})
    avoid = pol("PV4", "avoid_folder_subtree", {"path": r"C:\Docs"})
    ev, proj, rt = routed([A, B, C], [bdec("D1", "redundant_location", LOCATION, "B", True, [B, A, C])], [prefer, avoid], [])
    g1 = proj.group("C1")
    check("a prefer and an avoid at one tier: an advisory exception -- routed to Conflicts, eligibility untouched",
          [(c.kind, c.severity) for c in g1.conflicts] == [(R.SAME_PRECEDENCE_POLICY_TIE, R.ADVISORY)] and g1.ready_for_plan
          and g1.plan_eligible_reclaim_bytes == 1000 and rt.groups["C1"].primary == RT.CONFLICT)
    ev, proj, rt = routed([A, B, C], [bdec("D1", "canonical_location", GROUP, "C1", "A", [A, B, C])], [prefer, avoid], [])
    check("...but not when an explicit canonical makes the recommendation moot", not proj.group("C1").conflicts)
    big = loc("X1", r"C:\x\1.bin", content="C9", phys="P91", size=5000)
    big2 = loc("X2", r"C:\x\2.bin", content="C9", phys="P92", size=5000)
    small = loc("Y1", r"C:\y\1.bin", content="C8", phys="P81", size=10)
    small2 = loc("Y2", r"E:\Backup\1.bin", root="Backup", content="C8", phys="P82", size=10)
    ev, proj, rt = routed([A, B, C, big, big2, small, small2],
                          [bdec("D1", "redundant_location", LOCATION, "C", True, [C, A, B]),
                           bdec("D2", "redundant_location", LOCATION, "Y2", True, [small2, small]),
                           bdec("D3", "redundant_location", LOCATION, "X1", True, [big, big2]), bdec("D4", "redundant_location", LOCATION, "X2", True, [big2, big])],
                          [protect], [])
    order_ = [g.group_id for g in RT.ordered(proj, rt, RT.CONFLICT)]
    check("the Conflicts route orders by reclaim at stake, then the oldest unresolved", order_ == ["C9", "C1", "C8"], str(order_))

    # -- determinism --------------------------------------------------------------------------
    import random
    ev, proj, rt = routed([A, B, C, unknown, D], [drifted, redundant_c], [protect, prefer], [deferral, ev_row("E2", "skipped", GROUP, "C1")])
    canon = lambda r: json.dumps([x.as_dict() for x in r.groups.values()] + [x.as_dict() for x in r.locations.values()] + [r.counts], sort_keys=True)  # noqa: E731
    base = canon(rt)
    rnd = random.Random(3)
    shuffled = Evidence(dict(rnd.sample(list(ev.locations.items()), len(ev.locations))), dict(ev.groups),
                        rnd.sample(ev.decisions, len(ev.decisions)), rnd.sample(ev.policies, len(ev.policies)), list(ev.events), dict(ev.roots), ev.now)
    check("the same model in another row order routes identically", canon(RT.route_all(shuffled, R.resolve_all(shuffled), NOW)) == base)


# ---------------------------------------------------------------------------
# 7. Build 3 on the real project: the detector, deferrals, a rescan
# ---------------------------------------------------------------------------

def test_routing_project(tmp: Path):
    section("7. Routing on a real project: the detector, deferrals, Skip, a rescan that moves the evidence")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S

    app_root = tmp / "RouteRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "RouteCorpus"
    root1, root2 = build_corpus(corpus)
    before = fingerprint_tree(corpus)
    RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(root1), str(root2)], project_name="P3Route")).run()
    project_dir = app_root / "Projects" / "P3Route"
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:route")
    ev = E.load_evidence(conn)
    by_path = {l.path: l for l in ev.locations.values()}
    L = lambda *parts: by_path[str(Path(*parts))]                   # noqa: E731
    doc, down, back = L(root1, "Documents", "report.pdf"), L(root1, "Downloads", "report.pdf"), L(root2, "Backup", "report.pdf")
    link, tax = L(root1, "Documents", "report-link.pdf"), L(root1, "Documents", "tax.docx")
    rep, taxg = doc.content_id, tax.content_id
    check("root coverage is loaded for both roots, complete and available",
          len(ev.roots) == 2 and all(rc.complete and rc.available for rc in ev.roots.values()))
    check("a fresh project: nothing for the detector to write", RT.reconcile(conn, st) == {"written": 0})

    d1 = st.record_decision("canonical_location", GROUP, rep, doc.location_id)
    d2 = st.record_decision("redundant_location", LOCATION, down.location_id)
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("a decided group is Ready for plan; the untouched one is on the queue",
          rt.groups[rep].primary == RT.READY_FOR_PLAN and rt.groups[taxg].primary == RT.UNRESOLVED)
    check("the detector has nothing to write for decisions on current evidence, twice",
          RT.reconcile(conn, st) == {"written": 0} and RT.reconcile(conn, st) == {"written": 0})

    # -- Skip versus Defer -------------------------------------------------------------------
    n0 = st.counts()["review_events"]
    st.skip(GROUP, taxg)
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("Skip: one audit row; the group is exactly where it was", st.counts()["review_events"] == n0 + 1 and rt.groups[taxg].primary == RT.UNRESOLVED)
    e1 = st.defer(GROUP, taxg, "snooze_until_date", until="2099-01-01", note="later")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("Defer until a date: parked, with the trigger recorded", rt.groups[taxg].primary == RT.DEFERRED and e1["return_kind"] == "time"
          and conn.execute("SELECT return_on_utc FROM p3_review_event WHERE review_event_id=?", (e1["review_event_id"],)).fetchone()[0] == "2099-01-01T00:00:00Z")
    e2 = st.defer(GROUP, taxg, "snooze_until_evidence_change")
    row = conn.execute("SELECT detail_json FROM p3_review_event WHERE review_event_id=?", (e2["review_event_id"],)).fetchone()
    detail = json.loads(row[0])
    check("Defer until the evidence changes records the group's evidence, ids only",
          detail["disposition"] == "snooze_until_evidence_change" and len(detail["members"]) == 3 and all(m["observation_id"] for m in detail["members"]))
    def refused(fn):
        try:
            fn()
            return False
        except ValueError:
            return True
    check("a date snooze needs a date", refused(lambda: st.defer(GROUP, taxg, "snooze_until_date")))
    check("an unknown disposition is refused", refused(lambda: st.defer(GROUP, taxg, "snooze_until_preview_available")))
    r = st.restore(GROUP, taxg, refers_to=e2["review_event_id"], reason="by hand")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("Restore by hand returns it to the queue; the deferral rows stay", rt.groups[taxg].primary == RT.UNRESOLVED
          and conn.execute("SELECT COUNT(*) FROM p3_review_event WHERE event_kind='deferred'").fetchone()[0] == 2)
    e3 = st.defer(GROUP, taxg, "snooze_until_date", until="2026-01-01")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("a snooze that has already elapsed is on the queue, live", rt.groups[taxg].primary == RT.UNRESOLVED and rt.groups[taxg].deferral_fired)
    written = RT.reconcile(conn, st)
    check("...and the detector records the restore (system operation, never a decision)",
          written == {"restored": 1, "written": 1}
          and conn.execute("SELECT actor_kind, agent_kind FROM p3_operation ORDER BY operation_id DESC LIMIT 1").fetchone()[:] == ("system_evidence", "routing_detector")
          and RT.reconcile(conn, st) == {"written": 0})
    conn.close()

    # -- a rescan that moves the evidence under a decision ------------------------------------
    time.sleep(1.1)
    (root1 / "Downloads" / "report.pdf").write_bytes(b"alpha" * 20000 + b"!")   # the redundant-marked copy changes
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Scan again")).run()
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:route")
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj)
    check("the changed copy left the group; the group has three members now",
          tuple(sorted(ev.groups[rep].member_ids)) == tuple(sorted((doc.location_id, link.location_id, back.location_id))))
    check("the location it was is still loaded, off-group, so its decision can be judged",
          down.location_id in ev.locations and not ev.locations[down.location_id].in_current_group)
    check("the group routes to Needs Revalidation: the canonical decision's bound membership changed",
          rt.groups[rep].primary == RT.NEEDS_REVALIDATION and any("member left" in c for x in rt.groups[rep].drift for c in x.changes))
    check("the redundant mark on the changed copy drifted too, and is orphaned -- the decision rows are untouched",
          not rt.locations[down.location_id].drift[0].applicable and proj.orphaned_decisions == (str(d2["decision_id"]),)
          and conn.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0] == 2
          and conn.execute("SELECT COUNT(*) FROM p3_decision_withdrawal").fetchone()[0] == 0)
    written = RT.reconcile(conn, st)
    check("the detector writes one needs_revalidation flag per drifted target and nothing else",
          written == {"needs_revalidation": 2, "written": 2} and RT.reconcile(conn, st) == {"written": 0}, str(written))
    flag = conn.execute("SELECT return_kind, return_condition, detail_json FROM p3_review_event WHERE event_kind='needs_revalidation' AND target_kind=? AND target_ref=?",
                        (GROUP, rep)).fetchone()
    check("the flag says what changed, F10's shape", flag["return_kind"] == "evidence_change" and "member left" in flag["return_condition"]
          and json.loads(flag["detail_json"])["decision_ids"] == [str(d1["decision_id"])])
    # confirming: a new canonical decision on current evidence supersedes the old; the flag clears by comparison
    d3 = st.record_decision("canonical_location", GROUP, rep, doc.location_id, rationale="reconfirmed")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    check("re-recording the canonical on current evidence: the group leaves Needs Revalidation by itself",
          d3["supersedes_decision_id"] == d1["decision_id"] and not rt.groups[rep].has(RT.NEEDS_REVALIDATION))
    written = RT.reconcile(conn, st)
    check("...and the detector restores the flag (a row referring to it), nothing marked by hand",
          written == {"restored": 1, "written": 1}
          and conn.execute("SELECT refers_to_review_event_id FROM p3_review_event ORDER BY review_event_id DESC LIMIT 1").fetchone()[0] is not None)
    st.withdraw(d2["decision_id"], "no longer a duplicate")
    check("withdrawing the orphaned mark: the detector restores its flag too", RT.reconcile(conn, st) == {"restored": 1, "written": 1})
    check("the re-examined copy is not 'stale': its current observation has its own verdict (unique by size)",
          E.load_evidence(conn).locations[down.location_id].hash_current)

    # -- a decided copy changes and only the walk runs: stale, and blocked ----------------
    tax2 = L(root2, "Backup", "tax.docx")
    d4 = st.record_decision("redundant_location", LOCATION, tax2.location_id)
    conn.close()
    time.sleep(1.1)
    (root2 / "Backup" / "tax.docx").write_bytes(b"beta" * 30000 + b"?")
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Scan again")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:route")
    ev = E.load_evidence(conn)
    rt = RT.route_all(ev, R.resolve_all(ev))
    lr = rt.locations[tax2.location_id]
    check("a decided copy that changed and was not fingerprinted again: stale, so Blocked -- and drifted, which outranks it",
          not ev.locations[tax2.location_id].hash_current and lr.has(RT.BLOCKED)
          and lr.of(RT.BLOCKED)[0].kind == RT.STALE_CONTENT_HASH and lr.primary == RT.NEEDS_REVALIDATION)
    check("...the copy left its group (its identity is stale), so the group itself stays on the queue and the mark is orphaned",
          tax2.location_id not in ev.groups[taxg].member_ids and rt.groups[taxg].primary == RT.UNRESOLVED
          and str(d4["decision_id"]) in R.resolve_all(ev).orphaned_decisions)
    written = RT.reconcile(conn, st)
    check("the detector records the flag and the blocker (a decided target), returning on fingerprints being current",
          written == {"needs_revalidation": 1, "blocked": 1, "written": 2}
          and conn.execute("SELECT return_kind FROM p3_review_event WHERE event_kind='blocked' ORDER BY review_event_id DESC LIMIT 1").fetchone()[0] == "hash_current")
    conn.close()
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:route")
    written = RT.reconcile(conn, st)
    ev = E.load_evidence(conn)
    check("fingerprinting again clears the blocker: the detector restores it (the flag stays: the decision still drifted)",
          written == {"restored": 1, "written": 1} and not RT.route_all(ev, R.resolve_all(ev)).locations[tax2.location_id].has(RT.BLOCKED))
    st.withdraw(d4["decision_id"], "gone")
    with WriteGuard([project_dir, app_root / "Logs"]) as guard:
        st.defer(GROUP, taxg, "snooze_until_hash_current")
        st.skip(GROUP, taxg)
        st.restore(GROUP, taxg)
        RT.reconcile(conn, st)
        RT.route_all(E.load_evidence(conn), R.resolve_all(E.load_evidence(conn)))
    conn.close()
    check("no Python-level write reached a path outside the project folder while routing ran", not guard.violations, str(guard.violations[:5]))
    after = fingerprint_tree(corpus)
    changed = [p for p, v in before["files"].items() if after["files"].get(p) != v]
    check("only the two files the test rewrote differ on disk; everything else is byte-identical",
          sorted(changed) == sorted([str(root1 / "Downloads" / "report.pdf"), str(root2 / "Backup" / "tax.docx")])
          and after["everything"] == before["everything"], str(changed))


# ---------------------------------------------------------------------------
# 5. The window, headless
# ---------------------------------------------------------------------------

def _texts(widget):
    out = []
    for c in widget.winfo_children():
        try:
            out.append(str(c.cget("text")))
        except Exception:                                           # noqa: BLE001
            pass
        out.extend(_texts(c))
    return out


def test_gui(tmp: Path, before_corpus_fp, corpus):
    section("5. The window: the Decide line, the Decide page, every action, the dialogs")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from tkinter import messagebox
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2 import gui
    from Phase3 import review as RV

    app_root = tmp / "GuiRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus2 = tmp / "GuiCorpus"
    root1, root2 = build_corpus(corpus2)
    fp_before = fingerprint_tree(corpus2)
    RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(root1), str(root2)], project_name="P3Gui")).run()
    project_dir = app_root / "Projects" / "P3Gui"
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()

    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else k.get("title"))), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else k.get("title"), a[1] if len(a) > 1 else k.get("message"))))
    try:
        # The window's own fault log lives under the install's Logs folder (Axiom 19).
        with WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            check("the side panel has a Decide button", any(str(b.cget("text")) == "Decide" for b in app.nav_buttons))
            app.open_project(project_dir)
            app.update_idletasks()
            t = _texts(app.content)
            check("the project summary has a Decide heading with Review duplicates and Policies buttons",
                  "Decide" in t and "Review duplicates" in t and "Policies..." in t)
            check("the Decide line says 0 of 2 groups reviewed", any(x.startswith("0 of 2 groups reviewed") for x in t), str([x for x in t if "groups" in x]))
            check("with no policy the line says so", any(x.startswith("none -- protect") for x in t))

            # policies: add a protection through the dialog's own code path
            page_store = RV.store_for(app)
            dlg = RV.AddPolicyDialog(app, app, page_store, lambda: None)
            dlg.kind_var.set(RV.POLICY_KINDS["protect_source_root"].label)
            dlg._kind_changed()
            dlg.root_var.set(str(root2))
            dlg.reason.insert("1.0", "backup")
            dlg._confirm()
            app.update_idletasks()
            check("Add policy dialog records a protection of the second root", len(page_store.policies()) == 1
                  and page_store.policies()[0]["kind"] == "protect_source_root")

            app.show_decide()
            app.update_idletasks()
            page = app.review_page
            check("the Decide page lists both groups", len(page.table.get_children()) == 2)
            cid = [g.group_id for g in page.proj.groups if g.location_count == 4][0]
            page.table.selection_set(cid)
            page.show_group(cid)
            p = page.proj.group(cid)
            check("the selected group shows 4 members and its facts", len(page.members.get_children()) == 4
                  and "3 physical copies, 1 hard-link alias" in page.group_facts.cget("text"))
            by = {RV._folder(v.path).rsplit("\\", 1)[-1] + "/" + RV._file_name(v.path): v.location_id for v in p.members}
            doc, link, down, back = by["Documents/report.pdf"], by["Documents/report-link.pdf"], by["Downloads/report.pdf"], by["Backup/report.pdf"]

            def select(lid):
                page.members.selection_set(lid)
                page._member_selected()
            select(back)
            check("a protected member enables Override protection and Mark redundant is refused with an explanation",
                  str(page.buttons["override"].cget("state")) == "normal")
            page.mark_redundant()
            check("...refused", asked and asked[-1][0] == "showinfo" and "protected" in asked[-1][2])
            check("nothing was recorded by the refusal", page.store.counts()["decisions"] == 0)
            page.override_protection()
            app.update_idletasks()
            dlgs = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
            od = page.override_dialog
            check("the Override dialog opened, its button disabled until the acknowledgement and a reason are given",
                  len(dlgs) == 1 and str(od.ok.cget("state")) == "disabled")
            od.ack.set(True)
            od._update()
            check("...still disabled with the box ticked but no reason", str(od.ok.cget("state")) == "disabled")
            od.reason.insert("1.0", "retiring the backup drive")
            od._update()
            check("...enabled once both are given", str(od.ok.cget("state")) == "normal")
            od._confirm()
            app.update_idletasks()
            p = page.proj.group(cid)
            check("the override is recorded and the copy is no longer protected", p.verdict(back).status == R.UNDECIDED and p.verdict(back).overridden)
            select(back)
            page.mark_redundant()
            select(down)
            page.mark_redundant()
            p = page.proj.group(cid)
            check("two copies marked redundant: 195 KB plan-eligible", p.plan_eligible_reclaim_bytes == 200000 and p.review_state == R.IN_PROGRESS)
            select(doc)
            page.set_canonical()
            p = page.proj.group(cid)
            check("Set canonical", p.canonical == doc and p.verdict(doc).status == R.KEEPER)
            select(doc)
            page.mark_redundant()
            check("Mark redundant on the canonical is refused", asked[-1][0] == "showinfo" and "canonical" in asked[-1][2] and page.proj.group(cid).canonical == doc)
            select(link)
            page.keep()
            check("Keep", page.proj.group(cid).verdict(link).status == R.KEEPER)
            select(link)
            page.mark_redundant()
            p = page.proj.group(cid)
            check("Mark redundant after Keep supersedes it", p.verdict(link).status == R.REDUNDANT)
            page.keep_all()
            p = page.proj.group(cid)
            check("Keep All asked about the marks, withdrew them, and every copy is a keeper (resolved)",
                  asked[-1][0] == "yesno" and p.review_state == R.RESOLVED and len(p.keeper_set) == 4)
            page.defer()
            dd = page.defer_dialog
            dd.choice.set("snooze_until_date")
            dd._update()
            dd.date_var.set("2099-01-01")
            dd._confirm()
            r = page.routing.groups[cid]
            check("Defer... records a deferral with a time trigger; the group is parked and the button says Restore",
                  r.primary == RT.DEFERRED and r.deferral is not None and r.deferral.return_kind == "time"
                  and str(page.buttons["defer"].cget("text")) == "Restore")
            before_skip = page.store.counts()["review_events"]
            page.skip()
            check("Skip writes one audit row and changes nothing about the group's route",
                  page.store.counts()["review_events"] == before_skip + 1 and page.routing.groups[cid].primary == RT.DEFERRED)
            page.show_group(cid)                     # parked groups are off the Queue list; the detail pane still shows them
            page.defer()
            check("Restore ends the deferral: the group is back on its ordinary route", not page.routing.groups[cid].has(RT.DEFERRED))
            page.show_group(cid)
            rows = [iid for iid, d in page.history_rows.items() if d["active"] and d["decision_kind"] == "keep_all_group"]
            page.history.selection_set(rows[0])
            page._history_selected()
            check("Undo enabled for an active decision", str(page.undo_button.cget("state")) == "normal")
            page.undo_selected()
            check("Undo withdrew Keep All", page.proj.group(cid).review_state == R.IN_PROGRESS)
            inactive = [iid for iid, d in page.history_rows.items() if not d["active"]]
            page.history.selection_set(inactive[0])
            page._history_selected()
            check("Undo disabled for a withdrawn or superseded decision", str(page.undo_button.cget("state")) == "disabled")
            page.filter_var.set("Queue")
            page.refresh_list()
            check("the Queue route shows both groups again (one in progress, one unreviewed)", len(page.table.get_children()) == 2
                  and {page.routing.groups[i].word for i in page.table.get_children()} == {"In progress", "Unreviewed"})
            page.filter_var.set("Lens: Hard-Link Aliases")
            page.refresh_list()
            check("a lens: Hard-Link Aliases shows the group with the alias only",
                  [page.rows_by_iid[i].hardlink_aliases for i in page.table.get_children()] == [1])
            page.filter_var.set("Lens: Cross-Root")
            page.refresh_list()
            check("a lens: Cross-Root shows both (each spans two roots)", len(page.table.get_children()) == 2)
            page.filter_var.set("Ready for Plan")
            page.refresh_list()
            check("an empty route says so without claiming completion", "Nothing on this route" in page.count_var.get())
            page.filter_var.set("All")
            page.sort_by("copies")
            check("sort by copies ascending", [page.rows_by_iid[i].location_count for i in page.table.get_children()] == [3, 4])
            page.sort_by("copies")
            check("...then descending", [page.rows_by_iid[i].location_count for i in page.table.get_children()] == [4, 3])
            page.open_policies()
            app.update_idletasks()
            pd = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
            check("the Policies dialog opens", len(pd) == 1)
            for w in pd:
                w.destroy()
            app.show_files()
            app.update_idletasks()
            row = [r for r in app.current_rows if r.get("path.file_name") == "report.pdf"][0]
            app.show_file_detail(row)
            check("a duplicate's Details pane offers 'Review this duplicate group'", "Review this duplicate group" in _texts(app.detail_host))
            app.show_decide(file_path_id=row["path.id"])
            check("...which lands on that group", app.review_page.selected_group == cid)
            app.show_hub()
            t = _texts(app.content)
            check("back on the summary, the Decide line counts the reviewed group and its route",
                  any(x.startswith("1 of 2 groups reviewed") and "in progress" not in x for x in t), str([x for x in t if "groups" in x]))
            app.show_decide()
            page = app.review_page
            check("Confirm decisions is disabled while nothing has drifted", str(page.buttons["confirm"].cget("state")) == "disabled")
            # -- the queue emptied: Currently clear, never Complete ----------------------
            page.show_group(cid)
            page.keep_all()
            other = [g.group_id for g in page.proj.groups if g.group_id != cid][0]
            page.show_group(other)
            page.keep_all()
            page.filter_var.set("Queue")
            page.refresh_list()
            check("with every group decided the queue says 'Currently clear' and never 'complete'",
                  page.count_var.get().startswith("Currently clear") and "omplete" not in page.count_var.get(), page.count_var.get())
            app.release_connection()
            app.destroy()
        check("no Python-level write reached a path outside the project folder during the window session", not guard.violations, str(guard.violations[:5]))
        after = fingerprint_tree(corpus2)
        check("the corpus behind the window session is byte-for-byte unchanged",
              all(after["files"].get(p) == v for p, v in fp_before["files"].items()) and after["everything"] == fp_before["everything"])

        # -- a rescan moves the evidence: the window's own detector, then Confirm decisions --
        from Phase2.runner import RunOutcome, COMPLETED
        import time as _time
        _time.sleep(1.1)
        (root1 / "Downloads" / "report.pdf").write_bytes(b"alpha" * 20000 + b"!")
        RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Scan again")).run()
        RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()
        app = gui.Phase2App(None)
        app.withdraw()
        app.open_project(project_dir)
        flags_before = app.conn.execute("SELECT COUNT(*) FROM p3_review_event WHERE event_kind='needs_revalidation'").fetchone()[0]
        # what the window does when a run finishes: reopen, run the detector, announce, land on the summary
        app._run_finished(RunRequest(PRESCAN, "Scan again"), RunOutcome(COMPLETED, "Scan again complete."))
        flags_after = app.conn.execute("SELECT COUNT(*) FROM p3_review_event WHERE event_kind='needs_revalidation'").fetchone()[0]
        check("after a run finishes, the window's detector has recorded the drift (a needs_revalidation row)", flags_after > flags_before,
              f"{flags_before} -> {flags_after}")
        t = _texts(app.content)
        check("the summary's Decide line says how many need revalidation", any("need revalidation" in x for x in t), str([x for x in t if "groups" in x]))
        app.show_decide()
        page = app.review_page
        page.filter_var.set("Needs Revalidation")
        page.refresh_list()
        check("the Needs Revalidation route lists the group whose Keep all rests on a membership that changed",
              cid in page.table.get_children() and page.routing.groups[cid].primary == RT.NEEDS_REVALIDATION)
        page.show_group(cid)
        check("the route line explains what changed, in file terms",
              "member left" in page.route_label.cget("text") or "observed again" in page.route_label.cget("text"), page.route_label.cget("text"))
        check("Confirm decisions is enabled", str(page.buttons["confirm"].cget("state")) == "normal")
        page.confirm_decisions()
        keep_alls = app.conn.execute("SELECT decision_id, supersedes_decision_id FROM p3_decision WHERE decision_kind='keep_all_group' AND target_ref=? ORDER BY decision_id", (cid,)).fetchall()
        check("Confirm re-records Keep all on current evidence: the group leaves Needs Revalidation and the old row is superseded, not deleted",
              asked[-1][0] == "yesno" and not page.routing.groups[cid].has(RT.NEEDS_REVALIDATION)
              and len(keep_alls) >= 2 and keep_alls[-1]["supersedes_decision_id"] == keep_alls[-2]["decision_id"],
              f"asked={asked[-1][:2] if asked else None} route={page.routing.groups[cid].as_dict()} keep_alls={[tuple(r) for r in keep_alls]}")
        check("...and the detector restored the flag", app.conn.execute(
            "SELECT COUNT(*) FROM p3_review_event WHERE event_kind='restored' AND refers_to_review_event_id IS NOT NULL").fetchone()[0] >= 1)
        app.release_connection()
        app.destroy()
    finally:
        for n, f in real.items():
            setattr(messagebox, n, f)


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3reg_"))
    try:
        test_registry()
        test_resolver()
        project_dir, corpus, before, app_root = test_project(tmp)
        test_no_mutation(project_dir, corpus, before, app_root)
        test_routing_model()
        test_routing_project(tmp)
        test_gui(tmp, before, corpus)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 REGRESSION: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
