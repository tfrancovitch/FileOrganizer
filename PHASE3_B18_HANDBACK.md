# Phase 3 handback — B18 (the Phase 3 closeout round)

**Against:** `FileOrganizer-Phase3\phase_3_handoff_b18\02_B18_HANDOFF_PLAN.md` and
`01_B18_SYNTHESIS.md` (the B17 review; the owner's decisions Q1–Q5 of 2026-09-26;
the research coverage review's M3–M6 and S3–S6)
**Date:** 2026-09-27
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B18\`, a git worktree of branch
`phase3-b18` created from tag `phase3-b17`. Not committed, not tagged, not pushed.
`CHANGELOG-B18.md` says what changed; this document says how it measures against the plan.
The closeout assessment is `C:\FileOrganizerTesting\Docs\Handoffs\P3_CLOSEOUT.md`: its
§5a is empty, and P3.R12 is listed as pending.

---

## 0. Tag, then sync the root (task 0)

- **B17 was uncommitted.** I committed it exactly as handed back as
  **`711fd43`** (`711fd43c581847eebe8197f764e1158800694b0b`) and put the
  annotated tag **`phase3-b17`** on it (tag object `1f3385f7`). This is the
  step the plan names, and every earlier round took it too.
- **Both tags verify.** Clean checkouts of `phase3-b16` (203 files) and
  `phase3-b17` (208), every blob as git stores it, pass `Test-Installation.ps1`
  with "manifest verified".
- The five input hashes in `03_reference\README.md` match the tagged blobs.
- `phase3-b17` names both a branch and a tag (as every earlier round's name
  did), so git warns that the name is ambiguous. The tag and the branch point
  at the same commit.
- **Root sync (Q1).** `PROJECT_PLAN.md`, `README.md` and `STAGE_MAP.md` were
  copied from a clean checkout of the tag to `C:\FileOrganizerTesting\`. The
  stale root copies were moved (not deleted) to
  `C:\FileOrganizerTesting\_Archive\2026-09-26_root_docs\`.

| File | Root before (= archived copy) | The tag's copy | Root after |
|---|---|---|---|
| `PROJECT_PLAN.md` | `7B62DC73…96ABF2` | `BFB42A03…10022CD4C` | `8408EF1D…A8991CDBD2` |
| `README.md` | `2A0739CD…FACD885BC695` | `21633BEC…A5FAA2A90A91` | `21633BEC…A5FAA2A90A91` (= the tag) |
| `STAGE_MAP.md` | `8F34A4CC…ED9C0FE091D5` | `9FDF09B7…FAA506A11B31` | `9FDF09B7…FAA506A11B31` (= the tag) |

  The full hashes are:
  - `PROJECT_PLAN.md` before `7B62DC73AD5D6B41C94F572656D60732E070BA6B5C928B95C5AEE9F63896ABF2`, tag `BFB42A0322D3BD4E9FEEA647486D0B20812D642213A301CEF6512E010022CD4C`, after `8408EF1DDFF5180C9A11E209F7DD9605682DA10B41D4AB9EA2A026A8991CDBD2`;
  - `README.md` before `2A0739CD05553155719C22D6E3D7575693641408DDD4A5DEDCE4FACD885BC695`, tag and after `21633BEC0134BA8BECDF318F368F67EDF6E3628F97BAF0AE62A3A5FAA2A90A91`;
  - `STAGE_MAP.md` before `8F34A4CC1E902E6D19EBAEB9508B32CCE08EF9F6DCA3FEAC47E6892C0FE091D5`, tag and after `9FDF09B78A8C8AC80EF42A81C66C0CA35834549BEBEA3ED864AA492506A11B31`.

  The root `PROJECT_PLAN.md` differs from the tag's copy only by the one line
  the plan asked for, added under its header:
  > Synced from tag phase3-b17 on 2026-09-26. The workspace root copy is canonical; each round syncs it from the last tagged build.

  One step was refused: writing a short README into the archive folder was
  blocked by the session's permission check, so the folder holds only the three
  stale copies. Nothing else was retried.
- **The worktree** `FileOrganizer-Phase3-B18\` is on branch `phase3-b18` from
  the tag. As the plan says, the B18 documents are **not** synced to the root
  this round.

## 1. The research's gates (task 1)

### 1.1 The fingerprint specification (M3)

- **Where:** `C:\FileOrganizerTesting\Docs\Specifications\PLAN_FINGERPRINT_SPEC.md`
  (Docs was within reach). It covers the payload's fields and order, key
  sorting, separators, UTF-8 and `ensure_ascii=False`, the Unicode rule, integers,
  floats, null, the non-JSON rule and the schema string.
- **The Unicode rule is "as stored."** A plan hashes a path byte-exactly as the
  evidence holds it: a plan is a statement about those exact names, and
  normalizing would let two different on-disk names share one fingerprint.
  Folder-policy *matching* compares in NFC (5.3). That rule decides what a
  policy covers and never what a plan says.
- **Explicit encoders:** `plans.plain` / `plans.canonical` replace
  `default=str`. Anything that is not JSON-native (a date, a path object, a
  decimal, a set, bytes) is refused with a TypeError; NaN and the infinities are
  refused.
- **The golden vectors:** `Resources\Phase3\fingerprint_vectors.json`, 9 of
  them (an empty plan, a retain item, non-ASCII paths composed and decomposed,
  a metadata item, a blocked item, a removal with preconditions, key order,
  and an unpaired surrogate). They are checked by
  **`Scripts\p3_fingerprint_check.py`** (the vectors suite: 31/31), which also
  carries an independent encoder written from the spec alone.
- **No existing fingerprint changed** (Q2 below).

### 1.2 Mutation sensitivity (M5)

- `Scripts\p3_mutation_check.py` applies **32 source mutants**, one at a time, in
  a sandboxed copy of `Scripts\`, and runs the suite slice that should catch
  each one. Every slice is first run **unmutated** as a control. The real tree
  is hashed before and after.
- It covers every rule the plan names: the protection gate, the keeper and
  physical-copy floors, unknown-never-passes in `_settle`, frozen batch
  membership, preserve-not-overwrite, withdrawal-not-deletion, the pair chain
  guard, the no-retaining-decision block, the fingerprint covering every item,
  no metadata item from project metadata or Leave Unchanged, zero source
  write, and the integrity blockers. It also covers B18's new rules: the
  material-change rule, the commit guard, Confirm's undo, the cache, NFC
  matching, the lock guards and the plan status cache.
- **Result (run 2): 32/32 killed; 19/19 controls clean; the real tree
  untouched.** The suite reports 52/52 checks and writes
  `Logs\p3_mutation_report.json`, which is machine-readable.
- **Run 1 had three survivors**, which were findings. Each one got its missing
  test, and no mutant was weakened (Q1 below).
- The full killed / survived table is in `P3_CLOSEOUT.md` §3.8.

### 1.3 The state machines (M6)

- `ARCHITECTURE.md`, "State machines", has four transition tables, each with
  its events, guards and invalid transitions, and each citing the code that
  enforces it and the test that proves it:
  - (a) the decision lifecycle;
  - (b) review routing (the existing precedence table, referenced);
  - (c) the plan-revision lifecycle;
  - (d) conflicts.
- **The invalid-transition tests** are in `p3_closeout_check` §1 (57/57). They
  include: lock twice; lock an obsolete or superseded revision; edit a locked
  revision (no operation exists: a new build is a new revision); withdraw a withdrawn decision; supersede across domains; and obsolete
  never reverting.

## 2. Material change and bulk Confirm (task 2)

### 2.1 The rule

- A metadata decision now returns to review only when one of three things
  changed:
  - **the file's content identity**, meaning a new observation, unless both
    sides hold the same authoritative content hash;
  - **the field's observed value**;
  - **its value-state** (read, empty, conflict, unavailable...).
- A re-read that finds the same value, a new analyzer version, an unchanged
  rescan, or an integrity change does **not** send it back.
- The same rule (`metadata.binding_drift`) governs Leave Unchanged's return and
  the "until the evidence changes" deferral.
- For metadata-item obsolescence (`plans._metadata_obsolescence`), the rule
  compares the item's frozen observed value and state. **Integrity is kept as
  plan drift:** an integrity change still obsoletes a locked revision
  ("integrity evidence changed"), although the decision stays current.
- **Old bindings.** B16/B17 bindings hold analyzer-result ids. They are resolved
  to values when loaded (`evidence.resolve_bound_values`). A binding whose ids
  are gone falls back to comparing ids, so it is never silently current.

### 2.2 Bulk Confirm

- **Confirm...** on the Metadata queue runs on B10's machinery: a
  `metadata_question` member kind (`registry.METADATA_CONFIRM`), a five-bucket
  preview, a guarded commit and batch Undo.
- The commit is judged on `evidence.metadata_mark`, the evidence mark plus the
  newest analyzer result, so a re-analysis between preview and commit refuses
  it.
- Undo re-applies each superseded decision exactly as recorded.

### 2.3 Tests

`p3_revalidation_check` (59/59) covers each case the plan lists:

| Case | Result |
|---|---|
| Analyzers re-run on unchanged files | 0 drifted, 0 locked revisions obsoleted |
| File edited (content changes) | drifted |
| Value changes, content unchanged (synthetic analyzer row) | drifted |
| Value-state changes | drifted |
| Integrity change alone | decision current; plan preconditions change; locked revision obsolete, "integrity evidence changed" |
| B16/B17-shaped bindings | load, resolve and compare correctly (and fall back to ids when unresolvable) |
| Confirm: preview = commit | yes |
| Confirm: commit after the record moved / the evidence moved | refused / refused |
| Confirm: undo | restores every question |
| Confirm: a Clear or Leave Unchanged | never altered (re-recorded as the same kind) |

Three `p3_metadata_check` assertions from B16/B17 asserted the old id rule
("a re-read drifts"). They now assert the Q2 rule, and a value-changed
counterpart was added beside each one, so nothing was dropped (133/133).

### 2.4 Real scale (on a DB copy)

See §4 below. **Drift after the second re-run: 0.** After the third: 0.

## 3. Encryption evidence (task 3)

### 3.1 Legacy OLE2 flags (`OfficeAnalysis.py` 1.5.0)

- They are read **from header structures only**, through a bounded,
  loop-safe sector-chain reader (at most 64 KB per stream; FAT or mini FAT;
  a revisited sector stops the walk):
  - Word: the FibBase `fEncrypted` bit;
  - Excel: `FILEPASS` after the globals `BOF`;
  - PowerPoint: `EncryptedSummary`, the CurrentUserAtom token, or a UserEditAtom
    carrying `encryptSessionPersistIdRef`;
  - IRM `\tDRMContent` and `EncryptedPackage` are also read as encrypted.
- A structure that cannot be read gives `unknown`, never a crash. The suite
  feeds truncated headers, sector loops, a sector index out of range, and a
  stream longer than its chain.
- **Verified on genuine files for all three formats.** `C:\FOTest\Research\Samples\third_party\apache_tika\`
  holds Apache Tika's genuinely password-protected test documents. Each one is
  checked against the hash in its `PROVENANCE.json` before use.
  - `testWORD_protected_passtika.doc`, `testEXCEL_protected_passtika.xls` and
    `testPPT_protected_passtika.ppt` read `encrypted`. Each format's own check
    fires: Word's `fEncrypted`, Excel's `FILEPASS`, and PowerPoint's header
    token.
  - Their unprotected controls read `not_encrypted`, and so does a third-party
    `.doc`.
  - Tika's IRM Word file reads `encrypted`.
  - The genuine PowerPoint's `Current User` lives in the mini stream, and the
    bounded reader reads it in place.
- **Built compound files** cover every branch, with the flag set and clear
  (`p3_encryption_check` §3, 29/29 overall): `WRITEPROTECT` before `FILEPASS`,
  the `EncryptedSummary` stream, the header token alone, and the UserEditAtom
  alone.
- On your corpus every legacy OLE2 file reads `not_encrypted`, so the
  `encrypted` result is proved on the Tika files only.

### 3.2 `analyzer_result.encryption_state` (migration 016, schema 16)

- Written by both analyzers. It takes `encrypted`, `not_encrypted`, `unknown`,
  `not_applicable`, or NULL for a row written before B18.
- A file refused as password-protected is still an analyzer error, but its
  row now says `encrypted`. The engine keeps the exception's `evidence_fields`,
  and nothing else from a failed file.
- `evidence._integrity_evidence` reads the column first. The B17 text match is
  used **only** when the column is NULL; the suite checks both paths.

### 3.3 Real scale

See §4.

## 4. Real scale (tasks 2.4 and 3.3)

- **The copy:** a SQLite backup of your real project, migrated 8 → 16
  (0.7 s), kept under a scratch app root's own `Projects\` folder. It is
  deleted now.
- **Your original is unchanged:** 118,329,344 bytes and the same mtime before
  and after every step. The size and mtime of all 4,904 real documents are
  identical before and after (0 changed).
  The one timestamp that moved is the original's SQLite `-shm` file (the WAL
  shared-memory index), which SQLite touches whenever a connection opens the
  database, here the read-only backup that made the copy. The database file
  and its `-wal` (0 bytes) are unchanged.
- **Staging:**
  - 4,904 documents: PDF 1,529, OOXML 2,076, legacy OLE2 1,299;
  - 9,808 questions, each answered (a desired Creator on each document, plus
    its Title question);
  - 2,452 of the bindings rewritten into the B17 (ids-only) shape, so both
    shapes were exercised. All 2,452 resolved.
- **Three full re-analyses** (the real analyzers, read-only; 185–321 s each):
  Office analyzed 3,362 and errored 11 each time; PDF analyzed 1,529.

| | Drifted decisions | Locked revisions obsoleted |
|---|---|---|
| B17, one re-analysis (for comparison) | **4,902** | — |
| B18 re-analysis 1 | **0** | rev 1, **only** "integrity evidence changed" (4,285 items: unknown → known, the new encryption evidence), as it should be |
| B18 re-analysis 2 | **0** | 0 |
| B18 re-analysis 3, with revision 2 locked beforehand | **0** | **0** (revision 2 still stands) |

**Encryption state by family (task 3.3):**

| Family | `not_encrypted` | `encrypted` | `not_applicable` | NULL (analysis failed) |
|---|---|---|---|---|
| PDF | 1,515 | **14** | — | — |
| `.docx` | 1,736 | — | — | — |
| `.xlsx` | 153 | — | — | — |
| `.pptx` | 185 | — | — | — |
| `.doc` (OLE2) | 316 | — | — | — |
| `.doc` (RTF saved as `.doc`) | — | — | 947 | — |
| `.ppt` | 23 | — | — | — |
| `.xls` | 2 | — | — | 11 |

- **B17's 352 "unknown" legacy files:** 341 are now known (all not
  encrypted). The other 11 are the `.xls` files that are not OLE2 at all, so
  the Office analyzer errors on them (V1).
- The queue's integrity unknowns fell from B17's 354 files to **13**: those 11
  plus the 2 OOXML-family files (`.xlsm`, `.dotx`) the Office analyzer does not
  read. The questions: 26 unknown for each integrity check; `blocked_by_evidence`
  `signed_or_certified` 12 and `encrypted` 20 (6 and 10 files).

**Item states** (4,904 items, after the re-analyses):

| State / reason | B17 | B18 |
|---|---|---|
| `unsupported_action` / `writer_unsupported` | 4,534 | **4,875** |
| blocked / `signed_or_certified` | 6 | 6 |
| blocked / `encrypted` | 10 | 10 |
| `blocked_evidence` / `source_not_encrypted` (legacy OLE2) | 341 | **0** |
| `blocked_evidence` / `source_not_signed_or_certified` (unreadable) | 13 | 13 |

- 14 PDFs are encrypted, and 4 of them are also signed, so they are counted
  under `signed_or_certified` (B17 JC 3's ordering).
- **99.4%** of the items now show the true reason: there is no writer.

**The queue's cold open (task 4)**, 9,808 questions (4,904 rows), five cold
runs, each in a fresh process:

| | Time |
|---|---|
| Cold open, best / median / worst | **0.708 / 0.82 / 0.94 s** (runs: 0.808, 0.94, 0.708, 0.911, 0.82) |
| Second open in the same session (cache hit) | 0.21–0.37 s |
| Plan build (4,904 items) | 0.20–0.36 s |
| Drift computation (9,808 questions) | 0.56–0.79 s |

- An earlier five-run series, before the readings were narrowed, gave
  0.81–0.92 s.
- Under 1 s came from three changes:
  - the per-location work is done once per file;
  - each analyzer's newest reading is read on the current observation only;
  - an in-session cache (`refresh.MetadataCache`) is rebuildable, never
    authoritative, and keyed by watermarks plus the metadata evidence mark.
- **Equivalence:**
  - On the real copy, the narrowed read gives the same questions as B17's
    every-observation read: 9,808 of 9,808 are equal. So do the per-file
    readings (`location_metadata`) of the first 3,000 files that have a
    reading.
  - In the suite, 120 random steps over 3 sessions compare the cached result
    with a fresh load after every step (`p3_revalidation_check` §5).

## 5. The smaller recommendations (task 5)

| # | Done | Test |
|---|---|---|
| 5.1 (S3) | `store.export_current_state` / `current_state_rows` writes current keepers, canonical and redundant per group, pair relationships, desired and project metadata, each with its decision ids. It uses Phase 2's `write_rows_csv` and so its formula guard. **Export current state...** is on the Decide page and writes under the project's `Exports\`. | `p3_closeout_check` §5 and §5b. The formula guard is tested on `=`-led values: a file named `=SUM(1).txt` and a project tag `=cmd`, written with a leading apostrophe and never raw |
| 5.2 (S4) | The basis now records `registry_version`, `registry_digest`, `resolver_version`, `evaluator_version`, `payload_schema` and `analyzer_mark`, alongside `routing_version`. The lock also refuses a revision whose files were re-analyzed after its preview (the mark moved). | `p3_closeout_check` §3: the new basis; a B17-shaped basis reads, locks and verifies |
| 5.3 (S5) | `model.path_key`, stated in ARCHITECTURE.md "Path matching": separators (`/` = `\`, runs collapsed, UNC lead kept), the extended prefix (`\\?\`, `\\?\UNC\`), the trailing separator, NFC, simple lower-casing, and the segment boundary (`Docs` ≠ `Docs2`). Root policies compare through it too. | `p3_closeout_check` §4, one check per clause |
| 5.4 (S6) | `p3_plan_revision.status / locked / obsolete` is documented as a cache of `p3_plan_event`. `store.plan_status_from_events` / `plan_status_drift` rebuild it. | `p3_closeout_check` §2: rebuilt from the events for every revision and equal to the columns |

## 6. The closeout assessment (task 6)

- `C:\FileOrganizerTesting\Docs\Handoffs\P3_CLOSEOUT.md` is complete except
  §5a, which is left empty for you. **P3.R12 is listed as pending**, and the
  recommendation is "do not close yet."
- **§3 is the traceability matrix.** It covers the 15 acceptance questions,
  S1–S15, the principles, the invariants, the research's P0 items and
  M1–M6. Every row cites a test or fixture that exists in the B18 tree, or
  carries an explicit non-Met verdict with its reason. The mutation table is
  §3.8.
- **V1 is reconciled.** B17's Office signature table totals 3,373: 3,362
  analyzed and 11 errors, and the 11 are `.xls` files that are not OLE2. The
  13 unreadable-integrity files are those 11 plus one `.xlsm` and one `.dotx`,
  which the Office analyzer does not read.

## 7. Regression and bookkeeping (task 7)

- **Versions:** `APP_VERSION` = `Phase2.VERSION` = **B18**; schema **16**;
  `fo_db.MODULE_VERSION` 1.13.0.
- **`self_check`:** requires 16 and still checks 34 modules (no new runtime
  module).
- **Docs:** README, STAGE_MAP, ARCHITECTURE, PROJECT_PLAN, BASELINE,
  `Resources\Phase3\README.md` (the vectors), and this handback. PROJECT_PLAN
  covers:
  - the header;
  - the §3 row, "Built — closeout assessment drafted; awaiting P3.R12 and the
    owner's decision";
  - §4a B18;
  - §6 rewritten: P3.R12, then your closeout decision, then Phase 4 scoping
    with its two engineering preconditions;
  - the §7 tree.
- **Manifest:** `SOURCE_SHA256.csv` lists **217** files, B17's 208 plus nine
  new ones. It is verified in the worktree and in a clean materialization of
  the would-be commit tree (see BASELINE.md).

**Suite counts** (every suite run from the worktree):

| Suite | Result |
|---|---|
| `p3_fingerprint_check` (new; the vectors suite) | **31/31** |
| `p3_closeout_check` (new) | **57/57** |
| `p3_encryption_check` (new) | **29/29** |
| `p3_revalidation_check` (new) | **59/59** |
| `p3_mutation_check` (new) | **52/52** (32 mutants killed, 19 controls clean, tree untouched) |
| `p3_metadata_view_check` | 54/54 |
| `p3_metadata_check` | 133/133 |
| `p3_fixture_check` | 101/101 (F01–F16 green) |
| `p3_plan_check` | 81/81 |
| `p3_regression` | 345/345 |
| `p3_refresh_check` | 17/17 |
| `p3_survey_check` | 85/85 |
| `p3_visual_bulk_check` | 79/79 |
| `p3_visual_check` | 138/138 |
| `b6_regression` | 73/73 (see below) |
| `p2_regression`, `p2_dashboard_check` | all passed |
| `self_check` | ready (schema 16, 34 modules) |
| The lab's `validate_fixtures.py` | 16/16 |

**`b6_regression` failed once, on timing.** In the full back-to-back run, one
check failed: E.F002, "duplicate query latency is flat over runs" (run 1
2.15 ms → run 8 4.33 ms, 2.02×, over its 2× limit). It passed 73/73 on the
immediate rerun. The check times millisecond queries on a busy machine, and
B18 changes no Phase 2 query code. I report it rather than call the suite
green unqualified.

`p2_dashboard_check` prints "EOF marker not found" twice. That is pypdf
reading a deliberately truncated fixture; the suite passes.

## 8. Judgment calls (flag these)

1. **A binding binds values, and the ids are kept as provenance** (2.1).
   A new binding records the content id, the observed value(s) and the state.
   The analyzer-result ids stay for provenance and for the fallback.
2. **Content identity across a new observation is proved only by an
   authoritative content hash on both sides.** A B16/B17 binding has none, so a
   touched file sends it back for revalidation. That is conservative, and it
   is written in the closeout's known limits.
3. **Plan items stay observation-based.** A plan acts on a specific
   observation, so a metadata item still records its observation. Obsolescence
   compares the frozen value and state, but a changed observation with a
   changed value obsoletes, as before.
4. **A location the last scan did not find is `unavailable`**, even when its
   old readings remain (a B17 behaviour that the revalidation tests exposed).
5. **Confirm's Undo re-applies what it superseded.** Undo of a plain bulk
   action withdraws its decisions. A Confirm replaced decisions that were
   already right, so withdrawing alone would leave those questions undecided.
6. **Confirm's buckets:**
   - a *conflict* (readings disagree) and *blocked* (the file is gone, or its
     reading is stale) are never confirmed, because they need a person;
   - *already current* is shown but not re-recorded.
7. **Confirm does not end a deferral.** It re-records decisions and nothing
   else, so a question's deferral stays as it was. Ending one stays a single
   action.
8. **IRM and `EncryptedPackage` under a legacy name read `encrypted`.** Both
   stop a writer from reading or changing the content. Carried in the
   closeout's limits.
9. **The engine's `evidence_fields` hook** is the only thing kept from a failed
   file, so a refused encrypted file still records `encrypted`. No other field
   from a failed analysis is stored.
10. **The payload schema stays `/1`.** No existing fingerprint changed. The only
    place the explicit encoders differ from B15–B17 is the unpaired surrogate,
    and the old code raised there, so no old fingerprint can contain one.
11. **NFC matching over-covers, and it does so in bulk scopes too.** Two
    sibling folders that differ only by normalization are one scope for a
    policy and for a bulk folder scope. I chose the direction that can only
    widen a protection. The same comparison then widens a bulk action's
    folder scope; its preview lists every member.
12. **The spec lives in `Docs\Specifications\`, and the vectors live in the
    repo** (`Resources\Phase3\`), so the suite runs from any clean checkout.
13. **The lock guard on `analyzer_mark` is new.** A revision previewed before a
    re-analysis must be previewed again before it can be locked, even when no
    decision moved. That is the S4 basis doing its job.
14. **The mutation suite writes its report into `Logs\`.** The folder is
    removed from the handed-back tree, as usual.
15. **The material-change rule ignores integrity for decisions, not for
    plans.** The trap in the plan says to keep both halves, and both are
    tested.

## 9. Questions

1. **Did any mutant survive, and what does the new test cover?**
   Three survived run 1. Each was a missing test, added without weakening the
   mutant, and all three are killed in run 2.
   - **M03** (`resolve.py`, the physical-copy floor) survived because no test
     marked every *present* physical copy redundant while an absent location
     remained. The new test is `p3_closeout_check` §1d: the resolver keeps the
     floor (`MINIMUM_PHYSICAL_COPIES`) when the one location left is not
     present.
   - **M04** (`plans.py`, the physical-copy floor in the evidence preview)
     survived because the keeper and location floors always fired first. The
     new test is `p3_plan_check` §2b: the group's keeper is planned away by a
     pair, and the copy's removal must fail `keeper_exists`, the location floor
     **and** the physical-copy floor, each named separately.
   - **M29** (`store.py`, lock only a previewed revision) survived because the
     record and evidence marks always refused first. The new test is
     `p3_closeout_check` §1c: lock twice, and lock an obsolete revision, with
     both marks current, so only the status guard can refuse.

   Run 1 also had two unclean controls. `p3_regression` pinned B17's versions.
   `p3_revalidation_check` exposed that a conflict listed its values in an
   order that depended on the loaded history, which I fixed in
   `metadata.observe`. M08's run-1 kill was not counted while its control was
   unclean.
2. **Did task 1.1 change any existing fingerprint?** **No.**
   `p3_fingerprint_check` proves it three ways:
   - B15–B17's canonical form gives the same bytes for every vector;
   - revisions locked by the actual B15, B16 and B17 code (materialized from
     each tag with `git archive`) verify in this build under the schema they
     were locked with;
   - an audit shows `default=str` was never called for any of those payloads,
     nor for the two 4,904-item revisions on the real copy (both verify; old
     bytes = new bytes).

   The schema therefore stays `fileorganizer.p3.plan-payload/1`. Verification
   now uses the schema recorded in the revision (`plans.verify(...,
   payload_schema=)`, `store.verify_plan_revision`). An unknown schema is
   refused, never guessed.
3. **Is anything in the matrix only "Not evidenced"?** Yes, four rows, none of
   which bears on a destructive action:
   - **the rule registry as JSON Schema.** The substance is met (a strict,
     versioned, digested code registry that validates every kind, target and
     value), but the form the evaluation named is not built;
   - **identifier relabelling for I15.** Display order is shown to be inert
     when rows arrive in another order, but no test renumbers ids and compares;
   - **renamed roots in path matching.** A root moved on disk is a new root to
     Phase 1, and nothing re-maps policies to it;
   - **the research validator's stderr warning**, which comes from the research
     package's tool, not the product.

   You may accept these as they stand or name them as work. They are listed
   the same way in `P3_CLOSEOUT.md` §5.

**Not done, by the plan:**
- no move or rename decision kinds (P3.R12 first);
- no metadata writer;
- no bulk setting of desired values;
- no Phase 4 code, no cross-process lock, no persisted projection;
- §5a is not written;
- B18 is not committed, tagged or pushed;
- `C:\Dev\dev-notes` is not touched;
- no source file, and not the original real project, was modified.
