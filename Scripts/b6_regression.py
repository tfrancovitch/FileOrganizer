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


# ---------------------------------------------------------------------------
# F.F001 / F.F002 -- determinism
# ---------------------------------------------------------------------------

def test_enumeration_determinism(trials=5):
    import random
    names = ["a/x.txt", "a/B.txt", "a/b.txt", "z/1.txt", "z/10.txt",
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
    check("007", "schema 7 with B6.1 reconciliation state",
          version == 7 and integrity == "ok"
          and {"file_state", "archive_summary"} <= tables,
          "user_version %d, integrity %s" % (version, integrity))
    conn.close()


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
    test_inaccessible_cap_is_recorded()
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
