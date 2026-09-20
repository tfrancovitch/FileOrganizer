#!/usr/bin/env python3
r"""Build a Phase 2 acceptance corpus with known-answer ground truth.

The P2.11 acceptance run used a 146 KB single-extension corpus with no hashes,
no content, no duplicates and no extracted text. Fifteen of the reports that
"passed" returned zero rows. A corpus that cannot produce evidence cannot
falsify anything, so this builds one that can.

The corpus deliberately contains:

  * many extensions, including files with no extension
  * exact duplicates with a precomputed reclaimable-byte total
  * extractable text (DOCX, XLSX, PDF, TXT, CSV, RTF, HTML, JSON, XML, LOG,
    vCard, iCalendar, a file with no extension, the Word / Excel / PowerPoint
    97-2003 binaries, and email as .eml, .mbox, .mht and Outlook .msg)
    carrying known marker phrases so FTS can be verified against a specific
    expected hit -- for the email files, a phrase that lives only in an
    attachment, so attachment extraction is what is verified
  * a wide modified-date spread so the Age reports separate
  * deliberately malformed files so analyzer-failure reports are exercised
  * edge cases: zero-byte, unicode names, deep nesting, long paths

Everything is generated deterministically from a fixed seed, and every fact a
report should be able to state is written to GROUND_TRUTH.json next to the
corpus. Verification compares Phase 2's answers against that file -- never
against the query engine being tested.

The corpus is also the product's full test corpus: at least one file of
every type the program handles -- every image, RAW, audio, video, archive
and extractable-text format it names -- and the awkward cases (a scan that
needs OCR, a scan too poor to trust, a photograph that is not a document,
a mailbox, a file with no extension, a zip with a zip inside). Real
third-party samples the builder cannot make itself (Apache Tika's PST,
any WordPerfect or OneNote file the user provides) are copied in from
`--samples` when present and listed in the ground truth as such.

Since 2026-09-13 the corpus also carries the adversarial cases of the user's
Master Corpus Matrix (`C:\FOTest\Research\`). Every case is registered with
its matrix ID and an expected result written in this program's own vocabulary
(`file_state.state`, `file_observation.status`, `hash_status`, ...), and the
ground truth lists them under `cases` -- the manifest the matrix's section 5
asks for, computed by the builder rather than typed. Cases that cannot be
built on this machine are listed under `not_constructed` with the reason.

Two trees, one builder. `Corpus\` holds what is static and tool-safe: the
suites build a copy of it in %TEMP% on every run and the user opens it in
Explorer. `Hostile\` (`--hostile`) holds what breaks naive tools or changes
a scan's overall status -- denied folders, junction loops, hard links,
reserved names, ten thousand files in one directory -- under its own project
and its own check, and `--teardown` removes it, restoring every ACL first.

Usage:
    python Scripts/p2_build_acceptance_corpus.py [--root C:\FOTest] [--samples C:\FOTest\Research\Samples]
    python Scripts/p2_build_acceptance_corpus.py --hostile [--root C:\FOTest]
    python Scripts/p2_build_acceptance_corpus.py --teardown [--root C:\FOTest]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import random
import shutil
import stat
import subprocess
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260908
NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

#: Where the matrix's reconciliation lives, relative to --root. Read when
#: present so each case's condition text, priority, disposition and layer
#: come from the user's document, never retyped here.
MATRIX_CSV = Path("Research") / "ChatGPT Research" / "Personal_Archive_Manager_v1.1_Reconciliation.csv"

#: The "executable" the corpus uses: a DOS exit stub (mov ax,4C00h; int 21h)
#: followed by every byte value once. Not a program any 64-bit Windows can
#: run, and nothing here runs anything; it exists to look like one to a
#: reader -- NUL bytes and all -- where a single byte would read as text.
TINY_PROGRAM = b"\xb8\x00\x4c\xcd\x21" + bytes(range(256))

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


# --------------------------------------------------------------------------
# Paths the way Win32 needs them
# --------------------------------------------------------------------------

def ext_path(path) -> str:
    r"""The \\?\ form of an absolute path, for every write and delete.

    It is what crosses MAX_PATH (LongPathsEnabled is 0 on the build machine)
    and what switches off name normalisation, so a name with a trailing dot
    or space, or a reserved device name like CON, is created and removed as
    written. The scanner and every reader already do this at their own
    edges; the builder must too, or it cannot make the corpus the scanner is
    built to survive. Off Windows the path is returned as it is.
    """
    # Not os.path.abspath on an absolute path: that is Win32's
    # GetFullPathName, which strips a trailing dot or space -- the very
    # names the prefix exists to keep. (Found when `dot.` came out as `dot`.)
    text = str(path)
    if not os.path.isabs(text):
        text = os.path.abspath(text)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    text = text.replace("/", "\\")
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def plain_path(path) -> str:
    r"""The \\?\ prefix taken off again, for tools that do not accept it."""
    text = str(path)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def set_attributes(target: str, attributes: int) -> None:
    """OR the given FILE_ATTRIBUTE_* bits onto a file (hidden, system, read-only)."""
    if os.name != "nt":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    current = kernel32.GetFileAttributesW(target)
    if current == 0xFFFFFFFF:
        raise OSError("GetFileAttributesW failed for %s" % plain_path(target))
    if not kernel32.SetFileAttributesW(target, current | attributes):
        raise OSError("SetFileAttributesW failed for %s" % plain_path(target))


def set_creation_time(target: str, when: datetime) -> None:
    """Pin an NTFS creation time (os.utime only reaches modified and accessed)."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    kernel32.SetFileTime.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    FILE_WRITE_ATTRIBUTES, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS = 0x100, 3, 0x02000000
    handle = kernel32.CreateFileW(target, FILE_WRITE_ATTRIBUTES, 7, None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
    if handle in (None, 0xFFFFFFFFFFFFFFFF, -1):
        raise OSError("CreateFileW failed for %s" % plain_path(target))
    try:
        # FILETIME: 100-ns intervals since 1601-01-01 UTC.
        ticks = int((when - datetime(1601, 1, 1, tzinfo=timezone.utc)).total_seconds() * 10_000_000)
        filetime = wintypes.FILETIME(ticks & 0xFFFFFFFF, ticks >> 32)
        if not kernel32.SetFileTime(handle, ctypes.byref(filetime), None, None):
            raise OSError("SetFileTime failed for %s" % plain_path(target))
    finally:
        kernel32.CloseHandle(handle)


def make_sparse(target: str, size: int) -> None:
    """Mark a file sparse and extend it: the tail reads as zeros and occupies
    no disk. fsutil needs the plain path; it exits 0 on failure, so the
    size is checked afterwards."""
    if os.name != "nt":
        with open(target, "r+b") as handle:
            handle.truncate(size)
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    plain = plain_path(target)
    subprocess.run(["fsutil", "sparse", "setflag", plain], capture_output=True, creationflags=flags)
    subprocess.run(["fsutil", "file", "seteof", plain, str(size)], capture_output=True, creationflags=flags)
    if os.path.getsize(target) != size:
        raise OSError("could not make %s sparse to %d bytes" % (plain, size))


def set_compressed(target: str) -> None:
    """NTFS compression on one file (compact /c)."""
    if os.name != "nt":
        return
    subprocess.run(["compact", "/c", "/q", plain_path(target)], capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _is_reparse(entry) -> bool:
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def icacls(path, *arguments) -> bool:
    r"""Run icacls on one path, however long the path is.

    icacls refuses a path over 260 characters in both its plain and its
    \\?\ form. For those, a junction in %TEMP% is pointed at the parent
    folder and icacls is given the short path through it; the junction is
    removed afterwards. The owner of a file can always rewrite its ACL, so
    none of this needs elevation on anything the builder made.
    """
    if os.name != "nt":
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    plain = plain_path(path)
    if len(plain) <= 250:
        result = subprocess.run(["icacls", plain, *arguments], capture_output=True, creationflags=flags)
        return result.returncode == 0 and b"Failed processing 0" in result.stdout
    import _winapi
    import tempfile
    # The nearest ancestor short enough for CreateJunction (no \\?\ there
    # either); the remainder is spelt out through the bridge.
    ancestor, remainder = plain, ""
    while len(ancestor) > 200:
        ancestor, tail = os.path.split(ancestor)
        remainder = os.path.join(tail, remainder) if remainder else tail
    bridge = os.path.join(tempfile.gettempdir(), "fo_acl_bridge_%d" % os.getpid())
    if len(os.path.join(bridge, remainder)) > 250:
        raise OSError("path too long to reach through one junction: %s" % plain)
    if os.path.lexists(bridge):
        os.rmdir(bridge)
    _winapi.CreateJunction(ancestor, bridge)
    try:
        result = subprocess.run(["icacls", os.path.join(bridge, remainder), *arguments], capture_output=True, creationflags=flags)
        return result.returncode == 0 and b"Failed processing 0" in result.stdout
    finally:
        os.rmdir(bridge)


def reset_acl(path) -> None:
    """Put a file or folder's ACL back to what it inherits: what undoes an
    explicit deny the builder placed for an access case."""
    icacls(path, "/reset", "/Q")


def enable_case_sensitivity(directory) -> bool:
    """Make one (empty) directory case-sensitive, as WSL and some developer
    setups do. Works unelevated on Windows 10 1903+ -- but only with Full
    Control on the directory (Modify, which C:\\ hands down, is not enough),
    so the owner grants it to themselves first. fsutil exits 0 when it
    fails, so the flag is queried back rather than trusted."""
    if os.name != "nt":
        return False
    target = plain_path(ext_path(directory))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["icacls", target, "/grant", "%s:(F)" % os.environ.get("USERNAME", ""), "/Q"],
                   capture_output=True, creationflags=flags)
    subprocess.run(["fsutil", "file", "setCaseSensitiveInfo", target, "enable"], capture_output=True, creationflags=flags)
    query = subprocess.run(["fsutil", "file", "queryCaseSensitiveInfo", target], capture_output=True, text=True, creationflags=flags)
    return "is enabled" in (query.stdout or "")


def remove_tree(path) -> None:
    r"""Delete a corpus tree the way shutil.rmtree cannot.

    Through \\?\, so long paths and reserved names go; clearing the
    read-only attribute where a delete is refused; never entering a
    junction or symbolic link (the link itself is removed, its target is
    not touched -- a junction back to the parent must not become a
    recursive delete of the parent); and resetting the ACL of anything a
    deny keeps closed. Everything the builder can make, this can unmake.
    """
    root = ext_path(path)
    if not os.path.lexists(root):
        return
    if os.path.isdir(root) and not os.path.islink(root):
        _remove_directory(root)
    else:
        _remove_file(root)


def _remove_file(p):
    try:
        os.unlink(p)
    except PermissionError:
        try:
            os.chmod(p, stat.S_IWRITE)
            os.unlink(p)
        except PermissionError:
            reset_acl(p)
            os.chmod(p, stat.S_IWRITE)
            os.unlink(p)


def _remove_directory(d):
    try:
        entries = list(os.scandir(d))
    except PermissionError:
        reset_acl(d)
        entries = list(os.scandir(d))
    for entry in entries:
        p = os.path.join(d, entry.name)
        if entry.is_dir(follow_symlinks=False):
            if _is_reparse(entry):
                os.rmdir(p)                 # the junction or link, not what it points at
            else:
                _remove_directory(p)
        else:
            _remove_file(p)
    try:
        os.rmdir(d)
    except PermissionError:
        os.chmod(d, stat.S_IWRITE)
        reset_acl(d)
        os.rmdir(d)


def read_matrix(csv_path) -> dict[str, dict]:
    """The reconciliation CSV as {id: row}, or {} when it is not there."""
    csv_path = Path(csv_path) if csv_path else None
    if csv_path is None or not csv_path.is_file():
        return {}
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        return {row["id"].strip(): row for row in csv.DictReader(handle) if row.get("id")}

# Marker phrases are unique nonsense trigrams so an FTS hit cannot be accidental.
# `indexable` records whether content extraction covers that format: every
# format here is covered since extraction widened on 2026-09-12 (CSV was the
# pinned exception before that). A marker whose flag is False is expected to
# be unsearchable, and the acceptance check says so if it ever is found --
# so a widening or a narrowing of coverage fails loudly either way.
MARKERS = {
    "Documents/quarterly_report.docx": ("zarquon reconciliation variance", True),
    "Documents/budget_model.xlsx": ("flimberly allocation ceiling", True),
    "Documents/research_note.pdf": ("quixotic chloroplast cascade", True),
    "Documents/meeting_minutes.txt": ("vermillion quorum adjournment", True),
    "Mixed/inventory_extract.csv": ("grobnar stocktake delta", True),
    "Documents/legacy_memo.doc": ("wumbledore ledger fastening", True),
    "Documents/legacy_ledger.xls": ("pallister quotient harbor", True),
    "Documents/legacy_deck.ppt": ("crandall spindle overture", True),
    "Documents/cover_letter.rtf": ("obrenzo tariff lantern", True),
    "Documents/saved_page.html": ("kestrel varnish ledgerline", True),
    "Documents/export.json": ("mizzen tabulated cormorant", True),
    "Documents/misnamed_rtf.doc": ("pellucid gantry sonnet", True),
    "Documents/server.log": ("sienna quorum ledger", True),
    "Documents/settings.xml": ("basalt fathom marker", True),
    "Documents/contact.vcf": ("umber lattice firm", True),
    "Documents/hearing.ics": ("ochre gantry hearing", True),
    "Documents/README": ("cobalt hinge readme", True),
    "Mail/agenda.eml": ("harrow cadence enclosure", True),        # inside its .txt attachment
    "Mail/forwarded.eml": ("bittern relay nested", True),         # inside a forwarded message
    "Mail/archive.mbox": ("ferrule tally mailbox", True),         # the second message
    "Mail/saved_page.mht": ("tessellate archived page", True),
    "Mail/discovery_schedule.msg": ("barnacle ledger cadence", True),  # inside its attachment
    "Mail/tika_mailbox.pst": ("is the original email", True),            # Apache Tika's test mailbox
    "Documents/manual.odt": ("obsidian quill ledger", True),
    "Documents/ledger_open.ods": ("ledger open marker phrase", True),
    "Documents/deck_open.odp": ("deck open marker phrase", True),
    "Documents/novel.epub": ("sable canticle ridge", True),
    "Documents/memo_macro.docm": ("ambergris ledger macro", True),
    "Documents/memo_template.dotx": ("ambergris ledger template", True),
    "Documents/memo_macro_template.dotm": ("ambergris ledger both", True),
    "Documents/ledger_macro.xlsm": ("ledger macro marker phrase", True),
    "Documents/ledger_template.xltx": ("ledger template marker phrase", True),
    "Documents/ledger_macro_template.xltm": ("ledger macro template marker phrase", True),
    "Documents/deck_macro.pptm": ("deck macro marker phrase", True),
    "Documents/deck_template.potx": ("deck template marker phrase", True),
    "Documents/deck_macro_template.potm": ("deck macro template marker phrase", True),
    "Documents/deck_show.ppsx": ("deck show marker phrase", True),
    "Documents/deck_macro_show.ppsm": ("deck macro show marker phrase", True),
    "Code/app.py": ("tessellate ledger routine", True),
    "Code/settings.toml": ("quorum lantern setting", True),
    "Code/captions.srt": ("subtitle ochre cadence", True),
    "Archives/bundle.zip": ("nested quince memo", True),            # in a .docx inside a zip inside the zip
    "Archives/bundle.7z": ("sevenzip harrow note", True),
    "Scans/scan_letter.tif": ("scanned lantern covenant", True),    # only OCR can find these four
    "Scans/scan_letter.jpg": ("photographed ledger clause", True),
    "Scans/scanned_only.pdf": ("scanned tessera brief", True),
    "Scans/scan_pages.pdf": ("second page tessera", True),          # page two of a mixed PDF
}

#: 01_Naming (matrix section A). The names are computed once so the marker
#: table, the builder and the truth agree on every character.
A001_NAME = ("A001_the_longest_name_ntfs_allows_" * 8)[:251] + ".txt"      # 255 characters
A002_FOLDER = ("A002_the_longest_folder_name_ntfs_allows_" * 7)[:255]      # 255 characters
A003_TREE = "01_Naming/deep/" + "/".join("level%02d" % i for i in range(1, 41))   # 40 levels (A-004)
A003_LEAF = ("A003_long_leaf_" * 14)[:196] + ".txt"                        # 200 characters
MARKERS.update({
    "01_Naming/" + A001_NAME: ("bracken ledger longname", True),
    A003_TREE + "/" + A003_LEAF: ("gossamer quorum longpath", True),
    A003_TREE + "/deep.txt": ("fortieth level marker", True),
    # A-020: PDF bytes named .exe. Extraction is chosen by extension, so the
    # marker is expected NOT to be found; if coverage ever widens, this says so.
    "01_Naming/invoice.pdf.exe": ("ptarmigan invoice disguise", False),
})

#: 05_Corruption (matrix section E, Y-012..Y-017). A key that is not a file
#: path (`archive!member`) is a phrase expected NOT to be found: the FTS
#: checks assert an empty result for those, so a cap that stops holding
#: says so.
MARKERS.update({
    "05_Corruption/truncated_at_60pct.xlsx": ("halberd ledger truncated", False),
    "05_Corruption/crc_member.zip": ("wimple crc intact", True),           # member 1, readable
    "05_Corruption/crc_member.zip!bad_member": ("gorget crc broken", False),  # member 2, bad CRC
    "05_Corruption/nested/level1.zip": ("nesting two marker", True),       # the level-2 note, one archive down: read
    "05_Corruption/nested/level1.zip!level3": ("nesting three marker", False),  # depth 2: beyond MAX_ARCHIVE_DEPTH
    "05_Corruption/nested/level1.zip!level4": ("nesting four marker", False),
    "05_Corruption/nested/level1.zip!level5": ("nesting five marker", False),
    "05_Corruption/expansion/big_member.zip": ("ferrule beside giant", True),   # the small note beside the 100 MB member
    "05_Corruption/traversal/zip_slip.zip": ("escapee dotdot marker", True),  # read in memory; nothing written outside
    "05_Corruption/traversal/zip_slip.7z": ("escapee sevenzip marker", False),   # py7zr refuses the name; nothing is read
    "05_Corruption/protected/zipcrypto.zip!note": ("cipher zip secret", False),   # encrypted; never read
    "05_Corruption/protected/sevenzip_password.7z!note": ("cipher seven secret", False),
    "05_Corruption/protected/sevenzip_headers.7z!note": ("cipher headers secret", False),
    "05_Corruption/misnamed/document.txt": ("pdf under txt marker", True),    # PDF bytes named .txt: read as PDF
})

#: 07_File_Types and 19_Extraction_Safety.
MARKERS.update({
    "07_File_Types/misnamed/photo.jpg": ("docx under jpg marker", True),          # a real .docx named .jpg: read by its bytes
    "07_File_Types/misnamed/notes.docx": ("program under docx marker", False),    # a program named .docx: nothing to read
    "19_Extraction_Safety/control_chars.txt": ("escapes before marker", True),     # two control characters: within the allowance
    "19_Extraction_Safety/colour.log": ("ansi colour log marker", True),           # a colourised log: its escape sequences stripped (B7.2)
    "19_Extraction_Safety/nul_bytes.txt": ("nulls before marker", False),          # a NUL byte means binary: refused whole
    "19_Extraction_Safety/encodings/utf8_bom.txt": ("encoding utf8bom marker", True),
    "19_Extraction_Safety/encodings/utf16le_bom.txt": ("encoding utf16le marker", True),
    "19_Extraction_Safety/encodings/utf16be_nobom.txt": ("encoding utf16be marker", False),   # no BOM: the NULs read as binary
    "19_Extraction_Safety/encodings/cp1252.txt": ("encoding cp1252 marker", True),
    "19_Extraction_Safety/encodings/shift_jis.txt": ("encoding shiftjis marker", True),
    "19_Extraction_Safety/encodings/utf8_with_stray_bytes.txt": ("encoding straybytes marker", True),
})

MARKERS.update({
    # Release 1 -- documents. What the readers reach, and what they do not.
    "13_Excel_Office/hidden_sheets.xlsx": ("hidden sheet marker phrase", True),           # the hidden AND the very-hidden sheet are read
    "13_Excel_Office/formulas_openpyxl.xlsx": ("formula result marker", False),           # no cached values: never opened in Excel
    "13_Excel_Office/formulas_excel.xlsx": ("formula cached marker", True),               # the same workbook after Excel saved it
    "13_Excel_Office/defined_names.xlsx": ("defined name marker", False),                 # names are not extracted
    "13_Excel_Office/comments.xlsx": ("cell comment marker", False),                      # comments are not extracted
    "13_Excel_Office/hyperlinks.xlsx": ("hyperlink target marker", False),                # the URL is not extracted; the cell text is
    "13_Excel_Office/custom_properties.xlsx": ("custom property marker", False),          # custom document properties are not extracted
    "16_Templates/hidden_text.docx": ("hidden run marker", True),                         # w:vanish text is read like any other
    "16_Templates/tracked_changes.docx": ("deleted run marker", False),                   # w:delText is not w:t: deleted text is not the document's
    "20_Automation/deploy.bat": ("automation batch marker", True),
    "22_Security_Boundaries/db_connection.ini": ("hunter2 credential marker", True),      # a credential string is indexed like any other text
})

#: Files OCR is expected to flag for a person to look at.
OCR_REVIEW_EXPECTED = ["Scans/scan_faded.png", "Scans/scan_bad.pdf"]
#: Files OCR is expected to read cleanly (no review flag).
OCR_CLEAN_EXPECTED = ["Scans/scan_letter.tif", "Scans/scan_letter.jpg", "Scans/scanned_only.pdf"]
#: Files that are not documents: extraction records them as not processed.
NOT_DOCUMENTS = ["Scans/holiday_photo.jpg", "Documents/thumbnail"]


def marker(relpath: str) -> str:
    return MARKERS[relpath][0]


# --------------------------------------------------------------------------
# Minimal generators for real, parseable document formats
# --------------------------------------------------------------------------

def make_pdf(text: str, padding: int = 0) -> bytes:
    """A minimal single-page PDF with an extractable text stream. `padding`
    adds an unreferenced stream object of that many zero bytes: the file is
    that big on disk (and inside an archive, that big uncompressed) while
    every reader still finds one line of text -- the shape the archive
    expansion cases need."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    if padding:
        objs.append(b"<</Length " + str(padding).encode() + b">>\nstream\n" + bytes(padding) + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<</Size {len(objs) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n"
            "%%EOF\n").encode()
    return bytes(out)


def normalize_ooxml(data: bytes, keep_dates: bool = False) -> bytes:
    """Rewrite an OOXML archive with fixed entry timestamps (and, unless
    `keep_dates`, the document's own timestamps pinned to NOW).

    DOCX and XLSX are ZIPs, and both python-docx and openpyxl stamp every entry
    with the current clock time. That leaves files of identical size but
    different bytes on each build, so hashes and duplicate groups would not be
    reproducible from the seed. Rebuilding with one fixed date_time makes the
    corpus regenerable, which is what lets ground truth be trusted.
    """
    import re
    import zipfile

    fixed = NOW.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    iso = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?")

    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in sorted(src.namelist()):
            payload = src.read(name)
            # openpyxl rewrites docProps/core.xml <dcterms:modified> at save
            # time regardless of what the properties were set to, so pin any
            # timestamp the document carries rather than trusting the library.
            if name.startswith("docProps/") and not keep_dates:
                payload = iso.sub(fixed, payload)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            dst.writestr(info, payload)
    src.close()
    return out.getvalue()


def make_docx(paragraphs: list[str], author: str | None = None) -> bytes:
    from docx import Document
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    stamp = NOW.replace(tzinfo=None)
    if author is not None:
        doc.core_properties.author = author
    doc.core_properties.created = stamp
    doc.core_properties.modified = stamp
    doc.core_properties.last_modified_by = "p2_build_acceptance_corpus"
    doc.core_properties.revision = 1
    buf = io.BytesIO()
    doc.save(buf)
    return normalize_ooxml(buf.getvalue())


def make_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    # openpyxl stamps docProps/core.xml with the current time, which makes the
    # file -- and therefore its hash and size -- differ on every build. Pin the
    # timestamps so the corpus is genuinely reproducible from the seed.
    stamp = NOW.replace(tzinfo=None)
    wb.properties.created = stamp
    wb.properties.modified = stamp
    ws = wb.active
    ws.title = "Data"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return normalize_ooxml(buf.getvalue())


def xlsx_bytes(wb) -> bytes:
    """An openpyxl workbook as reproducible bytes (pinned properties, fixed
    entry times)."""
    stamp = NOW.replace(tzinfo=None)
    wb.properties.created = stamp
    wb.properties.modified = stamp
    buf = io.BytesIO()
    wb.save(buf)
    return normalize_ooxml(buf.getvalue())


def rewrite_ooxml(data: bytes, edits: dict[str, bytes | None]) -> bytes:
    """A copy of an OOXML package with parts replaced, added (name -> bytes)
    or removed (name -> None). The way to reach what python-docx and
    openpyxl do not expose: template content types, attached templates."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        names = list(src.namelist()) + [n for n in edits if n not in src.namelist()]
        for name in sorted(names):
            payload = edits[name] if name in edits else src.read(name)
            if payload is None:
                continue
            dst.writestr(zipfile.ZipInfo(name, NOW.timetuple()[:6]), payload, zipfile.ZIP_DEFLATED)
    return out.getvalue()


def make_dotx(paragraphs: list[str]) -> bytes:
    """A Word template: a .docx whose main part is typed as a template."""
    data = make_docx(paragraphs)
    types = zipfile.ZipFile(io.BytesIO(data)).read("[Content_Types].xml")
    types = types.replace(b"wordprocessingml.document.main+xml", b"wordprocessingml.template.main+xml")
    return rewrite_ooxml(data, {"[Content_Types].xml": types})


def attach_template(docx: bytes, target: str) -> bytes:
    """The document with settings.xml naming `target` as its attached
    template (the relationship Word writes for a document based on a
    .dotx); `target` is a file:/// URI or a plain path, kept verbatim."""
    zf = zipfile.ZipFile(io.BytesIO(docx))
    settings = zf.read("word/settings.xml")
    rels = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rIdTpl" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate" '
            b'Target="' + target.encode("utf-8") + b'" TargetMode="External"/></Relationships>')
    if b'xmlns:r="' not in settings:
        settings = settings.replace(b"<w:settings ", b'<w:settings xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" ', 1)
    settings = settings.replace(b"<w:settings", b"<w:settings", 1)
    head_end = settings.index(b">", settings.index(b"<w:settings")) + 1
    settings = settings[:head_end] + b'<w:attachedTemplate r:id="rIdTpl"/>' + settings[head_end:]
    return rewrite_ooxml(docx, {"word/settings.xml": settings, "word/_rels/settings.xml.rels": rels})


def make_xlsx_external(cells: list[list], links: list[tuple[str, str]], names: dict[str, str] | None = None) -> bytes:
    """A workbook whose formulas reach into other workbooks. `links` are
    (target, cached value) pairs -- the target as Excel stores it (a
    file:/// URI, a relative name, an https:// address) and the value the
    last save cached for [n]Sheet1!A1; formula cells in `cells` say
    "=[1]Sheet1!A1". `names` adds defined names ({name: refers_to}) so a
    reference can live in a name instead of a cell (Y-025)."""
    from openpyxl import Workbook
    from openpyxl.packaging.relationship import Relationship
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.workbook.external_link.external import (ExternalBook, ExternalCell, ExternalLink, ExternalRow,
                                                          ExternalSheetData, ExternalSheetDataSet, ExternalSheetNames)
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    for row in cells:
        ws.append(row)
    for target, cached in links:
        link = ExternalLink()
        link.externalBook = ExternalBook(
            sheetNames=ExternalSheetNames(sheetName=["Sheet1"]),
            sheetDataSet=ExternalSheetDataSet(sheetData=[ExternalSheetData(
                sheetId=0, row=[ExternalRow(r=1, cell=[ExternalCell(r="A1", t="str", v=cached)])])]))
        link.file_link = Relationship(type="externalLinkPath", Target=target, TargetMode="External")
        wb._external_links.append(link)
    for name, refers_to in (names or {}).items():
        wb.defined_names[name] = DefinedName(name, attr_text=refers_to)
    return xlsx_bytes(wb)


def excel_available() -> bool:
    try:
        import win32com.client  # noqa: F401
        return os.path.exists(r"C:\Program Files\Microsoft Office\Root\Office16\EXCEL.EXE")
    except ImportError:
        return False


def excel_saved(source: Path, name: str, password: str | None = None) -> bytes:
    """`source` opened, recalculated and saved again by Excel -- the only
    way to a workbook with cached formula values, and to one that needs a
    password to open. Excel runs invisible, on a file this builder wrote
    (asserted), and is quit and, if it lingers, ended by PID; nothing in
    the corpus is opened by it (rule 7)."""
    import gc
    import pythoncom
    import win32com.client
    import win32process
    assert source.parent.name == "excel_work", source            # only files this builder wrote
    out = source.parent / name
    if out.exists():
        out.unlink()
    pythoncom.CoInitialize()
    xl = win32com.client.DispatchEx("Excel.Application")
    _tid, pid = win32process.GetWindowThreadProcessId(xl.Hwnd)
    try:
        xl.Visible = False
        xl.DisplayAlerts = False
        xl.AutomationSecurity = 3                       # msoAutomationSecurityForceDisable: no macro could run
        book = xl.Workbooks.Open(str(source))
        xl.CalculateFull()
        if password:
            book.SaveAs(str(out), FileFormat=51, Password=password)
        else:
            book.SaveAs(str(out), FileFormat=51)
        book.Close(SaveChanges=False)
        book = None
    finally:
        xl.Quit()
        xl = None
        gc.collect()
        pythoncom.CoUninitialize()
        alive = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True).stdout
        if "EXCEL" in alive:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    return out.read_bytes()


def make_png(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def make_rtf(paragraphs: list[str]) -> bytes:
    """A small RTF with a font table, a hyperlink field and a hex-escaped
    character, so the reader's group handling is exercised, not just its
    happy path."""
    body = "\\par\n".join(paragraphs)
    return (
        "{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fswiss Arial;}}"
        "{\\colortbl;\\red0\\green0\\blue0;}{\\info{\\title Ignored title}}\n"
        "\\pard\\f0\\fs24 " + body + "\\par\n"
        "{\\field{\\*\\fldinst HYPERLINK \"http://example.invalid\"}{\\fldrslt visible link text}}\\par\n"
        "Caf\\'e9 closes.\\par\n}"
    ).encode("latin-1")


def make_html(title: str, paragraphs: list[str]) -> bytes:
    body = "".join(f"<p>{p}</p>\n" for p in paragraphs)
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\"><title>" + title + "</title>\n"
        "<style>p { color: #333 }</style><script>var hidden = 'not text';</script></head>\n"
        "<body><h1>" + title + "</h1>\n" + body +
        "<table><tr><td>cell one</td><td>cell two</td></tr></table>\n</body></html>\n"
    ).encode("utf-8")


def make_eml(subject: str, sender: str, to: str, date: str, body: str,
             attachments: list[tuple[str, bytes]] = (), forwarded=None) -> bytes:
    """A real RFC 822 message through the standard library, with attachments
    and optionally a forwarded message (message/rfc822) inside."""
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = date
    msg.set_content(body)
    for name, data in attachments:
        msg.add_attachment(data, maintype="text", subtype="plain", filename=name)
    if forwarded is not None:
        msg.add_attachment(forwarded, filename="forwarded.eml")
    if msg.is_multipart():
        # The generator invents a random boundary at every call, which made
        # the two multipart messages the only files in the corpus whose
        # bytes changed between builds. Fixed, so their hashes are truth.
        msg.set_boundary("----=_Corpus_Boundary_" + hashlib.sha256(subject.encode()).hexdigest()[:12])
    return msg.as_bytes()


def make_message(subject: str, sender: str, to: str, date: str, body: str):
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = date
    msg.set_content(body)
    return msg


def make_mbox(messages) -> bytes:
    out = []
    for sender, stamp, msg in messages:
        out.append(f"From {sender} {stamp}\n".encode("ascii"))
        out.append(msg.as_bytes().replace(b"\r\n", b"\n"))
        out.append(b"\n")
    return b"".join(out)


def make_mht(title: str, paragraphs: list[str]) -> bytes:
    html = make_html(title, paragraphs).decode("utf-8")
    return ("From: <Saved by the browser>\r\nSubject: " + title + "\r\nMIME-Version: 1.0\r\n"
            "Content-Type: multipart/related; boundary=\"----=_Part\"\r\n\r\n"
            "------=_Part\r\nContent-Type: text/html; charset=utf-8\r\n"
            "Content-Location: http://example.invalid/page\r\n\r\n" + html + "\r\n------=_Part--\r\n").encode("utf-8")


def make_odt(paragraphs: list[str]) -> bytes:
    """A minimal but valid OpenDocument Text package."""
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
        '<office:body><office:text>' + "".join("<text:p>%s</text:p>" % p for p in paragraphs) +
        '</office:text></office:body></office:document-content>')
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        zf.writestr("content.xml", content.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("META-INF/manifest.xml", manifest.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return normalize_ooxml(buf.getvalue())


def make_epub(title: str, chapters: list[tuple[str, str]]) -> bytes:
    """A minimal EPUB 3: container, package with spine, one XHTML per chapter."""
    items = "".join('<item id="c%d" href="chapter%d.xhtml" media-type="application/xhtml+xml"/>' % (i, i)
                    for i in range(len(chapters)))
    spine = "".join('<itemref idref="c%d"/>' % i for i in range(len(chapters)))
    opf = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:uuid:test</dc:identifier>'
           '<dc:title>%s</dc:title><dc:creator>Corpus Builder</dc:creator><dc:language>en</dc:language></metadata>'
           '<manifest>%s</manifest><spine>%s</spine></package>' % (title, items, spine))
    container = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
        for i, (heading, body) in enumerate(chapters):
            xhtml = ('<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>%s</title></head>'
                     '<body><h1>%s</h1><p>%s</p></body></html>' % (heading, heading, body))
            zf.writestr("OEBPS/chapter%d.xhtml" % i, xhtml.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return normalize_ooxml(buf.getvalue())


_OOXML_MAIN_TYPES = {
    ".docm": "application/vnd.ms-word.document.macroEnabled.main+xml",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
    ".dotm": "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
}


def make_word_variant(paragraphs: list[str], ext: str) -> bytes:
    """A .docm / .dotx / .dotm: a python-docx document with the package's
    main-part content type rewritten to the variant's. Structurally what
    Word writes for a document without macros."""
    base = make_docx(paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename == "[Content_Types].xml":
                data = data.replace(
                    b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
                    _OOXML_MAIN_TYPES[ext].encode("ascii"))
            dst.writestr(info, data)
    return normalize_ooxml(buf.getvalue())


def make_zip(entries: list[tuple[str, bytes]], level: int | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data, compresslevel=level)
    return buf.getvalue()


def make_zip_raw(entries: list[tuple[str, bytes]], password: str | None = None) -> bytes:
    """A zip written by hand: stored members, optionally under traditional
    PKWARE ("ZipCrypto") encryption, which Python's zipfile reads but will
    not write. Member names are written exactly as given -- `..\\` and drive
    letters included -- because the archive-safety cases need the archive a
    hostile source would ship, and ZipFile.writestr would not refuse them
    either. Verified after writing by reading every member back."""
    import struct
    import zlib

    def _crc_step(key, byte):
        # The table-driven step of PKWARE's stream cipher, one byte at a time.
        return (_CRC_TABLE[(key ^ byte) & 0xFF] ^ (key >> 8)) & 0xFFFFFFFF

    def encrypt(plain: bytes, crc: int, salt: bytes) -> bytes:
        keys = [0x12345678, 0x23456789, 0x34567890]

        def update(byte):
            keys[0] = _crc_step(keys[0], byte)
            keys[1] = ((keys[1] + (keys[0] & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
            keys[2] = _crc_step(keys[2], keys[1] >> 24)

        def stream_byte():
            temp = (keys[2] | 2) & 0xFFFF
            return ((temp * (temp ^ 1)) >> 8) & 0xFF

        for byte in password.encode("latin-1"):
            update(byte)
        header = salt[:11] + bytes([(crc >> 24) & 0xFF])
        out = bytearray()
        for byte in header + plain:
            out.append(byte ^ stream_byte())
            update(byte)
        return bytes(out)

    rng = random.Random(SEED + 11)
    body = bytearray()
    central = bytearray()
    dos_time, dos_date = 0, (2020 - 1980) << 9 | 1 << 5 | 1        # 2020-01-01 00:00
    flags = 0x1 if password else 0
    for name, data in entries:
        crc = zlib.crc32(data) & 0xFFFFFFFF
        payload = encrypt(data, crc, bytes(rng.getrandbits(8) for _ in range(11))) if password else data
        raw_name = name.encode("utf-8")
        offset = len(body)
        body += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags | 0x800, 0, dos_time, dos_date, crc,
                            len(payload), len(data), len(raw_name), 0) + raw_name + payload
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags | 0x800, 0, dos_time, dos_date, crc,
                               len(payload), len(data), len(raw_name), 0, 0, 0, 0, 0, offset) + raw_name
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(body), 0)
    archive = bytes(body + central + end)
    # Verify: every member reads back, with the password when there is one.
    # zipfile turns a backslash into a slash when it reads a name, so a
    # member shipped as `..\\..\\escape.txt` is listed as `../../escape.txt`;
    # the bytes on disk keep the backslashes.
    with zipfile.ZipFile(io.BytesIO(archive)) as check:
        for name, data in entries:
            assert check.read(name.replace("\\", "/"), pwd=password.encode("latin-1") if password else None) == data, name
    return archive


_CRC_TABLE = [0] * 256
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (0xEDB88320 ^ (_c >> 1)) if _c & 1 else (_c >> 1)
    _CRC_TABLE[_i] = _c


def make_7z(entries: list[tuple[str, bytes]], password: str | None = None,
            encrypt_header: bool = False, unchecked_names: bool = False) -> bytes | None:
    """A 7z archive with fixed entry timestamps.

    py7zr stamps every member with the clock (`ArchiveTimestamp.from_now`
    three times per file), so the same bytes made a different archive on
    every build; it is pinned to NOW for the duration of the write.
    `password` encrypts the members; `encrypt_header` hides the names too.
    `unchecked_names` writes member names py7zr's own `writef` refuses
    (`..\\`, absolute paths) -- for the archive-safety cases, where the
    corpus needs exactly the archive a hostile source would ship.
    """
    try:
        import py7zr
        from py7zr import helpers
    except ImportError:
        return None
    fixed = helpers.ArchiveTimestamp.from_datetime(NOW.timestamp())
    original = helpers.ArchiveTimestamp.from_now
    helpers.ArchiveTimestamp.from_now = classmethod(lambda cls: fixed)
    # An encrypted archive also draws a random AES initialisation vector
    # (py7zr.compressor.get_random_bytes); a fixed one keeps the two
    # password-protected archives the same bytes on every build. The IV
    # only has to be unpredictable to an attacker, and these have no secret.
    import py7zr.compressor as compressor
    original_random = compressor.get_random_bytes
    compressor.get_random_bytes = lambda n: random_bytes(SEED + 77, n)
    try:
        buf = io.BytesIO()
        with py7zr.SevenZipFile(buf, "w", password=password) as archive:
            if password and encrypt_header:
                archive.set_encrypted_header(True)
            for name, data in entries:
                if unchecked_names:
                    archive._writef(io.BytesIO(data), name)    # noqa: SLF001 -- deliberate
                else:
                    archive.writef(io.BytesIO(data), name)
        return buf.getvalue()
    finally:
        helpers.ArchiveTimestamp.from_now = original
        compressor.get_random_bytes = original_random


def render_page(lines: list[str], size=(1700, 2200), font_size=44, fill="black", background="white"):
    """A page of text as a picture -- what a scanner produces, minus the
    scanner. Letter size at 200 dpi."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    y = 160
    for line in lines:
        draw.text((160, y), line, fill=fill, font=font)
        y += int(font_size * 1.6)
    return img


def degrade(img):
    """Blur it, wash it out, tilt it, add noise: a copier's fourth-generation copy."""
    from PIL import ImageEnhance, ImageFilter
    out = img.filter(ImageFilter.GaussianBlur(2.5))
    out = ImageEnhance.Contrast(out).enhance(0.45)
    out = out.rotate(3.5, expand=False, fillcolor=(235, 235, 235))
    rng = random.Random(SEED + 7)
    pixels = out.load()
    for _ in range(4000):
        x, y = rng.randrange(out.width), rng.randrange(out.height)
        pixels[x, y] = (40, 40, 40)
    return out


def image_bytes(img, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return pin_pdf_dates(buf.getvalue()) if fmt.upper() == "PDF" else buf.getvalue()


def pin_pdf_dates(data: bytes) -> bytes:
    """Pillow's PDF writer stamps /CreationDate and /ModDate with the clock.
    Both are replaced by NOW, digit for digit, so no xref offset moves and
    the scanned PDFs hash the same on every build."""
    import re
    fixed = NOW.strftime("D:%Y%m%d%H%M%SZ").encode("ascii")
    return re.sub(rb"D:\d{14}Z", fixed, data)


def make_raw_tiff(make: str, model: str, when: str) -> bytes:
    """A TIFF with EXIF -- the structure every TIFF-based camera RAW shares
    (DNG, CR2, NEF, ARW, PEF ...), which is what the RAW analyzer reads."""
    from PIL import Image
    img = Image.new("RGB", (96, 64), (110, 90, 70))
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = model
    exif[0x8769] = {0x9003: when, 0x829A: (1, 250), 0x829D: (40, 10), 0x8827: 400, 0x920A: (35, 1)}
    return image_bytes(img, "TIFF", exif=exif.tobytes())


def ffmpeg_bytes(args: list[str], ext: str) -> bytes | None:
    """One small media file from ffmpeg's synthetic sources, or None when
    ffmpeg is not on PATH or refuses the format."""
    import subprocess
    import tempfile
    if shutil.which("ffmpeg") is None:
        return None
    tmp = tempfile.NamedTemporaryFile(prefix="fo_media_", suffix=ext, delete=False)
    tmp.close()
    try:
        cmd = (["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"] + args +
               ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1", tmp.name])
        result = subprocess.run(cmd, capture_output=True, timeout=120,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            return None
        return Path(tmp.name).read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def make_lnk(absolute_target: str, relative_target: str, is_dir: bool = False) -> bytes:
    """A Windows shortcut in the MS-SHLLINK format: a header, a LinkInfo
    block with the target's local base path, the relative path and a working
    directory. No ItemIDList -- the shell's CreateShortcut writes one, but
    it carries the target's creation and access times and so differs on
    every build; the corpus has to hash the same twice. WScript's
    TargetPath reads empty without the ItemIDList; a link parser reading
    LinkInfo or RELATIVE_PATH gets the target. The scanner treats any .lnk
    as an ordinary file, which is what these cases establish.
    """
    import struct
    flags = 0x02 | 0x08 | 0x10 | 0x80               # HasLinkInfo | HasRelativePath | HasWorkingDir | IsUnicode
    attributes = FILE_ATTRIBUTE_DIRECTORY if is_dir else 0x20
    header = struct.pack("<I16sIIQQQIIIHHII", 0x4C, bytes.fromhex("0114020000000000C000000000000046"),
                         flags, attributes, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    base = absolute_target.encode("mbcs", "replace") + b"\x00"
    volume = struct.pack("<IIII", 17, 3, 0, 16) + b"\x00"        # fixed drive, serial 0, empty label
    header_size = 28
    volume_offset = header_size
    base_offset = volume_offset + len(volume)
    suffix_offset = base_offset + len(base)
    link_info = (struct.pack("<IIIIIII", suffix_offset + 1, header_size, 0x1, volume_offset, base_offset, 0, suffix_offset)
                 + volume + base + b"\x00")

    def string_data(text):
        return struct.pack("<H", len(text)) + text.encode("utf-16-le")

    return header + link_info + string_data(relative_target) + string_data(".") + b"\x00\x00\x00\x00"


def dotnet_extension(name: str) -> str:
    """System.IO.Path.GetExtension, lower-cased -- what the scanner records
    as extension_key (win_meta.dotnet_extension): everything from the last
    dot unless the dot is the final character. `.gitignore` is `.gitignore`
    here and nothing to os.path.splitext; the truth follows the program."""
    dot = name.rfind(".")
    if dot < 0 or dot == len(name) - 1:
        return ""
    return name[dot:].lower()


def random_bytes(seed: int, n: int) -> bytes:
    """n bytes from one seeded generator. (`bytes(random.Random(seed).getrandbits(8)
    for _ in range(n))` re-seeds on every iteration and yields one byte n
    times -- three corpus files were built that way until 2026-09-13.)"""
    rng = random.Random(seed)
    return bytes(rng.getrandbits(8) for _ in range(n))


def make_gzip(payload: bytes) -> bytes:
    import gzip
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as fh:
        fh.write(payload)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Corpus definition
# --------------------------------------------------------------------------

class Corpus:
    #: Which tree this builder makes; the truth records it on every case.
    home = "Corpus"

    def __init__(self, root: Path, samples: Path | None = None, matrix: dict | None = None):
        self.root = root
        self.samples = samples
        self.matrix = matrix or {}
        self.files: list[dict] = []
        self.rng = random.Random(SEED)
        self.skipped: list[str] = []          # what could not be made here, and why
        self.cases: dict[str, dict] = {}      # matrix ID -> the case record (see case())
        self.not_constructed: list[dict] = []  # matrix IDs this machine cannot build, and why
        self.empty_folders: list[str] = []    # folders made with nothing in them (add_folder)
        self.link_folders: list[str] = []     # junctions / directory symlinks the walk must skip

    def add(self, relpath: str, data: bytes, *, age_days: int, note: str = "",
            expect_analyzer_failure: bool = False, case: str | None = None,
            attributes: int = 0, created: datetime | None = None,
            sparse_to: int | None = None, compress: bool = False, streams: dict | None = None):
        """Write one file and record its truth.

        `attributes` are FILE_ATTRIBUTE_* bits to set after writing (hidden,
        system, read-only). `created` pins the creation time -- NTFS keeps
        it separately from the modified time, which `age_days` sets.
        `sparse_to` marks the file sparse and extends it to that many bytes
        (the tail reads as zeros and occupies nothing); the truth records
        the on-disk size and the hash of data plus the zero tail.
        `compress` sets NTFS compression on the file. `streams` writes named
        alternate data streams ({name: bytes}); they are not part of the
        file's size or hash -- the scanner sees the main stream only.
        """
        path = self.root / relpath
        target = ext_path(path)
        os.makedirs(ext_path(path.parent), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        size, digest = len(data), hashlib.sha256(data).hexdigest()
        if sparse_to is not None and sparse_to > len(data):
            make_sparse(target, sparse_to)
            size = sparse_to
            tail = hashlib.sha256(data)
            remaining = sparse_to - len(data)
            zeros = bytes(1024 * 1024)
            while remaining > 0:
                chunk = min(remaining, len(zeros))
                tail.update(zeros[:chunk])
                remaining -= chunk
            digest = tail.hexdigest()
        if compress:
            set_compressed(target)
        for stream_name, stream_data in (streams or {}).items():
            with open(target + ":" + stream_name, "wb") as handle:
                handle.write(stream_data)
        stamp = NOW - timedelta(days=age_days)
        ts = stamp.timestamp()
        os.utime(target, (ts, ts))
        if created is not None:
            set_creation_time(target, created)
        if attributes:
            set_attributes(target, attributes)
        relative = relpath.replace("/", "\\")
        self.files.append({
            "relative_path": relative,
            "size_bytes": size,
            "sha256": digest,
            "extension": dotnet_extension(Path(relpath).name),
            "modified_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_days": age_days,
            "note": note,
            "expect_analyzer_failure": expect_analyzer_failure,
        })
        if attributes:
            self.files[-1]["attributes"] = attributes
        if created is not None:
            self.files[-1]["created_utc"] = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        if case:
            self.case(case)["paths"].append(relative)

    def add_folder(self, relpath: str, *, case: str | None = None, note: str = ""):
        """An empty folder. Folders are not rows in this program; the walk
        counts them (`Empty folders found` in the preliminary report), which
        is what the truth's scan_expectations carry."""
        os.makedirs(ext_path(self.root / relpath), exist_ok=True)
        relative = relpath.replace("/", "\\")
        self.empty_folders.append(relative)
        if case:
            self.case(case)["paths"].append(relative)

    # -- the matrix's cases ---------------------------------------------------

    def case(self, case_id: str, *, expected: dict | None = None, construction: str | None = None,
             setup=(), teardown=(), safety=(), notes: str = "", matrix_expects: str | None = None,
             classification: str | None = None, condition: str | None = None) -> dict:
        """Register (or update) the matrix condition this corpus embodies.

        `expected` is written in this program's vocabulary -- column names and
        the values a check asserts (`file_observation.status = 'inaccessible'`,
        `path_length > 260`). Condition text, priority, disposition and layer
        come from the reconciliation CSV when it is present; `matrix_expects`
        is the matrix's own wording where it wants more than the program
        records, so the gap stays visible beside the assertion.
        """
        record = self.cases.get(case_id)
        if record is None:
            row = self.matrix.get(case_id, {})
            record = self.cases[case_id] = {
                "id": case_id,
                "condition": row.get("condition"),
                "priority": row.get("priority"),
                "disposition": row.get("disposition"),
                "layer": row.get("layer"),
                "construction_in_matrix": row.get("construction"),
                "construction": None,
                "home": self.home,
                "paths": [],
                "setup": [],
                "teardown": [],
                "expected": {},
                "matrix_expects": row.get("expected_behavior"),
                "safety": [],
                "status": "PLANNED",
                "classification": None,
                "notes": "",
            }
        if expected:
            record["expected"].update(expected)
        if construction:
            record["construction"] = construction
        if matrix_expects:
            record["matrix_expects"] = matrix_expects
        if condition and not record["condition"]:
            record["condition"] = condition        # compound scenarios are not in the CSV
        if classification:
            # DEFECT: the engine is wrong and the expectation is the matrix's
            # (the check stays red until it is fixed). SCOPE: a recorded
            # limit; the expectation is the documented current behaviour and
            # matrix_expects keeps the gap in view.
            record["classification"] = classification
        record["setup"].extend(setup)
        record["teardown"].extend(teardown)
        record["safety"].extend(s for s in safety if s not in record["safety"])
        if notes:
            record["notes"] = (record["notes"] + " " + notes).strip()
        return record

    def decline(self, case_id: str, reason: str):
        """A matrix condition this machine cannot build: recorded, not skipped silently."""
        row = self.matrix.get(case_id, {})
        self.not_constructed.append({
            "id": case_id,
            "condition": row.get("condition"),
            "priority": row.get("priority"),
            "construction_in_matrix": row.get("construction"),
            "reason": reason,
        })

    # -- content families ---------------------------------------------------

    def build(self):
        self._documents()
        self._more_documents()
        self._code_and_config()
        self._archives()
        self._known_duplicates()
        self._images()
        self._every_image_format()
        self._scans()
        self._raw()
        self._media()
        self._malformed()
        self._edge_cases()
        self._bulk()
        self._samples()
        # The Master Matrix's categories, one method each (2026-09-13 on).
        self._naming()
        self._duplicates_identity()
        self._links()
        self._corruption()
        self._metadata()
        self._file_types()
        self._extreme_structures()
        self._extraction_safety()
        # Release 1 -- documents (matrix Stage 3), 2026-09-15 on.
        self._excel_office()
        self._protection()
        self._templates()
        self._document_relationships()
        self._embedded_objects()
        self._automation_and_organisation()
        self._registrations()
        self._parked()

    #: Matrix conditions this machine cannot build, by group, with the reason
    #: (Corpus_Design_Proposal_v1.0 section 7). Declined here so the truth
    #: lists them beside the cases and nothing is skipped silently.
    PARKED = (
        (("J-001", "J-002", "J-003", "J-004", "J-005", "J-006", "J-007", "J-008", "J-009", "J-010", "J-011", "J-012", "J-013",
          "K-001", "K-002", "K-003", "K-004", "K-005", "K-006", "K-007", "U-002", "U-005", "U-010",
          "Y-037", "Y-038", "Y-039", "Y-041", "Y-042", "X-009", "X-010", "X-011", "X-012"),
         "network and authentication: no controlled share; a loopback SMB share (New-SmbShare) needs administrator rights and Windows Pro"),
        (("L-003", "L-004", "L-005", "L-007", "L-008", "L-009", "X-013"),
         "cloud beyond OneDrive placeholders: sync conflicts and shared libraries need a second device or account"),
        (("L-001", "L-002", "L-006"),
         "OneDrive placeholders: real corpus only -- one 'Free up space' file the user makes; expected is_offline_or_cloud=1, "
         "hash and analyzers skipped_cloud_only, and the immutability check shows it was not hydrated (rule 2)"),
        (("F-008",), "EFS: Windows Home has no Encrypting File System"),
        (("W-002", "W-005", "W-006"),
         "FAT/exFAT: a USB stick formatted FAT32 would do it (timestamps to 2 s, no attributes, no links); the user supplies the stick"),
        (("V-005",), "security software: rule 7 -- nothing in the corpus may be dangerous to run against the inventory"),
        (("S-001", "S-002", "S-003", "S-004", "S-005", "S-006", "T-004", "T-005", "T-006", "T-007", "T-008", "T-009", "Y-040"),
         "corporate application dependencies: simulation only, and nothing in Phase 2 would observe them"),
        (("B-007", "B-008", "U-008", "U-009", "Y-054"), "similarity and authority: LATER per the Architecture Review"),
        # Release 1 (2026-09-15): what the libraries cannot author and Office would not.
        (("M-008", "M-013", "Y-026", "Y-027"),
         "pivot tables, Power Query and chart/text-box references need Excel to author them, and Excel's object model was used "
         "for two files only (cached formula values, a password to open)"),
        (("N-004", "N-005", "N-006", "N-007", "O-004", "V-004", "N-008", "N-009"),
         "VBA class modules, UserForms, a protected VBA project and macros that name external resources need VBA authored in the "
         "editor: no licence-clear sample has them and nothing here writes VBA (rule 7)"),
        (("P-006", "P-008"), "an inaccessible or corporate template: the attachedTemplate relationship is not read, so the case "
                             "would be indistinguishable from P-005 today; deferred to the relationship layer"),
        (("Q-005",), "a referenced workbook behind a denied ACL: as P-006 -- no link is read, so nothing would observe the denial"),
        (("D-006", "D-009"), "another user's file and unusual ownership need a second account or elevation (icacls /setowner)"),
        (("Y-003",), "an NTFS volume mount point needs a second volume"),
        (("Y-005", "Y-006"), "8.3 aliases and environment-variable paths are not inputs the program accepts: the root is always the "
                             "long, expanded path"),
        (("Y-007",), "NTFS refuses control characters in names even through the extended-length prefix (tested 2026-09-15); POSIX namespace only, as A-017"),
        (("Y-010",), "timestamp precision differences need a FAT volume, as W-002"),
        (("Y-036", "Y-044", "Y-046"), "folder redirection, application-virtualised files and a drive letter that changes between scans: "
                                      "the project root is a fixed path by design; a root that moves is a different project"),
    )

    # -- Release 1: documents -------------------------------------------------

    def _excel_office(self):
        """13_Excel_Office: matrix section M, and the Y rows about
        workbooks. The program records a workbook's properties and sheet
        count and extracts every sheet's cell text; it records nothing
        about hidden state, formulas, names, comments, links, tables,
        charts or custom properties. Each case makes that explicit."""
        from openpyxl import Workbook
        from openpyxl.chart import BarChart, Reference
        from openpyxl.comments import Comment
        from openpyxl.packaging.custom import StringProperty
        from openpyxl.workbook.defined_name import DefinedName
        from openpyxl.worksheet.table import Table, TableStyleInfo
        base = "13_Excel_Office"
        present = {"file_state.state": "present"}
        workbook = dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"})
        scope = dict(classification="SCOPE", construction="G")

        # M-001 -- the ordinary workbook already in Documents\.
        self.case("M-001", construction="G", expected=dict(workbook, marker_indexed=True), notes="Documents\\budget_model.xlsx")
        self.case("M-001")["paths"].append("Documents\\budget_model.xlsx")

        # M-002 / M-003 / Y-030 -- a hidden sheet and a very-hidden sheet, each carrying the marker.
        wb = Workbook()
        wb.active.title = "Visible"
        wb.active.append(["The visible sheet says nothing of note."])
        hidden = wb.create_sheet("Hidden")
        hidden.append(["The " + marker(base + "/hidden_sheets.xlsx") + " sits on a hidden sheet."])
        hidden.sheet_state = "hidden"
        very = wb.create_sheet("VeryHidden")
        very.append(["The " + marker(base + "/hidden_sheets.xlsx") + " sits on a very hidden sheet too."])
        very.sheet_state = "veryHidden"
        self.add(base + "/hidden_sheets.xlsx", xlsx_bytes(wb), age_days=80, note="FTS marker on a hidden and a very-hidden sheet", case="M-002")
        for cid, note in (("M-002", "the hidden sheet"), ("M-003", "the very-hidden sheet"),
                          ("Y-030", "both hidden sheets hold relevant content")):
            self.case(cid, expected=dict(workbook, marker_indexed=True, **{"analyzer.office.detail": {"ContentCount": "3"}}),
                      matrix_expects="Detect hidden state", **scope,
                      notes="%s: extraction reads every sheet whatever its state, so the marker is found and the sheet count "
                            "includes it; the hidden state itself is not recorded -- hidden content is extracted, by decision" % note)
            if cid != "M-002":
                self.case(cid)["paths"].append(base.replace("/", "\\") + "\\hidden_sheets.xlsx")

        # M-004 -- formulas: openpyxl writes them with no cached value; Excel caches results when it saves.
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["quantity", "unit price", "total"])
        ws.append([4, 2.5, "=A2*B2"])
        ws.append(["", "", '="formula "&"result marker"'])       # the marker exists only as a formula's result
        openpyxl_formulas = xlsx_bytes(wb)
        ws["C3"] = '="formula "&"cached marker"'                  # its own phrase, so the two files are told apart in the index
        for_excel = xlsx_bytes(wb)
        self.add(base + "/formulas_openpyxl.xlsx", openpyxl_formulas, age_days=81,
                 note="formulas with no cached values (never opened in Excel)", case="M-004")
        self.case("M-004", expected=dict(workbook, marker_indexed=False),
                  matrix_expects="Extract formula data separately from displayed value", **scope,
                  notes="a workbook written by a library and never opened in Excel has formulas and no cached values: extraction "
                        "reads values only (data_only), so the formula cells read as empty and the marker is not found. Real files "
                        "from generators (exports, scripts) look like this")
        excel = self._excel_made("formulas_excel.xlsx", for_excel, "formulas_openpyxl.xlsx")
        if excel is not None:
            self.add(base + "/formulas_excel.xlsx", excel, age_days=81, note="the same workbook saved by Excel: cached values", case="M-004b")
            self.case("M-004b", condition="Formulas, with cached values (saved by Excel)", construction="G",
                      expected=dict(workbook, marker_indexed=True),
                      notes="Excel opened the openpyxl workbook, recalculated and saved it (Research\\Samples\\excel_made\\); the "
                            "cached results are read and the marker is found. Formula text is still not recorded")

        # M-005 / Y-025 -- defined names, one of them reaching into another workbook.
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws.append(["The cells say nothing; the names do."])
        wb.defined_names["Marker"] = DefinedName("Marker", attr_text='"%s"' % marker(base + "/defined_names.xlsx"))
        wb.defined_names["Rate"] = DefinedName("Rate", attr_text="Data!$A$1")
        wb.defined_names["Elsewhere"] = DefinedName("Elsewhere", attr_text="'[source_rates.xlsx]Sheet1'!$A$1")
        self.add(base + "/defined_names.xlsx", xlsx_bytes(wb), age_days=82, note="three defined names, one pointing at another workbook", case="M-005")
        self.case("M-005", expected=dict(workbook, marker_indexed=False), matrix_expects="Extract/index if supported", **scope,
                  notes="defined names are not extracted: the marker, which exists only as a name's value, is not found")
        self.case("Y-025", expected=dict(workbook), matrix_expects="Detect relationship where supported", **scope,
                  notes="the name 'Elsewhere' refers to [source_rates.xlsx]; the relationship is not recorded")
        self.case("Y-025")["paths"].append(base.replace("/", "\\") + "\\defined_names.xlsx")

        # M-006 / M-007 -- a table and a chart.
        wb = Workbook()
        ws = wb.active
        ws.title = "Sales"
        ws.append(["region", "units"])
        for region, units in (("north", 12), ("south", 7), ("east", 9), ("west", 4)):
            ws.append([region, units])
        table = Table(displayName="Sales", ref="A1:B5")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=True)
        ws.add_table(table)
        chart = BarChart()
        chart.title = "Units by region"
        chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=5), titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=5))
        ws.add_chart(chart, "D2")
        self.add(base + "/table_and_chart.xlsx", xlsx_bytes(wb), age_days=83, note="a structured table and a bar chart", case="M-006")
        self.case("M-006", expected=workbook, matrix_expects="Extract structure if supported", **scope,
                  notes="the table's cells are extracted as cells; the table as a structure is not recorded")
        self.case("M-007", expected=workbook, matrix_expects="Detect/index", **scope, notes="the chart is not recorded")
        self.case("M-007")["paths"].append(base.replace("/", "\\") + "\\table_and_chart.xlsx")

        # M-009 -- a comment.
        wb = Workbook()
        ws = wb.active
        ws.title = "Data"
        ws["A1"] = "A cell with a comment on it."
        ws["A1"].comment = Comment("The " + marker(base + "/comments.xlsx") + " lives in this comment.", "p2_build_acceptance_corpus")
        self.add(base + "/comments.xlsx", xlsx_bytes(wb), age_days=84, note="marker in a cell comment", case="M-009")
        self.case("M-009", expected=dict(workbook, marker_indexed=False), matrix_expects="Extract/index", **scope,
                  notes="comments are not extracted: the marker in the comment is not found")

        # M-010 -- hyperlinks: the cell text is read, the destination is not.
        wb = Workbook()
        ws = wb.active
        ws.title = "Links"
        ws["A1"] = "the policy document"
        ws["A1"].hyperlink = "https://example.invalid/policies/hyperlink-target-marker.pdf"
        ws["A2"] = "the shared drive copy"
        ws["A2"].hyperlink = "file:///S:/Finance/hyperlink%20target%20marker.xlsx"
        self.add(base + "/hyperlinks.xlsx", xlsx_bytes(wb), age_days=85, note="two hyperlinks; the marker is in the destinations only", case="M-010")
        self.case("M-010", expected=dict(workbook, marker_indexed=False), matrix_expects="Extract/index destinations", **scope,
                  notes="the link text is extracted as cell text; the destinations are not")

        # M-015 -- custom document properties.
        wb = Workbook()
        wb.active.title = "Data"
        wb.active.append(["The cells say nothing; the properties do."])
        wb.custom_doc_props.append(StringProperty(name="Department", value="Finance"))
        wb.custom_doc_props.append(StringProperty(name="Note", value="The " + marker(base + "/custom_properties.xlsx") + " is a custom property."))
        self.add(base + "/custom_properties.xlsx", xlsx_bytes(wb), age_days=86, note="marker in a custom document property", case="M-015")
        self.case("M-015", expected=dict(workbook, marker_indexed=False), matrix_expects="Extract if supported", **scope,
                  notes="custom properties are not read: the analyzer records the core properties only")

        # N-002 -- the macro-enabled container with no macro in it (Tika's).
        self.case("N-002", construction="sample", expected=dict(present, **{"extracted_content.status": "extracted"}),
                  matrix_expects="Distinguish macro-enabled container from actual content", classification="SCOPE",
                  notes="Tika's .dotm, .xltm, .potm and .pptm carry no VBA project (olevba: no macros); the program does not "
                        "look, so it cannot tell them from the ones that do")
        self.case("N-002")["paths"].extend("Samples\\third_party\\apache_tika\\macros\\" + n
                                          for n in ("testDOTM.dotm", "testEXCEL_macro_enabled_template.xltm", "testPPT.potm", "testPPT.pptm"))

    def _excel_made(self, name: str, source: bytes, source_name: str, password: str | None = None) -> bytes | None:
        """A file only Excel can make, made once and kept in
        Research\\Samples\\excel_made\\ (so later builds are byte-identical
        and need no Excel). None when it is not there and Excel is not."""
        if self.samples is None:
            return None
        made = self.samples / "excel_made" / name
        if made.is_file():
            return made.read_bytes()
        if not excel_available():
            self.skipped.append("%s: Excel is not available to make it" % name)
            return None
        work = self.samples / "excel_made" / "excel_work"
        work.mkdir(parents=True, exist_ok=True)
        src = work / source_name
        src.write_bytes(source)
        try:
            data = excel_saved(src, name, password)
        except Exception as exc:                                       # noqa: BLE001
            self.skipped.append("%s: Excel refused (%s)" % (name, str(exc)[:120]))
            return None
        finally:
            shutil.rmtree(work, ignore_errors=True)
        made.write_bytes(data)
        return data

    def _protection(self):
        """15_Protection: matrix section O -- passwords to open (built here,
        by Excel; and the samples), structure and sheet protection."""
        base = "15_Protection"
        present = {"file_state.state": "present"}
        from openpyxl import Workbook
        from openpyxl.workbook.protection import WorkbookProtection

        # O-001 -- a workbook Excel saved with a password to open, from a workbook this builder wrote.
        wb = Workbook()
        wb.active.title = "Secret"
        wb.active.append(["Nothing reads this without the password, and nothing tries."])
        plain = xlsx_bytes(wb)
        made = self._excel_made("password_to_open.xlsx", plain, "plain_for_password.xlsx", password="corpus")
        if made is not None:
            self.add(base + "/password_to_open.xlsx", made, age_days=87, expect_analyzer_failure=True,
                     note="Excel-encrypted workbook; the password is 'corpus' and nothing supplies it", case="O-001")
            self.case("O-001", construction="G/sample",
                      notes="password_to_open.xlsx: made by Excel from a workbook this builder wrote (SaveAs Password:=), kept "
                            "in Research\\Samples\\excel_made\\; the password is 'corpus' and nothing in the program supplies one")

        # O-002 -- structure protection; and the sheet-protected sample is O-003.
        wb = Workbook()
        wb.active.title = "Locked structure"
        wb.active.append(["Sheets cannot be added or removed without the password; reading is not protected."])
        wb.security = WorkbookProtection(workbookPassword="corpus", lockStructure=True)
        self.add(base + "/structure_protected.xlsx", xlsx_bytes(wb), age_days=88, note="workbook structure protection", case="O-002")
        self.case("O-002", construction="G", expected=dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"}),
                  notes="protection of editing is not protection of reading: analyzed and extracted like any workbook")

    def _templates(self):
        """16_Templates: matrix section P, and the Word rows of Y. A
        document's template is a relationship in settings.xml; the program
        reads the document and never the relationship."""
        base = "16_Templates"
        present = {"file_state.state": "present"}
        document = dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"})
        scope = dict(classification="SCOPE", construction="G")
        root = plain_path(ext_path(self.root))

        # P-001 / P-002 -- styles: the ordinary document, and one with a custom style.
        self.case("P-001", construction="G", expected=document, notes="Documents\\quarterly_report.docx, ordinary styles")
        self.case("P-001")["paths"].append("Documents\\quarterly_report.docx")
        from docx import Document
        from docx.enum.style import WD_STYLE_TYPE
        doc = Document()
        style = doc.styles.add_style("Corporate Body", WD_STYLE_TYPE.PARAGRAPH)
        style.base_style = doc.styles["Normal"]
        style.font.name = "Georgia"
        doc.add_paragraph("A paragraph in a custom style named Corporate Body.", style=style)
        doc.add_paragraph("A paragraph in Normal.")
        stamp = NOW.replace(tzinfo=None)
        doc.core_properties.created = doc.core_properties.modified = stamp
        buf = io.BytesIO()
        doc.save(buf)
        self.add(base + "/custom_style.docx", normalize_ooxml(buf.getvalue()), age_days=89, note="a custom paragraph style", case="P-002")
        self.case("P-002", expected=document, matrix_expects="Detect/index styles where supported", **scope,
                  notes="the text is read; the style names are not recorded")

        # P-003 / P-004 / P-005 / P-007 / Y-032 / Y-033 -- documents attached to templates.
        self.add(base + "/templates/corporate.dotx", make_dotx(["The corporate template. Its body is boilerplate."]), age_days=90,
                 note="a Word template (.dotx), the target of based_on_dotx.docx")
        template_uri = "file:///" + (root + "\\" + base.replace("/", "\\") + "\\templates\\corporate.dotx").replace("\\", "/").replace(" ", "%20")
        self.add(base + "/based_on_dotx.docx", attach_template(make_docx(["A document based on corporate.dotx, which exists."]), template_uri),
                 age_days=90, note="settings.xml attachedTemplate -> templates\\corporate.dotx", case="P-003")
        self.case("P-003", expected=document, matrix_expects="Detect template relationship if observable", **scope,
                  notes="the document and its template are both inventoried; the attachedTemplate relationship is not recorded")
        self.add(base + "/based_on_missing.docx", attach_template(make_docx(["A document based on a template that is not there."]),
                                                                  "file:///C:/Templates/retired_2019.dotx"),
                 age_days=91, note="attachedTemplate -> a path that does not exist", case="P-005")
        self.case("P-005", expected=document, matrix_expects="Document remains inventoryable; dependency reported", **scope,
                  notes="inventoried and read like any document; the missing template is not reported because the relationship is not read")
        dotm = "Samples\\third_party\\apache_tika\\macros\\testDOTM.dotm"
        dotm_uri = "file:///" + (root + "\\" + dotm).replace("\\", "/")
        self.add(base + "/based_on_dotm.docx", attach_template(make_docx(["A document based on a macro-enabled template."]), dotm_uri),
                 age_days=92, note="attachedTemplate -> Tika's testDOTM.dotm", case="P-004")
        for cid, note in (("P-004", "the template is macro-enabled (.dotm)"), ("P-007", "the template could carry macros; none runs"),
                          ("Y-033", "a document/template relationship that could carry automation")):
            self.case(cid, expected=document, matrix_expects="Detect template/macro relationship", **scope,
                      notes=note + "; the relationship is not recorded and nothing executes")
            if cid != "P-004":
                self.case(cid)["paths"].append(base.replace("/", "\\") + "\\based_on_dotm.docx")
        self.case("Y-032", construction="sample", expected=dict(present, **{"extracted_content.status": "extracted"}),
                  matrix_expects="Detect macro-capable template", classification="SCOPE",
                  notes="Tika's testDOTM.dotm: a macro-enabled template (with no macro in it); read as a document, its nature not recorded")
        self.case("Y-032")["paths"].append(dotm)

        # Y-047 -- the misleading names of 07_File_Types; Y-048 / Y-050 -- document metadata against filesystem metadata.
        self.case("Y-047", construction="G", expected=present,
                  notes="G-008 and G-009: the filename is preserved and the bytes decide what is read (also A-021)")
        self.case("Y-047")["paths"].extend(self.case("A-021")["paths"])
        doc = Document()
        doc.add_paragraph("A document whose own metadata disagrees with the filesystem's.")
        doc.core_properties.author = "Priya Example"
        doc.core_properties.last_modified_by = "Finance Department"
        doc.core_properties.created = datetime(2015, 3, 4, 9, 0, 0)
        doc.core_properties.modified = datetime(2016, 5, 6, 10, 0, 0)
        doc.core_properties.category = "Board papers"
        doc.core_properties.keywords = "confidential; personnel"
        buf = io.BytesIO()
        doc.save(buf)
        self.add(base + "/metadata_contradicts.docx", normalize_ooxml(buf.getvalue(), keep_dates=True), age_days=93,
                 created=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
                 note="core.xml says created 2015, modified 2016, author Priya; the filesystem says 2026", case="Y-048")
        self.case("Y-048", construction="G",
                  expected=dict(document, created_utc="2026-01-02T12:00:00Z",
                                **{"analyzer.office.detail": {"Author": "Priya Example", "Created": "2015-03-04 09:00:00+00:00"}}),
                  notes="both are preserved: the document's dates and author in the analyzer's detail, the filesystem's in file_state")
        self.case("Y-050", construction="G", expected=document, classification="SCOPE",
                  matrix_expects="Extraction policy must distinguish content from potentially sensitive metadata",
                  notes="author, category and keywords are metadata columns, not extracted text; the two live in different tables, "
                        "which is the distinction the matrix asks for. No policy beyond that exists")
        self.case("Y-050")["paths"].append(base.replace("/", "\\") + "\\metadata_contradicts.docx")

        # Y-049 -- hidden text, and tracked changes: both read.
        doc = Document()
        run = doc.add_paragraph().add_run("Visible text, then a hidden run: ")
        hidden = doc.add_paragraph().add_run("the " + marker(base + "/hidden_text.docx") + " is formatted hidden.")
        hidden.font.hidden = True
        stamp = NOW.replace(tzinfo=None)
        doc.core_properties.created = doc.core_properties.modified = stamp
        buf = io.BytesIO()
        doc.save(buf)
        self.add(base + "/hidden_text.docx", normalize_ooxml(buf.getvalue()), age_days=94, note="marker in a hidden run (w:vanish)", case="Y-049")
        tracked = make_docx(["A paragraph with a tracked deletion after it."])
        document_xml = zipfile.ZipFile(io.BytesIO(tracked)).read("word/document.xml")
        deletion = ('<w:p><w:del w:id="1" w:author="Reviewer" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>the %s was deleted with '
                    'track changes on</w:delText></w:r></w:del></w:p>' % marker(base + "/tracked_changes.docx")).encode("utf-8")
        document_xml = document_xml.replace(b"</w:body>", deletion + b"</w:body>", 1) if b"<w:sectPr" not in document_xml else \
            document_xml.replace(b"<w:sectPr", deletion + b"<w:sectPr", 1)
        self.add(base + "/tracked_changes.docx", rewrite_ooxml(tracked, {"word/document.xml": document_xml}), age_days=94,
                 note="marker in a tracked deletion (w:del/w:delText)", case="Y-049b")
        self.case("Y-049", construction="G", expected=dict(document, marker_indexed=True), classification="SCOPE",
                  matrix_expects="Extract according to defined scope; don't assume visible content is all",
                  notes="hidden runs (w:vanish) are read like any other run: the marker is found. The scope decision is 'what "
                        "the document's runs say', made explicit here")
        self.case("Y-049b", condition="Document contains deleted text under track changes", construction="G",
                  expected=dict(document, marker_indexed=False), classification="SCOPE",
                  matrix_expects="Extract according to defined scope",
                  notes="deleted text (w:delText) is not a run of the document and is not read: the marker is not found. "
                        "The other half of the Y-049 decision")

    def _document_relationships(self):
        """17_Document_Relationships: matrix section Q -- workbooks reaching
        into other workbooks. The relationship lives in
        xl/externalLinks; the program reads the cells and never the link."""
        base = "17_Document_Relationships"
        present = {"file_state.state": "present"}
        workbook = dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"})
        scope = dict(classification="SCOPE", construction="G")
        root = plain_path(ext_path(self.root))

        def uri(relpath):
            return "file:///" + (root + "\\" + relpath.replace("/", "\\")).replace("\\", "/").replace(" ", "%20")

        cells = [["local value", "=[1]Sheet1!A1"], ["the formula in B1 reads the other workbook", ""]]
        self.add(base + "/source_rates.xlsx", make_xlsx([["rate", 0.15], ["the workbook others refer to", ""]]), age_days=95,
                 note="the workbook the links point at", case="Q-001")
        self.add(base + "/refers_to_existing.xlsx", make_xlsx_external(cells, [(uri(base + "/source_rates.xlsx"), "0.15")]),
                 age_days=95, note="externalLink -> source_rates.xlsx (exists)", case="Q-001")
        self.case("Q-001", expected=workbook, matrix_expects="Record dependency", **scope,
                  notes="both workbooks are inventoried and read; the link between them is not recorded (also M-011)")
        self.case("M-011", expected=workbook, matrix_expects="Detect relationship", **scope, notes="the workbooks of Q-001")
        self.case("M-011")["paths"].extend(self.case("Q-001")["paths"])
        self.add(base + "/refers_to_missing.xlsx", make_xlsx_external(cells, [("file:///C:/Finance/2019/rates_old.xlsx", "0.12")]),
                 age_days=96, note="externalLink -> a workbook that does not exist", case="Q-002")
        for cid in ("Q-002", "Y-028"):
            self.case(cid, expected=workbook, matrix_expects="Record missing target, not scanner failure", **scope,
                      notes="the cached value is read like any cell; the broken link is not reported because no link is read. "
                            "The scan does not fail")
            if cid != "Q-002":
                self.case(cid)["paths"].append(base.replace("/", "\\") + "\\refers_to_missing.xlsx")
        self.add(base + "/moved/source_rates_moved.xlsx", make_xlsx([["rate", 0.15], ["moved here after the link was made", ""]]), age_days=97, case="Q-003")
        self.add(base + "/refers_to_moved.xlsx", make_xlsx_external(cells, [(uri(base + "/source_rates_moved.xlsx"), "0.15")]),
                 age_days=97, note="externalLink -> a path the target has since left", case="Q-003")
        self.case("Q-003", expected=workbook, matrix_expects="Relationship may be broken; report", **scope)
        self.add(base + "/refers_to_renamed.xlsx", make_xlsx_external(cells, [(uri(base + "/source_rates_v1.xlsx"), "0.15")]),
                 age_days=97, note="externalLink -> the target's old name (it is source_rates.xlsx now)", case="Q-004")
        self.case("Q-004", expected=workbook, matrix_expects="Report broken/stale reference", **scope)
        self.add(base + "/refers_to_cloud.xlsx",
                 make_xlsx_external(cells, [("https://example-my.sharepoint.invalid/personal/finance/Documents/rates.xlsx", "0.15")]),
                 age_days=98, note="externalLink -> a SharePoint address", case="Q-007")
        self.case("Q-007", expected=workbook, matrix_expects="Record external dependency", **scope, notes="nothing is fetched")
        for i in (1, 2, 3):
            self.add(base + "/shared_source/report_%d.xlsx" % i, make_xlsx_external(cells, [(uri(base + "/source_rates.xlsx"), "0.15")]),
                     age_days=99, note="one of three workbooks reading source_rates.xlsx", case="Q-008")
        self.case("Q-008", expected=dict(workbook, row_count=3), matrix_expects="Represent many-to-one relationship", **scope)
        chain = base + "/chain"
        self.add(chain + "/c.xlsx", make_xlsx([["the end of the chain", 1]]), age_days=100, case="Q-009")
        self.add(chain + "/b.xlsx", make_xlsx_external(cells, [(uri(chain + "/c.xlsx"), "1")]), age_days=100, case="Q-009")
        self.add(chain + "/a.xlsx", make_xlsx_external(cells, [(uri(chain + "/b.xlsx"), "1")]), age_days=100, case="Q-009")
        self.case("Q-009", expected=dict(workbook, row_count=3), matrix_expects="Traverse/represent without uncontrolled recursion", **scope,
                  notes="a -> b -> c; nothing follows a link, so nothing can recurse")
        cycle = base + "/cycle"
        self.add(cycle + "/x.xlsx", make_xlsx_external(cells, [(uri(cycle + "/y.xlsx"), "1")]), age_days=101, case="Q-010")
        self.add(cycle + "/y.xlsx", make_xlsx_external(cells, [(uri(cycle + "/x.xlsx"), "1")]), age_days=101, case="Q-010")
        self.case("Q-010", expected=dict(workbook, row_count=2), matrix_expects="Detect cycle; never hang", **scope,
                  notes="x -> y -> x; the run completes because no link is followed")

        # M-012 / Y-029 -- a DDE link (Tika's), a data connection with no local source.
        dde = "Samples\\third_party\\apache_tika\\templates_links\\testDdeLink.xlsx"
        dde_expected = dict(present, **{"analyzer.office.status": "error", "extracted_content.status": "extracted"})
        for cid, note in (("M-012", "a DDE data connection"), ("Y-029", "a data connection whose source is not a local file")):
            self.case(cid, construction="sample", expected=dde_expected, classification="SCOPE", matrix_expects="Detect external dependency",
                      notes="Tika's testDdeLink.xlsx: " + note + "; not recorded, never activated. openpyxl cannot open it (a DDE link has "
                            "no relationship part, and the library insists on one), so the analyzer errors; the extractor reads the "
                            "cells from the XML and finds the text")
            self.case(cid)["paths"].append(dde)
        # Y-034 -- external-link behaviour that would need the user's say-so: the OPF documents of Q-006, and Tika's INCLUDETEXT field.
        self.case("Y-034", construction="sample", expected=dict(present, **{"extracted_content.status": "extracted"}), classification="SCOPE",
                  matrix_expects="Record relationship; don't activate it",
                  notes="OPF's externalLink.doc/.pdf and Tika's testInstrLink.docx (a field that would pull in another file): read as "
                        "documents, nothing activated, the relationship not recorded")
        self.case("Y-034")["paths"].extend(self.case("Q-006")["paths"] + ["Samples\\third_party\\apache_tika\\templates_links\\testInstrLink.docx"])

    def _embedded_objects(self):
        """18_Embedded_Objects: matrix section R, and Y-031/Y-055 -- real
        containers from Tika (Excel's OLEObjects.Add refused every file
        under automation here, and Word automation was not approved), and
        the archive-versus-standalone duplicate."""
        present = {"file_state.state": "present"}
        document = dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"})
        scope = dict(classification="SCOPE", construction="sample", matrix_expects="Detect containment")
        tika = "Samples\\third_party\\apache_tika\\embedded\\"
        self.case("R-001", expected=document, **scope,
                  notes="Tika's testWORD_embeded.docx: an Excel worksheet, a PowerPoint deck and a Word 97-2003 document under "
                        "word/embeddings/; the carrier is read, the embedded objects are not listed or read")
        self.case("R-001")["paths"].append(tika + "testWORD_embeded.docx")
        self.case("R-002", expected=document, **scope,
                  notes="Tika's testExcel_embeddedPDF.xlsx (xl/embeddings/oleObject1.bin holds the PDF) and its .xls: the "
                        "workbook is read, the PDF inside is not")
        self.case("R-002")["paths"].extend([tika + "testExcel_embeddedPDF.xlsx", tika + "testExcel_embeddedPDF.xls"])
        self.case("M-014", expected=document, **scope,
                  notes="Tika's testEXCEL_embeded.xlsx: two Word documents and a PowerPoint deck under xl/embeddings/")
        self.case("M-014")["paths"].append(tika + "testEXCEL_embeded.xlsx")
        self.case("R-005", expected=document, **dict(scope, matrix_expects="Outer document remains inventoryable"),
                  notes="Tika's test_recursive_embedded_npe.docx, an embedding that crashed Tika's parser: the outer document is "
                        "inventoried and read; nothing opens the embedding")
        self.case("R-005")["paths"].append(tika + "test_recursive_embedded_npe.docx")
        self.case("Y-031", expected=document, **dict(scope, matrix_expects="Detect embedded executable/document object without execution"),
                  notes="Tika's test_recursive_embedded.docx (a document inside a document inside a document), testWORD_embedded_pdf.docx, "
                        "testWORD_EMFAndAttachments.docx: read as documents; nothing inside is opened or executed")
        self.case("Y-031")["paths"].extend([tika + "test_recursive_embedded.docx", tika + "testWORD_embedded_pdf.docx",
                                           tika + "testWORD_EMFAndAttachments.docx"])
        self.case("R-003")["paths"].extend([tika + "testPPT_embeddedMP3.pptx"])
        self.case("R-003")["notes"] += "; Tika's testPPT_embeddedMP3.pptx (ppt/media/media1.mp3)"
        self.case("R-004")["paths"].append(tika + "test_embedded_zip.pptx")
        self.case("R-004")["notes"] += "; Tika's test_embedded_zip.pptx (a zip as an OLE package)"
        # Y-055 -- the same bytes standalone and inside an archive.
        base = "18_Embedded_Objects"
        standalone = b"The same memo, once on its own and once inside a zip.\n"
        self.add(base + "/memo.txt", standalone, age_days=102, case="Y-055")
        self.add(base + "/memo_archive.zip", make_zip([("memo.txt", standalone)]), age_days=102, case="Y-055")
        self.case("Y-055", construction="G", expected=dict(present, row_count=2, not_grouped=True), classification="SCOPE",
                  matrix_expects="Keep container relationship separate from filesystem duplicate",
                  notes="the standalone file and the archive are two rows with different hashes; the member inside the archive is "
                        "listed by the archive analyzer and is not a duplicate candidate. Kept separate by construction")

    def _automation_and_organisation(self):
        """20_Automation (T), 21_Corporate_Organization (U), 22_Security_Boundaries
        (V-002, V-006): scripts that reference other scripts, folder
        structures an organisation leaves behind, and files that name
        resources the program must never touch."""
        present = {"file_state.state": "present"}
        text = dict(present, **{"extracted_content.status": "extracted"})
        # T-002 / T-003 -- a batch file that calls a PowerShell script, which dot-sources another.
        auto = "20_Automation"
        self.add(auto + "/deploy.bat", ("@echo off\r\nrem %s\r\npowershell -NoProfile -ExecutionPolicy Bypass -File helper.ps1\r\n"
                                        "call cleanup.cmd\r\n" % marker(auto + "/deploy.bat")).encode("ascii"), age_days=103, case="T-003")
        self.add(auto + "/helper.ps1", b". .\\common.ps1\r\nWrite-Host 'helper: nothing here runs during an inventory'\r\n", age_days=103, case="T-002")
        self.add(auto + "/common.ps1", b"function Get-Nothing { 'nothing' }\r\n", age_days=103, case="T-002")
        self.add(auto + "/cleanup.cmd", b"@echo off\r\necho cleanup: never executed by the inventory\r\n", age_days=103)
        self.case("T-002", construction="G", expected=text, classification="SCOPE", matrix_expects="Detect/reference if applicable; never execute",
                  notes="scripts are read as source code, so the reference text ('common.ps1') is indexed; the dependency is not recorded and nothing runs")
        self.case("T-003", construction="G", expected=dict(text, marker_indexed=True), classification="SCOPE", matrix_expects="Record as related file",
                  notes="deploy.bat names helper.ps1 and cleanup.cmd; all are inventoried and read, none is run, the relation is not recorded")
        # U -- an organisation's folders: departments, personal folders, a former employee, a migration (whole and partial).
        org = "21_Corporate_Organization"
        for dept in ("Finance", "HR", "IT"):
            self.add(org + "/Departments/%s/%s_overview.txt" % (dept, dept.lower()), ("%s department overview\n" % dept).encode(), age_days=104, case="U-001")
        self.case("U-001", construction="G", expected=dict(present, row_count=3), notes="department folders: inventoried like any folders")
        for user in ("alice", "bob"):
            self.add(org + "/Users/%s/notes.txt" % user, ("%s's own notes\n" % user).encode(), age_days=105, case="U-003")
        self.case("U-003", construction="G", expected=dict(present, row_count=2), classification="SCOPE",
                  matrix_expects="Preserve ownership/path context",
                  notes="the path context is the row's path; NTFS ownership is not recorded")
        self.add(org + "/Archive/Former Staff/jdoe (left 2019)/handover.txt", b"handover notes from 2019\n", age_days=2600, case="U-004")
        self.case("U-004", construction="G", expected=present, notes="inventoried without assuming the files are obsolete; the age report will list it")
        finance = self.case("U-001")["paths"][0]
        migrated = b"Finance department overview\n"
        self.add(org + "/Migrated/Finance/finance_overview.txt", migrated, age_days=30, case="U-006")
        self.case("U-006", construction="G", expected=dict(present, duplicate_group_members=2),
                  notes="Migrated\\Finance is a copy of Departments\\Finance: the two are one duplicate group; the newer copy is the migration")
        self.case("U-006")["paths"].append(finance)
        self.add(org + "/Migrated/HR/hr_overview_part1.txt", b"HR overview, first half only\n", age_days=30, case="U-007")
        self.case("U-007", construction="G", expected=present, classification="SCOPE", matrix_expects="Report missing/inaccessible components",
                  notes="a migration that copied one of two files: nothing marks it partial, because nothing knows what the whole was")
        self.case("U-011", construction="sample", expected=present, notes="the WordPerfect samples: obsolete formats, inventoried normally")
        self.case("U-011")["paths"].extend(self.case("SAMPLE-WPD")["paths"] + self.case("SAMPLE-WPD-42")["paths"])
        # V-002 / V-006 -- resources behind authentication, and a file that holds a credential.
        sec = "22_Security_Boundaries"
        self.add(sec + "/intranet_login.url", b"[InternetShortcut]\r\nURL=https://intranet.example.invalid/login?next=/finance\r\n", age_days=106, case="V-002")
        self.case("V-002", construction="S", expected=dict(present, **{"analyzer.status": "none", "extracted_content.status": "none"}),
                  notes="a shortcut to a login page: inventoried as a file; nothing is fetched, no credential is asked for")
        self.add(sec + "/db_connection.ini", ("[database]\nserver=db.example.invalid\nuser=reporting\npassword=hunter2\n; %s\n"
                                              % marker(sec + "/db_connection.ini")).encode(), age_days=106, case="V-006")
        self.case("V-006", construction="S", expected=dict(text, marker_indexed=True), classification="SCOPE",
                  matrix_expects="Never discover/store credentials",
                  notes="a configuration file holding a password is extracted like any text and its words are indexed, the password "
                        "among them. The program does not look for credentials; it also does not look away from them")

    def _registrations(self):
        """Matrix rows that existing artefacts already embody, registered
        against them so the coverage accounting counts what is there."""
        present = {"file_state.state": "present"}

        def alias(cid, source_ids, *, notes, classification=None, matrix_expects=None, expected=None, construction="G"):
            paths = []
            for sid in source_ids:
                paths.extend(p for p in self.case(sid)["paths"] if p not in paths)
            self.case(cid, construction=construction, expected=expected or present, notes=notes,
                      classification=classification, matrix_expects=matrix_expects)["paths"].extend(paths)

        alias("D-001", ["M-001", "P-001"], notes="ordinary readable files, the normal case: Documents\\budget_model.xlsx and quarterly_report.docx",
              expected=dict(present, **{"analyzer.office.status": "analyzed", "extracted_content.status": "extracted"}))
        alias("W-001", ["F-006", "F-007", "F-010"], notes="NTFS-specific features in 06_Metadata: compression, sparse files, alternate data streams",
              classification="SCOPE", matrix_expects="Record according to platform")
        alias("W-004", ["A-012"], notes="the NFC/NFD pair of A-012: both names preserved as they are")
        alias("E-012", ["O-001", "O-001-LEGACY", "O-001-ODF", "E-011-PDF-OPEN"], construction="sample",
              expected=dict(present, **{"extracted_content.status": "error"}),
              notes="the encrypted samples: each is recorded as an extraction error that says password-protected; nothing tries a password")
        alias("V-003", ["E-012"], construction="sample", expected=dict(present, **{"extracted_content.status": "error"}),
              notes="the same encrypted files as E-012: protection recorded as the reason reading stopped, never bypassed")
        alias("N-003", ["N-001"], construction="sample", expected=dict(present, **{"extracted_content.status": ["extracted", "empty"]}),
              classification="SCOPE", matrix_expects="Detect/index where safe",
              notes="Tika's macro files carry VBA modules (olevba: Dirty, Embolden); the modules are not listed or indexed")
        for cid, what in (("N-010", "a macro that would act outside the document"), ("V-007", "a macro attempting an external action"),
                          ("T-001", "VBA automation")):
            alias(cid, ["N-001"], construction="sample",
                  expected=dict(present, **{"extracted_content.status": ["extracted", "empty"], "no_office_automation_in_program": True}),
                  notes=what + ": the program never starts Office or evaluates VBA (asserted by reading the program's own source for COM "
                               "automation), so a macro of any intent cannot run during an inventory. Tika's macros are harmless "
                               "(a comment, a bold toggle); the assertion is about the program, not the macro")

    def _parked(self):
        registered = set(self.cases)
        declined = {d["id"] for d in self.not_constructed}
        for ids, reason in self.PARKED:
            for case_id in ids:
                if case_id in self.matrix and case_id not in registered and case_id not in declined:
                    self.decline(case_id, reason)

    def _documents(self):
        """Extractable text carrying unique marker phrases for FTS."""
        self.add("Documents/quarterly_report.docx", make_docx([
            "Quarterly Report",
            f"The {marker('Documents/quarterly_report.docx')} was reviewed by the committee.",
            "No further action was recorded at this time.",
        ]), age_days=45, note="FTS marker: docx")

        self.add("Documents/budget_model.xlsx", make_xlsx([
            ["Category", "Planned", "Actual"],
            ["Infrastructure", 120000, 118400],
            [marker("Documents/budget_model.xlsx"), 5000, 5000],
        ]), age_days=120, note="FTS marker: xlsx")

        self.add("Documents/research_note.pdf",
                 make_pdf(marker("Documents/research_note.pdf")),
                 age_days=400, note="FTS marker: pdf")

        self.add("Documents/meeting_minutes.txt",
                 (f"Minutes of the meeting.\n"
                  f"Motion carried: {marker('Documents/meeting_minutes.txt')}.\n"
                  "Adjourned at 16:05.\n").encode("utf-8"),
                 age_days=15, note="FTS marker: txt")

        rows = [["sku", "description", "qty"]]
        rows += [[f"SKU-{i:04d}", f"component {i}", i * 3] for i in range(1, 40)]
        rows.append(["SKU-9999", marker("Mixed/inventory_extract.csv"), 1])
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        self.add("Mixed/inventory_extract.csv", buf.getvalue().encode("utf-8"),
                 age_days=200, note="FTS marker: csv")

        self.add("Documents/presentation_notes.txt",
                 b"Slide notes for the annual review.\nNothing confidential here.\n",
                 age_days=800)

        # The formats extraction gained on 2026-09-12. The three binaries are
        # genuine Office output (see p2_fixture_blobs.py); the rest are built.
        import p2_fixture_blobs
        self.add("Documents/legacy_memo.doc", p2_fixture_blobs.blob("legacy_memo.doc"),
                 age_days=2200, note="FTS marker: doc (Word 97-2003)")
        self.add("Documents/legacy_ledger.xls", p2_fixture_blobs.blob("legacy_ledger.xls"),
                 age_days=2300, note="FTS marker: xls (Excel 97-2003)")
        self.add("Documents/legacy_deck.ppt", p2_fixture_blobs.blob("legacy_deck.ppt"),
                 age_days=2400, note="FTS marker: ppt (PowerPoint 97-2003)")
        self.add("Documents/cover_letter.rtf", make_rtf([
            "Dear committee,",
            f"The {marker('Documents/cover_letter.rtf')} is enclosed for your review.",
        ]), age_days=700, note="FTS marker: rtf")
        self.add("Documents/saved_page.html", make_html("Saved page", [
            "A page saved from the browser.",
            f"It mentions the {marker('Documents/saved_page.html')} once.",
        ]), age_days=90, note="FTS marker: html")
        self.add("Documents/export.json", json.dumps({
            "export": "settings", "note": marker("Documents/export.json"), "items": [1, 2, 3],
        }, indent=2).encode("utf-8"), age_days=33, note="FTS marker: json")
        # A ".doc" that is RTF inside -- 947 of the real corpus's 1,263 were.
        self.add("Documents/misnamed_rtf.doc", make_rtf([
            f"Filed as a Word document, written as RTF: {marker('Documents/misnamed_rtf.doc')}.",
        ]), age_days=1500, note="FTS marker: RTF named .doc; read by its bytes")

        # The text-underneath formats added on 2026-09-13, and a file with no
        # extension at all.
        self.add("Documents/server.log", (
            "2026-01-04 08:00:01 INFO service started\n"
            f"2026-01-04 08:00:02 WARN {marker('Documents/server.log')}\n"
            "2026-01-04 08:00:03 INFO service stopped\n").encode("utf-8"),
            age_days=8, note="FTS marker: log")
        self.add("Documents/settings.xml", (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<settings>\n"
            f"  <note id=\"1\">{marker('Documents/settings.xml')}</note>\n</settings>\n").encode("utf-8"),
            age_days=9, note="FTS marker: xml (kept raw, element names included)")
        self.add("Documents/contact.vcf", (
            "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Example\r\n"
            f"ORG:{marker('Documents/contact.vcf')}\r\nEND:VCARD\r\n").encode("utf-8"),
            age_days=300, note="FTS marker: vCard")
        self.add("Documents/hearing.ics", (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            f"SUMMARY:{marker('Documents/hearing.ics')}\r\nDTSTART:20260115T140000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n").encode("utf-8"),
            age_days=7, note="FTS marker: iCalendar")
        self.add("Documents/README", (
            f"Read me first. {marker('Documents/README')}.\n").encode("utf-8"),
            age_days=20, note="FTS marker: no extension", case="A-019")
        self.case("A-019", construction="L", expected={
            "file_state.state": "present", "extension_key": "",
            "extracted_content.status": "extracted"},
            notes="both files: the README is read as text by its bytes; Edge/no_extension likewise")
        self.add("Documents/thumbnail", make_png(16, 16, (200, 30, 30)),
                 age_days=21, note="a picture with no extension, not a document: recorded as not processed")

        # Email: the marker is inside an attachment or a forwarded message,
        # so finding it proves the whole chain, not just the headers.
        forwarded = make_message("Forwarded note", "b@example.invalid", "a@example.invalid",
                                 "Tue, 02 Sep 2025 10:00:00 -0400",
                                 f"Original note: {marker('Mail/forwarded.eml')}.")
        self.add("Mail/agenda.eml", make_eml(
            "Agenda for Thursday", "Ann Example <ann@example.invalid>", "bob@example.invalid",
            "Wed, 03 Sep 2025 09:30:00 -0400", "The agenda is attached.",
            attachments=[("enclosure.txt", f"Enclosure: {marker('Mail/agenda.eml')}.\n".encode("utf-8"))]),
            age_days=375, note="FTS marker: eml, in its attachment")
        self.add("Mail/forwarded.eml", make_eml(
            "FW: note", "ann@example.invalid", "carol@example.invalid",
            "Wed, 03 Sep 2025 11:00:00 -0400", "See the forwarded message.", forwarded=forwarded),
            age_days=374, note="FTS marker: eml, in a forwarded message")
        first = make_message("First message", "ann@example.invalid", "bob@example.invalid",
                             "Mon, 01 Sep 2025 08:00:00 -0400", "Nothing to see in the first message.")
        second = make_message("Second message", "bob@example.invalid", "ann@example.invalid",
                              "Mon, 01 Sep 2025 09:00:00 -0400", f"Tally: {marker('Mail/archive.mbox')}.")
        self.add("Mail/archive.mbox", make_mbox([
            ("ann@example.invalid", "Mon Sep  1 08:00:00 2025", first),
            ("bob@example.invalid", "Mon Sep  1 09:00:00 2025", second)]),
            age_days=376, note="FTS marker: mbox, second of two messages")
        self.add("Mail/saved_page.mht", make_mht("Archived page", [
            "A page saved from the browser as a single file.",
            f"It says: {marker('Mail/saved_page.mht')}.",
        ]), age_days=50, note="FTS marker: mht")
        self.add("Mail/discovery_schedule.msg", p2_fixture_blobs.blob("discovery_schedule.msg"),
                 age_days=40, note="FTS marker: Outlook msg, in its attachment")

    def _more_documents(self):
        """OpenDocument, EPUB, and the Office Open XML variants."""
        import p2_fixture_blobs
        self.add("Documents/manual.odt", make_odt([
            "Manual, chapter one.", f"The {marker('Documents/manual.odt')} is described here."]),
            age_days=410, note="FTS marker: odt (built)")
        for name in ("ledger_open.ods", "deck_open.odp", "ledger_macro.xlsm", "ledger_template.xltx",
                     "ledger_macro_template.xltm", "deck_macro.pptm", "deck_template.potx",
                     "deck_macro_template.potm", "deck_show.ppsx", "deck_macro_show.ppsm"):
            self.add("Documents/" + name, p2_fixture_blobs.blob(name), age_days=95, note="FTS marker: genuine Office output")
        self.add("Documents/ledger_xml2003.xml", p2_fixture_blobs.blob("ledger_xml2003.xml"), age_days=96,
                 note="Excel 2003 XML spreadsheet (genuine)")
        self.add("Documents/novel.epub", make_epub("A Short Novel", [
            ("Chapter One", "It began quietly."),
            ("Chapter Two", f"By the {marker('Documents/novel.epub')} the weather had turned.")]),
            age_days=900, note="FTS marker: epub, second chapter")
        for ext, phrase in ((".docm", marker("Documents/memo_macro.docm")), (".dotx", marker("Documents/memo_template.dotx")),
                            (".dotm", marker("Documents/memo_macro_template.dotm"))):
            stem = {".docm": "memo_macro", ".dotx": "memo_template", ".dotm": "memo_macro_template"}[ext]
            self.add("Documents/%s%s" % (stem, ext), make_word_variant(["Memo variant.", f"Marker: {phrase}."], ext),
                     age_days=97, note="FTS marker: Word variant (content type rewritten from a docx)")

    def _code_and_config(self):
        """Source code, scripts, configuration, subtitles, markup -- text under other names."""
        self.add("Code/app.py", (
            "#!/usr/bin/env python3\n\"\"\"The %s lives in this docstring.\"\"\"\n\n"
            "def main():\n    return 42\n" % marker("Code/app.py")).encode("utf-8"), age_days=30, note="FTS marker: py")
        self.add("Code/settings.toml", ("[service]\nname = \"corpus\"\nnote = \"%s\"\n" % marker("Code/settings.toml")).encode("utf-8"),
                 age_days=31, note="FTS marker: toml")
        self.add("Code/captions.srt", ("1\n00:00:01,000 --> 00:00:03,000\n%s\n\n2\n00:00:04,000 --> 00:00:06,000\nSecond caption.\n"
                                       % marker("Code/captions.srt")).encode("utf-8"), age_days=32, note="FTS marker: srt")
        plain = {
            "Code/script.js": "function tally(a, b) { return a + b; }\n",
            "Code/module.ts": "export const tally = (a: number, b: number): number => a + b;\n",
            "Code/styles.css": "body { margin: 0; font-family: sans-serif; }\n",
            "Code/build.sh": "#!/bin/sh\nset -e\necho building\n",
            "Code/Deploy.ps1": "param([string]$Target)\nWrite-Host \"deploying to $Target\"\n",
            "Code/query.sql": "SELECT name, total FROM ledger WHERE total > 100 ORDER BY total DESC;\n",
            "Code/config.yml": "service:\n  name: corpus\n  replicas: 2\n",
            "Code/settings.ini": "[general]\nname=corpus\nverbose=1\n",
            "Code/app.cfg": "name = corpus\nmode = test\n",
            "Code/captions.vtt": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nA caption in WebVTT.\n",
            "Code/notes.rst": "Notes\n=====\n\nA reStructuredText file.\n",
            "Code/paper.tex": "\\documentclass{article}\n\\begin{document}\nA LaTeX source file.\n\\end{document}\n",
            "Code/main.c": "#include <stdio.h>\nint main(void) { puts(\"hello\"); return 0; }\n",
            "Code/Program.cs": "class Program { static void Main() { System.Console.WriteLine(\"hello\"); } }\n",
            "Code/Main.java": "public class Main { public static void main(String[] a) { System.out.println(\"hello\"); } }\n",
        }
        for i, (rel, body) in enumerate(sorted(plain.items())):
            self.add(rel, body.encode("utf-8"), age_days=40 + i)

    def _archives(self):
        """Documents inside archives, two levels deep."""
        memo = make_docx(["Nested memo.", f"The {marker('Archives/bundle.zip')} is inside a zip inside a zip."])
        inner = make_zip([("inner/memo.docx", memo), ("inner/readme.txt", b"inner readme\n")])
        self.add("Archives/bundle.zip", make_zip([("notes.txt", b"outer notes\n"), ("inner.zip", inner),
                                                  ("picture.png", make_png(8, 8, (1, 2, 3)))]),
                 age_days=120, note="FTS marker: docx inside a zip inside the zip", case="Y-012")
        self.case("Y-012", construction="G", expected={"file_state.state": "present", "analyzer.archive.status": "analyzed",
                                                        "extracted_content.status": "extracted", "marker_indexed": True},
                  notes="the marker lives in a .docx inside a zip inside this zip: two levels, the cap")
        seven = make_7z([("note.txt", ("A 7z note: %s.\n" % marker("Archives/bundle.7z")).encode("utf-8")),
                         ("memo.docx", make_docx(["Also inside the 7z."]))])
        if seven is not None:
            self.add("Archives/bundle.7z", seven, age_days=121, note="FTS marker: txt inside a 7z")
        else:
            self.skipped.append("Archives/bundle.7z: py7zr not installed")
        self.add("Archives/plain.zip", make_zip([("a.txt", b"a\n"), ("b.txt", b"b\n")]), age_days=122,
                 note="zip with plain members")

    def _known_duplicates(self):
        """Exact duplicates with a precomputed reclaimable-byte total."""
        # Group A: 3 identical text files -> 2 reclaimable copies (B-002: same
        # bytes under different names)
        payload_a = (b"The same paragraph repeated verbatim.\n" * 40)
        for i, rel in enumerate([
            "Duplicates/original_notes.txt",
            "Duplicates/copies/original_notes (copy).txt",
            "Duplicates/copies/original_notes - Copy (2).txt",
        ]):
            self.add(rel, payload_a, age_days=30 + i * 10, note="dup group A", case="B-002")
        self.case("B-002", construction="G", expected={"file_state.state": "present", "duplicate_group_members": 3,
                                                        "reclaimable_bytes": len(payload_a) * 2})

        # Group B: 2 identical binaries -> 1 reclaimable copy (B-001: same
        # bytes, same name, different folder)
        payload_b = random_bytes(SEED + 1, 64 * 1024)
        for i, rel in enumerate(["Duplicates/archive_blob.bin",
                                 "Duplicates/backup/archive_blob.bin"]):
            self.add(rel, payload_b, age_days=500 + i * 30, note="dup group B", case="B-001")
        self.case("B-001", construction="G", expected={"file_state.state": "present", "duplicate_group_members": 2,
                                                        "reclaimable_bytes": len(payload_b)})

        # Group C: 4 identical small config files -> 3 reclaimable copies
        payload_c = json.dumps({"setting": "value", "enabled": True}, indent=2).encode()
        for i in range(4):
            self.add(f"Duplicates/configs/node{i}/settings.json", payload_c,
                     age_days=90 + i, note="dup group C")

        # Near-miss: same size, different bytes. Must NOT group.
        self.add("Duplicates/decoy_same_size.bin", random_bytes(SEED + 2, 64 * 1024),
                 age_days=505, note="same size as group B, different content")

    def _images(self):
        red = make_png(64, 64, (200, 30, 30))
        self.add("Images/logo_red.png", red, age_days=250)
        self.add("Images/backup/logo_red.png", red, age_days=251, note="dup group D")
        self.add("Images/logo_blue.png", make_png(64, 64, (30, 30, 200)), age_days=250)
        self.add("Images/banner.jpg", self._jpeg(), age_days=1200)

    @staticmethod
    def _jpeg() -> bytes:
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (120, 80), (90, 140, 90)).save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def _every_image_format(self):
        """One picture in every format the image analyzer names."""
        from PIL import Image
        img = Image.new("RGB", (48, 32), (30, 120, 200))
        for ext, fmt, kw in ((".jpeg", "JPEG", {"quality": 80}), (".jfif", "JPEG", {"quality": 80}),
                             (".gif", "GIF", {}), (".bmp", "BMP", {}), (".tiff", "TIFF", {}), (".tif", "TIFF", {}),
                             (".webp", "WEBP", {}), (".ico", "ICO", {}), (".jp2", "JPEG2000", {})):
            try:
                self.add("Images/formats/swatch" + ext, image_bytes(img, fmt, **kw), age_days=200)
            except Exception as exc:                            # noqa: BLE001
                self.skipped.append("Images/formats/swatch%s: %s" % (ext, exc))
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
            for ext, fmt in ((".heic", "HEIF"), (".heif", "HEIF"), (".avif", "AVIF")):
                try:
                    self.add("Images/formats/swatch" + ext, image_bytes(img, fmt), age_days=201)
                except Exception as exc:                        # noqa: BLE001
                    self.skipped.append("Images/formats/swatch%s: %s" % (ext, exc))
        except ImportError:
            self.skipped.append("Images/formats/swatch.heic .heif .avif: pillow-heif not installed")

    def _scans(self):
        """Pages with no text layer: OCR's work, and the judgement of it."""
        from PIL import Image
        page1 = render_page(["SCANNED LETTER", "", "To whom it may concern,",
                             "The %s is hereby recorded." % marker("Scans/scan_letter.tif"),
                             "Yours faithfully, The Corpus Builder."])
        page2 = render_page(["Page two of the scanned letter.", "Nothing further."])
        buf = io.BytesIO()
        page1.save(buf, format="TIFF", save_all=True, append_images=[page2], dpi=(200, 200))
        self.add("Scans/scan_letter.tif", buf.getvalue(), age_days=700, note="FTS marker: two-page scan, OCR only")
        photo_page = render_page(["PHOTOGRAPHED PAGE", "", "The %s applies." % marker("Scans/scan_letter.jpg")])
        self.add("Scans/scan_letter.jpg", image_bytes(photo_page, "JPEG", quality=80, dpi=(200, 200)),
                 age_days=701, note="FTS marker: scan as JPEG, OCR only")
        faded = degrade(render_page(["FADED COPY", "", "The faded quorum affidavit is barely legible.",
                                     "Fourth-generation photocopy, tilted and blurred."]))
        self.add("Scans/scan_faded.png", image_bytes(faded, "PNG", dpi=(200, 200)), age_days=702,
                 note="OCR review expected: blurred, faint, tilted, speckled")
        only = render_page(["BRIEF, SCANNED", "", "The %s is submitted." % marker("Scans/scanned_only.pdf")])
        buf = io.BytesIO()
        only.save(buf, format="PDF", resolution=200.0)
        self.add("Scans/scanned_only.pdf", pin_pdf_dates(buf.getvalue()), age_days=703, note="FTS marker: image-only PDF, OCR only")
        bad = degrade(render_page(["BAD SCAN", "", "A page no reader should trust without a look."]))
        buf = io.BytesIO()
        bad.save(buf, format="PDF", resolution=200.0)
        self.add("Scans/scan_bad.pdf", pin_pdf_dates(buf.getvalue()), age_days=704, note="OCR review expected: image-only PDF, degraded")
        # A PDF with a real text page first and a scanned page second.
        mixed_text = make_pdf("Page one has a text layer.")
        scanned = render_page(["Page two is a scan.", "The %s is only here." % marker("Scans/scan_pages.pdf")])
        try:
            from pypdf import PdfReader, PdfWriter
            writer = PdfWriter()
            writer.append(PdfReader(io.BytesIO(mixed_text)))
            buf = io.BytesIO()
            scanned.save(buf, format="PDF", resolution=200.0)
            writer.append(PdfReader(io.BytesIO(buf.getvalue())))
            out = io.BytesIO()
            writer.write(out)
            self.add("Scans/scan_pages.pdf", pin_pdf_dates(out.getvalue()), age_days=705, note="FTS marker: text page + scanned page")
        except Exception as exc:                                # noqa: BLE001
            self.skipped.append("Scans/scan_pages.pdf: %s" % exc)
        gradient = Image.new("RGB", (640, 480))
        px = gradient.load()
        for x in range(640):
            for y in range(480):
                px[x, y] = (x * 255 // 639, y * 255 // 479, 128)
        self.add("Scans/holiday_photo.jpg", image_bytes(gradient, "JPEG", quality=85), age_days=706,
                 note="a photograph, not a document: extraction records it as not processed")

    def _raw(self):
        """Camera RAW: every TIFF-based format the RAW analyzer names, as a
        TIFF with EXIF; the others need real camera files."""
        for i, ext in enumerate((".dng", ".cr2", ".nef", ".arw", ".pef", ".srw", ".rwl", ".3fr", ".kdc",
                                 ".erf", ".iiq", ".nrw", ".sr2", ".srf")):
            self.add("Raw/shot" + ext, make_raw_tiff("CorpusCam", "Model " + ext[1:].upper(),
                                                     "2024:05:%02d 08:00:00" % (i + 1)), age_days=500 + i)
        if not (self.samples and (self.samples / "third_party" / "raw_pixls_us").is_dir()):
            for ext in (".orf", ".rw2", ".raf", ".x3f", ".cr3", ".raw", ".mrw"):
                self.skipped.append("Raw/shot%s: not TIFF-based; needs a real camera file (Samples/third_party/raw_pixls_us)" % ext)

    def _media(self):
        """One audio file and one video file in every format the analyzers name."""
        tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
        audio = {".wav": [], ".mp3": ["-c:a", "libmp3lame", "-b:a", "64k"], ".flac": ["-c:a", "flac"],
                 ".m4a": ["-c:a", "aac", "-b:a", "64k"], ".aac": ["-c:a", "aac", "-b:a", "64k", "-f", "adts"],
                 ".ogg": ["-c:a", "libvorbis"], ".wma": ["-c:a", "wmav2", "-b:a", "64k"],
                 ".opus": ["-c:a", "libopus", "-b:a", "48k"], ".aiff": ["-c:a", "pcm_s16be"]}
        for ext, codec in audio.items():
            data = ffmpeg_bytes(tone + codec, ext)
            if data is None:
                self.skipped.append("Media/tone%s: ffmpeg not on PATH or refused the format" % ext)
            else:
                self.add("Media/tone" + ext, data, age_days=150)
        pattern = ["-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10"]
        video = {".mp4": ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ".mkv": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
                 ".avi": ["-c:v", "mpeg4"], ".mov": ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
                 ".wmv": ["-c:v", "wmv2"], ".flv": ["-c:v", "flv"], ".webm": ["-c:v", "libvpx"],
                 ".m4v": ["-c:v", "libx264", "-pix_fmt", "yuv420p"], ".mpg": ["-c:v", "mpeg2video"],
                 ".mpeg": ["-c:v", "mpeg2video"], ".3gp": ["-c:v", "h263", "-s", "176x144"]}
        for ext, codec in video.items():
            data = ffmpeg_bytes(pattern + codec, ext)
            if data is None:
                self.skipped.append("Media/clip%s: ffmpeg not on PATH or refused the format" % ext)
            else:
                self.add("Media/clip" + ext, data, age_days=151)

    def _samples(self):
        """Third-party files the builder cannot make: copied in when present."""
        import p2_fixture_blobs
        self.add("Mail/tika_mailbox.pst", p2_fixture_blobs.blob("tika_testPST_variousBodyTypes.pst"), age_days=3000,
                 note="FTS marker: Apache Tika's test PST (Apache-2.0), four messages")
        if self.samples and self.samples.is_dir():
            for path in sorted(self.samples.iterdir()):
                if path.suffix.lower() in (".wpd", ".one", ".pst", ".orf", ".rw2", ".raf", ".x3f", ".cr3", ".raw", ".mrw") and path.is_file():
                    self.add("Samples/" + path.name, path.read_bytes(), age_days=2000, note="third-party sample from " + str(self.samples))
            self._third_party()
        else:
            self.skipped.append("Samples/: no samples folder (WordPerfect, OneNote and non-TIFF RAW need real files)")

    def _third_party(self):
        """Research\\Samples\\third_party\\<source>\\...: real files from named
        sources (PROVENANCE.json beside them carries origin, commit, licence
        and SHA-256 per file), copied in whole. The matrix conditions they
        embody are registered by folder and by name, one case per outcome,
        so every case asserts one thing about every path it lists; a
        source's READMEs and licence files stay behind -- they are
        provenance, not test material."""
        root = self.samples / "third_party"
        if not root.is_dir():
            return
        skip_names = {"readme.md", "license", "license.txt", "license.md", "provenance.json"}
        present = {"file_state.state": "present"}
        read = dict(present, **{"extracted_content.status": "extracted"})
        read_or_blank = dict(present, **{"extracted_content.status": ["extracted", "empty"]})
        unread = dict(present, **{"extracted_content.status": "error"})
        registered: dict[str, dict] = {}     # case id -> kwargs for self.case()

        def note_case(case_id, relative, **kwargs):
            registered.setdefault(case_id, kwargs)
            self.case(case_id)["paths"].append(relative)

        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name.lower() in skip_names:
                continue
            rel = path.relative_to(root)
            source, inner = rel.parts[0], "/".join(rel.parts[1:])
            relpath = "Samples/third_party/%s/%s" % (source, inner)
            self.add(relpath, path.read_bytes(), age_days=1500 + int(hashlib.sha256(str(rel).encode()).hexdigest()[:8], 16) % 900,
                     note="third-party sample: %s (see Research\\Samples\\third_party\\PROVENANCE.json)" % source)
            relative = relpath.replace("/", "\\")
            name, ext = path.name.lower(), path.suffix.lower()
            tika = source == "apache_tika"
            opf = source == "openpreserve_format-corpus"
            # -- the readers that had never met a real file (handoff 2.4)
            if tika and inner.startswith("wordperfect/") and ext == ".doc":
                note_case("SAMPLE-WPD-42", relative, condition="A WordPerfect 4.2 document named .doc (Tika)", construction="sample",
                          expected=dict(unread, **{"analyzer.office.status": "error"}), classification="SCOPE",
                          matrix_expects="the text of the document",
                          notes="WordPerfect 4.2 wrote no signature; named .doc the bytes are neither OLE2 nor RTF and the sniff "
                                "calls them binary. Recognising 4.2 by its control codes alone is not attempted")
            elif tika and inner.startswith("wordperfect/"):
                note_case("SAMPLE-WPD", relative, condition="WordPerfect documents the reader had never met (Tika)", construction="sample",
                          expected=read,
                          notes="testWordPerfect.wpd (WP 6+) and two WP 5.x .wp files. The first real files found two reader "
                                "defects (2026-09-13): 0xF0-0xFF read as variable groups stepped off the end of the WP6 file "
                                "after eight characters, and the 5.x attribute codes read as two bytes swallowed the first "
                                "letter of every bold word; .wp was not a named extension. All three fixed against these files")
            elif tika and inner.startswith("onenote/") and "fuzz" in name:
                note_case("SAMPLE-ONE-FUZZ", relative, condition="Fuzzed OneNote files (Tika): malformed parser input", construction="sample",
                          expected=read, notes="Y-024 for the OneNote reader: the text scan reads the printable runs that survive")
            elif tika and inner.startswith("onenote/"):
                note_case("SAMPLE-ONE", relative, condition="OneNote sections the reader had never met (Tika)", construction="sample",
                          expected=read, notes="2007 and 2016 formats, Office 365 exports, one with an embedded Word document, one in Chinese")
            elif source == "raw_pixls_us":
                note_case("SAMPLE-RAW", relative, condition="Camera RAW formats the analyzer had never met (raw.pixls.us, CC0)",
                          construction="sample",
                          expected=dict(present, **{"analyzer.raw_image.status": "analyzed",
                                                    "analyzer.raw_image.has_fields": ["CameraMake", "CameraModel", "DateTimeOriginal"]}),
                          notes=".orf .rw2 .raf .x3f .cr3 .mrw .raw -- one camera each. Against these files (2026-09-15) exifread "
                                "recognised only the TIFF- and JPEG-based three; RAF, MRW, CR3 and X3F wrap their EXIF in a "
                                "container it does not know and came back 'analyzed' with every field empty. The analyzer now "
                                "lifts the embedded TIFF or JPEG out of those four containers; all seven name their camera")
            # -- macros (matrix N), protection (O), and the PDF horrors
            elif tika and inner.startswith("macros/"):
                note_case("N-001", relative, construction="sample", expected=read_or_blank,
                          notes="Tika's macro-enabled Word, Excel, PowerPoint and ODF documents: read as documents, the VBA "
                                "or Basic never touched (the two ODF files carry only a macro and no text, so they read empty). "
                                "VBA presence is not recorded -- Phase 3, per the matrix")
            elif tika and name == "testaccess2_encrypted.accdb":
                note_case("O-001-ACCDB", relative, condition="Encrypted Access database (Tika)", construction="sample",
                          expected=dict(present, **{"analyzer.status": "none", "extracted_content.status": "none"}),
                          classification="SCOPE", matrix_expects="recognised as an encrypted database",
                          notes="nothing reads an Access database, encrypted or not; inventoried and left alone")
            elif tika and inner.startswith("protected/") and ext in (".xlsx", ".docx", ".pptx") and name != "protectedsheets.xlsx":
                note_case("O-001", relative, construction="sample",
                          expected=dict(unread, **{"analyzer.office.status": "error"}),
                          notes="password to open, Office Open XML: the bytes are an OLE2 container holding EncryptionInfo and "
                                "EncryptedPackage streams. Against these files (2026-09-15) the analyzer said 'not a zip file' "
                                "and the extractor 'OLE container without a document inside'; both now say password-protected. "
                                "No password is ever tried")
            elif tika and inner.startswith("protected/") and ext in (".xls", ".doc", ".ppt") and "drm" not in name:
                note_case("O-001-LEGACY", relative, condition="Password to open, Office 97-2003 (Tika)", construction="sample",
                          expected=dict(unread, **{"analyzer.office.status": "analyzed"}),
                          notes="the OLE2 summary streams stay readable, so the analyzer records title, author and dates; the "
                                "document stream is encrypted and the extractor says so. No password is ever tried")
            elif tika and name == "testodtencrypted.odt":
                note_case("O-001-ODF", relative, condition="Password to open, OpenDocument (Tika)", construction="sample",
                          expected=unread,
                          notes="content.xml is ciphertext described in the manifest; the extractor names it (it said "
                                "'not well-formed' before 2026-09-15). No password is ever tried")
            elif tika and name == "protectedsheets.xlsx":
                note_case("O-003", relative, construction="sample", expected=dict(read, **{"analyzer.office.status": "analyzed"}),
                          notes="sheet protection: editing is protected, reading is not")
            elif tika and "drm" in name:
                note_case("O-006", relative, construction="sample", expected=dict(read, **{"analyzer.office.status": "analyzed"}),
                          notes="a rights-managed Word 97-2003 document whose text is nonetheless in the clear; read like any other")
            elif opf and name == "encryption_openpassword.pdf" or source == "py-pdf_sample-files" and "password" in name:
                note_case("E-011-PDF-OPEN", relative, condition="PDFs needing a password to open (OPF, py-pdf)", construction="sample",
                          expected=dict(unread, **{"analyzer.pdf.status": "error"}),
                          notes="Against these files (2026-09-15) the extraction was recorded 'extracted' with no artifact and no "
                                "count: pdfminer raises an exception with an empty message, and an empty error text read as "
                                "success. The engine now records a message-less exception as the failure it is. No password is tried")
            elif opf and name.startswith("encryption_"):
                note_case("E-011-PDF-OWNER", relative, condition="PDFs with owner-password restrictions: no copy, no print, no text access (OPF)",
                          construction="sample", expected=dict(read, **{"analyzer.pdf.status": "analyzed"}),
                          notes="restrictions are flags for viewers; the bytes decrypt with the empty user password and are read. "
                                "The 'no text access' flag is not honoured -- a deliberate reading of a file the owner can open")
            elif opf and name == "javascript.pdf":
                note_case("Y-035", relative, construction="sample", expected=dict(read_or_blank, **{"analyzer.pdf.status": "analyzed"}),
                          notes="a PDF carrying JavaScript: read, never run (nothing in the program evaluates PDF actions)")
            elif opf and path.name in ("corruptionOneByteMissing.pdf", "veraPDFHiResWrongObjectID.pdf",
                                       "pdf-17-header18.pdf", "veraPDFHiResChangedHeight.pdf"):
                note_case("Y-024-PDF", relative, condition="Malformed PDFs (OPF): a byte missing, a wrong object id, a header at byte 18, a wrong height",
                          construction="sample", expected=dict(read_or_blank, **{"analyzer.pdf.status": "analyzed"}),
                          notes="pypdf and PDFium both tolerate these; the two veraPDF files are picture-only and read empty")
            elif opf and "attachment" in name:
                note_case("R-004", relative, construction="sample", expected=dict(read, **{"analyzer.status": "analyzed"}),
                          notes="PDFs and a Word file carrying file attachments: the carrier is read, the attachment is not "
                                "listed or opened -- scope, Phase 3")
            elif opf and "embedded_video" in name:
                note_case("R-003", relative, construction="sample", expected=dict(read, **{"analyzer.status": "analyzed"}),
                          notes="documents with embedded video: the text is read, the media is not recorded")
            elif opf and "externalLink" in path.name:
                note_case("Q-006", relative, construction="sample", expected=dict(read, **{"analyzer.status": "analyzed"}),
                          notes="documents with external links; never activated, not recorded")
            elif path.name == "unreadablemetadata.pdf":
                note_case("SAMPLE-PDF-META", relative, condition="A PDF whose metadata pypdf cannot read (py-pdf/sample-files)", construction="sample",
                          expected=dict(read, **{"analyzer.pdf.status": "error"}),
                          notes="the analyzer (pypdf) fails on /Pages; the extractor (PDFium) reads every word. Two readers, "
                                "two verdicts, both recorded")
            elif opf and path.name in ("text_only.doc", "webCapture.doc"):
                note_case("SAMPLE-DOC-OPF", relative, condition="Word 97-2003 documents (OPF)", construction="sample",
                          expected=dict(read, **{"analyzer.office.status": "analyzed"}))
            elif opf:
                note_case("SAMPLE-PDF-OPF", relative, condition="PDFs of many constructions (OPF): fonts not embedded, PDF/A, signed, JPEG 2000, web capture",
                          construction="sample", expected=dict(read_or_blank, **{"analyzer.pdf.status": "analyzed"}),
                          notes="balloon_a1b_jp2k.pdf and veraPDFHiRes.pdf are pictures and read empty")
            elif source == "mathiasbynens_small" and ext in (".mp4", ".webm", ".avi", ".wmv", ".wav"):
                note_case("SAMPLE-SMALLEST-MEDIA", relative, condition="The smallest valid media files ffprobe accepts (mathiasbynens/small, CC0)",
                          construction="sample", expected=dict(present, **{"analyzer.status": "analyzed"}))
            elif source == "mathiasbynens_small" and name in ("flashvideo.flv", "mp3.mp3", "gif.gif", "webp.webp", "pdf.pdf"):
                note_case("SAMPLE-SMALLEST-REFUSED", relative, condition="The smallest valid files the libraries refuse (mathiasbynens/small, CC0)",
                          construction="sample", expected=dict(present, **{"analyzer.status": "error"}), classification="SCOPE",
                          matrix_expects="analyzed",
                          notes="a 1x1 GIF with no image data, a WebP with no frame, an MP3 of one frame, an FLV of a header, a PDF "
                                "of one empty page: Pillow, ffprobe and pypdf refuse them; the inventory does not")
            elif source == "mathiasbynens_small":
                note_case("SAMPLE-SMALLEST", relative, condition="The smallest valid file of each format (mathiasbynens/small, CC0)",
                          construction="sample", expected=dict(present, **{"hash_status": ["size_unique", "unique_by_hash", "confirmed_duplicate"]}),
                          notes="formats the program names and had never met, and formats nothing names: every one is inventoried and hashed")
            elif source == "py-pdf_sample-files":
                note_case("SAMPLE-PDF", relative, condition="Real PDFs of many shapes (py-pdf/sample-files, CC-BY-SA-4.0)",
                          construction="sample", expected=dict(read_or_blank, **{"analyzer.pdf.status": "analyzed"}),
                          notes="PDF/A, Arabic, outlines, rotated and cropped pages, inline images, forms, overlays; the picture-only "
                                "ones (ImageMagick's, the grayscale and CMYK images) read empty")
            elif source == "jonasclaes_test-data" and ext in (".odt", ".pdf"):
                note_case("SAMPLE-ODT-PDF", relative, condition="The same short text as OpenDocument and as PDF, four languages (jonasclaes/test-data)",
                          construction="sample", expected=read)
            elif ext in (".mp4", ".webm"):
                note_case("SAMPLE-VIDEO", relative, condition="Short video clips (Big Buck Bunny; ffmpeg.wasm test data)",
                          construction="sample", expected=dict(present, **{"analyzer.video.status": "analyzed"}))
            elif ext == ".png":
                note_case("SAMPLE-PNG", relative, condition="Generated PNG gradients and a square (jonasclaes; ffmpeg.wasm)",
                          construction="sample", expected=dict(present, **{"analyzer.image.status": "analyzed",
                                                                            "extracted_content.status": "skipped"}),
                          notes="pictures that do not look like documents are skipped by the OCR gate, not failed")
            else:
                note_case("SAMPLE-MISC", relative, condition="Source, configuration and font files that came with the samples",
                          construction="sample", expected=present)
        for case_id, kwargs in registered.items():
            self.case(case_id, **kwargs)

    def _malformed(self):
        """Deliberate analyzer failures, so the quality reports have rows."""
        present = {"file_state.state": "present"}
        self.add("Malformed/truncated.pdf", b"%PDF-1.4\n1 0 obj\n<</Type/Catalog",
                 age_days=60, note="invalid PDF", expect_analyzer_failure=True, case="E-003")
        self.case("E-003", construction="G", expected=dict(present, **{"analyzer.pdf.status": "error", "extracted_content.status": "error"}))
        self.case("E-002", construction="G", expected=dict(present, **{"analyzer.status": "error"}),
                  notes="the truncated PDF and the truncated zip: the analyzer fails, the inventory does not")
        self.case("E-002")["paths"].append("Malformed\\truncated.pdf")
        self.add("Malformed/not_really.docx", b"This is plain text pretending to be a docx.",
                 age_days=61, note="invalid DOCX", expect_analyzer_failure=True, case="E-005")
        self.case("E-005", construction="G", expected=dict(present, **{"analyzer.office.status": "error"}),
                  notes="the fake .docx is plain text, so extraction reads it as text by its bytes; the truncated genuine .xlsx in 05_Corruption fails both")
        self.add("Malformed/broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                 age_days=62, note="invalid PNG", expect_analyzer_failure=True, case="E-004")
        self.case("E-004", construction="G", expected=dict(present, **{"analyzer.image.status": "error"}))
        self.add("Malformed/corrupt.gz", b"\x1f\x8b\x08\x00" + b"\xff" * 24,
                 age_days=63, note="invalid GZIP; no analyzer names .gz", case="E-006b")
        self.case("E-006b", construction="G", condition="Corrupt gzip (no analyzer names .gz)",
                  expected=dict(present, **{"analyzer.status": "none", "extracted_content.status": "none"}),
                  notes="nothing reads a .gz, so nothing fails; the file is inventoried and left alone")
        # Random bytes do not deflate, so 600 of ~4,100 bytes is a real
        # truncation. (4,000 letters deflated to a 130-byte zip that [:600]
        # did not cut at all -- 'truncated.zip' was a valid archive from
        # 2026-09-08 to 2026-09-13.)
        self.add("Malformed/truncated.zip",
                 make_zip([("a.bin", random_bytes(SEED + 5, 4000))])[:600],
                 age_days=64, note="truncated ZIP", expect_analyzer_failure=True, case="E-006")
        self.case("E-006", construction="G", expected=dict(present, **{"analyzer.archive.status": "error"}))
        self.case("E-002")["paths"].append("Malformed\\truncated.zip")
        self.add("Malformed/not_really.pst", b"!BDN" + bytes(self.rng.getrandbits(8) for _ in range(2000)),
                 age_days=65, note="PST signature over garbage", expect_analyzer_failure=True)
        self.add("Malformed/empty.msg", b"", age_days=66, note="zero-byte Outlook message", case="E-001")
        self.case("E-001", construction="L", expected=dict(present, size_bytes=0),
                  notes="Edge/empty.txt and Malformed/empty.msg; the report's 'Empty files found' counts them")
        # X-016: the corrupt Office file among valid ones -- the valid ones'
        # markers are all found (the FTS section), and this one fails alone.
        self.case("X-016", construction="G", condition="Corrupt Office file + valid files surrounding it",
                  expected=dict(present, **{"analyzer.office.status": "error"}),
                  notes="Malformed\\not_really.docx beside Documents\\; every valid document's marker is found by the FTS checks")
        self.case("X-016")["paths"].append("Malformed\\not_really.docx")

    def _edge_cases(self):
        self.add("Edge/empty.txt", b"", age_days=10, note="zero bytes", case="E-001")
        self.add("Edge/no_extension", b"payload without an extension\n", age_days=11, case="A-019")
        self.add("Edge/\u00fcnicode_n\u00e4me_\u65e5\u672c\u8a9e.txt",
                 "Unicode filename content.\n".encode("utf-8"), age_days=12,
                 note="non-ASCII filename", case="A-008")
        self.case("A-008", construction="L", expected={"file_state.state": "present",
                                                        "file_name": "\u00fcnicode_n\u00e4me_\u65e5\u672c\u8a9e.txt"})
        self.add("Edge/spaces and (parens) [brackets].txt", b"awkward name\n", age_days=13, case="A-005")
        self.case("A-005", construction="L", expected={"file_state.state": "present",
                                                        "file_name": "spaces and (parens) [brackets].txt"})
        self.add("Edge/" + "/".join(f"level{i}" for i in range(1, 9)) + "/deep.txt",
                 b"eight levels down\n", age_days=14, note="deep nesting")
        self.add("Edge/trailing.dot.multiple.suffixes.tar.gz",
                 make_gzip(b"compressed payload\n"), age_days=300)
        self.add("Edge/UPPERCASE.TXT", b"uppercase extension\n", age_days=16)

    # -- the matrix's categories -------------------------------------------

    def _naming(self):
        """01_Naming: matrix section A, the rows that are static and tool-safe.
        Reserved device names and trailing dots or spaces go to Hostile\\;
        reserved characters need the POSIX namespace and are declined."""
        present = {"file_state.state": "present"}

        # A-001 / A-002 / A-003 / A-004 -- long names, a long path, a deep tree.
        self.add("01_Naming/" + A001_NAME,
                 ("The %s sits under a 255-character name.\n" % marker("01_Naming/" + A001_NAME)).encode("utf-8"),
                 age_days=17, note="A-001: 255-character file name", case="A-001")
        self.case("A-001", construction="G", expected=dict(present, file_name_length=255, hash_status=["size_unique", "unique_by_hash"],
                                                           **{"extracted_content.status": "extracted", "marker_indexed": True}))
        self.add("01_Naming/" + A002_FOLDER + "/inside.txt", b"Inside a 255-character folder name.\n",
                 age_days=18, note="A-002: 255-character folder name", case="A-002")
        self.case("A-002", construction="G", expected=dict(present, depth=2, **{"extracted_content.status": "extracted"}))
        self.add(A003_TREE + "/" + A003_LEAF,
                 ("At the bottom of forty levels: %s.\n" % marker(A003_TREE + "/" + A003_LEAF)).encode("utf-8"),
                 age_days=19, note="A-003: complete path far beyond 260 characters", case="A-003")
        self.case("A-003", construction="G", expected=dict(present, path_length_gt=260, depth=42,
                                                           **{"extracted_content.status": "extracted", "marker_indexed": True}))
        self.add(A003_TREE + "/deep.txt",
                 ("The %s is here.\n" % marker(A003_TREE + "/deep.txt")).encode("utf-8"),
                 age_days=14, note="A-004: forty levels of nesting", case="A-004")
        self.case("A-004", construction="G", expected=dict(present, depth=42, **{"extracted_content.status": "extracted", "marker_indexed": True}),
                  notes="the walk keeps an explicit stack, so depth cannot overflow anything; the assertion is that every stage reaches the leaf")

        # A-006 -- leading and consecutive spaces, preserved exactly.
        self.add("01_Naming/ leading space.txt", b"a name that begins with a space\n", age_days=20, case="A-006")
        self.add("01_Naming/three   spaces.txt", b"three spaces inside the name\n", age_days=21, case="A-006")
        self.case("A-006", construction="L", expected=present,
                  notes="file_name is compared byte for byte by the totals-by-extension and file-count checks; the names are ' leading space.txt' and 'three   spaces.txt'")

        # A-009 / A-010 / A-011 -- accented, non-Latin, emoji.
        self.add("01_Naming/Résumé Ångström.txt", "accented, composed (NFC)\n".encode("utf-8"), age_days=22, case="A-009")
        self.case("A-009", construction="L", expected=dict(present, file_name="Résumé Ångström.txt"))
        for name, body in (("отчёт.txt", "Cyrillic"), ("تقرير.txt", "Arabic, right to left"),
                           ("रिपोर्ट.txt", "Devanagari, with combining vowel signs"), ("รายงาน.txt", "Thai")):
            self.add("01_Naming/scripts/" + name, ("%s script in the name\n" % body).encode("utf-8"), age_days=23, case="A-010")
        self.case("A-010", construction="L", expected=dict(present, row_count=4, **{"extracted_content.status": "extracted"}))
        self.add("01_Naming/emoji 🧪 test.txt", "one emoji, a surrogate pair in UTF-16\n".encode("utf-8"), age_days=24, case="A-011")
        self.add("01_Naming/family 👨‍👩‍👧.txt", "a ZWJ sequence: three emoji joined\n".encode("utf-8"), age_days=25, case="A-011")
        self.case("A-011", construction="L", expected=dict(present, row_count=2),
                  notes="path_length counts UTF-16 units, so the surrogate pairs count two each; the truth's long-path total is computed the same way")

        # A-012 / Y-008 -- the same visible name in two Unicode forms: two files.
        nfc, nfd = "caf\u00e9.txt", "cafe\u0301.txt"          # U+00E9; e + U+0301
        assert nfc != nfd and len(nfc) == 8 and len(nfd) == 9
        self.add("01_Naming/normalisation/" + nfc, b"NFC: e-acute is one code point\n", age_days=26, case="A-012")
        self.add("01_Naming/normalisation/" + nfd, b"NFD: e plus combining acute\n", age_days=27, case="A-012")
        self.case("A-012", construction="G", expected=dict(present, row_count=2, not_grouped=True),
                  notes="NTFS does not normalise, so both entries exist; nothing may fold them into one row (also Y-008)")
        self.case("Y-008", construction="G", expected={"row_count": 2}, notes="the same two files as A-012")
        self.case("Y-008")["paths"].extend(self.case("A-012")["paths"])

        # A-013 -- visually identical, different scripts.
        self.add("01_Naming/lookalike/payroll.txt", b"Latin a throughout\n", age_days=28, case="A-013")
        self.add("01_Naming/lookalike/p\u0430yroll.txt", b"Cyrillic a in the second position\n", age_days=29, case="A-013")
        self.case("A-013", construction="G", expected=dict(present, row_count=2, not_grouped=True))

        # A-014 / Y-009 / Y-053 (case-only names in a case-sensitive directory)
        # live in Hostile\: the engine folds the pair into one row, which
        # changes the project's totals -- see HostileCorpus._h_naming.

        # A-018 -- legal here, illegal elsewhere: 120 CJK characters are 120 UTF-16
        # units (NTFS allows 255) and 360 bytes of UTF-8 (ext4 and APFS allow 255).
        cjk = "\u6587\u4ef6\u540d" * 40 + ".txt"
        assert len(cjk) == 124 and len(cjk.encode("utf-8")) == 364
        self.add("01_Naming/" + cjk, "a name too long in bytes for Linux and macOS, fine on NTFS\n".encode("utf-8"), age_days=30, case="A-018")
        self.case("A-018", construction="G", expected=dict(present, file_name_length=124),
                  notes="inventoried according to the filesystem it is on: 124 characters, 364 bytes of UTF-8; a copy to ext4 or APFS would fail")

        # A-015 -- punctuation-only differences, and (B-002) the same bytes four times.
        same = b"The same bytes under four names that differ only in punctuation.\n"
        for name in ("a.b.txt", "a-b.txt", "a_b.txt", "a b.txt"):
            self.add("01_Naming/punctuation/" + name, same, age_days=32, case="A-015")
        self.case("A-015", construction="L", expected=dict(present, row_count=4, duplicate_group_members=4))

        # A-017 -- reserved characters need the POSIX namespace (WSL); not here.
        self.decline("A-017", "characters like * ? \" < > | cannot be created through Win32, even with \\\\?\\; "
                              "the POSIX namespace (WSL) would be needed")

        # A-020 -- a PDF named .exe. The name is kept whole; extraction is
        # chosen by extension, so the marker is expected NOT to be indexed.
        self.add("01_Naming/invoice.pdf.exe", make_pdf(marker("01_Naming/invoice.pdf.exe")), age_days=33, case="A-020")
        self.case("A-020", construction="G", classification="SCOPE", expected=dict(present, extension_key=".exe", **{
            "analyzer.status": "none", "extracted_content.status": "none", "marker_indexed": False}),
            matrix_expects="Preserve complete filename; don't assume final extension is type",
            notes="the name is preserved and nothing executes; extraction selects by extension and .exe is not selected -- the bytes decide only how a selected file is read",
            safety=["never executed; a PDF under an executable's name is inert to every reader"])

    def _duplicates_identity(self):
        """02_Duplicates: matrix section B, plus Y-051. Byte identity is the
        only identity Phase 2 claims; everything else (near-duplicates,
        versions, authority) is later and only the data is laid out here.
        Hard links are identity questions and live in Hostile\\."""
        present = {"file_state.state": "present"}

        # B-003 -- the same bytes under three extensions: one group of three,
        # and by_extension counts each extension once.
        same = b"Identical bytes, three extensions: the extension is a label, not the content.\n" * 6
        for ext in (".txt", ".log", ".dat"):
            self.add("02_Duplicates/same_bytes" + ext, same, age_days=34, case="B-003")
        self.case("B-003", construction="G", expected=dict(present, row_count=3, duplicate_group_members=3,
                                                           reclaimable_bytes=len(same) * 2))

        # B-004 -- the same bytes, modified ten years apart: grouped all the same.
        stale = b"Timestamps are not identity. These bytes were written twice, a decade apart.\n" * 4
        self.add("02_Duplicates/dated/recent.txt", stale, age_days=5, case="B-004")
        self.add("02_Duplicates/dated/decade_old.txt", stale, age_days=5 + 10 * 365, case="B-004")
        self.case("B-004", construction="G", expected=dict(present, duplicate_group_members=2),
                  notes="the age report counts one recent and one old file; the duplicate report one group")

        # B-005 -- a folder tree and its copy: five groups of two.
        for i in range(5):
            body = ("Tree file %d.\n" % i).encode("utf-8") * (i + 3)
            self.add("02_Duplicates/Tree/sub/file_%d.txt" % i, body, age_days=60, case="B-005")
            self.add("02_Duplicates/Tree - Copy/sub/file_%d.txt" % i, body, age_days=61, case="B-005")
        self.case("B-005", construction="G", expected=dict(present, row_count=10, duplicate_group_members=2),
                  notes="the folder-scope filter on either tree shows five files")

        # B-006 -- the same name, different bytes, two folders: not grouped.
        self.add("02_Duplicates/finance/Budget.xlsx", make_xlsx([["Line", "Amount"], ["Rent", 1200]]), age_days=70, case="B-006")
        self.add("02_Duplicates/finance_old/Budget.xlsx", make_xlsx([["Line", "Amount"], ["Rent", 1150]]), age_days=400, case="B-006")
        self.case("B-006", construction="G", expected=dict(present, row_count=2, not_grouped=True))

        # B-009 -- identical text, different document author: different bytes,
        # so not grouped; the Office analyzer reads two authors.
        text = ["Metadata-only variation.", "The words are the same in both files."]
        self.add("02_Duplicates/metadata/memo_by_ann.docx", make_docx(text, author="Ann Example"), age_days=80, case="B-009")
        self.add("02_Duplicates/metadata/memo_by_bob.docx", make_docx(text, author="Bob Example"), age_days=81, case="B-009")
        self.case("B-009", construction="G", expected=dict(present, row_count=2, not_grouped=True, **{"analyzer.office.status": "analyzed"}),
                  notes="Phase 2 identity is byte identity; the same words under two authors are two files")

        # B-010 / B-011 -- the version chain and the FINAL naming chaos. Laid
        # out for the later phases; here only names are preserved and the
        # three byte-identical members group, nothing is chosen.
        chain = ["Budget", "Budget_v2", "Budget_v3", "Budget_FINAL", "Budget_FINAL2", "Budget_FINAL_REVISED",
                 "Budget_FINAL_USE_THIS", "Budget_FINAL_USE_THIS_ONE"]
        final = make_xlsx([["Line", "Amount"], ["Rent", 1200], ["Power", 310]])
        for i, stem in enumerate(chain):
            data = final if stem in ("Budget_FINAL", "Budget_FINAL2", "Budget_FINAL_USE_THIS_ONE") \
                else make_xlsx([["Line", "Amount"], ["Rent", 1200 + i], ["Power", 300 + i]])
            self.add("02_Duplicates/versions/%s.xlsx" % stem, data, age_days=200 - i * 7, case="B-011")
        self.case("B-011", construction="G", expected=dict(present, row_count=8),
                  notes="B-010 too: eight names, three of them byte-identical (FINAL, FINAL2, FINAL_USE_THIS_ONE); no authority is chosen")
        self.case("B-010", construction="G", expected={"duplicate_group_members": 3, "reclaimable_bytes": len(final) * 2},
                  notes="the three identical members of the version chain")
        self.case("B-010")["paths"].append("02_Duplicates\\versions\\Budget_FINAL.xlsx")

        # Y-051 -- the same bytes, one copy hidden and read-only: grouped.
        twin = b"Attributes are not identity either.\n" * 8
        self.add("02_Duplicates/attributes/plain_copy.txt", twin, age_days=90)
        self.add("02_Duplicates/attributes/hidden_readonly_copy.txt", twin, age_days=91, case="Y-051",
                 attributes=FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_READONLY)
        self.case("Y-051", construction="G", expected=dict(present, duplicate_group_members=2,
                                                           attributes_has=["Hidden", "ReadOnly"]))

    def _links(self):
        """03_Links, the tool-safe half: Windows shortcuts. A .lnk is an
        ordinary file to this scanner -- nothing resolves it, so chains and
        cycles cannot hang anything, and the target is not recorded (the
        relationship layer is Phase 3). Junctions and symbolic links, which
        the walk must recognise and refuse, live in Hostile\\."""
        base = "03_Links"
        root = plain_path(ext_path(self.root))
        lnk = {"file_state.state": "present", "extension_key": ".lnk", "analyzer.status": "none",
               "extracted_content.status": "none"}
        scope = dict(classification="SCOPE", construction="G",
                     notes="the .lnk is inventoried as a file; its target is not recorded -- the relationship layer is Phase 3")

        def shortcut(relpath, target_rel, is_dir=False):
            absolute = root + "\\" + target_rel.replace("/", "\\")
            relative = ".\\" + os.path.relpath(target_rel, os.path.dirname(relpath)).replace("/", "\\")
            return make_lnk(absolute, relative, is_dir)

        self.add(base + "/targets/document.txt", b"The document a shortcut points at.\n", age_days=35)
        self.add(base + "/targets/folder/inside.txt", b"Inside the folder a shortcut points at.\n", age_days=35)

        # C-001 / C-002 / C-004 -- to a file, to a folder, to nothing.
        self.add(base + "/to_document.lnk", shortcut(base + "/to_document.lnk", base + "/targets/document.txt"), age_days=36, case="C-001")
        self.case("C-001", expected=lnk, **scope)
        self.add(base + "/to_folder.lnk", shortcut(base + "/to_folder.lnk", base + "/targets/folder", is_dir=True), age_days=36, case="C-002")
        self.case("C-002", expected=lnk, **scope)
        self.add(base + "/to_missing.lnk", shortcut(base + "/to_missing.lnk", base + "/targets/deleted_long_ago.txt"), age_days=36, case="C-004")
        self.case("C-004", expected=lnk, **scope)

        # C-003 / C-008 / C-009 / C-010 / X-003 -- a chain, a self-reference, a
        # long chain, a cycle. The assertion is that the run completes: no
        # reader opens a shortcut, so none can follow one.
        for i in (1, 2, 3):
            target = base + ("/chain/hop_%d.lnk" % (i + 1) if i < 3 else "/targets/document.txt")
            self.add(base + "/chain/hop_%d.lnk" % i, shortcut(base + "/chain/hop_%d.lnk" % i, target), age_days=37, case="C-003")
        self.case("C-003", expected=dict(lnk, row_count=3), construction="G",
                  notes="hop_1 -> hop_2 -> hop_3 -> document.txt")
        self.add(base + "/self.lnk", shortcut(base + "/self.lnk", base + "/self.lnk"), age_days=38, case="C-008")
        self.case("C-008", expected=lnk, construction="G", notes="points at itself; nothing here follows a shortcut, so nothing can loop")
        for i in range(1, 51):
            target = base + ("/long_chain/hop_%02d.lnk" % (i + 1) if i < 50 else "/targets/document.txt")
            self.add(base + "/long_chain/hop_%02d.lnk" % i, shortcut(base + "/long_chain/hop_%02d.lnk" % i, target), age_days=39, case="C-009")
        self.case("C-009", expected=dict(lnk, row_count=50), construction="G", notes="fifty hops to document.txt")
        for a, b in (("a", "b"), ("b", "c"), ("c", "a")):
            self.add(base + "/cycle/cycle_%s.lnk" % a, shortcut(base + "/cycle/cycle_%s.lnk" % a, base + "/cycle/cycle_%s.lnk" % b), age_days=40, case="C-010")
        self.case("C-010", expected=dict(lnk, row_count=3), construction="G", notes="a -> b -> c -> a")
        # C-005 / C-006 / C-007 -- targets off this volume: a share, a removable drive, a cloud address.
        self.add(base + "/to_share.lnk", make_lnk("\\\\fileserver.example.invalid\\finance\\ledger.xlsx", "", False), age_days=41, case="C-005")
        self.case("C-005", expected=lnk, **dict(scope, notes="a UNC target; nothing resolves it, so no network is touched"))
        self.add(base + "/to_removable.lnk", make_lnk("E:\\Backups\\2019\\photos", "", True), age_days=41, case="C-006")
        self.case("C-006", expected=lnk, **dict(scope, notes="a drive letter that is not mounted; nothing resolves it"))
        self.add(base + "/to_cloud.lnk", make_lnk("C:\\Users\\tom\\OneDrive\\Shared\\policy.docx", "", False), age_days=41, case="C-007")
        self.case("C-007", expected=lnk, **dict(scope, notes="a OneDrive path; nothing resolves it, so nothing could hydrate (rule 2)"))
        self.case("X-003", expected=dict(lnk, row_count=57), construction="G", condition="Shortcut -> shortcut -> circular link",
                  notes="the chain, the self-reference, the long chain and the cycle together; the run completes")
        for cid in ("C-003", "C-008", "C-009", "C-010"):
            self.case("X-003")["paths"].extend(self.case(cid)["paths"])

        # H-004 / H-005 -- a folder holding only shortcuts; only broken ones.
        for i in range(3):
            self.add(base + "/only_shortcuts/s%d.lnk" % i, shortcut(base + "/only_shortcuts/s%d.lnk" % i, base + "/targets/document.txt"), age_days=41, case="H-004")
            self.add(base + "/only_broken/b%d.lnk" % i, shortcut(base + "/only_broken/b%d.lnk" % i, base + "/targets/gone_%d.txt" % i), age_days=42, case="H-005")
        self.case("H-004", expected=dict(lnk, row_count=3), construction="G", notes="the folder is not empty: three rows")
        self.case("H-005", expected=dict(lnk, row_count=3), construction="G", notes="broken or not, a shortcut is a file; three rows")

    def _corruption(self):
        """05_Corruption: matrix section E and the container hazards of
        section 12.3 (Y-012..Y-017). The rule under test is the matrix's
        section 13: a failed parser is a successful inventory with a failed
        extraction, never a missing file -- and nothing an archive says about
        itself reaches the disk under its own name."""
        base = "05_Corruption"
        present = {"file_state.state": "present"}
        temp = os.environ.get("TEMP", os.environ.get("TMP", ""))
        canaries = [r"C:\escape.txt", r"C:\FOTest\escape.txt", plain_path(ext_path(self.root.parent)) + r"\escape.txt",
                    temp + r"\escape.txt", os.path.dirname(temp) + r"\escape.txt"]

        # E-005 (genuine) -- a real workbook cut off at 60 %.
        whole = make_xlsx([["Line", "Amount"], [marker(base + "/truncated_at_60pct.xlsx"), 1]])
        self.add(base + "/truncated_at_60pct.xlsx", whole[: len(whole) * 6 // 10], age_days=43,
                 expect_analyzer_failure=True, case="E-005")
        self.case("E-005")["expected"].update({"analyzer.office.status": "error"})
        self.case("Y-024", construction="G", condition="Malformed document parser input",
                  expected=dict(present, **{"analyzer.office.status": "error", "extracted_content.status": "error", "marker_indexed": False}),
                  notes="the truncated genuine workbook: both readers fail, the file stays in the inventory, the rest of the corpus is untouched (X-016)")
        self.case("Y-024")["paths"].append((base + "/truncated_at_60pct.xlsx").replace("/", "\\"))

        # E-007 -- a zip whose second member has a bad CRC (stored, one byte flipped).
        good = ("Member one is fine: %s.\n" % marker(base + "/crc_member.zip")).encode("utf-8")
        bad = ("Member two is damaged: %s.\n" % marker(base + "/crc_member.zip!bad_member")).encode("utf-8")
        archive = bytearray(make_zip_raw([("good.txt", good), ("bad.txt", bad)]))
        at = archive.find(bad)
        archive[at + 4] ^= 0x20                                   # one byte of member two, CRC now wrong
        self.add(base + "/crc_member.zip", bytes(archive), age_days=44, case="E-007")
        self.case("E-007", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "extracted_content.status": "extracted", "marker_indexed": True,
                                                                        "archive_members_include": ["good.txt", "bad.txt"]}),
                  notes="member one's marker is found, member two's is not: the outer archive is analysed and read, the bad member is reported and skipped")

        # E-008 -- invalid internal metadata: a docx whose core.xml dates are
        # not dates; a PDF whose /Info dates are garbage.
        docx = make_docx(["Dates in this document's properties are not dates.", "Body text is fine."])
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(docx)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                data = src.read(info)
                if info.filename == "docProps/core.xml":
                    import re
                    data = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*", rb"\1not-a-date", data)
                dst.writestr(info, data)
        self.add(base + "/bad_dates.docx", normalize_ooxml(buf.getvalue()), age_days=45, case="E-008")
        pdf = make_pdf("Info dictionary with garbage dates").replace(
            b"trailer\n<</Size", b"trailer\n<</Info<</CreationDate(garbage)/ModDate(D:99999999999999)>>/Size")
        self.add(base + "/bad_info.pdf", pdf, age_days=46, case="E-008b")
        self.case("E-008", construction="G", expected=dict(present, **{"extracted_content.status": "extracted", "analyzer.office.status": "analyzed"}),
                  notes="python-docx shrugs at the dates: analysed, text read")
        self.case("E-008b", construction="G", condition="Invalid internal metadata (PDF /Info dates)",
                  expected=dict(present, **{"extracted_content.status": "extracted", "analyzer.pdf.status": "analyzed"}),
                  matrix_expects="Graceful parser failure",
                  notes="pypdf's date conversion raises on '/CreationDate (garbage)'; until B7.2 the whole PDF analysis was an error though the "
                        "page is fine. Since B7.2 (defect 49) the garbage date empties one field ('garbage (unparseable)') and the file is analysed")

        # E-009 -- a partial download, under its download name and under the real one.
        partial = make_xlsx([["Line", "Amount"], ["Rent", 1200]])[:900]
        self.add(base + "/partial/report.xlsx.crdownload", partial, age_days=47, case="E-009")
        self.add(base + "/partial/report_partial.xlsx", partial, age_days=47, expect_analyzer_failure=True, case="E-009")
        self.case("E-009", construction="G", expected=present,
                  notes=".crdownload: no analyzer names it (no rows); .xlsx: the Office analyzer and extraction fail")
        self.case("E-009b", construction="G", condition="Partially downloaded file under its real name",
                  expected={"analyzer.office.status": "error", "extracted_content.status": "error"})
        self.case("E-009b")["paths"].append((base + "/partial/report_partial.xlsx").replace("/", "\\"))
        self.case("E-009c", construction="G", condition="Partially downloaded file under its download name",
                  expected={"analyzer.status": "none", "extracted_content.status": "none", "extension_key": ".crdownload"})
        self.case("E-009c")["paths"].append((base + "/partial/report.xlsx.crdownload").replace("/", "\\"))

        # E-010 -- the extension says one thing, the bytes another.
        self.add(base + "/misnamed/picture.jpg", make_png(24, 24, (10, 200, 10)), age_days=48, case="E-010")
        self.add(base + "/misnamed/document.txt", make_pdf(marker(base + "/misnamed/document.txt")), age_days=48, case="E-010")
        self.add(base + "/misnamed/archive.zip", bytes(self.rng.getrandbits(8) for _ in range(3000)), age_days=48,
                 expect_analyzer_failure=True, case="E-010")
        self.case("E-010", construction="G", expected=present,
                  notes="PNG bytes as .jpg: the image analyzer reads by content; PDF bytes as .txt: extraction routes by bytes and finds the marker; "
                        "random bytes as .zip: the archive analyzer fails")
        self.case("E-010b", construction="G", condition="PDF bytes named .txt", expected={"extracted_content.status": "extracted", "marker_indexed": True})
        self.case("E-010b")["paths"].append((base + "/misnamed/document.txt").replace("/", "\\"))
        self.case("E-010c", construction="G", condition="Random bytes named .zip", expected={"analyzer.archive.status": "error"})
        self.case("E-010c")["paths"].append((base + "/misnamed/archive.zip").replace("/", "\\"))

        # Y-013 -- five nested zips; the readers stop at depth two.
        nested = None
        for level in (5, 4, 3, 2, 1):
            key = base + "/nested/level1.zip" + ("" if level == 2 else "!level%d" % level)
            note = ("Level %d: %s.\n" % (level, marker(key) if level > 1 else "no marker at the top")).encode("utf-8")
            members = [("note_%d.txt" % level, note)]
            if nested is not None:
                members.append(("level%d.zip" % (level + 1), nested))
            nested = make_zip(members)
        self.add(base + "/nested/level1.zip", nested, age_days=49, case="Y-013")
        self.case("Y-013", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "extracted_content.status": "extracted", "marker_indexed": True}),
                  notes="the level-2 marker is found; 3, 4 and 5 are not (MAX_ARCHIVE_DEPTH = 2)")

        # Y-014 -- expansion: ten 60 MB members (600 MB, the 512 MB cap); one
        # 100 MB member (the 64 MB cap) beside a small readable note.
        sixty = make_pdf("Sixty megabytes of padding follow this line.", padding=60 * 1024 * 1024)
        self.add(base + "/expansion/ten_by_60mb.zip", make_zip([("block_%02d.pdf" % i, sixty) for i in range(10)], level=1),
                 age_days=50, case="Y-014")
        self.case("Y-014", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "extracted_content.status": "extracted"}),
                  notes="ten 60 MB PDFs (600 MB) stored in under a megabyte; reading stops past the 512 MB total, one member in memory at a time; each PDF yields one line")
        self.add(base + "/expansion/big_member.zip",
                 make_zip([("giant.pdf", make_pdf("A hundred megabytes of padding follow.", padding=100 * 1024 * 1024)),
                           ("note.txt", ("Beside the giant: %s.\n" % marker(base + "/expansion/big_member.zip")).encode("utf-8"))], level=1),
                 age_days=51, case="Y-014")
        self.case("Y-014b", construction="G", condition="Archive member over the 64 MB member cap",
                  expected={"extracted_content.status": "extracted", "marker_indexed": True},
                  notes="the 100 MB member is skipped, the note beside it is read")
        self.case("Y-014b")["paths"].append((base + "/expansion/big_member.zip").replace("/", "\\"))

        # Y-015 -- path-traversal member names, in a zip and in a 7z.
        slip = [("../../../escape.txt", ("Dot-dot member: %s.\n" % marker(base + "/traversal/zip_slip.zip")).encode("utf-8")),
                ("..\\..\\escape.txt", b"Backslash dot-dot member.\n"),
                ("C:\\escape.txt", b"Absolute member.\n")]
        self.add(base + "/traversal/zip_slip.zip", make_zip_raw(slip), age_days=52, case="Y-015")
        self.case("Y-015", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "extracted_content.status": "extracted", "marker_indexed": True,
                                                                        "archive_members_include": [n.replace("\\", "/") for n, _ in slip], "canary_absent": canaries}),
                  safety=["zip members are read in memory and written only to a randomly named %TEMP% file; the member's name never reaches the disk"],
                  notes="the names are recorded as archive members (zipfile reads a backslash as a slash); the text is read; no escape.txt appears anywhere")
        seven = make_7z([("../../../escape.txt", ("Dot-dot 7z member: %s.\n" % marker(base + "/traversal/zip_slip.7z")).encode("utf-8")),
                         ("..\\..\\escape.txt", b"Backslash dot-dot 7z member.\n")], unchecked_names=True)
        if seven is not None:
            self.add(base + "/traversal/zip_slip.7z", seven, age_days=52, case="Y-015b")
            self.case("Y-015b", construction="G", condition="Archive containing path-traversal member names (7z)",
                      expected=dict(present, **{"analyzer.archive.status": "analyzed", "extracted_content.status": "error",
                                                "marker_indexed": False, "canary_absent": canaries}),
                      safety=["7z members are extracted by py7zr into a %TEMP% folder; it refuses the name ('Specified path is bad') before writing anything"],
                      notes="observed 2026-09-13: py7zr raises on the dot-dot member, so the whole archive is an extraction error and nothing is read; the names are listed; no escape.txt appears anywhere")
        else:
            self.skipped.append(base + "/traversal/zip_slip.7z: py7zr not installed")

        # Y-016 -- executable content inside an archive: listed, never extracted.
        self.add(base + "/executable/tools.zip", make_zip([("tool.com", TINY_PROGRAM), ("tool.exe", b"not a program, but named as one\n"),
                                                           ("readme.txt", b"A zip that carries programs.\n")]), age_days=53, case="Y-016")
        self.case("Y-016", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "archive_members_include": ["tool.com", "tool.exe", "readme.txt"],
                                                                        "extracted_content.status": "extracted"}),
                  safety=["tool.com is a DOS exit stub, inert on 64-bit Windows; .com and .exe are not readable suffixes, so neither member is ever written out"],
                  notes="the members are listed; only readme.txt is read")

        # Y-017 / O-005 / E-011 -- password-protected archives: three shapes.
        secret = ("The secret note: %s.\n" % marker(base + "/protected/zipcrypto.zip!note")).encode("utf-8")
        self.add(base + "/protected/zipcrypto.zip", make_zip_raw([("note.txt", secret), ("readme.txt", b"encrypted zip\n")], password="corpus"),
                 age_days=54, case="Y-017")
        self.case("Y-017", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "archive_members_include": ["note.txt", "readme.txt"],
                                                                        "extracted_content.status": ["extracted", "empty"]}),
                  safety=["no password is anywhere in the corpus or the truth; the members are reported '(encrypted; not read)'"],
                  notes="ZipCrypto: names are listed, contents are not read; also O-005 and E-011")
        for twin in ("O-005", "E-011"):
            self.case(twin, construction="G", expected={"analyzer.archive.status": "analyzed"}, notes="the same archives as Y-017")
            self.case(twin)["paths"].append((base + "/protected/zipcrypto.zip").replace("/", "\\"))
        seven_pw = make_7z([("note.txt", ("Seven secret: %s.\n" % marker(base + "/protected/sevenzip_password.7z!note")).encode("utf-8"))], password="corpus")
        seven_hdr = make_7z([("note.txt", ("Headers secret: %s.\n" % marker(base + "/protected/sevenzip_headers.7z!note")).encode("utf-8"))],
                            password="corpus", encrypt_header=True)
        if seven_pw is not None and seven_hdr is not None:
            self.add(base + "/protected/sevenzip_password.7z", seven_pw, age_days=55, case="Y-017b")
            self.case("Y-017b", construction="G", condition="Password-protected 7z, member names in clear",
                      expected=dict(present, **{"analyzer.archive.status": "analyzed", "archive_members_include": ["note.txt"],
                                                "extracted_content.status": "error"}),
                      notes="listed; the member cannot be read without the password, which nothing supplies -- py7zr raises, so extraction is an error")
            self.add(base + "/protected/sevenzip_headers.7z", seven_hdr, age_days=56, expect_analyzer_failure=True, case="Y-017c")
            self.case("Y-017c", construction="G", condition="Password-protected 7z with encrypted headers",
                      expected=dict(present, **{"analyzer.archive.status": "error", "extracted_content.status": "error"}),
                      notes="even the names are hidden: the analyzer reports the failure, the file stays in the inventory")
            self.case("X-017", construction="G", condition="Protected archive + normal archive",
                      expected={"file_state.state": "present"},
                      notes="the protected archives beside Archives\\plain.zip and bundle.zip, whose markers are still found")
            self.case("X-017")["paths"].extend([(base + "/protected/zipcrypto.zip").replace("/", "\\"), "Archives\\plain.zip"])

    def _metadata(self):
        """06_Metadata: matrix section F and D-010. Timestamps the program
        must carry as they are, attributes it must record, and the sizes a
        sparse or compressed file has on disk versus in the directory."""
        base = "06_Metadata"
        present = {"file_state.state": "present"}
        days = lambda y, m, d: (NOW - datetime(y, m, d, 12, 0, 0, tzinfo=timezone.utc)).days   # noqa: E731

        # F-001 / F-002 -- very old, and in the future.
        self.add(base + "/dates/from_1980.txt", b"modified on the FAT epoch, 1980-01-01\n", age_days=days(1980, 1, 1), case="F-001")
        self.add(base + "/dates/from_1970.txt", b"modified the day after the Unix epoch\n", age_days=days(1970, 1, 2), case="F-001")
        self.case("F-001", construction="G", expected=dict(present, **{"modified_utc": ["1980-01-01T12:00:00Z", "1970-01-02T12:00:00Z"]}),
                  notes="the age report counts them as the oldest files in the corpus")
        self.add(base + "/dates/from_2099.txt", b"modified on 2099-12-31, a clock set wrong\n", age_days=days(2099, 12, 31), case="F-002")
        self.case("F-002", construction="G", expected=dict(present, modified_utc="2099-12-31T12:00:00Z"),
                  notes="age_days is negative in the truth; the window must show it without error (camera clocks do this)")

        # F-003 -- created after modified.
        self.add(base + "/dates/created_after_modified.txt", b"created 2030, modified 2020\n", age_days=days(2020, 6, 1),
                 created=datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc), case="F-003")
        self.case("F-003", construction="G", expected=dict(present, modified_utc="2020-06-01T12:00:00Z", created_utc="2030-01-01T12:00:00Z"),
                  notes="both preserved as they are; nothing reconciles them")

        # F-004 -- fifty files sharing one second.
        for i in range(50):
            self.add(base + "/same_second/batch_%02d.txt" % i, ("Batch file %d, stamped with the others.\n" % i).encode("utf-8"),
                     age_days=days(2024, 3, 15), case="F-004")
        self.case("F-004", construction="G", expected=dict(present, row_count=50, not_grouped=True, modified_utc="2024-03-15T12:00:00Z"),
                  notes="identical timestamps are not identity: the contents differ and nothing groups them")

        # F-005 / F-006 / D-010 -- hidden, system, read-only.
        for i in range(3):
            self.add(base + "/attributes/hidden_%d.txt" % i, b"a hidden file\n", age_days=57, attributes=FILE_ATTRIBUTE_HIDDEN, case="F-005")
        self.case("F-005", construction="G", expected=dict(present, row_count=3, attributes_has=["Hidden"], **{"extracted_content.status": "extracted"}),
                  notes="hidden files are in scope: inventoried, hashed, read; the report's 'Hidden files' counts them")
        self.add(base + "/attributes/system.txt", b"a system-attributed file\n", age_days=58, attributes=FILE_ATTRIBUTE_SYSTEM, case="F-006")
        self.case("F-006", construction="G", expected=dict(present, attributes_has=["System"]))
        self.add(base + "/attributes/read_only.txt", b"a read-only file\n", age_days=59, attributes=FILE_ATTRIBUTE_READONLY, case="D-010")
        self.case("D-010", construction="G", expected=dict(present, attributes_has=["ReadOnly"], **{"extracted_content.status": "extracted"}),
                  notes="recorded, read, never written; the builder clears the attribute before deleting the tree")

        # F-007 -- NTFS-compressed: the directory says 1 MB, the disk holds less.
        self.add(base + "/sizes/compressed.txt", b"The same line, compressed by NTFS on disk.\n" * 24000, age_days=60, compress=True, case="F-007")
        self.case("F-007", construction="G", expected=dict(present, attributes_has=["Compressed"], allocated_lt_size=True))

        # F-009 -- sparse: 64 MB in the directory, 4 KB on disk.
        self.add(base + "/sizes/sparse_64mb.bin", b"S" * 4096, age_days=61, sparse_to=64 * 1024 * 1024, case="F-009")
        self.case("F-009", construction="G", expected=dict(present, size_bytes=64 * 1024 * 1024, attributes_has=["SparseFile"], allocated_lt_size=True,
                                                           hash_status=["size_unique", "unique_by_hash"]),
                  notes="hashing reads the 64 MB of zeros in well under a second; the 4 GB one is in Hostile")

        # F-010 -- an alternate data stream: invisible to the scanner, by policy.
        self.add(base + "/streams/report.txt", b"The main stream: what every reader sees.\n", age_days=62,
                 streams={"secret": b"An alternate data stream nobody enumerates.\n"}, case="F-010")
        self.case("F-010", construction="G", classification="SCOPE", expected=dict(present, size_bytes=41),
                  matrix_expects="Detect/document support policy",
                  notes="the row is the main stream; the named stream is not enumerated, counted or read -- alternate data streams are out of scope, written down here")

    def _file_types(self):
        """07_File_Types: matrix section G (and A-021). The rule: an extension
        is a label. Analyzers are chosen by it; extraction is chosen by it
        and then reads the bytes."""
        base = "07_File_Types"
        present = {"file_state.state": "present"}
        nothing = {"analyzer.status": "none", "extracted_content.status": "none"}

        self.add(base + "/unknown/data.zzq", b"an extension nothing names\n", age_days=63, case="G-001")
        self.case("G-001", construction="G", expected=dict(present, extension_key=".zzq", **nothing))
        self.add(base + "/unknown/ledger.corp", b"a corporate application's own format, holding text\n", age_days=64, case="G-003")
        self.case("G-003", construction="G", expected=dict(present, extension_key=".corp", **nothing),
                  notes="recorded without assuming meaning; extraction is chosen by extension, so the text inside is not read")

        # G-004 -- an Office owner file, exactly as Word and Excel leave them: the
        # owner's name in ANSI then in UTF-16, 162 bytes, under the document's name.
        owner = b"\x0cTom Example" + b"\x00" * 42 + b"\x0c" + "Tom Example".encode("utf-16-le") + b"\x00" * 84
        self.add(base + "/temporary/~$Budget.xlsx", owner[:162], age_days=65, expect_analyzer_failure=True, case="G-004")
        self.case("G-004", construction="G", expected=dict(present, extension_key=".xlsx", **{"analyzer.office.status": "error",
                                                                                                "extracted_content.status": "error"}),
                  notes="a real-corpus staple: Office's owner file carries the document's extension; both readers fail, the row stays")
        self.add(base + "/temporary/report.lock", b"", age_days=66, case="G-005")
        self.add(base + "/temporary/.~lock.report.odt#", b"Tom Example,tom,host,13.09.2026 19:00,file:///home/tom;\n", age_days=66, case="G-005")
        self.case("G-005", construction="G", expected=dict(present, row_count=2, **nothing),
                  notes="a .lock and LibreOffice's .~lock.<name>.odt# (extension '.odt#'): recorded, nothing reads them")
        self.add(base + "/backup/report.bak", make_docx(["A backup copy under .bak."]), age_days=67, case="G-006")
        self.add(base + "/backup/report.docx~", make_docx(["An editor's backup under a tilde."]), age_days=67, case="G-006")
        self.case("G-006", construction="G", expected=dict(present, row_count=2, **nothing),
                  notes="both hold a Word document; '.bak' and '.docx~' are extensions nothing names")
        self.add(base + "/autosave/AutoRecovery save of report.asd", b"\xd0\xcf\x11\xe0" + b"\x00" * 508, age_days=68, case="G-007")
        self.add(base + "/autosave/Backup of report.wbk", b"\xd0\xcf\x11\xe0" + b"\x00" * 508, age_days=68, case="G-007")
        self.case("G-007", construction="G", expected=dict(present, row_count=2, **nothing))

        # G-008 / G-009 / A-021 -- a program named as a document, a document named as a picture.
        self.add(base + "/misnamed/notes.docx", TINY_PROGRAM, age_days=69, expect_analyzer_failure=True, case="G-008")
        self.case("G-008", construction="G", expected=dict(present, **{"analyzer.office.status": "error", "extracted_content.status": "error",
                                                                        "marker_indexed": False}),
                  safety=["the 'program' is a DOS exit stub no 64-bit Windows runs; nothing here executes anything"],
                  notes="the Office analyzer and extraction both fail on the bytes (the sniff calls them binary); the name is kept")
        self.add(base + "/misnamed/photo.jpg", make_docx(["A Word document wearing a picture's name.",
                                                          "Marker: %s." % marker(base + "/misnamed/photo.jpg")]),
                 age_days=70, expect_analyzer_failure=True, case="G-009")
        self.case("G-009", construction="G", expected=dict(present, **{"analyzer.image.status": "error", "extracted_content.status": "extracted",
                                                                        "marker_indexed": True}),
                  notes="the image analyzer fails; extraction, chosen by the .jpg name, reads the bytes as the Word document they are and finds the marker")
        self.case("A-021", construction="G", expected={"file_state.state": "present"},
                  notes="the misleading extensions of G-008 and G-009: the name is preserved, the bytes decide what is read")
        self.case("A-021")["paths"].extend([(base + "/misnamed/notes.docx").replace("/", "\\"), (base + "/misnamed/photo.jpg").replace("/", "\\")])
        self.case("G-002", construction="L", expected={"file_state.state": "present", "extension_key": ""}, notes="the same files as A-019")
        self.case("G-002")["paths"].extend(self.case("A-019")["paths"])
        self.case("G-010", construction="G", expected={"file_state.state": "present", "extension_key": "",
                                                        "extracted_content.status": "skipped"},
                  notes="Documents\\thumbnail: a picture with no extension, not a document -- recorded as not processed")
        self.case("G-010")["paths"].append("Documents\\thumbnail")

    def _extreme_structures(self):
        """08_Extreme_Structures, the tool-safe part: emptiness. The big and
        deep ones are in Hostile\\; the forty-level tree is A-004."""
        base = "08_Extreme_Structures"
        self.add_folder(base + "/empty_folder", case="H-001")
        self.case("H-001", construction="L", classification="SCOPE", expected={},
                  matrix_expects="Record folder",
                  notes="folders are not rows in this program: the walk counts it ('Empty folders found' in the report, asserted through scan_expectations) and the folder tree does not show it")
        self.add_folder(base + "/only_empty/level1/level2/level3/level4/level5", case="H-002")
        self.case("H-002", construction="G", classification="SCOPE", expected={},
                  matrix_expects="Complete traversal",
                  notes="five folders with nothing but the next; the walk enters all five and counts the last as empty (the others hold a folder)")
        for i in range(3):
            self.add(base + "/only_hidden/hidden_%d.txt" % i, b"hidden, in a folder of hidden files\n", age_days=71,
                     attributes=FILE_ATTRIBUTE_HIDDEN, case="H-003")
        self.case("H-003", construction="G", expected={"file_state.state": "present", "row_count": 3, "attributes_has": ["Hidden"]})
        self.case("H-009", construction="G", expected={"file_state.state": "present", "depth": 42}, notes="the forty-level tree of A-004")
        self.case("H-009")["paths"].extend(self.case("A-004")["paths"])

    def _extraction_safety(self):
        """19_Extraction_Safety: the matrix's section 12.4 (Y-018..Y-023).
        Extraction is a trust boundary: its input can be hostile and its
        output can be a hazard."""
        base = "19_Extraction_Safety"
        present = {"file_state.state": "present"}

        # Y-018 / Y-019 -- names that are formulas to a spreadsheet. The
        # payloads are harmless: 1+1, and a hyperlink to a reserved domain.
        for name in ("=1+1.txt", "+1.txt", "-1.txt", "@SUM(1).txt"):
            self.add(base + "/formula_names/" + name, b"a name a spreadsheet would run\n", age_days=72, case="Y-018")
        self.add(base + "/=HYPERLINK(example.invalid).d/inside.txt", b"in a folder named as a formula\n", age_days=72, case="Y-018")
        self.case("Y-018", construction="G", expected=dict(present, row_count=5, csv_export_safe=True),
                  safety=["the payloads are 1+1 and a .invalid hyperlink; the export must neutralise them, which is the assertion"],
                  notes="found 2026-09-13 and fixed: the Files and results exports wrote such cells unguarded; csv_cell now prefixes an apostrophe")
        self.case("Y-019", construction="G", expected={"csv_export_safe": True}, notes="the same files as Y-018: + - and @")
        self.case("Y-019")["paths"].extend(self.case("Y-018")["paths"])

        # Y-020 -- control characters before the marker: a bell and ANSI
        # escapes are still text; a NUL byte makes the whole file binary.
        self.add(base + "/control_chars.txt",
                 b"\x07 a bell, one escape \x1b[0m and then: " + marker(base + "/control_chars.txt").encode("utf-8") + b"\n",
                 age_days=73, case="Y-020")
        self.case("Y-020", construction="G", expected=dict(present, **{"extracted_content.status": "extracted", "marker_indexed": True}),
                  notes="two control characters (BEL, ESC) are within the sniff's allowance of max(2, length/200); the marker is found")
        colour = "".join("\x1b[32m2026-01-%02d\x1b[0m \x1b[1mINFO\x1b[0m step %d finished\n" % (i % 28 + 1, i) for i in range(60))
        colour += "\x1b[33mWARN\x1b[0m " + marker(base + "/colour.log") + "\n"
        self.add(base + "/colour.log", colour.encode("utf-8"), age_days=73, case="Y-020c")
        self.case("Y-020c", construction="G", condition="Text with ANSI colour codes (a terminal log)",
                  expected=dict(present, **{"extracted_content.status": "extracted", "marker_indexed": True}),
                  matrix_expects="Extraction does not corrupt output",
                  notes="until B7.2 ESC counted as a control character and a 3 KB log with 180 colour codes was refused as binary; since B7.2 "
                        "(defect 50) terminal escape sequences are removed where text is decoded, so the gate sees text and the stored text is "
                        "what a terminal would have shown -- a real-world shape (CI, npm, PowerShell transcripts)")
        self.add(base + "/nul_bytes.txt",
                 b"two nulls \x00\x00 and then: " + marker(base + "/nul_bytes.txt").encode("utf-8") + b"\n",
                 age_days=73, case="Y-020b")
        self.case("Y-020b", construction="G", condition="Text containing embedded null characters", classification="SCOPE",
                  expected=dict(present, **{"extracted_content.status": "error", "marker_indexed": False}),
                  matrix_expects="Extraction does not corrupt output",
                  notes="observed 2026-09-13: fo_extractors.sniff calls any NUL byte without a UTF-16 BOM 'binary', so the file is refused whole "
                        "('not a recognised document format (binary)') rather than read around the NULs; the output is not corrupted -- it is absent")

        # Y-022 -- binary presented as text, at the top level and inside an archive.
        noise = random_bytes(SEED + 22, 256 * 1024)
        self.add(base + "/binary_as_text/noise.txt", noise, age_days=74, case="Y-022")
        self.case("Y-022", construction="G", expected=dict(present, **{"extracted_content.status": "error"}),
                  notes="observed 2026-09-13: the sniff's binary gate refuses it ('not a recognised document format (binary)'); no misleading text is produced")
        self.add(base + "/binary_as_text/noise_inside.zip", make_zip([("noise.txt", random_bytes(SEED + 23, 100 * 1024))]),
                 age_days=74, case="Y-022b")
        self.case("Y-022b", construction="G", condition="Binary file presented as text, inside an archive",
                  expected=dict(present, **{"extracted_content.status": "extracted", "extracted_content.char_count_lt": 1000}),
                  matrix_expects="Do not produce misleading text output",
                  notes="until B7.2 the archive-member text path had no sniff, so 100 KB of random bytes named .txt inside a zip became 102,419 "
                        "characters of stored, indexed text; since B7.2 (defect 51) a member named as text meets the same binary gate as a "
                        "top-level file and yields a one-line note instead")

        # Y-023 -- six encodings of the same English line, each with its marker.
        def line(key):
            return "Encoding test: %s. Caf\u00e9, na\u00efve, \u65e5\u672c\u8a9e.\n" % marker(base + "/encodings/" + key)
        self.add(base + "/encodings/utf8_bom.txt", b"\xef\xbb\xbf" + line("utf8_bom.txt").encode("utf-8"), age_days=75, case="Y-023")
        self.add(base + "/encodings/utf16le_bom.txt", b"\xff\xfe" + line("utf16le_bom.txt").encode("utf-16-le"), age_days=75, case="Y-023")
        self.add(base + "/encodings/utf16be_nobom.txt", line("utf16be_nobom.txt").encode("utf-16-be"), age_days=75, case="Y-023")
        self.add(base + "/encodings/cp1252.txt", line("cp1252.txt").replace("\u65e5\u672c\u8a9e", "Muenchen").encode("cp1252") * 6, age_days=75, case="Y-023")
        self.add(base + "/encodings/shift_jis.txt", line("shift_jis.txt").replace("Caf\u00e9, na\u00efve", "Kafe").encode("shift_jis") * 6, age_days=75, case="Y-023")
        self.add(base + "/encodings/utf8_with_stray_bytes.txt",
                 line("utf8_with_stray_bytes.txt").encode("utf-8") + b"\xff\xfe stray bytes that are not UTF-8 \xff\n", age_days=75, case="Y-023")
        self.case("Y-023", construction="G", expected=dict(present, row_count=6),
                  notes="five of six markers are found (UTF-8 BOM, UTF-16 LE BOM, cp1252, Shift-JIS, UTF-8 with stray bytes); see Y-023b for the sixth")
        self.case("Y-023b", construction="G", condition="UTF-16 without a byte-order mark", classification="SCOPE",
                  expected={"extracted_content.status": "error", "marker_indexed": False},
                  matrix_expects="Detect/handle encoding uncertainty",
                  notes="observed 2026-09-13: UTF-16 is recognised by its BOM only; without one the NUL bytes read as binary and the file is refused (LE behaves the same)")
        self.case("Y-023b")["paths"].append((base + "/encodings/utf16be_nobom.txt").replace("/", "\\"))

    def _bulk(self):
        """Volume and size/date spread, so ranked reports actually rank."""
        words = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]
        for i in range(140):
            body = " ".join(self.rng.choice(words) for _ in range(self.rng.randint(20, 400)))
            self.add(f"Bulk/text/doc_{i:03d}.txt", (body + "\n").encode("utf-8"),
                     age_days=self.rng.randint(1, 3000))
        for i in range(40):
            size = self.rng.randint(1024, 512 * 1024)
            self.add(f"Bulk/data/blob_{i:03d}.dat",
                     bytes(self.rng.getrandbits(8) for _ in range(size)),
                     age_days=self.rng.randint(1, 3000))
        for i in range(20):
            self.add(f"Bulk/logs/app_{i:03d}.log",
                     "".join(f"2026-01-{(i % 28) + 1:02d} INFO event {n}\n"
                             for n in range(self.rng.randint(10, 200))).encode(),
                     age_days=self.rng.randint(1, 900))

    # -- ground truth -------------------------------------------------------

    def ground_truth(self) -> dict:
        by_ext: dict[str, dict] = {}
        for f in self.files:
            ext = f["extension"] or "(none)"
            slot = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
            slot["count"] += 1
            slot["bytes"] += f["size_bytes"]

        by_hash: dict[str, list[dict]] = {}
        for f in self.files:
            if f.get("sha256"):                  # a symbolic link carries no bytes of its own
                by_hash.setdefault(f["sha256"], []).append(f)

        groups = []
        reclaimable = 0
        for digest, members in sorted(by_hash.items()):
            if len(members) < 2:
                continue
            size = members[0]["size_bytes"]
            waste = size * (len(members) - 1)
            reclaimable += waste
            groups.append({
                "sha256": digest,
                "size_bytes": size,
                "member_count": len(members),
                "reclaimable_bytes": waste,
                "members": sorted(m["relative_path"] for m in members),
            })

        cutoff = NOW - timedelta(days=5 * 365)
        built = {f["relative_path"] for f in self.files}      # a marker counts only for a file that was made
        return {
            "generator": "p2_build_acceptance_corpus.py",
            "seed": SEED,
            "generated_utc": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "totals": {
                "file_count": len(self.files),
                "logical_bytes": sum(f["size_bytes"] for f in self.files),
                "distinct_extensions": len(by_ext),
            },
            "by_extension": dict(sorted(by_ext.items())),
            "duplicates": {
                "group_count": len(groups),
                "total_reclaimable_bytes": reclaimable,
                "groups": groups,
            },
            "age": {
                "cutoff_utc": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "modified_before_cutoff": sum(1 for f in self.files if f["age_days"] > 5 * 365),
            },
            "largest_10": [
                {"relative_path": f["relative_path"], "size_bytes": f["size_bytes"]}
                for f in sorted(self.files, key=lambda x: -x["size_bytes"])[:10]
            ],
            "fts_markers": {
                k.replace("/", "\\"): {"phrase": v[0], "expected_indexed": v[1]}
                for k, v in MARKERS.items() if "!" in k or k.replace("/", "\\") in built   # '!member' phrases stay
            },
            "expected_analyzer_failures": sorted(
                f["relative_path"] for f in self.files if f["expect_analyzer_failure"]),
            "ocr_review_expected": [p.replace("/", "\\") for p in OCR_REVIEW_EXPECTED],
            "ocr_clean_expected": [p.replace("/", "\\") for p in OCR_CLEAN_EXPECTED],
            "not_documents": [p.replace("/", "\\") for p in NOT_DOCUMENTS],
            "skipped": sorted(self.skipped),
            "zero_byte_files": sorted(
                f["relative_path"] for f in self.files if f["size_bytes"] == 0),
            "cases": self._case_records(),
            "not_constructed": sorted(self.not_constructed, key=lambda c: c["id"]),
            "scan_expectations": self._scan_expectations(),
            "files": sorted(self.files, key=lambda f: f["relative_path"]),
        }

    def _scan_expectations(self) -> dict:
        """The totals the preliminary report states, computed from what was
        built: the checks compare them with the latest PreliminaryReport.txt.
        Path lengths depend on where the tree is, so they are computed
        against this build's root -- the truth belongs to that tree."""
        root = plain_path(ext_path(self.root))
        hidden = system = long_paths = 0
        max_depth = 0
        for f in self.files:
            attributes = f.get("attributes", 0)
            hidden += bool(attributes & 0x2)
            system += bool(attributes & 0x4)
            full = root + "\\" + f["relative_path"]
            if len(full.encode("utf-16-le")) // 2 > 260:      # UTF-16 units, as win_meta.utf16_length counts
                long_paths += 1
            max_depth = max(max_depth, f["relative_path"].count("\\"))
        return {
            "hidden_files": hidden,
            "system_files": system,
            "empty_files": sum(1 for f in self.files if f["size_bytes"] == 0),
            "empty_folders": len(self.empty_folders),
            "long_paths": long_paths,
            "links_skipped": len(self.link_folders),
            "max_depth": max_depth,
            "access_errors": 0,
        }

    def _case_records(self) -> list[dict]:
        """Every registered case, sorted by ID. A case with artefacts on disk
        is CONSTRUCTED; one registered without any stays PLANNED."""
        out = []
        for case_id in sorted(self.cases):
            record = dict(self.cases[case_id])
            record["paths"] = sorted(record["paths"])
            if record["status"] == "PLANNED" and record["paths"]:
                record["status"] = "CONSTRUCTED"
            out.append(record)
        return out


class HostileCorpus(Corpus):
    r"""The second tree: what must not go into `Corpus\`.

    Anything that breaks naive tooling or changes a scan's overall status --
    a folder the walk cannot list, a junction back to its parent, three
    names for one file, `CON`, ten thousand files in one directory, a 4 GB
    file that occupies nothing. Built only on request, scanned by its own
    project (`P2Hostile`), asserted by `p2_hostile_check.py`, and removed by
    `remove_tree`, which restores every ACL on the way out.

    Its truth differs from `Corpus\`'s in three ways: hard-linked paths
    share one physical object (no reclaimable bytes between them); files
    under a denied folder are `unobservable` -- written, but not in `files`,
    because nothing can list them; and `expected_present_rows` allows for
    the rows the engine is known to fold (A-014's chimera), so the check
    can hold the count while the defect is listed.
    """
    home = "Hostile"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.unobservable: list[dict] = []     # written, but under a folder the walk cannot list
        self.folded: list[dict] = []           # {kept, dropped}: pairs the engine is known to fold into one row
        self.physical: dict[str, str] = {}     # relative_path -> the relative_path it is a hard link of
        self.symlinks: list[dict] = []         # file symlinks: the row is the link, the bytes are the target's
        self.denied: list[str] = []            # folders whose listing is denied (setup/teardown by icacls)
        self.conflations: list[dict] = []      # file symlinks the engine is known to group with their targets

    def build(self):
        self._h_naming()
        self._h_identity()
        self._h_links()
        self._h_access()
        self._h_extreme()
        self._h_compound()

    # -- helpers -------------------------------------------------------------

    def _user(self):
        return "%s\\%s" % (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", ""))

    def deny(self, relpath: str, rights: str = "(RD,X)"):
        """Deny the current user `rights` on a path the builder made. The
        owner can always undo it; remove_tree does, with icacls /reset."""
        if not icacls(ext_path(self.root / relpath), "/deny", "%s:%s" % (self._user(), rights), "/Q"):
            raise OSError("could not deny %s on %s" % (rights, relpath))

    def fold(self, *, kept: str, dropped: str):
        """Record that the engine is known to keep one row for two files:
        the `kept` name survives with its own observation and the `dropped`
        file is recorded as folded (a FOLDED_CASE_TWIN event), so the
        engine's totals lack the dropped file's bytes and one row.
        (Until B7.1 the row was a chimera -- the kept name carrying the
        dropped file's size -- and the totals lacked the kept file's bytes.)"""
        self.folded.append({"kept": kept, "dropped": dropped})

    def add_hardlink(self, relpath: str, of: str, *, case: str | None = None):
        """Another directory entry for an existing file's bytes."""
        original = next(f for f in self.files if f["relative_path"] == of.replace("/", "\\"))
        os.makedirs(ext_path((self.root / relpath).parent), exist_ok=True)
        os.link(ext_path(self.root / of), ext_path(self.root / relpath))
        relative = relpath.replace("/", "\\")
        self.files.append(dict(original, relative_path=relative, note="hard link of " + original["relative_path"]))
        self.physical[relative] = self.physical.get(original["relative_path"], original["relative_path"])
        if case:
            self.case(case)["paths"].append(relative)

    def add_symlink(self, relpath: str, target, *, is_dir: bool = False, case: str | None = None, note: str = ""):
        """A symbolic link (needs Developer Mode or elevation; Python asks
        for the unprivileged kind). `target` is a corpus-relative path or an
        absolute one. A directory link joins link_folders (the walk skips
        it); a file link is a row of its own whose bytes are the target's."""
        link = self.root / relpath
        os.makedirs(ext_path(link.parent), exist_ok=True)
        absolute = target if os.path.isabs(str(target)) else plain_path(ext_path(self.root / target))
        os.symlink(absolute, ext_path(link), target_is_directory=is_dir)
        relative = relpath.replace("/", "\\")
        if is_dir:
            self.link_folders.append(relative)
        else:
            self.files.append({"relative_path": relative, "size_bytes": 0, "sha256": None,
                               "extension": dotnet_extension(Path(relpath).name), "modified_utc": None, "age_days": 0,
                               "note": note or ("symbolic link to " + str(target)), "expect_analyzer_failure": False,
                               "symlink_target": str(target)})
            self.symlinks.append({"relative_path": relative, "target": str(target)})
        if case:
            self.case(case)["paths"].append(relative)

    def add_junction(self, relpath: str, target, *, case: str | None = None):
        """A directory junction; the walk skips it whatever it points at."""
        import _winapi
        link = self.root / relpath
        os.makedirs(ext_path(link.parent), exist_ok=True)
        absolute = target if os.path.isabs(str(target)) else plain_path(ext_path(self.root / target))
        stand_in = not os.path.isdir(ext_path(absolute))
        if stand_in:                                  # CreateJunction wants an existing target: make one, then take it away
            os.makedirs(ext_path(absolute))
        _winapi.CreateJunction(absolute, plain_path(ext_path(link)))
        if stand_in:
            os.rmdir(ext_path(absolute))
        relative = relpath.replace("/", "\\")
        self.link_folders.append(relative)
        if case:
            self.case(case)["paths"].append(relative)

    # -- families ------------------------------------------------------------

    def _h_naming(self):
        """01_Naming, the hostile half: what changes a project's totals or
        what no tool but this program's own opens."""
        present = {"file_state.state": "present"}

        # A-014 / Y-009 / Y-053 -- case-only names in a case-sensitive directory.
        # Found 2026-09-13: the walk yields both files (its count and byte
        # total say so) but the ingest keys a path by its lower-cased name,
        # so the second folds into the first. Until B7.1 the surviving row
        # was a chimera -- the first file's name with the second file's
        # size, flipping between the two on every scan; since B7.1 the
        # first record keeps the row and the second is recorded as a
        # FOLDED_CASE_TWIN event and counted in the report. Still one row
        # for two files, which is the defect that remains.
        case_dir = self.root / "01_Naming" / "case_sensitive"
        os.makedirs(ext_path(case_dir), exist_ok=True)
        if enable_case_sensitivity(case_dir):
            self.add("01_Naming/case_sensitive/Report.txt", b"capital R\n", age_days=30, case="A-014")
            self.add("01_Naming/case_sensitive/report.txt", b"small r\n", age_days=31, case="A-014")
            self.fold(kept="01_Naming\\case_sensitive\\Report.txt", dropped="01_Naming\\case_sensitive\\report.txt")
            self.case("A-014", construction="G", classification="DEFECT", matrix_expects="two objects: one row each, both present",
                      expected={"row_count": 1},
                      setup=["icacls <dir> /grant <user>:(F)", "fsutil file setCaseSensitiveInfo <dir> enable"],
                      notes="the engine keeps one row for the pair (the first file seen; the second is recorded "
                            "as a FOLDED_CASE_TWIN event and counted in the report); the walk counts two. "
                            "file_path is unique on a lower-cased relative_path_key")
            for twin in ("Y-009", "Y-053"):
                self.case(twin, construction="G", classification="DEFECT", expected={"row_count": 1},
                          matrix_expects="two rows", notes="the same two files as A-014")
                self.case(twin)["paths"].extend(self.case("A-014")["paths"])
            self.case("W-003", construction="G", classification="DEFECT", expected={"row_count": 1},
                      matrix_expects="Preserve actual semantics",
                      notes="a case-sensitive directory on NTFS (fsutil setCaseSensitiveInfo): the semantics are not preserved -- A-014")
            self.case("W-003")["paths"].extend(self.case("A-014")["paths"])
        else:
            for case_id in ("A-014", "Y-009", "Y-053"):
                self.decline(case_id, "the case-sensitivity flag could not be set on this volume")

        # A-007 -- trailing space, trailing dot: only \\?\ makes or opens them.
        self.add("01_Naming/trailing/space ", b"a name ending in a space, no extension\n", age_days=32, case="A-007")
        self.add("01_Naming/trailing/dot.", b"a name ending in a dot, no extension\n", age_days=32, case="A-007")
        self.case("A-007", construction="G", expected=dict(present, row_count=2, **{"hash_status": ["size_unique", "unique_by_hash"]}),
                  notes="Win32 strips a trailing space or dot unless the path is \\\\?\\-prefixed; every reader here prefixes")

        # A-016 -- reserved device names.
        for name, body in (("CON", b"the console's name\n"), ("NUL.txt", b"NUL with an extension\n"),
                           ("COM1", b"a serial port's name\n"), ("LPT1.log", b"a printer port's name with an extension\n")):
            self.add("01_Naming/reserved/" + name, body, age_days=33, case="A-016")
        self.add("01_Naming/reserved/AUX.docx", make_docx(["A Word document named after a device."]), age_days=33, case="A-016")
        self.case("A-016", construction="G", expected=dict(present, row_count=5, **{"hash_status": ["size_unique", "unique_by_hash"]}),
                  notes="NUL.txt and LPT1.log keep their extensions; AUX.docx is analysed and read like any other document")

        # A-003b -- a path of about two thousand characters (200 levels).
        deep = "01_Naming/very_deep/" + "/".join("d%03d" % i for i in range(1, 201)) + "/leaf.txt"
        self.add(deep, b"two hundred levels down\n", age_days=34, case="A-003b")
        self.case("A-003b", construction="G", condition="Complete path of about 1,000 characters (200 levels)",
                  expected=dict(present, path_length_gt=1000, depth=202, **{"extracted_content.status": "extracted"}))

        # A-022 / H-008 / H-007 -- ten thousand files in one directory.
        rng = random.Random(SEED + 8)
        for i in range(10000):
            self.add("01_Naming/fan_out/f%05d.txt" % i, ("file %d " % i).encode("ascii") * rng.randint(1, 40),
                     age_days=rng.randint(1, 2000), case="A-022" if i < 3 else None)
        self.case("A-022", construction="G", expected=dict(present, row_count=3),
                  notes="10,000 files in one directory; the case lists three of them, the totals count them all, the walk lists the directory whole")
        for twin, note in (("H-008", "the same directory"), ("H-007", "the same ten thousand tiny files")):
            self.case(twin, construction="G", expected={"file_state.state": "present"}, notes=note)
            self.case(twin)["paths"].extend(self.case("A-022")["paths"])

    def _h_identity(self):
        """02_Identity: one physical object, several names."""
        present = {"file_state.state": "present"}
        blob = random_bytes(SEED + 9, 48 * 1024)
        self.add("02_Identity/hard_links/original.bin", blob, age_days=40, case="Y-001")
        self.add_hardlink("02_Identity/hard_links/alias_1.bin", "02_Identity/hard_links/original.bin", case="Y-001")
        self.add_hardlink("02_Identity/hard_links/elsewhere/alias_2.bin", "02_Identity/hard_links/original.bin", case="Y-001")
        self.case("Y-001", construction="G", expected=dict(present, row_count=3, hard_link_count=3, same_physical_object=True,
                                                           duplicate_group_members=3, hard_link_alias_count=2, reclaimable_bytes=0),
                  notes="three directory entries, one file: the duplicate projection counts one physical copy and nothing to reclaim")
        for twin in ("Y-002", "Y-052"):
            self.case(twin, construction="G", expected={"same_physical_object": True, "reclaimable_bytes": 0}, notes="the same three entries as Y-001")
            self.case(twin)["paths"].extend(self.case("Y-001")["paths"])
        self.case("C-016", construction="G", expected={"same_physical_object": True}, notes="multiple paths to one object: the hard links of Y-001 (junctions to one folder are in 03_Links)")
        self.case("C-016")["paths"].extend(self.case("Y-001")["paths"])
        self.case("Y-011", construction="G", expected={"same_physical_object": True},
                  notes="the three names of Y-001 are one file ID: the program keys duplicates by physical object, not by path")
        self.case("Y-011")["paths"].extend(self.case("Y-001")["paths"])

    def _h_links(self):
        """03_Links, the hostile half: symbolic links and junctions -- what
        the walk must recognise and refuse to enter."""
        present = {"file_state.state": "present"}
        self.add("03_Links/targets/document.txt", b"The file the links point at.\n", age_days=41)
        self.add("03_Links/targets/folder/inside.txt", b"Inside the folder the links point at.\n", age_days=41)
        try:
            self.add_symlink("03_Links/file_link.txt", "03_Links/targets/document.txt", case="C-011")
        except OSError as exc:
            for case_id in ("C-011", "C-012", "C-013", "Z-001b"):
                self.decline(case_id, "symbolic links cannot be created here (%s); Developer Mode or elevation is needed" % exc)
        else:
            self.case("C-011", construction="G",
                      expected=dict(present, is_reparse_point=1, reparse_tag=0xA000000C, size_bytes=0,
                                    hash_status="skipped_link", not_grouped=True),
                      matrix_expects="Correctly classify link/object; do not conflate a link with its target",
                      notes="the row is the link (reparse point, size 0). Until B7.2 the hash stage opened the path, Windows resolved it, "
                            "and the link was hashed as its target's bytes, grouped with the target and offered its 29 bytes as reclaimable; "
                            "since B7.2 (defect 47) the hash stage recognises a junction or symbolic link and never opens it -- "
                            "hash_status 'skipped_link', no digest, no group")
            self.add_symlink("03_Links/folder_link", "03_Links/targets/folder", is_dir=True, case="C-012")
            self.case("C-012", construction="G", expected={"rows_below": 0},
                      notes="a directory symlink is skipped ('Symlinks / junctions (not recursed)'); the target's files appear once, at their real path")
            self.add_symlink("03_Links/broken_file_link.txt", "03_Links/targets/deleted.txt", case="C-013",
                             note="symbolic link to a file that does not exist")
            self.add_symlink("03_Links/broken_folder_link", "03_Links/targets/deleted_folder", is_dir=True, case="C-013c")
            self.case("C-013c", construction="G", condition="Broken symbolic link to a folder", expected={"rows_below": 0},
                      notes="skipped like any directory link, target or no target; no row, no error")
            self.case("C-013", construction="G", classification="SCOPE",
                      expected={"file_state.state": "present"},
                      matrix_expects="Record link and unresolved target",
                      notes="the file link is a present row (reparse point) whose hash stage records it as a link (skipped_link, B7.2) without resolving it; the folder link is skipped without error; neither records a target")
            self.case("C-013b", construction="G", condition="Broken symbolic link to a file: the hash stage",
                      expected={"is_reparse_point": 1, "hash_status": "skipped_link"},
                      notes="B7.2: a link is never opened by the hash stage, target or no target -- skipped_link; until B7.2 it was opened and reported FILE MISSING")
            self.case("C-013b")["paths"].append("03_Links\\broken_file_link.txt")
            self.case("Y-004", construction="G", expected={"is_reparse_point": 1, "reparse_tag": 0xA000000C},
                      notes="a symbolic link is a reparse point that is not a junction (tag IO_REPARSE_TAG_SYMLINK): recorded with its tag, "
                            "classified as a link, not assumed to be a directory")
            self.case("Y-004")["paths"].append("03_Links\\broken_file_link.txt")
            self.add_symlink("03_Links/link_out_of_root.md", plain_path(ext_path(self.root.parent / "README.md")), case="Z-001b")
            self.case("Z-001b", construction="G", condition="File symbolic link whose target is outside the root", classification="SCOPE",
                      expected=dict(present, is_reparse_point=1, size_bytes=0),
                      matrix_expects="the scan never follows a link out of the root",
                      notes="the row is inside the root and is marked a reparse point; since B7.2 the hash stage does not open it (skipped_link); an analyzer still reads C:\\FOTest\\README.md through it -- bytes from outside the root under a path inside it")
        self.add_junction("03_Links/junction_to_folder", "03_Links/targets/folder", case="C-014")
        self.case("C-014", construction="G", expected={"rows_below": 0}, notes="skipped; the target's files appear once, at their real path")
        self.add_junction("03_Links/loop/back_to_parent", "03_Links/loop", case="C-015")
        self.add("03_Links/loop/note.txt", b"a folder whose junction points at itself\n", age_days=42)
        self.case("C-015", construction="G", expected={"rows_below": 0}, notes="a junction to its own parent: skipped, the walk ends")
        self.add_junction("03_Links/two_to_one/first", "03_Links/targets/folder", case="C-016b")
        self.add_junction("03_Links/two_to_one/second", "03_Links/targets/folder", case="C-016b")
        self.case("C-016b", construction="G", condition="Two junctions to the same folder", expected={"rows_below": 0},
                  notes="both skipped; inside.txt is one row, under targets")
        self.add_junction("03_Links/junction_out_of_root", plain_path(ext_path(self.root.parent / "Research" / "Samples")), case="Z-001")
        self.case("Z-001", construction="G", condition="Junction whose target is outside the root", expected={"rows_below": 0},
                  notes="skipped like any junction; nothing outside the root is listed")
        self.add_junction("03_Links/cycle/a_to_b", "03_Links/cycle/b", case="X-003b")
        os.makedirs(ext_path(self.root / "03_Links/cycle/b"), exist_ok=True)
        self.add_junction("03_Links/cycle/b/b_to_a", "03_Links/cycle", case="X-003b")
        self.case("X-003b", construction="G", condition="Junction cycle: a -> b -> a", expected={"rows_below": 0},
                  notes="both junctions skipped; the walk completes")

    def _h_access(self):
        """04_Access: what exists but cannot be read, and what cannot be listed."""
        present = {"file_state.state": "present"}
        self.add("04_Access/read_denied.txt", b"a file whose bytes the user may not read\n", age_days=43, case="D-002")
        self.deny("04_Access/read_denied.txt", "(R)")
        self.case("D-002", construction="G", expected=dict(present, **{"hash.error_kind": "ACCESS DENIED", "analyzer.status": "error",
                                                                        "extracted_content.status": "error"}),
                  setup=["icacls <file> /deny <user>:(R)"], teardown=["icacls <file> /reset"],
                  notes="listed and stat'ed (present, size, timestamps); every reader is refused: the hash stage says ACCESS DENIED, the analyzers error")
        for twin in ("D-004", "D-007"):
            self.case(twin, construction="G", expected={"hash.error_kind": "ACCESS DENIED"}, notes="the same file as D-002")
            self.case(twin)["paths"].append("04_Access\\read_denied.txt")
        for i in range(3):
            self.add("04_Access/denied_folder/secret_%d.txt" % i, b"under a folder nobody may list\n", age_days=44, unobservable=True)
        self.deny("04_Access/denied_folder")
        self.denied.append("04_Access\\denied_folder")
        self.case("D-003", construction="G", expected={"file_observation.status": "inaccessible",
                                                        "file_observation.error_kind": "DIRECTORY ACCESS ERROR", "rows_below": 0,
                                                        "inventory_scan.status": "completed_with_warnings"},
                  setup=["icacls <dir> /deny <user>:(RD,X)"], teardown=["icacls <dir> /reset"],
                  notes="the folder's own path is an inaccessible row; its three files are unobservable and have no rows; the scan completes with warnings")
        self.case("D-003")["paths"].append("04_Access\\denied_folder")
        self.case("V-001", construction="G", expected={"file_observation.status": "inaccessible"}, notes="the protected directory of D-003")
        self.case("V-001")["paths"].append("04_Access\\denied_folder")
        # D-005 -- what a system-protected file looks like from here: System and Hidden, and unreadable.
        self.add("04_Access/system_protected.dat", b"the shape of a system file: attributes and a denial\n", age_days=44,
                 attributes=FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_HIDDEN, case="D-005")
        self.deny("04_Access/system_protected.dat", "(R)")
        self.case("D-005", construction="G", expected=dict(present, attributes_has=["System", "Hidden"], **{"hash.error_kind": "ACCESS DENIED"}),
                  setup=["attrib +S +H", "icacls <file> /deny <user>:(R)"], teardown=["icacls <file> /reset"],
                  notes="listed with its attributes; unreadable, and recorded so. A real pagefile.sys lives outside any corpus root")
        # D-008 / F-011 -- the denied folder's files inherit its ACL; the ACL itself is not recorded.
        self.case("D-008", construction="G", expected={"file_observation.status": "inaccessible"},
                  notes="the files under denied_folder inherit the denial: the folder is the inaccessible row, they have none (D-003)")
        self.case("D-008")["paths"].append("04_Access\\denied_folder")
        self.case("F-011", construction="G", expected={"hash.error_kind": "ACCESS DENIED"}, classification="SCOPE",
                  matrix_expects="Graceful metadata handling",
                  notes="read_denied.txt: the denial is recorded as the reason the hash failed; the ACL and owner themselves are not recorded")
        self.case("F-011")["paths"].append("04_Access\\read_denied.txt")

    def _h_extreme(self):
        """08_Extreme_Structures, the hostile half: size and count."""
        present = {"file_state.state": "present"}
        self.add("08_Extreme_Structures/four_gb_sparse.bin", b"G" * 4096, age_days=45, sparse_to=4 * 1024 ** 3, case="H-006")
        self.case("H-006", construction="G", expected=dict(present, size_bytes=4 * 1024 ** 3, attributes_has=["SparseFile"], allocated_lt_size=True,
                                                           hash_status=["size_unique", "unique_by_hash"]),
                  notes="4 GiB in the directory, 4 KB on disk; the hash stage reads four gigabytes of zeros; the largest-files report lists it first")
        self.add("08_Extreme_Structures/hundred_thousand_members.zip", make_zip([("m%06d.txt" % i, b"") for i in range(100000)], level=1),
                 age_days=46, case="Y-014c")
        self.case("Y-014c", construction="G", condition="Archive with 100,000 members",
                  expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                            "analyzer.archive.detail": {"EntryCount": "100000", "EntriesRecorded": "10000", "Truncated": "True", "AnalysisMode": "capped"}}),
                  notes="every member counted, ten thousand rows kept, and the listing says it is capped")
        line = ("lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt " * 4096 * 4).encode("ascii")
        self.add("08_Extreme_Structures/one_line_16mb.txt", line, age_days=47, compress=True, case="Y-021")
        self.case("Y-021", construction="G", expected=dict(present, size_bytes=len(line), **{"extracted_content.status": "extracted"}),
                  notes="one line of 16 MB: read whole (no cap exists), stored and indexed whole; the time and the index size are the observation")

    def _h_compound(self):
        """90_COMPOUND: the matrix's cross-layer scenarios that need this tree."""
        deep = "90_COMPOUND/X-002/" + "/".join("level%02d" % i for i in range(1, 41)) + "/denied"
        self.add(deep + "/inside.txt", b"under a denied folder at the bottom of a long path\n", age_days=48, unobservable=True)
        self.deny(deep)
        self.denied.append(deep.replace("/", "\\"))
        self.case("X-002", construction="G", condition="Long path + inaccessible folder",
                  expected={"file_observation.status": "inaccessible", "file_observation.error_kind": "DIRECTORY ACCESS ERROR", "rows_below": 0},
                  setup=["icacls <dir> /deny <user>:(RD,X)"], teardown=["icacls <dir> /reset"],
                  notes="the error row carries a path over 260 characters")
        self.case("X-002")["paths"].append(deep.replace("/", "\\"))
        for i in range(20):
            self.deny("01_Naming/fan_out/f%05d.txt" % (i * 500), "(R)")
        self.case("X-015", construction="G", condition="Massive directory + inaccessible entries",
                  expected={"file_state.state": "present", "hash.error_kind": "ACCESS DENIED"},
                  setup=["icacls <20 files> /deny <user>:(R)"], teardown=["icacls <file> /reset"],
                  notes="twenty of the ten thousand are read-denied: present, hashed as ACCESS DENIED; the other 9,980 hashed; the scan completes (a file error is not a directory error)")
        self.case("X-015")["paths"].extend("01_Naming\\fan_out\\f%05d.txt" % (i * 500) for i in range(20))
        self.add("90_COMPOUND/X-004/tree/a.txt", b"tree file a\n" * 3, age_days=49, case="X-004")
        self.add("90_COMPOUND/X-004/tree - Copy/a.txt", b"tree file a\n" * 3, age_days=50, case="X-004")
        self.add_junction("90_COMPOUND/X-004/tree/broken_junction", "90_COMPOUND/X-004/nowhere", case="X-004")
        self.add_junction("90_COMPOUND/X-004/tree - Copy/broken_junction", "90_COMPOUND/X-004/nowhere", case="X-004")
        self.case("X-004", construction="G", condition="Duplicate folder tree containing broken links",
                  expected={"duplicate_group_members": 2},
                  notes="the two a.txt group; the broken junctions (target never made) are skipped like any junction")

    # -- truth ---------------------------------------------------------------

    def add(self, relpath, data, *, unobservable: bool = False, **kwargs):
        if not unobservable:
            return super().add(relpath, data, **kwargs)
        # Written, then hidden behind a denied folder: it exists and nothing
        # can list it. Recorded apart from `files`, so the totals are what
        # the walk can see.
        before = len(self.files)
        super().add(relpath, data, **kwargs)
        self.unobservable.append(self.files.pop(before))

    def ground_truth(self) -> dict:
        truth = super().ground_truth()
        truth["generator"] = "p2_build_acceptance_corpus.py --hostile"
        for key in ("fts_markers", "ocr_review_expected", "ocr_clean_expected", "not_documents"):
            truth[key] = {} if isinstance(truth[key], dict) else []
        # Duplicates by physical object, not by directory entry: hard-linked
        # paths are one file. Symbolic links carry no bytes of their own.
        by_hash: dict[str, list[dict]] = {}
        for f in self.files:
            if f.get("sha256"):
                by_hash.setdefault(f["sha256"], []).append(f)
        groups, reclaimable = [], 0
        for digest, members in sorted(by_hash.items()):
            physical = {self.physical.get(m["relative_path"], m["relative_path"]) for m in members}
            if len(members) < 2:
                continue
            waste = members[0]["size_bytes"] * (len(physical) - 1)
            reclaimable += waste
            groups.append({"sha256": digest, "size_bytes": members[0]["size_bytes"], "member_count": len(members),
                           "physical_copies": len(physical), "reclaimable_bytes": waste,
                           "members": sorted(m["relative_path"] for m in members)})
        truth["duplicates"] = {"group_count": len(groups), "total_reclaimable_bytes": reclaimable, "groups": groups}
        truth["totals"]["logical_bytes"] = sum(f["size_bytes"] for f in self.files)
        sizes = {f["relative_path"]: f["size_bytes"] for f in self.files}
        truth["hostile"] = {
            "expected_present_rows": len(self.files) - len(self.folded),
            "expected_logical_bytes": truth["totals"]["logical_bytes"] - sum(sizes[f["dropped"]] for f in self.folded),
            "folded": sorted(self.folded, key=lambda f: f["kept"]),
            "symlink_conflations": sorted(self.conflations, key=lambda c: c["link"]),
            "unobservable": sorted(f["relative_path"] for f in self.unobservable),
            "denied_folders": sorted(self.denied),
            "symlinks": sorted(self.symlinks, key=lambda x: x["relative_path"]),
            "hard_links": dict(sorted(self.physical.items())),
            "link_folders": sorted(self.link_folders),
        }
        truth["scan_expectations"]["access_errors"] = len(self.denied)
        truth["scan_expectations"]["links_skipped"] = len(self.link_folders)
        return truth


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=r"C:\FOTest",
                    help="directory holding Corpus\\, Hostile\\ and the ground-truth files (the tree built is recreated)")
    ap.add_argument("--samples", default=None,
                    help="folder of third-party samples to copy in (default: <root>\\Research\\Samples)")
    ap.add_argument("--matrix", default=None,
                    help="the matrix reconciliation CSV (default: <root>\\%s)" % MATRIX_CSV)
    ap.add_argument("--hostile", action="store_true",
                    help="build Hostile\\ and HOSTILE_GROUND_TRUTH.json instead of Corpus\\")
    ap.add_argument("--teardown", action="store_true",
                    help="remove Hostile\\ (ACLs restored, links not followed) and its truth file; build nothing")
    args = ap.parse_args()

    base = Path(args.root)
    samples = Path(args.samples) if args.samples else base / "Research" / "Samples"
    matrix = read_matrix(Path(args.matrix) if args.matrix else base / MATRIX_CSV)

    if args.teardown:
        hostile_dir = base / "Hostile"
        remove_tree(hostile_dir)
        truth_path = base / "HOSTILE_GROUND_TRUTH.json"
        if truth_path.exists():
            truth_path.unlink()
        print(f"removed           : {hostile_dir} and {truth_path.name}")
        return

    if args.hostile:
        corpus_dir, truth_path, builder = base / "Hostile", base / "HOSTILE_GROUND_TRUTH.json", HostileCorpus
    else:
        corpus_dir, truth_path, builder = base / "Corpus", base / "GROUND_TRUTH.json", Corpus
    remove_tree(corpus_dir)
    os.makedirs(ext_path(corpus_dir))

    corpus = builder(corpus_dir, samples, matrix)
    corpus.build()
    truth = corpus.ground_truth()
    truth_path.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")

    t = truth["totals"]
    d = truth["duplicates"]
    print(f"corpus            : {corpus_dir}")
    print(f"ground truth      : {truth_path}")
    print(f"matrix rows       : {len(matrix)}" + ("" if matrix else "  (reconciliation CSV not found; case text left blank)"))
    print(f"files             : {t['file_count']:,}")
    print(f"logical bytes     : {t['logical_bytes']:,}")
    print(f"extensions        : {t['distinct_extensions']}")
    print(f"duplicate groups  : {d['group_count']}")
    print(f"reclaimable bytes : {d['total_reclaimable_bytes']:,}")
    print(f"analyzer failures : {len(truth['expected_analyzer_failures'])} expected")
    print(f"older than 5y     : {truth['age']['modified_before_cutoff']}")
    print(f"cases             : {len(truth['cases'])} registered, {len(truth['not_constructed'])} not constructed here")
    for line in truth["skipped"]:
        print(f"not made          : {line}")


if __name__ == "__main__":
    main()
