-- The File Organizer — schema 011
-- Phase 3, Build 4: Bulk / Policy. The fourth authoritative family, Bulk
-- Batch with Frozen Membership. Additive only; no Phase 1/2 table changes;
-- no source file is touched.
--
-- A batch is one person's decision applied to many targets at once, after
-- a preview. What it records is the fixture lab's shape: the scope kind
-- (the exact set a person checked, or what a filter listed at one moment),
-- the query that produced it, and the resolved membership -- frozen at
-- commit. A snapshot never becomes a policy (P3-A41): a target that comes
-- to match the same query later is NOT a member, and nothing here is ever
-- re-evaluated. Every decision the batch recorded carries
-- origin_kind = 'bulk_explicit_human' and origin_ref = the batch id, so
-- "what did this batch change?" is one query; undoing a batch withdraws
-- those decisions (withdrawal rows) and leaves these rows exactly as they
-- were.
--
-- The product writes a batch in the same operation (kind 'bulk') as the
-- decisions it records, so frozen and committed are both 1 here; the
-- columns keep the contract's shape for a batch frozen but not yet applied,
-- which no build records yet. Append-only, like every p3 table.

CREATE TABLE p3_bulk_batch (
    batch_id            INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    operation_id        INTEGER NOT NULL,       -- the 'bulk' operation: who, when, through what
    scope_kind          TEXT NOT NULL,          -- explicit_selection | query_result_snapshot (registry)
    query_json          TEXT,                   -- the frozen query; NULL for an explicit selection
    frozen              INTEGER NOT NULL DEFAULT 1,
    committed           INTEGER NOT NULL DEFAULT 1,
    action              TEXT NOT NULL,          -- one of Phase3.registry.BULK_ACTIONS
    parameters_json     TEXT NOT NULL,          -- the action's inputs: the folder, the deferral, the checked ids
    mode                TEXT NOT NULL DEFAULT 'preserve',   -- preserve (this build) | supersede (later)
    preview_json        TEXT NOT NULL,          -- the categorized counts exactly as previewed at commit
    note                TEXT,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id)
);
CREATE INDEX ix_p3_bulk_batch_operation ON p3_bulk_batch(operation_id);

-- One row per target the batch addressed, with what became of it. Only a
-- 'decided' member has a decision; the rest are the exceptions the preview
-- counted (already satisfied, preserved, conflict, blocked, not applicable),
-- kept so that the record says why the batch did not touch them.
CREATE TABLE p3_bulk_member (
    member_id           INTEGER NOT NULL PRIMARY KEY,
    batch_id            INTEGER NOT NULL,
    target_kind         TEXT NOT NULL,          -- file_location | exact_duplicate_group_version
    target_ref          TEXT NOT NULL,
    disposition         TEXT NOT NULL,          -- one of Phase3.registry.DISPOSITIONS
    detail              TEXT,
    FOREIGN KEY(batch_id) REFERENCES p3_bulk_batch(batch_id)
);
CREATE INDEX ix_p3_bulk_member_batch ON p3_bulk_member(batch_id, member_id);
CREATE INDEX ix_p3_bulk_member_target ON p3_bulk_member(target_kind, target_ref);

-- Decisions a batch records are found by their origin.
CREATE INDEX ix_p3_decision_origin ON p3_decision(origin_kind, origin_ref);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase3.bulk_schema','fileorganizer.p3.bulk/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
