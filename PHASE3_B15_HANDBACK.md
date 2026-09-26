# Phase 3 handback — Build 8 (Plan Builder), built as B15

**Against:** `phase_3_handoff_build8\02_BUILD8_HANDOFF_PLAN.md` and
`01_BUILD8_RESEARCH_SYNTHESIS.md` (P3.R7 read in full, scoped by P3.R10 §15)
**Date:** 2026-09-25
**Where:** `C:\FileOrganizerTesting\FileOrganizer-Phase3-B15\` — a git worktree of
branch `phase3-b15`, created from tag `phase3-b14`. Not committed, not tagged;
both are the owner's call after this review. `CHANGELOG-B15.md` says what
changed; this document says how it measures against the handoff.

---

## 0. First: the `phase3-b13` tag (handoff §0)

**New `phase3-b13` commit: `e48fbf0`** ("B13: manifest re-hashed on LF files (no
code change)"), on branch `phase3-b13`, parent `1f16ac2`. The annotated tag
`phase3-b13` has moved from `1f16ac2` to it.

- **What the commit changes:** `SOURCE_SHA256.csv` only, five lines (the
  hashes and sizes of ARCHITECTURE.md, README.md, CHANGELOG-B13.md,
  `Scripts\Phase3\imagegame.py`, `Scripts\p3_regression.py`).
- **Why that is all it takes.** The handoff says to normalize the five files
  to LF, but git already stores them as LF. Only the B13 *folder's* copies
  were CRLF, and the old manifest had been hashed from those. So the fix is
  the manifest alone. I rewrote the folder's seven CRLF files (the five plus
  PROJECT_PLAN and STAGE_MAP, which are unlisted) to the exact bytes git
  stores, then regenerated. `git checkout-index` would not do it: this
  repository has `core.autocrlf=true` and wrote CRLF again.
- **Verified:** a clean checkout of `e48fbf0` in a temporary worktree passes
  `Test-Installation.ps1` ("manifest verified").
- **B14** is committed as `989294b`, tagged `phase3-b14`. Its parent is
  `1f16ac2` (it was built before the fix); it carries the same corrected
  hashes. The fresh `phase3-b15` checkout of that tag also verifies.
- **One history note.** `e48fbf0` is a sibling of the B14 commit, not its
  ancestor. That is harmless because B14's manifest already has the
  corrected hashes, but it's worth knowing if you ever walk the branch
  history.

## 1. Synthesis §0: the schema shape, and why (necessity 1)

**Confirmed first:** nothing in the schema, migrations or code resembled a
plan table. Schema 13 is new.

**Chosen: a hybrid.** R7 §45's normalized family where the data is
**queried**; the fixture lab's JSON shape where it is only **read back
whole**.

| Normalized (own table) | Why |
|---|---|
| `p3_plan_item_decision` (item, decision, role justifies / retains), indexed by decision | **This is the dependency index** (R7 §33). "Which locked revisions does decision D feed?" is one indexed lookup (`store.revisions_depending_on`). It is also the only way a revision's own dependencies can be read without parsing every item. |
| `p3_plan_item_policy` | The same, for policy versions. |
| `p3_plan_dependency` | The item graph; the acyclic check runs over it. |
| `p3_plan_precondition` (key, class, severity, stage, parameters, result, detail) | Preconditions are first-class (P3-A93). The preview counts and filters them, the page lists them per item, and the lock payload includes them. |
| `p3_plan_event` | The revision's history (previewed, locked with its dependency set, obsolete with reasons, superseded), append-only like every p3 history. |

| Kept as JSON on `p3_plan_item` | Why |
|---|---|
| `evidence_binding_json` | **Build 1's existing pattern, reused:** the same ids-only shape and column name as `p3_decision.evidence_binding_json`, so `routing.binding_drift` reads a plan item's binding unchanged. |
| desired effect, estimated effect, postconditions | Read whole, never queried. |

**Did it matter?** Yes, in one place. The decision-link table is what makes
obsolescence dependency-scoped and cheap. The detector reads each locked
revision's *own* links, and "who depends on D" is one index lookup. With
the fixture lab's JSON arrays, both would mean parsing every item of every
revision. Everything else could have gone either way. The shape follows the
existing convention (`p3_bulk_member` normalized, bindings as JSON).

## 2. The necessities, item by item

| # | Necessity | Status | Where / how proved |
|---|---|---|---|
| 1 | Resolve §0; report the shape | **Done** | §1 above. |
| 2 | Plan tables supporting the five minimum action kinds | **Done** | Migration 013 (eight tables); registry `ACTION_KINDS`. `plans.build` makes `remove_location` and `no_op_preserve` from decisions. `plans.logical_item` makes `move_location` / `rename_location` (collision check) and `update_source_metadata` (unsupported: no writer, R10 §16). No decision kind asks for the last three yet, so the builder does not invent them (§4 JC 7). `p3_plan_check` §1, §2c. |
| 3 | Draft from current state, reusing Ready for Plan | **Done** | The population is every group and pair whose **decisions** resolve it ready for a plan: the resolver's `ready_for_plan`, the Ready-for-Plan route's own criterion. The builder never re-decides eligibility. Judgment call 1 covers the routes around it. |
| 4 | Frozen revision + SHA-256 + R7 §29's lock sequence | **Done** | `record_plan_revision` freezes in one `plan_build` operation. `lock_plan_revision`: (1) the record and evidence marks must be where the preview found them, else build again (B10/B12's commit guard); (2) canonical payload; (3) SHA-256; (4) locked; (5) `locked` event; (6) the exact dependency set in that event. A revision read back from the database fingerprints identically (§3); the same record in any row order gives the same fingerprint (§2c). |
| 5 | Decision, evidence and dependency links per item, reusing Build 1's bindings | **Done** | Decision links (justifying and retaining), policy links, an ids-only evidence binding in `p3_decision`'s shape (target and its observation and content id, keepers, source group or pair with members), and item dependencies (a removal depends on its keeper's preserve item). |
| 6 | Severity × result; hard unknown blocks | **Done** | R7 §16's vocabulary. `plans._settle`: a hard `fail` gives `blocked`, a hard `unknown` gives `blocked_evidence`, a hard `unsupported` gives `unsupported_action`; warnings never block. **Property test:** over 300 random result sets an item is ready exactly when no hard evidence-stage precondition is fail, unknown or unsupported. **F12** (below). |
| 7 | The Retention precondition, resolving §6 | **Done** | A hard `no_retaining_decision` precondition (the one Build 8 addition to R7 §37's vocabulary, JC 3). It reads **every** current decision naming the image. If any keeps it, the removal is generated and blocked, naming them. §5 below has the proof. |
| 8 | Evidence-only preview; three metrics distinct | **Done** | Every check reads the loaded model only. **Proved by a read guard:** no `open`, `stat`, `lstat`, `scandir` or `listdir` of any path under the corpus, and no write outside the project, during model load, build, freeze, lock, the detector and the page (§3 and §5 of the suite; 0 on the real collection). Plan-Eligible Reclaimable Bytes count a physical object once, only when all its names go. They are shown beside Phase 2's figure under its own name; "Verified Reclaimed Bytes exist only after Phase 4" is stated. |
| 9 | Obsolescence via the existing detector, dependency-scoped | **Done** | `routing.reconcile` now also calls `store.reconcile_plans`, which runs `plans.obsolescence` on each locked revision's **own** links, bindings and sources. Triggers proved on a real project: justifying decision withdrawn; superseded (a flipped preference); a new decision that keeps a planned removal; a protection policy over it; the removal's file changed on disk and rescanned; a new copy joined its group. **An unrelated decision does not obsolete** (suite §3; the real collection). An obsolete revision keeps its items, links and fingerprint byte for byte (P3-A97), and nothing is re-planned. |
| 10 | Blockers and warnings on the preview, with R7 §14's disclosure | **Done** | The preview in R7 §26's shape, with coverage per §27 ("live existence, content, permissions and rollback are deferred to Phase 4"). The disclosure, word for word, sits in a band that never scrolls away. Each item's detail lists the checks made now (with results and reasons) and those declared for Phase 4 (not evaluated). |
| 11 | Zero source mutation | **Done** | The same read and write guards, plus a byte fingerprint of every corpus file before and after (the files the test itself edits to trigger drift are excluded). Plan Builder writes only p3 rows. |

## 3. Acceptance (plan §4)

| Case | Result |
|---|---|
| **F11 (Plan_Drift)** | The lab's own locked, fingerprinted revision, judged by **the product's** `plans.obsolescence` against its record (D1 superseded by D3 after the lock): `obsolete` true, reason **"source decision changed"**, `locked` true, fingerprint the stored `504f7477…d4f4` (never rewritten), `auto_replanned` false. **PASS** (`p3_fixture_check`). |
| **F12 (Hard_Precondition_Unknown)** | **The product's** `plans.build` on the lab's evidence rebuilds the removal of A: `minimum_physical_copy_count_satisfied` = **unknown**; the item is **`blocked_evidence`**; the reason reads "physical identity unknown for A.bin: whether a physical copy would remain cannot be counted from the evidence". **PASS.** |
| **§6's resolution** | §5 below. |
| **F01–F10, F13–F15, F16** | F01–F10, F15 and F16 unchanged and green. **F13 and F14 are not in the product's lab** (they are Build 9's metadata scenarios; see JC 10). The lab's own `validate_fixtures.py`: **14/14 PASS** on what is shipped. |

**Real scale**, on a copy of the real collection (≈41,000 files; the state
B13's acceptance left: 76,064 decisions). The real record had nothing ready
for a plan: B12 and B13 recorded only keep-both. So, **on the copy and as a
mechanical measurement** (nobody looked at a file):
- Keep copies under `renpy-8.5.2-sdk\vscode` and mark copies under
  `renpy-8.5.2-sdk\tmp` redundant: the folder pair sharing the most exact
  groups, 2,313 of them.
- Prefer one image of each of the 264 isolated cross-folder pairs.

The copy has been deleted.

| | |
|---|---|
| Ready for a plan | 2,314 groups + 260 pairs |
| Items | 5,437: 2,719 removals, 2,718 preserves; **2,711 ready, 8 blocked** |
| Blocked by | `no_retaining_decision` **7** (B13's case, found in real data: exact-duplicate icons that B13's Survey actions kept both of, e.g. *"#73420: pair #1480 keeps both it and icon.png"*), `keeper_exists` 1 |
| Plan-Eligible Reclaimable Bytes | **786 MB** (490 MB exact duplicates, 296 MB equivalent images), beside Phase 2's **2.42 GB** Potentially Reclaimable |
| Build + evidence preview | 0.35 s |
| Freeze / read back / lock | 0.93 s / 0.51 s / 1.16 s; fingerprint round-trips |
| Detector incl. plan obsolescence | 6.8 s (the model load it already did is 5.5 s) |
| Unrelated decision → detector | revision still locked |
| One source decision withdrawn → detector | `obsolete`, "source decision changed", fingerprint and items unchanged |
| Source files opened / stat'ed / listed under the real roots | **0** |
| Plan page: open / Build / Lock / Compare | 6.3–7.2 s / 2.7 s / 1.7 s / 0.5 s |

Two costs were found and fixed on the way:
- **Linear group lookups.** `proj.group()` is a linear scan, and the builder
  called it once per removal: quadratic, the class of bug B12 hit. It's now
  indexed once.
- **Page reloads.** The Plan page first reloaded the whole model after every
  action (Build 13.9 s, Lock 7.5 s). It now reuses the detector's model at
  open, and any model while the record and evidence marks stand (Build 2.7 s,
  Lock 1.7 s). The suite proves one load at open.

## 4. Judgment calls (flag these)

1. **The population is decisions-ready, not only route Ready-for-Plan.**
   - A group whose decisions are ready but whose route is **Blocked**
     (physical identity unknown, stale fingerprint) or **Needs
     Revalidation** is *in* the plan, its removal visibly blocked with the
     reason. F12 requires exactly that: its group routes Blocked, yet the
     lab expects a blocked item, not a missing one.
   - A group **you deferred** stays out and is counted ("your not yet").
   - A group in **Conflicts** is never decisions-ready, so it's out too.
2. **A revision with blocked items can be locked.** Its blocked items are
   locked *as blocked*, never ready (R7 §46 rule 6); membership is frozen,
   so the alternative (refusing to lock while anything is blocked) would
   make any conflict in the project block every plan.
3. **`no_retaining_decision` is a new precondition key.** R7 §37's
   vocabulary is a baseline; the handoff described the check as
   "`keeper_exists`-shaped". It is class *retention*, severity *hard*. What
   it counts as "keeping" an image:
   - an explicit Keep;
   - its exact group's Keep all, its canonical, or explicit keeps;
   - a pair's preference for it or Keep both;
   - a related variant's default Keep both.

   Where a pair has both a relationship and a Keep both row, the Keep both
   row is the one named.
4. **The lock guard is B10/B12's:** any new operation or evidence change
   since the preview voids the lock; build again. Strict, simple, and
   consistent with every other commit guard.
5. **The fingerprint covers the preview's results and states** as well as
   the declarations (R7 §6's list). Locked is "this exact reviewed package",
   including which items were blocked.
6. **Two dry-run stages per precondition.** `evidence_preview` checks run
   now. `live_preflight` checks are declared with no result and never block
   in Phase 3. A cloud-only target is a *warning* (Phase 4 would need it
   local to verify its bytes), not a block.
7. **Move, rename and metadata items are not generated.** No decision asks
   for them yet (desired metadata is Build 9's). The builder never invents
   intent, and an item built without a decision fails `decision_current`.
8. **Undecided copies in a planned group get no item.** They are counted
   ("N undecided copies in planned groups; the Plan Builder never decides
   them"). A preserve item would imply a keep nobody decided.
9. **Plan obsolescence runs only when the detector has the pairs**: after
   collection runs, and when Similar Images or Plan opens. The Decide page's
   pair-less pass skips it, since a pair-dependent revision can't be judged
   without its pairs. The detector's counts include `plans_obsolete` only
   when a plan was marked (like every other count there), so the ten tests
   comparing those counts exactly stayed as they were.
10. **F11 and F12 were not in the product's lab.** The handoff says they
    were "already in the fixture lab". The *research* lab has them; the
    product had shipped only the fixtures its builds used. I copied both
    from `Docs\Research\phase_3\fixture_lab\`, verified each byte against
    the lab manifest's hashes, and updated the lab README and
    `Resources\Phase3\README.md`.
11. **The acceptance decisions were mechanical** (above). The real record
    had no plan-ready decision to measure.

## 5. The §6 resolution, proved (B13's judgment call 5)

**Models** (`p3_plan_check` §2b):
- **B13's exact shape:** X~Y prefers X; Y~Z keeps both. The resolver sees no
  conflict and pair X~Y routes Ready for Plan. The removal of Y **is
  generated**, is **blocked on `no_retaining_decision`** naming #D4 (pair
  Y~Z's Keep both), stays justified by X~Y's decisions and dependent on X's
  preserve item, and is counted blocked with no reclaim claimed.
- **Also blocked:** a related variant's default Keep both; the image's
  exact-duplicate group's Keep all. That second case found a real bug: the
  group's own rows were missed at first, because the resolver's per-copy
  verdict doesn't carry them. It's fixed.
- **The other way round:** an exact copy marked redundant while a visual
  pair keeps it.
- **Control:** without another keeping decision, the same removal is ready.
- **An explicit Keep on the pair's redundant image** puts that pair in
  Conflicts (the resolver's rule), so it never reaches the plan at all.

**Real images** (`p3_plan_check` §3): A_small.jpg is preferred away by its
pair with A.jpg and kept by its pair with A_bw.jpg. Its removal is generated
and blocked, naming that pair's Keep both row.

**Real data:** 7 such removals on the copy of your collection (§3).

## 6. Nice-to-haves (plan §2)

- **Plan diff between revisions: built.** `plans.diff`, which reuses the
  revision-reading machinery; *Compare with previous* on the page (items
  added and dropped by action, newly blocked or unblocked, the Plan-Eligible
  Reclaim moving).
- **Privacy-reduced export:** not built (not required).
- **Persisted preview projection: not built.** It did *not* turn out to be
  nearly free. The dependency index is per-revision link rows, not a
  projection of the whole project. The page-open cost is now the detector's
  own model load (~5.5 s of its ~6.8 s at 76,000 decisions), which is where a
  persisted projection would pay. That's the question below, not something
  to build silently.

## 7. Questions for the next round

1. **Locking with blocked items** (JC 2): keep it, or refuse to lock while
   any item is blocked?
2. **A persisted project projection** (UI-6 proper). At real scale every
   page open and every detector pass rebuilds the model (~5.5 s). Worth a
   scoped round before Phase 4, which will lean on it harder?
3. **Build 9 (project metadata)** will be the first source of
   `update_source_metadata` items. They'll plan and stay `unsupported_action`
   until a writer is certified (R10 §16). Is that the expected end state for
   Build 9, as the handoff suggested?
4. **F13 and F14** (metadata) come in with Build 9 the same way F11 and F12
   came in here, unless you'd rather ship the whole lab now.

## 8. Suites on B15

- `p3_plan_check` **80/80** (new)
- `p3_fixture_check` 84/84 (F11, F12 added)
- `p3_regression` 341/341
- `p3_refresh_check` 17/17
- `p3_survey_check` 85/85
- `p3_visual_bulk_check` 79/79
- `p3_visual_check` 138/138
- `b6_regression` 73/73
- `p2_regression` all
- `p2_dashboard_check` all
- `self_check` ready (schema 13, 32 runtime modules)
- The lab's `validate_fixtures.py`: 14/14
- `Test-Installation.ps1`: the regenerated `SOURCE_SHA256.csv` (192 files) verifies, in this folder and in a clean checkout.
