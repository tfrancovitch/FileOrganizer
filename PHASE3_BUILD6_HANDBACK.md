# Phase 3 handback — Build 6 (Bulk Visual Review), built as B12

**Against:** `phase_3_handoff_build6\02_BUILD6_HANDOFF_PLAN.md` §1–§5 and
`01_BUILD6_RESEARCH_SYNTHESIS.md` (drawing on P3.R4, *Bulk Decisions /
Propagation / Exceptions*, and P3.R10 §13, *Minimum Bulk System*)
**Date:** 2026-09-24
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B12\` — a git worktree of
branch `phase3-b12`, created from tag `phase3-b11`. Not committed, not
tagged; both the owner's call after this review. `CHANGELOG-B12.md` says
what changed; this document says how it measures against the handoff.

**About the base.** As the handoff asked, B11 is committed as `15b5966`
("Phase 3 Build 5: the Image Game (B11)") on `phase3-b11`, with the
annotated tag `phase3-b11` ("Phase 3 Build 5: the Image Game"), matching the
b8–b10 style. That was done at your instruction ("commit and give me the
handback"). Before committing, I confirmed that `pairs.py`, which the
session had flagged as changed on disk, still hashed exactly as B11's
manifest recorded. Nothing else in the repository moved.

## 0. The first question (plan necessity 1): was the bulk code group-shaped?

**Half.** My B11 handback called B10's preview and commit "group-shaped".
Reading the code before building, this is what held and what didn't:

| Part | Status for pairs |
|---|---|
| `p3_bulk_batch` / `p3_bulk_member` schema | **already generic**: a target kind per member. No migration. |
| `store.commit_bulk` | **already generic**: validates each row through B11's `_validate_decision` (which knows pairs); record guard; one operation |
| `store.undo_batch` | **already generic**: withdraws by batch origin in one operation |
| `BulkPreview` (counts, lines, as_dict) | nearly: only its nouns ("groups" / "copies") needed a third ("pairs") |
| `_classify_one`'s satisfied / preserved-by-domain check | **already generic**: keyed by (target kind, ref), so it works as is for pairs |
| Population (`scope_groups`, `query_targets`), planning (`plan`), the group-level cross-checks and the simulation (`_simulate` through `resolve_group`) | **group-shaped**: pair versions written (`pair_query_targets`, `plan_pairs`, `classify_pairs` simulating through `pairs.resolve_pairs`) |
| The Decide page's `BulkDialog` | page-specific: it offered every registered action, so it now offers only its own; the pairs got their own dialog |

So less changed than my B11 wording implied. The schema and the whole commit
and undo path were reused unchanged. What was new was the population, the
plan, the classification and a dialog.

## 1. The necessities, item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Extend the frozen query-result batch to `visual_candidate_pair`; report group-shaped or not | **Done** | §0. Three registry actions over pairs; `bulk.preview_pairs`; `store.commit_bulk` unchanged but for the evidence guard (item 9). |
| 2 | Population: folder scope (both images) and the lenses; exact copies not offered | **Done** | `pair_snapshot_query(show, folder, subfolders)` → `pair_query_targets`. The Show list is the page's own function, moved to `bulk.py`, so the snapshot freezes exactly the list shown (Strong near-copies, Across folders, Cross-root, any route, All). Folder: both images beneath it (default) or directly in it. A pair with one image outside, or in a sibling folder sharing a name prefix, is not in it. *Exact copies* is refused as a population, and a byte-identical pair inside a folder is counted *not applicable*. Proved in `p3_visual_bulk_check` §2 and §4. |
| 3 | A bulk relationship action writing the fast action's two-row shape; no new kinds | **Done** | *Related variant, keep both* = V's rows; *Equivalent copy, keep both* = E's; *Different* = D's. Recorded through `commit_bulk` → `_decision_row`, each row superseding in its own domain. §4 compares a batch's rows and binding with what the store writes for a fast action: identical. |
| 4 | Preview in R4's shape, blocked/conflict from B11's routing | **Done** | Population / would receive a decision / already satisfy / preserved (exceptions) / would conflict / blocked / not applicable, with samples and "No source files will be changed." Conflict and blocked come from `routing.pairs[...]`, the route the page shows, not from a second calculation. |
| 5 | Exceptions preserved, never overwritten | **Done** | Preserved: another relationship; a preferred copy (equivalent + canonical) that Keep both would undo; a retention row that Different would contradict; a deferral made by hand. The first row gates the second, so a pair never gets half of an action. §3 and §4, where the hand-marked Different pair is still Different after the batch. |
| 6 | Conflicts excluded, never bypassed (chain guard unchanged) | **Done** | A pair already in Conflicts → *conflict*. Then the whole batch is simulated through `pairs.resolve_pairs` with every planned row in place. A conflict the batch itself would create, chain guard included, keeps the pair it falls on out, with both its rows withheld. §3 proves both the existing-conflict case and a chain created inside one batch (the second half withheld, the first recorded). |
| 7 | Blocked / Needs Revalidation excluded | **Done** | *blocked* with the reason (cloud-only, changed since compared, "needs revalidation: …"). F08's distinction is kept: a deferral is a person's choice, so it counts as preserved; a blocker is the evidence's, so it counts as blocked. |
| 8 | Frozen membership, F04's guarantee for pairs | **Done** | §4: after the commit a new picture is added to the folder, rescanned and analyzed. The frozen query matches its pairs now, but the batch's members don't include them, and they wait in the Visual Similarity Review, unreviewed, with no decision. |
| 9 | Commit guard reused: any new operation voids the preview | **Done, and extended** | B10's record mark is unchanged: a skip, a decision or another batch voids the preview (§4). But a re-analysis or a scan adds no `p3_operation`, so that rule alone would not have voided a pair preview. The preview now also carries an evidence mark (newest image comparison and newest observation), and the commit refuses when it has moved. §4 proves all three: an operation, a re-analysis, a scan. |
| 10 | Batch-scoped undo via the existing withdrawal | **Done** | B10's `undo_batch`: one `bulk_undo` operation withdraws every decision the batch recorded that still stands; a pair changed by hand since is left alone (§4). It is the same mechanism as `withdraw_many` (a set of withdrawals in one operation), predating it, and it also restores a deferral batch's events. Judgment call 7. |
| 11 | Real-scale acceptance | **Done** | §2. |

**Nice-to-haves (plan §2).**
- **Explicit checked batch: built**, because the mechanism existed. B10 has
  a checked selection on the group list, and `SCOPE_EXPLICIT_SELECTION` flows
  through preview and commit unchanged. On the pair list I added the same UI
  (check column, Space, Check all shown, Clear, "N checked"). It's a
  per-page copy of a small pattern, not new infrastructure; the checked
  scope itself needed no new plumbing.
- **Export of the preview: built.** **Save the preview list…** writes every
  member with its bucket and reason as CSV into the project's `Exports\`.

**Non-goals (plan §3).** No dynamic policy of any kind: the three actions have
no policy twin and nothing re-applies itself. No Survey Mode. No bypass of the
chain guard, the blockers or preservation. No group- or crowd-level decision
object: one relationship row (and at most one retention row) per pair. Compare
Mode is unchanged. All followed.

## 2. Real-scale acceptance (plan §4)

On a **copy** of "Phase 2 Human Test 2 - Real Corpus", migrated to schema 12
and compared (51,924 pairs, as B11 measured). Inside the D&D folder I first
recorded, as a person would: one pair marked **Different**; a chain, A~B
(equivalent, keep A) then B~C (equivalent, keep B), so B~C trips the chain
guard. The copy was deleted afterwards; the original was never opened.

| Batch: *Related variant, keep both*, Show: All pairs, subfolders on | Population | Decided | Already | Preserved | Conflict | Blocked | Not applicable | Preview | Commit |
|---|---|---|---|---|---|---|---|---|---|
| `Tommys Pictures\2023 Dnd Campaign` | 31,393 | 31,250 | 0 | **2** | **1** | 0 | 140 | 1.7 s | 3.3 s (62,500 decisions) |
| `midjourney\MJ Gridtest` | 5,332 | 5,303 | 0 | 0 | 0 | 0 | 29 | 1.0 s | 0.5 s (10,606) |

- **The Different pair** → preserved, and still Different afterwards. **The
  chain pair** (B~C) → conflict, not recorded. **A~B** → preserved: it was
  already *equivalent*, and a *Related variant* batch never overwrites that.
- **What it did to the queue:** the Visual Similarity Review went from
  **50,290 to 13,734 pairs (−73%)** after both batches; with the D&D batch
  alone, 19,037.
- **What remains:**

  | Where | Pairs |
  |---|---|
  | Across folders | ~5,000 |
  | `midjourney\Sage Characters` | 1,926 |
  | `midjourney\SORTING` | 841 |
  | a `midjourney_session_…` folder | 772 |
  | `VN Sprites\Nameless` | 303 |

  The same bulk action handles each of these folders. The cross-folder pairs
  are where true copies are likeliest (the *Across folders* lens), and that's
  where Survey Mode (Build 7) earns its place.
- **In the window** (a separate run on a fresh copy):

  | Step | Time |
  |---|---|
  | Page open | 2.8 s |
  | Dialog preview over the D&D folder | 2.0 s |
  | Record and redraw (62,506 decisions) | 5.6 s |
  | Batches… listing 31,393 members | 2.4 s |
  | Undo and redraw | 5.4 s |
  | Moving between pairs | under 0.1 s |

- **Batch list and undo:** `batches()` takes 0.47 s; undoing the MJ batch
  (10,606 withdrawals) takes 0.9 s.

**A defect the acceptance run found, fixed.** The first acceptance run did not
finish in ten minutes. The store's chain-head query (asked once per decision
recorded) and its history query named the target without the `project_id`
that leads the index `ix_p3_decision_target`. So SQLite scanned the whole
decision table every time: `SCAN … USING COVERING INDEX` instead of `SEARCH`.
At B10's scale (6,000 decisions) that cost 3.4 s and went unnoticed; at
62,500 it was quadratic. Adding `project_id = 1` makes both queries index
searches (verified with `EXPLAIN QUERY PLAN`), and the commit took the 3.3 s
above. It is a two-line change in `store.py`, and it speeds up every Phase 3
write that supersedes.

## 3. Judgment calls (flag these)

1. **Folder scope = both images beneath the folder, subfolders included by
   default**, with a switch for "both directly in it". The handoff asked this
   not be decided silently, so here is what it measures on your collection:
   - **The D&D folder:** 31,393 pairs with subfolders. Of those, 30,415 are
     inside one subfolder, and **978 pair images from sibling subfolders**.
     The parent
     folder directly holds almost nothing, so without subfolders the scope
     finds **1 pair**.
   - **MJ Gridtest:** 5,332 pairs with subfolders, 5,153 without.

   A pair with one image outside the folder is never in scope, so pairs
   across the folder's edge are never swept in. The 978 sibling pairs are the
   call to look at: *Related variant, keep both* is a safe answer for
   variations of one campaign, but they are a different kind of pair from a
   same-subfolder crowd. Batching one subfolder at a time (subfolders off)
   leaves them in the queue.
2. **Bucket precedence.** Already satisfied, then conflict (the pair's
   route), then blocked or needs revalidation (the pair's route), then
   preserved (explicit intent), then decided, then the simulation. A pair
   that already says exactly this is a no-op whatever else is true of it. A
   pair in Conflicts or Blocked is reported there even if it also carries an
   explicit decision, because it needs a person or better evidence first
   (the handoff's items 6 and 7).
3. **A deferral made by hand counts as preserved.** The handoff says nothing
   of deferred pairs, and B10 did not exclude deferred groups either. But a
   deferral is a person's explicit "not yet", and a folder batch deciding a
   parked pair would overwrite it in spirit.
4. **The same-relationship pair.** If a pair is already *related* but has no
   Keep both, the batch records only Keep both (one row, not two). If it is
   *equivalent* with a preferred copy, *Equivalent, keep both* preserves it,
   because Keep both would undo the preference.
5. **Different over a pair with retention → preserved.** D by hand withdraws
   the retention row in the same operation, but a batch never withdraws.
6. **The evidence guard.** Pair previews carry an evidence mark (newest image
   comparison, newest observation, location count), and the commit refuses
   when it has moved. Group previews still carry only B10's record mark,
   because I didn't change B10's behaviour without asking. Worth deciding
   whether groups should get it too: a rescan between a group preview and its
   commit is today the detector's business afterwards (B10's judgment
   call 6).
7. **Undo is `undo_batch`, not `withdraw_many` by name.** They are the same
   mechanism (a set of withdrawals, one operation); `undo_batch` came first,
   also restores deferral batches, and refuses when nothing stands. Routing
   batch undo through `withdraw_many` would have been a rename, not a reuse.
8. **Checks and keys never meet** (B10's rule, carried over). Space toggles
   the focused row's check, and the letters act on the pair in view. §5 of
   the suite proves D records one decision on the pair in view while another
   pair is checked.
9. **After a batch, the page's Undo stack is cleared.** U after a batch falls
   back to "the latest action on this pair", which withdraws that pair's
   batch rows only. The whole batch is undone from **Batches…**.
10. **A new suite, not a longer one.** `p3_visual_bulk_check.py` (79),
    reusing `p3_visual_check`'s helpers, following the precedent B11 set.

## 4. Questions for the next round (not guessed at)

1. **The 978 sibling-subfolder pairs** (judgment call 1): with subfolders on,
   a parent-folder batch includes them. Is that the rule you want, or should
   the default be one subfolder at a time?
2. **Should group batches get the evidence guard too** (judgment call 6)?
3. **After these two folders, 13,734 pairs remain:** mostly other
   generated-art folders (the same bulk action serves them) and about 5,000
   pairs across folders. Is that the population Survey Mode (Build 7) should
   be scoped against?

## 5. Suites on B12

`p3_visual_bulk_check` 79/79 · `p3_visual_check` 138/138 · `p3_fixture_check`
65/65 · `p3_regression` 341/341 · `b6_regression` 73/73 · `p2_regression` all ·
`p2_dashboard_check` all · `self_check` ready (schema 12).
`Test-Installation.ps1` verifies the refreshed `SOURCE_SHA256.csv`.
