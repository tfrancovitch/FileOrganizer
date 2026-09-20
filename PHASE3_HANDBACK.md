# Phase 3 handback — Builds 1–2, built as B8

**Against:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\02_PHASE_3_HANDOFF_PLAN.md` §8
**Date:** 2026-09-20
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B8\` — a git worktree of
branch `phase3`, created from tag `phase2-b7.2`, not yet committed or tagged
(both are the owner's call after this review). `CHANGELOG-B8.md` says what
changed; this document says how it measures against the handoff.

## 1. The necessities (plan §1), item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | New schema for Operation, Decision, Policy Rule + Version, Evidence Binding | **Done** | `Scripts\Database\migrations\009_phase3_decisions.sql` (schema 9): `p3_operation`, `p3_decision`, `p3_decision_withdrawal`, `p3_policy`, `p3_policy_version`. Evidence binding is a JSON column of references (file, observation, content, hash run, the group's members with observations) — no new table, as the plan allowed. Fresh projects and migrated schema-8 projects both proved in `p3_regression.py` §3. |
| 2 | A real, deterministic keeper/canonical/protection resolution function, unit-testable, implementing the 8-step order | **Done** | `Scripts\Phase3\resolve.py` — `resolve_group()` / `resolve_all()`, pure functions of `model.Evidence`. Order asserted against the shipped catalog; class-by-class behaviour, determinism under shuffled input, and speed (4,000 groups / 14,000 locations in 0.2 s) in `p3_regression.py` §2. |
| 3 | The protection-override gate | **Done** | In the resolution (step 1), not a dialog: a redundant mark on a protected location has no effect and is a `redundant_protected` conflict until an `override_protection` decision naming the exact policy version exists for that location. The store refuses an override without the confirmation flag, a rationale, and a covering protection policy; the window's dialog is the only path. Proved in `p3_regression.py` §2, §3, §5 and by F01/F15. |
| 4 | Supersession/withdrawal, not deletion; same for policy versions | **Done** | A new decision in the same conflict domain on the same target supersedes the old (`supersedes_decision_id`); Undo is a `p3_decision_withdrawal` row; a policy change or retirement is a new `p3_policy_version`. The product has no UPDATE or DELETE against any `p3_*` table. Proved in §3 (row counts only grow; ids contiguous; the withdrawn/superseded/active states of a three-row chain). |
| 5 | Rebuildable projections (Current Keeper Set, Canonical, Protected Set, Plan-Eligible Reclaim) as a real function, proved against F15 | **Done** | Nothing derived is stored. `p3_fixture_check.py` F15: 9/9 oracle keys. `p3_regression.py` §3: the projection computed from a byte copy of the project database equals the live one. No cache exists, so no code path can prefer one. |
| 6 | Minimum Exact-Duplicate Review UI in the existing one-window Dashboard, with Keep / Keep All / Set Canonical / Mark Redundant Candidate / Defer / Override Protection (own confirmation) | **Done** | `Scripts\Phase3\review.py`, reached from the side panel (**Decide**), the summary (**Review duplicates**), and a file's Details pane (**Review this duplicate group**). Per group: locations, physical copies, hard-link aliases, size, roots, potential and plan-eligible reclaim, protection, policy recommendation labelled `(policy)`, conflicts. Plus Undo, Policies…, Export journal…, sortable headings, a status filter, keys k/a/c/r/d. Exercised headless in `p3_regression.py` §5. |
| 7 | Zero source-file mutation, confirmed by a test | **Done** | `p3_regression.py` §4 and §5: a SHA-256 / size / mtime fingerprint of every corpus file before and after everything Phase 3 does (store, resolver, the whole window session), the set of paths unchanged, and a guard wrapping every Python-level write primitive (`open` in write modes, `os.open` with write flags, `os.remove/unlink/rename/replace/utime/link/symlink/rmdir/mkdir/makedirs/chmod/truncate`) that records any write outside the project folder (and the application's `Logs\`): none. |
| 8 | Decision-kind registry: targets, value schema, evidence requirement, supersede-ability, reclaim effect, required UI confirmation | **Done** | `Scripts\Phase3\registry.py` — six kinds, each with all of those plus resolution class and conflict domain; five policy kinds with scope/effect validation. The store consults it before every write. Proved in `p3_regression.py` §1. |
| 9 | Provenance on Operation now: `actor_id`, `agent_kind`, `session_id`, `command_id` | **Done** | All four, plus `actor_kind`, `agent_version` (the build) and `occurred_utc`. `actor_id` is the Windows user name; `agent_kind` is `dashboard`; a session id per window; a command id per click. Proved in §3. |
| 10 | Fixture-driven acceptance against F01, F02, F03, F09, F15 | **Done** | `p3_fixture_check.py` runs the *general* resolver over each fixture's raw rows and diffs every `expected_projection` key. See §2 below. |

Nice-to-haves (plan §2): a `project_policy` slot in the order — **done**
(empty tier, step 6); keyboard shortcuts — **done** (k/a/c/r/d on the member
list); a materialized projection — **not built**, deliberately (the recompute
is 0.2 s for 4,000 groups, and no cache means no cache to distrust);
saved-view lenses — **not built** beyond the status filter (Build 3).

Non-goals (plan §3): no review-queue/routing system (`defer_review` is a
decision kind and a filter value, nothing more); no bulk or checked-selection
action — every action is one group or one location.

## 2. The five fixtures

| Fixture | Result | Oracle keys |
|---|---|---|
| F01 Keeper_Protected_Hardlink | **PASS** | keeper_set, canonical, protected, physical_copies, hardlink_aliases, redundant_candidates, plan_eligible_reclaimable_bytes — 7/7 |
| F02 Multiple_Intentional_Keepers | **PASS** | keeper_set, canonical, redundant_candidates, plan_eligible_reclaimable_bytes, ready_for_plan — 5/5 |
| F03 Folder_Priority_Exception | **PASS** | policy_suggestion, effective_canonical, explicit_exception_groups, conflicts — 4/4 |
| F09 Supersession_Undo_Reapply | **PASS** | history, current_canonical, history_rows_preserved — 3/3 |
| F15 Projection_Rebuild | **PASS** | keeper_set, canonical, protected, redundant, conflicts, review_state, ready_for_plan, plan_item_target — 8/8 |

Plus, per fixture, database `quick_check` and identical output from shuffled
rows over three seeds and a second run. `p3_fixture_check.py`: 37/37.

How the fixtures were mapped, since their schema is the research's: the
lab's `withdrawn` flag becomes a withdrawal; its `enabled` flag becomes the
version's active/retired status; its `evidence_location.protected` flag
becomes a `protect_source_root` policy for that root (every location of the
root carries the flag in all five fixtures; the loader would fall back to an
exact-path folder protection otherwise). F15's `plan_item_target` and
`review_state` come from the projection's plan-eligible locations and review
state — B8 has no plan or review-event tables, and the oracle was met without
them.

## 3. Judgment calls the research left open (all provisional)

1. **Working folder, base tag, version.** The plan named tag `phase2-b7` and a
   folder `TheFileOrganizer-Phase2-B7.2`; neither matched what exists (the
   post-P2.13 state is `phase2-b7.2`; installs are named
   `FileOrganizer-Phase<N>-B<build>`). Built from `phase2-b7.2` as **B8** in
   `FileOrganizer-Phase3-B8\`, branch `phase3`, as a git worktree so
   `phase2` stays untouched. Not committed, not tagged.
2. **Words on the screens** (open question UI-1 and the vocabulary note):
   side panel **Decide**; page title **Duplicate decisions**; actions **Keep**,
   **Keep all**, **Set canonical**, **Mark redundant**, **Defer** / **Undefer**,
   **Override protection…**, **Undo selected**; the file-detail jump-in
   **Review this duplicate group**; statuses Protected / Keeper / Redundant
   candidate / Undecided; group states Unreviewed / In progress / Deferred /
   Resolved / Conflict; the summary lines **Duplicate decisions** and
   **Policies**. The database stores kind keys, so a rename costs nothing.
3. **Withdrawal semantics.** Undo withdraws the decision and does *not* revive
   the one it had superseded (F09's own shape: the re-apply is a new row). The
   window says so in the Undo confirmation. A re-apply supersedes the withdrawn
   row so the chain stays complete.
4. **Keep All versus an existing redundant mark.** The order says an explicit
   location decision outranks the group decision, so Keep All after a mark
   would leave the mark standing; instead the window asks and withdraws the
   group's marks in the same operation, and the resolver still applies the
   strict order for data that arrives any other way (and notes it on the
   member). Set canonical on a marked copy withdraws that mark the same way.
5. **Mark redundant** is refused (nothing recorded) in three cases: a
   protected copy (use Override protection…), the canonical (set another
   first), and the last remaining copy (the product never removes the last
   one). The resolver would flag each as a conflict anyway; refusing keeps the
   record clean.
6. **The `actor_id`** is the Windows account name. Phase 1/2 keep user
   identifiers out of their records on purpose; a decision is attributable
   intent and the actor is part of it. If that is wrong for this product, it
   is one line in `store.py`.
7. **Physical identity unknown** (a member without volume/file index) makes
   the group's reclaim unknown (`None`, shown as Unknown), never a guess —
   the same rule Phase 2 applies.
8. **Evidence binding** carries ids only: file, observation, content, hash
   run, and the group's `[file_path_id, observation_id]` pairs. No path, no
   hash value. The group's membership at decision time is what Build 3's
   "needs revalidation" can compare against.
9. **A group whose every copy is protected and that has no decision is
   Unreviewed**, since protection is a constraint, not a review. Keep all
   marks it Resolved. Worth a "Reviewed" word of its own if real use finds it
   annoying.
10. **Policy scope matching** is by path key (lower-cased, backslashes, the
    folder itself or anything under it), stricter than the research
    validator's plain prefix (which would match `C:\Documents2` for
    `C:\Documents`). `prefer_folder_subtree … over …` is relative: it speaks
    only in a group that has a member under the "over" folder, exactly F03's
    semantics; without an "over" it always speaks. A copy both preferred and
    avoided is neutral and says so.
11. **Vocabulary CHECK constraints** were left off the `p3_*` kind columns so
    that Builds 3–7 can add kinds and actor/agent kinds without rebuilding
    tables (SQLite cannot alter a CHECK); the registry validates instead.
    `p3_policy_version.status` keeps its CHECK.

## 4. Questions for the next round (not guessed at)

- Should `ready_for_plan` require the surviving copies to be affirmatively
  decided (Keep / Keep all / Canonical), or is "at least one copy remains" —
  the F01 oracle's reading — the intended bar? B8 follows F01.
- Is the Windows account name the right `actor_id`, or does the product want
  a named reviewer per project?
- Build 3 will want a place for the "orphaned decisions" the summary already
  counts (decisions whose location or group is no longer current evidence) —
  that is the seed of Needs Revalidation.

## 5. Suites on B8

`p3_fixture_check` 37/37 · `p3_regression` 141/141 · `b6_regression` 73/73 ·
`p2_regression` all · `p2_dashboard_check` all · `self_check` ready (schema 9).
`Test-Installation.ps1` verifies the refreshed `SOURCE_SHA256.csv`.
