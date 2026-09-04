-- ============================================================
--  The File Organizer -- Migration 005
--  Schema version: 5
--  Introduced: Version Beta, R5 (Analyzer Result Persistence)
-- ============================================================
--
--  SCOPE OF SCHEMA VERSION 5
--
--  R5 answers: "What did the existing file analyzers discover
--  about those files?"
--
--      analyzer           the registry of analyzer types
--      analyzer_run       one analyzer's execution during one run
--      analyzer_result    what one analyzer observed about one
--                         file observation
--      archive_member     an entry INSIDE an analyzed archive --
--                         explicitly NOT a filesystem location
--      extracted_content  a REFERENCE to an extracted-text artifact,
--                         plus its summary metadata. No bodies.
--
--  Deliberately NOT created here:
--      authoritative export / equivalence tables   -> R6
--      extracted-content storage architecture      -> R9
--      remediation / decision tables               -> after Phase 1
--
--  Migrations 001, 002, 003 and 004 are unmodified.
--
-- ------------------------------------------------------------
--  THE IDENTITY CHAIN, CONTINUED
-- ------------------------------------------------------------
--
--   project -> source_root -> file_path -> file_observation -> content
--    (R1)       (R1)          (R3)         (R3)                (R4)
--                                             |
--                                             +-> analyzer_result (R5)
--
--  R3 established that a PATH is not an OBSERVATION. R4 established
--  that neither of those is CONTENT. R5 adds the fourth distinct thing:
--  an INTERPRETATION.
--
--    file_path         WHERE something was
--    file_observation  WHAT WAS SEEN there, on one scan
--    content           WHAT THE BYTES ARE
--    analyzer_result   WHAT ONE ANALYZER MADE OF IT, on one run
--
--  An analyzer result is an OBSERVATION, not an attribute. "This PDF
--  has 12 pages" is a statement about a file as it stood at a moment,
--  produced by a particular analyzer at a particular version. Attaching
--  it to a pathname would make it un-falsifiable: when the file is
--  replaced, the old claim would silently describe the new file. So
--  analyzer_result hangs off file_observation, and its provenance
--  (which run, which stage, which analyzer, which version, when) is
--  recorded, not implied.
--
-- ------------------------------------------------------------
--  THE SCHEMA DECISION: NORMALIZED CORE + JSON DETAIL
-- ------------------------------------------------------------
--
--  The brief required an explicit tradeoff. The nine analyzers produce
--  genuinely heterogeneous output -- perceptual hashes, EXIF, page
--  counts, codecs, word counts, archive statistics -- and there are
--  three ways to hold that.
--
--  (1) ONE WIDE TABLE. Every field every analyzer can emit, as its own
--      column. Rejected outright, and the brief rejects it too: ~60
--      columns of which any given row populates six. Every new analyzer
--      widens it further, and a reader cannot tell which columns are
--      meaningful for which rows.
--
--  (2) PER-ANALYZER DETAIL TABLES. image_analysis_detail,
--      pdf_analysis_detail, and seven more. Clean typing and clean
--      constraints -- genuinely the textbook answer. Rejected for R5
--      because the cost lands in exactly the place the brief says to
--      protect: adding a tenth analyzer, or one new field to an
--      existing one, becomes a migration plus a new table plus new
--      ingest code plus new query code. Nine tables of four to ten
--      columns each is also nine tables that R5 must populate and
--      verify, for a Phase-1 revision whose job is persistence, not
--      analysis.
--
--  (3) NORMALIZED COMMON CORE + JSON DETAIL. Chosen.
--
--      Everything shared -- provenance, file linkage, status, error
--      handling -- is a real column with a real foreign key, so the
--      questions asked of every analyzer are answered by ordinary SQL.
--      Analyzer-specific fields live in analyzer_result.detail_json.
--
--  WHAT THIS COSTS, STATED PLAINLY:
--
--    - No column-level type enforcement inside detail_json. A field
--      that should be an integer can arrive as text and the database
--      will not object.
--    - No foreign keys or CHECK constraints inside detail_json.
--    - Querying a detail field needs json_extract(), which is an
--      expression, so it is not index-backed unless an expression
--      index is created for it. SQLite has had JSON1 compiled in by
--      default since 3.38 (the target environment is 3.50.4), so this
--      is SQL, not application-side parsing -- but it is slower than a
--      real column on a large scan.
--    - A misspelled key is silently accepted.
--
--  WHAT MAKES THAT ACCEPTABLE HERE:
--
--    - R5 is faithful persistence of what an already-verified engine
--      already wrote to CSV. Historical note: those CSVs were authoritative at schema-5 introduction. B6.1 renders them from SQLite as derived outputs.
--      The database is not yet the thing anyone branches on.
--    - detail_json is written from a fixed, per-analyzer field list in
--      fo_analyzers.py -- not from arbitrary user input -- so keys are
--      stable in practice even though the schema does not force them.
--    - The fields that a file organizer actually searches on are
--      PROMOTED to real columns (see below), so the common queries
--      never touch JSON at all.
--    - A later revision can migrate any promoted field out of JSON
--      into a column additively, because the JSON stays as written.
--      The reverse -- discovering a per-analyzer table was the wrong
--      shape -- is the destructive direction.
--
--  THE PROMOTION RULE. A field becomes a real column on analyzer_result
--  only when AT LEAST TWO analyzers populate it. That single rule is
--  what keeps this from drifting into design (1): it is a bound, not a
--  preference. Eight columns currently qualify:
--
--    title                     PDF, Office, Text/Markdown
--    author                    PDF, Office
--    content_created_reported  PDF, Office, RAW  (verbatim, unparsed)
--    width_px, height_px       Image, Video
--    duration_seconds          Audio, Video
--    word_count, char_count    Text/Markdown, Content Extraction
--
--  Everything else -- pHash/aHash/dHash, IsEncrypted, camera make and
--  model, codecs, wikilink counts, compression ratios -- is populated
--  by exactly one analyzer and stays in detail_json, where it belongs.
--
--  content_created_reported is stored EXACTLY as the analyzer wrote it
--  and is not parsed. PDF, Office and EXIF each use a different date
--  format, and inventing a normalisation here would be the database
--  asserting something the analyzer never said. R3's timestamp-locale
--  caveat is the precedent: record what was written, do not improve it.
-- ============================================================


-- ------------------------------------------------------------
-- analyzer
--   The registry of analyzer types.
--
--   A lookup TABLE rather than a CHECK constraint on a text column,
--   for one reason: the brief requires that adding a tenth analyzer
--   not force a schema redesign. With a CHECK, a new analyzer needs a
--   migration -- and in SQLite, altering a CHECK means rebuilding the
--   table. With a registry row, it is an INSERT.
--
--   analyzer_key is the stable identifier code uses. label is what a
--   human reads. script_name and artifact_relpath document the Alpha
--   mapping IN the database, so the analyzer -> artifact -> row chain
--   is answerable from the database alone rather than only from the
--   README.
--
--   The nine Phase-1 analyzers are seeded at the bottom of this file.
-- ------------------------------------------------------------
CREATE TABLE analyzer (
    analyzer_id       INTEGER NOT NULL PRIMARY KEY,

    analyzer_key      TEXT    NOT NULL,
    label             TEXT    NOT NULL,

    script_name       TEXT,
    engine_name       TEXT,
    artifact_relpath  TEXT,
    secondary_artifact_relpath TEXT,

    sort_order        INTEGER NOT NULL DEFAULT 0,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),

    UNIQUE (analyzer_key)
);


-- ------------------------------------------------------------
-- analyzer_run
--   One analyzer's execution during one application run.
--
--   THE TWO-STATUS DESIGN -- the most important thing in this table.
--
--   analysis_status  what the ALPHA ANALYZER did
--   ingest_status    what R5's PERSISTENCE did
--
--   These are separate columns because they are separate facts, and
--   the brief is explicit that a persistence failure must never be
--   reported as an analyzer failure. With one status column, an
--   ingestion that could not open the database would have to be
--   written as either 'completed' (a lie about the persistence) or
--   'failed' (a lie about the analysis). Two columns let the row say
--   the truth: the PDF analyzer completed, and recording its results
--   failed. That is also the R3/R4 pattern, expressed at row level
--   rather than only at stage level.
--
--   ZERO APPLICABLE FILES. analysis_status = 'no_applicable_files' is
--   a first-class outcome, not an error and not an absence. On the
--   controlled suite the RAW and Video analyzers legitimately reach it:
--   they run, they find nothing of their type, and the shared harness
--   (file_organizer_common.run_analysis) returns without writing an
--   output CSV. R2 already derives exactly this status for the
--   run_stage; R5 records the same word here, so the two agree rather
--   than requiring reconciliation.
--
--   Such a row has applicable_count = 0 and NO analyzer_result rows.
--   The absence of results is the point: inventing placeholder rows to
--   make the table look uniform would be inventing evidence.
--
--   THE TWO STAGE LINKS.
--     run_stage_id         the stage that RAN the analyzer
--     ingest_run_stage_id  the stage that PERSISTED its results
--   Both are recorded because "which stage produced this result?" and
--   "which stage wrote it down?" have different answers, and the
--   second must never be mistaken for the first.
--
--   analyzer_version is read from the analyzer script itself at
--   ingestion time, so "which analyzer version produced this result?"
--   stays answerable after the script is upgraded.
-- ------------------------------------------------------------
CREATE TABLE analyzer_run (
    analyzer_run_id     INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,

    analyzer_id         INTEGER NOT NULL,
    run_id              INTEGER NOT NULL,
    run_stage_id        INTEGER,
    ingest_run_stage_id INTEGER,

    analysis_status     TEXT    NOT NULL CHECK (analysis_status IN (
                            'completed', 'completed_with_warnings', 'failed',
                            'no_applicable_files', 'skipped', 'paused',
                            'unknown')),

    ingest_status       TEXT    NOT NULL CHECK (ingest_status IN (
                            'completed', 'completed_with_warnings', 'failed',
                            'skipped', 'running')),

    analyzer_version    TEXT,
    engine_version      TEXT,

    started_utc         TEXT    NOT NULL,
    completed_utc       TEXT,
    duration_ms         INTEGER,

    -- Counts describe the ANALYZER's work, not the ingestion's.
    -- applicable_count is how many files the analyzer considered
    -- applicable (the row count of its artifact); succeeded/failed/
    -- skipped partition it. ingested_count is how many of those R5
    -- managed to attach to a file observation, and is deliberately
    -- allowed to be lower -- see unmatched_count.
    applicable_count    INTEGER NOT NULL DEFAULT 0,
    succeeded_count     INTEGER NOT NULL DEFAULT 0,
    failed_count        INTEGER NOT NULL DEFAULT 0,
    skipped_count       INTEGER NOT NULL DEFAULT 0,
    ingested_count      INTEGER NOT NULL DEFAULT 0,

    -- Analyzer rows that could not be matched to any R3 file
    -- observation. Counted rather than discarded: a result the
    -- analyzer really produced is evidence, and losing it silently
    -- would make the database quietly disagree with the CSV.
    unmatched_count     INTEGER NOT NULL DEFAULT 0,
    db_id_mismatch_count INTEGER NOT NULL DEFAULT 0,

    source_artifacts    TEXT,
    artifact_sha256     TEXT,
    notes               TEXT,

    UNIQUE (run_id, analyzer_id),
    FOREIGN KEY (project_id)   REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (analyzer_id)  REFERENCES analyzer (analyzer_id),
    FOREIGN KEY (run_id)       REFERENCES run (run_id) ON DELETE CASCADE,
    FOREIGN KEY (run_stage_id) REFERENCES run_stage (run_stage_id) ON DELETE SET NULL,
    FOREIGN KEY (ingest_run_stage_id) REFERENCES run_stage (run_stage_id) ON DELETE SET NULL
);

CREATE INDEX ix_analyzer_run_run      ON analyzer_run (run_id);
CREATE INDEX ix_analyzer_run_analyzer ON analyzer_run (analyzer_id, started_utc);


-- ------------------------------------------------------------
-- analyzer_result
--   What one analyzer observed about one file observation.
--
--   NO DUPLICATED FOREIGN KEYS. The brief asked that relationships
--   reachable through existing ones not be repeated. run_id,
--   run_stage_id and analyzer_id are all reachable through
--   analyzer_run_id, and project_id/source_root_id/file_path_id are
--   all reachable through file_observation_id. None of them is
--   repeated here. project_id is the single exception, kept only
--   because every table in this schema carries it as the isolation
--   guard the R1 design established -- a convention worth more than
--   the column it costs. (Contrast R4's hash_measurement, which does
--   carry run_id: a hash measurement can exist without a
--   duplicate_run. An analyzer_result cannot exist without an
--   analyzer_run, so the join is always available.)
--
--   status:
--     analyzed             the analyzer produced a result
--     error                the analyzer could not analyze THIS file --
--                          the stage itself was fine
--     skipped_cloud_only   deliberately not opened, to avoid forcing a
--                          multi-gigabyte cloud download
--     not_processed        the artifact lists the file but no result
--                          was recorded for it (an interrupted run)
--     unmatched            the analyzer produced a result R5 could not
--                          tie to any file observation. Kept so it is
--                          visible instead of silently dropped; see
--                          unmatched_path.
--
--   The first four are Alpha's own vocabulary, read from the Error
--   column the shared harness writes ('' / a message / 'SkippedCloudOnly'
--   / 'NotProcessed'). Per-file failure is therefore structurally
--   distinct from stage failure, which lives on analyzer_run and
--   run_stage.
--
--   file_observation_id is NULL only for status='unmatched', which is
--   the only case where there is, by definition, no observation to
--   point at. unmatched_path holds the path the analyzer reported so
--   the row remains diagnosable.
--
--   content_id is an ATTRIBUTION, not a deduplication key. When the
--   observation has a known content identity from R4, it is recorded
--   so "what did the analyzers say about this content?" is answerable.
--   R5 does NOT collapse results across observations that share
--   content: two identical PDFs in two folders get two rows. The brief
--   asks for faithful persistence of what the analyzer actually
--   observed, and the analyzer observed both. Reuse is a later
--   optimisation, and it needs this data to be evaluated at all.
--
--   legacy_db_id is Alpha's DB_ID, kept for traceability back to a CSV
--   line only. It is never the match key -- DB_IDs are reallocated
--   between scans. Disagreements are counted on analyzer_run.
--
--   detail_json holds the analyzer-specific fields. NULL when the
--   analyzer produced none (an errored file).
-- ------------------------------------------------------------
CREATE TABLE analyzer_result (
    analyzer_result_id  INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,

    analyzer_run_id     INTEGER NOT NULL,
    file_observation_id INTEGER,
    content_id          INTEGER,

    status              TEXT    NOT NULL CHECK (status IN (
                            'analyzed', 'error', 'skipped_cloud_only',
                            'not_processed', 'unmatched')),

    legacy_db_id        INTEGER,
    unmatched_path      TEXT,

    -- Promoted common fields. See the promotion rule in the header:
    -- a field appears here only when two or more analyzers populate it.
    title                    TEXT,
    author                   TEXT,
    content_created_reported TEXT,
    width_px                 INTEGER,
    height_px                INTEGER,
    duration_seconds         REAL,
    word_count               INTEGER,
    char_count               INTEGER,

    -- Analyzer-specific fields, as a JSON object.
    detail_json         TEXT,

    analyzed_utc        TEXT    NOT NULL,
    source_artifact     TEXT,

    error_kind          TEXT,
    error_message       TEXT,

    UNIQUE (analyzer_run_id, file_observation_id),
    FOREIGN KEY (project_id)          REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (analyzer_run_id)     REFERENCES analyzer_run (analyzer_run_id) ON DELETE CASCADE,
    FOREIGN KEY (file_observation_id) REFERENCES file_observation (file_observation_id) ON DELETE CASCADE,
    FOREIGN KEY (content_id)          REFERENCES content (content_id) ON DELETE SET NULL
);

CREATE INDEX ix_analyzer_result_run         ON analyzer_result (analyzer_run_id, status);
CREATE INDEX ix_analyzer_result_observation ON analyzer_result (file_observation_id);
CREATE INDEX ix_analyzer_result_content     ON analyzer_result (content_id);


-- ------------------------------------------------------------
-- archive_member
--   One entry INSIDE an analyzed archive.
--
--   THE POINT OF THIS TABLE IS WHAT IT DOES NOT REFERENCE.
--
--   An entry inside a .zip is not a location on the filesystem. It has
--   no directory entry, no size on disk of its own, no attributes, no
--   timestamps the scanner ever saw, and nothing hashed it. Writing
--   archive entries into file_path/file_observation would therefore
--   manufacture inventory records for things the inventory never
--   observed -- and every count derived from those tables, starting
--   with the accepted 4,354/4,385 baselines, would silently drift.
--
--   So archive_member has NO file_path_id, NO file_observation_id and
--   NO content_id. It hangs off the analyzer_result of the ARCHIVE
--   FILE, which IS a real observed location. The relationship reads
--   exactly as the truth does: "the archive at this location was
--   analyzed, and this is what the analyzer found listed inside it."
--
--   On the controlled suite that yields 4 archive analyzer_result rows
--   (ArchiveInventory.csv) and 5 archive_member rows
--   (ArchiveContents.csv) -- two different counts of two different
--   things, which is why they are two tables.
--
--   Whether an archived entry corresponds to a file that also exists
--   unpacked elsewhere is a Phase 2 comparison. ArchiveAnalysis.py's
--   own header says so, and this schema deliberately does not
--   pre-judge it: adding a nullable content_id later is additive.
--
--   entry_path_key is the lowercased comparison form, matching the
--   convention file_path.relative_path_key established.
-- ------------------------------------------------------------
CREATE TABLE archive_member (
    archive_member_id   INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,

    analyzer_result_id  INTEGER NOT NULL,

    entry_path          TEXT    NOT NULL,
    entry_path_key      TEXT    NOT NULL,
    entry_name          TEXT,
    entry_extension_key TEXT    NOT NULL DEFAULT '',

    entry_size_bytes            INTEGER,
    entry_compressed_size_bytes INTEGER,

    sequence            INTEGER,

    UNIQUE (analyzer_result_id, entry_path_key),
    FOREIGN KEY (project_id)         REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (analyzer_result_id) REFERENCES analyzer_result (analyzer_result_id) ON DELETE CASCADE
);

CREATE INDEX ix_archive_member_result    ON archive_member (analyzer_result_id);
CREATE INDEX ix_archive_member_extension ON archive_member (project_id, entry_extension_key);


-- ------------------------------------------------------------
-- extracted_content
--   A REFERENCE to one extracted-text artifact, and its summary
--   metadata. Not the text.
--
--   THE R5 / R9 BOUNDARY, MADE STRUCTURAL.
--
--   R5 persists: that extraction was attempted, which file observation
--   it belongs to, whether it succeeded, where the artifact is, how
--   big it is, and what the analyzer already counted (characters,
--   words). That is enough to answer "was this extracted, and where is
--   the result?" without R5 taking any position on storage.
--
--   R5 does NOT persist the extracted text. No BLOB column exists
--   here, and none should be added before R9. The Alpha artifact --
--   Inventory\ExtractedText\<hash>.txt -- is preserved exactly as
--   written and remains the only copy. Storing bodies in SQLite "just
--   to complete R5" would pre-commit the project to a storage
--   architecture (inline blob vs external store vs content-addressed
--   store vs FTS index) that R9 exists to decide, and unwinding it
--   would be the destructive migration this schema is designed to
--   avoid.
--
--   extracted_relpath is stored RELATIVE to the run folder, not
--   absolute. An absolute path breaks the moment a project folder is
--   moved or copied to another machine, which projects routinely are.
--
--   artifact_exists / artifact_bytes are recorded as of ingestion. A
--   file organizer that has already deleted a run folder should be
--   able to tell that the reference is stale rather than discovering
--   it on open.
--
--   What R9 will add, and what this table is shaped to accept
--   additively: a storage strategy, a content hash of the extracted
--   text, a deduplication key across identical content, and a
--   full-text index. None of those requires this table to change
--   shape; all of them require knowing which extraction produced what,
--   which is what this table already records.
-- ------------------------------------------------------------
CREATE TABLE extracted_content (
    extracted_content_id INTEGER NOT NULL PRIMARY KEY,
    project_id           INTEGER NOT NULL DEFAULT 1,

    analyzer_result_id   INTEGER NOT NULL,

    source_type          TEXT,

    extract_folder_relpath TEXT,
    extracted_relpath    TEXT,
    extracted_filename   TEXT,

    char_count           INTEGER,
    word_count           INTEGER,

    artifact_exists      INTEGER CHECK (artifact_exists IN (0, 1)),
    artifact_bytes       INTEGER,

    -- 'extracted'  the analyzer wrote an artifact
    -- 'empty'      it ran and produced no text (a scanned PDF, say)
    -- 'error'      extraction failed for this file
    -- 'skipped'    deliberately not attempted
    status               TEXT    NOT NULL CHECK (status IN (
                             'extracted', 'empty', 'error', 'skipped')),

    UNIQUE (analyzer_result_id),
    FOREIGN KEY (project_id)         REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (analyzer_result_id) REFERENCES analyzer_result (analyzer_result_id) ON DELETE CASCADE
);

CREATE INDEX ix_extracted_content_status ON extracted_content (project_id, status);


-- ------------------------------------------------------------
-- Seed the nine Phase-1 analyzers.
--
-- artifact_relpath is the file whose presence R2 already uses to
-- decide 'no_applicable_files', so the two agree by construction
-- rather than by coincidence.
-- ------------------------------------------------------------
INSERT INTO analyzer (analyzer_key, label, script_name, engine_name,
                      artifact_relpath, secondary_artifact_relpath, sort_order)
VALUES
    ('image',              'Image Analysis',
     'ImageAnalysis.ps1',     'ImageHash.py',
     'Inventory/ImageHashes.csv',       NULL, 10),

    ('pdf',                'PDF Analysis',
     'PDFAnalysis.ps1',       'PDFAnalysis.py',
     'Inventory/PDFInventory.csv',      NULL, 20),

    ('office',             'Office Analysis',
     'OfficeAnalysis.ps1',    'OfficeAnalysis.py',
     'Inventory/OfficeInventory.csv',   NULL, 30),

    ('raw_image',          'RAW Image Analysis',
     'RawImageAnalysis.ps1',  'RawImageAnalysis.py',
     'Inventory/RawImageInventory.csv', NULL, 40),

    ('audio',              'Audio Analysis',
     'AudioAnalysis.ps1',     'AudioAnalysis.py',
     'Inventory/AudioInventory.csv',    NULL, 50),

    ('video',              'Video Analysis',
     'VideoAnalysis.ps1',     'VideoAnalysis.py',
     'Inventory/VideoInventory.csv',    NULL, 60),

    ('text',               'Text / Markdown Analysis',
     'TextFileAnalysis.ps1',  'TextFileAnalysis.py',
     'Inventory/TextFileInventory.csv', NULL, 70),

    ('archive',            'Archive Analysis',
     'ArchiveAnalysis.ps1',   'ArchiveAnalysis.py',
     'Inventory/ArchiveInventory.csv',
     'Inventory/ArchiveContents.csv', 80),

    ('content_extraction', 'Content Extraction',
     'ContentExtraction.ps1', 'ContentExtraction.py',
     'Inventory/ContentIndex.csv',
     'Inventory/ExtractedText', 90);
