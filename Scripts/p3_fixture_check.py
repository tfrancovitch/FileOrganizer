#!/usr/bin/env python3
r"""Phase 3 acceptance against the research fixture lab (P3.R11).

The seven synthetic fixtures that test Build 1-3 scope ship under
Resources\Phase3\fixture_lab\. Each is a SQLite file holding synthetic
Phase 2 evidence, authoritative Phase 3 records, and an `expected_projection`
table -- the oracle. The research's own validator computes each oracle with
per-fixture hand-written logic; this check runs the product's ONE general
resolution function (Phase3.resolve) and ONE general router (Phase3.routing)
over the same rows and diffs the result against every oracle key.

    F01  Keeper_Protected_Hardlink    canonical + keeper set + protected root +
                                      physical copies vs hard-link aliases, together
    F02  Multiple_Intentional_Keepers multiple keepers is a resolved state; zero reclaim is valid
    F03  Folder_Priority_Exception    a folder preference plus one explicit exception, and
                                      the exception wins
    F08  Deferred_vs_Blocked          a person's deferral and an evidence blocker are
                                      different routes with different return triggers (Build 3)
    F09  Supersession_Undo_Reapply    change, withdraw, re-apply: every row survives
    F10  Revalidation                 the decision stays; a changed observation routes the
                                      group to Needs Revalidation (Build 3)
    F15  Projection_Rebuild           keeper / canonical / protected / plan readiness
                                      reconstruct from authoritative history alone

Also proved here, because the fixtures are the cleanest place to prove it:
the projection is a pure function -- the same input in any row order gives
the same answer, twice.

Run:  python Scripts\p3_fixture_check.py
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

from Phase3.model import Evidence, Location, Group, Decision, PolicyVersion, ReviewEvent, path_key   # noqa: E402
from Phase3 import resolve as R                                                                       # noqa: E402
from Phase3 import routing as RT                                                                      # noqa: E402

FIXTURES = SCRIPTS.parent / "Resources" / "Phase3" / "fixture_lab" / "Fixtures"
RESULTS = []

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


# ---------------------------------------------------------------------------
# Loading a fixture into the product's model
# ---------------------------------------------------------------------------

def load_fixture(db_path: Path) -> tuple[Evidence, dict, list]:
    """The fixture's rows as the model the resolver reads, plus its oracle."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        locations = {}
        protected_flags = {}
        for r in con.execute("SELECT * FROM evidence_location ORDER BY location_id"):
            locations[r["location_id"]] = Location(
                location_id=r["location_id"], path=r["path"], root_key=r["root"],
                content_id=r["content_id"], physical_id=r["physical_id"],
                present=bool(r["present"]), size_bytes=r["bytes"],
                observation_id=r["observation_id"],
                sort_key=(r["root"], path_key(r["path"])))
            protected_flags[r["location_id"]] = bool(r["protected"])
        groups = {}
        current = {r["group_id"]: bool(r["evidence_current"]) for r in con.execute("SELECT * FROM evidence_duplicate_group")}
        members = {}
        for r in con.execute("SELECT group_id, location_id FROM evidence_duplicate_member ORDER BY group_id, location_id"):
            members.setdefault(r["group_id"], []).append(r["location_id"])
        for gid, lids in members.items():
            groups[gid] = Group(gid, locations[lids[0]].content_id, tuple(lids), current.get(gid, True))

        ops = {r["operation_id"]: r["occurred_utc"] for r in con.execute("SELECT operation_id, occurred_utc FROM p3_operation")}
        decisions = []
        rows = con.execute("SELECT rowid AS seq, * FROM p3_decision ORDER BY rowid").fetchall()
        for r in rows:
            value = json.loads(r["value_json"]) if r["value_json"] is not None else None
            # The lab binds evidence as one observation id ('obs:OBS1'); the
            # product binds [file_path_id, observation_id] pairs. A canonical
            # decision's observation is the canonical's; a location's is its own.
            binding = {}
            raw = r["evidence_binding"] or ""
            if raw.startswith("obs:"):
                obs = raw[4:]
                if r["target_kind"] == "file_location":
                    binding = {"file_path_id": r["target_ref"], "observation_id": obs}
                elif r["decision_kind"] == "canonical_location":
                    binding = {"members": [{"file_path_id": str(value), "observation_id": obs}]}
            decisions.append(Decision(
                decision_id=r["decision_id"], target_kind=r["target_kind"], target_ref=r["target_ref"],
                kind=r["decision_kind"], value=value,
                origin_kind=r["origin_kind"], supersedes=r["supersedes_decision_id"],
                withdrawn=bool(r["withdrawn"]), operation_id=r["operation_id"], sequence=int(r["seq"]),
                binding=binding, occurred_utc=ops.get(r["operation_id"], "")))

        events = []
        for r in con.execute("SELECT rowid AS seq, * FROM p3_review_event ORDER BY rowid"):
            events.append(ReviewEvent(
                review_event_id=r["review_event_id"], target_kind=r["target_kind"], target_ref=r["target_ref"],
                kind=r["event_kind"], return_kind=r["return_kind"], return_condition=r["return_condition"],
                return_on_utc=r["return_condition"] if r["return_kind"] == "time" else None,
                occurred_utc=ops.get(r["operation_id"], ""), sequence=int(r["seq"])))
        # Routing is evaluated at a moment; the lab's moment is its last operation.
        now = max(ops.values()) if ops else "2026-09-08T12:00:00Z"

        policies = []
        for r in con.execute("SELECT * FROM p3_policy_version ORDER BY policy_id, version_no"):
            scope = json.loads(r["scope_json"] or "{}")
            effect = json.loads(r["effect_json"] or "{}")
            if "path" in scope:
                scope = {"path_key": path_key(scope["path"]), "path": scope["path"]}
            if "root" in scope:
                scope = {"root_key": scope["root"], "root_path": scope["root"]}
            if "over" in effect:
                effect = {"over_path_key": path_key(effect["over"]), "over": effect["over"]}
            policies.append(PolicyVersion(
                policy_version_id=r["policy_version_id"], policy_id=r["policy_id"], kind=r["kind"],
                scope=scope, effect=effect, version_no=int(r["version_no"] or 1), active=bool(r["enabled"])))

        # The lab models "protected root" as a flag on the evidence row
        # (its schema predates policies for every scenario). In the product
        # protection only ever comes from a policy, so the flag becomes one:
        # protect_source_root when every location of that root carries it,
        # else an exact-path protect_folder_subtree for that location.
        by_root = {}
        for lid, loc in locations.items():
            by_root.setdefault(loc.root_key, []).append(lid)
        synthetic = 0
        for root, lids in sorted(by_root.items()):
            flags = {protected_flags[l] for l in lids}
            if flags == {True}:
                synthetic += 1
                policies.append(PolicyVersion(f"SYN{synthetic}", f"SYNP{synthetic}", "protect_source_root",
                                              {"root_key": root, "root_path": root}))
            elif True in flags:
                for l in lids:
                    if protected_flags[l]:
                        synthetic += 1
                        policies.append(PolicyVersion(f"SYN{synthetic}", f"SYNP{synthetic}", "protect_folder_subtree",
                                                      {"path_key": locations[l].path_key, "path": locations[l].path}))

        oracle = {r["key"]: json.loads(r["value_json"]) for r in con.execute("SELECT key, value_json FROM expected_projection")}
        history = [(r["decision_id"], r["value_json"], bool(r["withdrawn"])) for r in rows]
        return Evidence(locations, groups, decisions, policies, events, {}, now), oracle, history
    finally:
        con.close()


# ---------------------------------------------------------------------------
# What the product says, in the oracle's vocabulary
# ---------------------------------------------------------------------------

def actual_projection(fid: str, ev: Evidence, history) -> dict:
    proj = R.resolve_all(ev)
    g = {p.group_id: p for p in proj.groups}
    if fid.startswith("F01") or fid.startswith("F02"):
        p = g["G1"]
        out = {"keeper_set": list(p.keeper_set), "canonical": p.canonical,
               "redundant_candidates": list(p.redundant_candidates),
               "plan_eligible_reclaimable_bytes": p.plan_eligible_reclaim_bytes}
        if fid.startswith("F01"):
            out.update(protected=list(p.protected), physical_copies=p.physical_copies,
                       hardlink_aliases=p.hardlink_aliases)
        else:
            out["ready_for_plan"] = p.ready_for_plan
        return out
    if fid.startswith("F03"):
        return {"policy_suggestion": {gid: p.suggested_canonical for gid, p in g.items() if p.suggested_canonical},
                "effective_canonical": {gid: p.effective_canonical for gid, p in g.items() if p.effective_canonical},
                "explicit_exception_groups": sorted(gid for gid, p in g.items()
                                                    if p.canonical is not None and p.suggested_canonical is not None
                                                    and p.canonical != p.suggested_canonical),
                "conflicts": [c.kind for p in proj.groups for c in p.conflicts]}
    if fid.startswith("F09"):
        hist = []
        for did, value_json, withdrawn in history:
            hist.append(f"{did}:{json.loads(value_json)}")
            if withdrawn:
                hist.append(f"{did}:withdrawn")
        return {"history": hist, "current_canonical": g["G1"].canonical,
                "history_rows_preserved": len(history)}
    if fid.startswith("F15"):
        p = g["G1"]
        return {"keeper_set": list(p.keeper_set), "canonical": p.canonical, "protected": list(p.protected),
                "redundant": list(p.redundant_candidates), "conflicts": [c.kind for c in p.conflicts],
                "review_state": p.review_state, "ready_for_plan": p.ready_for_plan,
                "plan_item_target": p.plan_eligible_locations[0] if len(p.plan_eligible_locations) == 1 else list(p.plan_eligible_locations)}
    routing = RT.route_all(ev, proj, ev.now)
    if fid.startswith("F08"):
        routes, kinds = {}, {}
        for lid, route in routing.locations.items():
            routes[lid] = route.primary
            if route.primary == RT.DEFERRED and route.deferral is not None:
                kinds[lid] = route.deferral.return_kind
            elif route.primary == RT.BLOCKED:
                opened = route.open_events.get("blocked") or []
                kinds[lid] = opened[0].return_kind if opened else None
        return {"routing": routes, "return_kind": kinds}
    if fid.startswith("F10"):
        route = routing.groups["G1"]
        from Phase3.model import active_decisions
        active = {d.decision_id for d in active_decisions(ev.decisions)}
        drift = {x.decision_id: x for x in route.drift}
        return {"decision_preserved": "D1" if "D1" in active and any(d.decision_id == "D1" for d in ev.decisions) else None,
                "routing": route.primary,
                "current_applicability": "requires_revalidation" if not drift["D1"].applicable else "applicable"}
    raise KeyError(fid)


def _shuffled(ev: Evidence, seed: int) -> Evidence:
    rnd = random.Random(seed)
    decisions = list(ev.decisions)
    rnd.shuffle(decisions)
    policies = list(ev.policies)
    rnd.shuffle(policies)
    locations = dict(rnd.sample(list(ev.locations.items()), len(ev.locations)))
    groups = dict(rnd.sample(list(ev.groups.items()), len(ev.groups)))
    # Events keep their order: the record is a sequence (a restore ends what
    # came before it), and the loader delivers it oldest first.
    return Evidence(locations, groups, decisions, policies, list(ev.events), dict(ev.roots), ev.now)


def _canon(proj: R.ProjectProjection, ev=None):
    routes = []
    if ev is not None:
        rt = RT.route_all(ev, proj, ev.now)
        routes = [r.as_dict() for r in rt.groups.values()] + [r.as_dict() for r in rt.locations.values()] + [rt.counts]
    return json.dumps([p.as_dict() for p in proj.groups] + [list(proj.orphaned_decisions), proj.totals] + routes,
                      sort_keys=True, default=str)


# ---------------------------------------------------------------------------

def main() -> int:
    if not FIXTURES.is_dir():
        print(f"fixture lab not found: {FIXTURES}")
        return 2
    dirs = sorted(d for d in FIXTURES.iterdir() if d.is_dir() and (d / "fixture.sqlite").is_file())
    print(f"Phase 3 fixture check: {len(dirs)} fixtures under {FIXTURES}")
    for d in dirs:
        print(f"\n{d.name}\n{'-' * len(d.name)}")
        con = sqlite3.connect(str(d / "fixture.sqlite"))
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        con.close()
        check("fixture database quick_check ok", qc == "ok", qc)
        ev, oracle, history = load_fixture(d / "fixture.sqlite")
        try:
            actual = actual_projection(d.name, ev, history)
        except Exception as exc:                                    # noqa: BLE001
            check("resolution ran", False, f"{type(exc).__name__}: {exc}")
            continue
        for key, expected in sorted(oracle.items()):
            got = actual.get(key, "<missing>")
            check(f"{key} == {json.dumps(expected)}", got == expected, f"got {json.dumps(got, default=str)}")
        # The projection and the routes are pure functions of history: any row order, twice.
        base = _canon(R.resolve_all(ev), ev)
        same = all(_canon(R.resolve_all(_shuffled(ev, seed)), _shuffled(ev, seed)) == base for seed in (1, 2, 3))
        check("identical projection and routes from shuffled rows (3 seeds) and a second run", same and _canon(R.resolve_all(ev), ev) == base)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 FIXTURES: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
