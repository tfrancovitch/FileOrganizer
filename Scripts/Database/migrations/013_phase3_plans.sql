-- The File Organizer — schema 013
-- Phase 3, Build 8: the Plan Builder (P3.R7, scoped by P3.R10 §15). Additive
-- only; no Phase 1/2 table changes; no source file is touched, now or ever,
-- by anything these tables describe being built, previewed or locked.
--
-- A Plan is proposed future intent -- a versioned snapshot of logical actions
-- derived from current Decisions and Policies -- never an action log and
-- never a guarantee (P3-A85, P3-A87). A Plan Revision's membership is frozen
-- when it is built (P3-A86): a later match goes into a later Revision. Once
-- built and previewed, a Revision's items, links, preconditions and
-- evidence bindings are never edited; locking adds the fingerprint and the
-- lock event; drift marks it obsolete by an event, never by rewriting it
-- (P3-A96, P3-A97).
--
-- Shape (Build 8's schema decision). The research fixture lab stores a Plan
-- Item's decision ids and hard preconditions as JSON arrays; P3.R7 §45
-- proposes a fully normalized family. This schema normalizes what is
-- QUERIED -- the decision and policy links (the dependency index: "which
-- revisions does decision D feed?" is one indexed lookup), the item
-- dependency graph, and the preconditions with their preview results
-- (first-class objects, P3-A93) -- and keeps as JSON what is only read back
-- whole: an item's evidence binding (the same ids-only shape and column name
-- as p3_decision.evidence_binding_json, so the revalidation detector's drift
-- check reads it unchanged), desired and estimated effects, postconditions.
-- Vocabularies (states, action kinds, precondition keys) are validated by
-- Phase3.registry, not by CHECK constraints, like every p3 table.

CREATE TABLE p3_plan (
    plan_id             INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    operation_id        INTEGER NOT NULL,       -- the operation that created it
    name                TEXT NOT NULL,
    note                TEXT,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id)
);

CREATE TABLE p3_plan_revision (
    plan_revision_id    INTEGER NOT NULL PRIMARY KEY,
    plan_id             INTEGER NOT NULL,
    revision_no         INTEGER NOT NULL,
    operation_id        INTEGER NOT NULL,       -- the 'plan_build' operation
    status              TEXT NOT NULL,          -- current state; its history is p3_plan_event
    fingerprint         TEXT,                   -- SHA-256 of the canonical payload, set at lock
    locked              INTEGER NOT NULL DEFAULT 0,
    obsolete            INTEGER NOT NULL DEFAULT 0,
    supersedes_plan_revision_id INTEGER,
    basis_json          TEXT NOT NULL,          -- what it was built on: record and evidence marks, routing version
    summary_json        TEXT NOT NULL,          -- the preview's counts and evidence-based estimated effects
    UNIQUE(plan_id, revision_no),
    FOREIGN KEY(plan_id) REFERENCES p3_plan(plan_id),
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id),
    FOREIGN KEY(supersedes_plan_revision_id) REFERENCES p3_plan_revision(plan_revision_id)
);
CREATE INDEX ix_p3_plan_revision_plan ON p3_plan_revision(plan_id, revision_no);

-- The Revision's history: built (previewed), locked, obsolete (with the
-- reasons and the dependencies that fired), superseded, archived. Append-only.
CREATE TABLE p3_plan_event (
    plan_event_id       INTEGER NOT NULL PRIMARY KEY,
    plan_revision_id    INTEGER NOT NULL,
    operation_id        INTEGER NOT NULL,
    event_kind          TEXT NOT NULL,          -- previewed | locked | obsolete | superseded | archived
    detail_json         TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(plan_revision_id) REFERENCES p3_plan_revision(plan_revision_id),
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id)
);
CREATE INDEX ix_p3_plan_event_revision ON p3_plan_event(plan_revision_id, plan_event_id);

CREATE TABLE p3_plan_item (
    plan_item_id        INTEGER NOT NULL PRIMARY KEY,
    plan_revision_id    INTEGER NOT NULL,
    item_no             INTEGER NOT NULL,       -- order within the revision (the fingerprint's order)
    action_kind         TEXT NOT NULL,          -- remove_location | move_location | rename_location |
                                                -- update_source_metadata | no_op_preserve (registry)
    target_kind         TEXT NOT NULL,
    target_ref          TEXT NOT NULL,
    source_kind         TEXT,                   -- what made it: an exact-duplicate group or a visual pair
    source_ref          TEXT,
    desired_effect_json TEXT NOT NULL,          -- logical intent only; never an execution method (P3-A88)
    evidence_binding_json TEXT,                 -- ids only, the p3_decision shape
    estimated_effect_json TEXT NOT NULL DEFAULT '{}',
    postconditions_json TEXT NOT NULL DEFAULT '[]',
    impact_class        TEXT,                   -- low | medium | high | critical
    state               TEXT NOT NULL,          -- ready | blocked | blocked_evidence | unsupported_action | preserve
    blocked_reason      TEXT,
    UNIQUE(plan_revision_id, item_no),
    FOREIGN KEY(plan_revision_id) REFERENCES p3_plan_revision(plan_revision_id)
);
CREATE INDEX ix_p3_plan_item_target ON p3_plan_item(target_kind, target_ref);

-- The dependency index: which decisions and policy versions feed which items.
CREATE TABLE p3_plan_item_decision (
    plan_item_id        INTEGER NOT NULL,
    decision_id         INTEGER NOT NULL,
    role                TEXT NOT NULL,          -- justifies | retains (a decision that blocks the item)
    PRIMARY KEY(plan_item_id, decision_id, role),
    FOREIGN KEY(plan_item_id) REFERENCES p3_plan_item(plan_item_id),
    FOREIGN KEY(decision_id) REFERENCES p3_decision(decision_id)
);
CREATE INDEX ix_p3_plan_item_decision_decision ON p3_plan_item_decision(decision_id);

CREATE TABLE p3_plan_item_policy (
    plan_item_id        INTEGER NOT NULL,
    policy_version_id   INTEGER NOT NULL,
    PRIMARY KEY(plan_item_id, policy_version_id),
    FOREIGN KEY(plan_item_id) REFERENCES p3_plan_item(plan_item_id),
    FOREIGN KEY(policy_version_id) REFERENCES p3_policy_version(policy_version_id)
);
CREATE INDEX ix_p3_plan_item_policy_policy ON p3_plan_item_policy(policy_version_id);

-- A directed, acyclic graph within one revision: an item that must not
-- happen unless another holds (a removal depends on its keeper's retention).
CREATE TABLE p3_plan_dependency (
    plan_item_id        INTEGER NOT NULL,
    depends_on_item_id  INTEGER NOT NULL,
    PRIMARY KEY(plan_item_id, depends_on_item_id),
    FOREIGN KEY(plan_item_id) REFERENCES p3_plan_item(plan_item_id),
    FOREIGN KEY(depends_on_item_id) REFERENCES p3_plan_item(plan_item_id)
);

-- Declarative preconditions (P3.R7 §36-37) and what the Phase 3 Evidence
-- Preview found. stage = evidence_preview (checked now, from project
-- evidence only) | live_preflight (declared for Phase 4, which does not
-- exist; result NULL -- not evaluated here). For a hard precondition,
-- 'unknown' blocks exactly like 'fail' (P3-A91).
CREATE TABLE p3_plan_precondition (
    plan_precondition_id INTEGER NOT NULL PRIMARY KEY,
    plan_item_id        INTEGER NOT NULL,
    condition_key       TEXT NOT NULL,
    condition_class     TEXT NOT NULL,          -- identity | retention | evidence | environment | collision |
                                                -- concurrency | reversibility
    severity            TEXT NOT NULL,          -- hard | warning | informational
    stage               TEXT NOT NULL,          -- evidence_preview | live_preflight
    parameters_json     TEXT NOT NULL DEFAULT '{}',
    result              TEXT,                   -- pass | fail | unknown | not_applicable | unsupported | NULL
    detail              TEXT,
    FOREIGN KEY(plan_item_id) REFERENCES p3_plan_item(plan_item_id)
);
CREATE INDEX ix_p3_plan_precondition_item ON p3_plan_precondition(plan_item_id);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase3.plan_schema','fileorganizer.p3.plan/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
