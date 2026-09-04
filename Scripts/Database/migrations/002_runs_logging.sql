-- ============================================================
--  The File Organizer -- Migration 002
--  Schema version: 2
--  Introduced: Version Beta, R2 (Logging + Run/Stage Persistence)
-- ============================================================
--
--  SCOPE OF SCHEMA VERSION 2
--
--  R2 establishes the OPERATIONAL RECORD: what the application did,
--  when, in what environment, and what went wrong while doing it.
--  This migration creates only the tables R2 code actually reads and
--  writes:
--
--      environment_snapshot  the machine/toolchain state a run ran under
--      run                   one execution session of the application
--      run_stage             one stage executed within that session
--      run_source_root       which roots a run was associated with
--      event                 structured warnings/errors/lifecycle records
--
--  Deliberately NOT created here:
--      file_path, file_observation, content, analysis_result,
--      duplicate_group  -> migrations 003+, when ingestion actually
--                          begins. R2 inserts NO inventory, file,
--                          content, duplicate, or analyzer rows. The
--                          At schema-2 introduction time the CSV pipeline was authoritative. B6.1 uses SQLite as authority; this comment is historical context only.
--
--  Same rationale as migration 001: migrations are forward-only and
--  additive, so a table costs nothing later but freezes design
--  decisions now, and a table no code writes to cannot be verified.
--  Every column below is written or read by R2 code.
--
--  WHAT "run" MEANS HERE
--  A run is ONE EXECUTION SESSION -- one operation the user started
--  (initial scan, duplicate hashing, full hashing, a set of analyzer
--  categories). It is NOT the Runs\<timestamp> output folder: that
--  folder is minted by PreliminaryInventory.ps1 (a protected Alpha
--  file) and is written to by several sessions over its lifetime.
--  run.run_folder records which folder a session wrote into, so the
--  two are relatable without conflating them. Only a session has a
--  start, an end, and a process that can die mid-way -- which is what
--  makes interruption detectable at all.
--
--  TERMINOLOGY NEUTRALITY
--  run.run_kind, run_stage.stage_key and run_stage.stage_role are
--  deliberately unconstrained TEXT. The workflow vocabulary (Duplicate
--  Run / Full Run / Choose What to Analyze) is known to be changing;
--  renaming a workflow must be a data concern, not a schema migration.
--  Only status vocabularies -- which the application branches on -- are
--  CHECK-constrained.
--
--  TRANSACTION CONTROL
--  This file contains no BEGIN/COMMIT. fo_db.py wraps the whole
--  migration -- DDL, bookkeeping row, and user_version bump -- in one
--  transaction so a failed migration leaves the database untouched.
-- ============================================================


-- ------------------------------------------------------------
-- environment_snapshot
--   The machine/toolchain state a run executed under, captured so
--   historical behaviour stays explicable ("that scan predates
--   LongPathsEnabled being turned on", "that build had pypdf 4.x").
--
--   Deduplicated by snapshot_hash: an unchanged machine produces one
--   row no matter how many runs reference it.
--
--   The frequently-queried facts are promoted to real columns. The
--   long tail -- per-dependency versions, per-drive characteristics --
--   lives in details_json rather than in a normalized dependency
--   table, because R2 only ever reads it back whole, for display. A
--   normalized table would be a design decision made before there is
--   a query to justify it.
--
--   Deliberately excluded: machine name, user name, domain, serial
--   numbers, MAC addresses, full user profile paths. Nothing here
--   identifies the operator or the machine.
-- ------------------------------------------------------------
CREATE TABLE environment_snapshot (
    environment_snapshot_id INTEGER NOT NULL PRIMARY KEY,
    project_id              INTEGER NOT NULL DEFAULT 1,
    snapshot_hash           TEXT    NOT NULL UNIQUE,
    captured_utc            TEXT    NOT NULL,

    os_caption              TEXT,
    os_version              TEXT,
    os_build                TEXT,
    powershell_version      TEXT,
    powershell_edition      TEXT,
    python_version          TEXT,
    sqlite_version          TEXT,
    app_version             TEXT,
    fo_db_module_version    TEXT,

    -- 1 = enabled, 0 = disabled, NULL = could not be determined.
    -- Both directly change what the processing engine can see.
    long_paths_enabled      INTEGER CHECK (long_paths_enabled IN (0, 1)),
    last_access_update      TEXT,

    details_json            TEXT    NOT NULL,

    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE
);


-- ------------------------------------------------------------
-- run
--   One execution session.
--
--   status vocabulary:
--     created                  row exists, work not started
--     running                  in progress; heartbeat_utc is live
--     completed                every stage finished cleanly
--     completed_with_warnings  finished, but warnings were recorded
--     failed                   a stage failed in a way that stopped the run
--     paused                   stopped at a checkpoint at the user's request
--     interrupted              the process disappeared while status was
--                              'running'; detected by reconciliation
--     cancelled                stopped deliberately, not at a checkpoint
--
--   'paused' is an addition to the vocabulary in the R2 brief. Pause is
--   already a first-class concept in this application (exit code 2, the
--   pause_requested.flag, the Paused screen). Recording a deliberate,
--   clean, resumable stop as 'interrupted' would make the one status
--   that should mean "something went wrong" mean nothing.
--
--   host_pid + process_started_utc + heartbeat_utc are what make stale
--   runs detectable. R2 RECORDS interrupted work; it does not compute
--   work-remaining or resume it. That is a later revision.
-- ------------------------------------------------------------
CREATE TABLE run (
    run_id                   INTEGER NOT NULL PRIMARY KEY,
    project_id               INTEGER NOT NULL DEFAULT 1,
    run_uid                  TEXT    NOT NULL UNIQUE,

    -- Name of the Runs\<folder> this session wrote into. NULL until
    -- known: an initial scan creates its run record before
    -- PreliminaryInventory.ps1 has minted the folder.
    run_folder               TEXT,

    run_kind                 TEXT    NOT NULL,
    run_label                TEXT,

    status                   TEXT    NOT NULL CHECK (status IN (
                                 'created', 'running', 'completed',
                                 'completed_with_warnings', 'failed',
                                 'paused', 'interrupted', 'cancelled')),

    started_utc              TEXT    NOT NULL,
    ended_utc                TEXT,
    duration_ms              INTEGER,

    heartbeat_utc            TEXT,
    host_pid                 INTEGER,
    process_started_utc      TEXT,

    environment_snapshot_id  INTEGER,

    app_version              TEXT    NOT NULL,
    schema_version           INTEGER NOT NULL,

    stage_count              INTEGER NOT NULL DEFAULT 0,
    warning_count            INTEGER NOT NULL DEFAULT 0,
    error_count              INTEGER NOT NULL DEFAULT 0,

    -- Set when reconciliation, not the run itself, decided the outcome.
    reconciled_utc           TEXT,
    notes                    TEXT,

    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (environment_snapshot_id)
        REFERENCES environment_snapshot (environment_snapshot_id)
);

CREATE INDEX ix_run_project_started ON run (project_id, started_utc);
CREATE INDEX ix_run_status          ON run (status);
CREATE INDEX ix_run_folder          ON run (run_folder);


-- ------------------------------------------------------------
-- run_stage
--   One stage executed within a session, in order.
--
--   status vocabulary adds three outcomes to run's, all of which the
--   R2 brief calls for keeping distinguishable:
--     skipped              not run (not selected, or a prerequisite
--                          was absent); skip_reason says which
--     no_applicable_files  ran, exited cleanly, found nothing of its
--                          type to work on -- an empty RAW analyzer on
--                          a project with no RAW files is not a failure
--                          and not a skip
--     interrupted          the process disappeared while this stage
--                          was the running one
--
--   stdout_log_path / stderr_log_path are stored RELATIVE to the run
--   folder, so a project folder stays movable.
-- ------------------------------------------------------------
CREATE TABLE run_stage (
    run_stage_id      INTEGER NOT NULL PRIMARY KEY,
    run_id            INTEGER NOT NULL,
    project_id        INTEGER NOT NULL DEFAULT 1,

    sequence          INTEGER NOT NULL,
    stage_key         TEXT    NOT NULL,
    stage_label       TEXT,
    stage_role        TEXT,

    status            TEXT    NOT NULL CHECK (status IN (
                          'created', 'running', 'completed',
                          'completed_with_warnings', 'failed',
                          'paused', 'interrupted', 'skipped',
                          'no_applicable_files', 'cancelled')),
    skip_reason       TEXT,

    command           TEXT,
    exit_code         INTEGER,

    started_utc       TEXT,
    ended_utc         TEXT,
    duration_ms       INTEGER,

    stdout_log_path   TEXT,
    stderr_log_path   TEXT,

    warning_count     INTEGER NOT NULL DEFAULT 0,
    error_count       INTEGER NOT NULL DEFAULT 0,

    notes             TEXT,

    UNIQUE (run_id, sequence),
    FOREIGN KEY (run_id)     REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE
);

CREATE INDEX ix_run_stage_run    ON run_stage (run_id, sequence);
CREATE INDEX ix_run_stage_status ON run_stage (status);


-- ------------------------------------------------------------
-- run_source_root
--   Which source roots a session was associated with, and what state
--   they were in at the time.
--
--   was_scanned distinguishes "this root belongs to the project and
--   the run knew about it" from "the processing engine actually walked
--   it". During Beta the Alpha engine still scans the single
--   settings.json TargetPath, so a multi-root project records its
--   other roots with was_scanned = 0. Recording them as covered would
--   be a comfortable lie that later duplicate analysis would trip over.
-- ------------------------------------------------------------
CREATE TABLE run_source_root (
    run_source_root_id INTEGER NOT NULL PRIMARY KEY,
    run_id             INTEGER NOT NULL,
    source_root_id     INTEGER NOT NULL,
    project_id         INTEGER NOT NULL DEFAULT 1,

    root_path_at_run   TEXT    NOT NULL,
    was_scanned        INTEGER NOT NULL DEFAULT 0 CHECK (was_scanned IN (0, 1)),
    was_available      INTEGER CHECK (was_available IN (0, 1)),

    drive_type         TEXT,
    filesystem         TEXT,
    total_bytes        INTEGER,
    free_bytes         INTEGER,

    UNIQUE (run_id, source_root_id),
    FOREIGN KEY (run_id)         REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (source_root_id) REFERENCES source_root (source_root_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id)     REFERENCES project (project_id) ON DELETE CASCADE
);


-- ------------------------------------------------------------
-- event
--   Structured application events: lifecycle, warnings, errors.
--
--   One unified model rather than per-analyzer error tables. The
--   columns are chosen to answer, for any recorded problem:
--     which project   -- this database is the project
--     which run       -- run_id
--     which stage     -- run_stage_id / stage_key
--     which file      -- file_path (NULL when not file-specific)
--     when            -- event_utc
--     how bad         -- severity
--     what happened   -- message / error_type / detail_json
--     did processing continue    -- continued
--     was the file skipped       -- file_skipped
--     is a retry worth trying    -- retryable
--
--   run_id is nullable: an application-level failure can occur before
--   any run exists. (An application-level failure occurring before a
--   PROJECT exists has no database to be written to at all, and goes
--   to Logs\app.log -- see fo_log.py.)
--
--   source records HOW the event was obtained, because in R2 not all
--   events are equal. The Alpha processing scripts are protected and
--   were not modified, so their problems are recovered by reading
--   their captured output and their errors.txt. Those events are
--   marked 'stage_stdout' / 'stage_stderr' / 'errors_txt' and are
--   inferred, not declared. Events a script emitted deliberately are
--   marked 'powershell' or 'python'. Mixing the two without a
--   distinction would present a guess with the same authority as a
--   fact.
--
--   The existing CSV Error columns are unchanged and remain
--   authoritative for per-file analyzer errors during Beta.
-- ------------------------------------------------------------
CREATE TABLE event (
    event_id      INTEGER NOT NULL PRIMARY KEY,
    project_id    INTEGER NOT NULL DEFAULT 1,
    run_id        INTEGER,
    run_stage_id  INTEGER,

    -- Ordinal within the run. Timestamps alone tie at millisecond
    -- resolution when a stage emits a burst of file errors.
    seq           INTEGER,

    event_utc     TEXT    NOT NULL,

    severity      TEXT    NOT NULL CHECK (severity IN (
                      'debug', 'info', 'warning', 'error', 'critical')),
    category      TEXT    NOT NULL,
    source        TEXT    NOT NULL,

    stage_key     TEXT,
    file_path     TEXT,

    message       TEXT    NOT NULL,
    error_type    TEXT,

    continued     INTEGER CHECK (continued    IN (0, 1)),
    file_skipped  INTEGER CHECK (file_skipped IN (0, 1)),
    retryable     INTEGER CHECK (retryable    IN (0, 1)),

    detail_json   TEXT,

    FOREIGN KEY (project_id)   REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id)       REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_stage_id) REFERENCES run_stage (run_stage_id) ON DELETE CASCADE
);

CREATE INDEX ix_event_run      ON event (run_id, seq);
CREATE INDEX ix_event_stage    ON event (run_stage_id);
CREATE INDEX ix_event_severity ON event (severity, event_utc);
