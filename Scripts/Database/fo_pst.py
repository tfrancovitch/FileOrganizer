r"""
fo_pst.py
Part of: The File Organizer

A read-only reader for Outlook Personal Folder files (.pst, and .ost of the
same layout): every message's headers, body and attachments, straight from
the file, following [MS-PST]. Nothing is written and Outlook is not
involved -- Outlook opens a PST for writing, which would change a source
file, and this product never changes a source file.

What is implemented, and only that:

  * the header (ANSI 97-2002 and Unicode 2003+ layouts; the 4 KB-page
    layout of newer OST files is refused with a plain message);
  * the node and block B-trees (NBT, BBT), read whole into dictionaries;
  * data trees (XBLOCK / XXBLOCK) and subnode trees (SLBLOCK / SIBLOCK);
  * the "permutative" and "cyclic" encodings data blocks are stored under
    (the tables of [MS-PST] 5.1 and 5.2);
  * the heap-on-node, the BTree-on-heap and the property context, where a
    message's, a folder's and an attachment's properties live.

Table contexts (folder listings) are deliberately not parsed: every message
is found by walking the node tree for message nodes, and its folder by
following the parent chain. That is less code, and it cannot miss a message
a folder's table forgot.
"""

import datetime as _dt
import struct
from collections import namedtuple

# -- the two encodings' tables ([MS-PST] 5.1, 5.2) ---------------------------

_COMPRESSIBLE = bytes.fromhex(
    "47f1b4e60b6a7248854e9eebe2f89453e0bba002e85a09abdbe3bac67cc310dd"
    "39059630f53760828cc9134a6b1df3fb8f2697ca911701c4322d6e3195ffd923"
    "d1005e79dc443b1a28c5615720903d83b943be67d2464276c06d5b7eb20f1629"
    "3ca903540dda5ddff6b7c762cd8d06d3695c86d614f7a56675acb1e94521700c"
    "879f74a4224c6fbf1f56aa2eb3783350b0a392bccf191ca763cb1e4d3e4b1b9b"
    "4fe7f0eead3ab55904ea40552551e57a893868527bfc27aed7bdfa07f4cc8e5f"
    "ef359c842b15d5773449b6120a7f7188fd9d18417d93d8582ccefe24afdeb836"
    "c8a180a69998a82f0e816573e4c2a28ad4e111d0088b2af2ed9a643fc16cf9ec")
_HIGH1 = bytes.fromhex(
    "41361362a8216ebbf416cc047f64e85d1ef2cb2a74c55e35d295479e962d9a88"
    "4c7d843fdbac31b6485ff6c4d8398be7233b388ec8c1df25b120a546604e9cfb"
    "aad35651457c550007c92b9d859b09a08fadb30f63ab894bd7a7155a716642bf"
    "264a6b98faea7753b270052cfd593a867ece06eb827857c78d43afb41cd45bcd"
    "e2e9274fc3087280cfb0eff5286dbe304d3492d50e3c2232e5e4f99fc2d10a81"
    "12e1ee918376e397e6618a1779a4b7dc907a5c8c02a6ca69de501a1193b95287"
    "58fced1d37491b6ae0293399bd6cd994f340546ff0c673b8d63e6518441fdd67"
    "10f10c19ecae03a1147ba90bfff8a3c0a201f72ebc2468750dfeba2fb5d0da3d")
_HIGH2 = bytes.fromhex(
    "14530f56b3c87a9ceb65481716159f02cc547c83000d0c0ba262a876dbd9edc7"
    "c5a4dcac8574d6d0a79bae9a967166c36399b8dd73928e847da55ed15d93b157"
    "5150808952944f4e0a6bbc8d7f6e47464140440111cb033ff7f4e1a98f3c3af9"
    "fbf0193082092ec99da08649ee6f4d6dc42d813425871b88aafc06a11238fd4c"
    "4272641337246a757743ffe6b44b365ce4d8353d45b92cecb7312b290768a30e"
    "697b189e2139be281a5b78f523ca2ab0af3efe048ce7e5983295d3f64ae8a6ea"
    "e9f3d52f7020f21f0567ad5510cecde3273bdabad7c226d4911dd21c2233f8fa"
    "f15aefcf90b68bb5bdc0bf08971e6ce261e0c6c159abbb58de5fdf60797eb28a")

CRYPT_NONE, CRYPT_PERMUTE, CRYPT_CYCLIC = 0, 1, 2


def decrypt(method, key, data):
    """Undo the block encoding. `key` is the block's bid (its low 32 bits)."""
    if method == CRYPT_NONE or not data:
        return data
    if method == CRYPT_PERMUTE:
        return data.translate(_COMPRESSIBLE)
    if method == CRYPT_CYCLIC:
        salt = ((key & 0xFFFF0000) >> 16) ^ (key & 0x0000FFFF)
        out = bytearray(len(data))
        for i, b in enumerate(data):
            lo = salt & 0xFF
            hi = (salt & 0xFF00) >> 8
            b = _HIGH1[(b + lo) & 0xFF]
            b = _HIGH2[(b + hi) & 0xFF]
            b = _COMPRESSIBLE[(b - hi) & 0xFF]
            out[i] = (b - lo) & 0xFF
            salt = (salt + 1) & 0xFFFF
        return bytes(out)
    raise ValueError("unsupported PST block encoding %d (Windows-encrypted?)" % method)


# -- property types and identifiers --------------------------------------------

PT_INT16, PT_INT32, PT_FLOAT, PT_DOUBLE, PT_BOOL, PT_INT64 = 0x0002, 0x0003, 0x0004, 0x0005, 0x000B, 0x0014
PT_STRING8, PT_UNICODE, PT_SYSTIME, PT_BINARY, PT_OBJECT, PT_ERROR = 0x001E, 0x001F, 0x0040, 0x0102, 0x000D, 0x000A
_INLINE = {PT_INT16, PT_INT32, PT_FLOAT, PT_BOOL, PT_ERROR}

PR_SUBJECT, PR_MESSAGE_CLASS = 0x0037, 0x001A
PR_SENDER_NAME, PR_SENT_REPRESENTING_NAME = 0x0C1A, 0x0042
PR_SENDER_EMAIL, PR_SENDER_SMTP, PR_SENT_REPRESENTING_EMAIL, PR_SENT_REPRESENTING_SMTP = 0x0C1F, 0x5D01, 0x0065, 0x5D02
PR_DISPLAY_TO, PR_DISPLAY_CC, PR_DISPLAY_BCC = 0x0E04, 0x0E03, 0x0E02
PR_CLIENT_SUBMIT_TIME, PR_MESSAGE_DELIVERY_TIME = 0x0039, 0x0E06
PR_BODY, PR_HTML, PR_RTF_COMPRESSED = 0x1000, 0x1013, 0x1009
PR_MESSAGE_CODEPAGE, PR_INTERNET_CPID = 0x3FFD, 0x3FDE
PR_DISPLAY_NAME = 0x3001
PR_ATTACH_DATA, PR_ATTACH_METHOD, PR_ATTACH_SHORT_NAME, PR_ATTACH_LONG_NAME, PR_ATTACH_MIME_TAG = 0x3701, 0x3705, 0x3704, 0x3707, 0x370E
ATTACH_BY_VALUE, ATTACH_EMBEDDED_MSG = 1, 5

NID_TYPE_FOLDER, NID_TYPE_SEARCH_FOLDER, NID_TYPE_MESSAGE, NID_TYPE_ATTACHMENT, NID_TYPE_ASSOC_MESSAGE = 0x02, 0x03, 0x04, 0x05, 0x08
NID_ROOT_FOLDER = 0x122

NbtEntry = namedtuple("NbtEntry", "nid bid_data bid_sub nid_parent")
Attachment = namedtuple("Attachment", "name method data message")
Message = namedtuple("Message", "nid folder subject sender sender_email to cc bcc sent delivered "
                                "message_class body attachments")


def _filetime(raw):
    if not raw or len(raw) < 8:
        return None
    ticks = struct.unpack("<Q", raw[:8])[0]
    if ticks == 0:
        return None
    try:
        return _dt.datetime(1601, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError):
        return None


class PstError(ValueError):
    pass


class PstFile:
    r"""One open PST. Use as a context manager; `messages()` yields every
    message with its folder path, text body and attachments."""

    PAGE = 512
    BLOCK_MAX = 8192

    def __init__(self, path):
        self.f = open(path, "rb")
        try:
            self._read_header()
            self.bbt = {}
            self.nbt = {}
            self._load_btree(self.bbt_root, "bbt")
            self._load_btree(self.nbt_root, "nbt")
        except Exception:
            self.f.close()
            raise

    def close(self):
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- header ---------------------------------------------------------------

    def _read_header(self):
        head = self.f.read(600)
        if head[:4] != b"!BDN":
            raise PstError("not an Outlook PST/OST file (no !BDN signature)")
        ver = struct.unpack_from("<H", head, 10)[0]
        if ver in (14, 15):
            self.unicode = False
        elif ver == 23:
            self.unicode = True
        elif ver >= 36:
            raise PstError("PST/OST with 4 KB pages (Outlook 2013+ OST layout) is not supported")
        else:
            raise PstError("unknown PST format version %d" % ver)
        if self.unicode:
            self.nbt_root = struct.unpack_from("<Q", head, 224)[0]
            self.bbt_root = struct.unpack_from("<Q", head, 240)[0]
            self.crypt = head[513]
        else:
            self.nbt_root = struct.unpack_from("<I", head, 188)[0]
            self.bbt_root = struct.unpack_from("<I", head, 196)[0]
            self.crypt = head[461]
        if self.crypt not in (CRYPT_NONE, CRYPT_PERMUTE, CRYPT_CYCLIC):
            raise PstError("PST block encoding %d is not supported (Windows-encrypted file?)" % self.crypt)
        self.idsize = 8 if self.unicode else 4
        self.idfmt = "<Q" if self.unicode else "<I"

    # -- the B-trees ----------------------------------------------------------

    def _load_btree(self, ib, kind, depth=0):
        if depth > 32:
            raise PstError("B-tree deeper than 32 levels; file is damaged")
        self.f.seek(ib)
        page = self.f.read(self.PAGE)
        if len(page) < self.PAGE:
            raise PstError("B-tree page at %d is beyond the end of the file" % ib)
        if self.unicode:
            n, _cmax, cb, level = page[488], page[489], page[490], page[491]
            entries = page[:488]
        else:
            n, _cmax, cb, level = page[492], page[493], page[494], page[495]
            entries = page[:492]
        if cb == 0 or n * cb > len(entries):
            raise PstError("B-tree page at %d has an impossible entry size" % ib)
        for i in range(n):
            e = entries[i * cb:(i + 1) * cb]
            if level > 0:
                # BTENTRY: btkey, BREF(bid, ib)
                child_ib = struct.unpack_from(self.idfmt, e, 2 * self.idsize)[0]
                self._load_btree(child_ib, kind, depth + 1)
            elif kind == "bbt":
                bid, ib_blk = struct.unpack_from(self.idfmt + self.idfmt[1:], e, 0)
                cb_blk = struct.unpack_from("<H", e, 2 * self.idsize)[0]
                self.bbt[bid] = (ib_blk, cb_blk)
            else:
                nid = struct.unpack_from("<I", e, 0)[0]          # low 32 bits are the NID
                bid_data = struct.unpack_from(self.idfmt, e, self.idsize)[0]
                bid_sub = struct.unpack_from(self.idfmt, e, 2 * self.idsize)[0]
                nid_parent = struct.unpack_from("<I", e, 3 * self.idsize)[0]
                self.nbt[nid] = NbtEntry(nid, bid_data, bid_sub, nid_parent)

    # -- blocks, data trees, subnode trees ------------------------------------

    def block(self, bid):
        """One block's payload. Data blocks are decoded; internal ones are stored plain."""
        ref = self.bbt.get(bid)
        if ref is None:
            raise PstError("block %d is not in the block B-tree" % bid)
        ib, cb = ref
        self.f.seek(ib)
        data = self.f.read(cb)
        if len(data) != cb:
            raise PstError("block %d is truncated" % bid)
        if bid & 2 == 0:
            data = decrypt(self.crypt, bid & 0xFFFFFFFF, data)
        return data

    def data_blocks(self, bid, depth=0):
        """The data blocks of a node's data, in order (a heap needs them apart)."""
        if bid == 0:
            return []
        if depth > 4:
            raise PstError("data tree deeper than expected; file is damaged")
        blk = self.block(bid)
        if bid & 2 == 0:
            return [blk]
        btype, level = blk[0], blk[1]
        if btype != 1:
            raise PstError("expected a data tree block, found type %d" % btype)
        n = struct.unpack_from("<H", blk, 2)[0]
        out = []
        for i in range(n):
            child = struct.unpack_from(self.idfmt, blk, 8 + i * self.idsize)[0]
            if level == 1:
                out.append(self.block(child))
            else:
                out.extend(self.data_blocks(child, depth + 1))
        return out

    def data(self, bid):
        return b"".join(self.data_blocks(bid))

    def subnodes(self, bid_sub, depth=0):
        """nid -> (bid_data, bid_sub) for a node's subnode tree."""
        out = {}
        if bid_sub == 0:
            return out
        if depth > 4:
            raise PstError("subnode tree deeper than expected; file is damaged")
        blk = self.block(bid_sub)
        if blk[0] != 2:
            raise PstError("expected a subnode block, found type %d" % blk[0])
        level = blk[1]
        n = struct.unpack_from("<H", blk, 2)[0]
        base = 8 if self.unicode else 4
        if level == 0:
            size = 3 * self.idsize
            for i in range(n):
                e = blk[base + i * size: base + (i + 1) * size]
                nid = struct.unpack_from("<I", e, 0)[0]
                bd = struct.unpack_from(self.idfmt, e, self.idsize)[0]
                bs = struct.unpack_from(self.idfmt, e, 2 * self.idsize)[0]
                out[nid] = (bd, bs)
        else:
            size = 2 * self.idsize
            for i in range(n):
                e = blk[base + i * size: base + (i + 1) * size]
                child = struct.unpack_from(self.idfmt, e, self.idsize)[0]
                out.update(self.subnodes(child, depth + 1))
        return out

    # -- heap-on-node, BTree-on-heap, property context ------------------------

    @staticmethod
    def _heap_item(blocks, hid):
        if hid == 0:
            return b""
        block_index = hid >> 16
        index = (hid >> 5) & 0x7FF
        if block_index >= len(blocks) or index == 0:
            raise PstError("heap id %#x points outside the heap" % hid)
        blk = blocks[block_index]
        ib_map = struct.unpack_from("<H", blk, 0)[0]
        n_alloc = struct.unpack_from("<H", blk, ib_map)[0]
        if index > n_alloc:
            raise PstError("heap id %#x is past the allocation map" % hid)
        start = struct.unpack_from("<H", blk, ib_map + 4 + (index - 1) * 2)[0]
        end = struct.unpack_from("<H", blk, ib_map + 4 + index * 2)[0]
        return blk[start:end]

    def _bth_records(self, blocks, hid, key_size, ent_size, levels):
        data = self._heap_item(blocks, hid)
        rec = key_size + (4 if levels > 0 else ent_size)
        out = []
        for i in range(len(data) // rec):
            r = data[i * rec:(i + 1) * rec]
            if levels > 0:
                child = struct.unpack_from("<I", r, key_size)[0]
                out.extend(self._bth_records(blocks, child, key_size, ent_size, levels - 1))
            else:
                out.append(r)
        return out

    def property_context(self, bid_data, bid_sub):
        r"""{property id: (type, value)} for a node -- a message, a folder,
        an attachment. Values are decoded: str for strings, bytes for
        binary, datetime for times, int for numbers, and for an embedded
        object the subnode (bid_data, bid_sub) holding it."""
        blocks = self.data_blocks(bid_data)
        if not blocks:
            return {}
        head = blocks[0]
        if len(head) < 12 or head[2] != 0xEC:
            raise PstError("node data is not a heap")
        if head[3] != 0xBC:
            return {}                                   # not a property context
        root = struct.unpack_from("<I", head, 4)[0]
        bth = self._heap_item(blocks, root)
        if len(bth) < 8 or bth[0] != 0xB5:
            raise PstError("property context has no BTree header")
        key_size, ent_size, levels = bth[1], bth[2], bth[3]
        records_hid = struct.unpack_from("<I", bth, 4)[0]
        subs = None
        props = {}
        codepage = None
        raw = {}
        for r in self._bth_records(blocks, records_hid, key_size, ent_size, levels):
            pid, ptype = struct.unpack_from("<HH", r, 0)
            raw[pid] = (ptype, r[4:8])
        # code page first, so 8-bit strings decode right
        for pid in (PR_MESSAGE_CODEPAGE, PR_INTERNET_CPID):
            if pid in raw and raw[pid][0] == PT_INT32:
                cp = struct.unpack_from("<I", raw[pid][1])[0]
                try:
                    "".encode("cp%d" % cp)
                    codepage = "cp%d" % cp
                    break
                except (LookupError, ValueError):
                    pass
        for pid, (ptype, value) in raw.items():
            try:
                if ptype in _INLINE:
                    if ptype == PT_BOOL:
                        props[pid] = (ptype, bool(value[0]))
                    elif ptype == PT_FLOAT:
                        props[pid] = (ptype, struct.unpack("<f", value)[0])
                    elif ptype == PT_INT16:
                        props[pid] = (ptype, struct.unpack("<h", value[:2])[0])
                    else:
                        props[pid] = (ptype, struct.unpack("<i", value)[0])
                    continue
                hnid = struct.unpack("<I", value)[0]
                if hnid == 0:
                    continue
                if hnid & 0x1F == 0:
                    payload = self._heap_item(blocks, hnid)
                else:
                    if subs is None:
                        subs = self.subnodes(bid_sub)
                    entry = subs.get(hnid)
                    if entry is None:
                        continue
                    payload = self.data(entry[0])
                if ptype == PT_OBJECT:
                    # [MS-PST] 2.3.3.5: the payload is {Nid, ulSize}; the Nid
                    # names the subnode holding the embedded object -- for an
                    # attached message, that message's own property context.
                    if len(payload) >= 4:
                        if subs is None:
                            subs = self.subnodes(bid_sub)
                        props[pid] = (ptype, subs.get(struct.unpack_from("<I", payload, 0)[0]))
                    continue
                if ptype == PT_UNICODE:
                    props[pid] = (ptype, payload.decode("utf-16-le", errors="replace").rstrip("\x00"))
                elif ptype == PT_STRING8:
                    props[pid] = (ptype, payload.decode(codepage or "cp1252", errors="replace").rstrip("\x00"))
                elif ptype == PT_SYSTIME:
                    props[pid] = (ptype, _filetime(payload))
                elif ptype == PT_INT64:
                    props[pid] = (ptype, struct.unpack("<q", payload[:8])[0])
                elif ptype == PT_DOUBLE:
                    props[pid] = (ptype, struct.unpack("<d", payload[:8])[0])
                else:
                    props[pid] = (ptype, payload)
            except (struct.error, PstError):
                continue
        return props

    # -- folders and messages -------------------------------------------------

    @staticmethod
    def _text(props, pid):
        v = props.get(pid)
        if v is None:
            return ""
        return v[1] if isinstance(v[1], str) else ""

    def folder_path(self, nid, cache):
        """Inbox/Cases/2019 -- the display names up the parent chain."""
        if nid in cache:
            return cache[nid]
        parts = []
        seen = set()
        cur = nid
        while cur and cur not in seen and cur != NID_ROOT_FOLDER:
            seen.add(cur)
            entry = self.nbt.get(cur)
            if entry is None:
                break
            try:
                name = self._text(self.property_context(entry.bid_data, entry.bid_sub), PR_DISPLAY_NAME)
            except PstError:
                name = ""
            parts.append(name or "(folder %d)" % cur)
            cur = entry.nid_parent
        path = "/".join(reversed(parts))
        cache[nid] = path
        return path

    def _message(self, nid, bid_data, bid_sub, folder, depth):
        props = self.property_context(bid_data, bid_sub)
        subject = self._text(props, PR_SUBJECT)
        if len(subject) >= 2 and subject[0] == "\x01":
            subject = subject[2:]                       # a prefix-length marker, not text
        body = self._text(props, PR_BODY)
        if not body:
            html = props.get(PR_HTML)
            if html and isinstance(html[1], (bytes, str)):
                body = ("HTML", html[1])
            else:
                rtf = props.get(PR_RTF_COMPRESSED)
                if rtf and isinstance(rtf[1], bytes):
                    body = ("RTF", rtf[1])
        attachments = []
        if depth < 3:
            for sub_nid, (sub_bd, sub_bs) in self.subnodes(bid_sub).items():
                if sub_nid & 0x1F != NID_TYPE_ATTACHMENT:
                    continue
                try:
                    ap = self.property_context(sub_bd, sub_bs)
                except PstError:
                    continue
                name = (self._text(ap, PR_ATTACH_LONG_NAME) or self._text(ap, PR_ATTACH_SHORT_NAME)
                        or self._text(ap, PR_DISPLAY_NAME))
                method = ap.get(PR_ATTACH_METHOD, (PT_INT32, ATTACH_BY_VALUE))[1]
                data = ap.get(PR_ATTACH_DATA)
                embedded = None
                payload = None
                if data is not None:
                    if data[0] == PT_OBJECT and data[1] is not None:
                        ebd, ebs = data[1]
                        try:
                            embedded = self._message(0, ebd, ebs, folder, depth + 1)
                        except PstError:
                            embedded = None
                    elif isinstance(data[1], bytes):
                        payload = data[1]
                attachments.append(Attachment(name, method, payload, embedded))
        sender = self._text(props, PR_SENDER_NAME) or self._text(props, PR_SENT_REPRESENTING_NAME)
        sender_email = (self._text(props, PR_SENDER_SMTP) or self._text(props, PR_SENT_REPRESENTING_SMTP)
                        or self._text(props, PR_SENDER_EMAIL) or self._text(props, PR_SENT_REPRESENTING_EMAIL))
        sent = props.get(PR_CLIENT_SUBMIT_TIME, (None, None))[1]
        delivered = props.get(PR_MESSAGE_DELIVERY_TIME, (None, None))[1]
        return Message(nid, folder, subject, sender, sender_email,
                       self._text(props, PR_DISPLAY_TO), self._text(props, PR_DISPLAY_CC), self._text(props, PR_DISPLAY_BCC),
                       sent, delivered, self._text(props, PR_MESSAGE_CLASS), body, attachments)

    def messages(self):
        """Every message in the file, in node order, with its folder path."""
        cache = {}
        for nid in sorted(self.nbt):
            if nid & 0x1F != NID_TYPE_MESSAGE:
                continue
            entry = self.nbt[nid]
            folder = self.folder_path(entry.nid_parent, cache)
            try:
                yield self._message(nid, entry.bid_data, entry.bid_sub, folder, 0)
            except PstError:
                continue

    def message_count(self):
        return sum(1 for nid in self.nbt if nid & 0x1F == NID_TYPE_MESSAGE)


# -- compressed RTF ([MS-OXRTFCP]) -- used by PST and MSG bodies -------------------

_LZFU_DICT = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
              b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0\\blue0"
              b"\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")


def decompress_rtf(data):
    """PidTagRtfCompressed -> RTF bytes."""
    if len(data) < 16:
        raise ValueError("compressed RTF too short")
    _comp, raw_size, kind = struct.unpack_from("<III", data, 0)
    if kind == 0x414C454D:                                  # "MELA": stored, not compressed
        return data[16:16 + raw_size]
    if kind != 0x75465A4C:                                  # "LZFu"
        raise ValueError("unknown compressed-RTF signature %#x" % kind)
    buf = bytearray(4096)
    buf[:len(_LZFU_DICT)] = _LZFU_DICT
    wp = len(_LZFU_DICT)
    out = bytearray()
    i = 16
    n = len(data)
    while i < n and len(out) < raw_size:
        flags = data[i]
        i += 1
        for bit in range(8):
            if i >= n or len(out) >= raw_size:
                break
            if not (flags >> bit) & 1:
                b = data[i]
                i += 1
                buf[wp] = b
                wp = (wp + 1) % 4096
                out.append(b)
            else:
                if i + 1 >= n:
                    break
                ref = (data[i] << 8) | data[i + 1]
                i += 2
                offset = ref >> 4
                length = (ref & 0x0F) + 2
                if offset == wp:
                    return bytes(out)                       # the end marker
                for _ in range(length):
                    b = buf[offset]
                    offset = (offset + 1) % 4096
                    buf[wp] = b
                    wp = (wp + 1) % 4096
                    out.append(b)
    return bytes(out)
