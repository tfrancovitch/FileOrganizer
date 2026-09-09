-- The File Organizer — schema 008
-- Phase 2 core + P2.3 evidence-persistence reconciliation.
-- Additive only: source files are never touched by this migration.

-- P1-CORR-001: distinguish future known reuse facts from historical default 0.
ALTER TABLE extracted_content ADD COLUMN reuse_known INTEGER
    NOT NULL DEFAULT 0 CHECK (reuse_known IN (0,1));

-- Safely backfill content-addressed extraction identity when the persisted
-- artifact name itself proves the SHA-256. This does not read source files.
UPDATE extracted_content
   SET text_sha256 = lower(substr(replace(extracted_filename,'\','/'), -68, 64)),
       storage_mode = 'content_addressed'
 WHERE text_sha256 IS NULL
   AND extracted_filename IS NOT NULL
   AND length(replace(extracted_filename,'\','/')) >= 68
   AND lower(substr(replace(extracted_filename,'\','/'), -4)) = '.txt'
   AND lower(substr(replace(extracted_filename,'\','/'), -68, 64)) NOT GLOB '*[^0-9a-f]*'
   AND length(substr(replace(extracted_filename,'\','/'), -68, 64)) = 64;

-- P1-CORR-002: conservatively reconstruct archive listing completeness from
-- facts B6.2 already persisted. We never claim a complete list unless the
-- recorded member count equals the analyzer's total EntryCount.
INSERT OR IGNORE INTO archive_summary (
    analyzer_result_id, project_id, analysis_mode,
    entry_total_count, entry_recorded_count, entry_cap, truncated,
    total_uncompressed_bytes, total_compressed_bytes)
SELECT
    ar.analyzer_result_id,
    ar.project_id,
    CASE
      WHEN COALESCE(am.recorded_count,0) = CAST(json_extract(ar.detail_json,'$.EntryCount') AS INTEGER)
        THEN 'complete'
      WHEN COALESCE(am.recorded_count,0) = 0
        THEN 'summary_only'
      ELSE 'capped'
    END,
    CAST(json_extract(ar.detail_json,'$.EntryCount') AS INTEGER),
    COALESCE(am.recorded_count,0),
    NULL,
    CASE WHEN COALESCE(am.recorded_count,0) < CAST(json_extract(ar.detail_json,'$.EntryCount') AS INTEGER)
         THEN 1 ELSE 0 END,
    CAST(NULLIF(json_extract(ar.detail_json,'$.TotalUncompressedSize'),'') AS INTEGER),
    CAST(NULLIF(json_extract(ar.detail_json,'$.TotalCompressedSize'),'') AS INTEGER)
FROM analyzer_result ar
JOIN analyzer_run rr ON rr.analyzer_run_id = ar.analyzer_run_id
JOIN analyzer a ON a.analyzer_id = rr.analyzer_id AND a.analyzer_key='archive'
LEFT JOIN (
    SELECT analyzer_result_id, COUNT(*) AS recorded_count
      FROM archive_member GROUP BY analyzer_result_id
) am ON am.analyzer_result_id = ar.analyzer_result_id
WHERE ar.status='analyzed'
  AND json_extract(ar.detail_json,'$.EntryCount') IS NOT NULL;

-- Saved questions are durable; each revision is immutable.
CREATE TABLE p2_saved_query (
    saved_query_id      INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    saved_query_uid     TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    description         TEXT,
    created_utc         TEXT NOT NULL,
    updated_utc         TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES project(project_id) ON DELETE CASCADE
);

CREATE TABLE p2_saved_query_revision (
    saved_query_revision_id INTEGER NOT NULL PRIMARY KEY,
    saved_query_id          INTEGER NOT NULL,
    revision_number         INTEGER NOT NULL,
    parent_revision_id      INTEGER,
    query_schema            TEXT NOT NULL,
    semantic_contract       TEXT NOT NULL,
    query_json              TEXT NOT NULL,
    query_fingerprint       TEXT NOT NULL,
    created_utc             TEXT NOT NULL,
    UNIQUE(saved_query_id, revision_number),
    FOREIGN KEY(saved_query_id) REFERENCES p2_saved_query(saved_query_id) ON DELETE CASCADE,
    FOREIGN KEY(parent_revision_id) REFERENCES p2_saved_query_revision(saved_query_revision_id) ON DELETE SET NULL
);
CREATE INDEX ix_p2_saved_query_name ON p2_saved_query(project_id, name);
CREATE INDEX ix_p2_saved_query_revision_fingerprint ON p2_saved_query_revision(query_fingerprint);

-- Deliberately retained executions only: reports/exports/snapshots, not every keystroke.
CREATE TABLE p2_execution_record (
    execution_id        INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    saved_query_revision_id INTEGER,
    execution_uid       TEXT NOT NULL UNIQUE,
    execution_kind      TEXT NOT NULL CHECK(execution_kind IN ('report','export','snapshot','saved_query')),
    query_fingerprint   TEXT NOT NULL,
    normalized_query_json TEXT NOT NULL,
    parameter_json      TEXT,
    executed_utc        TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('completed','cancelled','failed')),
    result_count        INTEGER,
    coverage_json       TEXT,
    warnings_json       TEXT,
    engine_version      TEXT,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(saved_query_revision_id) REFERENCES p2_saved_query_revision(saved_query_revision_id) ON DELETE SET NULL
);
CREATE INDEX ix_p2_execution_time ON p2_execution_record(project_id, executed_utc);

-- Rebuildable analytical infrastructure registry.
CREATE TABLE p2_derived_index (
    derived_index_key   TEXT NOT NULL PRIMARY KEY,
    index_kind          TEXT NOT NULL,
    definition_version  TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('absent','building','valid','stale','failed')),
    built_utc           TEXT,
    input_signature     TEXT,
    row_count           INTEGER,
    storage_bytes       INTEGER,
    engine_version      TEXT,
    parameters_json     TEXT,
    notes               TEXT
);

-- Small rebuildable current exact-duplicate projection.
CREATE TABLE p2_current_duplicate_member (
    file_path_id        INTEGER NOT NULL PRIMARY KEY,
    content_id          INTEGER NOT NULL,
    source_root_id      INTEGER NOT NULL,
    size_bytes          INTEGER,
    physical_key        TEXT,
    file_name           TEXT,
    extension_key       TEXT,
    FOREIGN KEY(file_path_id) REFERENCES file_path(file_path_id) ON DELETE CASCADE,
    FOREIGN KEY(content_id) REFERENCES content(content_id) ON DELETE CASCADE,
    FOREIGN KEY(source_root_id) REFERENCES source_root(source_root_id) ON DELETE CASCADE
);
CREATE INDEX ix_p2_dup_member_content ON p2_current_duplicate_member(content_id);
CREATE INDEX ix_p2_dup_member_root ON p2_current_duplicate_member(source_root_id, content_id);

CREATE TABLE p2_current_duplicate_summary (
    content_id                  INTEGER NOT NULL PRIMARY KEY,
    location_count              INTEGER NOT NULL,
    root_count                  INTEGER NOT NULL,
    physical_copy_count         INTEGER,
    hard_link_alias_count       INTEGER,
    physical_identity_complete  INTEGER NOT NULL CHECK(physical_identity_complete IN (0,1)),
    size_bytes                  INTEGER,
    reclaimable_bytes           INTEGER,
    file_name_variant_count     INTEGER NOT NULL DEFAULT 0,
    extension_variant_count     INTEGER NOT NULL DEFAULT 0,
    logical_bytes_represented   INTEGER,
    FOREIGN KEY(content_id) REFERENCES content(content_id) ON DELETE CASCADE
);
CREATE INDEX ix_p2_dup_summary_reclaim ON p2_current_duplicate_summary(reclaimable_bytes DESC);
CREATE INDEX ix_p2_dup_summary_roots ON p2_current_duplicate_summary(root_count DESC);

-- Mapping for contentless FTS5 rows. The virtual table itself is created by
-- the Phase 2 FTS manager only when the runtime confirms FTS5 support.
CREATE TABLE p2_fts_text_map (
    fts_rowid           INTEGER NOT NULL PRIMARY KEY,
    text_sha256         TEXT NOT NULL UNIQUE,
    extracted_relpath   TEXT NOT NULL,
    artifact_bytes      INTEGER,
    built_from_extracted_content_id INTEGER,
    FOREIGN KEY(built_from_extracted_content_id)
      REFERENCES extracted_content(extracted_content_id) ON DELETE SET NULL
);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase2.query_schema','fileorganizer.query/1',strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('phase2.semantic_contract','fileorganizer.query-semantics/1',strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('phase2.core_version','P2.9',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
