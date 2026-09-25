# B12 — Phase 3, Build 6: Bulk Visual Review

**Date:** 2026-09-24
**From:** B11 (tag `phase3-b11`, commit `15b5966`), on branch `phase3-b12`
**Handoff:** `Phase3_Handoff_Build6.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_build6\`
(research synthesis grounded in P3.R4 and P3.R10 §13, build plan)
**Handback:** `PHASE3_BUILD6_HANDBACK.md`

B12 lets a person answer a crowd of look-alike images once — a folder of
variations of one prompt, say — instead of pair by pair, without weakening
anything the Image Game guarantees about each pair. It is B10's frozen
query-result batch, extended to visual candidate pairs. It adds no table,
no decision kind and no policy, and it changes no file.

## What is new

### Three bulk actions over pairs — `Scripts\Phase3\registry.py`

| Action | Each pair in scope receives |
|---|---|
| **Related variant, keep both** (`pairs_related_keep_both`) | `related_variant` + `keep_both` — what **V** records |
| **Equivalent copy, keep both** (`pairs_equivalent_keep_both`) | `equivalent_copy` + `keep_both` — what **E** records |
| **Different** (`pairs_different`) | `different` — what **D** records |

None has a policy twin: the research rules out a dynamic policy for
relationship and retention decisions (P3.R4's table; its A/B–B/C–C/D chain
example), so nothing here re-applies itself to pairs found later.
`bulk_actions_for()` keeps each page to its own actions (the Decide page's
five, the Similar Images page's three).

### The batch — `Scripts\Phase3\bulk.py`

- **Population:** the Similar Images page's Show list (`filter_pairs`,
  moved here from the page so a snapshot freezes exactly what was listed),
  optionally narrowed to a folder — both images in it, beneath it
  (subfolders included, the default) or directly in it — or the pairs a
  person checked. Exact copies are never a bulk population; a byte-identical
  pair met inside a folder is counted *not applicable*.
- **The preview** (`preview_pairs`), R4's shape: population, new decisions,
  already satisfied, exceptions preserved, conflicts, blocked (plus not
  applicable). Per pair, in order: already says exactly this → *satisfied*;
  in Conflicts (the chain guard, protection, …) → *conflict*; Blocked or
  Needs Revalidation (preview unavailable, stale image analysis, moved
  evidence) → *blocked* — each read from the pair's route, the computation
  the page shows; then explicit intent is *preserved*: another relationship,
  a preferred copy that Keep both would undo, a retention row that Different
  would contradict, a deferral made by hand. The first row gates the second.
  Then the whole batch runs through the pair resolver at once
  (`classify_pairs`): a conflict the batch's own rows would create — the
  chain guard included — keeps the pair it falls on out. Every member lands
  in a bucket with its reason; none is dropped.
- **Bindings** are built from the loaded model (`evidence.pair_binding_from`,
  the same shape a fast action binds), so previewing thousands of pairs
  costs no per-pair query.
- `preview_table` — every member with its disposition and reason (the
  dialog saves it as CSV).

### The commit and the guards — `Scripts\Phase3\store.py`, `evidence.py`

`commit_bulk` is B10's: one operation of kind `bulk` holds the batch row
(`query_result_snapshot` or `explicit_selection`, the frozen query), one
member row per pair with its disposition, and every decision — origin
`bulk_explicit_human`, `origin_ref` = the batch id, bound to the pair's
evidence. New: a pair preview also carries an **evidence mark**
(`evidence.evidence_mark`: the newest image comparison and the newest
observation). The commit refuses if the decision record moved (B10's rule —
any new operation) **or** the evidence moved: a re-analysis or a scan adds
no operation, so the record guard alone would not see it. Undo is B10's
`undo_batch`.

**Fixed:** the store's chain-head and history queries named the target
without the project id that leads `ix_p3_decision_target`, so SQLite scanned
every decision per query — each decision a batch records asks one. It went
unseen at B10's 6,000 decisions (3.4 s); at the real collection's 62,500 it
did not finish in ten minutes. With the prefix they search the index
(`EXPLAIN QUERY PLAN`: SCAN → SEARCH): the same 62,500 now commit in 3.3 s.

### The Similar Images page — `Scripts\Phase3\imagegame.py`

- A **check column** on the pair list (click it, or Space on the focused
  row), **Check all shown** (the whole Show list), **Clear**, "N checked".
  The letter keys still act on the pair in view only.
- **Bulk action…** → the pair dialog: the three actions; *Apply to* the N
  checked pairs, the N pairs listed now (Show: …), or the pairs listed now
  with both images in a folder (the entry, **Browse…**, **This pair's
  folder**; *Include subfolders*); **Preview**; **Save the preview list…**
  (CSV into the project's `Exports\`, formula-safe); **Record N decisions on
  M pairs**, enabled only for the inputs previewed, voided by any change.
  A refused commit (record or evidence moved) says why and asks for a new
  preview.
- **Batches…** on the page. The Batches list names a pair member
  *a.jpg ~ b.jpg (pair #N)* and a checked-pair scope "N checked pairs".

### At the real scale

On a copy of the real collection (25,330 images, 51,924 pairs; a pair
marked Different by hand and a chain set up inside the D&D folder first):

| Batch (Related variant, keep both) | Pairs | Decided | Preserved | Conflict | Not applicable | Preview | Commit |
|---|---|---|---|---|---|---|---|
| `Tommys Pictures\2023 Dnd Campaign` (subfolders) | 31,393 | 31,250 | 2 | 1 | 140 | 1.7 s | 3.3 s (62,500 decisions) |
| `midjourney\MJ Gridtest` (subfolders) | 5,332 | 5,303 | — | — | 29 | 1.0 s | 0.5 s |

The Visual Similarity Review goes from 50,290 pairs to **13,734** (−73%);
the D&D batch alone to 19,037. In the window: preview 2.0 s, Record and
redraw 5.6 s, Batches… with 31,393 member rows 2.4 s, Undo and redraw
5.4 s, moving between pairs under 0.1 s.

### Elsewhere

- `fo_db.APP_VERSION` and `Phase2.VERSION` are `B12`; `fo_db.MODULE_VERSION`
  1.9.1; the schema stays **12** (no migration).
- `review.py`: the Decide page's bulk dialog offers only its own five
  actions.
- Tests: `Scripts\p3_visual_bulk_check.py` (new, 79 checks);
  `p3_regression.py` brought to B12 and the eight bulk actions.
- Documents: `README.md`, `ARCHITECTURE.md`, `STAGE_MAP.md`, `PROJECT_PLAN.md`.

## Verification

| Suite | Result |
|---|---|
| `p3_visual_bulk_check.py` — the registry (existing kinds, no policy twin, each page its own actions); the population (one Show function, the folder with and without subfolders, one image outside, a sibling with the same prefix, the frozen query); every bucket pair by pair (decided = V's rows, satisfied, preserved ×4 kinds of intent, conflict by the chain guard, blocked by cloud-only and by revalidation, not applicable), the gate, the whole-batch simulation (a chain inside one batch: the second half withheld), determinism, 11,000+ pairs previewed; a real project with real images (the folder counts, the commit's one operation, provenance, the same rows a fast action writes, the exceptions untouched, the guards after an operation / a re-analysis / a scan, a picture added later not a member and waiting unreviewed, undo leaving a hand-changed pair alone, checked pairs, exact copies refused, the CSV, the journal, zero mutation); the window (the check column and Space, keys unchanged, Check all shown, the dialog's scopes and folder, preview, voiding, the CSV, Record, the queue shrinking, Batches with pair labels and Undo, the Decide page's own actions) | 79/79 |
| `p3_visual_check.py` | 138/138 |
| `p3_fixture_check.py` | 65/65 |
| `p3_regression.py` | 341/341 |
| `b6_regression.py` | 73/73 |
| `p2_regression.py` | all passed |
| `p2_dashboard_check.py` | all passed |
| `self_check.py` | ready, schema 12 |

## Known and unchanged

- Everything B11 listed.
- Survey Mode is Build 7 (the revised sequence).
- The undo of a batch is B10's `undo_batch`, not `withdraw_many` by name:
  both withdraw a set of decisions in one operation; `undo_batch` also
  restores a deferral batch's events and refuses when nothing stands.
- Only the preserve mode is recorded (a batch never overwrites).
