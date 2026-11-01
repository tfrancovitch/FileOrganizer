# Changelog — B20 (Phase 3: organizing intent, the minimum move / rename set)

Built 2026-10-12/13 from tag `phase3-b19` (`d698531`). Branch `phase3-b20`. **Not committed, tagged or pushed.**
Schema 16 — **no migration** (`p3_decision` and `p3_bulk_member` carry no CHECK on kind, target or action).
`APP_VERSION` = `Phase2.VERSION` = **B20**; `MODULE_VERSION` 1.14.0 (unchanged); `REGISTRY_VERSION` `/3`
(the digest changes; the plan payload schema `/1` does not); `EVALUATOR_VERSION` `/3`; `self_check` 38 runtime modules.

## Task 0b — B19's red check (the owner's decision, 2026-10-12)
`p3_registry_schema_check`'s capture hook now **buffers** its rows and writes them after the suite has finished
(`flush_capture`), outside every guard. No guard changed (`p3_regression.py`'s `WriteGuard` is untouched); new check §5b
and mutant M45. The suite was 30/31; it is 33/33.

## What B20 adds
- **Two decision kinds** in the one engine: `desired_location` (`{destination_root_id, destination_folder, on_collision: "fail"}`)
  and `desired_name` (`{name, on_collision, extension_change_confirmed}`) on `file_location`; domains `organize_location` /
  `organize_name`; origins `explicit_human` / `bulk_explicit_human` only. Loaded **apart** from `Evidence.decisions`
  (`evidence.load_decisions(organize=True)`, `Evidence.organize`), so the router never sees them.
- **Record-time validation** (`organize.py`, `store._validate_organize`): reserved characters, control characters, device names
  (also with an extension — a policy choice), trailing space or period, 255 UTF-16 units per segment, no `.` / `..`, root-relative
  only, a segment that is an existing file refused, folder spelling canonicalised to the observed folder (two spellings refused),
  `extension_change_confirmed`, the file's own name / folder refused, `on_collision == "fail"`. A refused value writes nothing.
- **`override_protection` gains `action_scope`** (`removal` | `organize`). *This changes an existing kind's value schema.* A removal
  scope is stored with **no member** (every earlier row, byte for byte); the removal domain key is unchanged and the organize one is
  suffixed `:organize`; `resolve.location_protection(scope=)` lifts only the matching scope (P3-A144).
- **Plan Builder:** `logical_item` is extended (the B20 `desired_effect` shape; collisions through `path_key`) and called by `build`
  (`organize_items`, after the retention and metadata items): one `move_location` / `rename_location` item per location. New
  preconditions (14) with the built lowercase classes; reused keys reused (`target_not_protected`, `destination_collision_absent`,
  `source_local_if_required` at hard severity, `no_dangling_protection`, ...). Cross-volume is `unsupported` (never fail), a path through a
  recorded link is `unknown`; occupancy is "known free" only with complete coverage of the folder chain; the chain exception passes only for an
  evidence-clean vacator; swaps and rotations are blocked; companions (Adobe / darktable `.xmp` found by name) travel together and a
  shared-stem sidecar is unknown; a blocked family member blocks the unit. `organize_executor_supported` is always `unsupported`:
  **no item is ever ready.** Conditional attachments (`no_conflicting_intent_same_location`, `keeper_not_moving` on a removal;
  `no_organize_metadata_overlap` on a metadata item) exist only when an organize item does — a revision with no organize decisions is
  B19's, byte for byte (F39). Obsolescence compares the facts an item judged (occupant, coverage, protection, vacator, companions, dangling
  state); a new walk run alone obsoletes nothing.
- **ScopeFacts** (the scan as the evidence holds it) gains `present_paths`, `inaccessible`, `reparse`, `truncated`, `root_ids`, `volumes`;
  `Location` gains `rel_path`, `cloud_known`, `hard_links`.
- **Frozen batch over file locations** (`bulk.ORGANIZE_MOVE`, `preview_organize`): checked files or the Files page's query run once; buckets
  decided / already satisfied / preserved / conflict (removal intent, protected, destination collision) / blocked / not applicable /
  companion added; commit guarded by the record and evidence marks; `undo_batch` and the new `store.undo_preview`.
- **UI** (`Phase3/organize_view.py`, `Phase2/gui.py`): Move to... / Rename... on a row's context menu, **Move checked...**, **Move listed...**;
  the dialog shows the file's current intent with Withdraw, the folders to be created, collisions, companions with mapped names, the braces note,
  "Stored in the project; never moves the file." and "No source files will change."; **Override protection...** asks which action it lifts and
  defaults to none.
- **Resources:** `companion_patterns.json` (versioned, each pattern with a cited basis), `organize_contract.json` (the adopted contract), the
  regenerated `registry.schema.json` (+ the `x-fo-max-utf16-units` annotation keyword), three golden vectors in `fingerprint_vectors.json`
  (NFD destination, case-only rename, non-BMP folder — each verified by the independent encoder; no existing vector changed).

## Existing assertions extended (additions only, none weakened)
`p3_plan_check` (the precondition vocabulary; the hand-built move test now asserts B20's meaning: a free destination is `unsupported_action`, not
ready), `p3_regression` (16 decision kinds; the bulk-action set; `B20`; the override dialog must be told its action), `p3_dangling_check`
(`no_dangling_protection` is conditional on removals and declared on move / rename), `p3_registry_schema_check` (`/3`; the validator knows the one
new keyword; the corpus gains the organize values and override scopes; the new suite is captured), `p3_fingerprint_check` (three vectors),
`p3_mutation_check` (25 mutants, 4 slices).

## Tests
| Suite | B20 |
|---|---|
| `p3_organize_check` (new) | 138/138 (F17-F29, F31-F41) |
| `p3_registry_schema_check` | 33/33 (31 + the §5b guarded-capture checks) |
| `p3_fingerprint_check` | 34/34 (31 + 3 vectors) |
| `p3_regression` | 348/348 |
| `p3_mutation_check` | 99/99 — 69/69 mutants killed, 29/29 controls clean, tree untouched |
| `b6_regression` | 73/73, five consecutive runs (E.F002 1.06, 0.97, 1.03, 0.99, 0.99x) |
| `p3_plan_check` 81, `p3_closeout_check` 62, `p3_encryption_check` 29, `p3_revalidation_check` 59, `p3_metadata_check` 133, `p3_metadata_view_check` 54, `p3_fixture_check` 101, `p3_refresh_check` 17, `p3_survey_check` 85, `p3_visual_bulk_check` 79, `p3_visual_check` 138, `p3_relabel_check` 117, `p3_dangling_check` 67, `p3_lab_harness` 10/10, `p2_regression`, `p2_dashboard_check`, `self_check` | all passing; B19's counts are the floor |

## Files
New: `Scripts\Phase3\organize.py`, `organize_view.py`; `Scripts\p3_organize_check.py`, `p3_organize_regression_driver.py`;
`Resources\Phase3\companion_patterns.json`, `organize_contract.json`; this file; `PHASE3_B20_HANDBACK.md`.
Changed: `registry.py`, `registry_schema.py`, `model.py`, `evidence.py`, `scopes.py`, `resolve.py`, `store.py`, `plans.py`, `bulk.py`, `review.py`,
`Phase2\gui.py`, `Phase2\__init__.py`, `Database\fo_db.py`, `self_check.py`, the suites named above, `registry.schema.json`, `fingerprint_vectors.json`,
`README.md`, `STAGE_MAP.md`, `ARCHITECTURE.md`, `PROJECT_PLAN.md`.
