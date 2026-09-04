# The File Organizer — B6 Change Log

**From:** Phase 1 RC B4.5
**To:** Phase 1 RC B6
**Driver:** the consolidated B5-E / B5-F adversarial reconciliation report
**Schema:** 5 → 6 (migration `006_b6_state_identity.sql`)

---

## What B6 is

B5 concluded that several B4.5 defects were architectural rather than
local, and that the correct response was not to optimise B4.5. Two
findings in particular could not be patched:

- **E.F002** — the duplicate query, which is the product's central
  answer, got slower every time the product was used, on an unchanged
  disk. Not because the query was slow, but because B4.5 had a
  historical record and no concept of current state, so it used the
  history as if it were the present.
- **F.F003** — `created_utc` / `modified_utc` / `accessed_utc` did not
  contain UTC. They contained machine-local wall clock with no offset,
  and nothing in the row said so.

B6 introduces the two concepts those findings were missing: a
**current-state projection** separate from history, and a **canonical
timestamp model** separate from presentation. Most of the rest of this
document follows from those two.

---

## Verification method, and its limits

Every claim below was measured on the build machine, and each has an
executable check in `Scripts/b6_regression.py` (30 checks, all
passing). Where a number appears, it came from running the code, not
from reasoning about it.

**This is not evidence about Windows.** NTFS behaviour, real locale
APIs, long paths, cloud placeholders, OneDrive, removable and network
storage all remain untested — the same gaps B5 recorded as unavailable
evidence. They are B6 acceptance targets, not claims this work can
make. See *Outstanding* at the end.

---

## Scalability findings (B5-E)

### E.F001 — full observation population per run — **FIXED**

B4.5 appended one `file_observation` row per file per run whether or
not anything had changed: ~525 bytes per file per run on a corpus that
never moved.

B6 writes an observation when a location is **new** or **changed**, and
records an unchanged re-verification as a counter on the scan plus a
refreshed `verified_utc` on `file_state`. `change_kind` and
`supersedes_observation_id` make the history a change chain rather than
a series of snapshots.

> **Measured:** 8 runs over an unchanged 800-file corpus. Observations
> stayed at 800. B4.5's model would have produced 6,400.
> In a separate 10-run / 4,000-file test: 4,000 rows versus 40,000.

Every historical question B4.5's model could answer, B6's still
answers — first seen, every state held, when each state ended. What is
no longer stored is a byte-identical restatement of an unchanged file,
once per run, forever. `history.mode = 'full'` restores the old
behaviour deliberately, for anyone who wants it.

### E.F002 — duplicate query degrades with run history — **FIXED**

The finding that drove the architecture.

`file_state` holds exactly one row per location regardless of run
count. `fo_state.current_duplicate_sets()` reads it and nothing else.

> **Measured, head-to-head on identical data**, 4,000 files, 10 runs:
>
> | run | B4.5 query | B6 query |
> |----:|-----------:|---------:|
> | 1   | 3.80 ms    | 3.97 ms  |
> | 3   | 9.43 ms    | 4.98 ms  |
> | 5   | 13.97 ms   | 3.85 ms  |
> | 10  | 26.33 ms   | 3.87 ms  |
>
> Both return the same 200 groups. B4.5 degrades 6.9×; B6 is flat.

The old query survives as `fo_hashes.duplicate_sets_by_content()`,
correctly documented as the *historical* question it actually answers.

### E.F003 — fully materialized exports — **FIXED**

B4.5 built each artifact in a `StringIO`, called `getvalue()`, called
`.encode()`, then wrote — four full copies alive at once.

B6 streams rows to an incrementally-flushed binary handle, and the
export queries return cursors rather than `fetchall()` lists.

> **Measured:** 300,000-row inventory (55.5 MB artifact):
> **300.2 MB → 1.0 MB peak, a 299× reduction, byte-identical output.**
> All three CSV dialects verified identical against B4.5 across nulls,
> embedded commas, quotes, newlines and Unicode.

The reduction is constant, not proportional, so the 1M-file case that
reached ~1,922 MB should now sit near the same 1 MB.

One subtlety worth recording: B4.5 decided emptiness with `if not
rows:` before writing. Against generators that test is always true, so
`_write_streamed()` decides after the fact and removes a zero-row
artifact — preserving Alpha's "no file at all" contract, which a
header-only CSV would have quietly broken.

### E.F004 / E.F005 — missing indexes — **FIXED**

`ix_duplicate_member_hash` and `ix_observation_legacy_db_id` added.
Verified by `EXPLAIN QUERY PLAN`: the transient **automatic** index
SQLite was building on every export is gone.

### E.F006 — unbounded archive entry cardinality — **FIXED**

One 75 MB ZIP produced 500,000 rows and ~358 MB of heap, with no cap
and no way to see it happening.

B6 makes the analysis mode an explicit recorded property —
`complete` / `capped` / `summary_only` — in both the artifact and the
new `archive_summary` table. **Aggregates are always complete**; only
the per-entry listing is bounded, because counting entries is cheap and
remembering them is not.

Capping our own retention turned out to be insufficient, and that is
worth recording: `zipfile.ZipFile` parses the entire central directory
on open, before any of our code runs.

> **Measured**, 200,000-entry archive:
> stdlib parse alone **121.6 MB**; our retention **20.6 MB**.
> Capping only ours: 142 MB → 121 MB. Real, but nearly irrelevant.

So B6 also reads the entry count from the 22-byte End Of Central
Directory record and never opens an archive above the threshold at all.

> **Measured:** 142.27 MB → **0.07 MB**, entry count still truthfully
> reported as 200,000, mode `summary_only`. Ordinary archives are
> unaffected — still `complete`, still fully listed.

ZIP64 is handled, because it is the case that matters: any archive over
65,535 entries stores a placeholder in the classic record. An early
draft read only the classic record and therefore failed on precisely
the archives large enough to be a problem. A fixture at exactly 65,535
caught a second bug, where the literal count `0xFFFF` was mistaken for
the placeholder.

### E.F007 — all analyzer outcomes retained simultaneously — **FIXED**

`AnalyzerOutcome` now accumulates counts as results pass through
instead of deriving them from a retained list, and the engine accepts a
**sink** that persists each result as it is produced.

> **Measured:** 60,000 results, **3.2 MB → 0.00 MB**, identical counts.
> At 200,000: 56.5 MB → 0.0 MB.
> Sink and retained paths verified to produce **identical
> `analyzer_result` database contents**.

Child rows (archive members, extraction references) are written
per batch, while the batch is still in hand — that is what keeps the
archive analyzer, the one that caused E.F006, bounded.

### E.F008 — extracted text keyed by path — **FIXED**

Artifacts are now named by a hash of the **text** and sharded two levels
(`ab/cd/<sha>.txt`). Identical extracted text is written once and the
reuse is recorded rather than hidden. 65,536 buckets means a million
documents average ~15 per directory instead of a million in one.

Writes go to a `.partial` neighbour and are renamed, so a crash cannot
leave a truncated artifact at a content-addressed name that later runs
would trust.

### E.F009 — `len(text.split())` memory amplification — **FIXED**

> **Measured:** 9.6 MB document, 1.55M tokens:
> **81.7 MB → 0.0001 MB, identical count.**

`fo_text.count_words()` / `count_lines()` are exact replacements for
`len(text.split())` and `len(text.splitlines())`, verified over 1,517
adversarial inputs including CRLF pairs, `\x85`, `\u2028`, non-breaking
spaces and the full Unicode line-boundary set. `TextStats` carries its
state across chunk boundaries, verified against random chunk sizes.

An intermediate version counted boundaries in fixed-size slices and got
`\r\n` wrong when the pair straddled an edge. It was replaced with a
character state machine, and the boundary set is written out explicitly
rather than approximated.

### E.F013 — estimator models bytes only — **FIXED**

The model is now `max(bytes / rate, files × per_file) + persistence +
export`. `max` rather than a sum, because the two costs overlap.
The console output states what the estimate covers **and what it
excludes**, because an estimate that silently omits stages is wrong in a
direction the user cannot see.

### E.F044 — 5,000-entry inaccessible cap — **FIXED**

The cap remains — unbounded diagnostics are their own scalability
problem — but `inventory_scan` now records `inaccessible_seen_count`,
`inaccessible_cap` and `inaccessible_truncated`. A consumer can finally
distinguish "4,900 inaccessible files" from "at least 5,000, of which
5,000 were kept". The bound is still a bound; it is no longer a secret.

---

## Reliability and determinism findings (B5-F)

### F.F001 — enumeration order controls identifiers — **FIXED**

Two separate changes, because sorting alone is not the fix:

1. **The walk is sorted.** Removes the volatility at source.
2. **Identity is separated from presentation.** Durable identity is
   `file_path_id` and `content.sha256`. Presentation order is
   `source_root.root_ordinal` and `file_path.path_sort_key`, both
   derived from the **data** — a case-folded path under a case-folded
   root — not from the walk. Exports order by these, not by
   `legacy_db_id`.

A sorted walk still renumbers everything after an inserted file, so
`legacy_db_id` remains a per-scan ordinal and B6 keeps it out of durable
identity.

> **Measured:** 5 trials over shuffled filesystem creation orders
> produced byte-identical walk order.

### F.F002 — report tie selection depends on encounter order — **FIXED**

Top-N and oldest/newest ties are broken by path. Because paths are
unique within a project, **no ties remain**, so the ordering cannot
depend on arrival order at all.

> **Measured:** 5 trials, ten files of identical size, shuffled
> creation order — identical top-5 every time.

### F.F003 — timestamps are not UTC — **FIXED, with a deliberate limit**

The falsely-named columns are **renamed** to `created_local_naive`,
`modified_local_naive`, `accessed_local_naive` — which is precisely what
they contain. New honest `*_utc` columns are added, plus
`utc_offset_minutes` and a per-row `timestamp_model`.

**Pre-B6 values are not rewritten, and this is the important part.**
The offset in force when they were written was never recorded, so there
is no arithmetic that recovers UTC from them. Old rows keep their old
values under a correct name; the new UTC column is `NULL`, which is the
honest answer to "what was this in UTC?" for a row that never recorded
enough to say. Inventing a value would be exactly the fabricated record
B5-H exists to catch.

`utc_iso_seconds()` is pure epoch arithmetic and consults no timezone
database, so it cannot reintroduce the 2003 DST bug the existing local
converter was written to fix.

> **Verified:** upgrading a real B4.5 database preserves the legacy
> value under the new name, leaves `modified_utc` NULL, and marks the
> row `local_naive`.

### F.F004 — regional settings alter or erase exported timestamps — **FIXED**

**Data exports carry ISO-8601 UTC. Always. Locale formatting happens at
the display boundary only.**

`render_locale_timestamp()` survives but is now reachable only from the
Alpha-equivalence artifacts, which exist to be compared byte-for-byte
against Alpha's own output and are labelled as such. Every export a
user or downstream tool consumes goes through
`render_canonical_timestamp()`, which has **no blank-cell path** — the
B4.5 failure mode where ISO-style locale patterns produced empty cells
cannot occur.

Pre-B6 values render with an explicit ` (local)` suffix. It is
deliberately slightly ugly: a consumer must not be able to mistake a
value of unknown zone for one in UTC.

### F.F008 — source-root order changes identifiers — **FIXED**

`root_ordinal` is assigned from `root_path_key`, so reordering roots in
settings changes nothing persisted. Verified by building the same two
roots in both orders and comparing.

### F.F009 — product reads alter LastAccessTime — **MITIGATED, NOT SOLVED**

`accessed` is deliberately excluded from the change-detection fields.
Without that, every run would report every file it hashed as modified —
the product detecting its own footprint and calling it news.

The underlying behaviour is unchanged: reading a file still updates its
last-access time. A real fix requires opening with
`FILE_FLAG_BACKUP_SEMANTICS` and restoring the timestamp, which is
Windows-only work that cannot be validated here. **Carried forward.**

---

## Deferred constraints now partially addressed

### G — Resilience

- `run.finalized` is written `1` only inside the transaction that sets a
  terminal status, so an interrupted run cannot read as complete.
- `run_stage` gains `attempt`, `progress_done`, `progress_total`,
  `checkpoint_utc`.
- Observations and their state rows are written in **one transaction**
  per batch, so `file_state` can never point at an uncommitted
  observation.
- **A sink failure now fails the analyzer.** An earlier B6 draft logged
  it and carried on, producing a run that reported `completed` with zero
  rows written — a false completion manufactured by the error handling.
  It was found by a missing import. There is now a regression check for
  exactly this.

### H — Integrity

- **The staleness invariant.** `file_state.content_id` is authoritative
  only when `content_observation_id = current_observation_id`, enforced
  in SQL so a stale hash cannot enter a current duplicate group even by
  accident. Verified: modify a file, re-scan without re-hashing, and the
  hash is reported stale rather than quietly wrong.
- **Missing root ≠ empty root.** `root_available` gates vanished
  detection. Verified: an unreachable root leaves all 100 files
  `present` and marks zero missing.
- **Hard links** — `volume_serial`, `file_index`, `hard_link_count`
  columns exist; NULL means *not collected*, never *no hard links*.
  Collection is opt-in and off by default. **Population is carried
  forward.**

### I — Observability

Explicit state vocabularies (`present` / `inaccessible` / `missing` /
`unverified`), truncation counters, `stale_content_count()` exposed for
the dashboard, and archive `analysis_mode`. The four-way distinction
between failed, skipped, not-applicable and unsupported is preserved.

### J — Environmental fitness — **NOT ADDRESSED**

Nothing here is Windows evidence. Carried forward in full.

---

## Also changed

- `win_meta.stat_attributes()` derives the directory bit from the POSIX
  mode off Windows, and `join_child()` uses the platform separator.
  **No behaviour change on Windows** — the branches are not reached
  there. B4.5 hard-coded a backslash, which meant the walk built paths
  no `scandir` could open and the engine could not be exercised outside
  Windows at all. B5's "unavailable evidence" sections are largely a
  list of things that could not be checked without a Windows machine;
  an engine that can be exercised on the build machine gets exercised
  more often.
- `self_check.py` expects schema 6.
- `Scripts/b6_regression.py` added — 30 checks, one per finding.

---

## Outstanding

**Not fixed, and not claimed to be:**

- **F.F009** — last-access-time preservation needs Windows API work.
- **H** — hard-link population; the columns exist, nothing fills them.
- **J** — all environmental fitness: Windows 10/11, non-admin, NTFS and
  long paths, Unicode, OneDrive placeholders, removable and network
  storage, clean launch on a machine without a dev toolchain.
- **E.F010–E.F012, E.F019–E.F020** — expected growth characteristics,
  recorded as design constraints rather than defects.
- **Windows validation of everything above.** The measurements are real
  but they are Linux measurements. The migration, the export dialects
  and the timestamp model in particular deserve a Windows run before
  this is trusted in production.

**A note on the numbers.** Every figure in this document is
reproducible via `python Scripts/b6_regression.py`. Two claims in
earlier drafts of this work were wrong and were caught by testing
rather than by review — the chunked line counter, and the archive cap
before the pre-flight was added. Both are recorded above rather than
quietly corrected, because the pattern matters more than either bug:
measurement found what inspection did not.
