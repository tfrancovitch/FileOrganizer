-- ============================================================
--  The File Organizer -- Migration 001
--  Schema version: 1
--  Introduced: Version Beta, R1 (Database Foundation)
-- ============================================================
--
--  SCOPE OF SCHEMA VERSION 1
--
--  R1 is the database FOUNDATION revision. This migration creates
--  only the tables that R1 code actually reads and writes:
--
--      schema_migration   migration audit trail
--      app_meta           key/value database identity + settings
--      project            the single project this database serves
--      source_root        one or more scanned roots for that project
--
--  Deliberately NOT created here (see the R1 design summary):
--      run, run_stage, run_source_root, environment_snapshot
--          -> migration 002, at R2, where run records are first written
--      file_path, file_observation, content, analysis_result, event
--          -> migrations 003+, at R3-R5, where they are first populated
--
--  Rationale: migrations are forward-only and additive, so creating a
--  table costs nothing later but freezes design decisions now. A table
--  no code writes to cannot be verified, and an unverified table in a
--  "clean integrity_check" reads as more assurance than it is.
--
--  TRANSACTION CONTROL
--  This file contains no BEGIN/COMMIT. fo_db.py wraps the whole
--  migration -- DDL, bookkeeping row, and user_version bump -- in one
--  transaction so a failed migration leaves the database untouched.
-- ============================================================


-- ------------------------------------------------------------
-- schema_migration
--   Audit trail of every migration applied to this database.
--   PRAGMA user_version holds the same number for cheap checking
--   without a query; this table holds the history and the context.
-- ------------------------------------------------------------
CREATE TABLE schema_migration (
    version      INTEGER NOT NULL PRIMARY KEY,
    description  TEXT    NOT NULL,
    applied_utc  TEXT    NOT NULL,
    app_version  TEXT    NOT NULL,
    duration_ms  INTEGER
);


-- ------------------------------------------------------------
-- app_meta
--   Small key/value store for database-level identity and
--   settings. Used in R1 for the database UID and creation
--   provenance, which are what make the project-isolation check
--   possible.
-- ------------------------------------------------------------
CREATE TABLE app_meta (
    key          TEXT NOT NULL PRIMARY KEY,
    value        TEXT,
    updated_utc  TEXT NOT NULL
);


-- ------------------------------------------------------------
-- project
--   Exactly one row. The CHECK constraint is the schema-level
--   enforcement of the hard project-isolation rule: this database
--   cannot physically hold a second project's row.
--
--   project_id is carried on child tables anyway (it is always 1)
--   so that a future EXPLICIT, user-initiated cross-project
--   comparison or merge is an ATTACH plus INSERT...SELECT rather
--   than a redesign. It never enables automatic aggregation:
--   nothing in the application opens two project databases at once.
-- ------------------------------------------------------------
CREATE TABLE project (
    project_id            INTEGER NOT NULL PRIMARY KEY CHECK (project_id = 1),
    project_uid           TEXT    NOT NULL UNIQUE,
    name                  TEXT    NOT NULL,
    created_utc           TEXT    NOT NULL,
    app_version_created   TEXT    NOT NULL,
    notes                 TEXT
);


-- ------------------------------------------------------------
-- source_root
--   A directory this project scans. One project may have many.
--
--   root_path      exactly as Windows reported it (display form)
--   root_path_key  invariant-lowercase, trailing separator stripped;
--                  uniqueness only. Windows paths are case-insensitive,
--                  so C:\Docs and c:\docs must not become two roots.
--   removed_utc    soft removal -- history stays interpretable after a
--                  root is dropped from the project.
-- ------------------------------------------------------------
CREATE TABLE source_root (
    source_root_id  INTEGER NOT NULL PRIMARY KEY,
    project_id      INTEGER NOT NULL,
    root_path       TEXT    NOT NULL,
    root_path_key   TEXT    NOT NULL,
    label           TEXT,
    added_utc       TEXT    NOT NULL,
    removed_utc     TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE,
    UNIQUE (project_id, root_path_key)
);

CREATE INDEX ix_source_root_project ON source_root (project_id, is_active);
