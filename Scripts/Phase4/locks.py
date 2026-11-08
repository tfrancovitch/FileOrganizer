r"""The project lock and the root-level lock (B21; R4.3 3 and 5.5; gate 1 G18).

PROJECT LOCK. `<project_uid>.lock` in the journal-and-lock folder, held with msvcrt.locking(fd, LK_NBLCK, 1)
on byte 0 for the life of any execute, recovery or undo run. Owner information (Windows user, machine name,
PID, start time, operation, heartbeat, run id) is a JSON block from byte 1 onward and is mirrored in the
journal's `owner` row. The owner's run_id is a FENCING TOKEN, checked inside every C1, C2, C3 and C4
transaction (journal.Tx). A busy lock is retried for 5-10 s (the OS may release a dead holder's lock late)
before LockBusy is raised. No named mutex, no BEGIN EXCLUSIVE held across a run, no lease alone: the OS lock is
the authority; the owner block and the owner row are information (and the token).

The handle is opened with O_NOINHERIT (probe L4: even an inherited handle does not keep a byte lock alive after
its owner dies -- NT byte-range locks belong to the process that took them -- but the product does not rely on
that). Leftover lock files are harmless: only a HELD lock means anything.

ROOT-LEVEL LOCK. One file per source root in a folder shared by every project on the machine, named from the
root directory's key (st_dev, st_ino) as os.stat reports it (on Windows: the volume serial and the file ID --
the key synthesis 12 names; B22 may switch to FileIdInfo read by handle). The file records project_uid, the
root's display path and the key of every ancestor directory. To acquire: take a short-held `registry.lock` in
the same folder; refuse if any other HELD root lock (another project's) has a chain that contains this root's
key, or this root's chain contains its key (nested roots, either way); then lock the root's own file. Two
projects aimed at the same root: the second is refused with the holder's information.

Nothing here decides what to do with a file.
"""
from __future__ import annotations

import getpass
import json
import msvcrt
import os
import socket
import time
import uuid
from datetime import datetime, timezone

#: The project lock is retried for this long before "busy" is reported (R4.3 5.5: roughly 5-10 s). Probe L1/L3
#: measured the OS releasing a killed holder's lock in milliseconds, so this is generous.
DEFAULT_RETRY_S = 7.0
POLL_S = 0.05

OWNER_OFFSET = 1          # bytes 1.. hold the owner block; byte 0 is the locked byte
OWNER_BYTES = 768         # fixed-size block, space padded, so a rewrite never changes the file length
ROOT_BLOCK_BYTES = 8192   # a root lock also lists the key of every ancestor directory


class LockError(Exception):
    code = "lock_error"


class LockBusy(LockError):
    """The lock is held by someone else. `holder` is holder_info() of the lock (or None)."""
    code = "lock_busy"

    def __init__(self, message, holder=None):
        super().__init__(message)
        self.holder = holder


class RootLockBusy(LockBusy):
    code = "root_lock_busy"


class RootOverlap(LockBusy):
    """Another project holds a root that is the same as, contains, or is contained in this one."""
    code = "root_overlap"


class RootUnidentifiable(LockError):
    """A root or one of its ancestors could not be identified (os.stat failed). Unknown blocks."""
    code = "root_unidentifiable"


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_utc(text):
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------------
# The byte lock itself
# ---------------------------------------------------------------------------------------------------
def _open_fd(path):
    return os.open(path, os.O_RDWR | os.O_CREAT | os.O_BINARY | getattr(os, "O_NOINHERIT", 0))


def _try_lock(fd):
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock(fd):
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _write_block(fd, payload, size=OWNER_BYTES):
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(raw) > size - 1:
        raise LockError("the owner block does not fit in %d bytes (%d)" % (size, len(raw)))
    os.lseek(fd, OWNER_OFFSET, 0)
    os.write(fd, raw.ljust(size, b" "))


def _read_block(path, size=ROOT_BLOCK_BYTES):
    """The owner block of a lock file, or None. Readable while byte 0 is locked by someone else."""
    for _attempt in range(3):
        try:
            with open(path, "rb") as fh:
                fh.seek(OWNER_OFFSET)
                data = fh.read(size)
        except OSError:
            return None
        text = data.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            time.sleep(0.01)                  # a rewrite was in progress; try again
    return None


def _is_held(path):
    """True if some process holds byte 0 of `path` (we try, and let go at once)."""
    if not os.path.isfile(path):
        return False
    fd = _open_fd(path)
    try:
        if _try_lock(fd):
            _unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


def holder_info(lock_path):
    """What a second instance shows: who, which machine, since when, which operation, heartbeat age.

    Returns None when there is no lock file. `held` says whether the OS lock is held right now; when it is not,
    the rest describes the PREVIOUS holder (stale) and `stale` is True.
    """
    if not os.path.isfile(lock_path):
        return None
    held = _is_held(lock_path)
    block = _read_block(lock_path) or {}
    hb = _parse_utc(block.get("heartbeat_utc"))
    age = None if hb is None else max(0.0, (datetime.now(timezone.utc) - hb).total_seconds())
    return {
        "held": held, "stale": not held,
        "windows_user": block.get("windows_user"), "machine_name": block.get("machine_name"),
        "pid": block.get("pid"), "since_utc": block.get("started_utc"), "operation": block.get("operation"),
        "heartbeat_utc": block.get("heartbeat_utc"), "heartbeat_age_s": age, "run_id": block.get("run_id"),
        "project_uid": block.get("project_uid"), "root": block.get("display_path"),
    }


def describe(info):
    """One plain sentence about a holder_info() dict, for a busy message or a banner."""
    if not info:
        return "another instance"
    who = info.get("windows_user") or "someone"
    where = info.get("machine_name") or "this computer"
    what = info.get("operation") or "an operation"
    since = info.get("since_utc") or "an unknown time"
    age = info.get("heartbeat_age_s")
    beat = "" if age is None else " (last sign of life %d s ago)" % int(age)
    return "%s on %s is running %s, since %s%s" % (who, where, what, since, beat)


# ---------------------------------------------------------------------------------------------------
# The project lock
# ---------------------------------------------------------------------------------------------------
class ProjectLock:
    """Hold with `with ProjectLock(path, "execute") as lk:` or acquire()/release()."""

    def __init__(self, lock_path, operation, project_uid=None, retry_seconds=DEFAULT_RETRY_S,
                 clock=time.monotonic, sleep=time.sleep):
        self.path = os.fspath(lock_path)
        self.operation = operation
        self.project_uid = project_uid
        self.retry_seconds = float(retry_seconds)
        self._clock, self._sleep = clock, sleep
        self.run_id = None
        self.fd = None
        self.started_utc = None

    @property
    def held(self):
        return self.fd is not None

    def _block(self):
        return {
            "run_id": self.run_id, "windows_user": _user(), "machine_name": socket.gethostname(),
            "pid": os.getpid(), "started_utc": self.started_utc, "operation": self.operation,
            "heartbeat_utc": utc_now(), "project_uid": self.project_uid,
        }

    def acquire(self, journal=None):
        """Take the lock, retrying until `retry_seconds` have passed, then raise LockBusy(holder info).

        With `journal` the owner row is written too (the run_id is the fencing token).
        """
        if self.fd is not None:
            raise LockError("this lock object already holds the lock")
        fd = _open_fd(self.path)
        deadline = self._clock() + self.retry_seconds
        while True:
            if _try_lock(fd):
                break
            if self._clock() >= deadline:
                os.close(fd)
                info = holder_info(self.path)
                raise LockBusy("The project is in use: %s." % describe(info), holder=info)
            self._sleep(POLL_S)
        self.fd = fd
        self.run_id = uuid.uuid4().hex
        self.started_utc = utc_now()
        _write_block(fd, self._block())
        if journal is not None:
            try:
                journal.set_owner(self.run_id, _user(), socket.gethostname(), os.getpid(), self.operation)
            except BaseException:
                self.release()
                raise
        return self

    def owner_block(self):
        """The owner information this lock writes in its bytes 1 onward (who, machine, PID, since, operation,
        heartbeat, run_id). projection.open_session mirrors it into the journal's owner row after the journal is
        opened under the lock."""
        if self.fd is None:
            raise LockError("owner information of a lock that is not held")
        return self._block()

    def heartbeat(self, journal=None):
        if self.fd is None:
            raise LockError("heartbeat on a lock that is not held")
        _write_block(self.fd, self._block())
        if journal is not None:
            journal.heartbeat(self.run_id)

    def release(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


def _user():
    try:
        return getpass.getuser()
    except Exception:                                               # noqa: BLE001
        return os.environ.get("USERNAME", "unknown")


# ---------------------------------------------------------------------------------------------------
# The root-level lock
# ---------------------------------------------------------------------------------------------------
def dir_key(path):
    """(st_dev, st_ino) of a directory as one string, via os.stat. Raises RootUnidentifiable."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise RootUnidentifiable("could not identify %s (%s); unknown blocks" % (path, exc))
    if not st.st_ino:
        raise RootUnidentifiable("%s has no file id on this volume; unknown blocks" % path)
    return "%x-%x" % (st.st_dev, st.st_ino)


def ancestor_keys(real_path):
    """Keys of every ancestor directory of `real_path`, nearest first, up to the drive root."""
    keys = []
    cur = os.path.dirname(real_path.rstrip("\\/"))
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        keys.append(dir_key(cur))
        parent = os.path.dirname(cur.rstrip("\\/")) if len(cur.rstrip("\\/")) > 3 else cur
        if parent == cur or len(cur.rstrip("\\/")) <= 3:
            break
        cur = parent
    return keys


class RootLock:
    """A held root-level lock. Release with release()."""

    def __init__(self, path, fd, info):
        self.path, self.fd, self.info = path, fd, info

    @property
    def key(self):
        return self.info["key"]

    def release(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)


class RootLockSet:
    """A project's held root locks; releases them together."""

    def __init__(self, locks):
        self.locks = list(locks)

    def release(self):
        for lk in reversed(self.locks):
            lk.release()
        self.locks = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class RootLocks:
    """Acquire root-level locks for one project. `lock_dir` is shared by every project on the machine."""

    REGISTRY_RETRY_S = 5.0

    def __init__(self, lock_dir, project_uid, operation="execute", clock=time.monotonic, sleep=time.sleep):
        self.dir = os.fspath(lock_dir)
        self.project_uid = project_uid
        self.operation = operation
        self._clock, self._sleep = clock, sleep

    def _registry(self):
        path = os.path.join(self.dir, "registry.lock")
        fd = _open_fd(path)
        deadline = self._clock() + self.REGISTRY_RETRY_S
        while not _try_lock(fd):
            if self._clock() >= deadline:
                os.close(fd)
                raise RootLockBusy("The root-lock registry is busy; try again in a moment.", holder=None)
            self._sleep(0.01)
        return fd

    def _held_others(self, own_path):
        """Info of every root lock that is HELD right now, other than `own_path`."""
        out = []
        for name in sorted(os.listdir(self.dir)):
            if not (name.startswith("root_") and name.endswith(".lock")):
                continue
            path = os.path.join(self.dir, name)
            if os.path.normcase(path) == os.path.normcase(own_path):
                continue
            if _is_held(path):
                block = _read_block(path)
                if block:
                    block["_path"] = path
                    out.append(block)
        return out

    def acquire(self, root_path):
        """Lock one source root. Raises RootOverlap / RootLockBusy (with the holder's information) or
        RootUnidentifiable."""
        os.makedirs(self.dir, exist_ok=True)
        real = os.path.realpath(root_path)
        key = dir_key(real)
        chain = ancestor_keys(real)
        own_path = os.path.join(self.dir, "root_%s.lock" % key)
        rfd = self._registry()
        try:
            for other in self._held_others(own_path):
                if other.get("project_uid") == self.project_uid:
                    continue                                        # one project's own roots never exclude each other
                if other.get("key") in chain or key in (other.get("chain") or []):
                    raise RootOverlap(
                        "The folder %s overlaps %s, which is in use by another project: %s."
                        % (root_path, other.get("display_path"), describe({
                            "windows_user": other.get("windows_user"), "machine_name": other.get("machine_name"),
                            "operation": other.get("operation"), "since_utc": other.get("started_utc")})),
                        holder=other)
            fd = _open_fd(own_path)
            if not _try_lock(fd):
                os.close(fd)
                other = _read_block(own_path)
                raise RootLockBusy("The folder %s is in use by another project or instance: %s."
                                   % (root_path, describe(other)), holder=other)
            info = {"key": key, "chain": chain, "project_uid": self.project_uid, "display_path": os.fspath(root_path),
                    "windows_user": _user(), "machine_name": socket.gethostname(), "pid": os.getpid(),
                    "started_utc": utc_now(), "operation": self.operation, "heartbeat_utc": utc_now()}
            try:
                _write_block(fd, info, ROOT_BLOCK_BYTES)
            except BaseException:
                _unlock(fd)
                os.close(fd)
                raise
            return RootLock(own_path, fd, info)
        finally:
            _unlock(rfd)
            os.close(rfd)

    def acquire_all(self, roots):
        """Lock several roots (deduplicated by key, in key order). All or none."""
        keyed = {}
        for r in roots:
            keyed.setdefault(dir_key(os.path.realpath(r)), r)
        got = []
        try:
            for key in sorted(keyed):
                got.append(self.acquire(keyed[key]))
        except BaseException:
            for lk in reversed(got):
                lk.release()
            raise
        return RootLockSet(got)
