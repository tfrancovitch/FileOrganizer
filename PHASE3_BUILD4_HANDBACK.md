# Phase 3 handback — Build 4 (Bulk / Policy), built as B10

**Against:** `C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build4\02_BUILD4_HANDOFF_PLAN.md` §1–§4
**Date:** 2026-09-21
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B10\` — a git worktree of
branch `phase3-b10`, created from the B9 commit (`94dfc76` on `phase3-b9`).
Not committed, not tagged; both the owner's call after this review. When
accepted, `phase3` fast-forwards to it. `CHANGELOG-B10.md` says what
changed; this document says how it measures against the handoff.

**About the base.** The handoff assumed tag `phase3-b9` existed. As with
B8 last round, the reviewed B9 work was still uncommitted on `phase3-b9`;
so that Build 4 could branch from exactly the reviewed state I committed it
as `94dfc76` ("B9: Phase 3 Build 3 -- review routing") and left the tag to
you: `git tag -a phase3-b9 94dfc76`. Neither the B9 folder nor `phase3`
changed otherwise; `phase3-b8` is still yours to place on `e44613a`.

## 1. The necessities (plan §1), item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | `p3_bulk_batch` and `p3_bulk_member`, the fixture-lab shape | **Done** | `Scripts\Database\migrations\011_phase3_bulk_batches.sql` (schema 11): the lab's columns (`scope_kind`, `query_json`, `frozen`, `committed`; members as `target_kind` + `target_ref`) with the product's conventions (integer ids, `project_id`, FKs) plus `action` / `parameters_json` / `mode` / `preview_json` / `note` on the batch and a `disposition` + `detail` on each member. Both flags are 1: the product writes a batch at commit, in the same operation as its decisions (§3.9). Fresh projects and B9/B8/B7.x migrations proved in `p3_regression.py` §3. |
| 2 | Checked-selection UI: checkboxes, "N checked", a bulk entry point that appears only then | **Done** | `review.py`: a check column on the group list (click it, or Space on the focused row), Check all shown / Clear, "N checked (M shown)"; **Bulk action…** is packed only while the checked set is non-empty. The window session in §10 proves each. Checks are on groups only (§3.1). |
| 3 | Frozen query-result batch, reusing Build 3's filter | **Done** | The Show filter moved into `bulk.filter_groups`, one pure function the page draws from and a snapshot freezes; `bulk.snapshot_query` records the Show filter (and the folder, for a folder action); `query_targets` says what a query matches *now*. Membership is what the preview resolved, recorded at commit, never re-evaluated. Proved on models (§8), on a real project with a file added after the commit (§9 — F04 on real files), and through the fixture (§2). |
| 4 | Preview before commit: categorized counts with samples, through the existing resolver | **Done** | `bulk.preview` → `plan` + `classify`: every target's planned decisions are judged against what stands, then the group is resolved again by `resolve.resolve_group` with the batch's decisions in place (all of a group's at once). Buckets: decided / already satisfied / preserved / conflict / blocked / not applicable, with samples for the buckets that hide problems; the last line is "No source files will be changed." No second evaluation path exists. §8 exercises every bucket. |
| 5 | Preserve, don't overwrite | **Done** | An active decision in the same conflict domain saying otherwise, the group's explicit canonical, a Keep all in force (for a mark), a mark under a Keep all (for a keep-all), a mark on the copy a recommendation would make canonical — each is preserved and counted; the batch records nothing for that target and the member row says why. Where the person's own canonical stands, it gates the marks that would go with it (§3.5). §8, §9. |
| 6 | Provenance: `origin_kind = bulk_explicit_human`, `origin_ref = batch_id`; the origin registered | **Done** | `registry.ORIGIN_KINDS` (+ `origin_kind()` validator); `store._decision_row` writes the origin; an index on `(origin_kind, origin_ref)`; the history list and the journal name the batch. §9 checks the row. |
| 7 | Commit equals preview | **Done** | `store.commit_bulk` records exactly the candidates the preview classed as decided, each bound to the evidence the preview captured (a rescan in between shows up as drift afterwards, per synthesis §7). If the *decision record* moved between preview and commit, the commit refuses and asks for a new preview (§3.6). One operation, one transaction. §9. |
| 8 | Bulk undo via the withdrawal mechanism, batch rows untouched | **Done** | `store.undo_batch`: one operation of kind `bulk_undo` withdraws every decision of the batch that still stands and restores every deferral it recorded that is still open; a decision the person has since superseded or withdrawn by hand is left alone. Batch and member rows are never touched; a second undo is refused. §9, §10. |
| 9 | Policy-creation workflow: population now, and the statement about the future | **Done** | `bulk.policy_preview` resolves every group with the hypothetical policy in place and diffs it against the projection without it: file locations covered now (from `file_state`, group member or not), how many in duplicate groups, copies newly protected, new conflicts, recommendations that change, plan readiness — and the sentence "Future matching evidence WILL be evaluated against this policy…". The Add policy dialog previews first; **Create policy** is enabled only for the inputs previewed. No new backend mechanics (the F05 result, §2). §5, §9, §10. |
| 10 | The three choices in so many words, narrowest by default | **Done** | The bulk dialog's scope: **N checked groups** (default when any are checked) / **N current matches (Show: …)** — frozen at commit; a later match is not included / **Create a reusable policy instead (Protect folder: future matches too, no decision recorded)**, enabled only for the two folder actions and saying "no policy says this" otherwise. A bare "Apply to all" appears nowhere. §10 asserts the words. |
| 11 | F04 (must pass), F05 (run against the unmodified resolver first) | **Done** | See §2. F05 against the *unmodified* B9 code: the policy mechanics match the oracle with zero new code; only a location-level entry point was missing. |
| 12 | Zero source-file mutation on the new paths | **Done** | §9 wraps preview / commit / undo / `batches()` / `policy_preview` / the journal in the write guard and fingerprints the corpus before and after (only the two files the test added are new); §10 wraps the whole window session including the check column, the bulk dialog, the commit, Batches… and Undo, and the policy route. |

Nice-to-haves (plan §2): **Bulk Defer** — built (it fell out of the same
machinery: a deferral batch records events under the batch's operation and
its undo restores them). **Explicit Overwrite / Supersession mode** — not
built; the batch row carries a `mode` column that is always `preserve`, the
store refuses any other value, and the workflow that covers the case today
is *Undo the batch, then batch again* (§3.8). **Policy Impact View** — not
built as a standing page; the creation-time preview covers the need.

Non-goals (plan §3): no propagation tables, no separate exception / conflict
/ scope / rule tables, no bulk over visual pairs or metadata. Followed.

## 2. The fixtures

| Fixture | Result | Oracle keys |
|---|---|---|
| F04 Frozen_Bulk_Query | **PASS** | batch_members ["L1","L2"]; later_matching_target "L3"; later_target_in_batch false — 3/3 |
| F05 Dynamic_Policy_Future_Match | **PASS** | protected_locations ["L1","L2"]; explicit_human_decisions_for_policy_effect 0; policy_version "PV1" — 3/3 |
| F01, F02, F03, F08, F09, F10, F15 | **PASS**, unchanged | 32/32 |

Plus, per fixture, `quick_check` and identical projection, routes,
protected set and query matches from shuffled rows over three seeds and a
second run. `p3_fixture_check.py`: 56/56.

**F05 against the unmodified B9 resolver** (plan item 11), before any Build
4 code existed: `_policy_covers` says PV1 covers both `a.pdf` and
`future.pdf`, with zero decisions — all three oracle keys match. But the
question could only be asked location by location: B9's projection is
group-scoped, and F05 has *no duplicate groups at all* (its two files are
different content), so `resolve_all` had nothing to say. So: **the policy
mechanics needed no new backend work** — the one thing added is
`resolve.protected_locations(ev)`, step 1's protection factored out and
asked of every location the model holds, group member or not. That is a
refactor, not a mechanic; `resolve_group` calls the same
`location_protection`.

**F04 through the product**: the fixture's frozen query
(`{"extension": ".tmp", "folder": "C:\\Downloads"}`) is handed to the
product's own matcher, which names L1, L2 *and* L3 as current matches; the
batch's recorded membership is L1, L2. `later_matching_target` is what the
matcher finds that the batch does not have. On real files (§9) the same
shape is proved end to end: a snapshot over copies under `Downloads`, a
new duplicate copy added there afterwards, a rescan — the new copy is not a
member and has no decision, while a `protect_folder_subtree` policy made
before another file existed protects that file on arrival with no decision
(F05's contrast, on the same project).

## 3. Judgment calls (flag these)

1. **Checks are on the group list only.** "Group/location list" was read as
   the group list, whose rows are the units the routes and filters speak
   about; the copies of one group are few and the letter keys already act
   on them. A location-level checked selection is not built; the two
   folder actions are the location-level bulk.
2. **The letter keys and the checks never meet.** `k a c r d s` act on the
   selected (focused) row only, checked or not; **Space** toggles the
   focused row's check; the bulk path is the dialog and its preview, never
   a keystroke. Checking two groups and pressing `k` records one Keep, on
   the selected copy — proved in §10. The key legend on the page says so.
3. **Five bulk actions**, declared in the registry with the decision kinds
   they record and their policy twins: Keep all copies; Accept the
   recommendation (the policy-suggested canonical, and — unless switched
   off in the dialog — the other undecided copies marked redundant;
   protected and explicitly kept copies stay and the preview says how many);
   Keep copies under a folder (twin: Protect folder); Mark copies under a
   folder redundant (twin: Avoid folder — and the dialog says a policy
   cannot mark a copy redundant); Defer. "Accept the recommendation" is the
   one action that moves reclaim at scale; it is a person's explicit choice
   over a previewed set, recorded per target with the batch as origin.
4. **What counts as which bucket.** *Preserved* = an explicit decision
   (a person's or an earlier batch's) that says otherwise: the same
   conflict domain, the group's explicit canonical, a Keep all in force, a
   redundant mark under a Keep all, a mark on the copy the recommendation
   would make canonical. *Conflict* = a hard constraint the resolver would
   raise — a mark on a protected copy, marks that would leave no copy, or a
   group already in Conflicts & Exceptions — and it is **not recorded**: a
   batch never creates contradictory intent. *Blocked* = Build 3's
   computed blockers on the group; a blocked group receives no retention
   decision (it can still be deferred). The last-copy floor is judged over
   the whole batch, per group, so "mark every copy under C:\ redundant"
   is a conflict for each copy, not a coin toss about which one to spare.
5. **The canonical gates its marks.** In Accept the recommendation, when the
   person's own canonical stands (preserved), nothing at all is recorded
   for that group — the marks that go with the recommendation are not
   applied around it piecemeal. When the canonical is already the
   recommendation (satisfied), the marks still go in.
6. **Commit equals preview, with one guard.** The population and the
   bindings are the preview's; a rescan between preview and commit is the
   detector's business afterwards, as the synthesis says. But if the
   *decision record* moved between preview and commit (any new
   `p3_operation`, including the detector's), the commit refuses and asks
   for a new preview — what you saw is what you get, or nothing. In the
   one-window product the only way that happens is a run finishing while
   the dialog is up; previewing again costs a second.
7. **A batch is written at commit, both flags 1.** No frozen-but-uncommitted
   batch is recorded: the preview lives in the dialog, a cancelled preview
   leaves no row, and the append-only rule leaves no honest way to flip
   `committed` later. The columns keep the contract's shape for a build
   that saves a batch it has not applied.
8. **Overwrite / supersession mode deferred.** The store refuses any mode
   but `preserve`. The case it serves — "replace what a previous batch
   said" — is covered by undoing that batch, which is one click, and
   batching again. Worth building when a real case asks for it, likely
   beside the plan builder.
9. **The policy preview is a diff of two projections**, not a count of
   rows: every group resolved with the hypothetical policy and without,
   the same resolver. Its "covers N current file locations" counts every
   present file under the scope (from `file_state`), group member or not —
   the honest population — and says separately how many of those are in
   duplicate groups today.
10. **The Show filter moved.** The routes-and-lenses logic left the page for
    `bulk.filter_groups` so that "N current matches" is literally the
    page's list; `review.py` keeps its names (`FILTERS`, `LENSES`, …) as
    aliases. Behaviour on the page is unchanged.
11. **Words on the screens** (provisional, as before): the check column's
    ☐ / ☑, "N checked", Check all shown, Clear, Bulk action…, Batches…;
    the dialog's "One decision, many groups — after a preview", Apply to:
    N checked groups / N current matches (Show: …) / Create a reusable
    policy instead; Preview; Record N decisions / Record N deferrals /
    Nothing to record; the Batches list's "In force"; Undo batch; the Add
    policy dialog's Preview / Create policy. The summary's new line is
    "Bulk batches".

## 4. Scale — the resolver across a batch (plan §4)

Hand-built model, 4,000 groups × 3 copies with 500 existing decisions and
two policies (`p3_regression.py` §8 asserts each under 3 s): preview Keep
all **0.30 s**, Accept the recommendation (8,000 planned decisions)
**0.33 s**, Mark under a folder **0.25 s**.

A real project — 3,000 duplicate groups × 3 copies (9,000 small files in
two roots) through the real Pre-Scan (11 s) and Find My Duplicates (33 s):

| Step | Time |
|---|---|
| load evidence / resolve / route, 3,000 groups | 0.24 s / 0.19 s / 0.03 s |
| preview Keep all over All (3,000 members), with binding capture | 0.43 s (0.23 s without) |
| preview Accept the recommendation over the Queue (6,000 decisions) | 0.64 s |
| preview Mark copies under Downloads redundant (3,000) | 0.45 s |
| **commit** Accept the recommendation: 6,000 decisions, 3,000 members, one transaction | **3.4 s** |
| load / resolve / route again with 6,000 active decisions | 0.26 s / 0.24 s / **0.10 s** |
| `batches()` | 0.04 s |
| policy impact preview (Avoid Downloads, 3,000 groups) | 0.87 s |
| **undo** the batch: 6,000 withdrawals | **0.20 s** |
| the detector after all that | 0.49 s |
| the Decide page opening (detector + reload + 3,000 rows drawn) | 1.2 s |
| Check all shown (3,000) / Show: All refresh | 0.01 s / 0.10 s |

The first run of this measurement found two O(N²) paths and B10 fixes
both, since a bulk build makes thousands of decisions routine:

- **B9's router** scanned every active decision once per target
  (`route_target`); after the batch, routing 3,000 groups with 6,000
  decisions took **9.9 s**. `route_all` now indexes the active decisions by
  target once per pass: **0.10 s**. Same routes (fixtures and §6/§7
  unchanged).
- **The store's `_chain_head` and `_withdraw_row`** loaded the whole decision
  table per call, so committing 6,000 decisions took minutes and undoing
  them longer. Both are targeted queries now (the target's own rows; the
  one decision's withdrawal and supersession) — 3.4 s and 0.2 s above.
  The single-click path benefits the same way.

Nothing in the preview needed changing for scale: the per-target resolver
call is cheap because the simulation overlays a group's few slots on one
shared index instead of copying it.

## 5. Questions for the next round (not guessed at)

- Should a *blocked* group be allowed a bulk Keep (a keep is harmless)? B10
  says no retention decision of any kind on a blocked group, for one rule.
- Should the record guard at commit (judgment call 6) ignore the detector's
  own system operations, so a run finishing during a preview does not ask
  for a second preview? B10 treats any new operation as "the record moved".
- Is a location-level checked selection wanted (judgment call 1), or do the
  folder actions cover it?

## 6. Suites on B10

`p3_fixture_check` 56/56 · `p3_regression` 337/337 · `b6_regression` 73/73 ·
`p2_regression` all · `p2_dashboard_check` all · `self_check` ready (schema
11). `Test-Installation.ps1` verifies the refreshed `SOURCE_SHA256.csv`.
