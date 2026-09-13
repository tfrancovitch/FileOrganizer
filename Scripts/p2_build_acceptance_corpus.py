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

def make_pdf(text: str) -> bytes:
    """A minimal single-page PDF with an extractable text stream."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
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


def make_docx(paragraphs: list[str]) -> bytes:
    from docx import Document
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    stamp = NOW.replace(tzinfo=None)
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


def make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


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
             classification: str | None = None) -> dict:
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
                 age_days=120, note="FTS marker: docx inside a zip inside the zip")
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
        # Group A: 3 identical text files -> 2 reclaimable copies
        payload_a = (b"The same paragraph repeated verbatim.\n" * 40)
        for i, rel in enumerate([
            "Duplicates/original_notes.txt",
            "Duplicates/copies/original_notes (copy).txt",
            "Duplicates/copies/original_notes - Copy (2).txt",
        ]):
            self.add(rel, payload_a, age_days=30 + i * 10, note="dup group A")

        # Group B: 2 identical binaries -> 1 reclaimable copy
        payload_b = bytes(random.Random(SEED + 1).getrandbits(8) for _ in range(64 * 1024))
        for i, rel in enumerate(["Duplicates/archive_blob.bin",
                                 "Duplicates/backup/archive_blob.bin"]):
            self.add(rel, payload_b, age_days=500 + i * 30, note="dup group B")

        # Group C: 4 identical small config files -> 3 reclaimable copies
        payload_c = json.dumps({"setting": "value", "enabled": True}, indent=2).encode()
        for i in range(4):
            self.add(f"Duplicates/configs/node{i}/settings.json", payload_c,
                     age_days=90 + i, note="dup group C")

        # Near-miss: same size, different bytes. Must NOT group.
        self.add("Duplicates/decoy_same_size.bin",
                 bytes(random.Random(SEED + 2).getrandbits(8) for _ in range(64 * 1024)),
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
        self.add("Malformed/truncated.pdf", b"%PDF-1.4\n1 0 obj\n<</Type/Catalog",
                 age_days=60, note="invalid PDF", expect_analyzer_failure=True)
        self.add("Malformed/not_really.docx", b"This is plain text pretending to be a docx.",
                 age_days=61, note="invalid DOCX", expect_analyzer_failure=True)
        self.add("Malformed/broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
                 age_days=62, note="invalid PNG", expect_analyzer_failure=True)
        self.add("Malformed/corrupt.gz", b"\x1f\x8b\x08\x00" + b"\xff" * 24,
                 age_days=63, note="invalid GZIP", expect_analyzer_failure=True)
        self.add("Malformed/truncated.zip", make_zip([("a.txt", b"a" * 4000)])[:600],
                 age_days=64, note="truncated ZIP", expect_analyzer_failure=True)
        self.add("Malformed/not_really.pst", b"!BDN" + bytes(self.rng.getrandbits(8) for _ in range(2000)),
                 age_days=65, note="PST signature over garbage", expect_analyzer_failure=True)
        self.add("Malformed/empty.msg", b"", age_days=66, note="zero-byte Outlook message")

    def _edge_cases(self):
        self.add("Edge/empty.txt", b"", age_days=10, note="zero bytes")
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
            self.case("A-014", construction="G", classification="DEFECT",
                      expected={"file_state.state": "present", "row_count": 2, "not_grouped": True},
                      setup=["icacls <dir> /grant <user>:(F)", "fsutil file setCaseSensitiveInfo <dir> enable"],
                      notes="the engine keeps one row for the pair (name of the first, size of the second); "
                            "the walk counts two. file_path is unique on a lower-cased relative_path_key")
            for twin in ("Y-009", "Y-053"):
                self.case(twin, construction="G", classification="DEFECT", expected={"row_count": 2},
                          notes="the same two files as A-014")
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
