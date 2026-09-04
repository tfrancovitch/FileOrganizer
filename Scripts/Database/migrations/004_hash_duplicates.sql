-- ============================================================
--  The File Organizer -- Migration 004
--  Schema version: 4
--  Introduced: Version Beta, R4 (Hash + Duplicate Persistence)
-- ============================================================
--
--  SCOPE OF SCHEMA VERSION 4
--
--  R4 answers: "What content identity was calculated, and which
--  observed locations contain identical content?"
--
--      content            a distinct piece of content, by SHA-256
--      hash_measurement   what one run measured about one observation
--      duplicate_run      one duplicate analysis, with its totals
--      duplicate_group    one group of identical files, as that run saw it
--      duplicate_member   which observations were in that group
--
--  Deliberately NOT created here:
--      analysis_result / analyzer tables    -> R5
--      extracted-content storage            -> R9
--      dashboard/UI tables                  -> later
--
--  Migrations 001, 002 and 003 are unmodified.
--
-- ------------------------------------------------------------
--  THE IDENTITY CHAIN, CONTINUED
-- ------------------------------------------------------------
--
--      source_root -> file_path -> file_observation -> content
--       (R1)          (R3)         (R3)                (R4)
--
--  R3 established that a PATH is not content and an OBSERVATION is not
--  content. R4 adds the last link and keeps it just as separate:
--
--    file_path         WHERE something was
--    file_observation  WHAT WAS SEEN there, on one scan
--    content           WHAT THE BYTES ARE, by SHA-256
--
--  One content row may be reached from many observations, in different
--  folders, under different source roots. That is precisely what a
--  duplicate is. And one file_path may, across scans, point at
--  different content rows -- that is a file being edited or replaced.
--  Neither fact is expressible if these are collapsed.
--
-- ------------------------------------------------------------
--  WHAT COUNTS AS CONTENT IDENTITY -- THE IMPORTANT PART
-- ------------------------------------------------------------
--
--  A content row is created ONLY from a SHA-256 that covers the WHOLE
--  file. There are two ways the Alpha engine produces one, and they are
--  equally complete:
--
--    1. FullHash, computed by FullHash.ps1 / FullHashInventory.ps1.
--
--    2. PartialHash, WHERE the file is no larger than the partial
--       window. PartialHash.ps1 hashes the first N bytes (default
--       65,536). When a file is smaller than that window, the read
--       covers the entire file, so the value is a full-file SHA-256
--       that merely arrived early. The Alpha engine relies on this
--       explicitly: it marks a size group ConfirmedDuplicate without
--       ever calling FullHash.ps1 when every member is within the
--       window (see PartialHash.ps1, $AllWithinWindow).
--
--  On the accepted controlled suite, 66 of the 115 confirmed duplicate
--  files have NO FullHash value for exactly this reason. Refusing them
--  a content identity would have left more than half of the confirmed
--  duplicates unexplainable in the database.
--
--  A partial hash where the file is LARGER than the window is NOT
--  content identity and never creates a content row. It is a screening
--  value, recorded on hash_measurement, and nothing more. The
--  distinction is stored explicitly as
--  hash_measurement.partial_covers_file rather than left to be
--  recomputed by whoever queries next, because getting it wrong means
--  declaring two different files identical.
--
--  Empirical check on the accepted suite: 4,154 of 4,385 rows have a
--  complete-coverage SHA-256, yielding 4,072 distinct content values,
--  of which exactly 33 are shared by more than one location -- exactly
--  the 33 confirmed duplicate groups Alpha reported, with no hash
--  spanning two groups and no group spanning two hashes.
--
-- ------------------------------------------------------------
--  DUPLICATE GROUPS: DERIVED TRUTH + RUN SNAPSHOT
-- ------------------------------------------------------------
--
--  The brief asked for a recommendation between (A) permanent
--  duplicate_group entities, (B) deriving groups from shared content,
--  and (C) per-run snapshots.
--
--  RECOMMENDATION AND IMPLEMENTATION: B + C, not A.
--
--  (B) is the durable truth. "Which locations hold identical content?"
--  is answerable at any time by grouping observations on content_id --
--  no stored group needed, nothing to go stale, and it stays correct
--  across re-scans automatically. This is not merely tidy: it is
--  provably equivalent on the real data (33 shared hashes = 33 groups),
--  and it is sound BY CONSTRUCTION, because Alpha only ever confirms a
--  duplicate when it holds complete-coverage hashes for every member.
--
--  (C) is still needed, for three reasons a derived answer cannot
--  serve: it records what a PARTICULAR RUN concluded, at a time when
--  the filesystem may have differed; it preserves Alpha's own
--  DuplicateGroupID so a database row remains traceable to a CSV line,
--  which R6 will need; and it makes any future disagreement between
--  Alpha's grouping and content-derived grouping VISIBLE rather than
--  silently reconciled.
--
--  (A) is rejected. A permanent group entity would duplicate what
--  content_id already expresses, and would need invalidating and
--  rebuilding on every rescan -- a second source of truth whose only
--  job is to drift from the first.
-- ============================================================


-- ------------------------------------------------------------
-- content
--   A distinct piece of content, identified by a whole-file SHA-256.
--
--   size_bytes is recorded alongside because identical content must
--   have identical size; a mismatch would indicate corruption or a
--   collision and is worth being able to detect rather than assume
--   away.
--
--   identity_source records HOW the hash achieved full coverage --
--   whether it was computed as a full hash, or as a partial hash that
--   happened to span the entire file. Both are complete; recording
--   which is which keeps the claim auditable instead of asking a
--   reader to take it on trust.
-- ------------------------------------------------------------
CREATE TABLE content (
    content_id       INTEGER NOT NULL PRIMARY KEY,
    project_id       INTEGER NOT NULL DEFAULT 1,

    sha256           TEXT    NOT NULL,
    size_bytes       INTEGER,

    identity_source  TEXT    NOT NULL CHECK (identity_source IN (
                         'full_hash', 'partial_hash_complete')),

    first_seen_utc   TEXT    NOT NULL,
    first_seen_run_id INTEGER,
    last_seen_utc    TEXT    NOT NULL,
    last_seen_run_id INTEGER,

    UNIQUE (project_id, sha256),
    FOREIGN KEY (project_id)        REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (first_seen_run_id) REFERENCES run (run_id) ON DELETE SET NULL,
    FOREIGN KEY (last_seen_run_id)  REFERENCES run (run_id) ON DELETE SET NULL
);

CREATE INDEX ix_content_size ON content (project_id, size_bytes);


-- ------------------------------------------------------------
-- hash_measurement
--   What one run measured about one file observation.
--
--   This is the provenance record, and it is per (run, observation) --
--   NOT per file. Re-running duplicate analysis produces new rows; it
--   never rewrites old ones. "When was this hash measured, and by which
--   run?" stays answerable for every measurement ever taken.
--
--   content_id is NULL when no whole-file hash was obtained: a file
--   ruled out by a genuinely partial hash, one unique by size that was
--   never hashed at all, a cloud-only file that was skipped, or one
--   that errored. Those are four different situations and hash_status
--   keeps them apart.
--
--   measurement_mode distinguishes the two workflows the application
--   actually has, which the brief requires not be conflated:
--     selective   the Duplicate Run path -- size grouping, partial
--                 hash, then full hash only for the few groups that
--                 still need it
--     exhaustive  the Full Run path (FullHashInventory.ps1) -- every
--                 inventoried file gets a full hash
--
--   reused_from_previous exists to support the approved future
--   optimisation of inheriting a hash when path, size and modified
--   time are unchanged. R4 does NOT enable that: the Alpha workflow is
--   untouched and every row is written with 0, meaning "measured this
--   run". The column is here so switching it on later is a behaviour
--   change in one script rather than a schema migration -- and so that
--   when it is switched on, a measured hash and an inherited one remain
--   distinguishable forever after.
-- ------------------------------------------------------------
CREATE TABLE hash_measurement (
    hash_measurement_id INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,

    file_observation_id INTEGER NOT NULL,
    content_id          INTEGER,

    run_id              INTEGER NOT NULL,
    run_stage_id        INTEGER,
    duplicate_run_id    INTEGER,

    measurement_mode    TEXT    NOT NULL CHECK (measurement_mode IN (
                            'selective', 'exhaustive')),

    algorithm           TEXT    NOT NULL DEFAULT 'SHA256',
    size_bytes          INTEGER,

    size_group_id       INTEGER,

    partial_hash        TEXT,
    partial_hash_bytes  INTEGER,
    partial_group_id    INTEGER,
    -- 1 when the partial window spanned the whole file, so the partial
    -- hash IS a whole-file SHA-256. See the header note.
    partial_covers_file INTEGER CHECK (partial_covers_file IN (0, 1)),

    full_hash           TEXT,

    hash_status         TEXT    NOT NULL,
    alpha_final_status  TEXT,

    -- Whether the partial-hash stage concluded this file REQUIRED a full
    -- hash. Kept as its own column because hash_status holds the FINAL
    -- outcome, and the final outcome destroys this fact: a file that
    -- needed a full hash ends up 'ruled_out_full' or
    -- 'confirmed_duplicate', with nothing left to say it was ever a
    -- candidate. Deriving it from "full_hash IS NOT NULL" would be
    -- wrong in exactly the case that matters -- a file that needed a
    -- full hash and then failed to produce one.
    needed_full_hash    INTEGER CHECK (needed_full_hash IN (0, 1)),

    reused_from_previous INTEGER NOT NULL DEFAULT 0
                             CHECK (reused_from_previous IN (0, 1)),

    measured_utc        TEXT    NOT NULL,
    source_artifact     TEXT,

    error_kind          TEXT,
    error_message       TEXT,

    UNIQUE (run_id, file_observation_id, measurement_mode),
    FOREIGN KEY (project_id)          REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (file_observation_id) REFERENCES file_observation (file_observation_id) ON DELETE CASCADE,
    FOREIGN KEY (content_id)          REFERENCES content (content_id) ON DELETE SET NULL,
    FOREIGN KEY (run_id)              REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_stage_id)        REFERENCES run_stage (run_stage_id) ON DELETE SET NULL
);

CREATE INDEX ix_hash_observation ON hash_measurement (file_observation_id);
CREATE INDEX ix_hash_content     ON hash_measurement (content_id);
CREATE INDEX ix_hash_run         ON hash_measurement (run_id, measurement_mode);
CREATE INDEX ix_hash_status      ON hash_measurement (hash_status);


-- ------------------------------------------------------------
-- duplicate_run
--   One duplicate analysis, and the totals it reported.
--
--   The count columns are the seven numbers the controlled-suite
--   baseline is expressed in. Storing them is not redundancy for its
--   own sake: it records what ALPHA said, which is the thing regression
--   testing compares against, and it lets a later run be checked
--   against an earlier one without re-deriving anything.
-- ------------------------------------------------------------
CREATE TABLE duplicate_run (
    duplicate_run_id     INTEGER NOT NULL PRIMARY KEY,
    project_id           INTEGER NOT NULL DEFAULT 1,
    run_id               INTEGER NOT NULL,
    run_stage_id         INTEGER,

    mode                 TEXT    NOT NULL CHECK (mode IN ('selective', 'exhaustive')),
    status               TEXT    NOT NULL CHECK (status IN (
                             'running', 'completed', 'completed_with_warnings',
                             'failed', 'interrupted')),

    started_utc          TEXT    NOT NULL,
    completed_utc        TEXT,
    duration_ms          INTEGER,

    partial_hash_bytes   INTEGER,

    candidate_count      INTEGER NOT NULL DEFAULT 0,
    size_group_count     INTEGER NOT NULL DEFAULT 0,
    needs_full_hash_count INTEGER NOT NULL DEFAULT 0,
    ruled_out_full_count INTEGER NOT NULL DEFAULT 0,
    confirmed_group_count INTEGER NOT NULL DEFAULT 0,
    confirmed_file_count INTEGER NOT NULL DEFAULT 0,
    redundant_file_count INTEGER NOT NULL DEFAULT 0,
    error_count          INTEGER NOT NULL DEFAULT 0,

    source_artifacts     TEXT,
    notes                TEXT,

    UNIQUE (run_id, mode),
    FOREIGN KEY (project_id)   REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (run_id)       REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_stage_id) REFERENCES run_stage (run_stage_id) ON DELETE SET NULL
);

CREATE INDEX ix_duplicate_run_run ON duplicate_run (run_id);


-- ------------------------------------------------------------
-- duplicate_group
--   One group of identical files, AS THAT RUN REPORTED IT.
--
--   A snapshot, not the durable truth -- see the header note on B + C.
--   Durable truth is content: locations sharing a content_id are
--   duplicates by definition, derivable at any time.
--
--   legacy_group_id is Alpha's own DuplicateGroupID, kept so a database
--   row stays traceable to a CSV line.
--
--   confirmation_method records how Alpha reached the conclusion, which
--   is not always the same route even though both are complete:
--     full_hash              confirmed by FullHash.ps1
--     partial_hash_complete  confirmed by PartialHash.ps1 because every
--                            member fitted inside the read window
--
--   content_id is nullable so that a group whose content identity
--   cannot be established is still recorded rather than dropped. On the
--   accepted suite every confirmed group has one.
-- ------------------------------------------------------------
CREATE TABLE duplicate_group (
    duplicate_group_id  INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    duplicate_run_id    INTEGER NOT NULL,

    legacy_group_id     INTEGER,
    content_id          INTEGER,

    confirmation_method TEXT    NOT NULL CHECK (confirmation_method IN (
                            'full_hash', 'partial_hash_complete', 'unknown')),

    size_bytes          INTEGER,
    member_count        INTEGER NOT NULL DEFAULT 0,
    redundant_count     INTEGER NOT NULL DEFAULT 0,
    reclaimable_bytes   INTEGER,

    UNIQUE (duplicate_run_id, legacy_group_id),
    FOREIGN KEY (project_id)       REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (duplicate_run_id) REFERENCES duplicate_run (duplicate_run_id) ON DELETE CASCADE,
    FOREIGN KEY (content_id)       REFERENCES content (content_id) ON DELETE SET NULL
);

CREATE INDEX ix_duplicate_group_content ON duplicate_group (content_id);


-- ------------------------------------------------------------
-- duplicate_member
--   Which observations that run placed in that group.
--
--   Links to file_observation, so a member is tied to a specific
--   location seen on a specific scan -- never to a bare path. Two files
--   with the same relative path under different source roots are
--   different observations and remain distinguishable here, which is
--   what allows a duplicate group to legitimately span roots.
-- ------------------------------------------------------------
CREATE TABLE duplicate_member (
    duplicate_member_id INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    duplicate_group_id  INTEGER NOT NULL,
    file_observation_id INTEGER NOT NULL,
    hash_measurement_id INTEGER,

    UNIQUE (duplicate_group_id, file_observation_id),
    FOREIGN KEY (project_id)          REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (duplicate_group_id)  REFERENCES duplicate_group (duplicate_group_id) ON DELETE CASCADE,
    FOREIGN KEY (file_observation_id) REFERENCES file_observation (file_observation_id) ON DELETE CASCADE,
    FOREIGN KEY (hash_measurement_id) REFERENCES hash_measurement (hash_measurement_id) ON DELETE SET NULL
);

CREATE INDEX ix_duplicate_member_observation ON duplicate_member (file_observation_id);
