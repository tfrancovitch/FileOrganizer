-- The File Organizer — schema 017
-- B21 (Phase 4, the action journal): the catalog's record of WHICH journal belongs to this project.
--
-- The action journal is a separate SQLite file (Scripts\Phase4\journal.py) in a local, never-synced
-- folder keyed by project_uid. The catalog keeps exactly one fact about it, so that:
--   * a project that already has a journal never silently gets a blank one (binding present, journal
--     missing -> refuse);
--   * a restored or replaced catalog is noticed (journal present, binding absent, jobs recorded -> ask);
--   * a journal copied from another project or another machine is refused (uid / MachineGuid mismatch).
--
-- p4_journal_binding   at most ONE row (binding_id = 1)
--     project_uid     the project this journal belongs to (equals project.project_uid)
--     journal_uid     the journal file's own uid (journal_meta.journal_uid)
--     machine_guid    HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid of the machine that created it
--     machine_name    the machine name, for the message only
--     created_utc     when the binding was written
--
-- Additive: one new table. No existing table, row or column is changed, so every Phase 1-3 record
-- (including a plan revision locked under B20, its fingerprint and its obsolescence state) is
-- untouched (not even an app_meta row is added: the handoff allows exactly one new table). Nothing in
-- Phases 1-3 reads this table.

CREATE TABLE p4_journal_binding (
    binding_id    INTEGER NOT NULL PRIMARY KEY CHECK (binding_id = 1),
    project_uid   TEXT    NOT NULL,
    journal_uid   TEXT    NOT NULL,
    machine_guid  TEXT    NOT NULL,
    machine_name  TEXT    NOT NULL,
    created_utc   TEXT    NOT NULL
);
