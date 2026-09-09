"""Coverage/freshness summaries for GUI and result envelopes.

Query execution uses ``coverage_summary``: a lightweight scope-coverage check
that depends on scan/root evidence, not a corpus-wide recount. The Evidence
workspace uses ``evidence_health`` for the more expensive scoped health counts.
"""
from __future__ import annotations


def _scope_root_ids(conn, scope=None):
    """Conservatively resolve source roots relevant to a query scope."""
    scope = scope or {"kind": "project"}
    kind = scope.get("kind")
    if kind == "source_roots":
        return sorted({int(x) for x in (scope.get("source_root_ids") or [])})
    if kind == "folder_subtrees":
        return sorted({int(f["source_root_id"]) for f in (scope.get("folders") or [])})
    return [r[0] for r in conn.execute(
        "SELECT source_root_id FROM source_root WHERE project_id=1 AND is_active=1 ORDER BY source_root_id"
    )]


def _latest_scan_for_root(conn, root_id):
    return conn.execute(
        "SELECT inventory_scan_id,status,inaccessible_count,inaccessible_seen_count,"
        "inaccessible_truncated,root_availability,run_id "
        "FROM inventory_scan WHERE project_id=1 AND source_root_id=? "
        "ORDER BY inventory_scan_id DESC LIMIT 1", (root_id,)
    ).fetchone()


def coverage_summary(conn, scope=None):
    """Return lightweight, scope-aware observation coverage.

    This deliberately avoids scanning ``file_state`` or analyzer history so an
    ordinary query does not acquire O(corpus-size) overhead merely to attach a
    result-envelope coverage qualification.
    """
    result = {"coverage": "unknown", "scope_root_ids": [], "root_coverage": [], "warnings": []}
    roots = _scope_root_ids(conn, scope)
    result["scope_root_ids"] = roots
    complete = bool(roots)
    for root_id in roots:
        scan = _latest_scan_for_root(conn, root_id)
        if not scan:
            result["root_coverage"].append({"source_root_id": root_id, "coverage": "unknown", "scan_id": None})
            complete = False
            continue
        inaccessible = int(scan["inaccessible_seen_count"] or scan["inaccessible_count"] or 0)
        root_complete = (
            scan["status"] in ("completed", "completed_with_warnings")
            and scan["root_availability"] == "available"
            and inaccessible == 0
            and not int(scan["inaccessible_truncated"] or 0)
        )
        result["root_coverage"].append({
            "source_root_id": root_id,
            "coverage": "complete" if root_complete else "incomplete",
            "scan_id": scan["inventory_scan_id"],
            "run_id": scan["run_id"],
            "status": scan["status"],
            "root_availability": scan["root_availability"],
            "inaccessible_seen_count": inaccessible,
            "inaccessible_truncated": int(scan["inaccessible_truncated"] or 0),
        })
        complete = complete and root_complete
    if roots:
        result["coverage"] = "complete" if complete else "incomplete"
    if result["coverage"] != "complete":
        result["warnings"].append(
            "Selected scope contains unavailable, inaccessible, unverified, or otherwise incomplete observation evidence."
        )
    return result


def _file_scope_clause(scope, fs_alias="fs", fp_alias="fp"):
    """Compile the simple scope forms used by the Evidence workspace counts.

    Saved/composite scopes intentionally return no clause here; those belong to
    QueryEngine semantics, while full Evidence health falls back conservatively
    to project-wide counts for such complex scopes.
    """
    scope = scope or {"kind": "project"}
    kind = scope.get("kind")
    if kind == "project":
        return "", []
    if kind == "source_roots":
        ids = [int(x) for x in (scope.get("source_root_ids") or [])]
        if not ids:
            return "1=0", []
        return f"{fs_alias}.source_root_id IN ({','.join('?'*len(ids))})", ids
    if kind == "folder_subtrees":
        pieces=[]; bind=[]
        for f in scope.get("folders") or []:
            root=int(f["source_root_id"]); norm=(f.get("relative_path_key") or "").replace("/","\\").strip("\\").lower()
            if norm:
                pieces.append(f"({fs_alias}.source_root_id=? AND ({fp_alias}.relative_path_key=? OR ({fp_alias}.relative_path_key>=? AND {fp_alias}.relative_path_key<?)))")
                bind += [root,norm,norm+"\\",norm+"]"]
            else:
                pieces.append(f"({fs_alias}.source_root_id=?)"); bind.append(root)
        return ("("+" OR ".join(pieces)+")" if pieces else "1=0"), bind
    return "", []


def evidence_health(conn, scope=None):
    """Return detailed evidence health for an Evidence/Overview surface.

    Unlike ``coverage_summary``, this performs corpus/analyzer counts. Counts
    are scope-aware for project/source-root/folder scopes.
    """
    result = coverage_summary(conn, scope)
    result.update({
        "present_locations": 0,
        "inaccessible_locations": 0,
        "unverified_locations": 0,
        "missing_locations": 0,
        "stale_hashes": 0,
        "cloud_offline": 0,
        "current_analyzer_failures": 0,
    })
    clause, bind = _file_scope_clause(scope)
    join_fp = bool(clause and "fp." in clause)
    base = "FROM file_state fs" + (" JOIN file_path fp ON fp.file_path_id=fs.file_path_id" if join_fp else "")
    where = "WHERE fs.project_id=1" + (" AND ("+clause+")" if clause else "")

    for row in conn.execute(f"SELECT fs.state,COUNT(*) n {base} {where} GROUP BY fs.state", bind):
        result[row["state"] + "_locations"] = row["n"]
    result["stale_hashes"] = conn.execute(
        f"SELECT COUNT(*) {base} {where} AND fs.state='present' AND fs.content_id IS NOT NULL "
        "AND (fs.content_observation_id IS NULL OR fs.content_observation_id<>fs.current_observation_id)", bind
    ).fetchone()[0]
    result["cloud_offline"] = conn.execute(
        f"SELECT COUNT(*) {base} {where} AND fs.state='present' AND fs.is_offline_or_cloud=1", bind
    ).fetchone()[0]

    # Current analyzer attempts, then apply simple root/folder scope through the
    # current file location. This retains the latest-attempt semantics.
    ar_clause, ar_bind = _file_scope_clause(scope, "fs", "fp")
    ar_extra = (" AND ("+ar_clause+")" if ar_clause else "")
    result["current_analyzer_failures"] = conn.execute(
        """
        WITH current_ar AS (
          SELECT ar.analyzer_result_id, ar.status, a.analyzer_key,
                 ROW_NUMBER() OVER (
                   PARTITION BY fo.file_path_id,a.analyzer_key
                   ORDER BY ar.analyzed_utc DESC,ar.analyzer_result_id DESC) rn
            FROM analyzer_result ar
            JOIN analyzer_run rr ON rr.analyzer_run_id=ar.analyzer_run_id
            JOIN analyzer a ON a.analyzer_id=rr.analyzer_id
            JOIN file_observation fo ON fo.file_observation_id=ar.file_observation_id
            JOIN file_state fs ON fs.file_path_id=fo.file_path_id
                              AND fs.current_observation_id=ar.file_observation_id
            JOIN file_path fp ON fp.file_path_id=fs.file_path_id
           WHERE 1=1
        """ + ar_extra + """
        )
        SELECT COUNT(*) FROM current_ar WHERE rn=1 AND status='error'
        """, ar_bind
    ).fetchone()[0]

    # Preserve coverage warning, add health warnings.
    if result["stale_hashes"]:
        result["warnings"].append(f"{result['stale_hashes']} current locations have stale/non-authoritative content identity.")
    if result["current_analyzer_failures"]:
        result["warnings"].append(f"{result['current_analyzer_failures']} current analyzer attempts failed.")
    return result
