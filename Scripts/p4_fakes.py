r"""Phase 4 test-only helpers (B21): the sandbox, process and crash helpers.

TEST-ONLY. Nothing here is imported by the product package Scripts\Phase4. The fake filesystem and the
fake action (unit 4) are added to this file.

The sandbox rule (AGENTS.md, handoff B21 section 4):
  * every test makes its own root with tempfile.mkdtemp(prefix="fo_p4_b21_") under %TEMP%;
  * every path a test writes is asserted to be inside that root BEFORE it is written;
  * %LOCALAPPDATA%\FileOrganizer\ is never created or touched: a suite-level check records whether it
    exists, and its file listing, before the run, and asserts both are unchanged after.
"""
from __future__ import annotations

import ctypes
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SANDBOX_PREFIX = "fo_p4_b21_"

#: Sandbox roots that cleanup() could not remove (a file still open). A suite fails when one survives the run.
LEFTOVER_SANDBOXES = []


def sandbox_folders():
    """The set of fo_p4_b21_* folders under %TEMP% now (a suite compares the set before and after its run)."""
    return set(os.path.normcase(p) for p in glob.glob(os.path.join(tempfile.gettempdir(), SANDBOX_PREFIX + "*")))


# ---------------------------------------------------------------------------------------------------
# The sandbox
# ---------------------------------------------------------------------------------------------------
class SandboxError(AssertionError):
    """A path escaped (or could not be proved inside) the sandbox. The test must not run."""


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


class Sandbox:
    """One throwaway root. Use as a context manager; the root is removed on exit."""

    def __init__(self):
        base = tempfile.gettempdir()
        self.root = os.path.realpath(tempfile.mkdtemp(prefix=SANDBOX_PREFIX, dir=base))
        # Prove the root is where it must be before anything is written inside it.
        if not os.path.basename(self.root).startswith(SANDBOX_PREFIX):
            raise SandboxError("sandbox root has an unexpected name: %s" % self.root)
        if not _norm(self.root).startswith(_norm(os.path.realpath(base)) + os.sep):
            raise SandboxError("sandbox root is not under %%TEMP%%: %s" % self.root)
        self._closed = False
        self._closers = []

    def register(self, closer):
        """Call `closer()` when the sandbox is cleaned up, BEFORE its folder is removed (last registered, first
        called). A test that raises half-way still closes its databases, so the folder can be removed."""
        self._closers.append(closer)
        return closer

    # -- path guards ----------------------------------------------------------------------------
    def contains(self, path) -> bool:
        p = _norm(os.path.realpath(os.fspath(path)))
        r = _norm(self.root)
        return p == r or p.startswith(r + os.sep)

    def inside(self, path) -> str:
        """Return `path` as a string if it is inside the root, else raise SandboxError."""
        if not self.contains(path):
            raise SandboxError("path is outside the sandbox (%s): %s" % (self.root, path))
        return os.fspath(path)

    def path(self, *parts) -> str:
        return self.inside(os.path.join(self.root, *parts))

    def mkdir(self, *parts) -> str:
        p = self.path(*parts)
        os.makedirs(p, exist_ok=True)
        return p

    def write_bytes(self, rel, data: bytes) -> str:
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    # -- life cycle -----------------------------------------------------------------------------
    def cleanup(self):
        if self._closed:
            return
        self._closed = True
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception:                                   # noqa: BLE001
                pass
        self._closers = []
        # Only ever remove a folder this object made: re-check name and location first.
        if not (os.path.basename(self.root).startswith(SANDBOX_PREFIX) and self.contains(self.root)):
            return
        shutil.rmtree(self.root, ignore_errors=True)
        if os.path.exists(self.root):
            # Something still holds a file open. The test that opened it is at fault (the suite-level check
            # reports the survivor); record it so the failure names the folder.
            LEFTOVER_SANDBOXES.append(self.root)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False


# ---------------------------------------------------------------------------------------------------
# The production data folder must stay untouched
# ---------------------------------------------------------------------------------------------------
def localappdata_fileorganizer_state():
    """(exists, sorted file listing) of %LOCALAPPDATA%\\FileOrganizer\\ -- read-only look."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return (False, ())
    folder = os.path.join(base, "FileOrganizer")
    if not os.path.isdir(folder):
        return (False, ())
    listing = []
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
                listing.append((os.path.relpath(full, folder), st.st_size, st.st_mtime_ns))
            except OSError:
                listing.append((os.path.relpath(full, folder), -1, -1))
    return (True, tuple(sorted(listing)))


# ---------------------------------------------------------------------------------------------------
# Child processes and hard crashes (tests and probes only)
# ---------------------------------------------------------------------------------------------------
def worker_cmd(script, *args):
    """Command line for `python script --worker ...` with the interpreter that is running now."""
    return [sys.executable, os.fspath(script)] + [str(a) for a in args]


def spawn(script, *args, stdin=True, env=None):
    """Start a worker. stdout is a pipe (line protocol); stdin is a pipe when asked."""
    return subprocess.Popen(
        worker_cmd(script, *args),
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=env,
    )


def read_line(proc, timeout=20.0):
    """Read one stdout line from a worker, or return None after `timeout` seconds."""
    import threading
    out = []

    def _r():
        try:
            out.append(proc.stdout.readline())
        except Exception:                                           # noqa: BLE001
            out.append("")

    t = threading.Thread(target=_r, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive() or not out:
        return None
    return out[0].strip()


def send_line(proc, text):
    proc.stdin.write(text + "\n")
    proc.stdin.flush()


def taskkill_force(pid) -> float:
    """`taskkill /F /PID`; returns the monotonic time at which the command returned."""
    subprocess.run(["taskkill", "/F", "/PID", str(int(pid))], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)
    return time.perf_counter()


def terminate_process(proc) -> None:
    """TerminateProcess (what Task Manager's End process and a kill hook use)."""
    try:
        proc.kill()
    except OSError:
        pass


def hard_crash_access_violation():
    """Crash this process with an unhandled access violation, outside any Python/ctypes guard.

    A thread is started at address 0 with CreateThread; the exception happens on a thread no SEH
    handler covers, so the process dies with 0xC0000005. The error dialog is suppressed first.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetErrorMode(0x0001 | 0x0002 | 0x8000)   # FAILCRITICALERRORS | NOGPFAULTERRORBOX | NOOPENFILEERRORBOX
    k32.CreateThread.restype = ctypes.c_void_p
    k32.CreateThread.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_void_p]
    h = k32.CreateThread(None, 0, None, None, 0, None)
    time.sleep(30)          # never reached normally: the thread's fault ends the process
    return h


def suspend_process(pid) -> bool:
    """Suspend every thread of a process (NtSuspendProcess) -- the 'debugger freeze' of probe L11."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x0800, False, int(pid))                    # PROCESS_SUSPEND_RESUME
    if not h:
        return False
    try:
        return nt.NtSuspendProcess(ctypes.c_void_p(h)) == 0
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def resume_process(pid) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x0800, False, int(pid))
    if not h:
        return False
    try:
        return nt.NtResumeProcess(ctypes.c_void_p(h)) == 0
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def pid_alive(pid) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x1000, False, int(pid))                    # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_uint32()
        k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        return code.value == 259                                    # STILL_ACTIVE
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


def percentile(values, q):
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q / 100.0 * len(s) + 0.4999999)) - 1))
    return s[k]


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


# ---------------------------------------------------------------------------------------------------
# The fake filesystem and the fake action (B21 unit 4). TEST-ONLY.
#
# No real file is created, renamed, moved or deleted by anything below. The "disk" is ONE JSON file inside
# the sandbox, saved (write a complete new file, then replace) after every change, so a restart -- a new
# process reading the same JSON -- sees the "disk" state the crash left.
# ---------------------------------------------------------------------------------------------------
import json  # noqa: E402


class FakeFS:
    """A tiny case-insensitive filesystem: files (with an id, size, mtime) and folders. Rename never overwrites."""

    def __init__(self, sandbox, name="fakefs.json"):
        self.sb = sandbox
        self.path = sandbox.path(name)
        self.files = {}          # key -> {"path": display, "id": n, "size": n, "mtime": n}
        self.dirs = {"": "root"}  # key -> display
        self.next_id = 1000
        if os.path.exists(self.path):
            self.load()
        else:
            self.save()

    # -- persistence: complete new file, then replace ------------------------------------------------
    def save(self):
        tmp = self.sb.path(os.path.basename(self.path) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"files": self.files, "dirs": self.dirs, "next_id": self.next_id}, fh, sort_keys=True)
        os.replace(tmp, self.sb.inside(self.path))

    def load(self):
        with open(self.sb.inside(self.path), "r", encoding="utf-8") as fh:
            d = json.load(fh)
        self.files, self.dirs, self.next_id = d["files"], d["dirs"], d["next_id"]

    @staticmethod
    def key(p):
        return p.replace("/", "/").lower().strip("/")

    @staticmethod
    def parent_key(k):
        return k.rsplit("/", 1)[0] if "/" in k else ""

    # -- queries -------------------------------------------------------------------------------------
    def exists(self, p):
        k = self.key(p)
        return k in self.files or k in self.dirs

    def is_dir(self, p):
        return self.key(p) in self.dirs

    def identity(self, p):
        e = self.files.get(self.key(p))
        return None if e is None else {"id": e["id"], "size": e["size"], "mtime": e["mtime"], "path": e["path"]}

    def children(self, p):
        k = self.key(p)
        return [x for x in list(self.files) + list(self.dirs) if x and self.parent_key(x) == k]

    # -- changes (each saves) ------------------------------------------------------------------------
    def add_folder(self, p):
        k = self.key(p)
        if self.parent_key(k) not in self.dirs:
            raise FileNotFoundError(p)
        self.dirs[k] = p.strip("/")
        self.save()

    def add_file(self, p, size=100, mtime=1_700_000_000):
        k = self.key(p)
        if self.parent_key(k) not in self.dirs:
            raise FileNotFoundError(p)
        self.next_id += 1
        self.files[k] = {"path": p.strip("/"), "id": self.next_id, "size": size, "mtime": mtime}
        self.save()

    def rename_no_overwrite(self, src, dst):
        """Raises FileExistsError (the fake of error 183) / FileNotFoundError (the fake of 2 or 3)."""
        ks, kd = self.key(src), self.key(dst)
        if ks not in self.files:
            raise FileNotFoundError(src)
        if self.parent_key(kd) not in self.dirs:
            raise FileNotFoundError(dst)
        if kd in self.files or kd in self.dirs:
            raise FileExistsError(dst)
        e = self.files.pop(ks)
        e["path"] = dst.strip("/")
        self.files[kd] = e
        self.save()

    def create_folder(self, p):
        k = self.key(p)
        if self.parent_key(k) not in self.dirs:
            raise FileNotFoundError(p)
        if k in self.files or k in self.dirs:
            raise FileExistsError(p)
        self.dirs[k] = p.strip("/")
        self.save()

    def snapshot(self):
        return json.dumps({"files": self.files, "dirs": self.dirs}, sort_keys=True)


class FakeAction:
    """The only action B21 executes: a no-overwrite rename or a folder creation on a FakeFS.

    params: {"op": "rename", "src": ..., "dst": ...} or {"op": "create_folder", "path": ...}

    Fault injection, by plan_ref (set `faults[plan_ref] = {...}`):
        {"preflight": "CONFLICT"}              the live preflight refuses with that class
        {"locked": n}                          the step answers 'locked' (Win32 32, nothing mutated) n times, then works
        {"raise": "text"}                      the step raises an unclassified exception, nothing mutated
        {"silent_no_op": True}                 the step reports ok and changes nothing (the A8 shape)
        {"wrong_identity": True}               the step reports ok, renames, but the file seen afterwards has another id
    Counters: self.calls[plan_ref] = {"preflight": n, "step": n}
    """

    method = "fake_rename"

    def __init__(self, fs):
        self.fs = fs
        self.faults = {}
        self.calls = {}
        self._locked_left = {}

    def _count(self, item, what):
        c = self.calls.setdefault(item["plan_ref"], {"preflight": 0, "step": 0, "verify": 0})
        c[what] += 1

    @staticmethod
    def _params(item):
        p = item["params"]
        return json.loads(p) if isinstance(p, str) else p

    def preflight(self, item):
        from Phase4.jobs import Preflight
        self._count(item, "preflight")
        f = self.faults.get(item["plan_ref"], {})
        if "preflight" in f:
            return Preflight(refusal=f["preflight"], detail="injected preflight refusal %s" % f["preflight"])
        p = self._params(item)
        if p["op"] == "rename":
            ident = self.fs.identity(p["src"])
            if ident is None:
                return Preflight(refusal="STALE", detail="the source is gone")
            if self.fs.exists(p["dst"]):
                return Preflight(refusal="CONFLICT", detail="the destination exists")
            if self.fs.parent_key(self.fs.key(p["dst"])) not in self.fs.dirs:
                return Preflight(refusal="CONFLICT", detail="the destination folder is missing")
            return Preflight(before={"src": p["src"], "dst": p["dst"], "identity": ident}, recovery_token="undo:" + p["src"])
        if p["op"] == "create_folder":
            if self.fs.exists(p["path"]):
                return Preflight(refusal="CONFLICT", detail="the folder exists")
            if self.fs.parent_key(self.fs.key(p["path"])) not in self.fs.dirs:
                return Preflight(refusal="CONFLICT", detail="the parent folder is missing")
            return Preflight(before={"path": p["path"]}, recovery_token="remove-if-empty:" + p["path"])
        return Preflight(refusal="UNSUPPORTED", detail="unknown fake op %r" % p["op"])

    def step(self, item):
        from Phase4.jobs import StepResult
        self._count(item, "step")
        ref = item["plan_ref"]
        f = self.faults.get(ref, {})
        if "locked" in f:
            left = self._locked_left.setdefault(ref, f["locked"])
            if left > 0:
                self._locked_left[ref] = left - 1
                return StepResult("locked", "ERROR_SHARING_VIOLATION", 32)
        if "raise" in f:
            raise RuntimeError(f["raise"])
        if f.get("silent_no_op"):
            return StepResult("ok", "fake: reported success", 0)
        p = self._params(item)
        try:
            if p["op"] == "rename":
                self.fs.rename_no_overwrite(p["src"], p["dst"])
                if f.get("wrong_identity"):
                    e = self.fs.files[self.fs.key(p["dst"])]
                    e["id"] += 7777
                    self.fs.save()
            elif p["op"] == "create_folder":
                self.fs.create_folder(p["path"])
        except FileExistsError:
            return StepResult("error", "ERROR_ALREADY_EXISTS", 183)
        except FileNotFoundError:
            return StepResult("error", "ERROR_FILE_NOT_FOUND", 2)
        return StepResult("ok", "fake: ok", 0)

    def verify(self, item, before, result):
        from Phase4.jobs import Verification
        self._count(item, "verify")
        p = self._params(item)
        if p["op"] == "rename":
            src_there, dst = self.fs.exists(p["src"]), self.fs.identity(p["dst"])
            if result.status == "ok":
                if dst is not None and not src_there:
                    if dst["id"] != before["identity"]["id"]:
                        return Verification(False, "APPLIED_UNVERIFIED", "the file at the destination is not the one that was moved", dst)
                    return Verification(True, "APPLIED_VERIFIED", "same identity at the destination, source gone", dst,
                                        {"op": "rename", "from": p["src"], "to": p["dst"], "identity": dst})
                if src_there and dst is None:
                    return Verification(False, "RESULT_NOT_ON_DISK", "the step reported success but the source is still there")
                return Verification(False, "APPLIED_UNVERIFIED", "the disk shows neither expected shape")
            # a failed or refused step: the disk must be as it was
            if src_there and dst is None:
                return Verification(True, "NOT_APPLIED", "nothing changed on disk", None)
            return Verification(False, "UNKNOWN", "the step failed but the disk changed")
        if p["op"] == "create_folder":
            there = self.fs.is_dir(p["path"])
            if result.status == "ok":
                if there:
                    return Verification(True, "APPLIED_VERIFIED", "the folder exists and is empty" if not self.fs.children(p["path"]) else "folder exists",
                                        {"path": p["path"]}, {"op": "create_folder", "path": p["path"]})
                return Verification(False, "RESULT_NOT_ON_DISK", "the step reported success but there is no folder")
            if not there:
                return Verification(True, "NOT_APPLIED", "nothing changed on disk")
            return Verification(False, "UNKNOWN", "the step failed but the folder exists")
        return Verification(False, "APPLIED_UNVERIFIED", "unknown op")


def fake_job_items(fs, n, folder="in", dest="out"):
    """Make `n` files in `folder` of the fake FS and return n rename ItemSpecs (kind 'fake') to `dest`."""
    from Phase4.jobs import ItemSpec
    if not fs.is_dir(folder):
        fs.add_folder(folder)
    if not fs.is_dir(dest):
        fs.add_folder(dest)
    items = []
    for i in range(1, n + 1):
        name = "f%04d.dat" % i
        fs.add_file(folder + "/" + name, size=10 + i)
        items.append(ItemSpec("fake", "plan-%04d" % i, {"op": "rename", "src": folder + "/" + name, "dst": dest + "/" + name}))
    return items


# ---------------------------------------------------------------------------------------------------
# B21 units 5-6 (TEST-ONLY): attaching to a sandbox from a worker, the fake projector, the fake observer.
# ---------------------------------------------------------------------------------------------------
def attach_sandbox(root):
    """A worker process uses the sandbox its parent made. The root is proved to be a fo_p4_b21_ folder directly under
    %TEMP% before anything is written; the worker never removes it (the parent's Sandbox does)."""
    sb = Sandbox.__new__(Sandbox)
    base = os.path.realpath(tempfile.gettempdir())
    sb.root = os.path.realpath(os.fspath(root))
    if not os.path.basename(sb.root).startswith(SANDBOX_PREFIX) or _norm(os.path.dirname(sb.root)) != _norm(base):
        raise SandboxError("not a sandbox root: %s" % root)
    if not os.path.isdir(sb.root):
        raise SandboxError("the sandbox root does not exist: %s" % root)
    sb._closed = True              # cleanup() never removes it from here
    sb._closers = []
    return sb


def in_a_sandbox(path):
    """True when `path` is inside some fo_p4_b21_ folder directly under %TEMP%."""
    base = _norm(os.path.realpath(tempfile.gettempdir()))
    p = _norm(os.path.realpath(os.fspath(path))) if path else ""
    if not p.startswith(base + os.sep):
        return False
    first = p[len(base) + 1:].split(os.sep, 1)[0]
    return first.startswith(SANDBOX_PREFIX) and len(p) > len(base) + 1 + len(first)


class FakeProjector:
    """TEST-ONLY. The only projector B21 registers (handoff unit 5): it writes the sandbox-only table
    `p4_fake_projection` of a SANDBOX catalog, one row per action_id. It refuses a catalog that is not a file inside a
    fo_p4_b21_ sandbox. Idempotent: applying an event twice changes nothing; a different row already there under the
    same action_id is a ProjectionConflict and is never overwritten."""

    TABLE = "p4_fake_projection"
    DDL = ("CREATE TABLE IF NOT EXISTS p4_fake_projection (action_id TEXT PRIMARY KEY, event_id INTEGER NOT NULL, "
           "item_kind TEXT NOT NULL, payload TEXT NOT NULL, projected_utc TEXT NOT NULL)")

    def __init__(self):
        self.applied = []           # every apply() call, by action_id (a test counts them)

    @staticmethod
    def guard(cat):
        files = [r[2] for r in cat.execute("PRAGMA database_list").fetchall() if r[1] == "main"]
        if not files or not files[0] or not in_a_sandbox(files[0]):
            raise SandboxError("the fake projector writes only a sandbox catalog, not %r" % (files[0] if files else None,))

    @classmethod
    def _exists(cls, cat):
        return cat.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (cls.TABLE,)).fetchone() is not None

    def apply(self, cat, ev):
        from Phase4.projection import ProjectionConflict
        self.guard(cat)
        self.applied.append(ev["action_id"])
        cat.execute("BEGIN IMMEDIATE")
        try:
            cat.execute(self.DDL)
            row = cat.execute("SELECT event_id, payload FROM p4_fake_projection WHERE action_id = ?", (ev["action_id"],)).fetchone()
            if row is None:
                from Phase4.journal import utc_now
                cat.execute("INSERT INTO p4_fake_projection (action_id, event_id, item_kind, payload, projected_utc) "
                            "VALUES (?, ?, ?, ?, ?)", (ev["action_id"], ev["event_id"], ev["item_kind"],
                                                        ev["projection_payload"], utc_now()))
            elif (row[0], row[1]) != (ev["event_id"], ev["projection_payload"]):
                raise ProjectionConflict("the catalog holds another row for action %s; never overwritten" % ev["action_id"])
            cat.execute("COMMIT")
        except BaseException:
            try:
                cat.execute("ROLLBACK")
            except Exception:                                       # noqa: BLE001
                pass
            raise

    def observe(self, cat, ev):
        if not self._exists(cat):
            return "absent"
        row = cat.execute("SELECT event_id, payload FROM p4_fake_projection WHERE action_id = ?", (ev["action_id"],)).fetchone()
        if row is None:
            return "absent"
        return "match" if (row[0], row[1]) == (ev["event_id"], ev["projection_payload"]) else "differ"

    def action_ids(self, cat):
        if not self._exists(cat):
            return set()
        return {r[0] for r in cat.execute("SELECT action_id FROM p4_fake_projection")}


class FakeObserver:
    """TEST-ONLY. What the fake disk shows NOW, for reconcile.py. It reloads the fake filesystem's JSON and never saves
    it. Table A: the identities at the source and destination. Table F: the folder and its parent. Table C: the
    'current metadata' of a path, from `meta` (B21 has no metadata action; table C is reached by construction)."""

    def __init__(self, fs, meta=None):
        self.fs = fs
        self.meta = dict(meta or {})
        self.calls = 0

    def observe(self, item, table, params, before):
        self.calls += 1
        fs = self.fs
        fs.load()
        if table == "A":
            src, dst = params.get("src") or params.get("from"), params.get("dst") or params.get("to")
            si, di = fs.identity(src), fs.identity(dst)
            same_path = fs.key(src) == fs.key(dst)
            return {"src": si, "dst": di, "same_path": same_path,
                    "same_object": bool(si and di and si["id"] == di["id"])}
        if table == "F":
            p = params["path"]
            if not fs.is_dir(p):
                folder = "absent"
            else:
                folder = "not_empty" if fs.children(p) else "empty"
            return {"folder": folder, "parent_present": fs.parent_key(fs.key(p)) in fs.dirs}
        if table == "C":
            return {"current": self.meta.get(params.get("path"))}
        raise ValueError("no fake observation for table %r" % table)
