#!/usr/bin/env python3
r"""Phase 3 Build 7 (B13) regression: the group evidence guard, and Survey Mode.

Two pieces of work, two parts of this suite -- kept apart, as the handoff
asks, so each can be read on its own.

  A. The evidence guard for GROUP batches (the patch). On a real project with
     exact-duplicate groups: a group bulk preview now carries the evidence
     mark pair previews have carried since Build 6; the commit refuses it after
     a new operation (unchanged), and -- the new coverage -- after a rescan of
     unchanged files, a re-hash, and a scan that finds a changed file, none of
     which adds an operation. The B12 mark (observation + location count) did
     not move for the unchanged rescan; the Build 7 mark does. One check in
     commit_bulk serves both target kinds.

  B. Survey Mode.
     1. Clusters, on hand-built models: connected components of the open
        pairs in a Show list (a resolved or byte-identical pair joins
        nothing), numbered in the list's order; isolated pairs, 3-8 image
        clusters, larger ones reported with their size and folders; the
        pairs of a cluster = every candidate pair among its images (one
        outside the Show list included); non-edges listed; deterministic.
     2. The group controls as Build 6 batches: F06 as it ships (three
        candidate pairs, A~C marked Different) and F06 in the handoff's §34
        shape (A~C not a candidate): Mark Remaining Equivalent writes on the
        real pairs only -- two relationship decisions, nothing across the
        missing edge. Keep All; Mark Remaining Equivalent with a Canonical
        and Keepers; Mark Selected as Related Variant's edge rule (exactly
        one image selected); Defer Group (undecided pairs only); what stands
        is preserved, the chain guard excludes, blocked is blocked; Exclude
        shrinks the population; the fan-out over a six-image cluster.
     3. A real project with real images: the cluster the hashes make, a group
        control committed as one batch (members, decisions with batch
        provenance, the cluster noted on the batch), the Batches words, the
        journal, undo, the evidence guard on a Survey preview, zero source
        mutation.
     4. If a display is available: the window -- Survey Mode... on the page,
        the tiles, Keeper / Canonical / Select, Exclude records nothing,
        Compare opens the page's Compare Mode (a non-candidate pair offers no
        decision), a group control previewed and recorded, the queue
        shrinking, Undo, Next cluster, the report for a pair outside any 3-8
        cluster; headless.

Run:  python Scripts\p3_survey_check.py
"""
from __future__ import annotations

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
from Phase3.model import ReviewEvent                                         # noqa: E402
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import bulk as BK                                                # noqa: E402
from Phase3 import survey as SV                                              # noqa: E402

RESULTS = []
LOCATION, GROUP, PAIR = REG.LOCATION, REG.GROUP, REG.VISUAL_PAIR
D, SAT, PRE, CON, BLK, NA = (REG.DISP_DECIDED, REG.DISP_SATISFIED, REG.DISP_PRESERVED, REG.DISP_CONFLICT,
                             REG.DISP_BLOCKED, REG.DISP_NOT_APPLICABLE)
FIXTURES = SCRIPTS.parent / "Resources" / "Phase3" / "fixture_lab" / "Fixtures"


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


def rows(pv):
    return {c.target_ref: c.to_record for c in pv.candidates}


def b12_mark(conn):
    """The evidence mark as Build 6 (B12) computed it -- to show what it missed."""
    gen = conn.execute("SELECT COALESCE(MAX(generation_id), 0) FROM visual_candidate_generation").fetchone()[0]
    obs = conn.execute("SELECT COALESCE(MAX(current_observation_id), 0), COUNT(*) FROM file_state").fetchone()
    return f"generation {int(gen or 0)}; observation {int(obs[0] or 0)}; locations {int(obs[1] or 0)}"


# ===========================================================================
# PART A -- the evidence guard, extended to group batches
# ===========================================================================

def test_group_guard(tmp: Path):
    section("A. The evidence guard for group batches: a scan, a re-hash or a changed file between preview and commit refuses it")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    SNAP = REG.SCOPE_QUERY_SNAPSHOT

    app_root = tmp / "GuardRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "GuardCorpus"
    root1, root2 = BASE.build_corpus(corpus)
    RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(root1), str(root2)], project_name="P3Guard")).run()
    project_dir = app_root / "Projects" / "P3Guard"
    RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run()

    def opened():
        c = connect(project_dir, write=True)
        return c, S.DecisionStore(c, actor_id="tester", session_id="session:guard")

    def fresh(c):
        ev = E.load_evidence(c)
        proj = R.resolve_all(ev)
        rt = RT.route_all(ev, proj, ev.now)
        return BK.preview(ev, proj, rt, "keep_all", {}, SNAP, BK.snapshot_query(show="All"), conn=c)

    def run(kind, label):
        RunWorker(app_root, project_dir, RunRequest(kind, label)).run()

    conn, st = opened()
    pv = fresh(conn)
    check("a group preview (Keep all over All: two exact-duplicate groups) carries both marks: the record and the evidence",
          pv.record_mark is not None and pv.evidence_mark and pv.decision_count == 2 and "run " in pv.evidence_mark, str(pv.evidence_mark))
    st.skip(GROUP, pv.candidates[0].target_ref)
    check("unchanged: a new operation (a skip) voids it", refused(lambda: st.commit_bulk(pv)))

    pv = fresh(conn)
    old = b12_mark(conn)
    conn.close()
    run(PRESCAN, "Pre-Scan")                                           # nothing on disk changed
    conn, st = opened()
    check("a rescan of unchanged files adds no operation, and B12's mark does not see it (no new observation, same count)",
          BK.record_mark(conn) == pv.record_mark and b12_mark(conn) == old)
    try:
        st.commit_bulk(pv)
        msg = ""
    except ValueError as exc:
        msg = str(exc)
    check("...the Build 7 mark does: the commit refuses, and says why (the newest scan moved)",
          "the evidence changed since this preview was made" in msg and "preview again" in msg, msg)
    check("...and nothing was written by the refused commit", st.counts()["batches"] == 0)

    pv = fresh(conn)
    conn.close()
    run(DUPLICATES, "Find My Duplicates")                              # a re-hash of the same files
    conn, st = opened()
    check("a re-hash (Find My Duplicates again, nothing changed) voids a group preview", refused(lambda: st.commit_bulk(pv))
          and BK.record_mark(conn) == pv.record_mark)

    pv = fresh(conn)
    conn.close()
    (root1 / "Archive" / "tax.docx").write_bytes(b"changed" * 500)     # a group member's content changes on disk
    run(PRESCAN, "Pre-Scan")
    conn, st = opened()
    check("a scan that finds a group member changed voids it", refused(lambda: st.commit_bulk(pv)))

    pv = fresh(conn)
    res = st.commit_bulk(pv, note="previewed on the evidence as it is")
    check("previewed again on the evidence as it is, it commits", res["decisions"] == pv.decision_count and st.counts()["batches"] == 1)

    # One check, both target kinds: a pair preview is refused by the same rescan (Build 6's guard, now also on this case).
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, ev.now)
    ppv = replace(BK.BulkPreview("pairs_keep_both", {"targets": PAIR}, REG.SCOPE_EXPLICIT_SELECTION, None, 0, []),
                  record_mark=BK.record_mark(conn), evidence_mark=E.evidence_mark(conn))
    conn.close()
    run(PRESCAN, "Pre-Scan")
    conn, st = opened()
    try:
        st.commit_bulk(ppv)
        pmsg = ""
    except ValueError as exc:
        pmsg = str(exc)
    check("the same check refuses a pair preview after the same rescan, with the same words (one path for both kinds)",
          "the evidence changed since this preview was made" in pmsg)
    conn.close()


# ===========================================================================
# PART B -- Survey Mode
# ===========================================================================

def _evt(eid, ref):
    return ReviewEvent(eid, PAIR, ref, "deferred", "manual", None, None, {}, None, "2026-09-12T00:00:00Z", "explicit_human", int(eid[1:]))


def test_clusters():
    section("B1. Clusters: components of the open pairs in a Show list; isolated, 3-8, larger; the pairs among a cluster's images")
    L = [img(str(i), rf"C:\P\F{i % 3}\{i}.jpg") for i in range(1, 25)]
    P = [vp("10", "1", "2"), vp("11", "2", "3"),                          # cluster {1,2,3}, a chain
         vp("12", "4", "5"),                                              # isolated pair
         vp("13", "6", "7"), vp("14", "7", "8"), vp("15", "8", "6"), vp("16", "8", "9"),     # {6,7,8,9}
         vp("17", "10", "11", exact=True),                                # byte-identical: joins nothing
         vp("18", "12", "13"), vp("19", "13", "14")]                     # joined only if 18 is open
    P += [vp(str(100 + i), str(14 + i), str(15 + i)) for i in range(0, 10)]   # 14..24: with 19, a chain of 12 images
    decs = [dec("D1", "different", "18")]                                 # 18 resolved: 12 is not in 13's cluster
    ev, proj, rt = world(L, P, decs)
    m = SV.cluster_map(proj, rt, ev, BK.LENS_ALL)
    sizes = sorted(c.size for c in m.clusters)
    check("components of the open pairs: {1,2,3}, {4,5}, {6,7,8,9}, and 13..24 (twelve images)", sizes == [2, 3, 4, 12], str(sizes))
    check("a resolved pair (Different) and a byte-identical pair join nothing",
          "18" not in m.of_pair and "17" not in m.of_pair and "12" not in m.of_image and "10" not in m.of_image)
    check("kinds: an isolated pair, two survey clusters (3 and 4 images), one larger cluster",
          m.counts == {SV.ISOLATED: 1, SV.SURVEY: 2, SV.OVERSIZED: 1})
    big = m.of_kind(SV.OVERSIZED)[0]
    check("the larger cluster is reported with its size and folder spread -- not paged, not ranked",
          big.size == 12 and big.folders == 3 and "12 images" in SV.oversized_text(big) and "more than Survey Mode shows" in SV.oversized_text(big))
    s = m.summary()
    check("the summary line names clusters of 3-8, isolated pairs and the larger ones",
          "2 clusters of 3-8 images" in s and "1 isolated pair" in s and "1 larger cluster" in s and "the largest 12 images" in s, s)
    check("clusters are numbered in the Show list's order", [c.number for c in m.clusters] == list(range(1, len(m.clusters) + 1)))
    c = m.of_image["6"]
    edges = SV.cluster_edges(proj, c.images)
    check("a cluster's pairs: every candidate pair among its images (6~7, 7~8, 8~6, 8~9)",
          sorted(p.pair_id for p in edges) == ["13", "14", "15", "16"])
    check("...and the image pairs with no candidate between them are named as such (6-9, 7-9)",
          sorted(tuple(sorted(x)) for x in SV.non_edges(c.images, edges)) == [("6", "9"), ("7", "9")])
    # A pair outside the Show list, between two of the cluster's images, is still one of its pairs.
    L2 = [img("1", r"C:\X\a.jpg"), img("2", r"C:\Y\b.jpg"), img("3", r"C:\Y\c.jpg")]
    P2 = [vp("1", "1", "2"), vp("2", "1", "3"), vp("3", "2", "3")]
    ev2, proj2, rt2 = world(L2, P2)
    m2 = SV.cluster_map(proj2, rt2, ev2, BK.LENS_ACROSS_FOLDERS)
    c2 = m2.clusters[0]
    check("Show: Across folders joins a~b and a~c (b~c is inside one folder), and the cluster's pairs still include b~c",
          c2.pair_ids == ("1", "2") and sorted(p.pair_id for p in SV.cluster_edges(proj2, c2.images)) == ["1", "2", "3"])
    # Determinism.
    rnd = random.Random(7)
    base = [(c.images, c.pair_ids) for c in m.clusters]
    same = True
    for _ in range(3):
        l2, d2 = list(L), list(decs)
        rnd.shuffle(l2)
        e3, p3, r3 = world(l2, P, d2)
        same &= [(c.images, c.pair_ids) for c in SV.cluster_map(p3, r3, e3, BK.LENS_ALL).clusters] == base
    check("the same record in any order gives the same clusters (3 shuffles)", same)
    check("a cluster is never a decision target: no decision kind, target kind or table names one",
          not any("cluster" in k for k in REG.DECISION_KINDS) and not any("cluster" in t for t in REG.TARGET_KINDS))


def _fixture(prefix):
    import p3_fixture_check as FX
    db = next(FIXTURES.glob(prefix + "*")) / "fixture.sqlite"
    ev, _oracle, _history = FX.load_fixture(db)
    return ev


def _f06():
    return _fixture("F06")


def _world_from(ev, pairs=None, decisions=None):
    from Phase3.model import Evidence
    e = Evidence(dict(ev.locations), {}, list(ev.decisions if decisions is None else decisions), [], [], {}, ev.now)
    e.pairs = dict(ev.pairs if pairs is None else pairs)
    proj = R.resolve_all(e)
    return e, proj, RT.route_all(e, proj, e.now)


def test_controls():
    section("B2. Group controls as Build 6 batches: F06 (as shipped, and in §34's shape), every control, exceptions, the chain guard")
    f06 = _f06()
    check("F06 as it ships: three images, THREE candidate pairs (A~C is a weak candidate) and A~C marked Different",
          sorted(f06.pairs) == ["PAB", "PAC", "PBC"] and {d.target_ref: d.kind for d in f06.decisions}["PAC"] == "different")

    # F06 as shipped, decisions as recorded: Mark Remaining Equivalent over the cluster.
    ev, proj, rt = _world_from(f06)
    imgs = ("A", "B", "C")
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, imgs, BK.LENS_ALL)
    c = {x.target_ref: x for x in pv.candidates}
    check("F06 as shipped: A~C stays Different (preserved, nothing recorded on it); A~B and B~C already equivalent get only Keep both",
          c["PAC"].disposition == PRE and c["PAC"].to_record == () and "Different" in c["PAC"].detail
          and [o[0] for o in c["PAB"].outcomes] == [SAT, D] and [k for k, *_ in c["PAB"].to_record] == ["keep_both"]
          and [k for k, *_ in c["PBC"].to_record] == ["keep_both"])

    # F06 undecided: the cluster's three real pairs are the population -- nothing more.
    ev, proj, rt = _world_from(f06, decisions=[])
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, imgs, BK.LENS_ALL)
    check("F06 undecided: exactly its three candidate pairs are the population, each gets its own two rows",
          sorted(rows(pv)) == ["PAB", "PAC", "PBC"] and pv.decision_count == 6)

    # §34's shape: A~B, B~C candidates, A~C NOT a candidate -- fixture F16 (B14; built in this
    # suite by B13 as F06 without its weak pair, now in the fixture lab), its decisions set aside.
    f16 = _fixture("F16")
    check("F16 is F06's images with A~B and B~C only: A~C is not a candidate pair",
          set(f16.locations) == set(f06.locations) and sorted(f16.pairs) == ["PAB", "PBC"])
    shape = f16.pairs
    ev, proj, rt = _world_from(f16, decisions=[])
    m = SV.cluster_map(proj, rt, ev, BK.LENS_ALL)
    cl = m.clusters[0]
    edges = SV.cluster_edges(proj, cl.images)
    check("§34's shape: A, B and C shown together (one cluster through B), two candidate pairs, A-C a non-edge",
          len(m.clusters) == 1 and set(cl.images) == {"A", "B", "C"} and [p.pair_id for p in edges] == ["PAB", "PBC"]
          and SV.non_edges(cl.images, edges) == [("A", "C")])
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, cl.images, BK.LENS_ALL)
    rels = [(ref, k) for ref, rr in rows(pv).items() for k, *_ in rr if k in REG.PAIR_RELATIONSHIPS]
    targets = {r for rr in rows(pv).values() for (_k, _tk, r, _v) in rr}
    check("Mark Remaining Equivalent writes exactly two relationship decisions, on A~B and B~C -- nothing across the missing A-C edge",
          sorted(rels) == [("PAB", "equivalent_copy"), ("PBC", "equivalent_copy")] and targets == {"PAB", "PBC"}
          and len(pv.candidates) == 2)
    ev2, proj2, rt2 = _world_from(f16, pairs=shape, decisions=[
        dec("D1", "equivalent_copy", "PAB"), dec("D2", "keep_both", "PAB", ["A", "B"]),
        dec("D3", "equivalent_copy", "PBC"), dec("D4", "keep_both", "PBC", ["B", "C"])])
    check("...and with both recorded, the resolver still infers nothing about A and C (no pair, no decision, no group)",
          all(p.pair_id in ("PAB", "PBC") for p in proj2.pairs) and not proj2.groups
          and not any(d.target_ref not in ("PAB", "PBC") for d in ev2.decisions))

    # A six-image cluster: every control's fan-out and its exceptions.
    L = [img(str(i), rf"C:\S\F{i}\{i}.jpg") for i in range(1, 8)]
    P = {"20": vp("20", "1", "2"), "21": vp("21", "1", "3"), "22": vp("22", "2", "3"), "23": vp("23", "3", "4"),
         "24": vp("24", "4", "5"), "25": vp("25", "5", "6"), "26": vp("26", "2", "6"), "27": vp("27", "4", "6"),
         "28": vp("28", "6", "7")}
    six = ("1", "2", "3", "4", "5", "6")
    ev, proj, rt = world(L, list(P.values()), [])
    edges = SV.cluster_edges(proj, six)
    check("a six-image cluster (7 is outside the view): the eight pairs among the six, not the pair to 7",
          sorted(p.pair_id for p in edges) == ["20", "21", "22", "23", "24", "25", "26", "27"])
    pv = SV.preview_group(ev, proj, rt, SV.KEEP_ALL, six, BK.LENS_ALL)
    check("Keep All: Keep both on each of the eight pairs (retention only -- the relationship stays each pair's question)",
          pv.action == "pairs_keep_both" and pv.decision_count == 8
          and all(rr == (("keep_both", PAIR, ref, sorted(P[ref].members, key=int)),) for ref, rr in rows(pv).items()))
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, six, BK.LENS_ALL)
    check("Mark Remaining Equivalent, no Canonical: Build 6's 'Equivalent copy, keep both' over the eight pairs",
          pv.action == "pairs_equivalent_keep_both" and pv.decision_count == 16)
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, six, BK.LENS_ALL, canonical="3", keepers=["4"])
    r = rows(pv)
    check("...with Canonical 3 and Keeper 4: the pairs with 3 prefer 3 (1~3, 2~3), 3~4 keeps both (4 is a Keeper), the rest keep both",
          pv.action == "pairs_equivalent_prefer" and r["21"][1] == ("canonical_location", PAIR, "21", "3")
          and r["22"][1] == ("canonical_location", PAIR, "22", "3") and r["23"][1][0] == "keep_both"
          and all(r[x][1][0] == "keep_both" for x in ("20", "24", "25", "26", "27")) and all(r[x][0][0] == "equivalent_copy" for x in r))
    check("...the words say which rows it records", any("Set canonical (pairs with the Canonical) or Keep both" in l for l in pv.lines()))
    pv = SV.preview_group(ev, proj, rt, SV.SELECTED_RELATED, six, BK.LENS_ALL, selected=["6"])
    check("Mark Selected as Related Variant (6 selected): the pairs joining 6 to the rest -- 5~6, 2~6, 4~6 -- and no other",
          pv.action == "pairs_related_keep_both" and sorted(rows(pv)) == ["25", "26", "27"])
    pv = SV.preview_group(ev, proj, rt, SV.SELECTED_RELATED, six, BK.LENS_ALL, selected=["4", "6"])
    check("...4 and 6 selected: the pairs with exactly one selected image; 4~6 (both selected) is left as it is",
          sorted(rows(pv)) == ["23", "24", "25", "26"] and "27" not in rows(pv))
    check("...nothing selected, or everything selected: refused with the reason",
          refused(lambda: SV.preview_group(ev, proj, rt, SV.SELECTED_RELATED, six, BK.LENS_ALL))
          and refused(lambda: SV.preview_group(ev, proj, rt, SV.SELECTED_RELATED, six, BK.LENS_ALL, selected=six)))
    pv = SV.preview_group(ev, proj, rt, SV.DEFER_GROUP, six, BK.LENS_ALL, disposition="snooze_until_evidence_change")
    check("Defer Group: one deferral per pair, the per-pair mechanism fanned out, no decision",
          pv.action == "pairs_defer" and pv.event_count == 8 and pv.decision_count == 0
          and all(c.events_to_record == (("snooze_until_evidence_change", None),) for c in pv.candidates))
    check("...a disposition a pair does not offer is refused (fingerprints are a group's return trigger)",
          refused(lambda: SV.preview_group(ev, proj, rt, SV.DEFER_GROUP, six, BK.LENS_ALL, disposition="snooze_until_hash_current")))
    pv = SV.preview_group(ev, proj, rt, SV.KEEP_ALL, ("1", "2", "3", "4", "5"), BK.LENS_ALL, excluded=["6"])
    check("Exclude 6: the population shrinks to the pairs among the images left (20-24), and nothing is planned about 6",
          sorted(rows(pv)) == ["20", "21", "22", "23", "24"] and pv.parameters["survey"]["excluded"] == ["6"])

    # What stands is preserved; the chain guard excludes; blocked is blocked.
    L[4] = replace(L[4], cloud_only=True)                                   # 5 is cloud-only
    decs = [dec("D1", "different", "20"),                                     # 1~2 Different
            dec("D2", "related_variant", "22"),                               # 2~3 related
            dec("D3", "equivalent_copy", "23"), dec("D4", "keep_both", "23", ["3", "4"]),   # 3~4 answered
            dec("D5", "equivalent_copy", "28"), dec("D6", "canonical_location", "28", "7")]  # outside: 6~7 gives up 6
    ev, proj, rt = world(L, list(P.values()), decs, events=[_evt("E1", "26")])  # 2~6 deferred by hand
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, six, BK.LENS_ALL, canonical="6")
    d = disp(pv)
    check("Mark Remaining Equivalent over what stands: Different, related and the deferral preserved; the answered pair satisfied",
          d["20"] == PRE and d["22"] == PRE and d["26"] == PRE and d["23"] == SAT, str(d))
    check("...pairs touching cloud-only 5 blocked", d["24"] == BLK and d["25"] == BLK, str(d))
    check("...4~6 would keep 6 while 6~7 (outside the view) gives 6 up: the chain guard withholds both its rows",
          d["27"] == CON and "never inferred along a chain" in {x.target_ref: x for x in pv.candidates}["27"].detail, str(d))
    check("...1~3 is the one pair decided", [x for x in d if d[x] == D] == ["21"])
    pv = SV.preview_group(ev, proj, rt, SV.KEEP_ALL, six, BK.LENS_ALL)
    d = disp(pv)
    check("Keep All over it: Different preserved (retention would contradict it), the answered pair satisfied",
          d["20"] == PRE and "Different" in {x.target_ref: x for x in pv.candidates}["20"].detail and d["23"] == SAT)
    pv = SV.preview_group(ev, proj, rt, SV.DEFER_GROUP, six, BK.LENS_ALL)
    d = disp(pv)
    check("Defer Group over it: decided pairs not applicable, the deferred pair already satisfied, the undecided pairs deferred",
          d["20"] == NA and d["22"] == NA and d["23"] == NA and d["26"] == SAT and all(d[x] == D for x in ("21", "24", "25", "27")), str(d))
    # Keep both preserved against a Canonical row.
    ev, proj, rt = world(L, list(P.values()), [dec("D1", "equivalent_copy", "21"), dec("D2", "keep_both", "21", ["1", "3"])])
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, six, BK.LENS_ALL, canonical="3")
    check("a pair that keeps both is not turned into a preference: the Canonical row is preserved, nothing recorded on it",
          disp(pv)["21"] == PRE and rows(pv)["21"] == () and "Keep both" in {x.target_ref: x for x in pv.candidates}["21"].detail)
    check("the Build 6 Bulk dialogs offer none of Survey Mode's own actions",
          [a.key for a in REG.bulk_actions_for([PAIR])] == ["pairs_related_keep_both", "pairs_equivalent_keep_both", "pairs_different"])


def test_neighbourhood():
    section("B2b. The batch simulation over the batch's neighbourhood equals the whole-model simulation (the speed-up is exact)")
    import time
    rnd = random.Random(11)
    actions = ["pairs_related_keep_both", "pairs_equivalent_keep_both", "pairs_different", "pairs_keep_both",
               "pairs_equivalent_prefer"]
    kinds = [("equivalent_copy", None), ("related_variant", None), ("different", None), ("canonical", None), ("keep_both", None)]
    same, trials, with_conflict = True, 0, 0
    for trial in range(120):
        n = rnd.randrange(6, 40)
        L = [img(str(i), rf"C:\N\F{i % 4}\{i}.jpg") for i in range(1, n + 1)]
        seen, P = set(), []
        for k in range(rnd.randrange(n, 3 * n)):
            a, b = rnd.sample(range(1, n + 1), 2)
            key = frozenset((a, b))
            if key not in seen:
                seen.add(key)
                P.append(vp(str(1000 + k), str(a), str(b)))
        decs, dn = [], 0
        for p in rnd.sample(P, len(P) // 2):
            what = rnd.choice(kinds)[0]
            dn += 1
            if what == "canonical":
                decs += [dec(f"D{dn}", "equivalent_copy", p.pair_id), dec(f"D{dn + 5000}", "canonical_location", p.pair_id,
                                                                            rnd.choice(p.members))]
            elif what == "keep_both":
                decs.append(dec(f"D{dn}", "keep_both", p.pair_id, sorted(p.members, key=int)))
            else:
                decs.append(dec(f"D{dn}", what, p.pair_id))
        ev, proj, rt = world(L, P, decs)
        action = rnd.choice(actions)
        batch = rnd.sample([p.pair_id for p in P], rnd.randrange(1, min(8, len(P)) + 1))
        params = {"prefer": rnd.choice(rnd.choice(P).members), "keepers": []} if action == "pairs_equivalent_prefer" else {}
        pps = [proj.pair(i) for i in batch]
        c1, c2 = BK.plan_pairs(action, pps, ev, params), BK.plan_pairs(action, pps, ev, params)
        BK.classify_pairs(c1, ev, proj, rt)
        BK.classify_pairs(c2, ev, proj, rt, _whole=True)
        a = [(c.target_ref, c.disposition, c.outcomes) for c in c1]
        b = [(c.target_ref, c.disposition, c.outcomes) for c in c2]
        same &= a == b
        trials += 1
        with_conflict += any(c.disposition == CON for c in c1)
    check(f"{trials} random graphs and batches (every pair action, prefer included): identical buckets and reasons "
          f"({with_conflict} of them with a conflict)", same and with_conflict >= 10, f"conflict cases {with_conflict}")
    # Speed on a large model: a six-pair batch no longer re-resolves every pair.
    many = [img(str(i), rf"C:\Big\{i // 150}\{i}.jpg") for i in range(1, 12001)]
    mp = [vp(str(100000 + i), str(i), str(i + 1)) for i in range(1, 12000)]
    mp += [vp(str(200000 + i), str(i), str(i + 2)) for i in range(1, 11999)]
    md = [dec(f"D{i}", "related_variant", str(100000 + i)) for i in range(1, 12000, 2)]
    ev, proj, rt = world(many, mp, md)
    six = [str(i) for i in range(500, 504)]
    t0 = time.perf_counter()
    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, six, BK.LENS_ALL)
    took = time.perf_counter() - t0
    check(f"a Survey preview over a 24,000-pair model: {took * 1000:.0f} ms (< 500 ms)", took < 0.5 and pv.candidates, f"{took:.2f}s")


def test_project(tmp: Path):
    section("B3. A real project: the cluster the hashes make, a group control committed, undo, the guard, zero mutation")
    from Phase2.runner import RunRequest, RunWorker, PRESCAN
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S

    app_root, corpus, r1, r2, project_dir, outs = T.open_project(tmp, "P3Survey")
    check("Pre-Scan, Find My Duplicates, Analyze (pictures)", all(o.ok for o in outs))
    before = BASE.fingerprint_tree(corpus)
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:survey")

    def state():
        ev = E.load_evidence(conn)
        proj = R.resolve_all(ev)
        return ev, proj, RT.route_all(ev, proj, ev.now)
    ev, proj, rt = state()
    by = {BK.file_name(l.path): l.location_id for l in ev.locations.values()}
    m = SV.cluster_map(proj, rt, ev, RT.PAIR_QUEUE_WORD)
    a_cluster = m.of_image.get(by["A.jpg"])
    names = sorted(BK.file_name(ev.locations[i].path) for i in a_cluster.images) if a_cluster else []
    check("the queue's clusters: the A photograph's copies make one cluster; its byte copy joins through its own look-alike pairs",
          a_cluster is not None and a_cluster.kind == SV.SURVEY and {"A.jpg", "A_small.jpg", "A_bw.jpg"} <= set(names), str(names))
    edges = SV.cluster_edges(proj, a_cluster.images)
    check("...its pairs exclude the byte-identical pair (the exact-duplicate review answers that one)",
          edges and not any(p.exact_content for p in edges))
    c_cluster = m.of_image.get(by["C.jpg"])
    check("C and its edit: an isolated pair -- Compare Mode's case, not Survey Mode's", c_cluster is not None and c_cluster.kind == SV.ISOLATED)

    pv = SV.preview_group(ev, proj, rt, SV.REMAINING_EQUIVALENT, a_cluster.images, RT.PAIR_QUEUE_WORD,
                          canonical=by["A.jpg"], conn=conn)
    check("a Survey preview carries the record and evidence marks like any batch", pv.record_mark is not None and pv.evidence_mark)
    ops0 = st.counts()["operations"]
    with BASE.WriteGuard([project_dir, app_root / "Logs"]) as guard:
        res = st.commit_bulk(pv, note="Survey Mode: Mark Remaining Equivalent")
    b = conn.execute("SELECT * FROM p3_bulk_batch WHERE batch_id = ?", (res["batch_id"],)).fetchone()
    params = json.loads(b["parameters_json"])
    check("one operation: the batch, a member row per cluster pair, the decisions", st.counts()["operations"] == ops0 + 1
          and res["members"] == len(edges) and res["decisions"] == pv.decision_count)
    check("the batch is an explicit selection of the cluster's pairs, and notes the cluster it was over (a note, not a decision target)",
          b["scope_kind"] == REG.SCOPE_EXPLICIT_SELECTION and sorted(params["selection"]) == sorted(p.pair_id for p in edges)
          and params["survey"]["control"] == "Mark Remaining Equivalent" and sorted(params["survey"]["images"]) == sorted(a_cluster.images)
          and params["prefer"] == by["A.jpg"])
    drows = conn.execute("SELECT * FROM p3_decision WHERE origin_ref = ?", (str(res["batch_id"]),)).fetchall()
    check("every decision names one pair, carries batch provenance and the pair's evidence binding",
          drows and all(r["target_kind"] == PAIR and r["origin_kind"] == "bulk_explicit_human"
                        and json.loads(r["evidence_binding_json"])["target_kind"] == PAIR for r in drows))
    canon = [(r["target_ref"], json.loads(r["value_json"])) for r in drows if r["decision_kind"] == "canonical_location"]
    check("the pairs with A prefer A; no row prefers anything else", canon and all(v == by["A.jpg"] for _r, v in canon))
    gaps = SV.non_edges(a_cluster.images, edges, proj)
    check("the byte-identical pair is not called a non-candidate (it is a candidate the exact-duplicate review answers)",
          frozenset((by["A.jpg"], by["A_copy.jpg"])) not in {frozenset(g) for g in gaps}
          and frozenset((by["A.jpg"], by["A_copy.jpg"])) in {frozenset(g) for g in SV.non_edges(a_cluster.images, edges)})
    check("no Python-level write outside the project folder while it was recorded", not guard.violations, str(guard.violations[:3]))
    labels = {bb["batch_id"]: bb for bb in st.batches()}
    check("Batches... words: 'Survey Mode: Mark Remaining Equivalent over a cluster of N images'",
          "Survey Mode: Mark Remaining Equivalent over a cluster of" in BK.describe_query(None, labels[res["batch_id"]]["parameters"]))
    ev, proj, rt = state()
    check("afterwards the cluster's decided pairs are answered (Ready for Plan or Resolved), none in the queue",
          all(rt.pairs[r].primary in (RT.READY_FOR_PLAN, RT.RESOLVED) for r in {x["target_ref"] for x in drows}))
    journal = project_dir / "Exports" / "DecisionJournal.txt"
    (project_dir / "Exports").mkdir(exist_ok=True)
    S.export_decision_journal(conn, journal)
    check("the journal lists the batch", f"bulk batch #{res['batch_id']}" in journal.read_text(encoding="utf-8"))
    u = st.undo_batch(res["batch_id"])
    ev, proj, rt = state()
    check("undo at the action's scope: one operation withdraws the batch's decisions; the pairs are back in the queue",
          u["withdrawn"] == res["decisions"] and all(rt.pairs[p.pair_id].primary == RT.UNRESOLVED for p in edges))

    # Defer Group, undone: its deferrals are restored.
    pv = SV.preview_group(ev, proj, rt, SV.DEFER_GROUP, a_cluster.images, RT.PAIR_QUEUE_WORD, conn=conn)
    res = st.commit_bulk(pv)
    ev, proj, rt = state()
    check("Defer Group: each pair deferred (a per-pair event), none decided", res["events"] == len(edges) and res["decisions"] == 0
          and all(rt.pairs[p.pair_id].primary == RT.DEFERRED for p in edges))
    u = st.undo_batch(res["batch_id"])
    ev, proj, rt = state()
    check("...undone: the deferrals restored, the pairs back in the queue", u["restored"] == len(edges)
          and all(rt.pairs[p.pair_id].primary == RT.UNRESOLVED for p in edges))

    # The guard, on a Survey preview.
    pv = SV.preview_group(ev, proj, rt, SV.KEEP_ALL, a_cluster.images, RT.PAIR_QUEUE_WORD, conn=conn)
    conn.close()
    RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:survey")
    check("a rescan between a Survey preview and its commit refuses it", refused(lambda: st.commit_bulk(pv)))
    conn.close()
    check("every corpus file untouched", BASE.fingerprint_tree(corpus) == before)


def test_gui(tmp: Path):
    section("B4. The window: Survey Mode..., tiles, per-image controls, Exclude records nothing, Compare, a group control, Undo")
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

    app_root, corpus, r1, r2, project_dir, outs = T.open_project(tmp, "P3SurveyGui")
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
            app.open_project(project_dir)
            app.show_similar()
            app.update()
            page = app.similar_page
            st = RV.store_for(app)
            by = {BK.file_name(l.path): l.location_id for l in page.ev.locations.values()}
            c_pair = next(p.pair_id for p in page.proj.pairs if set(p.members) == {by["C.jpg"], by["C_edit.jpg"]})
            page.show_pair(c_pair)
            page.survey_mode()
            win = page.survey_window
            check("Survey Mode... from an isolated pair opens the list's first 3-8 cluster and says why",
                  win is not None and "isolated pair" in win.status_var.get() and 3 <= len(win.images) <= 8)
            check("the window names the list's clusters (Survey Mode's reach, isolated pairs)",
                  "cluster" in win.summary_var.get() and "isolated pair" in win.summary_var.get())
            check("one tile per image, the first Active", len(win.tiles) == len(win.images) and win.active == win.images[0]
                  and "Active" in win.tiles[win.active]["role"].cget("text"))
            check("the pair list shows the cluster's candidate pairs", len(win.pairs.get_children()) == len(win.edges) > 0)
            ops0, dec0 = st.counts()["operations"], st.counts()["decisions"]
            a_id = by["A.jpg"]
            win.toggle_canonical(a_id)
            win.toggle_keeper(win.images[-1])
            win.toggle_selected(win.images[-1])
            check("Keeper / Canonical / Select are view state: shown on the tiles, nothing recorded",
                  win.canonical == a_id and win.images[-1] in win.keepers and "Canonical" in win.tiles[a_id]["role"].cget("text")
                  and st.counts()["operations"] == ops0)
            victim = next(i for i in win.images if i != a_id and i != win.images[-1])
            n_edges = len(win.edges)
            win.exclude(victim)
            check("Exclude takes an image out of the view and its pairs out of the population -- and records NOTHING",
                  victim not in win.images and victim in win.excluded and len(win.edges) < n_edges
                  and st.counts()["operations"] == ops0 and st.counts()["decisions"] == dec0 and "nothing was recorded" in win.status_var.get())
            win.restore_excluded()
            check("Restore excluded brings it back", victim in win.images and not win.excluded)
            win.set_active(a_id)
            other = next(i for i in win.images if i != a_id and any(set(p.members) == {a_id, i} for p in win.edges))
            win.compare_with_active(other)
            check("Compare: the page's Compare Mode on the Active image and this one (their candidate pair)",
                  not page.compare_only and set(page.proj.pair(page.selected).members) == {a_id, other})
            gap = next(((x, y) for x, y in SV.non_edges(win.images, win.edges, page.proj)), None)
            if gap is not None:
                win.set_active(gap[0])
                win.compare_with_active(gap[1])
                check("...two images with no candidate pair: side by side, no pair decision on offer",
                      page.compare_only and str(page.buttons["equivalent_both"].cget("state")) == "disabled")
                win.set_active(a_id)
            queue0 = page.routing.pair_counts[RT.UNRESOLVED]
            win.run_preview(SV.REMAINING_EQUIVALENT)
            text = win.preview_text.get("1.0", "end")
            check("a group control previews first: R4's buckets and the promise; Record names the count",
                  "Mark Remaining Equivalent" in text and "No source files will be changed." in text
                  and str(win.ok.cget("state")) == "normal" and win.ok.cget("text").startswith("Record "))
            win.toggle_keeper(win.images[0] if win.images[0] != a_id else win.images[1])
            check("changing a Keeper voids the preview", str(win.ok.cget("state")) == "disabled" and win.preview is None)
            win.run_preview(SV.REMAINING_EQUIVALENT)
            n_dec = win.preview.counts[REG.DISP_DECIDED]
            win.record()
            app.update()
            check("Record: one batch; the queue shrinks by the pairs decided",
                  st.counts()["batches"] == 1 and page.routing.pair_counts[RT.UNRESOLVED] == queue0 - n_dec
                  and "recorded" in win.status_var.get(), f"{queue0} -> {page.routing.pair_counts[RT.UNRESOLVED]} ({n_dec})")
            win.undo_last()
            app.update()
            check("Undo the last group action: the pairs are back", page.routing.pair_counts[RT.UNRESOLVED] == queue0)
            win.run_preview(SV.SELECTED_RELATED)
            check("Mark Selected as Related Variant says which pairs it covers",
                  "exactly" not in win.preview_text.get("1.0", "end") and "joining a selected image" in win.preview_text.get("1.0", "end")
                  or "no candidate pair joins" in win.preview_text.get("1.0", "end"))
            win.next_cluster()
            check("Next cluster: none other waits in this small collection -- said, not guessed",
                  "No other cluster" in win.status_var.get())
            win.win.destroy()
            # A Show list with no 3-8 cluster: the reason is said, no window opens.
            page.filter_var.set("Deferred / Snoozed")                 # nothing is deferred: an empty list
            page.refresh_list()
            n_info = len(asked)
            page.survey_mode()
            check("a list with no cluster of 3-8 images: Survey Mode says so and opens nothing",
                  page.survey_window is None and len(asked) == n_info + 1 and "No cluster of 3-8 images" in asked[-1][2])
            app.destroy()
        check("no Python-level write outside the project and log folders", not guard.violations, str(guard.violations[:3]))
        check("every corpus file untouched", BASE.fingerprint_tree(corpus) == before)
        check("no unexpected error dialog", not [a for a in asked if a[0] == "showerror"], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)


# ---------------------------------------------------------------------------

def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3survey_"))
    try:
        test_group_guard(tmp)
        test_clusters()
        test_controls()
        test_neighbourhood()
        test_project(tmp)
        test_gui(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 SURVEY (Build 7): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
