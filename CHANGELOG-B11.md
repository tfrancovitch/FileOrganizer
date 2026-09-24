# B11 — Phase 3, Build 5: the Image Game

**Date:** 2026-09-23
**From:** B10 (tag `phase3-b10`, commit `a0cfa1c`), on branch `phase3-b11`
**Handoff:** `Phase3_Handoff_Build5.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build5\`
(research synthesis, build plan, fixture lab F06 and F07)
**Handback:** `PHASE3_BUILD5_HANDBACK.md`

B11 compares the images the product has always hashed, and lets a person
say — one pair at a time, mostly with one key — what two look-alike
pictures are to each other. It changes nothing about how B8 resolves an
exact-duplicate group, how B9 routes one or how B10 batches them. It
changes no file.

## What is new

### Phase 2 evidence: visual candidate pairs, migration 012 (schema 12)

The image analyzer stored one pHash / aHash / dHash per image
(`analyzer_result.detail_json`) and compared nothing. `Scripts\Phase2\visual.py`
compares them:

- **Which images:** each present file's *current* image hash — the newest
  image-analyzer attempt on its current observation, when that attempt
  analyzed it. A file observed again since it was analyzed is not paired
  until it is analyzed again. Cloud-only files were never hashed.
- **Which pairs:** pHash within **8 of 64 bits** (Hamming distance), found
  by a bucketed pigeonhole search — the hash cut into 9 segments; two
  hashes within 8 bits share at least one segment exactly, so only images
  sharing a segment value are compared, each pair once. numpy vectorizes
  the comparison where it is installed (imagehash requires it); plain
  Python gives the same pairs. aHash and dHash distances are recorded
  beside the pHash distance as supporting measurements.
- **A crowd is bounded:** each image keeps its **10 nearest** look-alikes
  (by distance, then folder order); the generation row counts the images a
  crowd trimmed.
- **Stable identity:** `visual_candidate` is UNIQUE on its two file
  locations (lower id first). A later generation that finds the same two
  images updates that row — same `candidate_id` — so a decision on the pair
  keeps its target. A pair the latest generation did not find is retired
  (`current = 0`), never deleted. Each row records the method, the
  distances and the observations whose hashes were compared.
- `visual_candidate_generation`: one row per comparison — the run it
  followed, method, bits, threshold, cap, images considered and skipped,
  pairs current / new / retired, images capped, the image-analysis
  watermark it read, how long it took.
- **When:** at the end of every image analysis, inside the Image Analysis
  stage (`RunCoordinator.run_analyzers`; no new stage key, no new run kind;
  the run log and an event say what it found; a failure is a warning and
  the analysis keeps its results). A project analyzed by an earlier build
  is compared the first time the Similar Images page opens
  (`ensure_candidates`, by the watermark); **Find similar images again** on
  that page compares on request.

### Decisions about a pair — `Scripts\Phase3\registry.py`, `store.py`

- A third target kind, `visual_candidate_pair` (target_ref = `candidate_id`).
- **Relationship**, one conflict domain: `equivalent_copy`,
  `related_variant`, `different`.
- **Retention**, separate rows: `canonical_location` now also targets a
  pair (the preferred image — Prefer Left / Right); `keep_both` is new (the
  pair's two locations, the pair-scoped Keep all).
- `store.record_decisions` records several decisions in one operation (a
  fast action's relationship row and retention row land together or not
  at all; each supersedes in its own domain). `store.withdraw_many`
  withdraws several in one operation (Undo of a fast action).
- The store refuses: a preferred image outside the pair; `keep_both` naming
  other images; a relationship on a group or a location; a pair that does
  not exist; a group trigger on a pair and the preview trigger on a group.
- Pair evidence binding: the pair, its method and measurement, the
  observations compared, and each image's current observation — ids only.
- A deferral trigger for pairs: **until a preview is possible** (both
  images present and local — the `preview_available` return kind B9
  reserved). Which triggers a target offers is in `DEFER_CHOICES`.

### Resolving a pair — `Scripts\Phase3\pairs.py`

Each pair on its own, a pure function like the resolver:

| Relationship | Retention | Kept | Redundant candidate |
|---|---|---|---|
| equivalent | canonical X | X | the other image |
| equivalent | keep both (+ canonical) | both | — |
| equivalent | none yet | — | — (in progress) |
| related variant | keep both, or none (the default) | both | — never ordinary cleanup |
| different | — | — | — (resolved: a false positive) |

Conflicts: a retention row still standing on a pair marked Different; the
image a pair would give up is protected (`protected_vs_redundant`, the
same gate as an exact group) or carries an explicit Keep; and **the chain
guard** — when the image one pair keeps is the image another pair gives
up, the first pair is in conflict: nothing asserts the two ends of the
chain are alike, and nothing is inferred. `ProjectProjection.pairs`
carries the pair projections; `resolve_all` computes them.

### Routing — `Scripts\Phase3\routing.py`

The same seven routes for pairs (`route_pair`, `route_pairs`); the pairs'
queue is the **Visual Similarity Review**. Two computed blockers:
`preview_unavailable` (an image cloud-only or not present) and
`stale_image_analysis` (an image re-observed since its hash was compared).
A decided pair whose images moved is Needs Revalidation — never
unreviewed again. A pair of byte-identical images routes to Resolved
(`exact_content`, word "Exact copies"). The detector reconciles pairs too
(and now runs after an Analyze run); a recorded blocked row names its
blocker in `detail.blocker` — an older row without one is read by its
return kind for Build 3's three kinds only, so a recorded preview row is
still honoured as recorded (F08 unchanged).

### The Similar Images page — `Scripts\Phase3\imagegame.py`

In the side panel (**Similar Images**), on the summary (**Similar images**
line, **Compare images**), and from the Duplicate decisions page
(**Compare images…**). One pair at a time: the pair list (Show: the
routes, and the lenses Strong near-copies, Across folders, Cross-root,
Exact copies, All; 500 rows listed around the pair in view), two large
previews with linked zoom and pan (the same region of each image, so a
resized copy lines up), Fit and 100% (the decoded original), unlink and
re-sync, swap sides; under each image its name, folder, resolution,
format, size, modified time and the capture date the file records; above
them the measurement by method ("Perceptual hash (pHash) -- Hamming
distance 4 of 64 bits (Strong near-copy); aHash 3/64, dHash 5/64").

Keys: **A** / **L** equivalent, prefer left / right · **E** equivalent,
keep both · **V** related variant, keep both · **D** different · **[** /
**]** canonical left / right · **W** defer / restore · **S** skip · **U**,
Ctrl+Z undo · **F** fit · **H** 100% · **+** / **−** zoom · **K** link ·
**Y** re-sync · **X** swap · **N** / **P** next / previous. After an answer
the page moves on; Undo withdraws what the last answer recorded.

Exact copies are not asked a relationship: on such a pair the pair keys
are off and **[** / **]** set the exact-duplicate **group's** canonical.

A preview opens the file read-only through Pillow; a file whose attributes
say cloud-only (checked live) is never opened.

### Speed at the real scale

The real collection (25,330 analyzed images; a migrated copy) gives 51,924
pairs. Comparison 7 s. After an answer the page re-reads only the
decision record and re-derives only the pairs, reusing every pair no
decision touches (checked equal to a fresh derivation): ~0.3 s an answer,
under 0.1 s to move between pairs, ~3.5 s to open. The Duplicate decisions
page and its detector leave the pairs out (0.95 s on that project); the
summary's line is plain SQL counts (0.17 s).

### Elsewhere

- `fo_db.APP_VERSION` and `Phase2.VERSION` are `B11`, `fo_db.MODULE_VERSION`
  1.9.0; `APP_SCHEMA_VERSION` and both `REQUIRED_SCHEMA_VERSION`s are 12.
  A B10 or earlier project migrates forward on first open after the usual
  backup.
- `Phase3\model.py`: `VisualPair`, `Evidence.pairs`. `evidence.py`:
  `load_pairs`, `pair_of`, `pair_binding`, `pair_counts`,
  `load_evidence(..., pairs=)`. `review.py`: the Defer dialog serves a
  pair; `decision_summary` and the page load no pairs; **Compare images…**.
  `hub.py`: the Similar images line. `gui.py`: the side-panel button,
  `show_similar`, the detector after Analyze.
- `self_check.py` imports `Phase2.visual`, `Phase3.pairs`, `Phase3.imagegame`
  (27 modules).
- `Resources\Phase3\fixture_lab\`: F06 and F07 join the nine; all 36
  shipped files match the research manifest.
- Tests: `Scripts\p3_visual_check.py` (new, 138 checks);
  `p3_fixture_check.py` loads the lab's visual candidates and tolerates a
  fixture's missing tables; `p3_regression.py` brought to B11.
- Documents: `README.md`, `ARCHITECTURE.md`, `STAGE_MAP.md`,
  `PROJECT_PLAN.md`, `Resources\Phase3\README.md`.

## Verification

| Suite | Result |
|---|---|
| `p3_visual_check.py` — the registry; the pair resolver (every relationship × retention, the conflicts, the chain guard, F06's shape, supersession, determinism, 6,000 pairs); pair routing (every route, both blockers, the preview trigger, exact copies, the lenses, the incremental shortcut equal to a fresh derivation); the generator (exact against brute force, numpy = plain Python, the cap, the words, 20,000 images); a real project with real images (pairs in the Image Analysis stage, on-demand generation, stable identity across re-analysis, fast action and undo, refusals, chain and protection on real files, deferral / skip / restore / detector, a changed image → Needs Revalidation → re-recorded, a cloud-only image never opened, zero mutation); the window (every key, linked / unlinked zoom, re-sync, swap, the evidence panel, Undo, exact copies and the group canonical, Compare images…, the pair Defer dialog) | 138/138 |
| `p3_fixture_check.py` — F01–F10, F15 through the product's resolver, router and matcher, order-independence | 65/65 |
| `p3_regression.py` | 341/341 |
| `b6_regression.py` | 73/73 |
| `p2_regression.py` | all passed |
| `p2_dashboard_check.py` | all passed |
| `self_check.py` | ready, schema 12 |

## Known and unchanged

- Everything B10 listed.
- Perceptual hash only (the research's Tier 1): a crop or a heavy edit is
  usually beyond it. SSCD / CLIP candidates, Survey Mode, flicker, a
  preview cache, Tournament Mode, overlay or heatmap, and bulk over pairs
  are not built.
- The queue on a real collection is large (see the handback): a folder of
  generated variations makes thousands of look-alike pairs, reviewed one
  pair at a time in this build.
