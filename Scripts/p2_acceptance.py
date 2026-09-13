#!/usr/bin/env python3
r"""Phase 2 acceptance: verify Phase 2's answers against known-answer ground truth.

Every expected value comes from GROUND_TRUTH.json, written by
p2_build_acceptance_corpus.py at corpus-build time from the bytes on disk.
Nothing here asks the query engine to confirm its own arithmetic.

What it checks:

  * evidence completeness -- a corpus that cannot produce evidence proves nothing
  * coverage honesty -- a fully readable root must report "complete"
  * scalar totals -- file count and logical bytes
  * storage by extension -- per-extension counts and bytes
  * exact duplicates -- group count and reclaimable bytes, the most complex
    analytical path and the one never previously exercised
  * literal FTS -- build, then one phrase query per marker document, each of
    which must return exactly the file that carries it
  * all 31 standard reports, headless, with no caller-supplied parameters
  * deep keyset continuation -- the paginated path the v1.2 run never reached

Usage:
    python Scripts/p2_acceptance.py --project <project-dir> --truth <GROUND_TRUTH.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

RESULTS: list[tuple[str, bool, str]] = []
TRUTH_PATH: Path | None = None       # set by main(); CASE_RESULTS.json is written beside it


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def run(project_dir: Path, truth: dict) -> int:
    from Phase2.core import connect, project_info, require_phase2_schema
    from Phase2.coverage import evidence_health
    from Phase2.derived import ensure_duplicate_projection
    from Phase2.fts import FtsManager
    from Phase2.query import QueryEngine
    from Phase2.reports import ReportCatalog
    from Phase2.saved import SavedQueryStore

    conn = connect(project_dir, write=True)
    require_phase2_schema(conn)
    fts = FtsManager(conn, project_dir)
    engine = QueryEngine(conn, saved_store=SavedQueryStore(conn), fts_manager=fts)
    catalog = ReportCatalog()

    print(f"project      : {project_info(conn).get('name')}  ({project_dir})")
    print(f"ground truth : {truth['totals']['file_count']} files, "
          f"{truth['totals']['logical_bytes']:,} bytes")

    # -- evidence completeness ------------------------------------------
    section("1. Evidence completeness (a corpus that proves nothing cannot fail)")
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ("hash_measurement", "content", "duplicate_group", "analyzer_result",
               "extracted_content")}
    for table, n in counts.items():
        check(f"{table} is non-empty ({n} rows)", n > 0, "no evidence of this kind exists")

    # -- coverage honesty -----------------------------------------------
    section("2. Coverage honesty")
    health = evidence_health(conn)
    check("fully readable root reports coverage 'complete'",
          health["coverage"] == "complete", f"got {health['coverage']!r}")
    check(f"present locations == {truth['totals']['file_count']}",
          health.get("present_locations") == truth["totals"]["file_count"],
          f"got {health.get('present_locations')}")
    check("no inaccessible locations", health.get("inaccessible_locations") == 0,
          f"got {health.get('inaccessible_locations')}")

    # -- scalar totals ---------------------------------------------------
    section("3. Scalar totals vs ground truth")
    n_files, n_bytes = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM file_state WHERE state='present'"
    ).fetchone()
    check(f"file count == {truth['totals']['file_count']}",
          n_files == truth["totals"]["file_count"], f"got {n_files}")
    check(f"logical bytes == {truth['totals']['logical_bytes']:,}",
          n_bytes == truth["totals"]["logical_bytes"], f"got {n_bytes:,}")

    # -- storage by extension --------------------------------------------
    section("4. Storage by extension vs ground truth")
    actual = {}
    for ext, cnt, byt in conn.execute(
        """SELECT COALESCE(NULLIF(fp.extension_key,''),'(none)'), COUNT(*),
                  COALESCE(SUM(fs.size_bytes),0)
             FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id
            WHERE fs.state='present' GROUP BY 1"""
    ):
        actual[ext] = {"count": cnt, "bytes": byt}
    expected = truth["by_extension"]
    check(f"distinct extensions == {len(expected)}", len(actual) == len(expected),
          f"got {len(actual)}: {sorted(set(actual) ^ set(expected))}")
    mismatched = [e for e in expected if actual.get(e) != expected[e]]
    check("every extension's count and bytes match", not mismatched,
          f"mismatched: {[(e, expected[e], actual.get(e)) for e in mismatched][:4]}")

    # -- exact duplicates -------------------------------------------------
    section("5. Exact duplicates vs ground truth")
    ensure_duplicate_projection(conn)
    groups, reclaimable = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(reclaimable_bytes),0) FROM p2_current_duplicate_summary"
    ).fetchone()
    td = truth["duplicates"]
    check(f"duplicate group count == {td['group_count']}",
          groups == td["group_count"], f"got {groups}")
    check(f"reclaimable bytes == {td['total_reclaimable_bytes']:,}",
          reclaimable == td["total_reclaimable_bytes"], f"got {reclaimable:,}")
    members = conn.execute("SELECT COUNT(*) FROM p2_current_duplicate_member").fetchone()[0]
    expected_members = sum(g["member_count"] for g in td["groups"])
    check(f"duplicate member count == {expected_members}",
          members == expected_members, f"got {members}")
    # The decoy is the same size as a real group but has different bytes.
    decoy = [f for f in truth["files"] if "decoy" in f["relative_path"]]
    if decoy:
        grouped = conn.execute(
            """SELECT COUNT(*) FROM p2_current_duplicate_member m
                 JOIN file_path fp ON fp.file_path_id=m.file_path_id
                WHERE fp.relative_path=?""", (decoy[0]["relative_path"],)).fetchone()[0]
        check("same-size-different-bytes decoy is NOT grouped as a duplicate",
              grouped == 0, f"decoy appears in {grouped} duplicate group(s)")

    # -- literal FTS ------------------------------------------------------
    section("6. Literal full-text index")
    check("SQLite runtime provides FTS5", fts.supported())
    t0 = time.perf_counter()
    built = fts.rebuild()
    build_ms = (time.perf_counter() - t0) * 1000
    check(f"FTS build indexed artifacts ({built['indexed']} texts, "
          f"{built['bytes_read']:,} bytes, {build_ms:.0f}ms)", built["indexed"] > 0)
    check("FTS build reported no missing evidence artifacts",
          built["missing_artifacts"] == 0, f"missing {built['missing_artifacts']}")

    for relpath, marker in truth["fts_markers"].items():
        phrase = marker["phrase"]
        expected = [relpath] if marker["expected_indexed"] else []
        query = {
            "query_schema": "fileorganizer.query/1",
            "semantic_contract": "fileorganizer.query-semantics/1",
            "label": f"FTS marker: {phrase}",
            "subject": {"entity": "file", "temporal": {"mode": "current"},
                        "current_file_states": ["present"]},
            "scope": {"kind": "project"},
            # No "select": the P2.4 AST has no projection clause. Each subject
            # returns its default column set, so compose the path from those.
            "where": {"all": [{"text_match": {
                "mode": "phrase",
                "query": {"kind": "literal", "value": phrase}}}]},
        }
        try:
            rows = engine.execute(query)["rows"]
            hits = sorted(
                "\\".join(p for p in (r.get("folder.parent"), r["path.file_name"]) if p)
                for r in rows)
            if marker["expected_indexed"]:
                label = f"phrase {phrase!r} matches only {Path(relpath).name}"
            else:
                # Pinned Phase 1 limitation, not a Phase 2 defect: content
                # extraction covers only .pdf .docx .pptx .xlsx .txt .md, so
                # this file's text never reaches the index. If extraction
                # coverage widens, this check fails and says so.
                label = (f"phrase {phrase!r} correctly absent "
                         f"({Path(relpath).suffix} not extracted by Phase 1)")
            check(label, hits == expected, f"got {hits}, expected {expected}")
        except Exception as exc:
            check(f"phrase {phrase!r} query runs", False, f"{type(exc).__name__}: {exc}")

    # FTS must read Project Evidence Artifacts, never original source files.
    check("FTS reads evidence artifacts under the project Runs folder, not source",
          "extracted_relpath" in [r[1] for r in conn.execute("PRAGMA table_info(p2_fts_text_map)")])

    # -- standard reports --------------------------------------------------
    section("7. All 31 standard reports, headless, no caller parameters")
    failed, empty = [], []
    slowest = ("", 0.0)
    for report in catalog.reports:
        rid = report["report_id"]
        t0 = time.perf_counter()
        try:
            result = catalog.run(engine, rid)
            ms = (time.perf_counter() - t0) * 1000
            if ms > slowest[1]:
                slowest = (rid, ms)
            if not result["rows"]:
                empty.append(rid)
        except Exception as exc:
            failed.append(f"{rid}: {type(exc).__name__}: {exc}")
    check(f"all {len(catalog.reports)} reports execute", not failed, "; ".join(failed[:3]))
    check(f"reports returning rows: {len(catalog.reports) - len(failed) - len(empty)}"
          f"/{len(catalog.reports)}  (empty: {', '.join(empty) if empty else 'none'})",
          len(empty) <= 8, f"{len(empty)} reports returned no rows")
    print(f"        slowest report: {slowest[0]} at {slowest[1]:.0f}ms")

    # -- deep keyset continuation ------------------------------------------
    section("8. Deep keyset continuation (never reached by the v1.2 run)")
    try:
        seen, cursor, pages = [], None, 0
        while pages < 20:
            page = engine.list_files(limit=25, after_id=cursor)
            rows = page["rows"] if isinstance(page, dict) else page
            if not rows:
                break
            seen.extend(r["path.id"] for r in rows)
            cursor = rows[-1]["path.id"]
            pages += 1
        check(f"keyset pagination walked {pages} page(s), {len(seen)} rows", pages > 1)
        check("keyset pagination returned no duplicate rows", len(seen) == len(set(seen)),
              f"{len(seen) - len(set(seen))} repeated row(s)")
        check(f"keyset pagination reached all {truth['totals']['file_count']} files",
              len(set(seen)) == truth["totals"]["file_count"], f"got {len(set(seen))}")
    except Exception as exc:
        check("keyset pagination runs", False, f"{type(exc).__name__}: {exc}")

    # -- the Master Matrix's cases ----------------------------------------
    if truth.get("cases") or truth.get("scan_expectations"):
        section("9. Master Matrix cases (each key of each case is one check)")
        import p2_cases
        p2_cases.assert_scan_expectations(project_dir, truth, check)
        p2_cases.assert_cases(conn, truth, check, project_dir=project_dir,
                              truth_path=TRUTH_PATH, home="Corpus")

    conn.close()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}")
    print(f"ACCEPTANCE: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
    print("=" * 70)
    return 0 if passed == len(RESULTS) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="project directory to test")
    ap.add_argument("--truth", required=True, help="GROUND_TRUTH.json for its corpus")
    args = ap.parse_args()
    global TRUTH_PATH
    TRUTH_PATH = Path(args.truth).resolve()
    truth = json.loads(TRUTH_PATH.read_text(encoding="utf-8"))
    return run(Path(args.project).resolve(), truth)


if __name__ == "__main__":
    raise SystemExit(main())
