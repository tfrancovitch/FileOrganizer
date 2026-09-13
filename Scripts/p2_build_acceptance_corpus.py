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

Usage:
    python Scripts/p2_build_acceptance_corpus.py [--root C:\FOTest] [--samples C:\FOTest\Samples]
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
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20260908
NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

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


def make_7z(entries: list[tuple[str, bytes]]) -> bytes | None:
    try:
        import py7zr
    except ImportError:
        return None
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as archive:
        for name, data in entries:
            archive.writef(io.BytesIO(data), name)
    return buf.getvalue()


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
    return buf.getvalue()


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
    def __init__(self, root: Path, samples: Path | None = None):
        self.root = root
        self.samples = samples
        self.files: list[dict] = []
        self.rng = random.Random(SEED)
        self.skipped: list[str] = []          # what could not be made here, and why

    def add(self, relpath: str, data: bytes, *, age_days: int, note: str = "",
            expect_analyzer_failure: bool = False):
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        stamp = NOW - timedelta(days=age_days)
        ts = stamp.timestamp()
        os.utime(path, (ts, ts))
        self.files.append({
            "relative_path": relpath.replace("/", "\\"),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "extension": Path(relpath).suffix.lower(),
            "modified_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_days": age_days,
            "note": note,
            "expect_analyzer_failure": expect_analyzer_failure,
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
            age_days=20, note="FTS marker: no extension")
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
        self.add("Scans/scanned_only.pdf", buf.getvalue(), age_days=703, note="FTS marker: image-only PDF, OCR only")
        bad = degrade(render_page(["BAD SCAN", "", "A page no reader should trust without a look."]))
        buf = io.BytesIO()
        bad.save(buf, format="PDF", resolution=200.0)
        self.add("Scans/scan_bad.pdf", buf.getvalue(), age_days=704, note="OCR review expected: image-only PDF, degraded")
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
            self.add("Scans/scan_pages.pdf", out.getvalue(), age_days=705, note="FTS marker: text page + scanned page")
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
        self.add("Edge/no_extension", b"payload without an extension\n", age_days=11)
        self.add("Edge/\u00fcnicode_n\u00e4me_\u65e5\u672c\u8a9e.txt",
                 "Unicode filename content.\n".encode("utf-8"), age_days=12,
                 note="non-ASCII filename")
        self.add("Edge/spaces and (parens) [brackets].txt", b"awkward name\n", age_days=13)
        self.add("Edge/" + "/".join(f"level{i}" for i in range(1, 9)) + "/deep.txt",
                 b"eight levels down\n", age_days=14, note="deep nesting")
        self.add("Edge/trailing.dot.multiple.suffixes.tar.gz",
                 make_gzip(b"compressed payload\n"), age_days=300)
        self.add("Edge/UPPERCASE.TXT", b"uppercase extension\n", age_days=16)

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
            "files": sorted(self.files, key=lambda f: f["relative_path"]),
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=r"C:\FOTest",
                    help="directory holding Corpus\\ and GROUND_TRUTH.json (Corpus is recreated)")
    ap.add_argument("--samples", default=None,
                    help="folder of third-party samples to copy in (default: <root>\\Samples)")
    args = ap.parse_args()

    base = Path(args.root)
    corpus_dir = base / "Corpus"
    if corpus_dir.exists():
        shutil.rmtree(corpus_dir)
    corpus_dir.mkdir(parents=True)
    samples = Path(args.samples) if args.samples else base / "Samples"

    corpus = Corpus(corpus_dir, samples)
    corpus.build()
    truth = corpus.ground_truth()

    truth_path = base / "GROUND_TRUTH.json"
    truth_path.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")

    t = truth["totals"]
    d = truth["duplicates"]
    print(f"corpus            : {corpus_dir}")
    print(f"ground truth      : {truth_path}")
    print(f"files             : {t['file_count']:,}")
    print(f"logical bytes     : {t['logical_bytes']:,}")
    print(f"extensions        : {t['distinct_extensions']}")
    print(f"duplicate groups  : {d['group_count']}")
    print(f"reclaimable bytes : {d['total_reclaimable_bytes']:,}")
    print(f"analyzer failures : {len(truth['expected_analyzer_failures'])} expected")
    print(f"older than 5y     : {truth['age']['modified_before_cutoff']}")
    for line in truth["skipped"]:
        print(f"not made          : {line}")


if __name__ == "__main__":
    main()
