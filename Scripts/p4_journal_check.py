r"""B21 -- the action journal, the job state machine, the projection and the reconciliation tables.

    py -3 Scripts\p4_journal_check.py

Sections (added unit by unit)
  1. Unit 1: probe J5 -- the cost of 10,000 C1/C2/C3 commit triples under synchronous=FULL and NORMAL, on a
     sandbox journal. REPORTED, not asserted (the stop condition is a median above 50 ms under FULL; this
     suite fails only when that stop condition is met, so the Architect sees it).
     Not run here (a VM or a person is needed): J2, J3. J1 is covered by the generated reconciliation tests
     (unit 6); J4 is cross-volume (out of scope); J6 and J7 are covered by units 2 and 5.
  2. Unit 2: paths (refusals, defaults), the journal store (pragmas, schema, fencing, commit failure), the
     opening rules, migration 017 on a B20-schema project.
  3. Unit 4: the state-machine tables as tests, a 500-item fake job, refusals / scopes / breaker / pause / resume,
     commit failure and fencing inside a run, the projection hook, the fakes' sandbox.
  4. Unit 5: the projection (fake projector, idempotent by action_id; P after C3; convergence after real kills around
     P; a restored catalog repaired by replay; a catalog row unknown to the journal stops everything).
  5. Unit 6: the reconciliation tables A, C, D, E, F as data; determinism over every key; one or more GENERATED tests
     per row, reached by a real kill (a child process killed with TerminateProcess at a named hook) where possible
     and constructed otherwise; the row list printed with pass/fail; recovery takes no action; the restore ceremony.

    py -3 Scripts\p4_journal_check.py --kill-worker <sandbox root> <spec.json>   (started by the suite itself)

Every test works under its own tempfile.mkdtemp(prefix="fo_p4_b21_") root. %LOCALAPPDATA%\FileOrganizer\ is
recorded before and checked after: it must not change.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                   # noqa: BLE001
    pass

import p4_fakes as F                                                # noqa: E402
import fo_db                                                        # noqa: E402
from Phase4 import journal as J                                     # noqa: E402
from Phase4 import paths as P                                       # noqa: E402

RESULTS = []
J5 = {}

#: The handoff's stop condition 6: a median above this under FULL is a finding for the Architect.
J5_STOP_MEDIAN_MS = 50.0

MACHINE = ("11111111-2222-3333-4444-555555555555", "TESTBOX")
OTHER_MACHINE = ("99999999-8888-7777-6666-555555555555", "OTHERBOX")


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def raises(exc_type, fn, *a, **k):
    """(True, exception) if fn raises exc_type; (False, repr) otherwise."""
    try:
        fn(*a, **k)
    except exc_type as e:                                           # noqa: PERF203
        return True, e
    except BaseException as e:                                      # noqa: BLE001
        return False, repr(e)
    return False, "no exception"


@contextlib.contextmanager
def schema17():
    """The unit-2 tests exercise migration 017 before unit 8 bumps APP_SCHEMA_VERSION: patch the constant for the
    duration of a block, in this process only, and restore it."""
    old = fo_db.APP_SCHEMA_VERSION
    fo_db.APP_SCHEMA_VERSION = 17
    try:
        yield
    finally:
        fo_db.APP_SCHEMA_VERSION = old


@contextlib.contextmanager
def schema16():
    """B21 unit 8 made schema 17 the product's own. The B20-schema fixture of test_migration_017 must still be a B20
    project (schema 16, no migration 017 applied), so it is built, in this process only, with the constant set back to 16."""
    old = fo_db.APP_SCHEMA_VERSION
    fo_db.APP_SCHEMA_VERSION = 16
    try:
        yield
    finally:
        fo_db.APP_SCHEMA_VERSION = old


def make_catalog(sb, name="cat"):
    """A real, empty project catalog at schema 17 inside the sandbox. Returns (connection, project_dir, uid)."""
    pdir = sb.mkdir(name)
    with schema17():
        info = fo_db.init_project(pdir, name)
    conn = sqlite3.connect(fo_db.ProjectPaths(pdir).database_file, isolation_level=None)
    sb.register(conn.close)
    return conn, pdir, info["project_uid"]


# ===================================================================================================
# J5 -- commit cost  (unit 1)
# ===================================================================================================
_J5_SCHEMA = """
CREATE TABLE item  (item_id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL, status TEXT NOT NULL, run_id TEXT NOT NULL);
CREATE TABLE snapshot (snapshot_id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES item(item_id), body TEXT NOT NULL);
CREATE TABLE event (event_id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES item(item_id),
                    seq INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL, projection TEXT NOT NULL,
                    UNIQUE(item_id, seq));
CREATE TABLE owner (owner_id INTEGER PRIMARY KEY CHECK (owner_id = 1), run_id TEXT NOT NULL);
"""


def j5_run(db_path, sync, triples):
    """Time `triples` C1/C2/C3 commit triples; returns per-triple and per-commit seconds."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA synchronous={sync}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_J5_SCHEMA)
    conn.execute("INSERT INTO owner (owner_id, run_id) VALUES (1, 'run-1')")
    actual = conn.execute("PRAGMA synchronous").fetchone()[0]
    body_snapshot, body_intent, body_result, body_verify = "S" * 600, "I" * 300, "R" * 300, "V" * 300
    triple_s, commit_s = [], []
    for i in range(1, triples + 1):
        t0 = time.perf_counter()
        a = time.perf_counter()                                     # C1 -- intent
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SELECT 1 FROM owner WHERE owner_id = 1 AND run_id = 'run-1'").fetchone()
        conn.execute("INSERT INTO item (item_id, job_id, status, run_id) VALUES (?, 1, 'INTENT', 'run-1')", (i,))
        conn.execute("INSERT INTO snapshot (item_id, body) VALUES (?, ?)", (i, body_snapshot))
        conn.execute("INSERT INTO event (item_id, seq, kind, body, projection) VALUES (?, 1, 'STEP_INTENT_RECORDED', ?, 'NOT_APPLICABLE')", (i, body_intent))
        conn.execute("COMMIT")
        commit_s.append(time.perf_counter() - a)
        a = time.perf_counter()                                     # C2 -- result
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SELECT 1 FROM owner WHERE owner_id = 1 AND run_id = 'run-1'").fetchone()
        conn.execute("INSERT INTO event (item_id, seq, kind, body, projection) VALUES (?, 2, 'STEP_RESULT_RECORDED', ?, 'NOT_APPLICABLE')", (i, body_result))
        conn.execute("UPDATE item SET status = 'APPLIED' WHERE item_id = ?", (i,))
        conn.execute("COMMIT")
        commit_s.append(time.perf_counter() - a)
        a = time.perf_counter()                                     # C3 -- verification and terminal state
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SELECT 1 FROM owner WHERE owner_id = 1 AND run_id = 'run-1'").fetchone()
        conn.execute("INSERT INTO event (item_id, seq, kind, body, projection) VALUES (?, 3, 'VERIFICATION_RECORDED', ?, 'PENDING')", (i, body_verify))
        conn.execute("UPDATE item SET status = 'APPLIED_VERIFIED' WHERE item_id = ?", (i,))
        conn.execute("COMMIT")
        commit_s.append(time.perf_counter() - a)
        triple_s.append(time.perf_counter() - t0)
    conn.close()
    return triple_s, commit_s, actual


def probe_j5(triples=10000):
    section(f"J5 -- {triples:,} C1/C2/C3 commit triples, synchronous=FULL vs NORMAL (WAL, sandbox journal)")
    with F.Sandbox() as sb:
        drive = os.path.splitdrive(sb.root)[0]
        for sync, code in (("FULL", 2), ("NORMAL", 1)):
            dbp = sb.path(f"j5_{sync.lower()}.journal.db")
            t0 = time.perf_counter()
            triple_s, commit_s, actual = j5_run(dbp, sync, triples)
            total = time.perf_counter() - t0
            ms = [x * 1000.0 for x in triple_s]
            cms = [x * 1000.0 for x in commit_s]
            J5[sync] = {
                "triples": len(ms), "synchronous_pragma": actual,
                "triple_median_ms": F.median(ms), "triple_p95_ms": F.percentile(ms, 95), "triple_max_ms": max(ms),
                "commit_median_ms": F.median(cms), "commit_p95_ms": F.percentile(cms, 95),
                "total_s": total,
            }
            j = J5[sync]
            print(f"    {sync:6s} (pragma reads {actual}) on {drive or '?'}: per triple median {j['triple_median_ms']:.2f} ms, "
                  f"p95 {j['triple_p95_ms']:.2f} ms, max {j['triple_max_ms']:.2f} ms; per commit median {j['commit_median_ms']:.2f} ms, "
                  f"p95 {j['commit_p95_ms']:.2f} ms; total {total:.1f} s")
            check(f"J5 {sync}: {triples:,} triples ran and the connection reported synchronous={code}",
                  len(ms) == triples and actual == code, f"ran {len(ms)}, pragma {actual}")
        check(f"J5 stop condition: median per triple under FULL is not above {J5_STOP_MEDIAN_MS:.0f} ms (reported, pragmas not changed)",
              J5["FULL"]["triple_median_ms"] <= J5_STOP_MEDIAN_MS,
              f"median {J5['FULL']['triple_median_ms']:.2f} ms -- STOP: report to the Architect")


# ===================================================================================================
# Unit 2 -- paths
# ===================================================================================================
class FakeClassifier(P.PathClassifier):
    """The injectable validator: choose the drive type and the OneDrive folders."""

    def __init__(self, drive=3, onedrive=()):
        self.drive, self.od = drive, list(onedrive)

    def drive_type(self, path):
        return self.drive

    def onedrive_folders(self):
        return list(self.od)


def tree(root):
    out = []
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            out.append(os.path.relpath(os.path.join(d, n), root))
    return sorted(out)


@contextlib.contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_paths():
    section("2a. paths.py -- where the journal and the locks may live")
    with F.Sandbox() as sb:
        good = sb.mkdir("store")
        ok, _ = raises(P.PathRefused, P.validate_store_path, good)
        check("a local fixed-drive folder under the sandbox is accepted", not ok and P.validate_store_path(good) == os.path.normpath(good))

        for label, bad in (("UNC \\\\server\\share\\x", "\\\\server\\share\\j"),
                           ("UNC with forward slashes //server/share/x", "//server/share/j"),
                           ("UNC in device form \\\\?\\UNC\\server\\share\\x", "\\\\?\\UNC\\server\\share\\j")):
            ok, e = raises(P.PathRefused, P.validate_store_path, bad)
            check(f"refused: {label}", ok and e.code == "unc" and "network" in str(e).lower(), repr(e))

        before = tree(sb.root)
        for dtype, nm in ((4, "network"), (2, "removable"), (5, "CD-ROM"), (6, "RAM disk"), (1, "no root"), (0, "unknown")):
            ok, e = raises(P.PathRefused, P.validate_store_path, good, FakeClassifier(drive=dtype))
            check(f"refused: drive type {dtype} ({nm}) is not DRIVE_FIXED", ok and e.code == "drive_not_fixed", repr(e))
        check("unknown drive type blocks with the words 'unknown blocks'",
              "unknown blocks" in str(raises(P.PathRefused, P.validate_store_path, good, FakeClassifier(drive=0))[1]))

        od = sb.mkdir("OneDrive")
        inside = os.path.join(od, "Sub", "journals")
        for label, path in (("a folder inside OneDrive", inside), ("OneDrive itself", od),
                            ("the same folder spelled in other case", inside.upper())):
            ok, e = raises(P.PathRefused, P.validate_store_path, path, FakeClassifier(onedrive=[od]))
            check(f"refused (injected classifier): {label}", ok and e.code == "onedrive", repr(e))
        sibling = sb.mkdir("OneDrive2")
        ok, _ = raises(P.PathRefused, P.validate_store_path, sibling, FakeClassifier(onedrive=[od]))
        check("a sibling whose name merely starts with 'OneDrive' is not refused (the match is on folder boundaries)", not ok)

        for var in P.ONEDRIVE_ENV_VARS:
            folder = sb.mkdir("env_" + var)
            with env(**{v: None for v in P.ONEDRIVE_ENV_VARS}):
                with env(**{var: folder}):
                    ok, e = raises(P.PathRefused, P.validate_store_path, os.path.join(folder, "j"))
                check(f"refused through the %{var}% environment variable (default classifier)", ok and e.code == "onedrive", repr(e))

        # A junction that leads into OneDrive is judged by where it leads.
        link = sb.path("link_to_onedrive")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, od], capture_output=True, text=True).returncode == 0
        if made:
            try:
                ok, e = raises(P.PathRefused, P.validate_store_path, os.path.join(link, "x"), FakeClassifier(onedrive=[od]))
                check("refused: a junction inside the sandbox that resolves into OneDrive", ok and e.code == "onedrive", repr(e))
            finally:
                os.rmdir(link)
        else:
            check("(junction could not be made here)", False, "mklink /J failed")

        ok, e = raises(P.PathRefused, P.validate_store_path, "")
        check("refused: an empty path", ok and e.code == "empty")
        for uid in ("..\\x", "a/b", "", "short", "has space in it"):
            ok, e = raises(P.PathRefused, P.validate_project_uid, uid)
            check(f"refused as a folder name: project uid {uid!r}", ok and e.code == "bad_project_uid", repr(e))
        check("a UUID project uid is accepted", P.validate_project_uid("0b2f6f7e-8d7c-4c46-a1b4-5d3a1c0d9e11"))
        check("no refusal created any file or folder in the sandbox (only the folders this test made itself exist)",
              [x for x in tree(sb.root) if x not in ("store", "OneDrive", "OneDrive2", "link_to_onedrive")
               and not x.startswith("env_")] == [x for x in before if x != "store"])

        # The production default is computed from %LOCALAPPDATA% and created by nobody here.
        fake_local = sb.mkdir("fake_local")
        with env(LOCALAPPDATA=fake_local):
            uid = "0b2f6f7e-8d7c-4c46-a1b4-5d3a1c0d9e11"
            folder = P.store_folder(uid)
            check("the production default is %LOCALAPPDATA%\\FileOrganizer\\journals\\<project_uid>",
                  folder == os.path.join(os.path.normpath(fake_local), "FileOrganizer", "journals", uid), folder)
            check("computing the default created nothing", tree(fake_local) == [])
            check("the root-lock folder default is shared, not keyed by project",
                  P.root_lock_folder().endswith(os.path.join("FileOrganizer", "journals", "_root_locks")))
            made = P.ensure_folder(uid)
            check("ensure_folder creates only the validated folder", os.path.isdir(made))
        with env(LOCALAPPDATA=None):
            ok, e = raises(P.PathRefused, P.store_folder, "0b2f6f7e-8d7c-4c46-a1b4-5d3a1c0d9e11")
            check("no %LOCALAPPDATA% and no override: refused, not guessed", ok and e.code == "no_localappdata")


# ===================================================================================================
# Unit 2 -- the journal store
# ===================================================================================================
EVENT_SQL = ("INSERT INTO operation_event (action_id, job_id, item_id, seq, kind, software_version, run_id, "
             "projection_state, created_utc) VALUES (?, ?, ?, ?, ?, 'B21', 'run-A', ?, ?)")


def seed_job(j, items=1):
    """One job with `items` items, directly (the runner comes in unit 4)."""
    now = J.utc_now()
    with j.tx("seed") as t:
        t.execute("INSERT INTO job (job_uid, state, revision_ref, manifest_hash, authorising_record, software_version, "
                  "approved_utc) VALUES ('job-1', 'APPROVED', 'rev-1', 'abc', 'test', 'B21', ?)", (now,))
        jid = t.execute("SELECT job_id FROM job WHERE job_uid = 'job-1'").fetchone()[0]
        ids = []
        for n in range(1, items + 1):
            t.execute("INSERT INTO operation_item (job_id, position, kind, state, plan_ref) VALUES (?, ?, 'fake', 'PLANNED', ?)",
                      (jid, n, f"plan-{n}"))
            ids.append(t.execute("SELECT item_id FROM operation_item WHERE job_id = ? AND position = ?", (jid, n)).fetchone()[0])
    return jid, ids


def test_store():
    section("2b. journal.py -- pragmas, schema, fencing, commit failure")
    with F.Sandbox() as sb:
        folder = sb.mkdir("store")
        jp = os.path.join(folder, "j.journal.db")
        j1 = J.Journal.create(jp, "proj-uid-00000001", MACHINE)
        j2 = J.Journal.open(jp)
        for label, jx in (("created", j1), ("opened a second time", j2)):
            pr = jx.pragmas()
            check(f"the {label} writing connection reports synchronous=2 (FULL) and journal_mode=wal",
                  pr["synchronous"] == 2 and pr["journal_mode"] == "wal", str(pr))
            check(f"the {label} connection has foreign_keys=ON and busy_timeout=5000",
                  pr["foreign_keys"] == 1 and pr["busy_timeout"] == 5000, str(pr))
        pr = j1.pragmas()
        check("the journal has its own application_id and user_version",
              pr["application_id"] == J.APPLICATION_ID and pr["user_version"] == J.JOURNAL_SCHEMA_VERSION, str(pr))
        m = j1.meta()
        check("journal_meta: one row with journal uid, project uid, machine guid and name, app version, created time",
              m and m["project_uid"] == "proj-uid-00000001" and m["machine_guid"] == MACHINE[0] and m["machine_name"] == "TESTBOX"
              and len(m["journal_uid"]) == 32 and m["app_version"] and m["created_utc"].endswith("Z"), str(m))
        tables = {r[0] for r in j1.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("tables: Job, OperationItem, EvidenceSnapshot, OperationEvent, RecoveryRecord, Owner and journal_meta",
              {"job", "operation_item", "evidence_snapshot", "operation_event", "recovery_record", "owner", "journal_meta"} <= tables)
        cols = {r[1] for r in j1.conn.execute("PRAGMA table_info(operation_event)")}
        need = {"action_id", "job_id", "item_id", "seq", "kind", "plan_ref", "evidence_snapshot_id", "before_identity", "after_identity",
                "method", "native_result", "win32_error", "verification_result", "recovery_token", "authorising_record",
                "software_version", "run_id", "projection_payload", "projection_state"}
        check("an event carries every minimum durable fact the handoff lists", need <= cols, str(need - cols))

        guid = J.read_machine_guid()
        check("the real MachineGuid is read (read-only winreg) and is GUID-shaped",
              re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", guid) is not None)

        ok, e = raises(J.JournalError, J.Journal.create, jp, "proj-uid-00000001", MACHINE)
        check("create refuses to write over an existing journal file", ok)

        jid, items = seed_job(j1, 2)
        now = J.utc_now()
        with j1.tx("e1") as t:
            t.execute(EVENT_SQL, ("act-1", jid, items[0], 1, "STEP_INTENT_RECORDED", "NOT_APPLICABLE", now))
        ok, e = raises(sqlite3.IntegrityError, lambda: j1.tx("dup").__enter__().execute(EVENT_SQL, ("act-1", jid, items[0], 1, "STEP_INTENT_RECORDED", "NOT_APPLICABLE", now)))
        with contextlib.suppress(sqlite3.Error):
            j1.conn.execute("ROLLBACK")
        check("UNIQUE(item_id, seq) refuses a second event with the same sequence number", ok, str(e))
        with j1.tx("e2") as t:
            t.execute(EVENT_SQL, ("act-1", jid, None, 1, "JOB_APPROVED", "NOT_APPLICABLE", now))
        ok, e = raises(sqlite3.IntegrityError, lambda: j1.tx("dup2").__enter__().execute(EVENT_SQL, ("act-x", jid, None, 1, "JOB_CLOSED", "NOT_APPLICABLE", now)))
        with contextlib.suppress(sqlite3.Error):
            j1.conn.execute("ROLLBACK")
        check("job-level events (no item) are unique per (job, seq) too", ok, str(e))
        with j1.tx("p1") as t:
            t.execute(EVENT_SQL, ("act-1", jid, items[0], 3, "VERIFICATION_RECORDED", "PENDING", now))
        ok, e = raises(sqlite3.IntegrityError, lambda: j1.tx("dup3").__enter__().execute(EVENT_SQL, ("act-1", jid, items[0], 4, "VERIFICATION_RECORDED", "PENDING", now)))
        with contextlib.suppress(sqlite3.Error):
            j1.conn.execute("ROLLBACK")
        check("at most one event of an action carries a projection (the catalog row is keyed by action_id)", ok, str(e))
        ok, e = raises(sqlite3.IntegrityError, lambda: j1.tx("bad").__enter__().execute(
            EVENT_SQL, ("a2", jid, items[1], 1, "NOT_A_KIND", "NOT_APPLICABLE", now)))
        with contextlib.suppress(sqlite3.Error):
            j1.conn.execute("ROLLBACK")
        check("an unknown event kind is refused by the schema", ok)
        ok, e = raises(sqlite3.IntegrityError, lambda: j1.tx("bad2").__enter__().execute(
            "UPDATE operation_item SET state = 'DONE_ISH' WHERE item_id = ?", (items[0],)))
        with contextlib.suppress(sqlite3.Error):
            j1.conn.execute("ROLLBACK")
        check("an unknown item state is refused by the schema", ok)

        # Fencing: the owner's run_id is checked inside the transaction, after BEGIN IMMEDIATE.
        j1.set_owner("run-A", "alice", "TESTBOX", 111, "execute")
        with j1.tx("c1", "run-A") as t:
            t.execute("UPDATE job SET state = 'RUNNING' WHERE job_id = ?", (jid,))
        j2.set_owner("run-B", "bob", "TESTBOX", 222, "execute")      # a second process took over (after the first froze)
        ok, e = raises(J.FencingLost, lambda: j1.tx("c2", "run-A").__enter__())
        st = j1.conn.execute("SELECT state FROM job WHERE job_id = ?", (jid,)).fetchone()[0]
        check("fencing: a run that no longer owns the journal cannot open a transaction (FencingLost), and writes nothing",
              ok and st == "RUNNING" and not j1.conn.in_transaction, str((e, st)))
        with j2.tx("c2b", "run-B") as t:
            t.execute("UPDATE job SET state = 'PAUSED' WHERE job_id = ?", (jid,))
        check("fencing: the new owner's transaction goes through", j1.conn.execute("SELECT state FROM job").fetchone()[0] == "PAUSED")
        check("the owner row carries user, machine, pid, operation and heartbeat",
              all(j1.owner()[k] for k in ("windows_user", "machine_name", "pid", "operation", "heartbeat_utc")))

        # Commit failure, including an injected SQLITE_FULL.
        def full(label):
            raise sqlite3.OperationalError("database or disk is full")
        j2.before_commit = full
        ok, e = raises(J.JournalCommitFailed, lambda: j2.tx("c3", "run-B").__enter__().__exit__(None, None, None))
        j2.before_commit = None
        with contextlib.suppress(sqlite3.Error):
            j2.conn.execute("ROLLBACK")
        ok2 = False
        try:
            with j2.tx("c3b", "run-B") as t:
                t.execute("UPDATE job SET state = 'COMPLETED' WHERE job_id = ?", (jid,))
                j2.before_commit = full
        except J.JournalCommitFailed:
            ok2 = True
        finally:
            j2.before_commit = None
        st = j2.conn.execute("SELECT state FROM job WHERE job_id = ?", (jid,)).fetchone()[0]
        check("an injected SQLITE_FULL at COMMIT raises JournalCommitFailed and the write did not happen", ok2 and st == "PAUSED", st)

        # Read-only view and backup.
        ro = J.Journal.open_readonly(jp)
        ok, e = raises(sqlite3.OperationalError, lambda: ro.conn.execute("UPDATE job SET state='ABANDONED'"))
        check("the read-only view can read the journal and cannot write it", ro.meta()["journal_uid"] == m["journal_uid"] and ok, str(e))
        ro.close()
        os.makedirs(sb.path("backup"), exist_ok=True)
        bk = j1.backup_to(sb.path("backup", "j.bak"))
        b = sqlite3.connect(bk)
        check("backup goes through SQLite's backup API and is a consistent copy",
              b.execute("SELECT COUNT(*) FROM operation_event").fetchone()[0] == j1.conn.execute("SELECT COUNT(*) FROM operation_event").fetchone()[0]
              and b.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        b.close()

        junk = sb.path("junk.db")
        c = sqlite3.connect(junk)
        c.execute("CREATE TABLE x(a)")
        c.close()
        ok, e = raises(J.JournalMismatch, J.Journal.open, junk)
        check("a SQLite file that is not a FileOrganizer journal is refused (application_id)", ok)
        ok, e = raises(J.JournalMissing, J.Journal.open, sb.path("nope.db"))
        check("opening a journal that does not exist is JournalMissing, not a new file", ok and not os.path.exists(sb.path("nope.db")))
        j1.close()
        j2.close()


# ===================================================================================================
# Unit 2 -- the opening rules
# ===================================================================================================
def journal_files(folder):
    return sorted(n for n in os.listdir(folder)) if os.path.isdir(folder) else []


def test_opening_rules():
    section("2c. The opening rules: binding x journal x jobs x identity")
    with F.Sandbox() as sb:
        conn, pdir, uid = make_catalog(sb, "cat1")
        folder = sb.path("store1")
        sig = dict(folder=folder, machine=MACHINE)
        check("migration 017 is part of a new schema-17 catalog: p4_journal_binding exists and is empty",
              conn.execute("SELECT COUNT(*) FROM p4_journal_binding").fetchone()[0] == 0)

        # 1. no binding, no journal -> create the journal, then write the binding
        j, how = J.open_for_project(conn, **sig)
        b = J.read_binding(conn)
        check("no binding and no journal: the journal is created, then the binding is written",
              how == "created" and b and b["project_uid"] == uid and b["journal_uid"] == j.meta()["journal_uid"]
              and b["machine_guid"] == MACHINE[0] and os.path.isfile(P.journal_path(P.store_folder(uid, folder), uid)))
        jid, items = seed_job(j)
        j.close()
        j, how = J.open_for_project(conn, **sig)
        check("binding and journal both present and agreeing: opened", how == "opened")
        j.close()

        # a catalog can never hold two bindings
        ok, e = raises(sqlite3.IntegrityError, lambda: conn.execute(
            "INSERT INTO p4_journal_binding VALUES (2, ?, 'x', 'y', 'z', 'now')", (uid,)))
        check("the catalog binding table holds at most one row (binding_id = 1)", ok)

        # 2. binding present, journal missing -> refuse, never create
        stored = P.store_folder(uid, folder)
        for n in journal_files(stored):
            if n.endswith((".db", ".db-wal", ".db-shm")):
                os.remove(os.path.join(stored, n))
        before = journal_files(stored)
        ok, e = raises(J.JournalMissing, J.open_for_project, conn, **sig)
        check("binding present, journal missing: refused (JournalMissing); NO blank journal is created",
              ok and journal_files(stored) == before and "never creates a blank journal" in str(e), str(e))

        # 3 / 4. journal present, binding absent
        conn2, pdir2, uid2 = make_catalog(sb, "cat2")
        folder2 = sb.path("store2")
        sig2 = dict(folder=folder2, machine=MACHINE)
        j, _ = J.open_for_project(conn2, **sig2)
        j.close()
        conn2.execute("DELETE FROM p4_journal_binding")
        j, how = J.open_for_project(conn2, **sig2)
        b2 = J.read_binding(conn2)
        check("journal present, binding absent, no jobs in the journal: the binding is completed",
              how == "completed_binding" and b2 and b2["journal_uid"] == j.meta()["journal_uid"])
        seed_job(j)
        j.close()
        conn2.execute("DELETE FROM p4_journal_binding")
        ok, e = raises(J.JournalBindingAsk, J.open_for_project, conn2, **sig2)
        check("journal present, binding absent, jobs in the journal: refused and asks (JournalBindingAsk); no binding written",
              ok and J.read_binding(conn2) is None, str(e))

        # 5. identity mismatches
        conn3, pdir3, uid3 = make_catalog(sb, "cat3")
        sig3 = dict(folder=sb.path("store3"), machine=MACHINE)
        j, _ = J.open_for_project(conn3, **sig3)
        j.close()
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, folder=sig3["folder"], machine=OTHER_MACHINE)
        check("another machine (MachineGuid differs from the binding's): refused", ok and "another machine" in str(e), str(e))
        conn3.execute("DELETE FROM p4_journal_binding")
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, folder=sig3["folder"], machine=OTHER_MACHINE)
        check("another machine (MachineGuid differs from the journal's, no binding): refused", ok, str(e))
        j, how = J.open_for_project(conn3, **sig3)                  # re-complete the binding on the right machine
        j.close()
        jpath3 = P.journal_path(P.store_folder(uid3, sig3["folder"]), uid3)
        raw = sqlite3.connect(jpath3)
        raw.execute("UPDATE journal_meta SET project_uid = 'some-other-project-uid'")
        raw.commit()
        raw.close()
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, **sig3)
        check("the journal's project_uid differs from the catalog's: refused", ok and "belongs to project" in str(e), str(e))
        raw = sqlite3.connect(jpath3)
        raw.execute("UPDATE journal_meta SET project_uid = ?", (uid3,))
        raw.execute("UPDATE journal_meta SET journal_uid = 'ffffffffffffffffffffffffffffffff'")
        raw.commit()
        raw.close()
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, **sig3)
        check("the journal file is not the one the catalog is bound to (journal uid differs): refused", ok and "bound to journal" in str(e), str(e))
        conn3.execute("UPDATE p4_journal_binding SET project_uid = 'another-project-uid'")
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, **sig3)
        check("the catalog's binding names a different project: refused", ok and "binding names project" in str(e), str(e))
        ok, e = raises(J.JournalMismatch, J.open_for_project, conn3, project_uid="not-this-project-xyz", **sig3)
        check("asked to open another project's journal: refused", ok, str(e))

        # J6: a refused folder creates nothing and writes no binding
        conn4, pdir4, uid4 = make_catalog(sb, "cat4")
        def stores():
            return [x for x in tree(sb.root) if not x.startswith("cat")]       # the catalogs are open; WAL files appear there

        snap = stores()
        for label, bad, cls in (("UNC", "\\\\server\\share\\j", None), ("non-fixed drive", sb.path("s4"), FakeClassifier(drive=4)),
                                ("OneDrive", sb.path("OneDrive", "s4"), FakeClassifier(onedrive=[sb.path("OneDrive")]))):
            ok, e = raises(P.PathRefused, J.open_for_project, conn4, folder=bad, machine=MACHINE, classifier=cls)
            check(f"J6: a {label} journal folder is refused with a clear message, no file or folder created, no binding written",
                  ok and stores() == snap and J.read_binding(conn4) is None, str(e))
        conn.close()
        conn2.close()
        conn3.close()
        conn4.close()


# ===================================================================================================
# Unit 2 -- migration 017 on a B20-schema project
# ===================================================================================================
def dump_tables(db_file):
    """{table: sorted rows} for every table, and the sqlite_master names."""
    c = sqlite3.connect(db_file)
    try:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out = {}
        for n in names:
            rows = [tuple(r) for r in c.execute(f'SELECT * FROM "{n}"')]
            out[n] = sorted(rows, key=repr)
        return out
    finally:
        c.close()


def test_migration_017():
    section("2d. Migration 017 on a B20-schema project: one new table, nothing else changes; a locked revision still verifies")
    import p3_dangling_check as DC
    from Phase3 import evidence as E
    from Phase3 import plans as PL
    from Phase3 import routing as RT
    with F.Sandbox() as sb:
        # Every connection this test opens is listed here and closed in `finally`, BEFORE the sandbox is removed,
        # even when a check or the scan raises: an open SQLite file cannot be deleted on Windows, and a leftover
        # sandbox is a broken promise (the Architect found one, 2026-11-07).
        conns = []

        def opened(c):
            conns.append(c)
            return c
        try:
            app_root = Path(sb.mkdir("AppRoot"))
            (app_root / "Projects").mkdir(parents=True, exist_ok=True)
            root = Path(sb.mkdir("Corpus", "Root1"))
            for rel in ("Inbox\\a.pdf", "Inbox\\b.pdf", "Inbox\\c.pdf", "Docs\\Existing.txt", "Dup\\k1.bin", "Dup\\k2.bin"):
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes((("same" if rel.startswith("Dup") else "content of " + rel).encode("utf-8")) * 40)
            with schema16():           # the product is at schema 17 now: a B20-schema project is built as B20 built it
                project_dir, ok, why = DC.scan(app_root, None, [root], "B20Fixture")
                check("a B20-schema project is built by the real worker in the sandbox (Pre-Scan and Find My Duplicates)", ok, why)
                conn, st, _E = DC.open_model(project_dir)
                opened(conn)
                check("the fixture project is at schema 16 (the build's current schema)", conn.execute("PRAGMA user_version").fetchone()[0] == 16)
                root_id = conn.execute("SELECT source_root_id FROM source_root WHERE root_path = ?", (str(root),)).fetchone()[0]
                c_id = DC.fid(conn, E, "Inbox", "c.pdf")
                st.record_organize(c_id, destination_root_id=root_id, destination_folder="Docs2")
                ev, proj, rt = DC.model(conn, E)
                draft = PL.build(ev, proj, rt)
                rid = st.record_plan_revision(draft, name="B20 plan")["plan_revision_id"]
                st.lock_plan_revision(rid)
                RT.reconcile(conn, st)
                rev0 = st.plan_revision(rid)[0]
                fp0 = rev0["fingerprint"]
                check("under B20 the plan revision is locked, verifies, and is not obsolete",
                      rev0["status"] == "locked" and st.verify_plan_revision(rid) is True, rev0["status"])
                check("the B20 plan has at least one item (so the fingerprint covers something)", len(draft.items) >= 1, str(len(draft.items)))
                conn.close()

            copy_dir = sb.path("B20FixtureCopy")
            shutil.copytree(project_dir, copy_dir)
            paths = fo_db.ProjectPaths(copy_dir)
            before = dump_tables(paths.database_file)
            master_before = opened(sqlite3.connect(paths.database_file)).execute(
                "SELECT type, name FROM sqlite_master ORDER BY type, name").fetchall()

            mconn = opened(sqlite3.connect(paths.database_file))
            with schema17():
                applied = fo_db.apply_migrations(mconn, paths)
            uv = mconn.execute("PRAGMA user_version").fetchone()[0]
            mconn.close()
            check("migration 017 applies on the sandbox copy, and only 017", applied == [17] and uv == 17, f"applied {applied}, user_version {uv}")

            after = dump_tables(paths.database_file)
            master_after = opened(sqlite3.connect(paths.database_file)).execute(
                "SELECT type, name FROM sqlite_master ORDER BY type, name").fetchall()
            new_objects = sorted(set(master_after) - set(master_before))
            gone = sorted(set(master_before) - set(master_after))
            check("exactly one new table (p4_journal_binding) and nothing removed from the schema",
                  [n for t, n in new_objects if t == "table"] == ["p4_journal_binding"] and not gone, str((new_objects, gone)))
            changed = [t for t in before if t != "schema_migration" and before[t] != after.get(t)]
            check("every other table is byte-identical after the migration (full row dump compare over %d tables)" % len(before),
                  not changed and after["p4_journal_binding"] == [], str(changed))
            sm_new = set(after["schema_migration"]) - set(before["schema_migration"])
            check("schema_migration gains exactly the 017 row", len(sm_new) == 1 and list(sm_new)[0][0] == 17 and len(before["schema_migration"]) + 1 == len(after["schema_migration"]))

            # the original (not migrated) project is untouched by all of this
            check("the original sandbox project is still schema 16", opened(sqlite3.connect(fo_db.ProjectPaths(project_dir).database_file)).execute("PRAGMA user_version").fetchone()[0] == 16)

            cconn, cst, _E2 = DC.open_model(copy_dir)
            opened(cconn)
            rev = cst.plan_revision(rid)[0]
            check("a plan revision locked under B20 STILL VERIFIES after migration 017 (fingerprint unchanged)",
                  cst.verify_plan_revision(rid) is True and rev["fingerprint"] == fp0, str(rev["status"]))
            RT.reconcile(cconn, cst)
            rev = cst.plan_revision(rid)[0]
            check("... and is NOT obsoleted by it (status still locked after the detector runs)", rev["status"] == "locked", rev["status"])
            cconn.close()
        finally:
            for c in reversed(conns):
                try:
                    c.close()
                except Exception:                                   # noqa: BLE001
                    pass


# ===================================================================================================
# Unit 4 -- the state machine and the runner
# ===================================================================================================
class Env:
    """One sandbox project run: a journal, the project lock, a fake filesystem, the fake action and a runner."""

    def __init__(self, sb, name="run", n_items=0, revision_ok=True, hook=None, projectors=None):
        from Phase4 import jobs as JB
        from Phase4 import locks as L
        from Phase4 import projection as PR
        self.JB, self.L, self.sb = JB, L, sb
        self.folder = sb.mkdir(name)
        self.uid = "proj-uid-00000001"
        self.jpath = os.path.join(self.folder, self.uid + ".journal.db")
        self.j = J.Journal.create(self.jpath, self.uid, MACHINE)
        self.lockpath = os.path.join(self.folder, self.uid + ".lock")
        self.lock = L.ProjectLock(self.lockpath, "execute", project_uid=self.uid, retry_seconds=0.3).acquire(journal=self.j)
        self.run_id = self.lock.run_id
        self.cat_conn, self.cat_dir, _uid = make_catalog(sb, name + "_cat")
        self.cat_paths = fo_db.ProjectPaths(self.cat_dir)
        self.fs = F.FakeFS(sb, name + "_fake.json")
        self.action = F.FakeAction(self.fs)
        self.items = F.fake_job_items(self.fs, n_items) if n_items else []
        self.revision_calls = []
        self.revision_ok = revision_ok
        self.sleeps = []
        self.timeline = []
        self.j.after_commit = lambda label: self.timeline.append("commit:" + label)
        self._user_hook = hook
        self.projection = PR.Projection(self.cat_conn, projectors) if projectors is not None else None
        self.runner = JB.JobRunner(self.j, self.run_id, {"fake": self.action}, self.revision_check,
                                   projection=self.projection, hook=self._hook, sleep=self.sleeps.append)
        sb.register(self.close)      # closed on cleanup even if the test raised (every step of close() is idempotent)

    def revision_check(self, ref):
        self.revision_calls.append(ref)
        return (self.revision_ok, "ok" if self.revision_ok else "obsolete: a decision changed")

    def _hook(self, point, item):
        self.timeline.append(point + ("" if item is None else "#%d" % item["position"]))
        if self._user_hook:
            self._user_hook(point, item)

    def backup(self):
        return fo_db.backup_database(self.cat_paths, "preJob")

    def approve(self, items=None, answer="Yes: OneDrive is synced and not paused", ref="rev-1"):
        spec = self.JB.JobSpec(ref, items if items is not None else self.items)
        return self.JB.approve_job(self.j, self.run_id, spec, revision_check=self.revision_check, backup_catalog=self.backup,
                                   onedrive_answer=answer, authorising_record="test: approved by the test person")

    def job(self, job_id):
        return self.runner.job(job_id)

    def states(self, job_id):
        return [r["state"] for r in self.runner.items(job_id)]

    def close(self):
        self.lock.release()
        self.j.close()
        self.cat_conn.close()


def count_states(states):
    out = {}
    for s in states:
        out[s] = out.get(s, 0) + 1
    return out


def test_tables():
    from Phase4 import jobs as JB
    section("4a. The state-machine tables as tests: every transition not in the table is refused")
    check("the item table has a row for every item state, and only names item states",
          set(JB.ITEM_TRANSITIONS) == set(J.ITEM_STATES) and all(set(v) <= set(J.ITEM_STATES) for v in JB.ITEM_TRANSITIONS.values()))
    check("the job table has a row for every job state, and only names job states",
          set(JB.JOB_TRANSITIONS) == set(J.JOB_STATES) and all(set(v) <= set(J.JOB_STATES) for v in JB.JOB_TRANSITIONS.values()))
    check("item kinds are exactly move, rename, case_only, quarantine, restore, create_folder, metadata_write and fake",
          set(J.ITEM_KINDS) == {"move", "rename", "case_only", "quarantine", "restore", "create_folder", "metadata_write", "fake"})
    check("the synthesis job states are exactly these seven",
          set(J.JOB_STATES) == {"APPROVED", "RUNNING", "PAUSED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_EXCEPTIONS", "ABANDONED"})
    check("nothing leaves APPLIED_VERIFIED except CHANGED_AFTER_FINAL; a completed job never moves",
          JB.ITEM_TRANSITIONS["APPLIED_VERIFIED"] == {"CHANGED_AFTER_FINAL"}
          and not JB.JOB_TRANSITIONS["COMPLETED"] and not JB.JOB_TRANSITIONS["ABANDONED"])
    check("INTENT is reachable only from PREFLIGHT_READY (no step without a preflight)",
          [s for s, to in JB.ITEM_TRANSITIONS.items() if "INTENT" in to] == ["PREFLIGHT_READY"])
    check("APPLIED is reachable only from INTENT (a result is never recorded before the intent)",
          sorted(s for s, to in JB.ITEM_TRANSITIONS.items() if "APPLIED" in to) == ["APPLIED_UNRECORDED", "INTENT"])

    with F.Sandbox() as sb:
        j = J.Journal.create(sb.path("t.journal.db"), "proj-uid-00000001", MACHINE)
        jid, items = seed_job(j, 1)
        item_id = items[0]
        bad_item = bad_job = ok_item = ok_job = 0
        wrong = []
        for old in J.ITEM_STATES:
            for new in J.ITEM_STATES:
                j.conn.execute("UPDATE operation_item SET state = ? WHERE item_id = ?", (old, item_id))
                allowed = new in JB.ITEM_TRANSITIONS[old]
                try:
                    with j.tx("t") as t:
                        JB.set_item_state(t, item_id, new)
                    done = True
                except JB.IllegalTransition:
                    done = False
                now = j.conn.execute("SELECT state FROM operation_item WHERE item_id = ?", (item_id,)).fetchone()[0]
                if done != allowed or now != (new if allowed else old):
                    wrong.append((old, new, done, now))
                ok_item += allowed
                bad_item += (not allowed)
        check("item states: all %d pairs behave as the table says (%d allowed and done, %d refused and left untouched)"
              % (len(J.ITEM_STATES) ** 2, ok_item, bad_item), not wrong, str(wrong[:3]))
        for old in J.JOB_STATES:
            for new in J.JOB_STATES:
                j.conn.execute("UPDATE job SET state = ? WHERE job_id = ?", (old, jid))
                allowed = new in JB.JOB_TRANSITIONS[old]
                try:
                    with j.tx("t") as t:
                        JB.set_job_state(t, jid, new)
                    done = True
                except JB.IllegalTransition:
                    done = False
                now = j.conn.execute("SELECT state FROM job WHERE job_id = ?", (jid,)).fetchone()[0]
                if done != allowed or now != (new if allowed else old):
                    wrong.append(("job", old, new, done, now))
                ok_job += allowed
                bad_job += (not allowed)
        check("job states: all %d pairs behave as the table says (%d allowed, %d refused)" % (len(J.JOB_STATES) ** 2, ok_job, bad_job), not wrong, str(wrong[:3]))
        j.close()

    check("scope: CONFLICT, STALE, BLOCKED_LOCKED and BLOCKED_CLOUD_ONLY are ITEM_LOCAL",
          all(JB.scope_of(c) == "ITEM_LOCAL" for c in ("CONFLICT", "STALE", "BLOCKED_LOCKED", "BLOCKED_CLOUD_ONLY")))
    check("scope: everything else -- journal, fencing, identity, sync-state unknown, postcondition mismatch, access, "
          "unsupported, an unnamed class -- is JOB_GLOBAL (unknown blocks)",
          all(JB.scope_of(c) == "JOB_GLOBAL" for c in ("JOURNAL_UNAVAILABLE", "FENCING_LOST", "IDENTITY_MISMATCH", "ROOT_IDENTITY_CHANGED",
                                                       "SYNC_STATE_UNKNOWN", "POSTCONDITION_MISMATCH", "BLOCKED_ACCESS", "UNSUPPORTED",
                                                       "UNKNOWN", "BLOCKED", "source_root_available", "something_new_nobody_named")))
    check("the breaker constants are the settled ones: 3 consecutive, 10 % with a minimum of 3 (DECISIONS.md 2026-11-07), "
          "lock retries 250 ms / 1 s / 3 s",
          JB.BREAKER_CONSECUTIVE == 3 and JB.BREAKER_PERCENT == 10 and JB.BREAKER_PERCENT_MIN == 3
          and JB.LOCK_RETRY_DELAYS == (0.25, 1.0, 3.0))
    expect = {1: 3, 2: 3, 8: 3, 10: 3, 29: 3, 30: 3, 31: 4, 45: 5, 100: 10, 101: 11, 500: 50}
    got = {n: JB.breaker_threshold(n) for n in expect}
    check("the per-class threshold is max(3, ceil(10 %% of the items)) (%s)" % ", ".join("%d->%d" % kv for kv in sorted(expect.items())),
          got == expect, str(got))


def test_happy_500():
    section("4b. A 500-item fake job runs to COMPLETED, through C0, C1, C2, C3, C4 in order")
    with F.Sandbox() as sb:
        env = Env(sb, "happy", n_items=500)
        jid = env.approve()
        job = env.job(jid)
        check("C0: the job is APPROVED with 500 items PLANNED; the manifest hash is the one recomputed from the items",
              job["state"] == "APPROVED" and env.states(jid) == ["PLANNED"] * 500
              and job["manifest_hash"] == env.JB.manifest_hash("rev-1", env.items), job["state"])
        check("C0 records the OneDrive answer exactly as given, the revision reference and the authorising record",
              job["onedrive_answer"] == "Yes: OneDrive is synced and not paused" and job["revision_ref"] == "rev-1"
              and job["authorising_record"].startswith("test:"))
        bk = job["catalog_backup_path"]
        bc = sqlite3.connect(bk)
        check("C0 records a catalog backup made with fo_db.backup_database (SQLite's backup API) inside the sandbox; it is a valid catalog",
              os.path.isfile(bk) and env.sb.contains(bk) and bc.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
              and bc.execute("SELECT COUNT(*) FROM p4_journal_binding").fetchone()[0] == 0 and "preJob" in os.path.basename(bk))
        bc.close()
        check("the revision check ran once, at C0", len(env.revision_calls) == 1, str(env.revision_calls))
        env.timeline.clear()
        t0 = time.perf_counter()
        out = env.runner.start(jid)
        took = time.perf_counter() - t0
        check("the job runs to COMPLETED: all 500 items APPLIED_VERIFIED", out.status == "completed" and out.job_state == "COMPLETED"
              and env.states(jid) == ["APPLIED_VERIFIED"] * 500, f"{out.status} {out.job_state} {count_states(env.states(jid))}")
        print(f"    500 items, 1,500 item commits + C0/C4 under synchronous=FULL: {took:.1f} s")
        check("the fake disk agrees: all 500 files moved, none left behind",
              len(env.fs.children("out")) == 500 and len(env.fs.children("in")) == 0)
        check("a counting revision check proves the job never triggers it from its own writes (still exactly one call)",
              len(env.revision_calls) == 1)
        # the order of the commit points for item 1
        first = []
        for e in env.timeline:
            first.append(e)
            if e == "C3:after#1":
                break
        want = ["preflight:before#1", "C1:before#1", "commit:C1", "C1:after#1", "step:before#1", "step:after#1", "C2:before#1",
                "commit:C2", "C2:after#1", "verify:after#1", "C3:before#1", "commit:C3", "C3:after#1"]
        check("item 1: preflight, C1 committed BEFORE the step, the step, THEN C2, verify, C3 -- in exactly that order, one commit each",
              first[0] == "commit:start" and first[1:] == want, str(first))
        commits = [e for e in env.timeline if e.startswith("commit:")]
        check("the whole job commits: start (RUNNING), then C1, C2, C3 for each item in order, then C4 last -- and nothing else",
              commits == ["commit:start"] + ["commit:C1", "commit:C2", "commit:C3"] * 500 + ["commit:C4"],
              str(commits[:6]) + "..." + str(commits[-4:]))
        ev = [dict(r) for r in env.j.conn.execute("SELECT * FROM operation_event WHERE item_id IS NOT NULL ORDER BY event_id")]
        kinds_ok = all([e["kind"] for e in ev[3 * i:3 * i + 3]] == ["STEP_INTENT_RECORDED", "STEP_RESULT_RECORDED", "VERIFICATION_RECORDED"]
                       and [e["seq"] for e in ev[3 * i:3 * i + 3]] == [1, 2, 3] and len({e["action_id"] for e in ev[3 * i:3 * i + 3]}) == 1
                       for i in range(500))
        check("every item has exactly three events, seq 1-3, STEP_INTENT_RECORDED / STEP_RESULT_RECORDED / VERIFICATION_RECORDED, one action_id",
              len(ev) == 1500 and kinds_ok)
        e1 = ev[0]
        check("the intent event carries the before identity, the evidence snapshot, the plan reference, the recovery token and the run id",
              e1["before_identity"] and e1["evidence_snapshot_id"] and e1["plan_ref"] == "plan-0001" and e1["recovery_token"].startswith("undo:")
              and e1["run_id"] == env.run_id and e1["software_version"] == "B21" and e1["authorising_record"])
        e2, e3 = ev[1], ev[2]
        check("the result event carries the native result and Win32 error; the verification event carries the after identity",
              e2["native_result"] and e2["win32_error"] == 0 and e3["after_identity"] and e3["verification_result"] == "APPLIED_VERIFIED")
        jr = env.job(jid)
        last = env.j.conn.execute("SELECT kind FROM operation_event WHERE item_id IS NULL ORDER BY seq DESC LIMIT 1").fetchone()[0]
        check("C4: the job is COMPLETED with a closed time and a JOB_CLOSED event", jr["state"] == "COMPLETED" and jr["closed_utc"] and last == "JOB_CLOSED")
        check("the journal still reports synchronous=2 / wal after the run", env.j.pragmas()["synchronous"] == 2)
        env.close()


def test_faults():
    from Phase4 import jobs as JB
    section("4c. Refusals, scopes, the circuit breaker, pause and resume")
    # -- unclassified failure on item 7 of 500 --------------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "unclass", n_items=500)
        env.action.faults["plan-0007"] = {"raise": "injected: an unclassified native failure"}
        jid = env.approve()
        out = env.runner.start(jid)
        st = env.states(jid)
        check("an injected unclassified failure on item 7 pauses the job (PAUSED, JOB_GLOBAL)",
              out.status == "paused" and out.scope == "JOB_GLOBAL" and env.job(jid)["state"] == "PAUSED", f"{out.status} {out.scope}")
        check("items 1-6 are APPLIED_VERIFIED; item 7 is FAILED_NOT_APPLIED (the verification found the disk unchanged); items 8-500 are untouched (PLANNED)",
              st[:6] == ["APPLIED_VERIFIED"] * 6 and st[6] == "FAILED_NOT_APPLIED" and st[7:] == ["PLANNED"] * 493, str(count_states(st)))
        untouched = all(env.action.calls.get("plan-%04d" % n) is None for n in range(8, 501)) and \
            env.j.conn.execute("SELECT COUNT(*) FROM operation_event e JOIN operation_item i ON i.item_id = e.item_id WHERE i.position > 7").fetchone()[0] == 0
        check("nothing was done to items 8-500: no preflight, no step, no journal event, and their files are still in place",
              untouched and len(env.fs.children("in")) == 494 and len(env.fs.children("out")) == 6)
        r7 = [dict(r) for r in env.j.conn.execute("SELECT e.kind, e.native_result, e.win32_error FROM operation_event e JOIN operation_item i ON i.item_id = e.item_id "
                                                  "WHERE i.position = 7 ORDER BY e.seq")]
        check("the item's evidence is on the record: intent, then a result that names the failure, then the verification",
              [r["kind"] for r in r7] == ["STEP_INTENT_RECORDED", "STEP_RESULT_RECORDED", "VERIFICATION_RECORDED"] and "unclassified failure" in r7[1]["native_result"])
        holder = env.L.holder_info(env.lockpath)
        check("a paused job keeps the project lock while its process lives", holder["held"] and holder["pid"] == os.getpid())

        # resume: the revision check runs again; if it fails the resume is blocked and nothing moves
        calls_before = dict((k, dict(v)) for k, v in env.action.calls.items())
        env.revision_ok = False
        ok, e = raises(JB.ResumeBlocked, env.runner.resume, jid)
        check("a revision check that fails at resume BLOCKS the resume (ResumeBlocked); the job stays PAUSED and no item is touched",
              ok and env.job(jid)["state"] == "PAUSED" and env.action.calls == calls_before and env.states(jid)[7:] == ["PLANNED"] * 493, str(e))
        check("the revision check ran at C0 and at each resume attempt: 1 + 1 = 2 calls so far", len(env.revision_calls) == 2, str(len(env.revision_calls)))
        env.revision_ok = True
        out2 = env.runner.resume(jid)
        check("with the revision current again the job resumes, re-runs each remaining item's preflight, and ends COMPLETED_WITH_EXCEPTIONS "
              "(item 7 stays FAILED_NOT_APPLIED)", out2.status == "completed" and out2.job_state == "COMPLETED_WITH_EXCEPTIONS"
              and env.states(jid).count("APPLIED_VERIFIED") == 499, f"{out2.job_state} {count_states(env.states(jid))}")
        check("... with exactly three revision checks in all (C0 and two resume attempts), none from the job's own writes",
              len(env.revision_calls) == 3, str(len(env.revision_calls)))
        check("the preflight of the remaining items ran after the resume (item 8: 1 preflight, 1 step)",
              env.action.calls["plan-0008"] == {"preflight": 1, "step": 1, "verify": 1})
        env.close()

    # -- 3 consecutive BLOCKED_LOCKED trip the breaker; the lock retry schedule -----------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "breaker", n_items=40)
        for ref in ("plan-0003", "plan-0004", "plan-0005"):
            env.action.faults[ref] = {"locked": 99}
        jid = env.approve()
        out = env.runner.start(jid)
        st = env.states(jid)
        check("three consecutive BLOCKED_LOCKED refusals trip the circuit breaker: the job pauses after item 5",
              out.status == "paused" and "circuit breaker" in out.reason and "3 consecutive BLOCKED_LOCKED" in out.reason
              and st[2:5] == ["BLOCKED_LOCKED"] * 3 and st[5:] == ["PLANNED"] * 35, f"{out.reason} {count_states(st)}")
        check("a lock refusal is retried at 250 ms, 1 s and 3 s (four attempts in all) before it ends BLOCKED_LOCKED, with a retry recorded in the result event",
              env.sleeps[:3] == [0.25, 1.0, 3.0] and env.action.calls["plan-0003"]["step"] == 4)
        ev = env.j.conn.execute("SELECT detail FROM operation_event e JOIN operation_item i ON i.item_id = e.item_id "
                                "WHERE i.position = 3 AND e.kind = 'STEP_RESULT_RECORDED'").fetchone()[0]
        att = json.loads(ev)["attempts"]
        check("the result event lists every attempt (four 'locked' answers, Win32 32) -- nothing was mutated in any of them",
              len(att) == 4 and all(a["status"] == "locked" and a["win32_error"] == 32 for a in att), ev)
        sc = env.j.conn.execute("SELECT refusal_class, refusal_scope FROM operation_item WHERE position = 5").fetchone()
        check("the refused item records its class and scope (BLOCKED_LOCKED, ITEM_LOCAL)", tuple(sc) == ("BLOCKED_LOCKED", "ITEM_LOCAL"))
        env.close()

    with F.Sandbox() as sb:
        env = Env(sb, "locked_then_ok", n_items=60)
        env.action.faults["plan-0003"] = {"locked": 2}
        jid = env.approve()
        out = env.runner.start(jid)
        check("a lock that clears after two retries is just retried: the item is APPLIED_VERIFIED and the job COMPLETED",
              out.job_state == "COMPLETED" and env.sleeps == [0.25, 1.0] and env.states(jid)[2] == "APPLIED_VERIFIED", f"{out.job_state} {env.sleeps}")
        env.close()

    # -- ITEM_LOCAL refusals continue; streaks reset; the 10 % rule ---------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "local", n_items=100)
        for ref, cls in (("plan-0003", "CONFLICT"), ("plan-0004", "CONFLICT"), ("plan-0010", "STALE"), ("plan-0011", "CONFLICT"),
                         ("plan-0012", "STALE"), ("plan-0050", "CONFLICT")):
            env.action.faults[ref] = {"preflight": cls}
        jid = env.approve()
        out = env.runner.start(jid)
        st = count_states(env.states(jid))
        check("ITEM_LOCAL refusals (CONFLICT, STALE) before any mutation do not stop the job: 2 consecutive, a success between, "
              "alternating classes, under 10 % -> it runs on and ends COMPLETED_WITH_EXCEPTIONS",
              out.status == "completed" and out.job_state == "COMPLETED_WITH_EXCEPTIONS" and st == {"APPLIED_VERIFIED": 94, "CONFLICT": 4, "STALE": 2}, f"{out.status} {st}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "pct", n_items=30)
        for ref in ("plan-0002", "plan-0005", "plan-0009"):
            env.action.faults[ref] = {"preflight": "CONFLICT"}
        jid = env.approve()
        out = env.runner.start(jid)
        check("10 % of the job's items refused in one class (3 of 30, never consecutive) trips the breaker at the third",
              out.status == "paused" and "3 of 30" in out.reason and env.states(jid)[9:] == ["PLANNED"] * 21, f"{out.reason}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "pct2", n_items=30)
        for ref in ("plan-0002", "plan-0005"):
            env.action.faults[ref] = {"preflight": "CONFLICT"}
        jid = env.approve()
        out = env.runner.start(jid)
        check("... and 2 of 30 (under 10 %) does not", out.status == "completed", f"{out.reason}")
        env.close()

    # -- small jobs: the Architect's ruling on J-8 (DECISIONS.md 2026-11-07): max(3, ceil(10 %)) -----------------------
    with F.Sandbox() as sb:
        env = Env(sb, "small", n_items=8)
        env.action.faults["plan-0002"] = {"preflight": "CONFLICT"}
        jid = env.approve()
        out = env.runner.start(jid)
        check("J-8 ruling: in a job of 8 items ONE item-local refusal no longer pauses the job (the minimum is 3)",
              out.status == "completed" and out.job_state == "COMPLETED_WITH_EXCEPTIONS" and env.states(jid).count("APPLIED_VERIFIED") == 7,
              f"{out.status} {out.reason}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "small2", n_items=8)
        for ref in ("plan-0002", "plan-0004"):
            env.action.faults[ref] = {"preflight": "CONFLICT"}
        jid = env.approve()
        out = env.runner.start(jid)
        check("... nor do 2 non-consecutive refusals of one class in 8 items", out.status == "completed", f"{out.reason}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "small3", n_items=8)
        for ref in ("plan-0002", "plan-0004", "plan-0006"):
            env.action.faults[ref] = {"preflight": "CONFLICT"}
        jid = env.approve()
        out = env.runner.start(jid)
        check("... but the third (never consecutive) pauses it: 3 of 8, items 7-8 untouched",
              out.status == "paused" and out.scope == "BREAKER" and "3 of 8" in out.reason and env.states(jid)[6:] == ["PLANNED"] * 2,
              f"{out.reason}")
        env.close()
    for n_items, refused, pauses in ((45, 4, False), (45, 5, True)):
        with F.Sandbox() as sb:
            env = Env(sb, "ceil%d" % refused, n_items=n_items)
            for k in range(refused):
                env.action.faults["plan-%04d" % (2 + 3 * k)] = {"preflight": "STALE"}
            jid = env.approve()
            out = env.runner.start(jid)
            check("ceil, not floor: %d STALE refusals (never consecutive) in %d items %s (threshold ceil(4.5) = 5)"
                  % (refused, n_items, "pause the job" if pauses else "do not pause it"),
                  (out.status == "paused" and "%d of %d" % (refused, n_items) in out.reason) if pauses else out.status == "completed",
                  f"{out.status} {out.reason}")
            env.close()

    # -- JOB_GLOBAL on item 1 stops the job ---------------------------------------------------------------------
    for cls in ("ROOT_IDENTITY_CHANGED", "source_root_available", "SYNC_STATE_UNKNOWN"):
        with F.Sandbox() as sb:
            env = Env(sb, "global", n_items=20)
            env.action.faults["plan-0001"] = {"preflight": cls}
            jid = env.approve()
            out = env.runner.start(jid)
            r = env.j.conn.execute("SELECT state, refusal_class, refusal_scope FROM operation_item WHERE position = 1").fetchone()
            check(f"a JOB_GLOBAL refusal on item 1 ({cls}) stops the job; items 2-20 untouched; the item is recorded JOB_GLOBAL",
                  out.status == "paused" and out.scope == "JOB_GLOBAL" and tuple(r) == ("BLOCKED", cls, "JOB_GLOBAL")
                  and env.states(jid)[1:] == ["PLANNED"] * 19 and "plan-0002" not in env.action.calls, f"{out.status} {tuple(r)}")
            env.close()

    # -- dependencies, kinds not built, create_folder -----------------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "dep", n_items=0)
        env.fs.add_folder("in")
        env.fs.add_file("in/a.dat")
        env.fs.add_file("in/b.dat")
        items = [JB.ItemSpec("fake", "mk", {"op": "create_folder", "path": "newdir"}),
                 JB.ItemSpec("fake", "mv-a", {"op": "rename", "src": "in/a.dat", "dst": "newdir/a.dat"}, depends_on=1),
                 JB.ItemSpec("fake", "mv-b", {"op": "rename", "src": "in/b.dat", "dst": "newdir/b.dat"}, depends_on=1)]
        jid = env.approve(items)
        out = env.runner.start(jid)
        check("D8: a create_folder step is its own journalled item, ordered before the moves that depend on it; all three verify",
              out.job_state == "COMPLETED" and env.fs.is_dir("newdir") and len(env.fs.children("newdir")) == 2, f"{out.job_state}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "dep2", n_items=0)
        env.fs.add_folder("in")
        env.fs.add_file("in/a.dat")
        env.fs.add_file("in/b.dat")
        items = [JB.ItemSpec("fake", "mk", {"op": "create_folder", "path": "newdir"}),
                 JB.ItemSpec("fake", "mv-a", {"op": "rename", "src": "in/a.dat", "dst": "newdir/a.dat"}, depends_on=1),
                 JB.ItemSpec("fake", "mv-b", {"op": "rename", "src": "in/b.dat", "dst": "newdir/b.dat"}, depends_on=1)]
        items += F.fake_job_items(env.fs, 12, folder="more", dest="done")      # a job big enough that 2 refusals are under 10 %
        env.action.faults["mk"] = {"preflight": "CONFLICT"}
        jid = env.approve(items)
        out = env.runner.start(jid)
        check("an item whose dependency did not reach APPLIED_VERIFIED is BLOCKED_DEPENDENCY (and nothing was done to it); the job goes on",
              env.states(jid)[:3] == ["CONFLICT", "BLOCKED_DEPENDENCY", "BLOCKED_DEPENDENCY"] and "mv-a" not in env.action.calls
              and out.job_state == "COMPLETED_WITH_EXCEPTIONS" and env.states(jid)[3:] == ["APPLIED_VERIFIED"] * 12, f"{env.states(jid)} {out.job_state}")
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "kinds", n_items=0)
        env.fs.add_folder("in")
        env.fs.add_file("in/a.dat")
        items = [JB.ItemSpec("fake", "ok1", {"op": "rename", "src": "in/a.dat", "dst": "in/a2.dat"}),
                 JB.ItemSpec("move", "mv", {"from": "x", "to": "y"}), JB.ItemSpec("quarantine", "q", {})]
        jid = env.approve(items)
        out = env.runner.start(jid)
        check("B21 executes only the fake kind: a 'move' item is UNSUPPORTED (no action certified) and, not being item-local, pauses the job",
              out.status == "paused" and env.states(jid) == ["APPLIED_VERIFIED", "UNSUPPORTED", "PLANNED"], f"{env.states(jid)}")
        ok, e = raises(JB.ApprovalRefused, env.approve, [JB.ItemSpec("metadata_write", "m", {"title": "x"})])
        check("a metadata_write item without a durable before-state is refused at approval (R4.3 row C4)", ok, str(e))
        ok, e = raises(JB.ApprovalRefused, env.approve, [JB.ItemSpec("delete_it", "m", {})])
        check("an item kind that does not exist (there is no delete kind: the product deletes nothing, D6) is refused at approval", ok, str(e))
        env.close()

    # -- a step that lies: reports success, disk disagrees -------------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "lie", n_items=10)
        env.action.faults["plan-0004"] = {"silent_no_op": True}
        env.action.faults["plan-0008"] = {"wrong_identity": True}
        jid = env.approve()
        out = env.runner.start(jid)
        st = env.states(jid)
        check("a postcondition mismatch (success reported, source still there = RESULT_NOT_ON_DISK) pauses the job at that item",
              out.status == "paused" and st[3] == "RESULT_NOT_ON_DISK" and st[4:] == ["PLANNED"] * 6, f"{st}")
        ok, e = raises(JB.ResumeBlocked, env.runner.resume, jid)
        check("an item in a recovery state needs the person's decision: resume refuses to run past it (nothing is auto-finished)",
              ok and env.states(jid)[4:] == ["PLANNED"] * 6 and env.action.calls.get("plan-0005") is None, str(e))
        env.close()


def test_pause_lock_commit_failure():
    from Phase4 import jobs as JB
    section("4d. Pause keeps the lock, exit leaves INTERRUPTED; a journal commit failure is JOB_GLOBAL; fencing")
    with F.Sandbox() as sb:
        env = Env(sb, "life", n_items=20)
        env.action.faults["plan-0005"] = {"raise": "boom"}
        jid = env.approve()
        env.runner.start(jid)
        check("PAUSED: the lock is still held while the process lives", env.L.holder_info(env.lockpath)["held"])
        env.runner.shutdown(jid)
        env.lock.release()
        jb = env.job(jid)
        check("on exit the job is left INTERRUPTED and the lock is released",
              jb["state"] == "INTERRUPTED" and not env.L.holder_info(env.lockpath)["held"])
        # a new process: a RUNNING job left by a dead run becomes INTERRUPTED at start
        env.j.conn.execute("UPDATE job SET state = 'RUNNING' WHERE job_id = ?", (jid,))
        lk2 = env.L.ProjectLock(env.lockpath, "recovery", retry_seconds=0.3).acquire(journal=env.j)
        got = JB.mark_orphans_interrupted(env.j, lk2.run_id)
        check("at start, with the lock held, a job found RUNNING (its process is gone) is marked INTERRUPTED -- classified later, never auto-finished",
              got == [jid] and env.job(jid)["state"] == "INTERRUPTED")
        lk2.release()
        env.j.close()
        env.cat_conn.close()

    # -- SQLITE_FULL at C1 of item 3: the step never runs ------------------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "full_c1", n_items=10)
        state = {"c1": 0}

        def full_at_c1(label):
            if label == "C1":
                state["c1"] += 1
                if state["c1"] == 3:
                    raise sqlite3.OperationalError("database or disk is full")
        env.j.before_commit = full_at_c1
        jid = env.approve()
        ok, e = raises(JB.JobStopped, env.runner.start, jid)
        env.j.before_commit = None
        st = env.states(jid)
        check("an injected SQLITE_FULL at C1 is JOB_GLOBAL: the runner stops (JobStopped), the job is PAUSED with the reason recorded",
              ok and e.scope == "JOB_GLOBAL" and env.job(jid)["state"] == "PAUSED" and "journal_commit_failed" in (env.job(jid)["pause_reason"] or ""), str(e))
        check("... the intent was not durable, so the step NEVER ran: item 3 is still PLANNED, its file is where it was, no step was called",
              st[:2] == ["APPLIED_VERIFIED"] * 2 and st[2] == "PLANNED" and env.action.calls["plan-0003"]["step"] == 0 and env.fs.exists("in/f0003.dat"))
        env.close()

    # -- SQLITE_FULL at C2: the step ran, the result is not recorded --------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "full_c2", n_items=10)
        state = {"c2": 0}

        def full_at_c2(label):
            if label == "C2":
                state["c2"] += 1
                if state["c2"] == 3:
                    raise sqlite3.OperationalError("database or disk is full")
        env.j.before_commit = full_at_c2
        jid = env.approve()
        ok, e = raises(JB.JobStopped, env.runner.start, jid)
        env.j.before_commit = None
        st = env.states(jid)
        kinds = [r[0] for r in env.j.conn.execute("SELECT e.kind FROM operation_event e JOIN operation_item i ON i.item_id = e.item_id WHERE i.position = 3 ORDER BY e.seq")]
        check("SQLITE_FULL at C2 (after the step ran): JobStopped; item 3 is INTENT with NO result event, while its file HAS moved -- "
              "exactly the 'applied, unrecorded' shape reconciliation row A2 classifies",
              ok and st[2] == "INTENT" and kinds == ["STEP_INTENT_RECORDED"] and env.fs.exists("out/f0003.dat") and not env.fs.exists("in/f0003.dat"), f"{st[:4]} {kinds}")
        ok, e = raises(JB.ResumeBlocked, env.runner.resume, jid)
        check("... and the job cannot be resumed past it (an INTENT item needs the person's decision)", ok, str(e))
        env.close()

    # -- fencing inside a run -----------------------------------------------------------------------------------
    with F.Sandbox() as sb:
        state = {}

        def take_over(point, item):
            if point == "C2:after" and item["position"] == 5:
                j2 = J.Journal.open(env.jpath)
                j2.set_owner("run-B-took-over", "bob", "TESTBOX", 4242, "execute")
                j2.close()
        env = Env(sb, "fence", n_items=10, hook=take_over)
        jid = env.approve()
        ok, e = raises(JB.JobStopped, env.runner.start, jid)
        st = env.states(jid)
        kinds = [r[0] for r in env.j.conn.execute("SELECT e.kind FROM operation_event e JOIN operation_item i ON i.item_id = e.item_id WHERE i.position = 5 ORDER BY e.seq")]
        check("FENCING in a run: when a second run takes the journal over after C2, the first run's C3 is refused -> JobStopped (JOB_GLOBAL); nothing more is written",
              ok and getattr(e.cause, "code", "") == "fencing_lost" and st[4] == "APPLIED" and kinds == ["STEP_INTENT_RECORDED", "STEP_RESULT_RECORDED"]
              and st[5:] == ["PLANNED"] * 5, f"{st} {kinds}")
        check("... the old run could not even record its own pause: the job is still RUNNING in the journal for the new owner to find",
              env.job(jid)["state"] == "RUNNING")
        j3 = J.Journal.open(env.jpath)
        got = JB.mark_orphans_interrupted(j3, "run-B-took-over")
        check("... and the new owner marks it INTERRUPTED at start", got == [jid] and j3.conn.execute("SELECT state FROM job").fetchone()[0] == "INTERRUPTED")
        j3.close()
        env.close()

    # -- obsolete at C0; frozen manifest; the journal's own triggers -----------------------------------------------------
    with F.Sandbox() as sb:
        env = Env(sb, "c0", n_items=5, revision_ok=False)
        ok, e = raises(JB.ApprovalRefused, env.approve)
        check("a revision check that fails at C0 refuses the approval; no job, no item, no event is written",
              ok and env.j.conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0 and env.j.conn.execute("SELECT COUNT(*) FROM operation_event").fetchone()[0] == 0, str(e))
        env.revision_ok = True
        ok, e = raises(JB.ApprovalRefused, lambda: JB.approve_job(env.j, env.run_id, JB.JobSpec("rev-1", env.items), revision_check=env.revision_check,
                                                                 backup_catalog=lambda: None, onedrive_answer=None, authorising_record="t"))
        check("a catalog backup that is not made refuses the approval too", ok and env.j.conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0, str(e))
        jid = env.approve(answer=None)
        check("an unanswered OneDrive question is stored as NULL, never as a guessed yes", env.job(jid)["onedrive_answer"] is None)
        for label, sql in (("the manifest hash", "UPDATE job SET manifest_hash = 'x'"), ("the revision reference", "UPDATE job SET revision_ref = 'x'"),
                           ("an item's kind", "UPDATE operation_item SET kind = 'move'"), ("an item's params", "UPDATE operation_item SET params = '{}'"),
                           ("an event's result", "UPDATE operation_event SET native_result = 'x'"), ("an event's deletion", "DELETE FROM operation_event"),
                           ("a projection backwards", "UPDATE operation_event SET projection_state = 'PENDING' WHERE projection_state = 'PROJECTED'")):
            ok, e = raises(sqlite3.IntegrityError, lambda: env.j.conn.execute(sql))
            if sql.startswith("UPDATE operation_event SET projection_state"):
                ok = True        # no PROJECTED row exists yet: covered with a projector below
            check(f"the journal refuses to change {label} after approval", ok, str(e))
        # a tampered manifest is caught at resume
        env.j.conn.execute("DROP TRIGGER item_frozen_at_c0")
        env.j.conn.execute("UPDATE operation_item SET params = '{\"op\":\"rename\",\"src\":\"in/f0001.dat\",\"dst\":\"elsewhere/x\"}' WHERE position = 1")
        env.j.conn.execute("UPDATE job SET state = 'PAUSED'")
        ok, e = raises(JB.ResumeBlocked, env.runner.resume, jid)
        check("a manifest that no longer matches the hash frozen at C0 blocks the resume", ok and "manifest" in str(e), str(e))
        env.close()


class _RecordingProjector:
    """A projector for the unit-4 ordering tests: records the action_ids it is asked to project, writes nothing."""

    def __init__(self, seen):
        self.seen = seen

    def apply(self, cat, ev):
        self.seen.append(ev["action_id"])

    def observe(self, cat, ev):
        return "absent"

    def action_ids(self, cat):
        return set()


def test_projection_hook():
    from Phase4 import jobs as JB
    section("4e. P and C4: a registered projector runs after C3 and the event is then marked PROJECTED; C4 waits for it")
    with F.Sandbox() as sb:
        seen = []
        env = Env(sb, "proj", n_items=5, projectors={"fake": _RecordingProjector(seen)})
        jid = env.approve()
        out = env.runner.start(jid)
        evs = [dict(r) for r in env.j.conn.execute("SELECT * FROM operation_event WHERE kind = 'VERIFICATION_RECORDED' ORDER BY event_id")]
        check("with a projector registered each verification event is PENDING at C3, projected after it, then marked PROJECTED; the job closes",
              out.job_state == "COMPLETED" and len(seen) == 5 and all(e["projection_state"] == "PROJECTED" and e["projected_utc"] for e in evs)
              and [e["action_id"] for e in evs] == seen, str([e["projection_state"] for e in evs]))
        order = [e for e in env.timeline if e.startswith(("commit:C3", "P:", "commit:P", "C4:before", "commit:C4"))][:6]
        check("the order is C3 commit, then P (apply, then the PROJECTED mark in its own commit), then the next item -- and C4 only at the end",
              order == ["commit:C3", "P:before#1", "P:after_apply#1", "commit:P", "P:after_mark#1", "commit:C3"], str(order))
        ok, e = raises(sqlite3.IntegrityError, lambda: env.j.conn.execute("UPDATE operation_event SET projection_state = 'PENDING' WHERE projection_state = 'PROJECTED'"))
        check("a projection never moves backwards (PROJECTED -> PENDING is refused by the journal)", ok)
        env.close()
    with F.Sandbox() as sb:
        env = Env(sb, "c4wait", n_items=3, projectors={"fake": _RecordingProjector([])})
        jid = env.approve()
        env.runner.start(jid)
        env.j.conn.execute("DROP TRIGGER projection_moves_forward")
        env.j.conn.execute("DROP TRIGGER event_is_a_fact")
        env.j.conn.execute("UPDATE operation_event SET projection_state = 'PENDING' WHERE event_id = (SELECT MAX(event_id) FROM operation_event WHERE kind = 'VERIFICATION_RECORDED')")
        env.j.conn.execute("UPDATE job SET state = 'RUNNING'")
        ok, e = raises(JB.JobError, env.runner._close, env.job(jid), 0)
        check("C4 refuses to close a job while an event is still PENDING (not PROJECTED / NOT_APPLICABLE)", ok and "not projected" in str(e), str(e))
        env.close()


def test_fakes_are_sandboxed():
    section("4f. The fakes touch nothing outside the sandbox")
    with F.Sandbox() as sb:
        fs = F.FakeFS(sb)
        fs.add_folder("a")
        fs.add_file("a/x.dat")
        fs.rename_no_overwrite("a/x.dat", "a/y.dat")
        fs2 = F.FakeFS(sb)
        check("the fake disk is one JSON file inside the sandbox and a restart (a new FakeFS on the same file) sees the state",
              sb.contains(fs.path) and fs2.exists("a/y.dat") and not fs2.exists("a/x.dat"))
        fs.add_file("a/z.dat")
        ok, e = raises(FileExistsError, fs.rename_no_overwrite, "a/z.dat", "a/y.dat")
        check("the fake rename never overwrites", ok and fs.exists("a/z.dat") and fs.exists("a/y.dat"))
        ok, e = raises(F.SandboxError, sb.inside, os.path.join(os.path.dirname(sb.root), "outside.txt"))
        check("the sandbox guard refuses a path outside the root before anything is written", ok)
        check("the fake leaves no file in the sandbox other than its own JSON (and the sandbox is the only place)",
              sorted(os.listdir(sb.root)) == ["fakefs.json"], str(os.listdir(sb.root)))


def run_unit4():
    test_tables()
    test_happy_500()
    test_faults()
    test_pause_lock_commit_failure()
    test_projection_hook()
    test_fakes_are_sandboxed()



# ===================================================================================================
# Units 5 and 6 -- the projection, and the reconciliation tables
# ===================================================================================================
import uuid                                                          # noqa: E402
from collections import namedtuple                                   # noqa: E402

from Phase4 import jobs as JB                                        # noqa: E402
from Phase4 import locks as L                                        # noqa: E402
from Phase4 import projection as PR                                  # noqa: E402
from Phase4 import reconcile as R                                    # noqa: E402

SCRIPT = Path(__file__).resolve()


def _items(fs, kind):
    if kind == "renames3":
        return F.fake_job_items(fs, 3)
    if kind == "folder_then_move":
        fs.add_folder("in")
        fs.add_file("in/a.dat")
        fs.add_folder("base")
        return [JB.ItemSpec("fake", "mk-newdir", {"op": "create_folder", "path": "base/newdir"}),
                JB.ItemSpec("fake", "mv-a", {"op": "rename", "src": "in/a.dat", "dst": "base/newdir/a.dat"}, depends_on=1)]
    raise ValueError(kind)


class World:
    """One sandbox project opened through the PRODUCT's open path (projection.open_session): a real catalog at schema 17
    in <sandbox>\\cat, the journal and the project lock in <sandbox>\\store, the fake disk <sandbox>\\fake.json, and the
    fake projector. The kill worker builds the same world in its own process."""

    def __init__(self, sb, create=True, projectors="fake", lock_retry=2.0):
        self.sb = sb
        self.cat_dir = sb.path("cat")
        self.store = sb.path("store")
        if create:
            sb.mkdir("cat")
            with schema17():
                fo_db.init_project(self.cat_dir, "cat")
        self.db = fo_db.ProjectPaths(self.cat_dir).database_file
        self.cat = sqlite3.connect(sb.inside(self.db), isolation_level=None)
        sb.register(self.cat.close)
        self.projector = F.FakeProjector()
        self.projection = PR.Projection(self.cat, {"fake": self.projector} if projectors == "fake" else dict(projectors or {}))
        self.session = PR.open_session(self.cat, self.projection, folder=self.store, machine=MACHINE, lock_retry_seconds=lock_retry)
        sb.register(self.session.close)
        self.j, self.run_id = self.session.journal, self.session.run_id
        self.fs = F.FakeFS(sb, "fake.json")
        self.action = F.FakeAction(self.fs)

    def runner(self, hook=None, revision_ok=True):
        return JB.JobRunner(self.j, self.run_id, {"fake": self.action}, lambda ref: (revision_ok, "ok"),
                            projection=self.projection, hook=hook, sleep=lambda s: None)

    def backup(self):
        return fo_db.backup_database(fo_db.ProjectPaths(self.cat_dir), "preJob")

    def approve(self, items, hook=None):
        return JB.approve_job(self.j, self.run_id, JB.JobSpec("rev-1", items), revision_check=lambda r: (True, "ok"),
                              backup_catalog=self.backup, onedrive_answer=None,
                              authorising_record="test: approved by the test person", hook=hook)

    def rows(self):
        return catalog_rows(self.cat)

    def close(self):
        self.session.close()
        self.cat.close()


def catalog_rows(cat):
    if cat.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='p4_fake_projection'").fetchone() is None:
        return []
    return [tuple(r) for r in cat.execute("SELECT action_id, event_id, item_kind, payload FROM p4_fake_projection ORDER BY action_id")]


class View:
    """READ-ONLY view of a world for recovery: the journal through a read-only connection (a write through it fails),
    the catalog read-only, the fake disk through the observer (which never saves). `fingerprint()` is compared before
    and after recover_on_start: recovery must change nothing."""

    def __init__(self, sb, projectors="fake", meta=None):
        self.sb = sb
        db = fo_db.ProjectPaths(sb.path("cat")).database_file
        self.cat = sqlite3.connect(Path(sb.inside(db)).as_uri() + "?mode=ro", uri=True, isolation_level=None)
        sb.register(self.cat.close)
        uid = self.cat.execute("SELECT project_uid FROM project WHERE project_id = 1").fetchone()[0]
        self.jpath = P.journal_path(sb.path("store"), uid)
        self.j = J.Journal.open_readonly(sb.inside(self.jpath))
        sb.register(self.j.close)
        self.projector = F.FakeProjector()
        self.projection = PR.Projection(self.cat, {"fake": self.projector} if projectors == "fake" else dict(projectors))
        self.fs = F.FakeFS(sb, "fake.json")
        self.observer = F.FakeObserver(self.fs, meta)

    def fingerprint(self):
        with open(self.fs.path, "rb") as fh:
            disk = fh.read()
        names = [r[0] for r in self.j.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        jd = {n: sorted((tuple(r) for r in self.j.conn.execute(f'SELECT * FROM "{n}"')), key=repr) for n in names}
        return disk, jd, catalog_rows(self.cat)

    def recover(self):
        before = self.fingerprint()
        rep = R.recover_on_start(self.j.conn, self.observer, self.projection)
        return rep, before == self.fingerprint()

    def item(self, position):
        return self.j.conn.execute("SELECT * FROM operation_item WHERE position = ? ORDER BY item_id DESC LIMIT 1", (position,)).fetchone()


# ---------------------------------------------------------------------------------------------------
# Real kills: a child process runs a job and is killed with TerminateProcess at a named hook
# ---------------------------------------------------------------------------------------------------
KILLS = {
    "C0":        {"items": "renames3", "point": "C0:after", "position": None},
    "C1@2":      {"items": "renames3", "point": "C1:after", "position": 2},
    "step@2":    {"items": "renames3", "point": "step:after", "position": 2},
    "C2@2":      {"items": "renames3", "point": "C2:after", "position": 2},
    "C2noop@2":  {"items": "renames3", "point": "C2:after", "position": 2, "faults": {"plan-0002": {"silent_no_op": True}}},
    "C2raise@2": {"items": "renames3", "point": "C2:after", "position": 2, "faults": {"plan-0002": {"raise": "injected native failure"}}},
    "C3@2":      {"items": "renames3", "point": "C3:after", "position": 2},
    "Papply@2":  {"items": "renames3", "point": "P:after_apply", "position": 2},
    "Pmark@2":   {"items": "renames3", "point": "P:after_mark", "position": 2},
    "C4before":  {"items": "renames3", "point": "C4:before", "position": None},
    "C4after":   {"items": "renames3", "point": "C4:after", "position": None},
    "fC1@1":     {"items": "folder_then_move", "point": "C1:after", "position": 1},
    "fstep@1":   {"items": "folder_then_move", "point": "step:after", "position": 1},
    "fC2@1":     {"items": "folder_then_move", "point": "C2:after", "position": 1},
}


def kill_worker(root, spec_path):
    """`p4_journal_check.py --kill-worker <sandbox root> <spec.json>`: build the world, run the job, and at the named
    hook print 'AT <point>' and wait to be killed. Never returns normally while the parent is alive."""
    sb = F.attach_sandbox(root)
    try:
        with open(sb.inside(spec_path), "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        w = World(sb)
        w.action.faults.update(spec.get("faults") or {})
        items = _items(w.fs, spec["items"])

        def hook(point, item):
            if point == spec["point"] and (spec.get("position") is None or (item is not None and item["position"] == spec["position"])):
                print("AT " + point, flush=True)
                sys.stdin.readline()
                time.sleep(3600)

        jid = w.approve(items, hook=hook)
        w.runner(hook=hook).start(jid)
        print("END the job finished without reaching the hook", flush=True)
    except BaseException as exc:                                    # noqa: BLE001
        print("ERR %s: %s" % (type(exc).__name__, exc), flush=True)
    sys.stdin.readline()
    return 3


def kill_at(sb, spec):
    """Run the worker in `sb` and kill it (TerminateProcess) at the named hook. Returns (reached, line, exit code)."""
    spec_path = sb.path("spec.json")
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh)
    proc = F.spawn(SCRIPT, "--kill-worker", sb.root, spec_path)

    def _stop():
        F.terminate_process(proc)
        try:
            proc.wait(20)
        except subprocess.TimeoutExpired:
            pass
        for s in (proc.stdin, proc.stdout):
            try:
                s.close()
            except Exception:                                       # noqa: BLE001
                pass
    sb.register(_stop)
    line = F.read_line(proc, timeout=120)
    _stop()
    return line == "AT " + spec["point"], line, proc.returncode


def copy_world(src_sb, dst_sb):
    """Copy a (killed) world: the catalog folder, the journal folder with its -wal and -shm as the kill left them, and
    the fake disk."""
    for name in ("cat", "store"):
        shutil.copytree(src_sb.path(name), dst_sb.inside(os.path.join(dst_sb.root, name)))
    shutil.copy2(src_sb.path("fake.json"), dst_sb.inside(os.path.join(dst_sb.root, "fake.json")))


class Masters:
    """Each kill runs once, into its own sandbox (the master); every test works on a fresh copy of it."""

    def __init__(self):
        self.sbs, self.results = {}, {}

    def get(self, key):
        if key not in self.sbs:
            sb = F.Sandbox()
            self.sbs[key] = sb
            self.results[key] = kill_at(sb, KILLS[key])
        return self.sbs[key], self.results[key]

    def copy(self, key, dst):
        src, res = self.get(key)
        copy_world(src, dst)
        return res

    def cleanup(self):
        for sb in self.sbs.values():
            sb.cleanup()


# -- disk edits made by a test after a kill (someone else changed the disk while the product was down) -------------
def _ed_rm(path):
    def ed(fs):
        fs.files.pop(fs.key(path))
        fs.save()
    ed.text = "%s removed from the fake disk" % path
    return ed


def _ed_hardlink(src, dst):
    def ed(fs):
        e = dict(fs.files[fs.key(src)])
        e["path"] = dst
        fs.files[fs.key(dst)] = e
        fs.save()
    ed.text = "a hard link to the source made at %s (same object)" % dst
    return ed


def _ed_add(path):
    def ed(fs):
        fs.add_file(path, size=999)
    ed.text = "a different file put at %s" % path
    return ed


def _ed_grow(path):
    def ed(fs):
        fs.files[fs.key(path)]["size"] += 1
        fs.save()
    ed.text = "%s changed (size + 1)" % path
    return ed


def _ed_move(a, b):
    def ed(fs):
        fs.rename_no_overwrite(a, b)
    ed.text = "the file moved back from %s to %s" % (a, b)
    return ed


def _ed_rmdir(path):
    def ed(fs):
        fs.dirs.pop(fs.key(path))
        fs.save()
    ed.text = "folder %s removed" % path
    return ed


def _ed_both(*eds):
    def ed(fs):
        for e in eds:
            e(fs)
    ed.text = "; ".join(e.text for e in eds)
    return ed


SRC2, DST2 = "in/f0002.dat", "out/f0002.dat"

#: How each row is reached. how: 'kill' (a real kill at the hook, nothing else), 'kill+edit' (a real kill, then the
#: test changes the fake disk as an outside actor would), 'constructed' (journal and disk state built directly).
Scen = namedtuple("Scen", "row how kill edit target note build")


def S(row, how, kill=None, edit=None, target=("item", 2), note="", build=None):
    return Scen(row, how, kill, edit, target, note, build)


# -- constructed states ---------------------------------------------------------------------------------------------
def _construct_intent(w, jid, position, before_body, result_status=None, with_c1=True):
    """Write C1 (and optionally C2) for one item DIRECTLY, through the state-machine functions, as the runner would.
    Used only where no B21 action can reach a row (metadata writes, case-only renames, a broken journal)."""
    item = w.j.conn.execute("SELECT * FROM operation_item WHERE job_id = ? AND position = ?", (jid, position)).fetchone()
    aid = uuid.uuid4().hex
    with w.j.tx("C1", w.run_id) as t:
        if with_c1:
            JB.set_item_state(t, item["item_id"], "PREFLIGHT_READY")
            JB.set_item_state(t, item["item_id"], "INTENT", current_action_id=aid)
            t.execute("INSERT INTO evidence_snapshot (item_id, snapshot_kind, body, created_utc) VALUES (?, 'before', ?, ?)",
                      (item["item_id"], json.dumps(before_body), J.utc_now()))
            snap = t.execute("SELECT MAX(snapshot_id) FROM evidence_snapshot").fetchone()[0]
            JB.add_event(t, w.run_id, jid, item["item_id"], aid, "STEP_INTENT_RECORDED", evidence_snapshot_id=snap,
                         before_identity=json.dumps(before_body))
        else:
            t.execute("UPDATE operation_item SET current_action_id = ? WHERE item_id = ?", (aid, item["item_id"]))
    if result_status is not None:
        with w.j.tx("C2", w.run_id) as t:
            JB.add_event(t, w.run_id, jid, item["item_id"], aid, "STEP_RESULT_RECORDED", native_result="constructed",
                         detail=json.dumps({"status": result_status}))
    return aid


def _build_meta(before_state="Old", target="New", current="Old", durable=True, result=None):
    def build(sb):
        w = World(sb)
        jid = w.approve([JB.ItemSpec("metadata_write", "md-1", {"op": "metadata", "path": "doc.pdf",
                                                                "before_state": before_state, "target": target})])
        body = {"path": "doc.pdf", "before_state": before_state} if durable else {"path": "doc.pdf"}
        _construct_intent(w, jid, 1, body, result_status=result)
        w.close()
        return View(sb, meta={"doc.pdf": current})
    return build


def _build_case_only(sb):
    w = World(sb)
    w.fs.add_folder("in")
    w.fs.add_file("in/a.dat")
    jid = w.approve([JB.ItemSpec("case_only", "co-1", {"src": "in/a.dat", "dst": "in/A.dat"})])
    _construct_intent(w, jid, 1, {"src": "in/a.dat", "dst": "in/A.dat", "identity": w.fs.identity("in/a.dat")})
    w.close()
    return View(sb)


def _build_inconsistent(sb):
    w = World(sb)
    jid = w.approve(_items(w.fs, "renames3"))
    _construct_intent(w, jid, 2, {}, result_status="ok", with_c1=False)      # a C2 with no C1 before it
    w.close()
    return View(sb)


def _completed(sb):
    w = World(sb)
    jid = w.approve(_items(w.fs, "renames3"))
    out = w.runner().start(jid)
    assert out.job_state == "COMPLETED", out
    return w, jid


def _build_abandoned(sb):
    w = World(sb)
    jid = w.approve(_items(w.fs, "renames3"))
    with w.j.tx("abandon", w.run_id) as t:
        JB.set_job_state(t, jid, "ABANDONED")
    w.close()
    return View(sb)


def _build_contradiction(sb):
    w, jid = _completed(sb)
    w.j.conn.execute("UPDATE operation_item SET state = 'INTENT' WHERE job_id = ? AND position = 2", (jid,))   # raw SQL
    w.close()
    return View(sb)


def restore_catalog_backup(sb, backup_path):
    """The catalog is restored from its C0 backup: every connection is closed first; the stale -wal and -shm are removed
    (a stale WAL beside a restored file would be replayed into it); then the backup is copied over the file."""
    db = sb.inside(fo_db.ProjectPaths(sb.path("cat")).database_file)
    for ext in ("-wal", "-shm"):
        if os.path.exists(db + ext):
            os.remove(sb.inside(db + ext))
    shutil.copy2(sb.inside(backup_path), db)


def _build_restored(sb):
    w, jid = _completed(sb)
    backup = w.j.conn.execute("SELECT catalog_backup_path FROM job WHERE job_id = ?", (jid,)).fetchone()[0]
    w.close()
    restore_catalog_backup(sb, backup)
    return View(sb)


def _build_unknown_row(sb):
    w, jid = _completed(sb)
    w.cat.execute("INSERT INTO p4_fake_projection (action_id, event_id, item_kind, payload, projected_utc) "
                  "VALUES ('feedfacefeedfacefeedfacefeedface', 9999, 'fake', '{}', 'x')")
    w.close()
    return View(sb)


def _build_differ(sb):
    w, jid = _completed(sb)
    w.cat.execute("UPDATE p4_fake_projection SET payload = '{\"edited\":true}' WHERE rowid = (SELECT MIN(rowid) FROM p4_fake_projection)")
    w.close()
    return View(sb)


def _build_no_projector(sb):
    w, jid = _completed(sb)
    w.close()
    return View(sb, projectors={})


SCENARIOS = [
    # ---- A: same-volume rename or move (the fake rename) ----
    S("A1", "kill", "C1@2", note="killed after C1 of item 2; the step never ran"),
    S("A2", "kill", "step@2", note="killed after the step of item 2, before C2"),
    S("A3", "kill+edit", "C1@2", _ed_rm(SRC2), note="killed after C1"),
    S("A4", "kill+edit", "C1@2", _ed_hardlink(SRC2, DST2), note="killed after C1"),
    S("A5", "kill+edit", "C1@2", _ed_add(DST2), note="killed after C1"),
    S("A6", "kill+edit", "C1@2", _ed_grow(SRC2), note="killed after C1"),
    S("A7", "kill", "C2@2", note="killed after C2 (success), before the verification"),
    S("A8", "kill", "C2noop@2", note="the step reported ok and changed nothing (fault: silent no-op); killed after C2"),
    S("A9", "kill+edit", "C2@2", _ed_rm(DST2), note="killed after C2 (success)"),
    S("A10", "kill", "C2raise@2", note="the step raised a native failure (fault); killed after C2 (failure)"),
    S("A11", "kill", "C3@2", target=("item-direct", 2), note="killed after C3 (verified), before P; classified directly (terminal)"),
    S("A12", "kill+edit", "C3@2", _ed_move(DST2, SRC2), target=("item-direct", 2), note="killed after C3; classified directly (terminal)"),
    S("AX", "kill+edit", "C1@2", _ed_both(_ed_rm(SRC2), _ed_add(DST2)), note="killed after C1"),
    S("AX", "constructed", target=("item", 1), build=_build_case_only, note="a case-only rename: source and destination are one path"),
    S("AX", "constructed", target=("item", 2), build=_build_inconsistent, note="a C2 without a C1 (the journal contradicts itself)"),
    # ---- C: metadata write (B21 has no metadata action: constructed) ----
    S("C1", "constructed", target=("item", 1), build=_build_meta(current="Old"), note="INTENT; the metadata equals the before-state"),
    S("C2", "constructed", target=("item", 1), build=_build_meta(current="New"), note="INTENT; the metadata equals the target"),
    S("C3", "constructed", target=("item", 1), build=_build_meta(current="Someone else's"), note="INTENT; neither"),
    S("C4", "constructed", target=("item", 1), build=_build_meta(durable=False), note="INTENT without a durable before-state (bypassing the refusals)"),
    S("CX", "constructed", target=("item", 1), build=_build_meta(before_state="Same", target="Same", current="Same"),
      note="the before-state equals the target: C1 and C2 cannot be told apart"),
    S("CX", "constructed", target=("item", 1), build=_build_meta(current="New", result="ok"), note="C2 recorded: table C has rows for INTENT only"),
    # ---- D: job level ----
    S("D1", "kill", "C1@2", target=("job",), note="killed mid-item: job RUNNING, item 2 INTENT"),
    S("D2", "kill", "C3@2", target=("job",), note="killed between items: job RUNNING, item 3 PLANNED"),
    S("D3", "kill", "C4before", target=("job",), note="killed before C4: every item verified"),
    S("D4", "kill", "C0", target=("job",), note="killed after C0, before the job started"),
    S("D5", "kill", "C4after", target=("job",), note="killed after C4"),
    S("D6", "constructed", target=("job",), build=_build_abandoned, note="an approved job abandoned (APPROVED -> ABANDONED)"),
    S("DX", "constructed", target=("job",), build=_build_contradiction, note="a COMPLETED job with an item set back to INTENT by raw SQL"),
    # ---- E: journal x projection ----
    S("E1", "kill", "C3@2", target=("event", 2), note="killed between C3 and P"),
    S("E2", "kill", "Papply@2", target=("event", 2), note="killed between the projection and the PROJECTED mark"),
    S("E3", "constructed", target=("event", 2), build=_build_restored, note="a completed job, then the C0 catalog backup restored over the catalog"),
    S("E4", "constructed", target=("unknown",), build=_build_unknown_row, note="a catalog row inserted for an action the journal never recorded"),
    S("E5", "kill", "Pmark@2", target=("event", 2), note="killed after the PROJECTED mark"),
    S("EX", "constructed", target=("event", 1), build=_build_differ, note="a catalog row edited so it no longer matches its event"),
    S("EX", "constructed", target=("event", 1), build=_build_no_projector, note="no projector registered that can read the row"),
    # ---- F: create_folder (the fake create_folder) ----
    S("F1", "kill", "fC1@1", target=("item", 1), note="killed after C1 of the folder step"),
    S("F2", "kill", "fstep@1", target=("item", 1), note="killed after the folder was created, before C2"),
    S("F3", "kill+edit", "fstep@1", _ed_add("base/newdir/x.dat"), target=("item", 1), note="killed after the step"),
    S("F4", "kill+edit", "fC1@1", _ed_rmdir("base"), target=("item", 1), note="killed after C1"),
    S("FX", "kill", "fC2@1", target=("item", 1), note="killed after C2: table F has rows for INTENT only"),
]


def _pick(view, rep, target):
    kind = target[0]
    if kind == "item":
        hits = [f for f in rep.items if f.position == target[1]]
        return hits[0] if len(hits) == 1 else None
    if kind == "item-direct":
        return R.classify_item(view.j.conn, view.item(target[1]), view.observer)
    if kind == "job":
        return rep.jobs[0] if len(rep.jobs) == 1 else None
    if kind == "event":
        hits = [f for f in rep.projection if f.position == target[1]]
        return hits[0] if len(hits) == 1 else None
    if kind == "unknown":
        hits = [f for f in rep.projection if f.key.get("projection") == "NONE"]
        return hits[0] if len(hits) == 1 else None
    raise ValueError(target)


def run_scenario(masters, sc):
    """Reach the scenario's state, then classify it READ-ONLY. Returns (finding or None, recovery changed nothing,
    how it was reached, an error text)."""
    with F.Sandbox() as sb:
        if sc.how in ("kill", "kill+edit"):
            reached, line, code = masters.copy(sc.kill, sb)
            if not reached:
                return None, False, "", "the kill worker did not reach %s: %r" % (KILLS[sc.kill]["point"], line)
            how = "kill at %s%s (TerminateProcess; exit code %s)" % (
                KILLS[sc.kill]["point"], "" if KILLS[sc.kill]["position"] is None else " of item %d" % KILLS[sc.kill]["position"], code)
            if sc.edit is not None:
                sc.edit(F.FakeFS(sb, "fake.json"))
                how += "; then " + sc.edit.text
            view = View(sb)
        else:
            view = sc.build(sb)
            how = "constructed: " + sc.note
        before = view.fingerprint()
        rep = R.recover_on_start(view.j.conn, view.observer, view.projection)
        f = _pick(view, rep, sc.target)
        return f, before == view.fingerprint(), how, ""


# ---------------------------------------------------------------------------------------------------
# 5. Unit 5 -- the projection
# ---------------------------------------------------------------------------------------------------
def test_projection_basics():
    section("5a. The projection registry and the fake projector: keyed by action_id, idempotent, sandbox-only")
    check("B21 registers no product projector (the real ones are B24's)", PR.PRODUCT_PROJECTORS == {})
    with F.Sandbox() as sb:
        conn, pdir, uid = make_catalog(sb, "pcat")
        proj = F.FakeProjector()
        ev = {"event_id": 7, "action_id": "a" * 32, "item_kind": "fake", "projection_payload": '{"op":"rename"}'}
        p = PR.Projection(conn, {"fake": proj})
        check("before any projection the event's row is absent", p.observe(ev) == "absent")
        p.apply(ev)
        once = catalog_rows(conn)
        p.apply(ev)
        twice = catalog_rows(conn)
        check("applying one event twice leaves exactly one row, unchanged (idempotent, keyed by action_id)",
              len(once) == 1 and once == twice and once[0][0] == "a" * 32, str(twice))
        check("... and the projection then observes it as 'match'", p.observe(ev) == "match")
        other = dict(ev, projection_payload='{"op":"something else"}')
        ok, e = raises(PR.ProjectionConflict, p.apply, other)
        check("a different event under the same action_id is a ProjectionConflict (JOB_GLOBAL) and is never written over",
              ok and catalog_rows(conn) == once and isinstance(e, J.JournalError) and e.scope == "JOB_GLOBAL", str(e))
        check("... and observes as 'differ'", p.observe(other) == "differ")
        check("an event of a kind no projector serves observes as 'unknown'", p.observe(dict(ev, item_kind="move")) == "unknown")
        ok, e = raises(PR.ProjectionError, p.apply, dict(ev, item_kind="move"))
        check("... and cannot be applied (ProjectionError)", ok, str(e))
        ok, e = raises(PR.ProjectionError, PR.Projection, sqlite3.connect(":memory:"), {})
        check("a catalog connection that is not autocommit is refused (each projector owns its transaction)", ok, str(e))
        mem = sqlite3.connect(":memory:", isolation_level=None)
        ok, e = raises(F.SandboxError, PR.Projection(mem, {"fake": F.FakeProjector()}).apply, ev)
        check("the fake projector refuses a catalog that is not a file inside a fo_p4_b21_ sandbox", ok, str(e))
        mem.close()


def test_projection_in_a_run():
    section("5b. P inside a run: one catalog row per verified action, written after C3, then the event marked PROJECTED")
    with F.Sandbox() as sb:
        w = World(sb)
        check("the world was opened through projection.open_session (the journal created and bound to the catalog)",
              w.session.opened == "created" and J.read_binding(w.cat) is not None)
        timeline = []
        w.j.after_commit = lambda label: timeline.append("commit:" + label)
        jid = w.approve(F.fake_job_items(w.fs, 5))
        out = w.runner(hook=lambda point, item: timeline.append(point + ("" if item is None else "#%d" % item["position"]))).start(jid)
        evs = [dict(r) for r in w.j.conn.execute("SELECT * FROM operation_event WHERE projection_state <> 'NOT_APPLICABLE' ORDER BY event_id")]
        rows = w.rows()
        check("a 5-item job COMPLETES with 5 projecting events, all PROJECTED, and exactly 5 catalog rows keyed by their action_ids",
              out.job_state == "COMPLETED" and len(evs) == 5 and all(e["projection_state"] == "PROJECTED" for e in evs)
              and sorted(r[0] for r in rows) == sorted(e["action_id"] for e in evs) and len(rows) == 5, f"{out.job_state} {len(rows)}")
        check("each catalog row carries its event's id and payload", all((r[1], r[3]) in {(e["event_id"], e["projection_payload"]) for e in evs} for r in rows))
        i = timeline.index("commit:C3")
        check("order for item 1: C3 commit, then P (P:before, the projector, P:after_apply, the PROJECTED mark in its own commit, P:after_mark)",
              timeline[i:i + 6] == ["commit:C3", "C3:after#1", "P:before#1", "P:after_apply#1", "commit:P", "P:after_mark#1"], str(timeline[i:i + 6]))
        check("C4 is the last commit, after every P", [t for t in timeline if t.startswith("commit:")][-1] == "commit:C4"
              and timeline.index("commit:C4") > max(k for k, t in enumerate(timeline) if t == "commit:P"))
        w.close()

    class _Failing(F.FakeProjector):
        def apply(self, cat, ev):
            raise PR.ProjectionError("injected: the catalog write failed")
    with F.Sandbox() as sb:
        w = World(sb)
        w.projection.projectors["fake"] = _Failing()
        jid = w.approve(F.fake_job_items(w.fs, 3))
        ok, e = raises(JB.JobStopped, w.runner().start, jid)
        job = w.j.conn.execute("SELECT state, pause_reason FROM job").fetchone()
        evs = [r[0] for r in w.j.conn.execute("SELECT projection_state FROM operation_event WHERE projection_state <> 'NOT_APPLICABLE'")]
        check("a projection failure inside a run is JOB_GLOBAL: the run stops, the job is PAUSED, the event stays PENDING, "
              "item 2 is never started and C4 is not reached",
              ok and e.scope == "JOB_GLOBAL" and job[0] == "PAUSED" and "projection" in (job[1] or "") and evs == ["PENDING"]
              and [r[0] for r in w.j.conn.execute("SELECT state FROM operation_item ORDER BY position")] == ["APPLIED_VERIFIED", "PLANNED", "PLANNED"],
              f"{job and tuple(job)} {evs}")
        w.close()

    # C4 waits for every projection (found by p4_mutation_check M17: no check owned this rule). P never ran, so every
    # event is still PENDING when the loop reaches C4; the job must not close.
    with F.Sandbox() as sb:
        w = World(sb)
        jid = w.approve(F.fake_job_items(w.fs, 3))
        r = w.runner()
        r._project = lambda item, event_id: None                 # P is skipped: every event stays PENDING
        ok, e = raises(JB.JobError, r.start, jid)
        job = w.j.conn.execute("SELECT state, closed_utc FROM job").fetchone()
        pend = w.j.conn.execute("SELECT COUNT(*) FROM operation_event WHERE projection_state = 'PENDING'").fetchone()[0]
        closes = w.j.conn.execute("SELECT COUNT(*) FROM operation_event WHERE kind = 'JOB_CLOSED'").fetchone()[0]
        check("C4 closes a job only when every event is PROJECTED or NOT_APPLICABLE: with 3 events still PENDING the close is refused, "
              "the job is not COMPLETED and no JOB_CLOSED event is written",
              ok and "not projected" in str(e) and job[0] not in ("COMPLETED", "COMPLETED_WITH_EXCEPTIONS") and job[1] is None
              and pend == 3 and closes == 0, f"{e} {tuple(job)} pending={pend} closes={closes}")
        w.close()


def test_projection_convergence(masters):
    section("5c. A kill around P converges on the next open (real kills); a restored catalog is repaired; an unknown row stops everything")
    for key, label in (("C3@2", "between C3 and P"), ("Papply@2", "between P and the PROJECTED mark")):
        with F.Sandbox() as sb:
            reached, line, code = masters.copy(key, sb)
            check(f"the kill worker was killed {label} of item 2 (TerminateProcess at {KILLS[key]['point']})", reached, repr(line))
            if not reached:
                continue
            pre = View(sb)
            pending = [f for f in R.classify_projection(pre.j.conn, pre.projection) if f.outcome != "none"]
            pre.j.close()
            pre.cat.close()
            w = World(sb, create=False)
            rows = w.rows()
            evs = [dict(r) for r in w.j.conn.execute("SELECT action_id, projection_state FROM operation_event WHERE projection_state <> 'NOT_APPLICABLE'")]
            check(f"next open ({label}): table E said {[f.row_id for f in pending]}; after open_session every projecting event is PROJECTED "
                  "and the catalog holds exactly one row per action (2 items verified, 2 rows)",
                  len(pending) == 1 and all(e["projection_state"] == "PROJECTED" for e in evs) and len(evs) == 2
                  and sorted(r[0] for r in rows) == sorted(e["action_id"] for e in evs), f"{rows} {evs}")
            jid = w.j.conn.execute("SELECT job_id FROM job").fetchone()[0]
            check("the job was found RUNNING and marked INTERRUPTED by the same open", w.session.orphans == [jid])
            out = w.runner().resume(jid)
            rows = w.rows()
            check("resumed, the job COMPLETES; the catalog ends with exactly 3 rows, one per action, none duplicated",
                  out.job_state == "COMPLETED" and len(rows) == 3 and len({r[0] for r in rows}) == 3, f"{out.job_state} {len(rows)}")
            w.close()
            w2 = World(sb, create=False)
            check("replay runs on EVERY open: a second open finds everything consistent (E5 only) and replays nothing",
                  set(w2.session.replay.counts()) == {"E5"} and not w2.session.replay.replayed and len(w2.rows()) == 3,
                  str(w2.session.replay.counts()))
            w2.close()

    with F.Sandbox() as sb:
        w, jid = _completed(sb)
        before_rows = w.rows()
        backup = w.j.conn.execute("SELECT catalog_backup_path FROM job").fetchone()[0]
        w.close()
        restore_catalog_backup(sb, backup)
        w = World(sb, create=False)
        check("an OLDER catalog (the C0 backup) restored under the newer journal is repaired by replay (E3 x3): the same 3 rows are back",
              w.session.replay.counts() == {"E3": 3} and w.rows() == before_rows, str(w.session.replay.counts()))
        w.close()

    with F.Sandbox() as sb:
        reached, line, code = masters.copy("C3@2", sb)
        cat = sqlite3.connect(sb.inside(fo_db.ProjectPaths(sb.path("cat")).database_file), isolation_level=None)
        cat.execute("INSERT INTO p4_fake_projection (action_id, event_id, item_kind, payload, projected_utc) "
                    "VALUES ('0123456789abcdef0123456789abcdef', 1, 'fake', '{}', 'x')")
        cat_before = catalog_rows(cat)
        cat.close()
        v = View(sb)
        jbefore = v.fingerprint()[1]
        v.j.close()
        v.cat.close()
        ok, e = raises(PR.ProjectionAsk, World, sb, False)
        v = View(sb)
        jafter = v.fingerprint()[1]
        pend = v.j.conn.execute("SELECT COUNT(*) FROM operation_event WHERE projection_state = 'PENDING'").fetchone()[0]
        check("a catalog row unknown to the journal stops everything: open_session raises ProjectionAsk (JOB_GLOBAL, 'ask', row E4)",
              ok and e.scope == "JOB_GLOBAL" and any(f.row_id == "E4" for f in e.findings), str(e))
        check("... and NOTHING was replayed or marked: item 2's event is still PENDING, the catalog unchanged, the job not touched",
              pend == 1 and catalog_rows(v.cat) == cat_before
              and {k: x for k, x in jafter.items() if k != "owner"} == {k: x for k, x in jbefore.items() if k != "owner"}, f"pending {pend}")
        lk = L.ProjectLock(P.lock_path(sb.path("store"), v.cat.execute("SELECT project_uid FROM project").fetchone()[0]), "check", retry_seconds=0.2)
        lk.acquire()
        lk.release()
        check("... and the refused open released the project lock (a new holder takes it at once)", True)


# ---------------------------------------------------------------------------------------------------
# 6. Unit 6 -- reconciliation
# ---------------------------------------------------------------------------------------------------
R43_A = {"A1": "INTERRUPTED_NOT_APPLIED", "A2": "APPLIED_UNRECORDED", "A3": "UNKNOWN_AFTER_INTERRUPT", "A4": "UNKNOWN_AFTER_INTERRUPT",
         "A5": "BLOCKED_COLLISION", "A6": "PRECONDITION_DRIFT", "A7": "APPLIED_UNVERIFIED", "A8": "RESULT_NOT_ON_DISK",
         "A9": "UNKNOWN_AFTER_INTERRUPT", "A10": "FAILED_NOT_APPLIED", "A11": "APPLIED_VERIFIED", "A12": "CHANGED_AFTER_FINAL"}
R43_A_OUTCOME = {"A1": "finish", "A2": "record", "A3": "ask", "A4": "ask", "A5": "ask", "A6": "ask", "A7": "record",
                 "A8": "finish", "A9": "ask", "A10": "none", "A11": "none", "A12": "none"}


def test_tables_as_data():
    section("6a. The tables are data: A1-A12, C1-C4, D, E, F, each with one X row; table B is not built")
    rows = R.all_rows()
    ids = [r.id for r in rows]
    check("row ids are unique across the five tables", len(ids) == len(set(ids)), str(ids))
    check("the tables are exactly A, C, D, E and F (B, cross-volume, is not built)", sorted(R.TABLES) == ["A", "C", "D", "E", "F"])
    check("each table has exactly one X row (no other row covers the key: R4.3's UNKNOWN_AFTER_INTERRUPT, 'ask')",
          all(sum(1 for r in R.TABLES[t] if r.when is None) == 1 and [r for r in R.TABLES[t] if r.when is None][0].outcome == "ask" for t in R.TABLES))
    check("every outcome is one of finish, record, roll_back, ask, none, replay", all(r.outcome in R.OUTCOMES for r in rows))
    a = {r.id: r for r in R.TABLE_A}
    check("table A is R4.3 2.5 rows A1-A12 (+ AX), each with R4.3's recovered state",
          {k: a[k].recovered_state for k in R43_A} == R43_A and set(a) == set(R43_A) | {"AX"})
    check("... and R4.3's next action (finish / record / ask / none)", {k: a[k].outcome for k in R43_A} == R43_A_OUTCOME,
          str({k: a[k].outcome for k in R43_A}))
    c = {r.id: (r.recovered_state, r.outcome) for r in R.TABLE_C}
    check("table C is R4.3 rows C1-C4 (+ CX): not applied / finish, applied unrecorded / record, unknown / ask, NO_UNDO_RECORD / ask",
          c == {"C1": ("INTERRUPTED_NOT_APPLIED", "finish"), "C2": ("APPLIED_UNRECORDED", "record"), "C3": ("UNKNOWN_AFTER_INTERRUPT", "ask"),
                "C4": ("NO_UNDO_RECORD", "ask"), "CX": ("UNKNOWN_AFTER_INTERRUPT", "ask")}, str(c))
    e = {r.id: r.outcome for r in R.TABLE_E}
    check("table E is the handoff's four rows (replay, record, replay, ask) plus E5 (consistent: none) and EX (ask)",
          e == {"E1": "replay", "E2": "record", "E3": "replay", "E4": "ask", "E5": "none", "EX": "ask"}, str(e))
    f = {r.id: (r.recovered_state, r.outcome) for r in R.TABLE_F}
    check("table F is the handoff's rows: absent -> not applied (finish); present and empty -> applied unrecorded (record); "
          "not empty -> ask; parent missing -> ask (+ FX)",
          f == {"F1": ("INTERRUPTED_NOT_APPLIED", "finish"), "F2": ("APPLIED_UNRECORDED", "record"), "F3": ("UNKNOWN_AFTER_INTERRUPT", "ask"),
                "F4": ("UNKNOWN_AFTER_INTERRUPT", "ask"), "FX": ("UNKNOWN_AFTER_INTERRUPT", "ask")}, str(f))
    d = {r.id: r.outcome for r in R.TABLE_D}
    check("table D: an open job with an item needing a decision stays INTERRUPTED and asks (D1); nothing in D finishes on its own "
          "(D2-D4 offer, the person chooses)", d["D1"] == "ask" and all(R.row(x).recovered_state in ("INTERRUPTED", "APPROVED") for x in ("D1", "D2", "D3", "D4")))
    item_states = set(J.ITEM_STATES)
    check("every recovered state of tables A, C and F is an item state of the journal",
          all(r.recovered_state in item_states for t in ("A", "C", "F") for r in R.TABLES[t]))
    parts = [set(JB.NEEDS_DECISION_STATES), {"PLANNED", "PREFLIGHT_READY"}, set(JB.TERMINAL_ITEM_STATES)]
    check("table D's three item summaries partition the item states (needs a decision | runnable | terminal)",
          set().union(*parts) == item_states and sum(len(p) for p in parts) == len(item_states))


def _physical_a(k):
    """A table-A key a real disk can show (distinct paths; 'same' consistent with presence and identity)."""
    if k["shape"] != "distinct_paths":
        return False
    if "absent" in (k["src"], k["dst"]):
        return k["same"] == "n/a"
    if k["same"] == "n/a":
        return False
    if k["src"] == k["dst"] == "match":
        return k["same"] == "yes"
    if k["same"] == "yes":
        return k["src"] == k["dst"]
    return True


def test_determinism():
    section("6b. Determinism (handoff 8.5): every key of every table's key space maps to exactly one row")
    for t in ("A", "C", "D", "E", "F"):
        keys = R.keyspace(t)
        multi = [k for k in keys if len(R.explicit_matches(t, k)) > 1]
        rows_used = {R.classify(t, k).id for k in keys}
        never = [r.id for r in R.TABLES[t] if r.id not in rows_used]
        fallback = [k for k in keys if not R.explicit_matches(t, k)]
        check("table %s: %d keys, each covered by at most one explicit row (%d by one, %d fall to %sX); every row is reachable"
              % (t, len(keys), len(keys) - len(fallback), len(fallback), t), not multi and not never, f"ambiguous {multi[:2]} unreachable {never}")
    gaps = [k for k in R.keyspace("A") if _physical_a(k) and k["journal"] != "INCONSISTENT" and not R.explicit_matches("A", k)]
    print("  INFO table A keys a real disk can show that no R4.3 row covers (they are AX, 'ask'): %d" % len(gaps))
    for k in gaps:
        print("       %-15s src=%-6s dst=%-6s same=%s" % (k["journal"], k["src"], k["dst"], k["same"]))
    ok, e = raises(R.ReconcileError, R.classify, "A", {"journal": "INTENT"})
    check("a key outside a table's key space is refused, never guessed", ok, str(e))
    saved = R.TABLES["A"]
    try:
        R.TABLES["A"] = saved + (R.Row("A", "A1bis", {"journal": ("INTENT",), "src": ("match",)}, "X", "finish", "", "test"),)
        ok, e = raises(R.AmbiguousRow, R.classify, "A", {"journal": "INTENT", "shape": "distinct_paths", "src": "match", "dst": "absent", "same": "n/a"})
        check("two rows covering one key raise AmbiguousRow (a deliberately overlapping row, in this process only)", ok, str(e))
    finally:
        R.TABLES["A"] = saved


def test_generated_rows(masters):
    section("6c. Every row has a generated test, reached by a real kill where possible and constructed otherwise; "
            "recovery changes nothing")
    by_row = {}
    for sc in SCENARIOS:
        by_row.setdefault(sc.row, []).append(sc)
    table_ids = [r.id for r in R.all_rows()]
    missing = [rid for rid in table_ids if rid not in by_row]
    extra = [rid for rid in by_row if rid not in table_ids]
    check("every row of tables A, C, D, E and F has at least one generated test (and no test names a row that does not exist)",
          not missing and not extra, f"no test: {missing}; unknown rows: {extra}")
    tally = {"kill": 0, "kill+edit": 0, "constructed": 0}
    rows_by_how = {}
    lines = []
    for rid in table_ids:
        for sc in by_row.get(rid, []):
            try:
                f, unchanged, how, err = run_scenario(masters, sc)
            except Exception as exc:                                # noqa: BLE001
                f, unchanged, how, err = None, False, "", "%s: %s" % (type(exc).__name__, exc)
            got = None if f is None else f.row_id
            ok = got == rid and unchanged and not err
            check("row %-4s reached (%s) and classified %s; recovery changed nothing" % (rid, sc.how, rid), ok,
                  err or f"classified {got}, unchanged {unchanged}, key {None if f is None else f.key}")
            if ok:
                tally[sc.how] += 1
                rows_by_how.setdefault(rid, set()).add(sc.how)
            r = R.row(rid)
            lines.append("  %-4s %-4s %-24s %-7s %-11s %s" % (rid, "PASS" if ok else "FAIL", r.recovered_state, r.outcome, sc.how, how))
    print("\n  The reconciliation row list (row, result, recovered state, next action, how reached, detail):")
    for ln in lines:
        print(ln)
    by_kill = sum(1 for rid, hs in rows_by_how.items() if "kill" in hs or "kill+edit" in hs)
    print("  Scenarios: %d by a real kill alone, %d by a real kill then a disk edit, %d constructed; rows reached by a kill: %d of %d"
          % (tally["kill"], tally["kill+edit"], tally["constructed"], by_kill, len(table_ids)))


def test_recovery_never_acts(masters):
    section("6d. Recovery shows and takes no action: read-only journal, no step, no projection, disk unchanged")
    with F.Sandbox() as sb:
        masters.copy("step@2", sb)
        v = View(sb)
        v.projection.projectors = {"fake": _ObserveOnly(v.projector)}
        rep, unchanged = v.recover()
        f = [x for x in rep.items if x.position == 2]
        check("item 2 is shown as A2 (applied, unrecorded) with its next action 'record' -- and the record is NOT written",
              len(f) == 1 and f[0].row_id == "A2" and unchanged, str([(x.row_id, x.outcome) for x in rep.items]))
        ok, e = raises(sqlite3.OperationalError, v.j.conn.execute, "UPDATE job SET pause_reason = 'x'")
        check("the journal connection recovery read through is read-only (a write fails)", ok, str(e))
        check("the observer was asked (the disk was looked at) and the projector's apply was never called",
              v.observer.calls >= 1 and v.projector.applied == [])
        check("the report lists every finding with its row and next action, for the person",
              any("[A2]" in ln and "record" in ln for ln in rep.lines()) and any("[D1]" in ln for ln in rep.lines()), "\n".join(rep.lines()))
        import inspect
        src = inspect.getsource(R)
        writes = [w for w in (".tx(", ".apply(", ".step(", ".preflight(", ".verify(", "set_item_state(", "set_job_state(",
                              "add_event(", "INSERT ", "UPDATE ", "DELETE ", ".commit(", ".save(") if w in src]
        check("reconcile.py contains no write and no call that acts: no transaction, apply, step, preflight, verify, state "
              "change, event, INSERT / UPDATE / DELETE, commit or save", not writes, str(writes))


class _ObserveOnly:
    """Wraps a projector so that anything but observe/action_ids fails the test."""

    def __init__(self, inner):
        self.inner = inner

    def observe(self, cat, ev):
        return self.inner.observe(cat, ev)

    def action_ids(self, cat):
        return self.inner.action_ids(cat)

    def apply(self, cat, ev):
        raise AssertionError("recovery applied a projection")


def test_restore_ceremony_documented():
    section("6e. The restore ceremony for a lost or corrupt journal is documented in ARCHITECTURE.md")
    text = (SCRIPTS.parent / "ARCHITECTURE.md").read_text(encoding="utf-8")
    i = text.find("restore ceremony")
    body = text[i:i + 6000].lower() if i >= 0 else ""
    check("ARCHITECTURE.md has the restore ceremony", i >= 0)
    for words, what in ((("latest", "backup"), "take the latest backup"),
                        (("last receipt", "identities on disk"), "compare its last receipt with the identities on disk"),
                        (("reconcile the catalog",), "reconcile the catalog"),
                        (("acknowledg",), "the person's acknowledgement before any action")):
        check("... step: %s" % what, all(w in body for w in words))


def run_units_5_6():
    masters = Masters()
    try:
        test_projection_basics()
        test_projection_in_a_run()
        test_projection_convergence(masters)
        test_tables_as_data()
        test_determinism()
        test_generated_rows(masters)
        test_recovery_never_acts(masters)
        test_restore_ceremony_documented()
        section("Units 5-6: every kill worker was killed at its hook")
        for key in sorted(masters.results):
            reached, line, code = masters.results[key]
            check("kill %-10s reached %s and was killed there (exit code %s)" % (key, KILLS[key]["point"], code), reached, repr(line))
    finally:
        masters.cleanup()


# ===================================================================================================
def main():
    before = F.localappdata_fileorganizer_state()
    sandboxes_before = F.sandbox_folders()
    only = sys.argv[1:]
    if not only or "j5" in only:
        probe_j5()
    test_paths()
    test_store()
    test_opening_rules()
    test_migration_017()
    run_unit4()
    run_units_5_6()

    section("The production data folder is untouched")
    after = F.localappdata_fileorganizer_state()
    check("%LOCALAPPDATA%\\FileOrganizer: existence and file listing unchanged by this run", before == after)
    survivors = sorted(F.sandbox_folders() - sandboxes_before)
    check("no fo_p4_b21_* sandbox folder created during this run survives it", not survivors,
          "; ".join(survivors[:5]) + ("" if not F.LEFTOVER_SANDBOXES else " (cleanup could not remove: %s)" % F.LEFTOVER_SANDBOXES[:5]))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\np4_journal_check: {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--kill-worker":
        sys.exit(kill_worker(sys.argv[2], sys.argv[3]))
    sys.exit(main())
