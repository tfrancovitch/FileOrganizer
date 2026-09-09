#!/usr/bin/env python3
r"""Time Phase 2 against a real corpus, and compare with the P2.5 claims.

The P2.5 benchmark report and the P2.10 million-row validation were measured
against synthetically injected rows -- P2.10 records build_seconds: 9.78 for a
million "files", which is row insertion, not a pipeline run. This times the same
shapes of query against a corpus that was really scanned, really hashed and
really stored, so the numbers include the indexes and row widths a user gets.

Each probe runs `--repeat` times; the median is reported, since a first run on a
cold page cache is not the number a user experiences repeatedly.

Usage:
    python Scripts/p2_benchmark.py --project <project-dir> [--repeat 5]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

# Published P2.5 medians. The report tabulates 10k/60k/100k/500k/1M, so a 100k
# corpus can be compared directly rather than extrapolated -- prefer the row that
# matches the corpus at hand over scaling from 1M.
P25_BY_SCALE = {
    100_000: {
        "largest_100": 16.2,
        "extension_facet": 43.7,
        "root_facet": 29.1,
        "compound_filter": 2.8,
        "recursive_folder": 2.0,
        "stale_hash_by_root": 12.8,
        "duplicate_aggregation": 113.2,
        # Section 7, at 100k files / 118,000 analyzer-result rows.
        "analyzer_targeted": 5.4,
        "analyzer_failures_window": 159.0,
        "analyzer_failures_not_exists": 38.5,
    },
    1_000_000: {
        "largest_100": 149.9,
        "extension_facet": 565.5,
        "root_facet": 310.9,
        "compound_filter": 67.3,
        "recursive_folder": 50.9,
        "stale_hash_by_root": 140.4,
        "duplicate_aggregation": 1156.4,
        "analyzer_targeted": 73.9,
        "analyzer_failures_window": 1646.7,
        "analyzer_failures_not_exists": 411.7,
    },
}


def nearest_published_scale(n_files):
    return min(P25_BY_SCALE, key=lambda k: abs(k - n_files))


def timed(fn, repeat):
    samples = []
    result = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples), min(samples), max(samples), result


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    from Phase2.core import connect, project_info
    from Phase2.derived import ensure_duplicate_projection
    from Phase2.fts import FtsManager
    from Phase2.query import QueryEngine
    from Phase2.reports import ReportCatalog
    from Phase2.saved import SavedQueryStore

    project_dir = Path(args.project).resolve()
    conn = connect(project_dir, write=True)
    engine = QueryEngine(conn, saved_store=SavedQueryStore(conn),
                         fts_manager=FtsManager(conn, project_dir))
    catalog = ReportCatalog()

    n_files, n_bytes = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM file_state WHERE state='present'"
    ).fetchone()
    db_bytes = (project_dir / "Database" / "FileOrganizer.db").stat().st_size

    print(f"project        : {project_info(conn).get('name')}")
    print(f"files          : {n_files:,}")
    print(f"logical bytes  : {n_bytes:,}")
    print(f"database bytes : {db_bytes:,}  ({db_bytes / n_files:.0f} bytes/file)")
    print(f"repeat         : {args.repeat} (median reported)")

    out: dict = {"files": n_files, "logical_bytes": n_bytes, "db_bytes": db_bytes,
                 "repeat": args.repeat, "reports": {}, "probes": {}}

    # -- every standard report -------------------------------------------
    print(f"\n{'report':10} {'median ms':>10} {'min':>8} {'max':>8} {'rows':>8}")
    print("-" * 48)
    worst = []
    for report in catalog.reports:
        rid = report["report_id"]
        try:
            med, lo, hi, res = timed(lambda r=rid: catalog.run(engine, r), args.repeat)
            rows = len(res["rows"])
            out["reports"][rid] = {"median_ms": round(med, 2), "rows": rows}
            worst.append((med, rid, rows))
            print(f"{rid:10} {med:10.2f} {lo:8.2f} {hi:8.2f} {rows:8,}")
        except Exception as exc:
            out["reports"][rid] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"{rid:10} {'FAIL':>10}  {type(exc).__name__}: {exc}")

    worst.sort(reverse=True)
    print(f"\nslowest three: " + ", ".join(f"{r} {m:.0f}ms" for m, r, _ in worst[:3]))

    # -- P2.5-shaped probes, in raw SQL against the same schema -----------
    ensure_duplicate_projection(conn)
    probes = {
        "largest_100":
            """SELECT fp.relative_path, fs.size_bytes
                 FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                WHERE fs.state='present' ORDER BY fs.size_bytes DESC LIMIT 100""",
        "extension_facet":
            """SELECT fp.extension_key, COUNT(*), SUM(fs.size_bytes)
                 FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                WHERE fs.state='present' GROUP BY fp.extension_key""",
        "root_facet":
            """SELECT fs.source_root_id, COUNT(*), SUM(fs.size_bytes)
                 FROM file_state fs WHERE fs.state='present' GROUP BY fs.source_root_id""",
        "compound_filter":
            """SELECT COUNT(*), COALESCE(SUM(fs.size_bytes),0)
                 FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                WHERE fs.state='present' AND fp.extension_key='.txt'
                  AND fs.size_bytes >= 2000 AND fs.modified_utc < '2021-01-01T00:00:00Z'""",
        "recursive_folder":
            """SELECT COUNT(*), COALESCE(SUM(fs.size_bytes),0)
                 FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                WHERE fs.state='present' AND fo_path_under(fp.relative_path_key,'dept_00')=1""",
        "stale_hash_by_root":
            """SELECT fs.source_root_id, COUNT(*) FROM file_state fs
                WHERE fs.state='present' AND (fs.hash_status IS NULL OR fs.hash_status<>'current')
                GROUP BY fs.source_root_id""",
        "duplicate_aggregation":
            """SELECT COUNT(*), COALESCE(SUM(reclaimable_bytes),0)
                 FROM p2_current_duplicate_summary""",
        # -- analyzer workload, P2.5 section 7 -----------------------------
        # Targeted lookup on a promoted analyzer column for one analyzer key.
        "analyzer_targeted":
            """SELECT COUNT(*) FROM analyzer_result ar
                 JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
                 JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
                WHERE a.analyzer_key='text' AND ar.title IS NOT NULL""",
        # Strategy A -- ROW_NUMBER() window. This is what Phase 2 ships:
        # coverage.evidence_health() uses exactly this shape, and the GUI
        # Overview calls it on every load.
        "analyzer_failures_window":
            """WITH current_ar AS (
                 SELECT ar.status,
                        ROW_NUMBER() OVER (PARTITION BY fo.file_path_id,a.analyzer_key
                                           ORDER BY ar.analyzed_utc DESC,
                                                    ar.analyzer_result_id DESC) rn
                   FROM analyzer_result ar
                   JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
                   JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
                   JOIN file_observation fo ON fo.file_observation_id=ar.file_observation_id
                   JOIN file_state fs ON fs.file_path_id=fo.file_path_id
                                     AND fs.current_observation_id=ar.file_observation_id
               )
               SELECT COUNT(*) FROM current_ar WHERE rn=1 AND status='error'""",
        # Strategy B -- correlated NOT EXISTS. P2.5 measured this ~4x faster
        # and its disposition recommended targeted/correlated strategies.
        # fts.exists_sql_for_current_file() already uses this shape.
        "analyzer_failures_not_exists":
            """SELECT COUNT(*) FROM analyzer_result ar
                 JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
                 JOIN file_observation fo ON fo.file_observation_id=ar.file_observation_id
                 JOIN file_state fs ON fs.file_path_id=fo.file_path_id
                                   AND fs.current_observation_id=ar.file_observation_id
                WHERE ar.status='error'
                  AND NOT EXISTS (
                      SELECT 1 FROM analyzer_result n
                       JOIN analyzer_run nr ON nr.analyzer_run_id=n.analyzer_run_id
                       WHERE n.file_observation_id=ar.file_observation_id
                         AND nr.analyzer_id=rr.analyzer_id
                         AND (n.analyzed_utc>ar.analyzed_utc
                              OR (n.analyzed_utc=ar.analyzed_utc
                                  AND n.analyzer_result_id>ar.analyzer_result_id)))""",
    }

    published_at = nearest_published_scale(n_files)
    published = P25_BY_SCALE[published_at]
    print(f"\nP2.5 comparison at its published {published_at:,}-file row "
          f"(this corpus: {n_files:,} files)")
    print(f"{'probe':30} {'median ms':>10} {'P2.5':>9} {'ratio':>8}")
    print("-" * 60)
    for name, sql in probes.items():
        try:
            med, lo, hi, res = timed(lambda s=sql: conn.execute(s).fetchall(), args.repeat)
            claimed = published[name]
            ratio = med / claimed if claimed else float("nan")
            out["probes"][name] = {"median_ms": round(med, 3),
                                   "p25_ms": claimed,
                                   "p25_scale": published_at,
                                   "ratio": round(ratio, 2),
                                   "first_row": [str(v) for v in res[0]] if res else None}
            print(f"{name:30} {med:10.2f} {claimed:9.1f} {ratio:7.2f}x")
        except Exception as exc:
            out["probes"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"{name:30} {'FAIL':>10}  {type(exc).__name__}: {exc}")

    # -- folder scoping: shipped helper vs the index Phase 1 already builds --
    print("\nFolder scoping -- same question, four formulations")
    print(f"{'formulation':34} {'median ms':>10} {'result':>22}")
    print("-" * 68)
    top = conn.execute(
        "SELECT fo_top_level(relative_path) t FROM file_path WHERE t<>'' LIMIT 1").fetchone()
    if top:
        scope = top[0]
        lo_scope = scope.lower()
        variants = {
            "fo_path_under() [shipped]": (
                """SELECT COUNT(*),COALESCE(SUM(fs.size_bytes),0) FROM file_state fs
                     JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                    WHERE fs.state='present' AND fo_path_under(fp.relative_path_key,?)=1""",
                (scope,)),
            "LIKE prefix": (
                """SELECT COUNT(*),COALESCE(SUM(fs.size_bytes),0) FROM file_state fs
                     JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                    WHERE fs.state='present'
                      AND (fp.relative_path_key=? OR fp.relative_path_key LIKE ?)""",
                (lo_scope, lo_scope + "\\%")),
            "path_sort_key range [indexed]": (
                """SELECT COUNT(*),COALESCE(SUM(fs.size_bytes),0) FROM file_state fs
                     JOIN file_path fp ON fp.file_path_id=fs.file_path_id
                    WHERE fs.state='present' AND fp.source_root_id=1
                      AND fp.path_sort_key>=? AND fp.path_sort_key<?""",
                (lo_scope, lo_scope + "\x01")),
        }
        out["folder_scope"] = {"scope": scope}
        for label, (sql, binds) in variants.items():
            med, _, _, res = timed(lambda s=sql, b=binds: conn.execute(s, b).fetchall(),
                                   args.repeat)
            row = tuple(res[0])
            out["folder_scope"][label] = {"median_ms": round(med, 3), "result": list(row)}
            print(f"{label:34} {med:10.2f} {str(row):>22}")
        print("\nAll formulations must return identical results; the times are the point.")

    conn.close()
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
