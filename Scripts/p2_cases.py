#!/usr/bin/env python3
r"""Assert the Master Matrix cases of a ground-truth file against a project.

Every case in the truth's `cases` list carries an `expected` dict written by
the corpus builder in this program's own vocabulary. This module turns each
key into a query against the project database and reports one named check
per key, so a failure reads "A-004 Deep directory nesting: depth" and
nothing else. It is shared by p2_acceptance (the `Corpus\` project) and
p2_hostile_check (the `Hostile\` project); the checks are the same, the
truth differs.

The keys, and what they assert. Per path (every path the case lists):

  file_state.state              file_state.state == value
  file_observation.status       the current observation's status
  file_observation.error_kind   the current observation's error_kind
  hash_status                   file_state.hash_status == value (or in the list)
  hash.error_kind               the latest hash_measurement's error_kind
  hash.size_bytes               the bytes the latest hash_measurement says it read
  hash.full_hash                the latest hash_measurement's digest (case-insensitive)
  analyzer.status               every non-extraction analyzer row == value;
                                "none" asserts there are no such rows
  analyzer.<key>.status         that analyzer's row (image, pdf, office, ...)
  analyzer.<key>.detail         fields of that analyzer's detail_json == the given values
  extracted_content.status      the extraction row's status; "none" = no row
  file_name                     file_path.file_name == value (byte-exact)
  file_name_length              len(file_name) == value
  extension_key                 file_path.extension_key == value
  depth                         file_path.depth == value (directories under the root)
  path_length_gt                file_state.path_length > value
  size_bytes                    file_state.size_bytes == value
  modified_utc, created_utc     file_state timestamps (a value, or a list of allowed values)
  attributes_has                every .NET attribute name listed is present
  attributes_lacks              none of the names listed is present
  is_reparse_point              file_state.is_reparse_point == value
  reparse_tag                   file_state.reparse_tag == value
  hard_link_count               file_state.hard_link_count == value
  allocated_lt_size             allocated_size_bytes < size_bytes
  marker_indexed                the truth lists a marker for the path with
                                expected_indexed == value (the FTS section
                                proves the hit; this ties the case to it)

Across the case's paths:

  row_count                     how many of the paths exist as file_path rows
  same_physical_object          every found row shares (volume_serial, file_index)
  duplicate_group_members       the current duplicate group holding the first
                                path has this many locations
  reclaimable_bytes             that group's reclaimable bytes
  hard_link_alias_count         that group's hard-link alias count
  not_grouped                   no path is in a current duplicate group
  rows_below                    file_path rows under the first path (a folder)
  archive_members_include       every listed member name has an archive_member
                                row for the first path (raw names, as shipped)
  canary_absent                 none of the listed paths exists on disk
                                (%VAR% expanded) -- nothing escaped an archive
  csv_export_safe               the Files export (Phase2.gui.export_files) has no
                                cell beginning with = + - @ tab or return, and every
                                case file is in it (needs the query engine)
  inventory_scan.status         the latest scan's status
  scan.inaccessible_count       the latest scan's inaccessible count

A key nobody implemented is reported as NOT ASSERTED, never silently passed.

Classified cases. A case always asserts what the builder wrote in
`expected`, which is what the program does today; `classification` says
how to read a pass. SCOPE: a recorded limit, the matrix wants more and
`matrix_expects` says what. DEFECT: the program is wrong and the assertion
pins the wrong behaviour so the suite stays green while the defect is
listed -- the plan's defect table carries it, and the day it is fixed this
check fails and says so, which is when the builder's expectation moves.
Both are printed before the case's checks and listed in CASE_RESULTS.json.

The truth's `scan_expectations` (totals the builder computed: hidden files,
empty folders, paths over 260, links skipped, ...) are compared with the
latest run's PreliminaryReport.txt once, not per case.

Results are also written next to the truth as CASE_RESULTS.json -- the
verification record: per case, per key, PASS or FAIL, with the project and
the time. The truth itself is never written by a check.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

NOT_ASSERTED = "NOT ASSERTED"


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _row(conn, relative_path):
    """file_path + file_state for one relative path, or None."""
    return conn.execute(
        "SELECT fp.file_path_id, fp.relative_path, fp.file_name, fp.extension_key, fp.depth AS fp_depth, "
        "       fs.state, fs.current_observation_id, fs.hash_status, fs.size_bytes, fs.path_length, "
        "       fs.attributes, fs.is_reparse_point, fs.reparse_tag, fs.hard_link_count, "
        "       fs.allocated_size_bytes, fs.volume_serial, fs.file_index, fs.content_id "
        "FROM file_path fp LEFT JOIN file_state fs USING (file_path_id) "
        "WHERE fp.relative_path = ?", (relative_path,)).fetchone()


def _observation(conn, observation_id):
    if observation_id is None:
        return None
    return conn.execute("SELECT status, error_kind, error_message FROM file_observation "
                        "WHERE file_observation_id = ?", (observation_id,)).fetchone()


def _hash_measurement(conn, observation_id):
    if observation_id is None:
        return None
    return conn.execute("SELECT hash_status, error_kind, error_message, size_bytes, full_hash FROM hash_measurement "
                        "WHERE file_observation_id = ? ORDER BY hash_measurement_id DESC LIMIT 1",
                        (observation_id,)).fetchone()


def _analyzer_rows(conn, observation_id):
    """{analyzer_key: status} for the current observation."""
    if observation_id is None:
        return {}
    rows = conn.execute(
        "SELECT a.analyzer_key, r.status FROM analyzer_result r "
        "JOIN analyzer_run ar USING (analyzer_run_id) JOIN analyzer a USING (analyzer_id) "
        "WHERE r.file_observation_id = ? ORDER BY r.analyzer_result_id", (observation_id,)).fetchall()
    return {key: status for key, status in rows}


def _analyzer_detail(conn, observation_id, analyzer_key):
    """The detail_json of one analyzer's row for the current observation, as a dict."""
    if observation_id is None:
        return None
    row = conn.execute(
        "SELECT r.detail_json FROM analyzer_result r JOIN analyzer_run ar USING (analyzer_run_id) "
        "JOIN analyzer a USING (analyzer_id) WHERE r.file_observation_id = ? AND a.analyzer_key = ? "
        "ORDER BY r.analyzer_result_id DESC LIMIT 1", (observation_id, analyzer_key)).fetchone()
    if row is None or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def _extraction_status(conn, observation_id):
    if observation_id is None:
        return None
    row = conn.execute(
        "SELECT ec.status FROM extracted_content ec "
        "JOIN analyzer_result r ON r.analyzer_result_id = ec.analyzer_result_id "
        "WHERE r.file_observation_id = ? ORDER BY ec.extracted_content_id DESC LIMIT 1",
        (observation_id,)).fetchone()
    return row[0] if row else None


def _duplicate_summary(conn, content_id):
    if content_id is None:
        return None
    return conn.execute("SELECT location_count, reclaimable_bytes, hard_link_alias_count, physical_copy_count "
                        "FROM p2_current_duplicate_summary WHERE content_id = ?", (content_id,)).fetchone()


def _latest_scan(conn):
    return conn.execute("SELECT status, observed_count, inaccessible_count FROM inventory_scan "
                        "ORDER BY inventory_scan_id DESC LIMIT 1").fetchone()


def _attribute_names(text):
    return {part.strip() for part in (text or "").split(",") if part.strip()}


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------

def _per_path(conn, truth, key, value, relative_path, row):
    """(ok, detail) for one per-path key, or None if the key is not per-path."""
    if row is None:
        return False, "no file_path row for this path"
    obs_id = row["current_observation_id"]
    if key == "file_state.state":
        return row["state"] == value, f"state={row['state']!r}"
    if key == "file_observation.status":
        obs = _observation(conn, obs_id)
        return obs is not None and obs["status"] == value, f"status={obs['status'] if obs else None!r}"
    if key == "file_observation.error_kind":
        obs = _observation(conn, obs_id)
        return obs is not None and obs["error_kind"] == value, f"error_kind={obs['error_kind'] if obs else None!r}"
    if key == "hash_status":
        allowed = value if isinstance(value, list) else [value]
        return row["hash_status"] in allowed, f"hash_status={row['hash_status']!r}"
    if key == "hash.error_kind":
        hm = _hash_measurement(conn, obs_id)
        return hm is not None and hm["error_kind"] == value, f"hash error_kind={hm['error_kind'] if hm else None!r}"
    if key == "hash.size_bytes":
        hm = _hash_measurement(conn, obs_id)
        return hm is not None and hm["size_bytes"] == value, f"hash size_bytes={hm['size_bytes'] if hm else None}"
    if key == "hash.full_hash":
        hm = _hash_measurement(conn, obs_id)
        got = (hm["full_hash"] or "").upper() if hm else None
        return got == str(value).upper(), f"full_hash={got}"
    if key == "analyzer.status":
        rows = {k: s for k, s in _analyzer_rows(conn, obs_id).items() if k != "content_extraction"}
        if value == "none":
            return not rows, f"analyzer rows={rows}"
        return bool(rows) and all(s == value for s in rows.values()), f"analyzer rows={rows}"
    m = re.fullmatch(r"analyzer\.([a-z_]+)\.status", key)
    if m:
        rows = _analyzer_rows(conn, obs_id)
        got = rows.get(m.group(1))
        if value == "none":
            return got is None, f"{m.group(1)}={got!r}"
        return got == value, f"{m.group(1)}={got!r}"
    m = re.fullmatch(r"analyzer\.([a-z_]+)\.detail", key)
    if m:
        detail = _analyzer_detail(conn, obs_id, m.group(1)) or {}
        wrong = {k: detail.get(k) for k, v in value.items() if str(detail.get(k)) != str(v)}
        return not wrong, f"{m.group(1)} detail differs: {wrong} (have {dict(list(detail.items())[:6])})"
    if key == "extracted_content.status":
        got = _extraction_status(conn, obs_id)
        if value == "none":
            return got is None, f"extracted_content={got!r}"
        allowed = value if isinstance(value, list) else [value]
        return got in allowed, f"extracted_content={got!r}"
    if key == "file_name":
        return row["file_name"] == value, f"file_name={row['file_name']!r}"
    if key == "file_name_length":
        return len(row["file_name"]) == value, f"len={len(row['file_name'])}"
    if key == "extension_key":
        return row["extension_key"] == value, f"extension_key={row['extension_key']!r}"
    if key == "depth":
        return row["fp_depth"] == value, f"depth={row['fp_depth']}"
    if key == "path_length_gt":
        return (row["path_length"] or 0) > value, f"path_length={row['path_length']}"
    if key == "size_bytes":
        return row["size_bytes"] == value, f"size_bytes={row['size_bytes']}"
    if key in ("modified_utc", "created_utc"):
        got = conn.execute(f"SELECT {key} FROM file_state WHERE file_path_id = ?", (row["file_path_id"],)).fetchone()[0]
        allowed = value if isinstance(value, list) else [value]
        return got in allowed, f"{key}={got!r}"
    if key == "attributes_has":
        names = _attribute_names(row["attributes"])
        return all(v in names for v in value), f"attributes={row['attributes']!r}"
    if key == "attributes_lacks":
        names = _attribute_names(row["attributes"])
        return not any(v in names for v in value), f"attributes={row['attributes']!r}"
    if key == "is_reparse_point":
        return (row["is_reparse_point"] or 0) == value, f"is_reparse_point={row['is_reparse_point']}"
    if key == "reparse_tag":
        return row["reparse_tag"] == value, f"reparse_tag={row['reparse_tag']}"
    if key == "hard_link_count":
        return row["hard_link_count"] == value, f"hard_link_count={row['hard_link_count']}"
    if key == "allocated_lt_size":
        alloc, size = row["allocated_size_bytes"], row["size_bytes"]
        ok = alloc is not None and size is not None and (alloc < size) == bool(value)
        return ok, f"allocated={alloc} size={size}"
    if key == "marker_indexed":
        entry = truth.get("fts_markers", {}).get(relative_path)
        return entry is not None and bool(entry.get("expected_indexed")) == bool(value), f"marker={entry}"
    return None


def _across(conn, truth, key, value, paths, rows, engine=None):
    """(ok, detail) for one set-level key, or None if it is not one."""
    found = [r for r in rows if r is not None]
    if key == "row_count":
        return len(found) == value, f"{len(found)} of {len(paths)} paths have rows"
    if key == "same_physical_object":
        ids = {(r["volume_serial"], r["file_index"]) for r in found}
        ok = bool(found) and len(ids) == 1 and None not in next(iter(ids))
        return ok == bool(value), f"identities={sorted(map(str, ids))}"
    if key in ("duplicate_group_members", "reclaimable_bytes", "hard_link_alias_count"):
        if not found:
            return False, "no rows"
        summary = _duplicate_summary(conn, found[0]["content_id"])
        if summary is None:
            return (value == 0 if key != "duplicate_group_members" else value <= 1), "not in a current duplicate group"
        col = {"duplicate_group_members": "location_count", "reclaimable_bytes": "reclaimable_bytes",
               "hard_link_alias_count": "hard_link_alias_count"}[key]
        return summary[col] == value, f"{col}={summary[col]} (group: {dict(summary)})"
    if key == "not_grouped":
        grouped = [r["relative_path"] for r in found
                   if r["content_id"] is not None and (_duplicate_summary(conn, r["content_id"]) or {"location_count": 0})["location_count"] > 1]
        return (not grouped) == bool(value), f"grouped={grouped}"
    if key == "rows_below":
        n = conn.execute("SELECT COUNT(*) FROM file_path WHERE relative_path LIKE ? ESCAPE '!'",
                         (paths[0].replace("!", "!!").replace("%", "!%").replace("_", "!_") + "\\%",)).fetchone()[0]
        return n == value, f"rows below={n}"
    if key == "archive_members_include":
        if not found:
            return False, "no rows"
        names = {r[0] for r in conn.execute(
            "SELECT m.entry_path FROM archive_member m JOIN analyzer_result r ON r.analyzer_result_id = m.analyzer_result_id "
            "WHERE r.file_observation_id = ?", (found[0]["current_observation_id"],))}
        missing = [v for v in value if v not in names]
        return not missing, f"members={sorted(names)[:8]}{'...' if len(names) > 8 else ''}; missing {missing}"
    if key == "canary_absent":
        import os
        present = [p for p in (os.path.expandvars(v) for v in value) if os.path.lexists(p)]
        return not present, f"canary files present: {present}"
    if key == "csv_export_safe":
        if engine is None:
            return False, "no query engine was given to the check"
        import csv
        import os
        import tempfile
        from Phase2.gui import export_files
        handle, out = tempfile.mkstemp(prefix="fo_cases_", suffix=".csv")
        os.close(handle)
        try:
            export_files(engine, out, ["path.file_name", "path.relative"])
            with open(out, encoding="utf-8-sig", newline="") as f:
                rows_out = list(csv.reader(f))[1:]
        finally:
            os.unlink(out)
        live = [cell for r in rows_out for cell in r if cell[:1] in ("=", "+", "-", "@", "\t", "\r")]
        names = {r["file_name"] for r in found}
        exported = {r[0] for r in rows_out}
        missing = [n for n in names if n not in exported and "'" + n not in exported]
        ok = (not live) == bool(value) and not missing
        return ok, f"cells a spreadsheet would run: {live[:4]}; case files absent from the export: {missing}"
    if key == "inventory_scan.status":
        scan = _latest_scan(conn)
        return scan is not None and scan["status"] == value, f"scan={dict(scan) if scan else None}"
    if key == "scan.inaccessible_count":
        scan = _latest_scan(conn)
        return scan is not None and scan["inaccessible_count"] == value, f"scan={dict(scan) if scan else None}"
    return None


def assert_case(conn, truth, case, check, engine=None):
    """Run every expected key of one case through `check(name, ok, detail)`.
    Returns {key: "PASS" | "FAIL" | NOT ASSERTED}."""
    label = f"{case['id']} {case.get('condition') or ''}".strip()
    classification = case.get("classification")
    if classification == "SCOPE":
        print(f"        ({case['id']} is a recorded limit; the matrix expects: {case.get('matrix_expects')})")
    elif classification == "DEFECT":
        print(f"        ({case['id']} is a KNOWN DEFECT, asserted as it behaves today; the matrix expects: "
              f"{case.get('matrix_expects')} -- {case.get('notes', '')[:160]})")
    paths = case.get("paths") or []
    rows = [_row(conn, p) for p in paths]
    outcome = {}
    for key, value in (case.get("expected") or {}).items():
        result = _across(conn, truth, key, value, paths, rows, engine) if paths else None
        if result is not None:
            ok, detail = result
            check(f"{label}: {key} = {value!r}", ok, detail)
            outcome[key] = "PASS" if ok else "FAIL"
            continue
        if not paths:
            check(f"{label}: {key}", False, "case lists no paths")
            outcome[key] = "FAIL"
            continue
        per_path = [_per_path(conn, truth, key, value, p, r) for p, r in zip(paths, rows)]
        if any(v is None for v in per_path):
            check(f"{label}: {key}", False, f"{NOT_ASSERTED}: no check implements this key")
            outcome[key] = NOT_ASSERTED
            continue
        failures = [f"{p}: {d}" for p, (ok, d) in zip(paths, per_path) if not ok]
        check(f"{label}: {key} = {value!r}" + (f" ({len(paths)} paths)" if len(paths) > 1 else ""),
              not failures, "; ".join(failures[:3]))
        outcome[key] = "FAIL" if failures else "PASS"
    return outcome


# ---------------------------------------------------------------------------
# The whole list, and the scan totals
# ---------------------------------------------------------------------------

def assert_cases(conn, truth, check, project_dir=None, truth_path=None, home=None, engine=None):
    """Every case of the truth (optionally only those of one `home`).
    Writes CASE_RESULTS.json beside the truth when `truth_path` is given."""
    cases = [c for c in truth.get("cases", []) if home is None or c.get("home") == home]
    results = {}
    for case in cases:
        results[case["id"]] = assert_case(conn, truth, case, check, engine)
    declined = truth.get("not_constructed", [])
    if declined:
        print(f"        ({len(declined)} matrix conditions not constructed on this machine: "
              + ", ".join(d["id"] for d in declined) + ")")
    if truth_path is not None:
        record = {
            "project": str(project_dir) if project_dir else None,
            "truth": str(truth_path),
            "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cases": {cid: {"result": ("FAIL" if "FAIL" in keys.values() else
                                       NOT_ASSERTED if NOT_ASSERTED in keys.values() else "PASS"),
                            "keys": keys}
                      for cid, keys in results.items()},
            "defects": [{"id": c["id"], "condition": c.get("condition"), "matrix_expects": c.get("matrix_expects"),
                         "notes": c.get("notes")} for c in cases if c.get("classification") == "DEFECT"],
            "scope": [{"id": c["id"], "condition": c.get("condition"), "matrix_expects": c.get("matrix_expects")}
                      for c in cases if c.get("classification") == "SCOPE"],
            "not_constructed": [d["id"] for d in declined],
        }
        out = Path(truth_path).with_name("CASE_RESULTS.json" if home in (None, "Corpus") else f"CASE_RESULTS_{home.upper()}.json")
        out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return results


_REPORT_LABELS = {
    # truth key             report line label (exact text before the colon)
    "hidden_files": "Hidden files",
    "system_files": "System files",
    "empty_folders": "Empty folders found",
    "empty_files": "Empty files found",
    "long_paths": "Files with path length > 260 characters",
    "links_skipped": "Symlinks / junctions (not recursed)",
    "max_depth": "Maximum folder depth",
    "access_errors": "Folders/files that could not be accessed",
}


def assert_scan_expectations(project_dir, truth, check):
    """Compare the builder's scan totals with the latest PreliminaryReport.txt."""
    expected = truth.get("scan_expectations")
    if not expected:
        return
    reports = sorted((Path(project_dir) / "Runs").glob("*/Reports/PreliminaryReport.txt"))
    if not reports:
        check("scan expectations: a PreliminaryReport.txt exists", False, "no run has one")
        return
    text = reports[-1].read_text(encoding="utf-8", errors="replace")
    for key, label in _REPORT_LABELS.items():
        if key not in expected:
            continue
        m = re.search(r"^\s*" + re.escape(label) + r"\s*:\s*(\d+)", text, re.M)
        got = int(m.group(1)) if m else None
        check(f"scan report: {label} == {expected[key]}", got == expected[key], f"got {got} in {reports[-1].name}")
