#!/usr/bin/env python3
"""
PDFAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts metadata from every PDF file selected by the database-backed analyzer engine: page count,
encryption status, whether the first page has extractable text (a cheap
signal for "scanned image PDF" vs. a real text document -- only the first
page is checked, not the whole document, to stay fast at scale), and
standard document properties (Title/Author/Producer/CreationDate).

Requires:
    pip install pypdf pdfplumber

"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import to_long_path

try:
    from pypdf import PdfReader
except ImportError:
    print("ERROR: pypdf is not installed. Run: pip install pypdf", file=sys.stderr)
    sys.exit(1)

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber is not installed. Run: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

try:
    import pypdfium2 as pdfium
except ImportError:                                            # pragma: no cover
    pdfium = None

EXTENSIONS = {".pdf"}
CHECKPOINT_FIELDS = ["Key", "PageCount", "IsEncrypted", "HasExtractableText",
                      "Title", "Author", "Producer", "CreationDate", "Error"]


def analyze_pdf(path):
    long_path = to_long_path(path)
    reader = PdfReader(long_path)
    result = {
        "PageCount": str(len(reader.pages)),
        "IsEncrypted": str(reader.is_encrypted),
        "HasExtractableText": "Unknown",
        "Title": "", "Author": "", "Producer": "", "CreationDate": "",
    }

    meta = reader.metadata
    if meta:
        result["Title"] = meta.title or ""
        result["Author"] = meta.author or ""
        result["Producer"] = meta.producer or ""
        # B7.2 (E-008b) -- pypdf raises on a /CreationDate it cannot parse
        # ("garbage", "D:99999999999999"). That empties one field; it does
        # not fail a file whose pages are fine. The raw string is kept
        # when the value is a string, so a person can still see it.
        try:
            result["CreationDate"] = str(meta.creation_date) if meta.creation_date else ""
        except Exception:                                       # noqa: BLE001
            raw = meta.get("/CreationDate")
            result["CreationDate"] = ("%s (unparseable)" % raw) if isinstance(raw, str) and raw else ""

    # "Has extractable text" means SOME page has text, not the first page.
    # Judged by page one alone, a book whose cover is a picture was called
    # "no text" while holding 780,000 characters (found on the real corpus).
    # PDFium reads a page's text in milliseconds, so every page can be asked
    # until one answers; the pdfplumber fallback keeps the old first-page
    # judgement when PDFium is not installed.
    try:
        if pdfium is not None:
            result["HasExtractableText"] = _any_page_has_text(long_path)
        else:
            with pdfplumber.open(long_path) as pdf:
                if len(pdf.pages) > 0:
                    text = pdf.pages[0].extract_text() or ""
                    result["HasExtractableText"] = str(bool(text.strip()))
    except Exception:
        pass  # leave as "Unknown" -- encrypted/corrupt PDFs can fail here

    return result


def _any_page_has_text(long_path):
    doc = pdfium.PdfDocument(long_path)
    try:
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    if (textpage.get_text_bounded() or "").strip():
                        return "True"
                finally:
                    textpage.close()
            finally:
                page.close()
        return "False"
    finally:
        doc.close()


def report_extra(results):
    encrypted = sum(1 for r in results if r.get("IsEncrypted") == "True")
    no_text = sum(1 for r in results if r.get("HasExtractableText") == "False")
    return [
        f"  Encrypted PDFs             : {encrypted}",
        f"  Likely scanned (no text)   : {no_text}",
    ]
