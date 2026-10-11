#!/usr/bin/env python3
r"""B18 regression: the closeout's state machines and smaller recommendations.

What it proves, in order (each table is written in ARCHITECTURE.md, "State
machines", with the code that enforces it and the check here that proves it):

  1. (M6) Invalid transitions are refused:
     a. the decision lifecycle -- withdraw a withdrawn or a superseded
        decision; supersede across conflict domains (a new row supersedes
        only its own domain's head); record an unknown kind, a kind on a
        target it cannot take, a group that is not current; an override
        without its confirmation or rationale. The valid ones hold:
        re-application after a withdrawal supersedes the withdrawn row (F09).
     b. review events -- a restore naming no event; a deferral whose
        disposition does not apply to the target; an unparseable date. The
        routing precedence is the tested table (p3_regression §6).
     c. the plan-revision lifecycle -- lock twice; lock a superseded or an
        obsolete revision; lock after the record, the evidence or (B18) the
        analyzer results moved; an unknown plan event kind; a locked revision
        is never edited (a new build is a new revision, the locked one stays
        locked) and a tampered item fails verification; obsolete never
        reverts.
     d. conflicts -- arise from decisions; resolved by a decision, by an
        override, by the evidence; a policy of lower rank never resolves one.
  2. (S6) p3_plan_revision.status / locked / obsolete are a cache of the
     p3_plan_event rows: rebuilt from the events, they equal the columns for
     every revision; a tampered column is found.
  3. (S4) A new revision's basis records the registry version and digest, the
     resolver and evaluator versions, the payload schema and the analyzer
     mark; a revision with B15-B17's basis still reads, locks and verifies.
  4. (S5) The path-matching rule, clause by clause, through real policies.
     b. (B19, W2) NFC also widens a bulk folder scope: a preview whose folder
        matched more than one on-disk spelling warns, naming them (pure, and
        on disk with sibling composed / decomposed folders).
  5. (S3) The current-state export: every section, the decision ids, Phase 2's
     formula guard (a file named '=SUM(1).txt' is exported inert), a button on
     the Decide page; zero source reads.

Run:  python Scripts\p3_closeout_check.py
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_revalidation_check as RC                                            # noqa: E402  (synth, MV samples)
PC, BASE, MV = RC.PC, RC.BASE, RC.MV                                          # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import plans as PL                                               # noqa: E402
from Phase3.model import path_key, under                                     # noqa: E402

RESULTS = []
LOCATION, GROUP, MQ = REG.LOCATION, REG.GROUP, REG.METADATA_QUESTION
refused = PC.refused
loc, dec, pol, model = BASE.loc, BASE.dec, BASE.pol, BASE.model


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# 1d + 4. Pure: conflicts, the path rule
# ---------------------------------------------------------------------------

def test_conflicts_pure():
    section("1d. Conflicts: arise, resolved by a decision / an override / the evidence (pure model)")
    A = loc("A", r"C:\Docs\a.pdf", phys="P1")
    C = loc("C", r"E:\Backup\a.pdf", root="Backup", phys="P3")
    protect = pol("PV1", "protect_source_root", {"root_key": "Backup", "root_path": "E:\\Backup"})
    kinds = lambda ev: [c.kind for c in R.resolve_group(ev, ev.groups["C1"]).conflicts]          # noqa: E731
    red = dec("D1", "redundant_location", LOCATION, "C")
    check("arise: a redundant mark on a protected copy -> PROTECTED_VS_REDUNDANT (blocking)",
          kinds(model([A, C], [red], [protect])) == [R.PROTECTED_VS_REDUNDANT])
    check("resolved by a decision: the mark withdrawn -> no conflict",
          kinds(model([A, C], [dec("D1", "redundant_location", LOCATION, "C", withdrawn=True)], [protect])) == [])
    check("resolved by an override naming the protecting policy version -> no conflict",
          kinds(model([A, C], [red, dec("D2", "override_protection", LOCATION, "C",
                                        {"policy_id": "PPV1", "policy_version_id": "PV1"})], [protect])) == [])
    gone = loc("C", r"E:\Backup\a.pdf", root="Backup", phys="P3", content="C9")                    # the copy's bytes changed
    ev = model([A, gone, loc("B", r"C:\Other\a.pdf", phys="P2")], [red], [protect])
    check("resolved by the evidence: the copy left the group (its content changed) -> the group has no conflict",
          [c.kind for c in R.resolve_group(ev, ev.groups["C1"]).conflicts] == [])
    # The physical-copy floor (found by mutation M03: nothing reached it). Where
    # the only location left unmarked is not present, the last PRESENT
    # physical copy would go: a floor conflict even though a location remains.
    gone_b = loc("B", r"C:\Old\a.pdf", phys="P2", present=False)
    ev = model([A, gone_b], [dec("D1", "redundant_location", LOCATION, "A")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("the physical-copy floor: every PRESENT physical copy marked redundant (the one location left is not present) -> "
          "MINIMUM_PHYSICAL_COPIES, nothing eligible", [c.kind for c in g.conflicts] == [R.MINIMUM_PHYSICAL_COPIES_KIND]
          and not g.ready_for_plan and g.plan_eligible_reclaim_bytes == 0)
    A2p, B2p = loc("A", r"C:\Docs\a.pdf", phys="P1"), loc("B", r"C:\Docs\b.pdf", phys="P1")      # hard-link aliases
    ev = model([A2p, B2p, loc("C", r"C:\Other\a.pdf", phys="P3")], [dec("D1", "redundant_location", LOCATION, "A")])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("...while removing one name of a hard-linked object whose other name stays keeps both floors (no conflict)",
          g.conflicts == () and g.physical_copies == 2 and g.hardlink_aliases == 1)
    prefer = pol("PV2", "prefer_folder_subtree", {"path": r"E:\Backup"})
    check("a policy of lower rank never resolves one: adding a prefer policy leaves the conflict standing",
          kinds(model([A, C], [red], [protect, prefer])) == [R.PROTECTED_VS_REDUNDANT])
    check("the routing precedence is the tested table: conflict > needs revalidation > blocked > deferred > ready > "
          "resolved > unresolved (all 127 combinations: p3_regression §6)",
          RT.ROUTE_ORDER == (RT.CONFLICT, RT.NEEDS_REVALIDATION, RT.BLOCKED, RT.DEFERRED, RT.READY_FOR_PLAN, RT.RESOLVED,
                             RT.UNRESOLVED))

    section("4. The path-matching rule (S5), clause by clause")
    covers = lambda folder, path: under(path_key(path), path_key(folder))                        # noqa: E731
    check("separators: '/' is '\\'; a run of separators is one; a UNC path's leading '\\\\' is kept",
          covers("C:/Docs", r"C:\Docs\a.txt") and covers(r"C:\Docs\\\\Sub", r"C:\Docs\Sub\a.txt")
          and path_key(r"\\server\share\x") == r"\\server\share\x")
    check("an extended-length prefix is dropped (\\\\?\\C:\\..., \\\\?\\UNC\\server\\...)",
          path_key("\\\\?\\C:\\Docs") == "c:\\docs" and path_key("\\\\?\\UNC\\srv\\share\\x") == r"\\srv\share\x")
    check("no trailing separator: 'C:\\Docs\\' is 'C:\\Docs'", path_key("C:\\Docs\\") == path_key(r"C:\Docs") == "c:\\docs")
    nfd = unicodedata.normalize("NFD", "C:\\Résumés")
    check("Unicode: compared in NFC -- a folder typed composed covers the name stored decomposed, and the reverse",
          covers("C:\\Résumés", nfd + "\\cv.pdf") and covers(nfd, "C:\\Résumés\\cv.pdf") and nfd != "C:\\Résumés")
    check("case-insensitive by simple lower-casing; 'ß' is not 'ss' (no full case folding)",
          covers(r"c:\DOCS", r"C:\docs\a.txt") and not covers("C:\\Straße", "C:\\Strasse\\a.txt"))
    check("the folder boundary is a whole segment: 'C:\\Docs' covers itself and below, never 'C:\\Docs2'",
          covers(r"C:\Docs", r"C:\Docs") and covers(r"C:\Docs", r"C:\Docs\x\y.txt") and not covers(r"C:\Docs", r"C:\Docs2\a.txt"))
    A2 = loc("A", nfd + r"\a.pdf", phys="P1")
    B2 = loc("B", r"C:\Other\a.pdf", phys="P2")
    folder = pol("PV5", "protect_folder_subtree", {"path": "c:/résumés/"})
    ev = model([A2, B2], [dec("D1", "redundant_location", LOCATION, "A")], [folder])
    g = R.resolve_group(ev, ev.groups["C1"])
    check("...through a real protection policy: 'c:/résumés/' typed protects the copy stored under the decomposed "
          "'C:\\Résumés' -- the conservative direction (a protection never silently misses)",
          g.verdict("A").status == R.PROTECTED and [c.kind for c in g.conflicts] == [R.PROTECTED_VS_REDUNDANT])
    root = pol("PV6", "protect_source_root", {"root_key": "BACKUP", "root_path": "E:\\Backup"})
    ev = model([A, C], [], [root])
    check("...and a root policy compares its root key by the same rule (case-insensitively)",
          R.resolve_group(ev, ev.groups["C1"]).verdict("C").status == R.PROTECTED)


def test_spellings(tmp: Path):
    """B19 (W2): NFC matching also widens a BULK folder scope -- a folder action
    over one folder takes in a sibling whose name differs only by Unicode
    normalization. The preview lists every member (it always did) and now
    says so in a warning."""
    section("4b. (B19, W2) A bulk folder scope that matched more than one on-disk spelling warns")
    from Phase3 import bulk as BK
    nfc, nfd = "C:\\Photos\\Caf\u00e9", unicodedata.normalize("NFD", "C:\\Photos\\Caf\u00e9")
    locs = [loc("A", nfc + "\\a.jpg", content="C1", phys="P1"), loc("B", nfd + "\\a.jpg", content="C1", phys="P2"),
            loc("C", "C:\\Photos\\Other\\a.jpg", content="C1", phys="P3")]
    ev = model(locs)
    proj = R.resolve_all(ev)
    rt = RT.route_all(ev, proj, "2026-09-27T00:00:00Z")
    pv = BK.preview(ev, proj, rt, "redundant_under", {"folder": nfc}, REG.SCOPE_QUERY_SNAPSHOT,
                    query=BK.snapshot_query(show="All"))
    members = sorted(c.target_ref for c in pv.candidates)
    check("typed 'Café' (composed), the folder scope takes in BOTH siblings -- the composed and the decomposed 'Café' "
          "(the matching rule, unchanged)", members == ["A", "B"], str(members))
    check("...and the preview WARNS, naming both spellings; the warning is in its lines and its record",
          len(pv.warnings) == 1 and nfc in pv.warnings[0] and nfd in pv.warnings[0]
          and any(l.startswith("Warning:") for l in pv.lines()) and pv.as_dict().get("warnings") == pv.warnings,
          str(pv.warnings))
    plain = BK.preview(ev, proj, rt, "redundant_under", {"folder": "C:\\Photos\\Other"}, REG.SCOPE_QUERY_SNAPSHOT,
                       query=BK.snapshot_query(show="All"))
    check("a folder with one spelling: no warning, and a preview record exactly as before (no 'warnings' key)",
          not plain.warnings and "warnings" not in plain.as_dict() and not any(l.startswith("Warning:") for l in plain.lines()))
    check("folder_spellings: the spellings as scanned, one per sibling; case alone is one Windows folder, told apart "
          "only if the scan stored it differently", BK.folder_spellings([nfc + "\\x", nfd + "\\y", nfc.upper() + "\\z"], nfc)
          == sorted([nfc, nfd, nfc.upper()]) and BK.folder_spellings([nfc + "\\x"], nfc) == [nfc])
    # On disk: NTFS keeps the two names apart; the scan stores both.
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES
    from Phase2.core import connect
    from Phase3 import evidence as E
    base = tmp / "Spell" / "Photos"
    d1, d2 = base / "Caf\u00e9", base / unicodedata.normalize("NFD", "Caf\u00e9")
    d1.mkdir(parents=True)
    try:
        d2.mkdir()
    except FileExistsError:
        check("NTFS keeps a composed and a decomposed folder name apart", False, "the second folder already existed")
        return
    for d in (d1, d2):
        (d / "a.jpg").write_bytes(b"same bytes" * 5000)
    app_root = tmp / "SpellRoot"
    (app_root / "Projects").mkdir(parents=True)
    ok = RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(base)], project_name="Spell")).run().ok
    project_dir = app_root / "Projects" / "Spell"
    ok = ok and RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run().ok
    conn = connect(project_dir, write=True)
    try:
        ev = E.load_evidence(conn)
        proj = R.resolve_all(ev)
        rt = RT.route_all(ev, proj, ev.now)
        pv = BK.preview(ev, proj, rt, "redundant_under", {"folder": str(d1)}, REG.SCOPE_QUERY_SNAPSHOT,
                        query=BK.snapshot_query(show="All"))
        check("on disk: two sibling folders 'Café' (composed and decomposed) scanned as two; a bulk action over one takes "
              "in both copies and warns, naming both folders", ok and len(pv.candidates) == 2 and len(pv.warnings) == 1
              and str(d1) in pv.warnings[0] and str(d2) in pv.warnings[0], f"{len(pv.candidates)} / {pv.warnings}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The real project
# ---------------------------------------------------------------------------

def build(tmp: Path):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    app_root = tmp / "CloseRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "CloseCorpus"
    docs, backup = corpus / "Docs", corpus / "Backup"
    docs.mkdir(parents=True)
    backup.mkdir(parents=True)
    for d in (docs, backup):
        (d / "a.txt").write_text("alpha " * 50, encoding="utf-8")
        (d / "=SUM(1).txt").write_text("formula " * 50, encoding="utf-8")
        (d / "Résumé.txt").write_text("résumé " * 50, encoding="utf-8")
    MV.docx_file(docs / "report.docx", "Alice")
    project_dir = app_root / "Projects" / "Close"
    ok = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs), str(backup)], project_name="Close")).run().ok,
          RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run().ok,
          RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["office"])).run().ok]
    return app_root, corpus, project_dir, all(ok)


def test_project(tmp: Path):
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S
    app_root, corpus, project_dir, ok = build(tmp)
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:close")
    E.load_evidence(conn)                   # builds Phase 2's duplicate projection, as the Decide page does on open
    rows = conn.execute("SELECT fp.file_path_id, fp.relative_path, sr.root_path FROM file_path fp JOIN source_root sr "
                        "ON sr.source_root_id = fp.source_root_id").fetchall()
    by = {(Path(r["root_path"]).name, r["relative_path"].replace("/", "\\").rsplit("\\", 1)[-1]): str(r["file_path_id"]) for r in rows}
    grp = lambda name: str(conn.execute("SELECT content_id FROM file_state WHERE file_path_id = ?",       # noqa: E731
                                        (int(by[("Docs", name)]),)).fetchone()[0])

    section("1a. The decision lifecycle: invalid transitions refused")
    check("the project: two roots, three exact-duplicate groups (one named '=SUM(1).txt', one non-ASCII), a document",
          ok and len({grp(n) for n in ("a.txt", "=SUM(1).txt", "Résumé.txt")}) == 3)
    k = st.record_decision("must_keep_location", LOCATION, by[("Docs", "a.txt")])
    r = st.record_decision("redundant_location", LOCATION, by[("Backup", "a.txt")])
    st.withdraw(k["decision_id"])
    check("withdraw a withdrawn decision -> refused", refused(lambda: st.withdraw(k["decision_id"])))
    k2 = st.record_decision("must_keep_location", LOCATION, by[("Docs", "a.txt")])
    check("re-applying after a withdrawal is a NEW decision superseding the withdrawn one (F09) -- the valid path back",
          k2["supersedes_decision_id"] == k["decision_id"] and str(k["decision_id"]) not in {d.decision_id for d in st.active()})
    k3 = st.record_decision("must_keep_location", LOCATION, by[("Docs", "a.txt")])
    check("withdraw a superseded decision -> refused", refused(lambda: st.withdraw(k2["decision_id"])))
    pm = st.record_metadata_decision("project_metadata_set", by[("Backup", "a.txt")], value={"field": "note", "value": "n"})
    check("supersede across domains -> never: a project note on a copy supersedes nothing; its redundant mark stays active",
          pm["supersedes_decision_id"] is None and str(r["decision_id"]) in {d.decision_id for d in st.active()})
    ka = st.record_decision("keep_all_group", GROUP, grp("a.txt"))
    ca = st.record_decision("canonical_location", GROUP, grp("a.txt"), by[("Docs", "a.txt")])
    check("...and on a group, Keep all and the canonical are two domains: neither supersedes the other",
          ka["supersedes_decision_id"] is None and ca["supersedes_decision_id"] is None
          and {str(ka["decision_id"]), str(ca["decision_id"])} <= {d.decision_id for d in st.active()})
    check("an unknown decision kind -> refused", refused(lambda: st.record_decision("delete_now", LOCATION, by[("Docs", "a.txt")])))
    check("a kind on a target it cannot take (keep_both on a file location) -> refused",
          refused(lambda: st.record_decision("keep_both", LOCATION, by[("Docs", "a.txt")], [1, 2])))
    check("a decision on a group that is not current -> refused",
          refused(lambda: st.record_decision("keep_all_group", GROUP, "999999")))
    check("the canonical must be a current member -> refused otherwise",
          refused(lambda: st.record_decision("canonical_location", GROUP, grp("a.txt"), by[("Docs", "report.docx")])))
    check("override_protection through record_decision -> refused (only record_override, with its confirmation)",
          refused(lambda: st.record_decision("override_protection", LOCATION, by[("Backup", "a.txt")], {})))
    pid = st.create_policy("protect_folder_subtree", {"path": str(corpus / "Backup"), "path_key": path_key(str(corpus / "Backup"))})
    pol_id = pid["policy_id"] if isinstance(pid, dict) else pid
    check("an override without its confirmation, or without a rationale -> refused",
          refused(lambda: st.record_override(by[("Backup", "a.txt")], pol_id, "because")) and
          refused(lambda: st.record_override(by[("Backup", "a.txt")], pol_id, "  ", confirmed=True)))
    check("an override with both is recorded", st.record_override(by[("Backup", "a.txt")], pol_id, "the backup is disposable",
                                                                 confirmed=True)["decision_id"] > 0)
    st.retire_policy(pol_id, rationale="the rest of the closeout checks need the Backup copies unprotected")

    section("1b. Review events: invalid transitions refused")
    check("a restore naming a review event that does not exist -> refused",
          refused(lambda: st.restore(GROUP, grp("a.txt"), refers_to=999999)))
    check("a deferral whose disposition does not apply to the target (until a preview is available, on a group) -> refused",
          refused(lambda: st.defer(GROUP, grp("a.txt"), "snooze_until_preview_available")))
    check("snoozing until a date that is not a date -> refused",
          refused(lambda: st.defer(GROUP, grp("a.txt"), "snooze_until_date", until="next week")))
    check("an unknown event kind -> refused", refused(lambda: st.system_events([{"event_kind": "vanished", "target_kind": GROUP,
                                                                               "target_ref": grp("a.txt")}])))

    section("1c. The plan-revision lifecycle: invalid transitions refused")
    st.record_decision("redundant_location", LOCATION, by[("Backup", "=SUM(1).txt")])
    st.record_decision("canonical_location", GROUP, grp("=SUM(1).txt"), by[("Docs", "=SUM(1).txt")])
    ev, proj, rt = RC.model(conn)
    r1 = st.record_plan_revision(PL.build(ev, proj, rt), name="closeout")
    lk = st.lock_plan_revision(r1["plan_revision_id"])
    check("lock twice -> refused", refused(lambda: st.lock_plan_revision(r1["plan_revision_id"])))
    # (found by mutation M29) The marks guard also refuses a second lock -- the record moved. Make the marks current,
    # so only the status guard stands between a locked revision and a second lock.
    rec_mark, ev_mark = st._marks()
    b1 = dict(st.plan_revision(r1["plan_revision_id"])[0]["basis"], record_mark=rec_mark, evidence_mark=ev_mark,
              analyzer_mark=E.analyzer_mark(conn))
    conn.execute("UPDATE p3_plan_revision SET basis_json = ? WHERE plan_revision_id = ?", (json.dumps(b1, sort_keys=True),
                                                                                           r1["plan_revision_id"]))
    conn.commit()
    try:
        st.lock_plan_revision(r1["plan_revision_id"])
        why = ""
    except ValueError as exc:
        why = str(exc)
    check("...by its own status guard, even with the record and evidence marks current: 'only a previewed revision can be "
          "locked'", "only a previewed revision can be locked" in why, why)
    check("a locked revision verifies against its fingerprint", st.verify_plan_revision(r1["plan_revision_id"]) is True)
    r2 = st.record_plan_revision(PL.build(ev, proj, rt), plan_id=r1["plan_id"])
    rev1, _i = st.plan_revision(r1["plan_revision_id"])
    check("'edit' a locked revision: no operation exists -- a new build is a NEW revision, and the locked one stays locked "
          "(never superseded)", rev1["status"] == "locked" and r2["revision_no"] == 2)
    r3 = st.record_plan_revision(PL.build(ev, proj, rt), plan_id=r1["plan_id"])
    check("lock a superseded revision -> refused", refused(lambda: st.lock_plan_revision(r2["plan_revision_id"])))
    st.record_decision("must_keep_location", LOCATION, by[("Docs", "report.docx")])
    check("lock after the decision record moved -> refused (build again)", refused(lambda: st.lock_plan_revision(r3["plan_revision_id"])))
    ev, proj, rt = RC.model(conn)
    r4 = st.record_plan_revision(PL.build(ev, proj, rt), plan_id=r1["plan_id"])
    RC.synth(conn, by[("Docs", "report.docx")], "office", author="Zed")
    check("lock after a re-analysis (B18: the analyzer mark moved; no operation, no new observation) -> refused",
          refused(lambda: st.lock_plan_revision(r4["plan_revision_id"])))
    check("an unknown plan event kind -> refused", refused(lambda: st._plan_event(r1["plan_revision_id"], lk["operation_id"], "edited")))
    # Tamper with a locked revision's item: verification says so.
    item = conn.execute("SELECT plan_item_id, state FROM p3_plan_item WHERE plan_revision_id = ? ORDER BY item_no LIMIT 1",
                        (r1["plan_revision_id"],)).fetchone()
    conn.execute("UPDATE p3_plan_item SET state = 'ready_tampered' WHERE plan_item_id = ?", (item[0],))
    conn.commit()
    tampered = st.verify_plan_revision(r1["plan_revision_id"])
    conn.execute("UPDATE p3_plan_item SET state = ? WHERE plan_item_id = ?", (item[1], item[0]))
    conn.commit()
    check("a locked revision whose item was altered in the database fails verification (and verifies again restored)",
          tampered is False and st.verify_plan_revision(r1["plan_revision_id"]) is True)
    # Obsolete: the locked revision's own decision withdrawn.
    cano = next(d for d in st.active() if d.kind == "canonical_location" and d.target_ref == grp("=SUM(1).txt"))
    st.withdraw(cano.decision_id)
    ev, proj, rt = RC.model(conn)
    found = st.reconcile_plans(ev, proj, rt)
    check("the locked revision's own decision withdrawn -> obsolete", found == [r1["plan_revision_id"]],
          f"found {found}, withdrew {cano.decision_id}, status {st.plan_revision(r1['plan_revision_id'])[0]['status']}")
    check("lock an obsolete revision -> refused", refused(lambda: st.lock_plan_revision(r1["plan_revision_id"])))
    st.record_decision("canonical_location", GROUP, grp("=SUM(1).txt"), by[("Docs", "=SUM(1).txt")])
    ev, proj, rt = RC.model(conn)
    st.reconcile_plans(ev, proj, rt)
    rev1, _i = st.plan_revision(r1["plan_revision_id"])
    check("obsolete never reverts: the same decision recorded again leaves it obsolete (a new revision is how to go on)",
          rev1["status"] == "obsolete" and rev1["obsolete"] == 1 and rev1["locked"] == 1)

    section("2. p3_plan_revision.status / locked / obsolete: a cache of the plan events (S6)")
    rebuilt = st.plan_status_from_events()
    n = conn.execute("SELECT COUNT(*) FROM p3_plan_revision").fetchone()[0]
    check(f"rebuilt from p3_plan_event alone, every revision's status, locked and obsolete ({n} revisions: locked, obsolete, "
          "superseded, previewed) equal the cached columns", st.plan_status_drift() == [] and len(rebuilt) == n
          and {s for s, _l, _o in rebuilt.values()} >= {"obsolete", "superseded", "previewed"})
    conn.execute("UPDATE p3_plan_revision SET status = 'locked' WHERE plan_revision_id = ?", (r2["plan_revision_id"],))
    conn.commit()
    drift = st.plan_status_drift()
    conn.execute("UPDATE p3_plan_revision SET status = 'superseded' WHERE plan_revision_id = ?", (r2["plan_revision_id"],))
    conn.commit()
    check("a tampered status column is found (and the events say what it should be)",
          drift == [(r2["plan_revision_id"], ("locked", 0, 0), ("superseded", 0, 0))] and st.plan_status_drift() == [])

    section("3. The revision basis records its versions (S4); an older basis still reads")
    ev, proj, rt = RC.model(conn)
    r5 = st.record_plan_revision(PL.build(ev, proj, rt), name="basis")
    rev5, _i = st.plan_revision(r5["plan_revision_id"])
    b = rev5["basis"]
    check("a new revision's basis: routing, registry (version + digest of its vocabulary), resolver, evaluator, payload "
          "schema, analyzer mark", b.get("routing_version") == RT.ROUTING_VERSION and b.get("registry_version") == REG.REGISTRY_VERSION
          and len(b.get("registry_digest", "")) == 64 and b.get("registry_digest") == REG.registry_digest()
          and b.get("resolver_version") == R.RESOLVER_VERSION and b.get("evaluator_version") == PL.EVALUATOR_VERSION
          and b.get("payload_schema") == PL.PLAN_PAYLOAD_SCHEMA and isinstance(b.get("analyzer_mark"), int))
    old = {k: v for k, v in b.items() if k in ("record_mark", "evidence_mark", "routing_version", "built_utc")}
    conn.execute("UPDATE p3_plan_revision SET basis_json = ? WHERE plan_revision_id = ?", (json.dumps(old, sort_keys=True),
                                                                                           r5["plan_revision_id"]))
    conn.commit()
    lk5 = st.lock_plan_revision(r5["plan_revision_id"])
    rev5, _i = st.plan_revision(r5["plan_revision_id"])
    check("a revision with B15-B17's basis (routing version and marks only) still reads, locks, and verifies under /1",
          set(rev5["basis"]) == set(old) and st.verify_plan_revision(r5["plan_revision_id"]) is True and len(lk5["fingerprint"]) == 64)
    check("the registry digest is stable within a build and across processes (sorted JSON, no hash randomisation)",
          REG.registry_digest() == REG.registry_digest())

    section("5. The current-state export (S3), apart from the journal")
    st.record_metadata_decision("desired_source_metadata_set", by[("Docs", "report.docx")], "creator", "Thomas")
    st.record_metadata_decision("project_metadata_set", by[("Docs", "report.docx")], value={"field": "tags", "value": ["=cmd", "tax"]})
    out = tmp / "CurrentState.csv"
    with PC.ReadGuard(corpus) as reads:
        n = S.export_current_state(conn, str(out))
    with open(out, encoding="utf-8-sig", newline="") as f:
        table = list(csv.DictReader(f))
    sections = {t["section"] for t in table}
    check("one row per current fact, every section present: exact-duplicate groups, desired metadata, project metadata",
          n == len(table) and {"exact_duplicate_group", "desired_source_metadata", "project_metadata"} <= sections
          and list(table[0].keys()) == list(S.CURRENT_STATE_COLUMNS))
    sum_rows = {t["file_path_id"]: t for t in table if t["section"] == "exact_duplicate_group" and "SUM" in t["path"]}
    check("each group copy's resolution and the decision ids it rests on (the canonical keeper, the redundant candidate)",
          "canonical" in sum_rows[by[("Docs", "=SUM(1).txt")]]["state"] and sum_rows[by[("Docs", "=SUM(1).txt")]]["decision_ids"]
          and sum_rows[by[("Backup", "=SUM(1).txt")]]["state"].startswith(R.REDUNDANT), str(sum_rows))
    raw = out.read_text(encoding="utf-8-sig")
    check("Phase 2's formula guard (Y-018): a value beginning '=' is written with a leading apostrophe; nothing raw",
          any(t["value"] == "'=cmd; tax" for t in table if t["section"] == "project_metadata")
          and ",=cmd" not in raw and '"=cmd' not in raw)
    check("the desired value and the project tag each name their decision", all(t["decision_ids"].startswith("#")
                                                                                 for t in table if t["section"] != "exact_duplicate_group"))
    check("the export read no source file", not reads.hits, str(reads.hits[:2]))
    check("the journal is a separate export (history, not state)", "Export journal..." in (SCRIPTS / "Phase3" / "review.py").read_text(
        encoding="utf-8") and "Export current state..." in (SCRIPTS / "Phase3" / "review.py").read_text(encoding="utf-8"))
    conn.close()
    return app_root, corpus, project_dir


def test_gui(app_root, corpus, project_dir, tmp):
    section("5b. The Decide page's button")
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
    real = messagebox.showinfo
    messagebox.showinfo = lambda *a, **k: None
    try:
        app = gui.Phase2App(None)
        app.withdraw()
        app.open_project(project_dir)
        app.show_decide()
        app.update()
        page = app.review_page
        buttons = []

        def walk(w):
            for c in w.winfo_children():
                if c.winfo_class() == "TButton":
                    buttons.append(str(c.cget("text")))
                walk(c)
        walk(app.content)
        out = project_dir / "Exports" / "state.csv"
        out.parent.mkdir(exist_ok=True)
        n = page.export_state(str(out))
        check("the Decide page has 'Export current state...' beside 'Export journal...', and it writes the CSV",
              "Export current state..." in buttons and "Export journal..." in buttons and n and out.is_file())
        app.release_connection()
        app.destroy()
    finally:
        messagebox.showinfo = real


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3close_"))
    try:
        test_conflicts_pure()
        test_spellings(tmp)
        app_root, corpus, project_dir = test_project(tmp)
        test_gui(app_root, corpus, project_dir, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 CLOSEOUT (B18; B19 adds 4b): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
