-- The File Organizer — schema 010
-- Phase 3, Build 3: Review Routing. The fifth authoritative family:
-- Review Event -- routing-relevant provenance. Additive only; no Phase 1/2
-- table changes; no source file is touched.
--
-- Queues route attention, they do not create truth. Every route the Decide
-- page shows (Conflicts & Exceptions, Needs Revalidation, Blocked by
-- Evidence, Deferred / Snoozed, Ready for Plan, the ordinary queue) is
-- DERIVED -- by Phase3.routing from Phase 2 evidence, the decision record
-- and these rows. What this table holds is the record of routing events:
-- who deferred what, with what return trigger; who skipped past what; what
-- the detector found stale or blocked, and when a route was restored.
--
-- Append-only, like every other p3 table: a restore is a new row that
-- refers to the deferral it ends; nothing is ever updated or deleted.

CREATE TABLE p3_review_event (
    review_event_id     INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    operation_id        INTEGER NOT NULL,
    target_kind         TEXT NOT NULL,      -- file_location | exact_duplicate_group_version
    target_ref          TEXT NOT NULL,
    event_kind          TEXT NOT NULL,      -- deferred | blocked | needs_revalidation | skipped | restored
    return_kind         TEXT,               -- time | evidence_change | source_available | hash_current
                                            -- | preview_available | manual | NULL (skipped, restored)
    return_condition    TEXT,               -- the trigger, readable
    return_on_utc       TEXT,               -- for return_kind = time: when
    detail_json         TEXT,               -- structured detail: the target's evidence at the time
                                            -- (for evidence_change), what drifted (needs_revalidation)
    refers_to_review_event_id INTEGER,      -- a restore names the event it ends
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id),
    FOREIGN KEY(refers_to_review_event_id) REFERENCES p3_review_event(review_event_id)
);
CREATE INDEX ix_p3_review_event_target ON p3_review_event(project_id, target_kind, target_ref, review_event_id);
CREATE INDEX ix_p3_review_event_operation ON p3_review_event(operation_id);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase3.review_event_schema','fileorganizer.p3.review-events/1',strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('phase3.routing_version','fileorganizer.p3.routing/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
