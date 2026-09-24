# Phase 3 handback — Build 5 (Image Game), built as B11

**Against:** `phase_3_handoff_build5\02_BUILD5_HANDOFF_PLAN.md` §1–§4 and
`01_BUILD5_RESEARCH_SYNTHESIS.md` (§0 first)
**Date:** 2026-09-23
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B11\` — a git worktree of
branch `phase3-b11`, created from tag `phase3-b10` (`a0cfa1c`). Not committed,
not tagged; both the owner's call after this review. `CHANGELOG-B11.md` says
what changed; this document says how it measures against the handoff.

**About the base.** `phase3-b8`, `phase3-b9` and `phase3-b10` are all tagged,
as the README said. One thing to know: the main checkout's `phase2` branch was
fast-forwarded to `a0cfa1c`, while `phase3` still points at `e44613a` (B8). I
branched from the tag, not from either branch, and changed neither.

## 0. The first question (synthesis §0): what does the image analyzer produce?

**Per-file hashes, no pairs.** The image analyzer (`ImageHash.py` via the
engine's `ImageAdapter`) stores, per image and per observation, `pHash`,
`aHash` and `dHash` as 16-hex-digit strings (64-bit, `hash_size = 8`) plus
`Format` and `HashSize` in `analyzer_result.detail_json`, with width and
height promoted. Nothing in Phase 1/2 ever compared two of them: no table,
no query, no report (the standard report catalog explicitly defers
near-duplicate images to Phase 3).

So B11 had to add the candidate-generation step. Following the synthesis's
boundary argument, it lives on the **Phase 2 side** (`Scripts\Phase2\visual.py`,
tables `visual_candidate_generation` and `visual_candidate` in migration 012):
finding candidates is evidence, not adjudication. It runs **inside the Image
Analysis stage** — after the analyzer's results are stored, the same stage
compares the current hashes — so it reuses the analyzer run rather than
inventing a new run kind or stage key. It is not in the time estimate, because
it takes seconds where hashing takes minutes (next section). A project analyzed
before B11 is compared when the Similar Images page first opens.

**What that search found on your real collection.** I ran the finder against a
*copy* of "Phase 2 Human Test 2 - Real Corpus". It read the stored hashes only:
no file was opened, and nothing touched the original.

| | |
|---|---|
| current image hashes | 25,330 (none unreadable), read in 0.34 s |
| pairs within 8 of 64 bits, 10 nearest kept per image | **51,924**, found in 3.2 s (7.3 s with the database writes) |
| images with more than 10 look-alikes (trimmed, counted) | 5,332 |
| pairs of byte-identical images (set aside) | 1,634 |
| pairs whose two images are in the **same folder** | 44,745 (86%) |
| by extension | PNG 48,136 · WebP 1,315 · JPEG 476 · ICO 344 |
| the folders with the most pairs | the 2023 D&D campaign portrait folders and the Midjourney grid tests — 1,000–2,300 images each, 1,700–6,500 pairs each |
| largest look-alike crowds | 144, 130, 121, 102, 101 … images |
| pairs at 4 bits or fewer | 35,649 — tightening the threshold barely helps |

These are mostly *generated variations*: many images from one prompt that
genuinely look alike. By the research's own vocabulary they are related
variants, not redundant copies. That is the most important finding of this
build, and it is question 1 below.

## 1. The necessities (plan §1), item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Resolve §0 and report; any generation step with the Phase 2 analyzers, reusing the run pattern | **Done** | §0 above. `Phase2\visual.py` compares the hashes inside the Image Analysis stage (`RunCoordinator.run_analyzers`), writes one generation row and the pairs, and notes the counts in the run log and events. `ensure_candidates` covers projects analyzed earlier. `p3_visual_check` §5: the generation is tied to the analysis run; a project whose visual tables are emptied is out of date and regenerates on demand. |
| 2 | Candidate evidence with stable pair identity across re-runs | **Done** | `visual_candidate` UNIQUE(a_file_path_id, b_file_path_id); UPSERT keeps `candidate_id`; not-found pairs retired, never deleted. §5: re-analysis → a new generation, **0 new pairs, every id unchanged**; a pair marked Different stays out of the queue; a decided pair stays decided. |
| 3 | `equivalent_copy`, `related_variant`, `different` on `visual_candidate_pair`, registered like the others | **Done** | `registry.py`: target `VISUAL_PAIR`; the three kinds share the `pair_relationship` conflict domain (a new one supersedes); value `true`; evidence binding required; supersedable; one-click confirmation. The store's gate refuses them on any other target. §1, §5. |
| 4 | Retention on a pair; decide how to represent keep-both | **Done — flagged** | `canonical_location` reused for a pair (the preferred image); `keep_both` new, value = the two location ids (the lab's `["A","B"]` shape). `keep_all_group` could not be reused: its target is an exact-duplicate group version and its binding *requires* that group's current membership. Judgment calls 3–4. |
| 5 | A/B Compare: large previews, synchronized zoom/pan (linked, unlink, re-sync), fit / 100%, the evidence panel, measurement labelled by tier | **Done** | `imagegame.py`: two canvases rendering any region of the full-resolution decode (a pyramid of reduced copies for zoomed-out views); linked by default, **K** unlinks, **Y** re-syncs; **F** fit, **H** 100%; per image: name, folder, W × H px, format, size, modified (UTC), capture date as the file records it (EXIF, read at preview); the line above: "Perceptual hash (pHash) -- Hamming distance 4 of 64 bits (Strong near-copy); aHash 3/64, dHash 5/64" — never a percentage. §6. |
| 6 | Four outcomes plus Defer; fast actions = two distinct rows | **Done** | **A / L / E / V / D** plus **W**; `store.record_decisions` puts the relationship row and the retention row in one operation, each its own decision (superseding only its own domain). §5 checks the two rows, and that a new relationship leaves the retention row standing. |
| 7 | Keyboard shortcuts on their own keymap | **Done** | `KEYMAP` (20 bindings) on the pair list and both canvases; the legend on the page. §6 checks every binding and drives each action through it. |
| 8 | Immediate undo via withdraw | **Done** | **U** / Ctrl+Z withdraws what the last answer recorded (`store.withdraw_many`, one operation); with nothing this session, the pair's latest operation. A decision it replaced is not revived (Build 1's rule). §5, §6. |
| 9 | Routing integration: a new target kind, the same routes, a Visual Similarity Review queue | **Done** | `routing.route_pair` / `route_pairs`, folded into `route_all` (`RoutingProjection.pairs`, `pair_counts`); precedence unchanged; the pairs' Unresolved route is the Visual Similarity Review. `ordered_pairs`. No parallel state machine: the same conditions, events, detector and Show filter shape. §3, §5. |
| 10 | Revalidation wired to pairs | **Done** | The pair binding records both images' observations; `binding_drift` also says when a pair is no longer current. A changed image → **Needs Revalidation** with the stale analysis named — never unreviewed — and the detector records it (idempotently). Analyzed again → same pair id, current again, still asking for revalidation until re-recorded; re-recording clears it and the detector restores the flag. §5. |
| 11 | Exact-duplicate short circuit with voluntary override | **Done** | Byte-identical pairs route to Resolved ("Exact copies") and stay out of the queue; Show → *Exact copies (compare on request)* or **Compare images…** on the Duplicate decisions page opens them side by side; **[** / **]** there record the exact **group's** canonical. §6. |
| 12 | F06, F07 | **Pass** | §2. |
| 13 | Zero source mutation, including previews; no silent hydration | **Done** | Generation opens no image. Previews: Pillow opens read-only, decodes in memory. Before opening, the file's attributes are checked **live** (`os.stat`, which does not hydrate) as well as the scan's flag; a cloud-only image is refused unopened — §5 proves the file is never passed to `Image.open`. §5 and §6 wrap generation, decisions, routing, previews, the journal and the whole window session in the write guard and fingerprint every corpus file before and after. |

**Nice-to-haves (plan §2).**
- **Lenses:** built — Strong near-copies (≤ 5 bits), **Across folders** (added
  after seeing your collection), Cross-root pairs, Exact copies, All.
- **Survey Mode, flicker, preview cache:** not built.
- **Bulk over pairs:** not built — see question 1. B10's batch machinery
  would extend: `commit_bulk` is target-agnostic. But the plan and preview
  functions are group-shaped, so it would be new plumbing, not a reuse.

**Non-goals (plan §3):** no SSCD/CLIP, no Tournament / overlay / heatmap /
face zoom, no quality score, no automatic keeper, nothing that touches a file.
Followed.

## 2. The fixtures

| Fixture | Result | Oracle keys |
|---|---|---|
| F06 Visual_Nontransitivity | **PASS** | pair_decisions {PAB: equivalent_copy, PAC: different, PBC: equivalent_copy}; transitive_equivalence_inferred false — 2/2 |
| F07 Related_Variant | **PASS** | relationship related_variant; keeper_set ["A","B"]; ordinary_redundant_candidate false — 3/3 |
| F01–F05, F08–F10, F15 | **PASS**, unchanged | 38/38 |

Each result is read back from the product's own pair projection (`pairs.py`).
The lab's `evidence_visual_candidate` rows load as the product's pairs; its
`score` (0.95 phash, 0.91 sscd) is kept as the method's own score, never a
percentage. F06 and F07 hold only the tables their scenario needs, so the
loader now treats a missing table as no rows. Per fixture, identical
projections and routes also come back from shuffled rows over three seeds.
`p3_fixture_check.py`: 65/65.

F08 deserves a note: its recorded `preview_available` blocker still routes
as *recorded*, even though B11 can now compute a preview blocker for pairs
(judgment call 11).

## 3. Judgment calls (flag these)

1. **Where generation lives and when it runs.** On the Phase 2 side, inside
   the Image Analysis stage (no new stage key or run kind), plus on demand
   when the page finds the pairs older than the newest image analysis (a
   watermark on `analyzer_result_id`), plus **Find similar images again**. It
   is not in the estimate: seconds against minutes of hashing.
2. **The threshold and the cap.** pHash within **8 of 64** bits; tiers are
   0–2 near-identical, 3–5 strong, 6–8 possible. Each image keeps its **10
   nearest** look-alikes (nearest by distance, then folder order), and the
   generation counts the images trimmed. aHash and dHash are recorded and
   shown, but do not select. Your collection says the threshold isn't the
   lever (35,649 pairs even at ≤ 4); the crowds are.
3. **Retention = `canonical_location` (reused) + `keep_both` (new).**
   "Prefer Left" records the left image as the pair's canonical. With an
   equivalent relationship, the other image becomes the pair's redundant
   candidate; with a related variant it does not. `keep_both` is the
   pair-scoped Keep all. The "optional canonical" of the plan is a canonical
   beside `keep_both`, naming the representative with both kept.
4. **Pair identity = the two locations, not the method.** A later method
   (SSCD, say) that finds the same two images would be more evidence about
   the same pair, not a new pair. In this build the row carries the latest
   method's measurement; a per-method evidence table can come with a second
   method.
5. **"Current" is live.** A pair is current when the latest generation found
   it *and* both images still rest on the observations it compared (checked
   in SQL at load). An undecided pair on a re-observed image leaves the
   queue until analyzed again, since there is no current evidence to review.
   A decided one routes to Needs Revalidation.
6. **Different withdraws retention.** Pressing D on a pair with a retention
   row withdraws that row in the same operation. A retention row somehow
   left beside Different is a conflict, not a silent effect.
7. **A related variant needs no retention row.** Its default is Keep both, so
   the relationship alone resolves it (F07's rule). V still records both rows
   explicitly.
8. **The chain guard is new and blocking.** If pair P keeps image B and
   another pair gives B up, P is a conflict until the person decides the two
   ends directly or keeps both. This is the research's "never infer
   transitivity" made enforceable at plan time: without it, a plan could
   remove both halves of a chain on the strength of an equivalence nobody
   asserted.
9. **Hard constraints stand in front of a pair's give-up.** Protection (the
   same gate as an exact group) and an explicit Keep on the image a pair
   would give up are conflicts.
10. **Exact pairs ask no relationship.** On a byte-identical pair the pair
    keys are off; **[** / **]** record the exact group's `canonical_location`
    (withdrawing a redundant mark on that copy, after asking, as the Decide
    page does). That is the voluntary override: look, and pick the canonical
    by eye, where that decision already lives.
11. **Blocker bookkeeping.** Two computed pair blockers:
    `preview_unavailable` (return `preview_available`) and
    `stale_image_analysis` (return `evidence_change`). Recorded blocked rows
    now name their blocker in `detail.blocker`. A row without one is read by
    return kind for Build 3's three kinds only, so an older `preview_available`
    row still routes as a recorded block (F08).
12. **Deferral choices per target.** A pair offers *until a preview is
    possible* instead of *until fingerprints are current*; groups and bulk are
    unchanged.
13. **Undo is "the last answer".** A session stack of what each action
    recorded; with an empty stack, the pair's latest operation. Nothing a
    withdrawn decision replaced comes back. So Different, then Equivalent →
    Prefer Left, then Undo leaves the pair **unreviewed**, not Different
    (F09's rule).
14. **Linked zoom means the same region, not the same scale.** Linked views
    show the same normalized region of each image, so a half-size copy lines
    up with its original. 100% puts each at its own actual pixels.
15. **The keymap:** A / L positional (left hand, right hand), E, V, D, [ / ],
    W, S, U / Ctrl+Z, F, H, + / −, K, Y, X, N / P. Provisional words, as
    always.
16. **Speed at the real scale shaped three things.** The Duplicate decisions
    page and its detector leave pairs out: the Similar Images page and every
    run reconcile them. The summary's Similar images line is plain SQL counts
    (decided / pairs / byte-identical), not routes. After an answer the page
    re-derives only the pairs, reusing any pair no decision touches, and lists
    500 rows at a time. The first real-scale run took 4.5 s per reload; see §4.
17. **A new suite, not a longer one.** Build 5's checks are
    `p3_visual_check.py` (138), which imports `p3_regression`'s helpers.
    `p3_regression.py` (341) only moved to B11 / schema 12 and the ten kinds.

## 4. Scale

Synthetic (`p3_visual_check` §4 asserts): 20,000 random hashes compared in
0.24 s. In the scratch benchmark 100,000 took 7.2 s, and the pure-Python path
took 79 s before the numpy vectorization. A model of 6,000 images, 5,999 pairs
and 6,000 decisions is resolved and routed in under 5 s (asserted).

The real collection, a copy migrated to schema 12:

| Step | Time |
|---|---|
| migrate 8 → 12 | 1.1 s |
| compare 25,330 images, write 51,924 pairs | 7.3 s |
| load / resolve / route everything (first cut) | 2.4 / 0.9 / 1.2 s → **4.5 s** |
| the same after the fixes (pair currency in SQL, fast paths for untouched pairs) | **1.5 s** |
| the summary's new line | 0.17 s (the rest of the summary, unchanged: 2.4 s) |
| Duplicate decisions page (groups only) | 0.95 s |
| Similar Images page open, detector included | ~3.5 s |
| move to the next pair | 0.02–0.08 s |
| an answer (A, V, D) / Undo | **~0.3 s** / 0.27 s (0.8 s before the incremental path) |

## 5. Questions for the next round (not guessed at)

1. **The queue on your collection is ~50,000 pairs, 86% of them inside a
   single folder of generated art.** One pair at a time will not get through
   that. The options the research already names:
   - **bulk over pairs**, e.g. "every pair inside this folder → Related
     variant, keep both", as a B10-style previewed, undoable batch;
   - **Survey Mode**, where you look at a crowd once and fan it out into
     pairwise decisions;
   - a **folder-level policy** saying "images in this folder are variations,
     not copies".

   Which comes first, and does it come before the plan builder? The *Across
   folders* lens is the stopgap: it holds the ~7,000 pairs where real copies
   are likeliest.
2. Are the defaults right: 8 of 64 bits, the 10-neighbour cap, and aHash /
   dHash as display only?
3. Is the chain guard (judgment call 8) the rule you want, blocking until
   decided?
4. Should a byte-identical pair ever take a relationship decision (currently
   the keys are off; the group canonical is offered instead)?

## 6. Suites on B11

`p3_visual_check` 138/138 · `p3_fixture_check` 65/65 · `p3_regression`
341/341 · `b6_regression` 73/73 · `p2_regression` all · `p2_dashboard_check`
all · `self_check` ready (schema 12, 27 modules). `Test-Installation.ps1`
verifies the refreshed `SOURCE_SHA256.csv`. One `b6_regression` run failed
E.F002 ("duplicate query latency is flat over runs", 2 ms → 8 ms) while I was
running real-corpus measurements alongside it; it passed alone, and nothing
in B11 touches that query.
