# The File Organizer — P3.R11 Synthetic Decision Fixture Lab

This is a **reference fixture package**, not a Phase 3 implementation.

It contains 16 synthetic SQLite fixtures with (F16, the P3.R3 §34 non-edge
shape, added in B14; the product ships the 12 its builds use so far -- F11 to
F14, plan and metadata scenarios, arrive with those builds):
- synthetic Phase 2 evidence stubs;
- authoritative Phase 3 Decision / Policy / Batch / Review / Plan records;
- expected current projections;
- an independent reference validator.

No real Source Files exist. Validation reads only the fixture databases.

## Run on Windows

Double-click:

`Run-P3.R11-Fixture-Validation.bat`

Expected:

`P3.R11 fixture validation: 16/16 PASS` (12/12 in the product's copy)
