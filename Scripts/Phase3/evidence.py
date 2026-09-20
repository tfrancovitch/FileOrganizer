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

from Phase2.derived import ensure_duplicate_projection

from . import EVIDENCE_BINDING_SCHEMA
from .model import Evidence, Location, Group, Decision, PolicyVersion, path_key
from .registry import LOCATION, GROUP

_MEMBER_SQL = """
SELECT m.file_path_id, m.content_id, m.source_root_id, m.size_bytes, m.physical_key,
       fp.relative_path, fp.path_sort_key,
       sr.root_path, sr.root_path_key, sr.root_ordinal,
       fs.state, fs.current_observation_id, fs.hash_run_id
  FROM p2_current_duplicate_member m
  JOIN file_path fp ON fp.file_path_id = m.file_path_id
  JOIN source_root sr ON sr.source_root_id = m.source_root_id
  JOIN file_state fs ON fs.file_path_id = m.file_path_id
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
        content_id=str(row["content_id"]),
        physical_id=row["physical_key"],
        present=(row["state"] == "present"),
        size_bytes=row["size_bytes"],
        observation_id=str(row["current_observation_id"]) if row["current_observation_id"] is not None else None,
        sort_key=(int(row["root_ordinal"] or 0), str(row["path_sort_key"] or ""), int(row["file_path_id"])))


def load_decisions(conn) -> list:
    rows = conn.execute(
        "SELECT d.decision_id, d.operation_id, d.target_kind, d.target_ref, d.decision_kind, d.value_json, "
        "       d.origin_kind, d.supersedes_decision_id, w.withdrawal_id "
        "  FROM p3_decision d LEFT JOIN p3_decision_withdrawal w ON w.decision_id = d.decision_id "
        " WHERE d.project_id = 1 ORDER BY d.decision_id").fetchall()
    return [Decision(
        decision_id=str(r["decision_id"]), target_kind=r["target_kind"], target_ref=str(r["target_ref"]),
        kind=r["decision_kind"], value=json.loads(r["value_json"]), origin_kind=r["origin_kind"],
        supersedes=str(r["supersedes_decision_id"]) if r["supersedes_decision_id"] is not None else None,
        withdrawn=r["withdrawal_id"] is not None, operation_id=str(r["operation_id"]),
        sequence=int(r["decision_id"])) for r in rows]


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
    return Evidence(locations, groups, load_decisions(conn), load_policies(conn))


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
