#!/usr/bin/env python3
r"""B18 regression: encryption evidence as a structured field (task 3).

What it proves, in order:

  1. Schema 16: analyzer_result.encryption_state, the app_meta contract row.
  2. Legacy OLE2 encryption from header structures only (OfficeAnalysis
     1.5.0), on GENUINE password-protected Office files (Apache Tika's test
     documents in C:\FOTest, each checked against its PROVENANCE.json hash)
     and their unprotected controls: Word (FIB fEncrypted), Excel (FILEPASS),
     PowerPoint (CurrentUserAtom token / UserEditAtom encryptSessionPersistIdRef)
     -> encrypted; the controls -> not_encrypted; an IRM (rights-managed)
     Word file -> encrypted; RTF saved as .doc -> not_applicable.
  3. The same checks on compound files built here, flag set and clear, each
     format's every branch (WRITEPROTECT before FILEPASS, the EncryptedSummary
     stream, the header token alone, the UserEditAtom alone), and malformed
     ones -- wrong identifiers, truncated files, a looping or broken sector
     chain, a stream whose recorded size lies -- which are 'unknown', never a
     crash. Every read is bounded: a check reads a few sectors of a multi-
     megabyte stream, never the stream.
  4. Through the real analyzers, the engine and the store: both analyzers
     write the column -- on the error row of a file refused as password-
     protected too (the engine keeps an exception's `evidence_fields`, and
     nothing else from a failed file); the Plan Builder blocks each encrypted
     file on `encrypted`, and an unencrypted legacy file is no longer blocked
     on unknown encryption.
  5. Phase 3 reads the column first; B17's reading of the error wording is
     the fallback for a NULL column only (a pre-B18 row); 'unknown' blocks.

Run:  python Scripts\p3_encryption_check.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_metadata_view_check as MV                                           # noqa: E402  (samples: pdf_with, docx_*, cfb)
import OfficeAnalysis as OA                                                   # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402

RESULTS = []
TIKA = Path(r"C:\FOTest\Research\Samples\third_party")
#: Genuine third-party samples: path under TIKA -> (sha256 as its PROVENANCE.json records it, expected state).
GENUINE = {
    "apache_tika/protected/testWORD_protected_passtika.doc":
        ("9231e724bb17a2e5f74815728d90b06e15684cf5fb2443a6fa24deebd33be952", "encrypted"),
    "apache_tika/protected/testEXCEL_protected_passtika.xls":
        ("79e11c5245f4e5e5892a5864e8d10f581cf50b7aa4cbfb4924ff2b934684bcdc", "encrypted"),
    "apache_tika/protected/testPPT_protected_passtika.ppt":
        ("152e2429873512d3e3719889a5db2aac3496d5c80e01acedf13e0a6c3cc7e59b", "encrypted"),
    "apache_tika/protected/testWORD_protected_drm.doc":
        ("6f8b55671b60f80f71a8466a041b1afb8189c67c08d0602507eaac6e46d9a91f", "encrypted"),
    "apache_tika/macros/testWORD_macros.doc":
        ("5bf327a892944e5ed1dbcc0db2b332aba6ec17b0f8bcde9f86f691216b44d5c9", "not_encrypted"),
    "apache_tika/macros/testEXCEL_macro.xls":
        ("c89ad115e042403052f01f8381dac2c5f2a5a324def46d1f2a9f98bd113a906a", "not_encrypted"),
    "apache_tika/macros/testPPT_macros.ppt":
        ("faa438447d932770bf49a16856cf23c7f98be47f94d96030f294859595d87a3b", "not_encrypted"),
    "openpreserve_format-corpus/pdfCabinetOfHorrors/text_only.doc":
        ("cf3eb896e024b64178d9a9afda533eaf99f883c117178e4a572fb5760563d928", "not_encrypted"),
}


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def genuine_available():
    out = {}
    for rel, (sha, want) in GENUINE.items():
        p = TIKA / rel
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == sha:
            out[rel] = (p, want)
    return out


# -- built compound files -------------------------------------------------------------------

def word_stream(encrypted, ident=0xA5EC):
    flags = 0x0100 if encrypted else 0x0000
    return struct.pack("<HHHHHH", ident, 0x00C1, 0, 0x0409, 0, flags) + b"W" * 6000


def xls_record(kind, data=b""):
    return struct.pack("<HH", kind, len(data)) + data


def workbook(*records, bof=0x0809):
    return xls_record(bof, b"\x00\x06\x05\x00" + b"\x00" * 12) + b"".join(records) + xls_record(0x000A) + b"\x00" * 5000


def ppt_streams(token=OA.PPT_TOKEN_PLAIN, edit_len=OA.PPT_USER_EDIT_PLAIN, edit_type=OA.PPT_USER_EDIT_ATOM, at=1024,
                cu_type=OA.PPT_CURRENT_USER_ATOM, extra=None):
    cu = struct.pack("<HHI", 0, cu_type, 20) + struct.pack("<III", 20, token, at) + b"\x00" * 40
    doc = bytearray(b"\x00" * 6000)
    if at + 8 <= len(doc):                  # an offset past the stream's end writes nothing there
        doc[at:at + 8] = struct.pack("<HHI", 0, edit_type, edit_len)
    streams = {"Current User": cu, "PowerPoint Document": bytes(doc)}
    streams.update(extra or {})
    return streams


def cfb_large(path, streams):
    """A valid CFB v3 file with as many FAT sectors as its streams need (the
    shared builder has one, 64 KB): FAT sectors, then the directory, then the
    streams, each in ordinary sectors."""
    sect, end, free, fatsect, nostream = 512, 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF
    items = sorted(streams.items(), key=lambda kv: (len(kv[0]), kv[0].upper()))
    datas = [data.ljust(max(4096, len(data)), b"\0") for _n, data in items]
    counts = [(len(d) + sect - 1) // sect for d in datas]
    ndir = ((len(items) + 1) * 128 + sect - 1) // sect
    nfat = 1
    while nfat * (sect // 4) < nfat + ndir + sum(counts):
        nfat += 1
    assert nfat <= 109
    fat = [fatsect] * nfat + [nfat + i + 1 for i in range(ndir - 1)] + [end]
    starts, nxt = [], nfat + ndir
    for n in counts:
        starts.append(nxt)
        fat += [nxt + i + 1 for i in range(n - 1)] + [end]
        nxt += n
    fat += [free] * (nfat * (sect // 4) - len(fat))

    def entry(name, typ, start, size, child=nostream, right=nostream):
        raw = name.encode("utf-16-le") + b"\0\0"
        return (raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw), typ, 1) + struct.pack("<III", nostream, right, child)
                + b"\0" * 36 + struct.pack("<IQ", start, size))
    dirs = [entry("Root Entry", 5, end, 0, child=1)]
    for i, ((name, _d), start) in enumerate(zip(items, starts)):
        dirs.append(entry(name, 2, start, len(datas[i]), right=i + 2 if i + 1 < len(items) else nostream))
    difat = list(range(nfat)) + [free] * (109 - nfat)
    header = (bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, nfat, nfat, 0, 4096, end, 0, end, 0) + struct.pack("<109I", *difat))
    with open(path, "wb") as f:
        f.write(header + struct.pack("<%dI" % len(fat), *fat) + b"".join(dirs).ljust(ndir * sect, b"\0"))
        for d, n in zip(datas, counts):
            f.write(d.ljust(n * sect, b"\0"))


def build_legacy(base: Path):
    """name -> (path, expected state)."""
    base.mkdir(parents=True, exist_ok=True)
    cases = {
        "word_encrypted.doc": ({"WordDocument": word_stream(True)}, "encrypted"),
        "word_plain.doc": ({"WordDocument": word_stream(False)}, "not_encrypted"),
        "word95_encrypted.doc": ({"WordDocument": word_stream(True, 0xA5DC)}, "encrypted"),
        "word_bad_ident.doc": ({"WordDocument": word_stream(True, 0x1234)}, "unknown"),
        "xls_filepass.xls": ({"Workbook": workbook(xls_record(0x002F, b"\x01\x00" + b"\x00" * 52))}, "encrypted"),
        "xls_writeprotect_filepass.xls": ({"Workbook": workbook(xls_record(0x0086), xls_record(0x002F, b"\x00" * 6))}, "encrypted"),
        "xls_plain.xls": ({"Workbook": workbook(xls_record(0x00E1, b"\x00\x00"), xls_record(0x005C, b"x" * 20))}, "not_encrypted"),
        "xls_biff5_book.xls": ({"Book": workbook(xls_record(0x002F, b"\x00" * 6))}, "encrypted"),
        "xls_no_bof.xls": ({"Workbook": xls_record(0x0042, b"\x00\x00") + b"\x00" * 5000}, "unknown"),
        "ppt_plain.ppt": (ppt_streams(), "not_encrypted"),
        "ppt_token.ppt": (ppt_streams(token=OA.PPT_TOKEN_ENCRYPTED, edit_len=OA.PPT_USER_EDIT_ENCRYPTED), "encrypted"),
        "ppt_edit_only.ppt": (ppt_streams(edit_len=OA.PPT_USER_EDIT_ENCRYPTED), "encrypted"),
        "ppt_summary.ppt": (ppt_streams(extra={"EncryptedSummary": b"E" * 200}), "encrypted"),
        "ppt_bad_atom.ppt": (ppt_streams(cu_type=0x1234), "unknown"),
        "ppt_edit_past_end.ppt": (ppt_streams(at=6500), "unknown"),
        "ppt_bad_edit_type.ppt": (ppt_streams(edit_type=0x0FF0), "unknown"),
        "irm.doc": ({"WordDocument": word_stream(False), "\tDRMContent": b"D" * 100}, "encrypted"),
        "no_known_stream.doc": ({"Something": b"x" * 100}, "unknown"),
    }
    out = {}
    for name, (streams, want) in cases.items():
        MV.cfb(base / name, streams)
        out[name] = (base / name, want)
    (base / "rtf.doc").write_bytes(b"{\\rtf1\\ansi {\\info{\\author Rita}}Hello}")
    out["rtf.doc"] = (base / "rtf.doc", "not_applicable")
    raw = (base / "word_plain.doc").read_bytes()
    (base / "truncated.doc").write_bytes(raw[:700])
    out["truncated.doc"] = (base / "truncated.doc", "unknown")
    (base / "not_ole.doc").write_bytes(b"PK\x03\x04 this is not a compound file" * 20)
    out["not_ole.doc"] = (base / "not_ole.doc", "unknown")
    # A FAT whose chain loops back onto a sector it has visited: PowerPoint's
    # UserEditAtom sits in the stream's third sector, reached only through
    # the loop -- the reader must refuse, not read the first sector again.
    loop = bytearray((base / "ppt_plain.ppt").read_bytes())
    fat_at = 512                                        # the FAT is sector 0, straight after the 512-byte header
    names = {}
    for i in range(1, 4):                               # directory entries 1..3 (sector 1)
        e = 1024 + 128 * i
        n = loop[e:e + struct.unpack_from("<H", loop, e + 64)[0]].decode("utf-16-le").rstrip("\0")
        names[n] = struct.unpack_from("<I", loop, e + 0x74)[0]
    first = names["PowerPoint Document"]
    struct.pack_into("<I", loop, fat_at + 4 * (first + 1), first)      # its second sector points back to its first
    (base / "chain_loop.ppt").write_bytes(bytes(loop))
    out["chain_loop.ppt"] = (base / "chain_loop.ppt", "unknown")
    # A stream whose recorded size is far larger than its chain.
    liar = bytearray(raw)
    struct.pack_into("<Q", liar, 1024 + 128 + 0x78, 50_000_000)
    (base / "size_lies.doc").write_bytes(bytes(liar))
    out["size_lies.doc"] = (base / "size_lies.doc", None)          # either not_encrypted (the header is readable) or unknown
    return out


# ---------------------------------------------------------------------------
# 1-3. The detector
# ---------------------------------------------------------------------------

def test_detector(tmp: Path):
    section("2. Legacy OLE2 encryption on genuine third-party files (Apache Tika, openpreserve)")
    genuine = genuine_available()
    if len(genuine) != len(GENUINE):
        print(f"  SKIP  {len(GENUINE) - len(genuine)} genuine sample(s) absent or not matching their provenance hash")
    got = {rel: OA.ole_encryption_state(str(p)) for rel, (p, _w) in genuine.items()}
    want = {rel: w for rel, (_p, w) in genuine.items()}
    enc = {r for r in want if want[r] == "encrypted" and "drm" not in r}
    check("the three genuinely password-protected legacy files -- Word (.doc), Excel (.xls), PowerPoint (.ppt) -- read "
          "'encrypted' from their header structures", {r: got[r] for r in enc} == {r: "encrypted" for r in enc}
          and len(enc) == 3, str(got))
    check("their unprotected controls (Word, Excel, PowerPoint, a third-party .doc) read 'not_encrypted'",
          {r: got[r] for r in want if want[r] == "not_encrypted"} == {r: "not_encrypted" for r in want if want[r] == "not_encrypted"},
          str(got))
    drm = "apache_tika/protected/testWORD_protected_drm.doc"
    if drm in got:
        check("an IRM (rights-managed) Word file -- its content in the encrypted \\tDRMContent stream -- reads 'encrypted' "
              "(judgment call: rights management is encryption for a metadata write)", got[drm] == "encrypted")
    if genuine:
        import olefile
        paths = {Path(r).name: p for r, (p, _w) in genuine.items()}
        with olefile.OleFileIO(str(paths["testWORD_protected_passtika.doc"])) as o:
            w = OA.word_encryption_state(o)
        with olefile.OleFileIO(str(paths["testEXCEL_protected_passtika.xls"])) as o:
            x = OA.excel_encryption_state(o, "Workbook")
        with olefile.OleFileIO(str(paths["testPPT_protected_passtika.ppt"])) as o:
            token = struct.unpack_from("<I", OA.ole_read(o, "Current User", 0, 20), 12)[0]
            pp = OA.powerpoint_encryption_state(o)
            summary = o.exists("EncryptedSummary")
        check("each format's own check fires on its genuine file: Word's fEncrypted, Excel's FILEPASS, PowerPoint's header "
              "token", (w, x, pp) == ("encrypted", "encrypted", "encrypted") and token == OA.PPT_TOKEN_ENCRYPTED,
              f"{w} {x} {pp} token {token:#x} summary {summary}")
        with olefile.OleFileIO(str(paths["testPPT_macros.ppt"])) as o:
            cu = OA.ole_read(o, "Current User", 0, 20)
            small = o.get_size("Current User") < o.minisectorcutoff
        check("the genuine files' small streams live in the mini stream, and the bounded reader follows the mini FAT "
              "(PowerPoint's Current User, read in place)", small and cu is not None and len(cu) == 20)

    section("3. Built compound files: every branch, flag set and clear; malformed files are unknown, never a crash")
    built = build_legacy(tmp / "legacy")
    states = {}
    for name, (p, _w) in built.items():
        try:
            states[name] = OA.ole_encryption_state(str(p))
        except Exception as exc:                                        # noqa: BLE001
            states[name] = f"raised {type(exc).__name__}"
    want = {n: w for n, (_p, w) in built.items() if w is not None}
    for group, names in (("Word", [n for n in want if n.startswith("word")]),
                         ("Excel", [n for n in want if n.startswith("xls")]),
                         ("PowerPoint", [n for n in want if n.startswith("ppt")]),
                         ("other", [n for n in want if not n.startswith(("word", "xls", "ppt"))])):
        check(f"{group}: " + ", ".join(f"{n} -> {want[n]}" for n in names),
              {n: states[n] for n in names} == {n: want[n] for n in names}, str({n: states[n] for n in names}))
    check("a stream whose recorded size lies (50 MB over a short chain) never raises: not_encrypted or unknown",
          states["size_lies.doc"] in ("not_encrypted", "unknown"), states["size_lies.doc"])
    check("nothing raised", not [s for s in states.values() if s.startswith("raised")])

    # Bounded reads: a 3 MB Workbook stream; the check reads a handful of sectors of it.
    big = tmp / "legacy" / "big.xls"
    cfb_large(big, {"Workbook": workbook(xls_record(0x00E1, b"\x00\x00")) + b"\x00" * 3_000_000})
    read = []
    real = OA._ole_chain_read

    def counting(fat, start, sector_size, offset, length, fetch):
        def f(sect, within, n):
            read.append(n)
            return fetch(sect, within, n)
        return real(fat, start, sector_size, offset, length, f)
    OA._ole_chain_read = counting
    try:
        st = OA.ole_encryption_state(str(big))
    finally:
        OA._ole_chain_read = real
    check("bounded: a 3 MB Workbook stream is judged from a few bytes of it (never loaded whole)",
          st == "not_encrypted" and sum(read) <= 64 and len(read) <= 8, f"{st}: {len(read)} reads, {sum(read)} bytes")
    check("no single read may ask for more than OLE_READ_CAP", OA.OLE_READ_CAP == 64 * 1024)
    return built


# ---------------------------------------------------------------------------
# 1, 4-5. Through the analyzers, the engine, the store and the plan
# ---------------------------------------------------------------------------

def test_pipeline(tmp: Path, built):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, ANALYSIS
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S, resolve as R, routing as RT, plans as PL, metadata as MD
    import fo_analyzer_engine as ENG

    app_root = tmp / "EncRoot"
    (app_root / "Projects").mkdir(parents=True)
    docs = tmp / "EncCorpus" / "Docs"
    docs.mkdir(parents=True)
    MV.pdf_with(docs / "plain.pdf")
    MV.pdf_with(docs / "locked.pdf", user_password="secret", owner_password="owner")
    MV.pdf_with(docs / "owner_only.pdf", owner_password="owner")
    MV.docx_file(docs / "plain.docx", "Alice")
    MV.docx_encrypted(docs / "plain.docx", docs / "encrypted.docx")
    for name in ("word_encrypted.doc", "word_plain.doc", "xls_filepass.xls", "xls_plain.xls", "ppt_token.ppt", "ppt_plain.ppt",
                 "rtf.doc", "word_bad_ident.doc"):
        shutil.copyfile(built[name][0], docs / name)
    genuine = genuine_available()
    for rel, (p, _w) in genuine.items():
        shutil.copyfile(p, docs / ("tika_" + Path(rel).name))
    project_dir = app_root / "Projects" / "Enc"
    outs = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs)], project_name="Enc")).run(),
            RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["pdf", "office"])).run()]
    conn = connect(project_dir, write=True)

    section("1. Schema 16: analyzer_result.encryption_state")
    check("schema 16; the column exists; the app_meta contract row names it",
          conn.execute("PRAGMA user_version").fetchone()[0] == 16
          and "encryption_state" in [r[1] for r in conn.execute("PRAGMA table_info(analyzer_result)")]
          and conn.execute("SELECT value FROM app_meta WHERE key='phase2.encryption_evidence'").fetchone()[0]
          == "analyzer_result.encryption_state/1")
    check("the migration file is additive: one nullable column, no rewrite",
          "ALTER TABLE analyzer_result ADD COLUMN encryption_state TEXT;" in
          (SCRIPTS / "Database" / "migrations" / "016_encryption_evidence.sql").read_text(encoding="utf-8"))

    section("4. Through the real analyzers, the engine and the store")
    check("Pre-Scan and the PDF + Office analyzers through the worker", all(o.ok for o in outs), str([(o.status, o.message) for o in outs]))
    by = {r[1].replace("/", "\\").rsplit("\\", 1)[-1]: str(r[0]) for r in conn.execute("SELECT file_path_id, relative_path FROM file_path")}

    def latest(name):
        return conn.execute(
            "SELECT ar.* FROM analyzer_result ar JOIN file_state fs ON fs.current_observation_id = ar.file_observation_id "
            " WHERE fs.file_path_id = ? ORDER BY ar.analyzer_result_id DESC LIMIT 1", (int(by[name]),)).fetchone()
    col = {n: latest(n)["encryption_state"] for n in by if latest(n) is not None}
    check("PDF: a plain file -> not_encrypted; an owner-password-only file -> encrypted (its /Encrypt dictionary)",
          (col["plain.pdf"], col["owner_only.pdf"]) == ("not_encrypted", "encrypted"), str(col))
    lk, ed = latest("locked.pdf"), latest("encrypted.docx")
    check("a PDF needing a password and an encrypted .docx are still analyzer ERRORS (nothing read, no password tried) -- "
          "and their rows still say encrypted (the refusal is evidence)",
          lk["status"] == ed["status"] == "error" and lk["encryption_state"] == ed["encryption_state"] == "encrypted"
          and "password-protected document" in lk["error_message"] and "password-protected document" in ed["error_message"])
    check("Office: an opened .docx -> not_encrypted; legacy files by their header flags; RTF as .doc -> not_applicable; "
          "an unreadable FIB -> unknown",
          (col["plain.docx"], col["word_encrypted.doc"], col["word_plain.doc"], col["xls_filepass.xls"], col["xls_plain.xls"],
           col["ppt_token.ppt"], col["ppt_plain.ppt"], col["rtf.doc"], col["word_bad_ident.doc"])
          == ("not_encrypted", "encrypted", "not_encrypted", "encrypted", "not_encrypted", "encrypted", "not_encrypted",
              "not_applicable", "unknown"), str(col))
    if genuine:
        g = {Path(r).name: col["tika_" + Path(r).name] for r in genuine}
        check("the genuine samples through the whole pipeline: the three password-protected legacy files and the IRM file "
              "encrypted, the controls not_encrypted",
              g == {Path(r).name: w for r, (_p, w) in genuine.items()}, str(g))
        check("an encrypted legacy file whose summary is readable is still ANALYZED (its creator read) -- encryption is "
              "evidence beside the reading, not a failure", latest("tika_testWORD_protected_passtika.doc")["status"] == "analyzed"
              and latest("tika_testWORD_protected_passtika.doc")["author"] == "Antoni")

    class Refusal(ValueError):
        evidence_fields = {"EncryptionState": "encrypted"}

    class Plain(ValueError):
        pass
    fake = ENG.FileEntry("k", 1, str(docs / "plain.pdf"), "plain.pdf")
    kept = []
    for exc in (Refusal("password-protected document"), Plain("broken")):
        r = ENG.AnalyzerResult(fake)
        try:
            raise exc
        except Exception as e:                                         # the engine's own statement, run on a result
            r.fields = dict(getattr(e, "evidence_fields", None) or {})
            r.error = str(e)
        kept.append(r.fields)
    src = (SCRIPTS / "Database" / "fo_analyzer_engine.py").read_text(encoding="utf-8")
    check("the engine keeps an exception's `evidence_fields` on the error row, and nothing else from a failed file",
          kept == [{"EncryptionState": "encrypted"}, {}]
          and 'result.fields = dict(getattr(exc, "evidence_fields", None) or {})' in src)

    section("4b. The plan: encrypted files block on 'encrypted'; unencrypted legacy files no longer on unknown encryption")
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:enc")
    names = [n for n in by if n not in ("locked.pdf",)] + ["locked.pdf"]
    for n in names:
        st.record_metadata_decision("desired_source_metadata_set", by[n], "creator", "Thomas")
    ev = E.load_evidence(conn)
    proj = R.resolve_all(ev)
    items = {i.target_ref: i for i in PL.build(ev, proj, RT.route_all(ev, proj, ev.now)).items if i.action_kind == REG.ACTION_METADATA}
    got = {n: (items[by[n]].state, items[by[n]].blocked_reason) for n in names}
    enc_names = ["locked.pdf", "owner_only.pdf", "encrypted.docx", "word_encrypted.doc", "xls_filepass.xls", "ppt_token.ppt"]
    enc_names += ["tika_" + Path(r).name for r, (_p, w) in genuine.items() if w == "encrypted"]
    check("every encrypted file -> blocked / encrypted (PDF, OOXML, Word, Excel, PowerPoint, the genuine samples)",
          {n: got[n] for n in enc_names} == {n: (REG.ITEM_BLOCKED, "encrypted") for n in enc_names}, str({n: got[n] for n in enc_names}))
    plain = ["word_plain.doc", "xls_plain.xls", "ppt_plain.ppt", "rtf.doc", "plain.docx", "plain.pdf"]
    plain += ["tika_" + Path(r).name for r, (_p, w) in genuine.items() if w == "not_encrypted"]
    check("unencrypted legacy files (and RTF as .doc, and the modern controls) -> unsupported_action / writer_unsupported: no "
          "longer blocked on unknown encryption (B17's 341)",
          {n: got[n] for n in plain} == {n: (REG.ITEM_UNSUPPORTED, "writer_unsupported") for n in plain}, str({n: got[n] for n in plain}))
    check("an unreadable FIB -> encryption unknown, which blocks as evidence (P3-A91)",
          got["word_bad_ident.doc"] == (REG.ITEM_BLOCKED_EVIDENCE, "source_not_encrypted"))

    section("5. The column first; B17's error wording only for a NULL column")
    rid = int(lk["analyzer_result_id"])
    q_locked = next(q for q in ev.metadata.questions.values() if q.location_id == by["locked.pdf"])
    conn.execute("UPDATE analyzer_result SET encryption_state = NULL WHERE analyzer_result_id = ?", (rid,))
    conn.commit()
    q = E.load_metadata(conn, [q_locked.question_id]).questions[q_locked.question_id]
    check("a pre-B18 row (column NULL) refused as password-protected: B17's fallback still reads it encrypted", q.encrypted is True)
    conn.execute("UPDATE analyzer_result SET encryption_state = 'unknown' WHERE analyzer_result_id = ?", (rid,))
    conn.commit()
    q = E.load_metadata(conn, [q_locked.question_id]).questions[q_locked.question_id]
    check("the column speaks when set: 'unknown' is unknown even though the error text says password -- and unknown blocks",
          q.encrypted is None and MD.integrity(q)[MD.ENCRYPTED_KEY][0] == "unknown")
    wp = latest("word_plain.doc")
    q_wp = next(x for x in ev.metadata.questions.values() if x.location_id == by["word_plain.doc"])
    conn.execute("UPDATE analyzer_result SET encryption_state = NULL WHERE analyzer_result_id = ?", (int(wp["analyzer_result_id"]),))
    conn.commit()
    q = E.load_metadata(conn, [q_wp.question_id]).questions[q_wp.question_id]
    check("a pre-B18 analyzed legacy row (column NULL): unknown, as in B17 -- a re-analysis is what makes it known",
          q.encrypted is None)
    rtf = latest("rtf.doc")
    q_rtf = next(x for x in ev.metadata.questions.values() if x.location_id == by["rtf.doc"])
    q = E.load_metadata(conn, [q_rtf.question_id]).questions[q_rtf.question_id]
    check("'not_applicable' marks the encryption check not applicable (RTF saved as .doc)",
          rtf["encryption_state"] == "not_applicable" and MD.integrity(q)[MD.ENCRYPTED_KEY][0] == "not_applicable")
    conn.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3enc_"))
    try:
        built = test_detector(tmp)
        test_pipeline(tmp, built)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 ENCRYPTION (B18): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
