# The File Organizer — Stage Map

**What this is:** every stage the product runs, in order, with what it reads, what
it writes, and whether it opens a file. One click is rarely one process, and this
is the reference for which processes a click actually starts.

**Last verified:** 2026-09-15, against build B7 running from
`FileOrganizer-Phase1-RC-B6.1` (the folder name is stale; see its `BASELINE.md`).

**Where the clicks are now.** Every stage below is started from the Dashboard
(`Scripts\Phase2\gui.py` + `hub.py`) through the blocking runner
(`Scripts\Phase2\runner.py`), which drives `RunCoordinator` exactly as the old
dashboard did. The stage keys, run kinds and tables are unchanged; what changed is
that every run starts on its click, measures its estimate as its first step and
logs it, asks "Begin now?" only when the estimate exceeds 15 minutes, owns the
window while it runs, and has a Cancel. `Dashboard.py --classic` still reaches the
pre-merge screens.

**Companion documents**
- `FileOrganizer-Phase2\The_File_Organizer_Project_Plan_Phases_1-8plus.docx` — the phase model
- `FileOrganizer-Phase2\The_File_Organizer_Phase_2_Living_Project_Plan_P2.11_TESTING_v1.3.md` — current Phase 2 plan
- `FileOrganizer-Phase2\CLAUDE_WORKFLOW_DECISIONS.md` — the workflow decisions this map reflects

---

## Reading this map

| Column | Meaning |
|---|---|
| **Stage key** | The identity recorded in `run_stage`. Historic `.ps1` names are kept deliberately: they are the accepted identity across every run record since R2, and the stage's `command` field records which engine actually ran. |
| **Opens files?** | Whether the stage reads file *contents*. This is the main driver of how long a stage takes. |
| **Writes** | The evidence tables the stage populates. |

Four **run kinds** group the stages: `prescan`, `duplicate_analysis`,
`exhaustive_identity`, `content_analysis`. Each is one row in `run`.

---

## Run kind 1 — `prescan`

**One button. Four recorded stages.** This always runs in full; the last two are
close to free and are what make the next screen's numbers possible.

| # | Stage key | What it does | Opens files? | Writes |
|---|---|---|---|---|
| 1 | `PreliminaryInventory.ps1` | Walks the source roots recording name, size, dates, attributes, **and file type**. Records unreadable paths rather than failing. | **No** — metadata only | `file_path`, `file_observation`, `inventory_scan` |
| 2 | `InventoryIngest` | Persists the walk into the current-state projection | No | `file_state` |
| 3 | `PotentialDuplicates.ps1` | Groups files by size. Anything with a unique size **cannot** have a duplicate and is set aside. | No — arithmetic on data already held | size-group candidates |
| 4 | `TimeEstimates.ps1` | Samples up to 15 real files (capped at 50 MB), measures throughput, applies a pessimistic factor | Yes — a bounded sample | estimates in `settings.json` |

**Stopping it.** Cancel is checked between directories. The files walked so far
are recorded; the scan row is `interrupted` (the schema's word for gaps that are an
artefact of the scan); **nothing is marked missing** on the strength of a walk that
did not finish; the run is `cancelled`; the hub says "at least N files" and offers
the Pre-Scan again. Roots not yet started are not scanned. There is no resume — the
next Pre-Scan walks from the beginning.

**Scanning again.** The folder changes after its scan, and until the next scan the
inventory cannot know. **Scan again**, beside the word SCANNED on the summary, runs
stages 1–4 over the project's own roots as one more `prescan` run. A file seen for
the first time becomes `present`; a file the completed walk did not see becomes
`missing` (its record, observations and results are kept); a file whose size or
modified time changed gets a new current observation, and everything measured on
the old one — fingerprint, analysis, extracted text — is marked stale by its
observation id, so the summary says the file has no current verdict and offers
the run that would examine it again. An unchanged file gets no new history row;
its `verified_utc` is refreshed and it stays the current input for every later
stage. The text index is unaffected: it is signed by the extracted texts alone.

**After a prescan you can already answer:** how many files, how much space, by
type, by folder, by age, largest files, largest folders, what could not be read.

**You cannot yet answer** anything about duplicates or file contents.

> Stage 1 runs at roughly 800–1,200 files/sec. Stage 2 ingests 100,000 rows in
> about 35 ms — the database has never been the bottleneck.

---

## The three doors

After the prescan the user chooses what to invest next. **None of these is final**
— the doors stay reachable from the project summary ("Collect more evidence...")
until every file is fingerprinted, at which point they have nothing left to do and
the button goes away.

| Door | Run kind | Cost | What it buys |
|---|---|---|---|
| **Find My Duplicates** | `duplicate_analysis` | Opens only size-collision candidates | Answers the duplicate question **completely** |
| **Full Fingerprinting** | `exhaustive_identity` | Opens every file | Gives **every** file a verifiable content identity |
| **Go to the project** | — | Nothing | Everything the prescan already knows — the project summary |

**The real difference between the first two is not thoroughness.** Find My
Duplicates finds every duplicate there is. Full Fingerprinting additionally gives
non-duplicates a content identity, which is what later enables **verifying a file
after a move**, **detecting that content changed on a re-scan**, and deduplicating
against a different corpus.

---

## Run kind 2 — `duplicate_analysis` ("Find My Duplicates")

**One button. Four internal sub-stages**, recorded under stage key
`PartialHash.ps1`.

| # | Sub-stage | What it does | Opens files? |
|---|---|---|---|
| 1 | Select size candidates | Narrows to files sharing a size with another file | No |
| 2 | Partial hash | Hashes the first N bytes of **candidates only** | Yes — partial read |
| 3 | Escalate | Groups by partial hash; discards anything now alone | No |
| 4 | Full hash and confirm | Full hash of the survivors | Yes — full read |

Writes `hash_measurement`, `content`, `duplicate_group`, `duplicate_member`, and
sets each file's `hash_status`:

| `hash_status` | Meaning |
|---|---|
| `size_unique` | **Proven not a duplicate without ever being opened.** A positive finding, not missing data. |
| `confirmed_duplicate` | Opened, hashed, matched another file |
| `unique_by_hash` | Opened, hashed, matched nothing |
| `ruled_out_partial` / `ruled_out_full` | Opened; a same-size peer's hash differed |
| `not_attempted` | **Only after a stop.** Never opened — not an error, nothing went wrong with it |
| `unresolved` | **Only after a stop.** Read, digest kept, but a same-size or same-partial-hash peer was not read, so no uniqueness verdict is claimed |

**Stopping it.** Cancel is checked between files in both the partial and the full
pass. Positive findings stand — identical files are identical whatever else was
unread — but "unique" and "ruled out" are claims about every peer having been
examined, so they are withheld wherever a peer was not (`unresolved`). The report
opens with a RUN STOPPED block; the hub says the duplicate question is not fully
answered and offers Find My Duplicates again; `settings.json` does not get a
"last completed" stamp. A later complete run re-reads every candidate and replaces
the verdicts — verified to return the exact ground-truth groups after a stopped run.

> Measured on 240 files (200 uniquely sized, 40 in 20 same-size pairs): the funnel
> narrowed 240 → 40, and only those 40 were ever opened. 0.17 s versus 1.60 s for
> the exhaustive run.

---

## Run kind 3 — `exhaustive_identity` ("Full Fingerprinting")

**One button. One stage, no tiering** — stage key `FullHashInventory.ps1`. Every
file is opened and fully hashed. Same tables as above; the `size_unique` files
become `unique_by_hash`.

> Running this *after* Find My Duplicates re-reads everything rather than topping
> up the files that were skipped. Worth knowing before offering it as an upgrade.
> The same is true of **Fingerprint again** beside COMPLETE on the summary: every
> file is read again, digest or no digest — which is exactly what makes it the one
> way to catch a file whose bytes changed under the same size and modified time.

**Stopping it.** Same rule: files never reached are `not_attempted`; a hashed file
whose digest matched nothing among the hashed files is `unresolved`, not
`unique_by_hash`, because the claim would rest on files never read.

---

## Run kind 4 — `content_analysis` (the file-type buckets)

**One stage per analyzer selected.** Each analyzer declares which extensions it
applies to, and reports `no_applicable_files` as a distinct, successful state.

| Stage key | Bucket | Handles | Produces |
|---|---|---|---|
| `ImageAnalysis.ps1` | Images | JPEG, PNG and similar | Perceptual hashes — finds *visually* similar images |
| `RawImageAnalysis.ps1` | RAW camera images | CR2, NEF, ARW, DNG | EXIF |
| `PDFAnalysis.ps1` | PDFs | `.pdf` | Page count, metadata, encryption status |
| `OfficeAnalysis.ps1` | Office documents | `.docx .xlsx .pptx` + legacy `.doc .xls .ppt` | Document properties |
| `AudioAnalysis.ps1` | Audio | needs ffprobe | Technical and tag metadata |
| `VideoAnalysis.ps1` | Video | needs ffprobe | Technical metadata |
| `TextFileAnalysis.ps1` | Text / Markdown | `.txt .md` | Word counts, tags, links |
| `ArchiveAnalysis.ps1` | Archives | `.zip .7z` | **Lists members without extracting** |
| `ContentExtraction.ps1` | Text extraction | 113 formats — documents, OpenDocument, EPUB, every Office variant, email in every shape, source and configuration, the documents inside zips, WordPerfect, OneNote, files with no extension, and scans by OCR (PDF pages with no text layer; pictures that look like documents) — each read by its bytes, not its name | Extracted-text artifacts; OCR pages, quality and review flag in the result |

Writes `analyzer_run`, `analyzer_result`, plus `archive_member` /
`archive_summary` and `extracted_content`.

**These do not require any fingerprinting.** Verified: analysis and extraction ran
successfully on a project with zero hashes. The current interface implies an order
the engine does not require.

**RAW and non-RAW are already separate buckets.** Selecting a narrower set than a
bucket — "only `.jpg`" — is not possible today: an analyzer run takes analyzer
keys, not a file filter. That is planned as a later refinement.

**Stopping it.** Cancel is checked between files. Every file already analysed is
kept (its results are current evidence); the stage and run are `cancelled`; the
bucket's `Last…Scan` stamp is not written; the hub shows "N of M have current
analysis" with Analyze offered. Running the bucket again starts from its first
file.

**The estimate before it starts** (`fo_estimates.estimate_analysis`) counts the
applicable files exactly from the inventory, runs the real analyzer over up to 6
files spread across the size range (24 MB cap), models files and bytes, and
applies the pessimistic factor. Extraction is estimated in two halves — PDFs by
**page** (seconds per page from a PDF-only sample × the pages the PDF analyzer
already counted), everything else by file and byte — because a PDF's cost follows
its pages, not its size. Extraction's sample writes to a temporary folder that is
deleted — never into `Runs\`.

**A re-scan no longer detaches results.** Results are attached to the current
observation the engine loaded the file under, not re-found by path in the newest
scan's rows (which, in `history.mode=changes`, do not exist for an unchanged file).

**Running a bucket again.** An analyzer run takes every current present file of
its type whether or not it has a result, so **Analyze again** (beside ANALYZED) is
a fresh `content_analysis` run whose results replace the old ones as the newest
for each file — the way a corrected analyzer (the PDF text flags, defect 21) is
applied to stored records. **Extract again** (beside INDEXED or EXTRACTED) is the
same for `ContentExtraction.ps1`, after a change on the Options page; it leaves the
text index out of date, and the summary says so and offers Index text.

---

## Run kind 5 — text indexing (Phase 2, on demand)

Not a Phase 1 run. Builds a full-text index **from the extracted-text artifacts**,
never from source files. The summary offers it as **Index text** once anything is
extracted, and again — with the Text line reading "index out of date" — after
any later extraction; a text search on an out-of-date index rebuilds it first,
which is slow on a large project. The index is signed by the extracted texts
alone, so a re-scan, fingerprinting or a bucket analysis does not stale it.

Writes `p2_fts_text`, `p2_fts_text_map`, `p2_derived_index`.

Now also a button — **Index text** — in the hub and on the analyze screen, run
through the same blocking runner with an estimate first
(`fo_estimates.estimate_indexing`, from the distinct texts and bytes extraction
produced). Not a `run` row: nothing is collected from a source file. A stopped
build leaves no half index.

**Three separately skippable decisions:**

1. **Analyse a bucket** — properties and metadata. Fast.
2. **Extract text** — produces the artifacts. Slow.
3. **Index the text** — makes it searchable. Slow.

You may do 1 without 2, and 2 without 3.

> **Coverage limit worth knowing:** extraction handles 113 formats, and a file
> is read as what its bytes are (a `.doc` holding RTF is read as RTF; a `.xls`
> holding an HTML export as HTML; a README with no extension as text; a PDF
> page with no text layer by OCR). What OCR reads is judged page by page and a
> poor scan is flagged for a person. The Options page can leave out OCR,
> pictures, or the documents inside archives; what is left out is recorded as
> not processed and is never searchable — and the search says nothing about it.

---

## What each level lets you answer

| After | Duplicates | Content identity | File properties | Text search |
|---|---|---|---|---|
| Prescan | — | — | Type, size, dates only | — |
| + Find My Duplicates | **Complete** | Candidates only | " | — |
| + Full Fingerprinting | **Complete** | **Every file** | " | — |
| + bucket analysis | " | " | **Full** for chosen buckets | — |
| + extraction | " | " | " | Artifacts exist |
| + indexing | " | " | " | **Searchable** |

---

## Where the record of all this lives

Every stage writes to the project database, not to a log folder:

- `run` — one row per run: version, host, environment snapshot, timing, status
- `run_stage` — one row per stage: exit code, attempts, checkpoints, durations
- `event` — severity, category, file path, error type, whether it was retryable

That is the audit trail, and it is already populated on every run.

---

## Not a run: the Decide page (Phase 3, from B8)

Recording a decision on the Decide page is a point interaction, not a run:
no estimate, no progress screen, no `run` row. Each click is one
`p3_operation` row (who, through what, when) with the decision, withdrawal,
policy-version or review-event rows it made, written in one transaction.
The audit trail for decisions is those tables; **Export journal…** writes
them as text into the project's `Exports\` folder. Nothing on that page
opens or changes a source file.

One thing does follow a run: when a Pre-Scan, Find My Duplicates or Full
Fingerprinting run finishes, the Phase 3 routing detector reads the
evidence the run just changed and records, under a system operation, which
decisions now rest on moved evidence, which decided targets are blocked,
and which snoozes have elapsed. It writes rows about routing; it never
changes a decision, and a failure to write them never fails the run.
