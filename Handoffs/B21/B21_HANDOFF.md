# Phase 4 handoff: B21 (the action journal, the locks and the job state machine)

**From:** the Architect · **Date:** 2026-11-07 · **For:** `fo-builder`
**Base:** branch `phase4` at the commit that files this handoff (its parent is `985366f` or a later Phase 4 planning
commit). B21 is the first Phase 4 build; no earlier `phase4-b##` tag exists.
**Build branch / worktree:** `build/b21` in `C:\FileOrganizerTesting\FileOrganizer-Phase4-B21\` (made by the steward:
`git -C C:\FileOrganizerTesting\FileOrganizer worktree add -b build/b21 C:\FileOrganizerTesting\FileOrganizer-Phase4-B21 phase4`).
**Handback goes to:** `PHASE4_B21_HANDBACK.md` at the worktree root; `CHANGELOG-B21.md` beside it; progress in
`PROGRESS_B21.md` (one line per unit).

**Read first, in this order:** `AGENTS.md`; this handoff; `Handoffs\Phase4\PHASE4_SYNTHESIS.md` **§8.2 "The journal"
and "The lock", §8.3 R4.7, and §12 "Journal and catalog" and "Running a job"** (§12 wins where they differ). Ask
`fo-scout` for anything else; do not read the research reports whole. The sections you may need:
`C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.3_report.md` §2.5 (reconciliation tables A, C, D), §3 and §5.5
(lock and probes); `R4_REVIEW_GATE1.md` §2.1 and §2.4 (G1, G2, G14, G18, G19, G24, G28).

## Short version
B21 builds the machinery every later Phase 4 build writes through, and **touches no real file**:
- a separate, durable action journal (SQLite, WAL, `synchronous=FULL`) that is the single authority for what was done;
- the project lock and a root-level lock that serialise every mutating run;
- the job and item state machine with its commit points, refusal scopes and circuit breaker;
- a replayable projection of journal events into the catalog;
- the reconciliation tables that classify an interrupted job on restart and never act on their own.

Everything is driven by a **fake action over a fake filesystem** and by fault injection at every commit point. The
thing most likely to go wrong: a commit point that is skipped, merged or reordered under a crash. The tests and
mutants in units 4-7 exist to catch it.

## Recommended models
| Session or unit | Model | Why |
|---|---|---|
| Build session (`fo-builder`), units 1-4, 7-8 | sonnet | default |
| Units 5-6 (projection and reconciliation) | **opus** | Safety-critical: they decide what the product believes after a crash. Finish unit 4, update `PROGRESS_B21.md`, stop. Thomas starts a new `fo-builder` session on Opus in the same worktree, which resumes from the progress file |
| Suite runs (`fo-runner`) | haiku | |
| Lookups (`fo-scout`) | haiku | |

Escalate only as this table allows. Record any escalation, and why, in the handback.

## 1. Expectations to check first
Report each in the handback's §1. If one fails, say so before building on it.
1. `git log -1` on `build/b21` is the filing commit; `git status` is clean except `PROGRESS_B21.md` once you create it.
2. `Scripts\Database\fo_db.py`: `APP_VERSION = "B20"`, `APP_SCHEMA_VERSION = 16`. `Scripts\Phase2\__init__.py`:
   `VERSION = "B20"`. Migrations `001`-`016` present and contiguous.
3. The `project` table (`001_initial.sql`) has `project_uid TEXT NOT NULL UNIQUE` and one row (`project_id = 1`).
4. `fo_db.backup_database(paths, reason)` uses SQLite's backup API (lines ~377-399).
5. `SOURCE_SHA256.csv` lists 235 files; `Scripts\Test-Installation.ps1` prints "manifest verified".
6. Suites at the base, run by `fo-runner` **before you change anything**: `Scripts\run_suites.ps1 -Tier quick`
   (STATUS.md records 20 of 20 exited 0, 1,585 checks, on 2026-11-01) and `-Tier standard` (mutation 69/69 killed,
   `b6_regression` 73/73 at B20). Record the real counts; they are the floor for §5.
7. `python --version` and `python -c "import sys; print(sys.executable)"` (the interpreter `run_suites.ps1` uses).
   Record both. Expected: 3.14.x on PATH. Whether `TheFileOrganizer.bat` selects the same interpreter is
   unverified: read its selection logic and say what it picks.
8. No `Scripts\Phase4\` folder and no `p4_*` file exist yet.

## 2. Decisions already made (do not reopen)
| Decision | Source |
|---|---|
| The journal is a **separate SQLite file**: `journal_mode=WAL`, `synchronous=FULL` on every connection that writes it, explicit `BEGIN IMMEDIATE`/`COMMIT`, `foreign_keys=ON`, `busy_timeout=5000`, its own `application_id` and `user_version`. It is backed up only through SQLite's backup API | D4, owner 2026-11-07; R4.3 §5.2 |
| The journal, its `-wal`/`-shm` and the locks live in a **local, never-synced, non-network folder keyed by `project_uid`**. Production default: `%LOCALAPPDATA%\FileOrganizer\journals\<project_uid>\`. A journal or lock path that is a UNC path, on a non-`DRIVE_FIXED` drive, or under any OneDrive folder is refused with a clear message | D4; gate 1 G18 |
| **The journal is the single authority for physical actions.** Every catalog change is an idempotent projection of a journal event, keyed by `action_id`, with a per-event projection state. Start-up replays unprojected events; the same replay repairs a restored older catalog | Gate 1 G1; synthesis §12 |
| Commit points, never merged and never reordered: **C0** job approval → preconditions → **C1** intent → the one step → **C2** result → verify → **C3** verification and terminal item state → **P** projection into the catalog, then the event marked projected → **C4** job close. A result is never recorded before the step ran. Call it "journalled intent and verification", never a transaction | R4.3 §5.3; the corpus; gate 1 G1 |
| A job **freezes its revision at C0** (the manifest hash). Obsolescence is checked at C0 and at every resume, never from the job's own writes | Gate 1 G2 |
| Recovery **classifies and shows; it never acts on its own.** Every resume or roll-back is a new journalled action that the person chooses. A retry is a new step with its own intent, never a replay | Axiom 17; R4.3 §2.5-2.6 |
| Refusal scopes `ITEM_LOCAL` / `JOB_GLOBAL`. The circuit breaker pauses the job on 3 consecutive refusals of one class, 10 % of the job's items refused in one class, or any `source_root_available` failure. A transient lock refusal is retried at 250 ms, 1 s and 3 s, then ends `BLOCKED_LOCKED` | Gate 1 G14; synthesis §8.3 R4.7 as amended |
| The project lock is `<project_uid>.lock`, held with `msvcrt.locking(fd, LK_NBLCK, 1)` on byte 0 for the life of any execute, recovery or undo run. Owner information (Windows user, machine name, PID, start time, operation, heartbeat) goes in bytes 1 onward and is mirrored in an `owner` row in the journal. The owner's `run_id` is a **fencing token** checked inside every C1, C2, C3 and C4 transaction. A busy lock is retried for 5-10 s before reporting busy. No named mutex, no `BEGIN EXCLUSIVE` held across a run, no lease alone | R4.3 §3, §5.5 |
| A **root-level lock** per source root, keyed on the root directory's (`st_dev`, `st_ino`) from `os.stat` (on Windows: the volume serial and the file ID, the key synthesis §12 names; B22 may switch to `FileIdInfo` read by handle), so two projects aimed at the same or nested roots cannot mutate at once. **v1 mutation is single-machine:** the journal is bound to the machine (`MachineGuid`), and the product **never auto-creates a blank journal for a project that already has one** | Gate 1 G18 |
| The product deletes nothing, anywhere (D6). A job may contain approved `create_folder` steps, each its own journalled, reversible item (D8) | Owner, 2026-11-07 |
| Hard-linked files are refused for move and rename, and moves bind to the file object. B22 and B24 enforce this; B21 only carries the item kinds | Gate 1 G4, G15 |

## 3. Units, in priority order
A session cut off part-way must still have delivered the most important units. New product code goes in a new
package `Scripts\Phase4\` (`__init__.py`, `paths.py`, `journal.py`, `locks.py`, `jobs.py`, `projection.py`,
`reconcile.py`). Test-only code (the fake filesystem, the fake action, crash helpers) goes in `Scripts\p4_fakes.py`,
never in the package. New suites: `Scripts\p4_journal_check.py`, `Scripts\p4_lock_check.py`,
`Scripts\p4_mutation_check.py`.

| # | Unit | Done when (testable) | Tests / fixtures / mutants | Model |
|---|---|---|---|---|
| 1 | **Probes first.** Run the R4.3 §5.5 lock and journal probes that work unattended in a sandbox: L1 (`taskkill /F`); L3 (`os._exit`, and an access violation through `ctypes`); L4 (a child process outlives the killed holder); L8 (1,000 racing acquisitions from two processes); L11 (holder suspended past its heartbeat); L12 (another process opens the lock file to read it); J5 (10,000 C1/C2/C3 commit triples timed under `FULL` and under `NORMAL`, on a sandbox journal). L2, L5, L6, L7, J2 and J3 need a person, a second Windows account or a VM: write their steps for Thomas in the handback and mark them **not run**. **Do not run L9 or L10 (OneDrive, SMB) at all**; unit 2 tests the refusal of those paths with an injected classifier | Each automated probe has a recorded result. L1/L3 record the measured lock-release delay after a kill, which sets unit 3's retry window. L4 shows the child does not hold the lock (if it does, open with `O_NOINHERIT`). L8 never shows two holders. J5 reports the median and 95th-percentile cost per triple | `p4_lock_check.py` (L probes); `p4_journal_check.py` (J5, reported, not asserted) | sonnet |
| 2 | **Paths and the journal store.** *`paths.py`*: the journal and lock folder for a `project_uid`, with an override that every test must use. Validation refuses UNC paths, non-fixed drives, and any path under the folders named by the `OneDrive`, `OneDriveConsumer` and `OneDriveCommercial` environment variables. Keep the validator injectable; B22 adds the Cloud Files check. *`journal.py`*: create and open with the pragmas of §2; a schema for Job, OperationItem, EvidenceSnapshot, OperationEvent, RecoveryRecord and Owner, plus one `journal_meta` row (journal uid, `project_uid`, the machine GUID read with `winreg` from `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid`, read-only, the machine name, app version, created time). Minimum durable facts per event: job and item ids; plan and evidence references; before and after identity; method; native result and Win32 error; verification result; recovery token; the authorising record; software version; `run_id`; the projection payload and state (`NOT_APPLICABLE` / `PENDING` / `PROJECTED`). `UNIQUE(item_id, seq)` on events. *Migration `017`*: one catalog table, `p4_journal_binding`, with at most one row (`project_uid`, `journal_uid`, `machine_guid`, `machine_name`, `created_utc`). *Opening rules:* no binding and no journal → create the journal, then write the binding. Binding present, journal missing → refuse (`JournalMissing`); never create one. Journal present, binding absent, no jobs in the journal → complete the binding. Journal present, binding absent, jobs in the journal → refuse and ask. The journal's `project_uid` or machine differs from the catalog's → refuse | A journal opened twice reports `synchronous=2` and `journal_mode=wal` on every writing connection. Each refusal rule has a test. Migration 017 on a sandbox copy of a B20-schema project leaves every other table byte-identical (dump compare), and a plan revision locked under B20 **still verifies and is not obsoleted** | `p4_journal_check.py`; a B20-schema fixture database built by the test (or copied from the Phase 3 fixture lab into the sandbox) | sonnet |
| 3 | **Locks.** *`locks.py`*: the project lock as in §2, plus `holder_info()` for a second instance (who, machine, since when, operation, heartbeat age), so B25 can show a read-only banner. *The root-level lock*: one file per root in the lock folder, named from the root's (`st_dev`, `st_ino`), recording `project_uid`, the root's display path and the key of every ancestor directory. To acquire it, take a short-held `registry.lock` in the same folder; refuse if any other **held** root lock's chain contains this root's key, or this root's chain contains its key; then lock its own file. The OS lock is the authority, so leftover lock files are harmless | Two project ids aimed at the same root: the second is refused with the holder's info. Nested roots (`A` and `A\B`): refused both ways. A holder killed with `taskkill /F` frees both locks within the window measured in unit 1. Fencing: when a second process takes the lock after the first was frozen, the first's next commit fails `JOB_GLOBAL` | `p4_lock_check.py` | sonnet |
| 4 | **The job and item state machine, and the runner.** *Item kinds* (`jobs.py`): `move`, `rename`, `case_only`, `quarantine`, `restore`, `create_folder`, `metadata_write`, and `fake`, the only one B21 executes. *States*: item and job states exactly as synthesis §8.3 R4.7, as amended in §12. *Commits*: C0-C4 and P in that order, each a separate commit, each checking the fencing token. *Refusals*: the scopes, circuit breaker and transient-lock retry of §2. *C0 records*: the manifest hash; a catalog backup taken with `fo_db.backup_database` into the sandbox; the person's answer to "OneDrive is synced and not paused", stored as given and never inferred; the revision reference. *Revision check*: a `revision_check` callback runs at C0 and at resume (B24 wires in the Phase 3 check; tests inject one). *Pause and resume*: a paused job keeps the lock while its process lives and releases it on exit, leaving the job `INTERRUPTED`; resume re-runs the item's preflight. *Commit failure*: any journal commit failure, including an injected `SQLITE_FULL`, is `JOB_GLOBAL`. *The fake action* (`p4_fakes.py`): no-overwrite renames and folder creation on a **fake filesystem** saved as one JSON file inside the sandbox, so a restart sees "disk" state | A 500-item fake job runs to `COMPLETED`. An injected unclassified failure on item 7 pauses the job with items 8-500 untouched. Three consecutive `BLOCKED_LOCKED` refusals trip the breaker. A `JOB_GLOBAL` refusal on item 1 stops the job. A revision check that fails at resume blocks the resume. A counting fake proves the job never triggers the revision check from its own writes | `p4_journal_check.py`: the state-machine tables as tests; every transition not in the table is refused | sonnet |
| 5 | **The projection.** *`projection.py`*: a registry of projectors by item kind. A projector is a function of one journal event that writes catalog rows keyed by `action_id`, and it is idempotent (running it twice changes nothing). B21 registers only a **fake projector**, writing a sandbox-only `p4_fake_projection` table in a sandbox catalog copy; the real projectors (succession, `file_state` epochs, observations) are B24's. *Order*: projection runs only after C3, and the event is then marked `PROJECTED`. C4 closes a job only when every event is `PROJECTED` or `NOT_APPLICABLE`. *On every journal open*: replay every `PENDING` event, and detect catalog rows whose `action_id` the journal has never heard of | A kill between C3 and P, or between P and the `PROJECTED` mark, converges on the next open with exactly one catalog row per action. An older catalog restored under a newer journal is repaired by replay. A catalog row unknown to the journal stops everything (`JOB_GLOBAL`, "ask") | `p4_journal_check.py`, with table E (unit 6) as the oracle | **opus** |
| 6 | **Reconciliation.** *`reconcile.py` holds five tables as data*, and table B (cross-volume) is not built:<br>• **A**, same-volume rename or move: R4.3 §2.5 rows A1-A12.<br>• **C**, metadata write: rows C1-C4.<br>• **D**, job level.<br>• **E**, journal × projection. Event `PENDING`, no catalog row → replay. Catalog row present, event not marked → record. Event `PROJECTED`, catalog row missing (restored catalog) → replay. Catalog row for an action the journal lacks → ask (`JOB_GLOBAL`).<br>• **F**, `create_folder`. Intent recorded, folder absent → not applied. Intent recorded, folder present and empty → applied unrecorded: record, then verify. Folder present and not empty, or parent missing → ask.<br>*The classifier* maps (last durable state, observed fake-disk state, identity match) to exactly one row. Recovery shows the row and its next action and takes none. *The tests are generated from the tables*, one or more per row, so a row without a test fails the suite. *The restore ceremony* for a lost or corrupt journal is documented in `ARCHITECTURE.md`: take the latest backup, compare its last receipt with the identities on disk, reconcile the catalog, and get the person's acknowledgement before any action | Every row of A, C, D, E and F has a generated test. Where possible it reaches the row by a real kill at the matching point (a child process killed with `TerminateProcess` at a named hook); where a kill cannot reach it, it constructs the journal and fake-disk state directly, and says so per row. Recovery executes no row's next action | `p4_journal_check.py`; the row list printed with pass/fail | **opus** |
| 7 | **Mutation sensitivity.** `p4_mutation_check.py`, using the method of `p3_mutation_check.py`: one text substitution in a sandboxed copy, a control run first, and a survivor adds a test (the mutant is never weakened). Mutants, at least: C1 and C2 merged into one commit; the result recorded before the step; recovery executing an A1 "resume" by itself; a writing connection opened with `synchronous=NORMAL`; the project lock taken blocking, or not at all; the fencing check removed; a projection without its `action_id` key (not idempotent); the breaker threshold disabled; a `JOB_GLOBAL` reason classed `ITEM_LOCAL`; a blank journal created when the binding exists; the revision check skipped at resume; a table row deleted from `reconcile.py` | Every mutant killed; the table printed | `p4_mutation_check.py` | sonnet |
| 8 | **Versions, docs, manifest.** Set `APP_VERSION = "B21"`, `VERSION = "B21"`, `APP_SCHEMA_VERSION = 17`. Add a "Phase 4 (B21): journal, locks, jobs" section to `ARCHITECTURE.md` (shape, commit points, the tables, the restore ceremony). Write `CHANGELOG-B21.md`. In `Scripts\run_suites.ps1`, register `p4_journal_check` and `p4_lock_check` in the `quick` tier and `p4_mutation_check` in `standard` (this is tooling, not product; the edit is approved). Regenerate and verify `SOURCE_SHA256.csv` | `Test-Installation.ps1` prints "manifest verified"; the suite table of §5 | | sonnet |

## 4. Rules for this round
- **Sandbox root:** every test creates its own root with `tempfile.mkdtemp(prefix="fo_p4_b21_")` under `%TEMP%` and
  passes it as the override for the journal and lock folder. A helper asserts that every path a test writes is inside
  its root before writing. A test that cannot prove where it writes does not run.
- **Product code renames, moves, creates and deletes no real file in B21.** The only "disk" the fake action changes is
  the fake filesystem's JSON file inside the sandbox. B22 is the first build with a real file step.
- **No test creates or touches `%LOCALAPPDATA%\FileOrganizer\`.** A suite-level check records whether that folder
  exists, and its file listing, before the run, and asserts both are unchanged after.
- Never touch OneDrive, the real corpus, the owner's project databases, or any path outside the sandbox. Migration
  017 is exercised on sandbox databases only. If Thomas offers a *copy* of a real project database for a real-scale
  migration check, he puts it in the sandbox himself, and its path never goes into the repository.
- `unknown` blocks. Never weaken an existing Phase 1-3 test or guard. A Phase 3 test that pins
  `APP_SCHEMA_VERSION == 16` may be updated to 17 only if pinning the version is its whole purpose; list each such
  change as a judgment call.
- Patch by writing a complete new file and replacing the old one. The PowerShell and Bash traps in `AGENTS.md` apply.

## 5. Suites
At the end, through `fo-runner`: `Scripts\run_suites.ps1 -Tier standard`, which now includes the three new suites.
Minimum: every base suite at or above its base count from §1.6; the three new suites exit 0; `p4_mutation_check`
kills every mutant. Report the table, not logs. Also report that `p3_mutation_check`'s 69 mutants are still killed.

## 6. Handback format
A short version first. Then:
- §1: each expectation, with what you found.
- An item-by-item table against §3, with the evidence in the same row (command, count, file).
- Unit 1's probe results with the measured numbers, and the not-run list with Thomas's steps.
- The reconciliation row list, with how each row was reached (kill or constructed).
- Judgment calls.
- Receipts: the suite table, the manifest result, the versions, the schema.
- Unverified items, said plainly.
- Helpers used, with their models.
- Any mistake made and how it was repaired.

## 7. Out of scope
| Not in B21 | Owner |
|---|---|
| Any real file operation; the handle-bound rename; `FileIdInfo` identity via `ctypes`; Cloud Files sync-root detection; `PHCM_EXPOSE_PLACEHOLDERS`; the Windows name-comparison fixture | B22 |
| Quarantine, its registered exclusion roots, the scan exclusion | B23 |
| Real projectors (`p4_path_succession`, `file_state` epochs, executor observations); derived fulfilment; job assembly from a locked Phase 3 revision; wiring scans and Phase 3 plan edits to take the project lock | B24 |
| Screens, including the second-instance read-only banner, the OneDrive confirmation question, and sleep and shutdown handling | B25 |
| The metadata writer | B26 |
| Table B (cross-volume) and any cross-volume method | after v1 |

## 8. Questions that must stop the build
Stop, write the question with options and a recommendation, and wait, if:
1. L1 or L3 shows the OS does **not** release the byte lock after a kill, or releases it later than 10 s.
2. L4 shows a child process keeps the lock, and `O_NOINHERIT` (or the equivalent) does not fix it.
3. L8 ever shows two holders.
4. The catalog needs more than migration 017's one table, or migration 017 obsoletes a locked revision or stops it
   verifying.
5. A reconciliation row cannot be classified deterministically from (journal state, disk state, identity match), or
   two rows match one situation.
6. J5 shows a median cost above 50 ms per C1/C2/C3 triple under `FULL`. Report the numbers; do not change the
   pragmas.
7. Anything needs a write outside the sandbox, a change to Phase 1-3 behaviour beyond migration 017, or a design
   choice this handoff does not make.
