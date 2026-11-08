# Changelog — B21 (Phase 4: the action journal, the locks and the job state machine)

Built 2026-11-07/08 from `phase4` at `10fddd3` (the commit that filed the B21 handoff). Branch `build/b21`. **Not committed, tagged or pushed.**
Schema **17**: migration `017_phase4_journal_binding.sql` adds **one** catalog table, `p4_journal_binding`. `APP_VERSION` = `Phase2.VERSION` = **B21**.
`MODULE_VERSION` is unchanged. **B21 touches no real file**: every file action runs against a fake filesystem (one JSON file in a test sandbox),
and no product code renames, moves, creates or deletes anything. There is no user-visible change: nothing in Phases 1-3 reads the new table, and
`PRODUCT_PROJECTORS` is empty.

## What B21 adds
New package `Scripts\Phase4\`; the test-only fakes are `Scripts\p4_fakes.py` and are not part of the package.
- **`paths.py`** — the journal and lock folder for a `project_uid` (production default `%LOCALAPPDATA%\FileOrganizer\journals\<project_uid>\`;
  every test passes a sandbox override). Refuses UNC paths, non-fixed drives and any path under a folder named by `OneDrive`, `OneDriveConsumer`
  or `OneDriveCommercial`. The classifier is injectable (B22 adds the Cloud Files check).
- **`journal.py`** — the action journal: a separate SQLite file, WAL, `synchronous=FULL` on every connection that writes it, explicit
  `BEGIN IMMEDIATE`/`COMMIT`, its own `application_id` and `user_version`. Tables `journal_meta`, `job`, `operation_item`, `evidence_snapshot`,
  `operation_event`, `recovery_record`, `owner`. Events are append-only facts (triggers); a job freezes its revision and manifest hash at C0.
  The fencing token (`run_id`) is checked inside every transaction. The opening rules: create when neither binding nor journal exist; **refuse**
  (`JournalMissing`) when the binding exists and the journal does not — the product never creates a blank journal for a project that has one;
  complete the binding when the journal has no jobs; ask (`JournalBindingAsk`) when it has jobs; refuse on a different project, machine or journal.
- **`locks.py`** — the project lock (`msvcrt.locking`, byte 0, non-blocking, retried 5-10 s, owner bytes plus a journal `owner` row, the `run_id`
  as fencing token, `holder_info()` for a second instance) and the root-level locks (one file per source root keyed on `st_dev`/`st_ino`, a short
  `registry.lock`, nested or identical roots of another project refused both ways).
- **`jobs.py`** — item kinds and states, the transition tables as data (every other edge refused), C0-C4 and P each its own commit, refusal scopes
  `ITEM_LOCAL`/`JOB_GLOBAL`, the circuit breaker (`max(3, ceil(10 %))`, three consecutive, `source_root_available`), transient-lock retries at 250 ms,
  1 s, 3 s, the revision check at C0 and at every resume (never from the job's own writes), pause/resume/shutdown, orphan jobs marked `INTERRUPTED`.
- **`projection.py`** — the projector registry (keyed by `action_id`, idempotent), P after C3 then the `PROJECTED` mark, replay on every open
  driven by table E, `open_session` (folder check, project lock, opening rules, owner row, replay, orphans).
- **`reconcile.py`** — tables A, C, D, E, F **as data** (36 rows), each with an X row ("ask") for a key no row covers; the classifier maps (journal
  state, observed disk state, identity match) to exactly one row; recovery reads, classifies and shows and **takes no action**.
- **Migration 017** — `p4_journal_binding` (at most one row: project uid, journal uid, machine guid, machine name, created time). No `app_meta`
  row; no other table changes; a plan revision locked under B20 still verifies and is not obsoleted.
- **`ARCHITECTURE.md`** — the "Phase 4 (B21): journal, locks, jobs" section (shape, commit points, locks, jobs, projection, tables, tests) with the
  restore ceremony for a lost or corrupt journal folded in.

## Versions and the existing assertions that follow them
`fo_db.APP_VERSION` and `Phase2.VERSION` = `B21`; `fo_db.APP_SCHEMA_VERSION` = 17; `self_check.REQUIRED_SCHEMA_VERSION` and
`Phase2.core.REQUIRED_SCHEMA_VERSION` = 17 (kept equal to the app schema, as `self_check` documents; its "Database creation" check compares for equality).
`fo_analyzers.REQUIRED_SCHEMA_VERSION` stays 16: it is the lowest schema that has the column the analyzers write, and migration 017 adds nothing they need.
Tests that pin the current version or schema were moved to it, none weakened: `p3_regression` (fresh project on schema 17, "one product version"
`B21`/17, a schema-8 project migrates to 17, migrations 9-16 stamped `B21`, the operation's `agent_version`), `p3_fingerprint_check` (migrated to 17),
`p3_visual_check` (on schema 17). `p3_encryption_check` pinned `user_version == 16` inside a check whose purpose is that the 016 column exists;
it now reads "schema 16 or later" (`>= 16`).

## Tests
| Suite | B21 |
|---|---|
| `p4_journal_check` (new) | 291/291 (J5 included: FULL median 10.11 ms per commit triple) |
| `p4_lock_check` (new) | 76/76 |
| `p4_mutation_check` (new) | 41/41 (23 mutants killed, 15 controls clean) |
| `p3_mutation_check` | 69/69 mutants killed (99/99 checks), unchanged |
| quick tier (20 base suites) | 1,585 checks, as at the base |

`p4_mutation_check` has 23 mutants: C1 and C2 merged; a result recorded before the step; recovery executing an A1 resume (injected); a writing
connection at `synchronous=NORMAL`; the project lock taken blocking; the project lock not taken; the fencing check removed; a projection without its
`action_id` key; the unique projection index dropped; the breaker's consecutive rule and its percentage rule disabled; a `JOB_GLOBAL` class
classed `ITEM_LOCAL`; a blank journal created when the binding exists; the revision check skipped at resume; the manifest not compared at resume; the
journal bound by guessing; table rows A3 and E3 deleted; pending events ignored at C4; the table E "ask" ignored; the PROJECTED mark before the
projection; orphan jobs never found; nested root locks refused one way only. Two survived the first run: C4-with-pending-events had no test
(one added to `p4_journal_check` 5b) and the manifest-at-resume test existed but under a slice the mutant did not list (the mutant's slice list was corrected).
No mutant was weakened.

## Not run
Probes L2, L5, L6, L7, J2, J3 need a person, a second Windows account or a VM (steps in the handback). L9 and L10 (OneDrive, SMB) were never run, by instruction.
