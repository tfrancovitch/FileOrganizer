#!/usr/bin/env python3
r"""B21 (unit 7): mutation sensitivity of the Phase 4 safety core -- break each safety rule on purpose and show
the B21 suites fail. The method is p3_mutation_check.py's.

Each mutant is ONE exact text substitution in a SANDBOXED COPY of Scripts\, Resources\ and ARCHITECTURE.md, made under
%TEMP% in a folder whose name starts with fo_p4_b21_ (the real tree is never touched: every file is hashed before and
after the run). The mutated copy runs the suite slice that owns the rule, in its own process. A mutant is KILLED when a
slice reports at least one failed check (or raises); it SURVIVES when every check passes -- a survivor is a finding:
a test is missing, and it is added to the right suite (the mutant is never weakened). Each slice is also run unmutated
first (the control): a slice that fails without a mutant proves nothing.

reconcile.py is read-only by design, so the mutant "recovery executes an A1 resume by itself" INJECTS the act (it
renames the file on the fake disk when a finding is row A1) and the suite must catch it.

Nothing here touches %LOCALAPPDATA%\FileOrganizer\, OneDrive or any real file; the only files written are inside
sandboxes under %TEMP%, and the report in Logs\ (which is not part of the product).

Run:  python Scripts\p4_mutation_check.py            (every mutant)
      python Scripts\p4_mutation_check.py M01 M14    (just these)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
PREFIX = "fo_p4_b21_"
#: A slice that is still running after this many seconds is reported by its own watchdog (longest control: ~21 s).
WATCHDOG_S = 120

#: slice id -> (suite module, [test functions], needs a Masters() of kill workers)
SLICES = {
    "store":    ("p4_journal_check", ["test_store"], False),
    "opening":  ("p4_journal_check", ["test_opening_rules"], False),
    "tables":   ("p4_journal_check", ["test_tables"], False),
    "happy":    ("p4_journal_check", ["test_happy_500"], False),
    "faults":   ("p4_journal_check", ["test_faults"], False),
    "pause":    ("p4_journal_check", ["test_pause_lock_commit_failure"], False),
    "proj5a":   ("p4_journal_check", ["test_projection_basics"], False),
    "proj5b":   ("p4_journal_check", ["test_projection_in_a_run"], False),
    "proj5c":   ("p4_journal_check", ["test_projection_convergence"], True),
    "tabdata":  ("p4_journal_check", ["test_tables_as_data", "test_determinism"], False),
    "gen6c":    ("p4_journal_check", ["test_generated_rows"], True),
    "never":    ("p4_journal_check", ["test_recovery_never_acts"], True),
    "plock":    ("p4_lock_check", ["test_project_lock"], False),
    "rlocks":   ("p4_lock_check", ["test_root_locks"], False),
    "xproc":    ("p4_lock_check", ["test_cross_process"], False),
}


def T(s):
    """A triple-quoted block without its first newline (so the text starts at the first character)."""
    return s[1:] if s.startswith("\n") else s


#: (id, rule, file under Scripts, old text, new text, slices)
MUTANTS = [
    ("M01", "C1 and C2 are two commits: intent is durable BEFORE the step (here: merged into one commit, after the step)",
     "Phase4/jobs.py",
     T('''
        with self.j.tx("C1", self.run_id) as t:
            if item["state"] == "PLANNED":
                set_item_state(t, item["item_id"], "PREFLIGHT_READY")
            set_item_state(t, item["item_id"], "INTENT", current_action_id=action_id, refusal_class=None, refusal_scope=None)
            t.execute("INSERT INTO evidence_snapshot (item_id, snapshot_kind, body, created_utc) VALUES (?, 'before', ?, ?)",
                      (item["item_id"], _json(pre.before), utc_now()))
            snap = t.execute("SELECT MAX(snapshot_id) FROM evidence_snapshot WHERE item_id = ?", (item["item_id"],)).fetchone()[0]
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_INTENT_RECORDED",
                      plan_ref=item["plan_ref"], evidence_snapshot_id=snap, before_identity=_json(pre.before),
                      recovery_token=pre.recovery_token, authorising_record=job["authorising_record"],
                      method=getattr(action, "method", "fake"), detail=_json({"kind": item["kind"], "params": json.loads(item["params"])}))
        self.hook("C1:after", item)
        # ---- the one step
        self.hook("step:before", item)
        result, attempts = self._step_with_lock_retries(action, item)
        self.hook("step:after", item)
        # ---- C2: result -- never before the step ran
        self.hook("C2:before", item)
        with self.j.tx("C2", self.run_id) as t:
            if result.status == "ok":
                set_item_state(t, item["item_id"], "APPLIED")
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_RESULT_RECORDED",
                      plan_ref=item["plan_ref"], method=getattr(action, "method", "fake"), native_result=result.native_result,
                      win32_error=result.win32_error, detail=_json({"status": result.status, "attempts": attempts, **result.detail}))
        self.hook("C2:after", item)
'''),
     T('''
        with self.j.tx("C1", self.run_id) as t:
            if item["state"] == "PLANNED":
                set_item_state(t, item["item_id"], "PREFLIGHT_READY")
            set_item_state(t, item["item_id"], "INTENT", current_action_id=action_id, refusal_class=None, refusal_scope=None)
            t.execute("INSERT INTO evidence_snapshot (item_id, snapshot_kind, body, created_utc) VALUES (?, 'before', ?, ?)",
                      (item["item_id"], _json(pre.before), utc_now()))
            snap = t.execute("SELECT MAX(snapshot_id) FROM evidence_snapshot WHERE item_id = ?", (item["item_id"],)).fetchone()[0]
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_INTENT_RECORDED",
                      plan_ref=item["plan_ref"], evidence_snapshot_id=snap, before_identity=_json(pre.before),
                      recovery_token=pre.recovery_token, authorising_record=job["authorising_record"],
                      method=getattr(action, "method", "fake"), detail=_json({"kind": item["kind"], "params": json.loads(item["params"])}))
            self.hook("C1:after", item)
            self.hook("step:before", item)
            result, attempts = self._step_with_lock_retries(action, item)
            self.hook("step:after", item)
            self.hook("C2:before", item)
            if result.status == "ok":
                set_item_state(t, item["item_id"], "APPLIED")
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_RESULT_RECORDED",
                      plan_ref=item["plan_ref"], method=getattr(action, "method", "fake"), native_result=result.native_result,
                      win32_error=result.win32_error, detail=_json({"status": result.status, "attempts": attempts, **result.detail}))
        self.hook("C2:after", item)
'''),
     ["happy", "faults", "gen6c"]),

    ("M02", "A result is never recorded before the step ran (here: C2 is committed, then the step is called)",
     "Phase4/jobs.py",
     T('''
        # ---- the one step
        self.hook("step:before", item)
        result, attempts = self._step_with_lock_retries(action, item)
        self.hook("step:after", item)
        # ---- C2: result -- never before the step ran
        self.hook("C2:before", item)
        with self.j.tx("C2", self.run_id) as t:
            if result.status == "ok":
                set_item_state(t, item["item_id"], "APPLIED")
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_RESULT_RECORDED",
                      plan_ref=item["plan_ref"], method=getattr(action, "method", "fake"), native_result=result.native_result,
                      win32_error=result.win32_error, detail=_json({"status": result.status, "attempts": attempts, **result.detail}))
        self.hook("C2:after", item)
'''),
     T('''
        # ---- the one step
        self.hook("step:before", item)
        result, attempts = StepResult("ok", "recorded before the step ran", None), [{"status": "ok", "win32_error": None}]
        self.hook("C2:before", item)
        with self.j.tx("C2", self.run_id) as t:
            if result.status == "ok":
                set_item_state(t, item["item_id"], "APPLIED")
            add_event(t, self.run_id, job["job_id"], item["item_id"], action_id, "STEP_RESULT_RECORDED",
                      plan_ref=item["plan_ref"], method=getattr(action, "method", "fake"), native_result=result.native_result,
                      win32_error=result.win32_error, detail=_json({"status": result.status, "attempts": attempts, **result.detail}))
        self.hook("C2:after", item)
        result, attempts = self._step_with_lock_retries(action, item)
        self.hook("step:after", item)
'''),
     ["happy", "faults", "gen6c"]),

    ("M03", "Recovery never acts (axiom 17): here recovery INJECTS the A1 'resume' by itself (renames on the fake disk)",
     "Phase4/reconcile.py",
     T('''
            f = classify_item(conn, item, observer)
            if f is not None:
                rep.items.append(f)
'''),
     T('''
            f = classify_item(conn, item, observer)
            if f is not None and f.row_id == "A1":
                _p = json.loads(item["params"])
                observer.fs.rename_no_overwrite(_p.get("src") or _p.get("from"), _p.get("dst") or _p.get("to"))
            if f is not None:
                rep.items.append(f)
'''),
     ["gen6c", "never"]),

    ("M04", "The journal is synchronous=FULL on every writing connection (here: NORMAL)",
     "Phase4/journal.py", '    conn.execute("PRAGMA synchronous=FULL")', '    conn.execute("PRAGMA synchronous=NORMAL")',
     ["store"]),

    ("M05", "The project lock is taken non-blocking (here: blocking, msvcrt.LK_LOCK)",
     "Phase4/locks.py", "        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)", "        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)",
     ["plock"]),

    ("M06", "The project lock is taken at all (here: acquire() never locks the file)",
     "Phase4/locks.py",
     T('''
            if _try_lock(fd):
                break
            if self._clock() >= deadline:
                os.close(fd)
                info = holder_info(self.path)
'''),
     T('''
            if True:
                break
            if self._clock() >= deadline:
                os.close(fd)
                info = holder_info(self.path)
'''),
     ["plock", "xproc"]),

    ("M07", "The fencing token is checked inside every journal transaction (here: not checked)",
     "Phase4/journal.py", "        if self.fence is not None:\n", "        if False:\n",
     ["store", "plock"]),

    ("M08", "A projection is keyed by action_id and idempotent (here: the fake projector has no key and no lookup)",
     "p4_fakes.py",
     T('''
            cat.execute(self.DDL)
            row = cat.execute("SELECT event_id, payload FROM p4_fake_projection WHERE action_id = ?", (ev["action_id"],)).fetchone()
            if row is None:
'''),
     T('''
            cat.execute(self.DDL.replace("action_id TEXT PRIMARY KEY", "action_id TEXT"))
            row = None
            if row is None:
'''),
     ["proj5a", "proj5c"]),

    ("M09", "At most one event of an action carries a projection (here: the unique index on action_id is not unique)",
     "Phase4/journal.py",
     "CREATE UNIQUE INDEX ux_event_projected_action ON operation_event (action_id) WHERE projection_state <> 'NOT_APPLICABLE';",
     "CREATE INDEX ux_event_projected_action ON operation_event (action_id) WHERE projection_state <> 'NOT_APPLICABLE';",
     ["store", "proj5a", "proj5b"]),

    ("M10", "Circuit breaker: three consecutive refusals of one class pause the job (here: disabled)",
     "Phase4/jobs.py", "                    if streak >= BREAKER_CONSECUTIVE:", "                    if False:",
     ["faults"]),

    ("M11", "Circuit breaker: max(3, ceil(10 %)) of the items refused in one class pause the job (here: disabled)",
     "Phase4/jobs.py", "                    if refused_by_class[cls] >= threshold:", "                    if False:",
     ["faults", "tables"]),

    ("M12", "Unknown blocks: a refusal class that is not named item-local is JOB_GLOBAL (here: always ITEM_LOCAL)",
     "Phase4/jobs.py", '    return "ITEM_LOCAL" if refusal_class in ITEM_LOCAL_CLASSES else "JOB_GLOBAL"',
     '    return "ITEM_LOCAL"',
     ["faults", "tables"]),

    ("M13", "The product never auto-creates a blank journal for a project whose binding exists (here: it does)",
     "Phase4/journal.py", "    if binding is None and not exists:\n", "    if not exists:\n",
     ["opening"]),

    ("M14", "The revision check runs again at every resume (here: skipped at resume)",
     "Phase4/jobs.py", '        ok, why = self.revision_check(job["revision_ref"])', '        ok, why = True, ""',
     ["faults"]),

    ("M15", "Table A has a row for 'INTENT, source gone, destination gone' (A3) (here: row deleted)",
     "Phase4/reconcile.py",
     T('''
    _r("A", "A3", {"journal": ("INTENT",), "shape": _D, "src": ("absent",), "dst": ("absent",)},
       "UNKNOWN_AFTER_INTERRUPT", "ask",
       "Ask. Search the recorded quarantine or staging path and recent-delete evidence; never infer", "R4.3 2.5 A3"),
'''),
     "",
     ["tabdata", "gen6c"]),

    ("M16", "Table E has a row for 'event PROJECTED, catalog row missing' (E3, the restored catalog) (here: row deleted)",
     "Phase4/reconcile.py",
     T('''
    _r("E", "E3", {"projection": ("PROJECTED",), "catalog": ("absent",)}, "PROJECTED", "replay",
       "Replay: a restored, older catalog lacks the row; the projector writes it again", "handoff unit 6 E; G1"),
'''),
     "",
     ["tabdata", "gen6c", "proj5c"]),

    ("M17", "C4 closes a job only when every event is PROJECTED or NOT_APPLICABLE (here: pending events ignored)",
     "Phase4/jobs.py", "            if open_items or pending:", "            if open_items:",
     ["proj5b", "faults"]),

    ("M18", "If a table E row says ask, nothing is replayed and everything stops (here: the stop is ignored)",
     "Phase4/projection.py", '    stops = [f for f in findings if f.outcome == "ask"]', "    stops = []",
     ["proj5c"]),

    ("M19", "P applies the projection, THEN marks the event PROJECTED (here: marked first)",
     "Phase4/projection.py",
     T('''
    if ev["projection_state"] == "PENDING":
        projection.apply(ev)
    if hook:
        hook("P:after_apply")
    mark_projected(journal, run_id, event_id)
'''),
     T('''
    mark_projected(journal, run_id, event_id)
    if hook:
        hook("P:after_apply")
    if ev["projection_state"] == "PENDING":
        projection.apply(ev)
'''),
     ["proj5b", "proj5c"]),

    ("M20", "A journal with recorded jobs and no catalog binding is not bound by guessing (here: bound anyway)",
     "Phase4/journal.py", "            if j.has_jobs():\n", "            if False:\n",
     ["opening"]),

    ("M21", "At resume the job's manifest must still be the one approved at C0 (here: not compared)",
     "Phase4/jobs.py", '        if now_hash != job["manifest_hash"]:', "        if False:",
     ["pause"]),

    ("M22", "A job left RUNNING or PAUSED by a process that is gone is marked INTERRUPTED at start (here: never found)",
     "Phase4/jobs.py", '"SELECT job_id, job_uid FROM job WHERE state IN (\'RUNNING\', \'PAUSED\')"',
     '"SELECT job_id, job_uid FROM job WHERE state IN (\'NO_SUCH_STATE\')"',
     ["pause", "proj5c"]),

    ("M23", "Root locks: nested roots are refused BOTH ways (here: only 'the holder is inside mine' is refused)",
     "Phase4/locks.py", '                if other.get("key") in chain or key in (other.get("chain") or []):',
     '                if other.get("key") in chain:',
     ["rlocks"]),
]


# ---------------------------------------------------------------------------
# The child: one slice, maybe one mutant, in a sandboxed tree
# ---------------------------------------------------------------------------

def child(tree: Path, slice_id: str):
    scripts = tree / "Scripts"
    sys.path.insert(0, str(scripts))
    sys.path.insert(0, str(scripts / "Database"))
    os.chdir(str(scripts))
    mod_name, fns, needs_masters = SLICES[slice_id]
    import importlib
    import io
    import contextlib
    import threading
    out = io.StringIO()
    error = None
    mod = None

    def report(err):
        failed, passed, names = 0, 0, []
        for name in ("p4_journal_check", "p4_lock_check"):
            m = sys.modules.get(name)
            if m is not None and hasattr(m, "RESULTS"):
                for n, ok, _d in list(m.RESULTS):
                    if ok:
                        passed += 1
                    else:
                        failed += 1
                        names.append(n[:120])
        sys.__stdout__.write(json.dumps({"failed": failed, "passed": passed, "error": err, "first_failures": names[:3]}) + "\n")
        sys.__stdout__.flush()

    def watchdog():
        # A mutant can make a test hang (e.g. a blocking lock). A hang is a kill: report what failed so far and stop.
        report("the slice was still running after %d s (a hang is a kill)" % WATCHDOG_S)
        os._exit(0)

    timer = threading.Timer(WATCHDOG_S, watchdog)
    timer.daemon = True
    timer.start()
    try:
        mod = importlib.import_module(mod_name)
        with contextlib.redirect_stdout(out):
            masters = None
            try:
                if needs_masters:
                    masters = mod.Masters()
                for fn in fns:
                    f = getattr(mod, fn)
                    f(masters) if needs_masters else f()
            finally:
                if masters is not None:
                    masters.cleanup()
    except SystemExit as exc:
        if exc.code not in (0, None):
            error = f"exit {exc.code}"
    except BaseException as exc:                                    # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"[:300]
    timer.cancel()
    report(error)


# ---------------------------------------------------------------------------
# The parent
# ---------------------------------------------------------------------------

def tree_hashes():
    out = {}
    for base in (ROOT / "Scripts", ROOT / "Resources"):
        for p in sorted(base.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                out[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    arch = ROOT / "ARCHITECTURE.md"
    out["ARCHITECTURE.md"] = hashlib.sha256(arch.read_bytes()).hexdigest()
    return out


def sandbox(dest: Path):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "InstallationCheckReport.txt", "DependencyCheckReport.txt")
    shutil.copytree(ROOT / "Scripts", dest / "Scripts", ignore=ignore)
    shutil.copytree(ROOT / "Resources", dest / "Resources", ignore=ignore)
    shutil.copy2(ROOT / "ARCHITECTURE.md", dest / "ARCHITECTURE.md")


def prefixed_folders():
    base = Path(tempfile.gettempdir())
    return {p.name for p in base.iterdir() if p.is_dir() and p.name.startswith(PREFIX)}


def run_slice(tree: Path, slice_id: str, timeout=300):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    t = time.time()
    try:
        p = subprocess.run([sys.executable, str(tree / "Scripts" / "p4_mutation_check.py"), "--child", str(tree), slice_id],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
        line = next((x for x in p.stdout.splitlines()[::-1] if x.startswith("{")), None)
        res = json.loads(line) if line else {"failed": 0, "passed": 0, "error": ("no result: " + p.stderr[-300:])}
    except subprocess.TimeoutExpired:
        res = {"failed": 0, "passed": 0, "error": "timeout"}
    res["seconds"] = round(time.time() - t, 1)
    return res


def main(argv):
    sys.path.insert(0, str(SCRIPTS))
    import p4_fakes as F
    local_before = F.localappdata_fileorganizer_state()
    folders_before = prefixed_folders()
    only = [a for a in argv if a.startswith("M")]
    mutants = [m for m in MUTANTS if not only or m[0] in only]
    before = tree_hashes()
    work = Path(tempfile.mkdtemp(prefix=PREFIX + "mut_"))
    results, controls, leaked = [], {}, []

    def sweep(why):
        """A mutant can break a test's own cleanup; whatever fo_p4_b21_* folder a slice left behind is recorded and removed."""
        for name in sorted(prefixed_folders() - folders_before - {work.name}):
            leaked.append(f"{name} ({why})")
            shutil.rmtree(Path(tempfile.gettempdir()) / name, ignore_errors=True)

    try:
        clean = work / "clean"
        sandbox(clean)
        # Every mutant's text must be found exactly once (a mutant that does not apply proves nothing).
        for mid, _rule, f, old, new, _sl in mutants:
            n = (clean / "Scripts" / f).read_text(encoding="utf-8").count(old)
            if n != 1:
                print(f"  ERROR  {mid}: its text occurs {n} times in {f}")
                return 2
            if old == new:
                print(f"  ERROR  {mid}: old and new text are the same")
                return 2
        print("Controls (each slice unmutated must pass):")
        for sl in sorted({s for m in mutants for s in m[5]}):
            controls[sl] = run_slice(clean, sl)
            c = controls[sl]
            print(f"  {'OK  ' if not c['failed'] and not c['error'] else 'BAD '} {sl}: {c['passed']} passed, {c['failed']} failed"
                  f"{' -- ' + c['error'] if c['error'] else ''} ({c['seconds']} s)", flush=True)
            sweep("control " + sl)
        print("\nMutants:")
        for mid, rule, f, old, new, slices in mutants:
            tree = work / mid
            sandbox(tree)
            p = tree / "Scripts" / f
            p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
            outcome = []
            for sl in slices:
                r = run_slice(tree, sl)
                outcome.append((sl, r))
                sweep(f"{mid} {sl}")
                if r["failed"] or r["error"]:
                    break                               # killed: no need to run its other slices
            killed = next(((sl, r) for sl, r in outcome if r["failed"] or r["error"]), None)
            results.append({"id": mid, "rule": rule, "file": f, "killed": killed is not None,
                            "by": killed[0] if killed else None,
                            "evidence": (f"{killed[1]['failed']} check(s) failed" + (f" / {killed[1]['error']}" if killed[1]["error"] else "")
                                         + (f": {killed[1]['first_failures'][0]}" if killed[1]["first_failures"] else ""))
                            if killed else "all checks passed",
                            "seconds": round(sum(r["seconds"] for _s, r in outcome), 1)})
            x = results[-1]
            print(f"  {'KILLED  ' if x['killed'] else 'SURVIVED'} {mid} {rule}\n           by {x['by']}: {x['evidence'][:200]}", flush=True)
            shutil.rmtree(tree, ignore_errors=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    after = tree_hashes()
    bad_controls = [s for s, c in controls.items() if c["failed"] or c["error"]]
    survived = [r["id"] for r in results if not r["killed"]]
    untouched = before == after
    local_same = local_before == F.localappdata_fileorganizer_state()
    survivors_left = sorted(prefixed_folders() - folders_before)
    (ROOT / "Logs").mkdir(exist_ok=True)
    report = {"mutants": results, "controls": controls, "tree_untouched": untouched, "leaked_and_removed": leaked}
    (ROOT / "Logs" / "p4_mutation_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    print(f"\n{'=' * 70}\nMutant table\n{'-' * 12}")
    print(f"{'id':4} {'result':8} {'killed by':9} {'file':20} rule")
    for r in results:
        print(f"{r['id']:4} {'KILLED' if r['killed'] else 'SURVIVED':8} {str(r['by'] or '-'):9} {r['file']:20} {r['rule'][:100]}")
    print(f"\nControls clean: {len(controls) - len(bad_controls)}/{len(controls)}; mutants killed: "
          f"{len(results) - len(survived)}/{len(results)}; real tree untouched: {untouched}")
    print(f"%LOCALAPPDATA%\\FileOrganizer unchanged: {local_same}; "
          f"fo_p4_b21_* folders left after the run: {len(survivors_left)}; "
          f"left behind by a mutated slice and removed by this run: {len(leaked)}")
    for name in leaked[:10]:
        print("  leaked and removed: " + name)
    if survived:
        print("SURVIVED (findings): " + ", ".join(survived))
    checks = [(len(results) - len(survived)), (len(controls) - len(bad_controls)), int(untouched), int(local_same),
              int(not survivors_left)]
    total = [len(results), len(controls), 1, 1, 1]
    print(f"P4 MUTATION (B21): {sum(checks)}/{sum(total)} checks passed")
    return 0 if not survived and not bad_controls and untouched and local_same and not survivors_left else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3])
    else:
        raise SystemExit(main(sys.argv[1:]))
