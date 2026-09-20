r"""The File Organizer Phase 3 -- Decide / Plan.

Builds 1-2 (B8): Core Persistence and Exact-Duplicate Decisions.

Phase 3 converts evidence into attributable human intent, and that intent
into an inspectable projection of what SHOULD happen -- without touching a
source file. Nothing in this package renames, moves, deletes or edits a
file, or writes anywhere but the project database. Acting on a decision is
Phase 4, which does not exist yet.

    registry.py   what each decision kind and policy kind may say
    model.py      the in-memory evidence + intent model the resolver reads
    evidence.py   builds that model from a project database (Phase 2 truth
                  plus the p3_* tables)
    resolve.py    the deterministic keeper / canonical / protection
                  resolution -- a pure function, unit-tested against the
                  research fixture lab
    store.py      records operations, decisions, withdrawals and policy
                  versions; append-only
    review.py     the Exact-Duplicate Review pages inside the one window

The product version is Phase2.VERSION (== fo_db.APP_VERSION); Phase 3 ships
inside the same build and carries no version of its own.
"""
from Phase2 import VERSION  # noqa: F401  -- one product, one version

#: The decision-record contract written to app_meta by migration 009.
DECISION_SCHEMA = "fileorganizer.p3.decisions/1"
EVIDENCE_BINDING_SCHEMA = "fileorganizer.p3.evidence-binding/1"
