# Phase 3 handback — Build 9 (project metadata / desired source metadata), built as B16

**Against:** `phase_3_handoff_build9\02_BUILD9_HANDOFF_PLAN.md` and
`01_BUILD9_RESEARCH_SYNTHESIS.md` (P3.R8 scoped by P3.R10 §4.6/§6-§8/§16)
**Date:** 2026-09-26
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B16\` — a git worktree of
branch `phase3-b16`, created from tag `phase3-b15`. Not committed, not tagged;
both are the owner's call after this review. `CHANGELOG-B16.md` says what
changed; this document says how it measures against the handoff.

---

## 0. The tag check (handoff §0)

- **B15 was uncommitted** when this round began (its handback left committing
  to you). The handoff assumes `phase3-b15`, so I committed B15 as **`98a1b1a`**
  and placed the annotated tag **`phase3-b15`** on it. That is the same step
  the Build 7, B14 and Build 8 rounds took, disclosed here for the same reason.
- **Both tags verify clean.** A clean checkout of `phase3-b14` and of
  `phase3-b15` (every blob as git stores it, LF) passes `Test-Installation.ps1`
  ("manifest verified").
- Nothing needed repairing, so nothing was repaired.

## 1. Synthesis §0: what the codebase already had (necessity 1)

| Question | Finding |
|---|---|
| **Does the Decision engine need a schema change?** | **No.** `p3_decision.decision_kind` and `target_kind` have no CHECK constraint (migration 009 left vocabularies to `Phase3.registry`). The four metadata kinds and the `metadata_question` target are registry data. `p3_metadata_check` §1 reads the DDL and asserts it. |
| **Does Phase 1/2 already expose observed metadata?** | **Yes, and its shape wins.** The PDF, Office, RAW and audio analyzers already promote `title`, `author` and `content_created_reported` onto `analyzer_result`, one row per analyzer per observation. Phase 2's query layer even maps `author` to a state (`known` / `null` / `unavailable` / `unknown`). Build 9 reads those rows: the newest attempt per analyzer on the file's current observation. It reads no file. |
| **Does it match the fixture lab's `evidence_metadata_question`?** | **Not directly: it needed translation**, in `evidence.load_metadata` (§4 JC 2). The lab has one pre-joined row per question: value, state, `writer_capability`, signed flag, hard-link flag. The real evidence spreads these across four places: the value in `analyzer_result` (per analyzer, per observation); the value-state derived by `metadata.observe` (latest attempt, staleness, conflicts); the capability from the new record by file family × field; the hard-link flag from `file_state.hard_link_count`. **Signature evidence does not exist in Phase 1/2 at all.** The fixture loader maps the lab's row straight onto the same `MetadataQuestion`, so the product answers F13/F14 with the same code. |
| **Did Build 8 leave a capability placeholder?** | **Yes, and it is replaced.** `registry.METADATA_WRITERS = ()` was Build 8's empty writer profile. A `filesystem_capability_supported` precondition made metadata items unsupported, and only through `plans.logical_item` (hand-built, never from a decision). Build 9 removes both from metadata items. The capability record (migration 014) and a `writer_supported` precondition replace them. `logical_item` no longer makes metadata items; only decisions do. |
| **Anything capability-shaped elsewhere?** | `Phase2\capability.py` exists, but it answers "what can this project answer", not "what can be written". It is unrelated and untouched. |

## 2. The necessities, item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Resolve §0 and report | **Done** | §1 above. |
| 2 | Semantic field registry (id, name, value type; text / date / keyword_list / other) | **Done** | `registry.METADATA_FIELDS`: title (text), creator (person), content_created (date), description (text), keywords (keyword_list), copyright (text). `VALUE_TYPES` and `validate_field_value` (ISO dates, distinct keyword lists, non-empty text). No write-mapping columns. `p3_metadata_check` §1. |
| 3 | `metadata_question` target, reusing Build 1's evidence binding | **Done** | `p3_metadata_question` (location × field, identity only). A decision's binding is Build 1's ids-only shape: the question, the location's current observation, the analyzer result ids the value came from, and the value-state word. No value is copied (§3 of the suite checks that "Alice" is not in it). |
| 4 | Capability record: family × field → read, write, status; only `supported` passes | **Done** | `p3_metadata_capability`, seeded from what the analyzers read (11 rows, all `read_only` / write `none`). A missing row is `unknown`. `writer_check` passes only `supported`; all five statuses are tested. No writer / version / strategy columns: those get added when something fills them. |
| 5 | Four decision kinds through the existing engine | **Done** | Registered in `DECISION_KINDS`. `project_metadata_set` targets `file_location` (a per-field domain). The other three target `metadata_question` and share one domain, so a later one supersedes. `store.record_metadata_decision` names the question and records the decision in one operation. Supersession, withdrawal, history and the journal are Build 1's. |
| 6 | Value-state fidelity; `unknown` never makes a clear | **Done** | `metadata.observe`: known / no_value / unknown / unsupported / unavailable / stale / conflicting, all seven reached and distinct. A conflict lists both values and picks no winner. Nothing in the product creates a decision: questions observed unknown / no value / unsupported / unavailable with no human decision give no item and no decision row. |
| 7 | "Blocked by evidence" projection, a query independent of any plan | **Done** | `metadata.blocked_by_evidence(questions)`: question → `signed_or_certified` / `hardlinked_generic_write`. Pure, over the evidence flags. The Plan Builder calls the same `metadata.integrity` for its two hard preconditions. F14 is answered with no plan code called. |
| 8 | `writer_supported` added to Plan Builder's precondition evaluation | **Done, additive** | One more hard evidence-stage key in `ACTION_PRECONDITIONS[update_source_metadata]` (with the two integrity keys), evaluated in `plans._preview_metadata`, settled by Build 8's unchanged `_settle`. No second mechanism. |
| 9 | F13's two layers: `blocked_reason` and `plan_item_state` | **Done** | `blocked_reason` is `writer_unsupported`. `registry.BLOCKED_REASONS` maps (key, result) to the research's reason word, for three pairs only; every Build 8 item keeps its key. The state is `unsupported_action`. The fixture check compares both, and checks that they differ. |
| 10 | `project_metadata_set` / `leave_source_metadata_unchanged` never make a Plan Item | **Done, tested** | Alone: zero items of any kind. Beside retention decisions (even project metadata on the copy being removed): exactly Build 8's items and fingerprint. On the real project: none. The preview counts them as "Never plan items". |
| 11 | Zero source mutation, zero source read | **Done** | A read guard (open / stat / lstat / scandir / listdir under the corpus) and a write guard around: listing a file's metadata, recording, building, freezing, locking, the detector and the Plan page. Zero hits. Every corpus byte is unchanged, except the one file the test edits to cause drift. On the real collection: zero reads under the real roots. |

## 3. Acceptance (plan §5)

| Case | Result |
|---|---|
| **F13** | The product's `plans.build` on the lab's rows: `observed_creator` "Alice", `desired_creator` "Thomas", `source_mutated` false, `plan_item_state` **`unsupported_action`**. The item's `blocked_reason` is **`writer_unsupported`**, matching the lab's own `p3_plan_item` row. Also checked: the same action, target and source decision (update_source_metadata on L1 from D1); `writer_supported` among its hard preconditions, 'unsupported' from status `read_only`; neither integrity check fires. **PASS.** |
| **F14** | `metadata.blocked_by_evidence` alone: `{"M1": "signed_or_certified", "M2": "hardlinked_generic_write"}`, `source_mutated` false. The fixture has no plan, revision or item, and the check confirms it. Both questions' capability is `supported`, so the blocks are evidence flags, not capability. Were a plan built, its items would carry the same two reasons (one set of checks). **PASS.** |
| **§4.1 lock with a blocked metadata item** | Built fresh on a real project with real files: a ready `remove_location` (an exact duplicate JPEG) plus an F13-shaped item (a desired Creator on a JPEG: no signature scheme, one hard link, writer `unsupported`). **The revision locks.** Read back after the lock, the item is present, `unsupported_action` / `writer_unsupported`. The stored fingerprint equals `fingerprint(items)` recomputed from the rows, and differs from the fingerprint without the item. The lock event's dependency set names the metadata decision, and the dependency index finds the revision from it. Withdrawing that decision makes the detector mark the revision obsolete ("source decision changed", fingerprint untouched); an unrelated metadata change does not. |
| **Necessity 10** | Above. |
| **F01–F12, F15, F16** | Unchanged and green. `p3_fixture_check` 84 → **101** (F13, F14 with their extra checks; metadata joins the shuffled-rows determinism check). The lab's own `validate_fixtures.py`: **16/16** (the product now ships the whole lab). |

**Real scale**, on a copy of the real collection (schema 8 → 14 by the
product's own migrations; the copy has since been deleted; the original is
untouched and still schema 8). The copy holds no decisions, so a desired
Creator was recorded mechanically on every PDF and Office document present
(1,529 PDF, 2,076 OOXML, 1,299 legacy Office: **4,904**), and each one's Title
question was opened too: **9,808 questions**. Nobody looked at a file.

| | |
|---|---|
| Observed Creator | 3,073 known, 1,818 no value, 13 unknown |
| Observed Title | 1,570 known, 3,321 no value, 13 unknown |
| Conflicting | none (each document is read by one analyzer) |
| Link counts | all 41,056 known (1): that scan resolved every file's identity |
| Blocked by evidence (definite) | none: nothing is hard-linked and no signature evidence exists |
| Signature unknown | 9,808 of 9,808 (PDF and Office can be signed; Phase 1/2 does not record it) |
| Plan items | 4,904 `update_source_metadata`, **every one `blocked_evidence` / `source_not_signed_or_certified`**, `writer_supported` also 'unsupported' on each (JC 1) |
| Recording | 4.1 ms per operation |
| Model load | 0.45 s → 0.58 s (the metadata slice alone: 0.39 s) |
| Build 4,904 items | 0.18 s |
| Review data / queue / blocked projection | 0.12 s / 0.06 s / 0.01 s |
| Freeze / lock / detector | 1.1 s / 1.2 s / 1.6 s |
| Retention items | identical fingerprint to the build before any metadata decision |
| Source files opened / stat'ed / listed | **0** |

## 4. Judgment calls (flag these)

1. **Missing signature evidence blocks as unknown, and outranks
   `writer_unsupported`.**
   - Phase 1/2 records no signatures. So for PDF, OOXML, legacy Office and
     ODF, `source_not_signed_or_certified` is `unknown`. Hard unknown blocks
     (P3-A91), and Build 8's `_settle` (unchanged) ranks fail > unknown >
     unsupported.
   - On real data, therefore, every document item reads *Blocked (unknown
     evidence)*, not *unsupported action*, although `writer_supported =
     unsupported` is recorded on each.
   - F13's exact shape appears on real files only where the signature question
     does not apply (the JPEG in the §4.1 test).
   - I kept the conservative rule rather than special-casing the order.
     Question 1 is the way out.
2. **The real evidence needed translation** (§1). The fixture's pre-joined row
   is assembled from `analyzer_result`, the capability record and
   `file_state`.
   - The value-state is derived, never stored.
   - The question row is identity only, so a rescan or re-analysis changes
     what a question says without anyone rewriting it.
3. **No `no_op_preserve` for Leave unchanged** (nice-to-have, declined).
   - A `no_op_preserve` is a plan item, and necessity 10 says Leave unchanged
     never generates one. The two conflict.
   - Audit visibility comes from three other places instead: the preview's
     "Never plan items: N metadata field(s) you chose to leave unchanged", the
     review data's `left_unchanged` state, and the decision journal.
4. **Metadata decisions are kept out of the duplicate / pair model at load.**
   - `evidence.load_decisions` reads the retention and review kinds by
     default. `metadata=True` reads the metadata kinds; `None` reads all.
   - Otherwise several generic sites would have counted a note as
     engagement or an orphaned decision: the resolver's "bearing" set, the
     orphan count, routing's engaged targets, the bound-location loader.
   - Proved on a real project: after metadata decisions, including project
     metadata on the very copy being removed, the duplicate projection and
     routes are byte-identical, and the detector writes nothing.
   - Side effect: `store.active()` / `active_for()` return retention
     decisions only, which is what their callers use them for.
5. **Project metadata fields.** Tags, category, note, review priority (low /
   normal / high), retention rationale, and **owner**. Owner is for R8 §54's
   case: the project records Owner = Thomas while the file keeps Author = Alice.
   A `None` value means the project holds no value for the field; undo is a
   withdrawal, as always.
6. **The semantic field registry is code; the capability record is a table.**
   - The fields are a vocabulary, validated like every p3 vocabulary.
   - The capability record is data that a later certification changes.
   - The read map covers only what the analyzers promote. Audio `Artist` is
     not mapped to Creator: which tag a field means is R8 §25's mapping
     profile, a writer's concern.
7. **One plan item per question** (file × field), not one per file. Capability
   and blockers differ per field, and F13 has one item per decision.
8. **Metadata items come after the retention items.** A revision without
   metadata numbers and fingerprints exactly as Build 8's did. B15's revisions
   and suites are unaffected.
9. **`blocked_reason` uses the research's words for three cases** only
   (`writer_unsupported`, `signed_or_certified`, `hardlinked_generic_write`).
   Every other item keeps Build 8's convention (the precondition key).
   `PlanItem.blocking` finds the settling precondition either way.
10. **Signature "not applicable" outside PDF / Office / ODF.** JPEG and the
    other image and media families have no signature scheme the product
    recognises, so the check is not applicable there. Content credentials such
    as C2PA do exist for images. See question 4.
11. **Obsolescence of a metadata item adds two reasons:** "metadata capability
    changed" (the status it was previewed with moved: a future certification
    obsoletes a plan that recorded it as unsupported) and "integrity evidence
    changed".
12. **No screen.** The synthesis reads R10 §16's "specialized review surface" as
    data. `metadata.review` / `queue` supply what R8 §51's mockup shows. The
    Plan page shows metadata items (source, desired vs observed, reasons).
    There is no UI to record a metadata decision yet: the store API only.

## 5. Nice-to-haves (plan §2)

- **`no_op_preserve` for Leave unchanged:** not emitted (JC 3).
- **Metadata Review through the routing machinery:** not taken. Routing's
  targets, events and detector are specific to groups, locations and pairs.
  Making the metadata question a routing target would touch `route_all`,
  `reconcile` and the review events, which is not cheap. The queue lives in
  `metadata.queue` instead, with the Left Unchanged rule (R8 §53) enforced
  there: out of the queue until the file is observed again, re-read, or its
  value-state changes. Proved on a real file edited and rescanned.

## 6. The carried-forward questions (plan §4)

- **§4.1, locking with blocked items:** left standing. The new case is proved
  (§3): an item no evidence refresh can unblock is locked as blocked, inside
  the fingerprint.
- **§4.2, a persisted project projection: still deferred.** Nothing was built.
  Measured anyway: the metadata slice adds 0.13 s to a model load at 9,808
  questions. That gives a materialized projection nothing new to target.

## 7. Questions for the next round

1. **Signature evidence.** Every real document item blocks on "is it signed?"
   because Phase 1/2 cannot say. Which of these do you want?
   - (a) Keep the current rule.
   - (b) A small Phase 2 evidence addition: the PDF analyzer notes signature
     fields, the Office analyzer notes a signature part. Items then read
     `unsupported_action` / `writer_unsupported`, and signed files block
     definitely.
   - (c) Declare the check for Phase 4's live preflight when evidence is
     missing.

   I'd recommend (b) before Phase 4. It is evidence, not a writer.
2. **A metadata screen.** The data a review surface needs exists. Is a UI
   round (see a file's metadata states; set / clear / leave unchanged; tags
   and notes) wanted before Phase 3 closes?
3. **Should Metadata Review join routing**, with deferral and review events,
   so it shares the Decide page's queue discipline? Or is the standalone
   queue enough?
4. **Image content credentials (C2PA)**: treat JPEG / PNG / HEIF signature
   status as unknown (blocking) rather than not applicable?
5. **Every build of the Phase 3 sequence now exists** (1–9). Is the next step
   a Phase 3 closeout, or the persisted projection / Phase 4 contract work
   PROJECT_PLAN §6 lists?

## 8. Suites on B16

- `p3_metadata_check` **132/132** (new)
- `p3_fixture_check` 101/101 (F13, F14 added)
- `p3_plan_check` 80/80 (the metadata placeholder checks now describe Build 9)
- `p3_regression` 345/345 (fourteen decision kinds; schema 14)
- `p3_refresh_check` 17/17
- `p3_survey_check` 85/85
- `p3_visual_bulk_check` 79/79
- `p3_visual_check` 138/138
- `b6_regression` 73/73
- `p2_regression` all
- `p2_dashboard_check` all
- `self_check` ready (schema 14, 33 runtime modules)
- The lab's `validate_fixtures.py`: 16/16
- `Test-Installation.ps1`: the regenerated `SOURCE_SHA256.csv` (203 files) verifies, in this folder and in a clean checkout of the branch's tree.
