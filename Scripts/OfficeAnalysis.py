#!/usr/bin/env python3
"""
OfficeAnalysis.py
Part of: The File Organizer
Version: 1.5.0

Extracts document properties from Office files selected by the database-backed analyzer engine:
title, author, created/modified dates, and a type-appropriate content
count (word count for Word, sheet count for Excel, slide count for
PowerPoint).

Modern Office Open XML files (.docx, .xlsx, .pptx) get full metadata via
python-docx/openpyxl/python-pptx, same as before.

Legacy binary formats (.doc, .xls, .ppt) get METADATA ONLY. Tries olefile
parsing the OLE2 Compound Document structure first (the expected format
for a genuine legacy Office file); if that specifically fails because
the file isn't OLE2 at all, falls back to checking for RTF -- confirmed
via direct diagnosis against real data that this is NOT a rare edge
case: 947 of 958 "OLE2 parse failures" in one real test turned out to be
RTF content saved with a .doc extension, a well-known pattern from
older/cross-platform word processors.

Regardless of which format is actually parsed, no content extraction,
no file conversion, no derivative files created from originals. This is
a deliberate scope decision: this program is built around litigation-
discovery preservation, and creating a converted copy of a document
(even a temporary, carefully-staged one) is out of scope for it. A
separate program handles bulk format conversion for personal
convenience; that is not this one.

Every legacy-file row is explicitly tagged ExtractionMode=MetadataOnly
(vs. Full for modern files) -- both in the CSV and in the report's type
breakdown -- so it's clear at a glance why a .doc file has less data
than a .docx sitting next to it, not something to wonder about later.

1.4.0 (B17): whether the file carries a digital signature (SignatureState:
signed | unsigned | not_applicable | unknown). A modern package is signed
when it holds an _xmlsignatures/ part or a digital-signature origin
relationship; a legacy OLE2 file when it holds a _signatures,
\\x05DigitalSignature or _xmlsignatures stream. RTF saved as .doc has no
signature scheme: not_applicable. Presence only -- never validated.

1.5.0 (B18): whether the file is encrypted (EncryptionState: encrypted |
not_encrypted | unknown | not_applicable), a structured field instead of
error wording. A modern package that opens is not encrypted; one saved with a
password to open is refused (PasswordProtectedError) and its error row still
says encrypted. A legacy OLE2 file is judged from its header structures only,
never its content: Word's FIB fEncrypted bit (FibBase, 0x0100), Excel's
FILEPASS record (0x002F) after the first BOF of the Workbook / Book stream,
PowerPoint's EncryptedSummary stream, CurrentUserAtom header token and
UserEditAtom encryption-session reference. Every read is bounded and walks
only the sectors it needs; a structure that cannot be read is unknown, never
a crash. RTF saved as .doc has no encryption scheme: not_applicable.

Requires:
    pip install python-docx openpyxl python-pptx olefile

"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "Database"))
import fo_text
from file_organizer_common import to_long_path, PasswordProtectedError

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
    import olefile
except ImportError:
    print("ERROR: olefile is not installed. Run: pip install olefile", file=sys.stderr)
    sys.exit(1)

MODERN_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
LEGACY_EXTENSIONS = {".doc", ".xls", ".ppt"}
EXTENSIONS = MODERN_EXTENSIONS | LEGACY_EXTENSIONS
CHECKPOINT_FIELDS = ["Key", "OfficeType", "ExtractionMode", "Title", "Author", "Created", "Modified", "ContentCount",
                     "SignatureState", "EncryptionState", "Error"]

#: What marks a signed file (B17), by container. Names only; nothing is verified.
OOXML_SIGNATURE_PREFIX = "_xmlsignatures/"
OOXML_SIGNATURE_ORIGIN = "http://schemas.openxmlformats.org/package/2006/relationships/digital-signature/origin"
OLE_SIGNATURE_STREAMS = {"_signatures", "\x05digitalsignature", "_xmlsignatures"}

LEGACY_TYPE_LABELS = {".doc": "Word (legacy)", ".xls": "Excel (legacy)", ".ppt": "PowerPoint (legacy)"}


def analyze_docx(path):
    d = docx.Document(to_long_path(path))
    props = d.core_properties
    # B6: counted per paragraph without materialising tokens. Same
    # defect as B5-E.F009, same fix -- a long .docx has as many words
    # as a long .txt and there is no reason to allocate them all.
    word_count = sum(fo_text.count_words(p.text) for p in d.paragraphs)
    return {
        "OfficeType": "Word",
        "ExtractionMode": "Full",
        "Title": props.title or "",
        "Author": props.author or "",
        "Created": str(props.created) if props.created else "",
        "Modified": str(props.modified) if props.modified else "",
        "ContentCount": str(word_count),
    }


def analyze_xlsx(path):
    wb = openpyxl.load_workbook(to_long_path(path), read_only=True, data_only=True)
    props = wb.properties
    return {
        "OfficeType": "Excel",
        "ExtractionMode": "Full",
        "Title": props.title or "",
        "Author": props.creator or "",
        "Created": str(props.created) if props.created else "",
        "Modified": str(props.modified) if props.modified else "",
        "ContentCount": str(len(wb.sheetnames)),
    }


def analyze_pptx(path):
    prs = Presentation(to_long_path(path))
    props = prs.core_properties
    return {
        "OfficeType": "PowerPoint",
        "ExtractionMode": "Full",
        "Title": props.title or "",
        "Author": props.author or "",
        "Created": str(props.created) if props.created else "",
        "Modified": str(props.modified) if props.modified else "",
        "ContentCount": str(len(prs.slides)),
    }


def _decode_ole_text(value, codepage):
    """olefile can return metadata text fields (title, author, etc.) as
    raw bytes rather than decoded str -- these are legacy files, not
    always UTF-8 internally. Decode using the file's own codepage
    (e.g. 1252 for Windows-1252/Western European) rather than showing a
    raw bytes repr like "b'Name'". Falls back safely if the codepage is
    missing or the encoding turns out to be wrong."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode(f"cp{codepage}" if codepage else "cp1252")
        except (UnicodeDecodeError, LookupError):
            return value.decode("cp1252", errors="replace")
    return str(value)


RTF_INFO_READ_CAP = 65536  # 64KB -- the \info block appears near the top
                            # of the file; matches the project's existing
                            # 64KB partial-read convention elsewhere.


def _rtf_info_field(rtf_text, field_name):
    """Extract a simple text field like {\\title ...} or {\\author ...}
    from an RTF \\info group. Returns "" if not present -- not every RTF
    file includes one."""
    pattern = r"\{\\" + field_name + r"\s?(.*?)\}"
    match = re.search(pattern, rtf_text, re.DOTALL)
    if not match:
        return ""
    raw = match.group(1)
    raw = re.sub(r"\\[a-z]+-?\d*\s?", " ", raw)  # strip inline RTF control words
    return raw.replace("\\", "").strip()


def _rtf_date_field(rtf_text, field_name):
    """Extract a date field like {\\creatim\\yr2010\\mo5\\dy15\\hr14\\min30}."""
    pattern = r"\\" + field_name + r"\\yr(\d+)\\mo(\d+)\\dy(\d+)(?:\\hr(\d+))?(?:\\min(\d+))?"
    match = re.search(pattern, rtf_text)
    if not match:
        return ""
    yr, mo, dy, hr, mn = match.groups()
    try:
        return f"{int(yr):04d}-{int(mo):02d}-{int(dy):02d} {int(hr or 0):02d}:{int(mn or 0):02d}:00"
    except ValueError:
        return ""


def analyze_rtf(path):
    """Metadata only, via lightweight regex parsing of RTF's optional
    \\info group. RTF is a plain-text markup format, not OLE2 -- a
    genuinely different extraction path, not a variant of the OLE2 one.
    Only reads the first RTF_INFO_READ_CAP bytes, since the \\info block
    appears near the top of the file, not scattered through a
    potentially large document body."""
    long_path = to_long_path(path)
    with open(long_path, "rb") as f:
        raw_bytes = f.read(RTF_INFO_READ_CAP)
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="replace")

    return {
        "OfficeType": "RTF (legacy)",
        "ExtractionMode": "MetadataOnly",
        "Title": _rtf_info_field(text, "title"),
        "Author": _rtf_info_field(text, "author"),
        "Created": _rtf_date_field(text, "creatim"),
        "Modified": _rtf_date_field(text, "revtim"),
        "ContentCount": "",
    }


def analyze_legacy(path):
    """Metadata only. Tries OLE2 first (the expected format for a
    genuine legacy Office file), and if that specifically fails because
    the file isn't OLE2 at all, checks for RTF's signature before giving
    up -- confirmed via direct diagnosis against real data that this is
    NOT a rare edge case: 947 of 958 "OLE2 parse failures" in one real
    test turned out to be RTF content saved with a .doc extension, a
    well-known pattern from older/cross-platform word processors. See
    module docstring for why this is a deliberate scope boundary
    (metadata only, no conversion) either way."""
    ext = Path(path).suffix.lower()
    long_path = to_long_path(path)

    try:
        ole = olefile.OleFileIO(long_path)
    except olefile.olefile.NotOleFileError:
        # Genuinely not OLE2 -- check the confirmed-common alternative
        # before treating this as an unrecoverable error.
        with open(long_path, "rb") as f:
            header = f.read(8)
        if header.startswith(b"{\\rtf"):
            return analyze_rtf(path)
        # Not OLE2, not RTF either -- re-raise so it's still recorded as
        # a real error (Logs\OfficeInventory.errors.txt), not silently
        # swallowed. Worth running Diagnose-LegacyOfficeFiles.py again if
        # this category turns out to be large.
        raise

    try:
        meta = ole.get_metadata()
        codepage = meta.codepage
        # num_words is populated for legacy Word docs; slides for legacy
        # PowerPoint. Legacy Excel doesn't reliably expose a sheet count
        # via this metadata alone -- left blank rather than guessed.
        content_count = ""
        if ext == ".doc" and meta.num_words is not None:
            content_count = str(meta.num_words)
        elif ext == ".ppt" and meta.slides is not None:
            content_count = str(meta.slides)
        return {
            "OfficeType": LEGACY_TYPE_LABELS[ext],
            "ExtractionMode": "MetadataOnly",
            "Title": _decode_ole_text(meta.title, codepage),
            "Author": _decode_ole_text(meta.author, codepage),
            "Created": str(meta.create_time) if meta.create_time else "",
            "Modified": str(meta.last_saved_time) if meta.last_saved_time else "",
            "ContentCount": content_count,
        }
    finally:
        ole.close()


def _encrypted_package(path):
    """True for a .docx/.xlsx/.pptx saved with a password to open: Office
    wraps the encrypted ZIP package in an OLE2 container holding the
    EncryptionInfo and EncryptedPackage streams. Named, never opened."""
    long_path = to_long_path(path)
    with open(long_path, "rb") as f:
        if f.read(8) != bytes.fromhex("d0cf11e0a1b11ae1"):
            return False
    ole = olefile.OleFileIO(long_path)
    try:
        names = {"/".join(entry).lower() for entry in ole.listdir(streams=True, storages=False)}
    finally:
        ole.close()
    return "encryptedpackage" in names and "encryptioninfo" in names


def ooxml_signature_state(path):
    """signed | unsigned | unknown for a readable .docx/.xlsx/.pptx package."""
    import zipfile
    try:
        with zipfile.ZipFile(to_long_path(path)) as z:
            names = [n.replace("\\", "/").lower() for n in z.namelist()]
            if any(n.startswith(OOXML_SIGNATURE_PREFIX) for n in names):
                return "signed"
            if "_rels/.rels" in names and OOXML_SIGNATURE_ORIGIN.encode() in z.read(
                    next(n for n in z.namelist() if n.replace("\\", "/").lower() == "_rels/.rels")):
                return "signed"
        return "unsigned"
    except Exception:                                           # noqa: BLE001
        return "unknown"


def ole_signature_state(path):
    """signed | unsigned | not_applicable (RTF saved as .doc) | unknown."""
    long_path = to_long_path(path)
    try:
        with open(long_path, "rb") as f:
            header = f.read(8)
        if header.startswith(b"{\\rtf"):
            return "not_applicable"
        if header != bytes.fromhex("d0cf11e0a1b11ae1"):
            return "unknown"
        ole = olefile.OleFileIO(long_path)
        try:
            names = {entry[-1].lower() for entry in ole.listdir(streams=True, storages=True)}
        finally:
            ole.close()
        return "signed" if names & OLE_SIGNATURE_STREAMS else "unsigned"
    except Exception:                                           # noqa: BLE001
        return "unknown"


# -- legacy OLE2 encryption (B18) ------------------------------------------------
#
# Header structures only. olefile parses the compound file's header, FAT and
# directory (structure, not content); the few bytes each check needs are then
# read by walking the stream's own sector chain to exactly the sectors that
# hold them -- a stream is never loaded whole, and no recorded size is
# trusted beyond the chain that backs it.

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_CHAIN_END = {0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFC}   # end, free, FAT sector, DIFAT sector
OLE_READ_CAP = 64 * 1024                                       # the most bytes one read may ask for
WORD_IDENTS = {0xA5EC, 0xA5DC}                                 # FibBase.wIdent: Word 97+ / Word 6-95
WORD_F_ENCRYPTED = 0x0100                                      # FibBase flags (offset 0x0A): fEncrypted
XLS_BOFS = {0x0809, 0x0409, 0x0209, 0x0009}
XLS_FILEPASS, XLS_WRITEPROTECT = 0x002F, 0x0086
XLS_RECORD_CAP = 4                                             # FILEPASS is the 2nd, or 3rd after WRITEPROTECT
OLE_ENCRYPTED_STREAMS = ("\tDRMContent", "\tDRMViewerContent", "EncryptedPackage")   # IRM content; an encrypted package
PPT_CURRENT_USER_ATOM, PPT_USER_EDIT_ATOM = 0x0FF6, 0x0FF5
PPT_TOKEN_PLAIN, PPT_TOKEN_ENCRYPTED = 0xE391C05F, 0xF3D1C4DF
PPT_USER_EDIT_PLAIN, PPT_USER_EDIT_ENCRYPTED = 0x1C, 0x20      # UserEditAtom length without / with encryptSessionPersistIdRef


def _ole_chain_read(fat, start, sector_size, offset, length, fetch):
    """`length` bytes from `offset` of the stream whose chain starts at
    `start`, reading only the sectors that hold them. None when the chain
    ends early, loops, or leaves the table."""
    out = bytearray()
    sid, index, first = start, 0, offset // sector_size
    seen = set()
    for _step in range(len(fat) + 1):
        if len(out) >= length:
            return bytes(out)
        if sid in _CHAIN_END or not (0 <= sid < len(fat)) or sid in seen:
            return None                     # ended early, left the table, or came back to a sector: a loop
        seen.add(sid)
        if index >= first:
            within = offset - index * sector_size if index == first else 0
            n = min(sector_size - within, length - len(out))
            chunk = fetch(sid, within, n)
            if chunk is None or len(chunk) < n:
                return None
            out += chunk
        sid, index = fat[sid], index + 1
    return bytes(out) if len(out) >= length else None


def ole_read(ole, name, offset, length):
    """At most `length` bytes (never more than OLE_READ_CAP) of stream `name`
    from `offset`; None when the stream is absent, the range lies outside
    its recorded size, or the sectors behind it cannot be read."""
    try:
        sid = ole._find(name)
    except (IOError, OSError, ValueError):
        return None
    entry = ole.direntries[sid]
    if entry is None or entry.entry_type != olefile.STGTY_STREAM or offset < 0 or offset >= entry.size:
        return None
    length = min(length, entry.size - offset, OLE_READ_CAP)
    fp, sector_size = ole.fp, ole.sectorsize

    def regular(sect, within, n):
        fp.seek(sector_size * (sect + 1) + within)
        return fp.read(n)
    if entry.size >= ole.minisectorcutoff:
        return _ole_chain_read(ole.fat, entry.isectStart, sector_size, offset, length, regular)
    if ole.minifat is None:
        ole.loadminifat()
    root = ole.root

    def mini(sect, within, n):
        at = sect * ole.minisectorsize + within
        if at + n > root.size:
            return None
        return _ole_chain_read(ole.fat, root.isectStart, sector_size, at, n, regular)
    return _ole_chain_read(ole.minifat, entry.isectStart, ole.minisectorsize, offset, length, mini)


def _u16(b, at):
    return int.from_bytes(b[at:at + 2], "little")


def _u32(b, at):
    return int.from_bytes(b[at:at + 4], "little")


def word_encryption_state(ole):
    """Word: FibBase at the start of the WordDocument stream -- wIdent
    (0xA5EC, or 0xA5DC for Word 6/95), then the flags word at offset 0x0A,
    whose fEncrypted bit is 0x0100 (set for RC4 and XOR obfuscation alike)."""
    head = ole_read(ole, "WordDocument", 0, 12)
    if head is None or len(head) < 12 or _u16(head, 0) not in WORD_IDENTS:
        return "unknown"
    return "encrypted" if _u16(head, 0x0A) & WORD_F_ENCRYPTED else "not_encrypted"


def excel_encryption_state(ole, stream):
    """Excel: the workbook-globals substream starts with BOF; when the
    workbook is encrypted a FILEPASS record (0x002F) comes next, after an
    optional WRITEPROTECT. Record headers stay plain even then."""
    first = ole_read(ole, stream, 0, 4)
    if first is None or len(first) < 4 or _u16(first, 0) not in XLS_BOFS:
        return "unknown"
    at = 4 + _u16(first, 2)
    for _n in range(XLS_RECORD_CAP):
        head = ole_read(ole, stream, at, 4)
        if head is None or len(head) < 4:
            return "unknown"
        kind = _u16(head, 0)
        if kind == XLS_FILEPASS:
            return "encrypted"
        if kind != XLS_WRITEPROTECT:
            return "not_encrypted"          # the globals went on without a FILEPASS
        at += 4 + _u16(head, 2)
    return "unknown"


def powerpoint_encryption_state(ole):
    """PowerPoint: an EncryptedSummary stream; the CurrentUserAtom's header
    token (0xF3D1C4DF encrypted, 0xE391C05F not); and the UserEditAtom that
    atom points to, which carries encryptSessionPersistIdRef -- the
    persist-directory reference to the CryptSession10Container -- exactly
    when the document is encrypted."""
    if ole.exists("EncryptedSummary"):
        return "encrypted"
    cu = ole_read(ole, "Current User", 0, 20)
    if cu is None or len(cu) < 20 or _u16(cu, 2) != PPT_CURRENT_USER_ATOM:
        return "unknown"
    token, edit_at = _u32(cu, 12), _u32(cu, 16)
    if token == PPT_TOKEN_ENCRYPTED:
        return "encrypted"
    edit = ole_read(ole, "PowerPoint Document", edit_at, 8)
    if edit is None or len(edit) < 8 or _u16(edit, 2) != PPT_USER_EDIT_ATOM:
        return "unknown"
    length = _u32(edit, 4)
    if length == PPT_USER_EDIT_ENCRYPTED:
        return "encrypted"
    if length == PPT_USER_EDIT_PLAIN and token == PPT_TOKEN_PLAIN:
        return "not_encrypted"
    return "unknown"


def ole_encryption_state(path):
    """encrypted | not_encrypted | not_applicable (RTF saved as .doc) |
    unknown, for a legacy .doc / .xls / .ppt. The check follows the streams
    the file holds, not its extension. Never raises."""
    long_path = to_long_path(path)
    try:
        with open(long_path, "rb") as f:
            header = f.read(8)
        if header.startswith(b"{\\rtf"):
            return "not_applicable"
        if header != OLE_MAGIC:
            return "unknown"
        ole = olefile.OleFileIO(long_path)
        try:
            # Rights-managed (IRM) content lives in an encrypted DRM stream,
            # and an encrypted OOXML package saved under a legacy name is an
            # EncryptedPackage stream: either is encrypted, whatever else the
            # file holds.
            if any(ole.exists(n) for n in OLE_ENCRYPTED_STREAMS):
                return "encrypted"
            if ole.exists("WordDocument"):
                return word_encryption_state(ole)
            for stream in ("Workbook", "Book"):
                if ole.exists(stream):
                    return excel_encryption_state(ole, stream)
            if ole.exists("PowerPoint Document") or ole.exists("Current User"):
                return powerpoint_encryption_state(ole)
            return "unknown"
        finally:
            ole.close()
    except Exception:                                           # noqa: BLE001
        return "unknown"


def analyze_office(path):
    ext = Path(path).suffix.lower()
    if ext in MODERN_EXTENSIONS and _encrypted_package(path):
        raise PasswordProtectedError("password-protected document (an encrypted Office package; no password is tried)")
    if ext == ".docx":
        result = analyze_docx(path)
    elif ext == ".xlsx":
        result = analyze_xlsx(path)
    elif ext == ".pptx":
        result = analyze_pptx(path)
    elif ext in LEGACY_EXTENSIONS:
        result = analyze_legacy(path)
    else:
        raise ValueError(f"Unsupported extension: {ext}")
    result["SignatureState"] = ooxml_signature_state(path) if ext in MODERN_EXTENSIONS else ole_signature_state(path)
    # (B18) A modern package that opened is not encrypted -- a password-to-open
    # package was refused above, before it was read.
    result["EncryptionState"] = ("not_encrypted" if ext in MODERN_EXTENSIONS else
                                 "not_applicable" if result.get("OfficeType") == "RTF (legacy)" else
                                 ole_encryption_state(path))
    return result


def report_extra(results):
    by_type = {}
    metadata_only_count = 0
    for r in results:
        t = r.get("OfficeType") or "Unknown"
        by_type[t] = by_type.get(t, 0) + 1
        if r.get("ExtractionMode") == "MetadataOnly":
            metadata_only_count += 1
    lines = ["  By type:"]
    for t, c in sorted(by_type.items()):
        lines.append(f"    {t:<20}: {c}")
    if metadata_only_count:
        lines.append("")
        lines.append(f"  Metadata-only (legacy format, no content extraction): {metadata_only_count}")
        lines.append("  (No conversion or derivative files created -- see module docstring.)")
    return lines
