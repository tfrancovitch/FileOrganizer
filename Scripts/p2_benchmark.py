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

# Published P2.5 medians at 1,000,000 files, for scale comparison.
P25_AT_1M = {
    "largest_100": 149.9,
    "extension_facet": 565.5,
    "root_facet": 310.9,
    "compound_filter": 67.3,
    "recursive_folder": 50.9,
    "stale_hash_by_root": 140.4,
    "duplicate_aggregation": 1156.4,
}


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
    }

    scale = n_files / 1_000_000
    print(f"\n{'probe':24} {'median ms':>10} {'P2.5@1M':>9} {'P2.5 scaled':>12} {'ratio':>7}")
    print("-" * 68)
    for name, sql in probes.items():
        try:
            med, lo, hi, _ = timed(lambda s=sql: conn.execute(s).fetchall(), args.repeat)
            claimed = P25_AT_1M[name]
            expected = claimed * scale
            ratio = med / expected if expected else float("nan")
            out["probes"][name] = {"median_ms": round(med, 3),
                                   "p25_at_1m_ms": claimed,
                                   "p25_scaled_ms": round(expected, 2),
                                   "ratio_vs_scaled": round(ratio, 2)}
            print(f"{name:24} {med:10.2f} {claimed:9.1f} {expected:12.2f} {ratio:6.2f}x")
        except Exception as exc:
            out["probes"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"{name:24} {'FAIL':>10}  {type(exc).__name__}: {exc}")

    print("\n'ratio' is measured time divided by the P2.5 figure linearly scaled to this")
    print("corpus size. Under 1.0 means faster than the published claim scales to; over")
    print("1.0 means slower. Linear scaling is an assumption, not a guarantee.")

    conn.close()
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
