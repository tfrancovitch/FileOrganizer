r"""The File Organizer Phase 4 -- Act (the first phase that changes the user's files).

B21 builds the machinery every later Phase 4 build writes through, and touches no real file:

    paths.py       where the journal and the locks live (local, never synced, never a network path)
    journal.py     the separate, durable action journal (SQLite, WAL, synchronous=FULL)
    locks.py       the project lock and the root-level lock (byte locks; the OS is the authority)
    jobs.py        the job and item state machine, the commit points C0-C4 and P, the refusal scopes
    projection.py  journal events projected, idempotently, into the catalog
    reconcile.py   the tables that classify an interrupted job on restart -- and never act on their own

Axiom 17: no AI, and no part of this package, is the action authority. Recovery classifies and shows; a
person chooses; every resume or roll-back is a new journalled action.
"""
