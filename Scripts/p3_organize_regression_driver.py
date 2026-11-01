#!/usr/bin/env python3
r"""F39's driver: one build's view of a plan, as canonical JSON -- run once against the archived phase3-b19 tree and
once against this one, in separate processes, so the two are compared as bytes.

  python p3_organize_regression_driver.py <tree> lab    <out.json>
        the 16 lab fixtures (F01-F16): for each, the plan the build makes -- fingerprint, every item with its
        preconditions, the summary, and every group's and pair's route.
  python p3_organize_regression_driver.py <tree> setup  <project_dir> <out.json>
        (B19 or B20 code, on a COPY of a real-scale project) record the same synthetic decisions and one dangling
        protection policy, build a revision and LOCK it; write the ids.
  python p3_organize_regression_driver.py <tree> real   <project_dir> <out.json>
        the plan and routes of that project.
  python p3_organize_regression_driver.py <tree> locked <project_dir> <out.json>
        (B20 code) the revision locked by B19: does it still verify, and is it still current?
  python p3_organize_regression_driver.py <tree> probe  <project_dir> <out.json>
        (B20 code, on a COPY) record a desired location for the first N files of the first root and report
        what the dangling guard and the coverage rule would block today.

<tree> is a directory holding Scripts\ (a clean checkout of a tag, or this worktree). Nothing here is imported by
the product; the suites run it as a subprocess.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

tree = Path(sys.argv[1])
sys.path.insert(0, str(tree / "Scripts"))
sys.path.insert(0, str(tree / "Scripts" / "Database"))
mode, out = sys.argv[2], Path(sys.argv[-1])

from Phase3 import plans as PL, resolve as R, routing as RT, evidence as E   # noqa: E402

NOW = "2026-10-12T00:00:00Z"


def dump(ev):
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, NOW)
    draft = PL.build(ev, proj, rt)
    return {"fingerprint": PL.fingerprint(draft.items), "items": [PL.item_dict(i) for i in draft.items],
            "summary": draft.summary,
            "group_routes": {k: [v.primary, [c.kind for c in v.conditions]] for k, v in sorted(rt.groups.items())},
            "pair_routes": {k: [v.primary, [c.kind for c in v.conditions]] for k, v in sorted((getattr(rt, "pairs", None) or {}).items())}}


def write(obj):
    out.write_text(json.dumps(obj, sort_keys=True, indent=1, default=str), encoding="utf-8")


def project(path):
    from Phase2.core import connect
    from Phase3 import store as S
    conn = connect(Path(path), write=True)
    return conn, S.DecisionStore(conn, actor_id="f39-driver", session_id="f39")


if mode == "lab":
    import p3_fixture_check as FX
    res = {}
    for d in sorted(x for x in FX.FIXTURES.iterdir() if x.is_dir() and (x / "fixture.sqlite").is_file()):
        ev, _oracle, _history = FX.load_fixture(d / "fixture.sqlite")
        ev.now = NOW
        res[d.name] = dump(ev)
    write(res)
elif mode == "setup":
    conn, st = project(sys.argv[3])
    ev = E.load_evidence(conn, pairs=False)
    rows = []
    for gid in sorted(ev.groups, key=lambda g: int(g))[:300]:
        members = list(ev.groups[gid].member_ids)
        if len(members) >= 2:
            rows.append(("canonical_location", "exact_duplicate_group_version", gid, members[0]))
            rows.append(("redundant_location", "file_location", members[1], True))
    st.record_decisions(rows, rationale="F39 synthetic")
    root = conn.execute("SELECT root_path FROM source_root WHERE is_active = 1 ORDER BY root_ordinal LIMIT 1").fetchone()[0]
    st.create_policy("protect_folder_subtree", {"path_key": (root.lower().rstrip("\\") + "\\nosuchfolder"), "path": root + "\\NoSuchFolder"})
    ev = E.load_evidence(conn, pairs=False)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, NOW)
    draft = PL.build(ev, proj, rt)
    rid = st.record_plan_revision(draft, name="F39")["plan_revision_id"]
    # the lock guards compare marks taken at the preview; nothing has moved
    st.lock_plan_revision(rid)
    write({"revision": rid, "decisions": len(rows), "items": len(draft.items),
           "fingerprint": st.plan_revision(rid)[0]["fingerprint"]})
elif mode == "migrate":
    import fo_db
    conn, _row = fo_db.open_project(sys.argv[3])
    conn.close()
    write({"migrated": True})
elif mode == "real":
    conn, st = project(sys.argv[3])
    ev = E.load_evidence(conn, pairs=False)
    ev.now = NOW
    write(dump(ev))
elif mode == "locked":
    conn, st = project(sys.argv[3])
    meta = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
    rid = meta["revision"]
    rev, items = st.plan_revision(rid)
    ev = E.load_evidence(conn, pairs=False)
    ev.now = NOW
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, NOW)
    reasons = PL.obsolescence(items, ev, proj, rt)
    RT.reconcile(conn, st, now=NOW)
    rev_after = st.plan_revision(rid)[0]
    write({"verifies": st.verify_plan_revision(rid), "status_before": rev["status"], "status_after": rev_after["status"],
           "fingerprint_unchanged": rev["fingerprint"] == meta["fingerprint"], "obsolescence_reasons": [r[0] for r in reasons],
           "registry_version_recorded": rev["basis"].get("registry_version"), "registry_digest_recorded": rev["basis"].get("registry_digest")})
elif mode == "probe":
    n = 500
    conn, st = project(sys.argv[3])
    row = conn.execute("SELECT source_root_id FROM source_root WHERE is_active = 1 ORDER BY root_ordinal LIMIT 1").fetchone()
    ids = [str(r[0]) for r in conn.execute("SELECT file_path_id FROM file_state WHERE state = 'present' AND source_root_id = ? "
                                           "ORDER BY file_path_id LIMIT ?", (row[0], n))]
    t0 = time.perf_counter()
    rows = [("desired_location", "file_location", i, {"destination_root_id": int(row[0]), "destination_folder": "B20Probe",
                                                       "on_collision": "fail"}) for i in ids]
    st.record_decisions(rows, rationale="B20 real-scale probe (a copy)")
    t1 = time.perf_counter()
    ev = E.load_evidence(conn, pairs=False)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    draft = PL.build(ev, proj, rt)
    t2 = time.perf_counter()
    items = [i for i in draft.items if i.action_kind == "move_location"]
    states = {}
    for i in items:
        states[i.blocked_reason or i.state] = states.get(i.blocked_reason or i.state, 0) + 1
    write({"files": len(ids), "items": len(items), "states": states, "record_seconds": round(t1 - t0, 2),
           "load_and_plan_seconds": round(t2 - t1, 2),
           "dangling_blocked": sum(1 for i in items if i.blocked_reason == "no_dangling_protection"),
           "coverage_unknown": sum(1 for i in items if i.condition("destination_collision_absent").result == "unknown"),
           "ready": sum(1 for i in items if i.state == "ready"), "reclaim_summary": draft.summary.get("organize")})
else:
    sys.exit(f"unknown mode {mode}")
