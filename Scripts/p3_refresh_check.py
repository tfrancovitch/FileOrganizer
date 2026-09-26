#!/usr/bin/env python3
r"""B14 regression: the Similar Images page's incremental refresh equals a full recomputation.

The incremental refresh (`Phase3/refresh.py`) reads only what the record
gained since the page last read it and re-derives only the pairs that can
have changed. A fast wrong answer is worse than a slow right one, so this
suite checks it the way B13 checked its neighbourhood simulation: against
the slow path that is certainly right.

  1. Random sessions on a real project with real images (three seeds, 240
     steps): relationship and retention answers (each superseding what
     stood), withdrawals, deferrals (including one whose date has already
     passed, so the clock fires it), restores, skips, an explicit Keep on an
     image, a group decision on the exact-duplicate copies, bulk batches and
     their undo, and a policy change. After EVERY step the page's model --
     brought up to date incrementally -- is compared with a model loaded,
     resolved and routed from scratch at the same instant: every decision,
     every event, every pair's projection, every pair's route, the counts.
     The chain guard, blockers and exceptions are exercised along the way.
  2. The store's targeted reads equal the whole-record reads they replace:
     `active_for`, `still_active`, `batch`.
  3. If a display is available, the window: page open reuses the detector's
     model (one load, not two) and equals a fresh model; an answer, an Undo
     and a Survey Mode group action take the incremental path and leave the
     page equal to a fresh model; a policy change falls back to the full
     refresh, and says so.

Run:  python Scripts\p3_refresh_check.py
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_visual_bulk_check as VB                                            # noqa: E402  (the variations corpus)
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import bulk as BK                                                # noqa: E402
from Phase3 import refresh as RF                                             # noqa: E402

RESULTS = []
LOCATION, GROUP, PAIR = REG.LOCATION, REG.GROUP, REG.VISUAL_PAIR


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def fresh_model(conn, now):
    from Phase3 import evidence as E
    ev = E.load_evidence(conn)
    ev.now = now
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, now)


def diff(a, b):
    """The first difference between two fingerprints, for the failure message."""
    for key in a:
        if a[key] != b[key]:
            if isinstance(a[key], dict):
                bad = [k for k in set(a[key]) | set(b[key]) if a[key].get(k) != b[key].get(k)]
                return f"{key}: {len(bad)} differ, e.g. {bad[:1]} {a[key].get(bad[0]) if bad else ''} != {b[key].get(bad[0]) if bad else ''}"
            if isinstance(a[key], list) and len(a[key]) == len(b[key]):
                i = next(i for i in range(len(a[key])) if a[key][i] != b[key][i])
                return f"{key}[{i}]: {str(a[key][i])[:200]} != {str(b[key][i])[:200]}"
            return f"{key}: lengths {len(a[key])} / {len(b[key])}"
    return ""


def _step(rnd, st, conn, ev, proj, pids, ids_by_name, exact_group, log):
    """One random action through the store, as the page (or a batch) would record it."""
    from Phase3 import evidence as E
    pid = rnd.choice(pids)
    pp = proj.pair(pid)
    a, b = pp.a_id, pp.b_id
    roll = rnd.random()
    try:
        if roll < 0.30:
            what = rnd.choice(["prefer", "prefer", "keep_both", "related", "different"])
            if what == "prefer":
                keep = rnd.choice([a, b])
                st.record_decisions([("equivalent_copy", PAIR, pid, True), ("canonical_location", PAIR, pid, keep)])
            elif what == "keep_both":
                st.record_decisions([("equivalent_copy", PAIR, pid, True), ("keep_both", PAIR, pid, [a, b])])
            elif what == "related":
                st.record_decisions([("related_variant", PAIR, pid, True), ("keep_both", PAIR, pid, [a, b])])
            else:
                st.record_decisions([("different", PAIR, pid, True)])
            log["answers"] += 1
        elif roll < 0.42:
            live = [d for d in st.active_for(PAIR, pid)]
            if live:
                st.withdraw_many([int(d.decision_id) for d in live[:rnd.randrange(1, len(live) + 1)]], reason="test undo")
                log["withdrawals"] += 1
        elif roll < 0.52:
            choice = rnd.choice(["defer_indefinitely", "snooze_until_date", "snooze_until_date"])
            until = rnd.choice(["2020-01-01", "2099-01-01"]) if choice == "snooze_until_date" else None
            st.defer(PAIR, pid, choice, until)
            log["deferrals"] += 1
        elif roll < 0.58:
            st.restore(PAIR, pid, reason="test restore")
            log["restores"] += 1
        elif roll < 0.62:
            st.skip(PAIR, pid)
        elif roll < 0.68:
            st.record_decision("must_keep_location", LOCATION, rnd.choice([a, b]))
            log["keeps"] += 1
        elif roll < 0.71 and exact_group:
            st.record_decision("keep_all_group", GROUP, exact_group)
            log["groups"] += 1
        elif roll < 0.83:
            from Phase3 import evidence as E2
            evx = E2.load_evidence(conn)
            px = R.resolve_all(evx)
            rx = RT.route_all(evx, px, evx.now)
            sel = rnd.sample(pids, rnd.randrange(2, 9))
            pv = BK.preview_pairs(evx, px, rx, rnd.choice(["pairs_related_keep_both", "pairs_equivalent_keep_both",
                                                            "pairs_different"]),
                                  REG.SCOPE_EXPLICIT_SELECTION, selection=sel, conn=conn)
            if pv.decision_count:
                res = st.commit_bulk(pv)
                log["batches"].append(res["batch_id"])
        elif roll < 0.92 and log["batches"]:
            bid = log["batches"].pop(rnd.randrange(len(log["batches"])))
            st.undo_batch(bid)
            log["batch_undos"] += 1
        elif roll < 0.93:
            st.create_policy("protect_folder_subtree", {"path_key": E.path_key(ev.locations[a].path.rsplit("\\", 1)[0]),
                                                        "path": ev.locations[a].path.rsplit("\\", 1)[0]}, rationale="test")
            log["policies"] += 1
    except ValueError:
        log["refused"] += 1


def test_sessions(tmp: Path):
    section("1. Random sessions through the store: after EVERY step, the incremental model equals a fresh full one")
    from Phase2.core import connect, utc_now
    from Phase3 import evidence as E, store as S

    app_root, corpus, root, variant, project_dir, outs = VB.open_variation_project(tmp, "P3Refresh")
    check("a real project: Pre-Scan, Find My Duplicates, Analyze (pictures)", all(o.ok for o in outs))
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:refresh")
    ev0 = E.load_evidence(conn)
    pids = sorted(ev0.pairs, key=int)
    exact_group = next(iter(ev0.groups), None)
    ids_by_name = {BK.file_name(l.path): l.location_id for l in ev0.locations.values()}
    total = {"steps": 0, "incremental": 0, "fallback": 0, "equal": 0, "conflict_steps": 0, "fired": 0, "blocked": 0}
    log = {"answers": 0, "withdrawals": 0, "deferrals": 0, "restores": 0, "keeps": 0, "groups": 0, "batches": [],
           "batch_undos": 0, "policies": 0, "refused": 0}
    first_bad = ""
    for seed in (1, 2, 3):
        rnd = random.Random(seed)
        marks = RF.marks(conn)
        now = utc_now()
        ev, proj, rt = fresh_model(conn, now)
        for _ in range(80):
            _step(rnd, st, conn, ev, proj, pids, ids_by_name, exact_group, log)
            now = utc_now()
            res = RF.apply(conn, ev, proj, rt, marks, now=now)
            if res is None:                                # what the page does: everything, from the database
                marks = RF.marks(conn)
                ev, proj, rt = fresh_model(conn, now)
                total["fallback"] += 1
            else:
                marks = res[0]
                total["incremental"] += 1
            ref = fresh_model(conn, now)
            a, b = RF.fingerprint(ev, proj, rt), RF.fingerprint(*ref)
            total["steps"] += 1
            if a == b:
                total["equal"] += 1
            elif not first_bad:
                first_bad = f"seed {seed}: {diff(a, b)}"
            total["conflict_steps"] += rt.pair_counts.get(RT.CONFLICT, 0) > 0
            total["fired"] += any(r.deferral_fired for r in rt.pairs.values())
            total["blocked"] += any(r.primary == RT.BLOCKED for r in rt.pairs.values())
    check(f"{total['steps']} steps over three seeds: the page's model equals a fresh full load, resolve and route after "
          f"every one ({total['equal']} equal)", total["equal"] == total["steps"], first_bad)
    check(f"...{total['incremental']} steps took the incremental path, {total['fallback']} fell back to a full reload "
          "(only a policy change is outside its reach)", total["incremental"] >= 200 and total["fallback"] == log["policies"],
          f"fallbacks {total['fallback']} vs policies {log['policies']}")
    check("...and the sessions reached every kind of change: answers, withdrawals, deferrals, restores, explicit Keeps, "
          "a group decision, batches and batch undo", log["answers"] > 40 and log["withdrawals"] > 5 and log["deferrals"] > 5
          and log["restores"] > 3 and log["keeps"] > 3 and log["groups"] >= 1 and log["batch_undos"] >= 1, str(log))
    check(f"...with conflicts standing on {total['conflict_steps']} steps (the chain guard and explicit Keeps), a dated "
          f"deferral firing by the clock on {total['fired']}", total["conflict_steps"] > 20 and total["fired"] > 5, str(total))
    conn.close()
    return project_dir, app_root, corpus


def test_store_reads(project_dir: Path):
    section("2. The store's targeted reads equal the whole-record reads they replace")
    from Phase2.core import connect
    from Phase3 import store as S
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:reads")
    everything = st.active()
    targets = {(d.target_kind, d.target_ref) for d in everything} | {(PAIR, "999999")}
    same = all(sorted(d.decision_id for d in st.active_for(k, r)) ==
               sorted(d.decision_id for d in everything if d.target_kind == k and d.target_ref == r) for k, r in targets)
    check(f"active_for (one target's rows) equals the whole record filtered, on all {len(targets)} targets", same)
    from Phase3 import evidence as E
    all_ids = [d.decision_id for d in E.load_decisions(conn)]
    check(f"still_active over all {len(all_ids)} decision ids equals the active set",
          st.still_active(all_ids) == {d.decision_id for d in everything})
    batches = st.batches()
    check(f"batch(id) (one batch's query) equals its entry in batches(), for all {len(batches)} batches",
          batches and all(st.batch(b["batch_id"]) == b for b in batches))
    conn.close()


def test_gui(tmp: Path):
    section("3. The window: page open reuses the detector's model; answers, Undo and Survey Mode take the incremental path")
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
    from Phase2.core import utc_now
    from Phase3 import evidence as E, imagegame as IG, survey as SV
    import p3_visual_check as T

    app_root, corpus, r1, r2, project_dir, outs = T.open_project(tmp, "P3RefreshGui")
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: True
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[:2])))
    loads = []
    real_load = E.load_evidence

    def counting(*a, **k):
        loads.append(1)
        return real_load(*a, **k)
    E.load_evidence = counting
    try:
        app = gui.Phase2App(None)
        app.withdraw()
        app.open_project(project_dir)
        loads.clear()
        app.show_similar()
        app.update()
        page = app.similar_page

        def equal_to_fresh():
            ref = fresh_model(app.conn, page.ev.now)
            return RF.fingerprint(page.ev, page.proj, page.routing) == RF.fingerprint(*ref)
        check("page open loads the model ONCE (the detector's), not twice", len(loads) == 1, f"{len(loads)} loads")
        check("...and the page it draws equals a fresh full model", equal_to_fresh())
        first = page.shown[0]
        page.show_pair(first)
        T.press(page, "e")
        check("an answer (E) refreshes incrementally, and the page equals a fresh model",
              not page.last_refresh.get("full") and page.last_refresh["decisions"] == 2 and equal_to_fresh(), str(page.last_refresh))
        page.show_pair(first)
        called = []
        real_active = page.store.active
        page.store.active = lambda: (called.append(1), real_active())[1]
        T.press(page, "u")
        page.store.active = real_active
        check("Undo reads only the rows it undoes (never the whole record) and refreshes incrementally",
              not called and page.last_refresh["withdrawals"] == 2 and not page.last_refresh.get("full") and equal_to_fresh())
        page.survey_mode()
        win = page.survey_window
        win.run_preview(SV.KEEP_ALL)
        win.record()
        app.update()
        check("a Survey Mode group action takes the same incremental path; the page equals a fresh model",
              not page.last_refresh.get("full") and page.last_refresh["decisions"] >= 1 and equal_to_fresh(), str(page.last_refresh))
        win.undo_last()
        app.update()
        check("...and so does its undo", not page.last_refresh.get("full") and equal_to_fresh())
        win.win.destroy()
        loc = page.ev.locations[page.proj.pair(first).a_id]
        folder = loc.path.rsplit("\\", 1)[0]
        page.store.create_policy("protect_folder_subtree", {"path_key": E.path_key(folder), "path": folder}, rationale="test")
        page.show_pair(first)
        T.press(page, "d")
        check("after a policy change the refresh cannot prove an increment: it falls back to a full reload",
              page.last_refresh.get("full") is True)
        check("...whose model equals a fresh one too", equal_to_fresh())
        app.destroy()
        check("no error dialog", not [a for a in asked if a[0] == "showerror"], str(asked[-3:]))
    finally:
        E.load_evidence = real_load
        for n, fn in real.items():
            setattr(messagebox, n, fn)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3refresh_"))
    try:
        project_dir, app_root, corpus = test_sessions(tmp)
        test_store_reads(project_dir)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 REFRESH (B14): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
