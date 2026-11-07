# FileOrganizer: status (one page)

**Updated:** 2026-11-07 · **Keep this current at every release and at the end of every session.** The canonical
plan is `PROJECT_PLAN.md` (workspace root); this page only says where things stand.

## Where we are
**Phase 4 (Act and Verify) is approved to start. Nothing is built yet.** On 2026-11-07 the owner approved the v1
boundary and the build order B21-B27 in `Handoffs/Phase4/PHASE4_SCOPING.md`.

**Next step:** run the five research briefs (`Handoffs/Phase4/B-R4.1`, `.2`, `.3`, `.6`, `.8`) with the
`fo-researcher` agent on Sonnet, and file the reports in `Docs/Research/phase_4/R4/`. Then the Architect writes the
synthesis, a different model reviews it (review gate 1), and the B21 handoff follows.

| Phase | Status | Final build / tag |
|---|---|---|
| 1 Observe | Shipped | B6.2 · `phase1-b6.2` |
| 2 Understand | Shipped; closeout approved 2026-09-19 | B7.2 · `phase2-b7.2` |
| 3 Decide / Plan | **Closed 2026-10-13** by the owner (`Docs\Handoffs\P3_CLOSEOUT.md` §5a) | B20 · `phase3-b20` = `045a0a1` |
| 4 Act and Verify | **Approved; research next** | none yet; builds are B21+ on `build/b##`, tags `phase4-b##` |
| 5 Maintain | Not started (the product-completion boundary) | |
| 6-8 | Research horizon | |

## Phase 4 v1 boundary (approved 2026-11-07)
In: same-volume move/rename of single files, removal into quarantine, one metadata writer, undo for each, local
NTFS fixed drives, collision policy "fail" only. Out (stay blocked, with the reason shown): cross-volume and
cloud-root moves, files that are not fully local, other collision policies, folders as movable units, network
shares and removable drives, templates.

## Build order (approved 2026-11-07)
| Build | Content |
|---|---|
| B21 | Action lock; journal; job/item state machine (fake action, fault injection; touches no real file) |
| B22 | Same-volume move/rename primitive, with just-in-time preflight; sandbox only |
| B23 | Remove Location into quarantine, with undo |
| B24 | Move / rename plan items, with location succession |
| B25 | Live preflight, run, verify and undo screens |
| B26 | The first certified metadata writer (one format, one field) |
| B27 | Adversarial round, a real-use session on a copy, the closeout |

Review gate 2 (a different-model design review of any code that moves, renames, deletes or writes) falls before
B22 and before B26.

## Research status (Phase 4 gap-fill)
- Scout lookups: done, 7 of 7 answered (`Handoffs/Phase4/R4_SCOUT_ANSWERS.md`).
- Five research briefs: **not run**. `Docs/Research/phase_4/R4/` is empty.
- Not research: R4.7 (partial-execution contract), R4.9, R4.11 and R4.12 are Architect answers in the synthesis.

## Tests at the base (`phase3-b20` code)
**Quick tier, 2026-11-01:** `Scripts\run_suites.ps1 -Tier quick`: **20 of 20 suites exited 0, 1,585 checks
passed.** Not re-run since. Standard and full tiers (mutation testing 69/69 killed, `b6_regression` 73/73) were last
run for the B20 handback.

## Known open items
- **A-014** (a case-only pair in a case-sensitive folder folds into one row) is kept by decision, 2026-09-20.
- **OneDrive placeholder cases** (L-001, L-002, L-006) have never been exercised; R4.6 plans a hand probe.
- **Stale plan text.** Root `PROJECT_PLAN.md` §6 "After B20" still calls §5a empty and B20 uncommitted. Fix it at
  the next sync.
- **Do not push local `phase2` as it is.** In the main clone it points at B10 code, ahead of the remote `phase2`.
- **Owner decisions pending:** fast-forward `main`; delete local `phase3-bNN` branches whose names match tags (use
  `refs/tags/...` when you mean the tag); slim `PROJECT_PLAN.md`; archive old build folders and duplicate documents;
  a different-model review of P3.R12 (combine with review gate 1).

## How work runs
See `AGENTS.md` for the team, the models, the branches and the rules. One planner owns the plan at a time.
- **Run the tests:** `Scripts\run_suites.ps1`.
- **Release a build:** `Scripts\fo_release.ps1`, dry run first.
- **Write a handoff:** start from `Handoffs/HANDOFF_TEMPLATE.md`.
