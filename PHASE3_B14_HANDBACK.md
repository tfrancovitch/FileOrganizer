# Phase 3 handback — B14 (maintenance: incremental review refresh + fixture lab sync)

**Against:** `phase_3_handoff_b14\02_B14_HANDOFF_PLAN.md` (Part A, Part B) and
`01_B14_RESEARCH_SYNTHESIS.md` (P3.R10 §22 / UI-6; P3.R3 §34)
**Date:** 2026-09-25
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B14\` — a git worktree of
branch `phase3-b14`, created from tag `phase3-b13`. Not committed, not tagged;
both are the owner's call after this review. `CHANGELOG-B14.md` says what
changed; this document says how it measures against the handoff.

**About the base, and a defect in it.** As the handoff asked, B13 is committed
as `1f16ac2` ("Phase 3 Build 7: Survey Mode (B13)") on `phase3-b13`, with the
annotated tag `phase3-b13`, exactly the tree handed back. **Checking that
commit turned up a defect of mine in B13**:

- **What happened.** Seven files I had edited through Python in B13 were
  written with Windows line endings (CRLF) in the B13 folder: README,
  ARCHITECTURE, STAGE_MAP, PROJECT_PLAN, CHANGELOG-B13, `imagegame.py`,
  `p3_regression.py`. The repository stores LF (`.gitattributes`:
  `eol=lf`), and B13's `SOURCE_SHA256.csv` was hashed from the CRLF copies.
- **What's affected.** The B13 folder itself is self-consistent: it passed
  `Test-Installation.ps1` when handed back, and still does. But **a fresh
  checkout of tag `phase3-b13` fails five manifest entries** (the five of
  those files that are listed). The code is identical; only the hashes of
  those five files are wrong.
- **What I did.** B14's manifest is regenerated from LF files and verifies on
  a clean checkout. Every edit this round writes LF explicitly, and
  `git ls-files --eol` shows no CRLF text file.
- **What I didn't do.** I did not move the `phase3-b13` tag. Question 1 asks
  whether you want it re-pointed at a one-commit fix.

The handoff package is copied to `FileOrganizer-Phase3\phase_3_handoff_b14\`.

---

## Part A — the Similar Images page's refresh, made incremental

### Necessity 1 — the cost, confirmed, and every call site

On a copy of the real collection staged exactly as B13's acceptance left it
(schema 12, B12's two folder batches, B13's 243 Survey actions → queue
12,258, **76,064 decision rows**; the replay reproduced B13's numbers), with
the window driven headless:

| Call site | Path | B13 time | Where the time went (profiled) |
|---|---|---|---|
| A Compare Mode answer (A/L/E/V/D, [ ], Defer/Restore) | `refresh_record` | 3.2–3.3 s | re-parse every decision 2.8 s · re-route 38k decided pairs 2.3 s · re-resolve 0.9 s |
| Compare Mode **Undo** | `refresh_record` + its own | 5.2 s | the above, **plus** `store.active()` — every decision parsed again — to learn which of the undone ids were still active |
| Survey Mode Record | `refresh_record` (**shared**) | 3.7 s | the same as an answer |
| Survey Mode Undo, Batches… undo | `refresh_record` + its own | 4.3 s | the above, **plus** `store.batch(id)` building all 247 batches to return one |
| **Page open (cold)** | **not `refresh_record`** | 8.8 s | the routing detector loads, resolves and routes the whole model, then `reload` does all of it **again** |

**Plain statement, as the plan asks.** Survey Mode's redraw **shared**
`refresh_record`'s path, so fixing it once fixed both. Page open did **not**
share it: a cold open has nothing to increment from. It had a waste of its
own, the model built twice, which is fixed separately (necessity 5). The
Undo and batch-undo extras were two more whole-record reads that the call-site
survey found.

### The necessities, item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Confirm the cost; find every call site | **Done** | The table above. |
| 2 | Incremental decision loading (a watermark) | **Done** | `refresh.marks` takes four watermarks — the newest decision, withdrawal, review event, policy version — *before* a load (a row written during the load is read again next time and skipped, never missed). `refresh.apply` reads only the rows above them and merges them: a new decision row is appended, a new withdrawal row flips `withdrawn` on the row it names, new events are appended. Exact because the record only grows: a decision row is never changed once written (withdrawn by a new row, superseded by a new row), and events are only added. |
| 3 | Incremental routing, reusing `bulk._neighbourhood`'s reasoning | **Done** | B13's function is generalized as `pairs.neighbourhood(ev, pair_ids, images)` (the batch simulation now calls it too). A change to a pair's rows, or to a decision about an image (a Keep on a location, Keep all on an exact group), can change only the pairs sharing an image with it: the first ring. The two-ring model makes their chain guard exact, and those pairs are re-resolved. Re-routed (`route_pairs(..., only=)`): that ring, the changed targets, and every pair with an open event, because a dated deferral fires as the clock moves. Every other route is reused. |
| 4 | Equal results, proved against the full recomputation | **Done** | `p3_refresh_check.py` §1: **240 random steps** (three seeds) through the real store on a real project with real images. Steps: answers superseding answers, withdrawals, deferrals (dated in the past, so the clock fires them, and in the future), restores, skips, explicit Keeps on images, Keep all on the exact group, bulk batches and their undo, a policy change. After **every** step, `refresh.fingerprint` of the incrementally refreshed model is compared with a model loaded, resolved and routed from scratch at the same instant: every decision, event, pair projection, route (conditions, drift, deferral, open events) and count. **240/240 equal** (237 incremental, 3 policy-change reloads); conflicts stood on 228 steps. **At real scale:** 40 random steps on the 76,064-decision copy, including Survey actions, batches and undo and a policy change: **40/40 equal**. |
| 5 | Apply wherever the cost shows up | **Done** | Answer, Undo, Defer/Restore, Bulk/Batches undo and Survey Mode Record/Undo all go through `refresh_record`, now incremental. **Page open:** `routing.reconcile(..., keep=)` hands the page the detector's model and the marks taken before it, and the page catches up with `apply` (the detector's own writes are review events): one load instead of two. **Undo:** `store.still_active(ids)` asks about those rows only. **Batch undo:** `store.batch(id)` reads one batch. §3 of the suite proves page open loads once and equals a fresh model; that an answer, an Undo, a Survey Record and its undo take the incremental path and leave the page equal to a fresh model; and that Undo never reads the whole record. |
| 6 | No functional change | **Done — nothing beyond the computation path moved** | No decision kind, value, supersession rule, routing rule, chain guard, blocker or exception handling changed. **One thing did change in what the page shows, in the direction of correctness:** B11's refresh never re-read *policies*, so a policy created during a Similar Images session (possible only through code today, since the Decide page rebuilds this page when you come back) would not have reached the page's protection conflicts until reopening. The incremental path refuses a policy change and **reloads everything**. The suite first failed exactly there, on B11's old path used as the fallback, which is how this was found. |

### Acceptance (plan §3)

Same staged real-scale copy, the same script against the B13 tree and the B14
tree (unprofiled wall time, the window driven headless):

| | B13 | **B14** |
|---|---|---|
| Compare Mode answer, E | 3.21 s | **0.25 s** |
| Compare Mode answer, D | 3.27 s | **0.21 s** |
| Compare Mode Undo | 5.16 s | **0.49 s** |
| Survey Mode group action (Keep All), Record + redraw | 3.70 s | **0.59 s** |
| Survey Mode Undo | 4.32 s | **0.28 s** |
| Similar Images page, cold open | 8.75 s | **4.69 s** |
| Decisions and routes equal to a full recomputation | — | **40/40 steps** (real copy), **240/240** (suite) |

Incremental refresh on the real copy: median **173 ms** over 40 steps (max
802 ms, a batch undo re-deriving a larger ring). What remains of an answer's
time is the page's own redraw of the list.

**What's left of page open (4.7 s):** one full load of 76,000 decisions (JSON
parse ~2 s), one resolve and route of 51,924 pairs, and the detector's scan
of every target. That is cold-start cost. Removing it would need the
projection persisted between sessions — UI-6's "materialization" proper — so
per the plan's non-goal it's question 2, not built.

---

## Part B — fixture F16, the §34 non-edge

| # | Necessity | Status |
|---|---|---|
| 1 | `F16` in the lab, built from the §34 shape B13 proved; same README / SCENARIO / sqlite shape; validated by the lab's own `validate_fixtures.py` | **Done.** `Fixtures\F16_Visual_NonEdge\`, built from F06's database: the same three images and the same two equivalent decisions (A~B, B~C), with F06's weak A~C candidate, A~C's Different decision and that decision's operation removed. Oracle: `pair_decisions` {PAB, PBC: equivalent}, `candidate_pairs` [PAB, PBC], `non_edges` [[A, C]], `transitive_equivalence_inferred` false. The lab validator raises on a fixture it doesn't know, so it needed an **F16 branch**, computed from the rows alone like every other branch. **`validate_fixtures.py`: 12/12 PASS** (F16 included) on the fixtures the product ships. `MANIFEST.json` gains F16's three files and re-hashes the two I changed; the lab README names F16; `Resources\Phase3\README.md` says what B14 changed in the "kept as received" lab. |
| 2 | F06 unchanged | **Done.** `git diff` on `F06_Visual_Nontransitivity\` is empty; it still validates (12/12 includes it). |
| 3 | Point `p3_survey_check.py` at the fixture, or keep the inline construction | **Pointed at F16.** Its §34 proof now loads F16 (one more check: F16 is F06's images with A~B and B~C only) instead of deleting PAC inline. Less churn than keeping two copies of one scenario. |

`p3_fixture_check.py` also runs F16 through the **product**, not just the
lab's oracle: the resolver, and Survey Mode's own `cluster_map`,
`cluster_edges`, `non_edges` and a Mark Remaining Equivalent preview. It must
cover A~B and B~C only (65 → 71 checks: the oracle, and the row-order
determinism the suite checks for every fixture).

---

## Judgment calls (flag these)

1. **The fallback is a full reload, not B11's refresh.** When `apply` can't
   prove its answer — a policy changed, or a decision names a pair or image
   the loaded model doesn't hold — the page reloads everything from the
   database. B11's refresh is kept only as `refresh.full`, to measure
   against; it never re-read policies (Part A, necessity 6).
2. **Re-routing every pair with an open event on every refresh** (not only the
   changed ones) keeps a dated deferral firing on time. It is cheap (open
   events are few) and it is what makes the suite's "dated deferral fired on
   218 steps" equal the full path.
3. **Survey Mode's redraw needed no change of its own.** It was
   `refresh_record` all along.
4. **Undo and batch undo** were not in the handoff's call-site list, but they
   were whole-record reads on the same keystrokes (necessity 5: apply
   wherever the cost shows up). `store.active_for` also reads one target's
   rows now; it relies on a decision only ever superseding a row on its own
   target, which `_chain_head` guarantees and §2 of the suite checks on
   every target.
5. **The page's exact-duplicate groups are still refreshed as B11 left
   them.** `refresh_record` never re-resolved groups, and the page reloads
   in full where it touches them (the group canonical keys, Undo of a group
   action). The equality proven is over what the refresh maintains:
   decisions, events, every pair's projection and route, and the counts.

## Questions for the next round

1. **The `phase3-b13` tag's manifest** (top of this document): re-point the
   tag at a one-commit fix (the five LF hashes), or leave B13 as tagged and
   let B14 carry the correct manifest?
2. **Cold page open (4.7 s at 76,000 decisions):** is a persisted projection
   (UI-6's "materialization" proper — written once, read on open, kept up to
   date by this round's increments) worth its own scoped round? Build 8 (Plan
   Builder) will need a plan-readiness index and may be the natural place.
3. **Noted for Build 8, as the handoff asked:** B13's judgment call 5 (an
   image that one pair gives up while another keeps it) is recorded in
   `PROJECT_PLAN.md` §6, on the Build 8 line.

## Suites on B14

`p3_refresh_check` 17/17 (new) · `p3_survey_check` 85/85 · `p3_visual_bulk_check`
79/79 · `p3_visual_check` 138/138 · `p3_fixture_check` 71/71 · `p3_regression`
341/341 · `b6_regression` 73/73 · `p2_regression` all · `p2_dashboard_check` all ·
`self_check` ready (schema 12, 27 runtime modules). The lab's own
`validate_fixtures.py` 12/12. `Test-Installation.ps1` verifies the
regenerated `SOURCE_SHA256.csv` (180 files), in this folder and in a clean
checkout.
