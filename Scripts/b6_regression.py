#!/usr/bin/env python3
r"""
b6_regression.py
===================================================================
PRODUCTION CODE
The File Organizer -- B6.1 Regression Suite
Module version: 1.1.0
===================================================================

One executable check per B5 finding that B6 claims to have fixed.

WHY THIS FILE EXISTS
--------------------
B5-E and B5-F found real defects. Fixing them is worth little if the
next revision reintroduces them, and several of these are the kind of
defect that comes back easily:

  * a `.fetchall()` added to a query for convenience undoes the
    streaming export;
  * a `len(text.split())` added to a new analyzer undoes the memory fix;
  * a query written against `hash_measurement` instead of `file_state`
    undoes the current-state separation and nothing visibly breaks --
    it just gets slower, run by run, exactly as before.

That last one is the reason this suite MEASURES rather than merely
exercising. A correctness test would pass on B4.5's duplicate query.
The defect was never that the answer was wrong.

WHAT THIS IS NOT
----------------
It is not a substitute for adversarial testing, and it is not evidence
about Windows. Everything here runs on the build machine, so NTFS
behaviour, real locale APIs, long paths, cloud placeholders and network
roots remain untested -- the same gaps B5 recorded as unavailable
evidence. Those become B6 acceptance targets, not claims this file can
make.

Usage:
    python Scripts/b6_regression.py
    python Scripts/b6_regression.py --quick     (skip the slow measurements)
"""

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
import zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
for path in (HERE, os.path.join(HERE, "Database")):
    if path not in sys.path:
        sys.path.insert(0, path)

import fo_db          # noqa: E402
import fo_exports     # noqa: E402
import fo_inventory_records  # noqa: E402
import fo_scan        # noqa: E402
import fo_state       # noqa: E402
import fo_text        # noqa: E402
import win_meta       # noqa: E402
import fo_estimates    # noqa: E402
import fo_hash_records # noqa: E402
import fo_hashes       # noqa: E402
import fo_analyzer_records  # noqa: E402
import fo_analyzer_engine   # noqa: E402
import fo_analyzers         # noqa: E402
import ContentExtraction  # noqa: E402


PASS, FAIL = "PASS", "FAIL"
_results = []


def check(finding, description, passed, detail=""):
    _results.append((finding, description, PASS if passed else FAIL, detail))
    print("  [%s] %-11s %-46s %s"
          % (PASS if passed else FAIL, finding, description, detail))
    return passed


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def build_corpus(base, files=800, dup_every=20):
    for i in range(files):
        sub = os.path.join(base, "d%02d" % (i % 16))
        os.makedirs(sub, exist_ok=True)
        body = ((b"DUP-%d" % (i % 40)) * 20 if i % dup_every == 0
                else b"unique-%d-" % i + hashlib.sha256(
                    str(i).encode()).digest())
        with open(os.path.join(sub, "f%04d.bin" % i), "wb") as handle:
            handle.write(body)
    return base


def new_project(corpus, history_mode="changes"):
    project = os.path.join(tempfile.mkdtemp(), "P")
    os.makedirs(project)
    fo_db.init_project(project, "P", source_roots=[corpus])
    conn, _ = fo_db.open_project(project)
    fo_state.set_policy(conn, "history.mode", history_mode)
    conn.commit()
    return conn, project


def do_run(conn, corpus, label, hash_files=True):
    """One scan, plus a stand-in for the hash stage."""
    cursor = conn.execute(
        "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
        " app_version, schema_version) VALUES (1,?,'scan','running',?,'B6.1',7)",
        (label, fo_state.utc_now()))
    run_id = cursor.lastrowid
    conn.commit()

    ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
    summary = ingestor.ingest_records(fo_scan.scan(corpus), corpus, "MDY",
                                      scan_errors=[])
    for scan_id in ingestor.scan_ids.values():
        conn.execute("UPDATE inventory_scan SET status = 'completed' "
                     "WHERE inventory_scan_id = ?", (scan_id,))

    if hash_files:
        rows = conn.execute(
            "SELECT fs.current_observation_id, sr.root_path, fp.relative_path "
            "FROM file_state fs "
            "JOIN file_path fp ON fp.file_path_id = fs.file_path_id "
            "JOIN source_root sr ON sr.source_root_id = fs.source_root_id "
            "WHERE fs.state = 'present'").fetchall()
        for row in rows:
            full = os.path.join(row["root_path"], row["relative_path"])
            with open(full, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            size = os.path.getsize(full)
            conn.execute(
                "INSERT OR IGNORE INTO content (project_id, sha256, size_bytes,"
                " identity_source, first_seen_utc, last_seen_utc) "
                "VALUES (1,?,?,'full_hash',?,?)",
                (digest, size, fo_state.utc_now(), fo_state.utc_now()))
            content_id = conn.execute(
                "SELECT content_id FROM content WHERE sha256 = ?",
                (digest,)).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO hash_measurement (project_id,"
                " file_observation_id, content_id, run_id, measurement_mode,"
                " hash_status, measured_utc, full_hash) "
                "VALUES (1,?,?,?,'exhaustive','hashed',?,?)",
                (row["current_observation_id"], content_id, run_id,
                 fo_state.utc_now(), digest))
        conn.commit()
        fo_state.attach_content(conn, run_id)

    conn.execute("UPDATE run SET status='completed', finalized=1 "
                 "WHERE run_id = ?", (run_id,))
    conn.commit()
    return summary


# ---------------------------------------------------------------------------
# E.F009 / E.F007 -- memory
# ---------------------------------------------------------------------------

def test_word_count_equivalence():
    import random
    cases = ["", " ", "a", "a b", " a b ", "a\tb\nc", "a\u00a0b", "x\u2028y",
             "\r\n" * 5, "tail\r", "\u000b\u000c\u001c\u001d\u001e\u0085"]
    random.seed(4)
    alphabet = " \n\r\u2028ab\u0085\u000b\t\u000c\u001c"
    for _ in range(800):
        cases.append("".join(random.choice(alphabet)
                             for _ in range(random.randint(0, 90))))
    bad_words = sum(1 for c in cases
                    if fo_text.count_words(c) != len(c.split()))
    bad_lines = sum(1 for c in cases
                    if fo_text.count_lines(c) != len(c.splitlines()))
    bad_stats = 0
    for case in cases[:200]:
        stats = fo_text.TextStats()
        index = 0
        while index < len(case):
            stats.feed(case[index:index + 3])
            index += 3
        stats.finish()
        if (stats.chars, stats.words, stats.lines) != (
                len(case), len(case.split()), len(case.splitlines())):
            bad_stats += 1
    check("E.F009", "word/line counts exactly match stdlib",
          bad_words == 0 and bad_lines == 0 and bad_stats == 0,
          "%d cases, %d mismatches" % (len(cases),
                                       bad_words + bad_lines + bad_stats))


def test_word_count_memory():
    text = " ".join("token%d" % i for i in range(400_000))
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    naive = len(text.split())
    old_peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()

    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    counted = fo_text.count_words(text)
    new_peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()

    check("E.F009", "word count is constant-memory",
          counted == naive and new_peak < old_peak / 100,
          "%.1f MB -> %.3f MB, same answer"
          % (old_peak / 1048576.0, new_peak / 1048576.0))


def test_analyzer_retention():
    import fo_analyzer_engine as engine

    class Result(object):
        __slots__ = ("error", "payload")

        def __init__(self):
            self.error = ""
            self.payload = "y" * 400

    def peak_for(retain, count=60_000):
        tracemalloc.start()
        base = tracemalloc.get_traced_memory()[0]
        outcome = engine.AnalyzerOutcome("k", "L", retain=retain)
        for _ in range(count):
            outcome.record(Result())
        peak = tracemalloc.get_traced_memory()[1] - base
        tracemalloc.stop()
        return peak, outcome

    retained_peak, retained = peak_for(True)
    sunk_peak, sunk = peak_for(False)
    check("E.F007", "analyzer results not retained when sunk",
          sunk_peak < retained_peak / 20
          and sunk.result_count == retained.result_count,
          "%.1f MB -> %.2f MB, identical counts"
          % (retained_peak / 1048576.0, sunk_peak / 1048576.0))


# ---------------------------------------------------------------------------
# E.F003 -- streaming exports
# ---------------------------------------------------------------------------

def test_sink_failure_is_not_success():
    r"""B5-G: a stage whose results never reached the database has not
    completed, and must not report that it did.

    This check exists because an earlier B6 draft logged sink failures
    and carried on, producing a run that read `completed` with zero
    rows written -- a false completion manufactured by the error
    handling rather than by a crash. It was found by a missing import,
    which is exactly the kind of accident this must survive.
    """
    import fo_analyzer_engine as engine

    folder = tempfile.mkdtemp()
    entries = []
    for i in range(4):
        path = os.path.join(folder, "f%d.txt" % i)
        with open(path, "w") as handle:
            handle.write("a b c\n")
        entries.append(type("E", (), {
            "path": path, "db_id": i + 1, "extension": ".txt",
            "size_bytes": 6, "file_name": os.path.basename(path),
            "is_offline_or_cloud": 0})())

    def broken_sink(_key, _result):
        raise RuntimeError("database is locked")

    outcomes = engine.AnalyzerEngine().run_all(
        entries, context={"extract_folder": folder}, only={"text"},
        sink=broken_sink)
    outcome = [o for o in outcomes if o.key == "text"][0]
    check("G", "persistence failure does not report completed",
          outcome.status == engine.STATUS_FAILED
          and bool(outcome.failure_reason),
          "status=%s" % outcome.status)


def test_export_streaming():
    rows = 120_000

    def make():
        for i in range(rows):
            yield [i, "f%d.txt" % i, ".txt", r"C:\D", r"C:\D\f%d.txt" % i,
                   4096, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                   "2026-01-01T00:00:00Z", "Archive", "False", "False", 2, 40]

    target = os.path.join(tempfile.mkdtemp(), "out.csv")
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    _path, count = fo_exports.POWERSHELL.write(
        target, fo_exports.INVENTORY_COLUMNS, make())
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    size = os.path.getsize(target)
    check("E.F003", "export peak is bounded, not proportional",
          count == rows and peak < size / 10,
          "%.1f MB artifact, %.2f MB peak" % (size / 1048576.0,
                                              peak / 1048576.0))


def test_export_dialects_unchanged():
    rows = [[1, "plain", None, ""],
            [2, "has,comma", 'has"quote', "has\nnewline"],
            [3, None, None, None],
            [4, "\u00fcn\u00efc\u00f8d\u00e9", "tab\there", "trailing "]]
    folder = tempfile.mkdtemp()
    signatures = {}
    for name, dialect in (("powershell", fo_exports.POWERSHELL),
                          ("python", fo_exports.PYTHON)):
        target = os.path.join(folder, name + ".csv")
        dialect.write(target, ["A", "B", "C", "D"],
                      iter([r[:] for r in rows]))
        with open(target, "rb") as handle:
            signatures[name] = handle.read()
    ps_ok = (signatures["powershell"].startswith(b"\xef\xbb\xbf")
             and b'"1","plain",,""' in signatures["powershell"])
    py_ok = (not signatures["python"].startswith(b"\xef\xbb\xbf")
             and b"\r\n" in signatures["python"])
    check("E.F003", "CSV dialects unchanged by streaming", ps_ok and py_ok,
          "BOM, quoting and NULL rules preserved")


# ---------------------------------------------------------------------------
# E.F001 / E.F002 -- history and the duplicate query
# ---------------------------------------------------------------------------

def test_history_and_duplicate_query(runs=8):
    corpus = build_corpus(tempfile.mkdtemp())
    conn, _project = new_project(corpus)

    timings, observation_counts = [], []
    for index in range(runs):
        do_run(conn, corpus, "r%d" % index)
        observation_counts.append(conn.execute(
            "SELECT COUNT(*) FROM file_observation").fetchone()[0])
        fo_state.current_duplicate_sets(conn)          # warm
        start = time.perf_counter()
        for _ in range(3):
            groups = fo_state.current_duplicate_sets(conn)
        timings.append((time.perf_counter() - start) / 3 * 1000)

    files = conn.execute("SELECT COUNT(*) FROM file_path").fetchone()[0]
    check("E.F001", "history does not grow on an unchanged corpus",
          observation_counts[-1] == observation_counts[0] == files,
          "%d runs, observations stayed at %d" % (runs, observation_counts[-1]))

    # The measurement, not just the behaviour: latency must not trend up.
    drift = timings[-1] / max(timings[0], 0.001)
    check("E.F002", "duplicate query latency is flat over runs",
          drift < 2.0,
          "run 1 %.2f ms -> run %d %.2f ms (%.2fx)"
          % (timings[0], runs, timings[-1], drift))
    check("E.F002", "duplicate query still finds the groups",
          len(groups) > 0, "%d groups" % len(groups))
    conn.close()


def test_change_and_vanish_detection():
    corpus = build_corpus(tempfile.mkdtemp(), files=200)
    conn, _project = new_project(corpus)
    do_run(conn, corpus, "r0")

    victim = conn.execute(
        "SELECT sr.root_path, fp.relative_path FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id = fs.file_path_id "
        "JOIN source_root sr ON sr.source_root_id = fs.source_root_id "
        "LIMIT 1").fetchone()
    path = os.path.join(victim["root_path"], victim["relative_path"])

    time.sleep(1.05)
    with open(path, "ab") as handle:
        handle.write(b"CHANGED")
    summary = do_run(conn, corpus, "r1", hash_files=False)
    check("H", "modification produces exactly one new observation",
          summary["changed"] == 1 and summary["new"] == 0,
          "changed=%d unchanged=%d" % (summary["changed"],
                                       summary["unchanged"]))
    check("H", "superseded hash is reported stale, not current",
          fo_state.stale_content_count(conn) == 1,
          "%d stale content identities"
          % fo_state.stale_content_count(conn))

    os.remove(path)
    summary = do_run(conn, corpus, "r2", hash_files=False)
    states = fo_state.state_summary(conn)
    check("H", "deletion becomes 'missing', not silence",
          summary["vanished"] == 1 and states["missing"] == 1,
          "missing=%d present=%d" % (states["missing"], states["present"]))
    conn.close()


def test_missing_root_is_not_empty_root():
    corpus = build_corpus(tempfile.mkdtemp(), files=100)
    conn, _project = new_project(corpus)
    do_run(conn, corpus, "r0")
    before = fo_state.state_summary(conn)["present"]

    # A root that could not be reached: no records, and explicitly
    # unavailable. Nothing may be marked missing.
    cursor = conn.execute(
        "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
        " app_version, schema_version) VALUES (1,'offline','scan','running',?,"
        "'B6',6)", (fo_state.utc_now(),))
    run_id = cursor.lastrowid
    conn.commit()
    ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
    ingestor.ingest_records(iter(()), corpus, "MDY", scan_errors=[],
                            root_available=False)
    after = fo_state.state_summary(conn)
    check("H", "unreachable root does not delete its files",
          after["present"] == before and after["missing"] == 0,
          "present %d -> %d, missing %d" % (before, after["present"],
                                            after["missing"]))
    conn.close()


def test_multi_root_ingest_keeps_earlier_roots():
    r"""B7.1 -- a later root's ingest must not vanish an earlier root.

    P2_Stress_Test (2026-09-19), the first two-root project: every file
    under the first root was marked missing during the prescan, and the
    hash, duplicate and analyzer passes then never saw it. The
    coordinator walks the roots one at a time into ONE ScanStatistics
    (the preliminary report is cumulative), so the second root's ingest
    was handed the first root's directory error and path events; those
    resolved to the first root, opened its scan row, and vanished-
    detection -- run over every scan row the ingest had touched, with a
    projector that had seen none of that root's files -- marked all of
    them missing.

    Two contracts are exercised against the same fixture, the way the
    coordinator sequences them: the OLD one (whole lists) proves the
    ingestor itself no longer declares files missing under a root it
    did not walk; the NEW one (per-root windows, consulted after the
    walk) proves each root's errors and events are attributed once, to
    the root they fell under.
    """
    first, second = tempfile.mkdtemp(), tempfile.mkdtemp()
    for folder, count in ((first, 6), (second, 4)):
        for i in range(count):
            with open(os.path.join(folder, "f%d.txt" % i), "w") as handle:
                handle.write("root file %d" % i)
    os.makedirs(os.path.join(first, "empty"))     # a path event under root 1
    denied = os.path.join(first, "denied")         # a directory error under root 1

    def walk(root, cursor, statistics):
        """The walk, plus the directory error the hostile corpus produces
        (appended when the generator is exhausted, as the real walker does)."""
        for record in fo_scan.scan(root, cursor, statistics):
            yield record
        if root == first:
            statistics.errors.append(fo_scan.ScanError(
                fo_scan.DIRECTORY_ACCESS_ERROR, denied, "Access is denied"))

    def scan_both(windowed):
        project = os.path.join(tempfile.mkdtemp(), "P")
        os.makedirs(project)
        fo_db.init_project(project, "P", source_roots=[first, second])
        conn, _ = fo_db.open_project(project)
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version) VALUES (1,?,'scan','running',?,'B7.1',8)",
            ("two-roots-%s" % windowed, fo_state.utc_now()))
        run_id = cursor.lastrowid
        conn.commit()

        statistics = fo_scan.ScanStatistics()
        summaries, next_id = [], 1
        for root in (first, second):
            ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
            errors_before = len(statistics.errors)
            events_before = len(statistics.path_events)
            if windowed:
                errors = lambda: statistics.errors[errors_before:]
                events = lambda: statistics.path_events[events_before:]
            else:
                errors, events = statistics.errors, statistics.path_events
            summaries.append(ingestor.ingest_records(
                walk(root, next_id, statistics), root, "MDY",
                scan_errors=errors, path_events=events,
                complete=lambda: not statistics.stopped))
            ingestor.finish()
            next_id = statistics.file_count + 1

        states = {}
        for row in conn.execute(
                "SELECT sr.root_path, fs.state, COUNT(*) AS n FROM file_state fs "
                "JOIN source_root sr ON sr.source_root_id = fs.source_root_id "
                "WHERE fs.size_bytes IS NOT NULL GROUP BY 1, 2"):
            states[(row["root_path"], row["state"])] = row["n"]
        scans = {row["root_path"]: dict(row) for row in conn.execute(
            "SELECT sr.root_path, s.observed_count, s.inaccessible_count, "
            "       s.inaccessible_seen_count, s.vanished_count, s.notes, "
            "       (SELECT COUNT(*) FROM scan_path_event e "
            "        WHERE e.inventory_scan_id = s.inventory_scan_id) AS events "
            "FROM inventory_scan s "
            "JOIN source_root sr ON sr.source_root_id = s.source_root_id")}
        conn.close()
        return summaries, states, scans

    for windowed in (False, True):
        label = "windowed" if windowed else "whole lists"
        summaries, states, scans = scan_both(windowed)
        check("B7.1", "second root's ingest keeps the first root (%s)" % label,
              states.get((first, "present")) == 6
              and states.get((first, "missing"), 0) == 0
              and states.get((second, "present")) == 4
              and summaries[1]["vanished"] == 0,
              "first present=%s missing=%s, second present=%s, vanished=%d"
              % (states.get((first, "present")), states.get((first, "missing"), 0),
                 states.get((second, "present")), summaries[1]["vanished"]))
        if not windowed:
            continue
        check("B7.1", "each root's scan row keeps its own counts",
              scans[first]["observed_count"] == 6
              and scans[second]["observed_count"] == 4
              and scans[first]["vanished_count"] == 0
              and scans[second]["vanished_count"] == 0,
              "observed %s / %s, vanished %s / %s"
              % (scans[first]["observed_count"], scans[second]["observed_count"],
                 scans[first]["vanished_count"], scans[second]["vanished_count"]))
        check("B7.1", "errors and events attributed once, to their root",
              scans[first]["inaccessible_count"] == 1
              and scans[first]["inaccessible_seen_count"] == 1
              and scans[second]["inaccessible_count"] == 0
              and scans[second]["inaccessible_seen_count"] == 0
              and summaries[0]["directory_errors"] == 1
              and summaries[1]["directory_errors"] == 0
              and scans[first]["events"] == 1 and scans[second]["events"] == 0
              and not (scans[second]["notes"] or ""),
              "inaccessible %s/%s seen %s/%s, dir_errors %s/%s, events %s/%s, "
              "second notes=%r"
              % (scans[first]["inaccessible_count"], scans[second]["inaccessible_count"],
                 scans[first]["inaccessible_seen_count"],
                 scans[second]["inaccessible_seen_count"],
                 summaries[0]["directory_errors"], summaries[1]["directory_errors"],
                 scans[first]["events"], scans[second]["events"],
                 scans[second]["notes"]))
    shutil.rmtree(first, ignore_errors=True)
    shutil.rmtree(second, ignore_errors=True)


# ---------------------------------------------------------------------------
# F.F001 / F.F002 -- determinism
# ---------------------------------------------------------------------------

def test_enumeration_determinism(trials=5):
    import random
    # No two names may differ only by case: on a case-insensitive volume
    # the second would overwrite the first and the surviving spelling would
    # follow the shuffle -- which is what this check found on 2026-09-15
    # (9 of 10 files, five trials in disagreement over a/B.txt vs a/b.txt).
    names = ["a/x.txt", "a/Y.txt", "a/b.txt", "z/1.txt", "z/10.txt",
             "z/2.txt", "m/n/deep.txt", "m/a.txt", "Top.txt", "top2.txt"]
    orders = []
    for _ in range(trials):
        folder = tempfile.mkdtemp()
        shuffled = names[:]
        random.shuffle(shuffled)
        for name in shuffled:
            target = os.path.join(folder, name.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as handle:
                handle.write(name)
        orders.append([r.path[len(folder):] for r in fo_scan.scan(folder)])
        shutil.rmtree(folder)
    check("F.F001", "walk order independent of creation order",
          all(o == orders[0] for o in orders),
          "%d trials identical, %d files" % (trials, len(orders[0])))


def test_report_tie_breaking(trials=5):
    import random
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
             "golf", "hotel", "india", "juliet"]
    tops = []
    for _ in range(trials):
        folder = tempfile.mkdtemp()
        shuffled = names[:]
        random.shuffle(shuffled)
        for name in shuffled:              # every file the SAME size
            with open(os.path.join(folder, name + ".bin"), "wb") as handle:
                handle.write(b"x" * 4096)
        statistics = fo_scan.ScanStatistics()
        list(fo_scan.scan(folder, statistics=statistics))
        tops.append([os.path.basename(p) for _s, p in statistics.largest[:5]])
        shutil.rmtree(folder)
    check("F.F002", "top-N ties resolved by data, not arrival",
          all(t == tops[0] for t in tops),
          "%d trials identical on all-equal sizes" % trials)


def test_root_order_does_not_matter():
    first = tempfile.mkdtemp()
    second = tempfile.mkdtemp()
    for folder in (first, second):
        for i in range(5):
            with open(os.path.join(folder, "f%d.txt" % i), "w") as handle:
                handle.write("x")

    ordinals = []
    for roots in ([first, second], [second, first]):
        project = os.path.join(tempfile.mkdtemp(), "P")
        os.makedirs(project)
        fo_db.init_project(project, "P", source_roots=roots)
        conn, _ = fo_db.open_project(project)
        fo_state.assign_root_ordinals(conn)
        conn.commit()
        ordinals.append(sorted(
            (row["root_path"], row["root_ordinal"]) for row in conn.execute(
                "SELECT root_path, root_ordinal FROM source_root")))
        conn.close()
    check("F.F008", "root ordinals independent of configured order",
          ordinals[0] == ordinals[1], "both orderings agree")


# ---------------------------------------------------------------------------
# F.F003 / F.F004 -- timestamps
# ---------------------------------------------------------------------------

def test_timestamps_are_utc():
    nanoseconds = 1_755_200_776_000_000_000
    utc = win_meta.utc_iso_seconds(nanoseconds)
    check("F.F003", "stored timestamps are true UTC with Z",
          utc is not None and utc.endswith("Z") and "T" in utc, utc)

    offset = win_meta.utc_offset_minutes(nanoseconds)
    check("F.F003", "UTC offset recorded alongside",
          offset is None or isinstance(offset, int),
          "offset %s minutes" % offset)


def test_export_timestamps_locale_independent():
    r"""B5-F.F004: one stored value must render identically everywhere."""
    stored = "2026-08-14T15:26:16Z"
    rendered = fo_exports.render_canonical_timestamp(stored)
    check("F.F004", "canonical export timestamp is ISO-8601",
          rendered == stored, rendered)

    legacy = fo_exports.render_canonical_timestamp(None, "2026-08-14T15:26:16")
    check("F.F004", "pre-B6 value is labelled, never silently blank",
          legacy.endswith("(local)") and legacy != "", legacy)

    # The B4.5 failure mode: an unknown pattern produced an empty cell.
    check("F.F004", "canonical renderer has no blank-cell path",
          fo_exports.render_canonical_timestamp("2026-01-01T00:00:00Z") != "",
          "no locale pattern can empty it")


# ---------------------------------------------------------------------------
# E.F006 -- archives
# ---------------------------------------------------------------------------

def test_archive_bounds():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "archive_analysis", os.path.join(HERE, "ArchiveAnalysis.py"))
    archive = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archive)

    folder = tempfile.mkdtemp()
    big = os.path.join(folder, "big.zip")
    entries = 80_000
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as handle:
        for i in range(entries):
            handle.writestr("d%03d/e%06d.txt" % (i % 200, i), b"x" * 8)

    check("E.F006", "entry count read without opening the archive",
          archive.peek_zip_entry_count(big) == entries,
          "%d entries from the EOCD record" % entries)

    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    aggregate, retained = archive.analyze_archive(big, 10_000, 50_000)
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    check("E.F006", "huge archive summarised, memory bounded",
          aggregate["AnalysisMode"] == archive.MODE_SUMMARY
          and retained == [] and peak < 5 * 1048576,
          "%.2f MB peak, EntryCount %s, mode %s"
          % (peak / 1048576.0, aggregate["EntryCount"],
             aggregate["AnalysisMode"]))

    capped, kept = archive.analyze_archive(big, 1_000, 0)
    check("E.F006", "capped listing keeps aggregates truthful",
          capped["EntryCount"] == str(entries) and len(kept) == 1_000
          and capped["Truncated"] == "True",
          "EntryCount %s, %d rows kept, truncated flagged"
          % (capped["EntryCount"], len(kept)))

    small = os.path.join(folder, "small.zip")
    with zipfile.ZipFile(small, "w") as handle:
        for i in range(300):
            handle.writestr("f%03d.txt" % i, b"y" * 64)
    normal, rows = archive.analyze_archive(small)
    check("E.F006", "ordinary archives still listed completely",
          normal["AnalysisMode"] == archive.MODE_COMPLETE and len(rows) == 300,
          "300 entries, mode complete")


# ---------------------------------------------------------------------------
# E.F044 -- the inaccessible cap
# ---------------------------------------------------------------------------

def test_inaccessible_cap_is_recorded():
    corpus = build_corpus(tempfile.mkdtemp(), files=20)
    conn, _project = new_project(corpus)
    fo_state.set_policy(conn, "inaccessible.cap", "5")
    conn.commit()

    errors = [fo_scan.ScanError(fo_scan.FILE_ERROR,
                                os.path.join(corpus, "denied%d.txt" % i),
                                "Access is denied") for i in range(17)]
    cursor = conn.execute(
        "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
        " app_version, schema_version) VALUES (1,'cap','scan','running',?,"
        "'B6',6)", (fo_state.utc_now(),))
    run_id = cursor.lastrowid
    conn.commit()
    ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
    ingestor.ingest_records(fo_scan.scan(corpus), corpus, "MDY",
                            scan_errors=errors)

    row = conn.execute(
        "SELECT inaccessible_seen_count, inaccessible_cap, "
        "       inaccessible_truncated FROM inventory_scan "
        "ORDER BY inventory_scan_id DESC LIMIT 1").fetchone()
    check("E.F044", "truncated diagnostics are visibly truncated",
          row["inaccessible_seen_count"] == 17 and row["inaccessible_cap"] == 5
          and row["inaccessible_truncated"] == 1,
          "seen %d, cap %d, truncated flagged"
          % (row["inaccessible_seen_count"], row["inaccessible_cap"]))
    conn.close()


# ---------------------------------------------------------------------------
# E.F004 / E.F005 -- indexes
# ---------------------------------------------------------------------------

def test_indexes_present():
    project = os.path.join(tempfile.mkdtemp(), "P")
    os.makedirs(project)
    fo_db.init_project(project, "P", source_roots=[tempfile.mkdtemp()])
    conn, _ = fo_db.open_project(project)
    names = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'")}
    check("E.F004", "duplicate_member.hash_measurement_id indexed",
          "ix_duplicate_member_hash" in names, "ix_duplicate_member_hash")
    check("E.F005", "file_observation.legacy_db_id indexed",
          "ix_observation_legacy_db_id" in names, "ix_observation_legacy_db_id")

    # The plan, not just the index's existence: a transient automatic
    # index is exactly what B5-E.F004 observed SQLite building.
    plan = " ".join(str(r[3]) for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM duplicate_member "
        "WHERE hash_measurement_id = 1"))
    check("E.F004", "no transient automatic index in the plan",
          "AUTOMATIC" not in plan.upper(), plan[:60])
    conn.close()


# ---------------------------------------------------------------------------
# B6.1 A-D reconciliation / integration regressions
# ---------------------------------------------------------------------------

def test_extension_contract():
    cases = [(".gitignore", ".gitignore"), ("trailing.", ""),
             ("REPORT.PDF", ".PDF"), ("plain", "")]
    bad=[]
    for name, expected in cases:
        cells=fo_exports.inventory_cells(
            1, name, "C:\\Root\\" + name, 1,
            "2026-01-01T00:00:00", "2026-01-01T00:00:00",
            "2026-01-01T00:00:00", "Archive", 0, 0, 0, len(name), "MDY")
        actual=cells[2]
        if actual != expected or win_meta.dotnet_extension(name) != expected:
            bad.append((name, actual, expected))
    check("A.F001", "scan/export extension semantics are identical",
          not bad, "4 edge cases" if not bad else repr(bad[:2]))


def test_text_decoding_and_content_addressing():
    raw="Same content\nwith unicode Ω\n".encode("utf-8")
    text, encoding=fo_text.decode_bytes(raw)
    check("A.NEW.UTF8", "valid UTF-8 wins before probabilistic detection",
          text == "Same content\nwith unicode Ω\n" and encoding == "utf-8",
          encoding)

    work=Path(tempfile.mkdtemp())
    try:
        a=work/"a.txt"; b=work/"b.txt"; store=work/"Extracted"
        a.write_bytes(raw); b.write_bytes(raw)
        analyze=ContentExtraction.make_analyze_fn(store, content_addressed=True)
        one=analyze(str(a)); two=analyze(str(b))
        artifact=store/one["ExtractedTextFile"]
        partials=list(store.rglob("*.partial"))
        passed=(one["TextSha256"] == two["TextSha256"]
                and one["ExtractedTextFile"] == two["ExtractedTextFile"]
                and one["ReusedExisting"] == "False"
                and two["ReusedExisting"] == "True"
                and artifact.read_text(encoding="utf-8") == text
                and not partials)
        check("E.F008", "content-addressed extraction reuses exact text",
              passed, one["ExtractedTextFile"])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _latest_scan_ids(conn):
    return [r[0] for r in conn.execute(
        "SELECT inventory_scan_id FROM inventory_scan "
        "WHERE status='completed' ORDER BY inventory_scan_id DESC LIMIT 1")]



def test_inventory_finish_tracks_unchanged_files():
    root=tempfile.mkdtemp()
    try:
        for name in ("a.txt","b.txt"):
            with open(os.path.join(root,name),"w",encoding="utf-8") as h: h.write(name)
        conn, project=new_project(root)
        scan_ids=[]
        for n in (1,2):
            run_id=conn.execute(
                "INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,schema_version) "
                "VALUES(1,?,'scan','running',?,'B6.1',7)",
                ("finish-%d"%n,fo_state.utc_now())).lastrowid
            conn.commit()
            stats=fo_scan.ScanStatistics()
            ing=fo_inventory_records.RecordIngestor(conn,run_id)
            ing.ingest_records(fo_scan.scan(root,statistics=stats),root,"MDY",
                               scan_errors=stats.errors,path_events=stats.path_events)
            status=ing.finish()
            scan_ids.append(max(ing.scan_ids.values()))
            conn.execute("UPDATE run SET status='completed',finalized=1 WHERE run_id=?",(run_id,))
            conn.commit()
            if status not in ("completed","completed_with_warnings"):
                break
        rows=conn.execute(
            "SELECT COUNT(*),COUNT(DISTINCT last_seen_scan_id),MIN(last_seen_scan_id),MAX(last_seen_scan_id) "
            "FROM file_path").fetchone()
        obs=conn.execute("SELECT COUNT(*) FROM file_observation").fetchone()[0]
        ok=(rows[0]==2 and rows[1]==1 and rows[2]==scan_ids[-1] and rows[3]==scan_ids[-1] and obs==2)
        check("B6.1.FIN", "scan finalization refreshes unchanged locations from current state",
              ok, "obs=%d last_seen_scan=%s"%(obs,rows[3]))
        conn.close()
    finally:
        shutil.rmtree(root,ignore_errors=True)

def test_repeat_scan_current_inputs_and_estimator():
    corpus=tempfile.mkdtemp()
    try:
        for name, body in (("a.bin", b"abcde"), ("b.bin", b"vwxyz"),
                           ("c.bin", b"1234567")):
            with open(os.path.join(corpus,name),"wb") as h: h.write(body)
        conn, project=new_project(corpus)
        do_run(conn,corpus,"repeat-1",hash_files=False)
        obs1=conn.execute("SELECT COUNT(*) FROM file_observation").fetchone()[0]
        do_run(conn,corpus,"repeat-2",hash_files=False)
        obs2=conn.execute("SELECT COUNT(*) FROM file_observation").fetchone()[0]
        scan_ids=_latest_scan_ids(conn)
        hrows=fo_hash_records.load_entries(conn,scan_ids)
        arows=fo_analyzer_records.load_entries(conn,scan_ids)
        totals=fo_estimates.inventory_totals(conn,scan_ids)
        counts=fo_estimates.inventory_file_counts(conn,scan_ids)
        samples=fo_estimates.sample_candidates(conn,scan_ids,limit=20)
        passed=(obs1 == 3 and obs2 == 3 and len(hrows) == 3 and len(arows) == 3
                and counts == (3,2) and totals == (17,10) and len(samples) == 3)
        check("B6.1.INT", "unchanged files remain current downstream inputs",
              passed, "obs %d->%d hash=%d analyzer=%d" %
              (obs1,obs2,len(hrows),len(arows)))

        old_measure=fo_estimates.measure_throughput
        try:
            fo_estimates.measure_throughput=lambda candidates: (1000.0, 3, 17)
            values=fo_estimates.calibrate(conn,scan_ids,drive_type="Fixed")
        finally:
            fo_estimates.measure_throughput=old_measure
        est_ok=(values["_total_files"] == 3 and values["_candidate_files"] == 2
                and values["_total_bytes"] == 17 and values["_candidate_bytes"] == 10
                and values["_per_file_seconds"] > 0
                and values["_full_stages"]["persist_seconds"] > 0)
        check("E.F013", "estimator models current file count and stages",
              est_ok, "files=%s candidates=%s" %
              (values["_total_files"], values["_candidate_files"]))
        conn.close()
    finally:
        shutil.rmtree(corpus, ignore_errors=True)


def test_physical_identity_and_scan_events():
    root=tempfile.mkdtemp()
    try:
        primary=os.path.join(root,"original.bin")
        alias=os.path.join(root,"alias.bin")
        independent=os.path.join(root,"independent.bin")
        os.mkdir(os.path.join(root,"empty"))
        with open(primary,"wb") as h: h.write(b"same bytes")
        hardlink_supported=True
        try:
            os.link(primary,alias)
        except (OSError, NotImplementedError):
            hardlink_supported=False
            shutil.copy2(primary,alias)
        shutil.copy2(primary,independent)
        stats=fo_scan.ScanStatistics()
        records=list(fo_scan.scan(root,statistics=stats))
        by_name={r.file_name:r for r in records}
        if hardlink_supported:
            p=by_name["original.bin"]; a=by_name["alias.bin"]; i=by_name["independent.bin"]
            identity_ok=(p.volume_serial is not None and p.file_index is not None
                         and p.volume_serial == a.volume_serial
                         and p.file_index == a.file_index
                         and (p.hard_link_count or 0) >= 2
                         and (p.volume_serial,p.file_index) != (i.volume_serial,i.file_index))
        else:
            identity_ok=True
        check("B.PHYS", "physical identity distinguishes aliases from copies",
              identity_ok, "hardlink fixture" if hardlink_supported else "hardlinks unavailable")

        conn, project=new_project(root)
        cur=conn.execute(
            "INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,schema_version) "
            "VALUES(1,'events','scan','running',?,'B6.1',7)", (fo_state.utc_now(),))
        run_id=cur.lastrowid; conn.commit()
        ing=fo_inventory_records.RecordIngestor(conn,run_id)
        ing.ingest_records(iter(records),root,"MDY",scan_errors=[],path_events=stats.path_events)
        for sid in ing.scan_ids.values():
            conn.execute("UPDATE inventory_scan SET status='completed' WHERE inventory_scan_id=?",(sid,))
        conn.commit()
        events=conn.execute("SELECT event_kind,relative_path FROM scan_path_event").fetchall()
        check("B.PATH", "empty directories survive as explicit scan facts",
              any(r[0] == "empty_directory" and "empty" in r[1] for r in events),
              "%d path events" % len(events))
        conn.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_hash_state_distinguishes_intentional_unhashed():
    corpus=tempfile.mkdtemp()
    try:
        with open(os.path.join(corpus,"unique.bin"),"wb") as h: h.write(b"unique-size")
        conn, project=new_project(corpus)
        do_run(conn,corpus,"hash-state",hash_files=False)
        scan_id=_latest_scan_ids(conn)[0]
        row=conn.execute("SELECT current_observation_id FROM file_state WHERE current_scan_id=?",(scan_id,)).fetchone()
        run_id=conn.execute("SELECT MAX(run_id) FROM run").fetchone()[0]
        conn.execute(
            "INSERT INTO hash_measurement(project_id,file_observation_id,run_id,measurement_mode,algorithm,size_bytes,hash_status,measured_utc) "
            "VALUES(1,?,?,'selective','SHA256',?,'size_unique',?)",
            (row[0],run_id,11,fo_state.utc_now()))
        conn.commit(); fo_state.attach_content(conn,run_id); conn.commit()
        state=conn.execute("SELECT hash_status,hash_measurement_mode,content_id FROM file_state").fetchone()
        check("B.NULL", "intentional no-hash state differs from unknown/failure",
              state[0] == "size_unique" and state[1] == "selective" and state[2] is None,
              "%s/%s content=%s" % tuple(state))
        conn.close()
    finally:
        shutil.rmtree(corpus, ignore_errors=True)




def test_hash_measurement_upsert():
    root=tempfile.mkdtemp()
    try:
        f=os.path.join(root,"x.bin")
        with open(f,"wb") as h: h.write(b"abcdef")
        conn, project=new_project(root)
        do_run(conn,root,"upsert-scan",hash_files=False)
        obs=conn.execute("SELECT current_observation_id FROM file_state").fetchone()[0]
        run_id=conn.execute(
            "INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,schema_version) "
            "VALUES(1,'upsert-hash','duplicate','running',?,'B6.1',7)",
            (fo_state.utc_now(),)).lastrowid
        conn.commit()
        ing=fo_hashes.HashIngestor(conn,run_id)
        ing.mode='selective'
        base={
            "file_observation_id":obs,"content_id":None,"size_bytes":6,
            "size_group_id":"S00000001","partial_hash":"a"*64,
            "partial_hash_bytes":6,"partial_group_id":"P00000001",
            "partial_covers_file":1,"full_hash":None,"hash_status":"hashed",
            "alpha_final_status":None,"needed_full_hash":0,
            "measured_utc":fo_state.utc_now(),"source_artifact":"engine",
            "error_kind":None,"error_message":None}
        ing._write_measurements([dict(base)])
        refined=dict(base); refined["full_hash"]="b"*64
        ing._write_measurements([refined]); conn.commit()
        rows=conn.execute(
            "SELECT COUNT(*),MAX(full_hash) FROM hash_measurement "
            "WHERE run_id=? AND file_observation_id=? AND measurement_mode='selective'",
            (run_id,obs)).fetchone()
        check("D.F001", "hash measurement refinement is one-row idempotent UPSERT",
              rows[0] == 1 and rows[1] == "b"*64,
              "rows=%d refined=%s" % (rows[0], bool(rows[1])))
        conn.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)

def test_unbound_export_is_error():
    root=tempfile.mkdtemp()
    try:
        conn, project=new_project(root)
        cur=conn.execute(
            "INSERT INTO run(project_id,run_uid,run_kind,status,started_utc,app_version,schema_version,run_folder) "
            "VALUES(1,'unbound-export','scan','completed',?,'B6.1',7,NULL)",
            (fo_state.utc_now(),))
        run_id=cur.lastrowid; conn.commit()
        raised=False
        detail=''
        try:
            fo_exports.Exporter(conn, run_id).inventory_scan_ids()
        except fo_exports.ExportError as exc:
            raised=True; detail=str(exc)
        check("A.F004", "unbound run cannot masquerade as empty export",
              raised and "no run_folder binding" in detail, detail[:80])
        conn.close()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_content_lookup_uses_project_index():
    project=os.path.join(tempfile.mkdtemp(), "P")
    os.makedirs(project)
    fo_db.init_project(project, "P", source_roots=[tempfile.mkdtemp()])
    conn,_=fo_db.open_project(project)
    plan=" ".join(str(r[3]) for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT content_id,sha256,size_bytes FROM content "
        "WHERE project_id=1 AND sha256 IN (?)", ("0"*64,)))
    ok=("SCAN content" not in plan and ("INDEX" in plan or "SEARCH content" in plan))
    check("D.F005", "content lookup uses project-scoped index", ok, plan[:90])
    conn.close()

def test_analyzer_registry_contract():
    """C.F003/C.F004: one analyzer identity/extension contract."""
    engine_keys = {a.key for a in fo_analyzer_engine.ADAPTERS}
    spec_keys = set(fo_analyzers.SPEC_BY_KEY)
    export_keys = set(fo_exports.ANALYZER_ARTIFACTS)
    conn, project = new_project(tempfile.mkdtemp())
    try:
        db_keys = {r[0] for r in conn.execute("SELECT analyzer_key FROM analyzer")}
    finally:
        conn.close()
        shutil.rmtree(os.path.dirname(project), ignore_errors=True)
    key_ok = engine_keys == spec_keys == export_keys == db_keys
    check("C.F003", "analyzer registries contain the same keys", key_ok,
          "%d analyzers" % len(engine_keys))

    mismatches = []
    for adapter in fo_analyzer_engine.ADAPTERS:
        module = adapter.module()
        if module is None:
            continue
        actual = set(getattr(module, adapter.extensions_attr)) if adapter.extensions_attr else set()
        if actual != set(adapter.declared_extensions):
            mismatches.append(adapter.key + ":extensions")
        if adapter.exclude_attr:
            excluded = set(getattr(module, adapter.exclude_attr))
            if excluded != set(adapter.declared_exclusions):
                mismatches.append(adapter.key + ":exclusions")
    check("C.F004", "loaded analyzer extension contracts match declarations",
          not mismatches, "no drift" if not mismatches else ", ".join(mismatches))

# ---------------------------------------------------------------------------
# Upgrade path
# ---------------------------------------------------------------------------

def test_schema_version():
    project = os.path.join(tempfile.mkdtemp(), "P")
    os.makedirs(project)
    fo_db.init_project(project, "P", source_roots=[tempfile.mkdtemp()])
    conn, _ = fo_db.open_project(project)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    # The expected version is whatever fo_db declares, not a literal. This
    # asserted `version == 7` until the P2.9.1 overlay added migration 008 and
    # moved APP_SCHEMA_VERSION to 8 -- at which point a correct schema started
    # failing a test whose real subject is "a new project lands on the current
    # schema, intact, with the B6.1 reconciliation tables present".
    expected = fo_db.APP_SCHEMA_VERSION
    check("007", "new project lands on the current schema, with B6.1 state",
          version == expected and integrity == "ok"
          and {"file_state", "archive_summary"} <= tables,
          "user_version %d (expected %d), integrity %s"
          % (version, expected, integrity))
    conn.close()


# ---------------------------------------------------------------------------
# B7.1 -- what the adversarial round (P2_Stress_Test, 2026-09-19/20) found
# ---------------------------------------------------------------------------

def _scan_with_directory_error(root, cursor, statistics, denied):
    """The walk, plus a directory error appended when the generator is
    exhausted -- the shape the real walker produces for a denied folder."""
    for record in fo_scan.scan(root, cursor, statistics):
        yield record
    statistics.errors.append(fo_scan.ScanError(
        fo_scan.DIRECTORY_ACCESS_ERROR, denied, "Access is denied"))


def test_inaccessible_stays_inaccessible_on_rescan():
    r"""A denied directory seen on two scans is 'inaccessible' after both.

    The unchanged-row refresh wrote state='present' unconditionally, so
    the second scan turned the directory into a present, sizeless file
    that the hash pass then received and reported as an error.
    """
    corpus = build_corpus(tempfile.mkdtemp(), files=20)
    denied = os.path.join(corpus, "denied")
    conn, _project = new_project(corpus)
    for label in ("r0", "r1"):
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version) VALUES (1,?,'scan','running',?,'B7.1',8)",
            (label, fo_state.utc_now()))
        run_id = cursor.lastrowid
        conn.commit()
        statistics = fo_scan.ScanStatistics()
        ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
        summary = ingestor.ingest_records(
            _scan_with_directory_error(corpus, 1, statistics, denied), corpus, "MDY",
            scan_errors=lambda: statistics.errors, path_events=lambda: statistics.path_events)
        ingestor.finish()
        for scan_id in ingestor.scan_ids.values():
            conn.execute("UPDATE inventory_scan SET status='completed' "
                         "WHERE inventory_scan_id=?", (scan_id,))
        conn.commit()
    row = conn.execute(
        "SELECT fs.state, fs.size_bytes FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        "WHERE fp.file_name='denied'").fetchone()
    entries = fo_hash_records.load_entries(conn, _latest_scan_ids(conn))
    fed = [e for e in entries if e.path.endswith("denied")]
    check("B7.1", "re-scanned denied directory stays inaccessible",
          row is not None and row["state"] == "inaccessible" and not fed
          and summary["unchanged"] == 21,
          "state=%s fed_to_hasher=%d unchanged=%s"
          % (row["state"] if row else None, len(fed), summary["unchanged"]))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


def test_estimator_samples_by_size():
    r"""The throughput sample is the largest files, read at most 8 MB each;
    the per-file cost is measured on the smallest, and calibrate() reports
    both. Path-ordered sampling of tiny files put a 12-minute Full Run at
    2 hours 37 minutes."""
    corpus = tempfile.mkdtemp()
    with open(os.path.join(corpus, "big.bin"), "wb") as handle:
        handle.write(os.urandom(1024) * (20 * 1024))            # 20 MB
    for i in range(30):
        with open(os.path.join(corpus, "a%02d.txt" % i), "wb") as handle:
            handle.write(b"x" * (i + 1))
    conn, _project = new_project(corpus)
    do_run(conn, corpus, "r0", hash_files=False)
    scan_ids = _latest_scan_ids(conn)

    big_first = fo_estimates.sample_candidates(conn, scan_ids)
    small_first = fo_estimates.sample_small_candidates(conn, scan_ids)
    rate, files_used, bytes_read = fo_estimates.measure_throughput(big_first)
    per_file, small_used = fo_estimates.measure_per_file(small_first)
    values = fo_estimates.calibrate(conn, scan_ids, drive_type="Fixed")
    check("B7.1", "throughput sample is largest-first, capped per file",
          big_first and big_first[0][1] == 20 * 1024 * 1024
          and small_first and small_first[0][1] == 1
          and bytes_read <= fo_estimates.PER_FILE_READ_CAP + 31 * 31
          and files_used >= 1 and rate and rate > 0,
          "first=%s bytes_read=%d files=%d"
          % (big_first[0][1] if big_first else None, bytes_read, files_used))
    check("B7.1", "per-file cost is measured and floored",
          per_file is not None and per_file > 0 and small_used == 15
          and values["_per_file_measured"] is not None
          and values["_small_files_used"] == 15
          and values["_per_file_seconds"] >= fo_estimates.DEFAULT_PER_FILE_SECONDS,
          "measured=%s used=%d per_file=%s"
          % (per_file, small_used, values["_per_file_seconds"]))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


def test_candidates_csv_and_drift_baseline():
    r"""PotentialDuplicates.csv is rendered from the outcome (the stage was
    always NO_APPLICABLE_FILES without it), and the drift baseline is read
    from PreliminaryInventory.csv rather than from the engine's own input."""
    import RunCoordinator
    import fo_hash_engine

    entries = [fo_hash_engine.FileEntry(key=i, db_id=i, path="C:\\r\\f%d.bin" % i,
                                        size=[5, 5, 9, 5, 7][i]) for i in range(5)]
    outcome = fo_hash_engine.EngineOutcome("selective", 65536)
    outcome.results = [fo_hash_engine.HashResult(e) for e in entries]
    by_key = {r.key: r for r in outcome.results}
    candidates, group_count = fo_hash_engine.select_size_candidates(entries)
    for group_id, entry in candidates:
        by_key[entry.key].size_group_id = group_id
    out_dir = tempfile.mkdtemp()
    count = RunCoordinator.RunCoordinator._write_candidates_csv(outcome, out_dir)
    csv_path = os.path.join(out_dir, "PotentialDuplicates.csv")
    with open(csv_path, "r", encoding="utf-8-sig") as handle:
        header = handle.readline().strip()
    empty = fo_hash_engine.EngineOutcome("selective", 65536)
    empty.results = []
    none_written = RunCoordinator.RunCoordinator._write_candidates_csv(
        empty, os.path.join(out_dir, "none"))
    check("B7.1", "candidate list written from the outcome; none when empty",
          count == 3 and group_count == 1 and os.path.isfile(csv_path)
          and header == '"DB_ID","FileName","Directory","Path","Length","SizeGroupID"'
          and none_written == 0
          and not os.path.exists(os.path.join(out_dir, "none", "PotentialDuplicates.csv")),
          "rows=%d groups=%d header=%s" % (count, group_count, header))

    prelim = os.path.join(out_dir, "PreliminaryInventory.csv")
    fo_exports.write_inventory_csv(prelim, (
        [i, "f%d" % i, ".bin", "C:\\r", "C:\\r\\f%d" % i, size, "", "", "",
         "Archive", "False", "False", 0, 10]
        for i, size in enumerate([5, 5, 9, 5, 7])))
    totals = RunCoordinator.RunCoordinator._preliminary_totals(None, prelim, 0, 0)
    absent = RunCoordinator.RunCoordinator._preliminary_totals(
        None, os.path.join(out_dir, "missing.csv"), 0, 0)
    check("B7.1", "drift baseline is the preliminary CSV, or honestly absent",
          totals == (5, 31) and absent == (None, None),
          "totals=%s absent=%s" % (totals, absent))
    shutil.rmtree(out_dir, ignore_errors=True)


def test_diagnostic_prefix_stripping_handles_repr():
    text = ("cannot identify image file '" + "\\" * 4 + "?" + "\\" * 2 + "C:"
            + "\\" * 2 + "Users" + "\\" * 2 + "a.png'")
    cleaned = fo_analyzer_engine.normalize_diagnostic_text(text)
    plain = fo_analyzer_engine.normalize_diagnostic_text(
        "Package not found at '\\\\?\\C:\\x\\fake.docx'")
    check("B7.1", "\\\\?\\ stripped from repr()-quoted paths too",
          cleaned == "cannot identify image file 'C:\\\\Users\\\\a.png'"
          and plain == "Package not found at 'C:\\x\\fake.docx'",
          "got %r" % cleaned)


def test_drive_type_vocabulary():
    words = {"Removable", "Fixed", "Network", "CDROM", "RamDisk", "Unknown"}
    system = os.environ.get("SystemDrive", "C:") + "\\"
    got = win_meta.drive_type(system)
    check("B7.1", "drive_type names the drive, not just Network/Unknown",
          got in words and (sys.platform != "win32" or got != "Unknown"),
          "%s -> %s" % (system, got))


def test_rescan_exports_are_complete():
    r"""A second scan under history.mode='changes' produces no new observations
    for unchanged files. The run-folder exports selected by this scan's
    observations and so listed only what had changed: Scan again wrote a
    3-row PreliminaryInventory.csv and Fingerprint again a 3-row
    FullHashInventory.csv for 52,200 files. Both now read the current
    projection; and an exhaustive pass no longer truncates and removes the
    Pre-Scan's PotentialDuplicates.csv."""
    import fo_hash_engine
    corpus = build_corpus(tempfile.mkdtemp(), files=30, dup_every=10)
    conn, _project = new_project(corpus)

    def scan_and_hash(folder):
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version, run_folder) VALUES (1,?,'prescan','running',?,'B7.1',8,?)",
            (folder + "-scan", fo_state.utc_now(), folder))
        scan_run = cursor.lastrowid
        ingestor = fo_inventory_records.RecordIngestor(conn, scan_run)
        ingestor.ingest_records(fo_scan.scan(corpus), corpus, "MDY", scan_errors=[])
        ingestor.finish()
        conn.commit()
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version, run_folder) VALUES (1,?,'exhaustive_identity','running',?,'B7.1',8,?)",
            (folder + "-hash", fo_state.utc_now(), folder))
        hash_run = cursor.lastrowid
        hasher = fo_hash_records.HashRecordIngestor(conn, hash_run, partial_hash_bytes=65536)
        entries = fo_hash_records.load_entries(conn, hasher.bind_scans(corpus))
        outcome = fo_hash_engine.HashEngine().run_exhaustive(entries)
        hasher.ingest_exhaustive_records(outcome)
        hasher.finish()
        conn.commit()
        out = tempfile.mkdtemp()
        with open(os.path.join(out, "PotentialDuplicates.csv"), "w") as handle:
            handle.write("from the pre-scan")
        fo_exports.export_run_inventory(conn, scan_run, os.path.join(out, "PreliminaryInventory.csv"))
        result = fo_exports.Exporter(conn, run_id=hash_run).export_hash_stage(out, mode="exhaustive")
        with open(os.path.join(out, "PreliminaryInventory.csv"), encoding="utf-8-sig") as handle:
            inventory_rows = sum(1 for _ in handle) - 1
        kept = os.path.isfile(os.path.join(out, "PotentialDuplicates.csv"))
        shutil.rmtree(out, ignore_errors=True)
        return len(entries), inventory_rows, result["row_counts"].get("full_hash_inventory", 0), kept

    first = scan_and_hash("F1")
    second = scan_and_hash("F2")
    check("B7.1", "second-scan exports list the whole inventory",
          first[:3] == (30, 30, 30) and second[:3] == (30, 30, 30),
          "first (entries, inventory csv, hash csv)=%s second=%s" % (first[:3], second[:3]))
    check("B7.1", "exhaustive export leaves the pre-scan's candidate list alone",
          first[3] and second[3], "kept=%s/%s" % (first[3], second[3]))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


def test_multi_root_report_qualifies_top_level_folders():
    r"""Two roots that both hold a "Documents" folder and root-level files
    are reported apart, as ROOT\Documents and ROOT\(root); a single root
    keeps R6's bare names."""
    first, second = tempfile.mkdtemp(), tempfile.mkdtemp()
    for folder in (first, second):
        os.makedirs(os.path.join(folder, "Documents"))
        for name in ("Documents\\a.txt", "top.txt"):
            with open(os.path.join(folder, name), "w") as handle:
                handle.write("x")
    shared = fo_scan.ScanStatistics()
    shared.root_count = 2
    for root in (first, second):
        for _record in fo_scan.scan(root, 1, shared):
            pass
    single = fo_scan.ScanStatistics()
    for _record in fo_scan.scan(first, 1, single):
        pass
    leaves = [os.path.basename(first), os.path.basename(second)]
    shown = sorted(entry[0] for entry in shared.by_top_level.values())
    expected = sorted(["%s\\Documents" % leaf for leaf in leaves]
                      + ["%s\\(root)" % leaf for leaf in leaves])
    check("B7.1", "multi-root report keeps same-named top-level folders apart",
          shown == expected and sorted(e[0] for e in single.by_top_level.values())
          == ["(root)", "Documents"],
          "multi=%s single=%s" % (shown, sorted(e[0] for e in single.by_top_level.values())))
    shutil.rmtree(first, ignore_errors=True)
    shutil.rmtree(second, ignore_errors=True)


def test_attribute_words_render_and_compare():
    r"""OneDrive's bits render by name, the remainder as hex; every value .NET
    could name renders as before; and a row recorded under the old bare
    number does not read as changed under the new spelling."""
    rendered = [win_meta.format_file_attributes(v)
                for v in (32, 0x2 | 0x20, 524320, 1572902, 0x20 | 0x1000000, 0)]
    round_trip = all(win_meta.parse_file_attributes(win_meta.format_file_attributes(v)) == v
                     for v in (1, 32, 524320, 1572902, 0x20 | 0x1000000, 0x7FFFFF))
    check("B7.1", "attribute words render by name and round-trip",
          rendered == ["Archive", "Hidden, Archive", "Archive, Pinned",
                       "Hidden, System, Archive, Pinned, Unpinned",
                       "Archive, 0x1000000", "0"]
          and win_meta.parse_file_attributes("524320") == 524320 and round_trip,
          "%s" % (rendered,))
    check("B7.1", "a change of spelling is not a change of the file",
          not win_meta.attributes_differ("524320", "Archive, Pinned")
          and win_meta.attributes_differ("Archive", "Hidden, Archive")
          and win_meta.attributes_differ("odd", "other")
          and not win_meta.attributes_differ("odd", "odd"),
          "")


def test_case_twin_folds_stably_and_visibly():
    r"""Two records whose names differ only by case (a case-sensitive directory,
    A-014) resolve to one row. The first keeps the row; the second is
    counted as folded and recorded as a FOLDED_CASE_TWIN event; and a
    re-scan reports nothing modified, where the row used to flip between
    the twins on every scan."""
    corpus = build_corpus(tempfile.mkdtemp(), files=3)
    conn, _project = new_project(corpus)

    def with_twin(root, cursor, statistics):
        for record in fo_scan.scan(root, cursor, statistics):
            yield record
            if record.file_name == "f0001.bin":
                fields = record.as_dict()
                fields.update(file_name="F0001.BIN", legacy_db_id=record.legacy_db_id + 1000,
                              path=record.path[:-len("f0001.bin")] + "F0001.BIN",
                              size_bytes=(record.size_bytes or 0) + 7)
                yield fo_scan.InventoryRecord(**fields)

    summaries, events = [], []
    for label in ("r0", "r1"):
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version) VALUES (1,?,'scan','running',?,'B7.1',8)",
            (label, fo_state.utc_now()))
        run_id = cursor.lastrowid
        conn.commit()
        statistics = fo_scan.ScanStatistics()
        ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
        summaries.append(ingestor.ingest_records(
            with_twin(corpus, 1, statistics), corpus, "MDY", scan_errors=[]))
        ingestor.finish()
        for scan_id in ingestor.scan_ids.values():
            conn.execute("UPDATE inventory_scan SET status='completed' "
                         "WHERE inventory_scan_id=?", (scan_id,))
        conn.commit()
        events.append(conn.execute(
            "SELECT COUNT(*) FROM event WHERE run_id=? AND error_type='FOLDED_CASE_TWIN'",
            (run_id,)).fetchone()[0])
    rows = conn.execute("SELECT COUNT(*) FROM file_state").fetchone()[0]
    kept = conn.execute(
        "SELECT fp.file_name, fs.size_bytes, o.size_bytes FROM file_state fs "
        "JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
        "JOIN file_observation o ON o.file_observation_id=fs.current_observation_id "
        "WHERE lower(fp.file_name)='f0001.bin'").fetchone()
    on_disk = os.path.getsize(os.path.join(corpus, "d01", "f0001.bin"))
    check("B7.1", "case-only twin folds into the first record's row, visibly",
          rows == 3 and kept[:] == ("f0001.bin", on_disk, on_disk)
          and [s["folded"] for s in summaries] == [1, 1] and events == [1, 1]
          and any("folded" in w for w in ingestor.warnings),
          "rows=%d kept=%s folded=%s events=%s" % (rows, kept[:] if kept else None,
                                                    [s["folded"] for s in summaries], events))
    check("B7.1", "a re-scan of the folded pair reports nothing modified",
          summaries[1]["changed"] == 0 and summaries[1]["unchanged"] == 3,
          "changed=%s unchanged=%s" % (summaries[1]["changed"], summaries[1]["unchanged"]))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


def test_link_is_never_opened_by_the_hash_engine():
    r"""B7.2 (C-011). A file symbolic link or junction is 'SkippedLink' in
    both pipelines -- no digest, no group -- and its target keeps its own
    verdict. Until B7.2 the engine opened the link, Windows resolved it, and
    the link joined the target's duplicate group offering the target's bytes
    as reclaimable."""
    import fo_hash_engine
    corpus = tempfile.mkdtemp()
    for name, body in (("a.txt", b"the same twenty-nine bytes!!\n"), ("b.txt", b"the same twenty-nine bytes!!\n")):
        with open(os.path.join(corpus, name), "wb") as handle:
            handle.write(body)
    entries = [fo_hash_engine.FileEntry(1, 1, os.path.join(corpus, "a.txt"), 29),
               fo_hash_engine.FileEntry(2, 2, os.path.join(corpus, "b.txt"), 29),
               # the link: size 0, is_link -- the engine must not even stat it
               fo_hash_engine.FileEntry(3, 3, os.path.join(corpus, "link.txt"), 0, is_link=True)]
    ex = fo_hash_engine.HashEngine().run_exhaustive(entries)
    se = fo_hash_engine.HashEngine().run_selective(entries)
    status = {r.key: r.final_status for r in ex.results}
    check("B7.2", "a link is SkippedLink in the Full Run and grouped with nothing",
          status[3] == fo_hash_engine.STATUS_SKIPPED_LINK and status[1] == status[2] == "ConfirmedDuplicate"
          and len(ex.groups) == 1 and all(m.key != 3 for g in ex.groups for m in g.members)
          and fo_hashes._EXHAUSTIVE_STATUS[fo_hash_engine.STATUS_SKIPPED_LINK] == "skipped_link",
          "%s groups=%d" % (status, len(ex.groups)))
    sstatus = {r.key: r.final_status for r in se.results}
    check("B7.2", "a link is SkippedLink in the Duplicate Run and never a size candidate",
          sstatus[3] == fo_hash_engine.STATUS_SKIPPED_LINK and se.candidate_count == 2
          and fo_hashes._SELECTIVE_STATUS[fo_hash_engine.STATUS_SKIPPED_LINK] == "skipped_link",
          "%s candidates=%d" % (sstatus, se.candidate_count))
    # And the loader recognises one from file_state: reparse point + link tag.
    check("B7.2", "load_entries marks a symlink-tagged reparse point as a link",
          win_meta.is_link_tag(0xA000000C) and win_meta.is_link_tag(0xA0000003)
          and not win_meta.is_link_tag(0x9000601A) and not win_meta.is_link_tag(None), "")
    shutil.rmtree(corpus, ignore_errors=True)


def test_size_changed_since_listing_is_reported_not_recorded():
    r"""B7.2 (I-005). A file whose size at open time is not the size listed
    gets CHANGED SINCE LISTING and no digest, in place of the new bytes'
    digest beside the old size."""
    import fo_hash_engine
    corpus = tempfile.mkdtemp()
    path = os.path.join(corpus, "f.txt")
    with open(path, "wb") as handle:
        handle.write(b"x" * 232)
    entries = [fo_hash_engine.FileEntry(1, 1, path, 51)]          # listed at 51, now 232
    ex = fo_hash_engine.HashEngine().run_exhaustive(entries)
    r = ex.results[0]
    check("B7.2", "a size that changed since listing is an error with no digest",
          r.final_status == "Error" and r.error_kind == fo_hash_engine.ERROR_CHANGED_SINCE_LISTING
          and r.full_hash is None and "51" in r.error_message and "232" in r.error_message
          and len(ex.errors) == 1,
          "status=%s kind=%s hash=%s" % (r.final_status, r.error_kind, r.full_hash))
    ok = fo_hash_engine.HashEngine().run_exhaustive([fo_hash_engine.FileEntry(1, 1, path, 232)]).results[0]
    check("B7.2", "the same file at its listed size hashes normally",
          ok.final_status == "UniqueByHash" and ok.full_hash, ok.final_status)
    shutil.rmtree(corpus, ignore_errors=True)


def test_unlistable_folder_leaves_its_files_unverified():
    r"""B7.2 (D-003c). Files under a directory that exists but could not be
    listed are 'unverified' after the scan, not 'missing'; a scan that lists
    it again finds them 'reappeared'. Files under a directory the OS says is
    gone are 'missing'."""
    corpus = build_corpus(tempfile.mkdtemp(), files=40)      # d00..d15, 40 files
    conn, _project = new_project(corpus)
    do_run(conn, corpus, "r0", hash_files=False)

    def scan_with(errors, label):
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version) VALUES (1,?,'scan','running',?,'B7.2',8)",
            (label, fo_state.utc_now()))
        run_id = cursor.lastrowid
        conn.commit()
        statistics = fo_scan.ScanStatistics()
        skip = {os.path.normcase(p) for p, _a in errors}

        def walk():
            for record in fo_scan.scan(corpus, 1, statistics):
                if os.path.normcase(os.path.dirname(record.path)) in skip:
                    continue                      # the walker could not list these
                yield record
            for path, absent in errors:
                statistics.errors.append(fo_scan.ScanError(
                    fo_scan.DIRECTORY_ACCESS_ERROR, path, "Access is denied", absent=absent))
        ingestor = fo_inventory_records.RecordIngestor(conn, run_id)
        summary = ingestor.ingest_records(walk(), corpus, "MDY",
                                          scan_errors=lambda: statistics.errors,
                                          path_events=lambda: statistics.path_events)
        ingestor.finish()
        for scan_id in ingestor.scan_ids.values():
            conn.execute("UPDATE inventory_scan SET status='completed' WHERE inventory_scan_id=?", (scan_id,))
        conn.commit()
        return summary

    def states_under(folder):
        return sorted(r[0] for r in conn.execute(
            "SELECT fs.state FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
            "WHERE fp.relative_path LIKE ? ESCAPE '!'", (folder + "\\%",)))

    denied, gone = os.path.join(corpus, "d03"), os.path.join(corpus, "d05")
    summary = scan_with([(denied, False), (gone, True)], "r1")
    present = conn.execute("SELECT COUNT(*) FROM file_state WHERE state='present'").fetchone()[0]
    check("B7.2", "files under an unlistable folder are unverified; under a gone folder, missing",
          set(states_under("d03")) == {"unverified"} and set(states_under("d05")) == {"missing"}
          and summary["unverified"] == len(states_under("d03")) and summary["vanished"] == len(states_under("d05"))
          and present == 40 - len(states_under("d03")) - len(states_under("d05")),
          "d03=%s d05=%s summary unverified=%s vanished=%s present=%d"
          % (states_under("d03"), states_under("d05"), summary["unverified"], summary["vanished"], present))
    summary = scan_with([], "r2")
    reappeared = conn.execute(
        "SELECT COUNT(*) FROM file_observation o JOIN file_path fp ON fp.file_path_id=o.file_path_id "
        "WHERE o.change_kind='reappeared' AND fp.relative_path LIKE 'd03\\%' ESCAPE '!'").fetchone()[0]
    check("B7.2", "once the folder lists again its files are present, as 'reappeared'",
          set(states_under("d03")) == {"present"} and reappeared == len(states_under("d03"))
          and summary["unverified"] == 0,
          "d03=%s reappeared=%d" % (states_under("d03"), reappeared))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


def test_terminal_logs_and_binary_members():
    r"""B7.2 (Y-020c, Y-022b). Terminal escape sequences are stripped where
    text is decoded, so a colourised log is text and its words survive; and
    an archive member named .txt whose bytes are binary meets the same gate
    as a top-level file."""
    import fo_extractors
    esc = chr(27)
    colour = "".join("%s[32m2026-01-%02d%s[0m %s[1mINFO%s[0m step %d finished\n"
                     % (esc, i % 28 + 1, esc, esc, esc, i) for i in range(60))
    colour += "%s[33mWARN%s[0m marker phrase here\n" % (esc, esc)
    text, _enc = fo_text.decode_bytes(colour.encode("utf-8"))
    check("B7.2", "a colourised log decodes to its words and passes the binary gate",
          esc not in text and "INFO step 7 finished" in text and "marker phrase here" in text
          and fo_extractors._looks_like_text(colour.encode("utf-8")),
          repr(text[:60]))
    noise = hashlib.sha256(b"seed").digest() * 3200        # 100 KB, never text
    note = fo_extractors._member_text("noise.txt", noise, 0, {".txt": None})
    plain = fo_extractors._member_text("plain.txt", b"just words\n", 0, {".txt": None})
    check("B7.2", "a binary archive member named .txt yields a one-line note, not its bytes",
          note is not None and len(note) < 200 and "binary" in note and plain == "just words\n",
          "note=%r" % (note,))


def test_garbage_pdf_date_empties_one_field():
    r"""B7.2 (E-008b). A /CreationDate pypdf cannot parse leaves one field
    marked unparseable; the file is analysed."""
    import p2_build_acceptance_corpus as builder
    import PDFAnalysis
    pdf = builder.make_pdf("Info dictionary with garbage dates").replace(
        b"trailer\n<</Size", b"trailer\n<</Info<</CreationDate(garbage)/ModDate(D:99999999999999)>>/Size")
    path = os.path.join(tempfile.mkdtemp(), "bad_info.pdf")
    with open(path, "wb") as handle:
        handle.write(pdf)
    try:
        result = PDFAnalysis.analyze_pdf(path)
        ok = result["PageCount"] == "1" and "unparseable" in result["CreationDate"]
        detail = "%s" % (result,)
    except Exception as exc:                                    # noqa: BLE001
        ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
    check("B7.2", "a garbage PDF date empties one field instead of failing the file", ok, detail)
    shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def test_run_finalized_flag():
    r"""Migration 006's anti-false-completion flag is finally written: 1 for a
    terminal status the process reached, 0 for a pause and for a run a later
    launch reconciled."""
    import fo_runs
    corpus = tempfile.mkdtemp()
    conn, _project = new_project(corpus)
    ids = []
    for label in ("done", "paused", "died"):
        cursor = conn.execute(
            "INSERT INTO run (project_id, run_uid, run_kind, status, started_utc,"
            " app_version, schema_version) VALUES (1,?,'scan','running',?,'B7.1',8)",
            (label, fo_state.utc_now()))
        ids.append(cursor.lastrowid)
    fo_runs.finish_run(conn, ids[0], "completed_with_warnings")
    fo_runs.finish_run(conn, ids[1], "paused")
    fo_runs.finish_run(conn, ids[2], "interrupted", notes="reconciled", finalized=False)
    conn.commit()
    flags = [conn.execute("SELECT status, finalized FROM run WHERE run_id=?",
                          (i,)).fetchone()[:] for i in ids]
    check("B7.1", "run.finalized written with the terminal status",
          flags == [("completed_with_warnings", 1), ("paused", 0), ("interrupted", 0)],
          "%s" % (flags,))
    conn.close()
    shutil.rmtree(corpus, ignore_errors=True)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="B6 regression suite -- one check per B5 finding.")
    parser.add_argument("--quick", action="store_true",
                        help="Skip the slower measurements.")
    args = parser.parse_args()

    print("=" * 78)
    print(" THE FILE ORGANIZER -- B6.1 REGRESSION SUITE")
    print(" A-F reconciliation + E/F regression + B6.1 integration checks.")
    print("=" * 78)
    print()

    print(" Schema and upgrade")
    test_schema_version()
    test_indexes_present()
    print()

    print(" A-D reconciliation and B6.1 integration")
    test_extension_contract()
    test_hash_measurement_upsert()
    test_unbound_export_is_error()
    test_content_lookup_uses_project_index()
    test_text_decoding_and_content_addressing()
    test_inventory_finish_tracks_unchanged_files()
    test_repeat_scan_current_inputs_and_estimator()
    test_physical_identity_and_scan_events()
    test_hash_state_distinguishes_intentional_unhashed()
    test_analyzer_registry_contract()
    print()

    print(" Determinism (B5-F)")
    test_enumeration_determinism()
    test_report_tie_breaking()
    test_root_order_does_not_matter()
    test_timestamps_are_utc()
    test_export_timestamps_locale_independent()
    print()

    print(" Integrity and state (B5-H constraints)")
    test_change_and_vanish_detection()
    test_missing_root_is_not_empty_root()
    test_multi_root_ingest_keeps_earlier_roots()
    test_inaccessible_cap_is_recorded()
    print()

    print(" B7.1 -- the adversarial round")
    test_inaccessible_stays_inaccessible_on_rescan()
    test_estimator_samples_by_size()
    test_candidates_csv_and_drift_baseline()
    test_diagnostic_prefix_stripping_handles_repr()
    test_drive_type_vocabulary()
    test_rescan_exports_are_complete()
    test_multi_root_report_qualifies_top_level_folders()
    test_attribute_words_render_and_compare()
    test_case_twin_folds_stably_and_visibly()
    test_run_finalized_flag()
    print()

    print(" B7.2 -- the rest of the adversarial round's defects")
    test_link_is_never_opened_by_the_hash_engine()
    test_size_changed_since_listing_is_reported_not_recorded()
    test_unlistable_folder_leaves_its_files_unverified()
    test_terminal_logs_and_binary_members()
    test_garbage_pdf_date_empties_one_field()
    print()

    print(" Scalability (B5-E)")
    test_word_count_equivalence()
    test_export_dialects_unchanged()
    if not args.quick:
        test_word_count_memory()
        test_analyzer_retention()
        test_sink_failure_is_not_success()
        test_export_streaming()
        test_archive_bounds()
        test_history_and_duplicate_query()
    print()

    failed = [r for r in _results if r[2] == FAIL]
    print("=" * 78)
    print(" %d checks, %d passed, %d failed"
          % (len(_results), len(_results) - len(failed), len(failed)))
    if failed:
        print()
        for finding, description, _status, detail in failed:
            print("   FAILED  %-11s %s  %s" % (finding, description, detail))
    print("=" * 78)
    print(" Not evidence about Windows. NTFS behaviour, real locale APIs,")
    print(" long paths, cloud placeholders and network roots remain")
    print(" untested here -- they are B6 acceptance targets.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
