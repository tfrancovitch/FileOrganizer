r"""B19 -- one machine-readable report shape for the Phase 3 harnesses (R11 gate 7).

A suite that writes a report writes Logs\<tool>.json:

  {"schema": "fileorganizer.p3.harness-report/1",
   "tool": ..., "tool_sha256": ..., "product_version": ...,
   "started_utc": ..., "finished_utc": ...,
   "environment": {python, implementation, platform, os_release, machine, sqlite, jsonschema},
   "checks": [{"name", "pass", "detail"}],
   "summary": {"checks", "passed", "failed"},
   ...tool-specific keys...,
   "exit_code": 0 | 1, "result": "pass" | "fail"}

No machine or user identifier is recorded (no host name, no user name, no
path outside the product tree) -- the rule fo_env follows for a run's
environment snapshot.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPORT_SCHEMA = "fileorganizer.p3.harness-report/1"
SCRIPTS = Path(__file__).resolve().parent
LOGS = SCRIPTS.parent / "Logs"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def environment() -> dict:
    try:
        import jsonschema
        js = jsonschema.__version__
    except Exception:                                               # noqa: BLE001
        js = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(), "os_release": platform.release(), "machine": platform.machine(),
            "sqlite": sqlite3.sqlite_version, "jsonschema": js}


def product_version():
    try:
        sys.path.insert(0, str(SCRIPTS / "Database"))
        import fo_db
        return {"app_version": fo_db.APP_VERSION, "schema_version": fo_db.APP_SCHEMA_VERSION}
    except Exception:                                               # noqa: BLE001
        return None


def write(tool, script_path, started_utc, results, extra=None) -> Path:
    """results: [(name, ok, detail)] as the suites keep them."""
    passed = sum(1 for _n, ok, _d in results if ok)
    exit_code = 0 if passed == len(results) and results else 1
    body = {"schema": REPORT_SCHEMA, "tool": tool, "tool_sha256": sha256_file(script_path),
            "product_version": product_version(), "started_utc": started_utc, "finished_utc": utc_now(),
            "environment": environment(),
            "checks": [{"name": n, "pass": bool(ok), "detail": d or ""} for n, ok, d in results],
            "summary": {"checks": len(results), "passed": passed, "failed": len(results) - passed}}
    body.update(extra or {})
    body["exit_code"] = exit_code
    body["result"] = "pass" if exit_code == 0 else "fail"
    LOGS.mkdir(exist_ok=True)
    out = LOGS / f"{tool}.json"
    out.write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return out
