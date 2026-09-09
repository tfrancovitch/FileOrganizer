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


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p2_regression_"))
    try:
        print(f"Phase 2 regression -- scratch project under {tmp}")
        project_dir = make_project(tmp)
        test_connection_boundary(project_dir)
        test_core_version_stamp(project_dir)
        test_reports_headless(project_dir)
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
