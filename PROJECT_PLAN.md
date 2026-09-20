# The File Organizer — Project Plan

**Single source of truth.** Where the project is, what was decided, what is next.
Supersedes the scattered planning documents; the detail they contain is preserved
under `Docs\` and indexed at the end of this file.

**Last updated:** 2026-09-20, late — **Phase 3 begun**: the Phase 3 handoff (Builds 1–2, core persistence + exact-duplicate decisions) built as **B8** in `FileOrganizer-Phase3-B8\` on branch `phase3` from tag `phase2-b7.2`; `PHASE3_HANDBACK.md` reports against it, item by item. Before that, 2026-09-19 evening — **the P2.12 closeout approved by the user**; build **B7** tagged `phase2-b7` and pushed to GitHub with branch `phase2`; the adversarial round is the last part of Phase 2. Earlier that day — the adversarial corpus is built: 260 cases across three truths, 199 of the Master Matrix's 289 rows embodied and 90 declined with a reason; the readers fixed against real files (defects 29–32); seven engine defects the corpus found stand recorded as DEFECT cases
**Current position:** Phase 2 (Understand) — built; used three times by a person,
twice on a real 41,056-file corpus; every note acted on; the crash fixed; all seven
analyzers run on the real corpus with source immutability confirmed; extraction
reads 114 formats including scans by OCR, and **is not run on the real corpus, by
the user's decision of 2026-09-19**; **the closeout approved the same day** — the
three real-use sessions judged sufficient; **the adversarial round (P2.13) is
done** — the P2 stress test of 2026-09-19 (the first two-root project) and the
Master Matrix corpus found defects 33–51, fixed in B7.1 and B7.2 (2026-09-20);
one case (A-014) stays by decision. The adversarial corpus
is built (`C:\FOTest`: `Corpus\` 853 files, `Hostile\` 10,026, the mutation
runner) and every suite is green on **B7.2**. **Phase 3 (Decide / Plan) has
begun**: Builds 1–2 of its seven — the decision record (schema 9), the
deterministic keeper / canonical / protection resolution, the protection
override gate, policies, and the Decide page inside the one window — are
built as **B8** and green on every suite (see §4a)
**Build:** **B8** — `fo_db.APP_VERSION` and `Phase2.VERSION` agree; every run,
project and log records it; `CHANGELOG-B8.md`; branch `phase3` in the git
repository, checked out as `FileOrganizer-Phase3-B8\` (a worktree — not yet
tagged; tagging is the user's call after review). B7.2 stays at tag
`phase2-b7.2`, install `FileOrganizer-Phase2-B7.2\`, untouched
**Code source of truth:** `FileOrganizer\` (git): branch `phase2` for Phase 2,
branch `phase3` (worktree `FileOrganizer-Phase3-B8\`) for Phase 3

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
| **2** | **Understand** | **Shipped — B7.2. Closeout approved 2026-09-19; the adversarial round (P2.13) closed 2026-09-20 with B7.1 and B7.2 (defects 33–51; A-014 kept by decision)** |
| 3 | Decide / Plan | **In progress — B8 (2026-09-20): Builds 1–2 of 7 built (core persistence, exact-duplicate decisions); Builds 3–7 (review routing, bulk/policy, Image Game, plan builder, project metadata) await their handoffs** |
| 4 | Act and Verify | Not started |
| 5 | Maintain | Not started — *product completion boundary* |
| 6 | Local LLMs | Research horizon |
| 7 | Organizational models | Research horizon |
| 8+ | Research and expansion | Research horizon |

**Phase 3 records what *should* happen** — human adjudication, decisions, plans.
**Phase 4 executes them** with verification and reversibility. Note axiom 10:
changing embedded metadata is an *action*, so metadata editing is Phase 4, not 3.

---

## 4a. Where Phase 3 stands — B8, 2026-09-20

The Phase 3 handoff (`C:\FileOrganizerTesting\FileOrganizer-Phase3\`: the
research synthesis, the build plan, five fixtures of the P3.R11 fixture lab
and the P3.R2 keeper-rule catalog) asked for Builds 1 and 2 of seven. Both
are built; `PHASE3_HANDBACK.md` reports against the plan's ten necessities
item by item and the five fixtures pass/fail. In one paragraph:

Migration 009 adds the four authoritative families as append-only tables —
`p3_operation` (actor, actor kind, agent kind and version, session, command,
time), `p3_decision` (immutable; typed value validated by a decision-kind
registry; evidence binding by reference into Phase 1/2 ids; `supersedes`),
`p3_decision_withdrawal` (a row, never a flag), `p3_policy` +
`p3_policy_version`. `Phase3\resolve.py` is the deterministic eight-step
resolution as a pure function: protection before ranking, explicit location
decisions, explicit group decisions, folder and root policies (recommendation
only), the empty project-policy and heuristic slots, display order last. The
protection gate is in that function: a redundant mark on a protected copy is
a conflict with no effect until an `override_protection` decision naming
the exact policy version exists — recorded only through its own dialog,
with a reason. Everything the Decide page shows is recomputed from the
record every time; nothing current is stored. The page lives inside the one
window (side panel **Decide**, the summary's **Review duplicates**, a file's
**Review this duplicate group**). Zero source-file mutation is asserted by
`p3_regression.py` (a before/after fingerprint of the corpus and a guard on
every Python-level write). Suites on B8: `p3_fixture_check` 37/37 (F01, F02,
F03, F09, F15 all pass), `p3_regression` 141/141, `b6_regression` 73/73,
`p2_regression` all, `p2_dashboard_check` all, `self_check` ready.

Judgment calls the research left open, all provisional and listed in the
handback: the working folder and version (`FileOrganizer-Phase3-B8`, B8,
branch `phase3` from `phase2-b7.2` — the handoff named `phase2-b7` and a
`TheFileOrganizer-Phase2-B7.2` folder, neither of which matched what exists);
the words on the screens (Decide, Duplicate decisions, Keep / Keep all / Set
canonical / Mark redundant / Defer / Override protection…, Review this
duplicate group); withdrawal does not revive what the withdrawn decision had
superseded (F09's own shape); a re-apply supersedes the withdrawn row so the
chain stays whole; `actor_id` is the Windows user name; a group whose only
status comes from protection counts as unreviewed until a person decides.

## 4. Where Phase 2 actually stands

### Verified against known-answer ground truth

Ground truth is computed from bytes on disk by a corpus generator, never from the
query engine being tested.

| Check | Result |
|---|---|
| Acceptance suite | **72 / 72** — 46 marker phrases, one per extractable format, each found only in its own file; the scans' only through OCR |
| Standard reports, headless, no parameters | **31 / 31** |
| Exact duplicates | 4 groups, 68,889 reclaimable bytes, 11 members — **exact** |
| Same-size-different-bytes decoy | Correctly **not** grouped |
| Full-text search | 4 / 4 marker phrases hit exactly the right file |
| Deep keyset pagination | 231 rows over 10 pages, no repeats |
| Source immutability | **231 / 231 unchanged** |
| Scale — 100,000 real files | Worst report 629 ms, median ~95 ms |
| **Dashboard checks** (`p2_dashboard_check.py`) | **194 / 194** — every run kind to completion and stopped, the folder changed after its scan (one file added, one changed, one removed) and Scan again, Fingerprint again, Analyze again, Extract again and Index text through the worker with the summary honest at each step, the estimate-first run screen and its caution, sorted paging over every file, exports, the summary's arithmetic, value lists, analysis columns, the failures view against ground truth, every marker phrase in 46 formats found through the index (the email ones only inside attachments, the scans' only by OCR), the clean scans unflagged and the poor scans flagged with reasons, the Options page's switches, and every view of the real window |
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
| 21 | **The PDF analyzer judged "has extractable text" by page one.** A 162-page book whose cover is a picture was recorded as having no text; it holds 782,000 characters. 348 of the real corpus's PDFs carry that flag and some of them are wrong | Every page is asked until one has text (PDFium, milliseconds a page); pdfplumber's first-page judgement remains the fallback. Re-running the PDF analyzer corrects the stored flags |
| 22 | **The extraction estimate said ~40 hours for a ~20-minute job.** It sampled six small PDFs, measured 0.06 MB/s, and extrapolated by bytes across 5.5 GB — but PDF extraction costs per page, and most of those bytes are pictures the reader never decodes | PDFs are estimated by page (seconds per page from a PDF-only sample × pages the PDF analyzer already counted); other documents by file and byte as before. Same corpus: ~17 min |
| 23 | `self_check.py` required schema 7 while every database is schema 8 — the health check failed on every machine | Requires 8, kept equal to `fo_db.APP_SCHEMA_VERSION` |
| 24 | An openpyxl read-only workbook was never closed after extraction, holding the source file open | Closed in a `finally` |
| 25 | **The summary said Fingerprints COMPLETE over a stale fingerprint.** The identity counts took `content_id` alone; a file re-observed since it was fingerprinted keeps the old `content_id`, marked stale by `content_observation_id <> current_observation_id` — the rule the query engine, the exports and the duplicate projection already applied. Found the day a re-scan could be started from the window | The capability applies the staleness rule to fingerprints, size-unique proofs and read failures alike; a changed file has “no verdict” and Find My Duplicates / Full Fingerprinting is offered again |
| 26 | **Extraction was counted as rows, not files.** `extracted_content` keeps a row per attempt, so a second extraction run would have said “16,480 of 8,240 extracted”, and a file re-observed since its extraction still counted as extracted | Counted per present file from its newest attempt against its current observation — extracted, failed, empty, not documents, cloud-only, OCR |
| 27 | **A vanished file stayed in a bucket's “analysed” count** while leaving its file count (“267 files, 268 analysed” after a re-scan) | Present files only |
| 29 | **The WordPerfect reader stepped off the end of the first real file it met.** WP6's 0xF0–0xFF codes are fixed-length, closed by their own byte; read as variable groups, `F0 1D 04 F0` gave a size of 61,444 — eight characters of a two-kilobyte document. And WP5's attribute codes are three bytes, not two, so every bold word lost its first letter (Tika's samples) | Both fixed against the three Tika files; `.wp` named as an extension |
| 30 | **The RAW analyzer came back "analyzed" with every field empty for four containers** — exifread knows TIFF and JPEG; Fujifilm RAF, Minolta MRW, Canon CR3 and Sigma X3F wrap their EXIF where it does not look | The reader lifts the embedded TIFF or JPEG out of each container (RAF header offset, MRW TTW block, CR3 CMT boxes, X3F directory) and hands it to exifread; all seven raw.pixls.us cameras named |
| 31 | **An encrypted Office document was reported as "not a zip file" / "OLE container without a document inside"**, an encrypted OpenDocument as "not well-formed" | `ole_kind` names the EncryptionInfo/EncryptedPackage pair, `odf_text` the manifest's encryption-data; both readers say password-protected. No password is ever tried |
| 32 | **A library exception with no message was recorded as success.** pdfminer raises an empty-message exception on a password-protected PDF; the engine stored an empty error text, so `extracted_content.status` read *extracted* with no artifact and no count | A message-less exception records the exception's name and is the failure it is |
| 28 | **The text index went stale after any run** — its signature was the whole evidence signature (every run, hash and observation) — and the next search rebuilt it in silence, with the summary still saying INDEXED: seconds on a fixture, minutes inside one click on a large project | Signed by the extracted texts alone (which file holds a text is decided at query time); when extraction does run again the summary reads “index out of date” and offers Index text |
| 33 | **A two-root project lost its first root at the Pre-Scan** — the coordinator handed every root's ingest the run's cumulative directory errors and path events; the second root's ingest adopted the first root's scan and marked all 11,141 of its files *missing*, unseen by the hash, duplicate, estimate and analyzer passes while every report said COMPLETED (P2_Stress_Test, the first two-root project, 2026-09-19) | Each root's ingest sees only what its own walk added (a window consulted after the generator drains, as `complete=` was); vanished-detection runs only for the roots the walk covered; `b6_regression` replays the two-root sequence against the pre-fix ingestor |
| 34 | **Finding Potential Duplicates was always recorded NO_APPLICABLE_FILES** — the status derivation looked for `PotentialDuplicates.csv`, which the Python candidates pass (persisting nothing) never wrote, while the report pointed at that file | The candidate list is rendered from the outcome, as the report is; none stays absent |
| 35 | **The hash reports' DRIFT CHECK compared the pass with itself** — the "preliminary" baseline was the engine's own input, 0 / 0 on the run that had lost a root; and the SCAN SUMMARY "recomputed independently from FullHashInventory.csv" was computed from the database | The baseline is `PreliminaryInventory.csv`, read and reported (or "not available"); the summary is recomputed from the exported CSV |
| 36 | **A re-scanned inaccessible location turned present** — the unchanged-row refresh wrote `state='present'` unconditionally, so a denied directory seen twice became a present, sizeless file the hash and analyzer passes tried to open | `touch_unchanged` keeps the row's own state |
| 37 | **The Full Run estimate was 13× the run** — the calibration sample was the first fifteen files by path (3.3 MB of thumbnails), so "throughput" was open/close cost, and the per-file term was never measured | Largest local files, 8 MB each at most, none sparse; per-file cost the median of the fifteen smallest; ~17 min estimated for a 10 min 20 s run |
| 38 | **Scan again wrote a 3-row inventory CSV and Fingerprint again a 3-row hash CSV for 52,200 files** — both exports selected the run's observations, which an unchanged file has none of under change-only history; and an exhaustive pass exported the selective artifacts too, truncating and removing the Pre-Scan's candidate list | Both exports read the current projection (`file_state`) with DB_IDs that join; `export_hash_stage(mode=)` writes only the pass's own artifacts |
| 39 | **`run.finalized` was never written** — migration 006 and CHANGELOG-B6 say it is written with the terminal status; every run read 0 and a Phase 2 query on it was always false | `finish_run` writes it; reconciliation of a run that died passes `finalized=False`, the case the flag exists for |
| 40 | **"Target drive type (detected): Unknown" for a fixed disk**, while the same run recorded `Fixed` in `run_source_root` from the same call | `win_meta.drive_type` speaks `GetDriveTypeW`'s words; the estimator still asks only "Network?" |
| 41 | **`\\?\` left mangled in analyzer messages** — Pillow quotes the path through `repr()`, so stripping the plain prefix left `'\\\C:\\…'` | The escaped spelling is stripped too |
| 42 | **OneDrive files' attributes rendered as a bare number** (`524320`, `1572902`) and the reports' Hidden/System counts — a substring test R6 also used — could not see them: 8 and 2 reported, 9 and 3 on disk | Pinned, Unpinned, RecallOnOpen, RecallOnDataAccess by name, any remainder as hex; values .NET could name render as before; change detection compares attribute words, so the spelling change is not a change of the file |
| 43 | **The folded case twin was a chimera** (A-014, still open) — the second name silently overwrote the first's size, so the row flipped between the twins on every scan, read "modified" each time and put 2 bytes in the drift check | The first record keeps the row; the second is a `FOLDED_CASE_TWIN` event, warned once per scan and counted in the preliminary report; the hostile truth's expected bytes moved with it |
| 44 | **Two-root bookkeeping** — the report merged same-named top-level folders across roots and printed an ambiguous "(root)", the header named one root, the ingest stage said "(0.0s)" for work that streams inside the walk, and a scan row inherited another root's counts, notes and duration | Top-level folders qualified by root when there is more than one (R6's lines otherwise); the header names every root; the stage note carries the time; each scan row keeps its own counts |
| 45 | **New Project's folder list was drawn behind its own row** — the list was a child of the form packed `in_` a sibling row created after it, which Tk stacks above; every Add landed in a list nobody could see, so Add looked like it wiped the path and added nothing | The list is a child of the row it sits in; a line under it says how many folders will be inventoried; `p2_dashboard_check` adds two through the real form (195 checks) |
| 46 | **Files under a folder that could not be listed were marked missing** (D-003c) — vanished-detection marked every location a completed scan had not seen, and a directory error did not stop it, so a changed ACL, a dropped share or a pulled drive mid-walk turned everything under it into *missing* | The walker records whether a failed directory exists or is gone (`ScanError.absent`); what lies under an unlistable one becomes `unverified` (migration 006's absence-of-evidence state) and is `reappeared` once the folder lists again; only a directory the OS reports as gone has its files marked missing |
| 47 | **A file symbolic link was hashed as its target** (C-011) — the row was the link (reparse point, size 0), but the hash stage opened the path, Windows resolved it, and the link joined the target's duplicate group offering the target's bytes as reclaimable | A junction or symbolic link to a file is handed to the engine as a link and never opened: `hash_status` `skipped_link`, no digest, no group, in both passes; the project summary counts links apart from unreadable files |
| 48 | **A file rewritten between listing and hashing kept the listed size beside the new digest** (I-005) — an inconsistent pair nothing marked stale | The hash stage compares the size at open time with the size listed; a difference is `CHANGED SINCE LISTING`, no digest, and the message says to scan again |
| 49 | **A garbage PDF date failed the whole PDF analysis** (E-008b) — pypdf raises on a `/CreationDate` it cannot parse | The date empties one field (`garbage (unparseable)`); the file is analysed |
| 50 | **A colourised terminal log was refused as binary** (Y-020c) — ESC counted as a control character; 180 colour codes in a 3 KB CI log | Terminal escape sequences are removed where text is decoded, so the gate sees text and the stored text is what a terminal showed |
| 51 | **An archive member named as text was decoded without a sniff** (Y-022b) — 100 KB of random bytes named `.txt` inside a zip became 102,419 characters of indexed "text" | A member named as text meets the same binary gate as a top-level file and yields a one-line note |

Two Phase 1 improvements followed: the allocated-size call is skipped for ordinary
files (self-validating per volume), and candidate-only identity narrowing is
available opt-in. **The walk is roughly 2× faster than B6.2.**

### Defects the adversarial corpus found — open, recorded as DEFECT cases

Each is asserted *as it behaves today* so the suites stay green and the case is
listed under `defects` in `C:\FOTest\CASE_RESULTS*.json`; the check fails the day
the defect is fixed, which is when the expectation moves. B7.2 fixed six of the
seven (defects 46–51); one remains, by decision.

| Case | Defect | Where |
|---|---|---|
| A-014 (= Y-009, Y-053, W-003) | A case-only pair in a case-sensitive directory folds into one row — `file_path` is unique on a lower-cased key. B7.1 (defect 43) made the fold stable and visible: the row is the first file's, the second is a `FOLDED_CASE_TWIN` event counted in the report. The per-directory case-sensitivity fix was **declined for B7.2 (2026-09-20)**; one row for two files remains, and the walk counts two | `fo_inventory` ingest; Hostile_Naming |

### Known limits

- **Text extraction covers 114 formats** — every document, email, OpenDocument, EPUB and Office variant, source and configuration, the documents inside zips, WordPerfect (verified on Tika's WP 4.2–6 files; 4.2 named `.doc` is not recognised, it has no signature) and OneNote (its UTF-16 strings, verified on Tika's ten files including three fuzzed), files with no extension, and **pages with no text layer and pictures that look like documents, by OCR** (Windows' engine; every page judged, poor scans flagged for a person). Not covered: anything the OCR gate calls a photograph; hidden state, formulas, names, comments, links, templates, embedded objects and VBA are read through or past, never recorded (the 69 SCOPE cases of the corpus — Phase 3's list). Text the product cannot extract is never searchable, **and the search says nothing about it.**
- **Analyzer runs cannot be scoped to a file subset** — analyzer keys only.
- **Upgrading fingerprints re-reads everything** rather than topping up. So does **Fingerprint again** — which is what makes it the one way to catch a file whose bytes changed under the same size and modified time.
- **A stopped run does not resume.** Everything it did is kept and the hub shows the gap, but running the stage again starts from its first file. The pre-run screen says so.
- **Text extraction is not run on the real corpus — the user's decision of 2026-09-19.** With OCR, pictures and archives all on, the estimate for it is **~15 h 17 min** (measured 2026-09-15, after the PDF analyzer was run again under B7 and its page-one text flags corrected: 305 PDFs with no text layer, not 348) — 1 h 26 for the PDFs (52 min of that OCR of **3,079** pages with no text layer, down from 9,868), 17 min for Office, 1 h 23 looking at 25,269 pictures, 3 min for text-like files, and **12 h 10 reading the documents inside 462 zips** (4.5 GB of scanned legal batches, OCR again). With OCR, pictures and archives switched off on the Options page it is **~23 minutes**. Declined, not deferred; the summary's Text line stays “not extracted” and says so. (`Docs\Validation\PHASE_2_REAL_CORPUS_RUN_2026-09-12.md` §7)
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

The user then asked about extraction — whether it covers every extractable
file or only the easy ones, whether 40 hours could be sped up, whether it includes
indexing, whether the computer stays usable, what happens to a file added
mid-run — and, on the answers, asked for everything: a faster PDF reader, an
honest estimate, and the missing formats. Same day:

| Asked for | Done |
|---|---|
| PDF text faster | PDF text through PDFium (already installed as a pdfplumber dependency): 4–13 ms a page against 125–190, the same text out; pdfplumber stays as the fallback |
| An estimate that does not say 40 hours for a short job | PDFs estimated by page count (defect 22); same corpus ~17 min |
| `.rtf .csv .json .html .doc .ppt .xls` | All read, by their bytes: 947 of the corpus's 1,263 `.doc` files turned out to be RTF inside and 10 of its 13 `.xls` are HTML exports — every one now reads as what it is. Word/PowerPoint 97-2003 are read directly from the OLE2 container; Excel 97-2003 through `xlrd` (optional) |

The corpus's extractable set went from 4,060 files to 7,900. The `phase2` branch
is still unpushed.

**2026-09-13.** The user asked for the rest: `.log .xml .vcf .ics`, files with no
extension, and email — `.msg`, `.pst`, `.eml` — and asked what else belongs and how
OCR would work. Built the same day, with `.mbox`, `.mht/.mhtml` and `.ost` alongside
because they are the same readers:

| Asked for | Done |
|---|---|
| `.log .xml .vcf .ics` | Read as text; XML kept raw so its element names stay searchable |
| Files with no extension | Read by their bytes (312 in the corpus: 280 text, 8 HTML, 24 pictures and programs); a picture with no extension is recorded as *not a document*, not as a failure, and the summary counts those apart |
| `.eml`, `.mbox`, `.mht` | Standard-library MIME parsing; every message rendered the same way — headers, body as text, then each attachment's text through the ordinary readers, forwarded messages included; an .mbox is read one message at a time |
| Outlook `.msg` | Read from its OLE2 streams — Unicode and 8-bit strings, dates from the fixed-property stream, attachments and embedded messages, HTML and compressed-RTF bodies. **Verified on messages written by Outlook 2016** (kept as a fixture) |
| Outlook `.pst` (and `.ost`) | `fo_pst.py`: a read-only reader written to [MS-PST] — both block encodings, both file layouts, data and subnode trees, heap, BTree-on-heap, property context; messages found by walking the node tree, folders by the parent chain; Outlook never involved, because Outlook opens a PST for writing. **Not yet verified on a real file** — Outlook automation hung on creating one and the corpus has none; the first real PST decides |
| The estimate | Non-PDF documents are now sampled by family (Office, email, text-like) — one sample across all of them swung between 17 minutes and an hour on the same project |

The extractable set is now 8,240 of the corpus's 41,056 files. An email's subject,
sender and date are its Title, Author and Created columns.

**2026-09-13, later.** The user asked for the remaining formats — OpenDocument, EPUB,
macro-enabled Office, source and configuration, the documents inside zips, WordPerfect,
OneNote — for OCR with Windows' engine and a flag for poor scans, and for `C:\FOTest`
to become a full test corpus with one file of every type. All built the same day:

| Asked for | Done |
|---|---|
| The formats | 113 in all. OpenDocument from `content.xml`; EPUB in spine order; every Office Open XML variant read raw from the package (also the fallback when a library refuses a `.docx`); fifty-odd source and configuration extensions; zips and 7z two levels deep with caps; WordPerfect (function codes stepped over) and OneNote (its UTF-16 strings) — those two by scanning, and **unverified**, a sample in `C:\FOTest\Samples` goes into the corpus on rebuild |
| OCR | Windows.Media.Ocr through `winocr` — free, offline, ~0.5 s a page — on PDFium renders of pages with no text layer, and on pictures that pass a document gate. **The quality judgement**: resolution, contrast, focus, speckle, the skew the engine measured, how much of the result reads as words, and ink-with-no-words; a score per page, reasons in plain words, `OcrReview = yes` under 70. Calibrated on a real 103-dpi scan and degraded copies. Columns on the Files page, a summary line with Show which, the Options page's three switches |
| The PST reader | Verified on Apache Tika's two test mailboxes (downloaded with the user's approval); one defect — embedded messages were not followed — fixed |
| `C:\FOTest` | `Corpus\` (344 files, 107 extensions: every image, RAW, audio, video, archive and document format the program names, scans clean and poor, a photograph, malformed files), `GROUND_TRUTH.json`, `Samples\` for real third-party files, `README.md`; the user's adversarial Master Matrix in `Research\` is the target its folders map onto. **Since grown into the adversarial corpus — see §4's "The adversarial corpus" and `C:\FOTest\README.md`** |

Word 2016 automation hung on every variant save (Excel and PowerPoint did not), so
the Word variants are derived; the rest of the Office fixtures are genuine and live
in `Resources\Fixtures`.

### The closeout — approved 2026-09-19

The living plan's own completion criteria, as the user decided them:

| Criterion | Status |
|---|---|
| Acceptance run against a meaningful real project | **Met** — Pre-Scan, Full Fingerprinting and all seven analyzers on the real 41,056-file folder, every estimate honoured; text extraction on it **declined by the user** (the capability is verified on the test corpus) |
| Shareable evidence reviewed | **Accepted as sufficient** — the summary, the failures list and the analyzers' columns read from real data in the third session |
| **Real user questions recorded** | **Met** — three sessions' notes recorded and acted on; the third asked the first questions of the corpus itself and each was answered from stored evidence; the user judged this sufficient |
| Analytical gaps classified | **Carried into the adversarial round** — the known limits are named; the seven engine defects the corpus found stand recorded as DEFECT cases; the round classifies the rest |
| High-value deficiencies corrected or deferred | **Done** — 32 defects fixed; the seven the corpus found are recorded for the round |
| Real source immutability confirmed | **Done — on the real folder**: 41,056 / 41,056 unchanged after every run |
| **"Demonstrably useful as an exploratory tool rather than merely technically functional"** | **Met by the user's verdict, 2026-09-19** |

**`Docs\Handoffs\P2.12_CLOSEOUT.md`** §5a records the decision and the three
choices behind it: extraction on the real corpus declined, not deferred; the
three sessions sufficient; the adversarial round (P2.13) the last part of Phase 2,
run on build B7, with fixes during it becoming B7.1, B7.2 … as in the B6 line.

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

### Decisions — 2026-09-20, the Phase 3 handoff

Settled with the project owner before the handoff was written, and treated
as settled in B8: first-round scope is Builds 1–2 only; the P3.R1/P3.R2
contradiction is resolved for P3.R2 — protection is a hard constraint, and
overriding it is its own explicit, confirmed operation; Phase 3 integrates
into the one window, not a separate program; the research's vocabulary
(Decision, Plan, Canonical, Keeper, Policy, Review, Protected, Reclaim) is
adopted as working names, expected to be renamed once real use has happened;
no copy is ever called the original.

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

**Phase 3, what comes after B8** — the remaining five builds of the Phase 3
plan, each with its own handoff: 3 review routing (queues: conflicts, needs
revalidation, blocked, deferred as routes; the "orphaned decision" count B8
already reports is the seed of "needs revalidation"); 4 bulk batch with
frozen membership and dynamic policy with preview; 5 the Image Game (the
perceptual hashes are already stored); 6 the plan builder, fingerprint and
the Phase 4 contract; 7 project / desired source metadata. Before Phase 4,
the two standing items from PHASE-2-HANDOFF: a cross-process lock, and the
autocommit connection revisited (Phase 3's store already uses real
transactions on the window's connection). Cheap follow-ups the handback
names: per-group re-resolution on the Decide page instead of a full recompute
per click (0.2 s for 4,000 groups today), and a "Reviewed" status for groups
whose every copy is protected.

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

**Done 2026-09-12, evening — extraction widened at the user's request**
11. PDF text through PDFium; the estimate counts pages; `.doc .ppt .xls .rtf
    .html .csv .json` read by their bytes; defects 21–24 ✔

**Done 2026-09-13 — the rest of the formats**
11a. `.log .xml .vcf .ics`, files with no extension, and email in every shape
     (`.eml .mbox .mht .msg .pst`); the estimate samples by family ✔
11b. OpenDocument, EPUB, Office variants, code and config, inside zips,
     WordPerfect and OneNote (unverified); OCR with a per-page judgement and
     the review flag; the PST reader verified on real files; the full test
     corpus at `C:\FOTest` ✔

**Done 2026-09-13 → 2026-09-19 — the adversarial corpus of the Master Matrix**
18. `Corpus_Design_Proposal_v1.0.md` (`C:\FOTest\Research\`): the research
    reconciled, the matrix's dispositions corrected against this machine, the
    program's vocabulary mapped, six questions answered by the user ✔
19. Release 0 into the one builder — 01 Naming, 02 Duplicates, 03 Links, 05
    Corruption, 06 Metadata, 07 File types, 08 Extreme structures, 19 Extraction
    safety — `cases` in `GROUND_TRUTH.json`, `p2_cases.py` asserting each in the
    program's own columns; the CSV export's formula guard (Y-018) ✔
20. `Hostile\` and `p2_hostile_check.py` — denied ACLs, junction loops, hard
    and symbolic links, reserved names, the case-sensitive directory, 10,000
    files, 4 GB sparse, 100,000 members, its own project and a canary ✔
21. `p2_mutation_check.py` — files created, deleted, renamed, rewritten, held
    and made unlistable *during* the walk ✔
22. The third-party samples (Tika, raw.pixls.us, OPF, py-pdf, mathiasbynens,
    Big Buck Bunny, ffmpeg.wasm, jonasclaes; `PROVENANCE.json`), the readers
    that had never met a real file fixed against them (defects 29–32) ✔
23. Release 1 — documents: hidden sheets, formulas with and without cached
    values, names, comments, hyperlinks, tables, charts, custom properties,
    a password to open (Excel-made), structure protection, templates,
    metadata against filesystem dates, hidden and deleted text, external
    workbook links in eight shapes, embedded objects, automation, an
    organisation's folders, a credential in a config file ✔
24. The parked rows declined with their reasons — 289 matrix rows: 199
    embodied, 90 declined, none untouched; `C:\FOTest\README.md` recomputed ✔

**Done 2026-09-19, evening — the build that governs the adversarial round**
25. Build **B7** tagged `phase2-b7` on the closeout commit and pushed to GitHub
    with branch `phase2`; the install verified byte-identical to the tag ✔

**Next — the last part of Phase 2**
26. **The adversarial round (P2.13)** ✔ — done 2026-09-19/20. The P2 stress test
    (the first two-root project) found defect 33, which had hidden half the
    project from every pass; fixing it uncovered 34–45 (B7.1); the six DEFECT
    cases the corpus had recorded became 46–51 (B7.2), each case now asserting
    what the matrix expects; A-014 stays by decision. Findings name the build.
27. **Then Phase 3** — next; the handoff follows from the closeout's §7, from
    the clean install `FileOrganizer-Phase2-B7.2\` (byte-identical to tag
    `phase2-b7.2`).

**Decided 2026-09-19 — the closeout**
12. Extract and index, or not — **not**, by the user's decision ✔
13. Ask the corpus things — the three sessions judged sufficient by the user ✔
14. Sign the closeout — **approved 2026-09-19** (`Docs\Handoffs\P2.12_CLOSEOUT.md` §5a) ✔

**Done 2026-09-15 — loose ends before adversarial testing**
15. **Scan again** on the summary (the Pre-Scan’s stages over the project’s own
    folders; new files present, vanished ones missing, changed ones re-observed),
    with **Fingerprint again**, **Analyze again** and **Extract again** beside
    their words — and the summary honest after a re-scan: defects 25–28 ✔
16. **B7** — one product version everywhere, `CHANGELOG-B7.md`, the window title
    shows the build ✔
17. The PDF analyzer run again on the real project (handoff item 2.2) — see
    `Docs\Validation\PHASE_2_REAL_CORPUS_RUN_2026-09-12.md` ✔

**Decisions still open**
- Does a re-run constitute a new project?
- Is consumer mode a preference or a separate edition?
- Does the journal need to be tamper-evident, or merely out of the way?
- Should text extraction cover more formats? 114 now, OCR included; the samples for WordPerfect, OneNote and the non-TIFF RAW formats are in and the readers verified. What remains is Phase 3's relationship layer (the 69 SCOPE cases) and the seven open defects above.
- The OneDrive placeholder (L-001/L-002/L-006) only the real corpus can hold: one "Free up space" file the user makes in `TOMMY STUFF`, then Scan again and Fingerprint again — `C:\FOTest\README.md` says how. The builder never touches the user's OneDrive.

**On GitHub:** branch `phase2` and tag `phase2-b7` pushed 2026-09-19
(`https://github.com/tfrancovitch/FileOrganizer`); the install is in step
(rework16 backup, manifest verified, byte-identical to the tag). `main` still
stands at the Phase 1 ship (`phase1-b6.2`); fast-forwarding it is a decision for
the end of Phase 2.

**Handoffs:** `Docs\Handoffs\PHASE_2_COMPLETION_HANDOFF.md` carries the environment,
the rules, the decisions already settled, the defects and the traps, for a session
picking this up cold; `Docs\Handoffs\PHASE_2_REMAINING_AND_ADVERSARIAL_HANDOFF.md`
(2026-09-13) is what to do next — the remaining Phase 2 items, and the adversarial
test corpus designed in `C:\FOTest\Research\`.

---

## 7. Where things live

```
C:\FileOrganizerTesting\
  PROJECT_PLAN.md              this document
  STAGE_MAP.md                 what each click actually runs
  FileOrganizer\               git repository - code source of truth (branch phase2 checked out)
  FileOrganizer-Phase1-RC-B6.1\  the Phase 1 install (BASELINE.md: it is B6.2)
  FileOrganizer-Phase2-B7.2\   the Phase 2 install, byte-identical to tag phase2-b7.2
  FileOrganizer-Phase3-B8\     Phase 3 work: git worktree of branch phase3 (B8)
  FileOrganizer-Phase3\        the Phase 3 handoff package (Builds 1-2)
  Docs\
    Charter\          the phase model, requirements, question catalog, decisions
    Specifications\   query model, information model, report catalog, GUI contract
    Research\         comparative studies, retention, undo, ecosystem
    Validation\       every test, benchmark and acceptance report
    Handoffs\         handoff and installation documents
    Reference\        the Phase 2 living plan, the GUI prototype, archived build
  _Archive\           superseded builds and documents, with an index
```

The test corpus is `C:\FOTest\Corpus` with `GROUND_TRUTH.json` beside it
(`C:\FOTest\README.md` says what is in it and how to rebuild it); the earlier
fixtures are under `C:\FOTest ARCHIVE\`.
