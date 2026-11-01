# FileOrganizer: status (one page)

**Updated:** 2026-11-01 · **Keep this current at every release.** The canonical plan is `PROJECT_PLAN.md`
(workspace root); this page only says where things stand.

## Where we are
**Phase 4 (Act and Verify) is being scoped.** Nothing is built yet. Next step: the Architect's scoping chat.
The brief for it is `Handoffs/PHASE4_ARCHITECT_START.md`.

| Phase | Status | Final build / tag |
|---|---|---|
| 1 Observe | Shipped | B6.2 · `phase1-b6.2` |
| 2 Understand | Shipped; closeout approved 2026-09-19 | B7.2 · `phase2-b7.2` |
| 3 Decide / Plan | **Closed 2026-10-13** by the owner (`Docs\Handoffs\P3_CLOSEOUT.md` §5a) | B20 · `phase3-b20` = `045a0a1` |
| 4 Act and Verify | **Scoping** | none yet; builds will be B21+ on `build/b##`, tags `phase4-b##` |
| 5 Maintain | Not started (the product-completion boundary) | |
| 6-8 | Research horizon | |

## What Phase 4 starts from
A locked, fingerprinted Phase 3 plan whose items (Remove Location, Move / Rename, Update Source Metadata) are
all held **blocked**, because no executor or writer is certified. The closeout's §7 is the seed: what Phase 4
must re-verify live, and four engineering items:
- the cross-process action lock;
- the action journal's transaction boundary;
- writer certification;
- a certified move / rename executor.

**Provisional build sequence**, which the Architect's scoping will revise:

| Build | Content |
|---|---|
| B21 | Lock and journal |
| B22 | Remove Location into quarantine, with undo |
| B23 | Move / rename, with location succession |
| B24 | Live preflight, verify and undo screens |
| B25 | The first certified metadata writer |
| B26 | Adversarial round, a real-use session on a copy, the closeout |

## Tests at the base (`phase3-b20` code)
**Quick tier, 2026-11-01:** `Scripts\run_suites.ps1 -Tier quick` on `phase3-b20` code: **20 of 20 suites
exited 0, 1,585 checks passed**, among them:
- `p3_regression` 348/348;
- `p3_organize_check` 138/138;
- `p3_visual_check` 138/138;
- `p3_metadata_check` 133/133;
- `p3_fixture_check` 101/101;
- `p3_registry_schema_check` 33/33.

**Not re-run on 2026-11-01:** the standard and full tiers (`p3_mutation_check`, `b6_regression`,
`p2_dashboard_check`, and the `C:\FOTest` suites). Their last results, from the B20 handback:
- mutation testing: 69/69 mutants killed;
- `b6_regression`: 73/73, five runs in a row.

## Known open items
- **A-014** (a case-only pair in a case-sensitive folder folds into one row) is kept by decision, 2026-09-20.
- **OneDrive placeholder cases** (L-001, L-002, L-006) have never been exercised. They need a "Free up space"
  file in the real corpus.
- **Stale plan text.** Root `PROJECT_PLAN.md` §6 "After B20" still calls §5a empty and B20 uncommitted. Fix it
  at the next sync.
- **Owner decisions pending:**
  - whether to push the local `phase2` branch;
  - whether to fast-forward `main`;
  - whether to delete the local `phase3-bNN` branches whose names match tags;
  - whether to slim `PROJECT_PLAN.md`;
  - a different-model review of P3.R12.

## How work runs
See `AGENTS.md` for the team, the models, the branches and the rules.
- **Run the tests:** `Scripts\run_suites.ps1`.
- **Release a build:** `Scripts\fo_release.ps1`, dry run first.
- **Write a handoff:** start from `Handoffs/HANDOFF_TEMPLATE.md`.
