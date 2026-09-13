r"""
fo_extractors.py
Part of: The File Organizer

Text readers for the formats ContentExtraction.py did not cover: RTF, HTML,
and the Word / PowerPoint / Excel 97-2003 binary formats -- plus the sniff
that tells what a file actually is, whatever its name says.

Why sniff: the real corpus had eleven ".doc" files that were not Word files
at all (the Office analyzer said "not an OLE2 structured storage file").
Routing by extension would fail them again; routing by the bytes reads them
as what they are -- RTF, HTML, plain text, or a misnamed .docx.

Word 97-2003 and PowerPoint 97-2003 are read here directly from the OLE2
container with olefile (already a dependency), following the MS-DOC piece
table and the MS-PPT record tree. They return the document's text, not its
formatting; that is all extraction needs. Excel 97-2003 goes through xlrd,
which is optional: without it an .xls file records an error naming the
package, and nothing else is affected.

Every reader takes a path and returns text, or raises with a reason that
becomes the file's recorded error. None of them writes anything.
"""

import io
import re
import struct
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import fo_text
from file_organizer_common import to_long_path

try:
    import olefile
except ImportError:                                            # pragma: no cover
    olefile = None

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: {7B5C52E4-D88C-4DA7-AEB1-5378D02996D3}, the OneNote 2010+ section header.
ONENOTE_GUID = bytes.fromhex("e4525c7b8cd8a74daeb15378d02996d3")


# ---------------------------------------------------------------------------
# What is this file, really?
# ---------------------------------------------------------------------------

def sniff(path):
    r"""One of: pdf, zip, ole, pst, 7z, wordperfect, onenote, picture, rtf,
    html, text, empty, binary.

    Reads the first 4 KB only. "text" means the bytes decode as text with
    almost nothing unprintable in them; "binary" means they do not, and no
    known signature was found.
    """
    with open(to_long_path(path), "rb") as f:
        head = f.read(4096)
    if not head:
        return "empty"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    if head.startswith(OLE_MAGIC):
        return "ole"
    if head.startswith(b"!BDN"):
        return "pst"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if head.startswith(b"\xffWPC"):
        return "wordperfect"
    if head.startswith(ONENOTE_GUID):
        return "onenote"
    if (head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
            or head[:4] == b"RIFF" and head[8:12] == b"WEBP"
            or head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypheif")):
        return "picture"
    if head.lstrip().startswith(b"{\\rtf"):
        return "rtf"
    lowered = head.lower()
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "html"
    tags = (b"<table", b"<tr", b"<td", b"<div", b"<span", b"<script", b"<body", b"<head",
            b"<br", b"<p>", b"<p ", b"<meta", b"<style", b"</")
    if lowered.lstrip().startswith(b"<") and any(t in lowered for t in tags):
        return "html"
    # Web "exports" often put a line of text before the markup begins.
    if sum(lowered.count(t) for t in tags) >= 5:
        return "html"
    if _looks_like_text(head):
        return "text"
    return "binary"


def _looks_like_text(raw):
    """Mostly printable after decoding: the test a person would apply."""
    if b"\x00" in raw and not (raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff")):
        return False
    try:
        text, _enc = fo_text.decode_bytes(raw)
    except Exception:                                          # noqa: BLE001
        return False
    if not text:
        return False
    bad = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t\f\v")
    return bad <= max(2, len(text) // 200)


def ole_kind(path):
    r"""For an OLE2 container: word, powerpoint, excel, outlook (a .msg), or
    None -- by its streams."""
    if olefile is None:
        raise RuntimeError("olefile is not installed")
    ole = olefile.OleFileIO(to_long_path(path))
    try:
        names = {"/".join(entry).lower() for entry in ole.listdir(streams=True, storages=False)}
        if "worddocument" in names:
            return "word"
        if "powerpoint document" in names:
            return "powerpoint"
        if "workbook" in names or "book" in names:
            return "excel"
        if "__properties_version1.0" in names:
            return "outlook"
        if "pp40" in names:
            raise RuntimeError("PowerPoint 4.0/95 presentations are not supported")
        return None
    finally:
        ole.close()


def zip_kind(path):
    r"""For a ZIP container: docx, pptx, xlsx, or None -- by its entries."""
    try:
        with zipfile.ZipFile(to_long_path(path)) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return None
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if "META-INF/container.xml" in names or "mimetype" in names and any(n.endswith(".opf") for n in names):
        return "epub"
    return None


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------

# Destinations whose content is not the document's text: tables of fonts,
# colours and styles, document info, pictures, field instructions, comments.
# Field RESULTS (\fldrslt) are deliberately not here: that is the visible
# text of a hyperlink. Footnotes, headers and footers are kept for the same
# reason -- they are words the person wrote.
_RTF_IGNORED = frozenset("""
aftncn aftnsep aftnsepc annotation atnauthor atndate atnicn atnid atnparent atnref atntime atrfend atrfstart
author background bkmkend bkmkstart blipuid buptim category colorschememapping colortbl comment company
creatim datafield datastore defchp defpap do doccomm docvar ebcend ebcstart factoidname falt
fchars ffdeftext ffentrymcr ffexitmcr ffformat ffhelptext ffl ffname ffstattext fldinst fldtype file filetbl
fname fontemb fontfile fonttbl formfield ftncn ftnsep ftnsepc g generator gridtbl hl hlfr hlloc hlsrc hsv
htmltag info keycode keywords latentstyles lchars levelnumbers leveltext lfolevel linkval list listlevel
listname listoverride listoverridetable listpicture liststylename listtable lsdlockedexcept macc maccPr
mailmerge maln malnScr manager margPr mbar mbarPr mbaseJc mbegChr mborderBox mborderBoxPr mbox mboxPr mchr
mcount mctrlPr md mdeg mdegHide mden mdiff mdPr me mendChr meqArr meqArrPr mf mfName mfPr mfunc mfuncPr
mgroupChr mgroupChrPr mgrow mhideBot mhideLeft mhideRight mhideTop mhtmltag mlim mlimloc mlimlow mlimlowPr
mlimupp mlimuppPr mm mmaddfieldname mmath mmathPict mmathPr mmaxdist mmc mmcJc mmconnectstr mmconnectstrdata
mmcPr mmcs mmdatasource mmheadersource mmmailsubject mmodso mmodsofilter mmodsofldmpdata mmodsomappedname
mmodsoname mmodsorecipdata mmodsosort mmodsosrc mmodsotable mmodsoudl mmodsoudldata mmodsouniquetag mmPr
mmquery mmr mnary mnaryPr mnoBreak mnum mobjDist moMath moMathPara moMathParaPr mopEmu mphant mphantPr
mplcHide mpos mr mrad mradPr mrPr msepChr mshow mshp msPre msPrePr msSub msSubPr msSubSup msSubSupPr msSup
msSupPr mstrikeBLTR mstrikeH mstrikeTLBR mstrikeV msub msubHide msup msupHide mtransp mtype mvertJc mvfmf
mvfml mvtof mvtol mzeroAsc mzeroDesc mzeroWid nesttableprops nextfile nonesttables objalias objclass objdata
object objname objsect objtime oldcprops oldpprops oldsprops oldtprops oleclsid operator panose password
passwordhash pgp pgptbl picprop pict pn pnseclvl pntext pntxta pntxtb printim private propname protend
protstart protusertbl pxe revtbl revtim rsidtbl rxe shp shpgrp shpinst shppict shprslt sn sp staticval
stylesheet subject sv svb tc template themedata title txe ud upr userprops wgrffxfilter windowcaption
writereservation writereservhash xe xform xmlattrname xmlattrvalue xmlclose xmlname xmlnstbl xmlopen
""".split())

_RTF_SPECIAL = {
    "par": "\n", "sect": "\n\n", "page": "\n\n", "line": "\n", "tab": "\t",
    "emdash": "\u2014", "endash": "\u2013", "emspace": "\u2003", "enspace": "\u2002",
    "qmspace": "\u2005", "bullet": "\u2022", "lquote": "\u2018", "rquote": "\u2019",
    "ldblquote": "\u201c", "rdblquote": "\u201d", "row": "\n", "cell": "\t",
    "nestcell": "\t", "nestrow": "\n", "chdate": "", "chtime": "", "chpgn": "",
    "zwj": "\u200d", "zwnj": "\u200c", "ltrmark": "", "rtlmark": "",
}

_RTF_TOKEN = re.compile(
    rb"\\([a-zA-Z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|[\r\n]+|(.)", re.S)


def rtf_to_text(raw):
    r"""RTF bytes -> the document's text.

    The usual group/destination walk: braces push and pop state, control
    words in the ignored set silence their group, \uN inserts a code point
    and skips its ANSI fallback, \'hh is a byte in the document's code page
    (taken from \ansicpgN, cp1252 when absent).
    """
    if isinstance(raw, str):
        raw = raw.encode("latin-1", errors="replace")
    m = re.search(rb"\\ansicpg(\d+)", raw[:512])
    codec = "cp1252"
    if m:
        try:
            codec = "cp%d" % int(m.group(1))
            "".encode(codec)
        except (LookupError, ValueError):
            codec = "cp1252"
    stack = []
    ignorable = False
    ucskip = 1
    curskip = 0
    out = []
    pending = bytearray()      # consecutive \'hh bytes, decoded together

    def flush():
        if pending:
            out.append(pending.decode(codec, errors="replace"))
            pending.clear()

    for match in _RTF_TOKEN.finditer(raw):
        word, arg, hexbyte, brace_char, brace, tchar = match.groups()
        if brace:
            flush()
            curskip = 0
            if brace == b"{":
                stack.append((ucskip, ignorable))
            elif stack:
                ucskip, ignorable = stack.pop()
        elif brace_char:
            flush()
            curskip = 0
            if brace_char == b"~":
                if not ignorable:
                    out.append("\xa0")
            elif brace_char in (b"{", b"}", b"\\"):
                if not ignorable:
                    out.append(brace_char.decode("latin-1"))
            elif brace_char == b"*":
                ignorable = True
            elif brace_char == b"_":
                if not ignorable:
                    out.append("-")
            # \- (optional hyphen) and \: contribute nothing
        elif word:
            flush()
            curskip = 0
            name = word.decode("latin-1")
            if name in _RTF_IGNORED:
                ignorable = True
            elif ignorable:
                pass
            elif name in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[name])
            elif name == "uc":
                ucskip = int(arg or 0)
            elif name == "u":
                code = int(arg or 0)
                if code < 0:
                    code += 0x10000
                try:
                    out.append(chr(code))
                except ValueError:
                    pass
                curskip = ucskip
        elif hexbyte:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                pending.append(int(hexbyte, 16))
        elif tchar:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                flush()
                out.append(tchar.decode("latin-1"))
    flush()
    return "".join(out)


def rtf_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    return rtf_to_text(raw)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table",
               "section", "article", "header", "footer", "blockquote", "pre", "ul", "ol",
               "dd", "dt", "dl", "hr", "title", "option", "form", "fieldset", "nav", "aside",
               "main", "figure", "figcaption", "address", "caption", "thead", "tbody", "tfoot"}
_HTML_SKIP = {"script", "style", "noscript", "template", "svg", "head"}


class _HtmlText(HTMLParser):
    """Visible text only: tags become whitespace, script and style vanish."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _HTML_SKIP:
            self._skip += 1
        if tag in _HTML_BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in _HTML_SKIP and self._skip:
            self._skip -= 1
        if tag in _HTML_BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        # <title> sits inside <head>, which is otherwise skipped; keep it.
        if self._skip and self.lasttag != "title":
            return
        self.parts.append(data)


_WS_RUN = re.compile(r"[ \t\xa0]+")
_NL_RUN = re.compile(r"\n\s*\n\s*(\n\s*)+")


def html_to_text(raw):
    r"""HTML bytes -> visible text, whitespace collapsed, paragraphs kept."""
    if isinstance(raw, bytes):
        codec = None
        m = re.search(rb"charset\s*=\s*[\"']?([A-Za-z0-9_\-]+)", raw[:4096], re.I)
        if m:
            try:
                codec = m.group(1).decode("ascii")
                "".encode(codec)
            except (LookupError, ValueError, UnicodeDecodeError):
                codec = None
        if codec:
            text = raw.decode(codec, errors="replace")
        else:
            text, _enc = fo_text.decode_bytes(raw)
    else:
        text = raw
    parser = _HtmlText()
    try:
        parser.feed(text)
        parser.close()
    except Exception:                                          # noqa: BLE001
        pass                # keep whatever was gathered; HTML in the wild is rarely well-formed
    joined = "".join(parser.parts)
    lines = [_WS_RUN.sub(" ", line).strip() for line in joined.split("\n")]
    return _NL_RUN.sub("\n\n", "\n".join(lines)).strip()


def html_text(path):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    return html_to_text(raw)


# ---------------------------------------------------------------------------
# Word 97-2003 (.doc) -- MS-DOC: the FIB, the CLX, the piece table
# ---------------------------------------------------------------------------

def doc_text(path):
    r"""Text of a Word binary document, in reading order.

    Word 97 onward stores text as pieces: the WordDocument stream holds the
    characters, the Table stream holds a piece table (CLX -> PlcPcd) that
    says which byte ranges, in which encoding (8-bit code page or UTF-16),
    make up the character stream. Word 6 and 95 keep the text contiguous
    between fcMin and fcMac. Fields keep their result and lose their code;
    paragraph and cell marks become newlines and tabs.
    """
    if olefile is None:
        raise RuntimeError("olefile is not installed")
    ole = olefile.OleFileIO(to_long_path(path))
    try:
        if not ole.exists("WordDocument"):
            raise ValueError("OLE container has no WordDocument stream")
        word = ole.openstream("WordDocument").read()
        if len(word) < 0x20:
            raise ValueError("WordDocument stream is truncated")
        ident, nfib = struct.unpack_from("<HH", word, 0)
        if ident not in (0xA5EC, 0xA5DC):
            raise ValueError("not a Word binary document (wIdent 0x%04X)" % ident)
        flags = struct.unpack_from("<H", word, 0x0A)[0]
        if flags & 0x0100:
            raise ValueError("password-protected document")
        fc_min, fc_mac = struct.unpack_from("<II", word, 0x18)
        if ident == 0xA5DC or nfib < 0x00C1:
            # Word 2.0, Word 6 and Word 95: the text is 8-bit and contiguous
            # between fcMin and fcMac. A fast-saved file keeps deleted pieces
            # in there too; the words survive, the order may not.
            raw = word[fc_min:fc_mac]
            return _clean_doc_text(raw.decode("cp1252", errors="replace"))
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise ValueError("Word document has no %s stream" % table_name)
        table = ole.openstream(table_name).read()
        fc_clx, lcb_clx = struct.unpack_from("<II", word, 0x1A2)
        if lcb_clx == 0 or fc_clx + lcb_clx > len(table):
            raise ValueError("piece table (CLX) is missing or out of range")
        clx = table[fc_clx:fc_clx + lcb_clx]
        # RgPrc entries (0x01 + Prc) precede the Pcdt (0x02 + lcb + PlcPcd).
        pos = 0
        while pos < len(clx) and clx[pos] == 0x01:
            cb = struct.unpack_from("<H", clx, pos + 1)[0]
            pos += 3 + cb
        if pos >= len(clx) or clx[pos] != 0x02:
            raise ValueError("piece table (Pcdt) not found in CLX")
        lcb = struct.unpack_from("<I", clx, pos + 1)[0]
        plc = clx[pos + 5:pos + 5 + lcb]
        n = (len(plc) - 4) // 12
        if n <= 0:
            raise ValueError("empty piece table")
        cps = struct.unpack_from("<%dI" % (n + 1), plc, 0)
        parts = []
        for i in range(n):
            base = 4 * (n + 1) + 8 * i
            fc_field = struct.unpack_from("<I", plc, base + 2)[0]
            compressed = bool(fc_field & 0x40000000)
            fc = fc_field & 0x3FFFFFFF
            count = cps[i + 1] - cps[i]
            if count <= 0:
                continue
            if compressed:
                start = fc // 2
                parts.append(word[start:start + count].decode("cp1252", errors="replace"))
            else:
                parts.append(word[fc:fc + 2 * count].decode("utf-16-le", errors="replace"))
        return _clean_doc_text("".join(parts))
    finally:
        ole.close()


_DOC_MAP = {
    "\r": "\n", "\x07": "\t", "\x0b": "\n", "\x0c": "\n", "\x0e": "\n",
    "\x01": "", "\x02": "", "\x03": "", "\x04": "", "\x05": "", "\x06": "", "\x08": "",
    "\x1e": "-", "\x1f": "", "\x00": "",
}


def _clean_doc_text(text):
    r"""Word's control characters -> plain text. Field codes are dropped, field
    results kept: 0x13 opens a field, 0x14 separates code from result, 0x15
    closes it. Fields nest (a hyperlink inside a table of contents)."""
    out = []
    depth = 0            # nesting of fields
    in_code = []         # per open field: are we still in its code part?
    for ch in text:
        if ch == "\x13":
            depth += 1
            in_code.append(True)
            continue
        if ch == "\x14":
            if in_code:
                in_code[-1] = False
            continue
        if ch == "\x15":
            if in_code:
                in_code.pop()
                depth -= 1
            continue
        if in_code and any(in_code):
            continue
        mapped = _DOC_MAP.get(ch)
        if mapped is not None:
            out.append(mapped)
        elif ch < " " and ch not in "\t\n":
            continue
        else:
            out.append(ch)
    text = "".join(out)
    # a row's last cell mark is also its row mark: "\t\n" reads as a row end
    return re.sub(r"\t+\n", "\n", text)


# ---------------------------------------------------------------------------
# PowerPoint 97-2003 (.ppt) -- MS-PPT: the record tree's text atoms
# ---------------------------------------------------------------------------

_PPT_TEXT_CHARS = 0x0FA0    # TextCharsAtom, UTF-16LE
_PPT_TEXT_BYTES = 0x0FA8    # TextBytesAtom, one byte per character
_PPT_MAIN_MASTER = 0x03F8   # MainMaster container: "Click to edit Master title style"


def ppt_text(path):
    r"""Every text atom in the presentation, slide order as stored.

    Walks the PowerPoint Document stream's record tree and collects
    TextCharsAtom / TextBytesAtom bodies -- the titles, body text, notes
    and text boxes. A fast-saved file keeps superseded copies of edited
    slides, so their text may appear more than once; for finding a phrase
    that is harmless, and it is said here so nobody mistakes it for a bug.
    """
    if olefile is None:
        raise RuntimeError("olefile is not installed")
    ole = olefile.OleFileIO(to_long_path(path))
    try:
        if ole.exists("Current User"):
            cu = ole.openstream("Current User").read()
            if len(cu) >= 16 and struct.unpack_from("<I", cu, 12)[0] == 0xF3D1C4DF:
                raise ValueError("password-protected presentation")
        if not ole.exists("PowerPoint Document"):
            if ole.exists("PP40"):
                raise ValueError("PowerPoint 4.0/95 presentations are not supported")
            raise ValueError("OLE container has no PowerPoint Document stream")
        data = ole.openstream("PowerPoint Document").read()
    finally:
        ole.close()
    parts = []
    _walk_ppt(data, 0, len(data), parts, 0)
    text = "\n".join(p for p in parts if p.strip())
    return text.replace("\r", "\n").replace("\x0b", "\n")


def _walk_ppt(data, start, end, parts, depth):
    pos = start
    while pos + 8 <= end:
        ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", data, pos)
        body = pos + 8
        body_end = min(body + rec_len, end)
        if (ver_inst & 0x000F) == 0x000F:
            if depth < 64 and rec_type != _PPT_MAIN_MASTER:
                _walk_ppt(data, body, body_end, parts, depth + 1)
        elif rec_type == _PPT_TEXT_CHARS:
            parts.append(data[body:body_end].decode("utf-16-le", errors="replace"))
        elif rec_type == _PPT_TEXT_BYTES:
            parts.append(data[body:body_end].decode("cp1252", errors="replace"))
        if rec_len == 0 and (ver_inst & 0x000F) != 0x000F:
            pos = body
        else:
            pos = body_end if body_end > pos else end


# ---------------------------------------------------------------------------
# Excel 97-2003 (.xls) -- xlrd
# ---------------------------------------------------------------------------

def xls_text(path):
    r"""Every sheet, every non-empty row, cells tab-separated -- the same
    shape ContentExtraction gives an .xlsx."""
    try:
        import xlrd
    except ImportError:
        raise RuntimeError("xlrd is not installed (pip install xlrd) -- needed for .xls text")
    with open(to_long_path(path), "rb") as f:
        contents = f.read()
    try:
        book = xlrd.open_workbook(file_contents=contents, on_demand=True)
    except xlrd.biffh.XLRDError as exc:
        raise ValueError(str(exc))
    parts = []
    try:
        for sheet in book.sheets():
            parts.append("--- Sheet: %s ---" % sheet.name)
            for r in range(sheet.nrows):
                cells = []
                for c in range(sheet.ncols):
                    cell = sheet.cell(r, c)
                    if cell.ctype == xlrd.XL_CELL_EMPTY or cell.ctype == xlrd.XL_CELL_BLANK:
                        cells.append("")
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            cells.append(xlrd.xldate_as_datetime(cell.value, book.datemode).isoformat(sep=" "))
                        except Exception:                      # noqa: BLE001
                            cells.append(str(cell.value))
                    elif cell.ctype == xlrd.XL_CELL_NUMBER:
                        v = cell.value
                        cells.append(str(int(v)) if float(v).is_integer() else str(v))
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        cells.append("TRUE" if cell.value else "FALSE")
                    else:
                        cells.append(str(cell.value))
                line = "\t".join(cells)
                if line.strip():
                    parts.append(line)
    finally:
        book.release_resources()
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# OpenDocument (.odt .ods .odp .odg) -- content.xml
# ---------------------------------------------------------------------------

_ODF_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODF_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"


def odf_text(path):
    r"""The text of an OpenDocument file: paragraphs and headings as lines,
    table cells tab-separated, every sheet or slide in order."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(to_long_path(path)) as zf:
        try:
            raw = zf.read("content.xml")
        except KeyError:
            raise ValueError("OpenDocument file without content.xml")
    out = []
    p_tag, h_tag = "{%s}p" % _ODF_TEXT_NS, "{%s}h" % _ODF_TEXT_NS
    s_tag, tab_tag, br_tag = "{%s}s" % _ODF_TEXT_NS, "{%s}tab" % _ODF_TEXT_NS, "{%s}line-break" % _ODF_TEXT_NS
    cell_tag, row_tag = "{%s}table-cell" % _ODF_TABLE_NS, "{%s}table-row" % _ODF_TABLE_NS
    in_cell = 0
    for event, elem in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
        if event == "start":
            if elem.tag == cell_tag:
                in_cell += 1
            elif elem.tag == s_tag:
                out.append(" " * int(elem.get("{%s}c" % _ODF_TEXT_NS, "1") or 1))
            elif elem.tag == tab_tag:
                out.append("\t")
            elif elem.tag == br_tag:
                out.append("\n")
            if elem.text and elem.tag not in (s_tag, tab_tag, br_tag):
                out.append(elem.text)
        else:
            if elem.tag in (p_tag, h_tag):
                out.append(" " if in_cell else "\n")     # a cell's paragraphs stay on its row
            elif elem.tag == cell_tag:
                in_cell = max(0, in_cell - 1)
                out.append("\t")
            elif elem.tag == row_tag:
                out.append("\n")
            if elem.tail:
                out.append(elem.tail)
            elem.clear()
    text = "".join(out)
    return re.sub(r"[ \t]*\n", "\n", re.sub(r"\t+\n", "\n", text)).strip()


def odf_kind(path):
    """odt / ods / odp / odg from the mimetype entry, or None."""
    try:
        with zipfile.ZipFile(to_long_path(path)) as zf:
            mime = zf.read("mimetype").decode("ascii", errors="replace").strip()
    except (KeyError, zipfile.BadZipFile, OSError):
        return None
    return {"application/vnd.oasis.opendocument.text": "odt",
            "application/vnd.oasis.opendocument.spreadsheet": "ods",
            "application/vnd.oasis.opendocument.presentation": "odp",
            "application/vnd.oasis.opendocument.graphics": "odg"}.get(mime)


# ---------------------------------------------------------------------------
# EPUB -- the spine, in reading order
# ---------------------------------------------------------------------------

def epub_text(path):
    r"""(text, fields) for an EPUB: every spine document as text, in
    reading order; the title and author from the package file."""
    import posixpath
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(to_long_path(path)) as zf:
        names = zf.namelist()
        opf_path = None
        try:
            container = ET.fromstring(zf.read("META-INF/container.xml"))
            for rf in container.iter("{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"):
                opf_path = rf.get("full-path")
                break
        except (KeyError, ET.ParseError):
            opf_path = None
        fields = {}
        order = []
        if opf_path and opf_path in names:
            try:
                opf = ET.fromstring(zf.read(opf_path))
                base = posixpath.dirname(opf_path)
                ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
                title = opf.find(".//dc:title", ns)
                creator = opf.find(".//dc:creator", ns)
                if title is not None and title.text:
                    fields["Title"] = title.text.strip()
                if creator is not None and creator.text:
                    fields["Author"] = creator.text.strip()
                manifest = {item.get("id"): (item.get("href"), item.get("media-type"))
                            for item in opf.findall(".//opf:manifest/opf:item", ns)}
                for ref in opf.findall(".//opf:spine/opf:itemref", ns):
                    href, media = manifest.get(ref.get("idref"), (None, None))
                    if href:
                        order.append(posixpath.normpath(posixpath.join(base, href)) if base else href)
            except ET.ParseError:
                order = []
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        parts = []
        for name in order:
            if name not in names:
                continue
            try:
                parts.append(html_to_text(zf.read(name)))
            except (KeyError, RuntimeError):
                continue
    return "\n\n".join(p for p in parts if p.strip()), fields


# ---------------------------------------------------------------------------
# Office Open XML read raw -- .docm .dotx .dotm .xlsm .xltx .xltm .pptm .potx
# .potm .ppsx .ppsm, and any .docx/.pptx/.xlsx a library refuses
# ---------------------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def _xml_text_runs(raw, text_tag, para_tag, tab_tag=None, br_tag=None):
    import xml.etree.ElementTree as ET
    out = []
    for event, elem in ET.iterparse(io.BytesIO(raw), events=("end",)):
        if elem.tag == text_tag:
            out.append(elem.text or "")
        elif elem.tag == para_tag:
            out.append("\n")
        elif tab_tag and elem.tag == tab_tag:
            out.append("\t")
        elif br_tag and elem.tag == br_tag:
            out.append("\n")
        elem.clear()
    return "".join(out)


def ooxml_text(path):
    r"""(SourceType, text) for any Office Open XML package, read from its
    XML directly: word/document.xml (with headers, footers and footnotes),
    every slide and its notes, every sheet with its shared strings."""
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(to_long_path(path)) as zf:
        names = set(zf.namelist())
        if "word/document.xml" in names:
            parts = [_xml_text_runs(zf.read("word/document.xml"), "{%s}t" % _W, "{%s}p" % _W,
                                    "{%s}tab" % _W, "{%s}br" % _W)]
            for name in sorted(names):
                if re.match(r"word/(header|footer)\d*\.xml$", name) or name in ("word/footnotes.xml", "word/endnotes.xml"):
                    parts.append(_xml_text_runs(zf.read(name), "{%s}t" % _W, "{%s}p" % _W, "{%s}tab" % _W, "{%s}br" % _W))
            return "Word", "\n".join(p.strip() for p in parts if p.strip())
        slides = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        if slides:
            parts = []
            for i, name in enumerate(slides, start=1):
                body = _xml_text_runs(zf.read(name), "{%s}t" % _A, "{%s}p" % _A, None, "{%s}br" % _A)
                notes = "ppt/notesSlides/notesSlide%d.xml" % i
                if notes in names:
                    body += "\n" + _xml_text_runs(zf.read(notes), "{%s}t" % _A, "{%s}p" % _A)
                parts.append("--- Slide %d ---\n%s" % (i, body.strip()))
            return "PowerPoint", "\n\n".join(parts)
        if "xl/workbook.xml" in names:
            shared = []
            if "xl/sharedStrings.xml" in names:
                cur = []
                for event, elem in ET.iterparse(io.BytesIO(zf.read("xl/sharedStrings.xml")), events=("end",)):
                    if elem.tag == "{%s}t" % _S:
                        cur.append(elem.text or "")
                    elif elem.tag == "{%s}si" % _S:
                        shared.append("".join(cur))
                        cur = []
                        elem.clear()
            rels = {}
            if "xl/_rels/workbook.xml.rels" in names:
                for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")).iter("{%s}Relationship" % _PR):
                    rels[rel.get("Id")] = rel.get("Target")
            sheets = []
            for sheet in ET.fromstring(zf.read("xl/workbook.xml")).iter("{%s}sheet" % _S):
                target = rels.get(sheet.get("{%s}id" % _R), "")
                target = target.lstrip("/")
                if not target.startswith("xl/"):
                    target = "xl/" + target
                sheets.append((sheet.get("name", ""), target))
            parts = []
            for sheet_name, target in sheets:
                if target not in names:
                    continue
                parts.append("--- Sheet: %s ---" % sheet_name)
                row_cells = []
                for event, elem in ET.iterparse(io.BytesIO(zf.read(target)), events=("end",)):
                    if elem.tag == "{%s}c" % _S:
                        kind = elem.get("t")
                        value = ""
                        if kind == "inlineStr":
                            value = "".join(t.text or "" for t in elem.iter("{%s}t" % _S))
                        else:
                            v = elem.find("{%s}v" % _S)
                            if v is not None and v.text is not None:
                                value = v.text
                                if kind == "s":
                                    try:
                                        value = shared[int(value)]
                                    except (ValueError, IndexError):
                                        pass
                                elif kind == "b":
                                    value = "TRUE" if value == "1" else "FALSE"
                        row_cells.append(value)
                    elif elem.tag == "{%s}row" % _S:
                        line = "\t".join(row_cells)
                        if line.strip():
                            parts.append(line)
                        row_cells = []
                        elem.clear()
            return "Excel", "\n".join(parts)
    raise ValueError("ZIP container that is not a Word, PowerPoint or Excel document")


# ---------------------------------------------------------------------------
# Archives -- the documents inside a .zip or .7z
# ---------------------------------------------------------------------------

MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 2000
MAX_ARCHIVE_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 2

#: Members that are text underneath are decoded in memory -- no temporary
#: file, no sniff -- which is what makes a zip of two thousand scripts
#: readable in seconds rather than minutes.
_TEXT_MEMBER_SUFFIXES = frozenset({
    ".txt", ".md", ".csv", ".json", ".xml", ".log", ".vcf", ".ics", ".srt", ".vtt", ".rst", ".tex",
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".less", ".sh", ".bash",
    ".zsh", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".sql", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs",
    ".java", ".go", ".rs", ".rb", ".php", ".pl", ".pm", ".r", ".swift", ".kt", ".kts", ".m", ".lua", ".vb",
    ".vbs", ".gradle", ".cmake", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".toml", ".properties", ".env",
    ".reg", ".html", ".htm"})


def _member_text(name, data, depth, readable):
    """One archive member through the ordinary readers -- in memory when it
    is text, via a temporary copy otherwise."""
    import os
    import tempfile
    suffix = Path(name).suffix.lower()
    if suffix not in readable:
        return None
    if suffix in _TEXT_MEMBER_SUFFIXES:
        if suffix in (".html", ".htm"):
            return html_to_text(data)
        text, _enc = fo_text.decode_bytes(data)
        return text
    tmp = tempfile.NamedTemporaryFile(prefix="fo_member_", suffix=suffix or ".bin", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            return readable[suffix](tmp.name, depth + 1)
        except Exception as exc:                               # noqa: BLE001
            return "(could not be read: %s)" % exc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def archive_text(path, depth, extract_fn, extensions):
    r"""(text, fields) for an archive: each readable member's text under an
    "[Entry: path]" heading, nested archives two levels deep.

    `extract_fn(path, depth)` reads a member written to a temporary file;
    `extensions` says which member names are worth reading at all.
    """
    readable = {ext: extract_fn for ext in extensions}
    parts = []
    read = skipped = 0
    lower = str(path).lower()
    if lower.endswith(".7z"):
        try:
            import py7zr
        except ImportError:
            raise RuntimeError("py7zr is not installed (pip install py7zr) -- needed for .7z contents")
        import os
        import tempfile
        # py7zr extracts to a folder; the readable members go to a temporary
        # one, are read from there, and the folder is deleted.
        with tempfile.TemporaryDirectory(prefix="fo_7z_") as tmpdir:
            with py7zr.SevenZipFile(to_long_path(path), "r") as archive:
                members = [m for m in archive.list() if not m.is_directory]
                wanted = []
                for m in members[:MAX_ARCHIVE_MEMBERS]:
                    if m.uncompressed > MAX_ARCHIVE_MEMBER_BYTES or Path(m.filename).suffix.lower() not in readable:
                        skipped += 1
                    else:
                        wanted.append(m.filename)
                if wanted:
                    archive.extract(path=tmpdir, targets=wanted)
            for name in wanted:
                extracted = os.path.join(tmpdir, *name.split("/"))
                if not os.path.isfile(extracted):
                    skipped += 1
                    continue
                try:
                    text = readable[Path(name).suffix.lower()](extracted, depth + 1)
                except Exception as exc:                       # noqa: BLE001
                    text = "(could not be read: %s)" % exc
                if text and text.strip():
                    parts.append("[Entry: %s]\n%s" % (name, text.strip()))
                read += 1
    else:
        with zipfile.ZipFile(to_long_path(path)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            total = 0
            for info in infos[:MAX_ARCHIVE_MEMBERS]:
                suffix = Path(info.filename).suffix.lower()
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES or suffix not in readable:
                    skipped += 1
                    continue
                total += info.file_size
                if total > MAX_ARCHIVE_TOTAL_BYTES:
                    skipped += 1
                    continue
                if info.flag_bits & 0x1:
                    parts.append("[Entry: %s]\n(encrypted; not read)" % info.filename)
                    skipped += 1
                    continue
                try:
                    data = zf.read(info)
                except (RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                    parts.append("[Entry: %s]\n(could not be read: %s)" % (info.filename, exc))
                    skipped += 1
                    continue
                text = _member_text(info.filename, data, depth, readable)
                if text and text.strip():
                    parts.append("[Entry: %s]\n%s" % (info.filename, text.strip()))
                read += 1
    return "\n\n".join(parts), {"EntriesRead": read, "EntriesSkipped": skipped}


# ---------------------------------------------------------------------------
# WordPerfect (.wpd) -- the text between the function codes
# ---------------------------------------------------------------------------

#: WordPerfect 6+ fixed-length function groups: code -> total size in bytes.
_WP6_FIXED = {0xC0: 4, 0xC1: 9, 0xC2: 11, 0xC3: 2, 0xC4: 2, 0xC5: 5, 0xC6: 6, 0xC7: 7}
#: Single-byte codes that stand for a break of some kind.
_WP_BREAKS = {0x0A, 0x0B, 0x0C, 0x0D, 0x84, 0x85, 0xCC, 0xCD, 0xCE, 0xCF}


def wpd_text(path):
    r"""The text of a WordPerfect document, 5.x or 6 and later.

    WordPerfect keeps text as 8-bit characters with formatting as function
    codes in between: single bytes for spaces and breaks, fixed-size groups
    (0xC0-0xCF) and variable-size groups (0xD0-0xFF, their length in bytes
    2-3). This reads the characters and steps over the codes; when the
    result does not look like language -- an unfamiliar version, a damaged
    file -- it falls back to the runs of printable text, which is what a
    person gets from a hex viewer.
    """
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    if not raw.startswith(b"\xffWPC"):
        raise ValueError("not a WordPerfect file (no WPC signature)")
    start = struct.unpack_from("<I", raw, 4)[0] if len(raw) >= 8 else 16
    major = raw[10] if len(raw) > 10 else 0
    if start <= 0 or start >= len(raw):
        start = 16
    out = []
    i = start
    n = len(raw)
    while i < n:
        b = raw[i]
        if 0x20 <= b <= 0x7E:
            out.append(chr(b))
            i += 1
        elif b in _WP_BREAKS:
            out.append("\n")
            i += 1
        elif b < 0x20:
            i += 1
        elif b < 0xC0:
            out.append(" ")                 # single-byte functions: spaces, hyphens, toggles
            i += 1
        elif b < 0xD0:
            size = _WP6_FIXED.get(b, 1)
            if b == 0xC0 and i + 3 < n:
                ch, charset = raw[i + 1], raw[i + 2]
                if charset == 0 and 0x20 <= ch <= 0x7E:
                    out.append(chr(ch))
                elif charset == 4 and ch < 0x80:
                    out.append(_WP_TYPOGRAPHIC.get(ch, ""))
                elif charset == 1:
                    out.append(_WP_MULTINATIONAL.get(ch, "?"))
            i += size
        else:
            if major == 0:
                # WordPerfect 5.x: the group's length sits at bytes 2-3 and
                # counts the bytes after it, closed by the code repeated.
                size = struct.unpack_from("<H", raw, i + 2)[0] + 4 if i + 4 <= n else n
            else:
                size = struct.unpack_from("<H", raw, i + 2)[0] if i + 4 <= n else n
            i += max(size, 4)
    text = re.sub(r"[ ]{2,}", " ", "".join(out))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if _wordlike_ratio(text) < 0.6:
        text = ascii_runs(raw[start:])
    return text


_WP_TYPOGRAPHIC = {0x1C: "‘", 0x1D: "’", 0x1F: "“", 0x20: "”", 0x22: "—", 0x21: "–",
                   0x00: "●", 0x03: "•", 0x05: "¶", 0x06: "§"}
_WP_MULTINATIONAL = {0x1A: "à", 0x1B: "Á", 0x1C: "á", 0x1F: "â", 0x23: "ä", 0x2E: "ç",
                     0x36: "è", 0x38: "é", 0x3A: "ê", 0x3C: "ë", 0x44: "í", 0x46: "î",
                     0x48: "ï", 0x4A: "ñ", 0x4E: "ó", 0x50: "ô", 0x52: "ö", 0x5A: "ú",
                     0x5C: "û", 0x5E: "ü", 0x62: "ß"}


def _wordlike_ratio(text):
    tokens = [t.strip(".,;:!?()[]{}\"'") for t in text.split()]
    tokens = [t for t in tokens if t]
    if len(tokens) < 5:
        return 1.0 if tokens else 0.0
    good = sum(1 for t in tokens if re.match(r"^[A-Za-z][A-Za-z'\-]*$", t) and (len(t) <= 2 or any(c in "aeiouyAEIOUY" for c in t))
               or re.match(r"^[$€£]?\d[\d,./:%-]*$", t))
    return good / len(tokens)


def ascii_runs(raw, min_len=4):
    r"""Runs of printable ASCII of at least `min_len` characters, one per
    line -- the text a hex viewer shows, for files whose format is not
    understood well enough to parse."""
    runs = re.findall(rb"[\x20-\x7e]{%d,}" % min_len, raw)
    return "\n".join(r.decode("ascii") for r in runs)


def utf16_runs(raw, min_len=4):
    r"""Runs of printable UTF-16LE text, for files that keep their words that
    way (OneNote does)."""
    pattern = rb"(?:[\x20-\x7e]\x00|[\xa0-\xff]\x00|[\x00-\xff][\x01-\xd7]){%d,}" % min_len
    out = []
    for run in re.finditer(pattern, raw):
        chunk = run.group(0)
        if len(chunk) % 2:
            chunk = chunk[:-1]
        try:
            text = chunk.decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        if sum(ch.isalpha() for ch in text) >= max(2, len(text) // 3):
            out.append(text)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# OneNote (.one) -- the words, by scanning
# ---------------------------------------------------------------------------

_ONENOTE_NOISE = re.compile(r"^(Calibri|Segoe UI|Arial|Times New Roman|Consolas|Cambria|Verdana|Tahoma|"
                            r"[0-9A-Fa-f\-{}]{20,}|\W+)$")


def onenote_text(path):
    r"""The text of a OneNote section, recovered by scanning for the UTF-16
    strings OneNote stores it as. Not a parse of the revision store ([MS-ONE]
    is a large specification), so headings, body text and deleted revisions
    come out together and in file order, with font names and other noise
    filtered by pattern. Enough to find a page by what it says."""
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    if not raw:
        raise ValueError("empty file")
    seen = set()
    lines = []
    for line in utf16_runs(raw, 3).split("\n"):
        line = line.strip()
        if not line or _ONENOTE_NOISE.match(line) or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines)
