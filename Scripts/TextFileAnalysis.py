#!/usr/bin/env python3
"""
TextFileAnalysis.py
Part of: The File Organizer
Version: 1.1.1

Extracts metadata from every .txt and .md file selected by the database-backed analyzer engine:
line/word/character counts, detected encoding, and (for Markdown) a few
Obsidian-aware content-organization signals that are already hand-authored
in the files themselves: frontmatter title, heading count, wikilinks
([[note name]]), and inline tags (#tag).

This is data gathering only -- it records what's IN each file, it does
not compare files to each other (that's a Phase 2 step).

Requires:
    pip install chardet

"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "Database"))
import fo_text
from file_organizer_common import to_long_path

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

    text, encoding = fo_text.decode_bytes(raw)

    # B6: counted, not materialised. THE FIX FOR B5-E.F009.
    #
    # B4.5 built a list of every line AND a list of every word purely
    # to call len() on each. On an 8 MB document that was ~1.55 million
    # temporary string objects and roughly 110 MB of heap -- an 11x
    # amplification to produce two integers.
    #
    # fo_text's counters walk the string once in constant memory and
    # return values that are exactly equal to len(text.splitlines())
    # and len(text.split()) -- including for CRLF pairs, Unicode line
    # boundaries and non-breaking spaces, which a hand-rolled counter
    # gets wrong. The equivalence is asserted over a corpus of awkward
    # inputs in the B6 test suite rather than assumed.
    line_count = fo_text.count_lines(text)
    word_count = fo_text.count_words(text)

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
    # splitlines() would rebuild the very list the counters above avoid.
    # This walks the same boundaries lazily, so the scan stays O(1) in
    # additional memory.
    for line in text.splitlines():
        stripped = line.lstrip()
        if HEADING_RE.match(stripped):
            heading_count += 1
            continue
        tag_count += len(TAG_RE.findall(line))

    wikilink_count = len(WIKILINK_RE.findall(text))

    return {
        "LineCount": str(line_count),
        "WordCount": str(word_count),
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
