# B18 — Phase 3: the closeout round

**Date:** 2026-09-27
**From:** B17 (tag `phase3-b17`, commit `711fd43`), on branch `phase3-b18`
**Handoff:** `FileOrganizer-Phase3\phase_3_handoff_b18\` (the B17 review, synthesis and plan; the owner's decisions of 2026-09-26)
**Handback:** `PHASE3_B18_HANDBACK.md`
**Closeout:** `Docs\Handoffs\P3_CLOSEOUT.md` (a draft; §5a is the owner's; P3.R12 pending)

B18 is the last build round of Phase 3. It does three things:
- closes the research's remaining gates (M3, M5, M6, S3–S6);
- fixes what B17's real-scale run exposed;
- writes the closeout assessment, whose §3 is the invariant-to-test traceability matrix (M4).

It adds no move or rename decision kinds (P3.R12 comes first), no metadata writer, and no Phase 4 code. Schema 16.

**Before the build:**
- B17 was committed as `711fd43` and tagged `phase3-b17` (annotated), as handed back.
- Clean checkouts of `phase3-b16` (203 files) and `phase3-b17` (208) pass `Test-Installation.ps1`.
- The workspace root's `PROJECT_PLAN.md`, `README.md` and `STAGE_MAP.md` were synced from a clean checkout of the tag. The stale copies were moved to `_Archive\2026-09-26_root_docs\`.

## What is new

### Encryption evidence — schema 16, `016_encryption_evidence.sql`

- **`analyzer_result.encryption_state`** takes the values `encrypted`, `not_encrypted`, `unknown`, `not_applicable`, or NULL (a row written before B18).
  - It is a promoted column: two analyzers populate it.
  - `fo_analyzers.REQUIRED_SCHEMA_VERSION` is 16.
- **The analyzer engine** now keeps an exception's `evidence_fields` on the error row, and nothing else from a failed file.
  - New `file_organizer_common.PasswordProtectedError`.
  - Both analyzers raise it for a password-to-open file, so the refused file's row still says `encrypted`.
- **`PDFAnalysis.py` 1.3.0:** `EncryptionState` comes from the `/Encrypt` dictionary. A file with only an owner password counts as encrypted.
- **`OfficeAnalysis.py` 1.5.0:**
  - An opened `.docx` / `.xlsx` / `.pptx` is `not_encrypted`.
  - Legacy OLE2 files are read **from header structures only**, through a bounded reader that walks only the sectors a check needs (FAT or mini FAT, loop-safe):
    - Word: the FibBase `fEncrypted` bit (0x0100; Word 97+ and Word 6/95 identifiers);
    - Excel: a `FILEPASS` record after the globals `BOF` (after an optional `WRITEPROTECT`), in `Workbook` or `Book`;
    - PowerPoint: an `EncryptedSummary` stream, the CurrentUserAtom header token, or a UserEditAtom carrying `encryptSessionPersistIdRef`;
    - IRM (`\tDRMContent`) and an `EncryptedPackage` stream under a legacy name are also read as encrypted.
  - A structure that cannot be read gives `unknown`, never a crash. RTF saved as `.doc` is `not_applicable`.
- **`evidence._integrity_evidence`** reads the column first. B17's reading of the error wording is kept **only** for a NULL column.

### What counts as a material change (the owner's Q2)

- **`metadata.binding_drift`** judges by *what* changed. A decision returns only when one of these changed:
  - the file's content identity (a new observation, unless both sides hold the same authoritative content hash);
  - the field's observed value;
  - its value-state.

  A re-read that finds the same value, an analyzer version, an unchanged rescan, or an integrity change does not send it back. The same rule governs Leave Unchanged's return and the "until the evidence changes" deferral.
- **Bindings:**
  - `evidence.question_binding_from` binds the content id, the observed value(s) and the state.
  - B16/B17 bindings (analyzer-result ids only) are resolved to values when loaded (`evidence.resolve_bound_values`).
  - A binding whose ids are gone falls back to comparing ids, so it is never silently current.
- **`plans._metadata_obsolescence`** compares the item's frozen observed value and state, not ids. Integrity changes still obsolete a locked revision ("integrity evidence changed").
- **`metadata.observe`:**
  - a location the last scan did not find is `unavailable`, even when its old readings remain;
  - a conflict lists its values in reading order, whatever history is loaded.

### Bulk Confirm on the Metadata queue

- **Registry:** `registry.METADATA_CONFIRM`, with member kind `metadata_question`.
- **Preview** (`bulk.preview_metadata_confirm` / `classify_confirm`), with five buckets:
  - will confirm — the same kind and value, re-recorded on fresh evidence;
  - already current;
  - conflict — the readings disagree;
  - blocked — the file is gone or its reading is stale;
  - not applicable — there is no decision.
- **Commit and undo:**
  - `store.commit_bulk` judges a metadata batch on `evidence.metadata_mark`, which adds the newest analyzer result to the evidence mark.
  - `undo_batch` re-applies each superseded decision exactly as recorded, so an undo returns every question to where it was.
- **The queue page:** a check column, Space, Check all shown, Clear checks, **Confirm...** (scope: the checked questions, or every question the filters list), and **Batches...**. Batch members are labelled "file -- field (question #n)".

### The queue's cold open

- `refresh.MetadataCache` is an in-session cache keyed by the record watermarks plus the metadata evidence mark:
  - a full load after an evidence change;
  - a catch-up above the watermarks otherwise.

  It is rebuildable and never authoritative, and it is dropped with the connection.
- `evidence._location_facts` computes the per-location work once per file, not once per question.
- `_analyzer_readings` reads each analyzer's newest reading on the current observation, and reads older observations only where there is none. The questions are identical to the old read (checked in the suite and on the real copy).

### The research's gates

- **M3:** `Docs\Specifications\PLAN_FINGERPRINT_SPEC.md`, with `Resources\Phase3\fingerprint_vectors.json` (9 vectors).
  - `plans.plain` / `canonical` use explicit encoders: anything that is not JSON-native is refused. They write unpaired surrogates in their generalized form and refuse NaN.
  - `plans.verify` and `store.verify_plan_revision` verify under the payload schema recorded in the revision.
  - **No existing fingerprint changed**, so the schema stays `/1`.
- **M5:** `Scripts\p3_mutation_check.py`: 32 source mutants in a sandboxed copy, with a control run of each slice and a before/after hash of the real tree. All 32 are killed.
- **M6:** state machines in ARCHITECTURE.md, with invalid-transition tests. `store.plan_status_from_events` / `plan_status_drift` rebuild the plan status cache (S6).
- **S3:** `store.export_current_state` / `current_state_rows`, and **Export current state...** on the Decide page. It goes through Phase 2's CSV writer and formula guard.
- **S4:**
  - Every revision's basis records `registry_version`, `registry_digest` (new `registry.REGISTRY_VERSION` / `registry_digest()`), `resolver_version` (`resolve.RESOLVER_VERSION`), `evaluator_version` (`plans.EVALUATOR_VERSION`), `payload_schema` and `analyzer_mark`.
  - The lock refuses a revision whose files were analyzed again after its preview.
  - Older bases still read, lock and verify.
- **S5:** `model.path_key` is the stated path-matching rule: separators, the extended prefix, the trailing separator, NFC, simple lower-casing, and the segment boundary. Root policies compare through it too.

### Versions

- `APP_VERSION` = `Phase2.VERSION` = **B18**; schema **16**; `fo_db.MODULE_VERSION` 1.13.0.
- `Phase2.core` / `self_check` require 16. `self_check` still checks 34 modules (no new runtime module).

### Tests

| Suite | Checks |
|---|---|
| `p3_fingerprint_check.py` **(new)** | 31 |
| `p3_closeout_check.py` **(new)** | 57 |
| `p3_encryption_check.py` **(new)** | 29 |
| `p3_revalidation_check.py` **(new)** | 59 |
| `p3_mutation_check.py` **(new)** | 52/52: 32 mutants killed, 19 controls clean, the tree untouched |

Changed suites:
- **`p3_metadata_check`:** three B16/B17 assertions now assert the Q2 rule, and a value-changed counterpart was added (133).
- **`p3_plan_check`:** the keeper-planned-away floors (81).
- **Schema and version pins** updated in `p3_regression`, `p3_visual_check` and `p3_metadata_view_check`.
