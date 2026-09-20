#!/usr/bin/env python3
"""
ContentExtraction.py
Part of: The File Organizer
Version: 1.1.1

Extracts the actual TEXT CONTENT of every content-bearing document in
the database-backed analyzer engine -- PDF, Word (.docx and .doc), PowerPoint
(.pptx and .ppt), Excel (.xlsx and .xls), RTF, HTML, CSV, JSON, XML, logs,
vCards, calendars, plain text (.txt), Markdown (.md), files with no
extension, and email in every shape (.eml, .mbox, .mht, Outlook .msg and
.pst/.ost) -- into individual .txt files, indexed by DB_ID.

A file is read as what its bytes say it is, not as what its name says: a
".doc" that is really RTF is read as RTF, a ".xls" that is really an HTML
export is read as HTML, and a document that is plain text under a document
extension is read as text. The recorded SourceType says which reader ran.

This is the last Phase 1 (data-gathering) gap: earlier scripts captured
metadata ABOUT documents (author, page count); this captures what they
actually SAY, which is what a future content-based organization/search/
comparison pass (Phase 2) will need to work with.

Output:
    ExtractedText/<hash>.txt  -- one file per document, full extracted text
    ContentIndex.csv          -- DB_ID, FileName, Path, SourceType,
                                 ExtractedTextFile, CharCount, WordCount, Error

    The supported product runtime stores extracted text by the SHA-256 of
    the extracted UTF-8 text, sharded by hash prefix. Identical extracted
    text therefore reuses one artifact. RunCoordinator uses the in-process analyzer engine and persists results
    directly to SQLite.

Requires:
    pip install pdfplumber python-docx openpyxl python-pptx chardet
Optional:
    pypdfium2 (installed with pdfplumber) -- reads PDF text 25-30x faster;
              pdfplumber is the fallback for any file it refuses
    xlrd      -- .xls text; without it an .xls records an error naming it

"""

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "Database"))
from file_organizer_common import to_long_path
import fo_text
import fo_extractors

try:
    import pdfplumber
except ImportError:
    print("ERROR: pdfplumber is not installed. Run: pip install pdfplumber", file=sys.stderr)
    sys.exit(1)

try:
    import pypdfium2 as pdfium
except ImportError:                                            # pragma: no cover
    pdfium = None

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

#: Source code and scripts: text, read as text, named for what they are.
CODE_EXTENSIONS = {".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".less",
                   ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".sql", ".c", ".h",
                   ".cpp", ".hpp", ".cc", ".cs", ".java", ".go", ".rs", ".rb", ".php", ".pl", ".pm", ".r",
                   ".swift", ".kt", ".kts", ".m", ".lua", ".vb", ".vbs", ".gradle", ".cmake"}
#: Configuration: also text.
CONFIG_EXTENSIONS = {".yml", ".yaml", ".ini", ".cfg", ".conf", ".toml", ".properties", ".env", ".reg"}
#: Office Open XML variants the standard libraries may refuse; read raw.
OOXML_EXTENSIONS = {".docm", ".dotx", ".dotm", ".xlsm", ".xltx", ".xltm", ".pptm", ".potx", ".potm",
                    ".ppsx", ".ppsm"}
#: Pictures: read by OCR when they look like documents (a scanned letter,
#: a photographed page), skipped as photographs when they do not.
PICTURE_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".jfif", ".bmp", ".gif", ".webp", ".heic", ".heif"}

EXTENSIONS = ({".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md",
               ".doc", ".ppt", ".xls", ".rtf", ".html", ".htm", ".csv", ".json",
               ".xml", ".log", ".vcf", ".ics", "",
               ".eml", ".mbox", ".mht", ".mhtml", ".msg", ".pst", ".ost",
               ".odt", ".ods", ".odp", ".odg", ".epub", ".zip", ".7z", ".wpd", ".wp", ".one",
               ".srt", ".vtt", ".rst", ".tex"}
              | CODE_EXTENSIONS | CONFIG_EXTENSIONS | OOXML_EXTENSIONS | PICTURE_EXTENSIONS)

#: What a text-like file is called by its extension. Anything else that
#: sniffs as text is "PlainText" -- including a .doc that turns out to be one.
TEXT_SOURCE_TYPES = {".csv": "CSV", ".json": "JSON", ".xml": "XML", ".log": "Log",
                     ".vcf": "vCard", ".ics": "iCalendar", ".srt": "Subtitles", ".vtt": "Subtitles"}
TEXT_SOURCE_TYPES.update({ext: "Source code" for ext in CODE_EXTENSIONS})
TEXT_SOURCE_TYPES.update({ext: "Configuration" for ext in CONFIG_EXTENSIONS})

#: Email formats are text (or OLE2) underneath, so the sniff cannot tell an
#: .eml from a .txt or an .mht from an .html; for these the name decides.
EMAIL_EXTENSIONS = {".eml": "Email (EML)", ".mbox": "Mailbox (MBOX)",
                    ".mht": "Web archive (MHTML)", ".mhtml": "Web archive (MHTML)"}

#: The engine's vocabulary for "this file was not a document at all": an
#: extensionless file that is a picture or a program. Recorded as not
#: processed rather than as an error, because nothing went wrong with it.
NOT_A_DOCUMENT = "NotProcessed"

#: What a run may leave out, from the project's settings (Options page):
#: OCR of pages with no text layer, pictures, the documents inside archives.
#: Each is on unless turned off; a file left out is recorded as not processed.
DEFAULT_OPTIONS = {"ocr": True, "pictures": True, "archives": True}
OPTIONS = dict(DEFAULT_OPTIONS)


def set_options(options):
    """Apply a run's extraction options (a dict with any of ocr / pictures / archives)."""
    OPTIONS.clear()
    OPTIONS.update(DEFAULT_OPTIONS)
    for key, value in (options or {}).items():
        if key in DEFAULT_OPTIONS:
            OPTIONS[key] = bool(value)
CHECKPOINT_FIELDS = ["Key", "SourceType", "ExtractedTextFile", "TextSha256",
                     "ReusedExisting", "CharCount", "WordCount", "Error"]


#: Extracted artifacts are sharded two levels deep by the first four
#: hex characters of their content hash: <ab>/<cd>/<full>.txt.
#:
#: B5-E.F008 flagged the flat directory as a real scalability problem
#: -- a large project produced one folder with hundreds of thousands of
#: entries in it, which NTFS handles poorly and Explorer handles worse.
#: Two levels of 256 gives 65,536 buckets, so a million extracted
#: documents average ~15 files per directory.
SHARD_DEPTH = 2


def content_to_relpath(text_sha256):
    r"""
ContentExtraction.py
Part of: The File Organizer B6.1

Per-file content extraction implementation used by the database-backed
in-process analyzer engine.

Supported document text is extracted into a content-addressed, two-level
sharded store keyed by SHA-256 of the extracted UTF-8 text. Identical extracted
text from different source paths reuses one artifact. The analyzer returns the
artifact reference, content hash, encoding/counts and error state; SQLite
persistence is owned by the analyzer runtime, not by this module.

Plain-text decoding is BOM-aware and tries strict UTF-8 before probabilistic
encoding detection. This prevents valid UTF-8 from being silently converted to
a plausible but incorrect legacy encoding.

Requires as applicable: pdfplumber, python-docx, openpyxl, python-pptx, chardet.
"""
    digest = text_sha256
    parts = [digest[i * 2:(i + 1) * 2] for i in range(SHARD_DEPTH)]
    return "/".join(parts + ["%s.txt" % digest])


def path_to_filename(path):
    """B4.5's path-addressed name. RETAINED for `storage_mode` fallback.

    Still reachable when a project sets `extraction.storage_mode` to
    'path_addressed', which exists so an operator upgrading mid-project
    is not forced to re-extract everything at once. New projects get
    content addressing, which is the default in migration 006.
    """
    h = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{h}.txt"


def _pdf_text_pdfium(long_path):
    """Every page's text through PDFium. Measured on the real corpus at 4-13
    ms per page against pdfplumber's 125-190: the same words, 25-30x sooner."""
    doc = pdfium.PdfDocument(long_path)
    try:
        parts = []
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    parts.append(textpage.get_text_bounded() or "")
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        doc.close()
    return "\n\n".join(part.replace("\r\n", "\n").replace("\r", "\n") for part in parts)


def _pdf_text_and_ocr(long_path):
    """Every page's text through PDFium; pages with no text layer read by
    OCR when the engine is available. Returns (text, fields, ocr_pages)."""
    import fo_ocr
    doc = pdfium.PdfDocument(long_path)
    try:
        parts = []
        blank = []
        for index in range(len(doc)):
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_bounded() or ""
                finally:
                    textpage.close()
            finally:
                page.close()
            parts.append(text)
            if len(text.strip()) < fo_ocr.MIN_TEXT_CHARS:
                blank.append(index)
        fields = {}
        if blank and OPTIONS.get("ocr", True) and fo_ocr.available():
            texts, reports = fo_ocr.ocr_pdf_pages(doc, blank)
            for index, text in texts.items():
                parts[index] = text
            fields = fo_ocr.summarize(reports)
    finally:
        doc.close()
    joined = "\n\n".join(part.replace("\r\n", "\n").replace("\r", "\n") for part in parts)
    return joined, fields, (len(blank) if fields else 0)


def extract_pdf(path):
    """(SourceType, text, fields) for a PDF: PDFium with OCR for pages that
    carry no text, pdfplumber as the fallback for a file PDFium refuses."""
    long_path = to_long_path(path)
    if pdfium is not None:
        try:
            text, fields, ocr_pages = _pdf_text_and_ocr(long_path)
            return ("PDF (OCR)" if ocr_pages else "PDF"), text, fields
        except Exception:                                      # noqa: BLE001
            pass            # pdfplumber gets the file; if it fails too, that is the error
    parts = []
    with pdfplumber.open(long_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "PDF", "\n\n".join(parts), {}


def extract_pdf_text(path):
    source, text, _fields = extract_pdf(path)
    return source, text


def extract_picture(path):
    """(SourceType, text, fields) for a picture file: OCR when it looks like
    a document, not processed when it looks like a photograph."""
    import fo_ocr
    from PIL import Image, ImageSequence
    if not (OPTIONS.get("pictures", True) and OPTIONS.get("ocr", True)):
        raise ValueError(NOT_A_DOCUMENT)          # pictures left out by the project's options
    if not fo_ocr.available():
        raise RuntimeError("OCR is not available (pip install winocr) -- pictures need it")
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception:                                          # noqa: BLE001
        pass
    with Image.open(to_long_path(path)) as img:
        try:
            img.draft("RGB", (1200, 1200))      # JPEG: decode small first, for the gate
        except Exception:                                      # noqa: BLE001
            pass
        gate = img.copy()
    if not fo_ocr.looks_like_document(gate):
        raise ValueError(NOT_A_DOCUMENT)
    with Image.open(to_long_path(path)) as img:
        dpi = fo_ocr.image_dpi(img)
        texts, reports = [], {}
        for index, frame in enumerate(ImageSequence.Iterator(img)):
            if index >= 500:
                break
            text, report = fo_ocr.ocr_image(frame.convert("RGB"), dpi)
            texts.append(text)
            reports[index] = report
    fields = fo_ocr.summarize(reports)
    return "Picture (OCR)", "\n\n".join(texts), fields


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
    try:
        for sheet in wb.worksheets:
            parts.append(f"--- Sheet: {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                line = "\t".join("" if v is None else str(v) for v in row)
                if line.strip():
                    parts.append(line)
    finally:
        # A read-only workbook keeps the file open until told otherwise; a
        # handle left on someone's file blocks its rename or sync.
        wb.close()
    return "Excel", "\n".join(parts)


def extract_plain_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    text, _encoding = fo_text.decode_bytes(raw)
    return "PlainText", text


def _member_extensions(depth):
    """What is worth reading out of an archive at this depth: documents, not
    the pictures (a game's zip of ten thousand textures is not a scan)."""
    exts = set(EXTENSIONS) - {""} - PICTURE_EXTENSIONS
    if depth + 1 >= fo_extractors.MAX_ARCHIVE_DEPTH:
        exts -= {".zip", ".7z"}
    return exts


def _member_reader(path, depth):
    """A member written to a temporary file -> its text."""
    _source, text, _fields = extract_document(path, depth)
    return text


def extract_document(path, depth=0):
    """(SourceType, text, fields) for a file, chosen by what the bytes are.

    The extension picks nothing except the name given to plain text -- and
    which email reader to use, since an .eml is text underneath; the
    signature picks the reader. So a ".doc" holding RTF is read as RTF,
    a ".xls" holding an HTML export is read as HTML, and a file that is
    really plain text is read as text whatever it is called. When the
    bytes are a container of the wrong kind for the name (a ".doc" that
    is a ZIP with word/ inside is a .docx) the container decides.

    `fields` carries what some readers know beyond the text: an email's
    Title (subject), Author (sender) and Created (date); a mailbox's
    message count.
    """
    import fo_email
    ext = Path(path).suffix.lower()
    kind = fo_extractors.sniff(path)
    if kind == "pdf":
        return extract_pdf(path)
    if kind == "picture":
        return extract_picture(path)
    if kind == "wordperfect":
        return "WordPerfect", fo_extractors.wpd_text(path), {}
    if kind == "onenote" or (ext == ".one" and kind == "binary"):
        return "OneNote (text scan)", fo_extractors.onenote_text(path), {}
    if kind == "7z":
        if not OPTIONS.get("archives", True):
            raise ValueError(NOT_A_DOCUMENT)      # archives left out by the project's options
        text, fields = fo_extractors.archive_text(path, depth, _member_reader, _member_extensions(depth))
        return "Archive (7z)", text, fields
    if kind == "pst":
        text, fields = fo_email.pst_text(path)
        return "Outlook mailbox (PST)", text, fields
    if kind == "rtf":
        return "RTF", fo_extractors.rtf_text(path), {}
    if ext in EMAIL_EXTENSIONS and kind in ("text", "html"):
        if ext == ".mbox":
            text, fields = fo_email.mbox_text(path)
        elif ext == ".eml":
            text, fields = fo_email.eml_text(path)
        else:
            text, fields = fo_email.mht_text(path)
        return EMAIL_EXTENSIONS[ext], text, fields
    if ext == ".xml" and kind in ("html", "text"):
        # XML keeps its element names: for a search they are as telling as
        # the values, and stripping them as HTML would lose them.
        _source, text = extract_plain_text(path)
        return "XML", text, {}
    if kind == "html":
        return "HTML", fo_extractors.html_text(path), {}
    if kind == "ole":
        inner = fo_extractors.ole_kind(path)
        if inner == "word":
            return "Word 97-2003", fo_extractors.doc_text(path), {}
        if inner == "powerpoint":
            return "PowerPoint 97-2003", fo_extractors.ppt_text(path), {}
        if inner == "excel":
            return "Excel 97-2003", fo_extractors.xls_text(path), {}
        if inner == "outlook":
            text, fields = fo_email.msg_text(path)
            return "Outlook message (MSG)", text, fields
        if inner == "encrypted":
            raise ValueError("password-protected document (an encrypted Office package; no password is tried)")
        raise ValueError("OLE container without a Word, PowerPoint, Excel or Outlook document inside")
    if kind == "zip":
        inner = fo_extractors.zip_kind(path)
        if inner in ("docx", "pptx", "xlsx"):
            reader = {"docx": extract_docx_text, "pptx": extract_pptx_text, "xlsx": extract_xlsx_text}[inner]
            if ext in OOXML_EXTENSIONS:
                source, text = fo_extractors.ooxml_text(path)
                return source + " (" + ext[1:] + ")", text, {}
            try:
                return reader(path) + ({},)
            except Exception:                                  # noqa: BLE001
                # The library refused a variant it does not know; the raw
                # package reader takes any Office Open XML.
                source, text = fo_extractors.ooxml_text(path)
                return source, text, {}
        odf = fo_extractors.odf_kind(path)
        if odf:
            return {"odt": "OpenDocument Text", "ods": "OpenDocument Spreadsheet",
                    "odp": "OpenDocument Presentation", "odg": "OpenDocument Drawing"}[odf], fo_extractors.odf_text(path), {}
        if inner == "epub" or ext == ".epub":
            text, fields = fo_extractors.epub_text(path)
            return "EPUB", text, fields
        if not OPTIONS.get("archives", True):
            raise ValueError(NOT_A_DOCUMENT)      # archives left out by the project's options
        text, fields = fo_extractors.archive_text(path, depth, _member_reader, _member_extensions(depth))
        return "Archive (zip)", text, fields
    if kind == "text":
        _source, text = extract_plain_text(path)
        return TEXT_SOURCE_TYPES.get(ext, "PlainText"), text, {}
    if kind == "empty":
        return "PlainText", "", {}
    if ext == "":
        # A picture, a program, a database with no extension: not a document,
        # and not a failure either.
        raise ValueError(NOT_A_DOCUMENT)
    raise ValueError("not a recognised document format (%s)" % kind)


def extract_any(path, depth=0):
    """(SourceType, text) -- extract_document without the fields."""
    source_type, text, _fields = extract_document(path, depth)
    return source_type, text


def make_analyze_fn(extract_folder, content_addressed=True, options=None):
    set_options(options)

    def analyze_content(path):
        ext = Path(path).suffix.lower()
        if ext not in EXTENSIONS:
            raise ValueError(f"Unsupported extension: {ext}")
        source_type, text, fields = extract_document(path)

        # Content addressing: the artifact is named for what is IN it.
        text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_addressed:
            relpath = content_to_relpath(text_sha)
        else:
            relpath = path_to_filename(path)

        target = extract_folder / relpath
        target.parent.mkdir(parents=True, exist_ok=True)

        # A hit here means another document already produced byte-for-byte
        # this text. Writing it again would produce an identical file, so
        # the write is skipped and the reuse is REPORTED rather than
        # hidden -- a consumer counting artifacts should be able to tell
        # deduplication from extraction failure.
        reused = target.exists()
        if not reused:
            # Written to a temporary neighbour and renamed, so a crash
            # mid-write cannot leave a truncated artifact sitting at a
            # content-addressed name that later runs will trust (B5-G).
            staging = target.with_suffix(".txt.partial")
            with open(staging, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(staging, target)

        # B6: counted, not materialised. len(text.split()) built a list
        # of every token to produce one integer -- B5-E.F009 measured an
        # 8 MB document costing ~110 MB of heap that way.
        out = {
            "SourceType": source_type,
            "ExtractedTextFile": relpath,
            "TextSha256": text_sha,
            "ReusedExisting": "True" if reused else "False",
            "CharCount": str(len(text)),
            "WordCount": str(fo_text.count_words(text)),
        }
        for key, value in (fields or {}).items():
            if value not in (None, ""):
                out[key] = str(value)
        return out
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
