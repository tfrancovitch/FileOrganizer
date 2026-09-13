#!/usr/bin/env python3
"""
ContentExtraction.py
Part of: The File Organizer
Version: 1.1.1

Extracts the actual TEXT CONTENT of every content-bearing document in
the database-backed analyzer engine -- PDF, Word (.docx and .doc), PowerPoint
(.pptx and .ppt), Excel (.xlsx and .xls), RTF, HTML, CSV, JSON, plain text
(.txt), and Markdown (.md) -- into individual .txt files, indexed by DB_ID.

A file is read as what its bytes say it is, not as what its name says: a
".doc" that is really RTF is read as RTF, a ".xls" that is really an HTML
export is read as HTML, and a document that is plain text under a document
extension is read as text. The recorded SourceType says which reader ran.

This is the last Phase 1 (data-gathering) gap: earlier scripts captured
metadata ABOUT documents (author, page count); this captures what they
actually SAY, which is what a future content-based organization/search/
comparison pass (Phase 2) will need to work with.

Output:
    ExtractedText/<hash>.txt  -- one file per document, full extracted text
    ContentIndex.csv          -- DB_ID, FileName, Path, SourceType,
                                 ExtractedTextFile, CharCount, WordCount, Error

    The supported product runtime stores extracted text by the SHA-256 of
    the extracted UTF-8 text, sharded by hash prefix. Identical extracted
    text therefore reuses one artifact. RunCoordinator uses the in-process analyzer engine and persists results
    directly to SQLite.

Requires:
    pip install pdfplumber python-docx openpyxl python-pptx chardet
Optional:
    pypdfium2 (installed with pdfplumber) -- reads PDF text 25-30x faster;
              pdfplumber is the fallback for any file it refuses
    xlrd      -- .xls text; without it an .xls records an error naming it

"""

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "Database"))
from file_organizer_common import to_long_path
import fo_text
import fo_extractors

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber is not installed. Run: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

try:
    import pypdfium2 as pdfium
except ImportError:                                            # pragma: no cover
    pdfium = None

try:
    import docx
except ImportError:
    print("ERROR: python-docx is not installed. Run: pip install python-docx", file=sys.stderr)
    sys.exit(1)

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl is not installed. Run: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

try:
    from pptx import Presentation
except ImportError:
    print("ERROR: python-pptx is not installed. Run: pip install python-pptx", file=sys.stderr)
    sys.exit(1)

try:
    import chardet
except ImportError:
    print("ERROR: chardet is not installed. Run: pip install chardet", file=sys.stderr)
    sys.exit(1)

EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md",
              ".doc", ".ppt", ".xls", ".rtf", ".html", ".htm", ".csv", ".json"}

#: What a text-like file is called by its extension. Anything else that
#: sniffs as text is "PlainText" -- including a .doc that turns out to be one.
TEXT_SOURCE_TYPES = {".csv": "CSV", ".json": "JSON"}
CHECKPOINT_FIELDS = ["Key", "SourceType", "ExtractedTextFile", "TextSha256",
                     "ReusedExisting", "CharCount", "WordCount", "Error"]


#: Extracted artifacts are sharded two levels deep by the first four
#: hex characters of their content hash: <ab>/<cd>/<full>.txt.
#:
#: B5-E.F008 flagged the flat directory as a real scalability problem
#: -- a large project produced one folder with hundreds of thousands of
#: entries in it, which NTFS handles poorly and Explorer handles worse.
#: Two levels of 256 gives 65,536 buckets, so a million extracted
#: documents average ~15 files per directory.
SHARD_DEPTH = 2


def content_to_relpath(text_sha256):
    r"""
ContentExtraction.py
Part of: The File Organizer B6.1

Per-file content extraction implementation used by the database-backed
in-process analyzer engine.

Supported document text is extracted into a content-addressed, two-level
sharded store keyed by SHA-256 of the extracted UTF-8 text. Identical extracted
text from different source paths reuses one artifact. The analyzer returns the
artifact reference, content hash, encoding/counts and error state; SQLite
persistence is owned by the analyzer runtime, not by this module.

Plain-text decoding is BOM-aware and tries strict UTF-8 before probabilistic
encoding detection. This prevents valid UTF-8 from being silently converted to
a plausible but incorrect legacy encoding.

Requires as applicable: pdfplumber, python-docx, openpyxl, python-pptx, chardet.
"""
    digest = text_sha256
    parts = [digest[i * 2:(i + 1) * 2] for i in range(SHARD_DEPTH)]
    return "/".join(parts + ["%s.txt" % digest])


def path_to_filename(path):
    """B4.5's path-addressed name. RETAINED for `storage_mode` fallback.

    Still reachable when a project sets `extraction.storage_mode` to
    'path_addressed', which exists so an operator upgrading mid-project
    is not forced to re-extract everything at once. New projects get
    content addressing, which is the default in migration 006.
    """
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{h}.txt"


def _pdf_text_pdfium(long_path):
    """Every page's text through PDFium. Measured on the real corpus at 4-13
    ms per page against pdfplumber's 125-190: the same words, 25-30x sooner."""
    doc = pdfium.PdfDocument(long_path)
    try:
        parts = []
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    parts.append(textpage.get_text_bounded() or "")
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        doc.close()
    return "\n\n".join(part.replace("\r\n", "\n").replace("\r", "\n") for part in parts)


def extract_pdf_text(path):
    long_path = to_long_path(path)
    if pdfium is not None:
        try:
            return "PDF", _pdf_text_pdfium(long_path)
        except Exception:                                      # noqa: BLE001
            pass            # pdfplumber gets the file; if it fails too, that is the error
    parts = []
    with pdfplumber.open(long_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "PDF", "\n\n".join(parts)


def extract_docx_text(path):
    d = docx.Document(to_long_path(path))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "Word", "\n".join(parts)


def extract_pptx_text(path):
    prs = Presentation(to_long_path(path))
    parts = []
    for i, slide in enumerate(prs.slides, start=1):
        slide_parts = [
            shape.text_frame.text for shape in slide.shapes if shape.has_text_frame
        ]
        parts.append(f"--- Slide {i} ---\n" + "\n".join(slide_parts))
    return "PowerPoint", "\n\n".join(parts)


def extract_xlsx_text(path):
    wb = openpyxl.load_workbook(to_long_path(path), read_only=True, data_only=True)
    parts = []
    try:
        for sheet in wb.worksheets:
            parts.append(f"--- Sheet: {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                line = "\t".join("" if v is None else str(v) for v in row)
                if line.strip():
                    parts.append(line)
    finally:
        # A read-only workbook keeps the file open until told otherwise; a
        # handle left on someone's file blocks its rename or sync.
        wb.close()
    return "Excel", "\n".join(parts)


def extract_plain_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    text, _encoding = fo_text.decode_bytes(raw)
    return "PlainText", text


def extract_any(path):
    """(SourceType, text) for a file, chosen by what the bytes are.

    The extension picks nothing except the name given to plain text; the
    signature picks the reader. So a ".doc" holding RTF is read as RTF,
    a ".xls" holding an HTML export is read as HTML, and a file that is
    really plain text is read as text whatever it is called. When the
    bytes are a container of the wrong kind for the name (a ".doc" that
    is a ZIP with word/ inside is a .docx) the container decides.
    """
    ext = Path(path).suffix.lower()
    kind = fo_extractors.sniff(path)
    if kind == "pdf":
        return extract_pdf_text(path)
    if kind == "rtf":
        return "RTF", fo_extractors.rtf_text(path)
    if kind == "html":
        return "HTML", fo_extractors.html_text(path)
    if kind == "ole":
        inner = fo_extractors.ole_kind(path)
        if inner == "word":
            return "Word 97-2003", fo_extractors.doc_text(path)
        if inner == "powerpoint":
            return "PowerPoint 97-2003", fo_extractors.ppt_text(path)
        if inner == "excel":
            return "Excel 97-2003", fo_extractors.xls_text(path)
        raise ValueError("OLE container without a Word, PowerPoint or Excel document inside")
    if kind == "zip":
        inner = fo_extractors.zip_kind(path)
        if inner == "docx":
            return extract_docx_text(path)
        if inner == "pptx":
            return extract_pptx_text(path)
        if inner == "xlsx":
            return extract_xlsx_text(path)
        raise ValueError("ZIP container that is not a Word, PowerPoint or Excel document")
    if kind == "text":
        _source, text = extract_plain_text(path)
        return TEXT_SOURCE_TYPES.get(ext, "PlainText"), text
    if kind == "empty":
        return "PlainText", ""
    raise ValueError("not a recognised document format (%s)" % kind)


def make_analyze_fn(extract_folder, content_addressed=True):
    def analyze_content(path):
        ext = Path(path).suffix.lower()
        if ext not in EXTENSIONS:
            raise ValueError(f"Unsupported extension: {ext}")
        source_type, text = extract_any(path)

        # Content addressing: the artifact is named for what is IN it.
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_addressed:
            relpath = content_to_relpath(text_sha)
        else:
            relpath = path_to_filename(path)

        target = extract_folder / relpath
        target.parent.mkdir(parents=True, exist_ok=True)

        # A hit here means another document already produced byte-for-byte
        # this text. Writing it again would produce an identical file, so
        # the write is skipped and the reuse is REPORTED rather than
        # hidden -- a consumer counting artifacts should be able to tell
        # deduplication from extraction failure.
        reused = target.exists()
        if not reused:
            # Written to a temporary neighbour and renamed, so a crash
            # mid-write cannot leave a truncated artifact sitting at a
            # content-addressed name that later runs will trust (B5-G).
            staging = target.with_suffix(".txt.partial")
            with open(staging, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(staging, target)

        # B6: counted, not materialised. len(text.split()) built a list
        # of every token to produce one integer -- B5-E.F009 measured an
        # 8 MB document costing ~110 MB of heap that way.
        return {
            "SourceType": source_type,
            "ExtractedTextFile": relpath,
            "TextSha256": text_sha,
            "ReusedExisting": "True" if reused else "False",
            "CharCount": str(len(text)),
            "WordCount": str(fo_text.count_words(text)),
        }
    return analyze_content


def report_extra(results):
    total_chars = sum(int(r["CharCount"]) for r in results if r.get("CharCount") and not r["Error"])
    total_words = sum(int(r["WordCount"]) for r in results if r.get("WordCount") and not r["Error"])
    empty_count = sum(1 for r in results if r.get("CharCount") == "0")

    by_type = {}
    for r in results:
        t = r.get("SourceType") or "Unknown"
        by_type[t] = by_type.get(t, 0) + 1

    lines = [
        f"  Total characters extracted   : {total_chars:,}",
        f"  Total words extracted        : {total_words:,}",
        f"  No extractable text (likely scanned/empty): {empty_count}",
        "  By source type:",
    ]
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"    {t:<12}: {c}")
    return lines
