r"""B19 -- a clean, auditable fixture-lab harness (the R11 evaluation's gate 7).

    py -3 Scripts\p3_lab_harness.py

The gate: "Eliminate or isolate the unrelated spreadsheet-runtime traceback,
record environment metadata, and include machine-readable exit status and
validator version in the final validation output." The traceback belonged to
the research environment's validator run, not to the product. This harness
proves the PRODUCT's copy of the lab runs clean, and records the proof.

  1. The lab is copied to a temporary folder and run there -- isolated: the
     reference validator writes VALIDATION_RESULTS.json next to itself, and
     the product tree is never written. The copy is checked against the lab's
     own MANIFEST.json first (every shipped lab file's SHA-256).
  2. Resources\Phase3\fixture_lab\validate_fixtures.py runs in a subprocess,
     twice: as shipped, and under Python's development mode (-X dev: every
     warning category shown). stdout, stderr and the exit code are captured.
  3. Asserted: exit 0, 16/16, and stderr EMPTY in both runs -- any stderr is a
     failure, reported verbatim.
  4. Logs\p3_lab_harness.json (the shape p3_report.py defines; the handoff
     calls it p3_lab_validation -- the same file is written under that name
     too): the validator's SHA-256 and version, the Python version and
     platform, each fixture's result, the stderr text, the exit code.

Exit status: 0 when every check passes, 1 otherwise.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                   # noqa: BLE001
    pass

import p3_report as REP                                             # noqa: E402

LAB = SCRIPTS.parent / "Resources" / "Phase3" / "fixture_lab"
VALIDATOR = "validate_fixtures.py"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def _manifest():
    doc = json.loads((LAB / "MANIFEST.json").read_text(encoding="utf-8"))
    return doc.get("schema"), {f["path"]: f for f in doc.get("files", [])}


def run_validator(copy: Path, mode):
    argv = [sys.executable] + (["-X", "dev"] if mode == "dev" else []) + [VALIDATOR]
    t0 = time.perf_counter()
    p = subprocess.run(argv, cwd=str(copy), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {"mode": mode, "argv": ["python"] + argv[1:], "exit_code": p.returncode, "stdout": p.stdout,
            "stderr": p.stderr, "duration_s": round(time.perf_counter() - t0, 3)}


def main() -> int:
    started = REP.utc_now()
    print("P3 fixture-lab harness (R11 gate 7)")
    schema, manifest = _manifest()
    validator = LAB / VALIDATOR
    sha = REP.sha256_file(validator)
    listed = manifest.get(VALIDATOR, {})
    version = f"P3.R11 reference validator ({schema}, sha256 {sha[:12]})"
    check("the validator is the lab manifest's own file (same SHA-256 and size as MANIFEST.json lists)",
          listed.get("sha256", "").upper() == sha and listed.get("bytes") == validator.stat().st_size,
          f"{listed.get('sha256')} vs {sha}")
    shipped = sorted(p.relative_to(LAB).as_posix() for p in LAB.rglob("*") if p.is_file())
    mismatched = [p for p in shipped if p in manifest and manifest[p]["sha256"].upper() != REP.sha256_file(LAB / p)]
    unlisted = [p for p in shipped if p not in manifest and p != "MANIFEST.json"]
    not_shipped = sorted(set(manifest) - set(shipped))
    check(f"every shipped lab file ({len(shipped)}) matches MANIFEST.json, none unlisted",
          not mismatched and not unlisted, f"mismatched {mismatched}, unlisted {unlisted}")

    tmp = Path(tempfile.mkdtemp(prefix="p3lab_"))
    runs, fixtures = [], []
    try:
        copy = tmp / "fixture_lab"
        shutil.copytree(LAB, copy)
        before = {p: REP.sha256_file(LAB / p) for p in shipped}
        for mode in ("default", "dev"):
            r = run_validator(copy, mode)
            runs.append(r)
            ok_line = "P3.R11 fixture validation: 16/16 PASS" in r["stdout"]
            check(f"{mode} run: exit code 0", r["exit_code"] == 0, str(r["exit_code"]))
            check(f"{mode} run: 16/16 PASS", ok_line, r["stdout"].strip()[-200:])
            check(f"{mode} run: stderr EMPTY" + (" (every warning category shown)" if mode == "dev" else ""),
                  r["stderr"] == "", "stderr, verbatim: " + repr(r["stderr"]))
        results_path = copy / "VALIDATION_RESULTS.json"
        if results_path.is_file():
            got = json.loads(results_path.read_text(encoding="utf-8"))
            fixtures = [{"fixture": d["fixture"], "quick_check": d["quick_check"], "pass": d["pass"]} for d in got["details"]]
        check("the validator's own results file lists 16 fixtures, every one pass and quick_check ok",
              len(fixtures) == 16 and all(f["pass"] and f["quick_check"] == "ok" for f in fixtures),
              str([f for f in fixtures if not f["pass"]]))
        after = {p: REP.sha256_file(LAB / p) for p in shipped}
        check("isolated: the product's lab folder is byte-identical afterwards and gained no file",
              before == after and sorted(p.relative_to(LAB).as_posix() for p in LAB.rglob("*") if p.is_file()) == shipped)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    extra = {"validator": {"path": "Resources/Phase3/fixture_lab/" + VALIDATOR, "sha256": sha,
                           "bytes": validator.stat().st_size, "version": version, "manifest_schema": schema,
                           "matches_manifest": listed.get("sha256", "").upper() == sha},
             "lab": {"shipped_files": len(shipped), "manifest_entries": len(manifest),
                     "listed_not_shipped": not_shipped},
             "runs": runs, "fixtures": fixtures,
             "fixture_summary": {"total": len(fixtures), "passed": sum(1 for f in fixtures if f["pass"]),
                                 "failed": sum(1 for f in fixtures if not f["pass"])},
             "stderr": {r["mode"]: r["stderr"] for r in runs}}
    out = REP.write("p3_lab_harness", __file__, started, RESULTS, extra)
    shutil.copyfile(out, out.with_name("p3_lab_validation.json"))
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 LAB HARNESS: {passed}/{len(RESULTS)} checks passed -- report {out.name} (and p3_lab_validation.json)")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
