"""Rebuildable Phase 2 analytical projections."""
from __future__ import annotations

import json
import os
import sqlite3

from . import VERSION
from .core import evidence_signature, utc_now

DUP_KEY = "current_exact_duplicates/v1"


def _storage_bytes(conn, tables):
    total = 0
    # dbstat may not be compiled in every runtime. Best effort only.
    try:
        for table in tables:
            row = conn.execute("SELECT COALESCE(SUM(pgsize),0) FROM dbstat WHERE name=?", (table,)).fetchone()
            total += int(row[0] or 0)
    except sqlite3.OperationalError:
        return None
    return total


def mark_stale_if_needed(conn):
    sig = evidence_signature(conn)
    row = conn.execute(
        "SELECT input_signature,status FROM p2_derived_index WHERE derived_index_key=?",
        (DUP_KEY,),
    ).fetchone()
    if row and row["status"] == "valid" and row["input_signature"] != sig:
        conn.execute(
            "UPDATE p2_derived_index SET status='stale', notes=? WHERE derived_index_key=?",
            ("Authoritative current inventory/hash evidence changed.", DUP_KEY),
        )
        conn.commit()
    return sig


def duplicate_projection_valid(conn):
    sig = mark_stale_if_needed(conn)
    row = conn.execute(
        "SELECT status,input_signature FROM p2_derived_index WHERE derived_index_key=?",
        (DUP_KEY,),
    ).fetchone()
    return bool(row and row["status"] == "valid" and row["input_signature"] == sig)


def rebuild_duplicate_projection(conn):
    """Rebuild exact-duplicate member/summary tables from authoritative current state."""
    sig = evidence_signature(conn)
    conn.execute(
        "INSERT INTO p2_derived_index(derived_index_key,index_kind,definition_version,status,engine_version) "
        "VALUES(?,?,?,?,?) ON CONFLICT(derived_index_key) DO UPDATE SET "
        "status='building', definition_version=excluded.definition_version, engine_version=excluded.engine_version, notes=NULL",
        (DUP_KEY, "projection", "1", "building", VERSION),
    )
    conn.commit()

    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM p2_current_duplicate_member")
        conn.execute("DELETE FROM p2_current_duplicate_summary")
        conn.execute(
            """
            INSERT INTO p2_current_duplicate_member(
                file_path_id, content_id, source_root_id, size_bytes, physical_key,
                file_name, extension_key)
            WITH eligible AS (
              SELECT fs.file_path_id, fs.content_id, fs.source_root_id, fs.size_bytes,
                     CASE WHEN fs.volume_serial IS NOT NULL AND fs.file_index IS NOT NULL
                          THEN CAST(fs.volume_serial AS TEXT)||':'||CAST(fs.file_index AS TEXT) END AS physical_key,
                     fp.file_name, fp.extension_key
                FROM file_state fs
                JOIN file_path fp ON fp.file_path_id=fs.file_path_id
               WHERE fs.project_id=1 AND fs.state='present'
                 AND fs.content_id IS NOT NULL
                 AND fs.content_observation_id=fs.current_observation_id
            ), dup_content AS (
              SELECT content_id FROM eligible GROUP BY content_id HAVING COUNT(*)>=2
            )
            SELECT e.file_path_id,e.content_id,e.source_root_id,e.size_bytes,e.physical_key,
                   e.file_name,e.extension_key
              FROM eligible e JOIN dup_content d ON d.content_id=e.content_id
            """
        )
        conn.execute(
            """
            INSERT INTO p2_current_duplicate_summary(
                content_id,location_count,root_count,physical_copy_count,
                hard_link_alias_count,physical_identity_complete,size_bytes,
                reclaimable_bytes,file_name_variant_count,extension_variant_count,
                logical_bytes_represented)
            SELECT m.content_id,
                   COUNT(*) AS location_count,
                   COUNT(DISTINCT m.source_root_id) AS root_count,
                   CASE WHEN COUNT(m.physical_key)=COUNT(*) THEN COUNT(DISTINCT m.physical_key) END,
                   CASE WHEN COUNT(m.physical_key)=COUNT(*) THEN COUNT(*)-COUNT(DISTINCT m.physical_key) END,
                   CASE WHEN COUNT(m.physical_key)=COUNT(*) THEN 1 ELSE 0 END,
                   MAX(m.size_bytes),
                   CASE WHEN COUNT(m.physical_key)=COUNT(*)
                        THEN (COUNT(DISTINCT m.physical_key)-1)*COALESCE(MAX(m.size_bytes),0) END,
                   COUNT(DISTINCT m.file_name),
                   COUNT(DISTINCT m.extension_key),
                   COUNT(*)*COALESCE(MAX(m.size_bytes),0)
              FROM p2_current_duplicate_member m
             GROUP BY m.content_id
            """
        )
        rows = conn.execute("SELECT COUNT(*) FROM p2_current_duplicate_summary").fetchone()[0]
        storage = _storage_bytes(conn, [
            "p2_current_duplicate_member","p2_current_duplicate_summary",
            "ix_p2_dup_member_content","ix_p2_dup_member_root",
            "ix_p2_dup_summary_reclaim","ix_p2_dup_summary_roots"
        ])
        conn.execute(
            "UPDATE p2_derived_index SET status='valid',built_utc=?,input_signature=?,row_count=?,storage_bytes=?,notes=NULL "
            "WHERE derived_index_key=?",
            (utc_now(), sig, rows, storage, DUP_KEY),
        )
        conn.commit()
        return rows
    except Exception as exc:
        try:
            conn.rollback()
            conn.execute(
                "UPDATE p2_derived_index SET status='failed',notes=? WHERE derived_index_key=?",
                (str(exc)[:1000], DUP_KEY),
            )
            conn.commit()
        except Exception:
            pass
        raise


def ensure_duplicate_projection(conn):
    if not duplicate_projection_valid(conn):
        return rebuild_duplicate_projection(conn)
    row = conn.execute("SELECT row_count FROM p2_derived_index WHERE derived_index_key=?", (DUP_KEY,)).fetchone()
    return int(row[0] or 0) if row else 0
