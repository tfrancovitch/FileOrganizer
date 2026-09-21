# Resources\Phase3 — what is here and why

Reference material from the Phase 3 research that the product's checks run
against. None of it is read at runtime by the window; it is test data and
provenance.

| Path | What | Used by |
|---|---|---|
| `keeper_rule_catalog.json` | The P3.R2 keeper-rule catalog: the resolution order and the rules `Phase3\resolve.py` implements. Byte-identical to the research's `The_File_Organizer_P3.R2_Keeper_Rule_Catalog.json`. | `p3_regression.py` asserts the code's order equals the catalog's |
| `fixture_lab\Fixtures\F01…F15` | Seven of the fifteen P3.R11 synthetic fixtures — the five that test Build 1–2 scope (F01 keeper + protected root + hard link; F02 multiple intentional keepers; F03 folder priority + explicit exception; F09 supersession / undo / reapply; F15 projection rebuild) and the two for Build 3 (F08 deferred versus evidence-blocked; F10 revalidation after an observation changed). Each is a SQLite file with synthetic evidence, authoritative Phase 3 rows and an `expected_projection` oracle. | `p3_fixture_check.py` |
| `fixture_lab\validate_fixtures.py` | The research's own reference validator — hand-written per fixture. Reference logic, not product code; the product's check uses the general resolver instead. It expects all fifteen fixtures and writes `VALIDATION_RESULTS.json` beside itself if run; it is not run by any suite here. | — |
| `fixture_lab\MANIFEST.json` | The research's manifest of the full fifteen-fixture lab, with SHA-256 per file. The twenty-four files shipped here match it exactly; the other entries belong to fixtures for Builds 4–7 and are not shipped. | provenance |
| `fixture_lab\README.md`, `QUERY_EXAMPLES.md` | The research's notes, kept as received (they describe the full lab and its `.bat` runner, which is not shipped). | — |

The fixture lab's schema is the research's, not the product's: its
`p3_decision.withdrawn` and `p3_policy_version.enabled` flags are what
`p3_fixture_check.py` translates into the product's withdrawal rows and
policy versions, its `evidence_location.protected` flag becomes a
`protect_source_root` policy, its one-observation evidence binding
(`obs:OBS1`) becomes a `[file_path_id, observation_id]` pair, and its
`p3_review_event` rows load as the product's review events (a `time`
deferral's date sits in `return_condition` there). The product's own schema
is `Scripts\Database\migrations\009_phase3_decisions.sql` and
`010_phase3_review_events.sql`.
