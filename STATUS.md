# FileOrganizer: status (one page)

**Updated:** 2026-11-07 · **Keep this current at every release and at the end of every session.** The canonical
plan is `PROJECT_PLAN.md` (workspace root); this page only says where things stand.

## Where we are
**Phase 4 (Act and Verify) is approved to start. Nothing is built yet.** On 2026-11-07 the owner approved the v1
boundary and the build order B21-B27 in `Handoffs/Phase4/PHASE4_SCOPING.md`.

**Next step:** the B21 handoff is written (`Handoffs\B21\B21_HANDOFF.md`, uncommitted). On the owner's yes the
steward files it on `phase4` and makes `build/b21`; then a `fo-builder` session on Sonnet builds units 1-4 and an
Opus session units 5-6. In parallel, the owner runs the extended hand probe (R4.6 §6 steps 1-8,
`R4_REVIEW_GATE1.md` §6 steps 9-15, `R4.13_report.md` §7 steps 16-23; needed before B22).

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
NTFS fixed drives, collision policy "fail" only; **rename in place inside a OneDrive sync root for files verified
fully local at the moment of action, after the probes pass (D1); moves between folders there after brief R4.13
and its probe**; creating missing destination folders as approved steps (D8). Out (stay blocked, with the reason
shown): any deletion by the product (D6), moves out of a sync root, Recycle Bin, metadata writes inside a sync
root, cross-volume moves, files that are not fully local, hard-linked files, other collision policies, folders as
movable units, network shares and removable drives, templates.

## Build order (approved 2026-11-07)
| Build | Content |
|---|---|
| B21 | Action lock; journal; job/item state machine (fake action, fault injection; touches no real file). **Handoff written 2026-11-07** |
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
- Synthesis: `Handoffs/Phase4/PHASE4_SYNTHESIS.md` (committed `985366f`; §12 amendments uncommitted): R4.7, R4.9,
  R4.11, R4.12 answered; final B21-B27 plan.
- **Review gate 1 held 2026-11-07**: ChatGPT and Gemini (independent) plus a Claude Sonnet pass. Disposition
  `Docs\Research\phase_4\R4\R4_REVIEW_GATE1.md`: 24 findings accepted, 4 partially, none rebutted outright. The
  five faults both independent reviewers found (cross-database commit gap, sticky fulfilment, destination bound by
  path, hidden quarantine unsynced in OneDrive, purge vs axiom 11) are all fixed in the design (synthesis §12).
- Brief `Handoffs\Phase4\B-R4.13.md` (OneDrive shared folders, shortcuts, Vault, naming limits, pin inheritance):
  **done 2026-11-07**, `Docs\Research\phase_4\R4\R4.13_report.md` (42 KB), adopted in synthesis §12 with owner
  decisions D10 (never touch shared folders or shortcuts) and D11 (the owner's per-job statement stands in where
  Windows cannot prove a folder private). Its probes (owner hand probes 16-23, B22 U1) are still to run; until they
  pass, inside OneDrive only rename-in-place is allowed.
- Decision log `Docs\DECISIONS.md` (outside the repo): **D1-D9 all Settled 2026-11-07.** D1 OneDrive intra-root
  actions on verified-local files (narrowed to rename in place pending R4.13); D2/D7 a visible synced quarantine
  folder per root; **D6 no purge in Phase 4, the product never deletes**; D3 PDF title by `pypdf` incremental;
  D4 journal local and never synced; D5 gate 1 scope; **D8 moves may create folders as approved steps**; D9 the
  PDF modified date is left as written; **D10 never touch shared folders or shortcuts; D11 the owner's per-job
  statement where Windows cannot prove a folder private** (D10-D11 added after R4.13).

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
