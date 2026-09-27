#!/usr/bin/env python3
r"""B18 regression: what counts as a material change for a metadata decision,
bulk Confirm on the Metadata queue, and the queue's session cache (tasks 2, 4).

What it proves, in order:

  1. The rule, on the pure model (synthesis §2): a decision needs revalidation
     only when what the person decided about changed -- the file's content
     identity, the field's observed value, or its value-state. A re-read with
     the same value, a new observation with the same content hash, and an
     integrity change are NOT material for the decision; integrity still
     re-checks plans (a locked revision goes obsolete, "integrity evidence
     changed"). The same rule governs Leave Unchanged's return, the "until the
     evidence changes" deferral, and metadata-item obsolescence. B16/B17-shaped
     bindings (analyzer-result ids only) compare by the values the ids resolve
     to; an unresolvable one falls back to ids -- never silently current.
  2. On a real project through the real analyzers: a re-analysis of unchanged
     files drifts nothing and obsoletes nothing (B17 sent every decision back);
     an integrity change alone keeps the decision current and obsoletes the
     locked revision; a value change with the content unchanged, a value-state
     change, and an edited file each drift.
  3. Bulk Confirm, store level: the preview's buckets; the commit records
     exactly the preview (same kind, same value, a fresh binding, origin
     bulk_explicit_human with the batch id); it refuses after the record or
     the evidence moved; Undo puts every question back exactly as it was; a
     Clear or a Leave Unchanged is never turned into anything else, a desired
     value never changed, a question without a decision never confirmed.
  4. The queue page, headless: the check column, Confirm..., Batches... and
     Undo through the window; zero source reads and writes.
  5. The session cache (refresh.MetadataCache): after every step of seeded
     random sessions -- decisions, withdrawals, deferrals, restores, new
     questions, Confirm batches and their Undo, re-analyses -- the cached slice
     equals a fresh load, and the review rows are identical.

Run:  python Scripts\p3_revalidation_check.py
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_metadata_check as MC                                                # noqa: E402  (mq, mdec, with_metadata, routed)
import p3_metadata_view_check as MV                                           # noqa: E402  (docx_file, pdf_with)
PC, BASE = MC.PC, MC.BASE                                                     # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import plans as PL                                               # noqa: E402
from Phase3 import metadata as MD                                            # noqa: E402
from Phase3.model import ReviewEvent                                         # noqa: E402

RESULTS = []
LOCATION, MQ = REG.LOCATION, REG.METADATA_QUESTION
META = REG.ACTION_METADATA


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


mq, mdec, with_metadata, routed, gloc = MC.mq, MC.mdec, MC.with_metadata, MC.routed, MC.gloc


# ---------------------------------------------------------------------------
# 1. The rule, on the pure model
# ---------------------------------------------------------------------------

def test_rule():
    section("1. The material-change rule (pure): judge by what changed, never by a newer row")
    q0 = mq("M1", "1", ids=("1",), obs="obs-1", value="Alice")
    b18 = {"observation_id": "obs-1", "content_id": "C1", "analyzer_result_ids": ["1"], "observed_state": "known",
           "observed_value": "Alice", "observed_values": ["Alice"]}
    b17 = {"observation_id": "obs-1", "analyzer_result_ids": ["1"], "observed_state": "known"}
    b17r = dict(b17, resolved_values=["Alice"])                        # as evidence.resolve_bound_values leaves it
    q0 = replace(q0, content_id="C1")
    reread = replace(q0, analyzer_result_ids=("9",))
    drift = lambda q, b: MD.binding_drift(q, b)                         # noqa: E731
    check("a re-read with the SAME value and state (a new analyzer row): no drift -- B18 binding, and a B16/B17 binding "
          "whose ids resolved to that value", drift(reread, b18) == () and drift(reread, b17r) == ())
    check("...a B16/B17 binding whose ids could NOT be resolved falls back to comparing ids: it drifts (never silently current)",
          drift(reread, b17) != () and "no longer in the database" in drift(reread, b17)[0])
    check("...and with nothing re-read, the unresolved B17 binding does not drift either", drift(q0, b17) == ())
    changed = replace(reread, observed_value="Carol", observed_values=("Carol",))
    check("the observed VALUE changed, the file's content not (same observation): drift, for every binding shape",
          all(drift(changed, b) and "observed value changed" in drift(changed, b)[0] for b in (b18, b17r))
          and "'Alice' -> 'Carol'" in drift(changed, b18)[0])
    conflicting = replace(reread, observed_state="conflicting", observed_value=None, observed_values=("Alice", "Carol"))
    unavailable = replace(reread, observed_state="unavailable", observed_value=None, observed_values=())
    check("the VALUE-STATE changed (known -> conflicting; known -> unavailable): drift",
          "observed state changed (known -> conflicting)" in drift(conflicting, b18)[0]
          and "observed state changed (known -> unavailable)" in drift(unavailable, b18)[0])
    same_content = replace(q0, observation_id="obs-2", analyzer_result_ids=("9",))
    check("a new observation with the SAME authoritative content hash (times touched, bytes identical): not material",
          drift(same_content, b18) == ())
    for what, q in (("a different content hash", replace(same_content, content_id="C2")),
                    ("no authoritative content hash", replace(same_content, content_id=None))):
        check(f"a new observation with {what}: the file changed -- drift", "the file changed since" in (drift(q, b18) or ("",))[0])
    check("a B16/B17 binding (no content id) with a new observation: drift (conservative -- it cannot prove the same bytes)",
          "the file changed since" in (drift(same_content, b17r) or ("",))[0])
    check("gone from the current evidence: drift", drift(replace(q0, observation_id=None), b18) == ("no longer in the current evidence",))
    integ = replace(reread, signed_or_certified=True, encrypted=True, hardlinked=True)
    check("an INTEGRITY change alone (now signed, encrypted, hard-linked): not material for the decision", drift(integ, b18) == ())

    # Leave Unchanged, the deferral trigger, the review state -- the same rule.
    L = [gloc("1", r"C:\D\report.docx", content=None, phys="P1")]
    ev, proj, rt = routed(L, [])
    with_metadata(ev, [reread], [mdec("E1", "leave_source_metadata_unchanged", "M1", binding=b18)])
    rows = MD.review(ev)
    check("Leave Unchanged after a same-value re-read: stays Left unchanged, out of the queue (R8 §53)",
          rows[0].review_state == MD.LEFT_UNCHANGED and not MD.queue(ev))
    ev, proj, rt = routed(L, [])
    with_metadata(ev, [changed], [mdec("E1", "leave_source_metadata_unchanged", "M1", binding=b18)])
    check("...after the value changed: needs revalidation, back in the queue",
          MD.review(ev)[0].review_state == MD.NEEDS_REVALIDATION and len(MD.queue(ev)) == 1)
    e = ReviewEvent("R1", MQ, "M1", "deferred", "evidence_change", "until the evidence changes", None, dict(b18))
    check("the 'until the evidence changes' deferral: a same-value re-read does not fire it; a value change does; an "
          "integrity change does not", (MD.deferral_fired(reread, e, "2026"), MD.deferral_fired(changed, e, "2026"),
                                        MD.deferral_fired(integ, e, "2026")) == (False, True, False))

    # Metadata-item obsolescence: its previewed inputs, not newer rows.
    ev, proj, rt = routed(L, [])
    with_metadata(ev, [q0], [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", binding=b18)])
    locked = PL.build(ev, proj, rt).items

    def reasons(q):
        e2, p2, r2 = routed(L, [])
        with_metadata(e2, [q], [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", binding=b18)])
        return [c for c, _w, _d in PL.obsolescence(locked, e2, p2, r2)]
    check("a locked metadata item after a same-value re-read: still stands (B17 obsoleted it)", reasons(reread) == [])
    check("...after the value changed, or its value-state: evidence changed",
          reasons(changed) == ["evidence_changed"] and reasons(conflicting) == ["evidence_changed"])
    check("...after an integrity change alone: integrity evidence changed -- plans still re-check (the decision does not)",
          reasons(replace(reread, signed_or_certified=True)) == ["integrity_changed"])
    e3, p3, r3 = routed([replace(gloc("1", r"C:\D\report.docx", content=None, phys="P1"), cloud_only=True)], [])
    with_metadata(e3, [reread], [mdec("E1", "desired_source_metadata_set", "M1", "Thomas", binding=b18)])
    check("...a cloud-only target now (it was local when previewed): evidence changed",
          [c for c, _w, _d in PL.obsolescence(locked, e3, p3, r3)] == ["evidence_changed"])
    it = locked[0]
    check("a B16/B17 locked item (no values beyond its estimated effect) compares by the estimated effect it froze",
          it.estimated_effect.get("observed_values") == ["Alice"] or list(it.estimated_effect.get("observed_values")) == ["Alice"])


# ---------------------------------------------------------------------------
# helpers for the real project
# ---------------------------------------------------------------------------

def synth(conn, fid, key, **cols):
    """A synthetic analyzer reading on a file's CURRENT observation, as a new
    run of analyzer `key` would record it -- a copy of the newest real row on
    that observation with `cols` changed. The file itself is not touched."""
    obs = conn.execute("SELECT current_observation_id FROM file_state WHERE file_path_id = ?", (int(fid),)).fetchone()[0]
    tmpl = conn.execute("SELECT ar.analyzer_result_id, ar.analyzer_run_id FROM analyzer_result ar WHERE ar.file_observation_id = ? "
                        "ORDER BY ar.analyzer_result_id DESC LIMIT 1", (obs,)).fetchone()
    old_run = conn.execute("SELECT run_id FROM analyzer_run WHERE analyzer_run_id = ?", (tmpl[1],)).fetchone()[0]
    rc = [c[1] for c in conn.execute("PRAGMA table_info(run)") if c[1] not in ("run_id", "run_uid")]
    new_run = conn.execute(f"INSERT INTO run (run_uid, {', '.join(rc)}) SELECT ?, {', '.join(rc)} FROM run WHERE run_id = ?",
                           (str(uuid.uuid4()), old_run)).lastrowid
    aid = conn.execute("SELECT analyzer_id FROM analyzer WHERE analyzer_key = ?", (key,)).fetchone()[0]
    ac = [c[1] for c in conn.execute("PRAGMA table_info(analyzer_run)") if c[1] not in ("analyzer_run_id", "run_id", "analyzer_id",
                                                                                     "run_stage_id", "ingest_run_stage_id")]
    new_ar = conn.execute(f"INSERT INTO analyzer_run (run_id, analyzer_id, {', '.join(ac)}) SELECT ?, ?, {', '.join(ac)} "
                          "FROM analyzer_run WHERE analyzer_run_id = ?", (new_run, aid, tmpl[1])).lastrowid
    rc2 = [c[1] for c in conn.execute("PRAGMA table_info(analyzer_result)") if c[1] not in ("analyzer_result_id", "analyzer_run_id")]
    sets = ", ".join(f"{c}" if c not in cols else "?" for c in rc2)
    args = [cols[c] for c in rc2 if c in cols]
    rid = conn.execute(f"INSERT INTO analyzer_result (analyzer_run_id, {', '.join(rc2)}) SELECT ?, {sets} FROM analyzer_result "
                       "WHERE analyzer_result_id = ?", [new_ar] + args + [tmpl[0]]).lastrowid
    conn.commit()
    return rid


def states(conn):
    """question id -> (review state, drift) as the Metadata queue reads them now."""
    from Phase3 import evidence as E
    from Phase3.model import Evidence
    from Phase2.core import utc_now
    ev = Evidence({}, {}, [], [], now=utc_now())
    ev.metadata = E.load_metadata(conn)
    return {r.question_id: r for r in MD.review(ev)}, ev


def model(conn):
    from Phase3 import evidence as E, resolve as R, routing as RT
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    return ev, proj, RT.route_all(ev, proj, ev.now)


def lock_now(st, conn):
    ev, proj, rt = model(conn)
    rec = st.record_plan_revision(PL.build(ev, proj, rt), name="locked for B18")
    st.lock_plan_revision(rec["plan_revision_id"])
    return rec["plan_revision_id"]


def reconcile(st, conn):
    ev, proj, rt = model(conn)
    found = st.reconcile_plans(ev, proj, rt)
    out = {}
    for rid in found:
        rev, _items = st.plan_revision(rid)
        out[rid] = sorted({r["code"] for e in rev["events"] if e["event_kind"] == "obsolete" for r in e["detail"]["reasons"]})
    return out


# ---------------------------------------------------------------------------
# 2-3. The real project: the rule through the real analyzers, and bulk Confirm
# ---------------------------------------------------------------------------

def test_project(tmp: Path):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    from Phase2.core import connect
    from Phase3 import store as S, bulk as BK

    section("2. The rule on a real project, through the real analyzers")
    app_root = tmp / "RevalRoot"
    (app_root / "Projects").mkdir(parents=True)
    docs = tmp / "RevalCorpus" / "Docs"
    docs.mkdir(parents=True)
    for name, author in (("a", "Alice"), ("b", "Bob"), ("d", "Dan"), ("e", "Eve"), ("f", "Fay"), ("g", "Gus"), ("h", "Hal")):
        MV.docx_file(docs / f"{name}.docx", author, f"The {name} document.")
    MV.pdf_with(docs / "c.pdf", author="Carol")
    project_dir = app_root / "Projects" / "Reval"
    ok = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs)], project_name="Reval")).run().ok,
          RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run().ok,
          RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["pdf", "office"])).run().ok]
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:reval")
    by = {r[1].replace("/", "\\").rsplit("\\", 1)[-1]: str(r[0]) for r in conn.execute("SELECT file_path_id, relative_path FROM file_path")}
    rec = {}
    rec["a"] = st.record_metadata_decision("desired_source_metadata_set", by["a.docx"], "creator", "Thomas")
    rec["b"] = st.record_metadata_decision("desired_source_metadata_clear", by["b.docx"], "creator", True)
    rec["c"] = st.record_metadata_decision("leave_source_metadata_unchanged", by["c.pdf"], "creator", True)
    rec["d"] = st.record_metadata_decision("desired_source_metadata_set", by["d.docx"], "creator", "Thomas")
    rec["e"] = st.defer_metadata(by["e.docx"], "creator", "snooze_until_evidence_change")
    rec["f"] = st.record_metadata_decision("desired_source_metadata_set", by["f.docx"], "creator", "Thomas")
    rec["g"] = st.record_metadata_decision("leave_source_metadata_unchanged", by["g.docx"], "creator", True)
    rec["h"] = st.record_metadata_decision("desired_source_metadata_clear", by["h.docx"], "creator", True)
    title_a = st.metadata_question(by["a.docx"], "title", create=True)
    q = {k: str(v["question_id"]) for k, v in rec.items()}
    # d's binding as B16/B17 wrote it: ids only.
    b = json.loads(conn.execute("SELECT evidence_binding_json FROM p3_decision WHERE decision_id = ?", (rec["d"]["decision_id"],)).fetchone()[0])
    for k in ("content_id", "observed_value", "observed_values"):
        b.pop(k)
    conn.execute("UPDATE p3_decision SET evidence_binding_json = ? WHERE decision_id = ?", (json.dumps(b, sort_keys=True), rec["d"]["decision_id"]))
    conn.commit()
    check("the project: Pre-Scan, Find My Duplicates, the PDF + Office analyzers; decisions of every source kind, one binding "
          "in B17's shape, a deferral until the evidence changes, an undecided question", all(ok) and set(b) == {
              "schema", "target_kind", "question_id", "file_path_id", "semantic_field", "observation_id", "analyzer_result_ids",
              "observed_state"})
    rev1 = lock_now(st, conn)

    max_ar = conn.execute("SELECT MAX(analyzer_result_id) FROM analyzer_result").fetchone()[0]
    ok = RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze again", analyzer_keys=["pdf", "office"])).run().ok
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:reval")
    new_rows = conn.execute("SELECT COUNT(*) FROM analyzer_result WHERE analyzer_result_id > ?", (max_ar,)).fetchone()[0]
    rows, ev = states(conn)
    drifted = [k for k in q if rows[q[k]].review_state == MD.NEEDS_REVALIDATION]
    check("the analyzers re-run on unchanged files (8 new readings, every id moved): ZERO decisions drift -- the B17-shaped "
          "binding resolves to its value and compares equal", ok and new_rows == 8 and drifted == [], str(drifted))
    check("...the deferral 'until the evidence changes' does not fire; Leave Unchanged stays out of the queue",
          rows[q["e"]].review_state == MD.DEFERRED and not rows[q["e"]].returned and rows[q["c"]].review_state == MD.LEFT_UNCHANGED
          and q["c"] not in {r.question_id for r in MD.queue(ev)})
    check("...and ZERO locked revisions go obsolete", reconcile(st, conn) == {})

    # Integrity alone: b.docx read again, same author, now signed.
    synth(conn, by["b.docx"], "office", signature_state="signed")
    rows, ev = states(conn)
    ev2, proj2, rt2 = model(conn)
    item = next(i for i in PL.build(ev2, proj2, rt2).items if i.action_kind == META and i.target_ref == by["b.docx"])
    check("an integrity change alone (b.docx now signed, the same author): its Clear stays current -- not revalidation",
          rows[q["b"]].review_state == MD.CLEAR_REQUESTED and not rows[q["b"]].drift)
    check("...but the plan re-checks it: its item is blocked signed_or_certified, and the locked revision goes obsolete for "
          "'integrity evidence changed' only", (item.state, item.blocked_reason) == (REG.ITEM_BLOCKED, "signed_or_certified")
          and reconcile(st, conn) == {rev1: ["integrity_changed"]})
    rev2 = lock_now(st, conn)

    # The value changes, the content does not (a synthetic reading on the same observation).
    synth(conn, by["a.docx"], "office", author="Zed")
    synth(conn, by["g.docx"], "office", author="Zoe")
    synth(conn, by["h.docx"], "office", author="Zak")
    synth(conn, by["f.docx"], "office", author="Zia")
    rows, ev = states(conn)
    check("the observed value changes with the content unchanged (a.docx's reading says Zed): its desired value needs "
          "revalidation, and says why", rows[q["a"]].review_state == MD.NEEDS_REVALIDATION
          and "observed value changed ('Alice' -> 'Zed')" in rows[q["a"]].drift[0])
    check("...the same for a Leave Unchanged (g) and a Clear (h): both back in the queue",
          rows[q["g"]].review_state == rows[q["h"]].review_state == MD.NEEDS_REVALIDATION
          and {q["g"], q["h"], q["a"]} <= {r.question_id for r in MD.queue(ev)})
    check("...and the locked revision goes obsolete: evidence changed (a previewed input moved)",
          "evidence_changed" in reconcile(st, conn).get(rev2, []))
    # The value-state changes: a second analyzer disagrees about c.pdf's creator.
    synth(conn, by["c.pdf"], "office", author="Other", status="analyzed")
    rows, ev = states(conn)
    check("the value-state changes (c.pdf: known -> conflicting): its Leave Unchanged returns",
          rows[q["c"]].review_state == MD.NEEDS_REVALIDATION and "known -> conflicting" in rows[q["c"]].drift[0])
    # The file is edited: new bytes, rescanned and re-analyzed.
    MV.docx_file(docs / "d.docx", "Dan", "The d document -- edited, and longer now.")
    MV.docx_file(docs / "e.docx", "Eve", "The e document -- edited too.")
    (docs / "f.docx").unlink()
    ok = [RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run().ok,
          RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["pdf", "office"])).run().ok]
    conn.close()
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:reval")
    rows, ev = states(conn)
    check("an edited file (d.docx, new bytes, the same author): drift 'the file changed since' -- through its B17-shaped binding",
          all(ok) and rows[q["d"]].review_state == MD.NEEDS_REVALIDATION and "the file changed since" in rows[q["d"]].drift[0])
    check("...and e.docx's deferral 'until the evidence changes' has come back", rows[q["e"]].returned)
    check("a file gone (f.docx deleted, rescanned): unavailable -- drift", rows[q["f"]].review_state == MD.NEEDS_REVALIDATION
          and rows[q["f"]].observed_state == "unavailable")
    check("the re-analysis read a.docx, g.docx and h.docx again from their unchanged bytes: the synthetic readings are "
          "superseded by the real ones, the values are back to what was decided on -- no longer drifted",
          all(rows[q[k]].review_state != MD.NEEDS_REVALIDATION for k in ("a", "g", "h")))
    synth(conn, by["a.docx"], "office", author="Zed")          # the value changes again, content untouched, for Confirm
    synth(conn, by["g.docx"], "office", author="Zoe")
    synth(conn, by["h.docx"], "office", author="Zak")
    rows, ev = states(conn)

    section("3. Bulk Confirm, store level")
    everything = list(rows)
    before = {k: dict(conn.execute("SELECT decision_id, decision_kind, value_json, evidence_binding_json FROM p3_decision WHERE "
                                   "target_kind = ? AND target_ref = ? ORDER BY decision_id DESC LIMIT 1", (MQ, q[k])).fetchone())
              for k in ("a", "d", "g", "h")}
    pv = BK.preview_metadata_confirm(conn, everything)
    disp = {c.target_ref: c.disposition for c in pv.candidates}
    want = {q["a"]: REG.DISP_DECIDED, q["d"]: REG.DISP_DECIDED, q["g"]: REG.DISP_DECIDED, q["h"]: REG.DISP_DECIDED,
            q["b"]: REG.DISP_SATISFIED, q["c"]: REG.DISP_CONFLICT, q["f"]: REG.DISP_BLOCKED,
            q["e"]: REG.DISP_NOT_APPLICABLE, str(title_a): REG.DISP_NOT_APPLICABLE}
    check("the preview's buckets: will confirm (a desired value, a desired value in B17's shape, a Leave Unchanged, a Clear) / "
          "already current / conflict (the readings disagree) / blocked (the file is gone) / not applicable (no decision)",
          disp == want, str({k: disp.get(k) for k in want}))
    check("...its words: counts per bucket, the kinds it records, and 'No source files will be changed.'",
          "4 would receive a decision (4 decisions: the same decision, re-recorded on the evidence as it is now)" in "\n".join(pv.lines())
          and pv.lines()[-1] == "No source files will be changed." and pv.member_word == "questions")
    res = st.commit_bulk(pv, note="confirm (test)")
    members = {m["target_ref"]: m["disposition"] for m in st.batch_members(res["batch_id"])}
    new = {k: dict(conn.execute("SELECT * FROM p3_decision WHERE target_kind = ? AND target_ref = ? ORDER BY decision_id DESC "
                                "LIMIT 1", (MQ, q[k])).fetchone()) for k in ("a", "d", "g", "h")}
    check("the commit records exactly the preview: its members and dispositions, one decision per 'will confirm'",
          members == disp and res["decisions"] == pv.decision_count == 4)
    check("each confirmed decision: the SAME kind and value, superseding the one it confirms, origin bulk_explicit_human with "
          "the batch id -- never a new desired value, a Clear stays a Clear, a Leave Unchanged stays one",
          all(new[k]["decision_kind"] == before[k]["decision_kind"] and new[k]["value_json"] == before[k]["value_json"]
              and new[k]["supersedes_decision_id"] == before[k]["decision_id"] and new[k]["origin_kind"] == "bulk_explicit_human"
              and new[k]["origin_ref"] == str(res["batch_id"]) for k in new))
    nb = {k: json.loads(new[k]["evidence_binding_json"]) for k in new}
    check("...bound to the evidence as it is now (the value it was confirmed on)", nb["a"]["observed_values"] == ["Zed"]
          and nb["g"]["observed_values"] == ["Zoe"] and nb["d"]["observed_values"] == ["Dan"])
    rows, ev = states(conn)
    check("afterwards they are current: out of the queue, each in its own state",
          (rows[q["a"]].review_state, rows[q["d"]].review_state, rows[q["g"]].review_state, rows[q["h"]].review_state)
          == (MD.DESIRED, MD.DESIRED, MD.LEFT_UNCHANGED, MD.CLEAR_REQUESTED))
    check("the conflict, the blocked, the undecided and the already-current were not touched",
          [rows[q[k]].decision_id for k in ("b", "c", "f")] == [str(rec[k]["decision_id"]) for k in ("b", "c", "f")]
          and rows[str(title_a)].decision_id is None)

    # The guards.
    synth(conn, by["a.docx"], "office", author="Yan")
    pv2 = BK.preview_metadata_confirm(conn, everything)
    st.record_metadata_decision("project_metadata_set", by["b.docx"], value={"field": "note", "value": "moved the record"})
    check("the commit refuses when the decision record moved since the preview", PC.refused(lambda: st.commit_bulk(pv2)))
    pv3 = BK.preview_metadata_confirm(conn, everything)
    synth(conn, by["h.docx"], "office", author="Xan")
    check("...and when the evidence moved (a re-analysis: a new analyzer reading, no new operation)",
          PC.refused(lambda: st.commit_bulk(pv3)))

    # Undo: every question exactly where it was.
    undo = st.undo_batch(res["batch_id"])
    after = {k: dict(conn.execute("SELECT * FROM p3_decision WHERE target_kind = ? AND target_ref = ? ORDER BY decision_id DESC "
                                  "LIMIT 1", (MQ, q[k])).fetchone()) for k in ("a", "d", "g", "h")}
    rows, ev = states(conn)
    check("Undo withdraws the batch's decisions and re-applies each one it superseded -- same kind, same value, its ORIGINAL "
          "binding -- in one operation", undo["withdrawn"] == undo["reapplied"] == 4
          and all(after[k]["decision_kind"] == before[k]["decision_kind"] and after[k]["value_json"] == before[k]["value_json"]
                  and after[k]["evidence_binding_json"] == before[k]["evidence_binding_json"] for k in after))
    check("...so each question is back exactly where it was: needs revalidation (never undecided)",
          all(rows[q[k]].review_state == MD.NEEDS_REVALIDATION for k in ("a", "d", "g", "h")))
    check("...the batch is no longer in force, and its record stays", st.batch(res["batch_id"])["in_force"] == 0
          and len(st.batch_members(res["batch_id"])) == len(everything))

    # Task 4's faster read: the newest reading on the current observation first, every observation's rows only where
    # there is none -- the same questions as reading every observation (B17's query), here where files were edited,
    # deleted, re-read synthetically and analyzed four times.
    from Phase3 import evidence as E
    marks_a = ",".join("?" for _ in E._READ_ANALYZERS)
    every = {}
    for r in conn.execute(f"SELECT {E._READING_COLUMNS} FROM file_observation fo JOIN analyzer_result ar ON ar.file_observation_id = "
                          "fo.file_observation_id JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id JOIN analyzer a "
                          f"ON a.analyzer_id = rr.analyzer_id WHERE a.analyzer_key IN ({marks_a})", E._READ_ANALYZERS):
        every.setdefault(str(r["file_path_id"]), []).append(r)
    caps = E.load_capabilities(conn)
    md = E.load_metadata(conn)
    qrows = conn.execute("SELECT q.question_id, q.semantic_field, x.* FROM p3_metadata_question q JOIN (" + E._QUESTION_LOCATION_SQL
                         + ") x ON x.file_path_id = q.file_path_id").fetchall()
    same = all(E._question(r["question_id"], r, r["semantic_field"], every.get(str(r["file_path_id"]), []), caps)
               == md.questions[str(r["question_id"])] for r in qrows)
    locs = [E.location_metadata(conn, by[n]) for n in by]
    same_locs = all(a == E._question(a.question_id, conn.execute(E._QUESTION_LOCATION_SQL + " WHERE fp.file_path_id = ?",
                                                                 (int(a.location_id),)).fetchone(), a.semantic_field,
                                     every.get(a.location_id, []), caps) for lm in locs for a in lm)
    check("the queue's faster read (newest reading on the current observation; older observations only where there is "
          "none) gives exactly the questions B17's every-observation read gives -- edited, deleted and re-read files included",
          same and same_locs and len(qrows) >= 9)
    return app_root, docs, project_dir, by, q


# ---------------------------------------------------------------------------
# 4. The window
# ---------------------------------------------------------------------------

def test_gui(app_root, corpus, project_dir, by, q):
    section("4. The queue page, headless: the check column, Confirm..., Batches... and Undo")
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
    from Phase3 import metadata_view as MVW, review as RV
    real = messagebox.askyesno
    messagebox.askyesno = lambda *a, **k: True
    try:
        with PC.ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as writes:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            app.show_metadata_review()
            app.update()
            page = app.metadata_page
            check("the queue page opens with a check column and the Confirm... / Batches... buttons",
                  isinstance(page, MVW.MetadataQueuePage) and page.tree["columns"][0] == "check"
                  and str(page.confirm_button.cget("text")) == "Confirm...")
            listed = [r.question_id for r in page.shown if r.question_id]
            i_a = next(i for i, r in enumerate(page.shown) if r.question_id == q["a"])
            page.tree.selection_set(str(i_a))
            page.toggle_check()
            check("Space (toggle_check) checks the selected row; the glyph and the count follow",
                  page.checked == {q["a"]} and page.tree.set(str(i_a), "check") == RV.CHECKED and "1 checked" in page.checked_var.get())
            dlg = page.confirm()
            check("Confirm... opens on the checked scope; nothing recordable before a preview",
                  dlg.scope_var.get() == "checked" and str(dlg.ok.cget("state")) == "disabled")
            pv = dlg.run_preview()
            check("the preview over the one checked question: 1 will confirm; the button says so",
                  pv.decision_count == 1 and str(dlg.ok.cget("text")) == "Confirm 1 decision")
            dlg.scope_var.set("listed")
            dlg._invalidate()
            pv = dlg.run_preview()
            n = pv.decision_count
            check("the other scope -- every question the filters list -- is a frozen query snapshot of the listed ids",
                  pv.scope_kind == REG.SCOPE_QUERY_SNAPSHOT and pv.query["queue"] is True
                  and sorted(pv.query["question_ids"]) == sorted(listed) and n >= 4)
            res = dlg._confirm()
            app.update()
            check("recording it: one batch, the page reloaded, the confirmed questions gone from the queue",
                  res is not None and res["decisions"] == n
                  and not {q["a"], q["d"], q["g"], q["h"]} & {r.question_id for r in page.rows})
            bd = page.open_batches()
            bd.table.selection_set(str(res["batch_id"]))
            bd._selected()
            labels = [bd.members.item(i, "values")[0] for i in bd.members.get_children()]
            check("Batches... lists the Confirm batch with its members labelled as file -- field (question #n)",
                  any("a.docx -- creator (question #" in x for x in labels))
            bd.undo()
            app.update()
            page = app.metadata_page
            page.reload()
            check("Undo from Batches...: the questions are back in the queue (needs revalidation)",
                  {q["a"], q["d"], q["g"], q["h"]} <= {r.question_id for r in page.rows})
            page.reload()
            check("a reload with nothing changed is served from the session cache", page.cache().last == "hit")
            try:
                bd.win.destroy()
            except Exception:                                       # noqa: BLE001
                pass
            app.release_connection()
            app.destroy()
        check("zero source reads (open, stat, listing under the corpus) and zero writes outside the project, around the window",
              not reads.hits and not writes.violations, str(reads.hits[:2] + writes.violations[:2]))
    finally:
        messagebox.askyesno = real


# ---------------------------------------------------------------------------
# 5. The cache: equal to a fresh load after every step
# ---------------------------------------------------------------------------

def test_cache(app_root, project_dir, by, q, seeds=(7, 18, 2026), steps=40):
    section("5. The session cache equals a fresh load after every step of random sessions")
    from Phase2.core import connect, utc_now
    from Phase3 import store as S, bulk as BK, refresh as RF, evidence as E
    from Phase3.model import Evidence
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:cache")
    files = [by[n] for n in ("a.docx", "b.docx", "c.pdf", "d.docx", "e.docx", "g.docx", "h.docx")]
    fields = ["creator", "title", "description", "keywords"]
    kinds = {"set": 0, "hit": 0, "catch_up": 0, "full": 0}
    mismatches = []
    batches = []

    def rows_of(md):
        ev = Evidence({}, {}, [], [], now="2026-09-26T00:00:00Z")
        ev.metadata = md
        return [(r.question_id, r.review_state, r.drift, r.decision_id, r.returned) for r in MD.review(ev)]
    for seed in seeds:
        rng = random.Random(seed)
        cache = RF.MetadataCache()
        cache.load(conn)
        for step in range(steps):
            act = rng.choice(["set", "clear", "leave", "withdraw", "defer", "restore", "project", "synth", "confirm", "undo",
                              "nothing"])
            fid, fld = rng.choice(files), rng.choice(fields)
            try:
                if act == "set":
                    st.record_metadata_decision("desired_source_metadata_set", fid, fld,
                                                ["k1", "k2"] if fld == "keywords" else f"v{rng.randint(1, 9)}")
                elif act == "clear":
                    st.record_metadata_decision("desired_source_metadata_clear", fid, fld, True)
                elif act == "leave":
                    st.record_metadata_decision("leave_source_metadata_unchanged", fid, fld, True)
                elif act == "withdraw":
                    active = [d.decision_id for d in st.active(S.SCOPE_METADATA)]
                    if active:
                        st.withdraw(rng.choice(active))
                elif act == "defer":
                    st.defer_metadata(fid, fld, rng.choice(["defer_indefinitely", "snooze_until_evidence_change"]))
                elif act == "restore":
                    qid = st.metadata_question(fid, fld)
                    if qid is not None:
                        st.restore(MQ, qid)
                elif act == "project":
                    st.record_metadata_decision("project_metadata_set", fid, value={"field": "note", "value": f"n{step}"})
                elif act == "synth":
                    synth(conn, fid, "office" if fid != by["c.pdf"] else "pdf", author=f"A{rng.randint(1, 3)}")
                elif act == "confirm":
                    ids = [r[0] for r in rows_of(E.load_metadata(conn)) if r[0]]
                    pv = BK.preview_metadata_confirm(conn, ids)
                    if pv.decision_count:
                        batches.append(st.commit_bulk(pv)["batch_id"])
                elif act == "undo" and batches:
                    bid = batches.pop()
                    if st.batch(bid)["in_force"]:
                        st.undo_batch(bid)
            except ValueError:
                pass
            md, conflicts = cache.load(conn)
            kinds[cache.last] = kinds.get(cache.last, 0) + 1
            fresh_md, fresh_conf = E.load_metadata(conn), E.conflicting_fields(conn)
            if RF.metadata_fingerprint(md, conflicts) != RF.metadata_fingerprint(fresh_md, fresh_conf) \
                    or rows_of(md) != rows_of(fresh_md):
                mismatches.append((seed, step, act))
    total = len(seeds) * steps
    check(f"{total} random steps over {len(seeds)} sessions (decisions, withdrawals, deferrals, restores, new questions, "
          "project metadata, re-reads, Confirm batches and their Undo): the cache equals a fresh load after EVERY step, and "
          "the review rows are identical", not mismatches, str(mismatches[:5]))
    check("...and it did work incrementally: record-only steps were caught up, evidence changes reloaded in full, "
          "no-op steps were hits", kinds["catch_up"] > 0 and kinds["full"] > 0 and kinds["hit"] > 0, str(kinds))
    cache = RF.MetadataCache()
    cache.load(conn)
    cache.reset()
    check("rebuildable, never authoritative: reset drops it and the next load is a full, correct read",
          cache.md is None and cache.load(conn) is not None and cache.last == "full")
    conn.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3reval_"))
    try:
        test_rule()
        app_root, corpus, project_dir, by, q = test_project(tmp)
        test_gui(app_root, corpus, project_dir, by, q)
        test_cache(app_root, project_dir, by, q)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 REVALIDATION (B18): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
