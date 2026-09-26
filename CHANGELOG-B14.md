# B14 — Phase 3 maintenance: incremental review refresh + fixture lab sync

**Date:** 2026-09-25
**From:** B13 (tag `phase3-b13`, commit `1f16ac2`), on branch `phase3-b14`
**Handoff:** `Phase3_Handoff_B14.zip`, kept at
`C:\FileOrganizerTesting\FileOrganizer-Phase3\phase_3_handoff_b14\`
(synthesis grounded in P3.R10 §22 / UI-6 and P3.R3 §34; plan)
**Handback:** `PHASE3_B14_HANDBACK.md`

Not a build: two small pieces of maintenance that B13's own measurements
surfaced. No table, no migration (schema still 12), no new decision kind, no
change to what any decision records, how routing decides, the chain guard,
blockers or exceptions. No file is changed.

---

## Part A — the Similar Images page's refresh, made incremental

At real scale (a copy of the owner's collection after B12's and B13's
batches: 76,064 decision rows) every answer on the page cost 3.2 s, an Undo
5.2 s, a Survey Mode group action 3.7 s, and the page took 8.8 s to open:
after any answer the page re-read and re-parsed every decision and re-routed
every decided pair.

- **`Scripts\Phase3\refresh.py` (new).** `marks(conn)` takes four
  watermarks — the newest decision, withdrawal, review event and policy
  version (`evidence.record_marks`). `apply(conn, ev, proj, routing, since)`
  reads only what lies above them (decision rows are never changed, only
  withdrawn or superseded by new rows; events are only added), re-resolves
  the first ring of pairs around what changed through `pairs.neighbourhood`
  (B13's batch-simulation neighbourhood, generalized — a change to a pair's
  rows, or to decisions about an image, reaches only pairs sharing an image
  with it), and re-routes that ring, the changed targets and every pair with
  an open event (a dated deferral fires by the clock). It returns None — and
  the page reloads everything — when it cannot prove its answer: a policy
  changed, or a decision names a pair or image the model does not hold.
  `fingerprint()` is the comparison the suites use; `full()` is the B13
  refresh, kept to measure against.
- **`imagegame.refresh_record`** uses `apply`, falling back to a full reload.
  Every caller gets it: a Compare Mode answer, Undo, Defer / Restore,
  Bulk action… and Batches… undo, and Survey Mode's Record and Undo (which
  call `refresh_record`).
- **Page open** (`show_similar` → `detect` → `reload`) loaded, resolved and
  routed the whole model twice: once in the routing detector, once for the
  page. `routing.reconcile(..., keep=)` / `review.run_detector(..., keep=)`
  now hand their model and the marks taken before loading it to the page,
  which brings it up to date with `apply` (the detector's own writes are
  review events).
- **`evidence.py`:** `load_decisions(conn, where, args)` (one parser for a
  full or a partial read), `load_events(conn, after)`, `record_marks`,
  `load_decisions_after`, `withdrawals_after`.
- **`pairs.neighbourhood(ev, pair_ids, images)`** — the two-ring model and
  the first ring's ids; `bulk._neighbourhood` now calls it (same answers).
- **`routing.route_pairs(..., only=)`** — re-route those pairs, reuse the
  previous route for the rest.
- **Undo's and batch undo's own full reads, found in the same survey:**
  Undo asked `store.active()` (every decision, parsed) which ids were still
  active; `store.still_active(ids)` asks for those rows only.
  `store.active_for(kind, ref)` reads one target's rows (a decision only ever
  supersedes the chain head on its own target). `store.batch(id)` built
  every batch (247 on the real copy) to return one; `batches(batch_id)` now
  reads the one.

| Real-scale copy, 76,064 decisions | B13 | B14 |
|---|---|---|
| Compare Mode answer (E / D) | 3.2–3.3 s | 0.21–0.25 s |
| Compare Mode Undo | 5.2 s | 0.49 s |
| Survey Mode group action, Record | 3.7 s | 0.59 s |
| Survey Mode Undo | 4.3 s | 0.28 s |
| Page open, cold | 8.8 s | 4.7 s |

## Part B — fixture F16, the P3.R3 §34 non-edge

- **`Resources\Phase3\fixture_lab\Fixtures\F16_Visual_NonEdge\`** (new):
  README.md / SCENARIO.json / fixture.sqlite in F01–F15's shape, built from
  F06 with its weak A~C candidate and A~C's Different decision (and that
  decision's operation) removed. Oracle: A~B and B~C equivalent, candidate
  pairs PAB and PBC, non-edge A–C, nothing inferred. **F06 is unchanged.**
- **`validate_fixtures.py`**: an F16 branch computed from the rows alone,
  like the others — **12/12 PASS** on the fixtures shipped. `MANIFEST.json`
  gains F16's three files and the two re-hashed ones; the lab README names F16.
- **`p3_fixture_check.py`**: F16 through the product — the resolver and
  Survey Mode's own cluster, pair and non-edge code (65 → 71 checks).
- **`p3_survey_check.py`**: its §34 proof loads F16 instead of building the
  shape inline (84 → 85 checks).

## Versions

`fo_db.APP_VERSION` = `Phase2.VERSION` = **B14**; `fo_db.MODULE_VERSION`
1.9.3; schema 12. `self_check.RUNTIME_MODULES` += `Phase3.refresh` (27).

## Tests

- **New:** `Scripts\p3_refresh_check.py` (17) — 240 random steps on a real
  project with real images, each compared with a fresh full load, resolve
  and route; the store's targeted reads against the whole-record reads; the
  window (one load at page open, the incremental path after an answer, an
  Undo and a Survey Mode action, the full reload after a policy change).
- `p3_regression.py`: version checks read B14.
