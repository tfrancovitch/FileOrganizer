-- The File Organizer — schema 016
-- B18 (the Phase 3 closeout round): encryption evidence as a structured
-- field, recorded by the PDF and Office analyzers (PDFAnalysis 1.3.0,
-- OfficeAnalysis 1.5.0). Phase 2 EVIDENCE, like migrations 012 and 015:
-- additive, one nullable column, no rewrite.
--
-- analyzer_result.encryption_state   encrypted | not_encrypted | unknown |
--                                    not_applicable, or NULL
--
-- A promoted column by 005's own rule, as signature_state was in 015: two
-- analyzers populate it. It is written for a file an analyzer refuses as
-- password-protected too (that row is an error, and it still says
-- 'encrypted'). Legacy OLE2 files are read from their header structures
-- only: Word's FIB fEncrypted bit, Excel's FILEPASS record, PowerPoint's
-- CurrentUserAtom header token / UserEditAtom encryption reference.
--
-- NULL means the result predates B18 (or the analyzer does not read
-- encryption). It is UNKNOWN. Phase 3 reads the column first and falls back
-- to B17's reading of the analyzers' error wording ("password-protected
-- document") ONLY when the column is NULL. A re-analysis fills it.

ALTER TABLE analyzer_result ADD COLUMN encryption_state TEXT;

INSERT OR REPLACE INTO app_meta(key,value,updated_utc) VALUES
 ('phase2.encryption_evidence','analyzer_result.encryption_state/1',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
