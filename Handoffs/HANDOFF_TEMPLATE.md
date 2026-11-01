# Phase 4 handoff: B## (<one-line title>)

**From:** the Architect · **Date:** YYYY-MM-DD · **For:** `fo-builder`
**Base:** branch `phase4` at `<commit>` (after `phase4-b##` was released)
**Build branch / worktree:** `build/b##` in `C:\FileOrganizerTesting\FileOrganizer-Phase4-B##\` (made by the steward)
**Handback goes to:** `PHASE4_B##_HANDBACK.md` at the worktree root; `CHANGELOG-B##.md` beside it.

## Short version
Three to five lines: what this round builds, why now, and the one thing most likely to go wrong.

## Recommended models
| Session or unit | Model | Why |
|---|---|---|
| Build session (`fo-builder`) | sonnet | default |
| Unit N (e.g. the journal's crash recovery) | opus | safety-critical; start that session on Opus |
| Suite runs (`fo-runner`) | haiku | |
| Lookups (`fo-scout`) | haiku | |
| Adversary round (`fo-adversary`), if this build has one | sonnet / opus | |

Escalate only as this table allows. Record any escalation, and why, in the handback.

## 1. Expectations to check first
What the builder must find true before building. Each one that fails is reported in the handback's §1.
Examples: the base commit; schema version; versions in `fo_db.APP_VERSION` / `Phase2.VERSION`; suite counts at
the base; manifest entries.

## 2. Decisions already made (do not reopen)
Each with its date and source: Thomas, a closeout section, a dev note.

## 3. Units, in priority order
A cut-off session must still have delivered the most important units.

| # | Unit | Done when (testable) | Tests / fixtures / mutants | Model |
|---|---|---|---|---|
| 1 | | | | |

## 4. Rules for this round
- The sandbox rule (`AGENTS.md`). Name the sandbox root this round's tests use.
- Anything the handoff adds: forbidden paths, a precondition that must stay a hard block, and so on.

## 5. Suites
Which tier the builder runs at the end (`Scripts\run_suites.ps1 -Tier ...`); any `b6_regression` repeat count;
new suites this round adds. Minimum counts: none below the base's.

## 6. Handback format
A short version first. Then:
- §1 expectations;
- an item-by-item table against §3, with evidence in the same row;
- judgment calls;
- the receipts: suite table, manifest, versions;
- unverified items;
- helpers used, with their models.

## 7. Out of scope
What this round must not touch, and which later round owns it.

## 8. Questions that must stop the build
Situations in which the builder stops and asks instead of choosing.
