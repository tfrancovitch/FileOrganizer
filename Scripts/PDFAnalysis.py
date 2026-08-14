#!/usr/bin/env python3
"""
PDFAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts metadata from every PDF file in the run's inventory CSV: page count,
encryption status, whether the first page has extractable text (a cheap
signal for "scanned image PDF" vs. a real text document -- only the first
page is checked, not the whole document, to stay fast at scale), and
standard document properties (Title/Author/Producer/CreationDate).

Requires:
    pip install pypdf pdfplumber

Usage:
    python PDFAnalysis.py --csv DuplicateHashInventory.csv --output PDFInventory.csv --report PDFReport.txt
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, to_long_path

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
        result["CreationDate"] = str(meta.creation_date) if meta.creation_date else ""

    try:
        with pdfplumber.open(long_path) as pdf:
            if len(pdf.pages) > 0:
                text = pdf.pages[0].extract_text() or ""
                result["HasExtractableText"] = str(bool(text.strip()))
    except Exception:
        pass  # leave as "Unknown" -- encrypted/corrupt PDFs can fail here

    return result


def report_extra(results):
    encrypted = sum(1 for r in results if r.get("IsEncrypted") == "True")
    no_text = sum(1 for r in results if r.get("HasExtractableText") == "False")
    return [
        f"  Encrypted PDFs             : {encrypted}",
        f"  Likely scanned (no text)   : {no_text}",
    ]


def main():
    parser = argparse.ArgumentParser(description="Extract PDF metadata for every PDF in the inventory.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    run_analysis(
        csv_path=args.csv, output_path=args.output, report_path=args.report,
        extensions=EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS, analyze_fn=analyze_pdf,
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="PDF ANALYSIS REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
