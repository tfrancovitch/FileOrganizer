# Phase 3 handback — Build 7 (Survey Mode) + the group evidence guard, built as B13

**Against:** `phase_3_handoff_build7\02_BUILD7_HANDOFF_PLAN.md` (Part A, Part B)
and `01_BUILD7_RESEARCH_SYNTHESIS.md` (P3.R3 §6/§14/§15/§34, R10)
**Date:** 2026-09-25
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B13\` — a git worktree of
branch `phase3-b13`, created from tag `phase3-b12`. Not committed, not
tagged; both are the owner's call after this review. `CHANGELOG-B13.md` says
what changed; this document says how it measures against the handoff.

**About the base.** The handoff asked for B12 to be tagged before starting.
B12 was still uncommitted, so I committed it as `84e8e44` ("Phase 3 Build 6:
Bulk Visual Review (B12)") on `phase3-b12`, with the annotated tag
`phase3-b12` ("Phase 3 Build 6: Bulk Visual Review"), matching b8–b11. That
commit is exactly the tree handed back for B12; nothing was changed first.
The Build 7 handoff package is copied to
`FileOrganizer-Phase3\phase_3_handoff_build7\`.

---

## Part A — the evidence guard, extended to group batches

**The patch, on its own:** `bulk.preview` (groups and copies) now captures
`evidence.evidence_mark(conn)` exactly as `bulk.preview_pairs` has since B12.
`store.commit_bulk` needed no new check. It already refused any preview whose
mark had moved, so it is now **one check for both target kinds**, with the
same message: "the evidence changed since this preview was made (a scan, a
re-hash or a re-analysis: …); preview again". `scope_groups`, `plan`,
`classify` and `_simulate` are unchanged (necessity 4).

**What the mark is (necessity 1), and a finding.** I mirrored B12's mark
rather than inventing a group one. Proving it the way the plan asks exposed
a gap that B12's mark has too. **A rescan of unchanged files does not move
it.** The scan refreshes each location's current row in place and keeps its
observation (`fo_state.touch_unchanged`); a file the scan finds gone is
marked missing in place too. So neither the newest observation nor the
location count moves. The mark now also records the **newest run that wrote
a location's current state** (`file_state.current_run_id`, `content_run_id`,
`hash_run_id`). Every scan and every fingerprinting pass stamps the rows it
refreshes with its run id. The function is shared, so this also closes the
same gap for pair batches.

**Proof (necessity 3)**, `p3_survey_check.py` §A, on a real project with two
exact-duplicate groups:

| Between preview and commit | B12 mark | Build 7 mark | Commit |
|---|---|---|---|
| a skip (an operation) | — | — | refused (record guard, unchanged) |
| **a rescan, no file changed** | **did not move** | moved | **refused** (new), nothing written |
| **Find My Duplicates again (a re-hash)** | — | moved | **refused** (new) |
| **a scan after a group member's content changed** | — | moved | **refused** (new) |
| nothing | — | — | previewed again, it commits |

The same rescan also refuses a pair preview, with the same words: one path
for both kinds (necessity 2).

---

## Part B — Survey Mode

### 1. Measure first (necessity 1)

Measured on a **copy** of "Phase 2 Human Test 2 - Real Corpus" (schema 8 → 12,
51,924 pairs), after replaying B12's two folder batches exactly as its
handback did. The queue came out at **13,734**, matching B12's figure.
The copy was deleted afterwards; the original was never opened for writing.

**Population:** the *Across folders* lens's pairs still in the Visual
Similarity Review: **4,911** (B12 said ~5,000). A component is a connected
set of images joined by those pairs.

| Component | Components | Images | Pairs | Share of the 4,911 |
|---|---|---|---|---|
| isolated pair (2 images) | 264 | 528 | 264 | 5% |
| **3–8 images (Survey Mode)** | **243** | 1,031 | **1,008** | **21%** |
| more than 8 | 62 | 1,356 | 3,639 | 74% |

Size distribution: 3:108, 4:57, 5:23, 6:30, 7:16, 8:9 · 9:9, 10:5, 11:7,
12:11, 13:3, 14–20:10, 24–30:6, 42–51:8, 68, 71, **142**.

**So Survey Mode reaches about a fifth of the cross-folder remainder.**
Three quarters of it sits in 62 components larger than eight. The largest
(necessity 7 / acceptance §4 — reported, not worked around):

| Images | Pairs | Folders | What it is |
|---|---|---|---|
| 142 | 679 | 62 | Ren'Py SDK theme and GUI assets (`renpy-8.5.2-sdk\renpy\common\_theme_awt`, `…\game\gui\scrollbar`, `…\slider`) |
| 71 | 139 | 3 | two `midjourney_session_2024-1-3_[150-200]` / `[200-250]` folders under `Sage Characters` |
| 68 | 304 | 18 | Ren'Py `game\gui\button` sets copied across `TEST VISUAL NOVEL\Beagle1`, `Beagle2`, `TEST123`, … |
| 51 | 151 | 6 | more `Sage Characters` sessions + `SORTING` |
| 45 | 93 | 2 | the VS Code `resources\app\resources\win32` icons, in two copies of VS Code |

These are not accidental duplicates in the sense Survey Mode was scoped
for. They are game-engine GUI assets and adjacent AI-image sessions. Several
sit in a handful of sibling folders, where a Build 6 folder batch on the
common parent (subfolders on) is the tool that fits.

### 2. The necessities, item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Measure first | **Done** | §1 above; `survey.cluster_map` reproduces the same 243 / 264 / 62 in 0.10 s. |
| 2 | Cluster display: tiles, one Active image, Keeper/Canonical, linked zoom, removable images | **Done** | `survey_view.SurveyWindow`: 4 tiles to a row (3–8), click = Active (blue frame), wheel/drag zoom and pan every tile together (untick *Link zoom and pan* for one), Fit / Zoom ±. The page's own `ImageView` (read-only decode; cloud-only never opened). Screenshot checked. §B4. |
| 3 | Group controls as Build 6 bulk actions | **Done** | Each control is `bulk.preview_pairs` over an explicit selection of the cluster's pairs → `store.commit_bulk` → `store.undo_batch`. Table below. §B2, §B3, §B4. |
| 4 | Per-image controls | **Done** | Keeper, Canonical (view state feeding the retention side), Select, **Compare with Active** (the page's Compare Mode, unchanged: `open_compare`; two images that are not a candidate pair are shown with no pair decision on offer), **Exclude from view**. |
| 5 | Non-transitivity proof on F06 | **Done, with a correction** | See judgment call 1. The §34 shape (A~B, B~C, no A~C): Mark Remaining Equivalent writes exactly two relationship decisions, on A~B and B~C, nothing across A–C; A–C is listed as a non-edge. §B2. |
| 6 | Exclude writes nothing | **Done** | The window's Exclude changes only the view; §B4 checks the operation and decision counts are unchanged and the status line says "nothing was recorded". §B2 checks the excluded image's pairs leave the population. |
| 7 | > 8 reported, not forced in | **Done** | `Cluster.kind` = oversized; `oversized_text` gives its size, open pairs and folders; the window's summary line counts them; Survey Mode… on such a pair says so and opens the first 3–8 cluster instead. No paging, ranking or champion mechanism exists. |
| 8 | Zero source mutation | **Done** | §B3 / §B4: `WriteGuard` (no Python-level write outside the project/log folders) and a byte fingerprint of every corpus file before and after. |

**The group controls, and which Build 6 action each one is:**

| Control | Batch action | Pairs covered | New mechanism? |
|---|---|---|---|
| Keep All | `pairs_keep_both` (new registry entry) | every candidate pair among the images in view | no, a new action in the same pipeline (Keep both alone was not a B12 action) |
| Mark Remaining Equivalent, no Canonical | `pairs_equivalent_keep_both` (B12's, unchanged) | every pair; an answered pair is preserved or satisfied | no |
| Mark Remaining Equivalent, a Canonical | `pairs_equivalent_prefer` (new) | same; per pair: the Canonical preferred where the pair includes it and its other image is not a Keeper, Keep both otherwise | small: `plan_pairs` chooses each pair's retention row |
| Mark Selected as Related Variant | `pairs_related_keep_both` (B12's, unchanged) | pairs with **exactly one** image selected | no |
| Defer Group | `pairs_defer` (new) | every pair with no relationship decision; one per-pair deferral each | small: `classify_pairs` learned a deferral candidate (B10's group deferral, for pairs) |

**What was not generic, said plainly** (as the plan asks): B12's pair
pipeline had no Keep-both-only action, no per-pair choice of retention, and
no deferral over pairs. Each is a few lines in `plan_pairs` /
`classify_pairs` plus a registry entry. There is **no second decision-writing
path**: every row still goes through `store.commit_bulk` → `_decision_row` /
`_deferral_row`. The three new actions are `survey_only`, so B12's Bulk
dialog still offers exactly its three (checked). Two preserve rules were
added: Keep both on a Different pair, and a Canonical row on a pair that
keeps both. Neither can fire for B12's actions, because their first row
gates the second.

**Guarantees inherited, and proved on a cluster** (§B2, a six-image
cluster): a Different pair, a related pair and a pair deferred by hand are
preserved; an answered pair is satisfied; pairs touching a cloud-only image
are blocked; a pair that would keep an image another pair (outside the view)
gives up is a **conflict, both rows withheld** (the chain guard); Defer Group
leaves decided pairs *not applicable*. Undo: one `bulk_undo` operation
withdraws the batch (and restores a Defer Group's deferrals, §B3).

### 3. Acceptance (plan §4)

On the same copy, after B12's two batches: **Mark Remaining Equivalent (keep
both, no Canonical) over every one of the 243 clusters**, one batch each,
driven the way the window drives it. This is a *mechanical measurement of
reach*, not an adjudication of your pictures. Nobody looked at them, and
the copy is deleted.

| | Before | After |
|---|---|---|
| Visual Similarity Review (the queue) | 13,734 | **12,258** |
| Across-folders pairs in the queue | 4,911 | **3,903** |
| Clusters of 3–8 left in Across folders | 243 | 0 |
| Isolated pairs / clusters > 8 | 264 / 62 | 264 / 62 (untouched, by design) |

243 batches, **2,952 decisions on 1,476 pairs**. The 1,476 is more than the
1,008 cross-folder pairs because a cluster's pairs include the same-folder
pairs among its images (judgment call 2). **116 pairs preserved** (B12's
*Related variant* rows stand); 0 conflicts, 0 blocked.

**Across Build 6 and Build 7:** the queue went **50,290 → 12,258 (−76%)**;
38,032 of 51,924 pairs carry a decision.

**Timing** (real copy; the window driven headless):

| Step | Time |
|---|---|
| Survey Mode… opens a 7-image cluster (7 tiles decoded) | 0.45 s |
| A group control's preview | 0.12–0.34 s (median 0.145 s over 243) |
| The commit itself | 0.03 s median |
| Record → the page and window redrawn | **3.5–3.8 s** |
| Next cluster | 0.13–0.26 s |
| Undo the last group action | 4.1 s |

**A defect the acceptance found, fixed.** The first acceptance run showed
each Survey preview taking **1.3 s**: B12's whole-batch simulation
re-resolved all 51,924 pairs to check a six-pair cluster. It now resolves
only the batch's two-ring neighbourhood (`bulk._neighbourhood`). Only a pair
sharing an image with the batch can gain a conflict, and every pair that
could give up such a pair's keeper shares that keeper. The answers are
identical: §B2b compares against the whole model on 120 random graphs, 12
of them with conflicts. A 24,000-pair model now previews in 29 ms. It is
B12's code, so it speeds up a small B12 batch too; a folder batch whose
neighbourhood is most of the project costs what it did.

**A cost found and NOT changed: see question 1.** The 3.5 s after Record is
not Survey Mode's. It is the Similar Images page's refresh after any answer
(`imagegame.refresh_record`, B11), which re-reads and re-parses every
decision (2.7 s for ~73,000 after B12's batches) and re-routes every decided
pair (2.2 s). **Every single-pair answer on the page (A / L / E / V / D) now
costs this too once a project holds B12-scale batches**, and the page takes
8.8 s to open (B12 measured 2.8 s on a fresh copy). The handoff lists
changes to Compare Mode as a non-goal, so I measured it and left it alone.

---

## 4. Judgment calls (flag these)

1. **F06 is not the shape the handoff describes.** The handoff and its
   `03_reference` call F06 "three images, two candidate edges, one non-edge".
   The fixture as shipped has **three** candidate pairs: A~C is a weak
   candidate (score 0.5), recorded as *Different*. So I proved both:
   - **F06 as shipped:** Mark Remaining Equivalent over {A, B, C} leaves A~C
     Different (preserved, nothing recorded on it). A~B and B~C, already
     equivalent, get only Keep both.
   - **The §34 shape:** F06 with the A~C candidate removed, built inside the
     suite. A, B, C are shown together through B; A–C is listed as a
     non-edge; exactly two relationship decisions are written.

   I did not add a fixture-lab scenario. If you want §34's shape in the lab,
   it is F06 without `PAC`.
2. **A cluster's pairs are every candidate pair among the images in view**,
   not only the Show list's pairs that joined them. The *images* come from
   the Show list's open pairs (components); the *population* of a group
   control is every current, non-exact candidate pair among those images,
   because those are the pairs a person looking at them can see. Under
   *Across folders* that adds the same-folder pairs among the same images
   (1,592 pairs among the 243 clusters' images vs 1,008 cross-folder ones).
   Most of the extra are already answered by B12's batches and are
   preserved or satisfied.
3. **What "open" means for joining images:** Visual Similarity Review,
   Conflicts, Needs Revalidation, Blocked, Deferred. Resolved and Ready for
   Plan pairs join nothing; neither does a byte-identical pair. Without
   this, B12's resolved D&D pairs would glue the cross-folder pairs into
   crowds of hundreds.
4. **Mark Selected as Related Variant = pairs with exactly one image
   selected.** I took the plan's own words literally: "the candidate edges
   connecting the selected image(s) to the rest of the cluster". A pair
   between two selected images is left alone (the preview says so), because
   selecting two images says each is a variant of the rest, not what they
   are to each other. Nothing selected, or everything selected, is refused
   with the reason.
5. **The Canonical's reach in Mark Remaining Equivalent.** The Canonical is
   preferred only on the pairs that include it. On pairs that don't, both
   images are kept. A Keeper vetoes being the image a pair gives up. So in a
   chain A–B–C with Canonical A: A~B prefers A (B becomes that pair's
   redundant candidate), B~C keeps both. B is then a candidate on one pair
   and kept on another. The resolver has no rule against that (the chain
   guard only fires when a pair's *keeper* is given up elsewhere). It is the
   same state two answers by hand would produce, but see question 2.
6. **Keep All leaves the relationship open.** It records Keep both only, as
   the synthesis maps it, so those pairs stay in the queue (in progress)
   until a relationship is answered.
7. **The evidence mark moves on any scan, even one that changed nothing.**
   That is the price of catching the unchanged-rescan case: a preview made
   before an idle rescan asks to be made again.
8. **Survey Mode is a separate window**, opened from a button beside Bulk
   action… / Batches…. Not a new top-level page and not a keystroke, so
   the page's 20-key map and Compare Mode are untouched. The Show list it
   works over is fixed when it opens.
9. **The batch notes its cluster** (`parameters_json["survey"]`: control,
   Show, images, pairs, excluded, keepers, canonical, selected). This is
   provenance for Batches… and the journal. Nothing reads it back as a
   decision; a cluster is still never a target.

## 5. Nice-to-haves (plan §2)

- **Entry from the Similar Images page:** built (Survey Mode… opens the pair
  in view's cluster).
- **Counts of isolated pairs and oversized components where you'll see
  them:** built, on the window's first line; also in the message shown when
  a Show list holds no 3–8 cluster.

## 6. Non-goals (plan §3)

No adjudicated-cluster object: no table, kind, target kind or projection
names a cluster (§B1 checks the registry). No Tournament Mode and no paging
or ranking of an oversized cluster. No flicker mode or preview cache. No
fourth decision-writing mechanism. No change to Compare Mode or to Build 6's
folder/lens actions' behaviour. B12's code was touched in three places:
`classify_pairs` gained two preserve rules that cannot fire for B12's
actions, the deferral candidate, and the neighbourhood speed-up (same
answers).

## 7. Questions for the next round (not guessed at)

1. **The refresh cost after a big batch** (§3): about 3.5 s per answer
   and an 8.8 s page open once a project holds ~73,000 decisions. Should the
   next round include a patch making the page's refresh incremental
   (re-read only new decisions, re-route only the pairs they touch)? It
   would change Compare Mode's code, which this round kept out of scope.
2. **Judgment call 5:** should a pair that keeps an image (Keep both) while
   another pair gives it up be flagged, the way the chain guard flags a
   given-up *keeper*? Today it is allowed, by hand and by Survey Mode alike.
   The plan builder (Build 8) will have to decide what such an image means.
3. **The 62 oversized components** (3,639 pairs, 74% of the cross-folder
   remainder) are mostly Ren'Py/VS Code GUI assets and adjacent Midjourney
   sessions. Is a folder batch on their common parents the answer you want
   (it already exists), or should they wait for something else?
4. **F06:** should the fixture lab gain the §34 shape (F06 without `PAC`) as
   its own scenario, so the handoff's description and the lab agree?

## 8. Suites on B13

`p3_survey_check` 84/84 (new) · `p3_visual_bulk_check` 79/79 ·
`p3_visual_check` 138/138 · `p3_fixture_check` 65/65 · `p3_regression`
341/341 · `b6_regression` 73/73 · `p2_regression` all · `p2_dashboard_check`
all · `self_check` ready (schema 12, 26 runtime modules).
`Test-Installation.ps1` verifies the refreshed `SOURCE_SHA256.csv` (173 files).
