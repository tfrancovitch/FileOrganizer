# The File Organizer — Handoff for Phase 2 and beyond

Written 2026-09-04, at the close of Phase 1. One document: what shipped, and
everything already established that a later phase needs to know.

---

## Part 1 — Phase 1, as shipped

### What it is

A Windows desktop application that **inventories files**: it records every
accessible piece of metadata plus content hashes into a per-project SQLite
database, finds duplicate files, and runs nine per-type analyzers (image, PDF,
office, RAW image, audio, video, text, archive, content extraction).

Pipeline: **Pre-Scan** (inventory + size-collision candidates) → **Duplicate
Run** (partial hash of the first 64 KB, escalating to a full hash only when
partials match) → **analyzers**.

### What it deliberately does NOT do

Phase 1 is **read-only**. It never moves, renames, deletes, or edits anything in
the folders it scans. `LastAccessTime` may change (the OS does that on read);
nothing else. This was verified at scale — a Pre-Scan plus a full Duplicate Run
over 182 GB / 59,000 real files, with a byte-level before/after fingerprint:
zero source mutation.

### Where to get it

Repository: **https://github.com/tfrancovitch/FileOrganizer**

| | |
|---|---|
| Latest release page | https://github.com/tfrancovitch/FileOrganizer/releases/latest |
| This release | https://github.com/tfrancovitch/FileOrganizer/releases/tag/phase1-b6.2 |
| Direct download | https://github.com/tfrancovitch/FileOrganizer/releases/download/phase1-b6.2/FileOrganizer-Phase1-B6.2.zip |
| Git tag | `phase1-b6.2` (on `main`); the previous PowerShell version is preserved at tag `alpha-final` |

**To run it:** download the zip, extract it, double-click `TheFileOrganizer.bat`.
Windows 10 or 11. If Python 3.11+ isn't present it is installed automatically
(per-user, no admin). The first launch runs a self-check and verifies its own
files against `SOURCE_SHA256.csv`.

### How it was validated

Four rounds of adversarial acceptance testing (two on Linux, two on Windows).
Summary:

- **Correctness is solid.** All hashes match an independent hasher. Duplicate
  group membership is exactly right — including on 8,012 real duplicate sets
  found in a live OneDrive folder. The partial-hash escalation shortcut (the
  riskiest design choice) is correct: files with an identical first 64 KB that
  differ later are correctly *not* duplicates.
- **It fails honestly.** Every wrong answer it could have given, it declined to
  give — an unreadable or locked file gets an error row and no invented hash,
  and the run still completes.
- **It survives abuse.** Two instances at once, a scan racing a hash run, files
  deleted mid-scan, a legacy console code page, a hard process kill — no
  corruption, `integrity_check` = ok every time, memory stays flat regardless of
  corpus size.
- **Performance:** ~11–22 minutes per million files for a Pre-Scan (warm/cold);
  the Duplicate Run is disk-read-bound on the actual file bytes.

Full detail: see the acceptance report kept with the project owner's working
notes, and `CHANGELOG-B6.md`, `CHANGELOG-B6.1.md`, `CHANGELOG-B6.2.md` in this
repo.

### Known limitations carried into Phase 2

1. **`conn.rollback()` is a no-op.** The database connection is autocommit, so a
   multi-step write that fails halfway leaves its completed half in place.
   Deliberate — a real transaction would hold the database lock for whole write
   blocks instead of microseconds, making concurrent runs and background queries
   far more contentious. **See Part 2 for the revisit trigger.**
2. **No cross-process lock.** Two instances can run against the same project at
   once. In Phase 1 that only wastes work (the last run to commit wins, and the
   database stays consistent). It is not safe to assume for a phase that writes.
3. **A size- and timestamp-preserving edit is invisible to the inventory.** On
   Windows there is no timestamp that changes on a content-only edit. The
   inventory will not notice; only a Duplicate Run (which re-hashes candidates
   from disk every time) will. `current_duplicate_sets()` is therefore only as
   fresh as the last Duplicate Run.
4. **Text metadata is best-effort for legacy encodings.** Word/character counts
   and extracted content are reliable for UTF-8 and BOM-marked files; a
   BOM-less legacy multi-byte file (Shift-JIS, GB2312, EUC-KR) falls back to a
   byte-safe decode labelled "uncertain".

### What was NOT tested (should not be forgotten)

- A real network share, a real removable/USB drive, and the drive holding the
  database being disconnected mid-run (only the code paths were tested; no such
  device was available).
- An actually non-US Windows **system** locale (not just the console).
- Power-loss durability (process-kill was tested; a real power cut was not).
- The Alpha-equivalence export family (needs Alpha's own output to diff).
- The B6.2 fix batch had one verification pass (regression suite + full pipeline
  + GUI smoke), not an independent from-scratch re-verification.

---

## Part 2 — Established for future phases

### The one decision with a hard revisit trigger

**Before any phase adds an operation that MOVES, RENAMES, or DELETES a file,
resolve the autocommit / rollback question.** At that point "either the whole
change happened or none of it did" stops being optional. The choice is: keep
autocommit (and make each destructive action independently idempotent and
crash-safe), or introduce explicit `BEGIN`/`COMMIT` around the write blocks that
must be atomic (and accept the heavier locking). The concrete implications
cannot be sized until the Phase 2 scope exists.

### Architecture invariants a new phase MUST respect

These are enforced in `ARCHITECTURE.md` and in SQL; a phase that ignores them
will produce wrong answers.

- **`file_observation` is append-only history. `file_state` is the current
  answer.** Never treat a historical row as current.
- **A hash is bound to the observation it was computed from.** Content identity
  on `file_state` is authoritative only when
  `content_observation_id == current_observation_id`. If a file changed after
  hashing, the hash is stale and the SQL of `current_duplicate_sets()` already
  excludes it.
- **Use `current_duplicate_sets()`. Never query `duplicate_group` directly** —
  that table records what a *run* concluded, i.e. history.
- **`detail_json` stores numbers as strings** (`"PageCount": "3"`). For numeric
  comparisons use the promoted, typed columns (`width_px`, `word_count`,
  `duration_seconds`, …), not `json_extract`.
- **Physical identity** (`file_index`, `hard_link_count`, `volume_serial`) is now
  populated on NTFS via a full `os.stat()` of the path. A duplicate group's
  `reclaimable_bytes` counts physical objects, not path locations.

### The checklist for any phase that acts on a file

Before moving / deleting / renaming anything on the strength of the database:

1. **Re-verify content.** Run a fresh Duplicate Run or Full Run since the last
   moment the files could have changed — or re-hash each specific target and
   compare — because a size+timestamp-preserving edit will not have been noticed
   by the inventory (limitation 3 above).
2. **Ensure no scan is running.** There is no cross-process lock (limitation 2).
   A destructive phase needs one — a lock file, a single-instance mutex, or a
   documented "do not run concurrently" that the UI enforces.
3. **Check coverage.** Do not trust an inventory that ended
   `completed_with_warnings`, or a `run_source_root` with `was_scanned = 0`, or
   a scan with a non-zero directory-error count — a subtree may be missing
   (e.g. a share dropped mid-scan). B6.2 makes those signals visible; a
   destructive phase must read them.
4. **Respect cloud-only files.** A file marked `is_offline_or_cloud` must not be
   opened — reading it triggers a download. The hash and analyzer engines
   already skip these (`skip_cloud_only`); any new file-touching code must too.

### Schema and sizing

- Migrations run through **007** (`Scripts/Database/migrations/`). Phase 2 work
  adds `008_*.sql` onward. The 6→7 migration was verified lossless and writes a
  pre-migration backup to `Scripts/Database/Backups/`.
- Database size on real data is **~1.7 GB per million files** (README updated).

### History / provenance

- **Alpha** — the original PowerShell + CSV tool. Preserved at tag
  `alpha-final`. Historical only.
- **B-series rewrite** — Python + SQLite, so later phases can query a database
  rather than parse CSVs, and PowerShell is used only where Python genuinely
  cannot reach a Windows metadata detail. Development ran B1 → B6.1.
- **B6.2** — the fixes from Windows acceptance testing. This release.
  See `CHANGELOG-B6.2.md`.

### How the build/test work was run (and should continue)

- The product stays **as fast and as simple as possible**; when a design choice
  trades product speed or simplicity for something else, product speed and
  simplicity win.
- The **building and testing** is as thorough as possible; time is not the
  constraint. Prefer many small verified checkpoints over one big unverified
  push. Every code change ships as a **complete replacement file** with
  independent verification (the regression suite AND independent ground truth —
  `Get-FileHash`, not the product's own hashing).
- Work must be **resumable**: any session can stop and resume with nothing lost.

### Suggested Phase 1.x follow-ups (optional, non-blocking)

- Independent from-scratch re-verification of the B6.2 fix batch.
- Cosmetic: the progress screen still shows old PowerShell stage names
  ("Running ImageAnalysis.ps1…"); the app window clips a few checkbox labels;
  "Resume Scan" / "View Duplicates" on the Continue Project screen are visible
  but non-functional.
- Work through the "not tested" list above when the hardware/environment is
  available.

---

*The regression suite ships as `Scripts/b6_regression.py`. One check
(`F.F001`) fails on Windows because its fixture creates `a/B.txt` and `a/b.txt`,
which are the same file on a case-insensitive volume — a broken test, not a
product defect.*
