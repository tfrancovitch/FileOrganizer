#!/usr/bin/env python3
"""Phase 2 (Understand/Query) regression tests.

Covers the boundaries that P2.11 real-Windows acceptance found the hard way:

  1. Connection boundary -- Phase 2's SQLite helper functions are connection-local.
     The fo_db migration connection does NOT have them; only Phase2.core.connect()
     does. Reusing the migration connection for analytics is the defect that
     stopped the P2.11 v1.2 acceptance run.

  2. Headless report catalog -- every standard report must run with NO
     caller-supplied parameters, using catalog-declared defaults. The GUI hides
     gaps here by prompting the user; unattended harnesses, saved queries and
     scheduled reports have nobody to prompt.

  3. Provenance stamp -- the database must report the analytical core version
     that actually touched it.

Run:  python Scripts/p2_regression.py
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
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


def make_project(tmp: Path) -> Path:
    """Create and migrate an empty project through the supported Phase 1 path."""
    import fo_db
    import fo_project

    projects = tmp / "Projects"
    projects.mkdir(parents=True)
    source = tmp / "Source"
    source.mkdir()
    created = fo_project.create_project(projects, "P2Regression", [str(source)],
                                        app_version="P2-regression")
    project_dir = Path(created["project_folder"])
    conn, _ = fo_db.open_project(str(project_dir), app_version="P2-regression")
    conn.close()
    return project_dir


def test_connection_boundary(project_dir: Path):
    print("\n[1] Connection boundary")
    import fo_db
    from Phase2.core import connect

    conn, _ = fo_db.open_project(str(project_dir), app_version="P2-regression")
    try:
        conn.execute("SELECT fo_parent('Alpha\\Beta\\Gamma.txt')").fetchone()
        helpers_on_migration_conn = True
    except sqlite3.OperationalError:
        helpers_on_migration_conn = False
    conn.close()
    check("fo_* helpers absent on the fo_db migration connection",
          not helpers_on_migration_conn,
          "helpers leaked onto the migration connection; the boundary test is no longer meaningful")

    c = connect(project_dir, write=True)
    for sql, expected in (
        ("SELECT fo_parent('Alpha\\Beta\\Gamma.txt')", "Alpha\\Beta"),
        ("SELECT fo_top_level('Alpha\\Beta\\Gamma.txt')", "Alpha"),
        ("SELECT fo_path_under('Alpha\\Beta\\Gamma.txt','Alpha')", 1),
        ("SELECT fo_path_under('Other\\Beta.txt','Alpha')", 0),
        ("SELECT fo_parent('Solo.txt')", ""),
    ):
        got = c.execute(sql).fetchone()[0]
        check(f"{sql.removeprefix('SELECT ')} == {expected!r}", got == expected, f"got {got!r}")
    c.close()


def test_core_version_stamp(project_dir: Path):
    print("\n[2] Provenance stamp")
    from Phase2 import VERSION
    from Phase2.core import connect, stamp_core_version

    c = connect(project_dir, write=True)
    stamp_core_version(c)
    stamped = c.execute("SELECT value FROM app_meta WHERE key='phase2.core_version'").fetchone()[0]
    c.close()
    check(f"app_meta.phase2.core_version == {VERSION!r}", stamped == VERSION, f"got {stamped!r}")

    ro = connect(project_dir, write=False)
    try:
        ok = stamp_core_version(ro)
        raised = False
    except Exception:
        ok, raised = None, True
    ro.close()
    check("stamp on a read-only connection returns False instead of raising",
          raised is False and ok is False, "read-only reporting must not fail over a stamp")


def test_reports_headless(project_dir: Path):
    """Every standard report must run with no caller-supplied parameters."""
    print("\n[3] Headless standard report catalog")
    from Phase2.core import connect
    from Phase2.fts import FtsManager
    from Phase2.query import QueryEngine
    from Phase2.reports import ReportCatalog
    from Phase2.saved import SavedQueryStore

    c = connect(project_dir, write=True)
    catalog = ReportCatalog()
    engine = QueryEngine(c, saved_store=SavedQueryStore(c), fts_manager=FtsManager(c, project_dir))

    check(f"catalog declares {catalog.reports and len(catalog.reports)} reports",
          len(catalog.reports) == 31, f"expected 31, got {len(catalog.reports)}")

    # Every declared parameter must resolve to a value without caller input --
    # this is what makes an unattended run possible at all.
    for report in catalog.reports:
        declared = report.get("parameters") or []
        if not declared:
            continue
        effective = catalog.effective_parameters(report["report_id"])
        missing = [p["name"] for p in declared if effective.get(p["name"]) is None]
        check(f"{report['report_id']} resolves all declared parameters from defaults",
              not missing, f"unresolved: {missing}")

    failed = []
    for report in catalog.reports:
        rid = report["report_id"]
        try:
            catalog.run(engine, rid)
        except Exception as exc:
            failed.append(f"{rid}: {type(exc).__name__}: {exc}")
    check("all 31 reports run with no caller-supplied parameters",
          not failed, "; ".join(failed))
    c.close()


def seed_layered_analyzer_attempts(project_dir: Path):
    """Seed three files, each analyzed twice, so 'latest attempt wins' is testable.

    analyzer_result is UNIQUE on (analyzer_run_id, file_observation_id) and
    analyzer_run is UNIQUE on (run_id, analyzer_id), so a second attempt at the
    same file needs its own run -- which is how re-analysis actually happens.

    Returns the number of files whose *latest* attempt is an error.
    """
    from Phase2.core import connect

    c = connect(project_dir, write=True)
    now = "2026-01-01T00:00:00Z"
    c.execute("INSERT INTO source_root(project_id,source_root_id,root_path,root_path_key,"
              "added_utc,is_active,root_ordinal) VALUES(1,1,'C:\\\\Seed','c:\\\\seed',?,1,1)"
              " ON CONFLICT DO NOTHING", (now,))
    c.execute("INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,"
              "schema_version) VALUES(1,'seed-scan','prescan','completed',?,'seed',8)", (now,))
    scan_run = c.execute("SELECT MAX(run_id) FROM run").fetchone()[0]
    c.execute("INSERT INTO inventory_scan(project_id,run_id,source_root_id,status,started_utc)"
              " VALUES(1,?,1,'completed',?)", (scan_run, now))
    scan = c.execute("SELECT MAX(inventory_scan_id) FROM inventory_scan").fetchone()[0]
    c.execute("INSERT INTO analyzer(analyzer_key,label) VALUES('seedtext','Seed Text')")
    analyzer_id = c.execute("SELECT MAX(analyzer_id) FROM analyzer").fetchone()[0]

    # Two runs of the same analyzer: an older pass and a newer one.
    runs = {}
    for tag, when in (("older", "2020-01-01T00:00:00Z"), ("newer", "2027-01-01T00:00:00Z")):
        c.execute("INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,"
                  "schema_version) VALUES(1,?,'content_analysis','completed',?,'seed',8)",
                  (f"seed-{tag}", when))
        rid = c.execute("SELECT MAX(run_id) FROM run").fetchone()[0]
        c.execute("INSERT INTO analyzer_run(project_id,analyzer_id,run_id,analysis_status,"
                  "ingest_status,started_utc) VALUES(1,?,?,'completed','completed',?)",
                  (analyzer_id, rid, when))
        runs[tag] = (c.execute("SELECT MAX(analyzer_run_id) FROM analyzer_run").fetchone()[0], when)

    # (older attempt, newer attempt) -> whether the latest is an error
    cases = [("recovered.txt", "error", "analyzed"),    # fixed on re-run  -> not counted
             ("regressed.txt", "analyzed", "error"),    # broke on re-run  -> counted
             ("persistent.txt", "error", "error")]      # still broken     -> counted
    expected = sum(1 for _, _, newer in cases if newer == "error")

    for name, older_status, newer_status in cases:
        c.execute("INSERT INTO file_path(project_id,source_root_id,relative_path,"
                  "relative_path_key,file_name,first_seen_utc,last_seen_utc)"
                  " VALUES(1,1,?,?,?,?,?)", (name, name.lower(), name, now, now))
        fpid = c.execute("SELECT MAX(file_path_id) FROM file_path").fetchone()[0]
        c.execute("INSERT INTO file_observation(project_id,inventory_scan_id,file_path_id,"
                  "status,observed_utc) VALUES(1,?,?,'observed',?)", (scan, fpid, now))
        obs = c.execute("SELECT MAX(file_observation_id) FROM file_observation").fetchone()[0]
        c.execute("INSERT INTO file_state(project_id,file_path_id,source_root_id,state,"
                  "current_observation_id,first_seen_utc,verified_utc,state_changed_utc)"
                  " VALUES(1,?,1,'present',?,?,?,?)", (fpid, obs, now, now, now))
        for tag, status in (("older", older_status), ("newer", newer_status)):
            run_id, when = runs[tag]
            c.execute("INSERT INTO analyzer_result(project_id,analyzer_run_id,"
                      "file_observation_id,status,analyzed_utc) VALUES(1,?,?,?,?)",
                      (run_id, obs, status, when))
    c.commit()
    c.close()
    return expected


def test_analyzer_failure_semantics(project_dir: Path):
    """Only the newest attempt per (location, analyzer) may count as a failure.

    evidence_health() uses a correlated NOT EXISTS rather than a ROW_NUMBER()
    window (P2.5 section 7 measured the window ~4x slower and recommended the
    correlated form). Both must agree that a re-run which succeeds clears an
    earlier error, and that an older error under a newer success stays cleared.
    """
    print("\n[4] Current-analyzer-failure semantics")
    from Phase2.core import connect
    from Phase2.coverage import evidence_health

    expected = seed_layered_analyzer_attempts(project_dir)
    c = connect(project_dir, write=False)
    health = evidence_health(c)
    got = health["current_analyzer_failures"]
    c.close()
    check(f"latest-attempt-wins: {got} current failures (expected {expected})",
          got == expected,
          "an older error under a newer success must not count, and a newer "
          "error over an older success must")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p2_regression_"))
    try:
        print(f"Phase 2 regression -- scratch project under {tmp}")
        project_dir = make_project(tmp)
        test_connection_boundary(project_dir)
        test_core_version_stamp(project_dir)
        test_reports_headless(project_dir)
        test_analyzer_failure_semantics(project_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAILED -- {len(FAILURES)} check(s): " + ", ".join(FAILURES))
        return 1
    print("ALL PHASE 2 REGRESSION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
