#!/usr/bin/env python3
r"""
Diagnose-LegacyOfficeFiles.py

A one-off diagnostic, NOT part of the pipeline -- reads the actual first
bytes of files that failed OfficeAnalysis.py with "not an OLE2 structured
storage file", to find out what format they really are before building a
fix based on a guess.

Checks each file's magic bytes against several known signatures:
  - OLE2 Compound Document (what olefile expects):  D0 CF 11 E0 A1 B1 1A E1
  - RTF (Rich Text Format):                          {\rtf
  - ZIP-based (would mean a modern .docx/.xlsx/.pptx
    that got saved with a legacy extension by mistake): PK\x03\x04
  - Plain text (no binary signature, readable as UTF-8/Latin-1)
  - Anything else: reports the raw hex so we can identify it manually

Usage:
    python Diagnose-LegacyOfficeFiles.py "path\to\OfficeInventory.errors.txt"

Reads paths directly from the errors.txt file (one "path -- error message"
line per file) -- no need to re-type paths by hand. Checks every file
listed by default; use --limit N to sample a subset instead if the list
is very long.
"""

import argparse
import sys
from pathlib import Path

SIGNATURES = {
    b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1": "OLE2 Compound Document (this SHOULD work with olefile -- if it's showing up here, something else is wrong)",
    b"{\\rtf": "RTF (Rich Text Format) -- not OLE2, needs separate handling",
    b"PK\x03\x04": "ZIP-based (modern .docx/.xlsx/.pptx saved with a legacy extension)",
    b"%PDF": "PDF (misnamed as a legacy Office file)",
}


def to_long_path(path_str):
    if sys.platform != "win32":
        return path_str
    if path_str.startswith("\\\\?\\"):
        return path_str
    if path_str.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path_str[2:]
    return "\\\\?\\" + path_str


def identify(path_str):
    try:
        with open(to_long_path(path_str), "rb") as f:
            header = f.read(16)
    except OSError as e:
        return f"COULD NOT OPEN: {e}"

    for sig, label in SIGNATURES.items():
        if header.startswith(sig):
            return label

    # No known binary signature matched -- check if it looks like plain text
    try:
        header.decode("utf-8")
        return "Plain text (UTF-8 readable) -- possibly a text file saved with a .doc extension"
    except UnicodeDecodeError:
        pass
    try:
        header.decode("latin-1")
        looks_texty = all(32 <= b <= 126 or b in (9, 10, 13) for b in header)
        if looks_texty:
            return "Plain text (Latin-1 readable) -- possibly a text file saved with a .doc extension"
    except UnicodeDecodeError:
        pass

    return f"UNKNOWN -- raw header bytes: {header.hex(' ')}"


def main():
    parser = argparse.ArgumentParser(description="Identify the real format of files that failed OLE2 parsing.")
    parser.add_argument("errors_file", help="Path to OfficeInventory.errors.txt")
    parser.add_argument("--limit", type=int, default=None, help="Only check the first N files (default: all)")
    args = parser.parse_args()

    errors_path = Path(args.errors_file)
    if not errors_path.exists():
        print(f"ERROR: file not found: {errors_path}")
        sys.exit(1)

    paths = []
    with open(errors_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if " -- not an OLE2 structured storage file" in line:
                file_path = line.split(" -- not an OLE2 structured storage file")[0]
                paths.append(file_path)

    if args.limit:
        paths = paths[: args.limit]

    print(f"Checking {len(paths)} file(s)...\n")

    from collections import Counter
    result_counts = Counter()

    for p in paths:
        result = identify(p)
        result_counts[result] += 1

    print("=== SUMMARY (grouped by identified format) ===\n")
    for result, count in result_counts.most_common():
        print(f"  {count:>5}  {result}")

    print(f"\nTotal checked: {len(paths)}")


if __name__ == "__main__":
    main()
