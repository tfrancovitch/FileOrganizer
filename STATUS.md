# FileOrganizer: status (one page)

**Updated:** 2026-11-07 · **Keep this current at every release and at the end of every session.** The canonical
plan is `PROJECT_PLAN.md` (workspace root); this page only says where things stand.

## Where we are
**Phase 4 (Act and Verify) is approved to start. Nothing is built yet.** On 2026-11-07 the owner approved the v1
boundary and the build order B21-B27 in `Handoffs/Phase4/PHASE4_SCOPING.md`.

**Next step:** review gate 1. The owner pastes `Docs\Research\phase_4\R4\R4_REVIEW_GATE1_BRIEF.md` and the files
it names to ChatGPT or Gemini; the Architect records the review in `R4_REVIEW_GATE1.md`, amends the synthesis if
needed, and writes the B21 handoff. In parallel the owner can run the R4.6 hand probe (needed before B22, not B21).

| Phase | Status | Final build / tag |
|---|---|---|
| 1 Observe | Shipped | B6.2 · `phase1-b6.2` |
| 2 Understand | Shipped; closeout approved 2026-09-19 | B7.2 · `phase2-b7.2` |
| 3 Decide / Plan | **Closed 2026-10-13** by the owner (`Docs\Handoffs\P3_CLOSEOUT.md` §5a) | B20 · `phase3-b20` = `045a0a1` |
| 4 Act and Verify | **Approved; research next** | none yet; builds are B21+ on `build/b##`, tags `phase4-b##` |
| 5 Maintain | Not started (the product-completion boundary) | |
| 6-8 | Research horizon | |

## Phase 4 v1 boundary (approved 2026-11-07; amended by D1 the same day)
In: same-volume move/rename of single files, removal into quarantine, one metadata writer, undo for each, local
NTFS fixed drives, collision policy "fail" only; **rename and move inside the same OneDrive sync root for files
verified fully local at the moment of action, after the probes pass (D1)**. Out (stay blocked, with the reason
shown): moves out of a sync root, delete or Recycle Bin inside one, cross-volume moves, files that are not fully
local, other collision policies, folders as movable units, network shares and removable drives, templates.

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
- Five research briefs: **done 2026-11-07**, reports `R4.1/.2/.3/.6/.8_report.md` in `Docs/Research/phase_4/R4/`
  (59 / 31 / 44 / 42 / 29 KB), each with a source ledger.
- Synthesis: **written**, `Handoffs/Phase4/PHASE4_SYNTHESIS.md` (uncommitted): R4.7, R4.9, R4.11, R4.12 answered;
  decisions D1-D5; final B21-B27 plan. Verdict: no further desk research; remaining unknowns are probes in B21/B22
  and one Thomas-run OneDrive probe. Review gate 1 not yet run.
- Decision log started: `Docs\DECISIONS.md` (outside the repo). **D1-D5 all Settled 2026-11-07**, as recommended:
  OneDrive intra-root moves of verified-local files in; quarantine per root with a 30-day purge guard; PDF title by
  `pypdf` incremental; journal in a local never-synced folder; gate 1 also covers P3.R12 and the plan contract.

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
  `refs/tags/...` when you mean the tag); slim `PROJECT_PLAN.md`; archive old build folders and duplicate documents.
  (The P3.R12 second opinion is now part of review gate 1, by decision D5.)

## How work runs
See `AGENTS.md` for the team, the models, the branches and the rules. One planner owns the plan at a time.
- **Run the tests:** `Scripts\run_suites.ps1`.
- **Release a build:** `Scripts\fo_release.ps1`, dry run first.
- **Write a handoff:** start from `Handoffs/HANDOFF_TEMPLATE.md`.
