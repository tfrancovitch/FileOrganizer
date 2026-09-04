-- ============================================================
--  The File Organizer -- Migration 003
--  Schema version: 3
--  Introduced: Version Beta, R3 (SQLite Inventory Ingestion)
-- ============================================================
--
--  SCOPE OF SCHEMA VERSION 3
--
--  R3 answers: "What files did this project observe during an
--  inventory scan?"
--
--      inventory_scan     one scan of one source root within one run
--      file_path          a durable LOCATION identity within a root
--      file_observation   what one scan saw at one location
--
--  Deliberately NOT created here:
--      duplicate_group, duplicate_member   -> R4
--      analysis_result, analyzer tables    -> R5
--      content / extracted-text storage    -> R9
--
--  Same discipline as migrations 001 and 002: every column below is
--  written or read by R3 code. A table no code writes to cannot be
--  verified, and an unverified table inside a clean integrity_check
--  reads as more assurance than it is.
--
-- ------------------------------------------------------------
--  THE CENTRAL DISTINCTION: IDENTITY vs OBSERVATION
-- ------------------------------------------------------------
--
--  These are three different things and R3 keeps them apart:
--
--    LOCATION identity   file_path
--        "Photos\image.jpg under source root 2 is a place this
--         project knows about."
--        Created once, never rewritten, survives re-scans.
--
--    OBSERVATION         file_observation
--        "On scan 7, that location held 1,048,576 bytes, last written
--         2026-08-14, Archive attributes."
--        Append-only. One row per (scan, location).
--
--    CONTENT identity    NOT IN R3
--        "These bytes hash to abc123."
--        Belongs to R4, where hashes are persisted. A path is NOT a
--        content identity: a file can be replaced in place, and two
--        paths can hold identical bytes.
--
--  Collapsing location and observation into one row would mean each
--  scan overwriting the last, which destroys the history needed to
--  answer "is this new?", "is it missing?" and "did it change?" -- the
--  re-scan questions this schema is explicitly required to keep
--  possible. It would also force a destructive redesign at R4.
--
--  file_path is a LOCATION identity, not a "physical file" identity.
--  Nothing here claims that the same path across two scans is the same
--  bytes. That claim requires a hash and arrives in R4.
--
-- ------------------------------------------------------------
--  ON Alpha's DB_ID
-- ------------------------------------------------------------
--
--  DB_ID is recorded as file_observation.legacy_db_id and is NOT a key
--  of anything. It is allocated per scan from settings.json's NextDBID
--  counter, so the SAME file receives a DIFFERENT DB_ID on a later
--  scan, and a DB_ID identifies a row in one CSV rather than a file in
--  the project. It is preserved because it is what makes a database
--  row traceable back to a line in the Alpha CSV -- which is exactly
--  what R6 will need to prove the two agree. It is not promoted to a
--  primary key, because it is not stable.
--
-- ------------------------------------------------------------
--  MULTI-ROOT CORRECTNESS
-- ------------------------------------------------------------
--
--  Uniqueness is (source_root_id, relative_path_key), never path
--  alone. So within one project:
--
--      C:\FolderA  +  Photos\image.jpg
--      E:\FolderB  +  Photos\image.jpg
--
--  are two file_path rows with the same relative_path_key and
--  different source_root_id. They cannot collide, and neither can
--  overwrite the other.
--
--  Paths are stored RELATIVE to their root, so a project whose root
--  moves (D:\Photos -> E:\Photos) does not invalidate every row.
--
--  relative_path_key is the invariant-lowercase form, because Windows
--  paths are case-insensitive and Photos\A.JPG must not become a
--  second location alongside photos\a.jpg. Lowercase, NOT casefold:
--  casefold maps 'ß' to 'ss', which would merge strasse.txt and
--  straße.txt -- genuinely different files on NTFS.
--
-- ------------------------------------------------------------
--  TRANSACTION CONTROL
-- ------------------------------------------------------------
--  This file contains no BEGIN/COMMIT. fo_db.py wraps the whole
--  migration -- DDL, bookkeeping row, and user_version bump -- in one
--  transaction so a failed migration leaves the database untouched.
-- ============================================================


-- ------------------------------------------------------------
-- inventory_scan
--   One ingestion of one source root's inventory, within one run.
--
--   status is load-bearing, not decoration. ABSENCE OF AN OBSERVATION
--   ONLY MEANS "FILE IS GONE" IF THE SCAN THAT WOULD HAVE SEEN IT
--   ACTUALLY COMPLETED. A scan that was interrupted or that failed
--   part-way through ingestion has gaps that are an artefact of the
--   scan, not of the filesystem. Without this column, a future
--   "missing files" feature would confidently report deletions that
--   never happened.
--
--   source_csv_sha256 pins the database record to the exact Alpha CSV
--   it mirrors. At schema-3 introduction time the CSV was authoritative. B6.1 uses SQLite as authority; being
--   able to prove which artefact a set of rows came from is what makes
--   R6 ("can the database reproduce the legacy output?") a check
--   rather than an argument.
--
--   timestamp_format_detected records how the CSV's locale-formatted
--   timestamps were read -- see fo_inventory.py. Stored per scan
--   because it is a property of the file, not of any one row.
-- ------------------------------------------------------------
CREATE TABLE inventory_scan (
    inventory_scan_id   INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    run_id              INTEGER NOT NULL,
    run_stage_id        INTEGER,
    source_root_id      INTEGER NOT NULL,

    status              TEXT    NOT NULL CHECK (status IN (
                            'running', 'completed', 'completed_with_warnings',
                            'failed', 'interrupted')),

    started_utc         TEXT    NOT NULL,
    completed_utc       TEXT,
    duration_ms         INTEGER,

    source_csv_relpath  TEXT,
    source_csv_sha256   TEXT,
    source_csv_rows     INTEGER,

    observed_count      INTEGER NOT NULL DEFAULT 0,
    inaccessible_count  INTEGER NOT NULL DEFAULT 0,
    new_path_count      INTEGER NOT NULL DEFAULT 0,

    timestamp_format_detected TEXT,
    notes               TEXT,

    UNIQUE (run_id, source_root_id),
    FOREIGN KEY (project_id)     REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id)         REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_stage_id)   REFERENCES run_stage (run_stage_id) ON DELETE SET NULL,
    FOREIGN KEY (source_root_id) REFERENCES source_root (source_root_id) ON DELETE CASCADE
);

CREATE INDEX ix_inventory_scan_root ON inventory_scan (source_root_id, started_utc);
CREATE INDEX ix_inventory_scan_run  ON inventory_scan (run_id);


-- ------------------------------------------------------------
-- file_path
--   A location this project has observed at least once.
--
--   first_seen / last_seen are maintained during ingestion. They are
--   derivable from file_observation, and are kept here deliberately:
--   "which locations did the newest completed scan not see?" is the
--   core re-scan question, and answering it by scanning every
--   observation row for a 50,000-file project with a long scan history
--   would be needlessly expensive.
--
--   first_seen is set when the location is inserted. last_seen is set
--   once per scan, at the end, by one set-based UPDATE derived from
--   the observations just written -- see fo_inventory._stamp_last_seen.
--   Updating it per row measured as the single largest cost in the
--   whole ingestion. If a scan fails part-way, last_seen simply still
--   refers to the previous scan: stale, never wrong, and never
--   consulted for a scan whose status is not completed.
--
--   extension_key is lowercased for grouping. depth and file_name come
--   from the Alpha CSV and are stored rather than recomputed, so the
--   database says what the scan said.
-- ------------------------------------------------------------
CREATE TABLE file_path (
    file_path_id        INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    source_root_id      INTEGER NOT NULL,

    relative_path       TEXT    NOT NULL,
    relative_path_key   TEXT    NOT NULL,
    file_name           TEXT    NOT NULL,
    extension_key       TEXT    NOT NULL DEFAULT '',
    depth               INTEGER,

    first_seen_utc      TEXT    NOT NULL,
    first_seen_scan_id  INTEGER,
    last_seen_utc       TEXT    NOT NULL,
    last_seen_scan_id   INTEGER,

    UNIQUE (source_root_id, relative_path_key),
    FOREIGN KEY (project_id)     REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (source_root_id) REFERENCES source_root (source_root_id) ON DELETE CASCADE,
    FOREIGN KEY (first_seen_scan_id) REFERENCES inventory_scan (inventory_scan_id) ON DELETE SET NULL,
    FOREIGN KEY (last_seen_scan_id)  REFERENCES inventory_scan (inventory_scan_id) ON DELETE SET NULL
);

CREATE INDEX ix_file_path_lastseen  ON file_path (source_root_id, last_seen_scan_id);
CREATE INDEX ix_file_path_extension ON file_path (project_id, extension_key);


-- ------------------------------------------------------------
-- file_observation
--   What one scan saw at one location. Append-only.
--
--   status:
--     observed      the scan inventoried it successfully; it is a row
--                   in PreliminaryInventory.csv
--     inaccessible  the scan encountered it and could NOT inventory
--                   it -- an entry in Logs\errors.txt. Recorded rather
--                   than dropped, because a file that could not be
--                   read is a fact about the scan, and silently
--                   omitting it would make it indistinguishable from a
--                   file that does not exist.
--
--   TIMESTAMPS. The Alpha CSV writes .NET DateTime values in the
--   scanning machine's CURRENT CULTURE ("8/14/2026 3:26:16 PM"), not
--   ISO-8601. These columns hold the parsed, normalised values and are
--   NULL when the text could not be read unambiguously;
--   timestamps_parsed says which. The raw text is NOT duplicated here:
--   Historical R3 note: PreliminaryInventory.csv was authoritative then and was never
--   deleted, so it is already the verbatim record. Copying it into the
--   database would add ~3 MB per 50,000 files to store a second copy
--   of something one directory away.
--
--   is_reparse_point and is_offline_or_cloud are kept as their own
--   columns rather than folded into the attributes string: a cloud
--   placeholder that has not been hydrated is the difference between
--   "this file is 4 GB" and "reading this file will download 4 GB",
--   and later phases must be able to filter on it directly.
-- ------------------------------------------------------------
CREATE TABLE file_observation (
    file_observation_id INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    inventory_scan_id   INTEGER NOT NULL,
    file_path_id        INTEGER NOT NULL,

    status              TEXT    NOT NULL CHECK (status IN ('observed', 'inaccessible')),

    legacy_db_id        INTEGER,

    size_bytes          INTEGER,
    created_utc         TEXT,
    modified_utc        TEXT,
    accessed_utc        TEXT,
    timestamps_parsed   INTEGER NOT NULL DEFAULT 0 CHECK (timestamps_parsed IN (0, 1)),

    attributes          TEXT,
    is_reparse_point    INTEGER CHECK (is_reparse_point    IN (0, 1)),
    is_offline_or_cloud INTEGER CHECK (is_offline_or_cloud IN (0, 1)),

    path_length         INTEGER,
    observed_utc        TEXT    NOT NULL,

    error_kind          TEXT,
    error_message       TEXT,

    UNIQUE (inventory_scan_id, file_path_id),
    FOREIGN KEY (project_id)        REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_scan_id) REFERENCES inventory_scan (inventory_scan_id) ON DELETE CASCADE,
    FOREIGN KEY (file_path_id)      REFERENCES file_path (file_path_id) ON DELETE CASCADE
);

CREATE INDEX ix_observation_scan ON file_observation (inventory_scan_id, status);
CREATE INDEX ix_observation_path ON file_observation (file_path_id, inventory_scan_id);
