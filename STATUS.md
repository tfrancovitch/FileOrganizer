# FileOrganizer: status (one page)

**Updated:** 2026-11-08 · **Keep this current at every release and at the end of every session.** The canonical
plan is `PROJECT_PLAN.md` (workspace root); this page only says where things stand.

## Where we are
**Phase 4 (Act and Verify): B21 is built, reviewed and released** as `phase4-b21` (approved by the owner
2026-11-08). B21 is the safety core: the action journal, the project and root locks, the job and item state machine,
the projection of journal events into the catalog, and the reconciliation tables. **It changes no real file**; its
only action is a fake one on a fake filesystem inside a test sandbox. Handback: `PHASE4_B21_HANDBACK.md`; changes:
`CHANGELOG-B21.md`; design: `ARCHITECTURE.md`, section "Phase 4 (B21): journal, locks, jobs".

**Next step:** the owner's extended hand probe on throwaway files (R4.6 §6 steps 1-8, `R4_REVIEW_GATE1.md` §6
steps 9-15, `R4.13_report.md` §7 steps 16-23), and the lock and journal probes that need a person or a VM (L2, L5,
L6, L7, J2, J3; steps in the B21 handback). Both are needed before B22. Then review gate 2 (a different-model design
review of the move/rename primitive) and the B22 handoff.

| Phase | Status | Final build / tag |
|---|---|---|
| 1 Observe | Shipped | B6.2 · `phase1-b6.2` |
| 2 Understand | Shipped; closeout approved 2026-09-19 | B7.2 · `phase2-b7.2` |
| 3 Decide / Plan | **Closed 2026-10-13** by the owner (`Docs\Handoffs\P3_CLOSEOUT.md` §5a) | B20 · `phase3-b20` = `045a0a1` |
| 4 Act and Verify | **In progress: B21 released** | B21 · `phase4-b21`; builds on `build/b##`, tags `phase4-b##` |
| 5 Maintain | Not started (the product-completion boundary) | |
| 6-8 | Research horizon | |

## Phase 4 v1 boundary (approved 2026-11-07; amended by D1 the same day)
In: same-volume move/rename of single files, removal into quarantine, one metadata writer, undo for each, local
NTFS fixed drives, collision policy "fail" only; **rename in place inside a OneDrive sync root for files verified
fully local at the moment of action, after the probes pass (D1); moves between folders there after brief R4.13
and its probe**; creating missing destination folders as approved steps (D8). Out (stay blocked, with the reason
shown): any deletion by the product (D6), moves out of a sync root, Recycle Bin, metadata writes inside a sync
root, cross-volume moves, files that are not fully local, hard-linked files, other collision policies, folders as
movable units, network shares and removable drives, templates, shared folders and shortcuts (D10).

## Build order (approved 2026-11-07)
| Build | Content |
|---|---|
| B21 | Action lock; journal; job/item state machine (fake action, fault injection; touches no real file). **Released 2026-11-08** |
| B22 | Same-volume move/rename primitive, with just-in-time preflight; sandbox only |
| B23 | Remove Location into quarantine, with undo |
| B24 | Move / rename plan items, with location succession |
| B25 | Live preflight, run, verify and undo screens |
| B26 | The first certified metadata writer (one format, one field) |
| B27 | Adversarial round, a real-use session on a copy, the closeout |

Review gate 2 (a different-model design review of any code that moves, renames, deletes or writes) falls before
B22 and before B26.

## Research and decisions
- Research for Phase 4 is done: scout lookups 7/7, reports R4.1/.2/.3/.6/.8 and R4.13 in
  `Docs/Research/phase_4/R4/`; synthesis `Handoffs/Phase4/PHASE4_SYNTHESIS.md` (§12 holds the gate-1 amendments and
  wins over earlier sections). Review gate 1 held 2026-11-07 (ChatGPT, Gemini, Claude); disposition
  `Docs\Research\phase_4\R4\R4_REVIEW_GATE1.md`.
- Decision log `Docs\DECISIONS.md` (outside the repo): **D1-D11 Settled**, plus the B21 build rulings (the breaker
  rule max(3, ceil(10 % of items)); the builder's questions Q-1..Q-6 answered as built). None open.

## Tests (`phase4-b21`)
**Standard tier, 2026-11-08** (`Scripts\run_suites.ps1 -Tier standard`, the builder's run): **26 of 26 suites exited
0.** The Phase 1-3 suites at their base counts (quick-tier Phase 1-3 suites 1,585 checks); `p3_mutation_check`
69/69 mutants killed; `b6_regression` 73/73. The new suites, re-run by the Architect: `p4_journal_check` 291/291,
`p4_lock_check` 76/76, `p4_mutation_check` 23/23 mutants killed (controls 15/15, the real tree untouched). No test
sandbox left; `%LOCALAPPDATA%\FileOrganizer\` never created. `Test-Installation.ps1`: manifest verified (249 rows).
Schema 17 (migration 017 adds one table, `p4_journal_binding`).

## Known open items
- **Not run yet:** lock and journal probes L2, L5, L6, L7, J2, J3 (need a person, a second account or a VM);
  L9 and L10 (OneDrive, SMB) are never run by tests. The owner's hand probes 1-23. All before B22.
- **A-014** (a case-only pair in a case-sensitive folder folds into one row) is kept by decision, 2026-09-20.
- **OneDrive placeholder cases** (L-001, L-002, L-006) have never been exercised; the hand probe covers them.
- **Stale plan text.** Root `PROJECT_PLAN.md` §6 "After B20" still calls §5a empty and B20 uncommitted. Fix it at
  the next sync.
- **Do not push local `phase2` as it is.** In the main clone it points at B10 code, ahead of the remote `phase2`.
- **Owner decisions pending:** fast-forward `main`; delete local `phase3-bNN` branches whose names match tags (use
  `refs/tags/...` when you mean the tag); slim `PROJECT_PLAN.md`; archive old build folders and duplicate documents.

## How work runs
See `AGENTS.md` for the team, the models, the branches and the rules. One planner owns the plan at a time.
- **Run the tests:** `Scripts\run_suites.ps1`.
- **Release a build:** `Scripts\fo_release.ps1`, dry run first.
- **Write a handoff:** start from `Handoffs/HANDOFF_TEMPLATE.md`.
