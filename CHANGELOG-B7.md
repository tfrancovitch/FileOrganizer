# Changelog — B7

Follows B6.2. B7 is the build that enters adversarial testing against the
Master Matrix corpus, as B5 was the build that met the A–F attacks. It is the
first build in which Phase 1 (Observe) and Phase 2 (Understand) ship as one
product with one version: `fo_db.APP_VERSION` and `Phase2.VERSION` are both
`B7`, every run, project and log records it, and the window's title shows it.

Schema 8, unchanged since the P2.9.1 overlay. Existing projects open as they
are. Two derived indexes rebuild themselves on first use because the core
version is part of their signature: the current-duplicate projection (a query,
instant) and the text index (the summary says "index out of date" and offers
Index text; a search rebuilds it on the spot otherwise).

## Added — Phase 2, the Understand phase

**One window** (`Scripts\Phase2\gui.py`, `hub.py`, `runner.py`).
`TheFileOrganizer.bat` runs the startup checks in a small centred window and
opens the Dashboard maximized. Open Project / New Project at the top of the
side panel; Project, Files, Reports, Saved Queries, Evidence, History; Options
and Exit at the bottom. New Project runs the Pre-Scan; then the three doors
(Find My Duplicates, Full Fingerprinting, Go to the project); then the project
summary. Every button starts its run at once; the estimate is measured as the
run's first step and shown in its log; only a run over fifteen minutes asks
"Begin now?". Cancel stops between files and keeps what was done. The old
screens remain behind `Dashboard.py --classic`.

**The project summary.** Totals, fingerprints, duplicates, files by type, the
text state — every number a fact in the database, from `capability.py`. The
first column of every line is the action (Analyze, Extract text, Find My
Duplicates…) or the word that says it is done (SCANNED, COMPLETE, ANALYZED,
INDEXED); beside each such word, a link to do it again (Scan again,
Fingerprint again, Analyze again, Extract again). Type buckets open the Files
page on that type; the files no analyzer could handle are named and listed
with their reasons; poor scans are listed for a person to check.

**The Files page.** Sort by any heading (server-side, both ways), filter by
right-clicking a heading (value columns show a checklist of the values
present, with counts), choose columns — including the analyzers' results as
columns (Title, Author, Pages, Width, Height, Duration, Words, Camera, Archive
entries, Reason not analyzed, OCR quality and reasons) — page both ways with
honest counts, scope to any folder of the tree, export to CSV. The Metadata
Explorer shows everything recorded about a file. Reports (31, all runnable
unattended) and Saved Queries export their results.

**Text extraction, 113 formats**, every file read as what its bytes are rather
than what its name says: documents and every Office variant, OpenDocument,
EPUB, RTF, HTML, plain-text formats, source and configuration, email in every
shape (.eml .mbox .mht .msg, and .pst/.ost through a read-only [MS-PST] reader
that never involves Outlook), the documents inside zips and 7z, files with no
extension, WordPerfect and OneNote by scanning; and **scans by OCR** through
Windows' own engine — pages with no text layer and pictures that pass a
document gate, every page judged (resolution, contrast, focus, speckle, skew,
how much reads as words), poor scans flagged `OcrReview = yes` with reasons.
PDF text through PDFium (25–30× faster than before). The Options page leaves
OCR, pictures or archives out, and the estimate follows.

**Honest estimates** for analysis, extraction and indexing, from a measured
sample per type: PDFs by page, other documents by family, archives by their
readable members.

**Cancel that works, and says what it left.** One `should_continue` hook
checked between files by the walk, the hash engine, the analyzers and the
index build. A stopped walk marks nothing vanished and the summary says "at
least N files"; stopped fingerprinting leaves `not_attempted` and `unresolved`
rather than a uniqueness claim; a stopped index build leaves nothing.

**The query engine** (`P2.9.1` core, extended): keyset paging over any sort,
chosen columns, counts, value lists, per-file analysis, `hash.authority`
(current / stale / absent) on every fingerprint.

**Phase 1 speed.** The walk is roughly 2× faster: the allocated-size call is
skipped for ordinary files (self-validating per volume) and candidate-only
identity narrowing is available opt-in.

**The test corpora and their suites.** `p2_build_acceptance_corpus.py` builds
`C:\FOTest\Corpus` (one file of every type the program names, markers, known
duplicates, malformed files, scans) with `GROUND_TRUTH.json` computed from the
bytes, and — under `--hostile` — the second tree of cases that break naive
tooling (reserved names, 200-level paths, ten thousand files in a folder,
links, junctions, denied files and folders, a 4 GiB sparse file, a
100,000-member zip). The Master Matrix cases are registered by ID in
`p2_cases.py`. Suites: `p2_acceptance`, `p2_dashboard_check` (194 checks
through the worker and the real window), `p2_hostile_check`,
`p2_mutation_check` (changes made while the walk runs), `p2_regression`,
`p1_identity_narrowing_check`, and `b6_regression` from B6.

## Fixed

Twenty-seven defects, numbered in `PROJECT_PLAN.md` §4, found by building the
above and by three real-use sessions on a 41,056-file corpus. The ones that
matter most:

- **The crash** (16): Tk objects freed by the garbage collector on a worker
  thread aborted the process (`Tcl_AsyncDelete`). The collector runs on the
  main thread before any thread starts; the run screen uses no Tk variables.
- **Analysis results detached after any re-scan** (11): matched by path in
  the newest scan's rows, which do not exist for an unchanged file. Results
  carry the observation they were made on.
- **Archive members were never persisted by a Dashboard run** (19): the
  streaming sink raised on every zip, the exception was caught as a warning,
  the run said "completed". Both writers take the batch; a persistence
  failure now fails its analyzer run.
- **The hash engine ignored Cancel** (9); **a stopped walk would have marked
  every unwalked file as vanished** (10); **the duplicate counts read a
  per-run table** and tripled (14); **`capability.py` overclaimed** after a
  stopped run (13).
- **The PDF analyzer judged "has extractable text" by page one** (21); **the
  extraction estimate said 40 hours for a 20-minute job** (22).
- **The summary after a re-scan** (25–27): a changed file still counted as
  fingerprinted (the staleness rule the query engine applies was not applied
  to the counts), extraction was counted as rows rather than files (a second
  run said "16,480 of 8,240 extracted"), and a vanished file stayed in a
  bucket's "analysed" count.
- **The text index went stale after any run** and the next search rebuilt it
  in silence; it is now signed by the extracted texts alone, and the summary
  says when it is out of date.
- **A CSV export could carry a formula** (matrix Y-018/Y-019): a cell
  beginning with `=` `+` `-` `@` tab or return gets a leading apostrophe.
- ffprobe consoles flashing under pythonw (17); the wheel binding that outlived
  the Reports page (20); `self_check.py` on the wrong schema (23); an openpyxl
  workbook left open (24); seekable folder scoping (7); the failure query's
  slow strategy (6).

## Documentation

`PROJECT_PLAN.md` (the single source of truth), `STAGE_MAP.md` (what each
click runs and what a stop leaves), `README.md`, and under
`..\Docs\Handoffs\` the completion handoff and the adversarial-corpus handoff.

## Known and unchanged

- A stopped run does not resume; running the stage again starts from its
  first file. The run screen says so.
- Cancel is honoured between files, never during one.
- Upgrading fingerprints (Fingerprint again) re-reads everything.
- Analyzer runs cannot be scoped to a file subset — analyzer keys only.
- OCR is English; another language needs the Windows language pack and the
  argument threaded through `fo_ocr`.
- WordPerfect and OneNote are read by scanning and had not met a real file
  when this was written; non-TIFF camera RAW is analysed, not read.
- Findings the adversarial corpus has already recorded as the program
  behaves today, for decision rather than silently changed: a file symbolic
  link is hashed as its target (C-011); a folder that exists but cannot be
  listed has its known files marked missing (D-003c); a file rewritten between
  the walk and the hash stage keeps the walk's size (I-005).
- `conn.rollback()` remains a no-op under the autocommit connection (B6.2).
