# Resources\Phase3 — what is here and why

Reference material from the Phase 3 research that the product's checks run
against. None of it is read at runtime by the window; it is test data and
provenance.

| Path | What | Used by |
|---|---|---|
| `keeper_rule_catalog.json` | The P3.R2 keeper-rule catalog: the resolution order and the rules `Phase3\resolve.py` implements. Byte-identical to the research's `The_File_Organizer_P3.R2_Keeper_Rule_Catalog.json`. | `p3_regression.py` asserts the code's order equals the catalog's |
| `fixture_lab\Fixtures\F01…F16` | Fourteen of the sixteen synthetic fixtures (thirteen of P3.R11's fifteen, and F16) — the five that test Build 1–2 scope (F01 keeper + protected root + hard link; F02 multiple intentional keepers; F03 folder priority + explicit exception; F09 supersession / undo / reapply; F15 projection rebuild), the two for Build 3 (F08 deferred versus evidence-blocked; F10 revalidation after an observation changed), the two for Build 4 (F04 a frozen query-result batch that does not acquire a later match; F05 a policy made before a file existed covering it with no decision) and the two for Build 5 (F06 A~B and B~C strong, A~C weak — decided per pair, nothing inferred; F07 an edited crop as a related variant kept alongside its original, never ordinary cleanup) -- and, added in B14, **F16** (P3.R3 §34's non-edge: A~B and B~C are candidates, A~C is not; F06 without its weak pair, the shape B13's Survey Mode proof built by hand) -- and, added in B15 for Build 8, **F11** (a locked, fingerprinted plan revision made obsolete when its source decision changes; never rewritten) and **F12** (a hard precondition that resolves to unknown blocks the item; unknown is not pass), copied byte-for-byte from the research lab and checked against its manifest. Each is a SQLite file with synthetic evidence, authoritative Phase 3 rows and an `expected_projection` oracle. | `p3_fixture_check.py` |
| `fixture_lab\validate_fixtures.py` | The research's own reference validator — hand-written per fixture. Reference logic, not product code; the product's check uses the general resolver instead. B14 added one branch, F16's, computed from the rows alone like the others; it had F11's and F12's from the start (it reports 14/14 on the fixtures shipped here). It writes `VALIDATION_RESULTS.json` beside itself if run; it is not run by any suite here. | — |
| `fixture_lab\MANIFEST.json` | The research's manifest of the lab, with SHA-256 per file; B14 added F16's three files and re-hashed the two it changed (`README.md`, `validate_fixtures.py`). The forty-five files shipped here match it exactly; the other entries belong to fixtures for Build 9 (and the research's `.bat` runner and results file) and are not shipped. | provenance |
| `fixture_lab\README.md`, `QUERY_EXAMPLES.md` | The research's notes, kept as received but for one B14 sentence in `README.md` naming F16 and what the product ships (they describe the full lab and its `.bat` runner, which is not shipped). | — |

The fixture lab's schema is the research's, not the product's: its
`p3_decision.withdrawn` and `p3_policy_version.enabled` flags are what
`p3_fixture_check.py` translates into the product's withdrawal rows and
policy versions, its `evidence_location.protected` flag becomes a
`protect_source_root` policy, its one-observation evidence binding
(`obs:OBS1`) becomes a `[file_path_id, observation_id]` pair, its
`p3_review_event` rows load as the product's review events (a `time`
deferral's date sits in `return_condition` there), and its `p3_bulk_batch`
/ `p3_bulk_member` rows load as the product's batches (the lab predates the
per-member disposition; every member there is one the batch decided). Its `evidence_visual_candidate` rows load as the product's visual candidate
pairs (the lab's `score` kept as the method's own score, never as a
percentage), and a lab fixture holding only the tables its scenario needs
loads with the others empty. The
product's own schema is `Scripts\Database\migrations\009_phase3_decisions.sql`,
`010_phase3_review_events.sql`, `011_phase3_bulk_batches.sql` and (the
visual candidates, Phase 2 evidence) `012_visual_candidates.sql`.
