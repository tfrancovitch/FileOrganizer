-- The File Organizer — schema 012
-- Phase 3, Build 5 (Image Game) needs a kind of Phase 2 evidence the product
-- did not produce before: visual candidate PAIRS. The image analyzer has
-- always stored one perceptual hash per image (analyzer_result.detail_json:
-- pHash / aHash / dHash / HashSize) and nothing compared them. These tables
-- hold the comparison. Additive only; no existing table changes; no source
-- file is touched (the hashes are already in the database).
--
-- This is EVIDENCE, not intent: a candidate pair says "these two images'
-- perceptual hashes are close", never "these are the same image". Whether
-- they are is a Phase 3 decision about the pair (p3_decision with
-- target_kind 'visual_candidate_pair', target_ref = candidate_id).
--
-- Identity is the pair of file locations: UNIQUE(a_file_path_id,
-- b_file_path_id), a < b. A later generation that finds the same two
-- locations again UPDATES that row -- same candidate_id -- so a decision on
-- the pair keeps pointing at it across re-analysis (the research's
-- "adjudicated pairs must not resurface as new" requirement). The
-- observations the compared hashes rested on are recorded on the row; when a
-- file is observed again, the pair no longer rests on current evidence and
-- Phase 3's revalidation says so.
--
-- `current` is 1 when the latest completed generation found the pair; a pair
-- it did not find is retired to 0, never deleted.

CREATE TABLE visual_candidate_generation (
    generation_id        INTEGER NOT NULL PRIMARY KEY,
    project_id           INTEGER NOT NULL DEFAULT 1,
    run_id               INTEGER,                -- the analysis run it followed; NULL when generated on demand
    method               TEXT    NOT NULL,       -- 'phash' in this build
    hash_bits            INTEGER NOT NULL,       -- 64 for the analyzer's hash size 8
    max_distance         INTEGER NOT NULL,       -- the Hamming-distance threshold
    neighbor_cap         INTEGER NOT NULL,       -- nearest neighbours kept per image (a crowd of look-alikes stays bounded)
    generator_version    TEXT    NOT NULL,
    started_utc          TEXT    NOT NULL,
    finished_utc         TEXT,
    status               TEXT    NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    images_considered    INTEGER,
    images_skipped       INTEGER,                -- analyzed images whose hash could not be read (reported, not guessed)
    pairs_current        INTEGER,
    pairs_new            INTEGER,
    pairs_retired        INTEGER,
    capped_images        INTEGER,                -- images with more look-alikes than the cap
    analysis_watermark   INTEGER,                -- MAX(analyzer_result_id) read: newer image results mean the pairs are out of date
    detail_json          TEXT,
    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE
);

CREATE TABLE visual_candidate (
    candidate_id         INTEGER NOT NULL PRIMARY KEY,
    project_id           INTEGER NOT NULL DEFAULT 1,
    a_file_path_id       INTEGER NOT NULL,
    b_file_path_id       INTEGER NOT NULL,
    method               TEXT    NOT NULL,       -- what found it: 'phash'
    hash_bits            INTEGER,
    distance             INTEGER,                -- pHash Hamming distance (the method's own measure; never a percentage)
    ahash_distance       INTEGER,                -- supporting measurements, shown beside it
    dhash_distance       INTEGER,
    a_observation_id     INTEGER,                -- the observations whose hashes were compared
    b_observation_id     INTEGER,
    a_analyzer_result_id INTEGER,
    b_analyzer_result_id INTEGER,
    first_generation_id  INTEGER NOT NULL,
    last_generation_id   INTEGER NOT NULL,
    current              INTEGER NOT NULL DEFAULT 1 CHECK (current IN (0, 1)),
    CHECK (a_file_path_id < b_file_path_id),
    UNIQUE (a_file_path_id, b_file_path_id),
    FOREIGN KEY (project_id)     REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (a_file_path_id) REFERENCES file_path (file_path_id),
    FOREIGN KEY (b_file_path_id) REFERENCES file_path (file_path_id)
);
CREATE INDEX ix_visual_candidate_b ON visual_candidate (b_file_path_id);
CREATE INDEX ix_visual_candidate_current ON visual_candidate (current, distance);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase2.visual_candidate_schema','fileorganizer.visual-candidates/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
