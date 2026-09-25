#!/usr/bin/env python3
r"""Phase 3 Build 6 (Bulk Visual Review, B12) regression: batches over visual pairs.

What it proves, in order:

  1. The registry: three bulk actions over visual candidate pairs, each
     recording decision kinds that already exist (no new kind), none with a
     policy twin (never a standing rule); the Decide page offers only its
     own five.
  2. The population, on hand-built models: the Show list is one function
     (the page's and the batch's); a folder means both images in it -- with
     or without subfolders -- and a pair with one image out is not in it; a
     frozen query says what it matches now.
  3. The preview buckets, pair by pair (R4's shape): decided (the same two
     rows a key records), already satisfied, preserved (a different
     relationship, a preference Keep both would undo, a retention row
     Different would contradict, a deferral made by hand), conflict (the
     chain guard, and any conflict the batch itself would create --
     simulated with the whole batch in place), blocked (preview unavailable,
     stale image analysis, needs revalidation), not applicable (byte-
     identical); every member lands in a bucket; the first row gates the
     second; determinism; speed at 20,000 pairs.
  4. A real project with real images (a folder of variations, a subfolder,
     a byte copy, a copy elsewhere): the preview over the folder with and
     without subfolders; the commit is one operation with the batch, every
     member and the decisions -- origin bulk_explicit_human, the batch id,
     bound to the pair's evidence, the same shape a fast action writes; the
     exceptions are untouched; the commit refuses after any new operation
     AND after a re-analysis or a scan; a pair found later is not a member
     (F04's guarantee, for pairs) and waits on the queue unreviewed; undo
     withdraws what still stands and leaves a hand-changed decision alone;
     checked pairs as the scope; exact copies refused; the preview list as
     CSV; zero source mutation.
  5. If a display is available: the window -- the check column and Space,
     Check all shown, Bulk action..., the dialog's scopes and folder, the
     preview, Record, the queue shrinking, Batches... with pair labels and
     Undo, the Decide page's dialog without the pair actions; headless.

Run:  python Scripts\p3_visual_bulk_check.py
"""
from __future__ import annotations

import csv
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

import p3_visual_check as T                                                  # noqa: E402  (model helpers, corpus maker)
BASE = T.BASE                                                                # noqa: E402  (p3_regression helpers)
from Phase3.model import Evidence, ReviewEvent, path_key                     # noqa: E402
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import pairs as PR                                               # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import bulk as BK                                                # noqa: E402

RESULTS = []
LOCATION, GROUP, PAIR = REG.LOCATION, REG.GROUP, REG.VISUAL_PAIR
SNAP, CHECKED = REG.SCOPE_QUERY_SNAPSHOT, REG.SCOPE_EXPLICIT_SELECTION
D, SAT, PRE, CON, BLK, NA = (REG.DISP_DECIDED, REG.DISP_SATISFIED, REG.DISP_PRESERVED, REG.DISP_CONFLICT,
                             REG.DISP_BLOCKED, REG.DISP_NOT_APPLICABLE)
RELATED = "pairs_related_keep_both"
EQUIV = "pairs_equivalent_keep_both"
DIFF = "pairs_different"


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


img, vp, dec, world = T.img, T.vp, T.dec, T.world


def disp(pv):
    return {c.target_ref: c.disposition for c in pv.candidates}


# ---------------------------------------------------------------------------
# 1. Registry
# ---------------------------------------------------------------------------

def test_registry():
    section("1. The registry: three pair actions, existing kinds, no policy")
    acts = REG.bulk_actions_for([PAIR])
    check("three bulk actions over visual pairs", [a.key for a in acts] == [RELATED, EQUIV, DIFF])
    check("each records only kinds that already target a pair -- no new decision kind",
          all(set(a.decision_kinds) <= {"related_variant", "equivalent_copy", "different", "keep_both"}
              and all(PAIR in REG.DECISION_KINDS[k].target_kinds for k in a.decision_kinds) for a in acts))
    check("Related variant, keep both = V's two rows; Equivalent, keep both = E's; Different = D's",
          REG.BULK_ACTIONS[RELATED].decision_kinds == ("related_variant", "keep_both")
          and REG.BULK_ACTIONS[EQUIV].decision_kinds == ("equivalent_copy", "keep_both")
          and REG.BULK_ACTIONS[DIFF].decision_kinds == ("different",))
    check("none has a policy twin, none needs a folder parameter, none records events (never a standing rule)",
          all(a.policy_twin is None and not a.needs_folder and not a.records_events for a in acts))
    check("the Decide page's actions are its own five, unchanged",
          [a.key for a in REG.bulk_actions_for([GROUP, LOCATION])] == ["keep_all", "accept_recommendation", "keep_under",
                                                                       "redundant_under", "defer"])
    check("a group action refuses pairs and a pair action refuses groups",
          refused(lambda: BK.plan_pairs("keep_all", [], None)))


# ---------------------------------------------------------------------------
# 2. Population
# ---------------------------------------------------------------------------

def test_population():
    section("2. The population: the Show list, the folder, the frozen query")
    from Phase3 import imagegame as IG
    check("the page's Show filter IS the batch's (one function, moved to bulk.py)", IG.filter_pairs is BK.filter_pairs
          and IG.FILTERS == BK.PAIR_FILTERS)
    L = [img("1", r"C:\P\V\a.jpg"), img("2", r"C:\P\V\b.jpg"), img("3", r"C:\P\V\Sub\c.jpg"), img("4", r"C:\P\Other\d.jpg"),
         img("5", r"C:\P\Vx\e.jpg")]
    P = [vp("10", "1", "2"), vp("11", "1", "3"), vp("12", "1", "4"), vp("13", "2", "5"), vp("14", "3", "2")]
    ev, proj, rt = world(L, P)
    sub = BK.pair_snapshot_query(RT.PAIR_QUEUE_WORD, r"C:\P\V", True)
    flat = BK.pair_snapshot_query(RT.PAIR_QUEUE_WORD, r"C:\P\V", False)
    check("folder with subfolders: both images beneath it (a~b, a~Sub\\c, Sub\\c~b)",
          sorted(BK.pair_query_targets(sub, ev, proj, rt)) == ["10", "11", "14"])
    check("folder without subfolders: both images directly in it (a~b only)", BK.pair_query_targets(flat, ev, proj, rt) == ["10"])
    check("a pair with one image outside the folder is not in it; a sibling folder with the same prefix is not inside it",
          "12" not in BK.pair_query_targets(sub, ev, proj, rt) and "13" not in BK.pair_query_targets(sub, ev, proj, rt))
    check("the frozen query names its targets and folder rule", sub["targets"] == PAIR and sub["subfolders"] is True
          and sub["folder_key"] == path_key(r"C:\P\V") and flat["subfolders"] is False)
    check("query_targets answers a pair query with the same function",
          BK.query_targets(sub, ev, proj, rt) == (PAIR, BK.pair_query_targets(sub, ev, proj, rt)))
    check("the words: 'both images under C:\\P\\V (subfolders included)'",
          "both images under C:\\P\\V (subfolders included)" in BK.describe_query(sub)
          and "(that folder only)" in BK.describe_query(flat))


# ---------------------------------------------------------------------------
# 3. Buckets
# ---------------------------------------------------------------------------

def _evt(eid, ref, kind="deferred", rk="manual"):
    return ReviewEvent(eid, PAIR, ref, kind, rk, None, None, {}, None, "2026-09-12T00:00:00Z", "explicit_human", int(eid[1:]))


def test_buckets():
    section("3. The preview, pair by pair: every bucket, the gate, the simulation, determinism, speed")
    L = [img(str(i), rf"C:\V\{i}.jpg") for i in range(1, 13)]
    L[10] = replace(L[10], cloud_only=True)                          # 11 is cloud-only
    pairs = {
        "20": vp("20", "1", "2"),                  # nothing yet -> decided (2 rows)
        "21": vp("21", "2", "3"),                  # related_variant + keep_both already -> satisfied
        "22": vp("22", "3", "4"),                  # related_variant alone -> decided, 1 row (keep_both)
        "23": vp("23", "4", "5"),                  # Different by hand -> preserved
        "24": vp("24", "5", "6"),                  # equivalent + prefer 5 -> relationship preserved (related), gated
        "25": vp("25", "6", "7"),                  # equivalent + prefer 6 while 24 gives up 6 -> chain conflict
        "26": vp("26", "7", "8"),                  # deferred by hand -> preserved
        "27": vp("27", "8", "11"),                 # cloud-only image -> blocked
        "28": vp("28", "9", "10", exact=True),     # byte-identical -> not applicable
        "29": vp("29", "10", "12"),                # decided, but its image moved -> needs revalidation -> blocked
        "30": vp("30", "1", "12"),                 # Keep both alone (no relationship yet) -> decided; Different preserves it
    }
    moved = {"29": T.pair_binding(pairs["29"], {"10": L[9], "12": replace(L[11], observation_id="old")})}
    decs = [dec("D1", "related_variant", "21"), dec("D2", "keep_both", "21", ["2", "3"]), dec("D3", "related_variant", "22"),
            dec("D4", "different", "23"), dec("D5", "equivalent_copy", "24"), dec("D6", "canonical_location", "24", "5"),
            dec("D7", "equivalent_copy", "25"), dec("D8", "canonical_location", "25", "6"),
            dec("D9", "related_variant", "29", binding=moved["29"]), dec("D10", "keep_both", "30", ["1", "12"])]
    ev, proj, rt = world(L, list(pairs.values()), decs, events=[_evt("E1", "26")])
    check("setup: 25 is in conflict (the chain guard), 27 blocked, 29 needs revalidation",
          rt.pairs["25"].primary == RT.CONFLICT and rt.pairs["27"].primary == RT.BLOCKED and rt.pairs["29"].primary == RT.NEEDS_REVALIDATION)
    q = BK.pair_snapshot_query(BK.LENS_ALL, r"C:\V", True)
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q)
    got = disp(pv)
    want = {"20": D, "21": SAT, "22": D, "23": PRE, "24": PRE, "25": CON, "26": PRE, "27": BLK, "28": NA, "29": BLK, "30": D}
    check("Related variant, keep both over the folder: every pair in its bucket", got == want, str(got))
    check("every member lands in a bucket -- none dropped", len(pv.candidates) == len(pairs) == sum(pv.counts.values()))
    c = {x.target_ref: x for x in pv.candidates}
    check("a decided pair gets exactly V's two rows; a pair already related gets only Keep both",
          c["20"].to_record == (("related_variant", PAIR, "20", True), ("keep_both", PAIR, "20", ["1", "2"]))
          and c["22"].to_record == (("keep_both", PAIR, "22", ["3", "4"]),) and c["30"].to_record == (("related_variant", PAIR, "30", True),)
          and pv.decision_count == 4)
    check("the reasons are said: Different stands, a deferral by hand, the chain guard, cloud-only, needs revalidation, byte-identical",
          "Different (#D4) stands" in c["23"].detail and "deferred by hand" in c["26"].detail
          and "already needs a person" in c["25"].detail and "never inferred along a chain" in c["25"].detail
          and "cloud-only" in c["27"].detail and "needs revalidation" in c["29"].detail and c["28"].detail == BK.EXACT_NOT_APPLICABLE)
    check("the first row gates the second: an equivalent pair keeps its preference (nothing recorded for 24)",
          all(o[0] == PRE for o in c["24"].outcomes) and "(gates the rest)" in c["24"].outcomes[1][1])
    pv2 = BK.preview_pairs(ev, proj, rt, EQUIV, SNAP, q)
    c2 = {x.target_ref: x for x in pv2.candidates}
    check("Equivalent, keep both on the equivalent pair that prefers 5: the relationship holds, Keep both is preserved (it would undo the preference)",
          [o[0] for o in c2["24"].outcomes] == [SAT, PRE] and c2["24"].disposition == PRE and "would undo that preference" in c2["24"].detail)
    check("...and on 21 (related): its relationship stands, so the pair is preserved", c2["21"].disposition == PRE)
    pv3 = BK.preview_pairs(ev, proj, rt, DIFF, SNAP, q)
    c3 = {x.target_ref: x for x in pv3.candidates}
    check("Different over the folder: a pair carrying Keep both is preserved (D by hand withdraws it; a batch never does)",
          c3["30"].disposition == PRE and "never does" in c3["30"].outcomes[0][1] and c3["20"].disposition == D
          and c3["20"].to_record == (("different", PAIR, "20", True),))
    lines = pv.lines()
    check("the preview reads in R4's shape: population, new, already, exceptions preserved, conflicts, blocked, and the promise",
          lines[0].startswith("11 pairs in scope") and any("would receive a decision (4 decisions: Related variant + Keep both)" in l for l in lines)
          and any("already satisfy" in l for l in lines) and any("preserved" in l for l in lines)
          and any("would conflict" in l for l in lines) and any("blocked by evidence" in l for l in lines)
          and lines[-1] == "No source files will be changed.", "\n".join(lines))
    check("checked pairs as the scope: exactly those", sorted(disp(BK.preview_pairs(ev, proj, rt, RELATED, CHECKED, selection=["20", "23"]))) == ["20", "23"])
    check("exact copies are never a bulk-relationship population", refused(lambda: BK.preview_pairs(ev, proj, rt, RELATED, SNAP,
                                                                                                       BK.pair_snapshot_query(BK.LENS_EXACT))))
    check("nothing checked, or no query: refused", refused(lambda: BK.preview_pairs(ev, proj, rt, RELATED, CHECKED, selection=[]))
          and refused(lambda: BK.preview_pairs(ev, proj, rt, RELATED, SNAP, None)))

    # The simulation: conflicts the batch itself would create are caught with the whole batch in place.
    A, B, C = img("1", r"C:\S\a.jpg"), img("2", r"C:\S\b.jpg"), img("3", r"C:\S\c.jpg")
    AB, BC = vp("40", "1", "2"), vp("41", "2", "3")
    ev, proj, rt = world([A, B, C], [AB, BC], [dec("D1", "equivalent_copy", "40"), dec("D2", "canonical_location", "40", "1")])
    cand = BK.Candidate(PAIR, "41", (("equivalent_copy", PAIR, "41", True), ("canonical_location", PAIR, "41", "2")), "", "b ~ c")
    BK.classify_pairs([cand], ev, proj, rt)
    check("the simulation: rows that would make a pair keep an image another pair gives up are a conflict, both rows withheld",
          cand.disposition == CON and "never inferred along a chain" in cand.detail and cand.to_record == ())
    ev, proj, rt = world([A, B, C], [AB, BC])
    c1 = BK.Candidate(PAIR, "40", (("equivalent_copy", PAIR, "40", True), ("canonical_location", PAIR, "40", "1")), "", "a ~ b")
    c2 = BK.Candidate(PAIR, "41", (("equivalent_copy", PAIR, "41", True), ("canonical_location", PAIR, "41", "2")), "", "b ~ c")
    BK.classify_pairs([c1, c2], ev, proj, rt)
    check("...within one batch too: the second half of a chain is withheld, the first recorded",
          c1.disposition == D and c2.disposition == CON)

    # Determinism and speed.
    rnd = random.Random(3)
    q = BK.pair_snapshot_query(BK.LENS_ALL, r"C:\V", True)
    ev, proj, rt = world(L, list(pairs.values()), decs, events=[_evt("E1", "26")])
    base = json.dumps([x.as_dict() for x in BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q).candidates], sort_keys=True)
    same = True
    for _ in range(3):
        d2, l2 = list(decs), list(L)
        rnd.shuffle(d2), rnd.shuffle(l2)
        e2, p2, r2 = world(l2, list(pairs.values()), d2, events=[_evt("E1", "26")])
        same &= json.dumps([x.as_dict() for x in BK.preview_pairs(e2, p2, r2, RELATED, SNAP, q).candidates], sort_keys=True) == base
    check("the same record in any order gives the same preview (3 shuffles)", same)
    many = [img(str(i), rf"C:\Big\{i // 150}\{i}.jpg") for i in range(1, 6001)]
    mp = [vp(str(100000 + i), str(i), str(i + 1 if (i % 150) else i - 1)) for i in range(1, 6000)]
    mp += [vp(str(200000 + i), str(i), str(i + 2)) for i in range(1, 5999) if (i % 150) not in (0, 149)]
    md = [dec(f"D{i}", "different", str(100000 + i)) for i in range(1, 6000, 7)]
    ev, proj, rt = world(many, mp, md)
    t0 = time.perf_counter()
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, BK.pair_snapshot_query(RT.PAIR_QUEUE_WORD, r"C:\Big", True))
    took = time.perf_counter() - t0
    check(f"{len(pv.candidates):,} pairs previewed in {took:.2f} s (< 10 s)", took < 10.0 and len(pv.candidates) > 10000, f"{took:.2f}s")


# ---------------------------------------------------------------------------
# 4. A real project
# ---------------------------------------------------------------------------

def build_variations(base: Path):
    """Photos\\Variations: seven edits of one picture and a byte copy of the
    first; Photos\\Variations\\More: two more edits; Photos\\Other: a resized
    copy of one edit (a look-alike outside the folder); an unrelated picture."""
    from PIL import ImageDraw, ImageEnhance
    root = base / "Photos"
    (root / "Variations" / "More").mkdir(parents=True)
    (root / "Other").mkdir(parents=True)
    pic = T._photo(11)

    def variant(k):
        im = pic.copy()
        x, y = 30 + 70 * (k % 7), 30 + 50 * (k // 7)
        ImageDraw.Draw(im).ellipse([x, y, x + 14, y + 14], fill=(255, 255, 255))
        return ImageEnhance.Brightness(im).enhance(1.06) if k % 3 == 1 else im
    for k in range(7):
        variant(k).save(root / "Variations" / f"v{k}.jpg", quality=92)
    shutil.copyfile(root / "Variations" / "v0.jpg", root / "Variations" / "v0_copy.jpg")
    for k in (8, 9):
        variant(k).save(root / "Variations" / "More" / f"v{k}.jpg", quality=92)
    variant(3).resize((320, 240)).save(root / "Other" / "v3_small.jpg", quality=80)
    T._photo(12).save(root / "Other" / "unrelated.jpg", quality=90)
    return root, variant


def open_variation_project(tmp: Path, name):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    app_root = tmp / f"{name}Root"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / f"{name}Corpus"
    root, variant = build_variations(corpus)
    outs = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(root)], project_name=name)).run()]
    project_dir = app_root / "Projects" / name
    outs.append(RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run())
    outs.append(RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze pictures", analyzer_keys=["image"])).run())
    return app_root, corpus, root, variant, project_dir, outs


def _ids(conn):
    out = {}
    for r in conn.execute("SELECT fs.file_path_id, fp.relative_path FROM file_state fs JOIN file_path fp ON fp.file_path_id = fs.file_path_id"):
        out[r["relative_path"].replace("\\", "/")] = str(r["file_path_id"])
    return out


def _model(conn):
    from Phase3 import evidence as E
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, ev.now)


def test_project(tmp: Path):
    section("4. A real project: the variations folder, the commit, the guards, frozen membership, undo, zero mutation")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, ANALYSIS
    from Phase2.core import connect
    from Phase2 import visual as V
    from Phase3 import evidence as E, store as S

    app_root, corpus, root, variant, project_dir, outs = open_variation_project(tmp, "P3VisBulk")
    check("Pre-Scan, Find My Duplicates, Analyze (pictures) through the worker", all(o.ok for o in outs), str([o.status for o in outs]))
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visbulk")
    F = _ids(conn)
    v = {k: F[f"Variations/v{k}.jpg"] for k in range(7)}
    v.update({k: F[f"Variations/More/v{k}.jpg"] for k in (8, 9)})
    copy0, small3, unrel = F["Variations/v0_copy.jpg"], F["Other/v3_small.jpg"], F["Other/unrelated.jpg"]
    ev, proj, rt = _model(conn)
    pid = {frozenset(p.members): p.pair_id for p in ev.pairs.values()}
    P = lambda a, b: pid[frozenset((a, b))]                           # noqa: E731
    check("the look-alikes pair up: 55 pairs among the 11 (ten edits, the copy, the resized copy); the unrelated picture in none",
          len(ev.pairs) == 55 and not any(unrel in p.members for p in ev.pairs.values()), str(len(ev.pairs)))

    # The state a person leaves behind before reaching for a batch.
    st.record_decision("different", PAIR, P(v[1], v[2]))                                      # an exception
    st.record_decisions([("equivalent_copy", PAIR, P(v[4], v[5]), True), ("canonical_location", PAIR, P(v[4], v[5]), v[4])])
    st.record_decisions([("equivalent_copy", PAIR, P(v[5], v[6]), True), ("canonical_location", PAIR, P(v[5], v[6]), v[5])])
    st.defer(PAIR, P(v[2], v[3]), "defer_indefinitely")
    st.record_decisions([("related_variant", PAIR, P(v[0], v[8]), True), ("keep_both", PAIR, P(v[0], v[8]), [v[0], v[8]])])
    conn.execute("UPDATE file_state SET is_offline_or_cloud = 1 WHERE file_path_id = ?", (int(v[9]),))
    conn.commit()
    ev, proj, rt = _model(conn)
    check("setup: the chain pair is a conflict, v9's pairs are blocked, the byte copy's pair is exact",
          rt.pairs[P(v[5], v[6])].primary == RT.CONFLICT and rt.pairs[P(v[9], v[1])].primary == RT.BLOCKED
          and proj.pair(P(v[0], copy0)).exact_content)

    folder = str(root / "Variations")
    q_sub = BK.pair_snapshot_query(RT.PAIR_QUEUE_WORD, folder, True)
    q_all = BK.pair_snapshot_query(BK.LENS_ALL, folder, True)
    q_flat = BK.pair_snapshot_query(BK.LENS_ALL, folder, False)
    n_sub, n_flat = len(BK.pair_query_targets(q_all, ev, proj, rt)), len(BK.pair_query_targets(q_flat, ev, proj, rt))
    check("the folder with its subfolder holds 45 pairs (ten images); directly in it, 28 (eight): the subfolder rule matters",
          n_sub == 45 and n_flat == 28, f"{n_sub} / {n_flat}")
    check("the pairs with the resized copy in Other are in neither (one image outside)",
          not any(small3 in ev.pairs[p].members for p in BK.pair_query_targets(q_all, ev, proj, rt)))
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q_all, conn=conn)
    d = disp(pv)
    check("the preview over All pairs in the folder: every pair accounted for",
          len(pv.candidates) == 45 and sum(pv.counts.values()) == 45)
    check("...the Different pair, the equivalent pair with its preference and the deferred pair preserved; the chain pair a conflict",
          d[P(v[1], v[2])] == PRE and d[P(v[4], v[5])] == PRE and d[P(v[2], v[3])] == PRE and d[P(v[5], v[6])] == CON)
    check("...v9's pairs blocked (cloud-only), the byte copy's pair not applicable, the already-answered pair satisfied",
          all(d[P(v[9], v[k])] == BLK for k in (0, 1, 2, 3, 4, 5, 6, 8)) and d[P(v[0], copy0)] == NA and d[P(v[0], v[8])] == SAT)
    decided = [c for c in pv.candidates if c.disposition == D]
    check(f"...and {len(decided)} pairs to decide, two rows each", pv.decision_count == 2 * len(decided) and len(decided) == 45 - 3 - 1 - 9 - 1 - 1,
          str(pv.counts))
    check("the preview carries both marks: the record and the evidence", pv.record_mark is not None and pv.evidence_mark)

    # Guards: any new operation, a re-analysis, a scan.
    st.skip(PAIR, P(v[6], v[8]))
    check("the commit refuses after a new operation (a skip is one)", refused(lambda: st.commit_bulk(pv)))
    ev, proj, rt = _model(conn)
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q_all, conn=conn)
    V.generate_candidates(conn)
    check("...and after a re-analysis (no operation: the evidence mark catches it)",
          refused(lambda: st.commit_bulk(pv)) and BK.record_mark(conn) == pv.record_mark)
    ev, proj, rt = _model(conn)
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q_all, conn=conn)
    conn.close()
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visbulk")
    check("...and after a scan", refused(lambda: st.commit_bulk(pv)))
    RT.reconcile(conn, st)

    # The commit.
    ev, proj, rt = _model(conn)
    before_queue = rt.pair_counts[RT.UNRESOLVED]
    pv = BK.preview_pairs(ev, proj, rt, RELATED, SNAP, q_all, conn=conn)
    n_pop = len(pv.candidates)
    check("the scan read v9 as local again: its pairs no longer rest on the compared observations and left the population "
          "(no current evidence -- analyze again)", n_pop == 45 - 9 and not any(v[9] in ev.pairs[c.target_ref].members for c in pv.candidates))
    exceptions = {c.target_ref: c.disposition for c in pv.candidates if c.disposition != D}
    ops0 = st.counts()["operations"]
    with BASE.WriteGuard([project_dir, app_root / "Logs"]) as guard:
        res = st.commit_bulk(pv, note="the variations are variations")
        (project_dir / "Exports").mkdir(exist_ok=True)
        path = project_dir / "Exports" / "BulkPreview.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as h:
            csv.writer(h).writerows(BK.preview_table(pv))
    check(f"one operation holds the batch, its {n_pop} members and every decision", st.counts()["operations"] == ops0 + 1
          and res["members"] == n_pop and res["decisions"] == pv.decision_count)
    batch = conn.execute("SELECT * FROM p3_bulk_batch WHERE batch_id = ?", (res["batch_id"],)).fetchone()
    q = json.loads(batch["query_json"])
    check("the batch row: a query-result snapshot, the frozen query (Show, folder, subfolders), frozen and committed",
          batch["scope_kind"] == SNAP and q["targets"] == PAIR and q["folder"] == folder and q["subfolders"] is True
          and batch["frozen"] == 1 and batch["committed"] == 1 and batch["mode"] == "preserve")
    mem = conn.execute("SELECT target_kind, target_ref, disposition FROM p3_bulk_member WHERE batch_id = ?", (res["batch_id"],)).fetchall()
    check("every member recorded with what became of it", len(mem) == n_pop and all(m["target_kind"] == PAIR for m in mem)
          and {m["target_ref"]: m["disposition"] for m in mem if m["disposition"] != D} == exceptions)
    rows = conn.execute("SELECT * FROM p3_decision WHERE origin_ref = ? ORDER BY decision_id", (str(res["batch_id"]),)).fetchall()
    b0 = json.loads(rows[0]["evidence_binding_json"])
    check("each decision: origin bulk_explicit_human, the batch id, bound to the pair's evidence (ids only)",
          all(r["origin_kind"] == "bulk_explicit_human" and r["target_kind"] == PAIR for r in rows)
          and b0["target_kind"] == PAIR and len(b0["members"]) == 2 and all(m["observation_id"] for m in b0["members"]))
    one = rows[0]["target_ref"]
    fb = E.pair_binding(conn, one)
    check("the binding built from the model equals the one read from the database (a fast action's)",
          {k: b0[k] for k in ("pair_id", "method", "distance", "hash_bits", "members")}
          == {k: fb[k] for k in ("pair_id", "method", "distance", "hash_bits", "members")})
    kinds = [(r["target_ref"], r["decision_kind"], json.loads(r["value_json"])) for r in rows if r["target_ref"] == one]
    a, b = sorted(ev.pairs[one].members, key=int)
    check("the same two rows V writes: related_variant = true, keep_both = the pair's two ids",
          kinds == [(one, "related_variant", True), (one, "keep_both", [a, b])])
    check("no Python-level write outside the project folder while the batch was recorded and its list saved", not guard.violations,
          str(guard.violations[:3]))
    with open(path, encoding="utf-8-sig", newline="") as h:
        saved = list(csv.reader(h))
    check("the preview list: one row per member with its disposition and reason", len(saved) == n_pop + 1 and saved[0][3] == "disposition")
    ev, proj, rt = _model(conn)
    check("afterwards: the decided pairs are Resolved (related variants, both kept); the queue shrank by exactly them",
          all(rt.pairs[c.target_ref].primary == RT.RESOLVED for c in pv.candidates if c.disposition == D)
          and rt.pair_counts[RT.UNRESOLVED] == before_queue - sum(1 for c in pv.candidates if c.disposition == D
                                                                   and c.target_ref in {p.pair_id for p in RT.ordered_pairs(proj, rt, RT.RESOLVED)}))
    check("the exceptions are exactly as they were: Different still Different, the preference still preferred, the conflict still a conflict",
          [x.kind for x in st.active_for(PAIR, P(v[1], v[2]))] == ["different"]
          and {x.kind for x in st.active_for(PAIR, P(v[4], v[5]))} == {"equivalent_copy", "canonical_location"}
          and rt.pairs[P(v[5], v[6])].primary == RT.CONFLICT and rt.pairs[P(v[2], v[3])].primary == RT.DEFERRED)

    # Frozen membership: a new variation appears in the folder later.
    variant(6).rotate(0).save(root / "Variations" / "v6b.jpg", quality=70)
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze pictures", analyzer_keys=["image"])).run()
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:visbulk")
    ev, proj, rt = _model(conn)
    new_id = _ids(conn)["Variations/v6b.jpg"]
    now = BK.pair_query_targets(q_all, ev, proj, rt)
    later = [p for p in now if new_id in ev.pairs[p].members]
    members = {m["target_ref"] for m in st.batch_members(res["batch_id"])}
    check("a picture added later: the frozen query matches its pairs NOW; the batch does not have them (F04, for pairs)",
          later and not (set(later) & members))
    check("...they carry no decision and wait in the Visual Similarity Review, unreviewed",
          all(not proj.pair(p).decision_ids and rt.pairs[p].primary == RT.UNRESOLVED and rt.pairs[p].word == "Unreviewed"
              for p in later if not proj.pair(p).exact_content))

    # Undo, with one pair changed by hand since.
    changed = next(c.target_ref for c in pv.candidates if c.disposition == D)
    st.record_decision("different", PAIR, changed)
    active_before = sum(1 for x in st.batch_decisions(res["batch_id"]) if x["active"])
    u = st.undo_batch(res["batch_id"])
    check("undo: one operation withdraws what the batch recorded and still stands; the hand-changed relationship is left alone",
          u["withdrawn"] == active_before and [x.kind for x in st.active_for(PAIR, changed)] == ["different"])
    ev, proj, rt = _model(conn)
    check("...the batch's other pairs are back in the queue; the batch and its members stay in the record",
          all(rt.pairs[c.target_ref].primary == RT.UNRESOLVED for c in pv.candidates if c.disposition == D and c.target_ref != changed)
          and len(st.batch_members(res["batch_id"])) == n_pop and refused(lambda: st.undo_batch(res["batch_id"])))

    # Checked pairs; Different; Equivalent.
    pick = [P(v[6], v[8]), P(v[3], v[8])]
    pvc = BK.preview_pairs(ev, proj, rt, DIFF, CHECKED, selection=pick, conn=conn)
    r2 = st.commit_bulk(pvc)
    b2 = conn.execute("SELECT scope_kind, query_json, parameters_json FROM p3_bulk_batch WHERE batch_id=?", (r2["batch_id"],)).fetchone()
    check("checked pairs: an explicit selection of exactly those, Different recorded on each",
          b2["scope_kind"] == CHECKED and b2["query_json"] is None and json.loads(b2["parameters_json"])["selection"] == pick
          and all([x.kind for x in st.active_for(PAIR, p)] == ["different"] for p in pick))
    ev, proj, rt = _model(conn)
    check("exact copies are refused as a population", refused(lambda: BK.preview_pairs(ev, proj, rt, EQUIV, SNAP, BK.pair_snapshot_query(BK.LENS_EXACT))))
    journal = project_dir / "Exports" / "DecisionJournal.txt"
    S.export_decision_journal(conn, journal)
    check("the journal lists the pair batches and marks their decisions", "bulk batch #" in journal.read_text(encoding="utf-8")
          and "on visual_candidate_pair" in journal.read_text(encoding="utf-8"))
    conn.close()


# ---------------------------------------------------------------------------
# 5. The window
# ---------------------------------------------------------------------------

def test_gui(tmp: Path):
    section("5. The window: checks, Bulk action..., the preview, Record, Batches and Undo")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from tkinter import messagebox, filedialog
    from Phase2 import gui
    from Phase3 import imagegame as IG, review as RV

    app_root, corpus, root, variant, project_dir, outs = open_variation_project(tmp, "P3VisBulkGui")
    before = BASE.fingerprint_tree(corpus)
    asked = []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    real_save = filedialog.asksaveasfilename
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[0] if a else k.get("title"))), True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else k.get("title"), a[1] if len(a) > 1 else k.get("message"))))
    try:
        with BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            app.show_similar()
            app.update()
            page = app.similar_page
            st = RV.store_for(app)
            check("the pair list has a check column; nothing checked yet",
                  page.table["columns"][0] == "check" and page.checked_var.get() == "Nothing checked")
            first = page.shown[0]
            page.table.focus(first)
            page.toggle_check()
            check("Space checks the pair in focus", page.checked == {first} and page.table.set(first, "check") == RV.CHECKED)
            n0 = st.counts()["decisions"]
            T.press(page, "d")
            check("a letter key still acts on the pair in view only -- checks never change a keystroke",
                  st.counts()["decisions"] == n0 + 1 and page.checked == {first})
            T.press(page, "u")
            page.check_all_shown()
            check("Check all shown checks every pair the Show list holds", page.checked == set(page.shown)
                  and page.checked_var.get().startswith(f"{len(page.shown):,} checked"))
            page.clear_checks()
            page.show_pair(first)
            page.bulk_action()
            dlg = page.bulk_dialog
            check("Bulk action... opens the pair dialog: its three actions, the folder of the pair in view, subfolders on",
                  [a.key for a in dlg.actions] == [RELATED, EQUIV, DIFF] and dlg.scope_var.get() == "folder"
                  and dlg.folder_var.get() == IG._folder(page.ev.locations[page.left_id].path) and dlg.sub_var.get())
            check("with nothing checked the checked scope is off; Record is off until a preview",
                  str(dlg.scope_buttons["checked"].cget("state")) == "disabled" and str(dlg.ok.cget("state")) == "disabled")
            dlg.folder_var.set(str(root / "Variations"))
            dlg.run_preview()
            text = dlg.preview_text.get("1.0", "end")
            n_dec = dlg.preview.counts[REG.DISP_DECIDED]
            check("the preview says it in R4's shape and Record names the count",
                  "pairs in scope" in text and "would receive a decision" in text and "No source files will be changed." in text
                  and str(dlg.ok.cget("state")) == "normal" and dlg.ok.cget("text") == f"Record {2 * n_dec:,} decisions on {n_dec:,} pairs")
            dlg.sub_var.set(False)
            dlg._inputs_changed()
            check("changing an input voids the preview", str(dlg.ok.cget("state")) == "disabled" and dlg.preview is None)
            dlg.sub_var.set(True)
            dlg._inputs_changed()
            dlg.run_preview()
            saved = project_dir / "Exports" / "gui_preview.csv"
            (project_dir / "Exports").mkdir(exist_ok=True)
            filedialog.asksaveasfilename = lambda *a, **k: str(saved)
            dlg.save_list()
            check("Save the preview list writes every member with its disposition, into the project's Exports",
                  saved.is_file() and len(saved.read_text(encoding="utf-8-sig").splitlines()) == len(dlg.preview.candidates) + 1)
            queue_before = page.routing.pair_counts[RT.UNRESOLVED]
            n_members = len(dlg.preview.candidates)
            dlg._confirm()
            app.update()
            check("Record: the batch is written and the window says what was left as it was",
                  asked and asked[-1][0] == "showinfo" and "recorded" in asked[-1][2] and st.counts()["batches"] == 1)
            check("the queue shrank by the pairs decided", page.routing.pair_counts[RT.UNRESOLVED] == queue_before - n_dec,
                  f"{queue_before} -> {page.routing.pair_counts[RT.UNRESOLVED]}")
            page.open_batches()
            bd = page.batches_dialog
            bid = bd.table.get_children()[0]
            labels = [bd.members.set(r, "target") for r in bd.members.get_children()]
            check("Batches... lists the pair batch, its members labelled as pairs", bd.batches[bid]["label"] == "Related variant, keep both"
                  and all(" ~ " in l and "(pair #" in l for l in labels) and len(labels) == n_members == 44)
            bd.undo()
            app.update()
            check("Undo batch from Batches...: the pairs are back in the queue", page.routing.pair_counts[RT.UNRESOLVED] == queue_before)
            bd.win.destroy()
            page.filter_var.set(IG.LENS_EXACT)
            page.refresh_list()
            page.bulk_action()
            check("over Exact copies, Bulk action... says the duplicate review answers them", asked[-1][0] == "showinfo"
                  and "Exact copies" in asked[-1][2])
            app.show_decide()
            app.update()
            rp = app.review_page
            if rp.proj.groups:
                rp.checked.add(rp.proj.groups[0].group_id)
                rp.bulk_action()
                check("the Decide page's Bulk action... offers its own five actions, none over pairs",
                      rp.bulk_dialog.actions == ["keep_all", "accept_recommendation", "keep_under", "redundant_under", "defer"])
                rp.bulk_dialog.win.destroy()
            app.destroy()
        check("no Python-level write outside the project and log folders", not guard.violations, str(guard.violations[:3]))
        check("every corpus file untouched", BASE.fingerprint_tree(corpus) == before)
        check("no unexpected error dialog", not [a for a in asked if a[0] == "showerror"], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)
        filedialog.asksaveasfilename = real_save


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3visbulk_"))
    try:
        test_registry()
        test_population()
        test_buckets()
        test_project(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 VISUAL BULK (Build 6): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
