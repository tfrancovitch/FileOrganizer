#!/usr/bin/env python3
r"""The filesystem changes while the program looks at it: matrix section I.

A static corpus cannot represent time, so this check makes a small tree in
%TEMP%, scans it once at rest, then scans it again while changing it at a
known moment -- the instant the walk lists folder D3, which is after D1
and D2 have been listed and yielded and before D4, D5 and D6 have. The
moment is deterministic because the walk lists folders in sorted order and
`fo_scan._list_directory` is the one function every listing goes through;
wrapping it is how the change is timed. Everything else is the real
product path: RunWorker, the coordinator, the walk, the ingest, the hash
stage.

What changes at that moment, and what the program is expected to say:

  I-001  D5\new.txt created (D5 not yet listed)       observed
         D1\late.txt created (D1 already listed)      no row until the next scan
  I-002  D2\f2.txt deleted (already listed)           observed; the hash stage says FILE MISSING
  I-003  D2\f3.txt renamed (already listed)           old name observed then FILE MISSING; new name absent
  I-005  D2\f1.txt rewritten larger (already listed)  the observation keeps the listed size; the hash
                                                      measurement says 51 bytes with the digest of 232
  I-008  D6 deleted whole (not yet listed)            its listing fails: DIRECTORY ACCESS ERROR, inaccessible
  D-003b D4 made unlistable (not yet listed)          DIRECTORY ACCESS ERROR -- and its files, known from
                                                      the scan at rest: what state do they get?
  Y-043  D3\f1.txt held open exclusively during       the hash stage says ACCESS DENIED (a sharing
         the hash stage                               violation reads the same as a denied ACL)
  Y-045  the root itself gone before a scan           root_availability 'missing'; nothing marked missing

Then a third scan at rest, with D4 listable again, checks recovery: the
deleted file is missing, the renamed one is present under its new name,
D6's files are missing, D4's files are present.

The cancel-during-walk case (an interrupted scan marks nothing missing) is
held by p2_regression and the dashboard suite already; it is not repeated.

Usage:
    py -3 Scripts\p2_mutation_check.py [--root C:\FOTest] [--keep]
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

RESULTS: list[tuple[str, bool, str]] = []


# A check name can carry any filename in the corpus; the console may be cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def body(d, f):
    return (f"folder {d}, file {f}\n" * (d + f)).encode("ascii")


REWRITTEN = ("rewritten, and longer than it was when the walk listed it\n" * 4).encode("ascii")


def make_tree(root: Path):
    for d in range(1, 7):
        for f in range(1, 4):
            path = root / f"D{d}" / f"f{f}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body(d, f))
            stamp = 1_750_000_000 + d * 1000 + f
            os.utime(path, (stamp, stamp))


def run_stage(app_root, project_dir, request):
    from Phase2.runner import RunWorker
    log = []
    outcome = RunWorker(app_root, project_dir, request, log=log.append, progress=lambda *a: None).run()
    check(f"stage runs: {request.title}", outcome.ok or outcome.status == "completed_with_warnings",
          f"{outcome.status}: {outcome.message}; " + " | ".join(log[-3:]))
    return outcome


class ExclusiveHold:
    """A handle that shares nothing: what an application holding a file
    open looks like to everyone else."""
    def __init__(self, path):
        self.path, self.handle = str(path), None

    def __enter__(self):
        k = ctypes.windll.kernel32
        k.CreateFileW.restype = ctypes.c_void_p
        self.handle = k.CreateFileW(self.path, 0x80000000, 0, None, 3, 0x80, None)
        if self.handle in (None, -1, 0xFFFFFFFFFFFFFFFF):
            raise OSError("could not hold %s" % self.path)
        return self

    def __exit__(self, *exc):
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=r"C:\FOTest", help="where MUTATION_GROUND_TRUTH.json and the results are written")
    ap.add_argument("--keep", action="store_true", help="leave the scratch tree and project behind")
    args = ap.parse_args()

    import p2_build_acceptance_corpus as builder
    import p2_cases
    import fo_scan
    from Phase2.core import connect
    from Phase2.runner import RunRequest, PRESCAN, FINGERPRINT

    matrix = builder.read_matrix(Path(args.root) / builder.MATRIX_CSV)
    tmp = Path(tempfile.mkdtemp(prefix="p2_mutation_"))
    app_root = tmp / "AppRoot"
    (app_root / "Projects").mkdir(parents=True)
    tree = tmp / "Tree"
    make_tree(tree)
    project_dir = app_root / "Projects" / "Mutation"
    print(f"scratch tree {tree}")

    def case(case_id, paths, expected, **extra):
        row = matrix.get(case_id, {})
        record = {"id": case_id, "condition": extra.pop("condition", None) or row.get("condition"),
                  "priority": row.get("priority"), "disposition": row.get("disposition"), "layer": row.get("layer"),
                  "construction_in_matrix": row.get("construction"), "construction": "runner", "home": "Mutation",
                  "paths": paths, "setup": [], "teardown": [], "expected": expected,
                  "matrix_expects": extra.pop("matrix_expects", None) or row.get("expected_behavior"),
                  "safety": [], "status": "CONSTRUCTED", "classification": extra.pop("classification", None),
                  "notes": extra.pop("notes", "")}
        return record

    try:
        # -- scan at rest ------------------------------------------------------
        section("1. The tree at rest")
        run_stage(app_root, None, RunRequest(PRESCAN, "Pre-Scan (at rest)", source_roots=[str(tree)], project_name="Mutation"))
        run_stage(app_root, project_dir, RunRequest(FINGERPRINT, "Full Fingerprinting (at rest)"))
        conn = connect(project_dir)
        present = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='present'").fetchone()[0]
        conn.close()
        check("18 files present at rest", present == 18, f"got {present}")

        # -- the scan during which the tree changes ----------------------------
        section("2. The tree changes while the walk lists D3")
        fired = {"done": False}
        original = fo_scan._list_directory

        def mutate():
            (tree / "D5" / "new.txt").write_text("created while the walk was in D3\n")
            (tree / "D1" / "late.txt").write_text("created after D1 was listed\n")
            (tree / "D2" / "f2.txt").unlink()
            (tree / "D2" / "f3.txt").rename(tree / "D2" / "f3_renamed.txt")
            (tree / "D2" / "f1.txt").write_bytes(REWRITTEN)
            shutil.rmtree(tree / "D6")
            builder.icacls(str(tree / "D4"), "/deny", "%s\\%s:(RD,X)" % (os.environ["USERDOMAIN"], os.environ["USERNAME"]), "/Q")

        def listing_with_mutation(directory):
            if not fired["done"] and builder.plain_path(str(directory)).lower().endswith("\\d3"):
                fired["done"] = True
                mutate()
            return original(directory)

        fo_scan._list_directory = listing_with_mutation
        try:
            run_stage(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan (changing)"))
        finally:
            fo_scan._list_directory = original
        check("the mutation fired when D3 was listed", fired["done"])
        with ExclusiveHold(tree / "D3" / "f1.txt"):
            run_stage(app_root, project_dir, RunRequest(FINGERPRINT, "Full Fingerprinting (D3\\f1.txt held open)"))

        truth = {"cases": [
            case("I-001", ["D5\\new.txt"], {"file_state.state": "present"},
                 notes="created in a folder not yet listed: observed by the same scan"),
            case("I-001b", ["D1\\late.txt"], {"row_count": 0}, condition="File created during scan, in a folder already listed",
                 notes="no row until the next scan; the observation is of the moment D1 was listed"),
            case("I-002", ["D2\\f2.txt"], {"file_state.state": "present", "hash.error_kind": "FILE MISSING"},
                 notes="listed before it was deleted: observed, then the hash stage finds nothing to open"),
            case("I-003", ["D2\\f3.txt"], {"file_state.state": "present", "hash.error_kind": "FILE MISSING"},
                 notes="the old name was listed: observed, then FILE MISSING at the hash stage"),
            case("I-003b", ["D2\\f3_renamed.txt"], {"row_count": 0}, condition="File renamed during scan: the new name",
                 notes="absent until the next scan"),
            case("I-004", ["D2\\f3.txt"], {"hash.error_kind": "FILE MISSING"}, notes="a rename is a move within the tree: the same observation as I-003"),
            case("I-005", ["D2\\f1.txt"], {"file_state.state": "present", "size_bytes": len(body(2, 1)), "hash.size_bytes": len(body(2, 1)),
                                            "hash.full_hash": hashlib.sha256(REWRITTEN).hexdigest().upper()},
                 classification="DEFECT", matrix_expects="Detect/change state if feasible",
                 notes="observed 2026-09-13: listed at 51 bytes, rewritten to 232, then hashed -- the measurement records size_bytes 51 (copied from "
                       "the observation) with the digest of the 232 new bytes, and nothing marks the observation stale; the file's size at open "
                       "time would have told the hash stage the file had changed. For the user's decision"),
            case("I-008", ["D6"], {"file_observation.status": "inaccessible", "file_observation.error_kind": "DIRECTORY ACCESS ERROR", "rows_below": 3},
                 classification="SCOPE", matrix_expects="Continue safely; report the folder as gone",
                 notes="a folder deleted before it was listed fails its listing like a denied one: DIRECTORY ACCESS ERROR (the OS error text says the path was not found); its three files keep their rows"),
            case("D-003b", ["D4"], {"file_observation.status": "inaccessible", "file_observation.error_kind": "DIRECTORY ACCESS ERROR"},
                 condition="Folder made unlistable between two scans", notes="see D-003c for what happens to the files it holds"),
            case("Y-043", ["D3\\f1.txt"], {"file_state.state": "present", "hash.error_kind": "ACCESS DENIED"},
                 classification="SCOPE", matrix_expects="Metadata scan continues where possible; distinguish a lock from a denial",
                 notes="a sharing violation is reported as ACCESS DENIED, the same words as a denied ACL (the hash engine classifies only as the OS does)"),
            case("D-011", ["D3\\f1.txt"], {"file_state.state": "present", "hash.error_kind": "ACCESS DENIED"},
                 classification="SCOPE", matrix_expects="Inventory metadata if possible; no crash",
                 notes="the locked file of Y-043: its metadata is inventoried, its bytes are not read, nothing crashes"),
        ]}
        conn = connect(project_dir)
        p2_cases.assert_cases(conn, truth, check, project_dir=project_dir, truth_path=Path(args.root) / "MUTATION_GROUND_TRUTH.json", home="Mutation")

        # The question the matrix cares most about: D4 still exists and holds
        # its three files; the scan could not list it. Are they missing now?
        section("3. Files under a folder that could not be listed")
        states = dict(conn.execute("SELECT fp.relative_path, fs.state FROM file_path fp JOIN file_state fs USING(file_path_id) "
                                   "WHERE fp.relative_path LIKE 'D4\\%' ESCAPE '!'").fetchall())
        d6_states = dict(conn.execute("SELECT fp.relative_path, fs.state FROM file_path fp JOIN file_state fs USING(file_path_id) "
                                      "WHERE fp.relative_path LIKE 'D6\\%' ESCAPE '!'").fetchall())
        scan = conn.execute("SELECT status, inaccessible_count, vanished_count FROM inventory_scan ORDER BY inventory_scan_id DESC LIMIT 1").fetchone()
        print(f"        D4 files after the changing scan: {states}")
        print(f"        D6 files after the changing scan: {d6_states}")
        print(f"        scan: {dict(scan)}")
        conn.close()
        truth["cases"].append(case("D-003c", sorted(states), {"file_state.state": "missing"},
                                   condition="Files under a folder that could not be listed, known from the scan before",
                                   classification="DEFECT",
                                   matrix_expects="Exists but cannot be accessed -- not missing; MISSING needs evidence of absence",
                                   notes="observed 2026-09-13: mark_vanished marks every location a completed scan did not see as 'missing', "
                                         "and a directory error does not stop it -- so the three files under D4, which exist and were merely "
                                         "unlistable, are 'missing'. A dropped share or a pulled drive mid-walk does the same to everything under it. "
                                         "For the user's decision"))
        conn = connect(project_dir)
        p2_cases.assert_case(conn, truth, truth["cases"][-1], check)
        conn.close()

        # -- recovery ------------------------------------------------------------
        section("4. A scan at rest afterwards (D4 listable again)")
        builder.reset_acl(str(tree / "D4"))
        run_stage(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan (recovered)"))
        conn = connect(project_dir)
        recovery = {"cases": [
            case("I-002r", ["D2\\f2.txt"], {"file_state.state": "missing"}, condition="Deleted file, next scan", notes="now there is evidence: missing"),
            case("I-003r", ["D2\\f3_renamed.txt"], {"file_state.state": "present"}, condition="Renamed file, next scan: the new name"),
            case("I-001r", ["D1\\late.txt", "D5\\new.txt"], {"file_state.state": "present"}, condition="Files created during the previous scan"),
            case("I-008r", ["D6\\f1.txt", "D6\\f2.txt", "D6\\f3.txt"], {"file_state.state": "missing"}, condition="Deleted folder's files, next scan"),
            case("D-003r", ["D4\\f1.txt", "D4\\f2.txt", "D4\\f3.txt"], {"file_state.state": "present"},
                 condition="Files under the folder that was unlistable, once it can be listed again", notes="present again, with their history"),
        ]}
        for c in recovery["cases"]:
            p2_cases.assert_case(conn, recovery, c, check)
        conn.close()
        truth["cases"].extend(recovery["cases"])

        # -- the root itself gone ------------------------------------------------
        section("5. The root is not there (Y-045)")
        conn = connect(project_dir)
        before_present = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='present'").fetchone()[0]
        before_scans = conn.execute("SELECT COUNT(*) FROM inventory_scan").fetchone()[0]
        conn.close()
        parked = tmp / "Tree_parked"
        os.rename(tree, parked)
        try:
            from Phase2.runner import RunWorker
            outcome = RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan (root gone)"), log=lambda *a: None, progress=lambda *a: None).run()
        finally:
            os.rename(parked, tree)
        check("a scan of a root that is not there fails, plainly", outcome.status == "failed", f"{outcome.status}: {outcome.message}")
        conn = connect(project_dir)
        after_present = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='present'").fetchone()[0]
        after_scans = conn.execute("SELECT COUNT(*) FROM inventory_scan").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM inventory_scan WHERE inventory_scan_id > ? AND status IN ('completed','completed_with_warnings')",
                                 (before_scans,)).fetchone()[0] if after_scans > before_scans else 0
        conn.close()
        check("no completed scan was recorded for the absent root", completed == 0, f"{completed} completed scan(s) added")
        check(f"every file keeps its last known state ({before_present} present before, {after_present} after)", before_present == after_present)
        truth["cases"].append(case("Y-045", ["D1\\f1.txt"], {"file_state.state": "present"},
                                   notes="the root was renamed away before the scan: the Pre-Scan fails ('The inventory scan failed'), no completed scan is recorded, and every file keeps its state -- nothing is marked missing"))

        record = {"generator": "p2_mutation_check.py", "tree": "six folders of three files, made in %TEMP%",
                  "cases": truth["cases"], "not_constructed": [
                      {"id": "I-006", "reason": "replace-during-scan is the delete and create of I-002 and I-001 in one step; not built separately"},
                      {"id": "I-007", "reason": "a folder created during the scan is I-001's folder case; not built separately"},
                      {"id": "I-009", "reason": "lock and unlock during the scan is Y-043 held for one stage; not built separately"},
                      {"id": "D-012", "reason": "a file being appended to during the hash stage is I-005's shape; not built separately"}]}
        (Path(args.root) / "MUTATION_GROUND_TRUTH.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    finally:
        if not args.keep:
            try:
                builder.reset_acl(str(tree / "D4"))
            except Exception:                                       # noqa: BLE001
                pass
            import faulthandler
            import gc
            faulthandler.disable()
            gc.collect()
            builder.remove_tree(tmp)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nMUTATION: {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
    print("=" * 70)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
