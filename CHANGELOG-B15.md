# B15 — Phase 3, Build 8: the Plan Builder

**Date:** 2026-09-25
**From:** B14 (tag `phase3-b14`, commit `989294b`), on branch `phase3-b15`
**Handoff:** `Phase3_Handoff_Build8.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build8\`
(research synthesis grounded in P3.R7 and P3.R10 §15/§24; plan)
**Handback:** `PHASE3_B15_HANDBACK.md`

B15 turns the decisions a person has already made into a **plan**: proposed
logical actions (remove this location; keep that one, and why), previewed
against the project's evidence, then frozen and fingerprinted. It is Phase 3
only. Nothing is carried out, no source file is read or written, and a
locked plan records reviewed intent, not the state of the disk (P3.R7).
Schema 13.

**Before the build (the handoff's step 0):** tag `phase3-b13` now points at
`e48fbf0`. That commit re-hashes five `SOURCE_SHA256.csv` entries on LF files
and makes no code change. A clean checkout of the tag verifies.

## What is new

### Schema 13 — `Scripts\Database\migrations\013_phase3_plans.sql`

Eight tables. They normalize what is **queried** and keep as JSON what is only
read back whole:

| Table | What it holds |
|---|---|
| `p3_plan` | the long-lived plan (name, note, the creating operation) |
| `p3_plan_revision` | an immutable snapshot: revision number, status, fingerprint (at lock), locked / obsolete, what it was built on (record and evidence marks), the preview's summary |
| `p3_plan_event` | the revision's history: previewed, locked (with the dependency set), obsolete (with the reasons), superseded |
| `p3_plan_item` | one logical action: kind, target, sources, desired effect, **evidence binding (the `p3_decision` shape)**, estimated effect, postconditions, impact, state, blocked reason |
| `p3_plan_item_decision` | **the dependency index**: which decisions justify an item, and which keep its target (indexed by decision) |
| `p3_plan_item_policy` | which policy versions bear on an item (indexed) |
| `p3_plan_dependency` | the item graph (a removal depends on its keeper's preserve item); acyclic |
| `p3_plan_precondition` | each declared precondition: class, severity, stage (evidence_preview / live_preflight), parameters, the preview's result and reason |

### The vocabulary — `Scripts\Phase3\registry.py`

- **States:** P3.R7 §5's eight.
- **Action kinds:** R10 §15's five. `remove_location`, `move_location`,
  `rename_location`, `update_source_metadata`, `no_op_preserve`. No hard-link
  replacement (R7 §21).
- **Preconditions:** R7 §37's twenty-two keys with their §15 classes, plus one
  Build 8 addition, `no_retaining_decision`.
- **Severities and results:** R7 §16's.
- **Per action:** which preconditions are checked now (evidence stage) and which
  are declared for Phase 4 (live stage). Also the postconditions and the impact
  class.
- **Capability profile:** an empty metadata-writer profile, and R7 §14's
  disclosure, word for word.

### The Plan Builder — `Scripts\Phase3\plans.py` (new, pure)

- **`build(ev, proj, routing)`.** The population is every exact-duplicate
  group and visual pair whose **decisions** resolve it ready for a plan (the
  Ready-for-Plan route's own criterion). A group you deferred stays out. One
  that the evidence blocks, or whose decisions need revalidation, comes in with
  its items visibly blocked.
  - A redundant candidate becomes a `remove_location`. A keeper, a protected
    copy or a preferred image becomes a `no_op_preserve`, saying why.
  - Items carry their justifying decisions, the policy versions that bear on
    them, an ids-only evidence binding (target, keepers, source), their
    dependencies and postconditions.
- **The Phase 3 Evidence Preview.** It evaluates every evidence-stage
  precondition from project evidence alone:
  - identity: the target was present at the last scan;
  - evidence: the decisions rest on current evidence, no conflict stands, the
    fingerprints and coverage are current;
  - retention: not protected (or explicitly overridden), **no current decision
    anywhere keeps it**, a keeper is decided, present and not itself planned
    away, and the minimum location and physical-copy counts hold;
  - a cloud-only copy is a warning.

  A hard `fail` blocks; a hard `unknown` blocks as `blocked_evidence` (never
  pass); `unsupported` is an unsupported action.
- **`no_retaining_decision`** answers the question B13 carried forward. It
  reads every current decision naming the image: an explicit Keep; its
  exact-duplicate group's Keep all, canonical, or explicit keeps; any pair's
  Keep both, preference, or a related variant's default Keep both. If any keeps
  the image, the removal is generated and blocked, naming the decisions. It is
  never dropped silently and never allowed. No decision kind or resolver rule
  changed.
- **`logical_item`:** move, rename and metadata items, previewed.
  - A destination the evidence already holds is a collision.
  - A metadata item is unsupported, because no writer exists.
  - An item no decision asks for fails `decision_current`: the Plan Builder
    never invents intent.
- **`summarize`.**
  - Plan-Eligible Reclaimable Bytes (Phase 3) count each physical object once,
    and only when every name of it is removed.
  - They are shown beside Phase 2's Potentially Reclaimable Exact-Duplicate
    Bytes, under their own name, and never in place of it. Verified Reclaimed
    Bytes are named as Phase 4's.
- **`fingerprint` / `payload`:** SHA-256 over the canonical payload. Items,
  targets, sources, desired effects, decision / policy / evidence references,
  preconditions with results, postconditions, dependencies, states.
  Deterministic in any row order.
- **`obsolescence`:** dependency-scoped reasons for a locked revision. A
  justifying or retaining decision withdrawn or superseded; a new decision that
  keeps a planned removal; a policy version changed; a new protection over a
  planned removal; the target's evidence changed; the source group's
  membership changed; the source pair no longer current; a new conflict on a
  source.
- **`diff`:** R7 §34, between revisions.

### Persistence and the detector — `Scripts\Phase3\store.py`, `routing.py`

- **`record_plan_revision`:** one `plan_build` operation writes everything and a
  `previewed` event. Earlier unlocked revisions of the plan become
  `superseded`; a locked revision never does.
- **`plan_revision`:** reads a revision back exactly; the fingerprint is equal.
- **`lock_plan_revision`** follows R7 §29:
  1. The record and evidence marks must be where the preview found them (B10/B12's commit guard), else build again.
  2. Canonicalize the payload.
  3. SHA-256.
  4. Lock.
  5. Record a `locked` event.
  6. Put the exact dependency set in that event.
- **`revisions_depending_on`:** the dependency index, one indexed lookup.
- **`reconcile_plans` / `mark_plans_obsolete`:** the existing revalidation
  detector (`routing.reconcile`) now also watches each locked revision's own
  dependencies. One whose dependencies moved becomes `obsolete` through a
  system operation and event. Its items, links and fingerprint are never
  touched. Only when the detector has the pairs loaded; the Decide page's
  pair-less pass leaves plans to the next full pass.

### The window — `Scripts\Phase3\planview.py` (new), `Phase2\gui.py`

A **Plan** entry in the navigation.
- **Top:** what is ready for a plan and what is left out.
- **Actions:** Build a new revision, Lock revision, Compare with previous.
- **Left:** the revisions with status and fingerprint.
- **Right:** the preview in R7 §26's shape, and the disclosure in a band that
  never scrolls away.
- **Below:** the items with state and reason, then the selected item's
  preconditions: those checked now, with results, and those declared for
  Phase 4. Also its decisions (justifying and keeping), dependencies and
  postconditions.
- **Model reuse:** the page reuses the detector's model at open, and any model
  while the record and evidence marks stand, so it loads once, not twice.

### Fixtures — `Resources\Phase3\fixture_lab\`

F11 (Plan_Drift) and F12 (Hard_Precondition_Unknown) are copied from the
research lab and verified against its manifest. `p3_fixture_check.py` answers
them with the product's code: `plans.obsolescence` on F11's locked revision and
`plans.build` on F12's evidence. The lab's own `validate_fixtures.py` reports
14/14 on the fixtures shipped. The lab README, `Resources\Phase3\README.md` and
the lab manifest say so.

### Versions

`fo_db.APP_VERSION` = `Phase2.VERSION` = **B15**; `fo_db.MODULE_VERSION`
1.10.0; schema **13** (`fo_db.APP_SCHEMA_VERSION`,
`core.REQUIRED_SCHEMA_VERSION`, `self_check.REQUIRED_SCHEMA_VERSION`).
`self_check.RUNTIME_MODULES` += `Phase3.plans`, `Phase3.planview` (self_check reports 32).

## Tests

- **New:** `Scripts\p3_plan_check.py` (80).
  - The vocabulary.
  - The builder on models: every precondition outcome; unknown never passes
    (300 random result sets); the population; no keeper means blocked, never
    chosen; the retention rule in five shapes; move, rename and metadata
    items; the three metrics; the fingerprint.
  - A real project with real images: freeze, read back, the lock guard and
    lock, supersession, the dependency index, obsolescence for each trigger
    and none for an unrelated change, B13's case on real images.
  - Zero source access under a read guard and a write guard.
  - The window.
- **`p3_fixture_check.py`:** F11 and F12 (65 → 84 checks with them and F16).
- **`p3_regression.py`:** schema 13 and the sixteen p3 tables; B15.
- **`p3_visual_check.py`:** schema 13.
- Every other suite is unchanged and green.
