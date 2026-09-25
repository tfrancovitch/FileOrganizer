# B13 — Phase 3, Build 7: Survey Mode (+ the group evidence guard)

**Date:** 2026-09-25
**From:** B12 (tag `phase3-b12`, commit `84e8e44`), on branch `phase3-b13`
**Handoff:** `Phase3_Handoff_Build7.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build7\`
(research synthesis grounded in P3.R3 §6/§14/§15/§34 and R10, build plan)
**Handback:** `PHASE3_BUILD7_HANDBACK.md`

Two separate pieces of work, as the handoff asked: a small patch (Part A)
and a build (Part B). No table, no migration (schema still 12), no new
decision kind, and no file is changed.

---

## Part A — the evidence guard, extended to group batches

B12 gave pair-batch previews an *evidence mark* so a re-analysis or a scan
between preview and commit refuses the commit (they add no `p3_operation`,
so B10's record guard alone cannot see them). Group batches never got it.

- `bulk.preview` (groups and copies) now captures `evidence.evidence_mark`,
  exactly as `bulk.preview_pairs` does. `store.commit_bulk` already checked
  the mark whenever a preview carries one: **one check, both target kinds**
  (the comment and the message now say so: "a scan, a re-hash or a
  re-analysis").
- `evidence.evidence_mark` gains the newest **run** that wrote a location's
  current state (`MAX` of `file_state.current_run_id`, `content_run_id`,
  `hash_run_id`). Found while building the proof: a rescan of *unchanged*
  files writes no new observation (`fo_state.touch_unchanged` refreshes the
  current row in place) and a file a scan finds gone is marked missing in
  place — B12's mark (newest observation + location count) moved for
  neither. Every scan and every fingerprinting pass stamps the rows it
  refreshes with its run id, so the mark now moves for both. This also
  strengthens the pair guard (same function); nothing else about group
  batches changed — `scope_groups`, `plan`, `classify` and `_simulate` are
  untouched.

## Part B — Survey Mode

### The clusters — `Scripts\Phase3\survey.py` (new, pure)

- `cluster_map(proj, routing, ev, show)`: connected components of the
  **open** pairs of a Show list (routes Visual Similarity Review, Conflicts,
  Needs Revalidation, Blocked, Deferred; never Resolved, Ready for Plan or a
  byte-identical pair), numbered in the list's order. Each `Cluster` is an
  *isolated pair* (2 images), a *survey* cluster (3–8, R3 §14) or
  *oversized* (> 8 — reported with its size and folder spread, never paged
  or ranked). `ClusterMap.summary()` is the line the window shows.
- `cluster_edges(proj, images)`: every current, non-exact candidate pair
  between two of the images in view — a group control's population.
  `non_edges(...)`: the image pairs shown together with no candidate pair
  (nothing is ever recorded across them; a byte-identical pair is a
  candidate, not a non-edge).
- `group_action` / `preview_group`: each group control as a Build 6 batch —
  an explicit selection of the cluster's pairs through
  `bulk.preview_pairs`, with the cluster noted on the batch's parameters
  (`"survey"`: control, Show, images, pairs, excluded, keepers, canonical,
  selected). Committed by `store.commit_bulk`, undone by `store.undo_batch`.
  A cluster is never a decision target.

| Control | Batch | Pairs covered |
|---|---|---|
| **Keep All** | `pairs_keep_both` (new) — Keep both only | every pair in view |
| **Mark Remaining Equivalent** | no Canonical: `pairs_equivalent_keep_both` (B12's); a Canonical: `pairs_equivalent_prefer` (new) — equivalent + the Canonical preferred on its pairs, unless the other image is a Keeper; keep both elsewhere | every pair in view; an answered pair is preserved |
| **Mark Selected as Related Variant** | `pairs_related_keep_both` (B12's) | pairs with **exactly one** image selected |
| **Defer Group** | `pairs_defer` (new) — one per-pair deferral each | every pair no relationship decision has settled |

### The batch — `Scripts\Phase3\registry.py`, `bulk.py`

- Three registry actions, marked `survey_only` so `bulk_actions_for()` does
  not offer them in either Bulk dialog (B12's pair dialog still offers its
  three). `BulkAction.kinds_text` words a choice of retention rows.
- `plan_pairs(..., params)`: per-pair retention for
  `pairs_equivalent_prefer`; one deferral per pair for `pairs_defer`
  (dispositions a pair offers only).
- `classify_pairs`: a deferral candidate is *satisfied* when the pair is
  already deferred, *not applicable* when a relationship is decided, else
  *decided*; Keep both on a pair marked Different is *preserved*; a
  Canonical row on a pair that keeps both is *preserved* (a batch never
  withdraws). Neither new rule can fire for B12's three actions (their first
  row gates the second).
- **The batch simulation's neighbourhood** (`bulk._neighbourhood`, used by
  `classify_pairs`): the whole-batch simulation now resolves only the pairs
  within two image-hops of the batch instead of every pair in the project.
  A pair's conflicts come from its own rows and the chain guard (its keeper
  given up by another pair), so nothing further away can change: same
  answers, checked against the whole model on 120 random graphs. On the
  real collection a Survey preview went from 1.3 s to 0.15 s; a folder
  batch whose neighbourhood is most of the project costs what it did.
- `preview_pairs(..., params)` carries the control's inputs into the plan
  and the batch record; `describe_query` words a Survey batch
  ("Survey Mode: Mark Remaining Equivalent over a cluster of 5 images …"),
  used by **Batches…** (`review.BatchesDialog`) and the preview.

### The window — `Scripts\Phase3\survey_view.py` (new), `imagegame.py`

**Survey Mode…** on the Similar Images page opens the pair in view's
cluster in the current Show list (or the list's first 3–8 cluster, saying
why: an isolated pair, or an oversized cluster with its size and folders).
Tiles, four to a row, each with Keeper, Canonical, Select, Compare and
Exclude; click a tile to make it Active; zoom and pan linked across every
tile (the page's `ImageView`, read-only decoding, cloud-only never opened).
The cluster's pairs are listed, with the count of image pairs that are not
candidates. Each group control previews (R4's buckets, the promise), then
**Record** writes exactly the preview; any change to the images in view,
the Keepers, the Canonical, the selection or the deferral voids it. **Undo
the last group action**, **Batches…**, **Previous / Next cluster**.
Compare opens the page's own Compare Mode (unchanged) on the Active image
and the tile. **Exclude records nothing.**

### Versions

`fo_db.APP_VERSION` = `Phase2.VERSION` = **B13**; `fo_db.MODULE_VERSION`
1.9.2; schema 12. `self_check.RUNTIME_MODULES` += `Phase3.survey`,
`Phase3.survey_view` (26).

## Tests

- **New:** `Scripts\p3_survey_check.py` — Part A's guard (§A) and Survey
  Mode (§B1 clusters, §B2 group controls incl. F06 as shipped and in §34's
  shape, §B2b the neighbourhood simulation equal to the whole model on 120
  random graphs, §B3 a real project with real images, §B4 the window) — 84
  checks.
- `p3_regression.py`: version checks read B13; the registry check lists
  Build 7's three actions and that they are Survey-only.
- Unchanged and green: `p3_visual_bulk_check`, `p3_visual_check`,
  `p3_fixture_check`, `b6_regression`, `p2_regression`,
  `p2_dashboard_check`, `self_check`.
