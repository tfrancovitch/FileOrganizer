#!/usr/bin/env python3
"""
ContentExtraction.py
Part of: The File Organizer
Version: 1.1.1

Extracts the actual TEXT CONTENT of every content-bearing document in
the run's inventory CSV -- PDF, Word (.docx), PowerPoint (.pptx), Excel
(.xlsx), plain text (.txt), and Markdown (.md) -- into individual .txt
files, indexed by DB_ID. This is the last Phase 1 (data-gathering) gap:
earlier scripts captured metadata ABOUT documents (author, page count);
this captures what they actually SAY, which is what a future content-based
organization/search/comparison pass (Phase 2) will need to work with.

Output:
    ExtractedText/<hash>.txt  -- one file per document, full extracted text
    ContentIndex.csv          -- DB_ID, FileName, Path, SourceType,
                                 ExtractedTextFile, CharCount, WordCount, Error

    The extracted-text filename is a hash of the source path (not the
    DB_ID) purely so this script doesn't need any changes to the shared
    orchestrator's analyze_fn(path) signature -- ContentIndex.csv is the
    actual DB_ID -> file lookup, so nothing else needs to care about the
    hash naming.

Requires:
    pip install pdfplumber python-docx openpyxl python-pptx chardet

Usage:
    python ContentExtraction.py --csv DuplicateHashInventory.csv --output ContentIndex.csv --extract-folder ExtractedText --report ContentExtractionReport.txt
"""

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, to_long_path

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber is not installed. Run: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

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

EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md"}
CHECKPOINT_FIELDS = ["Key", "SourceType", "ExtractedTextFile", "CharCount", "WordCount", "Error"]


def path_to_filename(path):
    """Deterministic, collision-free filename for the extracted-text file,
    derived from the source path (not DB_ID -- see module docstring)."""
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{h}.txt"


def extract_pdf_text(path):
    parts = []
    with pdfplumber.open(to_long_path(path)) as pdf:
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
    for sheet in wb.worksheets:
        parts.append(f"--- Sheet: {sheet.title} ---")
        for row in sheet.iter_rows(values_only=True):
            line = "\t".join("" if v is None else str(v) for v in row)
            if line.strip():
                parts.append(line)
    return "Excel", "\n".join(parts)


def extract_plain_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        text = raw.decode("utf-8", errors="replace")
    return "PlainText", text


def make_analyze_fn(extract_folder):
    def analyze_content(path):
        ext = Path(path).suffix.lower()
        if ext == ".pdf":
            source_type, text = extract_pdf_text(path)
        elif ext == ".docx":
            source_type, text = extract_docx_text(path)
        elif ext == ".pptx":
            source_type, text = extract_pptx_text(path)
        elif ext == ".xlsx":
            source_type, text = extract_xlsx_text(path)
        elif ext in (".txt", ".md"):
            source_type, text = extract_plain_text(path)
        else:
            raise ValueError(f"Unsupported extension: {ext}")

        filename = path_to_filename(path)
        with open(extract_folder / filename, "w", encoding="utf-8") as f:
            f.write(text)

        return {
            "SourceType": source_type,
            "ExtractedTextFile": filename,
            "CharCount": str(len(text)),
            "WordCount": str(len(text.split())),
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


def main():
    parser = argparse.ArgumentParser(description="Extract full text content from every document in the inventory.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True, help="Path for ContentIndex.csv")
    parser.add_argument("--extract-folder", required=True, help="Folder to write extracted .txt files into")
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    extract_folder = Path(args.extract_folder)
    extract_folder.mkdir(parents=True, exist_ok=True)

    run_analysis(
        csv_path=args.csv, output_path=args.output, report_path=args.report,
        extensions=EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS,
        analyze_fn=make_analyze_fn(extract_folder),
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="CONTENT EXTRACTION REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
