-- The File Organizer — schema 015
-- B17 (Phase 3, finishing Build 9): signature evidence, recorded by the PDF
-- and Office analyzers (PDFAnalysis 1.2.0, OfficeAnalysis 1.4.0). Phase 2
-- EVIDENCE, like migration 012: additive, one nullable column, no rewrite.
--
-- analyzer_result.signature_state   signed | certified | unsigned | unknown |
--                                   not_applicable, or NULL
--
-- It is a promoted column by 005's own rule: two analyzers populate it (PDF,
-- Office), so it is a column, not a detail_json key. Presence only -- a
-- signature is never validated (P3.R8 §2.8, §33; P3-A115).
--
-- NULL means the result predates B17 (or the analyzer does not read
-- signatures). It is UNKNOWN, never "unsigned": reading an absent value as
-- unsigned would unblock every metadata item on a file analyzed before this
-- build. A re-analysis fills it.

ALTER TABLE analyzer_result ADD COLUMN signature_state TEXT;

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase2.signature_evidence','analyzer_result.signature_state/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
