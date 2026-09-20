# Changelog — B7.2

Follows B7.1. B7.2 closes the adversarial round (P2.13): the six defects the
Master Matrix corpus had recorded as DEFECT cases — asserted at their wrong
behaviour so the suites stayed green — are fixed, and each case now asserts
the behaviour the matrix expects. `fo_db.APP_VERSION` and `Phase2.VERSION`
are both `B7.2`; every run, project and log records it, and the window's
title shows it.

Schema 8, unchanged. Existing projects open as they are; the two derived
indexes rebuild themselves on first use, as at every core version. Two
vocabularies grew, no table changed: `hash_status` gains `skipped_link`, and
`file_state.state` finally uses the `unverified` value migration 006 defined.

## Fixed

Six defects, numbered 46–51 in `PROJECT_PLAN.md` §4.

- **Files under a folder that could not be listed were marked missing**
  (46; D-003c). Vanished-detection marked every location a completed scan
  had not seen, and a directory error did not stop it — so the files under
  a folder whose ACL was changed, or under a share that dropped or a drive
  that was pulled mid-walk, read as gone. The walker now records whether
  a failed directory *exists and could not be listed* or *is not there*
  (`ScanError.absent`); what lies under an unlistable one becomes
  `unverified` — migration 006's "absence of evidence, not absence" —
  and a scan that lists the folder again finds it `reappeared`. What lies
  under a directory the OS reports as gone is missing, as before. The run
  summary and the scan's notes say how many files were left unverified.
- **A file symbolic link was hashed as its target** (47; C-011). The row
  was the link (a reparse point, size 0), but the hash stage opened the
  path, Windows resolved it, and the link joined the target's duplicate
  group offering the target's bytes as reclaimable — deleting the link
  reclaims nothing, deleting the target breaks the link. A junction or
  symbolic link to a file is now handed to the engine as what it is and
  never opened: `hash_status` `skipped_link`, no digest, no group, in both
  the Duplicate Run and the Full Run. The project summary counts links
  apart from files that could not be read; a link's verdict is that it is
  a link.
- **A file rewritten between listing and hashing kept the listed size
  beside the new digest** (48; I-005). The hash stage now compares the
  size at open time with the size the inventory listed; a difference is
  recorded as `CHANGED SINCE LISTING` with no digest — the new bytes'
  digest beside the old size would have been an inconsistent pair that
  nothing marked stale — and the message says to scan again.
- **A garbage PDF date failed the whole PDF analysis** (49; E-008b).
  pypdf raises on a `/CreationDate` it cannot parse; that now empties one
  field (`garbage (unparseable)`) and the file is analysed.
- **A colourised terminal log was refused as binary** (50; Y-020c). ESC
  counted as a control character, so a 3 KB CI log with 180 colour codes
  was "not a recognised document format (binary)". Terminal escape
  sequences (CSI, OSC and the two-byte forms) are removed where text is
  decoded — the one place the binary gate, every reader and the word
  counts share — so the gate sees text and the stored text is what a
  terminal would have shown.
- **An archive member named as text was decoded without a sniff** (51;
  Y-022b). 100 KB of random bytes named `.txt` inside a zip became
  102,419 characters of stored, indexed "text". A member named as text
  now meets the same binary gate as a top-level file and yields a
  one-line note instead.

## Not fixed, by decision

- **A-014** (= Y-009, Y-053, W-003): a case-only pair in a case-sensitive
  directory still keeps one row. B7.1 made the fold stable and visible
  (the first file's row; the second a `FOLDED_CASE_TWIN` event, counted in
  the report). The per-directory case-sensitivity fix was declined for
  B7.2 on 2026-09-20; the case stays recorded as DEFECT.

## Verification

On the final code: `b6_regression` 73 checks (10 new, marked B7.2),
`p2_mutation_check` 36/36 (one fewer: I-005 asserts the error kind rather
than the inconsistent pair), `p2_hostile_check` 109/109 with
`--rebuild`, `p2_acceptance` 710/710 (one more: the new key) against a `P2Accept` rebuilt
from scratch by B7.2 over a rebuilt `Corpus\`, `p2_dashboard_check`
195 (all passed). The `CASE_RESULTS*.json` `defects` lists hold A-014's four
case IDs and nothing else.

`p2_cases` gains one assertion key, `extracted_content.char_count_lt`.
`SOURCE_SHA256.csv` lists 99 files: `Scripts\Phase2\capability.py`, never listed,
joins it, and `CHANGELOG-B7.2.md`.

## Known and unchanged

- A-014, above.
- An analyzer still reads *through* a file symbolic link (the text of the
  target under the link's row); only the hash and duplicate passes treat a
  link as a link. The analyzer engine has the same skip mechanism as for
  cloud-only files, should that be wanted.
- `CHANGED SINCE LISTING` compares sizes; a rewrite that keeps the size
  is caught by the next scan (its modified time), not by the hash stage.
- Everything B7.1 listed as known and unchanged.
