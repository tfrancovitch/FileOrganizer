# The File Organizer — Project Plan

**Single source of truth.** Where the project is, what was decided, what is next.
Supersedes the scattered planning documents; the detail they contain is preserved
under `Docs\` and indexed at the end of this file.

**Last updated:** 2026-09-11 (evening — the Dashboard build)
**Current position:** Phase 2 (Understand) — built and verified; closeout awaits a
session on real files
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
| **2** | **Understand** | **Built and verified; closeout awaits a session on real files** |
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
| **Dashboard checks** (`p2_dashboard_check.py`) | **93 / 93** — every run kind to completion and stopped, through the runner and through the real window |
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

Two Phase 1 improvements followed: the allocated-size call is skipped for ordinary
files (self-validating per volume), and candidate-only identity narrowing is
available opt-in. **The walk is roughly 2× faster than B6.2.**

### Known limits

- **Text extraction covers six formats** — `.pdf .docx .pptx .xlsx .txt .md`. Text in a `.csv`, `.json`, `.log` or extensionless file is never extracted and never searchable, **and the search says nothing about it.** Blocking for any legal claim.
- **Analyzer runs cannot be scoped to a file subset** — analyzer keys only.
- **Upgrading fingerprints re-reads everything** rather than topping up.
- **A stopped run does not resume.** Everything it did is kept and the hub shows the gap, but running the stage again starts from its first file. The pre-run screen says so.
- **Cancel is honoured between files, never during one.** A single very large file finishes before the stop takes effect.
- **One unexplained outlier:** a 100,000-file scan took 2,629 s once and 124.7 s every time since. Not reproduced; cause unknown.

### What remains before Phase 2 closes

The living plan's own completion criteria:

| Criterion | Status |
|---|---|
| Acceptance run against a meaningful real project | Partial — purpose-built corpus, not real data |
| Shareable evidence reviewed | Partial |
| **Real user questions recorded** | **Not done** |
| Analytical gaps classified | Partial |
| High-value deficiencies corrected or deferred | **Done** |
| Real source immutability confirmed | **Done** |
| **"Demonstrably useful as an exploratory tool rather than merely technically functional"** | **Not done** |

**The two outstanding items require a person using it on real files.** The
Dashboard is built for exactly that session, and
`Docs\Validation\PHASE_2_REAL_USE_RECORD.md` is where its questions and findings
go. Then **P2.12 — Closeout and Phase 3 Handoff** formally ends the phase.

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

**To close Phase 2** — these need a person on real files; an agent cannot do them
6. Use it on a real corpus and fill in `Docs\Validation\PHASE_2_REAL_USE_RECORD.md`
7. P2.12 closeout and Phase 3 handoff

**Decisions still open**
- Does a re-run constitute a new project?
- Is consumer mode a preference or a separate edition?
- Does the journal need to be tamper-evident, or merely out of the way?
- Should text extraction cover more formats? Email (`.msg`, `.pst`, `.eml`) is the largest gap for legal use.

**Unpushed:** the `phase2` branch is **23 commits** and exists only on this machine.

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
