# B16 — Phase 3, Build 9: project metadata and desired source metadata

**Date:** 2026-09-26
**From:** B15 (tag `phase3-b15`, commit `98a1b1a`), on branch `phase3-b16`
**Handoff:** `Phase3_Handoff_Build9_1.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build9\`
(research synthesis grounded in P3.R8 and P3.R10 §4.6/§6-§8/§16; plan)
**Handback:** `PHASE3_B16_HANDBACK.md`

B16 lets a person record what they want a file's metadata to say, and what
the project itself should hold about a file, without anything being written
to any file. Desired source metadata becomes an **Update Source Metadata**
plan item that stays blocked, because no metadata writer exists and this
build certifies none (R10 §16). Project metadata (tags, a note, an owner, a
review priority, a retention rationale) lives only in the project and is
never a plan item. Schema 14.

**Before the build:** B15 was committed as `98a1b1a` and tagged `phase3-b15`
(annotated); clean checkouts of `phase3-b14` and `phase3-b15` both pass
`Test-Installation.ps1`. The handoff assumed the tag; nothing needed fixing.

## What is new

### The Decision side needs no schema change

`p3_decision.decision_kind` and `target_kind` carry no CHECK (migration 009
left them to the registry), so the four metadata kinds and the
`metadata_question` target are registry data in the one generic Decision
engine (R10 §4.6). They are recorded, superseded and withdrawn like every
other decision row.

### Schema 14 — `Scripts\Database\migrations\014_phase3_metadata.sql`

| Table | What it holds |
|---|---|
| `p3_metadata_capability` | **The metadata capability record** (R8 §16-18, narrowed): file family × semantic field → read support, write support, status. Seeded with what Phase 1/2's analyzers read (PDF and Office Title / Author / creation date, RAW DateTimeOriginal, audio Title): every row `read_only`, write `none`. No row says `supported`. A family/field with no row is `unknown`. No writer, version or strategy columns: those are later work, added when there is something to put in them. |
| `p3_metadata_question` | **The `metadata_question` target** (R10 §7): one file location × one semantic field, with a stable id. Identity only. What Phase 2 observed, the capability that applies and the integrity flags are read from the evidence when asked, never copied here. |

### The vocabulary — `Scripts\Phase3\registry.py`

- **The semantic field registry** (R8 §3-4): `title` (text), `creator`
  (person), `content_created` (date), `description` (text), `keywords`
  (keyword list), `copyright` (text). Typed values: an ISO date, a list of
  distinct keywords, non-empty text. A semantic field is not a physical tag;
  the mapping to tags is R8 §25's, a writer's concern, not built.
- **Project metadata fields**, a separate registry: tags, category, note,
  review priority (low / normal / high), retention rationale, owner. Labelled
  "Stored in the File Organizer project -- never written to the file".
- **Four decision kinds:**
  - `project_metadata_set` targets a **file location** (`{"field", "value"}`;
    conflicts per field).
  - `desired_source_metadata_set`, `desired_source_metadata_clear` and
    `leave_source_metadata_unchanged` target a **metadata question** and share
    one conflict domain: a later one supersedes the earlier.
  - An empty desired value is refused. Clearing is its own decision (P3-A111).
- **Observed value-states** (R8 §5): known, no_value, unknown, unsupported,
  unavailable, stale, conflicting.
- **Capability statuses:** supported, experimental, read_only, blocked,
  unknown. Only `supported` satisfies `writer_supported`.
- **File families** by extension, and which families can carry a signature
  (PDF, Office, ODF).
- **Plan preconditions for `update_source_metadata`:** `writer_supported`
  (hard, from the capability record), `source_not_signed_or_certified` and
  `source_not_hardlinked` (hard, a new `integrity` class), a cloud-only
  warning. Build 8's `filesystem_capability_supported` placeholder and its
  empty `METADATA_WRITERS` profile are gone from metadata items.
- **`blocked_reason`:** the settling precondition's key, except where the
  research names the reason: `writer_unsupported`, `signed_or_certified`,
  `hardlinked_generic_write` (F13, F14).

### Metadata — `Scripts\Phase3\metadata.py` (new, pure)

- **`observe`:** one field's value-state from analyzer results. The newest
  attempt on the current observation speaks; an older observation only makes
  it `stale`. Two readings that disagree are `conflicting`, with both values,
  and no winner is picked (P3-A110).
- **`writer_check`:** only `supported` passes. `experimental`, `read_only`,
  `blocked` and `unknown` are all `unsupported`. No partial credit.
- **`integrity`:** a signed or hard-linked file fails; a known single name or
  unsigned file passes; missing evidence is `unknown` (P3-A91). Signature
  checks are not applicable to families with no recognised scheme.
- **`blocked_by_evidence`:** the "Blocked by Evidence" projection (R10 §6).
  Question → `signed_or_certified` | `hardlinked_generic_write`, answered from
  the evidence flags with **no plan in existence** (F14).
- **`review` / `queue`:** the data a metadata review surface needs:
  value-state and value, capability, writer, blockers and unknowns, the
  current decision and desired value, review state (undecided, desired, left
  unchanged, needs revalidation). The Metadata Review queue holds the
  undecided and the drifted. **Left Unchanged stays out until the evidence
  materially changes** (R8 §53): the file observed again, re-read by an
  analyzer, or its value-state changed.
- **`project_metadata`:** the project's own metadata per location, with the
  label.
- Nothing here makes a decision. An `unknown` observation never becomes a
  clear.

### Evidence — `Scripts\Phase3\evidence.py`

- **Metadata decisions are kept out of the duplicate and pair model.**
  `load_decisions` reads the retention and review kinds by default (`metadata=True`
  reads the metadata kinds, `None` every row); `withdrawals_after` likewise.
  A note on a redundant copy is not a Keep, and a desired Creator is not an
  orphaned decision. Everything Builds 1-8 compute is unchanged.
- **`load_metadata`:** every question with what Phase 1/2 says now: the value
  from `analyzer_result` (promoted Title / Author / creation-date columns;
  latest attempt per analyzer on the current observation), `hardlinked` from
  `file_state.hard_link_count` (> 1 true, 1 false, NULL unknown), `signed`
  always unknown (Phase 1/2 records no signature evidence), plus the
  capability record and every metadata decision. `load_evidence` attaches it
  as `ev.metadata` and loads each question's location.
- **`location_metadata`:** every field of one file as it stands, asked or not,
  asking nothing.
- **`question_binding`:** Build 1's ids-only pattern. The question, the file's
  current observation, the analyzer results the value came from, and the
  value-state word.

### The store — `Scripts\Phase3\store.py`

- **`record_metadata_decision`:** one operation names the question (creating
  it the first time) and records the decision. Project metadata goes on the
  file location directly.
- **`metadata_question`:** read or create a question.
- **Validation:** the question must exist, and a desired value must match
  its field's type.
- `counts()` adds `metadata_questions`. The journal lists questions.

### The Plan Builder — `Scripts\Phase3\plans.py`

- **`build`** now also makes one `update_source_metadata` item per question
  with an active desired set or clear, after the retention items. A revision
  with no metadata decision numbers and fingerprints exactly as Build 8's did.
  `project_metadata_set` and `leave_source_metadata_unchanged` never make an
  item; they are counted ("Never plan items").
- **Each item's evidence preview:**
  - target present;
  - decision current (the question's evidence has not moved);
  - `writer_supported` from the capability status;
  - the two integrity checks, the same ones `blocked_by_evidence` answers;
  - a cloud-only warning.

  Build 8's `_settle` is unchanged: fail, then unknown, then unsupported. The
  preview says metadata items are "planned only".
- **`obsolescence`** judges a metadata item on its own dependencies:
  - its decision withdrawn or superseded;
  - its file observed again or re-read;
  - its question gone;
  - the capability record's status changed (`metadata capability changed`);
  - its integrity evidence changed (`integrity evidence changed`).
- `PlanItem.blocking` names the precondition that settled a blocked item.
- `logical_item` no longer makes metadata items (only decisions do).

### The window — `Scripts\Phase3\planview.py`

The Plan page names a metadata item's source ("metadata question #n"), shows
the desired value beside the observed state, and gives the settling reason in
the items list. No new screen. The handoff asks for the data a metadata
review surface needs, not the surface.

### Fixtures — `Resources\Phase3\fixture_lab\`

F13 (Metadata_Desired_Unsupported) and F14 (Metadata_Integrity_Blockers) are
copied from the research lab and verified against its manifest (which already
listed them). The product lab now ships all sixteen, and the lab's own
`validate_fixtures.py` reports 16/16.

### Versions

`fo_db.APP_VERSION` = `Phase2.VERSION` = **B16**; `fo_db.MODULE_VERSION`
1.11.0; schema **14**. `self_check.RUNTIME_MODULES` += `Phase3.metadata`
(self_check reports 33).

## Tests

- **New:** `Scripts\p3_metadata_check.py` (132).
  - The vocabulary and schema: no CHECK on the decision columns; the record's
    vocabulary; no `supported` row.
  - All seven value-states.
  - Writer and integrity checks, and the evidence-level projection.
  - Plan items in every shape: F13's two fields, each blocker, unknown
    blocks, a supported writer would pass, no partial credit.
  - Necessity 10: neither kind ever makes an item, in every combination.
  - Unknown manufactures nothing.
  - The review data and queue.
  - Obsolescence by each dependency and not for unrelated changes.
  - A real project with real .docx/.pdf/.jpg files and a real NTFS hard link
    through the real analyzers: Phase 1/2 readings, the store, the
    duplicate model unchanged, the plan, **the §4.1 lock with a blocked
    metadata item**, obsolescence through the detector, drift after an edit
    and rescan, the journal.
  - Zero source access; the Plan page.
- **`p3_fixture_check.py`:** F13 and F14 through the product's code
  (84 → 101 checks), metadata included in the shuffled-rows determinism check.
- **`p3_plan_check.py`:** the metadata placeholder checks now describe
  Build 9's shape (80).
- **`p3_regression.py`:** schema 14, eighteen p3 tables, fourteen decision
  kinds, B16 (345).
- **`p3_visual_check.py`:** schema 14.
- Every other suite is unchanged and green.
