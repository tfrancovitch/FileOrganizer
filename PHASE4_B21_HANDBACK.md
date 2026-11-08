# Phase 4 B21 handback (COMPLETE: all 8 units; awaiting Architect review)

## Units 7-8 session (Sonnet 5.5, 2026-11-08): read this first

**Short version**
- **Unit 7 done.** `Scripts\p4_mutation_check.py`, by `p3_mutation_check`'s method: **23 mutants**, 15 test slices, a control run of every slice first,
  one text substitution each in a copy under `%TEMP%\fo_p4_b21_mut_*`. In the final standard-tier run **23 of 23 are killed**, controls 15/15, the real
  tree untouched (hashes of `Scripts\`, `Resources\` and `ARCHITECTURE.md` before and after), `%LOCALAPPDATA%\FileOrganizer` unchanged, no `fo_p4_b21_*`
  folder left. **The first full run killed 21 of 23.** Two survived (below); neither mutant was weakened.
- **Unit 8 done.** `APP_VERSION` = `Phase2.VERSION` = `B21`, `APP_SCHEMA_VERSION` = 17; `ARCHITECTURE.md` has "Phase 4 (B21): journal, locks, jobs" with the
  restore ceremony folded in; `CHANGELOG-B21.md` written; `run_suites.ps1` registers the three suites; `SOURCE_SHA256.csv` regenerated (249 rows, was 235) and
  `Test-Installation.ps1` prints "Installation check complete -- manifest verified."
- **Standard tier, through `fo-runner` (Haiku): table in section 6.** The Phase 1-3 suites and `b6_regression` are at their base counts; `p3_mutation_check`
  still kills **69/69**.
- **Two survivors in the first run, both dealt with in the open.** (1) **M17**, "C4 closes a job only when every event is PROJECTED": **no test owned the rule.** I added one
  check to `p4_journal_check` 5b (a job whose P never ran must not close). (2) **M21**, "the manifest is compared at resume": the test already existed (in
  `test_pause_lock_commit_failure`); **my slice list for that mutant was wrong** (it named `faults`), so the run could not see it. I corrected the list.
- **A hang is a kill.** M05 (the project lock taken blocking, `msvcrt.LK_LOCK`) made the lock test's fake-clock retry loop run for many minutes (each real attempt blocks
  ~10 s). The harness now has a 120 s watchdog in the slice process: it reports the checks that had already failed (the real-time "non-blocking" check fails first) and stops.
- **Migration test fixed.** Bumping to 17 broke five checks in `test_migration_017` (the "B20-schema" fixture was now built at 17). The fixture is now built under
  a `schema16()` patch, as B20 built it (see J-30).
- **Not run, said plainly:** L2, L5, L6, L7, J2, J3 (need a person or a VM), L9, L10 (never, by instruction). Nothing committed, tagged or pushed.

**Unit 7 mutant table** (final run; `Logs\p4_mutation_report.json` holds it, `Logs\` is not tracked)

| # | Rule broken (one substitution) | File | Killed by |
|---|---|---|---|
| M01 | C1 and C2 merged into one commit (after the step) | jobs.py | `happy` (500-item run's commit order) |
| M02 | the result committed before the step ran | jobs.py | `happy` |
| M03 | recovery executes the A1 "resume" by itself (injected: renames on the fake disk) | reconcile.py | `gen6c` (A1's scenario: "recovery changed nothing") |
| M04 | a writing connection at `synchronous=NORMAL` | journal.py | `store` |
| M05 | the project lock taken blocking | locks.py | `plock` (non-blocking check; then the watchdog) |
| M06 | the project lock not taken at all | locks.py | `plock` |
| M07 | the fencing check removed | journal.py | `store` |
| M08 | a projection without its `action_id` key (fake projector) | p4_fakes.py | `proj5a` |
| M09 | the unique projection index on `action_id` dropped | journal.py | `store` |
| M10 | the breaker's three-consecutive rule disabled | jobs.py | `faults` |
| M11 | the breaker's max(3, ceil(10 %)) rule disabled | jobs.py | `faults` |
| M12 | a `JOB_GLOBAL` class classed `ITEM_LOCAL` | jobs.py | `faults` |
| M13 | a blank journal created when the binding exists | journal.py | `opening` |
| M14 | the revision check skipped at resume | jobs.py | `faults` |
| M15 | table row A3 deleted | reconcile.py | `tabdata` |
| M16 | table row E3 deleted | reconcile.py | `tabdata` |
| M17 | C4 ignores events still PENDING | jobs.py | `proj5b` (**new check**) |
| M18 | a table E "ask" no longer stops the replay | projection.py | `proj5c` |
| M19 | the PROJECTED mark made before the projection | projection.py | `proj5b` |
| M20 | a journal with jobs and no binding is bound by guessing | journal.py | `opening` |
| M21 | the manifest not compared at resume | jobs.py | `pause` (existing check; slice list corrected) |
| M22 | orphan RUNNING/PAUSED jobs never found at start | jobs.py | `pause` |
| M23 | nested root locks refused one way only | locks.py | `rlocks` |

The handoff's twelve (§3 row 7) map to M01 (C1/C2 merged), M02 (result before the step), M03 (recovery acts), M04 (`NORMAL`), M05 and M06 (lock blocking, lock not taken), M07 (fencing), M08 (projection without its key), M10 and M11 (breaker), M12 (`JOB_GLOBAL` as `ITEM_LOCAL`), M13 (blank journal), M14 (revision check at resume) and M15 (a row deleted): all killed.
The "killed by" column is the first slice that failed; the full run stops a mutant at its first kill, as `p3_mutation_check` does.

**Unit 8 judgment calls**

| # | Call | Why / how to change |
|---|---|---|
| J-28 | `p3_encryption_check.py:327` read `user_version == 16` inside a check that also asserts the 016 column and the `app_meta` row. **Pinning the version is not its whole purpose**, so I did not move it to 17: it now reads `>= 16` ("schema 16 or later"), and the label says so. | Handoff: change to 17 only if pinning is its whole purpose. `>= 16` is weaker than `== 16` only in allowing later schemas, which is what the check's section ("Schema 16: encryption_state") is about. To pin it, change `>= 16` to `== 17` |
| J-29 | Other pins of the current version or schema, found by grep, **were their whole purpose** and moved to B21 / 17: `p3_regression` (fresh project on schema 17; "one product version" `B21`, schema 17 everywhere; a schema-8 project migrates to 17; migrations 9-16 stamped `B21`; the operation's `agent_version`), `p3_fingerprint_check` (migrated to 17), `p3_visual_check` (on schema 17) | Each is a "this build's version" check; each moves with every bump |
| J-30 | `test_migration_017` (units 1-4) now builds its B20-schema fixture inside `schema16()` (the constant set back to 16 in this process, restored afterwards). `schema17()` stays, now a no-op, because other tests wrap their catalogs in it | Without it the fixture was a schema-17 project and five checks failed in the first unit 8 run |
| J-31 | `self_check.REQUIRED_SCHEMA_VERSION` and `Phase2.core.REQUIRED_SCHEMA_VERSION` set to 17; `fo_analyzers.REQUIRED_SCHEMA_VERSION` left at 16. `self_check` says it is "kept equal to fo_db.APP_SCHEMA_VERSION" and its "Database creation" check compares for equality (it would fail at 16); `p3_regression` asserts the two equal the app schema. `fo_analyzers`' constant is the lowest schema with the column it writes (B17, B18 notes) and 017 adds nothing it needs | The handoff names three constants; these two follow from the same bump. **Not named in the handoff: please confirm** |
| J-32 | Manifest: 14 files added (`CHANGELOG-B21.md`, this file, migration 017, the seven `Scripts\Phase4\` files, `p4_fakes.py`, `p4_journal_check.py`, `p4_lock_check.py`, `p4_mutation_check.py`); `PROGRESS_B21.md` is **not** listed (a working note, like `STATUS.md`). Existing rows keep their order and are re-hashed; LF, UTF-8 with BOM, backslash paths, as before | AGENTS.md: tooling is not listed. If `PROGRESS_B21.md` should be, add one row |
| J-33 | Mutants M08 (a fake in `p4_fakes.py`) and M03 (an injected act) mutate test code and an injected line respectively: they prove the suite catches a non-idempotent projector and a recovery that acts. They are not changes to what the product does | Handoff: "the mutant must inject the act" for recovery |
| J-34 | A mutated slice can break a test's own cleanup, so the harness removes any `fo_p4_b21_*` folder a slice leaves and **reports it** (5 in the final run: M05, M06, M07, M20, M23; the run then ends with 0). The leak happens only in the mutated copy's tests, never in an unmutated run | Honest count, not a hidden clean-up; the run fails if any folder survives it |

## Units 5-6 session (Opus 5.5, 2026-11-08): read this first

**Short version**
- **The Architect's three fixes, made before unit 5.** (1) `PROGRESS_B21.md`: a correction line says the unit 2-4
  times were the model's estimates; the files' own write times are the evidence (jobs.py 19:31, PROGRESS 19:35).
  The old lines are left as they were. Every new line takes its time from `date`. (2) The sandbox leak is reproduced
  and fixed. The migration-017 test opened five connections it never closed. They are now closed in `finally`.
  `Sandbox.register()` closes a test's databases before its folder is removed, even after an exception. Both suites
  now end with "no `fo_p4_b21_*` folder created during this run survives it". The earlier claim of no leftovers is
  corrected below (§6, §9). (3) The breaker follows the J-8 ruling: `jobs.breaker_threshold(n)` = max(3, ceil(10 % of
  n)). The test that pinned the literal reading is replaced. J-8 is resolved.
- **Unit 5, the projection: done.** `Scripts\Phase4\projection.py` holds the registry. `PRODUCT_PROJECTORS` is empty;
  the fake projector is in `p4_fakes.py`. P runs after C3: the catalog write is its own commit, then the PROJECTED
  mark is its own fenced journal commit. C4 waits for every projection. Every open (`open_session`) replays by
  table E. If any table E row says "ask", nothing is replayed. Real kills between C3 and P, and between P and the
  mark, converge on the next open with exactly one row per action. A restored C0 catalog is repaired (E3). A catalog
  row the journal never recorded stops everything (`ProjectionAsk`, JOB_GLOBAL).
- **Unit 6, reconciliation: done.** `Scripts\Phase4\reconcile.py` holds tables A, C, D, E and F as data: **36 rows**
  (A 13, C 5, D 7, E 6, F 5, each table with one "X" row for "matches no expected shape: ask"). The full key space
  of every table was enumerated (376 keys): no key is covered by two rows, and every row is reachable.
  **40 generated scenarios cover every row.** 17 reach their row by a real kill alone, 9 by a real kill followed by
  an outside change to the fake disk, and 14 by construction. **26 of the 36 rows are reached by a real kill.**
  Kills use 14 child processes, each ended with TerminateProcess at a named hook. Recovery reads through a
  read-only journal connection, and every scenario proves the disk, journal and catalog are unchanged. The restore
  ceremony is in `ARCHITECTURE.md`.
- **Suites, run through `fo-runner` (Haiku):** `p4_journal_check` **290/290** (J5 included: FULL median 9.79 ms per triple, p95 13.09 ms); `p4_lock_check`
  **76/76**; `run_suites.ps1 -Tier quick` **20 of 20 exited 0, 1,585 checks** (equal to the floor). No sandbox
  survived any of them, and `%LOCALAPPDATA%\FileOrganizer` was unchanged.
- **Questions for the Architect: Q-1 to Q-6** (in the judgment-call table below). None blocks the build. Every key
  maps to exactly one row. **Q-1 is shaped like stop condition 8.5:** the handoff's F rows "folder absent → not
  applied" and "parent missing → ask" both match when the parent is gone. I read the second as an exception (ask
  wins) and flagged it rather than stopping. **Q-2:** R4.3's tables do not cover every situation. The gaps fall to
  the X rows ("ask"), by R4.3's own rule.
- **Stopped after unit 6, as briefed.** Units 7 (mutation) and 8 (versions, docs, manifest, suite registration) are
  not started. Nothing committed, tagged or pushed. `Test-Installation.ps1` will **not** verify until unit 8
  regenerates the manifest, because `ARCHITECTURE.md` (listed) now carries the restore ceremony.

**For units 7 and 8 (the next Sonnet session)**
- Unit 7 mutants already have tests that should kill them. "A table row deleted from reconcile.py" fails 6c (every
  row needs a test, and the row's own scenario then reaches the X row). "Recovery executing an A1 resume by itself"
  fails 6c and 6d (the fingerprints are unchanged, the journal connection is read-only, and a static check finds
  no write or acting call in reconcile.py). "A projection without its action_id key" fails 5a (idempotency) and
  5c (one row per action). Run them anyway; a survivor adds a test.
- Unit 8: fold the "Phase 4 (B21): the restore ceremony" section of `ARCHITECTURE.md` into the B21 section it
  writes. Regenerate the manifest; the new files are `projection.py` and `reconcile.py`, and `jobs.py`, `locks.py`
  and `ARCHITECTURE.md` changed. J-2's `p3_encryption_check.py:327` pin still applies.
- `JobRunner(..., projectors=)` is now `projection=` (a `projection.Projection`).

## Where the units 1-4 session stopped (2026-11-07; the section above supersedes it where they differ)

- **Session scope:** handoff section 1 (expectations) and **units 1-4**, then stop. Units 5-8 are **not started**.
  Next is **unit 5 (projection) on Opus**, in this worktree, resuming from `PROGRESS_B21.md`; then unit 6 (Opus),
  7, 8.
- **Done:** section 1 checked (all eight hold; see §1). Unit 1 probes run. Unit 2 (paths, journal store,
  migration 017). Unit 3 (project lock, root-level lock). Unit 4 (state machine, runner, fakes).
- **Suites written by this session:** `Scripts\p4_lock_check.py` **75/75**, `Scripts\p4_journal_check.py`
  **170/170** (the full run, J5 included, ~2.5 min). Not yet registered in
  `Scripts\run_suites.ps1` (unit 8).
- **Interruption:** the computer was switched off during unit 1 while the J5 timing run was going. Nothing
  was committed; the half-written sandbox database of that run was my own and was deleted; J5 was **re-run from a
  fresh sandbox** and its numbers are the ones below.
- **Nothing half-finished.** Not done and not attempted: reconciliation tables (unit 6), the real projection
  contract (unit 5), mutation suite (7), version bump / ARCHITECTURE.md / CHANGELOG-B21.md / manifest / suite
  registration (8). Because `APP_SCHEMA_VERSION` is still 16, migration 017 exists in the tree but the product
  does not apply it yet; the unit 2 tests patch the constant to 17 in-process.
- **One open question for the Architect** (**resolved 2026-11-07**: max(3, ceil(10 %)); built in the units 5-6 session) (not a stop condition; I implemented the literal reading): "10 % of the
  job's items refused in one class" pauses a job of fewer than 10 items on its **first** item-local refusal (1 of 8
  is 12 %). See judgment call J-8. Recommended: add a minimum count (for example 3). `BREAKER_PERCENT` in
  `jobs.py` is the one constant to change.
- **Stop conditions (§8):** none triggered. (1) L1/L3 release in milliseconds, not seconds. (2) L4: no child kept
  the lock. (3) L8: zero two-holder observations. (4) migration 017 is one table and obsoletes nothing. (5) not
  reached (unit 6). (6) J5 median 11.1 ms, under 50 ms. (7) nothing needed a write outside the sandbox.

Receipts for this part are listed per unit below. Helpers used: `fo-runner` (Haiku) twice, `fo-scout` (Haiku) once.

## 1. Expectations (handoff §1)

| # | Expectation | Found |
|---|---|---|
| 1 | `git log -1` is the filing commit; tree clean | `10fddd3` ("... B21 handoff filed") on `build/b21`; `git status` clean at start. Now untracked: `PROGRESS_B21.md`, this file, `Scripts\Phase4\`, `Scripts\p4_*.py`, migration 017. **Holds.** |
| 2 | `APP_VERSION = "B20"`, `APP_SCHEMA_VERSION = 16`; Phase2 `VERSION = "B20"`; migrations 001-016 contiguous | `fo_db.py:155` B20, `fo_db.py:197` 16, `Phase2\__init__.py:7` B20; `Scripts\Database\migrations` holds `001`-`016` with no gap. **Holds.** |
| 3 | `project.project_uid TEXT NOT NULL UNIQUE`, one row (`project_id = 1`) | `001_initial.sql:76-78`: `project_id ... CHECK (project_id = 1)`, `project_uid TEXT NOT NULL UNIQUE`; the row is inserted by `init_project` (`fo_db.py:544`). Verified on sandbox projects (the unit 2 B20 fixture has `project_id = 1`); **no owner project database was opened.** **Holds.** |
| 4 | `backup_database` uses SQLite's backup API | `fo_db.py:377-399`: `source.backup(dest)` at 390-396. **Holds.** |
| 5 | Manifest lists 235 files; `Test-Installation.ps1` prints "manifest verified" | `SOURCE_SHA256.csv` has 236 lines = header + 235 files; `Test-Installation.ps1` ended "Installation check complete -- manifest verified." **Holds.** |
| 6 | Base suites before any change | `run_suites.ps1 -Tier standard` (it contains the whole quick tier, so I ran standard once instead of quick then standard) by `fo-runner`: **23 of 23 exit 0**. Quick-tier suites (20): the 18 that print N/N sum to **1,585 checks** (p3_regression 348, p3_fixture_check 101, p3_lab_harness 10, p3_plan_check 81, p3_closeout_check 62, p3_encryption_check 29, p3_revalidation_check 59, p3_metadata_check 133, p3_metadata_view_check 54, p3_refresh_check 17, p3_survey_check 85, p3_visual_check 138, p3_visual_bulk_check 79, p3_relabel_check 117, p3_dangling_check 67, p3_registry_schema_check 33, p3_fingerprint_check 34, p3_organize_check 138); self_check and p2_regression report pass lines without counts. `p3_mutation_check`: **69/69 mutants killed**, controls 29/29, real tree untouched (99/99 checks). `b6_regression`: **73 PASS, 0 FAIL**. `p2_dashboard_check`: all passed. **Equals the STATUS.md floor (1,585).** Caveats: the logs are in this session's scratchpad (I passed `-LogDir`), not under `%TEMP%\fo_suites`; and because of the power-off I did **not** re-run the base after restart. Instead the quick tier was re-run through `fo-runner` after unit 2 (with migration 017 present): **20 of 20 exit 0, 1,585 checks again.** The tracked tree was unchanged at restart. |
| 7 | Interpreter | `python --version` = **Python 3.14.6**; `sys.executable` = `%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe`. `TheFileOrganizer.bat` selection logic: it uses whichever `python` is first on PATH if that is 3.11 or newer (else it downloads 3.12.7 per-user), then launches `pythonw` from PATH (falling back to `python`); it never names a path. `where python` from my Bash tool listed `...\WindowsApps\python.exe` first and `...\Local\Python\bin\python.exe` second, so **which one a double-click gets is unverified** (PATH order differs by shell and by user). |
| 8 | No `Scripts\Phase4\` and no `p4_*` file | Confirmed absent before I started (both listings errored). **Holds.** |

## 2. Item-by-item against §3 (units 1-6, and the Architect's fixes F1-F3)

| # | Unit | Status | Evidence |
|---|---|---|---|
| 1 | Probes first | **Done**, with the not-run list below | `Scripts\p4_lock_check.py` section 1: L1, L3 (two ways), L4 (+ control), L8, L11, L12 each have a recorded result and assertions; J5 in `Scripts\p4_journal_check.py` (reported; asserted only against the 50 ms stop condition). Numbers below. |
| 2 | Paths and the journal store | **Done** | `Scripts\Phase4\paths.py`, `journal.py`; migration `Scripts\Database\migrations\017_phase4_journal_binding.sql`. `p4_journal_check.py` sections 2a-2d (83 checks): two writing connections report `synchronous=2` / `wal`; every opening rule has a test (create, JournalMissing without creating a file, completed binding, JournalBindingAsk, project/machine/journal-uid/binding mismatches); UNC, non-fixed drive, OneDrive (injected classifier, the three environment variables, a junction into OneDrive) are refused with no file created (J6); migration 017 on a scanned B20-schema project copy: applies as `[17]`, **exactly one new table, 52 other tables byte-identical (full row dump compare)**, one new `schema_migration` row; a plan revision locked under B20 **still verifies (fingerprint unchanged) and stays `locked` after the obsolescence detector runs**. |
| 3 | Locks | **Done** | `Scripts\Phase4\locks.py`; `p4_lock_check.py` sections 3a-3c (52 checks): second holder refused with the holder's information, answer is non-blocking; default retry window 7 s (checked 5-10 s on a fake clock); `holder_info`; two projects on the same root refused with holder info; nested roots refused both ways; a junction alias judged by where it points; sibling and unrelated roots allowed; all-or-none `acquire_all`; registry busy; unidentifiable root refused; five rounds in which a child holding the project lock, the journal owner row and a root lock is killed with `taskkill /F` and **all three are free (and the owner row replaced) within the window: median 4.6 ms, max 60.9 ms**. Fencing: after a second run takes the lock, the first run's next commit raises `FencingLost` and writes nothing. |
| 4 | Job and item state machine, runner | **Done** | `Scripts\Phase4\jobs.py`, fakes in `Scripts\p4_fakes.py`; `p4_journal_check.py` sections 4a-4f (87 checks). Tables walked over all 625 item-state pairs and 49 job-state pairs (allowed ones succeed, every other is refused and leaves the row untouched). **500-item fake job runs to COMPLETED** in 9 s: one `start` commit, then C1, C2, C3 per item in order, C4 last, 1,500 item events with seq 1-3. Item 1's order is `C1 committed -> step -> C2 -> verify -> C3`. **Unclassified failure on item 7: job PAUSED, item 7 FAILED_NOT_APPLIED, items 8-500 PLANNED with no preflight, step or event and files still in place.** Three consecutive BLOCKED_LOCKED trip the breaker (lock retried at 0.25 / 1 / 3 s, four attempts recorded). ITEM_LOCAL refusals continue; the 10 % rule; JOB_GLOBAL on item 1 (three classes, including `source_root_available`) stops the job with items 2-20 untouched. A failing revision check at resume blocks the resume (no item touched); a counting fake shows the revision check ran at C0 and once per resume attempt, **never from the job's own writes** (1 call over a 500-item run). C0 records the manifest hash, a `fo_db.backup_database` backup (valid catalog, in the sandbox), the OneDrive answer as given (or NULL), the revision reference. Paused job keeps the lock; exit leaves it INTERRUPTED and frees the lock; a RUNNING job found at start is marked INTERRUPTED. Injected `SQLITE_FULL` at C1: JOB_GLOBAL, **the step never ran**; at C2: step ran, no result event (the A2 shape), resume refused. Fencing inside a run: C3 refused after a takeover, run stopped. |

| F1 | Architect fix 1: progress times | **Done** | `PROGRESS_B21.md` gains the line "2026-11-08 10:59 CORRECTION (Opus session, units 5-6): the times on the UNIT 2, UNIT 3 and UNIT 4 lines above (19:50, 20:35, 21:10) are not real ...". The old lines are not rewritten. Write times read with `ls --time-style=full-iso`: `jobs.py` 2026-11-07 19:31:16, `PROGRESS_B21.md` 19:35:16. Every later line's time comes from `date` at that moment. |
| F2 | Architect fix 2: sandbox leak | **Done** | Reproduced first. A scratch runner of `test_migration_017` alone left one `fo_p4_b21_*` holding `AppRoot\Projects\B20Fixture\Database\FileOrganizer.db` (+ `-wal`, `-shm`) and `B20FixtureCopy\Database\FileOrganizer.db` (+ `-wal`, `-shm`). The cause was five `sqlite3.connect(...)` calls in that test that were never closed (`master_before`, `master_after`, the "still schema 16" check, and the two `open_model` connections). Fix: every connection the test opens goes into a list closed in `finally`. `p4_fakes.Sandbox.register(closer)` runs the closers before the folder is removed, and `make_catalog` and `Env` register theirs. `cleanup()` records any folder it could not remove. Both suites end with "no fo_p4_b21_* sandbox folder created during this run survives it". After the fix: 0 leftovers from the test alone, and 0 when a failure is injected half-way (after the 6th check, with the copy's connections open). Full runs: see receipts. |
| F3 | Architect fix 3: breaker ruling | **Done** | `jobs.py`: `BREAKER_PERCENT_MIN = 3`, `breaker_threshold(n) = max(3, ceil(10 % of n))`, and the pause tests `refused >= threshold`. `BLOCKED_DEPENDENCY` is still not counted. Tests: threshold table 1→3, 2→3, 8→3, 10→3, 29→3, 30→3, 31→4, 45→5, 100→10, 101→11, 500→50. In 8 items, 1 refusal and 2 non-consecutive refusals do not pause and the third does ("3 of 8", items 7-8 untouched). In 45 items, 4 STALE do not pause and 5 do (so the rule is ceil, not floor). "3 of 30 pauses" and "2 of 30 does not" still pass. The literal-reading test is removed (it pinned the behaviour the ruling replaced). |
| 5 | The projection | **Done** | `Scripts\Phase4\projection.py` (269 lines). **5a:** `PRODUCT_PROJECTORS == {}`. The fake projector applied twice leaves one identical row, keyed by `action_id`. A different event under the same `action_id` raises `ProjectionConflict` (JOB_GLOBAL) and changes nothing. An unhandled kind is "unknown". A non-autocommit catalog connection is refused, and so is a catalog outside a `fo_p4_b21_` sandbox. **5b:** a 5-item job ends COMPLETED with 5 PROJECTED events and exactly 5 rows. The order is `commit:C3, C3:after, P:before, P:after_apply, commit:P, P:after_mark`, and C4 is the last commit. A projector failure in a run pauses the job JOB_GLOBAL, leaves the event PENDING, and leaves items 2-3 PLANNED. **5c (real kills):** killed at `C3:after` of item 2, table E says E1; killed at `P:after_apply`, it says E2. In both, the next `open_session` leaves every event PROJECTED with exactly one row per action (2 of 2). The job was found RUNNING and marked INTERRUPTED. Resumed, it COMPLETES with 3 rows and no duplicate, and a second open finds only E5 and replays nothing. A restored C0 catalog is repaired: `{"E3": 3}`, and the same 3 rows are back. With an unknown catalog row, `open_session` raises `ProjectionAsk` (JOB_GLOBAL, E4): item 2's event is still PENDING, the catalog and job are unchanged, and the lock is free afterwards. |
| 6 | Reconciliation | **Done** | `Scripts\Phase4\reconcile.py` (494 lines). **6a:** tables are exactly A, C, D, E and F; ids are unique; one X row each; A1-A12 carry R4.3's recovered states and next actions; C1-C4 R4.3's; E1-E4 the handoff's outcomes (plus E5 and EX); F1-F4 the handoff's (plus FX). Every A/C/F recovered state is a journal item state, and D's three item summaries partition the 25 item states. **6b:** A 270 keys (41 covered by an explicit row, 229 fall to AX), C 40 (7/33), D 21 (14/7), E 15 (5/10), F 30 (6/24). No key is covered by two rows and every row is reachable. A deliberately overlapping row raises `AmbiguousRow`, and a key outside the space is refused. The 24 table-A keys a real disk can show that R4.3 does not cover are printed (Q-2). **6c:** 40 scenarios, every row with at least one; the row list is below. **6d:** recovery on a world killed after the step shows A2 "record" and does not write it. The journal connection refuses a write, the projector's `apply` is never called, and the report lists `[A2]` and `[D1]`. reconcile.py contains no transaction, apply, step, preflight, verify, state change, event, INSERT/UPDATE/DELETE, commit or save. **6e:** `ARCHITECTURE.md` has the restore ceremony with its four steps. |

| 7 | Mutation sensitivity | **Done** | `Scripts\p4_mutation_check.py` (23 mutants, 15 slices, controls first, sandbox copy under `%TEMP%\fo_p4_b21_mut_*`). Final run: controls 15/15, **mutants killed 23/23**, real tree untouched, `%LOCALAPPDATA%\FileOrganizer` unchanged, 0 folders left, "P4 MUTATION (B21): 41/41 checks passed" (table above). First run 21/23: M17 got a new check (`p4_journal_check` 5b), M21's slice list was corrected; no mutant weakened. `p3_mutation_check` still **69/69** (99/99 checks) |
| 8 | Versions, docs, manifest | **Done** | `fo_db.py` `APP_VERSION = "B21"`, `APP_SCHEMA_VERSION = 17`; `Phase2\__init__.py` `VERSION = "B21"`; `ARCHITECTURE.md` "Phase 4 (B21): journal, locks, jobs" (the restore ceremony folded in as its last subsection, not repeated); `CHANGELOG-B21.md`; `run_suites.ps1` (`p4_journal_check`, `p4_lock_check` in quick; `p4_mutation_check` in standard; `-Tier standard -List` shows them); `SOURCE_SHA256.csv` 249 rows; `Test-Installation.ps1`: "Installation check complete -- manifest verified." Pin changes: J-28 to J-31 |

## 3. Unit 1 probe results

Measured on this machine (Windows 11 10.0.26300, Python 3.14.6, sandbox under `%TEMP%` on C:).

| Probe | Result |
|---|---|
| L1 `taskkill /F` on the holder | lock free for a second process after **median 13.4 ms, max 21.3 ms** (10 runs, all released; busy while the holder lived) |
| L3 `os._exit(3)` | **median 2.8 ms, max 7.6 ms** (10 runs) |
| L3 access violation (a thread started at address 0, exit code 0xC0000005, error dialog suppressed) | **median 2.7 ms, max 3.9 ms** (5 runs) |
| **Measured release delay** | milliseconds in every run, so unit 3's 5-10 s retry window (7 s default) is generous by about two orders of magnitude |
| L4 a child outlives the killed holder | child alive after the kill, lock acquired by a second process at once; the child did **not** inherit the (O_NOINHERIT) handle. **Control:** with an inheritable handle the child demonstrably held a handle to the lock file (it wrote a byte through it) and **the lock was still released when the holder died**: NT byte-range locks belong to the process that took them. `O_NOINHERIT` stays as belt and braces. |
| L8 1,000 racing acquisitions from two processes | 2,000 acquisitions (1,000 each), **0 two-holder observations**, 323,483 refused attempts (the race was real), 2.1 s |
| L11 holder suspended (`NtSuspendProcess`) 2.5 s past a 0.2 s heartbeat | second process stays refused (OS lock still held); heartbeat reads 2.51 s stale; after resume it is fresh again and the lock is still held |
| L12 another process opens the lock file | reader reads the owner bytes, byte 0 itself is denied to it; lock unaffected while the reader holds read/write handles and after it closes |
| J5 10,000 C1/C2/C3 triples, WAL, sandbox journal on C: | **FULL: median 10.14 ms per triple (3.33 ms per commit), p95 12.70 ms, max 58 ms; NORMAL: median 0.15 ms, p95 0.38 ms** (the final full run). The first fresh-sandbox run after the restart read FULL median 11.10 ms, p95 20.79 ms, max 858 ms; NORMAL median 0.29 ms. Stop condition 6 (median above 50 ms) not met. Pragmas not changed. The triple uses an ad-hoc schema with row sizes like the real events, not the real journal schema. |

**Not run (need a person, a second Windows account or a VM), Thomas's steps:**

- **L2** (Task Manager End task / End process tree): start the holder from a test script that prints its PID and
  holds the lock; in Task Manager choose *End task*, and again with *End process tree*; run `locks.holder_info` and
  a second `ProjectLock.acquire` from another console and record the delay. Pass: acquired within the retry window.
- **L5** (hibernate and resume): hold the lock in one console, confirm a second process is refused, hibernate,
  resume, confirm the second process is still refused and the first still holds. Pass: no bypass either side.
- **L6** (fast user switching, two Windows users): hold the lock as user A, switch to user B (no sign-out), run the
  second-instance `holder_info` from B. Pass: B sees "busy" and A's owner information, and cannot acquire.
- **L7** (two users, different ACLs on the lock folder): create the lock folder as A, give B modify rights with
  `icacls`, lock as A, read and try to lock as B and the reverse. Record the ACL that lets both lock and read.
- **J2** (hard reset of a VM during 10,000 journalled operations under `FULL`) and **J3** (the same under
  `NORMAL`): in a VM with a throwaway sandbox, run a loop of C1/C2/C3 triples against a fake disk, hard power-off
  mid-loop, reopen. J2 pass: the journal opens cleanly and no committed C1 is missing; record how often a result is
  missing on disk (row A8). J3 is expected to lose a tail; it shows the difference.
- **L9, L10** (OneDrive, SMB): **never run, by instruction.** Unit 2 tests the refusal with an injected classifier.
- J1 is covered by unit 6's generated tests; J4 is cross-volume (out of scope); J6 and J7 are covered by units 2 and 5.

## 4. Reconciliation row list (units 5-6 session)

How a row is reached:
- **kill**: a child process (`p4_journal_check.py --kill-worker`) builds the world through the product's open path,
  runs the job, prints `AT <hook>` at the named hook, and is killed there with TerminateProcess (`Popen.kill`). Exit
  code 1 in every case.
- **kill+edit**: the same, then the test changes the fake disk as an outside actor would. The edit is named in the
  row.
- **constructed**: the journal and fake-disk state are built directly, because no B21 action can reach the row.

Each test works on a fresh copy of the killed world, including the journal's `-wal` and `-shm` as the kill left
them. It classifies through a read-only journal connection and checks that the disk, journal and catalog are
unchanged afterwards.

| Row | Recovered state | Next action | How reached | Detail |
|---|---|---|---|---|
| A1 | INTERRUPTED_NOT_APPLIED | finish | kill | kill at C1:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A2 | APPLIED_UNRECORDED | record | kill | kill at step:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A3 | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at C1:after of item 2 (TerminateProcess; exit code 1); then in/f0002.dat removed from the fake disk (PASS) |
| A4 | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at C1:after of item 2 (TerminateProcess; exit code 1); then a hard link to the source made at out/f0002.dat (same object) (PASS) |
| A5 | BLOCKED_COLLISION | ask | kill+edit | kill at C1:after of item 2 (TerminateProcess; exit code 1); then a different file put at out/f0002.dat (PASS) |
| A6 | PRECONDITION_DRIFT | ask | kill+edit | kill at C1:after of item 2 (TerminateProcess; exit code 1); then in/f0002.dat changed (size + 1) (PASS) |
| A7 | APPLIED_UNVERIFIED | record | kill | kill at C2:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A8 | RESULT_NOT_ON_DISK | finish | kill | kill at C2:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A9 | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at C2:after of item 2 (TerminateProcess; exit code 1); then out/f0002.dat removed from the fake disk (PASS) |
| A10 | FAILED_NOT_APPLIED | none | kill | kill at C2:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A11 | APPLIED_VERIFIED | none | kill | kill at C3:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| A12 | CHANGED_AFTER_FINAL | none | kill+edit | kill at C3:after of item 2 (TerminateProcess; exit code 1); then the file moved back from out/f0002.dat to in/f0002.dat (PASS) |
| AX | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at C1:after of item 2 (TerminateProcess; exit code 1); then in/f0002.dat removed from the fake disk; a different file put at out/f0002.dat (PASS) |
| AX | UNKNOWN_AFTER_INTERRUPT | ask | constructed | constructed: a case-only rename: source and destination are one path (PASS) |
| AX | UNKNOWN_AFTER_INTERRUPT | ask | constructed | constructed: a C2 without a C1 (the journal contradicts itself) (PASS) |
| C1 | INTERRUPTED_NOT_APPLIED | finish | constructed | constructed: INTENT; the metadata equals the before-state (PASS) |
| C2 | APPLIED_UNRECORDED | record | constructed | constructed: INTENT; the metadata equals the target (PASS) |
| C3 | UNKNOWN_AFTER_INTERRUPT | ask | constructed | constructed: INTENT; neither (PASS) |
| C4 | NO_UNDO_RECORD | ask | constructed | constructed: INTENT without a durable before-state (bypassing the refusals) (PASS) |
| CX | UNKNOWN_AFTER_INTERRUPT | ask | constructed | constructed: the before-state equals the target: C1 and C2 cannot be told apart (PASS) |
| CX | UNKNOWN_AFTER_INTERRUPT | ask | constructed | constructed: C2 recorded: table C has rows for INTENT only (PASS) |
| D1 | INTERRUPTED | ask | kill | kill at C1:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| D2 | INTERRUPTED | finish | kill | kill at C3:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| D3 | INTERRUPTED | finish | kill | kill at C4:before (TerminateProcess; exit code 1) (PASS) |
| D4 | APPROVED | finish | kill | kill at C0:after (TerminateProcess; exit code 1) (PASS) |
| D5 | (as recorded) | none | kill | kill at C4:after (TerminateProcess; exit code 1) (PASS) |
| D6 | ABANDONED | none | constructed | constructed: an approved job abandoned (APPROVED -> ABANDONED) (PASS) |
| DX | (as recorded) | ask | constructed | constructed: a COMPLETED job with an item set back to INTENT by raw SQL (PASS) |
| E1 | PENDING | replay | kill | kill at C3:after of item 2 (TerminateProcess; exit code 1) (PASS) |
| E2 | PENDING | record | kill | kill at P:after_apply of item 2 (TerminateProcess; exit code 1) (PASS) |
| E3 | PROJECTED | replay | constructed | constructed: a completed job, then the C0 catalog backup restored over the catalog (PASS) |
| E4 | (none) | ask | constructed | constructed: a catalog row inserted for an action the journal never recorded (PASS) |
| E5 | PROJECTED | none | kill | kill at P:after_mark of item 2 (TerminateProcess; exit code 1) (PASS) |
| EX | (as recorded) | ask | constructed | constructed: a catalog row edited so it no longer matches its event (PASS) |
| EX | (as recorded) | ask | constructed | constructed: no projector registered that can read the row (PASS) |
| F1 | INTERRUPTED_NOT_APPLIED | finish | kill | kill at C1:after of item 1 (TerminateProcess; exit code 1) (PASS) |
| F2 | APPLIED_UNRECORDED | record | kill | kill at step:after of item 1 (TerminateProcess; exit code 1) (PASS) |
| F3 | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at step:after of item 1 (TerminateProcess; exit code 1); then a different file put at base/newdir/x.dat (PASS) |
| F4 | UNKNOWN_AFTER_INTERRUPT | ask | kill+edit | kill at C1:after of item 1 (TerminateProcess; exit code 1); then folder base removed (PASS) |
| FX | UNKNOWN_AFTER_INTERRUPT | ask | kill | kill at C2:after of item 1 (TerminateProcess; exit code 1) (PASS) |

**Counts:** 36 rows. 40 scenarios: 17 kill, 9 kill+edit, 14 constructed. 26 rows are reached by a kill (alone or
with an edit), and 10 only by construction: AX's two extra scenarios, C1-C4, CX, D6, DX, E3, E4 and EX. Why those
ten cannot be reached by a kill: B21 has no metadata action (C rows); "abandon" is not built (D6); DX and EX need a
contradicting journal or catalog; E3 needs a catalog restored from backup; E4 needs a row nobody's journal wrote.
14 kill workers ran, each killed at its hook (the last section of `p4_journal_check` lists them).

**Gaps that fall to the X rows (Q-2):** table A has 40 key combinations a real disk can show, 24 of which no R4.3 row
covers. They are AX ("ask"):

- INTENT with the source gone and a different file at the destination.
- RESULT_SUCCESS with any shape but A7, A8 or A9.
- RESULT_FAILURE with any shape but A10.
- VERIFIED with both present, both absent, or the destination changed.

Table C has rows only for INTENT, and so does table F. A create_folder killed after C2 is therefore FX ("ask"),
where a rename killed at the same point is A7 ("record").


## 5. Judgment calls (J-1 to J-13: units 1-4; J-14 to J-27: units 5-6)

| # | Call | Why / how to change |
|---|---|---|
| J-1 | Unit 1 probes use a bare byte lock written in `p4_lock_check.py`; `locks.py` is tested separately in unit 3, including the cross-process kill | `locks.py` did not exist in unit 1 |
| J-2 | Unit 2 tests call `fo_db.APP_SCHEMA_VERSION = 17` in-process (restored afterwards) to apply migration 017; the real bump is unit 8. Migration 017 adds **no** `app_meta` row, to keep "every other table byte-identical" literal | Unit 8 must also update `p3_encryption_check.py:327` (pins `user_version == 16`), the only pin I found (`b6_regression` reads the constant). List it there as a judgment call. |
| J-3 | Journal schema: `action_id` is **per attempt at an item** (a retry gets a new one), shared by that attempt's events; at most one event per action carries a projection (partial unique index); `UNIQUE(item_id, seq)` plus a partial unique index for job-level events; item and job states, kinds, projection states and refusal scopes are `CHECK` constraints (a new state in a later build needs a journal schema bump); triggers freeze a job's manifest hash / revision / authorising record and an item's kind / params / plan ref, make events append-only (only `projection_state` PENDING -> PROJECTED may move) and forbid deleting events | The handoff lists the minimum durable facts; the rest is my design. Unit 5 (Opus) should read this before building on it. |
| J-4 | `paths.root_lock_folder()` is **shared by all projects** (`...\journals\_root_locks`), not keyed by project uid, because the root lock exists to exclude other projects | Handoff says "in the lock folder"; a per-project folder could not exclude another project |
| J-5 | One project's own nested roots do not exclude each other (the project lock already serialises it); only another project's held lock does | Avoids a spurious self-conflict |
| J-6 | Item transition table (`jobs.ITEM_TRANSITIONS`) is my derivation from synthesis 8.3 R4.7 and R4.3 2.5 (states are exactly the synthesis ones, plus `BLOCKED_LOCKED`, `BLOCKED_CLOUD_ONLY`, `BLOCKED_ACCESS`). Edges out of refusals / failures / recovery states go only to PLANNED or SKIPPED (the person's choices; a retry is a new step) or to the documented recovery edges. Unit 6 may add recovery edges as data with tests | The synthesis names states, not edges |
| J-7 | A transient lock refusal is retried **inside the single C1..C2 window** (after 0.25 / 1 / 3 s, with the live preflight re-checked before each retry), because a 'locked' answer proves nothing was mutated; all attempts are listed in the C2 event. The alternative (a new intent per retry) is heavier and I did not choose it | Handoff: "retried at 250 ms, 1 s and 3 s" |
| J-8 | **The 10 % rule is implemented literally** (`refused_in_class * 100 >= 10 * job_items`). For a job under 10 items a single item-local refusal pauses it. `BLOCKED_DEPENDENCY` items are **not** counted toward the breaker (they are a consequence, not a refusal of their own) | **Resolved 2026-11-07** by the Architect (`Docs\DECISIONS.md`): max(3, ceil(10 %)); built and tested in the units 5-6 session (item F3); the pinning test replaced |
| J-9 | An exception raised by an action's step is an *unclassified native failure*: it is recorded in C2 and the verification decides (disk unchanged -> FAILED_NOT_APPLIED; anything else -> UNKNOWN_AFTER_INTERRUPT); either way the job pauses | R4.7: "anything else pauses" |
| J-10 | `resume` refuses to run past an item in INTENT, APPLIED or a recovery state ("needs the person's decision"); the person's choices (retry, skip, accept as unknown) are **not built**; they belong to unit 6's journalled recovery actions and B25's screens | Axiom 17 |
| J-11 | A journal failure inside a run (commit failure, lost fencing token) tries to record a pause, then raises `JobStopped(JOB_GLOBAL)`. After a takeover the old run cannot record its pause, so the job stays RUNNING in the journal until the new owner's start marks it INTERRUPTED (`mark_orphans_interrupted`) | |
| J-12 | `ProjectLock` writes the owner heartbeat only when the caller calls `heartbeat()`; there is no background thread | Keeps the lock free of threads; a caller (B25's run loop) schedules it |
| J-13 | Job runner takes `backup_catalog` as an injected callable (tests pass `fo_db.backup_database`); product wiring is B24's | `jobs.py` stays free of `fo_db` |
| J-14 | The fake projector lives in `p4_fakes.py` (test-only), not in the package. `projection.PRODUCT_PROJECTORS` is empty | Handoff §3: test-only code never goes in the package; its table is "sandbox-only" |
| J-15 | A projector is an object with `apply`, `observe` (absent / match / differ) and `action_ids`, not a bare function. Each owns its catalog transaction; the catalog connection must be autocommit | Table E needs to see the catalog (E2, E3, E4), so a write-only function was not enough |
| J-16 | `projection.open_session` is "the open": validate the folder, then take the project lock **before** the opening rules run, so creating the journal and writing the binding happen under the lock. Validating creates the folder and the lock file, never a journal; on `JournalMissing` an empty folder and a lock file are left. Then the owner row, table E, then orphans marked INTERRUPTED. Table E is classified before anything is written, so an "ask" leaves only the owner row changed | The handoff says "on every journal open" without naming the function. Adds `ProjectLock.owner_block()` (public) to unit 3's `locks.py` |
| J-17 | Table E's replay and record are the one reconciliation action the product takes by itself, in `open_session`. reconcile.py itself never acts | Unit 5 and G1 require start-up replay; unit 6 says recovery acts on no row. Reconciled as: catalog-only replay derived from the journal; no file action anywhere |
| J-18 | Table E additions: **E5** (PROJECTED and the row matches: none) and **EX** (row differs, or no projector can read it: ask, everything stops). **E4** is read as "a catalog row with no projecting event for its action_id", which covers both an action the journal never heard of and one it knows only through events that project nothing | **Q-3.** Needed so that every key has a row |
| J-19 | **Table D rows are my derivation** from R4.3's D paragraph (the handoff names D but lists no rows). D1: open job, an item needs a decision: stays INTERRUPTED, ask. D2: runnable: offer resume. D3: all terminal: offer close (C4). D4: APPROVED, never started: offer start or abandon. D5: closed: none. D6: ABANDONED: none. DX: contradictions: ask. "Open" means RUNNING, PAUSED or INTERRUPTED; `mark_orphans_interrupted` (unit 4) writes the INTERRUPTED | **Q-4** |
| J-20 | **The X rows.** A key no explicit row covers goes to the table's X row, always "ask", recovered state UNKNOWN_AFTER_INTERRUPT. R4.3 2.5: "UNKNOWN_AFTER_INTERRUPT is the explicit state for rows where the disk matches none of the expected shapes" | **Q-2.** Without it R4.3's tables are not total |
| J-21 | **Table F precedence.** F1 is "INTENT, folder absent, **parent present**". With the parent missing, F4 (ask) applies, never F1 | **Q-1, the §8.5 shape.** The handoff's F1 ("folder absent") and "parent missing" both match a missing parent. I read "..., or parent missing → ask" as an exception. Option (b): F1 regardless of the parent; the resume's preflight would then refuse with CONFLICT. Recommendation: (a), as built |
| J-22 | **What "journal state" means.** It is derived from the events of the item's current action: C1 only → INTENT; C2 ok → RESULT_SUCCESS; C2 not ok → RESULT_FAILURE; C3 APPLIED_VERIFIED after a C2 ok → VERIFIED. A C3 recording a mismatch does not upgrade the state, so the runner's own RESULT_NOT_ON_DISK reads as A8 again. Anything malformed → INCONSISTENT → X row | R4.3: "the highest state recorded" |
| J-23 | **Identity** for table A is every field of the C1 snapshot's `identity` except the display `path`. "Same object" (A4 vs A5) is the observer's answer: same file id. Table A's key is (journal state, distinct or same path, source absent/match/differ, destination absent/match/differ, same object yes/no/n-a) | B22 replaces the fake identity with FileIdInfo; the rule stays here |
| J-24 | **case_only** cannot be decided by table A, because source and destination are one path. It is always AX (ask); B22 needs its own table. **Metadata with before-state == target** cannot tell C1 from C2, so it is CX (ask). Approval does not yet refuse such a no-op write | **Q-6** |
| J-25 | **Which items recovery lists:** every job (D), and every item that has an action and is not terminal (R4.3 D: "any item not in a terminal state ... listed with its row"). A11 and A12 concern terminal (verified) items, so the tests reach them through `classify_item` directly. A CHANGED_AFTER_FINAL item is therefore **not shown at start-up**; it belongs to the post-job rescan (synthesis 12), which is B24's | **Q-5** |
| J-26 | **Recovery records nothing.** `recovery_record` (unit 2's table) is not written by B21; it waits for the person's choices (B25). Recovery returns a report and nothing else | Handoff: "shows the row and its next action and takes none" |
| J-27 | Kill tests: each kill runs once into a "master" sandbox, and every scenario works on a copy (`copytree` of the catalog and journal folders, `-wal`/`-shm` included). The child is the suite itself in `--kill-worker` mode | Keeps 26 kill-reached rows to 14 child processes (~13 s) |

### Questions for the Architect (units 5-6; none blocks, all built conservatively)

| # | Question | Options | Recommendation |
|---|---|---|---|
| Q-1 | Table F: "folder absent → not applied" and "parent missing → ask" overlap (§8.5 shape) | (a) the parent-missing row wins (built); (b) F1 regardless of the parent | (a): with the parent gone a resume cannot work, and "ask" is the safe answer |
| Q-2 | R4.3's tables are not total: 24 real table-A shapes, every non-INTENT state of C and F | (a) the X row, "ask" (built, by R4.3's rule); (b) add rows, e.g. F rows mirroring A7-A11 for create_folder | (a) for B21; consider (b) in B24 when create_folder is real (today a create_folder killed after C2 asks where a rename records) |
| Q-3 | Table E: E5 and EX added; E4 read as "no projecting event" | accept / rule otherwise | accept |
| Q-4 | Table D's rows (J-19) are a derivation | accept / rewrite | accept; B25's screens will exercise them |
| Q-5 | CHANGED_AFTER_FINAL (A12) is not shown at start-up (terminal items are not listed) | (a) as built, the post-job rescan shows it (B24); (b) list verified items of open jobs too | (a) |
| Q-6 | case_only rename and no-op metadata writes always "ask" | (a) as built; (b) refuse a no-op metadata write at approval now | (a) in B21; (b) belongs with B26's writer |

## 6. Receipts so far

### Receipts (units 7-8 session): the final standard tier

Run: `Scriptsun_suites.ps1 -Tier standard`, by `fo-runner` (Haiku), 2026-11-08 18:06-18:27 (about 20 minutes), logs in `%TEMP%o_suites_b21_final`. **26 run: 26 exited 0, 0 failed or missing, 0 skipped.** The 18 Phase 3 suites that print N/N sum to **1,585 checks** (the base's number); `b6_regression` all [PASS]; `p2_dashboard_check` all passed. `p3_mutation_check`: **mutants killed 69/69**, controls 29/29, real tree untouched, 99/99 checks. `p4_mutation_check`: controls 15/15, **mutants killed 23/23**, real tree untouched, `%LOCALAPPDATA%\FileOrganizer` unchanged, 0 `fo_p4_b21_*` folders left (5 left by mutated slices were removed by the run). `p4_journal_check` J5: FULL median 10.11 ms per triple, p95 12.74 ms (stop condition 6 not met).

| Suite | Exit | Seconds | Last result line |
|---|---|---|---|
| self_check | 0 | 2 | Ready. Everything the program needs is present. |
| p2_regression | 0 | 1 | ALL PHASE 2 REGRESSION CHECKS PASSED |
| p3_regression | 0 | 20 | P3 REGRESSION: 348/348 checks passed |
| p3_fixture_check | 0 | 1 | P3 FIXTURES: 101/101 checks passed -- report Logs\p3_fixture_check.json |
| p3_lab_harness | 0 | 1 | P3 LAB HARNESS: 10/10 checks passed -- report p3_lab_harness.json (and p3_lab_validation.json) |
| p3_plan_check | 0 | 8 | P3 PLAN (Build 8): 81/81 checks passed |
| p3_closeout_check | 0 | 6 | P3 CLOSEOUT (B18; B19 adds 4b): 62/62 checks passed |
| p3_encryption_check | 0 | 5 | P3 ENCRYPTION (B18): 29/29 checks passed |
| p3_revalidation_check | 0 | 8 | P3 REVALIDATION (B18): 59/59 checks passed |
| p3_metadata_check | 0 | 7 | P3 METADATA (Build 9): 133/133 checks passed |
| p3_metadata_view_check | 0 | 9 | P3 METADATA VIEW (B17): 54/54 checks passed |
| p3_refresh_check | 0 | 10 | P3 REFRESH (B14): 17/17 checks passed |
| p3_survey_check | 0 | 10 | P3 SURVEY (Build 7): 85/85 checks passed |
| p3_visual_check | 0 | 8 | P3 VISUAL (Build 5): 138/138 checks passed |
| p3_visual_bulk_check | 0 | 8 | P3 VISUAL BULK (Build 6): 79/79 checks passed |
| p3_relabel_check | 0 | 5 | P3 RELABEL: 117/117 checks passed |
| p3_dangling_check | 0 | 12 | P3 DANGLING: 67/67 checks passed |
| p3_registry_schema_check | 0 | 162 | P3 REGISTRY SCHEMA: 33/33 checks passed |
| p3_fingerprint_check | 0 | 11 | P3 FINGERPRINT (B18): 34/34 checks passed |
| p3_organize_check | 0 | 10 | P3 ORGANIZE (B20): 138/138 checks passed -- report Logs\p3_organize_check.json |
| p4_journal_check | 0 | 156 | p4_journal_check: 291/291 checks passed |
| p4_lock_check | 0 | 17 | p4_lock_check: 76/76 checks passed |
| p3_mutation_check | 0 | 314 | P3 MUTATION (B20): 99/99 checks passed |
| p4_mutation_check | 0 | 314 | P4 MUTATION (B21): 41/41 checks passed |
| b6_regression | 0 | 27 | [PASS] B7.1        errors and events attributed once, to their root inaccessible 1/0 seen 1/0, dir_errors 1/0, |
| p2_dashboard_check | 0 | 53 | ALL DASHBOARD CHECKS PASSED |

Before and after the run (the runner, and my own listing afterwards): `%LOCALAPPDATA%\FileOrganizer` does not exist, before and after; no `fo_p4_b21_*` folder in `%TEMP%`, before and after.
Manifest: `SOURCE_SHA256.csv` 249 rows (235 + 14); `Scripts\Test-Installation.ps1` printed "Installation check complete -- manifest verified.".
Versions: `fo_db.APP_VERSION` = `Phase2.VERSION` = `B21`; `fo_db.APP_SCHEMA_VERSION` = 17 (`self_check` and `Phase2.core` required schema 17); `p3_regression`'s "one product version ... schema 17 everywhere" passes. Schema: `p4_journal_binding` is the only new table (`p4_journal_check` 2d).



- Suites: `p4_lock_check.py` 75/75; `p4_journal_check.py` 170/170 (J5 numbers above); base and post-unit-2 quick tier
  as in §1 row 6. **Quick tier has not been re-run after units 3-4**: those add only new files; the final
  `-Tier standard` run is unit 8.
- (Superseded: the units 7-8 session did all of the following.) Not done yet: manifest regeneration and `Test-Installation.ps1`, versions, schema bump, `ARCHITECTURE.md`,
  `CHANGELOG-B21.md`, suite registration (unit 8).
- `%LOCALAPPDATA%\FileOrganizer\`: both suites record its existence and listing before and assert them unchanged
  after; it was never created (the Architect also found it absent).
- Sandbox: every test uses `tempfile.mkdtemp(prefix="fo_p4_b21_")`; `p4_fakes.Sandbox.inside()` asserts each
  path before it is written. The one real-looking write outside a sandbox that I considered and did not make:
  nothing wrote to OneDrive, the corpus or any owner database.

### Receipts (units 5-6 session)

- **Final runs through `fo-runner` (Haiku), one after another:** 2026-11-08 11:25:08 to 11:35:52 EST, logs in this session's scratchpad (`final\`).

  | Run | Exit | Count | Failures |
  |---|---|---|---|
  | `python Scripts/p4_journal_check.py` (full, J5 included) | 0 | **290/290** | none |
  | `python Scripts/p4_lock_check.py` | 0 | **76/76** | none |
  | `run_suites.ps1 -Tier quick` | 0 | **20 of 20 exited 0; 1,585 checks** (the 18 suites that print N/N: p3_regression 348, p3_fixture_check 101, p3_lab_harness 10, p3_plan_check 81, p3_closeout_check 62, p3_encryption_check 29, p3_revalidation_check 59, p3_metadata_check 133, p3_metadata_view_check 54, p3_refresh_check 17, p3_survey_check 85, p3_visual_check 138, p3_visual_bulk_check 79, p3_relabel_check 117, p3_dangling_check 67, p3_registry_schema_check 33, p3_fingerprint_check 34, p3_organize_check 138) | none |

  J5 (full run): FULL median 9.79 ms per triple, p95 13.09 ms, max 35.30 ms (3.22 ms per commit); NORMAL median
  0.12 ms, p95 0.22 ms. Stop condition 6 (above 50 ms) not met. Both suites: "[PASS] no fo_p4_b21_* sandbox folder
  created during this run survives it" and "%LOCALAPPDATA%\FileOrganizer: existence and file listing unchanged".
  The quick tier does not yet contain the p4 suites (unit 8 registers them); it shows that units 5-6 and the
  fixes changed no Phase 1-3 result.
- **One leftover sandbox, mine, removed.** The runner's before and after listings both showed `fo_p4_b21_5biawmsv`
  (created 11:00:15). It was made by my deliberate reproduction of the leak **before** the fix (that run printed
  "NEW LEFTOVERS: 1"), and it held only that test's two sandbox fixture databases. No run after the fix left one.
  I deleted it at the end, because this session created it; `%TEMP%` then held no `fo_p4_b21_*` folder.
- Development runs (by me, in this session): `p4_journal_check.py x` (no J5) 173/173 after the fixes, then
  286/286 after units 5-6, before the last test was added (a projector failure inside a run).
- `%TEMP%` held no `fo_p4_b21_*` folder at the start of this session (`glob`, 10:59). The runner's before and after
  listings are above.
- Files written this session: `Scripts\Phase4\projection.py` (new, 269 lines), `Scripts\Phase4\reconcile.py` (new,
  494 lines); changed: `jobs.py` (breaker, `projection=`, P through `projection.project_event`), `locks.py`
  (`owner_block()`), `p4_fakes.py` (`register`, survivor helpers, `attach_sandbox`, `FakeProjector`, `FakeObserver`),
  `p4_journal_check.py` (2,159 lines; sections 5a-5c, 6a-6e, the kill worker), `p4_lock_check.py` (the survivor
  check), `ARCHITECTURE.md` (the restore ceremony, +3.4 KB), `PROGRESS_B21.md`, this file. Every change was made as
  a complete new file that then replaced the old one (patch scripts written with the Write tool).
- (Unit 8 was done in the units 7-8 session; see the receipts at the top of section 6.)

## 7. Unverified

- L2, L5, L6, L7, J2, J3 (not run; steps above). Which Python a double-click on the `.bat` selects.
- Behaviour of anything under a OneDrive or SMB path (L9, L10, never run).
- Units 7-8: the mutants prove the suites notice 23 chosen breakages, not that no other breakage exists. M05's kill relies on the slice watchdog as well as on a failed check. The mutated slices were run in-process in a child, not through `run_suites.ps1`'s own process model.
- `fo_analyzers.REQUIRED_SCHEMA_VERSION` was left at 16 (J-31); nothing in B21 reads it at 17.
- The final `p3_encryption_check` reading `>= 16` (J-28) is looser than before by design.
- Units 5-6: the behaviour on a real disk (B22) and with real projectors (B24). The key spaces are proved total and
  unambiguous, but only the rows listed above were exercised. The classifier has never seen a real `FileIdInfo`.
- Whether a read-only journal connection works when the folder is not writable. The tests open read-only beside
  the `-wal` and `-shm` a kill left, in a writable sandbox.
- Kill timing: a kill lands at a Python-level hook, between commits. A kill **inside** a commit (mid-`fsync`) is
  J2's territory (VM, not run).

## 8. Helpers used

Units 7-8 session (Sonnet 5.5): `fo-runner` (Haiku) twice: the standard tier after the version bump (it found the five `test_migration_017` failures) and the final standard tier. No `fo-scout`. No other helpers. Development runs of the mutation suite and of `p4_journal_check x` were made by this session directly.

`fo-runner` (Haiku): base standard tier; quick tier after unit 2. `fo-scout` (Haiku): how Phase 3 tests lock and
verify a plan revision. No other helpers; the build ran on Sonnet 5.5.

Units 5-6 session (Opus 5.5): `fo-runner` (Haiku) once, for the final runs (the three commands above, one after
another). No `fo-scout`: the synthesis sections and R4.3 §2.5 were read directly, as the brief named them. No other
helpers. Development runs were made by this session directly, with the output filtered to failures and counts.

## 9. Mistakes made and repaired

- **Units 7-8 session: M21's slice list was wrong** (it named `faults`; the manifest-at-resume check lives in `pause`), so the first full run reported a survivor that was not a missing test. Repaired by correcting the list; no test was added for it and nothing was weakened. M17, by contrast, was a real gap and got a check.
- **Units 7-8 session: my first full run hung at M05** (a blocking lock makes the fake-clock retry loop run for minutes); the run then ended with exit 1 after 17 minutes and no summary, and my later `timeout 400` run killed it again. Those two kills left two of **my own** folders in `%TEMP%` (`fo_p4_b21_mut_*` and one sandbox, both created 17:25:30); the runner's first before/after listing showed them as present both times. I removed them, after reading their times and contents, and added the watchdog. The final run started with none.
- **Units 7-8 session: a patch script left a broken string literal** (a `"\n"` collapsed to a real newline inside a heredoc: the `AGENTS.md` trap again) in `p4_mutation_check.py`; it did not parse, nothing ran from it, and I repaired it with the Edit tool.
- **Units 7-8 session: bumping the schema broke `test_migration_017`** (five checks), which the units 1-4 session had written to run before the bump; found by the first standard-tier run, not by my own earlier dev runs of other suites. Repaired (J-30).

- I patched `p4_lock_check.py` through a quoted Bash heredoc, which collapsed the `\n` escapes (the trap
  `AGENTS.md` names) and left a syntax error. Repaired by rebuilding the block with a patch script written by the
  Write tool; the file then ran 23/23.
- My first L4 control assumed an inherited handle would keep the lock alive. It did not (see the probe table), so the
  control as first written failed. I replaced it with one that proves the child holds the handle and then records
  that the lock is released anyway; no assertion was weakened for the primary L4 check.
- The power-off during J5 left a ~13 MB sandbox database (my own); I removed it and re-ran J5 from a fresh sandbox.
- Eight `fo_p4_b21_*` sandbox folders were left in `%TEMP%` by my failed or crashed development runs (a traceback
  skips the cleanup of a test that still holds a database open). All were mine (the prefix is unique to this build)
  and held only test data; I deleted them after the last run. The final full runs of both suites left none. **Corrected 2026-11-08: not so; every full run of `p4_journal_check.py` left one (see item F2 and the last bullets below).**
- **Progress times (units 1-4 session, disclosed at the Architect's request).** `PROGRESS_B21.md` recorded units 2-4
  at "19:50", "20:35" and "21:10", but the file was last written at 19:35. Those times were the model's estimates,
  not clock readings. Repaired by an added correction line (2026-11-08 10:59), leaving the old lines as they were.
  Every later line's time comes from `date`.
- **The "no leftovers" claim was wrong (units 1-4 session).** §6 and the last bullet of this section said the final
  full runs left no sandbox. Each full run of `p4_journal_check.py` left one (the migration-017 test's unclosed
  connections). Repaired as item F2: connections are closed in `finally`, the sandbox runs closers, and both suites
  now check for survivors.
- **Units 5-6 session:** my first patch script for fix 2 used a quoted Bash heredoc, which collapsed `\\n` (the
  `AGENTS.md` trap). It failed to compile before touching any file; I rewrote it with the Write tool. My first
  expected order in 5b left out the `C3:after` hook. I corrected the expectation, and the test still asserts the
  whole sequence. Neither change weakened a test.
- **Units 5-6 session: my reproduction of the leak left a sandbox** (`fo_p4_b21_5biawmsv`, 11:00:15). The run was
  deliberately made before the fix, to prove the leak. I removed the folder at the end, after the final runs had
  listed it as present before they started. No run after the fix left one.
