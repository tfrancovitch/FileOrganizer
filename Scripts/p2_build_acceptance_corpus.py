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
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
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


def _is_reparse(entry) -> bool:
    try:
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def reset_acl(path) -> None:
    """Put a file or folder's ACL back to what it inherits.

    `icacls /reset` is what undoes an explicit deny the builder placed for an
    access case. The owner of a file can always rewrite its ACL, so this
    works without elevation on anything the builder made.
    """
    if os.name != "nt":
        return
    subprocess.run(["icacls", plain_path(path), "/reset", "/Q"],
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


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


def normalize_ooxml(data: bytes) -> bytes:
    """Rewrite an OOXML archive with fixed entry timestamps.

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
            if name.startswith("docProps/"):
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
            attributes: int = 0, created: datetime | None = None):
        """Write one file and record its truth.

        `attributes` are FILE_ATTRIBUTE_* bits to set after writing (hidden,
        system, read-only). `created` pins the creation time -- NTFS keeps
        it separately from the modified time, which `age_days` sets.
        """
        path = self.root / relpath
        target = ext_path(path)
        os.makedirs(ext_path(path.parent), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
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
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "extension": Path(relpath).suffix.lower(),
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
        for ext in (".orf", ".rw2", ".raf", ".x3f", ".cr3", ".raw", ".mrw"):
            self.skipped.append("Raw/shot%s: not TIFF-based; needs a real camera file" % ext)

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
        else:
            self.skipped.append("Samples/: no samples folder (WordPerfect, OneNote and non-TIFF RAW need real files)")

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
        self.add(base + "/bad_info.pdf", pdf, age_days=46, expect_analyzer_failure=True, case="E-008b")
        self.case("E-008", construction="G", expected=dict(present, **{"extracted_content.status": "extracted", "analyzer.office.status": "analyzed"}),
                  notes="python-docx shrugs at the dates: analysed, text read")
        self.case("E-008b", construction="G", condition="Invalid internal metadata (PDF /Info dates)", classification="DEFECT",
                  expected=dict(present, **{"extracted_content.status": "extracted", "analyzer.pdf.status": "error"}),
                  matrix_expects="Graceful parser failure",
                  notes="observed 2026-09-13: pypdf's date conversion raises on '/CreationDate (garbage)' and the whole PDF analysis is an error, "
                        "though the page is fine and extraction reads it; a garbage date should empty one field, not fail the file. Minor; for the user's decision")

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
        self.add(base + "/executable/tools.zip", make_zip([("tool.com", b"\xc3"), ("tool.exe", b"not a program, but named as one\n"),
                                                           ("readme.txt", b"A zip that carries programs.\n")]), age_days=53, case="Y-016")
        self.case("Y-016", construction="G", expected=dict(present, **{"analyzer.archive.status": "analyzed",
                                                                        "archive_members_include": ["tool.com", "tool.exe", "readme.txt"],
                                                                        "extracted_content.status": "extracted"}),
                  safety=["tool.com is a single RET (0xC3), inert on 64-bit Windows; .com and .exe are not readable suffixes, so neither member is ever written out"],
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
                for k, v in MARKERS.items()
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
    project, asserted by its own check, and removed by `remove_tree`, which
    restores every ACL on the way out.

    Families are added one matrix category at a time; each is a `_h_*`
    method. None yet: this is the scaffold, so the flags, the truth file and
    the teardown exist before the first hostile artefact does.
    """
    home = "Hostile"

    def build(self):
        self._h_naming()

    def _h_naming(self):
        """01_Naming, the hostile half: what changes a project's totals."""
        # A-014 / Y-009 / Y-053 -- case-only names in a case-sensitive directory.
        # Found 2026-09-13: the walk yields both files (its count and byte
        # total say so) but the ingest keys a path by its lower-cased name,
        # so the second folds into the first -- and the surviving row is a
        # chimera: the first file's name with the second file's size. The
        # summary then disagrees with the walk's own count. Recorded as a
        # DEFECT for the user's decision; the expectation stays the matrix's.
        case_dir = self.root / "01_Naming" / "case_sensitive"
        os.makedirs(ext_path(case_dir), exist_ok=True)
        if enable_case_sensitivity(case_dir):
            self.add("01_Naming/case_sensitive/Report.txt", b"capital R\n", age_days=30, case="A-014")
            self.add("01_Naming/case_sensitive/report.txt", b"small r\n", age_days=31, case="A-014")
            self.case("A-014", construction="G", classification="DEFECT", matrix_expects="two objects: one row each, both present",
                      expected={"row_count": 1},
                      setup=["icacls <dir> /grant <user>:(F)", "fsutil file setCaseSensitiveInfo <dir> enable"],
                      notes="the engine keeps one row for the pair (name of the first, size of the second); "
                            "the walk counts two. file_path is unique on a lower-cased relative_path_key")
            for twin in ("Y-009", "Y-053"):
                self.case(twin, construction="G", classification="DEFECT", expected={"row_count": 1},
                          matrix_expects="two rows", notes="the same two files as A-014")
                self.case(twin)["paths"].extend(self.case("A-014")["paths"])
        else:
            for case_id in ("A-014", "Y-009", "Y-053"):
                self.decline(case_id, "the case-sensitivity flag could not be set on this volume")

    def ground_truth(self) -> dict:
        truth = super().ground_truth()
        truth["generator"] = "p2_build_acceptance_corpus.py --hostile"
        # Not a document corpus: the marker, OCR and not-document lists
        # describe `Corpus\`, and would mislead a check reading this file.
        for key in ("fts_markers", "ocr_review_expected", "ocr_clean_expected", "not_documents"):
            truth[key] = {} if isinstance(truth[key], dict) else []
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
