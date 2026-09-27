# Architecture — The File Organizer, B18 (Phases 1–2 shipped, Phase 3 built; closeout drafted)

This describes the software as it is. It is not a history of how it got here.
The body below is Phase 1's architecture as B6.1 left it, still accurate for
the engines; the **Phase 2** section (the one window, the query core, the
current-duplicate projection) and the **Phase 3** section (decisions,
routing, bulk, similar images, bulk over pairs, Survey Mode, the incremental
refresh, the Plan Builder, metadata and its page, the closeout round) at the end say what B7 and B8–B18 added on top;
the Phase 3 section also holds its written state machines, the path-matching rule and the plan fingerprint.

---

## The shape of it

```
        TheFileOrganizer.bat          finds/installs Python, launches the app
                  |
             Dashboard.py             startup checks, then hands off to
                  |                   Phase2\gui.py -- the one window (B7)
          RunCoordinator.py           runs, stages, events, the DB session
                  |
   +--------------+--------------+------------------+
   |              |              |                  |
 inventory      hash &        analyzer          project /
 engine        duplicate       runtime          estimates
   |            engine            |                  |
   +--------------+--------------+------------------+
                  |
          FileOrganizer.db           SQLite, schema 12 -- AUTHORITATIVE
                  |
             fo_exports.py           renders every CSV and report FROM the DB
                  |
        Inventory\*.csv  Reports\*.txt
```

**Python is the sole database writer.** Nothing else opens the database for
writing — not the GUI, not an analyzer, not a script.

**The database is the authoritative state. Every CSV and report is an export**,
rendered from it on demand. They are outputs, never inputs: no stage reads a CSV
another stage wrote. That is what keeps an export from silently disagreeing with
the record it claims to describe.

---

## One project, one database, one or more source roots

```
    ONE PROJECT
        -> ONE ISOLATED PROJECT DATABASE
            -> ONE OR MORE SOURCE ROOTS
```

A project owns exactly one database, stored inside the project folder. Projects
never read each other's data, and **content identity is never merged across
projects** — two projects that happen to contain the same file each record that
fact independently.

A project may cover several source roots, chosen at creation time and stored in
`settings.json` as `SourceRoots`. A Pre-Scan walks **every** active root into
the same run, creating one `inventory_scan` per root, and the hash and analyzer
engines bind to every scan of that run — which is what allows a duplicate group
to span roots.

`TargetPath` also appears in `settings.json`. It is a **compatibility mirror of
the first root**, not the source of truth: project persistence helpers
resolve a project through a single path, and rewriting them was a larger change
than this consolidation warranted. `SourceRoots` is authoritative.

The identity chain keeps roots distinct:

```
    source root  ->  file path  ->  file observation  ->  content identity
```

Two identical files under different roots are two *locations* resolving to one
*content identity*. Duplicate membership derives from complete content
identity — never from filename or size.

B6.1 adds a third distinction needed for later safe remediation: **physical
object identity**. Where the filesystem exposes it, the current/observation
record also carries volume/device identity, file index/inode, hard-link count
and allocated size. Multiple paths can therefore be recognised as aliases of
one physical object rather than over-counted as independent reclaimable copies.
These fields are environment-derived facts, not replacements for SHA-256.

---

## The engines

### Inventory (`fo_scan.py`, `fo_inventory_records.py`)

Walks each source root, capturing size, timestamps, attributes, depth, path
length, physical identity where available and Windows metadata. New/changed
states become `file_observation` history; every verified location refreshes its
`file_state` current projection. Empty directories and deliberately skipped
reparse directories are recorded as scan coverage events. Inaccessible coverage
is explicit and any storage cap is recorded as truncated rather than silent.

### Hash and duplicates (`fo_hash_engine.py`, `fo_hash_records.py`)

Four stages:

1. **Size candidates** — group by exact byte length; a unique size cannot be a
   duplicate, so those files are set aside.
2. **Partial hash** — SHA-256 over the first 64 KB. Re-group by
   `(size group, partial hash)`.
3. **Escalation** — a bucket whose members all fit inside the window is already
   settled. Any bucket containing a larger file goes to a full SHA-256.
4. **Confirmation** — regroup by complete digest.

> **A partial hash counts as complete content identity only when the entire
> file fits inside the 64 KB window.** For anything larger, partial equality is
> a screening result and never proof.

Full hashing streams in bounded chunks. File bodies are never held in memory, so
a million-file project is a million small records, not a million file bodies.

**Full Run** is a separate workflow: a complete SHA-256 for every file, with its
own vocabulary — `UniqueByHash` means *hashed, and nothing matched*, a stronger
claim than `UniqueBySize`, which means *never hashed, because nothing shared its
size*.

### Analyzers (`fo_analyzer_engine.py`, `fo_analyzer_records.py`)

Nine analyzers run **in-process**, in sequence, each isolated:

| | |
|---|---|
| image | perceptual hashes, dimensions |
| pdf | page count, metadata, text presence |
| office | Word / Excel / PowerPoint properties |
| raw_image | EXIF from RAW photos |
| audio | tags and duration (ffprobe) |
| video | streams and duration (ffprobe) |
| text | encoding, word and line counts |
| archive | contents listing, per-entry rows |
| content_extraction | document text written to `Inventory\ExtractedText\` |

Isolation is explicit, because one process no longer provides it for free:

- a corrupt file fails **its own row**, carrying an error and no invented data;
- an analyzer that raises, or whose library is missing, fails **only itself**;
- the remaining analyzers still run.

**Zero applicable files is a success.** `no_applicable_files` is a real
completed run with no results and no CSV — distinct from `failed`, and distinct
from an empty successful result set. A project containing no videos is not a
project where video analysis went wrong.

`extracted_content` holds **references, counts and content hash/provenance**.
Extracted text lives in a content-addressed sharded store on disk; identical
extracted text is reused rather than duplicated by source path.

---

## Database

Schema **`user_version = 16`**, created by `fo_db.py` applying migrations
`001`–`016`. Migration 006 introduced current-state separation; migration 007
completes the A–F reconciliation/current projection; 008 is Phase 2's core
(saved queries, retained executions, the derived-index registry, the current
exact-duplicate projection, the FTS map); 009 is Phase 3's decision record;
010 its review events; 011 its bulk batches; 012 the visual candidate pairs
(Phase 2 evidence: the image hashes, compared); 013 the plans; 014 the metadata
capability record and questions; 015 `analyzer_result.signature_state`; 016
`analyzer_result.encryption_state` (both Phase 2 evidence).

| Table group | Holds |
|---|---|
| `project`, `source_root`, `run`, `run_stage`, `event` | identity and history |
| `inventory_scan`, `file_path`, `file_observation`, `file_state` | scan history, locations and current state |
| `content`, `hash_measurement` | content identity and what was measured |
| `duplicate_run`, `duplicate_group`, `duplicate_member` | what a run concluded |
| `analyzer`, `analyzer_run`, `analyzer_result` | analyzer outcomes |
| `archive_member`, `archive_summary`, `extracted_content` | bounded analyzer detail |
| `scan_path_event` | empty/skipped directory coverage facts |
| `p2_saved_query`, `p2_saved_query_revision`, `p2_execution_record` | Phase 2: saved questions, retained executions |
| `p2_derived_index`, `p2_current_duplicate_member`, `p2_current_duplicate_summary`, `p2_fts_text_map` | Phase 2: rebuildable projections and indexes |
| `p3_operation`, `p3_decision`, `p3_decision_withdrawal`, `p3_policy`, `p3_policy_version` | Phase 3: the append-only decision record |
| `p3_review_event` | Phase 3: routing provenance — deferrals with return triggers, skips, what the detector found, restores |
| `p3_bulk_batch`, `p3_bulk_member` | Phase 3: a bulk batch — its scope, frozen query, the preview as confirmed, and every member with what became of it |
| `visual_candidate_generation`, `visual_candidate` | Phase 2 evidence (B11): each comparison of the image hashes, and every pair of images within the threshold — identity = the two file locations, kept across generations; `current` = the latest generation found it |

Analyzer results keep **normalised promoted columns** (title, author,
dimensions, duration, word count) *plus* the analyzer's own JSON detail. The
promoted columns make cross-analyzer queries possible; the JSON keeps whatever
that particular analyzer knew that the shared columns cannot express.

Every run's conclusions are stored **as that run concluded them**, while
content-derived grouping stays available at any time by grouping on
`content_id`. Keeping both is what lets the two be compared.

---

## Windows specifics

### Long paths

Paths beyond `MAX_PATH` work with `LongPathsEnabled = 0`. The `\\?\` prefix is
applied **at the file-open boundary and nowhere else** — `win_meta.to_extended_path`,
called immediately before an open.

**No stored, displayed or exported path ever carries the prefix.** Records hold
ordinary Windows paths; the prefixed form exists for the duration of one system
call.

That includes **error text**. Some third-party libraries quote the path they
were given back in the exception they raise, so a diagnostic can pick up an
internal representation the caller never chose. Analyzer error text therefore
passes through one normalization boundary — `fo_analyzer_engine.
normalize_diagnostic_text` — before it reaches an `AnalyzerResult`, the
database, a CSV or the screen. The failure itself is untouched: same file, same
message, still an error; only the path representation becomes the ordinary one.

### Stage keys

`run_stage.stage_key` uses names like `PreliminaryInventory.ps1`,
`PartialHash.ps1` and `ImageAnalysis.ps1`.

**These are stable identifiers, not references to files.** The scripts they were
named after no longer exist; the names remain because they appear in every
recorded run, in exports, and in stored history. Renaming them would require
rewriting historical rows for a cosmetic gain, so they are treated as opaque
strings and left alone.

### PowerShell

Two scripts remain, and both earn it by needing to work when **Python cannot**:

| Script | Why it is not Python |
|---|---|
| `Install-Dependencies.ps1` | installs Python packages, and unblocks files after a zip download — a Python script cannot fix a broken Python |
| `Test-Installation.ps1` | verifies the installation is intact before the app starts, including when the Python side is what is broken |

Everything else the program does at runtime is Python.

---

## Source safety

Inventory, hashing and analysis are **reads**. The runtime performs no source
writes, renames, moves, deletes, attribute changes or metadata edits.

Reading content may cause Windows to update **LastAccessTime**. The program does
not restore it, deliberately: writing a timestamp back would itself be the
source mutation this rule exists to prevent.

Content extraction writes only into the run's own output folder.

---

## Error handling

Failures are **explicit, attributable and local**.

- A file that cannot be hashed gets an error kind and message, and **no
  digest** — never a partial or invented one.
- A file that fails one analyzer is still analysed by the others.
- One bad file never discards its successful siblings.
- A failed stage is recorded against its run with what went wrong, and a run
  containing a failed optional stage reports `completed_with_warnings` rather
  than `failed`.

An error row and a missing row mean different things, and the schema keeps them
different.

---

## Performance

Built for eventual 100 K–1 M file projects:

- file contents stream in bounded chunks, never whole-file reads;
- persistence is batched, not per-row;
- queries carry deterministic ordering where output order matters;
- sequential and deterministic — no worker pools, no async, no subprocess fan-out.

Determinism is a property of the code rather than of filesystem arrival order:
current/presentation ordering is derived from root/path sort keys and explicit
tie-breakers. Durable identity never depends on traversal order.

---

## B6/B6.1: current state, separate from history

B5's adversarial review found that the product had a historical record
and treated it as the current state, because it had nothing else to
treat as the current state. B6 introduces the missing concept.

```
  file_state          WHAT IS TRUE NOW
                      One row per location. Never grows with run count.
                      Every current-state question reads only this --
                      including the duplicate query, which is the
                      product's central answer.

  file_observation    WHAT WAS TRUE, AND WHEN IT CHANGED
                      Append-only CHANGE history. An observation is
                      written when a location is new or different, not
                      once per file per run. Read for historical
                      questions and at no other time.

  hash_measurement    WHAT A PARTICULAR RUN MEASURED
                      Provenance. Never scanned to answer "are these
                      files duplicates today?"
```

### The staleness invariant

A current-state table makes one risk sharper, not softer: it becomes
possible to hold a hash from run 3 next to an observation from run 7
and present the pair as a fact about the disk today.

So content identity on `file_state` is authoritative **only** when

```
    content_observation_id == current_observation_id
```

If a file was hashed, then modified, then re-scanned, the new
observation supersedes the old one and the two ids diverge. The hash is
then *visibly stale* rather than quietly wrong. The condition lives in
the SQL of `current_duplicate_sets()`, not in a convention callers are
asked to remember — B5-F's finding on `legacy_db_id` is what happens
when an invariant lives in a comment.

### Identity is not presentation order

```
  DURABLE SEMANTIC IDENTITY    file_path_id, content.sha256
      Stable for the life of the project. Never derived from
      traversal order.

  DETERMINISTIC PRESENTATION   root_ordinal, path_sort_key
      Derived from the DATA -- a case-folded path under a case-folded
      root -- not from the order the filesystem handed entries back.
      Two machines scanning the same tree in different physical orders
      produce the same presentation order, because neither consults
      the walk.
```

`legacy_db_id` survives as a per-scan ordinal for Alpha equivalence and
is explicitly excluded from durable identity.

### One canonical timestamp model

Stored values are true UTC in ISO-8601 with an explicit `Z`, plus the
offset in force at observation. Locale formatting happens at the
export and display boundary and nowhere else. Pre-B6 rows are labelled
`local_naive` and their UTC columns are NULL, because the offset they
were written under was never recorded and inventing one would be a
fabricated record.

### Two export families

```
  Alpha-equivalence artifacts   Byte-comparable against Alpha's own
                                output. Locale-rendered, legacy
                                ordering. Exist to prove the past.

  Canonical B6 artifacts        Inventory-Canonical.csv,
                                Duplicates-Current.csv. ISO-8601 UTC,
                                deterministic ordering, scoped to
                                current state. The contract going
                                forward.
```

Both are produced. B5 established that B4.5's outputs were untrustworthy
in specific ways, not that the equivalence evidence should be discarded.

---

## Phase 2 (B7): the one window and the query core

`Dashboard.py` keeps only the startup checks (stale-run reconciliation,
`Test-Installation.ps1`, `Install-Dependencies.ps1`) and hands off to
`Scripts\Phase2\gui.py`, the one window: Open/New Project, the project
summary (`hub.py`, rendered from `capability.py` — "what can this project
answer, and which run would change that"), Files, Reports, Saved Queries,
Evidence, History, Options. Every collection run goes through
`runner.py` — estimate first, a blocking run screen with Cancel — into the
same `RunCoordinator`. The analytical side (`query.py`, `reports.py`,
`saved.py`, `fts.py`, `coverage.py`, `derived.py`) reads stored evidence
only; no source file is reopened to answer a question. The current
exact-duplicate projection (`p2_current_duplicate_*`) is rebuilt from
`file_state` whenever the authoritative evidence changes, and is the only
place the product reads "current duplicates" from — never `duplicate_group`,
which is a per-run snapshot.

## Phase 3 (B8): decisions

```
   Phase 2 evidence (p2_current_duplicate_*, file_state, file_path, content)
                  |
        Phase3\evidence.py          loads the model: locations, groups, decisions, policies
                  |
        Phase3\resolve.py           the deterministic resolution -- a pure function
                  |                 (protection > explicit location > explicit group >
                  |                  folder policy > root policy > project > heuristic > display)
                  v
   keeper set / canonical / protected / redundant candidates / conflicts /
   plan-eligible reclaim  -- DERIVED, never stored
                  |
        Phase3\routing.py           the routes -- a pure function of the projection, the
                  |                 review events, root coverage and the moment
                  v                 (conflict > needs revalidation > blocked > deferred >
   one primary route per group      ready for plan > resolved > queue)
   + every condition that applies  -- DERIVED, never stored
                  |
        Phase3\bulk.py              bulk: the Show filter as one function, the frozen
                  |                 query, and the preview -- every target through the
                  v                 same resolver with the batch's decisions in place
   decided / already satisfied / preserved / conflict / blocked / not applicable
                  ^
        Phase3\store.py             the only writer of p3_*: one operation = one transaction
                  ^                 (decisions, withdrawals, policy versions, review events,
                  |                  a batch with its members and decisions; the detector's
                  |                  rows under a system operation)
        Phase3\review.py            the Decide page inside the one window
        Phase3\pairs.py             (B11) visual candidate pairs resolved one by one --
                                    relationship and retention, the chain guard
        Phase3\imagegame.py         (B11) the Similar Images page: A/B compare
```

Four authoritative families, all append-only: **operation** (one user
action: actor, agent, session, command, time), **decision** (immutable:
target, kind, typed value validated by `registry.py`, evidence binding by
reference into Phase 1/2 ids, `supersedes`, and — from B10 — an origin:
`explicit_human`, or `bulk_explicit_human` with the batch id), **withdrawal**
(a row, not a flag), **policy + version** (protect / prefer / avoid over a
root or a folder; a change or a retirement is a new version). B9 added the
review event and B10 the bulk batch (below). The product never UPDATEs or
DELETEs a p3 row.

What the page shows — keepers, the canonical, the protected set, redundant
candidates, conflicts, plan-eligible reclaim — is recomputed from those
tables and Phase 2 evidence every time it draws. Nothing about it is stored;
a copy of the database gives the same answer (`p3_regression.py` proves it,
and `p3_fixture_check.py` proves the resolution against the research's
fixture lab).

The protection gate is in the resolution, not in a dialog: a
`redundant_location` decision on a location an active `protect_*` policy
covers has no effect and is reported as a conflict, until an
`override_protection` decision naming that exact policy version exists for
that location. The window asks for the override in its own dialog with a
required reason; the store refuses to record one without the confirmation
flag, the reason, and a policy that actually covers the location.

Physical identity is Phase 1's (`volume_serial` + `file_index`): a hard-link
alias is not a separate physical copy, reclaim counts objects whose every
name is a redundant candidate, and unknown identity means unknown reclaim,
never a guess. Multiple keepers are a normal resolved state; the canonical is
optional and always a keeper; no copy is ever labelled the original.

Phase 3 writes nowhere but the project database (and, on request, a journal
export under the project's `Exports\`). No source file is opened.

### Review routing (B9)

Queues route attention; they do not create truth. `routing.py` gives each
group one primary route from the conditions that apply, in a fixed
precedence (conflict, needs revalidation, blocked, deferred, ready for plan,
resolved, the queue), and reports every condition. The only new authoritative
family is the review event — a person's deferral with its return trigger, a
skip, a restore, and what the detector found — under `p3_operation` like
everything else. Needs Revalidation is computed from B8's evidence bindings
(a bound observation that is no longer the location's current one; a group
decision whose bound membership changed) and clears when the comparison
clears — a new decision, a withdrawal — never by a mark. Blocked is computed
from Phase 1/2 evidence (physical identity unknown, a stale fingerprint, a
root whose latest walk is unusable); Deferred from open deferral events
whose trigger, evaluated live, has not fired. The detector (`reconcile`)
runs when the Decide page opens and after a scan or fingerprint run, writes
the provenance rows for what it finds under a system operation
(`actor_kind = system_evidence`), and never touches a decision.

### Bulk decisions and the policy workflow (B10)

One decision over many targets is a **batch**: the fourth authoritative
family (`p3_bulk_batch`, `p3_bulk_member`). Its scope is either the exact
groups a person checked or what the Show filter listed at one moment, and
its membership is **frozen at commit** — the frozen query is recorded so
the record can say what the batch was over, but nothing ever re-evaluates
it: a target that comes to match later is not a member (a snapshot is not a
policy). Every decision a batch records carries `origin_kind =
bulk_explicit_human` and `origin_ref = the batch id`; every member carries
its disposition, so the exceptions are in the record, not just in the
preview.

`bulk.py` is pure, like the resolver and the router. The preview plans
what an action would record for each target, then judges each planned
decision against what stands — an active decision in the same conflict
domain, the group's explicit canonical, a Keep all in force, a mark on the
recommended copy — and runs the group through `resolve_group` with the
batch's decisions in place (all of a group's at once, so the last-copy
floor is judged over the whole batch). An explicit, incompatible decision
is preserved and counted, never overwritten; a hard-constraint conflict is
not recorded; a blocked group receives no retention decision. The Show
filter itself lives in `bulk.filter_groups`, one function the page draws
from and a snapshot freezes.

`store.commit_bulk` records exactly the preview — one operation of kind
`bulk` with the batch row, every member and every decided decision, each
bound to the evidence the preview captured — and refuses if the decision
record moved since the preview. `store.undo_batch` withdraws what the
batch recorded and still stands (a decision the person has since
superseded is left alone) under one operation of kind `bulk_undo`. The
policy side (`bulk.policy_preview`) resolves every group with the
hypothetical policy in place and diffs it against the projection without,
so the Add policy dialog can show what the policy covers and changes today
and state that future matches will be evaluated against it, before the
policy exists. Nothing here opens a source file.

After a batch there are thousands of active decisions; `routing.route_all`
indexes them by target once per pass (3,000 groups with 6,000 decisions
route in 0.1 s), and the store's chain-head and withdrawal checks are
targeted queries, not table loads.

### Similar images — the Image Game (B11)

```
   analyzer_result (image: pHash / aHash / dHash per current observation)
                  |
        Phase2\visual.py            compare: pigeonhole buckets (9 segments of a 64-bit
                  |                 hash; two hashes within 8 bits share a segment),
                  |                 the neighbour cap, then UPSERT by (a, b) -- run at the
                  v                 end of the Image Analysis stage, or on demand
   visual_candidate (Phase 2 EVIDENCE: two locations, method, distance, the
                     observations compared; current / retired, never deleted)
                  |
        Phase3\evidence.py          current pairs (live: both images still on the compared
                  |                 observations), plus any a decision or event names
        Phase3\pairs.py             each pair on its own: relationship x retention ->
                  |                 keepers, the redundant candidate, conflicts
        Phase3\routing.py           the same seven routes; the pairs' queue is the
                  |                 Visual Similarity Review
        Phase3\imagegame.py         one pair at a time, keys, linked previews
```

**Evidence on the Phase 2 side.** The image analyzer stored hashes and never
pairs; `Phase2\visual.py` makes the pairs, because finding candidates is
evidence, not adjudication. A pair's identity is its two file locations
(`UNIQUE(a_file_path_id, b_file_path_id)`): a later generation that finds
the same two images updates that row, so a decision on the pair keeps its
target. A pair the latest generation did not find is retired
(`current = 0`), never deleted. The search is bucketed, not all-against-all:
cut the 64-bit pHash into 9 segments; two hashes within 8 bits agree
exactly on at least one, so only images sharing a segment value are
compared (vectorized with numpy where it is installed — imagehash needs
it — and the same answer in plain Python). Each image keeps its 10 nearest
look-alikes; the generation row counts the images a crowd trimmed.

**Relationship and retention are two decisions** on the pair
(`target_kind = visual_candidate_pair`): `equivalent_copy` /
`related_variant` / `different` in one conflict domain, and retention —
`canonical_location` (reused: the preferred image) and `keep_both` (new: the
pair-scoped Keep all) — in their own. A fast action records both in one
operation (`store.record_decisions`); Undo withdraws both
(`store.withdraw_many`). `pairs.py` resolves each pair alone: equivalent +
canonical makes the other image the pair's redundant candidate; keep_both
keeps both; a related variant keeps both by default and never feeds the
ordinary cleanup; different is a resolved false positive. The one place
pairs meet is a guard, not an inference: when the image one pair keeps is
the image another pair gives up, the first pair is a conflict — nothing
asserts that the two ends of the chain are alike. Protection and an
explicit Keep stand in front of a pair's redundant candidate exactly as in
an exact group.

**Routing.** The same seven routes; the pairs' Unresolved route is the
Visual Similarity Review. Two blockers of their own, computed live:
*preview unavailable* (an image cloud-only or not present — returns on the
`preview_available` trigger, which B9 reserved) and *stale image analysis*
(an image re-observed since its hash was compared). A decided pair whose
images moved is Needs Revalidation, never unreviewed again. A pair of
byte-identical images routes to Resolved (*exact copies*): the exact
review answers it, and it can still be opened. A recorded blocked event
now names its blocker (`detail.blocker`); an older row without one is read
by its return kind as before.

**Scale.** On a real collection (25,330 analyzed images, 51,924 pairs) the
comparison takes 7 s; the page opens in about 3.5 s including the detector,
moves between pairs in under 0.1 s and records an answer in about 0.3 s:
after an answer only the decision record is re-read and only the pairs are
re-derived, and a pair no decision touches keeps its projection and route
(the checks compare that shortcut with a fresh derivation). The Duplicate
decisions page and the summary leave the pairs out (the summary's line is
plain SQL counts).

**Previews** are reads: Pillow opens the file read-only and decodes it in
memory, EXIF orientation applied; a pyramid of reduced copies serves a
zoomed-out view and 100% is the decoded original. A file whose attributes
say cloud-only — checked live, not only from the last scan — is never
opened.

### Bulk over visual pairs (B12)

A crowd of look-alikes (a folder of variations of one prompt) is decided
once over a **frozen batch** of pairs — B10's query-result snapshot, whose
tables already record a target kind per member — never as a standing rule:
the research rules out a dynamic policy for relationship and retention
decisions (P3.R4), because a rule that re-applies itself would let a
folder's membership expand what a person decided without their looking.

```
   Similar Images page: Show list (bulk.filter_pairs -- moved from the page)
        + optional folder (both images in it; subfolders or not)  or  checked pairs
                  |
        bulk.preview_pairs      plan_pairs: per pair, the rows V / E / D record
                  |             classify_pairs: satisfied -> the pair's route
                  |               (Conflicts; Blocked / Needs Revalidation) ->
                  |               explicit intent preserved (another relationship,
                  |               a preference Keep both would undo, retention
                  |               Different would contradict, a deferral) ->
                  |               the whole batch through pairs.resolve_pairs
                  |               (a conflict it would create keeps that pair out)
                  v
   decided / already satisfied / preserved / conflict / blocked / not applicable
                  |
        store.commit_bulk       one 'bulk' operation: batch, members, decisions
                                (origin bulk_explicit_human, the batch id, bound
                                to the pair's evidence); refuses if the record
                                OR the evidence moved since the preview
```

Nothing new in the schema or the decision vocabulary: three bulk actions in
the registry (`pairs_related_keep_both`, `pairs_equivalent_keep_both`,
`pairs_different`) record kinds Build 5 registered, the same rows a key
records on one pair. The preview's evidence mark (`evidence.evidence_mark`:
the newest generation and the newest observation) is new, because a
re-analysis or a scan adds no operation for the record guard to see. The
bindings are built from the loaded model (`evidence.pair_binding_from`, the
same shape `pair_binding` reads) so a preview of thousands of pairs costs no
per-pair query. Undo is B10's `undo_batch`: one operation withdrawing what
the batch recorded and still stands.

**Evidence mark, every batch (B13).** Group and copy previews (`bulk.preview`)
carry the evidence mark too; `store.commit_bulk` has one check for every
target kind. The mark also records the newest run that wrote a location's
current state (`file_state.current_run_id` / `content_run_id` /
`hash_run_id`), because a rescan of unchanged files keeps each observation
(`fo_state.touch_unchanged`) and a vanished file is marked missing in place
-- neither moved the B12 mark.

**The simulation's neighbourhood (B13).** `classify_pairs` resolves only the
pairs within two image-hops of the batch (`bulk._neighbourhood`): a pair's
conflicts come from its own rows and from the chain guard (its keeper given
up by another pair), so nothing further away can change. Same answers as the
whole model (checked on random graphs); a six-pair preview on the real
collection went from 1.3 s to milliseconds.

### Survey Mode (B13)

A small cluster of look-alikes, looked at together; each decision still names
one pair. Nothing records a cluster.

```
   Similar Images page: Show list
        |  survey.cluster_map: connected components of the list's OPEN pairs
        |    (not Resolved / Ready for Plan / byte-identical), in list order
        v
   isolated pair (2)      survey cluster (3-8)           oversized (> 8)
   = Compare Mode         = survey_view.SurveyWindow      reported: size, folders
                                 |
        survey.cluster_edges: every current non-exact candidate pair among the
        images in view (Exclude removes an image from the view -- records nothing)
                                 |
        survey.group_action -> one Build 6 batch over those pairs:
          Keep All                      pairs_keep_both
          Mark Remaining Equivalent     pairs_equivalent_keep_both | pairs_equivalent_prefer
          Mark Selected as Related      pairs_related_keep_both  (pairs with exactly one
                                          image selected)
          Defer Group                   pairs_defer (undecided pairs only)
                                 |
        bulk.preview_pairs (explicit selection, "survey" noted in the parameters)
          -> store.commit_bulk -> store.undo_batch
```

The three new actions are registry entries marked `survey_only` (not offered
by either Bulk dialog). `plan_pairs` chooses each pair's retention for
`pairs_equivalent_prefer` (the Canonical where the pair has it and its other
image is not a Keeper, Keep both otherwise) and plans one deferral per pair
for `pairs_defer`. Per-image Compare opens the page's Compare Mode
(`open_compare`), unchanged: two images that are not a candidate pair are
shown with no pair decision on offer.

### The Similar Images page's incremental refresh (B14)

The page holds one model (evidence, projection, routes). After an answer
the evidence has not moved, only the record has, and the record only grows:
a decision row is never changed (it is withdrawn by a new withdrawal row or
superseded by a new decision row) and review events are only added.

```
   refresh.marks(conn)     newest decision / withdrawal / review event / policy version
        |                  (taken BEFORE a load; a row read twice is skipped, never missed)
   refresh.apply(...)      the rows above the marks -> the changed pairs and images
        |                    pairs.neighbourhood: re-resolve the first ring (the two-ring
        |                    model makes the chain guard exact -- B13's argument)
        |                    routing.route_pairs(only=): the ring, the changed targets and
        |                    every pair with an open event (a dated deferral fires by the clock)
        v
   None  -> the page reloads everything: a policy changed, or a decision names a pair or
            an image the model does not hold
```

Page open: `routing.reconcile(..., keep=)` hands the page the model the
detector just loaded and the marks taken before it; `apply` catches up with
the detector's own events, so the page no longer loads and routes the whole
model twice. `p3_refresh_check.py` compares `refresh.fingerprint` of the
incremental model with a fresh full load after every step of random sessions
(and so did the acceptance run on a copy of the real collection).

### The Plan Builder (B15, schema 13)

A Plan is proposed logical intent derived from current decisions and policies
(P3.R7): never an action log, never a guarantee, never an execution method.

```
   decisions + evidence --> resolve / routing (unchanged)
        |   population: groups and pairs whose decisions resolve them ready for a plan
        |   (deferred left out; evidence-blocked or needing revalidation IN, items blocked)
   plans.build (pure)
        |   remove_location per redundant candidate, no_op_preserve per keeper / protected / preferred
        |   links: justifying decisions, policy versions, an ids-only evidence binding, dependencies
        |   Phase 3 Evidence Preview: evidence-stage preconditions -> pass | fail | unknown |
        |     not_applicable | unsupported; hard fail -> blocked, hard unknown -> blocked_evidence,
        |     unsupported -> unsupported_action; live-stage ones declared for Phase 4, never evaluated
        |   no_retaining_decision: any current decision anywhere that keeps the image blocks the removal
   store.record_plan_revision      one 'plan_build' operation, status previewed, basis = record +
        |                          evidence marks
   store.lock_plan_revision        marks unchanged -> canonical payload -> SHA-256 -> locked + event
        |                          with the dependency set
   routing.reconcile -> store.reconcile_plans -> plans.obsolescence (the revision's OWN dependencies)
                                   -> 'obsolete' event; items and fingerprint untouched
```

The schema normalizes what is queried -- decision links (`p3_plan_item_decision`,
indexed by decision: the dependency index), policy links, the item graph, the
preconditions with their results -- and keeps as JSON what is read back whole:
the evidence binding (the `p3_decision` shape, so `routing.binding_drift` reads
it), effects, postconditions. The Plan page (`planview.py`) reads the model once
at open (the detector's) and reuses it while the record and evidence marks
stand; nothing it does opens a source file.

### Project metadata and desired source metadata (B16, schema 14)

Four layers, never one "field -> value -> save" (P3.R8, P3-A103): observed
source metadata (Phase 1/2 evidence), project metadata, desired source
metadata (a decision), and an executed / verified change (Phase 4's; nothing
here simulates it). Metadata reuses the one Decision engine (R10 §4.6): four
decision kinds, one new target.

```
   analyzer_result (Phase 1/2: Title / Author / created, latest attempt on the current observation)
   file_state.hard_link_count           p3_metadata_capability (014: family x field -> read, write, status)
        |                                    |
   evidence.load_metadata --> MetadataQuestion (p3_metadata_question: location x semantic field, identity only)
        |   observed value-state (known | no_value | unknown | unsupported | unavailable | stale | conflicting),
        |   capability, signed (always unknown: no Phase 1/2 evidence), hard-linked (true | false | unknown)
        |   + every metadata decision (kept OUT of ev.decisions: the duplicate / pair model never sees them)
   metadata.py (pure)
        |   blocked_by_evidence(questions)  -> question -> signed_or_certified | hardlinked_generic_write   (no plan)
        |   review / queue                  -> the data a review surface needs; Left Unchanged returns only on drift
        |   project_metadata                -> the project's own values, labelled "stored in the project"
   plans.build (after the retention items)
        |   desired set / clear -> update_source_metadata item; preconditions: writer_supported (the record's
        |   status; only 'supported' passes), source_not_signed_or_certified, source_not_hardlinked (the same
        |   checks blocked_by_evidence uses), decision_current, target_exists; a cloud-only warning
        |   project_metadata_set, leave_source_metadata_unchanged -> never an item (counted)
   plans.obsolescence: a metadata item's own decision, evidence, capability status and integrity flags
```

`store.record_metadata_decision` names the question (creating it the first
time) and records the decision in one operation. Its binding is ids only: the
question, the location's observation, and the analyzer results the value came
from. No row in the capability record says `supported`, so every metadata
item is blocked.

### The Metadata page and its evidence (B17, schema 15)

```
   PDFAnalysis 1.2.0 / OfficeAnalysis 1.4.0 --> analyzer_result.signature_state (015; promoted: two analyzers)
        |   PDF: /FT /Sig with /V -> signed, /Perms /DocMDP -> certified, an embedded signed PDF -> signed,
        |        empty placeholder -> unsigned; needs a password -> "password-protected document" (error)
        |   Office: _xmlsignatures/ or the origin relationship -> signed; OLE2 _signatures /
        |        \x05DigitalSignature / _xmlsignatures stream -> signed; RTF as .doc -> not_applicable
   evidence._integrity_evidence  newest PDF / Office attempt on the current observation:
        |   signed_or_certified (NULL -> unknown), encrypted (IsEncrypted; the password-protected wording;
        |   an opened OOXML package -> false; legacy OLE2 -> unknown), not-applicable checks
   metadata.integrity  signed | encrypted (new) | hard-linked -> plans' hard preconditions and blocked_by_evidence
   metadata_view.py    the Metadata page: the queue (metadata.queue + evidence.conflicting_fields,
        |              filters) and one file (evidence.file_metadata, metadata.file_review, the Plan
        |              Builder's own metadata_items for the expected action); actions through
        |              store.record_metadata_decision / defer_metadata / withdraw / restore
```

Metadata questions' review events (deferrals) are loaded apart from the
retention model (`load_events(metadata=True)`), as their decisions are;
`routing.py` does not see them. A decision on a deferred question ends the
deferral in the same operation. The plan notes: S1 (a file in an
exact-duplicate group would leave it) is an informational note in the
item's estimated effect; S2 (a removal and a metadata update on the same
location) is a warning precondition attached only to those two items, so a
revision without metadata items is exactly Build 8's.

### The closeout round (B18, schema 16)

```
   PDFAnalysis 1.3.0 / OfficeAnalysis 1.5.0 --> analyzer_result.encryption_state (016; promoted: two analyzers)
        |   PDF: the /Encrypt dictionary (an owner-password-only file too); needs a password ->
        |        PasswordProtectedError, whose ERROR row still says encrypted (engine keeps exc.evidence_fields)
        |   Office: an opened .docx/.xlsx/.pptx -> not_encrypted; a password-to-open package -> the same error;
        |        legacy OLE2 from header structures only (bounded sector-chain reads, never a whole stream):
        |        Word FibBase fEncrypted 0x0100; Excel FILEPASS after the globals BOF (after an optional
        |        WRITEPROTECT); PowerPoint EncryptedSummary / CurrentUserAtom token / UserEditAtom length
        |        (encryptSessionPersistIdRef); IRM \tDRMContent -> encrypted; unreadable -> unknown; RTF -> n/a
   evidence._integrity_evidence   the column first; B17's error-text reading only when it is NULL (pre-B18 rows)
   metadata.binding_drift          THE MATERIAL-CHANGE RULE: content identity (a new observation, unless both
        |                          sides hold the same authoritative content hash), the observed value, the
        |                          value-state. Not: a re-read with the same value, an analyzer version, an
        |                          unchanged rescan, integrity evidence. Governs revalidation, Leave Unchanged's
        |                          return, the "until the evidence changes" deferral; plans._metadata_obsolescence
        |                          compares the item's frozen observed value/state likewise (integrity still
        |                          obsoletes: "integrity evidence changed")
   evidence.question_binding_from  B18 bindings carry content_id + observed value(s) + state; B16/B17 bindings
        |                          (ids only) are resolved to values at load (resolve_bound_values), or fall
        |                          back to the ids when unresolvable -- never silently current
   bulk.preview_metadata_confirm   bulk Confirm (member kind metadata_question): decided (the same kind and value
        |                          re-recorded on fresh evidence) / already current / conflict / blocked / not
        |                          applicable; store.commit_bulk judged on evidence.metadata_mark (+ newest
        |                          analyzer result); undo_batch re-applies each superseded decision as recorded
   refresh.MetadataCache           the queue's session cache: full load on an evidence change, catch-up above the
                                   record watermarks otherwise; rebuildable, never authoritative
```

`observe()` reads a location the last scan did not find as `unavailable`
even when its old readings remain (B18). `_analyzer_readings` reads the
newest reading of each analyzer on the current observation, and every
observation's rows only where there is none -- the same questions,
without re-reading every past analysis run (the queue's cold open).

Plan revisions record, in their basis, the registry version and a digest
of its vocabulary, the resolver and evaluator versions, the payload schema
and the newest analyzer result (S4); the lock refuses a revision whose
files were analyzed again since its preview. `store.export_current_state`
(the Decide page's "Export current state...") writes what the decisions say
now -- group resolutions, decided pairs, desired and project metadata, each
with its decision ids -- through Phase 2's CSV writer and formula guard (S3).

### The plan fingerprint

Specified in `Docs\Specifications\PLAN_FINGERPRINT_SPEC.md` (M3), with
golden vectors in `Resources\Phase3\fingerprint_vectors.json`
(`p3_fingerprint_check`). In short: SHA-256 over the canonical JSON of the
payload `{"schema": "fileorganizer.p3.plan-payload/1", "items": [...]}`
(15 members per item, in item-number order); keys sorted by code point;
no whitespace; strings as stored (no Unicode normalization -- the path
Phase 4 acts on is the byte-exact observed name); UTF-8, an unpaired
surrogate in its generalized three-byte form; only JSON-native values (a
date, path, decimal, set or NaN is refused, never `str()`-ed). B18 wrote
this down without changing a fingerprint: B15-B17's `default=str` was
never called. Verification uses the schema recorded in the revision's
basis (none before B18: /1): `plans.verify`, `store.verify_plan_revision`.

### Path matching (folder and root policies, bulk folder scopes)

`model.path_key` is the one rule every folder policy, root policy and
bulk folder scope compares through (S5; `p3_closeout_check` §4):

1. separators: `/` is `\`; a run of separators is one; a UNC path's
   leading `\\` is kept; an extended-length prefix (`\\?\`, `\\?\UNC\`) is
   dropped;
2. no trailing separator;
3. Unicode: compared in **NFC** -- a folder typed composed matches the
   name stored decomposed and the reverse. Two sibling folders differing
   only by normalization become one scope: the conservative direction for
   protection (a policy can over-cover, never silently miss). Plans and
   fingerprints never normalize (see above);
4. case-insensitive by simple lower-casing (`str.lower`), as Windows
   compares names -- not full case folding (`ß` is not `ss`);
5. the folder boundary is a whole segment: `C:\Docs` covers `C:\Docs` and
   `C:\Docs\x`, never `C:\Docs2` (`model.under`).

Symbolic links and reparse points are not followed by policy matching: a
policy covers the path the evidence stores (Phase 1 records a link as the
link, never its target), and mount boundaries and renamed roots are
matched by the stored root key, not re-resolved.

### State machines (M6)

Each lifecycle below is enforced in the code named, and its invalid
transitions are refused in the checks named (`p3_closeout_check` unless
said otherwise). "Refused" means the store raises `ValueError` and writes
nothing (one operation, one transaction).

**(a) The decision lifecycle** -- states are derived (`model.active_decisions`),
never stored: a row is *active* until a later row supersedes it or a
withdrawal row names it.

| From | Event | Guard | To | Code |
|---|---|---|---|---|
| (none) | record | the registry permits the kind on the target; its value validates; a GROUP/PAIR/QUESTION target is current; a canonical is a member | active; supersedes the chain head **in its own conflict domain** only | `store.record_decision`, `_validate_decision`, `_chain_head` |
| (none) | record override | explicit confirmation + rationale + an active protection policy that covers the location | active | `store.record_override` |
| active | supersede (a later row, same target, same domain) | -- | superseded | `_decision_row` |
| active | withdraw | the row is active | withdrawn (a withdrawal row; the decision row stays) | `store.withdraw`, `_withdraw_row` |
| withdrawn | re-apply | as record | the new row supersedes the withdrawn one (F09) | `_chain_head` includes withdrawn rows |
| active (Confirm batch member) | batch Undo | the row still speaks | withdrawn, and the decision it superseded re-applied as recorded (B18) | `store.undo_batch` |

Invalid, refused: withdraw a withdrawn or a superseded decision; an unknown
kind; a kind on a target it cannot take; a group that is not current; a
canonical that is not a member; `override_protection` through
`record_decision`; an override without confirmation or rationale.
Impossible by construction: supersession across conflict domains (a
project note never supersedes a redundant mark; Keep all and the canonical
are two domains), reviving a superseded row, editing or deleting a row
(the store has no such statement; mutant M08 shows the suites catch one).

**(b) Review routing** -- the route is derived, never stored: `routing.route_target`
applies the precedence **conflict > needs revalidation > blocked > deferred >
ready for plan > resolved > unresolved** (`routing.ROUTE_ORDER`); every one of
the 127 combinations is tested in `p3_regression` §6. Review events are
append-only rows (`p3_review_event`): a deferral opens, a restore closes the
one it names (or all), a newer deferral replaces an open one; the detector
writes needs_revalidation / blocked / restored under a system operation and
never a decision. Invalid, refused: a restore naming an event that does not
exist; a deferral whose disposition does not apply to the target (a preview
trigger on a group; a hash trigger on a metadata field); an unparseable
date; an unknown event kind (`store._event_row`, `store.defer`).

**(c) The plan-revision lifecycle** -- the events (`p3_plan_event`) are the
record; `p3_plan_revision.status / locked / obsolete` are a **cache** of
them (S6), written in the same operation and rebuildable
(`store.plan_status_from_events`, checked equal by `plan_status_drift`).

| From | Event | Guard | To | Code |
|---|---|---|---|---|
| (none) | build | the dependency graph is acyclic | previewed (`previewed` event) | `record_plan_revision` |
| previewed | a newer build of the same plan | -- | superseded | `record_plan_revision` |
| previewed | lock | the record, the evidence and (B18) the analyzer results are where the preview found them; the payload schema is current | locked (fingerprint + `locked` event with the dependency set) | `lock_plan_revision` |
| locked | its own dependency changed (detector) | `plans.obsolescence` names a reason | obsolete (`obsolete` event with reasons) | `reconcile_plans`, `mark_plans_obsolete` |

Invalid, refused: lock twice; lock a superseded or an obsolete revision;
lock after the record, the evidence or the analyzer results moved; an
unknown plan event kind. "Edit a locked revision" has no statement: a new
build makes a new revision and the locked one stays locked; an item
altered in the database fails `verify_plan_revision`. Obsolete never
reverts (the same decision recorded again leaves it obsolete; a new
revision is how to go on).

**(d) Conflicts** -- derived, never stored (`resolve.resolve_group`,
`pairs.resolve_pairs`): a conflict *arises* from decisions against
protection, the floors or each other; it is *resolved by a decision* (the
mark withdrawn, Keep, a canonical), *by an override* (only
`override_protection` naming the policy version), or *by the evidence*
(the copy left the group; the pair is no longer a candidate). A policy of
lower rank never resolves one; a blocking conflict withholds plan
eligibility and routes first. Checked in `p3_closeout_check` §1d,
`p3_regression` §2 and §6, `p3_visual_check`.
