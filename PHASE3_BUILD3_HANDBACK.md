# Phase 3 handback — Build 3 (Review Routing), built as B9

**Against:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build3\02_BUILD3_HANDOFF_PLAN.md` §5
**Date:** 2026-09-20
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B9\` — a git worktree of
branch `phase3-b9`, created from the B8 commit (`e44613a` on `phase3`). Not
committed, not tagged; both are the owner's call after this review. When
accepted, `phase3` fast-forwards to it. `CHANGELOG-B9.md` says what
changed; this document says how it measures against the handoff.

**About the base.** The handoff assumed tag `phase3-b8` existed. It did not:
the reviewed B8 work was still uncommitted on `phase3`. So that Build 3
could branch from exactly the reviewed state, I committed B8 as `e44613a`
(message "B8: Phase 3 begins …") and left the tag to you:
`git tag -a phase3-b8 e44613a`. The B8 folder is otherwise untouched.

## 1. The necessities (plan §1), item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | `p3_review_event` table, append-only | **Done** | `Scripts\Database\migrations\010_phase3_review_events.sql` (schema 10): the fixture lab's shape plus `return_on_utc` (a time snooze's date, queryable), `detail_json` (the target's evidence for an evidence-change snooze; what drifted for a flag) and `refers_to_review_event_id` (a restore names what it ends). Every row under a `p3_operation`; never updated or deleted. Fresh projects and B8/B7.x migrations proved in `p3_regression.py` §3. |
| 2 | The revalidation detector, wired after rescans and on review open | **Done** | `Phase3\routing.py`: `binding_drift` / `decision_drift` compare each active decision's bound `[file_path_id, observation_id]` pairs (and, for a group decision, bound membership) against current evidence; `reconcile` writes a `needs_revalidation` row on mismatch and never touches the decision. Wired in `gui._run_finished` (Pre-Scan, Find My Duplicates, Full Fingerprinting) and in `show_review`. Proved on hand-built models (§6), on a real project after a rescan that changed a copy (§7), and through the window's own run-finished path (§5). F10 passes. |
| 3 | Deferred with real return triggers, replacing the bare `defer_review` | **Done** | `store.defer` records a `deferred` event with the disposition's return kind; `routing.deferral_fired` evaluates the trigger live — a date against `now`, an evidence change against the evidence the deferral recorded (the same comparison as #2), the source available (root available, no cloud-only copy), fingerprints current; `reconcile` writes the `restored` row when one fires. Manual restore always available (`store.restore`, the Restore button). B8 `defer_review` decisions read as open manual deferrals; the Defer action no longer records a decision. Every trigger proved in §6 and §7. |
| 4 | Blocked by Evidence — `physical_identity_unknown`, `stale_content_hash`, `incomplete_root_coverage` — computed, never decided | **Done** | `routing.blockers_for`, from the group projection, `file_state` (via the loader's `hash_current`) and `Phase2.coverage.coverage_summary`. A recorded blocker of a kind this build cannot compute (F08's `preview_available`) is honoured while open. Proved in §6 and §7 (a decided copy that changed and was only re-walked is stale and blocked; fingerprinting again clears it and the detector restores it). F08 passes. |
| 5 | Conflicts & Exceptions as a real route: `protected_vs_redundant`, `same_precedence_policy_tie`, `canonical_no_longer_keeper`, sorted per synthesis §3 | **Done** | `resolve.py` names the kinds the research's way (B8's `redundant_protected` → `protected_vs_redundant`; its two canonical kinds folded into `canonical_no_longer_keeper` with a reason) and adds `same_precedence_policy_tie` as an *advisory* conflict; `routing.ordered(CONFLICT)` sorts by potential reclaim then the oldest active decision. The Show box's "Conflicts & Exceptions" lists them in that order. §2, §6. |
| 6 | Ready for Plan as a route | **Done** | The same `ready_for_plan` computation, now a route and a Show filter; "Resolved" (every copy kept, nothing to plan) is its own terminal route beside it. |
| 7 | The routing precedence function, unit-tested | **Done** | `routing.route_target` / `route_all`: every condition that applies, one primary route by `ROUTE_ORDER`. `p3_regression.py` §6 asserts the primary for all 127 combinations of the seven routes, then peels conditions off one group end to end. |
| 8 | Skip vs Defer, genuinely distinct | **Done** | `s` = `store.skip` (an audit row; the group stays exactly where it was; the selection moves on); `d` = the Defer dialog (a trigger, a note; the group is parked). Proved in §5 and §7. |
| 9 | "Currently Clear" wording | **Done** | The Queue's empty state reads "Currently clear — no group awaits an ordinary decision (N ready for plan, …)"; no route says "complete". §5 asserts the words. |
| 10 | Fixture-driven acceptance: F08, F10 | **Done** | Both ship under `Resources\Phase3\fixture_lab\` (byte-identical to the research manifest) and pass through the general resolver and router — see §2. |
| 11 | Zero source-file mutation, re-confirmed on the new paths | **Done** | `p3_regression.py` §7 wraps defer / skip / restore / reconcile / routing in the write guard and fingerprints the corpus before and after (only the two files the test itself rewrote differ); §5 wraps the whole window session including the Defer dialog, Skip, Restore, the run-finished detector and Confirm decisions. |

Nice-to-haves (plan §2): the lenses **Filename Divergence**, **Extension
Divergence**, **Policy Tie** — built (they fell out of the Show box).
Deferred-queue hygiene views (Due Now, Older than 30/90 days, No Return
Trigger) — **not built**: the Conditions column already says "snoozed until
a date" / "deferred", and the Deferred route lists them; with no real
backlog yet there is nothing to triage (see §4). Manual priority tiers —
**not built**, per the research's "only when the user wants it". Queue
definition versioning — the minimum: every detector row carries
`routing_version` in its detail and the operation carries the build.

Non-goals (plan §3): no Visual / Metadata queues, no frozen queues, no
bulk-decision-exception kind — the conflict `kind` column is free text
validated in code, so it can be added later without a table rebuild.

## 2. The fixtures

| Fixture | Result | Oracle keys |
|---|---|---|
| F08 Deferred_vs_Blocked | **PASS** | routing {L1: deferred, L2: blocked}; return_kind {L1: time, L2: preview_available} — 2/2 |
| F10 Revalidation | **PASS** | decision_preserved "D1"; routing "needs_revalidation"; current_applicability "requires_revalidation" — 3/3 |
| F01, F02, F03, F09, F15 (B8's) | **PASS**, unchanged | 27/27 |

Plus, per fixture, `quick_check` and identical projection *and routes* from
shuffled rows over three seeds and a second run. `p3_fixture_check.py`:
46/46. Routing is evaluated at the fixture's own moment (its last operation
time), so F08's 2026-10-01 snooze stays a snooze whenever the check runs.

How the two fixtures were mapped: F10 binds evidence as one observation id
(`obs:OBS1`); the product binds pairs, so the loader gives the canonical
decision the pair `[A, OBS1]` (A is the value); A's current observation is
OBS2 → drift → Needs Revalidation, with D1 active and untouched. F08's
`blocked` row carries `return_kind = preview_available` — a Build 5 blocker
the router honours as a recorded blocker while it is open.

## 3. Judgment calls (flag these)

1. **The routing precedence table — adopted exactly as the synthesis
   recommended**, with one addition below "Ready for Plan": **Resolved**
   (every copy kept; nothing to plan) as its own terminal route, so a
   Keep-all group is neither "ready" nor "unresolved". Nothing above it
   moved.
2. **`incomplete_root_coverage` blocks only when the root's latest walk is
   unusable** — interrupted, failed, unavailable, never scanned. A
   *completed* walk with inaccessible folders (`completed_with_warnings`)
   is a coverage *warning* on the group, not a blocker. Reason: one denied
   folder in a 41,000-file root would otherwise park every group in the
   project and the queue would read "Currently clear" with everything
   blocked. If you want the stricter reading, it is one condition in
   `routing.blockers_for`.
3. **Stale means "an older observation's identity is all there is".** A
   copy that changed and was fingerprinted again — found unique by size,
   say — has a current verdict; it is not stale, it is simply no longer a
   duplicate (its decision drifts and is orphaned instead). A copy that
   changed and was only re-walked is stale, and blocked. This is Phase 2's
   staleness rule seen from the routing side; the Evidence strip's "stale
   hashes" count still uses Phase 2's own, slightly broader, definition.
4. **The detector records `blocked` rows only for targets a person has
   engaged with** (a decision on the group or one of its copies, or an open
   deferral). Every group's blocked *route* is computed regardless; the row
   explains why a decision is not moving, and the table does not gain one
   row per untouched group with unknown identity.
5. **`same_precedence_policy_tie` is advisory.** It goes to Conflicts &
   Exceptions and withholds nothing: a prefer/avoid overlap contradicts the
   *recommendation*, not anyone's decision. It is raised only while it
   matters — no explicit canonical, and the copy is not already marked
   redundant.
6. **The Conflicts order** is potential reclaim descending, then the
   earliest active decision — the synthesis's stand-in, since no Plan
   exists yet.
7. **Defer records an event, not a decision.** B8's `defer_review` decision
   kind stays registered (its rows still read as manual deferrals; Restore
   withdraws them) but the Defer action no longer writes one: S2 says
   Deferred is a review state, and the review event is now its home.
   Restore-by-hand writes a `restored` row naming the deferral.
8. **An open `needs_revalidation` flag never routes by itself.** The live
   comparison decides; the open flag rides on the condition as provenance
   ("flagged on …") and the detector restores it when the comparison
   clears. This is what makes revalidation clear "automatically based on
   the evidence binding comparison clearing" even if the detector has not
   run since.
9. **A member's deferral parks the copy, not the group** (unless every
   undecided copy is parked); a member's revalidation flag or blocker is
   the group's too. The window defers groups; the model allows locations
   (F08 needs them).
10. **Words on the screens** (provisional, as before): Status = Conflict /
    Needs revalidation / Blocked / Deferred / Ready for plan / Resolved / In
    progress / Unreviewed; Show = Queue, Conflicts & Exceptions, Needs
    Revalidation, Blocked by Evidence, Deferred / Snoozed, Ready for Plan,
    Resolved, All, Lens: …; buttons Defer… / Restore, Skip, Confirm
    decisions; the empty Queue's "Currently clear". Keys `d` and `s`.
11. **The Conflicts route ignores the column sort** while the default sort
    is in force, so its own order shows; clicking a heading sorts it like
    any other list.

## 4. What real-corpus testing says about the hygiene views

Not run on `TOMMY STUFF` this round (no decisions exist there yet). On the
suites' corpora the Deferred route holds a handful of groups and the
Conditions column says each one's trigger; a "Due Now" view would show
nothing the Queue does not already show (an elapsed snooze is back on the
Queue, live, before the detector even runs). Worth building when a real
backlog exists — the research's own condition — and cheap then: each is a
filter over `Route.deferral`.

## 5. Questions for the next round (not guessed at)

- Should a *completed-with-warnings* walk block (judgment call 2)? B9 warns.
- Should the detector also record `blocked` rows for untouched groups
  (judgment call 4), so the record shows every blocker the page ever showed?
- When Build 6's Plan exists, "conflicts blocking a current plan first" can
  replace the reclaim-then-oldest stand-in literally; the sort lives in
  `routing.ordered`.

## 6. Suites on B9

`p3_fixture_check` 46/46 · `p3_regression` 227/227 · `b6_regression` 73/73 ·
`p2_regression` all · `p2_dashboard_check` all · `self_check` ready (schema
10). `Test-Installation.ps1` verifies the refreshed `SOURCE_SHA256.csv`.
