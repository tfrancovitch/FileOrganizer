# The File Organizer — Project Plan

**Single source of truth.** Where the project is, what was decided, what is next.
Supersedes the scattered planning documents; the detail they contain is preserved
under `Docs\` and indexed at the end of this file.

**Last updated:** 2026-09-12, afternoon — the analyzers run on the real corpus
**Current position:** Phase 2 (Understand) — built; used twice by a person on a
real 41,056-file corpus; every note acted on; the crash fixed; all seven analyzers
run on the real corpus with source immutability confirmed; extraction awaits the
user's decision; closeout drafted
**Code source of truth:** `FileOrganizer\` (git), branch `phase2`

---

## 1. What the product is

Point it at a folder — a drive, a share, a photo library — and it builds a
complete, verifiable record of what is there: every file, its properties, whether
it is genuinely unique or a duplicate, and what is inside it. Then it answers
questions about that record.

Its tagline is **"Inventory. Organize. De-duplicate."** Today it does the first
and the third; organizing is Phases 3 and 4.

Two properties distinguish it from a disk-space utility:

**It does not modify source files.** Verified: 231 of 231 files byte-identical and
timestamp-unchanged after a full pipeline and every analytic.

**It says when it does not know.** If part of a corpus could not be read, every
answer touching it is stamped *incomplete* with a count. It will not present
partial evidence as complete.

---

## 2. The axioms

From `Docs\Charter\The_File_Organizer_Project_Plan_Phases_1-8plus.docx`. These
govern; anything below that conflicts with them is wrong.

1. **Reality outranks the database.** The filesystem is the thing being described.
2. **Observation, understanding, decision and action are different kinds of work.** The system must not blur them.
3. **The build phases are not a mandatory runtime workflow.** A user must not be forced to complete a full inventory before getting value.
4. **Incremental progress is a product requirement.** Large projects may take months.
5. **Evidence and inference must be labelled differently.** A cryptographic match is not visual similarity.
6. **Uncertainty must remain visible** — unknown, stale, incomplete, uncertain, inferred.
7. **Human authority governs consequential decisions.**
8. **Every meaningful conclusion needs provenance.**
9. **Phase 2 is data-only.** Understanding tools interact only with already-collected evidence.
10. **Mutation of a source file is an action** — including changing embedded metadata or renaming.
11. **Every Phase 4 action must be reversible.**
12. **Resumability is mandatory.**
13. **Completeness is a property, not an assumption.**
14. **Cloud-only content must not be silently hydrated.**
15. **Speed and simplicity are product virtues; rigor belongs behind them.**
16. **The product should reduce cognitive work.**
17. **No future AI may become the action authority.**
18. **Phase 5 is a complete product boundary.**

A nineteenth, adopted 2026-09-10:

19. **Not everything the product does needs a screen.** The interface exists to
    solve problems; anything else belongs on disk, with the interface pointing at it.

---

## 3. Phase model and status

| Phase | Name | Status |
|---|---|---|
| **1** | **Observe** | **Shipped** — B6.2 |
| **2** | **Understand** | **Built; used on a real corpus through fingerprinting and every analyzer, source files confirmed untouched; closeout drafted — the user's decision** |
| 3 | Decide / Plan | Not started |
| 4 | Act and Verify | Not started |
| 5 | Maintain | Not started — *product completion boundary* |
| 6 | Local LLMs | Research horizon |
| 7 | Organizational models | Research horizon |
| 8+ | Research and expansion | Research horizon |

**Phase 3 records what *should* happen** — human adjudication, decisions, plans.
**Phase 4 executes them** with verification and reversibility. Note axiom 10:
changing embedded metadata is an *action*, so metadata editing is Phase 4, not 3.

---

## 4. Where Phase 2 actually stands

### Verified against known-answer ground truth

Ground truth is computed from bytes on disk by a corpus generator, never from the
query engine being tested.

| Check | Result |
|---|---|
| Acceptance suite | **30 / 30** |
| Standard reports, headless, no parameters | **31 / 31** |
| Exact duplicates | 4 groups, 68,889 reclaimable bytes, 11 members — **exact** |
| Same-size-different-bytes decoy | Correctly **not** grouped |
| Full-text search | 4 / 4 marker phrases hit exactly the right file |
| Deep keyset pagination | 231 rows over 10 pages, no repeats |
| Source immutability | **231 / 231 unchanged** |
| Scale — 100,000 real files | Worst report 629 ms, median ~95 ms |
| **Dashboard checks** (`p2_dashboard_check.py`) | **149 / 149** — every run kind to completion and stopped, the estimate-first run screen and its caution, sorted paging over every file, exports, the summary's arithmetic, value lists, analysis columns, the failures view against ground truth, and every view of the real window |
| **Real corpus** — 41,056 files, 73.6 GB, OneDrive | Pre-Scan 68 s; Full Fingerprinting 9 min (estimate said ~8); 3,753 duplicate groups, 8,519 files, 2.4 GB reclaimable. **All seven analyzers run on the real project: 30 min against a ~51 min estimate; 31,254 files analysed, 16 per-file errors recorded with reasons; 30,112 archive members listed** (`Docs\Validation\PHASE_2_REAL_CORPUS_RUN_2026-09-12.md`) |
| **Source immutability, real folder** | **41,056 / 41,056 unchanged** in size and modification time after the Pre-Scan, Full Fingerprinting, all seven analyzers and the archive re-run |
| Stopped Find My Duplicates, then a complete one | Complete run still returns the exact 4 groups / 68,889 bytes |

### Defects found and fixed

| # | Defect | Resolution |
|---|---|---|
| 1 | Acceptance corpus could not exercise Phase 2 — 146 KB, one extension, no hashes, no content. 15 of 29 "passing" reports returned zero rows | Built a corpus that can falsify |
| 2 | Catalog parameter defaults ignored headlessly — CMP-003 failed | `effective_parameters()` |
| 3 | AGE-002 had required parameters with no defaults | Relative-time defaults |
| 4 | Provenance stamp said P2.9 while running P2.9.1 | `stamp_core_version()` |
| 5 | Phase 2 was not in version control at all | Committed; `fo_db.py` was at schema 7, so a fresh checkout could not have worked |
| 6 | `evidence_health()` used the strategy P2.5 measured as 4× slower | Correlated `NOT EXISTS` — 500 ms → 208 ms |
| 7 | Folder scoping used an `OR` that blocked an index seek | `UNION ALL` — 39.4 ms → 8.4 ms |
| 8 | Integrity manifest stale — Dashboard opened with a setup-failure dialog | Regenerated; now covers 67 files |
| 9 | **The hash engine ignored Cancel.** It stored the `should_continue` hook and no loop consulted it — a Cancel during Find My Duplicates or Full Fingerprinting would have done nothing | Checked between files; files never opened are `not_attempted`, uniqueness verdicts resting on an unread peer are `unresolved`, positive findings kept |
| 10 | **A stopped inventory walk would have marked every unwalked file as vanished** — the ingestor treated an available root as fully walked | Walk reports `stopped`; nothing marked vanished; scan recorded `interrupted`; coverage reads incomplete |
| 11 | **Analysis results detached after any re-scan.** Results were matched to observations by path within the newest scan's rows, which in `history.mode=changes` do not exist for an unchanged file — so they were stored as `unmatched` and the hub would say "none analysed" forever | The engine's own current observation id rides along on each result |
| 12 | The Pause button wrote a flag file nothing read | Replaced by Stop, wired to `should_continue` |
| 13 | `capability.py` said "the duplicate question is fully answered" whenever anything had been fingerprinted | Says "not fully answered" when any file has no verdict; "at least N files" when a walk did not finish |
| 14 | Duplicate counts were read from `duplicate_group`, which keeps one row per group **per run** — 9 groups shown where the truth was 4 after a stopped, a complete and a fingerprinting run | Counted from the current-duplicate projection |
| 15 | Files page: "Next 200" on 25 files led to a blank page with no way back | Previous and Next, both real, with "Files 1–25 of 25" |
| 16 | **The crash.** Tk objects whose last reference sits in a reference cycle are freed by the garbage collector on whichever thread triggers it; freed on a worker thread, a Tk variable raises and a Tk instance **aborts the process** (`Tcl_AsyncDelete`). Timing-dependent — glitched once, crashed the second time. Reproduced in the suite and in isolation | Collector run on the main thread after every screen teardown and before every thread; no Tk variables on the run screen; faulthandler armed; callback errors logged |
| 17 | ffprobe spawned without `CREATE_NO_WINDOW` — under pythonw every probe flashed a console window ("a bunch of brief windows popped up") | Flag added |
| 18 | `needs_caution` bound its threshold at definition time; found by the check that tried to lower it | Read when called |
| 19 | **Archive members were never persisted by a Dashboard run.** The streaming persistence path passed `results=` to a writer that never accepted it and never called the summary writer; every run with a `.zip` raised, was caught as a warning, reported "succeeded", and stored zero member rows with the ingest status stuck at *running*. No fixture had an archive; the real corpus had 462 | Both writers take the batch; summaries written; a persistence failure marks its analyzer run *failed* with the reason. 30,112 members persisted on re-run; the suite now carries zips through the window |
| 20 | **"Unexpected Error" on a mouse-wheel turn after leaving the Reports page.** The scrolling list bound the wheel application-wide (`bind_all`) to its own canvas; the next page destroyed the canvas but the binding stayed, so the first wheel turn anywhere raised `invalid command name` | `clear()` drops the wheel binding with the page; the handler checks its canvas still exists. The suite turns the wheel after leaving Reports and expects silence |

Two Phase 1 improvements followed: the allocated-size call is skipped for ordinary
files (self-validating per volume), and candidate-only identity narrowing is
available opt-in. **The walk is roughly 2× faster than B6.2.**

### Known limits

- **Text extraction covers six formats** — `.pdf .docx .pptx .xlsx .txt .md`. Text in a `.csv`, `.json`, `.log` or extensionless file is never extracted and never searchable, **and the search says nothing about it.** Blocking for any legal claim.
- **Analyzer runs cannot be scoped to a file subset** — analyzer keys only.
- **Upgrading fingerprints re-reads everything** rather than topping up.
- **A stopped run does not resume.** Everything it did is kept and the hub shows the gap, but running the stage again starts from its first file. The pre-run screen says so.
- **Text extraction can be very slow on real PDFs.** The real corpus's 4,060 extractable documents estimated at ~40 hours (the sample hit 6 s/file). The estimate is pessimistic by design; the true figure is unknown until it runs. Extraction was deliberately not started unattended.
- **Cancel is honoured between files, never during one.** A single very large file finishes before the stop takes effect.
- **One unexplained outlier:** a 100,000-file scan took 2,629 s once and 124.7 s every time since. Not reproduced; cause unknown.

### The first real-use session — 2026-09-11

The user ran the Dashboard and wrote notes (`Phase 2 Real person Notes 2026-09-1.txt`
in the workspace root). Every note was acted on the same day:

| Note | Done |
|---|---|
| Startup-check window too large, lands anywhere | Small, centred; the Dashboard then opens maximized |
| Side panel: Open Project and New Project on top, Options and Exit at the bottom, nothing open initially | Exactly so; Options exists with nothing to set yet |
| "What this project can answer" reads as an AI answering — make it a project summary | "Project *name*": totals, fingerprints, duplicates, files by type, text state |
| Could not get back from Browse files; Next 200 on 25 files went blank | "Project summary" button on every page; real Previous/Next with counts |
| Reports: Run button too far from the name; only ~20 of 31 visible | Run in the first column; the list scrolls |
| Files: details pane squished; choose columns; sort and filter like a spreadsheet; more columns; export CSV | All built — sort by heading (server-side, both directions), right-click to filter a column or choose columns, horizontal scroll, minimum pane widths, CSV of everything the question covers |
| Reports should export to CSV | Reports and saved queries export their result |
| What is the difference between saved queries and reports? | A standard report ships with the product; a saved query is one you composed on the Files page and named. Both are queries; both re-run on current evidence. The page now says so |
| Clicking Projects closed the open project without asking | Asks first, naming the project |

Two things the session did **not** produce, and the closeout has to say so: it ran
on `PrefixSiblings` — a 25-file fixture, not real data — and its notes are about
the interface, not questions asked of a corpus. See `Docs\Handoffs\P2.12_CLOSEOUT.md`.

### The second real-use session — 2026-09-12, a real corpus

`C:\Users\tfran\OneDrive\Documents\TOMMY STUFF`: 41,056 files, 73.6 GB. Pre-Scan in
68 s, Full Fingerprinting in 9 min against an estimate of ~8. Notes in
`Phase 2 Human Test 2 - Real Corpus Notes.txt`; every one acted on the same day:

| Note | Done |
|---|---|
| A screen between "Create project and run the Pre-Scan" and the Pre-Scan; another between a door and its scan | Gone. Every button starts its run. The estimate is measured as the run's first step and shown in its log; only a run over 15 minutes asks "Begin now?" |
| "Walking #,### files so far" — liked | Kept |
| Doors: too much text; wants name / Estimated time / Begin Scan | Three columns, exactly so, one sentence at the foot |
| Fingerprints should read files (bytes); duplicates as files, groups, bytes reclaimable; buckets with count and bytes | Done |
| Button far right, unclear which line it belongs to; first column should be Analyze or ANALYZED | First column is the action or the word |
| No way to analyze everything at once (excluding extraction) | "Analyze all" |
| "Choose what to analyze" does not fit the one-at-a-time interface | Removed |
| The empty confirm page, Back going home, brief windows, then **the crash** | Confirm page removed (was an empty "measuring" screen); the brief windows were ffprobe consoles (#17); the crash was #16, reproduced and fixed |

The exploration stopped at the crash. The questions of the corpus itself — what is
taking the space, which duplicates matter — are still to be asked.

### The third real-use session — 2026-09-12, the real corpus explored

The seven analyzers had run on the real project (30 min, ~51 estimated). The user
explored the reworked window and wrote `Phase 2 Human Test 2 - Real Corpus
Notes 2.txt`. This is the first session whose notes include questions **of the
corpus** — "how can 11 files be 0 B?", "16 analyzer failures — which, and why?",
"where is the data the analyzers collected?" — alongside the interface notes.
Every note acted on the same day:

| Note | Done |
|---|---|
| The type buckets on the summary should be clickable and open the file list on just that type | They are links; "PDFs" opens the Files page on the PDFs, largest first; "Other" opens everything no analyzer handles |
| "16 analyzer failures" — analyzers or files? Want a diagnosis | Files. The summary says "Could not be analyzed  16 files (5 images, 11 office documents)" with **Show which**; the strip says "16 files could not be analyzed — click to see which"; both open the list with the reason in a column (11 are not real Office files — "not an OLE2 structured storage file"; 2 images exceed the decompression-bomb limit; 2 could not be identified; 1 PDF is truncated) |
| The home page should invite exploring; "Collect more evidence" no longer applies after a full fingerprint | The summary ends with **Explore the files** and **Reports**; the doors button appears only while fingerprinting is incomplete |
| 11 files at 0 B — how? | They are genuinely empty (Google Takeout placeholders and the like); the display stays "0 B" at the user's request |
| The folder tree does not expand; wants to pick a subfolder | Expands to any depth, lazily; picking a folder scopes the page |
| Size vs Allocated? What is a hard link? | "Allocated" is now **Size on disk**; Choose columns… describes every column, including those two |
| Filtering by Hash Status showed a blank box; wants the list of values, like Excel, for every filter | Value columns show a checklist of the values present with counts, All / None, drawn from the page's own question |
| The analyzers' metadata should be columns | Title, Author, Pages, Width, Height, Duration, Words, Camera make/model, Archive entries, Reason not analyzed — sortable and filterable, server-side |
| Metadata Explorer not useful — where is the collected data? | It now shows everything recorded about the file: every field of every analyzer, the archive's members, the error |
| Reports page: scroll wheel → "Unexpected Error" | Defect 20 |

The user's decision on text extraction (~40 h estimated) is still open, and the
`phase2` branch is still unpushed.

### What remains before Phase 2 closes

The living plan's own completion criteria:

| Criterion | Status |
|---|---|
| Acceptance run against a meaningful real project | **Partial → mostly met** — Pre-Scan and Full Fingerprinting on 41,056 real files, estimate honoured; analysis on a copy; the exploration was cut short by the crash before questions were asked |
| Shareable evidence reviewed | Partial |
| **Real user questions recorded** | **Partial → mostly met** — three sessions' notes recorded and acted on; the third session's include the first questions of the corpus itself (the empty files, the files that could not be analyzed, where the collected data is). The space and duplicate questions are still to be asked |
| Analytical gaps classified | Partial |
| High-value deficiencies corrected or deferred | **Done** — 20 defects, all fixed |
| Real source immutability confirmed | **Done — on the real folder**: 41,056 / 41,056 unchanged after every run |
| **"Demonstrably useful as an exploratory tool rather than merely technically functional"** | **Not evidenced** — the session did not reach a verdict |

**`Docs\Handoffs\P2.12_CLOSEOUT.md`** assesses each criterion against what
exists and puts the decision to the user: close the phase on the evidence so far,
or run one more session on a real folder with the reworked window first. The
notes file or `Docs\Validation\PHASE_2_REAL_USE_RECORD.md` — either form — is
where that session's questions go.

---

## 5. The workflow

Build phases are not the user's workflow (axiom 3). The workflow is:

```
New Project  ->  Pre-Scan  ->  three doors  ->  the query interface (the hub)
                (4 stages)         |                      ^
                                   +----------------------+
                              all three stay available from the hub
```

The query interface is **the hub, not the destination.** A user may arrive with
only an inventory and pull in more evidence when a question needs it.

**Built 2026-09-11, in one window.** `TheFileOrganizer.bat` runs the startup
checks, then opens the Dashboard (`Scripts\Phase2\gui.py`) on the Projects screen.
New Project runs the Pre-Scan; the three doors follow; the hub
(`Scripts\Phase2\hub.py`) renders `capability.py` with a button on every gap;
"Choose what to analyze" lives in the hub as buckets, with extraction and indexing
as separate rows. Every run goes through the blocking runner
(`Scripts\Phase2\runner.py`): an estimate screen first, then a progress screen
that owns the window, with a Cancel that stops between files and keeps what was
done. The old dashboard screens remain reachable with `Dashboard.py --classic`.

`STAGE_MAP.md` in this folder is the authoritative reference for what each click
actually runs.

### The three doors

| Door | Buys |
|---|---|
| **Find My Duplicates** | Answers the duplicate question **completely** |
| **Full Fingerprinting** | Also gives **every** file a verifiable identity — needed for move verification and change detection |
| **Go to the query interface** | Everything the Pre-Scan already knows |

The difference between the first two is not thoroughness.

### Decisions — 2026-09-10

1. **One window.** Dashboard and Explore & Understand merge; they become the Dashboard.
2. **Three doors** after the Pre-Scan, none of them final.
3. **Work is blocking** — set it and forget it.
4. **Every long operation shows an estimate first.** With blocking work the estimate is a safety feature.
5. **A visible Cancel** on the progress screen. Blocking is about attention, not being trapped.
6. **Re-run ships greyed out** — whether a re-run is a new project is undecided.
7. **The Pre-Scan keeps both stages, and we keep calling it two stages.** One click is not one process.
8. **"Choose What to Analyze" moves into the hub**, where the question arises.
9. **Buckets now, finer filtering later.** RAW and non-RAW are already separate.
10. **Extraction and indexing are separately deferrable.** The engine already supports this.
11. **Logs and journals live on disk, not in the interface** (axiom 19).

### Undo, when Phase 4 arrives

Established by research, recorded here so it is not re-derived:

| Operation | Undo cost |
|---|---|
| Rename; move within a volume | **~200 bytes** — only a directory entry changes |
| Move across volumes | **None** — copy, verify by hash, then delete |
| Delete a duplicate | **~200 bytes** — the content survives elsewhere |
| Delete unique content | **Full size** — the only case needing retention |
| Metadata write | **Prevention, not undo** — write to temp, verify it parses, then replace |

**Never delete the last copy**, enforced structurally. The Windows Recycle Bin is a
courtesy, not a mechanism: a programmatic delete bypasses it, it is size-capped
rather than time-boxed, and it does not exist for network shares.

---

## 6. Next

**Done since this list was written**
- A "what can this project answer?" surface — `Scripts\Phase2\capability.py`, the
  thing that would have prevented the P2.11 misreading
- Cancellation: one coordinator-level `should_continue` hook reaching the scan,
  hash, analyzer and FTS engines. Verified — 500 files, stopped at 120, all 120 kept

**Done 2026-09-11 — the build queue**
1. The hub view — `Scripts\Phase2\hub.py`, rendering `capability.py` ✔
2. The three doors screen ✔
3. The blocking runner: close the connection, run, reopen, with a working Cancel ✔
   (and Cancel made real in the hash engine and the walk — defects 9 and 10)
4. Real estimates for analysis, extraction and indexing — per-type sampling with the
   real analyzers, `fo_estimates.estimate_analysis()` / `estimate_indexing()` ✔
5. One window — `Dashboard.py` hands off after its startup checks ✔

**Done 2026-09-11, later — the first real-use session**
6. The user's notes, every one acted on (see section 4) ✔
7. `Docs\Handoffs\P2.12_CLOSEOUT.md` drafted ✔

**Done 2026-09-12 — the second real-use session, on a real corpus**
8. Every note acted on; the crash reproduced and fixed; the flow is click-and-go ✔
9. The seven analyzers run on the real project at the user's request; immutability
   confirmed on the real folder; defect 19 found and fixed ✔

**Done 2026-09-12, later — the third session, the real corpus explored**
10. Every note acted on: clickable buckets, the failures named and listed with
    reasons, value lists on filters, analysis columns, the explorer showing
    everything, the folder tree, Explore the files; defect 20 found and fixed ✔

**To close Phase 2** — the user's decision
11. Extract and index the 4,060 documents, or not (~40 h estimated; it will ask)
12. Ask the corpus things — what is taking the space, which duplicate groups are
    worth acting on, what is in the archives — and record what was asked and
    whether it answered
13. Sign the closeout; Phase 3 handoff follows from it

**Decisions still open**
- Does a re-run constitute a new project?
- Is consumer mode a preference or a separate edition?
- Does the journal need to be tamper-evident, or merely out of the way?
- Should text extraction cover more formats? Email (`.msg`, `.pst`, `.eml`) is the largest gap for legal use.

**Unpushed:** the `phase2` branch is **49 commits** and exists only on this machine.

**Handoff:** `Docs\Handoffs\PHASE_2_COMPLETION_HANDOFF.md` carries the build queue,
the decisions already settled, and the traps, for a session picking this up cold.

---

## 7. Where things live

```
C:\FileOrganizerTesting\
  PROJECT_PLAN.md              this document
  STAGE_MAP.md                 what each click actually runs
  FileOrganizer\               git repository - code source of truth
  FileOrganizer-Phase1-RC-B6.1\  the running install (BASELINE.md: it is B6.2)
  Docs\
    Charter\          the phase model, requirements, question catalog, decisions
    Specifications\   query model, information model, report catalog, GUI contract
    Research\         comparative studies, retention, undo, ecosystem
    Validation\       every test, benchmark and acceptance report
    Handoffs\         handoff and installation documents
    Reference\        the Phase 2 living plan, the GUI prototype, archived build
  _Archive\           superseded builds and documents, with an index
```

Test corpora live under `C:\FOTest\`; `P2Accept` and `P2Scale` carry
`GROUND_TRUTH.json` and are the acceptance fixtures.
