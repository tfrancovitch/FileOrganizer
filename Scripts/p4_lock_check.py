r"""B21 -- the project lock and the root-level lock: probes (unit 1) and tests (unit 3).

    py -3 Scripts\p4_lock_check.py

Sections
  1. Unit 1, the R4.3 section 5.5 probes that run unattended, on a bare byte-lock written in this file
     (the product's locks.py comes in unit 3 and is tested in section 2):
       L1  holder killed with `taskkill /F`        -> the delay until a second process can acquire
       L3  holder crashes hard (os._exit; an access violation) -> the same delay
       L4  holder started a child, then was killed -> the child must not keep the lock
       L8  1,000 racing acquisitions from two processes -> never two holders
       L11 holder suspended past its heartbeat       -> still held, heartbeat visibly stale
       L12 another process opens the lock file       -> the lock is unaffected
     Not run here (a person, a second Windows account or a VM is needed): L2, L5, L6, L7.
     NEVER run, by instruction: L9 (OneDrive), L10 (SMB). Unit 2 tests the refusal of those paths with an
     injected classifier.
  2. Unit 3, locks.py (added with unit 3).

Every test works under its own tempfile.mkdtemp(prefix="fo_p4_b21_") root. %LOCALAPPDATA%\FileOrganizer\ is
recorded before and checked after: it must not change.
"""
from __future__ import annotations

import msvcrt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                                   # noqa: BLE001
    pass

import p4_fakes as F                                                # noqa: E402

RESULTS = []
PROBES = {}                       # probe id -> dict of measured numbers (printed at the end)
SELF = Path(__file__).resolve()

#: Unit 3's retry window is "5-10 s" (handoff); a release later than this is a stop condition (section 8.1).
STOP_RELEASE_LIMIT_S = 10.0

OWNER_OFFSET = 1                  # owner text starts at byte 1 (byte 0 is the locked byte)
SLOT_OFFSET = 128                 # L8 occupancy slot (8 bytes)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ===================================================================================================
# A bare byte lock, for the probes only
# ===================================================================================================
def open_lock_fd(path, inheritable=False):
    flags = os.O_RDWR | os.O_CREAT | os.O_BINARY
    if not inheritable:
        flags |= getattr(os, "O_NOINHERIT", 0)
    fd = os.open(path, flags)
    if inheritable:
        os.set_inheritable(fd, True)
    return fd


def try_lock_fd(fd) -> bool:
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def try_acquire(path):
    """Non-blocking acquire; returns an open fd holding the lock, or None."""
    fd = open_lock_fd(path)
    if try_lock_fd(fd):
        return fd
    os.close(fd)
    return None


def release_fd(fd):
    try:
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def write_owner(fd, text):
    data = text.encode("ascii", "replace")[:100].ljust(100, b" ")
    os.lseek(fd, OWNER_OFFSET, 0)
    os.write(fd, data)


def read_owner(path):
    with open(path, "rb") as fh:                    # share read+write: reading bytes 1+ is allowed
        fh.seek(OWNER_OFFSET)
        return fh.read(100).decode("ascii", "replace").strip()


def heartbeat_age(path):
    """Seconds since the holder last wrote its heartbeat, or None."""
    txt = read_owner(path)
    for part in txt.split():
        if part.startswith("hb="):
            try:
                return time.time() - float(part[3:])
            except ValueError:
                return None
    return None


def wait_acquire(path, limit_s):
    """Poll (1 ms) for the lock. Returns (fd or None, seconds waited)."""
    fd = open_lock_fd(path)
    t0 = time.perf_counter()
    while True:
        if try_lock_fd(fd):
            return fd, time.perf_counter() - t0
        if time.perf_counter() - t0 > limit_s:
            os.close(fd)
            return None, time.perf_counter() - t0
        time.sleep(0.001)


# ===================================================================================================
# Worker processes (this file run with --worker ...)
# ===================================================================================================
#: The child of probe L4: if the numeric handle it was given resolves to the lock file in ITS handle table,
#: it writes one byte through it (offset 200) -- proof that the handle was inherited.
_CHILD_CODE = (
    "import ctypes, msvcrt, os, sys, time\n"
    "h = int(sys.argv[1]); want = sys.argv[2].lower()\n"
    "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "k.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]\n"
    "buf = ctypes.create_unicode_buffer(1024)\n"
    "n = k.GetFinalPathNameByHandleW(ctypes.c_void_p(h), buf, 1024, 0)\n"
    "if n and buf.value.lower().endswith(want):\n"
    "    fd = msvcrt.open_osfhandle(h, os.O_RDWR)\n"
    "    os.lseek(fd, 200, 0); os.write(fd, b'X')\n"
    "time.sleep(120)\n"
)


def worker_hold(lockpath, inherit, heartbeat):
    fd = open_lock_fd(lockpath, inheritable=inherit)
    if not try_lock_fd(fd):
        print("BUSY", flush=True)
        return
    pid = os.getpid()

    def beat():
        while True:
            try:
                write_owner(fd, f"pid={pid} hb={time.time():.3f}")
            except OSError:
                pass
            time.sleep(heartbeat)

    write_owner(fd, f"pid={pid} hb={time.time():.3f}")
    if heartbeat > 0:
        threading.Thread(target=beat, daemon=True).start()
    print(f"LOCKED {pid}", flush=True)
    child = None
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "exit":
            os._exit(3)
        elif cmd == "crash":
            F.hard_crash_access_violation()
        elif cmd == "spawnchild":
            h = msvcrt.get_osfhandle(fd)
            child = subprocess.Popen([sys.executable, "-c", _CHILD_CODE, str(h), os.path.basename(lockpath)],
                                     close_fds=False, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"CHILD {child.pid}", flush=True)
        elif cmd == "release":
            release_fd(fd)
            print("RELEASED", flush=True)
            return
        elif cmd == "quit":
            return


def worker_race(lockpath, n, start_at, tag):
    fd = open_lock_fd(lockpath)
    while time.time() < float(start_at):
        pass
    got = violations = busy = 0
    mine = int(tag).to_bytes(8, "little")
    zero = bytes(8)
    i = 0
    while got < int(n):
        if not try_lock_fd(fd):
            busy += 1
            continue
        os.lseek(fd, SLOT_OFFSET, 0)
        seen = os.read(fd, 8)
        if seen not in (b"", zero):                  # somebody else's tag: two holders
            violations += 1
        os.lseek(fd, SLOT_OFFSET, 0)
        os.write(fd, mine)
        i += 1
        if i % 3 == 0:
            time.sleep(0.001)                        # hold across a scheduling slice now and then
        os.lseek(fd, SLOT_OFFSET, 0)
        again = os.read(fd, 8)
        if again != mine:
            violations += 1
        os.lseek(fd, SLOT_OFFSET, 0)
        os.write(fd, zero)
        os.lseek(fd, 0, 0)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        got += 1
    print(f"DONE {got} {violations} {busy}", flush=True)


def worker_reader(lockpath):
    out = []
    with open(lockpath, "rb") as fh:                 # what an indexer or a virus scanner does
        fh.seek(OWNER_OFFSET)
        data = fh.read(100)
        out.append("owner_ok" if data.strip() else "owner_empty")
        fh.seek(0)
        try:
            fh.read(1)
            out.append("byte0_readable")
        except OSError:
            out.append("byte0_denied")
        rw = open(lockpath, "r+b")                   # and keeps a read/write handle open
        print("READY " + ",".join(out), flush=True)
        for line in sys.stdin:
            if line.strip() == "quit":
                break
        rw.close()


def worker_main(argv):
    mode = argv[0]
    if mode == "hold":
        worker_hold(argv[1], "--inherit" in argv, float(argv[2]) if len(argv) > 2 and not argv[2].startswith("--") else 0.0)
    elif mode == "race":
        worker_race(argv[1], argv[2], argv[3], argv[4])
    elif mode == "reader":
        worker_reader(argv[1])
    elif mode == "plock":
        worker_plock(argv[1], argv[2], argv[3], argv[4], argv[5])
    elif mode == "rtry":
        worker_rtry(argv[1], argv[2], argv[3])
    else:
        raise SystemExit("unknown worker mode " + mode)


# ===================================================================================================
# Probes
# ===================================================================================================
def start_holder(sb, name, inherit=False, heartbeat=0.0):
    lockpath = sb.path(name)
    args = ["--worker", "hold", lockpath, heartbeat]
    if inherit:
        args.append("--inherit")
    proc = F.spawn(SELF, *args)
    line = F.read_line(proc, 20)
    if not line or not line.startswith("LOCKED"):
        F.terminate_process(proc)
        raise RuntimeError("holder did not lock: %r" % (line,))
    return proc, lockpath, int(line.split()[1])


def kill_and_measure(sb, how, runs):
    """Hold in a child, make it die in the way `how` says, and time the second acquire."""
    delays = []
    ok_all = True
    for k in range(runs):
        proc, lockpath, pid = start_holder(sb, f"{how}_{k}.lock")
        # While the holder lives the lock is busy.
        fd_busy = try_acquire(lockpath)
        if fd_busy is not None:
            release_fd(fd_busy)
            ok_all = False
        fd = open_lock_fd(lockpath)
        if how == "taskkill":
            t0 = F.taskkill_force(pid)
        elif how == "os_exit":
            F.send_line(proc, "exit")
            t0 = time.perf_counter()
        elif how == "access_violation":
            F.send_line(proc, "crash")
            t0 = time.perf_counter()
        else:
            raise ValueError(how)
        t_limit = STOP_RELEASE_LIMIT_S + 5
        while True:
            if try_lock_fd(fd):
                delays.append((time.perf_counter() - t0) * 1000.0)
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                break
            if time.perf_counter() - t0 > t_limit:
                delays.append(float("inf"))
                ok_all = False
                break
            time.sleep(0.001)
        os.close(fd)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            F.terminate_process(proc)
        code = proc.returncode
        if how == "access_violation" and code not in (3221225477, -1073741819):
            ok_all = False
            print(f"    note: access-violation exit code was {code!r}, not 0xC0000005")
        PROBES.setdefault(how + "_exit_codes", set()).add(code)
    return delays, ok_all


def probe_l1_l3():
    section("L1 / L3 -- the OS frees the byte lock when the holder dies")
    with F.Sandbox() as sb:
        for label, how, runs in (("L1 taskkill /F", "taskkill", 10),
                                 ("L3 os._exit(3)", "os_exit", 10),
                                 ("L3 access violation", "access_violation", 5)):
            delays, ok = kill_and_measure(sb, how, runs)
            finite = [d for d in delays if d != float("inf")]
            if finite:
                med, mx = F.median(finite), max(finite)
            else:
                med = mx = float("inf")
            PROBES[how] = {"runs": runs, "median_ms": med, "max_ms": mx, "all_released": len(finite) == runs}
            print(f"    {label}: {len(finite)}/{runs} released; median {med:.1f} ms, max {mx:.1f} ms; exit codes {sorted(PROBES.get(how + '_exit_codes', []))}")
            check(f"{label}: busy while alive, free after death, within {STOP_RELEASE_LIMIT_S:.0f} s (all {runs} runs)",
                  ok and len(finite) == runs and mx < STOP_RELEASE_LIMIT_S * 1000,
                  f"released {len(finite)}/{runs}, max {mx:.1f} ms")


def read_marker(path):
    with open(path, "rb") as fh:
        fh.seek(200)
        return fh.read(1)


def probe_l4():
    section("L4 -- a child of the killed holder does not keep the lock")
    with F.Sandbox() as sb:
        # The product case: the lock handle is not inheritable (O_NOINHERIT).
        proc, lockpath, pid = start_holder(sb, "l4.lock", inherit=False)
        F.send_line(proc, "spawnchild")
        line = F.read_line(proc, 20) or ""
        child = int(line.split()[1]) if line.startswith("CHILD") else 0
        time.sleep(1.0)                                             # let the child run its check
        check("L4: the holder started a child that is alive", child and F.pid_alive(child))
        check("L4: the child did NOT inherit the non-inheritable lock handle (no byte written through it)",
              read_marker(lockpath) != b"X", repr(read_marker(lockpath)))
        F.taskkill_force(pid)                                       # kill the holder only, not its tree
        fd, waited = wait_acquire(lockpath, 12)
        got = fd is not None
        if got:
            release_fd(fd)
        PROBES["L4"] = {"child_alive_after_kill": bool(child and F.pid_alive(child)), "acquired_ms": round(waited * 1000, 1)}
        check("L4: lock acquired by a second process while the child still lives",
              got and child and F.pid_alive(child), f"acquired={got}, waited {waited * 1000:.0f} ms")
        if child:
            F.taskkill_force(child)

        # Control: the same sequence with an inheritable handle that the child demonstrably holds. It shows
        # what the probe can see, and it records an NT fact the design can lean on: byte-range locks belong
        # to the process that took them, so a surviving child with a duplicate handle does not keep one.
        proc2, lockpath2, pid2 = start_holder(sb, "l4c.lock", inherit=True)
        F.send_line(proc2, "spawnchild")
        line = F.read_line(proc2, 20) or ""
        child2 = int(line.split()[1]) if line.startswith("CHILD") else 0
        time.sleep(1.0)
        inherited = read_marker(lockpath2) == b"X"
        check("L4 control: with an inheritable handle the child really holds a handle to the lock file (it wrote through it)",
              inherited and child2 and F.pid_alive(child2))
        F.taskkill_force(pid2)
        fd2, waited2 = wait_acquire(lockpath2, 12)
        released = fd2 is not None
        if fd2 is not None:
            release_fd(fd2)
        PROBES["L4_control_inheritable"] = {"child_inherited_handle": inherited, "lock_released_with_child_alive": released,
                                            "child_alive": bool(child2 and F.pid_alive(child2)), "acquired_ms": round(waited2 * 1000, 1)}
        print(f"    L4 control (inheritable handle): child holds a handle = {inherited}; lock released after the holder died = {released}")
        check("L4 control: even then the lock is released when the holder dies (NT byte locks are owned by the locking process); "
              "O_NOINHERIT stays as belt and braces", released and child2 and F.pid_alive(child2))
        if child2:
            F.taskkill_force(child2)


def probe_l8():
    section("L8 -- 1,000 racing acquisitions from two processes never show two holders")
    with F.Sandbox() as sb:
        lockpath = sb.path("l8.lock")
        sb.write_bytes("l8.lock", bytes(256))
        start_at = time.time() + 1.0
        t0 = time.perf_counter()
        procs = [F.spawn(SELF, "--worker", "race", lockpath, 1000, f"{start_at:.3f}", tag, stdin=False)
                 for tag in (101, 202)]
        outs = [F.read_line(p, 120) for p in procs]
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                F.terminate_process(p)
        took = time.perf_counter() - t0
        parsed = []
        for o in outs:
            if o and o.startswith("DONE"):
                parsed.append([int(x) for x in o.split()[1:]])
        got = sum(p[0] for p in parsed)
        viol = sum(p[1] for p in parsed)
        busy = sum(p[2] for p in parsed)
        PROBES["L8"] = {"acquisitions": got, "two_holder_violations": viol, "contended_attempts": busy,
                        "seconds": round(took, 2)}
        print(f"    L8: {got} acquisitions, {viol} two-holder violations, {busy} refused attempts (contention), {took:.1f} s")
        check("L8: both processes finished their 1,000 acquisitions", len(parsed) == 2 and got == 2000, str(outs))
        check("L8: zero two-holder observations", viol == 0, f"{viol} violations")
        check("L8: the race was real (some attempts were refused as busy)", busy > 0, f"{busy} refused")


def probe_l11():
    section("L11 -- a frozen holder keeps the lock; its heartbeat goes visibly stale")
    with F.Sandbox() as sb:
        proc, lockpath, pid = start_holder(sb, "l11.lock", heartbeat=0.2)
        time.sleep(0.6)
        fresh = heartbeat_age(lockpath)
        check("L11: before the freeze the heartbeat is fresh", fresh is not None and fresh < 0.8, f"age {fresh}")
        suspended = F.suspend_process(pid)
        check("L11: the holder was suspended (NtSuspendProcess)", suspended)
        time.sleep(2.5)                                             # well past the 0.2 s heartbeat
        fd = try_acquire(lockpath)
        busy = fd is None
        if fd is not None:
            release_fd(fd)
        stale = heartbeat_age(lockpath)
        PROBES["L11"] = {"busy_while_frozen": busy, "heartbeat_age_s": None if stale is None else round(stale, 2)}
        print(f"    L11: busy while frozen = {busy}; heartbeat age {stale if stale is None else round(stale, 2)} s")
        check("L11: still held (second process refused) while frozen past the heartbeat", busy)
        check("L11: the owner heartbeat reads stale (> 2 s) to a second instance", stale is not None and stale > 2.0,
              f"age {stale}")
        F.resume_process(pid)
        time.sleep(0.8)
        again = heartbeat_age(lockpath)
        check("L11: after resume the heartbeat is fresh again and the lock is still held",
              again is not None and again < 0.8 and try_acquire(lockpath) is None, f"age {again}")
        F.taskkill_force(pid)
        fd, _ = wait_acquire(lockpath, 12)
        check("L11: freed after the holder is killed", fd is not None)
        if fd is not None:
            release_fd(fd)


def probe_l12():
    section("L12 -- another process opening the lock file does not disturb the lock")
    with F.Sandbox() as sb:
        proc, lockpath, pid = start_holder(sb, "l12.lock", heartbeat=0.2)
        time.sleep(0.4)
        reader = F.spawn(SELF, "--worker", "reader", lockpath)
        line = F.read_line(reader, 20) or ""
        PROBES["L12"] = {"reader": line}
        print(f"    L12: reader reported {line!r}")
        check("L12: the reader could read the owner bytes while byte 0 was locked", "owner_ok" in line, line)
        check("L12: byte 0 itself is denied to the reader (byte-range lock, not share mode)", "byte0_denied" in line, line)
        busy = try_acquire(lockpath) is None
        check("L12: lock still held while the reader keeps read/write handles open", busy)
        F.send_line(reader, "quit")
        try:
            reader.wait(timeout=10)
        except subprocess.TimeoutExpired:
            F.terminate_process(reader)
        busy2 = try_acquire(lockpath) is None
        check("L12: lock still held after the reader closed its handles", busy2)
        F.taskkill_force(pid)
        fd, _ = wait_acquire(lockpath, 12)
        check("L12: freed after the holder is killed", fd is not None)
        if fd is not None:
            release_fd(fd)


# ===================================================================================================
# Unit 3 -- locks.py
# ===================================================================================================
def worker_plock(lockpath, journalpath, project_uid, root_dir_arg, roots_arg):
    """A process that holds the project lock (owner mirrored in the journal) and root locks, until told to quit."""
    from Phase4 import journal as J
    from Phase4 import locks as L
    j = J.Journal.open(journalpath)
    lk = L.ProjectLock(lockpath, "execute", project_uid=project_uid, retry_seconds=0.5)
    try:
        lk.acquire(journal=j)
    except L.LockBusy:
        print("BUSY", flush=True)
        return
    held = None
    if roots_arg != "-":
        rl = L.RootLocks(root_dir_arg, project_uid)
        try:
            held = rl.acquire_all(roots_arg.split("|"))
        except L.LockError as exc:
            print("ROOTREFUSED " + exc.code, flush=True)
            return
    print(f"LOCKED {lk.run_id} {os.getpid()}", flush=True)
    for line in sys.stdin:
        if line.strip() == "quit":
            break


def worker_rtry(lock_dir, project_uid, root):
    from Phase4 import locks as L
    rl = L.RootLocks(lock_dir, project_uid)
    try:
        got = rl.acquire(root)
        print("ACQUIRED", flush=True)
        got.release()
    except L.LockError as exc:
        print("REFUSED " + exc.code, flush=True)


class FakeClock:
    """A clock whose sleep() advances it, so a 7-second retry window costs no real time."""

    def __init__(self):
        self.t = 0.0
        self.slept = 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s
        self.slept += s


def make_journal(sb, name, uid="proj-uid-00000001"):
    from Phase4 import journal as J
    folder = sb.mkdir(name)
    return J.Journal.create(os.path.join(folder, uid + ".journal.db"), uid, ("guid-test", "TESTBOX")), os.path.join(folder, uid + ".journal.db")


def test_project_lock():
    from Phase4 import journal as J
    from Phase4 import locks as L
    section("3a. locks.py -- the project lock")
    with F.Sandbox() as sb:
        lp = sb.path("p.lock")
        a = L.ProjectLock(lp, "execute", project_uid="proj-uid-00000001", retry_seconds=0.3).acquire()
        check("the lock handle is not inheritable (O_NOINHERIT)", not os.get_inheritable(a.fd))
        info = L.holder_info(lp)
        check("holder_info: held, with user, machine, pid, operation, a start time and a fresh heartbeat",
              info["held"] and info["pid"] == os.getpid() and info["operation"] == "execute" and info["windows_user"]
              and info["machine_name"] and info["since_utc"] and info["heartbeat_age_s"] is not None and info["heartbeat_age_s"] < 5
              and info["run_id"] == a.run_id, str(info))

        b = L.ProjectLock(lp, "execute", retry_seconds=0.3)
        t0 = time.perf_counter()
        ok, e = False, None
        try:
            b.acquire()
        except L.LockBusy as exc:
            ok, e = True, exc
        took = time.perf_counter() - t0
        check("a second holder is refused with LockBusy that carries the first holder's information",
              ok and e.holder and e.holder["pid"] == os.getpid() and "execute" in str(e) and e.holder["windows_user"] in str(e), str(e))
        check("the busy answer is NON-BLOCKING: it comes back at the end of the retry window, not after a blocking wait",
              0.25 <= took < 2.5, f"{took:.2f} s for a 0.3 s window")
        check("the second holder holds nothing after a refusal", not b.held)

        check("the default retry window is 5-10 s (R4.3 5.5)", 5.0 <= L.DEFAULT_RETRY_S <= 10.0, str(L.DEFAULT_RETRY_S))
        fc = FakeClock()
        c = L.ProjectLock(lp, "execute", clock=fc.clock, sleep=fc.sleep)
        try:
            c.acquire()
            got = False
        except L.LockBusy:
            got = True
        check("with the default window the lock keeps being retried for 5-10 s (fake clock) before it reports busy",
              got and 5.0 <= fc.slept <= 10.5, f"retried for {fc.slept:.2f} s")

        a.heartbeat()
        check("heartbeat rewrites the owner block (age resets)", L.holder_info(lp)["heartbeat_age_s"] < 5)
        a.release()
        info2 = L.holder_info(lp)
        check("after release the lock file stays (harmless) but holder_info says not held / stale", info2 and not info2["held"] and info2["stale"])
        d = L.ProjectLock(lp, "recovery", retry_seconds=0.3).acquire()
        check("a leftover lock file is harmless: it can be taken again at once", d.held and d.run_id != a.run_id)
        d.release()
        check("holder_info of a lock that never existed is None", L.holder_info(sb.path("none.lock")) is None)

        # the owner is mirrored in the journal; its run id is the fencing token
        j, jp = make_journal(sb, "jr1")
        a = L.ProjectLock(sb.path("p2.lock"), "execute", retry_seconds=0.3).acquire(journal=j)
        o = j.owner()
        check("the owner row in the journal mirrors the lock: run id (the fencing token), user, machine, pid, operation",
              o["run_id"] == a.run_id and o["pid"] == os.getpid() and o["operation"] == "execute" and o["windows_user"])
        with j.tx("C1", a.run_id) as t:
            t.execute("INSERT INTO job (job_uid, state, revision_ref, manifest_hash, authorising_record, software_version, approved_utc) "
                      "VALUES ('j', 'APPROVED', 'r', 'h', 't', 'B21', ?)", (J.utc_now(),))
        check("while it owns the journal the run commits through the fencing check", True)

        # fencing: a second run takes the journal over; the first run's next commit fails (JOB_GLOBAL)
        a.release()                                             # the first run lost its lock (or was frozen past a takeover)
        j2 = J.Journal.open(jp)
        b = L.ProjectLock(sb.path("p2.lock"), "execute", retry_seconds=0.3).acquire(journal=j2)
        ok, e = False, None
        try:
            with j.tx("C2", a.run_id) as t:
                t.execute("UPDATE job SET state = 'RUNNING'")
        except J.FencingLost as exc:
            ok, e = True, exc
        check("FENCING: when a second run takes the lock, the first run's next commit is refused (FencingLost, JOB_GLOBAL) and writes nothing",
              ok and j2.conn.execute("SELECT state FROM job").fetchone()[0] == "APPROVED", str(e))
        with j2.tx("C2", b.run_id) as t:
            t.execute("UPDATE job SET state = 'RUNNING'")
        check("... and the new owner's commit goes through", j2.conn.execute("SELECT state FROM job").fetchone()[0] == "RUNNING")
        b.release()
        j.close()
        j2.close()


def test_root_locks():
    from Phase4 import locks as L
    section("3b. locks.py -- the root-level lock")
    with F.Sandbox() as sb:
        ld = sb.path("rootlocks")
        A = sb.mkdir("Corpus", "A")
        AB = sb.mkdir("Corpus", "A", "B")
        AC = sb.mkdir("Corpus", "A", "C")
        Z = sb.mkdir("Corpus", "Z")
        p1 = L.RootLocks(ld, "project-one-0001")
        p2 = L.RootLocks(ld, "project-two-0002")

        k1, k2 = L.dir_key(A), L.dir_key(A.upper() + "\\")
        check("the root key is the directory's (st_dev, st_ino): the same folder spelled in another case with a trailing separator has the same key",
              k1 == k2 and "-" in k1, f"{k1} {k2}")
        check("different folders have different keys", len({L.dir_key(x) for x in (A, AB, AC, Z)}) == 4)

        h1 = p1.acquire(A)
        check("the root lock records project_uid, the display path and the key of every ancestor",
              h1.info["project_uid"] == "project-one-0001" and h1.info["display_path"] == A and len(h1.info["chain"]) >= 3
              and L.dir_key(os.path.dirname(A)) in h1.info["chain"])
        ok, e = False, None
        try:
            p2.acquire(A.upper())
        except L.RootLockBusy as exc:
            ok, e = True, exc
        check("two projects aimed at the SAME root: the second is refused with the holder's information",
              ok and e.holder and e.holder["project_uid"] == "project-one-0001" and e.holder["display_path"] == A
              and e.holder["windows_user"] in str(e), str(e))
        for label, hold, ask in (("nested: A held, A\\B asked", None, AB),):
            ok, e = False, None
            try:
                p2.acquire(ask)
            except L.RootOverlap as exc:
                ok, e = True, exc
            check(f"{label} -> refused (RootOverlap) with the holder's information", ok and e.holder["project_uid"] == "project-one-0001", str(e))
        h1.release()
        h2 = p2.acquire(AB)
        ok, e = False, None
        try:
            p1.acquire(A)
        except L.RootOverlap as exc:
            ok, e = True, exc
        check("nested the other way: A\\B held by another project, A asked -> refused (RootOverlap)", ok and e.holder["display_path"] == AB, str(e))
        h3 = p1.acquire(AC)
        check("sibling roots (A\\B and A\\C) of different projects do not exclude each other", h3 is not None)
        h2.release()
        h3.release()
        hz = p2.acquire(Z)
        h1 = p1.acquire(A)
        check("unrelated roots (A and Z) of different projects can be held together", hz.fd is not None and h1.fd is not None)
        hz.release()
        h1.release()

        # a junction is the same directory
        link = sb.path("alias_of_A")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, A], capture_output=True, text=True).returncode == 0
        check("(a junction could be made in the sandbox for the alias test)", made)
        if made:
            try:
                h1 = p1.acquire(A)
                ok, e = False, None
                try:
                    p2.acquire(os.path.join(link, "B"))
                except L.RootOverlap as exc:
                    ok, e = True, exc
                check("a root reached through a junction is judged by where it really is: alias\\B overlaps A -> refused", ok, str(e))
                ok2, e2 = False, None
                try:
                    p2.acquire(link)
                except L.RootLockBusy as exc:
                    ok2, e2 = True, exc
                check("the junction to A has the same key as A -> refused as the same root", ok2, str(e2))
                h1.release()
            finally:
                os.rmdir(link)

        # one project's own nested roots do not exclude each other; acquire_all dedups and is all-or-none
        both = p1.acquire_all([A, AB, A.upper()])
        check("one project may hold nested roots of its own; duplicates by key are taken once", len(both.locks) == 2)
        both.release()
        hi = max((A, Z), key=L.dir_key)                         # acquire_all takes roots in key order
        lo = Z if hi == A else A
        hb = p2.acquire(hi)
        ok, e = False, None
        try:
            p1.acquire_all([lo, hi])
        except L.RootLockBusy as exc:
            ok, e = True, exc
        free = p2.acquire(lo)
        check("acquire_all is all-or-none: the first root was taken, the second was busy, and the first was released again",
              ok and free is not None, str(e))
        free.release()
        hb.release()

        # the registry lock
        fc = FakeClock()
        guard = L.RootLocks(ld, "project-three-0003", clock=fc.clock, sleep=fc.sleep)
        reg = os.path.join(ld, "registry.lock")
        rfd = os.open(reg, os.O_RDWR | os.O_CREAT | os.O_BINARY)
        os.lseek(rfd, 0, 0)
        msvcrt.locking(rfd, msvcrt.LK_NBLCK, 1)
        ok, e = False, None
        try:
            guard.acquire(Z)
        except L.RootLockBusy as exc:
            ok, e = True, exc
        os.lseek(rfd, 0, 0)
        msvcrt.locking(rfd, msvcrt.LK_UNLCK, 1)
        os.close(rfd)
        check("a busy registry.lock is retried and then reported (the registry lock is short-held)", ok, str(e))

        ok, e = False, None
        try:
            p1.acquire(sb.path("Corpus", "does_not_deepen"))
        except L.RootUnidentifiable as exc:
            ok, e = True, exc
        check("a root that cannot be identified is refused (unknown blocks)", ok, str(e))

        deep = sb.mkdir("Corpus", *[f"lv{i:02d}" for i in range(1, 31)])
        hd = p1.acquire(deep)
        check("a deeply nested root (30 levels) still fits its owner block (chain recorded in full)", len(hd.info["chain"]) >= 31)
        hd.release()


def test_cross_process():
    from Phase4 import journal as J
    from Phase4 import locks as L
    section("3c. locks.py -- across processes: a killed holder frees both locks")
    with F.Sandbox() as sb:
        uid = "proj-uid-00000001"
        j, jp = make_journal(sb, "jrx", uid)
        lockp = sb.path("jrx", uid + ".lock")
        ld = sb.path("rootlocks")
        A = sb.mkdir("Corpus", "A")
        AB = sb.mkdir("Corpus", "A", "B")
        delays = []
        for round_no in range(5):
            proc = F.spawn(SELF, "--worker", "plock", lockp, jp, uid, ld, A)
            line = F.read_line(proc, 30) or ""
            check(f"[round {round_no + 1}] a child process holds the project lock, the journal owner row and the root lock A",
                  line.startswith("LOCKED"), line)
            if not line.startswith("LOCKED"):
                F.terminate_process(proc)
                break
            _tag, run_id, pid = line.split()
            busy = False
            try:
                L.ProjectLock(lockp, "execute", retry_seconds=0.2).acquire()
            except L.LockBusy:
                busy = True
            other = L.RootLocks(ld, "other-project-0009")
            overlap = False
            try:
                other.acquire(AB)
            except L.LockBusy:
                overlap = True
            if round_no == 0:
                info = L.holder_info(lockp)
                check("while the child lives: the project lock is busy, the nested root is refused, and a second instance can read who holds it",
                      busy and overlap and info["held"] and info["pid"] == int(pid) and info["run_id"] == run_id and j.owner()["run_id"] == run_id,
                      str((busy, overlap, info)))
            F.taskkill_force(int(pid))
            t0 = time.perf_counter()
            window = L.DEFAULT_RETRY_S
            got = None
            while time.perf_counter() - t0 < window:
                try:
                    got = L.ProjectLock(lockp, "recovery", retry_seconds=0).acquire(journal=j)
                    break
                except L.LockBusy:
                    time.sleep(0.002)
            delays.append((time.perf_counter() - t0) * 1000.0)
            check(f"[round {round_no + 1}] after taskkill /F the project lock is free inside the retry window", got is not None, f"{delays[-1]:.1f} ms")
            rl = None
            try:
                rl = other.acquire(AB)
            except L.LockError as exc:
                rl = None
            check(f"[round {round_no + 1}] ... and so is the root lock (the dead holder's leftover file and chain are ignored)", rl is not None)
            if rl:
                rl.release()
            check(f"[round {round_no + 1}] ... and the journal owner row now names the new run, not the dead one",
                  got is not None and j.owner()["run_id"] == got.run_id != run_id)
            if got:
                got.release()
            proc.wait(timeout=10)
        if delays:
            print(f"    release delay after taskkill, project lock via locks.py: median {F.median(delays):.1f} ms, max {max(delays):.1f} ms")
        j.close()


def run_unit3():
    test_project_lock()
    test_root_locks()
    test_cross_process()


def print_probe_summary():
    section("Probe summary (unit 1)")
    for k in ("taskkill", "os_exit", "access_violation"):
        v = PROBES.get(k)
        if v:
            print(f"  {k:17s} runs={v['runs']:<3d} release delay median {v['median_ms']:.1f} ms, max {v['max_ms']:.1f} ms")
    for k in ("L4", "L4_control_inheritable", "L8", "L11", "L12"):
        if k in PROBES:
            print(f"  {k:24s} {PROBES[k]}")
    print("  Not run (need a person, a second Windows account or a VM): L2, L5, L6, L7. Never run (by rule): L9, L10.")


# ===================================================================================================
def main():
    before = F.localappdata_fileorganizer_state()
    sandboxes_before = F.sandbox_folders()
    probe_l1_l3()
    probe_l4()
    probe_l8()
    probe_l11()
    probe_l12()
    print_probe_summary()
    run_unit3()

    section("The production data folder is untouched")
    after = F.localappdata_fileorganizer_state()
    check("%LOCALAPPDATA%\\FileOrganizer: existence and file listing unchanged by this run", before == after)
    survivors = sorted(F.sandbox_folders() - sandboxes_before)
    check("no fo_p4_b21_* sandbox folder created during this run survives it", not survivors,
          "; ".join(survivors[:5]) + ("" if not F.LEFTOVER_SANDBOXES else " (cleanup could not remove: %s)" % F.LEFTOVER_SANDBOXES[:5]))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\np4_lock_check: {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker_main(sys.argv[2:])
    else:
        sys.exit(main())
