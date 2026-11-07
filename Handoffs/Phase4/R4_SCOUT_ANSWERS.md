# R4 scout answers

Answered: 7 of 7. Paths are under `Scripts\` unless shown.

Filed by the Architect session from the fo-scout report. fo-scout had only Read, Grep and Glob, so it could not write this file itself. Nothing was committed, staged, tagged or pushed.

## 1. Identity fields

- `file_observation` (one row per observation) has `size_bytes` (Database\migrations\003_inventory.sql:252), `created_utc` and `modified_utc` (006_b6_state_identity.sql:113-114; `accessed_utc` named at 006:79), `attributes` (003:258), `is_reparse_point` (003:259), `reparse_tag` (007_b61_reconciliation.sql:47), `allocated_size_bytes` (007:48), `volume_serial` and `file_index` as TEXT, and `hard_link_count` as INTEGER (006:172-174).
- `file_state` (one row per location) has the same set (006:238-239; 007:21-33).
- Confirmed: volume_serial, file_index, hard_link_count, attributes, reparse_tag, size, times.
- InventoryRecord slots: Database\fo_scan.py:128-135.
- `win_meta.stat_physical_identity` (Database\win_meta.py:915-944) stores `str(st_dev)`, `str(st_ino)` and `st_nlink`.
- `file_index` is Python's `st_ino` as a decimal string, not FileIdInfo.
- Whether `st_ino` is 64-bit on this build: **unverified** from the repo.
- Content hash: yes. `content.sha256` is a whole-file SHA-256 (004_hash_duplicates.sql:118, 131-139), with `identity_source` of `full_hash` or `partial_hash_complete`. `hashlib.sha256` at Database\fo_hash_engine.py:236, 258.

## 2. Metadata fields

- Semantic (Phase3\registry.py:857-865): title, creator, content_created, description, keywords, copyright. Offered for every location (evidence.py:907).
- Project-only, never written (registry.py:870-879): tags, category, note, review_priority, retention_rationale, owner.
- Writer capability: every row has `write_support='none'` and `status='read_only'` (Database\migrations\014_phase3_metadata.sql:68-79). No row means unknown (014:24). So no family/field pair passes `writer_supported` (Phase3\metadata.py:131-135).
- Readers (registry.py:938-943; 014:65-79):
  - title: pdf, office (ooxml, ole_office), audio
  - creator: pdf, office
  - content_created: pdf, office, raw_image
  - description, keywords, copyright: none
  - odf has no capability row.

## 3. Plan items

- Stored columns in `p3_plan_item` (Phase3\store.py:1077-1084). Preconditions in `p3_plan_precondition` (store.py:1099-1102).
- **Remove**
  - evidence keys: registry.py:681-691; conditional 766-769; live 676-692 (target_exists, target_content_matches, source_root_available, permissions_sufficient, action_lock_available, no_conflicting_run, rollback_ready, keeper_content_matches).
- **Move**
  - evidence 698-716; live 717-720 (destination_available, free_space_sufficient, filesystem_capability_supported, source_identity_matches, companions_present_unchanged, source_local_if_required, same_volume) plus common.
- **Rename**
  - evidence 721-736; live 737-739 plus common.
- **Update Source Metadata**
  - evidence 742-749; live is common only (750).
- **Postconditions** (registry.py:776-783)
  - Remove: source_removed, keeper_still_exists, minimum_retention_satisfied, rollback_record_exists, verified_reclaim_recorded.
  - Move/Rename: destination_exists, source_path_absent, physical_identity_unchanged, destination_content_matches, companions_at_destinations, no_other_file_replaced, project_state_updated, rollback_record_exists.
  - Metadata: desired_metadata_observed, rollback_record_exists.
- **desired_effect**: Remove (plans.py:364-365); Move/Rename (694-698); Metadata (421-425).
- Live results are not evaluated in Phase 3 (plans.py:91).

## 4. Paths

- `desired_location` = {destination_root_id, destination_folder, on_collision} (registry.py:164-184).
- `desired_name` = {name, on_collision, extension_change_confirmed} (registry.py:187-188).
- Decisions are keyed to `file_path_id` (Phase3\store.py:456-462).
- `file_path_id` is a SQLite INTEGER PRIMARY KEY assigned by INSERT OR IGNORE (Database\fo_inventory.py:240-244, in `_resolve_path_ids` at 204). It is unique on (source_root_id, relative_path_key) (003_inventory.sql:201). The key is lowercased (fo_inventory.py:50-51).
- Path succession: not found as a notion. A file renamed outside the tool is a new location (Scripts\p3_organize_check.py:643-645, A139).
- `supersedes_observation_id` (006:165-166) is observation-level.

## 5. Scan exclusions

- No name-based exclusion list.
- Only junctions and symlinks are not recursed (fo_scan.py:498-501; win_meta.py:124-126).
- Cloud placeholders are recursed (fo_scan.py:44-47).
- Hidden and System files are counted only (fo_scan.py:275-278).
- Empty directories are counted and skipped (fo_scan.py:44).

## 6. Concurrency

- Writers:
  - Dashboard starts RunCoordinator (Scripts\Dashboard.py:45, 150) and calls `reconcile_all_projects` (Dashboard.py:520).
  - RunCoordinator writes the inventory, hash, analyzer and run tables.
  - The Phase 2 GUI opens a write connection (Phase2\gui.py:312, Phase2\core.py:57-74) that the Phase 3 DecisionStore uses (Phase3\review.py:130). One transaction per operation (Phase3\store.py:129-139).
  - FTS rebuild at Phase2\runner.py:492.
- Locks: SQLite WAL, busy_timeout 5000, BEGIN IMMEDIATE for migrations (Database\fo_db.py:301-338, 441).
- The single-writer rule is documented, not enforced (fo_db.py:317-318; Phase2\runner.py:11-16).
- Run record `host_pid` and `reconcile_stale_runs` (Database\fo_runs.py:147-158, 537-600) is reconciliation, not a lock.
- In-process threading locks only (RunCoordinator.py:437, 2674).
- No lock file, PID file or single-writer check found.
- `no_conflicting_run` and `action_lock_available` are declared live conditions (registry.py:678), not implemented.

## 7. no_dangling_protection

- Declared in registry.py:635 (class retention).
- Hard, evidence stage only: STAGE_EVIDENCE at registry.py:705 (Move), 728 (Rename), 767 (Remove, conditional). Not in the live lists (676-679).
- Evaluated at Phase3\plans.py:310 and 383 (removals, `_dangling_protection` 615-632), 761-767 (Move/Rename), 1474-1478 (ready-removal summary), 1550-1552 (re-judgement on change).
- The dangling test is Phase3\scopes.py:252. Design note at scopes.py:16.
