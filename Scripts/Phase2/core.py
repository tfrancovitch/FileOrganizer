"""Project/database primitives for Phase 2.

Phase 2 may read the project database and project-controlled evidence artifacts.
It never opens original source files to obtain new evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION

REQUIRED_SCHEMA_VERSION = 9
DB_RELATIVE = Path("Database") / "FileOrganizer.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parent_path(value):
    if value is None:
        return None
    s = str(value).replace("/", "\\").strip("\\")
    if not s or "\\" not in s:
        return ""
    return s.rsplit("\\", 1)[0]


def _top_level(value):
    if value is None:
        return None
    s = str(value).replace("/", "\\").strip("\\")
    return s.split("\\", 1)[0] if s else ""


def _path_under(path_key, folder_key):
    p = (path_key or "").replace("/", "\\").strip("\\").lower()
    f = (folder_key or "").replace("/", "\\").strip("\\").lower()
    if not f:
        return 1
    return 1 if p == f or p.startswith(f + "\\") else 0


def connect(project_dir: str | os.PathLike, write: bool = False) -> sqlite3.Connection:
    project_dir = Path(project_dir).resolve()
    db = project_dir / DB_RELATIVE
    if not db.is_file():
        raise FileNotFoundError(f"Project database not found: {db}")

    if write:
        conn = sqlite3.connect(str(db), timeout=5.0)
    else:
        uri = db.as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.create_function("fo_parent", 1, _parent_path, deterministic=True)
    conn.create_function("fo_top_level", 1, _top_level, deterministic=True)
    conn.create_function("fo_path_under", 2, _path_under, deterministic=True)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def require_phase2_schema(conn: sqlite3.Connection):
    version = schema_version(conn)
    if version < REQUIRED_SCHEMA_VERSION:
        raise RuntimeError(
            f"This project is schema {version}. The File Organizer {VERSION} requires schema "
            f"{REQUIRED_SCHEMA_VERSION}."
        )
    return version


def stamp_core_version(conn: sqlite3.Connection) -> bool:
    """Record which analytical core actually touched this project.

    Migration 008 writes the literal 'P2.9', so a P2.9.1 overlay applied on top
    of it leaves the database misreporting its own provenance. Traceability is a
    Phase 2 requirement, so correct the stamp once a writable connection exists.

    Returns False on a read-only connection rather than raising: reporting is a
    legitimate read-only activity and must not fail over a provenance stamp.
    """
    try:
        conn.execute(
            "INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES('phase2.core_version',?,?)",
            (VERSION, utc_now()),
        )
        conn.commit()
        return True
    except sqlite3.OperationalError:
        return False


def project_info(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT project_id, project_uid, name, created_utc FROM project ORDER BY project_id LIMIT 1"
    ).fetchone()
    return dict(row) if row else {}


def evidence_signature(conn: sqlite3.Connection) -> str:
    """Conservative current-evidence signature for caches/materializations."""
    parts = {}
    for name, sql in {
        "file_state_max_run": "SELECT COALESCE(MAX(current_run_id),0) FROM file_state",
        "file_state_max_obs": "SELECT COALESCE(MAX(current_observation_id),0) FROM file_state",
        "hash_max": "SELECT COALESCE(MAX(hash_measurement_id),0) FROM hash_measurement",
        "analysis_max": "SELECT COALESCE(MAX(analyzer_result_id),0) FROM analyzer_result",
        "extracted_max": "SELECT COALESCE(MAX(extracted_content_id),0) FROM extracted_content",
        "run_max": "SELECT COALESCE(MAX(run_id),0) FROM run",
    }.items():
        try:
            parts[name] = conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            parts[name] = None
    parts["phase2"] = VERSION
    return fingerprint(parts)


def table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table_name,)
    ).fetchone()
    return row is not None


def project_artifact_path(project_dir: str | os.PathLike, run_folder: str | os.PathLike, relative: str):
    """Resolve only a project-controlled evidence artifact path.

    This helper intentionally accepts a project/run-relative base. It never accepts
    a source-root path and never attempts source reconstruction.
    """
    project = Path(project_dir).resolve()
    runs_root = (project / "Runs").resolve()
    base = (runs_root / run_folder).resolve()
    try:
        base.relative_to(runs_root)
    except ValueError:
        raise ValueError("Run folder escapes the project Runs directory")
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise ValueError("Evidence artifact path escapes its recorded run folder")
    return candidate
