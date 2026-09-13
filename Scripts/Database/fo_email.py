r"""
fo_email.py
Part of: The File Organizer

Text of email, in every shape it is kept: an .eml (one message as sent), an
.mbox (many messages, one after another -- what Gmail's Takeout gives), an
.mht/.mhtml (a web page saved as a MIME message), an Outlook .msg (one
message as an OLE2 container), and an Outlook .pst/.ost (a whole mailbox).

Every message becomes the same text:

    Subject: ...
    From: name <address>
    To: ...
    Cc: ...
    Date: ...
    Attachments: report.pdf; notes.docx

    <the body, as plain text -- HTML and RTF bodies are converted>

    [Attachment: report.pdf]
    <the attachment's text, when it is a format extraction can read>

A container (.mbox, .pst) lists its messages in order, each headed with
its folder and number. Attachments are read through the same readers as
files on disk (a temporary copy, deleted at once), down to three levels of
messages inside messages. The recorded Title, Author and Created of an
.eml or .msg are its subject, sender and date, so they show as columns.

Nothing here writes to a source file. A PST is read directly (fo_pst),
never through Outlook, which would open it for writing.
"""

import email
import email.policy
import email.utils
import io
import os
import struct
import tempfile
from email.header import decode_header, make_header
from pathlib import Path

import fo_text
from file_organizer_common import to_long_path

try:
    import olefile
except ImportError:                                            # pragma: no cover
    olefile = None

MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_DEPTH = 3


# -- the shared shape ----------------------------------------------------------

def _header_block(subject, sender, to, cc, date, attachment_names):
    lines = []
    if subject:
        lines.append("Subject: " + subject)
    if sender:
        lines.append("From: " + sender)
    if to:
        lines.append("To: " + to)
    if cc:
        lines.append("Cc: " + cc)
    if date:
        lines.append("Date: " + date)
    if attachment_names:
        lines.append("Attachments: " + "; ".join(n for n in attachment_names if n))
    return "\n".join(lines)


def _attachment_text(name, data, depth):
    r"""An attachment's text through the ordinary readers, via a temporary
    copy that is deleted at once. Empty when the format is not one extraction
    reads, or the attachment is too large to be worth it."""
    if not data or len(data) > MAX_ATTACHMENT_BYTES or depth >= MAX_DEPTH:
        return ""
    import ContentExtraction                                    # lazy: it imports this module
    suffix = Path(name or "").suffix.lower()
    if suffix and suffix not in ContentExtraction.EXTENSIONS:
        return ""
    tmp = tempfile.NamedTemporaryFile(prefix="fo_attach_", suffix=suffix or ".bin", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        try:
            if suffix in (".eml", ".msg"):
                return message_file_text(tmp.name, depth + 1)
            _source, text = ContentExtraction.extract_any(tmp.name)
            return text
        except Exception:                                      # noqa: BLE001
            return ""            # an unreadable attachment is not a failed email
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _with_attachments(head, body, attachments, depth):
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    parts = [head, "", body.strip()]
    for name, data, embedded_text in attachments:
        text = embedded_text if embedded_text is not None else _attachment_text(name, data, depth)
        if text and text.strip():
            parts.append("")
            parts.append("[Attachment: %s]" % (name or "(unnamed)"))
            parts.append(text.strip())
    return "\n".join(parts).strip() + "\n"


# -- RFC 822: .eml, .mbox, .mht -------------------------------------------------

def _decode_header_value(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).replace("\r", " ").replace("\n", " ").strip()
    except Exception:                                          # noqa: BLE001
        return str(value).strip()


def _decode_part(part):
    try:
        payload = part.get_payload(decode=True) or b""
    except Exception:                                          # noqa: BLE001
        return ""
    charset = part.get_content_charset()
    if charset:
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, ValueError):
            pass
    text, _enc = fo_text.decode_bytes(payload)
    return text


def _collect(part, plain, html, attachments, depth):
    r"""Sort a message's parts into its own text and its attachments.

    A forwarded message (message/rfc822) is an attachment with its own
    text, rendered through message_text again -- not folded into the
    outer body, which is what a plain walk over every part would do.
    """
    ctype = part.get_content_type()
    if ctype == "message/rfc822":
        inner = part.get_payload()
        inner = inner[0] if isinstance(inner, list) and inner else None
        name = _decode_header_value(part.get_filename()) if part.get_filename() else ""
        if inner is not None:
            name = name or (_decode_header_value(inner.get("Subject")) or "message") + ".eml"
            inner_text = message_text(inner, depth + 1)[0] if depth < MAX_DEPTH else ""
            attachments.append((name, None, inner_text))
        return
    if part.is_multipart():
        for sub in part.get_payload():
            _collect(sub, plain, html, attachments, depth)
        return
    disposition = (part.get_content_disposition() or "").lower()
    name = part.get_filename()
    if disposition == "attachment" or (name and ctype not in ("text/plain", "text/html")):
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:                                      # noqa: BLE001
            data = b""
        attachments.append((_decode_header_value(name) if name else "", data, None))
        return
    if ctype == "text/plain":
        plain.append(_decode_part(part))
    elif ctype == "text/html":
        html.append(_decode_part(part))


def message_text(msg, depth=0):
    """One parsed email.message.Message -> the shared text shape."""
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    to = _decode_header_value(msg.get("To"))
    cc = _decode_header_value(msg.get("Cc"))
    date = _decode_header_value(msg.get("Date"))
    plain, html, attachments = [], [], []
    _collect(msg, plain, html, attachments, depth)
    if plain:
        body = "\n\n".join(plain)
    elif html:
        import fo_extractors
        body = "\n\n".join(fo_extractors.html_to_text(h) for h in html)
    else:
        body = ""
    head = _header_block(subject, sender, to, cc, date, [a[0] for a in attachments])
    return _with_attachments(head, body, attachments, depth), {
        "Title": subject, "Author": sender, "Created": _iso_date(date)}


def _iso_date(value):
    if not value:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return dt.isoformat(sep=" ") if dt else ""
    except (TypeError, ValueError, IndexError):
        return value


def _parse_bytes(raw):
    return email.message_from_bytes(raw, policy=email.policy.compat32)


def eml_text(path, depth=0):
    with open(to_long_path(path), "rb") as f:
        raw = f.read()
    return message_text(_parse_bytes(raw), depth)


def mht_text(path):
    """A web page saved as MHTML: its HTML part as text, headed by its subject."""
    text, fields = eml_text(path)
    return text, fields


def iter_mbox(path):
    """Each message's raw bytes, split on the 'From ' lines that separate
    them. Read line by line, so a multi-gigabyte Takeout export is never
    held whole in memory."""
    current = []
    with open(to_long_path(path), "rb") as f:
        for line in f:
            if line.startswith(b"From ") and current:
                yield b"".join(current)
                current = []
            elif line.startswith(b"From ") and not current:
                continue
            else:
                current.append(line)
    if current:
        yield b"".join(current)


def mbox_text(path):
    parts = []
    count = 0
    for raw in iter_mbox(path):
        count += 1
        try:
            text, _fields = message_text(_parse_bytes(raw))
        except Exception as exc:                               # noqa: BLE001
            text = "(message %d could not be parsed: %s)\n" % (count, exc)
        parts.append("==== Message %d ====\n%s" % (count, text))
    return "\n".join(parts), {"Messages": count}


# -- Outlook .msg ([MS-OXMSG]) --------------------------------------------------

_PT_UNICODE, _PT_STRING8, _PT_BINARY, _PT_OBJECT = 0x001F, 0x001E, 0x0102, 0x000D


class _MsgStorage:
    """One message's properties inside an OLE2 file: the top level, or an
    embedded message's storage under an attachment."""

    def __init__(self, ole, prefix, codepage=None):
        self.ole = ole
        self.prefix = prefix
        self.names = set()
        for entry in ole.listdir(streams=True, storages=True):
            joined = "/".join(entry)
            if not prefix or joined.startswith(prefix + "/"):
                self.names.add(joined[len(prefix) + 1:] if prefix else joined)
        self.codepage = codepage or self._codepage()

    def _stream(self, name):
        if name not in self.names:
            return None
        full = (self.prefix + "/" + name) if self.prefix else name
        try:
            return self.ole.openstream(full).read()
        except (OSError, ValueError):
            return None

    def _codepage(self):
        for pid in (0x3FFD, 0x3FDE):
            raw = self.fixed_property(pid)
            if raw is not None:
                cp = struct.unpack("<I", raw[:4])[0]
                try:
                    "".encode("cp%d" % cp)
                    return "cp%d" % cp
                except (LookupError, ValueError):
                    pass
        return "cp1252"

    def fixed_property(self, pid):
        """The 8 value bytes of a fixed-size property from __properties_version1.0.

        The stream's header is 32 bytes at the top level, 24 in an embedded
        message's storage and 8 in an attachment's or recipient's; after it,
        16-byte entries: tag (type low, id high), flags, value.
        """
        raw = self._stream("__properties_version1.0")
        if not raw:
            return None
        if not self.prefix:
            pos = 32
        elif self.prefix.endswith("__substg1.0_3701000D"):
            pos = 24
        else:
            pos = 8
        while pos + 16 <= len(raw):
            tag = struct.unpack_from("<I", raw, pos)[0]
            if (tag >> 16) == pid:
                return raw[pos + 8:pos + 16]
            pos += 16
        return None

    def text(self, pid):
        raw = self._stream("__substg1.0_%04X001F" % pid)
        if raw is not None:
            return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
        raw = self._stream("__substg1.0_%04X001E" % pid)
        if raw is not None:
            return raw.decode(self.codepage, errors="replace").rstrip("\x00")
        return ""

    def binary(self, pid):
        return self._stream("__substg1.0_%04X0102" % pid)

    def time(self, pid):
        raw = self.fixed_property(pid)
        if raw is None:
            return None
        import fo_pst
        return fo_pst._filetime(raw)

    def int32(self, pid):
        raw = self.fixed_property(pid)
        return struct.unpack("<i", raw[:4])[0] if raw else None

    def attachment_storages(self):
        found = sorted({n.split("/")[0] for n in self.names if n.startswith("__attach_version1.0_")})
        return [(self.prefix + "/" + n) if self.prefix else n for n in found]


def _msg_body(store):
    body = store.text(0x1000)
    if body:
        return body
    html = store.binary(0x1013)
    if html:
        import fo_extractors
        return fo_extractors.html_to_text(html)
    rtf = store.binary(0x1009)
    if rtf:
        import fo_extractors
        import fo_pst
        try:
            return fo_extractors.rtf_to_text(fo_pst.decompress_rtf(rtf))
        except ValueError:
            return ""
    return ""


def _msg_storage_text(ole, prefix, depth):
    store = _MsgStorage(ole, prefix)
    subject = store.text(0x0037)
    sender = store.text(0x0C1A) or store.text(0x0042)
    sender_email = store.text(0x5D01) or store.text(0x5D02) or store.text(0x0C1F) or store.text(0x0065)
    if sender_email and sender_email.lower() not in sender.lower():
        sender = ("%s <%s>" % (sender, sender_email)) if sender else sender_email
    to, cc = store.text(0x0E04), store.text(0x0E03)
    when = store.time(0x0039) or store.time(0x0E06)
    date = when.isoformat(sep=" ") if when else ""
    attachments = []
    for att_prefix in store.attachment_storages():
        att = _MsgStorage(ole, att_prefix, store.codepage)
        name = att.text(0x3707) or att.text(0x3704)
        method = att.int32(0x3705)
        embedded_prefix = att_prefix + "/__substg1.0_3701000D"
        if method == 5 or any(n.startswith("__substg1.0_3701000D") for n in att.names):
            if depth < MAX_DEPTH:
                try:
                    inner_text, _f = _msg_storage_text(ole, embedded_prefix, depth + 1)
                except Exception:                              # noqa: BLE001
                    inner_text = ""
                attachments.append((name or "(embedded message)", None, inner_text))
            continue
        attachments.append((name, att.binary(0x3701), None))
    head = _header_block(subject, sender, to, cc, date, [a[0] for a in attachments])
    return _with_attachments(head, _msg_body(store), attachments, depth), {
        "Title": subject, "Author": sender, "Created": date}


def msg_text(path, depth=0):
    if olefile is None:
        raise RuntimeError("olefile is not installed")
    ole = olefile.OleFileIO(to_long_path(path))
    try:
        if not ole.exists("__properties_version1.0"):
            raise ValueError("OLE container is not an Outlook message (no properties stream)")
        return _msg_storage_text(ole, "", depth)
    finally:
        ole.close()


# -- Outlook .pst / .ost --------------------------------------------------------

def _pst_message_text(m, depth):
    import fo_extractors
    import fo_pst
    body = m.body
    if isinstance(body, tuple):
        kind, payload = body
        if kind == "HTML":
            body = fo_extractors.html_to_text(payload) if isinstance(payload, bytes) else payload
        else:
            try:
                body = fo_extractors.rtf_to_text(fo_pst.decompress_rtf(payload))
            except ValueError:
                body = ""
    sender = m.sender
    if m.sender_email and m.sender_email.lower() not in sender.lower():
        sender = ("%s <%s>" % (sender, m.sender_email)) if sender else m.sender_email
    when = m.sent or m.delivered
    attachments = []
    for a in m.attachments:
        if a.message is not None:
            attachments.append((a.name or "(embedded message)", None,
                                _pst_message_text(a.message, depth + 1) if depth < MAX_DEPTH else ""))
        else:
            attachments.append((a.name, a.data, None))
    head = _header_block(m.subject, sender, m.to, m.cc, when.isoformat(sep=" ") if when else "",
                         [a[0] for a in attachments])
    return _with_attachments(head, body or "", attachments, depth)


def pst_text(path):
    import fo_pst
    parts = []
    count = 0
    with fo_pst.PstFile(to_long_path(path)) as pst:
        for m in pst.messages():
            count += 1
            try:
                text = _pst_message_text(m, 0)
            except Exception as exc:                           # noqa: BLE001
                text = "(message %d could not be read: %s)\n" % (count, exc)
            parts.append("==== Message %d -- %s ====\n%s" % (count, m.folder or "(root)", text))
    return "\n".join(parts), {"Messages": count}


# -- dispatch by what the bytes are ------------------------------------------------

def message_file_text(path, depth=0):
    """(.eml or .msg) -> text, for an attachment that is itself a message."""
    with open(to_long_path(path), "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return msg_text(path, depth)[0]
    return eml_text(path, depth)[0]
