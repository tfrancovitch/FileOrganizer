# Changelog — B7.1

Follows B7. B7.1 is the first fix build of the adversarial round: what the
P2 stress test — the first project ever to hold two source roots, `C:\FOTest`
beside the 41,059-file OneDrive corpus — found on 2026-09-19, and what fixing
it uncovered on 2026-09-20. `fo_db.APP_VERSION` and `Phase2.VERSION` are both
`B7.1`; every run, project and log records it, and the window's title shows it.

Schema 8, unchanged. Existing projects open as they are. The two derived
indexes rebuild themselves on first use, as at every core version. A project
scanned under B7 sees no spurious change on its next scan: attribute words
are compared as words, not as the spelling B7.1 changed (see 42).

## Fixed

Thirteen defects, numbered 33–45 in `PROJECT_PLAN.md` §4.

- **A two-root project lost its first root at the Pre-Scan** (33). The
  coordinator walks the roots one at a time into one `ScanStatistics`
  (the preliminary report is cumulative), so the second root's ingest was
  handed the first root's directory errors and path events; those resolved
  to the first root, opened its scan row, and vanished-detection — run over
  every scan the ingest had touched, with a projector that had seen none of
  that root's files — marked all 11,141 of them *missing*. The hash pass, the
  duplicate pass, the estimates and all seven analyzers then never saw them,
  and every report said COMPLETED. Each root's ingest now receives only what
  its own walk added (a window consulted after the generator drains, as
  `complete=` already was), and vanished-detection runs only for the roots
  the walk covered.
- **Finding Potential Duplicates was always recorded NO_APPLICABLE_FILES**
  (34): the status derivation looked for `PotentialDuplicates.csv`, which the
  Python candidates pass never wrote — it persists nothing, so the exporter
  had nothing to render — while the report pointed at that same file. The
  candidate list is rendered from the outcome, as the report is; none stays
  absent.
- **The hash reports' DRIFT CHECK compared the pass with itself** (35): the
  "preliminary" baseline was the engine's own input, so it read 0 / 0 on the
  run that had never received one root. It reads `PreliminaryInventory.csv`
  now, says so when it cannot, and names what a difference can mean. The
  SCAN SUMMARY "recomputed independently from FullHashInventory.csv" was
  computed from the database; it is recomputed from the CSV.
- **A re-scanned inaccessible location turned present** (36): the
  unchanged-row refresh wrote `state='present'` unconditionally, so a denied
  directory seen twice became a present, sizeless file the hash and analyzer
  passes then tried to open.
- **The Full Run estimate was 13× the run** (37): the calibration sample was
  the first fifteen files in path order — 3.3 MB of thumbnails and JSON — so
  "throughput" was open/close cost, and the per-file term was never measured.
  The throughput sample is the largest local files, at most 8 MB each and
  none that is sparse; the per-file cost is the median over the smallest
  fifteen. Stress corpus: ~17 min estimated, 10 min 20 s run.
- **Scan again wrote a three-row inventory CSV, Fingerprint again a three-row
  hash CSV** (38): both exports selected the run's *observations*, and under
  change-only history an unchanged file has none — 3 of 52,200. Both read
  the current projection, with DB_IDs that join. And an exhaustive pass
  exported the selective set too: zero candidate rows, so the writer
  truncated and removed the Pre-Scan's list. Each pass writes its own
  artifacts only.
- **`run.finalized` was never written** (39), though migration 006 and
  CHANGELOG-B6 say it is written in the transaction that sets a terminal
  status; every run read 0. `finish_run` writes it; a run closed by a later
  launch's reconciliation keeps 0, which is the case the flag exists for.
- **"Target drive type (detected): Unknown" for a fixed disk** (40), while the
  same run recorded `Fixed` in `run_source_root` from the same call.
  `win_meta.drive_type` speaks `GetDriveTypeW`'s words; the estimator still
  asks only whether the answer is Network.
- **`\\?\` left mangled in analyzer messages** (41): Pillow quotes the path
  through `repr()`, so stripping the plain prefix left `'\\\C:\\…'`. The
  escaped spelling is stripped too.
- **OneDrive files' attributes rendered as a bare number** (42): `524320` for
  Archive + Pinned, `1572902` for one that was also Hidden and System — and
  the reports' Hidden/System counts, a substring test R6 also used, could not
  see them (8 and 2 reported; 9 and 3 on disk). The four bits Windows names
  and the .NET enum lacks render by name (Pinned, Unpinned, RecallOnOpen,
  RecallOnDataAccess), any remainder as hex; every value .NET could name
  renders exactly as before. Change detection compares attribute *words*,
  so the change of spelling is not a change of the file.
- **The folded case twin was a chimera** (43; A-014, still open): two names
  differing only by case in a case-sensitive directory fold into one row,
  and the second silently overwrote the first's size, so the row flipped
  between the twins on every scan and read as "modified" each time, and the
  drift check showed 2 bytes. The first record keeps the row; the second is
  recorded as a `FOLDED_CASE_TWIN` event, warned once per scan, and counted
  in the preliminary report ("Case-only twins folded into one row"). One row
  for two files remains the defect; the hostile truth's expected bytes moved
  with it.
- **Two-root report and ingest bookkeeping** (44): "Largest top-level
  folders" merged same-named folders across roots and printed an ambiguous
  "(root)" — qualified by root when there is more than one, R6's lines
  otherwise; the header names every root. The ingest stage's "(0.0s)" — its
  work streams inside the walk — now carries the time it took. Scan rows no
  longer inherit another root's counts, notes or duration; a root's
  `was_scanned` is its own.
- **New Project's folder list was drawn behind its own row** (45): the list
  was a child of the form packed `in_` a sibling row created after it, and
  Tk stacks the later sibling above — so every Add landed in a list nobody
  could see, and Add looked like it had wiped the path and added nothing.
  The list is a child of the row it sits in, and a line under it says how
  many folders will be inventoried.

## Verification

On the final code: `b6_regression` 63 checks (19 new, marked B7.1; the
two-root one reproduces the stress-test failure in miniature against the
pre-fix ingestor), `p2_mutation_check` 37/37, `p2_hostile_check` 109/109 with
`--rebuild`, `p2_dashboard_check` 195 (all passed; one new, which adds two folders through the real form), `p2_acceptance` 709/709
against a `P2Accept` rebuilt from scratch by B7.1. The stress project,
re-created and then scanned and fingerprinted again: 52,200 rows, both roots
in every pass, 52,177 hashed, the 23 designed hostile errors, drift +0 / +0.

`SOURCE_SHA256.csv` lists 97 files; the twelve corpus-suite files that
rework16 had listed twice (`/` and `\` spellings) are listed once. The
acceptance suite's "no Office automation in the program" check now excludes
the suites by file name and the backup folders by prefix: a kept copy of the
corpus builder under `rework17` had tripped it.

## Known and unchanged

- A-014 (= Y-009, Y-053, W-003) still keeps one row for a case-only pair;
  the row is now the first file's, and the second is visible. The fix is
  per-directory case sensitivity in the path key — a B7.2 item if such trees
  turn up in real use.
- The `Attributes` column diverges from R6 only on rows R6 rendered as a
  bare number; the R5 byte-equivalence on the controlled suite stands.
- Everything B7 listed as known and unchanged.
