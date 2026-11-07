# Research brief B-R4.3: journal durability in SQLite, and the cross-process lock

**For:** `fo-researcher` (sonnet) · **From:** the Architect, 2026-11-01 · **Covers:** R4.3, R4.4
**Report to:** `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.3_report.md`. Target 3,000-4,500 words.
Budget: ~20 web fetches.

## Context (do not re-research)
- The 2026-09 corpus settles the journal *shape*: entities Job, OperationItem, EvidenceSnapshot,
  OperationEvent, RecoveryRecord; write-ahead intent → one bounded step → result → verify → verification record;
  "do not call it a transaction" (`Phase_4_Research_Results.md` lines ~2133-2335; read those only).
- The product's database today (`Scripts\Database\fo_db.py` lines ~300-450): Python `sqlite3`,
  `isolation_level=None` (autocommit; explicit `BEGIN IMMEDIATE` in scripts), `journal_mode = WAL`
  (DELETE on some paths), `synchronous = NORMAL`, `busy_timeout = 5000`. Several processes may open it
  (Dashboard, RunCoordinator). Read those lines.

## Questions: durability (R4.3)
1. Under WAL with `synchronous = NORMAL`, what is lost on (a) process crash, (b) OS crash or power cut? Same
   for `synchronous = FULL` and `EXTRA`, and for rollback-journal mode. Cite sqlite.org pages by section.
2. Should the action journal live in the **same** database file or a **separate** one (with its own pragmas)?
   Trade-offs: atomic joint commits with plan tables vs isolating durability cost; `ATTACH` and WAL atomicity
   across attached databases (sqlite.org says multi-file commits are not atomic in WAL mode: confirm).
3. On Windows, does `FlushFileBuffers` (which SQLite uses on sync) reach the disk through write caches? What
   does "durable" mean on a consumer laptop SSD? Plain-language limits.
4. **Filesystem step durability.** After a rename returns success, is it durable on NTFS? `MOVEFILE_WRITE_THROUGH`
   effect for a same-volume rename; can a non-admin process flush a directory or volume? If not, what must the
   recovery logic assume after a power cut (the rename may or may not be there)?
5. **Reconciliation.** Produce a table: last durable journal state × what is found on disk (source present /
   absent, destination present / absent, identity match / mismatch) → recovered state and next action
   (finish, roll back, ask the person, `UNKNOWN_AFTER_INTERRUPT`). Cite any established tool that does this
   (e.g. package managers, rsync `--partial`, Git's lock files, database-backed sync tools).
6. **Idempotency.** Which steps can be safely retried as-is, which must be re-verified first, which never retried?

## Questions: the lock (R4.4)
7. Compare, for Python on Windows: a lock file held open with share mode 0 (`CreateFileW`, or `msvcrt.locking`),
   a named mutex (`CreateMutexW`, `WAIT_ABANDONED`, `Local\` vs `Global\`), SQLite `BEGIN EXCLUSIVE` held for a
   run, and a lease row with heartbeat. For each: released on crash? on kill from Task Manager? across sleep /
   hibernate? two Windows users on one PC? a project folder on a network share or in OneDrive?
8. What must the lock cover: execute only, or execute + scan + plan edits on the same project? Recommend a
   granularity (per project database is the Architect's starting hypothesis) and how a second Dashboard
   instance should behave (read-only view, clear message).

## Deliverables in the report
- Recommended pragmas and file layout for the journal, with the commit points of one action named in order.
- The reconciliation table.
- One recommended lock mechanism, with a crash-test probe list for B21.
