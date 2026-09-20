"""The File Organizer Phase 2 — the Understand core."""

#: The product version, kept equal to fo_db.APP_VERSION (which every run
#: and project records). This copy is stamped as the analytical core that
#: touched a project (app_meta phase2.core_version) and goes into the
#: signature of every derived index, so a new core rebuilds them.
VERSION = "B7.2"
QUERY_SCHEMA = "fileorganizer.query/1"
SEMANTIC_CONTRACT = "fileorganizer.query-semantics/1"
