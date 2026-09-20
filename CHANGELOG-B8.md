# B8 — Phase 3 begins: Builds 1–2 (core persistence, exact-duplicate decisions)

**Date:** 2026-09-20
**From:** B7.2 (tag `phase2-b7.2`), on branch `phase3`
**Handoff:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\` (research synthesis,
build plan, fixture lab F01/F02/F03/F09/F15, the P3.R2 keeper-rule catalog)
**Handback:** `PHASE3_HANDBACK.md`

B8 is the first build of Phase 3 (Decide / Plan). It records what *should*
happen to exact duplicates — as attributable, append-only human intent — and
shows the current answer derived from that record. It changes no file. Acting
on a decision is Phase 4, which does not exist.

## What is new

### The decision record — migration 009 (schema 9)

Five tables, every one append-only; the product never UPDATEs or DELETEs a row:

| Table | Holds |
|---|---|
| `p3_operation` | one user action: kind, `actor_kind`, `actor_id`, `agent_kind`, `agent_version`, `session_id`, `command_id`, time, note |
| `p3_decision` | an immutable decision: target (`file_location` by `file_path_id`, or `exact_duplicate_group_version` by `content_id`), kind, typed `value_json`, origin, `evidence_binding_json`, `supersedes_decision_id`, rationale |
| `p3_decision_withdrawal` | the withdrawal of a decision — a row, never a flag on the decision |
| `p3_policy` | a policy's identity and kind |
| `p3_policy_version` | its versions: scope, effect, status `active` / `retired`, `supersedes_policy_version_id` |

The evidence binding is references only — file, observation, content, hash
run, and the group's members with their observations at the moment of
deciding — never a copy of Phase 2 evidence. The kind vocabularies are
validated by the registry in code rather than by CHECK constraints, so the
later builds can widen them without rebuilding the tables.

### The registry — `Scripts\Phase3\registry.py`

Six decision kinds — `must_keep_location`, `keep_all_group`,
`canonical_location`, `redundant_location`, `defer_review`,
`override_protection` — each declaring what it may target, its value shape,
its resolution class, its conflict domain (a new decision in the same domain
on the same target supersedes the old), whether it needs evidence, whether it
can be superseded, whether it affects reclaim, and what confirmation the
window must obtain (`none` for five; an explicit override dialog for one).
Five policy kinds: `protect_source_root`, `protect_folder_subtree`,
`prefer_source_root`, `prefer_folder_subtree` (optionally "over" one other
folder), `avoid_folder_subtree`. The store refuses anything the registry does
not describe.

### The resolution — `Scripts\Phase3\resolve.py`

A pure function of the model (`model.py`), loaded from a project by
`evidence.py` or from the research fixtures by `p3_fixture_check.py`. The
eight-step order from the keeper-rule catalog, an earlier class outranking
every later one: hard constraints (protection minus explicit overrides,
explicit must-keep, the floors), explicit location decisions (canonical,
redundant), the explicit group decision (keep all), folder policy, source-root
policy, the empty project-policy slot, the empty heuristic slot, display
order last and never as intent. Per member: Protected, Keeper, Redundant
candidate, Undecided. Per group: keeper set, explicit canonical, policy
suggestion (labelled `policy`), effective canonical, conflicts, location
count, physical copies, hard-link aliases, size, potential reclaim (Phase 2's
number), plan-eligible reclaim (objects whose every name is a redundant
candidate, in a group with no conflict), ready-for-plan, review state
(unreviewed / in progress / deferred / resolved / conflict). Multiple keepers
are a normal resolved state; the canonical is always a keeper; unknown
physical identity gives unknown reclaim, never a guess; decisions whose
target is no longer current evidence are reported as orphaned, not applied.

### The protection gate

A `redundant_location` decision on a location an active protection policy
covers has **no effect**: the copy stays Protected and the group carries a
`redundant_protected` conflict (no reclaim, not ready) until an
`override_protection` decision naming that exact policy version exists for
that location. The gate is in the resolution. The store records an override
only with the explicit confirmation flag, a non-empty rationale, and a
protection policy that actually covers the location; the window's Override
dialog names the rule, requires the reason and an acknowledgement, and
records it as its own operation kind.

### The store — `Scripts\Phase3\store.py`

One user action is one operation is one transaction. Recording a decision
supersedes the active decision in the same conflict domain on the same
target (a new canonical supersedes the old; a Keep supersedes a redundant
mark on the same copy). Withdrawal (Undo) adds a withdrawal row and does not
revive what the withdrawn decision had superseded — the research's F09 shape;
a later re-apply supersedes the withdrawn row so the chain stays whole. Keep
All may withdraw the group's redundant marks in the same operation. Policies
are created as version 1; a change, a retirement or a reactivation is a new
version. `export_decision_journal` writes the whole record as text.

### The Decide page — `Scripts\Phase3\review.py`

Inside the one window: **Decide** in the side panel; **Review duplicates**
and **Policies…** on the project summary's new Decide lines; **Review this
duplicate group** in a file's Details pane on the Files page. Left: every
current group (file, copies, physical, aliases, size, potential, eligible,
roots, status, canonical), sortable by heading, filtered by status. Right:
the selected group's facts and conflicts, its members with status /
protection / what decided it / policy tier, the actions Keep, Keep all, Set
canonical, Mark redundant, Defer / Undefer, Override protection…, and the
group's decision history with Undo selected. Keys k / a / c / r / d on the
member list. The Policies dialog adds (root picker or folder browser; the
optional "over" folder), retires and reactivates. Recording is instant — no
estimate, no progress screen. Every action re-derives the page from the
record.

### Elsewhere

- `fo_db.APP_VERSION` and `Phase2.VERSION` are `B8`; `APP_SCHEMA_VERSION`,
  `Phase2.core.REQUIRED_SCHEMA_VERSION` and `self_check.REQUIRED_SCHEMA_VERSION`
  are 9. A B7.x project migrates forward on first open, after the usual
  pre-migration backup; every Phase 2 row is untouched.
- `self_check.py` imports the Phase 2 and Phase 3 modules too (22 modules).
- `hub.py`'s `line()` takes `view=True` for buttons that open a page rather
  than start a run (they do not need the project under `Projects\`).
- `Resources\Phase3\`: the five fixtures of the research fixture lab, its
  reference validator and manifest (byte-identical to the research's), and
  the keeper-rule catalog. `Resources\Phase3\README.md` says what is there.
- `README.md` (B8 headline, "4a. Decide", what it does / does not do, where
  the record lives), `ARCHITECTURE.md` (re-headed for B8; the Phase 2 and
  Phase 3 sections; schema 9), `STAGE_MAP.md` (the Decide page is not a run),
  `PROJECT_PLAN.md` (§4a Phase 3 status, the 2026-09-20 decisions, §6 next).

## Verification

| Suite | Result |
|---|---|
| `p3_fixture_check.py` — F01, F02, F03, F09, F15 against the general resolver, plus order-independence | 37/37 |
| `p3_regression.py` — registry gate, resolver (order, gate, override, floors, hard links, unknown identity, policies, history, determinism, 4,000 groups in 0.2 s), a real project (runs, migration fresh and from schema 8, store, transactions, rebuild-from-copy, journal), zero source mutation (fingerprint + write guard), the window headless (Decide line, page, every action, both dialogs, Undo, filter/sort, jump-in) | 141/141 |
| `b6_regression.py` | 73/73 |
| `p2_regression.py` | all passed |
| `p2_dashboard_check.py` | all passed |
| `self_check.py` | ready, schema 9 |

`SOURCE_SHA256.csv` lists the shipped files including the new ones; every
one hashes as recorded.

## Known and unchanged

- Everything B7.2 listed as known and unchanged, including A-014.
- No cross-process lock exists (PHASE-2-HANDOFF); two windows on one project
  would both record decisions. Needed before Phase 4, not for a record that
  only appends.
- The Decide page recomputes the whole projection on every action (0.2 s for
  4,000 groups); per-group re-resolution is the obvious optimisation if a
  real corpus makes it felt.
- A group whose every copy is protected and that carries no decision shows
  as unreviewed — protection is a constraint, not a review.
- The vocabulary on the screens is provisional (see the handback).
