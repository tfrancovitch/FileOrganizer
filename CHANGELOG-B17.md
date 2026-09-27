# B17 — Phase 3: finishing metadata (the Metadata page and its evidence)

**Date:** 2026-09-26
**From:** B16 (tag `phase3-b16`, commit `d65863d`), on branch `phase3-b17`
**Handoff:** `FileOrganizer-Phase3\phase_3_handoff_b17\` (the B16 review,
synthesis, plan; decisions of 2026-09-26)
**Handback:** `PHASE3_B17_HANDBACK.md`

B17 completes Build 9 as the research defines it (R10 §16). It adds:
- **the Metadata Review screen**, inside the one window;
- **signature evidence** from the PDF and Office analyzers;
- **an encryption blocker**, read from evidence Phase 2 already records;
- **two plan-preview notes** from R8 (§47, §48);
- **the three review fixes** R1–R3.

Still no metadata is written anywhere, and no writer exists. Schema 15.

**Before the build:** B16 was committed as `d65863d` and tagged `phase3-b16`
(annotated), as handed back. Clean checkouts of `phase3-b15` and
`phase3-b16` both pass `Test-Installation.ps1`.

## What is new

### Review fixes

- **R1.** `store.active()` and `active_for()` take a named decision `scope`:
  `SCOPE_RETENTION` (the default, as in B16), `SCOPE_METADATA` or
  `SCOPE_ALL`. An unknown scope is refused. The product's two call sites
  (`review.py`, `imagegame.py`) name theirs. No behaviour change.
- **R2.** Model-load timing is reported standalone and incremental (handback §3).
- **R3.** `SOURCE_SHA256.csv` uses `\` in every entry. Four had `/`.

### Evidence — schema 15, `015_signature_evidence.sql`

- **`analyzer_result.signature_state`**: signed, certified, unsigned,
  unknown, not_applicable, or NULL. Two analyzers populate it, so it is a
  promoted column by 005's own rule. Presence only; never validated.
- **NULL is unknown, never "unsigned".** NULL means the result predates B17.
- **`PDFAnalysis.py` 1.2.0:**
  - A signature field (`/FT /Sig`) with a value `/V` → signed.
  - `/Perms /DocMDP` → certified.
  - An empty signature placeholder → unsigned.
  - **A PDF that embeds a signed or certified PDF** (a portfolio, an
    attachment) → signed. At most 20 embedded PDFs are read; more, or an
    unreadable one → unknown.
  - A PDF that needs a password to open is reported as "password-protected
    document (an encrypted PDF; no password is tried)", the Office analyzer's
    wording, instead of pypdf's own message.
- **`OfficeAnalysis.py` 1.4.0:**
  - A package with an `_xmlsignatures/` part, or a digital-signature origin
    relationship → signed.
  - A legacy OLE2 file with a `_signatures`, `\x05DigitalSignature` or
    `_xmlsignatures` stream → signed.
  - RTF saved as `.doc` → not_applicable.
- **`fo_analyzers`** writes the new column (`REQUIRED_SCHEMA_VERSION` 15).

### Phase 3 reads it — `evidence.py`, `metadata.py`, `registry.py`, `model.py`

- **Signature** (`evidence._integrity_evidence`). The newest PDF or Office
  attempt on the file's current observation speaks: signed / certified →
  true, unsigned → false, anything else → unknown.
- **Encryption.** A new hard `integrity` precondition, `source_not_encrypted`,
  on `update_source_metadata`; blocked reason `encrypted`. It reads evidence
  Phase 2 already stores:
  - the PDF analyzer's `IsEncrypted`;
  - the analyzers' "password-protected document" wording (and pypdf's "has
    not been decrypted", which pre-B17 PDF results carry);
  - an OOXML package the analyzer opened is not encrypted;
  - a legacy OLE2 file is unknown (its summary can be read while its content
    is encrypted).
- `metadata.blocked_by_evidence` answers `encrypted` too, with no plan.
- Families with no scheme, and RTF saved as `.doc`, are not applicable.
- `MetadataQuestion` gains `encrypted`, `signature_state`, `integrity_na` and `path`.

### Deferral for a metadata question

- Build 3's review events and triggers: until you restore it, until a date,
  until the evidence changes. `registry.DEFER_CHOICES[metadata_question]`.
- **`store.defer_metadata`** names the question and records the deferral in
  one operation.
- **A decision ends an open deferral** on its question, in the same operation.
- These events are loaded apart from the retention model
  (`evidence.load_events(metadata=...)`). **`routing.py` is unchanged**: owner
  decision Q3.

### The Metadata Review queue's population (`metadata.queue`)

The questions that need a person:
1. named and undecided (or a deferral that came back);
2. drifted (needs revalidation);
3. a deferral whose trigger has fired;
4. fields observed `conflicting` that no question names yet
   (`evidence.conflicting_fields`, listed without creating a row).

Left Unchanged and an open deferral stay out. Review states add **Clear
requested** and **Deferred**. `metadata.queue_filter` filters by field,
family, value-state and blocker. `metadata.file_review` gives one file's rows.

### The Metadata page — `Scripts\Phase3\metadata_view.py` (new)

- **The queue.** Filters, counts, and "Currently clear" when empty. Opening a
  row opens the file.
- **One file.** The header: path, family, observation, present or cloud-only.
  Every semantic field: observed value and value-state (a conflict shows both
  readings), desired value, write capability, blockers, review state, and the
  expected action. The expected action is the Plan Builder's own
  `metadata_items` over this file alone.
  - The source section is labelled "Stored in the source file. Saving
    records what you want; it creates a Phase 4 plan item and never edits
    the file."
  - **Actions:** Save desired value (typed; refused with the reason), Clear...
    (its own confirmation, default No; never the default action), Leave source
    unchanged, Defer... (Build 3's dialog, with a hook), Undo (a withdrawal,
    or the deferral's restore).
  - **Project metadata:** tags (add / remove), category, note, review
    priority, retention rationale, owner. Labelled as stored in the project.
  - **History:** the file's metadata decisions.
- **Entry points:**
  - **Metadata** in the side panel (`gui.show_metadata_review`);
  - **Metadata for this file** in a file's Details;
  - **Open this file's metadata** for a plan's metadata item (`planview.py`).
- Viewing creates no question. Nothing on the page opens, previews or stats
  a source file.

### Plan notes — `plans.py`

- **S1 (R8 §47).** A metadata item on a file in a current exact-duplicate
  group carries the note "If written, this file will leave exact-duplicate
  group <n>", in its estimated effect. Informational: not a precondition, not
  in the fingerprint. The Plan page shows it; the preview counts it.
- **S2 (R8 §48).** When a revision both removes a location and updates its
  metadata, a **warning** precondition (`no_removal_metadata_overlap`) is
  attached to both items, each naming the other. Warnings never block;
  nothing is reordered or dropped. It is attached only where it applies.
- **Retention fingerprints are unchanged.** The suite runs the `phase3-b16`
  code (from git) and this build on the same model and compares.

### Versions

`fo_db.APP_VERSION` = `Phase2.VERSION` = **B17**; `fo_db.MODULE_VERSION`
1.12.0; schema **15**. `self_check.RUNTIME_MODULES` += `Phase3.metadata_view` (34).

## Tests

- **New:** `Scripts\p3_metadata_view_check.py` (54).
  - R1 scopes.
  - Every signature and encryption outcome through the real analyzers, the
    store and the plan. The files are built in the test (filled, empty and
    certified signature fields, user- and owner-password PDFs, a signed
    `.docx`, an msoffcrypto-encrypted `.docx`, OLE2 `.doc` with and without a
    signature stream, RTF as `.doc`), plus a real third-party signed portfolio
    from `C:\FOTest` (provenance hash checked) and a B16-shaped result with no
    signature.
  - S1/S2, and the cross-build fingerprint.
  - The Metadata page headless: three entry points; every action with its
    kind and provenance; Clear never the default for an unknown field;
    viewing creates nothing; the queue (Left Unchanged out, back after
    re-analysis; a fired deferral back; filters; Currently clear). Zero source
    reads and writes around both window sessions.
- **`p3_metadata_check.py`:** its hand-built questions state their encryption
  evidence. Its real project now shows signatures recorded, and report.docx
  with F13's shape. Still 132.
- **`p3_plan_check.py`:** the precondition vocabulary includes B17's two keys.
- **`p3_regression.py`, `p3_visual_check.py`:** schema 15, B17.
- **`p3_fixture_check.py`:** the lab predates encryption evidence. Its
  synthetic files load as not encrypted, documented in the loader. 101,
  F01–F16 green.
- Every other suite is unchanged and green.
