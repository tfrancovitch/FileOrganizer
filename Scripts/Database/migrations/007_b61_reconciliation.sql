-- ============================================================
--  The File Organizer -- Migration 007
--  Schema version: 7
--  Introduced: B6.1 (A-F reconciliation / current-state completion)
-- ============================================================
--
--  B6 separated current state from observation history. B6.1 completes
--  that separation by making file_state a full CURRENT projection rather
--  than only a partial one, and by preserving physical/filesystem facts
--  that B5-B established are required for safe later remediation.
--
--  No BEGIN/COMMIT here. fo_db owns migration transaction control.
-- ============================================================

-- Current-run / current-observation facts needed by hash, analyzers and
-- exports even when an unchanged file does not receive a new history row.
ALTER TABLE file_state ADD COLUMN current_legacy_db_id INTEGER;
ALTER TABLE file_state ADD COLUMN created_local_naive TEXT;
ALTER TABLE file_state ADD COLUMN modified_local_naive TEXT;
ALTER TABLE file_state ADD COLUMN accessed_local_naive TEXT;
ALTER TABLE file_state ADD COLUMN created_utc TEXT;
ALTER TABLE file_state ADD COLUMN accessed_utc TEXT;
ALTER TABLE file_state ADD COLUMN utc_offset_minutes INTEGER;
ALTER TABLE file_state ADD COLUMN timestamp_model TEXT;
ALTER TABLE file_state ADD COLUMN attributes TEXT;
ALTER TABLE file_state ADD COLUMN is_reparse_point INTEGER;
ALTER TABLE file_state ADD COLUMN reparse_tag INTEGER;
ALTER TABLE file_state ADD COLUMN depth INTEGER;
ALTER TABLE file_state ADD COLUMN path_length INTEGER;
ALTER TABLE file_state ADD COLUMN volume_serial TEXT;
ALTER TABLE file_state ADD COLUMN file_index TEXT;
ALTER TABLE file_state ADD COLUMN hard_link_count INTEGER;
ALTER TABLE file_state ADD COLUMN allocated_size_bytes INTEGER;
ALTER TABLE file_state ADD COLUMN created_time_state TEXT
    CHECK (created_time_state IN ('known','unavailable','unknown'));
ALTER TABLE file_state ADD COLUMN modified_time_state TEXT
    CHECK (modified_time_state IN ('known','unavailable','unknown'));
ALTER TABLE file_state ADD COLUMN accessed_time_state TEXT
    CHECK (accessed_time_state IN ('known','unavailable','unknown'));
ALTER TABLE file_state ADD COLUMN hash_observation_id INTEGER;
ALTER TABLE file_state ADD COLUMN hash_run_id INTEGER;
ALTER TABLE file_state ADD COLUMN hash_measurement_mode TEXT
    CHECK (hash_measurement_mode IN ('selective','exhaustive'));
ALTER TABLE file_state ADD COLUMN hash_status TEXT;

-- Observation facts that B5-B found were either absent or collapsed.
ALTER TABLE file_observation ADD COLUMN reparse_tag INTEGER;
ALTER TABLE file_observation ADD COLUMN allocated_size_bytes INTEGER;
ALTER TABLE file_observation ADD COLUMN created_time_state TEXT
    CHECK (created_time_state IN ('known','unavailable','unknown'));
ALTER TABLE file_observation ADD COLUMN modified_time_state TEXT
    CHECK (modified_time_state IN ('known','unavailable','unknown'));
ALTER TABLE file_observation ADD COLUMN accessed_time_state TEXT
    CHECK (accessed_time_state IN ('known','unavailable','unknown'));

-- The path list cannot itself prove that an empty directory or an
-- intentionally skipped reparse subtree existed. Preserve those scan facts
-- explicitly rather than making "not represented" ambiguous.
CREATE TABLE scan_path_event (
    scan_path_event_id INTEGER NOT NULL PRIMARY KEY,
    project_id         INTEGER NOT NULL DEFAULT 1,
    inventory_scan_id  INTEGER NOT NULL,
    source_root_id     INTEGER NOT NULL,
    event_kind         TEXT NOT NULL CHECK (event_kind IN (
                           'empty_directory','skipped_reparse_directory')),
    relative_path      TEXT NOT NULL,
    relative_path_key  TEXT NOT NULL,
    reparse_tag        INTEGER,
    observed_utc       TEXT NOT NULL,
    UNIQUE (inventory_scan_id, event_kind, relative_path_key),
    FOREIGN KEY (project_id) REFERENCES project (project_id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_scan_id) REFERENCES inventory_scan (inventory_scan_id) ON DELETE CASCADE,
    FOREIGN KEY (source_root_id) REFERENCES source_root (source_root_id) ON DELETE CASCADE
);
CREATE INDEX ix_scan_path_event_scan ON scan_path_event (inventory_scan_id, event_kind);
CREATE INDEX ix_scan_path_event_root ON scan_path_event (source_root_id, relative_path_key);

-- Backfill the new current-projection columns from each state's current
-- observation. Pre-B6 rows remain honest: values that never existed remain
-- NULL rather than being fabricated.
UPDATE file_state SET
    current_legacy_db_id = (SELECT o.legacy_db_id FROM file_observation o
                            WHERE o.file_observation_id = file_state.current_observation_id),
    created_local_naive = (SELECT o.created_local_naive FROM file_observation o
                            WHERE o.file_observation_id = file_state.current_observation_id),
    modified_local_naive = (SELECT o.modified_local_naive FROM file_observation o
                            WHERE o.file_observation_id = file_state.current_observation_id),
    accessed_local_naive = (SELECT o.accessed_local_naive FROM file_observation o
                            WHERE o.file_observation_id = file_state.current_observation_id),
    created_utc = (SELECT o.created_utc FROM file_observation o
                   WHERE o.file_observation_id = file_state.current_observation_id),
    accessed_utc = (SELECT o.accessed_utc FROM file_observation o
                    WHERE o.file_observation_id = file_state.current_observation_id),
    utc_offset_minutes = (SELECT o.utc_offset_minutes FROM file_observation o
                          WHERE o.file_observation_id = file_state.current_observation_id),
    timestamp_model = (SELECT o.timestamp_model FROM file_observation o
                       WHERE o.file_observation_id = file_state.current_observation_id),
    attributes = (SELECT o.attributes FROM file_observation o
                  WHERE o.file_observation_id = file_state.current_observation_id),
    is_reparse_point = (SELECT o.is_reparse_point FROM file_observation o
                        WHERE o.file_observation_id = file_state.current_observation_id),
    depth = (SELECT fp.depth FROM file_path fp WHERE fp.file_path_id = file_state.file_path_id),
    path_length = (SELECT o.path_length FROM file_observation o
                   WHERE o.file_observation_id = file_state.current_observation_id),
    volume_serial = (SELECT o.volume_serial FROM file_observation o
                     WHERE o.file_observation_id = file_state.current_observation_id),
    file_index = (SELECT o.file_index FROM file_observation o
                  WHERE o.file_observation_id = file_state.current_observation_id),
    hard_link_count = (SELECT o.hard_link_count FROM file_observation o
                       WHERE o.file_observation_id = file_state.current_observation_id),
    created_time_state = CASE
        WHEN (SELECT o.created_utc FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
         AND (SELECT o.created_local_naive FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
        THEN 'unavailable' ELSE 'known' END,
    modified_time_state = CASE
        WHEN (SELECT o.modified_utc FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
         AND (SELECT o.modified_local_naive FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
        THEN 'unavailable' ELSE 'known' END,
    accessed_time_state = CASE
        WHEN (SELECT o.accessed_utc FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
         AND (SELECT o.accessed_local_naive FROM file_observation o WHERE o.file_observation_id = file_state.current_observation_id) IS NULL
        THEN 'unavailable' ELSE 'known' END;

UPDATE file_observation SET
    created_time_state = CASE WHEN created_utc IS NULL AND created_local_naive IS NULL
                              THEN 'unavailable' ELSE 'known' END,
    modified_time_state = CASE WHEN modified_utc IS NULL AND modified_local_naive IS NULL
                               THEN 'unavailable' ELSE 'known' END,
    accessed_time_state = CASE WHEN accessed_utc IS NULL AND accessed_local_naive IS NULL
                               THEN 'unavailable' ELSE 'known' END
WHERE created_time_state IS NULL OR modified_time_state IS NULL OR accessed_time_state IS NULL;

CREATE INDEX ix_file_state_physical ON file_state (volume_serial, file_index)
    WHERE state = 'present' AND volume_serial IS NOT NULL AND file_index IS NOT NULL;
