# Scout brief: Phase 4 code lookups (R4.5, R4.8, R4.11, R4.12)

**For:** `fo-scout` (haiku) · **From:** the Architect, 2026-11-01 · **Worktree:** `FileOrganizer-Phase4-Base`
**Write answers to:** `Handoffs\Phase4\R4_SCOUT_ANSWERS.md`. Every answer carries `file:line` references.
Answer only what is asked; say "not found" plainly. Do not read `PROJECT_PLAN.md` or `ARCHITECTURE.md` whole.

1. **Identity fields.** Which identity fields does the Phase 1 scan store per file (table and column names)?
   Confirm: `volume_serial`, `file_index`, `hard_link_count`, `attributes`, `reparse_tag`, size, times. Is
   `file_index` the 64-bit `nFileIndex` or the 128-bit `FileIdInfo`? (Start at `Scripts\Database\win_meta.py`
   ~lines 909-960 and `fo_scan.py` ~130-160.) Is a content hash stored, and which algorithm?
2. **Metadata fields people can set.** List every (file family × semantic field) a person can give a desired
   value in Phase 3 (`Scripts\Phase3\registry.py`: `METADATA_FIELDS`, `PROJECT_METADATA_FIELDS`,
   `METADATA_READ_MAP`). Which analyzers read each field today?
3. **Plan items.** For each Phase 3 plan item kind (Remove Location, Move / Rename, Update Source Metadata), list
   the stored fields, the `live_preflight` condition keys, and the postcondition keys (`Scripts\Phase3\plans.py`).
4. **Paths.** How are `desired_location` and `desired_name` stored, and how is a `file_path_id` assigned? Is
   there any existing notion of one path succeeding another?
5. **Scan exclusions.** Does the Phase 1 walk have an exclusion list (folders it never enters)? Where is it?
6. **Concurrency today.** Which processes write the project database (Dashboard, RunCoordinator, Phase 3)? Is
   there any existing lock, single-writer rule or PID file? (`fo_db.py` ~300-450, `RunCoordinator.py`.)
7. **`no_dangling_protection`.** Where is it evaluated, and is it an evidence-stage check only?
