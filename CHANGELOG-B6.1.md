# CHANGELOG — B6.1

B6.1 is the **A–F reconciliation build**. B6 solved the major B5-E/B5-F scale and determinism problems; B6.1 brings the earlier Correctness, Completeness, Simplicity and Efficiency findings into the same architecture and fixes integration defects discovered while doing so.

See [`B5_A-F_RECONCILIATION.md`](B5_A-F_RECONCILIATION.md) for the finding-by-finding ledger.

## Correctness

- One canonical extension rule now serves scan and export (`.gitignore`, `trailing.` regression fixtures included).
- Folder-scoped exports fail visibly when a run has no `run_folder` binding instead of emitting apparently successful empty artifacts.
- Text decoding is BOM-aware and strict-UTF-8-first before probabilistic fallback.
- Pre-B6 local-naive timestamps remain local-naive; UTC is never invented.

## Current-state / completeness model

- Schema **7** extends `file_state` into the complete current projection used by hash, analyzer, export and estimate stages.
- Unchanged files remain current downstream inputs without creating duplicate historical observations.
- Physical identity fields are populated where available: volume/device ID, file index/inode, hard-link count, allocated size and reparse tag.
- Empty directories and deliberately skipped reparse directories are persisted as scan coverage events.
- Timestamp availability is recorded per timestamp.
- Selective-run “intentionally not hashed because unique by size” is an explicit current state, not an ambiguous NULL.
- Image perceptual hash parameter provenance includes `HashSize`.

## Simplicity / cleanup

- Removed the retired standalone analyzer CSV/checkpoint/report orchestration; the in-process database-backed analyzer engine is canonical.
- Removed dead inventory/hash CSV-ingestion entry points and their unreachable parsing subgraphs.
- Removed Dashboard's unreachable PowerShell pre-scan fallback.
- Installation integrity now derives from `SOURCE_SHA256.csv` rather than a second partial file roster.
- Analyzer engine/persistence/export/database key sets and loaded extension contracts are executable regression invariants.
- Dependency rosters reconciled.

## Efficiency

- Hash measurement persistence uses a single idempotent UPSERT rather than INSERT+UPDATE per row.
- Hash/analyzer stage exports are scoped; unrelated preliminary inventory is not regenerated.
- Content lookup includes `project_id`, using the leading key of the `(project_id, sha256)` unique index.
- Canonical current exports do not reparse product-owned timestamps through `strptime`.
- The 64-KB reread on selective escalation is deliberately retained: removing it would add candidate-state memory and temporal-coherence complexity for a bounded read.
- The PDF two-parser path is deliberately retained because the parsers supply different required facts and no equivalent one-parser implementation was established.

## B6 scale/reliability work preserved

- current-state duplicate queries remain flat with run history;
- exports stream with bounded memory;
- archive analysis has complete/capped/summary-only modes and EOCD/ZIP64 preflight;
- analyzer results persist incrementally through a sink;
- extracted text is content-addressed and sharded;
- word counting avoids token-list amplification;
- deterministic traversal/root/tie ordering remains;
- canonical timestamps remain ISO-8601 UTC.

## Integration defects found while building B6.1

B6.1 intentionally records these because they are exactly why internal regression precedes external acceptance.

- B6's change-only history caused unchanged files to disappear from hash/analyzer inputs on later scans. Hash, analyzer and estimator loaders now read current state.
- A sink exception could be swallowed by the analyzer engine and leave a false `completed` outcome. Persistence failure now fails the analyzer.
- Archive-member child rows could be lost in sink mode because retained results are empty. Child rows now persist per batch.
- A valid UTF-8 file could be decoded as a legacy single-byte encoding by `chardet`; strict UTF-8 now wins.

## Verification in the build environment

- full regression suite: **44/44 PASS** before final packaging;
- schema-6 database created by B6 upgraded through migration 007 to schema 7;
- automatic pre-migration backup created;
- `PRAGMA integrity_check = ok`;
- `PRAGMA foreign_key_check` returned no rows;
- `self_check.py` passed core checks; missing analyzer libraries were reported as optional rather than fatal.

These are **not Windows acceptance evidence**. B6.1 still requires native Windows adversarial/operational verification before production use.
