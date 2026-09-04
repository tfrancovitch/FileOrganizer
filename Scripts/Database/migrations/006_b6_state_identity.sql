-- ============================================================
--  The File Organizer -- Migration 006
--  Schema version: 6
--  Introduced: B6 (post-B5 adversarial reconciliation)
-- ============================================================
--
--  WHY THIS MIGRATION EXISTS
--
--  B5-E and B5-F established that several B4.5 defects were
--  ARCHITECTURAL rather than local. This migration implements the
--  structural half of the B6 answer. The behavioural half lives in
--  fo_state.py, fo_inventory_records.py, fo_hashes.py and
--  fo_exports.py.
--
--  It addresses, by finding number:
--
--    E.F001  full observation population per run
--            -> file_state carries CURRENT truth; file_observation
--               becomes CHANGE history (see change_kind).
--    E.F002  duplicate query degrades with run history
--            -> the current duplicate query reads file_state only,
--               which has one row per location regardless of how many
--               times the project has been run.
--    E.F004  missing index on duplicate_member.hash_measurement_id
--    E.F005  missing index on file_observation.legacy_db_id
--    E.F006  unbounded archive entry cardinality
--            -> archive_summary states, per archive, whether the
--               member listing is complete, capped or summary-only.
--    E.F008  extracted text keyed by path
--            -> extracted_content gains content addressing and an
--               explicit dedup link.
--    E.F044  5,000-entry inaccessible cap
--            -> inventory_scan now records how many were SEEN as well
--               as how many were STORED, so a truncated diagnostic
--               record is visibly truncated instead of silently short.
--    F.F001  enumeration order controls identifiers
--            -> durable semantic identity (file_path_id, content.sha256)
--               is separated from deterministic PRESENTATION order
--               (source_root.root_ordinal, file_path.path_sort_key).
--    F.F003  created_utc / modified_utc are not UTC
--            -> the falsely-named columns are RENAMED to what they
--               actually held, and honest UTC columns are added
--               alongside. See the timestamp note below.
--    F.F008  source-root order changes identifiers
--            -> root_ordinal is derived from root_path_key, not from
--               the order roots appear in settings.json.
--
--  And the deferred G/H/I constraints the PM made mandatory for B6:
--
--    G  run.finalized, run_stage.attempt / progress columns
--    H  file_state.content_observation_id (staleness),
--       run_source_root.availability (missing root != empty root),
--       file_observation physical-identity columns (hard links)
--    I  inventory_scan truncation counters, run_stage progress,
--       explicit state vocabularies
--
-- ------------------------------------------------------------
--  THE TIMESTAMP CHANGE -- READ THIS BEFORE TOUCHING THESE COLUMNS
-- ------------------------------------------------------------
--
--  B4.5 stored machine-LOCAL wall-clock time, with no offset, in
--  columns named created_utc / modified_utc / accessed_utc. B5-F.F003
--  is correct that this is a false column contract: the same file
--  produced different stored values on two machines, and nothing in
--  the row said so.
--
--  The stored VALUES cannot be corrected retroactively. The offset in
--  force when they were written was never recorded, so there is no
--  arithmetic that recovers UTC from them. Rewriting them would be
--  inventing evidence -- exactly what B5-H exists to catch.
--
--  So this migration does the only truthful thing available:
--
--    1. The existing columns are RENAMED to created_local_naive,
--       modified_local_naive, accessed_local_naive. That is precisely
--       what they contain. Old rows keep their old values, now
--       correctly labelled.
--
--    2. New created_utc / modified_utc / accessed_utc columns are
--       added. B6 writes true UTC into them, in ISO-8601 with an
--       explicit 'Z'. They are NULL on every pre-B6 row, and NULL is
--       the honest answer to "what was this in UTC?" for a row that
--       never recorded enough to say.
--
--    3. utc_offset_minutes records the offset in force at observation,
--       so the local rendering is reproducible from the UTC value
--       without consulting the machine that produced it.
--
--    4. inventory_scan.timestamp_model says which model a scan used
--       ('local_naive' for pre-B6, 'utc_offset' for B6), so a consumer
--       never has to guess which rows it is looking at.
--
--  Presentation formatting is NOT stored. It belongs at the export
--  boundary -- see fo_exports.py and B5-F.F004.
--
-- ------------------------------------------------------------
--  TRANSACTION CONTROL
-- ------------------------------------------------------------
--  No BEGIN/COMMIT here. fo_db.py wraps the whole migration -- DDL,
--  bookkeeping row and user_version bump -- in one transaction, so a
--  failed migration leaves the database untouched.
-- ============================================================


-- ------------------------------------------------------------
-- 1. TIMESTAMPS: rename the false columns, add honest ones.
-- ------------------------------------------------------------

ALTER TABLE file_observation RENAME COLUMN created_utc  TO created_local_naive;
ALTER TABLE file_observation RENAME COLUMN modified_utc TO modified_local_naive;
ALTER TABLE file_observation RENAME COLUMN accessed_utc TO accessed_local_naive;

ALTER TABLE file_observation ADD COLUMN created_utc  TEXT;
ALTER TABLE file_observation ADD COLUMN modified_utc TEXT;
ALTER TABLE file_observation ADD COLUMN accessed_utc TEXT;

-- Offset in force at the moment of observation, in minutes east of UTC.
-- Lets the local rendering be reproduced from the UTC value alone.
ALTER TABLE file_observation ADD COLUMN utc_offset_minutes INTEGER;

-- 'local_naive'  pre-B6 rows: local wall clock, offset unknown
-- 'utc_offset'   B6 rows: true UTC plus a recorded offset
ALTER TABLE file_observation ADD COLUMN timestamp_model TEXT
    NOT NULL DEFAULT 'local_naive'
    CHECK (timestamp_model IN ('local_naive', 'utc_offset'));

ALTER TABLE inventory_scan ADD COLUMN timestamp_model TEXT
    NOT NULL DEFAULT 'local_naive'
    CHECK (timestamp_model IN ('local_naive', 'utc_offset'));


-- ------------------------------------------------------------
-- 2. HISTORY: file_observation becomes CHANGE history.
--
--    B4.5 appended one observation per file per run whether or not
--    anything about the file had changed -- ~525 bytes per file per
--    run for an unchanged corpus (E.F001), and a permanently growing
--    table for the duplicate query to scan (E.F002).
--
--    B6 writes an observation when the observed state is NEW or
--    DIFFERENT, and records an unchanged re-verification as a counter
--    on the scan plus a refreshed verified_utc on file_state. The
--    facts preserved are the same facts: when a location was first
--    seen, every state it has held, and when each state ended.
--    What is no longer preserved is a byte-identical restatement of
--    an unchanged file, once per run, forever.
--
--    change_kind is the vocabulary:
--      first_seen    the location had no prior observation
--      modified      size, timestamps or attributes differed
--      reappeared    previously missing, now present again
--      vanished      previously present, not seen by a COMPLETED scan
--      inaccessible  encountered but could not be inventoried
--      verified      written only when history_mode = 'full'
--
--    supersedes_observation_id chains an observation to the one it
--    replaced, so "what did this location look like before?" is a
--    single indexed hop rather than a scan of the whole table.
-- ------------------------------------------------------------

ALTER TABLE file_observation ADD COLUMN change_kind TEXT
    CHECK (change_kind IN ('first_seen', 'modified', 'reappeared',
                           'vanished', 'inaccessible', 'verified'));

ALTER TABLE file_observation ADD COLUMN supersedes_observation_id INTEGER
    REFERENCES file_observation (file_observation_id) ON DELETE SET NULL;

-- Physical identity, when the project has enabled it. NULL means "not
-- collected", never "no hard links" -- see fo_scan.collect_physical_identity.
-- H requires that hard links be handled truthfully; recording an
-- uncollected value as 1 would be the untruth.
ALTER TABLE file_observation ADD COLUMN volume_serial   TEXT;
ALTER TABLE file_observation ADD COLUMN file_index      TEXT;
ALTER TABLE file_observation ADD COLUMN hard_link_count INTEGER;

CREATE INDEX ix_observation_legacy_db_id
    ON file_observation (inventory_scan_id, legacy_db_id);

CREATE INDEX ix_observation_supersedes
    ON file_observation (supersedes_observation_id);

CREATE INDEX ix_observation_physical
    ON file_observation (volume_serial, file_index);


-- ------------------------------------------------------------
-- 3. file_state -- THE CURRENT-STATE PROJECTION.
--
--    The single most important table in B6, and the direct answer to
--    E.F002.
--
--    ONE ROW PER LOCATION. Not one per location per run. A project
--    scanned two hundred times has exactly as many rows here as it has
--    locations, so every current-state question -- including the
--    duplicate query, which is the product's central answer -- costs
--    the same on run two hundred as on run one.
--
--    WHAT MAKES IT SAFE (B5-H, "current vs stale authority explicit"):
--
--    content_id is authoritative ONLY when content_observation_id
--    equals current_observation_id. If a file was hashed on run 3 and
--    then modified before run 4, run 4 writes a new observation, and
--    the two ids diverge. The content identity is then visibly STALE
--    rather than quietly wrong -- which is the failure mode that turns
--    a duplicate group into a false claim about the current disk.
--
--    fo_state.current_duplicate_sets() enforces that equality in SQL,
--    so a stale hash cannot enter a current duplicate group at all.
--
--    state vocabulary:
--      present       a completed scan saw it
--      inaccessible  a completed scan encountered it and could not read it
--      missing       a completed scan of its root did not see it
--      unverified    its root was not scanned, or the scan did not
--                    complete -- ABSENCE OF EVIDENCE, not absence
--
--    'unverified' is the load-bearing one. B5-H requires that a
--    missing root not read as an empty root. A root that was offline
--    leaves its locations 'unverified'; only a scan that actually
--    completed may move a location to 'missing'.
-- ------------------------------------------------------------

CREATE TABLE file_state (
    file_path_id            INTEGER NOT NULL PRIMARY KEY,
    project_id              INTEGER NOT NULL DEFAULT 1,
    source_root_id          INTEGER NOT NULL,

    state                   TEXT    NOT NULL CHECK (state IN (
                                'present', 'inaccessible', 'missing',
                                'unverified')),

    current_observation_id  INTEGER,
    current_scan_id         INTEGER,
    current_run_id          INTEGER,

    -- Denormalised from the current observation so that current-state
    -- queries -- which are the common case -- need no join at all.
    size_bytes              INTEGER,
    modified_utc            TEXT,
    is_offline_or_cloud     INTEGER CHECK (is_offline_or_cloud IN (0, 1)),

    -- Content identity, and the observation it was measured against.
    content_id              INTEGER,
    content_observation_id  INTEGER,
    content_run_id          INTEGER,

    first_seen_utc          TEXT    NOT NULL,
    verified_utc            TEXT    NOT NULL,
    state_changed_utc       TEXT    NOT NULL,

    FOREIGN KEY (file_path_id)   REFERENCES file_path (file_path_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id)     REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (source_root_id) REFERENCES source_root (source_root_id) ON DELETE CASCADE,
    FOREIGN KEY (current_observation_id)
        REFERENCES file_observation (file_observation_id) ON DELETE SET NULL,
    FOREIGN KEY (content_id) REFERENCES content (content_id) ON DELETE SET NULL
);

-- The duplicate query's covering index. content_id first because that
-- is what it groups on.
CREATE INDEX ix_file_state_content ON file_state (content_id, state);
CREATE INDEX ix_file_state_root    ON file_state (source_root_id, state);
CREATE INDEX ix_file_state_state   ON file_state (state);
CREATE INDEX ix_file_state_size    ON file_state (size_bytes)
    WHERE state = 'present';


-- ------------------------------------------------------------
-- 4. DETERMINISTIC PRESENTATION ORDER, SEPARATE FROM IDENTITY.
--
--    B5-F.F001's real complaint is not that ids changed; it is that a
--    VOLATILE encounter order was being promoted into DURABLE
--    identifiers. B6 answers it by making the two different things
--    and naming both:
--
--      DURABLE SEMANTIC IDENTITY   file_path_id, content.sha256
--          Stable for the life of the project. Never derived from
--          traversal order.
--
--      DETERMINISTIC PRESENTATION  root_ordinal, path_sort_key
--          Derived from the DATA -- a case-folded path under a
--          case-folded root -- not from the order the filesystem
--          happened to hand entries back. Two machines that scan the
--          same tree in different physical orders produce the same
--          presentation order, because neither one consults the walk.
--
--    root_ordinal is assigned from root_path_key, so reordering
--    SourceRoots in settings.json cannot renumber anything (F.F008).
-- ------------------------------------------------------------

ALTER TABLE source_root ADD COLUMN root_ordinal INTEGER;

ALTER TABLE file_path ADD COLUMN path_sort_key TEXT;

CREATE INDEX ix_file_path_sort ON file_path (source_root_id, path_sort_key);


-- ------------------------------------------------------------
-- 5. SCAN COMPLETENESS AND TRUNCATION (E.F044, I).
--
--    B4.5 capped stored inaccessible-path diagnostics at 5,000 and
--    said nothing about it, so a badly permissioned tree produced a
--    database that was quietly an incomplete record.
--
--    B6 keeps a cap -- unbounded diagnostics are their own scalability
--    problem -- but records both numbers. A consumer can now tell
--    "4,900 inaccessible files" from "at least 5,000, of which 5,000
--    were stored", which are very different facts about a disk.
-- ------------------------------------------------------------

ALTER TABLE inventory_scan ADD COLUMN inaccessible_seen_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inventory_scan ADD COLUMN inaccessible_cap        INTEGER;
ALTER TABLE inventory_scan ADD COLUMN inaccessible_truncated  INTEGER NOT NULL DEFAULT 0
    CHECK (inaccessible_truncated IN (0, 1));

-- History accounting, so E.F001's saving is visible rather than
-- implied. changed + unchanged + new + vanished should reconcile with
-- what the walk actually met.
ALTER TABLE inventory_scan ADD COLUMN unchanged_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inventory_scan ADD COLUMN changed_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE inventory_scan ADD COLUMN vanished_count  INTEGER NOT NULL DEFAULT 0;

-- 'changes'  observations written only for new/changed locations
-- 'full'     an observation for every location, every scan (B4.5's
--            behaviour, retained as an explicit opt-in)
ALTER TABLE inventory_scan ADD COLUMN history_mode TEXT NOT NULL DEFAULT 'changes'
    CHECK (history_mode IN ('changes', 'full'));

-- H: a root that could not be reached must not read as a root with no
-- files in it.
ALTER TABLE inventory_scan ADD COLUMN root_availability TEXT
    CHECK (root_availability IN ('available', 'missing',
                                 'permission_denied', 'unknown'));

ALTER TABLE run_source_root ADD COLUMN availability TEXT
    CHECK (availability IN ('available', 'missing',
                            'permission_denied', 'unknown'));


-- ------------------------------------------------------------
-- 6. RESILIENCE AND OBSERVABILITY (G, I).
--
--    run.finalized is the anti-false-completion flag. It is written 1
--    ONLY inside the same transaction that sets a terminal status. A
--    process that dies leaves finalized = 0, and reconciliation can
--    tell a run that ended from a run that merely stopped being
--    written to -- which a status column alone cannot, because the
--    status was written before the work finished.
-- ------------------------------------------------------------

ALTER TABLE run ADD COLUMN finalized INTEGER NOT NULL DEFAULT 0
    CHECK (finalized IN (0, 1));

ALTER TABLE run_stage ADD COLUMN attempt        INTEGER NOT NULL DEFAULT 1;
ALTER TABLE run_stage ADD COLUMN progress_done  INTEGER;
ALTER TABLE run_stage ADD COLUMN progress_total INTEGER;
ALTER TABLE run_stage ADD COLUMN checkpoint_utc TEXT;

CREATE INDEX ix_run_finalized ON run (finalized, status);


-- ------------------------------------------------------------
-- 7. ARCHIVE BOUNDS (E.F006).
--
--    One 75 MB ZIP produced 500,000 rows and ~358 MB of heap in B4.5,
--    with no cap, no estimate and no way for the user to see it
--    happening.
--
--    B6 does not answer that by silently storing less. It answers it
--    by making the analysis mode an explicit, recorded property of
--    each archive:
--
--      complete      every member is in archive_member
--      capped        the first N members are stored; entry_total_count
--                    says how many there really were
--      summary_only  no members stored; aggregates only
--
--    A consumer that needs completeness can now detect that it does
--    not have it, which is the difference between a bounded record and
--    an incomplete one.
-- ------------------------------------------------------------

CREATE TABLE archive_summary (
    analyzer_result_id   INTEGER NOT NULL PRIMARY KEY,
    project_id           INTEGER NOT NULL DEFAULT 1,

    analysis_mode        TEXT    NOT NULL CHECK (analysis_mode IN (
                             'complete', 'capped', 'summary_only')),

    entry_total_count    INTEGER,
    entry_recorded_count INTEGER NOT NULL DEFAULT 0,
    entry_cap            INTEGER,
    truncated            INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),

    total_uncompressed_bytes INTEGER,
    total_compressed_bytes   INTEGER,

    FOREIGN KEY (analyzer_result_id)
        REFERENCES analyzer_result (analyzer_result_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE
);

CREATE INDEX ix_archive_summary_mode ON archive_summary (analysis_mode, truncated);


-- ------------------------------------------------------------
-- 8. CONTENT-ADDRESSED EXTRACTED TEXT (E.F008).
--
--    B4.5 wrote one flat .txt per document, named from a hash of the
--    SOURCE PATH, into one directory. Two identical documents in two
--    folders produced two identical artifacts, and a large project
--    produced a directory with hundreds of thousands of entries in it
--    -- which is a genuine problem on NTFS.
--
--    B6 names the artifact after a hash of the TEXT, and shards it two
--    levels deep. Identical extracted text is written once, and
--    dedup_of_extracted_content_id records that the second document
--    resolved to the same artifact rather than pretending it produced
--    its own.
-- ------------------------------------------------------------

ALTER TABLE extracted_content ADD COLUMN text_sha256 TEXT;

ALTER TABLE extracted_content ADD COLUMN storage_mode TEXT
    NOT NULL DEFAULT 'path_addressed'
    CHECK (storage_mode IN ('path_addressed', 'content_addressed'));

ALTER TABLE extracted_content ADD COLUMN reused_existing INTEGER
    NOT NULL DEFAULT 0 CHECK (reused_existing IN (0, 1));

ALTER TABLE extracted_content ADD COLUMN dedup_of_extracted_content_id INTEGER
    REFERENCES extracted_content (extracted_content_id) ON DELETE SET NULL;

CREATE INDEX ix_extracted_content_sha ON extracted_content (text_sha256);


-- ------------------------------------------------------------
-- 9. THE INDEXES B5-E MEASURED AS MISSING.
--
--    E.F004: exports join duplicate_member on hash_measurement_id, and
--    SQLite was building a transient automatic index every time.
--    E.F005 is covered by ix_observation_legacy_db_id above.
-- ------------------------------------------------------------

CREATE INDEX ix_duplicate_member_hash
    ON duplicate_member (hash_measurement_id);

CREATE INDEX ix_duplicate_member_group
    ON duplicate_member (duplicate_group_id, file_observation_id);

-- hash_measurement is now queried run-scoped for exports and never
-- scanned whole for current state; this supports the run-scoped form.
CREATE INDEX ix_hash_run_observation
    ON hash_measurement (run_id, file_observation_id);


-- ------------------------------------------------------------
-- 10. HISTORY RETENTION POLICY.
--
--    Theme 2 requires history retention to be EXPLICIT rather than
--    emergent. These are defaults; fo_state.compact_history() reads
--    them and the dashboard can change them.
--
--    0 for retention_runs means "keep everything", which is a choice a
--    user can make deliberately -- unlike B4.5, where it was the only
--    behaviour available.
-- ------------------------------------------------------------

INSERT OR REPLACE INTO app_meta (key, value, updated_utc) VALUES
    ('history.mode',            'changes',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('history.retention_runs',  '0',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('archive.member_cap',      '10000',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('inaccessible.cap',        '5000',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('extraction.storage_mode', 'content_addressed',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ('timestamps.model',        'utc_offset',
     strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));


-- ------------------------------------------------------------
-- 11. BACKFILL.
--
--    Existing projects get a file_state built from what they already
--    have, so B6 is an upgrade rather than a re-scan requirement.
--
--    Everything backfilled here is marked 'unverified' -- NOT
--    'present'. B6 has not itself observed any of these files, and
--    claiming otherwise would be exactly the invented record B5-H
--    exists to prevent. The next completed scan promotes them.
--
--    root_ordinal and path_sort_key are computed here because they are
--    pure functions of data already stored.
-- ------------------------------------------------------------

UPDATE source_root
   SET root_ordinal = (
       SELECT COUNT(*) FROM source_root AS earlier
        WHERE earlier.project_id = source_root.project_id
          AND earlier.root_path_key <= source_root.root_path_key)
 WHERE root_ordinal IS NULL;

UPDATE file_path
   SET path_sort_key = relative_path_key
 WHERE path_sort_key IS NULL;

INSERT INTO file_state (
    file_path_id, project_id, source_root_id, state,
    current_observation_id, current_scan_id, current_run_id,
    size_bytes, modified_utc, is_offline_or_cloud,
    content_id, content_observation_id, content_run_id,
    first_seen_utc, verified_utc, state_changed_utc)
SELECT
    fp.file_path_id,
    fp.project_id,
    fp.source_root_id,
    'unverified',
    latest.file_observation_id,
    latest.inventory_scan_id,
    NULL,
    latest.size_bytes,
    NULL,                      -- no honest UTC exists for a pre-B6 row
    latest.is_offline_or_cloud,
    NULL,                      -- content is re-established by the next run
    NULL,
    NULL,
    fp.first_seen_utc,
    fp.last_seen_utc,
    fp.last_seen_utc
FROM file_path fp
LEFT JOIN (
    SELECT o.file_path_id,
           o.file_observation_id,
           o.inventory_scan_id,
           o.size_bytes,
           o.is_offline_or_cloud
      FROM file_observation o
      JOIN (SELECT file_path_id, MAX(file_observation_id) AS newest
              FROM file_observation
             GROUP BY file_path_id) pick
        ON pick.file_path_id = o.file_path_id
       AND pick.newest = o.file_observation_id
) latest ON latest.file_path_id = fp.file_path_id;
