# Handoff: the FileOrganizer Architect, start of Phase 4

**For:** a new Claude Cowork chat on Opus, acting as the **Architect**.
**From:** a Claude Code session, 2026-11-01, at Thomas's request.
**Context folders the chat needs:** `C:\FileOrganizerTesting` and `C:\Dev\dev-notes`.

## Short version
Phase 3 is closed and pushed. Your job in this chat is **Phase 4 scoping**:
- the research questions;
- the research itself, or its commissioning;
- the synthesis;
- the build sequence;
- the B21 handoff.

You plan and review; you never write product code. Phase 4 is the first phase whose code changes the user's
files, so it carries the most risk in the project.

## 1. Who you are and how the team works
- **Your role.** You are the Architect of the team described in the repository's `AGENTS.md`
  (`C:\FileOrganizerTesting\FileOrganizer-Phase4-Base\AGENTS.md`). Read it.
- **Cheaper agents do the rest:**
  - building: `fo-builder` (Sonnet);
  - test runs: `fo-runner` (Haiku);
  - lookups: `fo-scout` (Haiku);
  - breaking things: `fo-adversary`;
  - bookkeeping and releases: `fo-steward` (Haiku);
  - research: `fo-researcher` (Sonnet).
- **Cowork cannot run git or the suites on this machine.** When you need a receipt (a count, a hash, a commit),
  write the exact request and ask Thomas to start a Claude Code session in the right worktree to run it, usually
  as `fo-runner` or `fo-steward`.
- **Cost.** The plan is $20/month. Spend Opus on judgment:
  - read files by section, not whole;
  - in every handoff, name a model for each session and unit;
  - use the "Cost and model use" rules in `C:\Dev\dev-notes\AGENTS.md`.

## 2. Read first, in this order (and nothing else until a task needs it)
1. `C:\Dev\dev-notes\AGENTS.md`, `INDEX.md`, and the newest FileOrganizer note
   (`FileOrganizer\20261101-phase3-closed-pushed.md`).
2. `C:\FileOrganizerTesting\FileOrganizer-Phase4-Base\STATUS.md`: one page, where things stand.
3. `C:\FileOrganizerTesting\FileOrganizer-Phase4-Base\AGENTS.md`: the team, branches, rules.
4. `C:\FileOrganizerTesting\Docs\Handoffs\P3_CLOSEOUT.md`, **§5a, §6 and §7 only**. §7 is the Phase 4 seed:
   - what a locked plan guarantees;
   - what Phase 4 must re-verify live;
   - what stays blocked;
   - the four pre-Phase 4 engineering items.
5. `C:\FileOrganizerTesting\PROJECT_PLAN.md`, **§2 (the 19 axioms) and §3 (the phase table) only.**
   It is 90 KB; do not read it whole.

## 3. Where things stand (2026-11-01)
- **Phases 1-3 are done.**
  - Phase 1 (Observe) shipped as B6.2.
  - Phase 2 (Understand) shipped as B7.2.
  - Phase 3 (Decide / Plan) was closed by Thomas on 2026-10-13. Its final build, B20, is commit `045a0a1`,
    tag `phase3-b20`.
- **GitHub:** `tfrancovitch/FileOrganizer` (public).
  - Branch `phase3` and tags `phase3-b8` … `phase3-b20` are pushed.
  - Branch `phase4` holds this tooling.
- **Phase 3's output, Phase 4's input:** a locked, fingerprinted plan of logical intent. Its items include:
  - Remove Location;
  - Move / Rename (`desired_location`, `desired_name`);
  - Update Source Metadata.

  Every item is held blocked, because no executor or writer is certified.
- **Carried into Phase 4 (closeout §5a.5 and §7):**
  - a cross-process action lock;
  - the action journal's transaction boundary;
  - writer certification, per format and field;
  - a certified move / rename executor;
  - location succession and decision fulfilment;
  - the keeper-move ordering rule;
  - the case-only rename method;
  - collision policies other than fail;
  - cross-volume and cloud-root moves;
  - whether `no_dangling_protection` is re-checked live;
  - a Phase 1 action to remove a source root.
- **Test suites:** fresh results are in `STATUS.md`.

## 4. Your task in this chat
1. **Phase 4 research questions.** Draft them as R4.1, R4.2, … Each gets a decision it informs and a
   "done when". Candidates:
   - what Windows actually guarantees for rename, move, replace and delete:
     - same volume vs cross-volume;
     - case-only renames;
     - long paths;
     - attributes, timestamps, ACLs and alternate data streams preserved or lost;
   - **where a removed file goes:** the Recycle Bin or an app-owned quarantine folder, and how each is undone
     (axiom 11);
   - **the action journal:**
     - write-ahead order;
     - the transaction boundary;
     - crash recovery and idempotent replay;
     - verification after each action (postconditions);
   - the cross-process action lock;
   - the live preflight;
   - OneDrive Files On-Demand: detecting placeholders without hydrating them (axiom 14);
   - the partial-execution contract (what "done" means when step 7 of 20 fails);
   - how one metadata writer is certified (one format, one field).
2. **Commission the research.**
   - Either write briefs for `fo-researcher` (Sonnet) that Thomas runs in Claude Code, or research yourself in
     this chat where that is cheaper.
   - Each report ends with a source ledger.
3. **Write the Phase 4 synthesis and the build sequence.** A provisional sequence, yours to change:

   | Build | Content |
   |---|---|
   | B21 | Lock and journal |
   | B22 | Remove Location into quarantine, with undo |
   | B23 | Move and rename, with succession |
   | B24 | Live preflight, verify and undo screens |
   | B25 | The first certified metadata writer |
   | B26 | Adversarial round, then a real-use session on a copy, then the closeout |
4. **Review gate 1.** Ask Thomas to put the synthesis to a model from a different family (ChatGPT or Gemini,
   free tier). Record what it raised and your response.
5. **Write the B21 handoff** from `Handoffs\HANDOFF_TEMPLATE.md`, into
   `C:\FileOrganizerTesting\FileOrganizer-Phase4-Base\Handoffs\B21\`. Name the models. The steward files it
   on Thomas's yes; then a build session starts.

## 5. Decisions waiting for Thomas
Bring each with options and a recommendation, one at a time:
- **Removed files:** where they go, the Recycle Bin or a quarantine folder. Yours to recommend from R4.
- **`PROJECT_PLAN.md`:** whether to move its build history out to `Docs\History\`, leaving a lean plan.
  Recommended earlier.
- **Old branches:**
  - whether to push the local `phase2` (3 commits ahead; the objects are already on GitHub through `phase3`);
  - whether to fast-forward `main`;
  - whether to delete the local `phase3-bNN` branches whose names match tags.
- **Second opinion on P3.R12:** whether to get a different-model review of the research before Phase 4 relies
  on it.

## 6. Rules you hold the builders to
- **The axioms**, above all:
  - 10: changing metadata or a name is an action;
  - 11: every Phase 4 action is reversible;
  - 13: completeness is a property;
  - 14: never hydrate cloud content;
  - 17: no AI is the action authority.
- **The sandbox rule** in `AGENTS.md`: Phase 4 code is exercised only on throwaway roots. Real files are acted on
  only by Thomas.
- **Committing, tagging and pushing are Thomas's call.** Releases go through `Scripts\fo_release.ps1` (dry run
  first).

## 7. How Thomas likes to work
- Short version first, then the one decision you need from him, then detail.
- Plain words: he is not a programmer, so explain jargon the first time.
- Plan first, then build.
- Receipts over claims; "unverified" said plainly.
- He has ADHD and may not answer everything at once.

## 8. End of the chat
Write a dev note in `C:\Dev\dev-notes\FileOrganizer\` from `_templates\dev-note.md`, and add one line at the
top of the log in `INDEX.md`.
