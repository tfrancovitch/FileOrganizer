r"""Build the resolver's model from a project database.

Evidence comes from Phase 2's current exact-duplicate projection
(`p2_current_duplicate_member`, rebuilt from `file_state` when the
authoritative evidence changed), which already applies the staleness
invariant -- a member's content identity rests on its current observation
-- and already carries the physical key hard links share. Intent comes
from the p3_* tables. Nothing here opens a source file.

The evidence binding a decision records is also built here: references
(ids) into file_path / file_observation / content and the group's current
membership at the moment of deciding -- never a copy of the evidence.

Build 5 adds the visual candidate pairs (`visual_candidate`, Phase 2's
comparison of the image hashes): every pair the latest generation found
whose two images still rest on the observations it compared, plus any pair
a decision or review event names. Their images join `locations` (read from
file_state, like any location a decision names).
"""
from __future__ import annotations

import json
import sqlite3

from Phase2.core import utc_now
from Phase2.coverage import coverage_summary
from Phase2.derived import ensure_duplicate_projection

from . import EVIDENCE_BINDING_SCHEMA
from .model import (Evidence, Location, Group, Decision, PolicyVersion, ReviewEvent, RootCoverage, VisualPair, path_key,
                    MetadataEvidence, MetadataQuestion, CapabilityRecord)
from .registry import (LOCATION, GROUP, VISUAL_PAIR, METADATA_QUESTION, METADATA_DECISION_KINDS, METADATA_FIELDS,
                       METADATA_READ_MAP, file_family)
from .metadata import observe, capability_for

_MEMBER_SQL = """
SELECT m.file_path_id, m.content_id, m.source_root_id, m.size_bytes, m.physical_key,
       fp.relative_path, fp.path_sort_key,
       sr.root_path, sr.root_path_key, sr.root_ordinal,
       fs.state, fs.current_observation_id, fs.hash_run_id, fs.is_offline_or_cloud,
       1 AS hash_current, 1 AS in_current_group
  FROM p2_current_duplicate_member m
  JOIN file_path fp ON fp.file_path_id = m.file_path_id
  JOIN source_root sr ON sr.source_root_id = m.source_root_id
  JOIN file_state fs ON fs.file_path_id = m.file_path_id
"""

#: A location that is NOT a current duplicate member (a decision or a review
#: event names it): the same shape, read from file_state directly. Its state
#: may not be present. `hash_current` is Phase 2's staleness rule seen from
#: the routing side: the fingerprint is current when its content identity
#: rests on the current observation, OR when the current observation has a
#: fingerprint verdict of its own (unique by size, say -- the file was
#: re-examined and is simply no longer a duplicate); it is stale only when
#: an older observation's identity is all there is. Never fingerprinted is
#: not stale: nothing predates anything.
_STATE_SQL = """
SELECT fs.file_path_id, fs.content_id, fs.source_root_id, fs.size_bytes,
       CASE WHEN fs.volume_serial IS NOT NULL AND fs.file_index IS NOT NULL
            THEN CAST(fs.volume_serial AS TEXT)||':'||CAST(fs.file_index AS TEXT) END AS physical_key,
       fp.relative_path, fp.path_sort_key,
       sr.root_path, sr.root_path_key, sr.root_ordinal,
       fs.state, fs.current_observation_id, fs.hash_run_id, fs.is_offline_or_cloud,
       CASE WHEN fs.content_id IS NOT NULL AND fs.content_observation_id = fs.current_observation_id THEN 1
            WHEN fs.hash_observation_id = fs.current_observation_id THEN 1
            WHEN fs.content_id IS NULL AND fs.hash_observation_id IS NULL THEN 1
            ELSE 0 END AS hash_current,
       0 AS in_current_group
  FROM file_state fs
  JOIN file_path fp ON fp.file_path_id = fs.file_path_id
  JOIN source_root sr ON sr.source_root_id = fs.source_root_id
"""


def full_path(root_path, relative_path) -> str:
    return str(root_path).rstrip("\\/") + "\\" + str(relative_path)


def _member_rows(conn, where="", args=()):
    order = " ORDER BY m.content_id, sr.root_ordinal, fp.path_sort_key, m.file_path_id"
    return conn.execute(_MEMBER_SQL + where + order, args).fetchall()


def _location(row) -> Location:
    return Location(
        location_id=str(row["file_path_id"]),
        path=full_path(row["root_path"], row["relative_path"]),
        root_key=str(row["root_path_key"] or path_key(row["root_path"])),
        content_id=str(row["content_id"]) if row["content_id"] is not None else None,
        physical_id=row["physical_key"],
        present=(row["state"] == "present"),
        size_bytes=row["size_bytes"],
        observation_id=str(row["current_observation_id"]) if row["current_observation_id"] is not None else None,
        sort_key=(int(row["root_ordinal"] or 0), str(row["path_sort_key"] or ""), int(row["file_path_id"])),
        hash_current=bool(row["hash_current"]),
        cloud_only=bool(row["is_offline_or_cloud"]),
        in_current_group=bool(row["in_current_group"]))


def _bound_location_ids(decisions, events):
    """Every file location an active decision's binding, a decision's value
    or a review event names -- so routing can see a location that has left
    its group (its content changed, it vanished) and say so."""
    ids = set()
    for d in decisions:
        if d.target_kind == LOCATION:
            ids.add(str(d.target_ref))
        b = d.binding or {}
        if b.get("file_path_id") is not None:
            ids.add(str(b["file_path_id"]))
        for m in (b.get("members") or []) + ((b.get("group") or {}).get("members") or []):
            if m.get("file_path_id") is not None:
                ids.add(str(m["file_path_id"]))
        if d.kind == "canonical_location":
            ids.add(str(d.value))
    for e in events:
        if e.target_kind == LOCATION:
            ids.add(str(e.target_ref))
        for m in (e.detail or {}).get("members") or []:
            if m.get("file_path_id") is not None:
                ids.add(str(m["file_path_id"]))
    return ids


#: (Build 9) The metadata decision kinds, as an SQL list. They are p3_decision
#: rows like any other, but they are not retention or review intent: the
#: duplicate and pair model never reads them (a note on a file is not a Keep),
#: and the metadata slice (`load_metadata`) reads nothing else.
_METADATA_KINDS_SQL = "(" + ",".join(f"'{k}'" for k in METADATA_DECISION_KINDS) + ")"


def _kind_filter(metadata):
    if metadata is None:
        return ""
    return f" AND d.decision_kind {'IN' if metadata else 'NOT IN'} {_METADATA_KINDS_SQL}"


def load_decisions(conn, where="", args=(), metadata=False) -> list:
    """Every decision row, oldest first -- or (B14) those a further condition
    on `d` picks: newer than a watermark (`load_decisions_after`), or on one
    target (`store.active_for`). One parser, so a row read either way is the
    same Decision. `metadata` (Build 9): False (the default) reads the
    retention and review decisions only, True the metadata decisions only,
    None every row."""
    rows = conn.execute(
        "SELECT d.decision_id, d.operation_id, d.target_kind, d.target_ref, d.decision_kind, d.value_json, "
        "       d.origin_kind, d.supersedes_decision_id, d.evidence_binding_json, w.withdrawal_id, o.occurred_utc "
        "  FROM p3_decision d LEFT JOIN p3_decision_withdrawal w ON w.decision_id = d.decision_id "
        "  JOIN p3_operation o ON o.operation_id = d.operation_id "
        " WHERE d.project_id = 1" + _kind_filter(metadata) + (f" AND ({where})" if where else "")
        + " ORDER BY d.decision_id", tuple(args)).fetchall()
    out = []
    for r in rows:
        try:
            binding = json.loads(r["evidence_binding_json"] or "{}")
        except ValueError:
            binding = {}
        out.append(Decision(
            decision_id=str(r["decision_id"]), target_kind=r["target_kind"], target_ref=str(r["target_ref"]),
            kind=r["decision_kind"], value=json.loads(r["value_json"]), origin_kind=r["origin_kind"],
            supersedes=str(r["supersedes_decision_id"]) if r["supersedes_decision_id"] is not None else None,
            withdrawn=r["withdrawal_id"] is not None, operation_id=str(r["operation_id"]),
            sequence=int(r["decision_id"]), binding=binding if isinstance(binding, dict) else {},
            occurred_utc=r["occurred_utc"] or ""))
    return out


def load_events(conn, after=None) -> list:
    """Every review event, oldest first (Build 3) -- or (B14) only those newer
    than the watermark `after` (review events are never changed, only added)."""
    rows = conn.execute(
        "SELECT e.*, o.occurred_utc, o.actor_kind FROM p3_review_event e JOIN p3_operation o ON o.operation_id = e.operation_id "
        " WHERE e.project_id = 1" + (" AND e.review_event_id > ?" if after is not None else "") + " ORDER BY e.review_event_id",
        (int(after),) if after is not None else ()).fetchall()
    out = []
    for r in rows:
        try:
            detail = json.loads(r["detail_json"] or "{}")
        except ValueError:
            detail = {}
        out.append(ReviewEvent(
            review_event_id=str(r["review_event_id"]), target_kind=r["target_kind"], target_ref=str(r["target_ref"]),
            kind=r["event_kind"], return_kind=r["return_kind"], return_condition=r["return_condition"],
            return_on_utc=r["return_on_utc"], detail=detail if isinstance(detail, dict) else {},
            refers_to=str(r["refers_to_review_event_id"]) if r["refers_to_review_event_id"] is not None else None,
            occurred_utc=r["occurred_utc"] or "", actor_kind=r["actor_kind"] or "explicit_human",
            sequence=int(r["review_event_id"])))
    return out


def record_marks(conn) -> dict:
    """Where the decision record stands (B14): the newest decision row,
    withdrawal row, review event and policy version. A decision row is never
    changed once written -- it is only withdrawn (a new withdrawal row) or
    superseded (a new decision row) -- so what is newer than these marks is
    everything that changed since they were taken."""
    q = lambda sql: int(conn.execute(sql).fetchone()[0] or 0)              # noqa: E731
    return {"decision": q("SELECT MAX(decision_id) FROM p3_decision"),
            "withdrawal": q("SELECT MAX(withdrawal_id) FROM p3_decision_withdrawal"),
            "event": q("SELECT MAX(review_event_id) FROM p3_review_event"),
            "policy": q("SELECT MAX(policy_version_id) FROM p3_policy_version")}


def load_decisions_after(conn, after) -> list:
    """The decision rows newer than the watermark `after`."""
    return load_decisions(conn, "d.decision_id > ?", (int(after),))


def withdrawals_after(conn, after) -> list:
    """(withdrawal_id, decision_id) for each withdrawal row newer than `after`
    -- of the decisions `load_decisions` reads by default (Build 9: not the
    metadata decisions, which the model it refreshes never held)."""
    return [(int(r[0]), str(r[1])) for r in conn.execute(
        "SELECT w.withdrawal_id, w.decision_id FROM p3_decision_withdrawal w JOIN p3_decision d ON d.decision_id = w.decision_id "
        " WHERE w.withdrawal_id > ?" + _kind_filter(False) + " ORDER BY w.withdrawal_id",
        (int(after),))]


def load_roots(conn) -> dict:
    """What the latest scan says about each active root, keyed by root key."""
    keys = {int(r["source_root_id"]): (r["root_path_key"] or path_key(r["root_path"]))
            for r in conn.execute("SELECT source_root_id, root_path, root_path_key FROM source_root WHERE project_id=1")}
    roots = {}
    try:
        summary = coverage_summary(conn)
    except Exception:                                               # noqa: BLE001
        return roots
    for rc in summary.get("root_coverage", []):
        key = keys.get(int(rc["source_root_id"]))
        if key is None:
            continue
        if rc.get("scan_id") is None:
            roots[key] = RootCoverage(key, complete=False, available=False, detail="never scanned")
            continue
        available = rc.get("root_availability") == "available"
        complete = rc.get("coverage") == "complete"
        bits = []
        if not available:
            bits.append(f"root {rc.get('root_availability') or 'unavailable'}")
        if rc.get("status") not in ("completed", "completed_with_warnings"):
            bits.append(f"latest scan {rc.get('status')}")
        if rc.get("inaccessible_seen_count"):
            bits.append(f"{rc['inaccessible_seen_count']:,} inaccessible")
        if rc.get("inaccessible_truncated"):
            bits.append("inaccessible list truncated")
        roots[key] = RootCoverage(key, complete=complete, available=available, detail=", ".join(bits))
    return roots


def load_policies(conn) -> list:
    """The current (newest) version of every policy; `active` says whether it speaks."""
    rows = conn.execute(
        "SELECT p.policy_id, p.kind, v.policy_version_id, v.version_no, v.scope_json, v.effect_json, v.status "
        "  FROM p3_policy p JOIN p3_policy_version v ON v.policy_id = p.policy_id "
        " WHERE p.project_id = 1 AND v.version_no = (SELECT MAX(version_no) FROM p3_policy_version x WHERE x.policy_id = p.policy_id) "
        " ORDER BY p.policy_id").fetchall()
    return [PolicyVersion(
        policy_version_id=str(r["policy_version_id"]), policy_id=str(r["policy_id"]), kind=r["kind"],
        scope=json.loads(r["scope_json"]), effect=json.loads(r["effect_json"] or "{}"),
        version_no=int(r["version_no"]), active=(r["status"] == "active")) for r in rows]


#: A visual candidate pair with what its currency rests on, read live: the
#: generation's own flag, each image's state and current observation (a
#: re-observed image no longer rests on the hash that was compared), and each
#: one's content identity (byte-identical images are exact duplicates).
#: The pair is LIVE when the latest generation found it and both images are
#: present on the very observations it compared; EXACT when both rest on a
#: current content identity and it is the same one.
_LIVE = ("(vc.current = 1 AND fa.state = 'present' AND fb.state = 'present' "
         "AND fa.current_observation_id = vc.a_observation_id AND fb.current_observation_id = vc.b_observation_id)")
_EXACT = ("(fa.content_id IS NOT NULL AND fa.content_id = fb.content_id "
          "AND fa.content_observation_id = fa.current_observation_id AND fb.content_observation_id = fb.current_observation_id)")
_PAIR_SQL = f"""
SELECT vc.candidate_id, vc.a_file_path_id, vc.b_file_path_id, vc.method, vc.hash_bits, vc.distance,
       vc.ahash_distance, vc.dhash_distance, vc.a_observation_id, vc.b_observation_id,
       {_LIVE} AS live, {_EXACT} AS exact
  FROM visual_candidate vc
  JOIN file_state fa ON fa.file_path_id = vc.a_file_path_id
  JOIN file_state fb ON fb.file_path_id = vc.b_file_path_id
"""


def _pair(r) -> VisualPair:
    pid, a, b, method, bits, distance, ad, dd, a_obs, b_obs, live, exact = tuple(r)
    detail = {}
    if ad is not None:
        detail["ahash_distance"] = int(ad)
    if dd is not None:
        detail["dhash_distance"] = int(dd)
    return VisualPair(str(pid), str(a), str(b), method, distance, bits, None,
                      str(a_obs) if a_obs is not None else None, str(b_obs) if b_obs is not None else None,
                      bool(live), bool(exact), detail)


def load_pairs(conn, named=()) -> dict:
    """pair_id -> VisualPair: every pair that is current now, plus the named
    ones (a decision or review event targets them) whatever their state. A
    database without the table (a model built by hand) has none."""
    pairs = {}
    try:
        for r in conn.execute(_PAIR_SQL + f" WHERE vc.current = 1 AND {_LIVE}"):
            p = _pair(r)
            pairs[p.pair_id] = p
        ids = sorted({int(n) for n in named if str(n).isdigit()} - {int(p) for p in pairs})
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            for r in conn.execute(_PAIR_SQL + f" WHERE vc.candidate_id IN ({marks})", tuple(chunk)):
                p = _pair(r)
                pairs[p.pair_id] = p
    except sqlite3.OperationalError:
        return {}
    return pairs


def pair_counts(conn) -> dict:
    """The project summary's numbers for the Similar images line, straight
    from SQL (no resolution): current pairs, how many are byte-identical, and
    how many carry an active decision."""
    try:
        row = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM({_EXACT}), 0) FROM visual_candidate vc "
            "  JOIN file_state fa ON fa.file_path_id = vc.a_file_path_id "
            f"  JOIN file_state fb ON fb.file_path_id = vc.b_file_path_id WHERE vc.current = 1 AND {_LIVE}").fetchone()
        decided = conn.execute(
            "SELECT COUNT(DISTINCT d.target_ref) FROM p3_decision d WHERE d.target_kind = ? "
            "   AND NOT EXISTS (SELECT 1 FROM p3_decision_withdrawal w WHERE w.decision_id = d.decision_id) "
            "   AND NOT EXISTS (SELECT 1 FROM p3_decision x WHERE x.supersedes_decision_id = d.decision_id)",
            (VISUAL_PAIR,)).fetchone()[0]
    except sqlite3.OperationalError:
        return {"pairs": 0, "exact": 0, "decided": 0}
    return {"pairs": int(row[0] or 0), "exact": int(row[1] or 0), "decided": int(decided or 0)}


def load_evidence(conn, content_ids=None, pairs=True) -> Evidence:
    """The whole model: every current exact-duplicate group (or the given
    content ids only), every decision row, every policy's current version,
    and (for the whole model, unless `pairs` is False -- the Duplicate
    decisions page has no use for them) the visual candidate pairs.

    Needs a writable connection when the duplicate projection is stale --
    the same rule capability.py works under.
    """
    ensure_duplicate_projection(conn)
    if content_ids:
        marks = ",".join("?" for _ in content_ids)
        rows = _member_rows(conn, f" WHERE m.content_id IN ({marks})", tuple(int(c) for c in content_ids))
    else:
        rows = _member_rows(conn)
    locations, members = {}, {}
    for r in rows:
        loc = _location(r)
        locations[loc.location_id] = loc
        members.setdefault(loc.content_id, []).append(loc.location_id)
    groups = {cid: Group(cid, cid, tuple(lids)) for cid, lids in members.items()}
    decisions = load_decisions(conn)
    events = load_events(conn)
    want_pairs, pairs = pairs, {}
    if want_pairs and not content_ids:
        named = {d.target_ref for d in decisions if d.target_kind == VISUAL_PAIR}
        named |= {e.target_ref for e in events if e.target_kind == VISUAL_PAIR}
        pairs = load_pairs(conn, named)
    # Locations a decision or event names that are no longer current members
    # (content changed, vanished, hash stale): routing needs to see them. And
    # every image of a visual candidate pair (Build 5).
    wanted = _bound_location_ids([d for d in decisions if not d.withdrawn], events)
    for p in pairs.values():
        wanted.update(p.members)
    # Build 9: every location a metadata question or project metadata names
    # (a plan item on it needs its evidence; so does a review of it).
    metadata = load_metadata(conn) if not content_ids else MetadataEvidence()
    wanted.update(q.location_id for q in metadata.questions.values())
    wanted.update(str(d.target_ref) for d in metadata.decisions if d.target_kind == LOCATION and not d.withdrawn)
    wanted -= set(locations)
    if wanted and not content_ids:
        ids = sorted(int(i) for i in wanted if str(i).isdigit())
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            for r in conn.execute(_STATE_SQL + f" WHERE fs.file_path_id IN ({marks})", tuple(chunk)):
                loc = _location(r)
                locations[loc.location_id] = loc
    ev = Evidence(locations, groups, decisions, load_policies(conn), events, load_roots(conn), utc_now())
    ev.pairs = pairs
    ev.metadata = metadata
    return ev


# -- Build 9: metadata questions, read from Phase 1/2 evidence ------------------------------------

def load_capabilities(conn) -> dict:
    """The metadata capability record (migration 014): (family, field) -> CapabilityRecord."""
    try:
        rows = conn.execute("SELECT * FROM p3_metadata_capability").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(r["file_family"], r["semantic_field"]): CapabilityRecord(
        r["file_family"], r["semantic_field"], r["read_support"], r["write_support"], r["status"], r["note"] or "")
        for r in rows}


_QUESTION_LOCATION_SQL = """
SELECT fp.file_path_id, fp.relative_path, sr.root_path, fs.state, fs.current_observation_id,
       fs.is_offline_or_cloud, fs.hard_link_count
  FROM file_path fp JOIN source_root sr ON sr.source_root_id = fp.source_root_id
  LEFT JOIN file_state fs ON fs.file_path_id = fp.file_path_id
"""
_READ_ANALYZERS = tuple(sorted({a for readers in METADATA_READ_MAP.values() for a, _k, _s in readers}))


def _analyzer_readings(conn, file_path_ids) -> dict:
    """file_path_id -> [(analyzer_result_id, analyzer_key, observation_id, status, row)] for the
    analyzers that read metadata, every observation (staleness is judged later). Read only."""
    out = {}
    ids = sorted({int(i) for i in file_path_ids if str(i).isdigit()})
    marks_a = ",".join("?" for _ in _READ_ANALYZERS)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" for _ in chunk)
        for r in conn.execute(
                "SELECT ar.analyzer_result_id, a.analyzer_key, ar.file_observation_id, ar.status, ar.title, ar.author, "
                "       ar.content_created_reported, ar.detail_json, fo.file_path_id "
                "  FROM file_observation fo JOIN analyzer_result ar ON ar.file_observation_id = fo.file_observation_id "
                "  JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id JOIN analyzer a ON a.analyzer_id = rr.analyzer_id "
                f" WHERE fo.file_path_id IN ({marks}) AND a.analyzer_key IN ({marks_a})",
                tuple(chunk) + _READ_ANALYZERS):
            out.setdefault(str(r["file_path_id"]), []).append(r)
    return out


def _reading(row, semantic_field):
    """One analyzer result's value for a field, per METADATA_READ_MAP."""
    for analyzer, kind, source in METADATA_READ_MAP.get(semantic_field, ()):
        if analyzer != row["analyzer_key"]:
            continue
        if kind == "column":
            return row[source]
        try:
            return (json.loads(row["detail_json"] or "{}") or {}).get(source)
        except ValueError:
            return None
    return None


def _question(question_id, loc_row, semantic_field, readings, capabilities) -> MetadataQuestion:
    path = full_path(loc_row["root_path"], loc_row["relative_path"])
    family = file_family(path)
    cap = capability_for(capabilities, family, semantic_field)
    results = [(int(r["analyzer_result_id"]), r["analyzer_key"], r["file_observation_id"], r["status"],
                _reading(r, semantic_field)) for r in readings]
    obs = loc_row["current_observation_id"]
    present = loc_row["state"] == "present"
    state, value, values, ids, detail = observe(results, semantic_field, cap, present=present,
                                                cloud_only=bool(loc_row["is_offline_or_cloud"]), current_observation_id=obs)
    links = loc_row["hard_link_count"]
    return MetadataQuestion(
        question_id=str(question_id) if question_id is not None else None, location_id=str(loc_row["file_path_id"]),
        semantic_field=semantic_field, observed_state=state, observed_value=value, observed_values=values,
        file_family=family, capability=cap,
        signed_or_certified=None,                   # Phase 1/2 records no signature evidence
        hardlinked=None if links is None else int(links) > 1,
        observation_id=str(obs) if obs is not None else None, analyzer_result_ids=ids, detail=detail)


def load_metadata(conn, question_ids=None) -> MetadataEvidence:
    """Build 9's slice: every metadata question (or the given ones) with what
    the evidence says now, every metadata decision row, the capability record.
    Nothing is read from a source file: values come from analyzer_result, the
    link count from file_state."""
    caps = load_capabilities(conn)
    try:
        sql = ("SELECT q.question_id, q.semantic_field, x.* FROM p3_metadata_question q "
               "  JOIN (" + _QUESTION_LOCATION_SQL + ") x ON x.file_path_id = q.file_path_id WHERE q.project_id = 1")
        args = ()
        if question_ids is not None:
            ids = [int(i) for i in question_ids if str(i).isdigit()]
            sql += f" AND q.question_id IN ({','.join('?' for _ in ids) or 'NULL'})"
            args = tuple(ids)
        rows = conn.execute(sql + " ORDER BY q.question_id", args).fetchall()
    except sqlite3.OperationalError:
        return MetadataEvidence(capabilities=caps)
    readings = _analyzer_readings(conn, [r["file_path_id"] for r in rows])
    questions = {}
    for r in rows:
        q = _question(r["question_id"], r, r["semantic_field"], readings.get(str(r["file_path_id"]), []), caps)
        questions[q.question_id] = q
    decisions = load_decisions(conn, metadata=True) if question_ids is None else []
    return MetadataEvidence(questions, decisions, caps)


def location_metadata(conn, file_path_id) -> list:
    """Every semantic field of one location as a MetadataQuestion -- the ones
    already asked with their ids, the rest with question_id None: what a
    review surface lists for a file before anyone records anything."""
    row = conn.execute(_QUESTION_LOCATION_SQL + " WHERE fp.file_path_id = ?", (int(file_path_id),)).fetchone()
    if row is None:
        raise ValueError(f"no such file location: {file_path_id}")
    asked = {}
    try:
        asked = {r["semantic_field"]: r["question_id"] for r in conn.execute(
            "SELECT question_id, semantic_field FROM p3_metadata_question WHERE project_id = 1 AND file_path_id = ?",
            (int(file_path_id),))}
    except sqlite3.OperationalError:
        pass
    caps = load_capabilities(conn)
    readings = _analyzer_readings(conn, [file_path_id]).get(str(int(file_path_id)), [])
    return [_question(asked.get(f), row, f, readings, caps) for f in METADATA_FIELDS]


def question_binding(conn, question_id) -> dict:
    """What a metadata decision rests on (Build 1's pattern, ids only): the
    question, its location's current observation, and the analyzer results
    the observed value was read from -- plus the value-state word, so a
    material change (R8 §53) can be told from a re-read that found the same."""
    md = load_metadata(conn, [question_id])
    q = md.questions.get(str(question_id))
    if q is None:
        return {}
    return {
        "schema": EVIDENCE_BINDING_SCHEMA,
        "target_kind": METADATA_QUESTION,
        "question_id": int(question_id),
        "file_path_id": int(q.location_id),
        "semantic_field": q.semantic_field,
        "observation_id": int(q.observation_id) if q.observation_id is not None else None,
        "analyzer_result_ids": [int(i) for i in q.analyzer_result_ids],
        "observed_state": q.observed_state,
    }


def group_members(conn, content_id):
    """The current members of one group, as Locations in display order."""
    return [_location(r) for r in _member_rows(conn, " WHERE m.content_id = ?", (int(content_id),))]


def location_of(conn, file_path_id):
    """One current duplicate member as a Location, or None when the file is
    not (any longer) in a current exact-duplicate group."""
    rows = _member_rows(conn, " WHERE m.file_path_id = ?", (int(file_path_id),))
    return _location(rows[0]) if rows else None


# -- evidence bindings -----------------------------------------------------------

def group_binding(conn, content_id) -> dict:
    """What the group looked like when the decision was made: its content
    identity and each member's current observation. Ids only."""
    members = group_members(conn, content_id)
    return {
        "schema": EVIDENCE_BINDING_SCHEMA,
        "target_kind": GROUP,
        "content_id": int(content_id),
        "members": [{"file_path_id": int(m.location_id), "observation_id": int(m.observation_id) if m.observation_id else None}
                    for m in members],
    }


def location_binding(conn, file_path_id) -> dict:
    row = conn.execute(
        "SELECT fs.current_observation_id, fs.content_id, fs.content_observation_id, fs.hash_run_id, fs.state "
        "  FROM file_state fs WHERE fs.file_path_id = ?", (int(file_path_id),)).fetchone()
    if row is None:
        raise ValueError(f"no such file location: {file_path_id}")
    binding = {
        "schema": EVIDENCE_BINDING_SCHEMA,
        "target_kind": LOCATION,
        "file_path_id": int(file_path_id),
        "observation_id": row["current_observation_id"],
        "content_id": row["content_id"],
        "content_observation_id": row["content_observation_id"],
        "hash_run_id": row["hash_run_id"],
        "state": row["state"],
    }
    if row["content_id"] is not None:
        binding["group"] = group_binding(conn, row["content_id"])
    return binding


def pair_of(conn, pair_id):
    """One visual candidate pair as a VisualPair (current or not), or None."""
    if not str(pair_id).isdigit():
        return None
    try:
        row = conn.execute(_PAIR_SQL + " WHERE vc.candidate_id = ?", (int(pair_id),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return _pair(row) if row is not None else None


def pair_binding(conn, pair_id) -> dict:
    """What the pair looked like when the decision was made: the candidate,
    the method's measurement, and each image's current observation. Ids
    (and the measurement) only."""
    p = pair_of(conn, pair_id)
    if p is None:
        return {}
    members = []
    for lid in p.members:
        row = conn.execute("SELECT current_observation_id FROM file_state WHERE file_path_id = ?", (int(lid),)).fetchone()
        members.append({"file_path_id": int(lid), "observation_id": row["current_observation_id"] if row else None})
    return {
        "schema": EVIDENCE_BINDING_SCHEMA,
        "target_kind": VISUAL_PAIR,
        "pair_id": int(pair_id),
        "method": p.method,
        "distance": p.distance,
        "hash_bits": p.bits,
        "compared_observations": [p.a_observation_id, p.b_observation_id],
        "members": members,
    }


def evidence_mark(conn) -> str:
    """Where the evidence stands, for a preview to be checked against at commit
    (Build 6 for pairs; Build 7 for every batch): the newest comparison of the
    image hashes, the newest observation of any file, and the newest run that
    wrote a location's current state -- a scan or a fingerprinting pass. A
    re-analysis, a scan or a re-hash moves one of them.

    The run is Build 7's addition. A rescan of files that did not change
    writes no new observation (the scan refreshes the current state in place,
    keeping the observation), and a location a scan finds gone is marked
    missing in place too -- neither moved the Build 6 mark. Every scan and
    every fingerprinting pass stamps the rows it refreshes with its run id."""
    try:
        gen = conn.execute("SELECT COALESCE(MAX(generation_id), 0) FROM visual_candidate_generation").fetchone()[0]
    except sqlite3.OperationalError:
        gen = 0
    obs = conn.execute(
        "SELECT COALESCE(MAX(current_observation_id), 0), COUNT(*), COALESCE(MAX(current_run_id), 0), "
        "       COALESCE(MAX(content_run_id), 0), COALESCE(MAX(hash_run_id), 0) FROM file_state").fetchone()
    run = max(int(obs[2] or 0), int(obs[3] or 0), int(obs[4] or 0))
    return f"generation {int(gen or 0)}; observation {int(obs[0] or 0)}; locations {int(obs[1] or 0)}; run {run}"


def pair_binding_from(ev, pair) -> dict:
    """The same binding `pair_binding` reads from the database, built from a
    loaded model (Build 6: a batch previews thousands of pairs at once; the
    commit guard refuses if the evidence moved after this model was read)."""
    members = []
    for lid in pair.members:
        loc = ev.locations.get(str(lid))
        obs = loc.observation_id if loc is not None else None
        members.append({"file_path_id": int(lid), "observation_id": int(obs) if obs is not None and str(obs).isdigit() else obs})
    return {
        "schema": EVIDENCE_BINDING_SCHEMA,
        "target_kind": VISUAL_PAIR,
        "pair_id": int(pair.pair_id) if str(pair.pair_id).isdigit() else pair.pair_id,
        "method": pair.method,
        "distance": pair.distance,
        "hash_bits": pair.bits,
        "compared_observations": [pair.a_observation_id, pair.b_observation_id],
        "members": members,
    }


def binding_for(conn, target_kind, target_ref) -> dict:
    if target_kind == VISUAL_PAIR:
        return pair_binding(conn, target_ref)
    if target_kind == METADATA_QUESTION:
        return question_binding(conn, target_ref)
    return location_binding(conn, target_ref) if target_kind == LOCATION else group_binding(conn, target_ref)
