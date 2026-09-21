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
"""
from __future__ import annotations

import json

from Phase2.core import utc_now
from Phase2.coverage import coverage_summary
from Phase2.derived import ensure_duplicate_projection

from . import EVIDENCE_BINDING_SCHEMA
from .model import Evidence, Location, Group, Decision, PolicyVersion, ReviewEvent, RootCoverage, path_key
from .registry import LOCATION, GROUP

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


def load_decisions(conn) -> list:
    rows = conn.execute(
        "SELECT d.decision_id, d.operation_id, d.target_kind, d.target_ref, d.decision_kind, d.value_json, "
        "       d.origin_kind, d.supersedes_decision_id, d.evidence_binding_json, w.withdrawal_id, o.occurred_utc "
        "  FROM p3_decision d LEFT JOIN p3_decision_withdrawal w ON w.decision_id = d.decision_id "
        "  JOIN p3_operation o ON o.operation_id = d.operation_id "
        " WHERE d.project_id = 1 ORDER BY d.decision_id").fetchall()
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


def load_events(conn) -> list:
    """Every review event, oldest first (Build 3)."""
    rows = conn.execute(
        "SELECT e.*, o.occurred_utc, o.actor_kind FROM p3_review_event e JOIN p3_operation o ON o.operation_id = e.operation_id "
        " WHERE e.project_id = 1 ORDER BY e.review_event_id").fetchall()
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


def load_evidence(conn, content_ids=None) -> Evidence:
    """The whole model: every current exact-duplicate group (or the given
    content ids only), every decision row, every policy's current version.

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
    # Locations a decision or event names that are no longer current members
    # (content changed, vanished, hash stale): routing needs to see them.
    wanted = _bound_location_ids([d for d in decisions if not d.withdrawn], events) - set(locations)
    if wanted and not content_ids:
        ids = sorted(int(i) for i in wanted if str(i).isdigit())
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            for r in conn.execute(_STATE_SQL + f" WHERE fs.file_path_id IN ({marks})", tuple(chunk)):
                loc = _location(r)
                locations[loc.location_id] = loc
    return Evidence(locations, groups, decisions, load_policies(conn), events, load_roots(conn), utc_now())


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


def binding_for(conn, target_kind, target_ref) -> dict:
    return location_binding(conn, target_ref) if target_kind == LOCATION else group_binding(conn, target_ref)
