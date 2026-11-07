# Phase 4 synthesis: the research, the gaps, the design decisions and the build plan

**From:** the Architect (Claude Code, Fable 5.1) · **Date:** 2026-11-07 · **Status:** D1-D5 approved by the owner 2026-11-07, all as recommended (`Docs\DECISIONS.md`, "Phase 4 scope"); next is review gate 1 (a different-model review; paste brief in `Docs\Research\phase_4\R4\R4_REVIEW_GATE1_BRIEF.md`), then the B21 handoff.
**Inputs:** `STATUS.md`, `AGENTS.md`, `PHASE4_SCOPING.md`, `R4_SCOUT_ANSWERS.md`, the five R4 reports in `Docs\Research\phase_4\R4\`, `PROJECT_PLAN.md` §1-§3 and "Undo, when Phase 4 arrives", `P3_CLOSEOUT.md` §1, §4-§7, `ARCHITECTURE.md` (shape, source safety, Plan Builder, B20), `02_PHASE_4_GUIDE.md`, the corpus's R13 register and R14 open questions, the 2026-11-01 and 2026-11-07 dev notes. Nothing here changes a source file. Nothing is committed.

---

## 0. Short version

**The research is sufficient. No further desk research is needed before building.** All five reports delivered what their briefs asked for, with sources. What remains unknown is *empirical* (how Windows, NTFS, OneDrive and this Python behave on this machine), and the corpus is explicit that the test matrix, not documentation, decides those. Those unknowns become the first units of B21 and B22, plus one hand probe that only Thomas can run.

**One finding changes the approved v1 boundary, and it needs Thomas's decision first.** The approved boundary excludes "cloud-root moves" and "any file that is not fully local". Thomas's own 41,056-file corpus lives inside a OneDrive sync root (Known Folder Move puts `Documents` there). Read strictly, v1 could do nothing on his own files, and B27's "real-use session on a copy" would be a session on a copy made somewhere else. R4.6 gives a safe middle path: **allow rename and move *inside* the same sync root for files verified fully local at the moment of action, after a hand probe and a Python probe both pass; block moves out of the root, deletes and the Recycle Bin there.** This document recommends that amendment (decision D1 in §8).

**The other decisions waiting for Thomas** (all in §8, each with a recommendation): D2 where removed files go (an app quarantine folder per source root; never an automatic purge); D3 the first metadata writer (PDF title; pure-Python `pypdf` incremental write as the product's writer, ExifTool as the independent test oracle); D4 where the journal lives (a local, never-synced folder; refuse network and OneDrive paths); D5 whether review gate 1 also covers P3.R12.

**The build order B21-B27 stands**, with three additions: a probe unit at the start of B21 and of B22; a Thomas-run OneDrive probe before B22; and three small Phase 1 engineering changes carried inside B22 and B23 (sync-root detection as an environment fact, placeholder visibility at start-up, a scan exclusion for the quarantine folder).

---

## 1. What the product is for

`PROJECT_PLAN.md` §1: *point it at a folder and it builds a complete, verifiable record of what is there; then it answers questions about that record.* Tagline: **Inventory. Organize. De-duplicate.** Two properties set it apart from a disk-space utility: **it does not modify source files** (verified byte-for-byte after every pipeline), and **it says when it does not know** (every answer that touches an unread part of the corpus is stamped incomplete, with a count).

The 19 axioms (§2) govern everything below. The ones that bear most on Phase 4: **1** reality outranks the database; **2** observation, understanding, decision and action are different kinds of work; **7** human authority governs consequential decisions; **10** mutating a source file is an action, including a rename or an embedded-metadata change; **11** every Phase 4 action must be reversible; **12** resumability is mandatory; **13** completeness is a property, not an assumption; **14** cloud-only content must not be silently hydrated; **17** no future AI may become the action authority; **19** not everything needs a screen.

The project's working posture, set by the owner: verifiability, redundancy, security boundaries, designated roles and adversarial checks over speed. "Receipts over claims; unverified said plainly." That is the posture this synthesis takes.

## 2. What Phases 1-3 were for, and what they left

| Phase | Purpose | Shipped as | What it hands forward |
|---|---|---|---|
| **1 Observe** | A complete, verifiable inventory: every file, its properties, identity (volume serial + file index + link count), hash, whether it is unique or a duplicate. Reads only. | B6.2 | The evidence record Phase 4 must re-verify against: `file_observation` / `file_state` with size, times, attributes, reparse tag, volume serial, file index, hard-link count, SHA-256. Placeholder recognition by attribute bits (B6.2 P4b), never exercised on a real cloud-only file. |
| **2 Understand** | Answer questions about the record (the hub), analyzers over 114 formats, duplicates completely. Data-only (axiom 9). Three real-use sessions, closeout 2026-09-19, adversarial round closed with B7.2. | B7.2 | The one-window Dashboard and runner that Phase 4's screens (B25) will live in; the single-writer rule (documented, not enforced). |
| **3 Decide / Plan** | "A small, durable intent-and-planning engine": evidence in, attributable human intent recorded, an immutable fingerprinted Plan out. Never reads content to decide, never changes a file. Closed 2026-10-13 on test evidence (150 matrix rows, 69/69 mutants killed). | B20 (`phase3-b20`, `045a0a1`) | A **locked plan revision** of logical intent: `remove_location`, `move_location`, `rename_location`, Update Source Metadata items; each with typed preconditions, the **live-stage conditions declared and never evaluated**, postconditions, dependencies; every move/rename/metadata item held at `unsupported_action` or blocked because no executor or writer is certified. |

Phase 3's closeout §7 is the Phase 4 seed. A locked revision guarantees that its items are exactly what a person locked and that they verify by fingerprint; **it guarantees nothing about the filesystem now** (P3-A87). Carried to Phase 4 by the owner's decision (§5a.5): the cross-process action lock; the journal's transaction boundary; writer certification per format and field; a certified move/rename executor; location succession and decision fulfilment; the keeper-move ordering rule; the case-only rename method; collision policies other than fail; cross-volume and cloud-root moves.

## 3. The existing build, reviewed for Phase 4

What B20 already gives Phase 4, and what it lacks. Line references are the scout's (`R4_SCOUT_ANSWERS.md`).

**Strengths to build on**
- The plan contract is already typed. Every Remove, Move, Rename and Metadata item carries evidence-stage preconditions with results and a **declared live list** (`registry.py` 676-750): `target_exists`, `target_content_matches`, `source_root_available`, `permissions_sufficient`, `action_lock_available`, `no_conflicting_run`, `rollback_ready`, `keeper_content_matches`, `destination_available`, `free_space_sufficient`, `filesystem_capability_supported`, `source_identity_matches`, `companions_present_unchanged`, `source_local_if_required`, `same_volume`. Phase 4 fills in evaluators; it does not invent a vocabulary.
- Postconditions are declared too (`registry.py` 776-783): `destination_exists`, `source_path_absent`, `physical_identity_unchanged`, `destination_content_matches`, `no_other_file_replaced`, `project_state_updated`, `rollback_record_exists`, and for metadata `desired_metadata_observed`.
- Identity is already stored: volume serial, file index, link count, size, times, attributes, reparse tag, SHA-256.
- The plan is immutable once locked, fingerprinted, and goes obsolete on dependency change. Blocked-by-default is the rule: unknown never passes. Protection is a hard constraint lifted only by an explicit, action-scoped override.
- Every Phase 3 write is one explicit transaction (`store._run`); the database is the authoritative state and every export is rendered from it.
- `file_path_id` is keyed on `(source_root_id, lowercased relative path)` (`fo_inventory.py` 50-51, `003_inventory.sql` 201). **Consequence: a case-only rename keeps the same `file_path_id`.** Only the display spelling changes; no succession record is needed for that case.

**Gaps Phase 4 must close (none is a research gap; each is an engineering item with a build named in §9)**
1. `synchronous = NORMAL` on every connection (`fo_db.py` 301-338): survives a program crash, not a power cut. Fine for a rebuildable catalog; not for a journal that must say "about to move this file" before the move. → B21 (separate journal, `FULL`).
2. No cross-process lock; two Dashboards can open one project (`closeout §6`). → B21.
3. Identity is Python's `st_ino` as a decimal string, 64-bit on the builds that wrote it; the strongest Windows identity is 128-bit `FileIdInfo` plus a 64-bit serial. On this machine Python is 3.14.6 and `st_ino` is now the wide form (receipt: a temp-file stat returned a 54-bit value and a 64-bit `st_dev`). → B22 stores `FileIdInfo` in the journal and compares the low 64 bits with the Phase 1 record; probe P4 confirms the relation.
4. No scan exclusion mechanism of any kind (`fo_scan.py`): only junctions and symlinks are skipped. A quarantine folder inside a source root would be re-inventoried as new duplicates. → B23.
5. No sync-root awareness. Phase 1 recognises placeholders by attribute bits only (`win_meta.py` 109-119) and cannot say whether a path is *inside* a OneDrive root. The real corpus has never had a cloud-only file scanned (L-001/L-002/L-006 never exercised). `python.exe` runs in placeholder "disguise" mode by default (R4.6 §2.2); whether the `RECALL_*` bits stay visible there is undocumented. Phase 1 did see `Pinned`/`Unpinned` bits on the real corpus (defect 42), so at least those survive disguise. → B22 (sync-root detection; `PHCM_EXPOSE_PLACEHOLDERS` at start-up with a self-test), and the Thomas probe.
6. No notion of path succession; a file renamed outside the tool is a new location (A139). → B24.
7. The single-writer rule is documented, not enforced; `no_conflicting_run` and `action_lock_available` are declared, not implemented. → B21.
8. `LongPathsEnabled` is 0 on this machine (receipt, registry read), and the python.exe on PATH carries a `longPathAware` manifest (receipt). So paths over 260 characters work only with the `\\?\` prefix, which the executor must add itself (R4.1 §3.6). → B22 preflight.

## 4. The research, reviewed

### 4.1 What went into Phases 1-3 and the 2026-09 Phase 4 corpus
- **Phase 2 research** (comparative systems, GUI study, preservation note) shaped the hub and the analyzers; closed with its own review.
- **Phase 3 research** P3.R1-R11 plus five technical evaluations and the P3.R12 organizing-intent round (with its own evaluation). All of it, evaluations included, was written by Claude (separate subagents). A different-model review was offered and never done (closeout §5a.6). Phase 4 leans on P3.R7 (the plan contract) and P3.R12 (move/rename intent) directly.
- **The 2026-09 Phase 4 corpus** (31,000 words, stitched Q&A): settles the general engine shape (preflight → authorize → execute → verify → journal → recover; the result enum; 12 validation categories; collision as policy; "never call it a transaction"; sharing violations as normal outcomes). Its second half (GCC High migration) is out of Phase 4 scope by decision. It ends with 16 open questions it calls product decisions, not research; §8.4 below answers or defers each.

### 4.2 The five R4 reports against their briefs

| Report | Brief asked for | Delivered | Where it stands |
|---|---|---|---|
| **R4.1** (8,300 words; brief 3,000-5,000) | call × property table; one primitive with exact sequence; probe list | All three. Table of 8 calls; handle-bound `SetFileInformationByHandle(FileRenameInfo, ReplaceIfExists=FALSE)` recommended with a 7-step sequence; 12 probes P1-P12 | Strong on primary sources (MS-FSA, WDK, Win32). Honest that **no Microsoft text documents the no-overwrite check as atomic under a racing creator** (a Microsoft Q&A staff answer, 2026-10-28, secondary). NTFS tunneling identified as a real hazard for verification and undo (15 s default, from a 2003 KB mirror; registry confirms defaults are in force here). CFA error code and rename coverage undocumented. |
| **R4.2** (4,500; 2,500-4,000) | Recycle Bin vs quarantine table; placement rule, manifest, retention; questions for Thomas | All three. Quarantine per source root; manifest outline; never auto-purge; five plain-language questions | Microsoft never documents when the shell destroys instead of recycling; everything about "too big / removable / network / disabled" is secondary. That weakness is why the Recycle Bin loses. Depends on R4.6 for the OneDrive case. |
| **R4.3** (7,000; 3,000-4,500) | pragmas and layout with commit points; reconciliation table; one lock with crash probes | All three. Separate journal file, WAL + `FULL`; commit points C0-C4; reconciliation tables A (same-volume), B (cross-volume), C (metadata), D (job); `msvcrt.locking` byte lock plus owner row; probes L1-L12, J1-J7 | SQLite facts verified against sqlite.org. Key honest finding: **no non-admin Windows API documents that a same-volume rename is durable when the call returns**; recovery must treat disk state as evidence, which the tables do. One inconsistency with R4.1 (it names `MoveFileExW` as the move call); R4.1's handle-bound primitive wins, see §8.2. |
| **R4.6** (5,600; 2,500-4,000) | per-operation table; v1 allow/block; a hand-probe list for Thomas | All three. Table of 11 operations; option B recommended with A as fallback; 8-step hand probe on one throwaway file | The weakest evidence base, by the nature of the subject: Microsoft documents only "reading data hydrates" and "moving out of the root hydrates and removes from OneDrive". Everything else is **untested**. The report says so at every row. Two findings matter now: `python.exe` is in disguise mode by default; "fully local" is knowable from attribute bits but only as a snapshot (Storage Sense can dehydrate between check and action). |
| **R4.8** (4,400; 2,500-4,000) | ranked pairs and a first pair; library with exact procedure; certification checklist and corpus | All three. PDF/title first; ExifTool `-o` recommended with `pypdf` incremental as the alternative; 15-point checklist C1-C15; four public corpora plus synthetic cases | Verified the decisive property: a PDF incremental update leaves the original as a **byte prefix** of the new file, so certification can assert "old bytes untouched" exactly. `ReplaceFileW` semantics verified (new file ID; creation time, ACLs, streams kept). "How often a person needs each field" has no data; that criterion is judgement. |

**Confidence and provenance notes for the reviewer.** Every report labels each claim verified / reported / inferred / untested and ends with a source ledger; four ran over their word targets because of tables and probe lists, not padding. Two Microsoft Q&A threads cited (R4.1 and R4.3) are dated 2026-10-27 and 2026-11-02 and ask exactly our questions; they are staff answers but secondary, and nothing in this synthesis rests on them beyond "Microsoft documents nothing stronger". All five reports and this synthesis were written by Claude; **review gate 1 is the first different-model look at any Phase 4 design**.

## 5. What Phase 4 is for

**Act and Verify.** Take a locked Phase 3 plan revision, re-verify each item against live reality immediately before acting, carry it out through a journaled, verified, reversible process, and record what happened so that a crash, a power cut or a person's change of mind leaves the files in a state the product can explain and undo. It is the only phase that ever changes a source file (axiom 10), and so the one that carries the project's risk.

In the terms of the corpus: Phase 3 Evidence Preview = zero source reads; Phase 4 Live Preflight = mandatory fresh reads immediately before execution; result enum, not boolean; journal for recovery, not a claim of transactions.

What "done" means for the phase (proposed; the closeout decides): the four pre-Phase 4 engineering items exist and are tested (lock, journal boundary, one certified writer, one certified executor); every v1 action has undo; the adversarial round found nothing that moves, overwrites or loses a file outside the plan; a real-use session on a copy of real files went as the journal says it did; everything outside v1 still reads `unsupported_action` or blocked, with the reason shown.

## 6. Gap review: the research against the purpose

Measured against §5 and the axioms, the gaps fall into three kinds.

**A. Scope gaps (decisions, not research)**
- **A1. The sync-root boundary (the big one).** See §0 and D1. Research basis: R4.6 §4.
- **A2. Removed files**: Recycle Bin or quarantine, and retention. Research basis: R4.2. → D2.
- **A3. The writer**: external ExifTool or pure Python. Research basis: R4.8 §3. → D3.
- **A4. Journal location** and the refusal of network and OneDrive paths. → D4.

**B. Empirical gaps (probes, not research)** — every "untested" in the reports resolves to one of these, and each is already written as a probe with setup, action, expected result and the meaning of a different result:
- R4.1 P1-P12 (struct layout, no-overwrite error codes, race behaviour, case-only rename, identity relation, read-only and held-open files, long paths, what survives a rename, OneDrive attribute-only open, CFA, tunneling window, same-volume check, journal crash points) → **B22 unit 1**, except P8 and P9 which need Thomas.
- R4.3 L1-L12, J1-J7 (lock release on kill, sleep, two users, racing acquisitions; journal durability under kill and VM power-off; the cost of `FULL`) → **B21 unit 1**.
- R4.6 Python probes 1-3 (do `RECALL_*` bits survive disguise; does `os.stat` or a `DELETE` open hydrate; does a raw move inside the root hydrate) and the 8-step **hand probe** → **Thomas, before B22**, on one throwaway file in a `FO_Probe` folder. The builder writes the read-only probe script; Thomas runs it. The sandbox rule stands: no automated test ever touches his OneDrive.

**C. Architect answers (R4.7, R4.9, R4.11, R4.12)** — written in §8.2-§8.3 below.

**What is not a gap.** Cross-volume moves, other collision policies, folders as units, network and removable drives, templates, GCC High: all deliberately outside v1 and already researched enough (corpus R1-R2, R4.3 table B) to be built later without a new research round. Mutagen and olefile write behaviour, Restart Manager for lock owners, ReFS and exFAT rename semantics: not needed for v1; noted for later rounds.

## 7. Is more research needed?

**No.** The decision rule: a question goes back to research only if a document could settle it and no document has been read. Every remaining unknown fails that test; it is either a product decision (§6 A), or behaviour Microsoft does not document and only a probe can show (§6 B). Spending more on desk research now would buy inferences, not facts.

Two cheap receipts were taken during this synthesis instead of a research brief (one temp file under `%TEMP%`, deleted after): Python on PATH is 3.14.6 with a `longPathAware` manifest; `st_ino` is the wide form; `LongPathsEnabled` is 0; the tunneling registry values are unset (defaults in force). These are recorded in §3 and feed B22's preflight.

Three optional lookups are cheap enough for a Haiku scout if a reviewer asks: the current CPython `os.rename` flags (R4.1 relies on a 2012 commit); the HRESULT `CfGetSyncRootInfoByPath` returns outside a sync root; the exact `FILE_RENAME_INFO` offsets from the installed SDK headers. None blocks the B21 handoff; P1 and the probe scripts find them out anyway.

---

## 8. The synthesis

### 8.1 Decisions for Thomas

Each with the recommendation first. One at a time is fine.

**D1. Files inside OneDrive (amends the approved v1 boundary). — APPROVED by the owner, 2026-11-07** (recorded in `Docs\DECISIONS.md`, "Phase 4 scope").
*Recommend:* allow **rename and move inside the same sync root** for a file **verified fully local at the moment of action**, only after the hand probe (R4.6 §6) and the Python probe both pass; otherwise fall back to blocking everything in sync roots. Block, in every case: moves out of a sync root, deletes and the Recycle Bin inside one, any action on a cloud-only file or on a folder carrying the recall bit, and any path whose sync-root status is *unknown*. Quarantine inside a sync root goes to a quarantine folder inside that same root (R4.2), so it is a rename inside the root, under the same gate.
*Why:* without it, v1 cannot touch the owner's own files, and the product's first real use of its riskiest phase would happen on a corpus unlike his. With it, axiom 14 is still met (no data read of a non-local file; move-out blocked), axiom 11 is met locally (rename back), and the journal records "local done; cloud state unknown" honestly.
*What it costs:* a sync-root detector (`CfGetSyncRootInfoByPath` via `ctypes`), an attribute re-read inside the executor step (before and after), the `PHCM_EXPOSE_PLACEHOLDERS` start-up call with a self-test, and acceptance that OneDrive's own behaviour (conflict copies, delayed upload, rename semantics) is observed, not controlled. In plain words: the tool will rename and tidy files that are already fully on your disk, inside your OneDrive folder, and will refuse to touch anything that is only in the cloud or to move anything out of OneDrive.

**D2. Where a removed file goes. — APPROVED by the owner, 2026-11-07, as recommended** (quarantine; 30-day purge guard; restore stops on an occupied old path; missing folders re-created only as approved steps).
*Recommend:* an app quarantine, one hidden folder per source root (`<root>\.FileOrganizer-Quarantine\<entry-id>\<original name>`) with a marker file and a manifest per entry; moved by the same rename primitive, no overwrite, same volume checked per file; **no automatic purge ever**; purge is a separate explicit action by Thomas, refused for entries younger than 30 days unless he overrides by name, and refused for any entry whose surviving copy no longer verifies (the quarantined file would be the last copy). Restore refuses an occupied old path and never re-creates a deleted parent folder on its own; both become a separate, approved step. No Recycle Bin in v1.
*Why:* the Recycle Bin cannot be driven or restored reliably by a program, evicts on its own, and does not exist for removable or network drives (R4.2; `PROJECT_PLAN` "Undo" already says it is a courtesy, not a mechanism). Quarantine makes axiom 11 structural: nothing leaves the disk until Thomas says so.
*Plain words:* "Remove" will move the duplicate into a hidden folder next to your files. It keeps taking space until you choose to delete it for good, and the tool will never do that by itself.

**D3. The first metadata writer: PDF title. Which writer? — APPROVED by the owner, 2026-11-07, as recommended** (`pypdf` incremental; XMP `dc:title` only where a packet exists; `ModDate` may change).
*Recommend:* **`pypdf` with `incremental=True` as the product's writer**, `pikepdf` (or `qpdf --check`) as the mandatory independent second reader, and **ExifTool kept out of the shipped product and used only as a test oracle** in B26's certification corpus. Order of the procedure exactly as R4.8 §4 (stage on the same volume, write to a new file, verify C1-C15, `ReplaceFileW` with a backup name and no ignore flags, re-verify, keep the backup).
*Why:* the certification is about the procedure and the checks, not the library (R4.8), and the byte-prefix test applies equally to both writers. `pypdf` is already a dependency, pure Python, BSD; ExifTool is an external Perl program with no download checksum on its install page and a parser RCE in its history (fixed 2021), which sits badly with `Security Considerations.md` and the project's "no external execution" posture. If B26's corpus shows `pypdf` failing the prefix test on real files, the writer-agnostic certification lets the Architect bring ExifTool back as a decision, not a redesign.
*Open inside D3:* whether `ModDate` may change; whether to write XMP `dc:title` when an XMP packet exists (R4.8 says yes, only then). Recommend both as R4.8 states.

**D4. Where the journal lives. — APPROVED by the owner, 2026-11-07, as recommended.**
*Recommend:* `<project>.journal.db` (WAL, `synchronous=FULL`), its `-wal`/`-shm` and `<project>.lock` in a **local, never-synced, non-network application-data folder keyed by project ID**; the catalog stores the journal ID and the journal stores the project ID; mismatch is a hard stop; a journal path on a network drive or inside a OneDrive folder is refused with a clear message (R4.3 §5.1). Backups of the journal only through SQLite's backup API, never a raw copy.
*Why:* durability must not depend on a sync client copying `.db` files without their `-wal`; and a catalog restored from backup must not erase the record of actions already taken.

**D5. Review gate 1 scope. — APPROVED by the owner, 2026-11-07, as recommended.**
*Recommend:* put this synthesis to a different-model reviewer (ChatGPT or Gemini, free tier) **together with** the P3.R12 disposition (closeout §4) and the plan contract (closeout §7), since R4.12 leans on both and neither has had a non-Claude review. Record who reviewed, what was raised, what was accepted or rebutted, and why (AGENTS.md).

### 8.2 The engine, as the reports settle it

**The primitive (B22).** R4.1 option C: open the source once with `CreateFileW(DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, share FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT)`; read `FileIdInfo`, `FileBasicInfo`, `FileStandardInfo`, `FileAttributeTagInfo` from that handle; compare with the Phase 1 record; `SetFileInformationByHandle(FileRenameInfo, ReplaceIfExists=FALSE, absolute destination)`; close; reopen the destination attribute-only and re-read identity as the postcondition; confirm the old path is gone. Undo is the same sequence reversed after a fresh identity check. No data access is ever requested, so hydration is not asked for (axiom 14; P8 confirms). `os.rename` is the documented fallback only if P1 shows the handle call cannot be made safely, and only by Thomas's decision. R4.3's mention of `MoveFileExW` is superseded by this; `MOVEFILE_WRITE_THROUGH` promises nothing for a same-volume rename anyway.

Expected side effects of a rename, to be listed as such and never "repaired" (axiom 10): `ChangeTime` changes, `ARCHIVE` is set, parent-directory times change, and **creation time may change through NTFS tunneling** if the destination name was vacated within the last ~15 s. Rule: the postcondition compares creation time only when the journal shows no name vacated at that destination in the preceding 30 s; a hit is reported as a finding; a plan item whose destination was just vacated waits.

**Identity (R4.5).** The key is `FileIdInfo` (128-bit ID + 64-bit serial); size, times, attributes (with `ARCHIVE` masked), link count and reparse tag corroborate; the volume GUID path is captured beside the serial. At action time the journal stores the full tuple; the comparison with Phase 1's 64-bit `file_index` uses the low 64 bits (P4 confirms the relation on NTFS). File IDs are reused after deletion, so the ID alone is never identity.

**Preflight refusals, path-only, before any handle (R4.1 §5 step 1):** reserved device names with or without extension; names ending in space or period; reserved characters; a case-sensitive directory (`FileCaseSensitiveInfo`); drive not `DRIVE_FIXED`; file system not NTFS; source and destination on different volumes; any reparse tag or recall attribute on the source (unless D1's gate passes); destination name vacated in the last 30 s. Over 260 characters: always the `\\?\` form, built by the product.

**Environment blockers (R4.10).** `ERROR_SHARING_VIOLATION` (32): bounded retry (a few attempts over a few seconds) only when the file's mtime is recent, else `BLOCKED_LOCKED` and report. `ERROR_ACCESS_DENIED` (5) is ambiguous (permissions, read-only, Controlled Folder Access): `BLOCKED_ACCESS`, with "possibly Controlled folder access" when the path is under a profile Documents/Pictures/Music/Videos/Favorites folder and Defender event 1123 is readable; the product never adds exclusions or clears attributes (axiom 17 flavour: no self-granted authority).

**The journal (B21).** Separate file, WAL + `FULL`, explicit `BEGIN IMMEDIATE`/`COMMIT`. Entities as the corpus: Job, OperationItem, EvidenceSnapshot, OperationEvent, RecoveryRecord, plus an owner row. Commit points, in order and never merged: **C0** job approval with manifest hash → preconditions and snapshot → **C1** intent (snapshot, before-state, recovery path, item `INTENT`) → the one file step → **C2** result (native call, Win32 error, observed post-state) → verify → **C3** verification and terminal item status → **C4** job close. Name it "journalled intent and verification", not a transaction. Recovery on every start: classify each non-terminal item by R4.3's tables A-D (`INTERRUPTED_NOT_APPLIED`, `APPLIED_UNRECORDED`, `UNKNOWN_AFTER_INTERRUPT`, `BLOCKED_COLLISION`, `PRECONDITION_DRIFT`, `APPLIED_UNVERIFIED`, `RESULT_NOT_ON_DISK`, `FAILED_NOT_APPLIED`, `CHANGED_AFTER_FINAL`), show, and **act only when the person chooses** (axiom 17); every resume or roll-back is itself a journalled, reversible action; a retry is a new step with its own intent, never a replay.

**The lock (R4.4, B21).** `<project>.lock` held with `msvcrt.locking(LK_NBLCK)` on byte 0 for the life of any execute, recovery or undo run; owner information in the following bytes and mirrored in the journal's owner row, which also supplies a fencing token checked at C1/C2/C3. Scans and plan edits take the lock briefly. A second Dashboard retries for 5-10 s (the OS may release a dead holder's lock late), then opens read-only with a banner naming the holder; no "break lock" button in v1. Rejected: a named mutex (misses a second Windows user; ACL and squatting issues), `BEGIN EXCLUSIVE` (cannot span the journal's commits), a lease alone (dead window; fencing after sleep).

**Cloud (R4.6, D1).** Sync-root detection by `CfGetSyncRootInfoByPath` on the parent folder; registry `SyncRootManager` as corroboration; any other failure is *unknown*, which blocks. "Fully local" = `RECALL_ON_DATA_ACCESS`, `RECALL_ON_OPEN` and `OFFLINE` all clear, read from directory enumeration (which carries `RECALL_ON_OPEN`), re-read inside the executor step before and after the call. `PINNED` alone is not "local" (pinned-but-downloading exists). The process calls `RtlSetProcessPlaceholderCompatibilityMode(PHCM_EXPOSE_PLACEHOLDERS)` at start-up and self-tests on a known cloud-only file once Thomas has made one; it changes nothing on disk. Post-action scans must not treat OneDrive `-PCNAME` conflict copies as the product's own.

### 8.3 The Architect's answers (R4.7, R4.9, R4.11, R4.12)

**R4.7 The partial-execution contract.**
- A **job** is the ordered item list of one locked revision (or a person's subset of it). Items are independent unless a dependency is declared: a chain vacator (B20's chain exception), a sidecar companion unit, or a keeper whose removal depends on it.
- **Item states:** `PLANNED → PREFLIGHT_READY | STALE | CONFLICT | BLOCKED | UNSUPPORTED | UNKNOWN` (the corpus enum, from live preflight) `→ INTENT → APPLIED → APPLIED_VERIFIED` or `FAILED_NOT_APPLIED`, plus the recovery states of §8.2, plus `SKIPPED` (by the person) and `BLOCKED_DEPENDENCY`.
- **Job states:** `APPROVED → RUNNING → PAUSED | INTERRUPTED → COMPLETED | COMPLETED_WITH_EXCEPTIONS | ABANDONED`.
- **Stop-or-continue rule for v1: any outcome that is not `APPLIED_VERIFIED` or an *expected* refusal pauses the job.** Expected refusals are the classified ones the preflight predicted could happen (`BLOCKED_LOCKED`, `CONFLICT`, `STALE`, `BLOCKED` cloud-only); they mark the item and the job continues to the next independent item. Anything else (a native error not in the classification, a verification mismatch, `UNKNOWN_AFTER_INTERRUPT`) pauses the job with the item's evidence shown; the person chooses resume (skip it), retry (a new step), or stop. Items 8-20 after a pause stay `PLANNED`; nothing was done to them. An item whose dependency did not reach `APPLIED_VERIFIED` is `BLOCKED_DEPENDENCY`.
- **"Done"** means every item is terminal; the job is `COMPLETED` only if every item is `APPLIED_VERIFIED`, else `COMPLETED_WITH_EXCEPTIONS` with the list. The screen says both counts.
- **Undo of a part-done job** is per applied item, in reverse order, each its own journalled action (R4.9); unapplied items have nothing to undo; a job-level "undo everything applied" is that loop with one confirmation.
- **The keeper-move ordering rule:** within one job, move items run before the removal items whose keeper they move, and a removal whose keeper's move did not verify is `BLOCKED_DEPENDENCY`. B20 already refuses a removal whose keeper has an organize item in the same plan (`keeper_not_moving`), so in v1 the rule mostly orders; the full dependency only matters once that Phase 3 block is relaxed.

**R4.9 The undo model.**
- The undo record is the journal's own pre-image and post-image of the item: paths, `FileIdInfo` tuple, size, times, attributes, link count, and for metadata the backup path and the before/after hashes; for quarantine, the manifest entry.
- **Undo is an action.** It gets its own job and items, goes through the same live preflight (source = the file at its new place must match the post-image; destination = the old path must be free; same volume; not cloud-only; lock held), the same C1-C3 commits, the same verification. It is reversible: undo of an undo is a redo, another action under the same rules.
- **Undo is impossible, and the screen says which:** the file at the new place no longer matches the post-image (changed since; "the file has changed since the tool moved it"); the old path is occupied by a different file ("the old place is taken"); the old folder is gone ("the folder it came from no longer exists" — re-creating it is an approved extra step, D2); the quarantine entry was purged ("deleted for good on <date> by you"); a metadata backup's hash no longer matches ("the file has been edited since"); a creation-time tunneling hit at the old name ("another file just left that name; wait 30 s or proceed knowing the created date may change").
- Metadata undo = restore the backup, only while the live file's hash equals the journalled after-hash; otherwise it is a *new* "set the title back" action, labelled as such, never "undo" (R4.8).

**R4.11 The live preflight mapped.** Each Phase 3 live condition, the corpus category it belongs to, when it runs (**Preview**: at screen open, path-based, no handle; **JIT**: inside the executor step, on the handle) and its result values. Unknown blocks at both stages, as at the evidence stage.

| Phase 3 live condition | Category | Stage | Result |
|---|---|---|---|
| `target_exists` | 2 source resolution | Preview + JIT | `READY` / `STALE` (gone) |
| `source_identity_matches`, `target_content_matches`, `keeper_content_matches` | 3 identity and evidence binding | JIT (identity from the handle); hash only if fully local and the item is a removal | `READY` / `STALE` / `UNKNOWN` (cloud-only: no hash) |
| `source_root_available` | 2 / 9 | Preview + JIT | `READY` / `BLOCKED` |
| `permissions_sufficient` | 4 accessibility | JIT (the `DELETE` open is the test) | `READY` / `BLOCKED_ACCESS` / `BLOCKED_LOCKED` |
| `source_local_if_required` | 9 cloud | Preview (enumeration bits) + JIT (re-read) | `READY` / `BLOCKED` (cloud-only) / `UNKNOWN` (sync-root status unknown) |
| `same_volume`, `filesystem_capability_supported` | 7 / 8 | Preview (serials, NTFS, `DRIVE_FIXED`) + JIT (serial from the handle) | `READY` / `UNSUPPORTED` |
| `destination_available` | 5 / 6 destination and collision | Preview (advisory `GetFileAttributesExW`) + JIT (the rename's own 183 is the real test) | `READY` / `CONFLICT` |
| `free_space_sufficient` | 5 | Preview | `READY` / `BLOCKED` (irrelevant for a same-volume rename; kept for cross-volume later) |
| `companions_present_unchanged` | 3 | Preview + JIT per companion | `READY` / `STALE` |
| `action_lock_available`, `no_conflicting_run` | 1 / 12 | Job start (the lock) | `READY` / `BLOCKED` |
| `rollback_ready` | 11 recovery readiness | Preview + JIT: quarantine folder creatable and same volume; backup folder writable; journal `FULL` commit succeeded | `READY` / `BLOCKED` |
| (new) journal readiness | 12 | JIT: C1 committed before the call | fail closed |
| `no_dangling_protection` (closeout question) | retention | **Preview, re-evaluated against the current record**; not JIT | `READY` / `BLOCKED` |
| Metadata: writer certified; not signed / encrypted / hard-linked now; backup and verify | 10 / 11 | JIT (R4.8 preconditions 1) | `READY` / `BLOCKED` / `UNSUPPORTED` |

**R4.12 Location succession and decision fulfilment.** Written against the Phase 3 schema and the scout's findings.
- On `APPLIED_VERIFIED` of a move or rename, the executor (not a scan) writes, in one journal-side transaction and one catalog transaction: a `file_path` row for the destination if none exists (same `source_root_id`, the new relative path key); a **`p4_path_succession`** row (`old_file_path_id`, `new_file_path_id`, `action_id`, `FileIdInfo` tuple, time, kind = move/rename/quarantine/restore); the old location's `file_state` marked **superseded by succession**, not missing; and a new `file_observation` for the destination with `supersedes_observation_id` pointing at the last observation of the source. Case-only renames keep the `file_path_id` (lowercased key) and write only the display spelling and the succession row with `old = new`.
- **Fulfilment:** the `desired_location` / `desired_name` decision keyed to the old `file_path_id` gets a `fulfilled` event naming the action. The revalidation detector treats "the file is now at the decided place" as fulfilment, not drift, by consulting succession before `binding_drift`. Removal decisions whose keeper moved resolve the keeper through succession, so `keeper_still_exists` is checked at the keeper's new path.
- **Identity, never name:** succession is written only from the handle-verified `FileIdInfo` match. B19's identity *suggestion* (a hard link elsewhere can look like a move) is confirmed by a succession record where one exists and stays a suggestion elsewhere.
- **Schema:** one new table and one new decision event kind; `p3_decision` and `p3_plan_item` payloads unchanged (the plan fingerprint spec is untouched).
- **Metadata writes** follow R4.8 §"Interaction with identity": the executor records the after-observation and a `metadata_write` journal row; a later scan matching the after-hash is "as expected"; the before-hash means the write was undone or lost; anything else is real drift. The backup folder is excluded from scans like the quarantine.

### 8.4 The corpus's 16 open questions (R14), answered or deferred

| # | Question | Answer for v1 |
|---|---|---|
| 1 | Evidence fields retained | Volume serial, 64-bit file index, link count, size, times, attributes, reparse tag, SHA-256 (scout §1). No USN, streams, ACL digest. `FileIdInfo` added by the journal in B22. |
| 2 | Stack | Python 3 + `ctypes` on Windows; SQLite. Settled. |
| 3 | Directories as movable units | **No** in v1 (approved boundary). |
| 4 | Backup relationship | The product makes **no claim** about recoverability outside its own quarantine and backups. |
| 5 | Quarantine on the same volume | **Always**; per source root; checked per file (D2). |
| 6 | Retention | Quarantine and backups: never purged automatically; explicit purge with a 30-day guard (D2). Journal: kept; housekeeping by checkpoint only. |
| 7 | Windows, filesystems, long paths | Windows 11 (this machine 10.0.26300); NTFS fixed drives only; `\\?\` always; `LongPathsEnabled` not required. |
| 8 | Cloud roots | **D1**: intra-root rename/move of verified-local files after probes; everything else blocked. |
| 9 | First metadata scope | PDF / title (D3). Creator and dates deferred; OOXML title second; legacy OLE and RAW last. |
| 10 | Provenance for metadata changes | Original kept as a backup (`ReplaceFileW` with a backup name) plus the journal; no sidecars. |
| 11 | What a duplicate is | Phase 3's answer stands: byte-identical content (SHA-256) for retention decisions; visual similarity is a separate, labelled relationship (axiom 5). |
| 12 | OneDrive, Dropbox, SMB, removable, VHD, WSL | OneDrive: D1. Dropbox/Google Drive: same cfapi plumbing, untested → treated as sync roots and gated the same, or blocked if detection fails. SMB, removable, exFAT/FAT: blocked (`UNSUPPORTED`). VHD/WSL: not researched; the NTFS + `DRIVE_FIXED` gate decides. |
| 13 | Reporting at 50,000 items | B25: counts by state, the exceptions list, the journal on disk (axiom 19). Not a screen per item. |
| 14 | Confirmation model | One confirmation per job at approval (C0); the job pauses on any unexpected outcome (R4.7); purge, restore into an occupied or missing place, and folder re-creation are each their own confirmation. |
| 15 | `UNKNOWN_AFTER_INTERRUPT` resolution | Guided: the reconciliation screen shows the row's evidence (what the journal says, what is on disk, identity match) and offers finish / record / roll back / accept-as-unknown; never automatic. |
| 16 | Test corpus | Throwaway roots under `%TEMP%` built by the tests; VHD volumes for second-volume and exFAT probes; one Thomas-run OneDrive probe; the existing `C:\FOTest` corpus for scan-exclusion and no-mutation checks; fault injection at every commit point (J1, P12, C12). |

### 8.5 Risks the reviewer should press on

1. **Atomicity of no-overwrite is observed, not guaranteed.** We accept "fail-closed per call plus post-verify" because the spec's only path for an existing target is failure and P2b characterises the race. A reviewer may disagree.
2. **Durability of a rename is best-effort for a non-admin process.** Recovery treats disk state as evidence (R4.3 tables), which is the mitigation; there is no stronger one without admin rights, which the product will not ask for.
3. **D1 rests on untested OneDrive behaviour.** The gate is probes-first with A as the fallback; the residual risk is Storage Sense dehydrating between the two attribute reads, which the post-read detects and reports.
4. **The reconciliation table is original work**, built on SQLite and Git precedent; no consumer tool publishes one. B21's crash probes are its test.
5. **Tunneling.** The 15 s window comes from a 2003 KB; P10 measures it on this build.
6. **`python.exe` disguise mode.** If `RECALL_*` bits are hidden from Python, Phase 1's cloud recognition has been partially blind since B6.2, and the fix (`PHCM_EXPOSE_PLACEHOLDERS`) must land before any Phase 4 action in a sync root.

---

## 9. The build plan (B21-B27, final unless gate 1 changes it)

Order approved 2026-11-07; additions marked **(+)**. Models per `AGENTS.md`: builder Sonnet unless named; `fo-runner` Haiku for every suite run; `fo-scout` Haiku for lookups. Every Phase 4 test runs under a throwaway root it creates itself. Gate 2 (different-model design review of code that moves, renames, deletes or writes) before B22 and before B26.

| Build | Units, in priority order | Model | Done when |
|---|---|---|---|
| **B21** Lock, journal, state machine | **(+) U1 probe harness**: L1-L12 lock probes, J1-J7 journal probes (J2/J3 on a VM if available, else recorded as not run); cost of `FULL` measured. U2 journal store: separate file, pragmas, schema (Job, OperationItem, EvidenceSnapshot, OperationEvent, RecoveryRecord, owner), `application_id`/`user_version`, refuse network and OneDrive paths (D4). U3 lock: `msvcrt.locking` + owner row + fencing token; second-instance read-only path. U4 item/job state machine and commit points C0-C4 with a **fake action** and fault injection at every point. U5 reconciliation: tables A, C, D as code and as the test oracle row by row; recovery classifies and shows, never acts. | Sonnet; U5 on Opus | Every table row has a test; kill at every commit point lands on the predicted row; 1,000 racing lock acquisitions never yield two holders; no real file touched |
| **Thomas probe** (+) | R4.6 hand probe (8 steps) on one throwaway `FO_Probe` file; then the builder's read-only Python probe script (RECALL bits under disguise and exposed; `os.stat` and a `DELETE` open on a cloud-only file; raw intra-root rename) run by Thomas on that file. Results filed beside the R4 reports. | Thomas; script by Sonnet | Each probe has icon, size-on-disk, `attrib` letters and web view recorded; D1 confirmed as B or falls back to A |
| **Gate 2a** | Different-model review of the B22 primitive design (R4.1 §5 + §8.2 here) | Thomas pastes; Architect records | Raised points answered |
| **B22** The move/rename primitive | **(+) U1 probes P1-P7, P10-P12** (P8, P9 are Thomas's). U2 identity module: `FileIdInfo` tuple, volume GUID, comparison with the Phase 1 record, `ARCHIVE` masking. U3 path rules: `\\?\` builder, reserved names, trailing space/period, case-sensitive directory refusal, NTFS + `DRIVE_FIXED` gate, same-volume check. **(+) U4 sync-root detection** (`CfGetSyncRootInfoByPath` + registry corroboration) as a per-root and per-file environment fact; `PHCM_EXPOSE_PLACEHOLDERS` at start-up with self-test; the "fully local" attribute rule with pre/post re-read. U5 the primitive with the 7-step sequence, error classification (32, 5, 183, 17, 123), bounded retry rule, tunneling cooldown. U6 undo as the reversed primitive through the same preflight. U7 the just-in-time preflight (R4.11 JIT column). Sandbox only; no screens. | **Opus** (executor safety) | P1-P7, P10-P12 pass or their "different result means" is acted on; a mutant that overwrites, copies across volumes, requests data access, or skips C1 is killed; undo round-trips 1,000 sandbox files with identity preserved |
| **B23** Remove into quarantine | U1 placement and folder creation as journalled steps (hidden attribute, marker file). U2 manifest per entry; "never the last copy" re-checked live. U3 restore: identity check, occupied-path and missing-folder refusals, tunneling cooldown. U4 purge as a separate explicit action with the 30-day guard and survivor re-check. **(+) U5 Phase 1 scan exclusion** of `.FileOrganizer-Quarantine` and the metadata backup folder (by name plus marker), with the exclusion counted and shown (axiom 13). | Sonnet | Quarantine, restore and purge each journalled and reversible except purge, which is labelled irreversible; a scan of a root with a quarantine folder reports it excluded with a count, and re-inventories nothing from it |
| **B24** Move / rename plan items | U1 `p4_path_succession` and the `fulfilled` decision event; the detector consults succession before drift. U2 executor over a locked revision's move/rename items: job assembly, dependency order (vacators first, moves before dependent removals), the R4.7 stop-or-continue rule. U3 companions as units. U4 case-only rename path (same `file_path_id`). | Sonnet; U1 on Opus | A locked revision with 500 sandbox move items runs, pauses on an injected unexpected failure with items after it untouched, resumes, and ends `COMPLETED_WITH_EXCEPTIONS` with the right counts; fulfilled decisions show no drift on the next scan |
| **B25** Screens | Live preflight (Preview column), run with progress and pause, the reconciliation screen (finish / record / roll back / accept-as-unknown), undo, quarantine report (count, size, oldest), second-instance banner. In the one window, through the blocking runner. | Sonnet | Every state in §8.3 has a screen or a line in a report on disk; no control acts without the lock |
| **Gate 2b** | Different-model review of the B26 writer design (R4.8 §4 + D3) | Thomas; Architect records | Raised points answered |
| **B26** The first certified writer | U1 refusals (R4.8 preconditions 1). U2 staged `pypdf` incremental write. U3 certification C1-C15 as tests over the corpus (pdf.js, pikepdf resources, veraPDF, synthetic cases; ExifTool as the oracle in tests only). U4 `ReplaceFileW` with backup, error 1175/1176/1177 handling. U5 identity and observation update; backup-folder exclusion. U6 undo = restore the backup under the hash rule. | Sonnet; U3 on Opus | C15: every corpus file passes C1-C10 or is refused; zero "wrote but failed verify after replace"; the Phase 3 capability row for pdf/title flips to `write_support` certified with the test receipt |
| **B27** Close | Adversarial round (`fo-adversary`, Opus for executor safety): its own probes and hostile fixtures in a sandbox; claims checked against the handbacks. A real-use session by Thomas on a **copy** of real files (made by him, under his OneDrive if D1 is B). Closeout document with the traceability matrix; `STATUS.md`, `PROJECT_PLAN.md` synced. | Opus / Thomas | Nothing found that moves, overwrites or loses a file outside the plan; the session's journal matches what Thomas saw |

**Carried, not in Phase 4 v1** (each a candidate for a later build): cross-volume moves (R4.3 table B is the design); other collision policies; folders as units; network, removable, exFAT/FAT; templates; a Phase 1 "remove root" action; Restart Manager for lock owners; OOXML title (bespoke ZIP rewrite) and audio title; cloud-native rename through Graph; GCC High.

## 10. What review gate 1 is asked to do

Give the reviewer this document and, per D5, closeout §4 (P3.R12) and §7. Ask it to challenge, in order: §8.5 risks 1-6; the R4.7 stop-or-continue rule; the R4.12 succession design against the Phase 3 schema facts in §3; D1 and D3. Ask for anything a safe file-management tool does that this plan omits. Record its points and the Architect's accept/rebut with reasons in `Docs\Research\phase_4\R4\R4_REVIEW_GATE1.md`; the B21 handoff follows from this document as amended.

## 11. Receipts and provenance

- Reports: `Docs\Research\phase_4\R4\R4.1_report.md` (59,029 bytes), `R4.2_report.md` (30,954), `R4.3_report.md` (44,382), `R4.6_report.md` (42,240), `R4.8_report.md` (28,761); word counts 8,308 / 4,523 / 6,978 / 5,623 / 4,409. Each ends with a source ledger.
- Scout answers: `Handoffs\Phase4\R4_SCOUT_ANSWERS.md`, 7 of 7.
- Local receipts (this session, one temp file under `%TEMP%`, removed): Python on PATH 3.14.6, `longPathAware` manifest present, `st_ino` wide form, `st_dev` 64-bit; `HKLM\...\FileSystem\LongPathsEnabled` = 0; `MaximumTunnelEntryAgeInSeconds` and `MaximumTunnelEntries` unset; Windows 10.0.26300.
- Repository state: branch `phase4` at `ce8baf7`; working tree unchanged except the two pre-existing untracked files and this document. Nothing committed, tagged or pushed.
- Unverified in this synthesis: that the python.exe on PATH is the interpreter `TheFileOrganizer.bat` selects; every claim the reports label untested.
