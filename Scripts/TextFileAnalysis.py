#!/usr/bin/env python3
"""
TextFileAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts metadata from every .txt and .md file in the run's inventory CSV:
line/word/character counts, detected encoding, and (for Markdown) a few
Obsidian-aware content-organization signals that are already hand-authored
in the files themselves: frontmatter title, heading count, wikilinks
([[note name]]), and inline tags (#tag).

This is data gathering only -- it records what's IN each file, it does
not compare files to each other (that's a Phase 2 step).

Requires:
    pip install chardet

Usage:
    python TextFileAnalysis.py --csv DuplicateHashInventory.csv --output TextFileInventory.csv --report TextFileReport.txt
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from file_organizer_common import run_analysis, to_long_path

try:
    import chardet
except ImportError:
    print("ERROR: chardet is not installed. Run: pip install chardet", file=sys.stderr)
    sys.exit(1)

EXTENSIONS = {".txt", ".md"}
CHECKPOINT_FIELDS = ["Key", "LineCount", "WordCount", "CharCount", "Encoding",
                      "HasFrontmatter", "Title", "HeadingCount", "WikilinkCount", "TagCount", "Error"]

HEADING_RE = re.compile(r'^#{1,6}\s+')
TAG_RE = re.compile(r'#[A-Za-z][\w\-/]*')
WIKILINK_RE = re.compile(r'\[\[.*?\]\]')
FRONTMATTER_TITLE_RE = re.compile(r'^title:\s*(.+)$', re.MULTILINE)
H1_RE = re.compile(r'^#\s+(.+)$', re.MULTILINE)


def analyze_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()

    detected = chardet.detect(raw)
    encoding = detected.get("encoding") or "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except (LookupError, TypeError):
        text = raw.decode("utf-8", errors="replace")
        encoding = "utf-8 (fallback)"

    lines = text.splitlines()
    words = text.split()

    has_frontmatter = text.startswith("---")
    title = ""
    if has_frontmatter:
        end_idx = text.find("\n---", 3)
        if end_idx != -1:
            frontmatter = text[3:end_idx]
            m = FRONTMATTER_TITLE_RE.search(frontmatter)
            if m:
                title = m.group(1).strip().strip('"').strip("'")

    if not title:
        m = H1_RE.search(text)
        if m:
            title = m.group(1).strip()

    # Walk line-by-line so heading lines ("# Heading") aren't miscounted
    # as inline tags -- a single combined regex can't cleanly tell them apart.
    heading_count = 0
    tag_count = 0
    for line in lines:
        stripped = line.lstrip()
        if HEADING_RE.match(stripped):
            heading_count += 1
            continue
        tag_count += len(TAG_RE.findall(line))

    wikilink_count = len(WIKILINK_RE.findall(text))

    return {
        "LineCount": str(len(lines)),
        "WordCount": str(len(words)),
        "CharCount": str(len(text)),
        "Encoding": encoding,
        "HasFrontmatter": str(has_frontmatter),
        "Title": title,
        "HeadingCount": str(heading_count),
        "WikilinkCount": str(wikilink_count),
        "TagCount": str(tag_count),
    }


def report_extra(results):
    md_results = [r for r in results if r.get("HasFrontmatter") or r.get("WikilinkCount")]
    total_words = sum(int(r["WordCount"]) for r in results if r.get("WordCount") and not r["Error"])
    total_wikilinks = sum(int(r.get("WikilinkCount") or 0) for r in results if not r["Error"])
    total_tags = sum(int(r.get("TagCount") or 0) for r in results if not r["Error"])
    with_frontmatter = sum(1 for r in results if r.get("HasFrontmatter") == "True")

    return [
        f"  Total word count            : {total_words:,}",
        f"  Files with frontmatter       : {with_frontmatter}",
        f"  Total wikilinks ([[...]])   : {total_wikilinks}",
        f"  Total inline tags (#tag)    : {total_tags}",
    ]


def main():
    parser = argparse.ArgumentParser(description="Extract metadata from every .txt/.md file in the inventory.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-cloud-only", action="store_true")
    args = parser.parse_args()

    run_analysis(
        csv_path=args.csv, output_path=args.output, report_path=args.report,
        extensions=EXTENSIONS, checkpoint_fields=CHECKPOINT_FIELDS, analyze_fn=analyze_text,
        force=args.force, skip_cloud_only=args.skip_cloud_only,
        report_title="TEXT FILE ANALYSIS REPORT", extra_report_lines_fn=report_extra,
    )


if __name__ == "__main__":
    main()
