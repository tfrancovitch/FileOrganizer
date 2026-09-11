#!/usr/bin/env python3
r"""Dashboard checks: the blocking runner, Cancel, and the estimates -- headless.

Everything the one-window Dashboard does that is not drawing is exercised
here without Tk: RunWorker drives RunCoordinator exactly as the window does,
against a fresh application root and the acceptance corpus (known ground
truth, computed from bytes on disk by the corpus builder, never by the engine
under test).

What it proves:

  1. The hash engine honours a stop, between files, with honest statuses:
     files never opened are NotAttempted, uniqueness verdicts that would rest
     on an unread peer are Unresolved, and positive findings are kept.
  2. A stopped Pre-Scan records the scan as interrupted and marks NOTHING
     missing; a later complete scan finds every file again.
  3. Every run kind -- Pre-Scan, Find My Duplicates, Full Fingerprinting,
     analysis, extraction, indexing -- completes through the worker with the
     same run records the classic dashboard left, and every one of them can
     be stopped, is recorded as cancelled, and keeps what it did.
  4. capability.py stops claiming "the duplicate question is fully answered"
     when a stopped run left files without a verdict.
  5. The estimates count applicable files exactly and measure a real sample.
  6. The window's non-drawing helpers (project listing, folder validation,
     default naming) behave.
  7. If a display is available: the window constructs, and every view --
     start, hub, doors, analyze, the pre-run estimate screen -- renders
     without raising. No mainloop, no clicks.

Run:  python Scripts/p2_dashboard_check.py
"""
from __future__ import annotations

import collections
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

FAILURES: list[str] = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}" + (f" -- {detail}" if detail else ""))
        FAILURES.append(name)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# 1. The hash engine's stop semantics, on a controlled corpus
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, key, path):
        self.key = key
        self.db_id = key
        self.path = str(path)
        self.size = path.stat().st_size
        self.is_offline_or_cloud = False


def test_hash_engine_stop(tmp: Path):
    section("1. Hash engine stop semantics")
    import fo_hash_engine as E

    root = tmp / "HashStop"
    root.mkdir()
    files = {}

    def put(name, data):
        p = root / name
        p.write_bytes(data)
        files[name] = p

    # size group A: three identical; B: two same-size different; C: two identical
    put("a1.bin", b"A" * 5000); put("a2.bin", b"A" * 5000); put("a3.bin", b"A" * 5000)
    put("b1.bin", b"B" * 7000); put("b2.bin", b"C" * 7000)
    put("c1.bin", b"D" * 9000); put("c2.bin", b"D" * 9000)
    for i, n in enumerate((11, 22, 33, 44)):
        put(f"u{i}.bin", b"U" * n)
    entries = [_Entry(i + 1, files[n]) for i, n in enumerate(sorted(files))]

    def run(mode, stop_after):
        calls = {"n": 0}

        def cont():
            calls["n"] += 1
            return calls["n"] <= stop_after
        engine = E.HashEngine(should_continue=cont)
        out = engine.run_selective(entries) if mode == "selective" else engine.run_exhaustive(entries)
        return out, collections.Counter(r.final_status for r in out.results)

    out, by = run("selective", 10 ** 9)
    check("selective, no stop: 2 groups, 4 size-unique, nothing withheld",
          not out.cancelled and len(out.groups) == 2 and by["UniqueBySize"] == 4
          and by["NotAttempted"] == 0 and by["Unresolved"] == 0, str(dict(by)))
    out, by = run("exhaustive", 10 ** 9)
    check("exhaustive, no stop: 2 groups, 6 unique by hash",
          not out.cancelled and len(out.groups) == 2 and by["UniqueByHash"] == 6, str(dict(by)))

    # Candidates are ordered (size group, db_id): a1 a2 a3 | c1 c2 | b1 b2.
    out, by = run("selective", 4)
    check("selective stopped after 4: a-group confirmed, c1 Unresolved (peer unread), rest NotAttempted",
          out.cancelled and by["ConfirmedDuplicate"] == 3 and by["Unresolved"] == 1
          and by["NotAttempted"] == 3 and by["UniqueBySize"] == 4 and len(out.groups) == 1,
          str(dict(by)))
    check("selective stopped: no file is called Error or RuledOut on the strength of an unread peer",
          by["Error"] == 0 and by["RuledOutByPartialHash"] == 0, str(dict(by)))
    check("selective stopped: summary carries cancelled/not_attempted/unresolved",
          out.summary()["cancelled"] and out.summary()["not_attempted"] == 3
          and out.summary()["unresolved"] == 1)

    out, by = run("exhaustive", 4)
    check("exhaustive stopped after 4: a-group confirmed, b1 Unresolved, 7 NotAttempted, no UniqueByHash claim",
          out.cancelled and by["ConfirmedDuplicate"] == 3 and by["Unresolved"] == 1
          and by["NotAttempted"] == 7 and by["UniqueByHash"] == 0, str(dict(by)))

    # Full-pass stop: files larger than the partial window with equal heads.
    big = root / "big"
    big.mkdir()
    head = b"H" * E.DEFAULT_PARTIAL_HASH_BYTES
    bfiles = {}
    for name, tail in (("x1.bin", b"1" * 100), ("x2.bin", b"1" * 100), ("x3.bin", b"2" * 100),
                       ("y1.bin", b"3" * 200), ("y2.bin", b"4" * 200)):
        p = big / name
        p.write_bytes(head + tail)
        bfiles[name] = p
    bentries = [_Entry(i + 1, bfiles[n]) for i, n in enumerate(sorted(bfiles))]

    def runb(stop_after):
        calls = {"n": 0}

        def cont():
            calls["n"] += 1
            return calls["n"] <= stop_after
        return E.HashEngine(should_continue=cont).run_selective(bentries)

    o = runb(10 ** 9)
    st = {os.path.basename(r.path): r.final_status for r in o.results}
    check("full pass, no stop: x1/x2 confirmed, x3/y1/y2 ruled out by full hash",
          st == {"x1.bin": "ConfirmedDuplicate", "x2.bin": "ConfirmedDuplicate",
                 "x3.bin": "RuledOutByFullHash", "y1.bin": "RuledOutByFullHash",
                 "y2.bin": "RuledOutByFullHash"}, str(st))
    o = runb(5 + 4)   # 5 partial checks, then 4 full-hash checks: y2 never opened
    st = {os.path.basename(r.path): r.final_status for r in o.results}
    check("full pass stopped before y2: x3 still RuledOut (its group complete), y1 Unresolved, y2 NotAttempted",
          o.cancelled and st["x3.bin"] == "RuledOutByFullHash" and st["y1.bin"] == "Unresolved"
          and st["y2.bin"] == "NotAttempted", str(st))


# ---------------------------------------------------------------------------
# 2-4. The worker, end to end, on the acceptance corpus
# ---------------------------------------------------------------------------

class StoppingWorker:
    """A RunWorker whose should_continue stops after N consultations."""

    def __init__(self, worker, stop_after):
        self.worker = worker
        self.stop_after = stop_after
        self.calls = 0
        worker.should_continue = self.should_continue

    def should_continue(self):
        self.calls += 1
        return self.calls <= self.stop_after


def make_worker(app_root, project_dir, request, log_lines=None, progress_calls=None):
    from Phase2.runner import RunWorker
    return RunWorker(app_root, project_dir, request,
                     log=(log_lines.append if log_lines is not None else None),
                     progress=(lambda label, done, total: progress_calls.append((label, done, total)))
                     if progress_calls is not None else None)


def query(project_dir, sql, args=()):
    from Phase2.core import connect
    c = connect(project_dir)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


def scalar(project_dir, sql, args=()):
    rows = query(project_dir, sql, args)
    return rows[0][0] if rows else None


def capabilities(project_dir):
    from Phase2.core import connect
    from Phase2.capability import project_capabilities
    c = connect(project_dir)
    try:
        return {cap.key: cap for cap in project_capabilities(c)}
    finally:
        c.close()


def test_worker(tmp: Path):
    from Phase2.runner import (RunRequest, PRESCAN, DUPLICATES, FINGERPRINT, ANALYSIS,
                               INDEX_TEXT, CANCELLED, COMPLETED, COMPLETED_WITH_WARNINGS)
    import p2_build_acceptance_corpus as corpus_builder

    app_root = tmp / "AppRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus_dir = tmp / "Corpus"
    corpus_dir.mkdir()
    corpus = corpus_builder.Corpus(corpus_dir)
    corpus.build()
    truth = corpus.ground_truth()
    n_files = truth["totals"]["file_count"]
    expected_groups = truth["duplicates"]["group_count"]

    # -- Pre-Scan that creates the project -------------------------------
    section("2. Pre-Scan through the worker (creates the project)")
    log = []
    progress = []
    request = RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(corpus_dir)],
                         project_name="DashCheck", after="doors")
    outcome = make_worker(app_root, None, request, log, progress).run()
    project_dir = app_root / "Projects" / "DashCheck"
    check("prescan completed", outcome.ok, f"{outcome.status}: {outcome.message}")
    check("project folder, database and settings exist",
          (project_dir / "Database" / "FileOrganizer.db").is_file()
          and (project_dir / "settings.json").is_file())
    if not outcome.ok:
        return
    present = scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='present'")
    check(f"inventory has all {n_files} files", present == n_files, f"got {present}")
    stages = [r[0] for r in query(project_dir, "SELECT stage_key FROM run_stage ORDER BY run_stage_id")]
    check("the four Pre-Scan stages were recorded under their historical keys",
          stages[:1] == ["New-Project.ps1"] and "PreliminaryInventory.ps1" in stages
          and "InventoryIngest" in stages and "PotentialDuplicates.ps1" in stages
          and "TimeEstimates.ps1" in stages, str(stages))
    check("run status completed", scalar(project_dir, "SELECT status FROM run ORDER BY run_id DESC LIMIT 1") == "completed")
    from Phase2.runner import load_settings
    settings = load_settings(project_dir)
    check("doors have their numbers: file count, candidates, both estimates",
          settings.get("LastPreliminaryFileCount") == n_files
          and isinstance(settings.get("LastPotentialDuplicatesCandidateCount"), int)
          and settings.get("DuplicateRunEstimateText") and settings.get("FullRunEstimateText"),
          str({k: settings.get(k) for k in ("LastPreliminaryFileCount", "DuplicateRunEstimateText")}))
    # The walk reports progress on a time interval, and 231 files walk in well
    # under it, so the hook's wiring is checked directly rather than waited for.
    from Phase2.runner import RunWorker
    probe = RunWorker(app_root, project_dir, request)
    coordinator = probe._make_coordinator("DashCheck")
    check("worker wires should_continue and progress into the coordinator",
          coordinator.should_continue is not None and coordinator.progress is not None)
    caps = capabilities(project_dir)
    check("hub: inventory available, identity unavailable with Find My Duplicates as the action",
          caps["inventory"].state == "available" and caps["identity"].state == "unavailable"
          and caps["identity"].action == "Find My Duplicates")

    # -- a stopped re-scan must not invent missing files ------------------
    section("3. A stopped Pre-Scan marks nothing missing")
    worker = make_worker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan", after="doors"), log)
    StoppingWorker(worker, 2)          # two directory checks, then stop
    outcome = worker.run()
    check("stopped prescan reports cancelled", outcome.cancelled, f"{outcome.status}: {outcome.message}")
    check("run recorded as cancelled", scalar(project_dir, "SELECT status FROM run ORDER BY run_id DESC LIMIT 1") == "cancelled")
    check("inventory stage recorded as cancelled",
          scalar(project_dir, "SELECT status FROM run_stage WHERE stage_key='PreliminaryInventory.ps1' ORDER BY run_stage_id DESC LIMIT 1") == "cancelled")
    check("the scan row says interrupted, not completed",
          scalar(project_dir, "SELECT status FROM inventory_scan ORDER BY inventory_scan_id DESC LIMIT 1") == "interrupted")
    missing = scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='missing'")
    present = scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='present'")
    check(f"no file was marked missing by the stopped walk (present still {n_files})",
          missing == 0 and present == n_files, f"missing={missing} present={present}")
    caps = capabilities(project_dir)
    check("hub says the inventory is incomplete after a stopped scan",
          caps["inventory"].state == "partial" and caps["inventory"].action == "Pre-Scan",
          f"{caps['inventory'].state}: {caps['inventory'].detail}")
    from Phase2.core import connect
    from Phase2.coverage import evidence_health
    c = connect(project_dir)
    health = evidence_health(c)
    c.close()
    check("evidence health reports coverage incomplete", health["coverage"] == "incomplete", health["coverage"])

    # -- a complete re-scan restores complete coverage --------------------
    outcome = make_worker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan", after="doors"), log).run()
    check("a complete re-scan finishes", outcome.ok, outcome.message)
    present = scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='present'")
    check(f"every file present again ({n_files}), none missing",
          present == n_files and scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='missing'") == 0)
    check("hub: inventory available again", capabilities(project_dir)["inventory"].state == "available")

    # -- Find My Duplicates, stopped -------------------------------------
    section("4. Find My Duplicates: stopped, then complete")
    progress = []
    worker = make_worker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates"), log, progress)
    StoppingWorker(worker, 3)          # three files, then stop
    outcome = worker.run()
    check("stopped duplicate run reports cancelled", outcome.cancelled, f"{outcome.status}: {outcome.message}")
    check("run and stage recorded as cancelled",
          scalar(project_dir, "SELECT status FROM run ORDER BY run_id DESC LIMIT 1") == "cancelled"
          and scalar(project_dir, "SELECT status FROM run_stage WHERE stage_key='PartialHash.ps1' ORDER BY run_stage_id DESC LIMIT 1") == "cancelled")
    statuses = {r[0]: r[1] for r in query(project_dir, "SELECT hash_status, COUNT(*) FROM file_state WHERE state='present' GROUP BY 1")}
    check("files never reached carry hash_status not_attempted; size-unique proofs stand",
          statuses.get("not_attempted", 0) > 0 and statuses.get("size_unique", 0) > 0, str(statuses))
    check("nothing was recorded as error by the stop", statuses.get("error", 0) == 0, str(statuses))
    check("settings do NOT claim a completed duplicate run", not load_settings(project_dir).get("LastFullHashScan"))
    caps = capabilities(project_dir)
    check("hub: identity partial, 'not fully answered', Find My Duplicates offered",
          caps["identity"].state == "partial" and "not fully answered" in caps["identity"].detail
          and caps["identity"].action == "Find My Duplicates", caps["identity"].detail)
    check("hub: duplicates partial, 'there may be more'",
          caps["duplicates"].state == "partial" and "may be more" in caps["duplicates"].detail,
          caps["duplicates"].detail)
    groups_so_far = scalar(project_dir, "SELECT COUNT(*) FROM duplicate_group")
    check(f"groups found so far ({groups_so_far}) never exceed ground truth ({expected_groups})",
          0 <= groups_so_far <= expected_groups)
    report = project_dir / "Runs" / load_settings(project_dir)["CurrentRun"] / "Reports" / "PartialHashReport.txt"
    check("the hash report says at the top that the run was stopped",
          report.is_file() and "RUN STOPPED BY THE USER" in report.read_text(encoding="utf-8-sig"))

    # -- Find My Duplicates, complete ------------------------------------
    progress = []
    outcome = make_worker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates"), log, progress).run()
    check("complete duplicate run finishes", outcome.ok, f"{outcome.status}: {outcome.message}")
    groups = scalar(project_dir, "SELECT COUNT(*) FROM duplicate_group WHERE duplicate_run_id=(SELECT MAX(duplicate_run_id) FROM duplicate_run)")
    check(f"duplicate groups == ground truth ({expected_groups})", groups == expected_groups, f"got {groups}")
    from Phase2.derived import ensure_duplicate_projection
    c = connect(project_dir, write=True)
    ensure_duplicate_projection(c)
    reclaim = c.execute("SELECT COALESCE(SUM(reclaimable_bytes),0) FROM p2_current_duplicate_summary").fetchone()[0]
    c.close()
    check(f"reclaimable bytes == ground truth ({truth['duplicates']['total_reclaimable_bytes']:,})",
          reclaim == truth["duplicates"]["total_reclaimable_bytes"], f"got {reclaim:,}")
    statuses = {r[0]: r[1] for r in query(project_dir, "SELECT hash_status, COUNT(*) FROM file_state WHERE state='present' GROUP BY 1")}
    check("no not_attempted or unresolved status survives a complete run",
          statuses.get("not_attempted", 0) == 0 and statuses.get("unresolved", 0) == 0, str(statuses))
    caps = capabilities(project_dir)
    check("hub: duplicate question fully answered; Full Fingerprinting offered for identity",
          caps["duplicates"].state == "available" and caps["identity"].action == "Full Fingerprinting",
          caps["identity"].detail)
    check("progress hook saw hashing with totals", any(p[2] for p in progress), str(progress[:3]))
    check("settings now claim a completed duplicate run", bool(load_settings(project_dir).get("LastFullHashScan")))

    # -- Full Fingerprinting, stopped then complete ------------------------
    section("5. Full Fingerprinting: stopped, then complete")
    worker = make_worker(app_root, project_dir, RunRequest(FINGERPRINT, "Full Fingerprinting"), log)
    StoppingWorker(worker, 10)
    outcome = worker.run()
    check("stopped fingerprinting reports cancelled", outcome.cancelled, f"{outcome.status}: {outcome.message}")
    statuses = {r[0]: r[1] for r in query(project_dir, "SELECT hash_status, COUNT(*) FROM file_state WHERE state='present' GROUP BY 1")}
    check("no unique_by_hash claim survives a stopped exhaustive run",
          statuses.get("unique_by_hash", 0) == 0 and statuses.get("not_attempted", 0) > 0, str(statuses))
    outcome = make_worker(app_root, project_dir, RunRequest(FINGERPRINT, "Full Fingerprinting"), log).run()
    check("complete fingerprinting finishes", outcome.ok, f"{outcome.status}: {outcome.message}")
    identified = scalar(project_dir, "SELECT COUNT(*) FROM file_state WHERE state='present' AND content_id IS NOT NULL")
    check(f"every file has an identity ({n_files})", identified == n_files, f"got {identified}")
    caps = capabilities(project_dir)
    check("hub: identity available, no action", caps["identity"].state == "available" and caps["identity"].action is None)

    # -- Analysis: stopped then complete ------------------------------------
    section("6. Analysis: stopped, then complete")
    worker = make_worker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["text"]), log)
    StoppingWorker(worker, 20)
    outcome = worker.run()
    check("stopped analysis reports cancelled", outcome.cancelled, f"{outcome.status}: {outcome.message}")
    check("analysis stage recorded as cancelled",
          scalar(project_dir, "SELECT status FROM run_stage WHERE stage_key='TextFileAnalysis.ps1' ORDER BY run_stage_id DESC LIMIT 1") == "cancelled")
    analysed = scalar(project_dir, "SELECT COUNT(*) FROM analyzer_result ar JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id JOIN analyzer a ON a.analyzer_id=rr.analyzer_id WHERE a.analyzer_key='text'")
    check("the files analysed before the stop are kept (20)", analysed == 20, f"got {analysed}")
    check("settings do NOT claim text analysis was done", not load_settings(project_dir).get("LastTextFileAnalysisScan"))
    caps = capabilities(project_dir)
    check("hub: text bucket partial with Analyze offered",
          caps["analyze.text"].state == "partial" and caps["analyze.text"].action == "Analyze", caps["analyze.text"].detail)
    outcome = make_worker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["text", "pdf", "office", "image", "archive"]), log).run()
    check("complete analysis finishes", outcome.ok, f"{outcome.status}: {outcome.message}")
    caps = capabilities(project_dir)
    check("hub: text bucket available", caps["analyze.text"].state == "available", caps["analyze.text"].detail)
    check("unselected analyzers were recorded as skipped stages",
          scalar(project_dir, "SELECT COUNT(*) FROM run_stage WHERE run_id=(SELECT MAX(run_id) FROM run) AND status='skipped'") >= 3)
    check("settings claim text analysis was done", bool(load_settings(project_dir).get("LastTextFileAnalysisScan")))

    # -- Extraction, then indexing (stopped, then complete) -----------------
    section("7. Extraction and indexing")
    outcome = make_worker(app_root, project_dir, RunRequest(ANALYSIS, "Extract text", analyzer_keys=["content_extraction"]), log).run()
    check("extraction finishes", outcome.ok, f"{outcome.status}: {outcome.message}")
    extracted = scalar(project_dir, "SELECT COUNT(*) FROM extracted_content WHERE status='extracted'")
    check("extracted-text rows exist", extracted > 0, f"got {extracted}")
    caps = capabilities(project_dir)
    check("hub: search unavailable, Index text offered", caps["search"].action == "Index text", caps["search"].detail)

    worker = make_worker(app_root, project_dir, RunRequest(INDEX_TEXT, "Index text"), log)
    worker.stop_event.set()            # stop at the first check
    outcome = worker.run()
    check("stopped index build reports cancelled", outcome.cancelled, f"{outcome.status}: {outcome.message}")
    check("a stopped build leaves no index rows", scalar(project_dir, "SELECT COUNT(*) FROM p2_fts_text_map") == 0)
    outcome = make_worker(app_root, project_dir, RunRequest(INDEX_TEXT, "Index text"), log).run()
    check("index build finishes", outcome.ok, f"{outcome.status}: {outcome.message}")
    caps = capabilities(project_dir)
    check("hub: search available", caps["search"].state == "available", caps["search"].detail)
    from Phase2.fts import FtsManager
    from Phase2.query import QueryEngine
    from Phase2.saved import SavedQueryStore
    c = connect(project_dir, write=True)
    engine = QueryEngine(c, saved_store=SavedQueryStore(c), fts_manager=FtsManager(c, project_dir))
    from Phase2.query import QUERY_SCHEMA, SEMANTIC_CONTRACT
    rows = engine.execute({
        "query_schema": QUERY_SCHEMA, "semantic_contract": SEMANTIC_CONTRACT,
        "subject": {"entity": "file", "temporal": {"mode": "current"},
                    "current_file_states": ["present"]},
        "scope": {"kind": "project"},
        "where": {"all": [{"text_match": {"mode": "phrase",
                                          "query": {"kind": "literal",
                                                    "value": "zarquon reconciliation variance"}}}]},
    })["rows"]
    c.close()
    check("a phrase search against the new index finds exactly its marker document",
          [r["path.file_name"] for r in rows] == ["quarterly_report.docx"],
          str([r["path.file_name"] for r in rows]))

    # -- estimates -------------------------------------------------------------
    section("8. Estimates")
    import fo_estimates
    c = connect(project_dir)
    breakdown = fo_estimates.estimate_analysis(c, ["text", "pdf", "image", "audio", "content_extraction"])
    idx = fo_estimates.estimate_indexing(c)
    c.close()
    by_key = {a["key"]: a for a in breakdown["analyzers"]}
    n_text = truth["by_extension"].get(".txt", {}).get("count", 0) + truth["by_extension"].get(".md", {}).get("count", 0)
    check(f"text analyzer applicable count is exact ({n_text})", by_key["text"]["applicable"] == n_text,
          f"got {by_key['text']['applicable']}")
    check("audio has no applicable files and costs nothing", by_key["audio"]["applicable"] == 0 and by_key["audio"]["seconds"] == 0)
    check("text analyzer was measured, not guessed", by_key["text"]["sample"] and by_key["text"]["sample"]["files_used"] > 0
          and not by_key["text"]["guessed"])
    check("extraction sample left no artifacts in the project", not list((project_dir / "Runs").rglob("fo_estimate_*")))
    check("total estimate is positive and formatted", breakdown["total_seconds"] > 0 and breakdown["text"])
    distinct = scalar(project_dir, "SELECT COUNT(DISTINCT text_sha256) FROM extracted_content WHERE status='extracted' AND artifact_exists=1 AND text_sha256 IS NOT NULL")
    check(f"index estimate counts distinct texts ({distinct})", idx["texts"] == distinct, f"got {idx['texts']}")
    text = fo_estimates.describe_analysis(breakdown)
    check("the description names each analyzer and the estimate", "Text / Markdown" in text and "Estimated time" in text)

    return project_dir


# ---------------------------------------------------------------------------
# 6-7. The window's helpers, and -- with a display -- the views
# ---------------------------------------------------------------------------

def test_gui_helpers(tmp: Path):
    section("9. Window helpers")
    from Phase2.gui import list_projects, default_project_name, validate_folder
    projects = tmp / "AppRoot" / "Projects"
    check("list_projects finds the created project", "DashCheck" in list_projects(projects), str(list_projects(projects)))
    (projects / "New-Project").mkdir()
    check("default name skips a taken name", default_project_name(projects) == "New-Project (2)", default_project_name(projects))
    ok, _, _ = validate_folder(str(tmp))
    check("validate_folder accepts a readable folder", ok)
    ok, title, _ = validate_folder(str(tmp / "does-not-exist"))
    check("validate_folder distinguishes not-found", not ok and "not found" in title.lower(), title)
    somefile = tmp / "afile.txt"
    somefile.write_text("x")
    ok, title, msg = validate_folder(str(somefile))
    check("validate_folder refuses a file, saying so", not ok and "file, not a folder" in msg, msg)


def test_gui_views(project_dir: Path):
    section("10. Window views (no mainloop)")
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
    except Exception as exc:                                    # noqa: BLE001
        print(f"  SKIP  no display available ({exc})")
        return
    from Phase2.gui import Phase2App
    from Phase2 import hub as hub_view
    from Phase2.runner import RunRequest, DUPLICATES, ANALYSIS
    app = Phase2App(None)
    try:
        app.update()
        check("start screen renders", app.project_dir is None and app.content.winfo_children())
        app.open_project(project_dir)
        app.update()
        check("hub renders for an opened project", app.conn is not None and app.content.winfo_children())
        hub_view.show_doors(app)
        app.update()
        check("doors screen renders", len(app.content.winfo_children()) > 0)
        hub_view.show_analyze(app)
        app.update()
        check("analyze screen renders", len(app.content.winfo_children()) > 0)
        hub_view.confirm_and_run(app, RunRequest(DUPLICATES, "Find My Duplicates"))
        deadline = time.time() + 20
        while time.time() < deadline:
            app.update()
            buttons = [w for f in app.content.winfo_children() for w in f.winfo_children()
                       if isinstance(w, tk.ttk.Button) and w.cget("text") == "Start"]
            if buttons and str(buttons[0].cget("state")) == "normal":
                break
            time.sleep(0.05)
        check("pre-run estimate screen enables Start once measured", bool(buttons) and str(buttons[0].cget("state")) == "normal")
        hub_view.confirm_and_run(app, RunRequest(ANALYSIS, "Analyze", analyzer_keys=["text", "pdf"]))
        deadline = time.time() + 30
        while time.time() < deadline:
            app.update()
            buttons = [w for f in app.content.winfo_children() for w in f.winfo_children()
                       if isinstance(w, tk.ttk.Button) and w.cget("text") == "Start"]
            if buttons and str(buttons[0].cget("state")) == "normal":
                break
            time.sleep(0.05)
        check("analysis estimate screen enables Start once measured", bool(buttons) and str(buttons[0].cget("state")) == "normal")
        app.show_files(); app.update()
        app.show_reports(); app.update()
        app.show_evidence(); app.update()
        app.show_hub(); app.update()
        check("Files, Reports, Evidence and Hub views render", True)
    finally:
        try:
            app.release_connection()
            app.destroy()
        except Exception:                                       # noqa: BLE001
            pass


def test_gui_runner(tmp: Path):
    """Drive the REAL window through a run: run_blocking, its queue, Cancel,
    and _run_finished. The worker is the same one sections 2-7 proved; this
    proves the Tk half around it, with the app root pointed at scratch."""
    section("11. Window runner (run_blocking, Cancel, _run_finished)")
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
    except Exception as exc:                                    # noqa: BLE001
        print(f"  SKIP  no display available ({exc})")
        return
    import Phase2.gui as gui
    from Phase2.runner import RunRequest, PRESCAN, DUPLICATES
    import p2_build_acceptance_corpus as corpus_builder

    app_root = tmp / "GuiRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus_dir = tmp / "GuiCorpus"
    corpus_dir.mkdir()
    corpus_builder.Corpus(corpus_dir).build()

    # Point the window at the scratch root, as an installed copy would be.
    saved = (gui.APP_ROOT, gui.PROJECTS_DIR)
    gui.APP_ROOT, gui.PROJECTS_DIR = app_root, app_root / "Projects"
    # The window announces outcomes with modal dialogs; nobody is here to
    # click them, so they are recorded instead.
    dialogs = []
    saved_boxes = {name: getattr(gui.messagebox, name) for name in ("showinfo", "showwarning", "showerror")}
    for name in saved_boxes:
        setattr(gui.messagebox, name, lambda title, message, name=name, **kw: dialogs.append((name, title, message)))
    app = gui.Phase2App(None)
    try:
        app.app_root = app_root

        def pump(until, timeout):
            deadline = time.time() + timeout
            while time.time() < deadline:
                app.update()
                if until():
                    return True
                time.sleep(0.03)
            return False

        # Pre-Scan that creates the project, through the window.
        app.start_run(RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(corpus_dir)],
                                 project_name="GuiCheck", after="doors"))
        check("start_run registers an active run and disables the navigation",
              app.active_run is not None and str(app.nav_buttons[0].cget("state")) == "disabled")
        finished = pump(lambda: app.active_run is None, 180)
        check("the window's Pre-Scan finishes", finished)
        check("the window opened the new project and landed on the doors",
              app.project_dir is not None and app.project_dir.name == "GuiCheck"
              and any("What next?" in str(w.cget("text")) for w in app.content.winfo_children()
                      if isinstance(w, tk.ttk.Label)))
        check("navigation is enabled again after the run", str(app.nav_buttons[0].cget("state")) == "normal")
        check("the analytical connection was reopened", app.conn is not None and app.engine is not None)

        # Find My Duplicates, cancelled through the Cancel button's path.
        app.start_run(RunRequest(DUPLICATES, "Find My Duplicates"))
        check("connection is released while a run owns the window", app.conn is None)
        stop_event, thread = app.active_run
        stop_event.set()                     # what the Cancel button does
        finished = pump(lambda: app.active_run is None, 120)
        check("a cancelled window run finishes and hands the window back", finished and app.conn is not None)
        status = app.conn.execute("SELECT status FROM run ORDER BY run_id DESC LIMIT 1").fetchone()[0]
        check("the cancelled run is recorded as cancelled", status == "cancelled", status)
        check("the window returned to the hub", any("can answer" in str(w.cget("text"))
              for w in app.content.winfo_children() if isinstance(w, tk.ttk.Label)))
        check("the cancellation was announced to the person, as information not error",
              any(d[0] == "showinfo" and "Stopped" in d[2] for d in dialogs), str(dialogs))
    finally:
        gui.APP_ROOT, gui.PROJECTS_DIR = saved
        for name, fn in saved_boxes.items():
            setattr(gui.messagebox, name, fn)
        try:
            app.release_connection()
            app.destroy()
        except Exception:                                       # noqa: BLE001
            pass


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p2_dashboard_"))
    print(f"Dashboard checks -- scratch root {tmp}")
    project_dir = None
    try:
        test_hash_engine_stop(tmp)
        project_dir = test_worker(tmp)
        test_gui_helpers(tmp)
        if project_dir:
            test_gui_views(project_dir)
        test_gui_runner(tmp)
    finally:
        import fo_log
        try:
            fo_log.reset_app_log()
        except Exception:                                       # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} DASHBOARD CHECK(S) FAILED:")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("ALL DASHBOARD CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
