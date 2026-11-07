# Research brief B-R4.8: certifying the first metadata writer

**For:** `fo-researcher` (sonnet) · **From:** the Architect, 2026-11-01 · **Covers:** R4.8
**Report to:** `C:\FileOrganizerTesting\Docs\Research\phase_4\R4\R4.8_report.md`. Target 2,500-4,000 words.
Budget: ~20 web fetches. **Run after the scout answers** (`Handoffs\Phase4\R4_SCOUT_ANSWERS.md`, item 2: the
fields people can set) and read them first.

## Context (do not re-research)
Axiom 10: changing embedded metadata is an action; axiom 11: reversible. `PROJECT_PLAN.md`: "Metadata write:
prevention, not undo: write to temp, verify it parses, then replace." The 2026-09 corpus, section "R7: Metadata,
sanitization, and provenance" (`Phase_4_Research_Results.md` lines ~210-263; read only that) covers the general
policy. Phase 3 holds every Update Source Metadata item blocked until a writer is certified for that exact
family × field. Signed, encrypted and hard-linked files stay blocked. The product is Python on Windows and
must never execute content (no macros, no active content).

## Questions
1. **Which pair first?** Candidates are limited to pairs the product can already *read back*, because
   verification needs a reader (scout answer 2; `Scripts\Database\migrations\014_phase3_metadata.sql` lines
   64-79): PDF title / creator / content_created; OOXML (docx, xlsx, pptx) title / creator / content_created;
   legacy OLE Office the same three; RAW content_created (EXIF DateTimeOriginal); audio title. JPEG is **not**
   a candidate (no reader today). Rank the 3 best pairs by: how often a person needs it, how simple the
   format's storage of that field is, library support, and the blast radius of a bad write. Architect's
   hypothesis to test, not assume: PDF Title or OOXML core-properties title rank highest; legacy OLE and RAW
   lowest.
2. **Library.** For the top pair, compare: ExifTool (external program, Perl; `-overwrite_original` vs keeping
   `_original`; licence; does running it count as "executing content"? It executes a program, not the file's
   content: say how other careful tools treat it), `piexif`, `pyexiv2`, `pillow`, `pikepdf` (incremental vs
   full rewrite). Maintenance status, Windows install, known corruption bugs (cite issue trackers).
3. **Certification evidence.** What proves a writer safe for one pair? Propose a checklist, for example:
   round trip (write, read back, equal); every byte outside the metadata segment unchanged (or, if the format
   forces a rewrite, an independent reader decodes identical image / page content); a second independent reader
   agrees on the new value; maker notes and other fields preserved; temp-write then same-volume replace
   (`ReplaceFileW`: does it keep the file ID, ACL, ADS, creation time?); the original kept as a backup until
   verified; a corpus of awkward real-world samples (cite public test-image corpora, such as ExifTool's or
   exiv2's test files).
4. **Undo.** Restore from the kept backup vs writing the old value back: which is honest about what changed?
5. **Interaction with identity.** After a rewrite the size, modified time and hash change. How should Phase 1/2
   re-observe the file, and how does the journal link old and new evidence so this is not read as drift?

## Deliverables in the report
- The ranked pairs and a recommended first pair.
- The recommended library, with the exact write procedure.
- A certification checklist B26 can turn into tests, and the sample corpus to use.
