#!/usr/bin/env python3
r"""The hostile corpus, end to end: build it, scan it, assert it, prove it untouched.

`Corpus\` is what the acceptance and dashboard suites run on: static and
tool-safe. `Hostile\` is the rest of the Master Matrix -- folders the walk
cannot list, junctions back to their parent, symbolic links out of the
root, three names for one file, CON and `trailing.`, ten thousand files in
one directory, a 4 GB file that occupies nothing -- built by the same
builder under --hostile, scanned by its own project so its denied folder
and its folded rows change no other suite's totals.

What this does, in order:

  1. Builds C:\FOTest\Hostile (unless it exists and --rebuild is not given)
     and reads HOSTILE_GROUND_TRUTH.json.
  2. Recreates the P2Hostile project in the install's Projects folder and
     runs every stage through RunWorker: Pre-Scan, Full Fingerprinting,
     Analyze all, Extract text, Index text.
  3. Asserts: the scan completed with warnings and counted the denied
     folders; the present rows are the truth's less the rows the engine is
     known to fold; the preliminary report's totals; every matrix case
     (p2_cases, home Hostile); the canary paths; and, last, that no file in
     the tree changed -- size, modified time, and the hash of everything
     readable -- and nothing new appeared.
  4. Leaves the tree and the project for a person to look at; --teardown
     removes the tree (ACLs restored, links not followed).

Usage:
    py -3 Scripts\p2_hostile_check.py [--root C:\FOTest] [--rebuild] [--teardown]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

APP_ROOT = Path(r"C:\FileOrganizerTesting\FileOrganizer-Phase1-RC-B6.1")
RESULTS: list[tuple[str, bool, str]] = []


# A check name can carry any filename in the corpus; the console may be cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# 1. The tree
# ---------------------------------------------------------------------------

def build_tree(base: Path, rebuild: bool) -> dict:
    import p2_build_acceptance_corpus as builder
    tree, truth_path = base / "Hostile", base / "HOSTILE_GROUND_TRUTH.json"
    if rebuild or not tree.is_dir() or not truth_path.is_file():
        print(f"building {tree} ...")
        builder.remove_tree(tree)
        os.makedirs(builder.ext_path(tree))
        matrix = builder.read_matrix(base / builder.MATRIX_CSV)
        corpus = builder.HostileCorpus(tree, base / "Research" / "Samples", matrix)
        corpus.build()
        truth = corpus.ground_truth()
        truth_path.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        print(f"using {tree} as it stands")
    return json.loads(truth_path.read_text(encoding="utf-8"))


def fingerprint(base: Path, truth: dict) -> dict:
    """Size, mtime and (where readable) SHA-256 of every file the truth lists,
    plus the set of every path that exists under the tree -- what must not
    change while the program runs."""
    import p2_build_acceptance_corpus as builder
    tree = base / "Hostile"
    prints, unreadable = {}, []
    for f in truth["files"]:
        target = builder.ext_path(tree / f["relative_path"])
        st = os.lstat(target)
        digest = None
        if f.get("sha256"):
            try:
                h = hashlib.sha256()
                with open(target, "rb") as handle:
                    for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
                        h.update(chunk)
                digest = h.hexdigest()
            except PermissionError:
                unreadable.append(f["relative_path"])
        prints[f["relative_path"]] = (st.st_size, st.st_mtime_ns, digest)
    everything = set()
    for dirpath, dirnames, filenames in os.walk(builder.ext_path(tree)):
        # never enter a junction or symlink: that is what the walk under test must not do either
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))
                       and not _is_reparse(os.path.join(dirpath, d))]
        for name in filenames + dirnames:
            everything.add(os.path.join(dirpath, name))
    return {"files": prints, "unreadable": unreadable, "everything": everything}


def _is_reparse(path):
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# 2. The project
# ---------------------------------------------------------------------------

def run_project(base: Path, name: str) -> Path:
    import p2_build_acceptance_corpus as builder
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, FINGERPRINT, ANALYSIS, INDEX_TEXT
    project_dir = APP_ROOT / "Projects" / name
    if project_dir.exists():
        builder.remove_tree(project_dir)

    def run(project, request):
        log = []
        started = time.monotonic()
        outcome = RunWorker(APP_ROOT, project, request, log=log.append, progress=lambda *a: None).run()
        print(f"  {request.title:<20} {outcome.status:<26} {time.monotonic() - started:6.1f}s  {outcome.message}")
        check(f"stage runs: {request.title}", outcome.ok, f"{outcome.status}: {outcome.message}; " + " | ".join(log[-3:]))
        return outcome

    run(None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(base / "Hostile")], project_name=name))
    if not (project_dir / "Database" / "FileOrganizer.db").is_file():
        raise SystemExit("the project was not created")
    run(project_dir, RunRequest(FINGERPRINT, "Full Fingerprinting"))
    run(project_dir, RunRequest(ANALYSIS, "Analyze all",
                                analyzer_keys=["image", "pdf", "office", "raw_image", "audio", "video", "text", "archive"]))
    run(project_dir, RunRequest(ANALYSIS, "Extract text", analyzer_keys=["content_extraction"]))
    run(project_dir, RunRequest(INDEX_TEXT, "Index text"))
    return project_dir


# ---------------------------------------------------------------------------
# 3. The assertions
# ---------------------------------------------------------------------------

def assert_project(base: Path, project_dir: Path, truth: dict, before: dict):
    import p2_cases
    from Phase2.core import connect, require_phase2_schema
    from Phase2.derived import ensure_duplicate_projection
    from Phase2.fts import FtsManager
    from Phase2.query import QueryEngine
    from Phase2.saved import SavedQueryStore

    conn = connect(project_dir, write=True)
    require_phase2_schema(conn)
    ensure_duplicate_projection(conn)
    engine = QueryEngine(conn, saved_store=SavedQueryStore(conn), fts_manager=FtsManager(conn, project_dir))
    hostile = truth["hostile"]

    section("1. The scan")
    scan = conn.execute("SELECT status, observed_count, inaccessible_count FROM inventory_scan "
                        "ORDER BY inventory_scan_id DESC LIMIT 1").fetchone()
    check("the scan completed with warnings (denied folders are directory errors)",
          scan and scan["status"] == "completed_with_warnings", f"scan={dict(scan) if scan else None}")
    check(f"inaccessible locations == {len(hostile['denied_folders'])} (the denied folders)",
          scan and scan["inaccessible_count"] == len(hostile["denied_folders"]), f"got {scan['inaccessible_count'] if scan else None}")
    present = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='present'").fetchone()[0]
    check(f"present rows == {hostile['expected_present_rows']} (the truth's {truth['totals']['file_count']} files "
          f"less {len(hostile['folded'])} the engine is known to fold)",
          present == hostile["expected_present_rows"], f"got {present}")
    inaccessible = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='inaccessible'").fetchone()[0]
    check(f"inaccessible rows == {len(hostile['denied_folders'])}", inaccessible == len(hostile["denied_folders"]), f"got {inaccessible}")
    outside = conn.execute("SELECT COUNT(*) FROM file_path WHERE relative_path LIKE '..%' OR relative_path LIKE '_:%'").fetchone()[0]
    check("no row's path leaves the root", outside == 0, f"{outside} rows")
    unobservable = [p for p in hostile["unobservable"]
                    if conn.execute("SELECT 1 FROM file_path WHERE relative_path = ?", (p,)).fetchone()]
    check(f"none of the {len(hostile['unobservable'])} files under denied folders has a row", not unobservable, str(unobservable[:3]))
    n_bytes = conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM file_state WHERE state='present'").fetchone()[0]
    check(f"logical bytes == {hostile['expected_logical_bytes']:,} (the truth's {truth['totals']['logical_bytes']:,} "
          f"less the bytes of each folded pair's kept name -- the chimera carries the dropped file's size)",
          n_bytes == hostile["expected_logical_bytes"], f"got {n_bytes:,}")

    section("2. The preliminary report")
    p2_cases.assert_scan_expectations(project_dir, truth, check)

    section("3. Duplicates by physical object")
    groups, reclaimable = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(reclaimable_bytes),0) FROM p2_current_duplicate_summary").fetchone()
    td = truth["duplicates"]
    conflations = hostile.get("symlink_conflations", [])
    extra_groups, extra_bytes = len(conflations), sum(c["bytes"] for c in conflations)
    if conflations:
        print(f"        ({extra_groups} known conflation(s): a file symbolic link grouped with its target -- C-011, a KNOWN DEFECT)")
    check(f"duplicate groups == {td['group_count']} + {extra_groups} known conflation(s)",
          groups == td["group_count"] + extra_groups, f"got {groups}")
    check(f"reclaimable bytes == {td['total_reclaimable_bytes']:,} (hard links reclaim nothing) + {extra_bytes} from the known conflation(s)",
          reclaimable == td["total_reclaimable_bytes"] + extra_bytes, f"got {reclaimable:,}")

    section("4. Master Matrix cases (each key of each case is one check)")
    p2_cases.assert_cases(conn, truth, check, project_dir=project_dir,
                          truth_path=base / "HOSTILE_GROUND_TRUTH.json", home="Hostile", engine=engine)
    conn.close()

    section("5. Nothing outside the root, nothing changed inside it")
    after = fingerprint(base, truth)
    changed = [p for p, v in before["files"].items() if after["files"].get(p) != v]
    check(f"every one of the {len(before['files'])} files has the size, modified time and bytes it was built with",
          not changed, f"changed: {changed[:5]}")
    appeared = sorted(after["everything"] - before["everything"])
    vanished = sorted(before["everything"] - after["everything"])
    check("no path appeared or vanished under the tree", not appeared and not vanished,
          f"appeared {appeared[:3]}, vanished {vanished[:3]}")
    if before["unreadable"]:
        print(f"        ({len(before['unreadable'])} read-denied files compared by size and time only: they cannot be hashed by anyone)")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=r"C:\FOTest")
    ap.add_argument("--project-name", default="P2Hostile")
    ap.add_argument("--rebuild", action="store_true", help="rebuild Hostile\\ even if it exists")
    ap.add_argument("--teardown", action="store_true", help="remove Hostile\\ afterwards (the project is kept)")
    args = ap.parse_args()
    base = Path(args.root)

    truth = build_tree(base, args.rebuild)
    print(f"truth: {truth['totals']['file_count']:,} files, {truth['totals']['logical_bytes']:,} bytes, "
          f"{len(truth['cases'])} cases, {len(truth['hostile']['denied_folders'])} denied folders, "
          f"{len(truth['hostile']['link_folders'])} link folders")
    before = fingerprint(base, truth)

    section("0. The stages, through RunWorker")
    project_dir = run_project(base, args.project_name)
    assert_project(base, project_dir, truth, before)

    if args.teardown:
        import p2_build_acceptance_corpus as builder
        builder.remove_tree(base / "Hostile")
        print(f"\nremoved {base / 'Hostile'}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nHOSTILE: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
    print("=" * 70)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
