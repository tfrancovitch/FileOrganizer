# B9 — Phase 3, Build 3: Review Routing

**Date:** 2026-09-20
**From:** B8 (branch `phase3`, commit `e44613a`), on branch `phase3-b9`
**Handoff:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build3\`
(research synthesis, build plan, fixture lab F08 and F10)
**Handback:** `PHASE3_BUILD3_HANDBACK.md`

B9 routes attention. It does not change what B8 records or how B8 resolves
a group; it says where each group's attention is owed and why, from the
evidence, the decision record and a new record of routing events. Queues
route attention, they do not create truth. It changes no file.

## What is new

### The fifth family — review events, migration 010 (schema 10)

`p3_review_event`: append-only rows of routing provenance, each under a
`p3_operation` like every other p3 row —

| `event_kind` | Who writes it | What it records |
|---|---|---|
| `deferred` | a person | "not now", with a return trigger: `return_kind` `manual` / `time` (+ `return_on_utc`) / `evidence_change` (the target's evidence at that moment, ids only, in `detail_json`) / `source_available` / `hash_current` |
| `skipped` | a person | passed by; an audit row and nothing else |
| `restored` | a person, or the detector | a deferral, blocker or flag ended; `refers_to_review_event_id` names it |
| `needs_revalidation` | the detector | a decision's evidence moved; `return_condition` says what, `detail_json` names the decisions |
| `blocked` | the detector | an evidence blocker on a target a person has engaged with, with the return kind it clears on |

The detector's rows sit under an operation whose `actor_kind` is
`system_evidence` and `agent_kind` `routing_detector` (agent version = the
build): a system agent as a first-class actor kind, never a human
pretending, never a decision. No p3 row is ever updated or deleted.

### The routes — `Scripts\Phase3\routing.py`

A pure function of the model plus the moment (`Evidence.now`), like the
resolver. Every condition that applies to a group is reported; the earliest
in this order is its one primary route:

```
1 Conflicts & Exceptions   contradictory intent -- needs a decision now
2 Needs Revalidation       a decision exists but its evidence moved -- needs a look
3 Blocked by Evidence      nothing can be decided safely yet -- quiescent
4 Deferred / Snoozed       a person chose not to decide yet -- quiescent
5 Ready for Plan           resolved, something to plan
6 Resolved                 resolved, nothing to plan (every copy kept)
7 Queue                    the ordinary review queue (In progress / Unreviewed)
```

- **Conflicts** are the resolver's, now named the research's way:
  `protected_vs_redundant` (B8's `redundant_protected`),
  `canonical_no_longer_keeper` (reason `marked_redundant` or `left_group`;
  B8's two kinds folded), `same_precedence_policy_tie` (new: a prefer-folder
  and an avoid-folder policy both cover a copy the recommendation still has
  to rank -- **advisory**: it routes attention, it withholds nothing), and
  the last-copy floors. The route lists conflicts by reclaim at stake, then
  the oldest unresolved.
- **Needs Revalidation** is computed live from B8's evidence bindings: a
  bound `[file_path_id, observation_id]` pair whose observation is no longer
  the location's current one; a group decision whose bound membership is no
  longer the current membership. The decision is untouched. It clears the
  same way -- by the comparison clearing (a new decision on current
  evidence, or a withdrawal) -- never by a mark.
- **Blocked** is computed from Phase 1/2 evidence, never decided:
  `physical_identity_unknown`, `stale_content_hash` (an older observation's
  identity is all the copy has; a copy re-examined and found unique by size
  is current, not stale), `incomplete_root_coverage` (the root's latest walk
  is interrupted, failed, unavailable or absent -- a *completed* walk with
  inaccessible folders warns and does not block, so one denied folder cannot
  park every group). A recorded blocker of a kind this build cannot compute
  (a preview, Build 5's) is honoured while open.
- **Deferred** is an open deferral whose trigger has not fired, evaluated
  live: a date against `now`; an evidence change against the evidence the
  deferral recorded; the source root available and no copy cloud-only;
  every fingerprint current. A B8 `defer_review` decision counts as an open
  manual deferral. Manual restore is always available.
- **Skip is not Defer**: key `s` writes a `skipped` row and moves the
  selection to the next group; key `d` opens the Defer dialog.

Every route is dynamic -- recomputed, never copied. A group's members'
revalidation flags and blockers are the group's too; a member's deferral
parks the copy, not the group, unless every undecided copy is parked.

### The detector — `routing.reconcile`

Runs when the Decide page opens and when a Pre-Scan / Find My Duplicates /
Full Fingerprinting run finishes (`gui._run_finished`). Under one system
operation it writes what changed and nothing else: a `needs_revalidation`
flag for a target whose own decisions drifted and has none open; a
`restored` row for a flag whose drift cleared, for a computed blocker that
cleared, and for a deferral whose trigger fired; a `blocked` row for a
computed blocker on a target a person has engaged with (a decision on it or
one of its copies, or a deferral). A second run writes nothing. It never
touches a decision; a failure to write provenance is logged and never stops
a run or a page.

### The store — `Scripts\Phase3\store.py`

`defer(target, disposition, until, note)` for the five dispositions,
`skip`, `restore(refers_to, withdraw=)`, `review_events_for`, and
`system_events` for the detector. The journal export lists review events
with their triggers.

### The Decide page — `Scripts\Phase3\review.py`

- **Status** is the route word; a **Conditions** column lists every
  condition that applies (evidence changed, identity unknown, snoozed until
  a date, policy tie, ...). The group's detail explains each in file terms.
- **Show** filters by route -- Queue (the default), Conflicts & Exceptions,
  Needs Revalidation, Blocked by Evidence, Deferred / Snoozed, Ready for
  Plan, Resolved, All -- and by lens: High Reclaim, Cross-Root, Unknown
  Physical Identity, Hard-Link Aliases, Filename Divergence, Extension
  Divergence, Policy Tie.
- **Defer...** opens a dialog: defer indefinitely, snooze until a date, until
  the evidence changes, until the source is available, until fingerprints
  are current, with a note. On a parked group the button reads **Restore**.
- **Skip** (key `s`) and **Confirm decisions** (re-records the drifted
  decisions on current evidence in one operation, each superseding its
  predecessor; a decision that no longer makes sense -- a canonical that
  left, a mark on a copy that is no longer a duplicate -- is left for Undo
  and said so).
- An empty Queue says **Currently clear**, never complete; other routes'
  counts follow. A group an action just moved off the shown route stays in
  view so the result is seen.
- The history list carries the routing events alongside the decisions;
  events are not undoable (they are not decisions).
- The summary's Decide line counts the routes.

### Elsewhere

- `fo_db.APP_VERSION` and `Phase2.VERSION` are `B9`; `APP_SCHEMA_VERSION`
  and the two `REQUIRED_SCHEMA_VERSION`s are 10. A B8 or B7.x project
  migrates forward on first open after the usual backup.
- `Phase3\model.py`: `Decision.binding`, `ReviewEvent`, `RootCoverage`;
  `Location.hash_current / cloud_only / in_current_group`;
  `Evidence.events / roots / now`. `evidence.py` loads decisions with their
  bindings, the events, root coverage (from `Phase2.coverage`), and every
  location a decision or event names that is no longer a current member.
- `self_check.py` imports `Phase3.routing` (23 modules).
- `Resources\Phase3\fixture_lab\`: F08 and F10 join the five; the research
  manifest still matches every shipped file.
- Documents: `README.md`, `ARCHITECTURE.md`, `STAGE_MAP.md`, `PROJECT_PLAN.md`,
  `Resources\Phase3\README.md`.

## Verification

| Suite | Result |
|---|---|
| `p3_fixture_check.py` — F01, F02, F03, F08, F09, F10, F15 against the general resolver and router, plus order-independence | 46/46 |
| `p3_regression.py` — B8's 145 checks brought to B9, plus routing on hand-built models (the precedence table over all 127 combinations, open/closed events, every trigger, drift, blockers, warnings vs unusable roots, the advisory tie, the Conflicts order, determinism), routing on a real project (idempotent detector, Skip vs Defer, an elapsed snooze, a rescan that moves the evidence, stale vs re-examined, Confirm), the window (Defer dialog, Skip, Restore, routes, lenses, Currently clear, the run-finished detector, Confirm decisions), zero mutation | 227/227 |
| `b6_regression.py` | 73/73 |
| `p2_regression.py` | all passed |
| `p2_dashboard_check.py` | all passed |
| `self_check.py` | ready, schema 10 |

## Known and unchanged

- Everything B8 listed.
- The detector writes `blocked` rows only for engaged targets; an untouched
  group's blocker is a computed route with no row behind it, by design.
- `snooze_until_preview_available` is not offered (Build 5); a recorded
  `preview_available` blocker or deferral is honoured if one exists.
- The Deferred-queue hygiene views (Due Now, Older than 30 / 90 days, No
  Return Trigger) and manual priority tiers are not built; the Show box's
  routes and the Conditions column cover today's volumes -- see the handback.
