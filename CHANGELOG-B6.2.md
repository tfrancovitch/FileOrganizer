# Changelog — B6.2

Follows B6.1. Windows acceptance testing (see the acceptance report) confirmed
B6.1 correct on the parts that are hard to get right, and produced this set of
fixes. No database or schema change; existing projects keep working.

## Fixed

**Run-diagnostics contextmanager (`RunCoordinator._db`).**
A `@contextmanager` that yielded twice on a body exception, so every failure
inside a `with self._db()` block was reported as
`RuntimeError: generator didn't stop after throw()` instead of its real cause.
Setup and body are now separate; the original exception propagates intact.

**Inventory CSV export (`fo_exports.export_run_inventory`).**
Called `len()` on a streaming generator, which raised `TypeError` *after* the CSV
had been written correctly and in full — so every Windows run reported
"the inventory was recorded but its CSV export failed". The row count the writer
already returns is used instead.

**Physical identity on Windows (`fo_scan`, `win_meta`).**
`file_index` and `hard_link_count` were NULL on every NTFS row because identity
was read from `os.DirEntry.stat()`, which returns zeros for those on Windows.
A full `os.stat()` of the path is now used (`win_meta.physical_identity_for_path`),
restoring hardlink / physical-copy detection and the `reclaimable_bytes` figure.
Validated on real data: 8,012 duplicate sets, 100% with a complete physical
rollup. `win_meta.stat_physical_identity` also now treats a zero volume serial as
unknown, matching how it already treated a zero index and link count.

**Cloud / online-only files are never opened (`fo_hash_engine`, `fo_scan`,
`win_meta`).**
Pointed at a OneDrive folder with "Files On-Demand" freeing up space, a Duplicate
Run or Full Run would open online-only placeholders to hash them — which makes
Windows download them. The inventory now recognises modern placeholders (they
carry `RECALL_ON_DATA_ACCESS` / `RECALL_ON_OPEN`, not the legacy `OFFLINE`
attribute), and the hash engine skips any file marked cloud-only, recording it
with `hash_status = skipped_cloud_only` and no digest. Matches the analyzer
engine's existing behaviour. Can be turned off with `skip_cloud_only=False`.

**A partial scan is no longer reported as complete (`fo_inventory_records`,
`RunCoordinator`).**
If a folder became unreadable mid-scan (a network share dropping, a removable
drive removed), the run still finished `completed` with no warning and the
source folder marked fully scanned. Now: any inaccessible path raises a warning
(previously only past a 5,000-path cap), so the run reports
`completed_with_warnings`; and a root whose walk could not list a directory is
recorded as **not** fully scanned, so a later phase can see the coverage gap.

**Legacy text decoding (`fo_text.decode_bytes`).**
The byte-order-mark table used mis-escaped literals and never matched, so a
UTF-8-with-BOM file kept an invisible U+FEFF at the start of its extracted text
and was mislabeled `utf-8`. Fixed. The character-set detector's guess is now
trusted only above a confidence threshold; below it, a lossless fallback keeps
word and character counts correct rather than decoding through a wrong codec.

## Documentation

- README disk estimate corrected from ~1 GB to ~1.7 GB per million files
  (measured on real data).

## Known and unchanged

- `conn.rollback()` is a no-op under the autocommit connection. A multi-step
  write that fails halfway leaves its completed half in place. Deliberate for
  Phase 1 (a real transaction would hold the database lock for whole write
  blocks); to be revisited before any later phase moves, renames or deletes a
  file.
