# Resources\Phase3 — what is here and why

Reference material from the Phase 3 research that the product's checks run
against. None of it is read at runtime by the window; it is test data and
provenance.

| Path | What | Used by |
|---|---|---|
| `keeper_rule_catalog.json` | The P3.R2 keeper-rule catalog: the resolution order and the rules `Phase3\resolve.py` implements. Byte-identical to the research's `The_File_Organizer_P3.R2_Keeper_Rule_Catalog.json`. | `p3_regression.py` asserts the code's order equals the catalog's |
| `fixture_lab\Fixtures\F01…F16` | All sixteen synthetic fixtures (P3.R11's fifteen, and F16) — the five that test Build 1–2 scope (F01 keeper + protected root + hard link; F02 multiple intentional keepers; F03 folder priority + explicit exception; F09 supersession / undo / reapply; F15 projection rebuild), the two for Build 3 (F08 deferred versus evidence-blocked; F10 revalidation after an observation changed), the two for Build 4 (F04 a frozen query-result batch that does not acquire a later match; F05 a policy made before a file existed covering it with no decision) and the two for Build 5 (F06 A~B and B~C strong, A~C weak — decided per pair, nothing inferred; F07 an edited crop as a related variant kept alongside its original, never ordinary cleanup) -- and, added in B14, **F16** (P3.R3 §34's non-edge: A~B and B~C are candidates, A~C is not; F06 without its weak pair, the shape B13's Survey Mode proof built by hand) -- and, added in B15 for Build 8, **F11** (a locked, fingerprinted plan revision made obsolete when its source decision changes; never rewritten) and **F12** (a hard precondition that resolves to unknown blocks the item; unknown is not pass) -- and, added in B16 for Build 9, **F13** (a desired Creator on a read-only field: an Update Source Metadata item whose blocked_reason is writer_unsupported and whose state is unsupported_action) and **F14** (a signed PDF and a hard-linked JPEG blocked by evidence alone, with no plan anywhere), each copied byte-for-byte from the research lab and checked against its manifest. Each is a SQLite file with synthetic evidence, authoritative Phase 3 rows and an `expected_projection` oracle. | `p3_fixture_check.py` |
| `fixture_lab\validate_fixtures.py` | The research's own reference validator — hand-written per fixture. Reference logic, not product code; the product's check uses the general resolver instead. B14 added one branch, F16's, computed from the rows alone like the others; it had F11's to F14's from the start (it reports 16/16 on the fixtures shipped here). It writes `VALIDATION_RESULTS.json` beside itself if run -- so (B19) `Scripts\p3_lab_harness.py` runs it only in a temporary copy of the lab, as shipped and under `-X dev`, and requires exit 0, 16/16 and an empty stderr (R11 gate 7), recording the proof in `Logs\p3_lab_validation.json`. | `p3_lab_harness.py` |
| `fixture_lab\MANIFEST.json` | The research's manifest of the lab, with SHA-256 per file; B14 added F16's three files and re-hashed the two it changed (`README.md`, `validate_fixtures.py`). B19's harness found its `README.md` entry stale (the product's README was edited again in B15-B16) and re-hashed it. The fifty-one files shipped here match it exactly (the harness checks); its other entries (the research's `.bat` runner and results file) are not shipped. | provenance; `p3_lab_harness.py` |
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
visual candidates, Phase 2 evidence) `012_visual_candidates.sql`, `013_phase3_plans.sql` and
`014_phase3_metadata.sql`. Its `evidence_metadata_question` rows (F13, F14) load as the
product's metadata questions: `writer_capability` is the capability record's status, the
signed and hard-linked flags the integrity evidence; its metadata decisions are kept apart
from the retention model, as the product's loader keeps them.

## fingerprint_vectors.json (B18)

The golden test vectors of the plan fingerprint
(`Docs\Specifications\PLAN_FINGERPRINT_SPEC.md`, payload schema
`fileorganizer.p3.plan-payload/1`): nine fixed payloads -- an empty plan, a
removal with every kind of precondition result, a non-ASCII path composed
and decomposed (two hashes: strings are hashed as stored), an Update Source
Metadata item, a blocked item, an unpaired surrogate, escaping and key order,
two items -- each with its canonical UTF-8 bytes (hex) and SHA-256.
`Scripts\p3_fingerprint_check.py` recomputes every one through the product's
`plans.canonical` and through an independent encoder written from the
specification alone. The file is ASCII; its JSON escapes are its own.

## registry.schema.json (B19)

The rule registry as JSON Schema (draft 2020-12; `$id`
`urn:fileorganizer:p3:registry:2`; `version` `fileorganizer.p3.registry/2`):
one definition per decision kind (its targets and value shape) and per policy
kind (its scope and effect shapes), the project-metadata and semantic-field
value shapes. **Generated** from `Scripts\Phase3\registry.py` by
`Scripts\Phase3\registry_schema.py` -- never edit it by hand; after a registry
change run `py -3 Scripts\p3_registry_schema_check.py --write`.
`p3_registry_schema_check.py` fails if this file differs from a fresh
generation, and proves with the independent `jsonschema` package that the
schema accepts a value exactly when the code does, and that every decision
and policy version the fixtures and the suites record is valid. The window
never reads it; `registry.registry_digest()` covers its bytes.
