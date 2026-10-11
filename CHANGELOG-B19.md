# B19 — Phase 3: the four "Not evidenced" rows

**Date:** 2026-09-27
**From:** B18 (tag `phase3-b18`, commit `0ad1d86`), on branch `phase3-b19`
**Handoff:** `FileOrganizer-Phase3\phase_3_handoff_b19\` (the B18 review, the synthesis of the four fixes, the plan)
**Handback:** `PHASE3_B19_HANDBACK.md`
**Closeout:** `Docs\Handoffs\P3_CLOSEOUT.md`, revised (the four rows now **Met (B19)**; §5a empty; P3.R12 pending)

B18's closeout could mark four rows only *Not evidenced*. The owner decided on
2026-09-27 to fix all four before closing. B19 does that, carries the B18
review's two small items (W1, W2), and revises the closeout. It adds no move or
rename decision kinds, no metadata writer and no Phase 4 code. **Schema 16 --
no migration** (the dangling guard needed none: every fact it reads was
already recorded).

**Before the build:**
- B18 was committed as `0ad1d86` and tagged `phase3-b18` (annotated), as handed
  back (the six reference hashes checked first). Disclosed in the handback: a
  first attempt created the tag on B17's commit through a mangled command; it
  was deleted and re-made on the B18 commit within seconds, nothing pushed.
- Clean checkouts of `phase3-b17` (208 files) and `phase3-b18` (217) pass
  `Test-Installation.ps1`.
- The root sync was prepared but **not performed**: moving the root copies
  into `_Archive\` was refused by the session's permission check (see the
  handback, task 0).

## What is new

### 1. The rule registry as JSON Schema

- **`Resources\Phase3\registry.schema.json`** -- draft 2020-12, `$id`
  `urn:fileorganizer:p3:registry:2`, `version` = `REGISTRY_VERSION`
  (`fileorganizer.p3.registry/2`). One definition per decision kind (its
  targets, its value shape, and its registry attributes as `x-fo-*`
  annotations) and per policy kind (its scope and effect shapes); the
  project-metadata and semantic-field value shapes.
- **Generated, never hand-edited:** `Scripts\Phase3\registry_schema.py`
  (`schema()`, `schema_bytes()`, `write()`); the bytes are deterministic
  (sorted keys, one-space indent, ASCII, LF, one trailing newline).
  `p3_registry_schema_check.py --write` regenerates the file.
- **`registry_digest()`** now covers the schema's bytes and the conditional
  preconditions; `REGISTRY_VERSION` is `/2`.
- **The code's gates were narrowed to what a schema can state** (found while
  writing it; handback Q3):
  - `keep_both`: each id is text with no surrounding whitespace (through B18
    any value was turned into text: `None` became the id `"None"`);
  - `override_protection`: each id is an integer or non-blank text (`None`,
    `True`, `[]` were accepted as ids);
  - policy scopes: the key is non-blank text and the display path, when given,
    is text (`5` was a root key); a policy effect is `None` or an object (`0`,
    `""`, `[]` were read as "no effect"); Prefer folder's `over_path_key` is
    text;
  - dates (a desired Content-created value): an explicit rule -- ASCII digits,
    a real calendar day in 0001-9999, `HH:MM[:SS]` up to 23:59:59, an offset up
    to ±23:59 -- instead of Python's `fromisoformat`, which in Python 3.14
    accepts `T24:00` and a `+05:60` offset;
  - a project-metadata field name must be text (a list raised `TypeError`).
- **`jsonschema`** is a test dependency only: listed as optional in
  `Install-Dependencies.ps1` and in `self_check`'s report; the product never
  imports it (checked).

### 2. Identifier relabelling (I15)

- New suite `p3_relabel_check.py`. The audit it drives found four places
  where an id decided an order or a label, all fixed:
  - `routing.ordered` / `ordered_pairs`: a conflict queue's "oldest decision"
    was the smallest decision id as an integer (a non-digit id fell through to
    the target's own id). Now the decisions' recording sequence
    (`RoutingProjection.sequence`, kept current by `refresh.apply`); a tie
    keeps display order.
  - `resolve.location_protection`: which protection a copy names as its rule,
    when two cover it, followed the policies' id order. Now
    `resolve.policy_order` (kind, scope, effect).
  - `pairs.resolve_pairs`: the presentation order keyed on image *a* -- the
    lower location id. Now both images in display order. The chain guard's
    message named the other pair with the lowest id; now the one whose
    preference was recorded first.
  - Conflicts inside a group or pair were ordered by their location ids; now
    by where they fall in display order (`resolve.conflict_order`).
  - Also: `_latest` and `routing._by_target` no longer break a sequence tie on
    the id; `plans.retaining_decisions` lists in recording order (the words a
    blocked removal shows), and a ready removal names its kept copies in
    display order. No payload field changed, so no fingerprint changed.
- `Decision.sequence` / `ReviewEvent.sequence` are still derived by the loader
  from the append-only row id -- the one place an id becomes an order, stated
  in ARCHITECTURE.md (handback JC 2).

### 3. Renamed roots and folders; links and mounts

- New `Scripts\Phase3\scopes.py`: each policy's scope is **present**,
  **unavailable** (its root was not observed: offline, not walked, part
  unreadable) or **dangling** (observed absent: the root's folder missing
  while its volume answered, or nothing at or under the folder in a completed
  scan).
- `evidence.root_states`, `evidence.load_scope_facts` (cached per database
  and evidence mark), `Evidence.scopes` (loaded when any policy is active).
- **The guard:** while an active protection is dangling, every Remove
  Location item carries the hard precondition `no_dangling_protection`
  (class retention; result `unknown`; blocked on evidence), naming the policy
  and how to clear it; the item links the policy version. It is attached only
  then (`registry.CONDITIONAL_PRECONDITIONS`), so a plan with no dangling
  protection is byte-for-byte B18's. A locked revision with a ready removal
  goes obsolete (`protection_dangling`). A dangling prefer / avoid is a
  warning in the preview only. `EVALUATOR_VERSION` is `/2`.
- **The surfaces:** a banner on the Decide page; an "In the latest scan"
  column, a status line and **Re-point...** in the Policies dialog
  (`review.RepointDialog`: a new version of the same policy, `revise_policy`;
  the identity suggestion shown with its label, filled in only on "Use the
  suggestion", nothing recorded until Re-point); the Plan page's population
  line and every revision's preview.
- **The suggestion** comes from identity alone: a file known under the old
  scope and not present now, whose volume serial + file index is present at a
  path keeping its place under the scope. A copy is never a match.
- **Offline roots now block** (`evidence.load_roots`): a root the latest
  inventory run did not walk is not available, whatever its own last scan
  said; its copies route BLOCKED (`INCOMPLETE_ROOT_COVERAGE`). Through B18 an
  unreachable root kept reading complete from its previous scan.
- **Phase 1 fix** (`RunCoordinator.get_environment`): each root's existence,
  drive type and file system are probed at every run. Through B18 they were
  cached for the whole session, so a root unplugged or renamed while the
  program stayed open was recorded as available.
- **Links and mounts:** written in ARCHITECTURE.md "Path matching" and tested
  on real junctions: a scope covers paths as scanned; Phase 1 never descends
  into a junction, directory symlink or volume mount point and records each
  at its own path.

### 4. A clean, auditable harness

- New `Scripts\p3_lab_harness.py`: the product's lab validator run in an
  isolated copy (checked against the lab's MANIFEST first), as shipped and
  under `-X dev`; exit 0, 16/16, stderr empty; `Logs\p3_lab_validation.json`
  (also `p3_lab_harness.json`).
- New `Scripts\p3_report.py`: the report shape (`fileorganizer.p3.harness-report/1`);
  `p3_fixture_check` writes `Logs\p3_fixture_check.json` in it.
- The lab's `MANIFEST.json` entry for its `README.md` was stale since B14-B16
  (the product's README had been edited); updated to the shipped bytes.

### 5. The B18 review's items

- **W1:** `b6_regression` E.F002 measures each run's latency as the median of
  21 timed queries after two warm-ups, with the garbage collector held off.
  The 2x limit is unchanged.
- **W2:** a bulk preview whose folder scope matched more than one on-disk
  spelling warns, naming them (`bulk.folder_spellings`, `spelling_warning`;
  `BulkPreview.warnings`, in its lines and its record only when present).
  Tested pure and on disk (`p3_closeout_check` §4b).

## Versions

`APP_VERSION` = `Phase2.VERSION` = **B19**; `fo_db.MODULE_VERSION` 1.14.0;
schema 16 (unchanged); `REGISTRY_VERSION` `/2`; `EVALUATOR_VERSION` `/2`;
`RESOLVER_VERSION` `/1` (unchanged: the same evidence and decisions resolve to
the same keepers; only presentation order and a rule's label changed);
`self_check` 36 runtime modules (+ `Phase3.registry_schema`, `Phase3.scopes`).

## Tests

| Suite | B19 |
|---|---|
| `p3_registry_schema_check` (new) | 30/31 (B20 task 0a, 2026-10-11): sections 1-5 and the captured rows pass; one section-6 check fails -- see "B20 task 0a correction" in `PHASE3_B19_HANDBACK.md` |
| `p3_relabel_check` (new) | 117/117 |
| `p3_dangling_check` (new) | 67/67 |
| `p3_lab_harness` (new) | 10/10; lab 16/16, stderr empty |
| `p3_closeout_check` | 62/62 (+ §4b) |
| `p3_mutation_check` | 70/70 (B20 task 0a, 2026-10-11): 44/44 mutants killed, 25/25 controls clean, the real tree untouched |
| `b6_regression` | 73/73, five consecutive full runs (E.F002 ratios 0.29-1.11×; limit 2×) |
| every other Phase 3 suite, `p2_*`, `self_check` | unchanged counts, all passing (handback) |

## Files

New: `Resources\Phase3\registry.schema.json`; `Scripts\Phase3\registry_schema.py`,
`scopes.py`; `Scripts\p3_registry_schema_check.py`, `p3_relabel_check.py`,
`p3_dangling_check.py`, `p3_lab_harness.py`, `p3_report.py`; this file;
`PHASE3_B19_HANDBACK.md`.

Changed: `Scripts\Phase3\registry.py`, `resolve.py`, `pairs.py`, `routing.py`,
`refresh.py`, `plans.py`, `planview.py`, `review.py`, `evidence.py`,
`model.py`, `bulk.py`; `Scripts\RunCoordinator.py`; `Scripts\Database\fo_db.py`;
`Scripts\Phase2\__init__.py`; `Scripts\self_check.py`;
`Scripts\Install-Dependencies.ps1`; `Scripts\b6_regression.py`,
`p3_fixture_check.py`, `p3_mutation_check.py`, `p3_closeout_check.py`,
`p3_plan_check.py`, `p3_regression.py`;
`Resources\Phase3\fixture_lab\MANIFEST.json`; `README.md`, `ARCHITECTURE.md`,
`STAGE_MAP.md`, `PROJECT_PLAN.md`, `Resources\Phase3\README.md`, `BASELINE.md`,
`SOURCE_SHA256.csv`.
