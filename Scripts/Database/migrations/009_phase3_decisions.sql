-- The File Organizer — schema 009
-- Phase 3 (Decide / Plan), Builds 1-2: Core Persistence + Exact-Duplicate Decisions.
-- Additive only: source files are never touched by this migration, and no
-- Phase 1/2 table changes.
--
-- Four authoritative families, every one append-only:
--   p3_operation            one user action: who, through what, when
--   p3_decision             immutable human adjudication of one target
--   p3_decision_withdrawal  the withdrawal of a decision, as its own row
--   p3_policy + version     reusable, versioned intent (protect / prefer / avoid)
--
-- Everything else Phase 3 shows -- the current keeper set, the canonical,
-- the protected set, plan-eligible reclaim -- is DERIVED from these tables
-- and Phase 2 evidence by Phase3.resolve, never stored as truth.
--
-- Nothing here is ever UPDATEd or DELETEd by the product: changing one's
-- mind is a new decision that supersedes the old one, or a withdrawal row;
-- changing a policy is a new version. A decision's evidence binding is a
-- reference into Phase 1/2 tables (file_path, file_observation, content),
-- never a copy of the evidence.

CREATE TABLE p3_operation (
    operation_id        INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    kind                TEXT NOT NULL,      -- decision | withdrawal | policy | override_protection
    -- Who decided (actor) and through what (agent). Only one actor kind
    -- exists in this build ('explicit_human'); a later software agent is a
    -- new actor/agent kind, never a human pretending. The vocabularies are
    -- validated by Phase3.registry / Phase3.store rather than by CHECK
    -- constraints, so that Builds 3-7 can widen them without rebuilding
    -- these tables (SQLite cannot alter a CHECK in place).
    actor_kind          TEXT NOT NULL,
    actor_id            TEXT,
    agent_kind          TEXT NOT NULL,
    agent_version       TEXT NOT NULL,
    session_id          TEXT NOT NULL,
    command_id          TEXT NOT NULL,
    occurred_utc        TEXT NOT NULL,
    note                TEXT,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE
);
CREATE INDEX ix_p3_operation_time ON p3_operation(project_id, occurred_utc, operation_id);

CREATE TABLE p3_decision (
    decision_id             INTEGER NOT NULL PRIMARY KEY,
    project_id              INTEGER NOT NULL DEFAULT 1,
    operation_id            INTEGER NOT NULL,
    target_kind             TEXT NOT NULL,  -- file_location | exact_duplicate_group_version (registry)
    target_ref              TEXT NOT NULL,
    decision_kind           TEXT NOT NULL,  -- one of Phase3.registry.DECISION_KINDS
    value_json              TEXT NOT NULL,
    origin_kind             TEXT NOT NULL,  -- explicit_human in this build
    origin_ref              TEXT,
    evidence_binding_json   TEXT NOT NULL,
    supersedes_decision_id  INTEGER,
    rationale               TEXT,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id),
    FOREIGN KEY(supersedes_decision_id) REFERENCES p3_decision(decision_id)
);
CREATE INDEX ix_p3_decision_target ON p3_decision(project_id, target_kind, target_ref, decision_id);
CREATE INDEX ix_p3_decision_operation ON p3_decision(operation_id);
CREATE INDEX ix_p3_decision_supersedes ON p3_decision(supersedes_decision_id);

-- A withdrawal is a row, not a flag: the decision it withdraws stays exactly
-- as recorded. A decision is withdrawn at most once.
CREATE TABLE p3_decision_withdrawal (
    withdrawal_id       INTEGER NOT NULL PRIMARY KEY,
    decision_id         INTEGER NOT NULL UNIQUE,
    operation_id        INTEGER NOT NULL,
    reason              TEXT,
    FOREIGN KEY(decision_id) REFERENCES p3_decision(decision_id),
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id)
);

-- A policy is an identity with a fixed kind; what it says is its versions.
CREATE TABLE p3_policy (
    policy_id           INTEGER NOT NULL PRIMARY KEY,
    project_id          INTEGER NOT NULL DEFAULT 1,
    kind                TEXT NOT NULL,      -- one of Phase3.registry.POLICY_KINDS
    created_operation_id INTEGER NOT NULL,
    FOREIGN KEY(project_id) REFERENCES project(project_id) ON DELETE CASCADE,
    FOREIGN KEY(created_operation_id) REFERENCES p3_operation(operation_id)
);

-- Versions are immutable. The newest version of a policy is its current
-- statement; a 'retired' version is how a policy is switched off (the row
-- that retires it is itself never changed).
CREATE TABLE p3_policy_version (
    policy_version_id   INTEGER NOT NULL PRIMARY KEY,
    policy_id           INTEGER NOT NULL,
    operation_id        INTEGER NOT NULL,
    version_no          INTEGER NOT NULL,
    scope_json          TEXT NOT NULL,
    effect_json         TEXT NOT NULL,
    status              TEXT NOT NULL CHECK(status IN ('active','retired')),
    supersedes_policy_version_id INTEGER,
    rationale           TEXT,
    UNIQUE(policy_id, version_no),
    FOREIGN KEY(policy_id) REFERENCES p3_policy(policy_id),
    FOREIGN KEY(operation_id) REFERENCES p3_operation(operation_id),
    FOREIGN KEY(supersedes_policy_version_id) REFERENCES p3_policy_version(policy_version_id)
);

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase3.decision_schema','fileorganizer.p3.decisions/1',strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('phase3.evidence_binding_schema','fileorganizer.p3.evidence-binding/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
