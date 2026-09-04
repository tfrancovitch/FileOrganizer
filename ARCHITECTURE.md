# Architecture — The File Organizer, Phase 1, B6.1

This describes the software as it is. It is not a history of how it got here.

---

## The shape of it

```
        TheFileOrganizer.bat          finds/installs Python, launches the app
                  |
             Dashboard.py             Tkinter GUI, screens and stage sequencing
                  |
          RunCoordinator.py           runs, stages, events, the DB session
                  |
   +--------------+--------------+------------------+
   |              |              |                  |
 inventory      hash &        analyzer          project /
 engine        duplicate       runtime          estimates
   |            engine            |                  |
   +--------------+--------------+------------------+
                  |
          FileOrganizer.db           SQLite, schema 7 -- AUTHORITATIVE
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

Schema **`user_version = 7`**, created by `fo_db.py` applying migrations
`001`–`007`. Migration 006 introduced current-state separation; migration 007
completes the A–F reconciliation/current projection.

| Table group | Holds |
|---|---|
| `project`, `source_root`, `run`, `run_stage`, `event` | identity and history |
| `inventory_scan`, `file_path`, `file_observation`, `file_state` | scan history, locations and current state |
| `content`, `hash_measurement` | content identity and what was measured |
| `duplicate_run`, `duplicate_group`, `duplicate_member` | what a run concluded |
| `analyzer`, `analyzer_run`, `analyzer_result` | analyzer outcomes |
| `archive_member`, `archive_summary`, `extracted_content` | bounded analyzer detail |
| `scan_path_event` | empty/skipped directory coverage facts |

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
