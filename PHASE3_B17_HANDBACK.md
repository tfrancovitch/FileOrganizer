# Phase 3 handback — B17 (finishing metadata: the Metadata page and its evidence)

**Against:** `FileOrganizer-Phase3\phase_3_handoff_b17\02_B17_HANDOFF_PLAN.md` and
`01_B17_SYNTHESIS.md` (the B16 review; P3.R8 §6, §22, §2.8/§33, §34, §47–§54; P3.R10 §10, §16, §20–§22)
**Date:** 2026-09-26
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B17\`, a git worktree of branch
`phase3-b17` created from tag `phase3-b16`. Not committed, not tagged, not pushed.
`CHANGELOG-B17.md` says what changed; this document says how it measures against the plan.

---

## 0. Tag check (task 0)

- **B16 was uncommitted.** I committed it exactly as handed back as
  **`d65863d`** and put the annotated tag **`phase3-b16`** on it. This is the
  step the plan names, and every earlier round took it too.
- **Both tags verify.** Clean checkouts of `phase3-b15` (`98a1b1a`) and
  `phase3-b16` (`d65863d`), every blob as git stores it, pass
  `Test-Installation.ps1` with "manifest verified".
- The seven input hashes in `03_reference\README.md` match the files.

## 1. Review fixes

| # | Result | Receipt |
|---|---|---|
| 1.1 (R1) | `store.active(scope=)` and `active_for(..., scope=)` take `SCOPE_RETENTION` (the default, as in B16), `SCOPE_METADATA` or `SCOPE_ALL`. An unknown scope is refused. The two product call sites name theirs: `review.py` `active(SCOPE_RETENTION)`, `imagegame.py` `active_for(VISUAL_PAIR, …, SCOPE_RETENTION)`. `still_active` takes ids, so it has no scope. No behaviour change. | `p3_metadata_view_check` §1: retention excludes a metadata decision on the same location, metadata includes only it, all has both, an unknown scope is refused, and both call sites are read. Every earlier suite keeps its count. |
| 1.2 (R2) | **Model load at 9,808 questions, real copy, three runs:** see the line below. B16's "+0.13 s" was the increment; its "0.39 s" was the slice loaded alone. They measure different things. | §3 below. |
| 1.3 (R3) | The four `/` entries (`Scripts/ContentExtraction.py`, `OfficeAnalysis.py`, `RawImageAnalysis.py`, `p2_acceptance.py`) now use `\`. Every entry uses `\`. | The manifest verifies in the worktree and in a clean materialization of the would-be commit. |

**Model load (R2):**
- with no metadata questions: 0.42–0.46 s;
- with 9,808 questions: 0.63–0.68 s, so **+0.20–0.25 s incremental**;
- the metadata slice loaded alone (`load_metadata`): 0.42–0.46 s **standalone**.

## 2. Evidence: signatures and encryption

| # | Result |
|---|---|
| 2.1 | **PDFAnalysis 1.2.0, OfficeAnalysis 1.4.0** record `SignatureState`, following synthesis §5. **PDF:** `/FT /Sig` with `/V` → signed; `/Perms /DocMDP` → certified; an empty placeholder → unsigned; unreadable → unknown. **OOXML:** an `_xmlsignatures/` part or the origin relationship → signed, otherwise unsigned. **OLE2:** a `_signatures` / `\x05DigitalSignature` / `_xmlsignatures` stream → signed, otherwise unsigned. **RTF as .doc** → not_applicable. ODF has no analyzer, so it stays unknown. Presence only; nothing is validated. |
| 2.2 | **A promoted column**, `analyzer_result.signature_state` (migration 015: additive, nullable). **Why:** 005's own promotion rule says a field becomes a column when two or more analyzers populate it, and PDF and Office both do. NULL (a result from before B17) is unknown, never unsigned. |
| 2.3 | **`source_not_encrypted`**: hard, class `integrity`, blocked reason **`encrypted`**, between the signature and hard-link checks. `metadata.blocked_by_evidence` answers `encrypted` with no plan. It reads what Phase 2 already stores (synthesis §6): the PDF's `IsEncrypted`, and the analyzers' "password-protected document" wording (JC 2). Missing evidence → unknown, which blocks. Families with no scheme → not applicable. |
| 2.4 | `evidence._integrity_evidence`: the newest PDF / Office attempt on the file's current observation speaks. It sets `signed_or_certified`, `encrypted` and `signature_state`, plus the not-applicable checks for RTF. An absent value or a pre-B17 row → **None**. `_settle` and the Decision engine are unchanged. |
| 2.5 | All shown through the **real analyzers, the store and the plan**, on files built inside the test (`p3_metadata_view_check` §2), plus one real third-party file. Detail below. |
| 2.6 | The real-scale run on a copy: §3 below. |

**Task 2.5 samples** (the file list, then every outcome through the plan):

- **PDFs:** a filled `/Sig` field; an empty placeholder; a certified file
  (`/Perms /DocMDP`); one needing a password; one with an owner password
  only; a plain control.
- **Office:** a `.docx` with an `_xmlsignatures/` part; a real encrypted
  `.docx` (msoffcrypto, ECMA-376 agile); a plain `.docx`.
- **Legacy:** OLE2 `.doc` files with and without a `_signatures` stream, built
  as valid CFB files by the test; RTF saved as `.doc`.
- **A result as B16 wrote it:** `signature_state` NULL.
- **A real signed file:** `C:\FOTest`'s `digitally_signed_3D_Portfolio.pdf`
  (openpreserve/format-corpus, commit `366f068`, SHA-256 checked against its
  `PROVENANCE.json` entry).

| Evidence | Plan item |
|---|---|
| filled / certified / signed `.docx` / signed `.doc` / the real portfolio | **blocked, `signed_or_certified`** |
| password PDF / owner-password PDF / encrypted `.docx` | **blocked, `encrypted`** |
| plain PDF, empty placeholder, plain `.docx`, RTF `.doc` | **`unsupported_action` / `writer_unsupported`** |
| pre-B17 row (NULL) | **`blocked_evidence` / `source_not_signed_or_certified`** |
| plain OLE2 `.doc` | **`blocked_evidence` / `source_not_encrypted`** (JC 4) |

**No `PROVENANCE.json` in `C:\FOTest` lists a signed legacy OLE2 file, and I
could not make a genuinely signed one here.** The OLE2 stream check is
verified on a synthetic compound file carrying the stream. Against a real
Office-signed `.doc`, `.xls` or `.ppt` it is **unverified**.

## 3. Real scale (task 2.6)

- **The copy:** a SQLite backup of your real project, migrated 8 → 15, kept
  under a scratch app root's own `Projects\` folder, deleted afterwards.
- **Your original is unchanged:** schema 8, 118,329,344 bytes, and the same
  mtime before and after. The size and mtime of all 4,904 real documents are
  identical before and after.
- **Questions:** 4,904 PDF / Office documents present; B16's 9,808 questions
  re-opened (a desired Creator on each, plus its Title question).
- **Re-analysis:** the real PDF and Office analyzers read every document once,
  read-only, in 187–289 s. Office analyzed 3,362 and could not read 11; PDF
  analyzed 1,529.

| `signature_state` | PDF | Office |
|---|---|---|
| signed | **1** | 0 |
| certified | **5** | 0 |
| unsigned | 1,523 | 2,415 |
| not_applicable (RTF as .doc) | — | 947 |
| none (analysis failed) | 0 | 11 |

- **Encrypted (password-protected PDFs):** **14**. Four of them are also
  signed, so the evidence-level projection names signature first.
- **Encryption unknown:** 352 legacy OLE2 files (JC 4), plus 2 OOXML files
  the analyzer could not read.
- **Cloud-only skipped:** 0. Your copy's documents are all local.
- `blocked_by_evidence` (question → first definite block):
  `signed_or_certified` 12 (6 files × 2 questions), `encrypted` 20.

**Item states.**
- **Immediately after re-analysis:** 4,902 of 4,904 items read
  **blocked / `decision_current`**. The re-read moved every decision's
  evidence binding, so each needs revalidation. That is correct (R8 §53, the
  plan's trap), and nothing suppresses it.
- **After a person revalidates** (re-recording each decision, 17 s), the
  items total 4,904:

| State / reason | Items |
|---|---|
| `unsupported_action` / `writer_unsupported` (F13's shape) | **4,534** |
| blocked / `signed_or_certified` | 6 |
| blocked / `encrypted` | 10 |
| `blocked_evidence` / `source_not_encrypted` (legacy OLE2) | 341 |
| `blocked_evidence` / `source_not_signed_or_certified` (unreadable) | 13 |

Before B17 the same collection had every document item blocked on "is it
signed?" (B16 JC 1). Now 92% show the true reason: there is no writer.

**Timings** (task 3.6; target under 1 s for both):

| | Time |
|---|---|
| Queue page open, 9,808 questions, best of three (warm) | **0.84 s** |
| Queue page open, first in a full run (cold) | 0.84 s, 0.99 s and 1.14 s across three full runs (9,806 rows in the second, 4,904 in the others) |
| File page open | **0.16–0.18 s** |
| Plan build (4,904 metadata items) | 0.49–0.59 s |
| Recording 9,808 questions | 32–34 s (about 3.4 ms per operation) |

The queue's cost is mostly the metadata slice load (0.46 s). One cheap fix
went in: the page computed the review rows twice. **The cold first open can
exceed 1 s.** I did not build a cache for it (see Q5).

## 4. The Metadata page (task 3)

| # | Result |
|---|---|
| 3.1 | **One file:** the header (path, family, current observation, present / not present / cloud-only). One row per semantic field: observed value and value-state in the product's words (a conflict lists both readings), desired value, write capability, blockers, review state, and the expected action. The expected action is the Plan Builder's own `metadata_items` over this file alone, e.g. "Update Source Metadata -- blocked: writer_unsupported". The source label carries the synthesis's wording. The actions are listed below. Project metadata, labelled as stored in the project. The history. |
| 3.2 | **The queue:** `metadata.queue` with the population in JC 7, filters (field, family, value-state, blocker), counts, "Currently clear" when empty, and a row opens its file. |
| 3.3 | **Deferral:** Build 3's events and triggers. A question's events are loaded apart from the retention model, so **`routing.py` is untouched**. `store.defer_metadata` names the question in the same operation; a decision ends an open deferral in its operation; `metadata.queue` honours it until the trigger fires. |
| 3.4 | **Entry points:** the side panel's **Metadata**; the Details pane's **Metadata for this file**; **Open this file's metadata** for a plan's metadata item. No other top-level area (R10 §21). |
| 3.5 | **Headless tests** (`p3_metadata_view_check` §4): every action through the window, and every assertion the plan lists. Detail below. |
| 3.6 | §3 above. |

**Actions on a field (3.1):**
- **Save desired value.** Typed; invalid input is refused with the reason.
- **Clear...** Its own confirmation, default No; never the default action.
- **Leave source unchanged.**
- **Defer...** Build 3's dialog.
- **Undo.** A withdrawal, or ending the deferral.

**What the headless run (3.5) shows:**
- Each action records the right kind, with provenance: a person's `decision`
  operation, the dashboard, the window's session, a command id.
- An invalid date is refused with the reason.
- Clear asks first (default No; No records nothing).
- Undo ends a deferral, then withdraws a decision.
- Tags are added and removed, and a note is set.
- An unknown field (an encrypted `.docx`'s creator) offers Save, never Clear, by default.
- Viewing creates no question row.
- The queue:
  - a named question is listed;
  - Left Unchanged and an open deferral leave it;
  - after the file is edited, rescanned and re-analyzed, Left Unchanged
    returns (needs revalidation), the "until the evidence changes" deferral
    returns, and the desired Creator needs revalidation.
- Filters work, and an empty project says "Currently clear".
- **Zero source reads (open, stat, lstat, scandir, listdir under the corpus)
  and zero writes outside the project**, around both window sessions. The
  rescan and re-analysis between them are Phase 1/2 runs, outside the guards.

## 5. Plan notes (task 4)

- **4.1 (S1):** "If written, this file will leave exact-duplicate group <n>."
  It is a note in the item's estimated effect: not a precondition, not a
  block, not fingerprinted. The Plan page's item detail shows it, and the
  preview counts it.
- **4.2 (S2):** a **warning** (`no_removal_metadata_overlap`, class
  `collision`) on both the removal and the metadata update of the same
  location, each naming the other's item number. The removal stays ready;
  items are neither reordered nor dropped; no other item carries either note.
- **Done-when:** both tested. **A revision without metadata items
  fingerprints exactly as B16 does.** The suite materializes the
  `phase3-b16` code from git (`git archive`), runs it and this build on the
  same retention model, and compares the fingerprints: identical.

## 6. Regression and bookkeeping (task 5)

- **Versions:** B17; schema 15; `MODULE_VERSION` 1.12.0.
- **`self_check`:** 34 modules (`Phase3.metadata_view` added).
- **Docs:** README (the Metadata page), STAGE_MAP (the page; what Analyze
  records), ARCHITECTURE, PROJECT_PLAN, BASELINE, and this handback.
  PROJECT_PLAN covers the header, §3 row, §4a B17, §6 (next: **B18, the
  Phase 3 closeout**, pointing to the coverage review) and §7 tree.
- **Manifest:** regenerated with 208 entries, all `\`, verified in the
  worktree and in a clean materialization of the would-be commit tree.

**Suite counts:**

| Suite | Result |
|---|---|
| `p3_metadata_view_check` (new) | **54/54** |
| `p3_metadata_check` | 132/132 |
| `p3_fixture_check` | 101/101 (F01–F16 green) |
| `p3_plan_check` | 80/80 |
| `p3_regression` | 345/345 |
| `p3_refresh_check` | 17/17 |
| `p3_survey_check` | 85/85 |
| `p3_visual_bulk_check` | 79/79 |
| `p3_visual_check` | 138/138 |
| `b6_regression` | 73/73 |
| `p2_regression`, `p2_dashboard_check` | all |
| `self_check` | ready (schema 15, 34 modules) |
| The lab's `validate_fixtures.py` | 16/16 |

## 7. Judgment calls (flag these)

1. **Signature storage is a promoted column** (2.2): 005's two-analyzer rule.
2. **A PDF needing a password now fails with the Office analyzer's wording.**
   The message is "password-protected document (an encrypted PDF; no password
   is tried)"; pypdf's "File has not been decrypted" is no longer used.
   Phase 3 reads both phrasings, so a PDF analyzed before B17 still counts as
   encrypted. The file is still an analyzer error (nothing is read), exactly
   as an encrypted Office package always was.
3. **A PDF that embeds a signed or certified PDF is "signed".** Your real
   sample is a portfolio whose container has no signature, but one embedded
   document ("Design of Landing Gear.pdf") is **certified**. The detector
   finds it. Presence blocks (P3-A115), so the container reads signed. The
   detector reads at most 20 embedded PDFs; past that, or if one is
   unreadable → unknown.
4. **Legacy OLE2 encryption is unknown**, and blocks: 341 real items. A `.doc`,
   `.xls` or `.ppt` can have readable summary properties while its content is
   RC4-encrypted, and Phase 2 records no flag for that. An opened OOXML
   package counts as not encrypted, because password-to-open packages are
   refused before they are read. See Q4.
5. **RTF saved as `.doc`: neither the signature nor the encryption check
   applies** (the analyzer names it RTF). Those 947 real items plan as
   `writer_unsupported`.
6. **Encrypted before signed?** The precondition order is signed, encrypted,
   hard-linked. A signed and encrypted file reports `signed_or_certified`
   first; its item detail lists both.
7. **The queue's population** (synthesis §3's recommendation, taken whole):
   - a question a person named and has not decided;
   - a drifted decision (needs revalidation);
   - a fired deferral;
   - a field observed `conflicting` that no question names yet, surfaced
     without creating a row (`evidence.conflicting_fields`).

   Left Unchanged and an open deferral stay out. It is not every file × every
   field. Your collection has no conflicting field: each document is read by
   one analyzer.
8. **Metadata deferral reuses Build 3's events** without making metadata a
   routing target. The events are filtered at load, as B16 filtered the
   decisions. Deferring names the question (a person recording something);
   viewing never does.
9. **S1 is a note, S2 a warning precondition**, each attached only where it
   applies. That is what keeps retention fingerprints identical.
10. **The fixture lab predates encryption evidence.** Its synthetic files load
    as not encrypted; the loader says so. F13 and F14 still pass.
11. **The page loads synchronously on the main thread.** Its loads are
    0.16–1.1 s, so no worker thread is needed. That avoids the Tk-on-a-worker
    trap entirely.
12. **Re-analysis sends every decided question back for revalidation**: 4,902
    of 4,904 real items. That is correct, but there is no "confirm all" for
    metadata. Revalidating 4,904 decisions was scripted here. See Q2.

## 8. Questions

1. **Which `PROJECT_PLAN.md` is the source of truth?** (review R4) Flagged,
   not resolved: I updated the worktree copy only, as every round has.
2. **Bulk metadata before the closeout?** (R8 §20 / §49) Not built. It would
   fall out of B10's batch machinery fairly cheaply:
   - a `metadata_question` member kind;
   - frozen scope over queue rows or checked files;
   - the same `commit_bulk` guard.

   The real re-analysis above shows the first use: confirm or re-apply a
   desired value on thousands of drifted questions. Want it in B18, or later?
3. **"Organize" (move / rename intent) as a Phase 3 decision kind** before
   Phase 4 is scoped? The research didn't ask for one; `move_location` and
   `rename_location` exist only in the plan vocabulary.
4. **Legacy OLE2 encryption evidence.** The Word FIB `fEncrypted` bit, the Excel
   `FILEPASS` record and the PowerPoint `CryptSession` would turn 341 unknowns
   into known. Is that a small analyzer addition worth making?
5. **The queue's cold open (up to 1.14 s)** is the metadata slice load. Leave
   it, or cache it in the persisted-projection work you deferred?
