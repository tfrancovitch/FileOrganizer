# B10 — Phase 3, Build 4: Bulk / Policy

**Date:** 2026-09-21
**From:** B9 (branch `phase3-b9`, commit `94dfc76`), on branch `phase3-b10`
**Handoff:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build4\`
(research synthesis, build plan, fixture lab F04 and F05)
**Handback:** `PHASE3_BUILD4_HANDBACK.md`

B10 lets one decision reach many groups at once — after a preview, with the
membership frozen, never overwriting an explicit decision — and makes
creating a policy a deliberate act that shows what it covers today and says
that future matches will be evaluated against it. It changes nothing about
how B8 resolves a group or how B9 routes one. It changes no file.

## What is new

### The fourth family — bulk batches, migration 011 (schema 11)

`p3_bulk_batch` and `p3_bulk_member`, the fixture lab's shape with the
product's conventions, append-only under `p3_operation` like every p3 row:

| Column | What it records |
|---|---|
| `scope_kind` | `explicit_selection` (the exact groups a person checked) or `query_result_snapshot` (what a filter listed at one moment) |
| `query_json` | the frozen query — the Show filter that listed the groups and, for a folder action, the folder; NULL for a checked selection |
| `frozen`, `committed` | both 1: the product writes a batch at commit, in the same operation as its decisions |
| `action`, `parameters_json` | which bulk action, with its inputs (the folder, the deferral, the checked ids) |
| `mode` | `preserve` — the only mode this build records |
| `preview_json` | the categorized counts exactly as previewed when the person confirmed |
| `p3_bulk_member` | one row per target the batch addressed, with its **disposition** — `decided`, `already_satisfied`, `preserved`, `conflict`, `blocked`, `not_applicable` — and why |

Every decision a batch records carries `origin_kind = 'bulk_explicit_human'`
and `origin_ref = <batch id>` (`registry.py` registers the origin kinds; the
store validates them; an index on the origin serves "what did this batch
change?"). Membership is frozen at commit and never re-evaluated: a target
that comes to match the same query later is not a member. **A snapshot never
becomes a policy** (P3-A41).

### Bulk decisions — `Scripts\Phase3\bulk.py`

A pure function of the model, like the resolver and the router. Five
actions, each declared in the registry with the decision kinds it records
and — where one exists — the policy kind that says the same about the
future:

| Action | Members | Records |
|---|---|---|
| **Keep all copies** | groups | `keep_all_group` |
| **Accept the recommendation** | groups | `canonical_location` = the policy-suggested copy, plus (unless switched off) `redundant_location` on the other undecided copies; protected and explicitly kept copies stay, and the preview says so |
| **Keep copies under a folder** | copies | `must_keep_location` — policy twin *Protect folder* |
| **Mark copies under a folder redundant** | copies | `redundant_location` — policy twin *Avoid folder* (a policy cannot mark a copy redundant; the dialog says so) |
| **Defer** | groups | a `deferred` review event with a return trigger, no decision |

**The preview** (synthesis §3) runs every target in scope through the same
resolver Builds 1–3 tested, with the batch's planned decisions in place
(per group, all at once, so the last-copy floor is judged over the whole
batch), and buckets each one:

```
N groups in scope (Show: Queue)
  -> X would receive a decision (D decisions: ...)
  -> Y already satisfy it
  -> Z have an explicit incompatible decision, preserved (caution)
  -> W would conflict -- not recorded; needs a person (conflict)
  -> V are blocked by evidence -- not recorded (blocked)
       preserved (explicit decision stands): report.pdf -- Keep (#12) stands
       ...
No source files will be changed.
```

**Preserve, don't overwrite** (synthesis §4): an active decision in the
same conflict domain saying otherwise, the group's explicit canonical, a
Keep all in force, a redundant mark under a Keep all, a mark on the copy a
recommendation would make canonical — each is an explicit decision the
batch leaves standing and counts. A mark on a protected copy, a set of
marks that would leave no copy, a group already in Conflicts & Exceptions —
each is a conflict the batch does not record. A group Build 3 blocks
receives no retention decision (it can still be deferred). Where a
recommendation is accepted, the canonical decision gates the marks that go
with it: when the person's own canonical stands, nothing is recorded for
that group.

**Commit equals preview** (synthesis §7): `store.commit_bulk` records
exactly the candidates the preview classed as decided, bound to the
evidence the preview captured — if a rescan moved the evidence in between,
the revalidation detector says so afterwards. If the *decision record*
moved in between (any new operation), the commit refuses and asks for a
new preview. One operation of kind `bulk` holds the batch row, every
member row and every decision; a failure rolls all of it back.

**Undo** (`store.undo_batch`): one operation of kind `bulk_undo` withdraws
every decision the batch recorded that still stands, and restores every
deferral it recorded that is still open. A decision the person has since
superseded or withdrawn by hand is left alone — their later intent stands.
The batch and its member rows are untouched.

### The Decide page — `Scripts\Phase3\review.py`

- A **check column** on the group list — click it, or **Space** on the
  focused row — with **Check all shown** / **Clear** and a running
  **N checked**. **Bulk action…** appears only once something is checked.
  The letter keys (`k a c r d s`) act on the selected row only, checked or
  not: a check never changes what a keystroke does.
- **Bulk action…** — the action with its inputs (folder, deferral trigger,
  whether to mark the other copies), then the scope in the research's
  words, narrowest first and the default: **N checked groups** / **N
  current matches (Show: …)** — frozen at commit; a later match is not
  included / **Create a reusable policy instead** (enabled for the two
  folder actions, naming the twin: future matches too, no decision
  recorded). **Preview** fills the categorized counts with samples;
  **Record N decisions** is enabled only for the inputs previewed, and any
  change voids it. On commit the window says what was recorded and what
  was left as exceptions, clears the checks and re-derives.
- **Batches…** (also on the summary's new **Bulk batches** line) lists
  every batch — when, action, scope, members, recorded, in force, note —
  with each batch's members and what became of them, and **Undo batch**.
- The history list names the batch a decision came from: *Keep (batch #3)*.
- **Add policy** previews first: **Preview** shows what the policy covers
  now (every present file location under the root or folder, and how many
  of those are in duplicate groups), what changes today (copies newly
  protected, new conflicts, recommendations that change, plan readiness),
  and states that **future matching evidence WILL be evaluated against this
  policy** — a file that appears there later is covered too, with no
  decision recorded for it. **Create policy** is enabled only for the
  inputs previewed. The bulk dialog's policy route lands here prefilled.

### The resolver — `Scripts\Phase3\resolve.py`

Step 1's protection is factored into `location_protection` and exposed as
`protected_locations(ev)`: every location the model holds that an active
protection policy covers and no override lifts — group member or not. No
mechanics changed; F05 asks the question for files that are in no group.

### Elsewhere

- `fo_db.APP_VERSION` and `Phase2.VERSION` are `B10`; `APP_SCHEMA_VERSION`
  and the two `REQUIRED_SCHEMA_VERSION`s are 11. A B9, B8 or B7.x project
  migrates forward on first open after the usual backup.
- `Phase3\model.py`: `Batch`, `BatchMember`, `Evidence.batches`.
  `Phase3\__init__.py`: `BULK_SCHEMA`, `BULK_QUERY_SCHEMA`.
- `store.py`: `record_decision` is now `_validate_decision` +
  `_decision_row` (origin-aware); `defer` shares `_deferral_row` with the
  batch; `history_for` carries the origin; `counts()` counts batches;
  `batches`, `batch`, `batch_members`, `batch_decisions`, `batch_events`;
  the journal export lists batches and marks a batch's decisions.
- The Show filter (routes and lenses) moved from the page into
  `bulk.filter_groups`, one pure function the page draws from and a
  snapshot freezes; `review.py` keeps its names.
- `self_check.py` imports `Phase3.bulk` (24 modules).
- `Resources\Phase3\fixture_lab\`: F04 and F05 join the seven; the research
  manifest still matches every shipped file.
- Documents: `README.md`, `ARCHITECTURE.md`, `STAGE_MAP.md`, `PROJECT_PLAN.md`,
  `Resources\Phase3\README.md`.

## Verification

| Suite | Result |
|---|---|
| `p3_fixture_check.py` — F01, F02, F03, F04, F05, F08, F09, F10, F15 against the general resolver, router and query matcher, plus order-independence | 56/56 |
| `p3_regression.py` — B9's 227 checks brought to B10, plus bulk on hand-built models (the registry, the Show filter as one function, the frozen query and F04's shape, every bucket, the floor over a whole batch, the canonical gating its marks, a deferral batch, determinism, 4,000 groups previewed), bulk on a real project (one operation per batch, provenance, the record guard, undo leaving a hand-superseded decision alone, a deferral batch, accept the recommendation, F04 beside F05 on real files, the policy impact preview, zero mutation), the window (checks and Space, the letter keys unchanged, the bulk dialog's three scopes, preview and commit, Batches and Undo, the policy route, the summary line, zero mutation), and the Add-policy preview gate | 337/337 |
| `b6_regression.py` | 73/73 |
| `p2_regression.py` | all passed |
| `p2_dashboard_check.py` | all passed |
| `self_check.py` | ready, schema 11 |

## Known and unchanged

- Everything B9 listed.
- Only the preserve mode is recorded; explicit overwrite / supersession
  across a batch (the research's distinct operation) is not offered — the
  store refuses any other mode. The workflow that covers the case is Undo
  the batch, then batch again.
- Checks are on the group list only; the copies of one group are few and
  the letter keys cover them. A location-level checked selection is not
  built.
- A policy impact view that stands on its own (outside the creation
  preview) is not built; the creation-time preview covers the need.
- `frozen` and `committed` are always 1: nothing yet records a batch it
  has not applied.
