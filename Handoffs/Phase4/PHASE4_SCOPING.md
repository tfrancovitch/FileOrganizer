# Phase 4 scoping: research questions, method, provisional sequence

**From:** the Architect (Cowork, Opus) · **Date:** 2026-11-01 · **Status:** v1 boundary (§5) and build order B21-B27 (§4) **approved by the owner 2026-11-07**; builds start only after the synthesis and review gate 1 (§6)
**Base:** `phase4` worktree, `phase3-b20` code. Inputs read: `STATUS.md`, `AGENTS.md`, P3 closeout §5a/§6/§7,
`PROJECT_PLAN.md` §2/§3 and "Undo, when Phase 4 arrives", `Docs\Research\02_PHASE_4_GUIDE.md`, the heading map
of `Docs\Research\phase_4\Phase_4_Research_Results.md`, and a grep of `win_meta.py`, `fo_scan.py`, `fo_db.py`,
`Phase3\metadata.py`; the dev-notes bootstrap, `INDEX.md`, standing notes and the 2026-11-01 FileOrganizer note.

## Short version
- **Most of the general research is already done.** The 2026-09 Phase 4 corpus (31,000 words) settles the
  engine shape: preflight → execute → verify → journal → recover; a result enum, not a boolean; collision as
  policy; "never call it a transaction". Re-researching that would waste money.
- **What it does not settle** is how *this* product (Python 3, SQLite, Windows, OneDrive) does each step safely.
  That is the R4 set below: 12 questions, 5 research briefs for `fo-researcher` (Sonnet), 1 lookup brief for
  `fo-scout` (Haiku), and 4 questions I answer myself in the synthesis.
- **One finding already, from the code:** the database runs SQLite in WAL mode with `synchronous = NORMAL`
  (`fo_db.py` lines 307-336). That survives a crashed *program* but, per SQLite's own documentation, can lose the
  last commits on a *power cut*. Fine for Phases 1-3; not fine for a journal that must record "about to move
  this file" before the move. R4.3 settles the fix.
- **Proposed change to the build order:** removing a file into quarantine *is* a same-volume move, so the move
  primitive has to exist first. See §4.

## 1. What the existing corpus already settles (not re-researched)
| Topic | Where | Taken as given |
|---|---|---|
| Engine stages and result enum (`READY` … `UNKNOWN`) | guide Thread A | yes |
| 12 validation categories, per-action preflight profiles | corpus "Required validations" | yes, mapped onto P3 `live_preflight` |
| Journal entities (Job, OperationItem, EvidenceSnapshot, OperationEvent, RecoveryRecord); write-ahead pattern | corpus "Implementing operation journals" | shape yes; storage details → R4.3 |
| Collision vocabulary; `FAIL_ON_CONFLICT` default | corpus "Collision semantics" | yes |
| Sharing violations: classify, bounded retry with full revalidation, never force | corpus "Handling Windows locks" | yes |
| Cross-volume = copy, verify, separately-authorised source removal; `DESTINATION_VERIFIED_SOURCE_RETAINED` | corpus R1-R2, `MoveFileEx` | yes; out of Phase 4 v1 (§5) |
| GCC High / compliance thread | guide Thread B | **out of Phase 4 scope**; kept for the cloud-root phase |
| Undo cost table; "never delete the last copy"; Recycle Bin is a courtesy | `PROJECT_PLAN.md` "Undo, when Phase 4 arrives" | yes |

## 2. The R4 research questions
Method key: **Brief** = `fo-researcher` (Sonnet) in Claude Code; **Scout** = `fo-scout` (Haiku);
**Arch** = answered by me in the synthesis from the corpus plus the briefs; **Probe** = an empirical sandbox test,
run as the first unit of the build that needs it (the corpus says the test matrix, not API docs, decides).

| # | Question | Decision it informs | Done when | Method |
|---|---|---|---|---|
| **R4.1** | From Python on Windows, which call performs a same-volume rename/move with *no silent overwrite*, and can the identity check and the rename be bound to one open handle (so the file cannot be swapped between check and act)? Includes case-only renames, long paths (`\\?\`), reserved names, and NTFS "tunneling" (a new file inheriting a just-removed name's creation time). | The executor primitive for B22 | Each candidate (`os.rename`, `os.replace`, `MoveFileExW`, `SetFileInformationByHandle` with `FileRenameInfo(Ex)`) has: overwrite behaviour, case-only behaviour, error codes, NTFS vs exFAT/FAT32 vs ReFS, Windows 10 vs 11, all cited; one recommended, with a probe list | Brief **B-R4.1** + Probe |
| **R4.2** | Where does a removed file go: the Recycle Bin, or an app-owned quarantine folder? Where on disk, how is it undone, how is it kept out of Phase 1 scans, what happens inside a OneDrive folder? | Thomas's decision on removed files; B23 | Both options compared on restore-by-program, capacity, per-volume behaviour, network/removable drives, OneDrive (local and online recycle bins), axiom 11 and 14; a placement rule for quarantine | Brief **B-R4.2** |
| **R4.3** | How is the action journal made durable and recoverable in SQLite on Windows: journal mode, `synchronous` level, separate database file or same, where the transaction boundary sits relative to the filesystem step, and how recovery reconciles an interrupted step? | B21 journal design | A cited answer on WAL vs rollback journal durability under power loss; a write-ahead sequence with each commit point named; a reconciliation table (journal state × what is on disk → resulting state) | Brief **B-R4.3** |
| **R4.4** | How do two FileOrganizer processes (two Dashboards, or a scan and an execute) exclude each other, so that the lock is released automatically if the holder crashes? | B21 lock | Options compared (exclusive-share lock file, named mutex, `msvcrt.locking`, SQLite `BEGIN EXCLUSIVE`, a lease row); crash and sleep behaviour cited; one recommended | Brief **B-R4.3** (same brief) |
| **R4.5** | Which identity evidence is strong enough to say "this is the same file Phase 3 planned for", read *without* opening the content? 64-bit file index (stored today) vs 128-bit `FileIdInfo`; volume serial; size; times; reparse tag; hard-link count | Live preflight `source identity matches`; location succession (B24) | A table: field, how read from Python, cost, hydration risk, reliability per file system; what the scan stores today (Scout) | Brief **B-R4.1** + Scout |
| **R4.6** | OneDrive Files On-Demand: which operations hydrate a placeholder (opening for attributes, rename inside the sync root, move out of it, delete, hash)? Can a process block hydration for itself? | Axiom 14 enforcement; which cloud-root actions Phase 4 v1 allows | A per-operation table with sources, labelled verified / reported / untested; the process-level blocking mechanism, if any; a probe list Thomas runs by hand on one throwaway OneDrive file | Brief **B-R4.6** + Thomas probe |
| **R4.7** | The partial-execution contract: when item 7 of 20 fails, what happens to 8-20, what the job's state is, what "done" means, how undo applies to a part-done job | Job state model (B21), screens (B25) | A state machine for item and job; dependency rule (keeper moves, succession); stop vs continue rule | **Arch** |
| **R4.8** | How is one metadata writer certified (one format, one field)? Which format and field first, with which library, and what evidence certifies it? | B26 | A recommended first pair from the fields Phase 3 lets people set (Scout); library compared (ExifTool, piexif, pikepdf, …); a certification checklist: round trip, bytes outside the field unchanged, independent reader agrees, temp-write then replace, backup kept | Brief **B-R4.8** (after Scout) |
| **R4.9** | The undo model: what the undo record holds, and whether undo is itself an action that goes through preflight | B22-B24 | Undo defined per action; undo of an undo; what makes undo impossible (and how the screen says so) | **Arch** |
| **R4.10** | Windows environment blockers: Defender Controlled Folder Access (blocks unknown programs from changing files in Documents, Pictures, …), antivirus and Search Indexer holding files open, read-only and system attributes | Preflight classification; the first real-use session | How each shows up to Python (error code), how to detect it before acting, what the person is told | Brief **B-R4.1** (same brief) |
| **R4.11** | Mapping the P3 closeout's `live_preflight` conditions onto the corpus's 12 categories, and deciding which run where (preview vs just-in-time) | B22-B24 preflight | Every condition in closeout §7 has a check, a stage and a result value | **Arch** + Scout |
| **R4.12** | Location succession and decision fulfilment: after a move, how the old `file_path_id` is linked to the new one by identity, and how a fulfilled `desired_location` stops reading as drift | B24 | A rule written against the Phase 3 schema | **Arch** + Scout |

**Second opinion on P3.R12** (decision waiting for Thomas): R4.12 is where Phase 4 leans on P3.R12. If a
different-model review is wanted, its cheapest moment is together with review gate 1 (§6).

## 3. Commissioning
| Brief | File | Model | Runs in | Report lands in |
|---|---|---|---|---|
| Scout lookups (run first, cheap) | `Handoffs\Phase4\R4_SCOUT.md` | haiku | Claude Code, `FileOrganizer-Phase4-Base` | `Handoffs\Phase4\R4_SCOUT_ANSWERS.md` |
| B-R4.1 rename/move primitive, identity, environment blockers | `Handoffs\Phase4\B-R4.1.md` | sonnet | same | `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.1_report.md` |
| B-R4.2 removed files | `Handoffs\Phase4\B-R4.2.md` | sonnet | same | `…\R4\R4.2_report.md` |
| B-R4.3 journal durability and the lock | `Handoffs\Phase4\B-R4.3.md` | sonnet | same | `…\R4\R4.3_report.md` |
| B-R4.6 OneDrive placeholders | `Handoffs\Phase4\B-R4.6.md` | sonnet | same | `…\R4\R4.6_report.md` |
| B-R4.8 first metadata writer | `Handoffs\Phase4\B-R4.8.md` | sonnet | same, after the scout answers | `…\R4\R4.8_report.md` |

Reports go under `Docs\Research` (outside the public repository, like the earlier research). Each ends with a
source ledger. Run at most three at once (`AGENTS.md`); the scout first, then R4.1 / R4.3 / R4.6, then R4.2 and
R4.8. R4.2 benefits from R4.6's answer about moving placeholders.

**How Thomas runs them** (Claude Code, opened in `C:\FileOrganizerTesting\FileOrganizer-Phase4-Base`):
1. `claude --model haiku`, then: *"Run the fo-scout agent on Handoffs/Phase4/R4_SCOUT.md."*
2. New session, `claude --model sonnet`, then: *"Run three fo-researcher agents in parallel, one per brief:
   Handoffs/Phase4/B-R4.1.md, B-R4.3.md, B-R4.6.md. Report only each file's path and its short answer."*
3. Same session or a new Sonnet one: *"Run fo-researcher on Handoffs/Phase4/B-R4.2.md, then on B-R4.8.md."*
4. Come back to the Architect chat and say the reports are in. I read them by section, not whole.

## 4. Provisional build sequence (revised; final after the synthesis)
| Build | Content | Why here | Builder model |
|---|---|---|---|
| **B21** | Action lock; journal (separate durable store, write-ahead, reconciliation on start); job/item state machine; result enum. Exercised with a fake action and fault injection; touches no real file | Everything below writes to it | sonnet; the reconciliation unit on opus |
| **B22** | The same-volume move/rename primitive: handle-bound identity check, `FAIL_ON_CONFLICT`, case-only method, verify, undo record. Just-in-time preflight for it. No screens; sandbox only | Remove-into-quarantine and Move both are this primitive | opus (executor safety) |
| **B23** | Remove Location into quarantine: placement rule, manifest, restore, retention, Phase 1 scan exclusion; "never the last copy" rechecked live | Highest user value; smallest new surface once B22 exists | sonnet |
| **B24** | Move / Rename plan items: location succession, decision fulfilment, keeper-move ordering | Needs B22 and the journal's succession records | sonnet; succession unit opus |
| **B25** | Screens: live preflight, run with progress, verify, undo | Built on settled engines | sonnet |
| **B26** | The first certified metadata writer (one format, one field) | Independent of moves; last because riskiest per byte | sonnet; certification unit opus |
| **B27** | Adversarial round (opus), real-use session on a copy by Thomas, closeout | | opus / Thomas |

**Gate 2** (`AGENTS.md`: a different-model review of the design of any code that moves, renames, deletes or
writes) falls before B22 and before B26.

## 5. Phase 4 v1 boundary (approved by the owner 2026-11-07)
In: same-volume move/rename of single files, removal into quarantine, one metadata writer, undo for each,
local NTFS fixed drives. Out (stay `unsupported_action` / blocked, with the reason shown): cross-volume and
cloud-root moves; anything on a file that is not fully local; collision policies other than fail; folders as
movable units; network shares and removable drives; templates. Each "out" is a candidate for a later build,
not a dead end.

## 6. Next steps
1. Thomas: run the scout brief, then the research briefs, in Claude Code (§3).
2. Architect (new chat or this one): the synthesis, from the reports; the R4.7 / R4.9 / R4.11 / R4.12 answers.
3. Review gate 1: Thomas puts the synthesis to ChatGPT or Gemini; I record and answer what it raises.
4. The B21 handoff into `Handoffs\B21\`; the steward files it on Thomas's yes.
