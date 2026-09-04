# B5 A–F Reconciliation — B6.1

**Artifact:** The File Organizer, Phase 1, B6.1  
**Purpose:** Trace every material B5-A through B5-F finding into a B6.1 design disposition.  
**Rule:** A finding may be true without its adversary's implied remedy becoming the design. B6.1 resolves conflicts across Correctness, Completeness, Simplicity, Efficiency, Scalability and Reliability.

## Reconciliation decisions that govern B6.1

1. **Current state and history are different things.** `file_state` answers what is true now; `file_observation` is change history; measurements keep provenance.
2. **Location, physical object and content are different identities.** A path is not a file object; a physical object is not a content digest; presentation order is neither.
3. **Selective duplicate analysis remains selective.** A unique-size file is deliberately not hashed. B6.1 records that state explicitly rather than pretending a hash exists. A Full Run remains the route to complete content identity.
4. **Derived artifacts are outputs, not a second architecture.** SQLite is authoritative. The retired analyzer CSV/checkpoint execution pipeline was removed. Exports stream from the database.
5. **Bounds must be honest.** Bounded diagnostics/archive detail are allowed only when the database records that a bound was applied and what was omitted.
6. **Do not fabricate old facts.** Pre-B6 local-naive timestamps are not retroactively converted to UTC. Missing physical identity from historical scans remains NULL.
7. **Measured cost alone does not authorize weakening truth.** The 64-KB selective-hash reread and the PDF double parse remain where eliminating them would require new state or weaken the fact set.

---

## B5-A — Correctness

| Finding | B6.1 disposition | Result |
|---|---|---|
| **A.F001** Exported `Extension` disagrees with scan/database semantics | Exporters now use the same `.NET`-compatible extension function as the scanner; edge cases `.gitignore` and `trailing.` are regression-tested. | **FIXED** |
| **A.F002** `fo_exports.py` documentation contradicts shipped behaviour | Export documentation now states SQLite authority, streaming/current exports, and the deliberately retained Alpha-equivalence surface. | **FIXED / DOC** |
| **A.F003** Live-looking CSV→DB ingestion path has no caller | Dead inventory/hash CSV ingestion entry points and subgraphs removed. | **REMOVED** |
| **A.F004** NULL `run_folder` silently renders empty exports | Folder-scoped export selection now raises `ExportError` when the run has no folder binding. | **FIXED** |
| **A.F005** Migration commentary says CSV is authoritative | Historical comments are explicitly labelled historical; current docs state SQLite authority. | **FIXED / DOC** |
| **PM-discovered** valid UTF-8 can be mis-decoded by `chardet` | BOM-aware Unicode and strict UTF-8 are tried before probabilistic fallback; Unicode fixture added. | **FIXED** |

---

## B5-B — Completeness

| Finding | B6.1 disposition | Result |
|---|---|---|
| **B.F001** Physical storage identity not preserved; hard links overstate physical copies/reclaim | Current and observation models carry volume/device identity, file index/inode, hard-link count and allocated size where available. Duplicate presentation distinguishes locations from physical copies. | **FIXED FOUNDATION; WINDOWS VERIFICATION PENDING** |
| **B.F002** No move/rename successor relationship | B6.1 does **not** manufacture a predecessor table. Same-volume continuity can be reconstructed from physical object identity; content continuity from complete hashes. Explicit move/rename workflows remain later phase. | **RECONCILED / NO EXTRA TABLE** |
| **B.F003** Unique-size files lack content identity in selective run | Preserved deliberately. `hash_status='size_unique'`, measurement mode and NULL content identity make the state explicit. Full Run exists when complete identity is required. | **ACCEPTED DESIGN TRADEOFF** |
| **B.F004** Deliberately unwalked subtrees leave no trace | Empty directories and skipped reparse directories are persisted as `scan_path_event` facts. Inaccessible coverage remains explicitly counted/capped. | **FIXED FOUNDATION** |
| **B.F005** Reparse point stored as bare boolean | Reparse tag is now preserved where available. | **FIXED** |
| **B.F006** No allocated size | Allocated size is captured where the filesystem/runtime exposes it. | **FIXED FOUNDATION; ENVIRONMENT-DEPENDENT** |
| **B.F007** Inaccessible capture capped silently | Cap remains for scalability, but `seen`, `cap`, and `truncated` are persisted and surfaced. | **RECONCILED: BOUND RETAINED, SILENCE REMOVED** |
| **B.F008** Perceptual hash lacks parameter provenance | Image hash result persists `HashSize`. | **FIXED** |
| **B.F009** One aggregate `timestamps_parsed` masks per-field failure | Per-timestamp `known/unavailable/unknown` states added to observation/current state. | **FIXED** |
| **B.F010** Directory failure represented in file-location model | Retained for attributable inaccessible-location diagnostics; current-file queries require `state='present'`. Scan-only directory facts use `scan_path_event`. | **ACCEPTED / NARROWED SEMANTICS** |
| **B.F011** Native Windows identity semantics unavailable in adversary environment | B6.1 implements the foundation but does not claim Windows proof. | **DEFERRED TO WINDOWS ACCEPTANCE** |

---

## B5-C — Simplicity / Cleanliness

| Finding | B6.1 disposition | Result |
|---|---|---|
| **C.F001** Second complete analyzer pipeline ships beside live engine | Standalone CSV/checkpoint/report analyzer orchestration removed. Per-file analyzer functions remain and are called only by the in-process engine. | **REMOVED** |
| **C.F002** Dead R3/R4 CSV-ingestion machinery | Dead inventory/hash ingestion methods and parsing subgraphs removed. | **REMOVED** |
| **C.F003** Analyzer roster independently declared in many places | Runtime still has specialized registries, but an executable invariant now requires engine, persistence, export and seeded-DB analyzer keys to agree. | **CONSOLIDATED BY CONTRACT** |
| **C.F004** Duplicated extension invariant claimed verifier-checked | Regression suite now verifies loaded analyzer extension/exclusion declarations against adapters. | **FIXED** |
| **C.F005** Unreachable PowerShell fallback in Dashboard | Removed. | **REMOVED** |
| **C.F006** Version constants proliferated/inconsistent | Dead/unread constants removed where encountered; remaining module versions are local diagnostics, while schema authority is `fo_db.APP_SCHEMA_VERSION`. | **REDUCED / RECONCILED** |
| **C.F007** Repeated `ensure_schema()` guards | Retained as small defensive/diagnostic boundaries with subsystem-specific errors. | **ACCEPTED DEFENSIVE REDUNDANCY** |
| **C.F008** Multiple long-path/path-key boundaries | Retained only where platform/open-boundary semantics differ; canonical stored/exported paths remain ordinary paths. | **ACCEPTED ARCHITECTURAL BOUNDARY** |
| **C.F009** Analyzer table duplicates registry fact | Retained because it is persistence identity/provenance, not runtime discovery authority. Registry consistency is executable. | **ACCEPTED REPRESENTATIONAL REDUNDANCY** |
| **C.F010** `run_source_root` write-only | Read helper added; table remains run provenance. | **FIXED** |
| **C.F011** Stale architecture descriptions | README, architecture, analyzer/content-extraction documentation refreshed for B6.1. | **FIXED** |
| **C.F012** Installation integrity roster incomplete | `Test-Installation.ps1` now consumes `SOURCE_SHA256.csv` instead of a partial hand-maintained file list. | **FIXED** |
| **C.F013** Dependency rosters disagree | Dependency declarations/install/self-check reconciled, including `olefile`, `py7zr`, `pillow-heif`. | **FIXED** |
| **C.F014** Unconsumed read-helper families | Leaf helpers with low coupling are retained when they support diagnostics/queryability; dead ingestion helpers were removed. | **PARTIAL CLEANUP / ACCEPTED LOW COST** |
| **C.F015** Zero-caller duplicate `sha256_file` helper | Removed during cleanup; the live hash path remains canonical. | **REMOVED** |
| **C.F016** Status vocabulary spread across modules | Distinct vocabularies remain because they describe different subjects; B6.1 adds explicit current hash/timestamp states rather than flattening meaning. | **ACCEPTED SEMANTIC DIVERSITY** |
| **C.F017** Module-level mutable objects/caches | Dead CSV-path cache removed with its pipeline; remaining caches/tables are bounded or effectively constant. | **REDUCED / ACCEPTED** |

---

## B5-D — Efficiency

| Finding | B6.1 disposition | Result |
|---|---|---|
| **D.F001** `hash_measurement` inserted then immediately updated | Engine persistence uses one idempotent UPSERT per measurement. | **FIXED** |
| **D.F002** Escalated file rereads initial 64 KB | Retained. Avoiding it would require keeping/reusing digest/read state across stages, increasing candidate-count memory and temporal-coherence complexity. | **ACCEPTED TRADEOFF** |
| **D.F003** Duplicate Run regenerates unchanged preliminary inventory | Stage exports are scoped; hashing/analyzers no longer regenerate unrelated inventory artifacts. | **FIXED STRUCTURALLY** |
| **D.F004** Product reparses its own timestamps with `strptime` | Canonical exports use canonical timestamp fields directly. Parsing remains only in labelled Alpha-equivalence formatting. | **REMOVED FROM CURRENT CONTRACT** |
| **D.F005** Content lookup omits leading `project_id` index key | Content lookup now includes `project_id=1`, matching the unique index prefix. | **FIXED** |
| **D.F006** PDF parsed by both `pypdf` and `pdfplumber` | Retained: the parsers supply different required facts; no single-parser equivalent was proven. | **ACCEPTED JUSTIFIED COST** |
| **D.F007** One `ffprobe` process per media file | Dependency limitation retained/documented. | **EXPECTED LIMITATION** |
| **D.F008** Per-file `which(ffprobe)` | Measured negligible; no dedicated optimization. | **NO ACTION** |
| **D.F009** Estimator calibration rereads bounded sample | Retained as bounded estimation cost; estimator now models current file count/stages honestly. | **ACCEPTED TRADEOFF** |

---

## B5-E — Scalability / Resource Behavior

| Finding | B6.1 disposition | Result |
|---|---|---|
| **E.F001** Full observation population recreated every run | `file_state` current projection + change-only observations. | **FIXED** |
| **E.F002** Duplicate query degrades with history | Current duplicate query reads `file_state`, not all historical measurements. | **FIXED** |
| **E.F003** Fully materialized exports | Streaming exports. | **FIXED** |
| **E.F004** Missing duplicate-member index | Added and plan-regression-tested. | **FIXED** |
| **E.F005** Missing observation ordering index | Added. | **FIXED** |
| **E.F006** Unbounded archive member cardinality | EOCD/ZIP64 preflight plus complete/capped/summary-only modes. | **FIXED / BOUNDED** |
| **E.F007** All analyzer outcomes retained | Result sink persists incrementally; zero retained outcomes in sink mode. | **FIXED** |
| **E.F008** Flat per-path extracted-text artifacts | Content-addressed sharded extraction with reuse. | **FIXED** |
| **E.F009** `text.split()` memory amplification | Streaming/exact word counting without token-list materialization. | **FIXED** |
| **E.F010** Path text stored redundantly | Some path redundancy remains for provenance/queryability; current-state model avoids multiplying it per unchanged run. | **ACCEPTED / REDUCED GROWTH** |
| **E.F011** Root count multiplies SQL statements | Batching retained; high-root-count overhead accepted pending real use evidence. | **EXPECTED GROWTH** |
| **E.F012** Worst-case collision bucket reads all candidate bytes | Fundamental to exact duplicate confirmation. | **EXPECTED GROWTH** |
| **E.F013** Estimator lacks per-file term | Per-file term and explicit stage terms added; inputs read current state. | **FIXED** |
| **E.F014–F018, F024, F031, F043** Positive scalability properties | Preserve linear inventory/grouping, constant-memory full hashing, flat indexed lookup, bounded report output, project isolation/collision safety. | **PRESERVE VIRTUES** |
| **E.F019–F020** Group-count / integrity-check growth | Expected database work; no false constant-time claim. | **EXPECTED GROWTH** |
| **E.F021–F023** Unresolved high-scale memory interactions | Not claimed resolved by Linux tests. | **DEFERRED TO B6.1 ADVERSARIAL SCALE TEST** |
| **E.F044** 5,000 inaccessible-path cap | Cap is explicit and persisted with seen/stored/truncated counts. | **RECONCILED** |
| **E.F045/F046/F035** Windows/media/NTFS environment unavailable | Not converted to PASS. | **DEFERRED TO ENVIRONMENTAL ACCEPTANCE** |

---

## B5-F — Reliability / Determinism

| Finding | B6.1 disposition | Result |
|---|---|---|
| **F.F001** Encounter order controls identifiers | Stable semantic identity separated from deterministic sort/presentation keys. | **FIXED** |
| **F.F002** Top-N ties depend on arrival | Explicit deterministic tie-breaks. | **FIXED** |
| **F.F003** `*_utc` held local-naive values | Honest local-naive columns + real UTC fields; old values not fabricated. | **FIXED** |
| **F.F004** Regional settings alter/blank exports | Canonical machine-readable exports use ISO-8601; locale formatting isolated to labelled legacy-equivalence display. | **FIXED** |
| **F.F005/F006/F007/F010** Repetition/rebuild/isolation/content-identity virtues | Regression suite preserves them. | **PRESERVE VIRTUES** |
| **F.F008** Root order changes incidental IDs/order | Root ordinals derived deterministically from root keys. | **FIXED** |
| **F.F009** Reads may change LastAccessTime | Explicitly documented source-observation limitation; B6.1 does not write timestamps back. | **EXPECTED LIMITATION** |
| **F.F011** Native Windows evidence unavailable | Not converted to PASS. | **DEFERRED TO WINDOWS ACCEPTANCE** |

---

## B6.1 newly discovered integration defects resolved during implementation

These were not B4.5 findings; they were caught while reconciling B6.

1. **Current-state downstream omission.** B6 wrote observations only on change but hash/analyzer loaders still consumed current-scan observation rows, so unchanged files could disappear from later stages. B6.1 makes hashing, analyzers, exports and estimator consume the current `file_state` projection scoped to scans/roots verified by the current run.
2. **Analyzer sink false completion.** A persistence-sink exception could be logged while the analyzer outcome still reported completed. Persistence failure now makes the analyzer fail; regression-tested.
3. **Archive sink child-row loss.** Archive members were previously read from retained outcomes, which are empty in sink mode. Child rows are now written batch-by-batch through the sink.
4. **UTF-8 extraction corruption.** Valid UTF-8 is no longer handed first to probabilistic detection.

---

## Deferred acceptance questions — not PASS

B6.1 has strong Linux/build-machine evidence but **does not claim** that the following are proven:

- native Windows 10/11 operational fitness;
- NTFS hard-link/file-ID behavior as implemented through the Windows API boundary;
- long-path behavior on Windows;
- OneDrive/cloud placeholders;
- removable and network roots;
- Windows locale/code-page behavior of the complete GUI workflow;
- LastAccessTime behavior under Windows policy;
- interruption/restart/fault-injection semantics beyond the internal persistence-failure regression;
- full observability/diagnostic sufficiency.

Those belong to independent B6.1 adversarial acceptance.
