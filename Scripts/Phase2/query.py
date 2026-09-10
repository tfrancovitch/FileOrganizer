"""Backend-neutral Query AST execution for Phase 2 P2.9.

The compiler intentionally maps semantic field IDs to the B6.2/P2.9 schema.
Saved Query AST never stores raw SQL.
"""
from __future__ import annotations

import calendar
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

from .core import canonical_json, fingerprint, utc_now
from .derived import ensure_duplicate_projection
from .coverage import coverage_summary

QUERY_SCHEMA = "fileorganizer.query/1"
SEMANTIC_CONTRACT = "fileorganizer.query-semantics/1"


class QueryError(ValueError):
    pass


class QueryCancelled(QueryError):
    pass


# Semantic physical mappings. P2.6 meaning remains stable even if storage moves.
FILE_FIELDS = {
    "path.id": "fs.file_path_id",
    "path.file_name": "fp.file_name",
    "path.relative": "fp.relative_path",
    "path.extension": "fp.extension_key",
    "path.depth": "fs.depth",
    "path.length": "fs.path_length",
    "folder.parent": "fo_parent(fp.relative_path)",
    "folder.top_level": "fo_top_level(fp.relative_path)",
    "source_root.id": "fs.source_root_id",
    "file.state": "fs.state",
    "file.size_bytes": "fs.size_bytes",
    "file.allocated_size_bytes": "fs.allocated_size_bytes",
    "file.is_offline_or_cloud": "fs.is_offline_or_cloud",
    "file.hard_link_count": "fs.hard_link_count",
    "file.physical_identity_known": "CASE WHEN fs.volume_serial IS NOT NULL AND fs.file_index IS NOT NULL THEN 1 ELSE 0 END",
    "file.physical_object_key": "CASE WHEN fs.volume_serial IS NOT NULL AND fs.file_index IS NOT NULL THEN CAST(fs.volume_serial AS TEXT)||':'||CAST(fs.file_index AS TEXT) END",
    "file.in_current_exact_duplicate_group": "CASE WHEN dm.file_path_id IS NULL THEN 0 ELSE 1 END",
    "time.created_utc": "fs.created_utc",
    "time.modified_utc": "fs.modified_utc",
    "time.accessed_utc": "fs.accessed_utc",
    "time.created_local_naive": "fs.created_local_naive",
    "time.modified_local_naive": "fs.modified_local_naive",
    "time.accessed_local_naive": "fs.accessed_local_naive",
    "time.created_state": "fs.created_time_state",
    "time.modified_state": "fs.modified_time_state",
    "time.accessed_state": "fs.accessed_time_state",
    "time.timestamp_model": "fs.timestamp_model",
    "content.id": "CASE WHEN fs.content_observation_id=fs.current_observation_id THEN fs.content_id END",
    "hash.status": "fs.hash_status",
    "hash.authority": "CASE WHEN fs.content_id IS NULL THEN 'absent' WHEN fs.content_observation_id=fs.current_observation_id THEN 'current' ELSE 'stale' END",
}

HISTORY_FIELDS = {
    "path.id": "o.file_path_id",
    "path.file_name": "fp.file_name",
    "path.relative": "fp.relative_path",
    "path.extension": "fp.extension_key",
    "folder.parent": "fo_parent(fp.relative_path)",
    "source_root.id": "fp.source_root_id",
    "observation.id": "o.file_observation_id",
    "observation.scan_id": "o.inventory_scan_id",
    "observation.status": "o.status",
    "observation.observed_utc": "o.observed_utc",
    "observation.change_kind": "o.change_kind",
    "observation.error_kind": "o.error_kind",
    "observation.error_message": "o.error_message",
    "file.size_bytes": "o.size_bytes",
}

ANALYSIS_FIELDS = {
    "analysis.result_id": "ca.analyzer_result_id",
    "analysis.analyzer_key": "ca.analyzer_key",
    "analysis.status": "ca.status",
    "analysis.title": "ca.title",
    "analysis.author": "ca.author",
    "analysis.author_state": "CASE WHEN ca.status='analyzed' AND ca.author IS NOT NULL THEN 'known' WHEN ca.status='analyzed' THEN 'null' WHEN ca.status='skipped_cloud_only' THEN 'unavailable' WHEN ca.status='not_processed' THEN 'unknown' WHEN ca.status='error' THEN 'unknown' ELSE 'unknown' END",
    "analysis.content_created_reported": "ca.content_created_reported",
    "analysis.width_px": "ca.width_px",
    "analysis.height_px": "ca.height_px",
    "analysis.duration_seconds": "ca.duration_seconds",
    "analysis.word_count": "ca.word_count",
    "analysis.char_count": "ca.char_count",
    "analysis.error_kind": "ca.error_kind",
    "analysis.error_message": "ca.error_message",
    "analysis.analyzed_utc": "ca.analyzed_utc",
    "analysis.pdf.page_count": "CAST(json_extract(ca.detail_json,'$.PageCount') AS INTEGER)",
    "analysis.pdf.encrypted": "CASE lower(CAST(json_extract(ca.detail_json,'$.IsEncrypted') AS TEXT)) WHEN 'true' THEN 1 WHEN '1' THEN 1 WHEN 'false' THEN 0 WHEN '0' THEN 0 END",
    "analysis.camera.make": "json_extract(ca.detail_json,'$.CameraMake')",
    "analysis.camera.model": "json_extract(ca.detail_json,'$.CameraModel')",
    "analysis.audio.artist": "json_extract(ca.detail_json,'$.Artist')",
    "analysis.audio.album": "json_extract(ca.detail_json,'$.Album')",
    "analysis.audio.codec": "json_extract(ca.detail_json,'$.Codec')",
    "analysis.audio.bitrate": "CAST(json_extract(ca.detail_json,'$.Bitrate') AS INTEGER)",
    "analysis.video.codec": "json_extract(ca.detail_json,'$.VideoCodec')",
    "analysis.video.bitrate": "CAST(json_extract(ca.detail_json,'$.Bitrate') AS INTEGER)",
    "analysis.text.line_count": "CAST(json_extract(ca.detail_json,'$.LineCount') AS INTEGER)",
    "analysis.text.encoding": "json_extract(ca.detail_json,'$.Encoding')",
    "analysis.archive.entry_count": "ars.entry_total_count",
    "analysis.archive.total_uncompressed_bytes": "ars.total_uncompressed_bytes",
    "analysis.archive.total_compressed_bytes": "ars.total_compressed_bytes",
    "analysis.archive.completeness": "ars.analysis_mode",
    "text.status": "ec.status",
    "text.source_type": "ec.source_type",
    "text.char_count": "ec.char_count",
    "text.word_count": "ec.word_count",
    "text.sha256": "ec.text_sha256",
    "text.artifact_exists": "ec.artifact_exists",
    "text.storage_mode": "ec.storage_mode",
    "path.id": "ca.file_path_id",
    "path.file_name": "fp.file_name",
    "path.relative": "fp.relative_path",
    "path.extension": "fp.extension_key",
    "folder.parent": "fo_parent(fp.relative_path)",
    "source_root.id": "ca.source_root_id",
    "file.size_bytes": "ca.size_bytes",
}

DUP_FIELDS = {
    "duplicate.content_id": "dg.content_id",
    "duplicate.location_count": "dg.location_count",
    "duplicate.root_count": "dg.root_count",
    "duplicate.physical_copy_count": "dg.physical_copy_count",
    "duplicate.hard_link_alias_count": "dg.hard_link_alias_count",
    "duplicate.physical_identity_complete": "dg.physical_identity_complete",
    "duplicate.size_bytes": "dg.size_bytes",
    "duplicate.reclaimable_bytes": "dg.reclaimable_bytes",
    "duplicate.file_name_variant_count": "dg.file_name_variant_count",
    "duplicate.extension_variant_count": "dg.extension_variant_count",
    "duplicate.logical_bytes_represented": "dg.logical_bytes_represented",
}

RUN_FIELDS = {
    "run.id": "r.run_id",
    "run.kind": "r.run_kind",
    "run.status": "r.status",
    "run.started_utc": "r.started_utc",
    "run.ended_utc": "r.ended_utc",
    "run.duration_ms": "r.duration_ms",
    "run.warning_count": "r.warning_count",
    "run.error_count": "r.error_count",
    "run.finalized": "r.finalized",
}

FOLDER_FIELDS = {
    "folder.relative_path": "folder_relative_path",
    "folder.parent": "parent_folder",
    "folder.depth": "folder_depth",
    "folder.location_count_direct": "location_count_direct",
    "folder.location_count_recursive": "location_count_recursive",
    "folder.logical_bytes_direct": "logical_bytes_direct",
    "folder.logical_bytes_recursive": "logical_bytes_recursive",
}

DEFAULT_FILE_COLUMNS = [
    "path.id", "path.file_name", "folder.parent", "path.extension",
    "file.size_bytes", "time.modified_utc", "file.in_current_exact_duplicate_group",
    "hash.authority"
]
DEFAULT_ANALYSIS_COLUMNS = [
    "analysis.result_id","path.id","path.file_name","path.extension",
    "analysis.analyzer_key","analysis.status","analysis.author","analysis.analyzed_utc"
]
DEFAULT_DUP_COLUMNS = list(DUP_FIELDS)
DEFAULT_HISTORY_COLUMNS = ["observation.id","path.id","path.file_name","path.relative","observation.change_kind","observation.observed_utc","observation.status"]
DEFAULT_RUN_COLUMNS = ["run.id","run.kind","run.status","run.started_utc","run.ended_utc","run.warning_count","run.error_count"]


def _parse_utc(value):
    if isinstance(value, datetime):
        return value
    s = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _add_months(dt, months):
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _resolve_value(expr, params, execution_time):
    kind = expr.get("kind") if isinstance(expr, dict) else None
    if kind == "literal":
        return expr.get("value")
    if kind == "parameter":
        name = expr.get("name")
        if name not in params:
            raise QueryError(f"Missing query parameter: {name}")
        return params[name]
    if kind == "relative_time":
        dt = execution_time
        off = expr.get("offset") or {}
        if off.get("years"):
            dt = _add_months(dt, int(off["years"]) * 12)
        if off.get("months"):
            dt = _add_months(dt, int(off["months"]))
        dt += timedelta(weeks=int(off.get("weeks",0)), days=int(off.get("days",0)),
                        hours=int(off.get("hours",0)), minutes=int(off.get("minutes",0)))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raise QueryError(f"Unsupported value expression: {expr}")


class QueryEngine:
    ENGINE_VERSION = "P2.9.1-query-1"

    def __init__(self, conn, saved_store=None, fts_manager=None):
        self.conn = conn
        self.saved_store = saved_store
        self.fts_manager = fts_manager

    def normalize(self, query, parameters=None):
        q = json.loads(json.dumps(query))
        if q.get("query_schema") != QUERY_SCHEMA:
            raise QueryError("Unsupported query schema")
        if q.get("semantic_contract") != SEMANTIC_CONTRACT:
            raise QueryError("Unsupported semantic contract")
        subj = q.get("subject") or {}
        if subj.get("entity") == "file" and (subj.get("temporal") or {}).get("mode") == "current":
            if not subj.get("current_file_states"):
                raise QueryError("Current file Query Definitions must explicitly declare current_file_states")
        q["subject"] = subj
        q.setdefault("scope", {"kind":"project"})
        q.setdefault("coverage_requirement", "allow_with_disclosure")
        if q["coverage_requirement"] not in ("allow_with_disclosure","require_complete"):
            raise QueryError("Invalid coverage requirement")
        temporal=(subj.get("temporal") or {}).get("mode")
        entity=subj.get("entity")
        if entity=="duplicate_group" and temporal in ("history","as_of"):
            raise QueryError("Historical duplicate groups require an explicit run snapshot")
        if entity=="duplicate_group" and temporal=="run":
            raise QueryError("Historical duplicate-run snapshots are not implemented in the P2.9 core; current duplicate truth remains available")
        if entity=="file" and temporal=="current":
            allowed={"present","inaccessible","missing","unverified"}
            bad=set(subj.get("current_file_states") or [])-allowed
            if bad: raise QueryError(f"Invalid current file state(s): {sorted(bad)}")
        ident=re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
        groups=[g.get("id") for g in q.get("group_by") or []]
        aggs=[a.get("id") for a in q.get("aggregates") or []]
        if any(not x or not ident.match(x) for x in groups+aggs):
            raise QueryError("Group and aggregate IDs must be simple identifiers")
        if len(groups)!=len(set(groups)) or len(aggs)!=len(set(aggs)):
            raise QueryError("Duplicate group/aggregate ID")
        if q.get("having") and not (groups or aggs):
            raise QueryError("HAVING requires grouping or aggregation")
        for sort in q.get("sort") or []:
            ref=sort.get("ref") or {}
            if ref.get("kind")=="aggregate" and ref.get("id") not in aggs:
                raise QueryError(f"Unknown aggregate sort reference: {ref.get('id')}")
            if ref.get("kind")=="group" and ref.get("id") not in groups:
                raise QueryError(f"Unknown group sort reference: {ref.get('id')}")
        q["_resolved_parameters"] = dict(parameters or {})
        q["_normalized_execution_utc"] = utc_now()
        return q

    @staticmethod
    def _uses_current_duplicate_semantics(value):
        if isinstance(value, dict):
            if value.get("id") == "file.in_current_exact_duplicate_group":
                return True
            if value.get("relation") in ("current_exact_duplicate_group", "current_exact_duplicate_members"):
                return True
            return any(QueryEngine._uses_current_duplicate_semantics(v) for v in value.values())
        if isinstance(value, list):
            return any(QueryEngine._uses_current_duplicate_semantics(v) for v in value)
        return False

    def query_fingerprint(self, query, parameters=None):
        q = self.normalize(query, parameters)
        q.pop("_normalized_execution_utc", None)
        return fingerprint(q)

    def execute(self, query, parameters=None, limit=None, cursor=None, retain_kind=None, saved_revision_id=None, cancel_check=None):
        normalized = self.normalize(query, parameters)
        entity = normalized["subject"]["entity"]
        if cancel_check is not None:
            self.conn.set_progress_handler(lambda: 1 if cancel_check() else 0, 1)
        try:
            if entity == "file" and (normalized["subject"].get("temporal") or {}).get("mode") == "current":
                if self._uses_current_duplicate_semantics(normalized):
                    ensure_duplicate_projection(self.conn)
            if normalized.get("expand"):
                result = self._execute_expanded(normalized, external_limit=limit)
            elif entity == "folder":
                result = self._execute_folders(normalized)
            else:
                if entity == "duplicate_group":
                    ensure_duplicate_projection(self.conn)
                sql, bind = self._compile(normalized, cursor=cursor, external_limit=limit)
                cur = self.conn.execute(sql, bind)
                rows = [dict(r) for r in cur.fetchall()]
                result = {"rows": rows, "columns": list(rows[0]) if rows else [], "next_cursor": self._next_cursor(normalized, rows)}
            health = coverage_summary(self.conn, normalized.get("scope"))
            warnings = list(health.get("warnings") or [])
            if normalized.get("coverage_requirement") == "require_complete" and health.get("coverage") != "complete":
                raise QueryError("This query requires complete coverage, but the current project evidence is incomplete.")
            result.update({
                "normalized_query": normalized,
                "query_fingerprint": self.query_fingerprint(query, parameters),
                "executed_utc": normalized["_normalized_execution_utc"],
                "coverage": health,
                "warnings": warnings,
                "engine_version": self.ENGINE_VERSION,
            })
            if retain_kind:
                self.retain_execution(result, retain_kind, parameters or {}, saved_revision_id)
            return result
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryCancelled("Query cancelled") from exc
            raise
        finally:
            if cancel_check is not None:
                self.conn.set_progress_handler(None, 0)

    def retain_execution(self, result, kind, parameters=None, saved_revision_id=None):
        if kind not in ("report","export","snapshot","saved_query"):
            raise QueryError(f"Unsupported retained execution kind: {kind}")
        normalized={k:v for k,v in result["normalized_query"].items() if not k.startswith("_")}
        self.conn.execute(
            "INSERT INTO p2_execution_record(project_id,saved_query_revision_id,execution_uid,execution_kind,"
            "query_fingerprint,normalized_query_json,parameter_json,executed_utc,status,result_count,"
            "coverage_json,warnings_json,engine_version) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
            (saved_revision_id,"exec:"+str(uuid.uuid4()),kind,result["query_fingerprint"],
             canonical_json(normalized),canonical_json(parameters or {}),result["executed_utc"],"completed",
             len(result.get("rows") or []),canonical_json(result.get("coverage") or {}),
             canonical_json(result.get("warnings") or []),self.ENGINE_VERSION))
        self.conn.commit()

    def _field_map(self, entity, temporal):
        if entity == "file" and temporal == "history": return HISTORY_FIELDS
        if entity == "file": return FILE_FIELDS
        if entity == "analysis_result": return ANALYSIS_FIELDS
        if entity == "duplicate_group": return DUP_FIELDS
        if entity == "run": return RUN_FIELDS
        if entity == "folder": return FOLDER_FIELDS
        raise QueryError(f"Unsupported query subject: {entity}/{temporal}")

    def _base(self, q):
        entity=q["subject"]["entity"]
        temporal=q["subject"]["temporal"]["mode"]
        if entity == "file" and temporal == "current":
            return (
                "FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id "
                "JOIN source_root sr ON sr.source_root_id=fs.source_root_id "
                "LEFT JOIN p2_current_duplicate_member dm ON dm.file_path_id=fs.file_path_id",
                "fs.file_path_id"
            )
        if entity == "file" and temporal == "history":
            return (
                "FROM file_observation o JOIN file_path fp ON fp.file_path_id=o.file_path_id "
                "JOIN source_root sr ON sr.source_root_id=fp.source_root_id",
                "o.file_observation_id"
            )
        if entity == "analysis_result":
            if temporal == "current":
                cte = """
                WITH current_analysis AS (
                  SELECT ar.*,a.analyzer_key,fo.file_path_id,fs.source_root_id,fs.size_bytes
                    FROM analyzer_result ar
                    JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
                    JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
                    JOIN file_observation fo ON fo.file_observation_id=ar.file_observation_id
                    JOIN file_state fs ON fs.file_path_id=fo.file_path_id
                                      AND fs.current_observation_id=ar.file_observation_id
                   WHERE NOT EXISTS (
                      SELECT 1 FROM analyzer_result newer
                      JOIN analyzer_run nrr ON nrr.analyzer_run_id=newer.analyzer_run_id
                      WHERE newer.file_observation_id=ar.file_observation_id
                        AND nrr.analyzer_id=rr.analyzer_id
                        AND (newer.analyzed_utc>ar.analyzed_utc OR
                            (newer.analyzed_utc=ar.analyzed_utc AND newer.analyzer_result_id>ar.analyzer_result_id))
                   )
                )
                """
                base = (
                    "FROM current_analysis ca JOIN file_path fp ON fp.file_path_id=ca.file_path_id "
                    "JOIN source_root sr ON sr.source_root_id=ca.source_root_id "
                    "LEFT JOIN extracted_content ec ON ec.analyzer_result_id=ca.analyzer_result_id "
                    "LEFT JOIN archive_summary ars ON ars.analyzer_result_id=ca.analyzer_result_id"
                )
                return cte + base, "ca.analyzer_result_id"
            return (
                "FROM analyzer_result ca JOIN analyzer_run rr ON rr.analyzer_run_id=ca.analyzer_run_id "
                "JOIN analyzer a ON a.analyzer_id=rr.analyzer_id "
                "LEFT JOIN file_observation fo ON fo.file_observation_id=ca.file_observation_id "
                "LEFT JOIN file_path fp ON fp.file_path_id=fo.file_path_id "
                "LEFT JOIN source_root sr ON sr.source_root_id=fp.source_root_id "
                "LEFT JOIN extracted_content ec ON ec.analyzer_result_id=ca.analyzer_result_id "
                "LEFT JOIN archive_summary ars ON ars.analyzer_result_id=ca.analyzer_result_id",
                "ca.analyzer_result_id"
            )
        if entity == "duplicate_group":
            return "FROM p2_current_duplicate_summary dg", "dg.content_id"
        if entity == "run":
            return "FROM run r", "r.run_id"
        raise QueryError(f"Unsupported subject: {entity}/{temporal}")

    def _scope_sql(self, q, field_map):
        scope=q.get("scope") or {"kind":"project"}
        kind=scope.get("kind")
        entity=q["subject"]["entity"]
        temporal=q["subject"]["temporal"]["mode"]
        if kind == "project": return "", []
        if kind == "source_roots":
            ids=scope.get("source_root_ids") or []
            if not ids: raise QueryError("source_roots scope is empty")
            if entity == "duplicate_group":
                ph=",".join("?"*len(ids))
                return f"EXISTS (SELECT 1 FROM p2_current_duplicate_member sm WHERE sm.content_id=dg.content_id AND sm.source_root_id IN ({ph}))", list(ids)
            expr=field_map.get("source_root.id")
            if not expr: raise QueryError("Subject cannot be scoped by source root")
            ph=",".join("?"*len(ids))
            return f"{expr} IN ({ph})", list(ids)
        if kind == "folder_subtrees":
            folders=scope.get("folders") or []
            if not folders: raise QueryError("folder scope is empty")
            pieces=[]; bind=[]
            for f in folders:
                root=int(f["source_root_id"]); key=f.get("relative_path_key") or ""
                if entity == "duplicate_group":
                    pieces.append("EXISTS (SELECT 1 FROM p2_current_duplicate_member sm JOIN file_path sfp ON sfp.file_path_id=sm.file_path_id WHERE sm.content_id=dg.content_id AND sm.source_root_id=? AND fo_path_under(sfp.relative_path_key,?)=1)")
                else:
                    root_expr=field_map.get("source_root.id")
                    if not root_expr: raise QueryError("Subject cannot be folder scoped")
                    # file/history/analysis subjects all use the indexed B6.2
                    # normalized path key through alias fp.  Exact folder match
                    # or a descendant prefix is represented as a BINARY range,
                    # allowing SQLite to use (source_root_id,relative_path_key).
                    norm=(key or "").replace("/","\\").strip("\\").lower()
                    path_id_expr=field_map.get("path.id")
                    if norm and path_id_expr:
                        # Two seekable arms rather than one disjunction. Both
                        # must stay: the exact-match arm is what stops a folder
                        # scope swallowing its prefix-siblings, since a lone
                        # range [norm, norm+']') also matches 'dept_00abc'.
                        # Written as OR, SQLite abandons
                        # (source_root_id,relative_path_key) -- the table's own
                        # UNIQUE index -- and drives from ix_file_state_root,
                        # filtering every row it looks up. As UNION ALL each arm
                        # seeks that index: 39.4ms -> 0.5ms on 100k files,
                        # identical results.
                        pieces.append(
                            f"({path_id_expr} IN ("
                            "SELECT file_path_id FROM file_path"
                            " WHERE source_root_id=? AND relative_path_key=?"
                            " UNION ALL "
                            "SELECT file_path_id FROM file_path"
                            " WHERE source_root_id=? AND relative_path_key>=?"
                            " AND relative_path_key<?))")
                        bind += [root,norm,root,norm+"\\",norm+"]"]
                    elif norm:
                        # No path id exposed for this subject: keep the original
                        # predicate, which needs fp joined but is still correct.
                        pieces.append(f"({root_expr}=? AND (fp.relative_path_key=? OR (fp.relative_path_key>=? AND fp.relative_path_key<?)))")
                        bind += [root,norm,norm+"\\",norm+"]"]
                    else:
                        pieces.append(f"({root_expr}=?)")
                        bind.append(root)
                    continue
                bind += [root,key]
            return "("+" OR ".join(pieces)+")",bind
        if kind == "composite":
            scopes=scope.get("scopes") or []
            if len(scopes)<2: raise QueryError("Composite scope requires at least two scopes")
            parts=[]; bind=[]
            for child in scopes:
                child_q=json.loads(json.dumps(q)); child_q["scope"]=child
                part, child_bind=self._scope_sql(child_q, field_map)
                # Project scope contributes TRUE to union and no restriction to intersection.
                if not part:
                    part="1"
                parts.append("("+part+")"); bind += child_bind
            joiner=" OR " if scope.get("combine")=="union" else " AND "
            return "("+joiner.join(parts)+")", bind
        if kind == "saved_query":
            if not self.saved_store: raise QueryError("Saved-query scope requires a SavedQueryStore")
            rev=self.saved_store.get_revision(scope["saved_query_revision_id"])
            sub=rev["query"]
            if sub.get("subject",{}).get("entity") != "file":
                raise QueryError("Only file-result saved queries can be used as a scope in P2.9")
            sub_norm=self.normalize(sub, self._resolve_bindings(scope.get("bindings") or {}, q.get("_resolved_parameters") or {}, q))
            sub_sql, sub_bind=self._compile(sub_norm, ids_only=True)
            if entity == "file":
                id_expr="fs.file_path_id" if temporal=="current" else "o.file_path_id"
            elif entity == "analysis_result": id_expr="ca.file_path_id"
            else: raise QueryError("Saved file-query scope is supported for file/analysis subjects in P2.9")
            return f"{id_expr} IN (SELECT entity_id FROM ({sub_sql}))", sub_bind
        raise QueryError(f"Unsupported scope kind: {kind}")

    def _resolve_bindings(self, bindings, parent_params, q):
        out={}
        now=_parse_utc(q.get("_normalized_execution_utc") or utc_now())
        for name,expr in bindings.items(): out[name]=_resolve_value(expr,parent_params,now)
        return out

    def _compile_predicate(self, pred, fmap, q, aggregate_aliases=None, group_aliases=None):
        bind=[]
        if "all" in pred:
            parts=[]
            for child in pred["all"]:
                s,b=self._compile_predicate(child,fmap,q,aggregate_aliases,group_aliases); parts.append("("+s+")"); bind+=b
            return " AND ".join(parts),bind
        if "any" in pred:
            parts=[]
            for child in pred["any"]:
                s,b=self._compile_predicate(child,fmap,q,aggregate_aliases,group_aliases); parts.append("("+s+")"); bind+=b
            return " OR ".join(parts),bind
        if "not" in pred:
            s,b=self._compile_predicate(pred["not"],fmap,q,aggregate_aliases,group_aliases); return "NOT ("+s+")",b
        if "condition" in pred:
            c=pred["condition"]; ref=c["left"]; kind=ref["kind"]; rid=ref["id"]
            if kind=="field": expr=fmap.get(rid)
            elif kind=="aggregate": expr=(aggregate_aliases or {}).get(rid,rid)
            elif kind=="group": expr=(group_aliases or {}).get(rid,rid)
            else: expr=None
            if not expr: raise QueryError(f"Unknown field/reference: {rid}")
            op=c["op"]
            if op=="is_null": return f"{expr} IS NULL",[]
            if op=="is_not_null": return f"{expr} IS NOT NULL",[]
            if op=="is_true": return f"{expr}=1",[]
            if op=="is_false": return f"{expr}=0",[]
            now=_parse_utc(q.get("_normalized_execution_utc") or utc_now()); params=q.get("_resolved_parameters") or {}
            if op in ("in","not_in"):
                vals=[_resolve_value(v,params,now) for v in c.get("values",[])]
                if not vals: return ("0" if op=="in" else "1"),[]
                return f"{expr} {'IN' if op=='in' else 'NOT IN'} ({','.join('?'*len(vals))})",vals
            if op=="between":
                vals=[_resolve_value(v,params,now) for v in c.get("values",[])]
                if len(vals)!=2: raise QueryError("between requires two values")
                return f"{expr} BETWEEN ? AND ?",vals
            val=_resolve_value(c.get("value"),params,now)
            sqlops={"eq":"=","ne":"<>","gt":">","gte":">=","lt":"<","lte":"<=","before":"<","after":">","on":"="}
            if op in sqlops: return f"{expr} {sqlops[op]} ?",[val]
            if op in ("contains","not_contains","starts_with","ends_with"):
                sval=str(val)
                if op in ("contains","not_contains"): sval="%"+sval+"%"
                elif op=="starts_with": sval=sval+"%"
                else: sval="%"+sval
                return f"lower({expr}) {'NOT LIKE' if op=='not_contains' else 'LIKE'} lower(?)",[sval]
            raise QueryError(f"Unsupported operator: {op}")
        if "relation" in pred:
            rel=pred["relation"]
            if rel.get("relation") != "current_analysis":
                raise QueryError("P2.9 relation predicate currently supports current_analysis")
            # Compile relation fields using an isolated current-analysis alias set.
            sub_map={}
            for k, v in ANALYSIS_FIELDS.items():
                expr=v.replace("ca.analyzer_key", "aa.analyzer_key")
                expr=expr.replace("ca.", "ra.").replace("fp.", "rfp.")
                expr=expr.replace("ec.", "rec.").replace("ars.", "rars.")
                sub_map[k]=expr
            child_sql, child_bind=self._compile_predicate(rel["where"],sub_map,q)
            outer_entity=q["subject"]["entity"]
            if outer_entity != "file": raise QueryError("current_analysis relation currently starts from file")
            exists=f"""EXISTS (
                SELECT 1 FROM analyzer_result ra
                JOIN analyzer_run rrr ON rrr.analyzer_run_id=ra.analyzer_run_id
                JOIN analyzer aa ON aa.analyzer_id=rrr.analyzer_id
                JOIN file_observation rfo ON rfo.file_observation_id=ra.file_observation_id
                JOIN file_path rfp ON rfp.file_path_id=rfo.file_path_id
                LEFT JOIN extracted_content rec ON rec.analyzer_result_id=ra.analyzer_result_id
                LEFT JOIN archive_summary rars ON rars.analyzer_result_id=ra.analyzer_result_id
                WHERE rfo.file_path_id=fs.file_path_id AND ra.file_observation_id=fs.current_observation_id
                  AND NOT EXISTS (SELECT 1 FROM analyzer_result rn JOIN analyzer_run rnr ON rnr.analyzer_run_id=rn.analyzer_run_id
                    WHERE rn.file_observation_id=ra.file_observation_id AND rnr.analyzer_id=rrr.analyzer_id
                      AND (rn.analyzed_utc>ra.analyzed_utc OR (rn.analyzed_utc=ra.analyzed_utc AND rn.analyzer_result_id>ra.analyzer_result_id)))
                  AND ({child_sql})
            )"""
            if rel.get("quantifier")=="none": exists="NOT ("+exists+")"
            elif rel.get("quantifier")=="all":
                # all X == no current related row that fails X; vacuous truth intentionally avoided by requiring one current row.
                neg=exists.replace("AND ("+child_sql+")", "AND NOT ("+child_sql+")")
                exists="("+exists+" AND NOT ("+neg+"))"
                child_bind=child_bind+child_bind
            return exists,child_bind
        if "text_match" in pred:
            if q["subject"]["entity"] != "file" or q["subject"]["temporal"]["mode"] != "current":
                raise QueryError("P2.9 literal text matching currently targets current file results")
            if self.fts_manager is None:
                raise QueryError("Literal text search is unavailable until an FtsManager is attached")
            tm=pred["text_match"]; now=_parse_utc(q.get("_normalized_execution_utc") or utc_now()); params=q.get("_resolved_parameters") or {}
            value=_resolve_value(tm["query"],params,now)
            return self.fts_manager.exists_sql_for_current_file(tm.get("mode","term"),value)
        if "membership" in pred:
            raise QueryError("membership predicate not yet implemented in P2.9 core")
        if "fragment_ref" in pred:
            raise QueryError("Query fragments require a fragment registry; deferred from P2.9 initial implementation")
        raise QueryError(f"Unsupported predicate: {pred}")

    def _group_expr(self, g, fmap):
        field=g["field"]; expr=fmap.get(field)
        if not expr: raise QueryError(f"Unknown group field: {field}")
        bucket=g.get("bucket")
        if bucket:
            if bucket["kind"]=="date_part":
                fmt={"year":"%Y","month":"%Y-%m","day":"%Y-%m-%d","quarter":None}[bucket["part"]]
                if bucket["part"]=="quarter":
                    expr=f"CASE WHEN {expr} IS NULL THEN NULL ELSE substr({expr},1,4)||'-Q'||(((CAST(substr({expr},6,2) AS INTEGER)-1)/3)+1) END"
                else: expr=f"strftime('{fmt}', {expr})"
            elif bucket["kind"]=="numeric_ranges":
                bounds=bucket.get("boundaries") or []
                cases=[]; last=None
                for i,b in enumerate(bounds):
                    if i==0: cases.append(f"WHEN {expr} < {float(b)} THEN '< {b}'")
                    else: cases.append(f"WHEN {expr} < {float(b)} THEN '{last}–{b}'")
                    last=b
                expr="CASE WHEN "+expr+" IS NULL THEN NULL "+" ".join(cases)+(f" ELSE '≥ {last}' END" if last is not None else " END")
        if g.get("nonvalue_policy","separate_states")=="separate_states":
            expr=f"CASE WHEN {expr} IS NULL THEN 'Unknown/No value' ELSE {expr} END"
        return expr

    def _compile(self, q, cursor=None, external_limit=None, ids_only=False,
                 id_constraint_sql=None, id_constraint_bind=None):
        entity=q["subject"]["entity"]; temporal=q["subject"]["temporal"]["mode"]
        if entity=="folder": raise QueryError("folder subject uses specialized executor")
        fmap=self._field_map(entity,temporal)
        base,id_expr=self._base(q)
        cte_prefix=""
        if base.lstrip().startswith("WITH "):
            # split at last ) followed by FROM current_analysis is cumbersome; the returned string includes CTE+FROM.
            pos=base.find("FROM current_analysis")
            cte_prefix=base[:pos]
            base=base[pos:]
        where=[]; bind=[]
        if entity=="file" and temporal=="current":
            states=q["subject"].get("current_file_states") or ["present"]
            where.append("fs.state IN (%s)" % ",".join("?"*len(states))); bind += states
        if entity=="analysis_result" and temporal=="current":
            # file current state may be non-present; ordinary analysis current includes current observation regardless of state.
            pass
        if temporal=="run":
            run_id=q["subject"]["temporal"].get("run_id")
            where.append("r.run_id=?"); bind.append(run_id)
        scope_sql,scope_bind=self._scope_sql(q,fmap)
        if scope_sql: where.append(scope_sql); bind+=scope_bind
        if id_constraint_sql:
            where.append(f"{id_expr} IN ({id_constraint_sql})")
            bind += list(id_constraint_bind or [])
        if q.get("where"):
            s,b=self._compile_predicate(q["where"],fmap,q); where.append(s); bind+=b

        group_defs=q.get("group_by") or []
        aggs=q.get("aggregates") or []
        group_aliases={}; agg_aliases={}
        select=[]; group_sql=[]
        if ids_only:
            if group_defs or aggs: raise QueryError("Grouped query cannot be used as a file-ID saved scope")
            select=[f"{id_expr} AS entity_id"]
        elif group_defs or aggs:
            for g in group_defs:
                expr=self._group_expr(g,fmap); alias=g["id"]; group_aliases[alias]=alias
                select.append(f"{expr} AS \"{alias}\""); group_sql.append(expr)
            for a in aggs:
                aid=a["id"]; fn=a["function"]; field=a.get("field")
                if fn=="count": expr="COUNT(*)"
                elif fn=="count_distinct": expr=f"COUNT(DISTINCT {fmap[field]})"
                elif fn in ("sum","avg","min","max"):
                    if field not in fmap: raise QueryError(f"Unknown aggregate field: {field}")
                    expr=f"{fn.upper()}({fmap[field]})"
                    if a.get("evidence_policy","known_only_with_disclosure")=="known_only_with_disclosure":
                        select.append(f"SUM(CASE WHEN {fmap[field]} IS NULL THEN 1 ELSE 0 END) AS \"{aid}__nonknown\"")
                elif fn=="percent_of_total":
                    # Compiler implements percentage of a previously-defined aggregate by a window over grouped rows.
                    ref=a.get("of"); refexpr=agg_aliases.get(ref)
                    if not refexpr: raise QueryError("percent_of_total references unknown aggregate")
                    expr=f"(100.0*({refexpr})/NULLIF(SUM({refexpr}) OVER (),0))"
                else: raise QueryError(f"Unsupported aggregate: {fn}")
                select.append(f"{expr} AS \"{aid}\""); agg_aliases[aid]=expr
        else:
            cols = DEFAULT_FILE_COLUMNS if entity=="file" and temporal=="current" else DEFAULT_HISTORY_COLUMNS if entity=="file" else DEFAULT_ANALYSIS_COLUMNS if entity=="analysis_result" else DEFAULT_DUP_COLUMNS if entity=="duplicate_group" else DEFAULT_RUN_COLUMNS
            for fid in cols:
                if fid in fmap: select.append(f"{fmap[fid]} AS \"{fid}\"")

        sql=cte_prefix+"SELECT "+", ".join(select)+" "+base
        if where: sql += " WHERE "+" AND ".join("("+w+")" for w in where)
        if group_sql: sql += " GROUP BY "+", ".join(group_sql)
        if q.get("having"):
            hs,hb=self._compile_predicate(q["having"],fmap,q,agg_aliases,group_aliases); sql += " HAVING "+hs; bind+=hb
        sort_parts=[]
        for s in q.get("sort") or []:
            ref=s["ref"]; direction=s.get("direction","asc").upper(); nulls=s.get("nulls")
            if ref["kind"]=="field": expr=fmap.get(ref["id"])
            elif ref["kind"]=="group": expr='"'+ref["id"]+'"'
            else: expr='"'+ref["id"]+'"'
            if not expr: raise QueryError(f"Unknown sort reference: {ref}")
            if nulls: sort_parts.append(f"({expr} IS NULL) {'ASC' if nulls=='last' else 'DESC'}")
            sort_parts.append(f"{expr} {direction}")
        # deterministic row tie-breaker for ungrouped result browsing.
        if not group_defs and not aggs and not ids_only:
            if not sort_parts: sort_parts=[f"{id_expr} ASC"]
            elif all(id_expr not in x for x in sort_parts): sort_parts.append(f"{id_expr} ASC")
        if sort_parts: sql += " ORDER BY "+", ".join(sort_parts)
        lim = q.get("semantic_limit") or external_limit
        if lim: sql += " LIMIT ?"; bind.append(int(lim))
        return sql,bind

    def _execute_expanded(self, q, external_limit=None):
        """Execute the P2.4 current exact-duplicate member expansion.

        Seed filters/scope select the starting File Locations. Expansion then
        includes related current exact-duplicate members even when those related
        locations fall outside the seed filter/scope. Grouping/aggregation, when
        present, applies to the expanded population.
        """
        if q["subject"].get("entity") != "file" or (q["subject"].get("temporal") or {}).get("mode") != "current":
            raise QueryError("P2.9 relation expansion currently supports current file queries only")
        expansions=q.get("expand") or []
        if len(expansions) != 1:
            raise QueryError("P2.9 supports one relation expansion per query")
        exp=expansions[0]
        if exp.get("relation") != "current_exact_duplicate_members":
            raise QueryError("P2.9 expansion currently supports current_exact_duplicate_members")
        if not exp.get("deduplicate", True):
            raise QueryError("P2.9 file expansion requires deduplicate=true")
        ensure_duplicate_projection(self.conn)

        seed=json.loads(json.dumps(q))
        seed.pop("expand",None)
        seed.pop("group_by",None); seed.pop("aggregates",None); seed.pop("having",None)
        seed.pop("sort",None); seed.pop("semantic_limit",None)
        seed_sql, seed_bind=self._compile(seed, ids_only=True)
        if exp.get("include_seed", True):
            candidate=(
                "WITH seed(entity_id) AS ("+seed_sql+") "
                "SELECT entity_id FROM seed UNION "
                "SELECT m2.file_path_id FROM seed s "
                "JOIN p2_current_duplicate_member m1 ON m1.file_path_id=s.entity_id "
                "JOIN p2_current_duplicate_member m2 ON m2.content_id=m1.content_id"
            )
        else:
            candidate=(
                "WITH seed(entity_id) AS ("+seed_sql+") "
                "SELECT DISTINCT m2.file_path_id FROM seed s "
                "JOIN p2_current_duplicate_member m1 ON m1.file_path_id=s.entity_id "
                "JOIN p2_current_duplicate_member m2 ON m2.content_id=m1.content_id "
                "WHERE m2.file_path_id NOT IN (SELECT entity_id FROM seed)"
            )

        final=json.loads(json.dumps(q))
        final.pop("expand",None); final.pop("where",None)
        final["scope"]={"kind":"project"}
        sql, bind=self._compile(final, external_limit=external_limit,
                               id_constraint_sql=candidate, id_constraint_bind=seed_bind)
        rows=[dict(r) for r in self.conn.execute(sql,bind).fetchall()]
        return {"rows":rows,"columns":list(rows[0]) if rows else [],
                "next_cursor":self._next_cursor(final,rows)}

    def _next_cursor(self,q,rows):
        # P2.9 GUI primarily uses id-keyset helper below. General multi-column cursor
        # representation is kept explicit but execution support is intentionally narrow.
        if not rows: return None
        entity=q["subject"]["entity"]
        if entity=="file" and not q.get("group_by") and not q.get("aggregates"):
            return {"path.id": rows[-1].get("path.id")}
        return None

    def list_files(self, scope=None, search=None, filters=None, limit=200, after_id=None):
        """Fast ordinary Files-workspace query with location-id cursor."""
        q={
            "query_schema":QUERY_SCHEMA,"semantic_contract":SEMANTIC_CONTRACT,
            "subject":{"entity":"file","temporal":{"mode":"current"},"current_file_states":["present"]},
            "scope":scope or {"kind":"project"},
        }
        preds=list(filters or [])
        if search:
            preds.append({"any":[
                {"condition":{"left":{"kind":"field","id":"path.file_name"},"op":"contains","value":{"kind":"literal","value":search}}},
                {"condition":{"left":{"kind":"field","id":"path.relative"},"op":"contains","value":{"kind":"literal","value":search}}},
            ]})
        if after_id:
            preds.append({"condition":{"left":{"kind":"field","id":"path.id"},"op":"gt","value":{"kind":"literal","value":after_id}}})
        if preds: q["where"]={"all":preds} if len(preds)>1 else preds[0]
        q["sort"]=[{"ref":{"kind":"field","id":"path.id"},"direction":"asc"}]
        return self.execute(q,limit=limit)

    def _execute_folders(self,q):
        """Specialized analytical folder backend derived only from stored current paths."""
        states=q["subject"].get("current_file_states") or ["present"] if q["subject"].get("current_file_states") else ["present"]
        scope=q.get("scope") or {"kind":"project"}
        where=["fs.state='present'"]; bind=[]
        if scope.get("kind")=="source_roots":
            ids=scope["source_root_ids"]; where.append("fs.source_root_id IN (%s)"%",".join("?"*len(ids))); bind+=ids
        elif scope.get("kind")=="folder_subtrees":
            pieces=[]
            for f in scope["folders"]:
                root=int(f["source_root_id"]); norm=(f.get("relative_path_key") or "").replace("/","\\").strip("\\").lower()
                if norm:
                    pieces.append("(fs.source_root_id=? AND (fp.relative_path_key=? OR (fp.relative_path_key>=? AND fp.relative_path_key<?)))")
                    bind += [root,norm,norm+"\\",norm+"]"]
                else:
                    pieces.append("(fs.source_root_id=?)"); bind.append(root)
            where.append("("+" OR ".join(pieces)+")")
        rows=self.conn.execute(
            "SELECT fs.source_root_id,fp.relative_path,fs.size_bytes FROM file_state fs JOIN file_path fp ON fp.file_path_id=fs.file_path_id WHERE "+" AND ".join(where),bind
        ).fetchall()
        folders={}
        for r in rows:
            rel=(r["relative_path"] or "").replace("/","\\").strip("\\")
            parts=rel.split("\\")[:-1]
            size=int(r["size_bytes"] or 0)
            for i in range(len(parts)+1):
                folder="\\".join(parts[:i])
                key=(r["source_root_id"],folder)
                rec=folders.setdefault(key,{"source_root.id":r["source_root_id"],"folder.relative_path":folder,
                    "folder.parent":"\\".join(parts[:max(0,i-1)]),"folder.depth":i,
                    "folder.location_count_direct":0,"folder.location_count_recursive":0,
                    "folder.logical_bytes_direct":0,"folder.logical_bytes_recursive":0})
                rec["folder.location_count_recursive"]+=1; rec["folder.logical_bytes_recursive"]+=size
                if i==len(parts): rec["folder.location_count_direct"]+=1; rec["folder.logical_bytes_direct"]+=size
        out=list(folders.values())
        # report folder queries currently sort direct semantic fields only.
        for s in reversed(q.get("sort") or []):
            fid=s["ref"]["id"]; rev=s.get("direction")=="desc"; null_last=s.get("nulls")=="last"
            out.sort(key=lambda x: (x.get(fid) is None, x.get(fid) if x.get(fid) is not None else 0), reverse=rev)
        lim=q.get("semantic_limit")
        if lim: out=out[:int(lim)]
        return {"rows":out,"columns":list(out[0]) if out else [],"next_cursor":None}
