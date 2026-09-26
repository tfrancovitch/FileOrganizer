-- The File Organizer — schema 014
-- Phase 3, Build 9: project metadata and desired source metadata (P3.R8,
-- scoped by P3.R10 §4.6, §6-§8, §16). Additive only; no Phase 1/2 table
-- changes. Nothing described here writes metadata into any file: no writer
-- exists, and this build certifies none (R10 §16).
--
-- The Decision side needs NO schema change. p3_decision.decision_kind and
-- target_kind carry no CHECK constraint (migration 009 left them to
-- Phase3.registry so later builds could widen them), so the four metadata
-- decision kinds -- project_metadata_set, desired_source_metadata_set,
-- desired_source_metadata_clear, leave_source_metadata_unchanged -- and the
-- metadata_question target are registry data, recorded through the same
-- generic Decision engine (R10 §4.6: no second engine).
--
-- What IS new, with no precedent in any earlier schema:
--
--   p3_metadata_capability  the metadata capability RECORD (R8 §16-18,
--                           narrowed by the Build 9 synthesis §8): file family
--                           x semantic field -> read support, write support,
--                           status. Only status 'supported' satisfies the
--                           Plan Builder's writer_supported precondition, and
--                           no row here says 'supported': the rows record what
--                           Phase 1/2's analyzers READ; nothing is writable.
--                           A family/field with no row is status 'unknown'.
--                           Writer, writer version, write / verification /
--                           reversibility strategy are writer-certification
--                           bookkeeping with nothing to populate them yet:
--                           columns a later build adds, not columns left empty.
--
--   p3_metadata_question    the metadata_question target (R10 §7): one file
--                           location x one semantic field, with a stable id a
--                           Decision can name. The row is identity only. What
--                           Phase 2 observed (value and value-state), the
--                           capability that applies and the integrity flags
--                           (signed / hard-linked) are read from the evidence
--                           and the capability record when asked, never
--                           copied here; a Decision's evidence binding records
--                           which observation and analyzer results it rested on.
--
-- The semantic field registry itself (identifier, display name, value type)
-- is Phase3.registry.METADATA_FIELDS, like every other p3 vocabulary.

CREATE TABLE p3_metadata_capability (
    file_family         TEXT NOT NULL,          -- Phase3.registry.FILE_FAMILIES (pdf, ooxml, jpeg, ...)
    semantic_field      TEXT NOT NULL,          -- Phase3.registry.METADATA_FIELDS key
    read_support        TEXT NOT NULL,          -- phase2_analyzer | none | unknown
    write_support       TEXT NOT NULL,          -- none | unknown (certified writers are later work)
    status              TEXT NOT NULL,          -- supported | experimental | read_only | blocked | unknown
    note                TEXT,
    PRIMARY KEY(file_family, semantic_field)
);

CREATE TABLE p3_metadata_question (
    question_id         INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    file_path_id        INTEGER NOT NULL,
    semantic_field      TEXT NOT NULL,
    operation_id        INTEGER NOT NULL,       -- the operation that first named it
    UNIQUE(project_id, file_path_id, semantic_field),
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(file_path_id) REFERENCES file_path(file_path_id),
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id)
);

-- What Phase 1/2 reads today (Scripts\Database\fo_analyzers.py): the PDF and
-- Office analyzers promote Title, Author and the creation date; the RAW
-- analyzer DateTimeOriginal; the audio analyzer Title. Read, never written.
INSERT INTO p3_metadata_capability(file_family, semantic_field, read_support, write_support, status, note) VALUES
 ('pdf',        'title',           'phase2_analyzer', 'none', 'read_only', 'PDF Analysis: document information Title'),
 ('pdf',        'creator',         'phase2_analyzer', 'none', 'read_only', 'PDF Analysis: document information Author'),
 ('pdf',        'content_created', 'phase2_analyzer', 'none', 'read_only', 'PDF Analysis: document information CreationDate'),
 ('ooxml',      'title',           'phase2_analyzer', 'none', 'read_only', 'Office Analysis: core properties title'),
 ('ooxml',      'creator',         'phase2_analyzer', 'none', 'read_only', 'Office Analysis: core properties creator'),
 ('ooxml',      'content_created', 'phase2_analyzer', 'none', 'read_only', 'Office Analysis: core properties created'),
 ('ole_office', 'title',           'phase2_analyzer', 'none', 'read_only', 'Office Analysis (legacy): summary information title'),
 ('ole_office', 'creator',         'phase2_analyzer', 'none', 'read_only', 'Office Analysis (legacy): summary information author'),
 ('ole_office', 'content_created', 'phase2_analyzer', 'none', 'read_only', 'Office Analysis (legacy): summary information created'),
 ('raw_image',  'content_created', 'phase2_analyzer', 'none', 'read_only', 'RAW Image Analysis: EXIF DateTimeOriginal'),
 ('audio',      'title',           'phase2_analyzer', 'none', 'read_only', 'Audio Analysis: tag Title');

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase3.metadata_schema','fileorganizer.p3.metadata/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
