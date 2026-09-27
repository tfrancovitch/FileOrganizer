#!/usr/bin/env python3
r"""B17 regression: the Metadata page, signature and encryption evidence, the plan notes.

What it proves, in order:

  1. Review fix R1: store.active() / active_for() name their decision scope --
     the retention scope excludes a metadata decision, the metadata scope
     includes it, 'all' has both; the default is B16's (retention).
  2. Evidence, through the REAL analyzers (PDFAnalysis 1.2.0, OfficeAnalysis
     1.4.0), the store and the Plan Builder, on files built here: a PDF with a
     filled signature field, an empty signature placeholder, a certified PDF
     (/Perms /DocMDP), a real third-party PDF portfolio that embeds a certified
     document (C:\FOTest's openpreserve sample, checked against its
     provenance hash; skipped if absent), a PDF needing a password, an
     owner-password PDF, a signed .docx (_xmlsignatures/), an encrypted .docx
     (msoffcrypto), legacy OLE2 .doc files with and without a signature
     stream, RTF saved as .doc, and unsigned controls -- plus a result as B16
     wrote it (no signature_state). Every outcome: signed -> signed_or_certified;
     encrypted -> encrypted; unsigned and unencrypted -> unsupported_action /
     writer_unsupported; absent evidence -> unknown, which blocks.
  3. The plan notes: S1 (a metadata item on a file in an exact-duplicate group
     says it would leave the group -- informational, not a precondition) and
     S2 (a revision that removes a location AND updates its metadata warns on
     both items; neither reordered or dropped). A revision without metadata
     items fingerprints EXACTLY as the phase3-b16 code does (the B16 package is
     materialized from git and run on the same model).
  4. The window, headless: every entry point (the side panel's Metadata, the
     Details pane's "Metadata for this file", a plan's metadata item); every
     action (save desired, invalid input refused, clear with its own
     confirmation, leave unchanged, defer with a trigger, undo; tags and a
     note) recorded with the right kind and provenance; an unknown field never
     offers Clear by default; viewing creates no question; the queue --
     Left Unchanged out, back after a re-analysis; a fired deferral back;
     filters; "Currently clear". Zero source reads and writes around the
     window sessions.

Run:  python Scripts\p3_metadata_view_check.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import io
import zipfile
from pathlib import Path

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "Database"))

import p3_plan_check as PC                                                   # noqa: E402  (ReadGuard)
BASE = PC.BASE                                                               # noqa: E402  (p3_regression helpers)
from Phase3 import resolve as R                                              # noqa: E402
from Phase3 import routing as RT                                             # noqa: E402
from Phase3 import registry as REG                                           # noqa: E402
from Phase3 import plans as PL                                               # noqa: E402
from Phase3 import metadata as MD                                            # noqa: E402

RESULTS = []
LOCATION, MQ = REG.LOCATION, REG.METADATA_QUESTION
REMOVE, META = REG.ACTION_REMOVE, REG.ACTION_METADATA
READY, BLOCKED, BLOCKED_EV, UNSUPPORTED = REG.ITEM_READY, REG.ITEM_BLOCKED, REG.ITEM_BLOCKED_EVIDENCE, REG.ITEM_UNSUPPORTED
PORTFOLIO = Path(r"C:\FOTest\Research\Samples\third_party\openpreserve_format-corpus\pdfCabinetOfHorrors"
                 r"\digitally_signed_3D_Portfolio.pdf")
PORTFOLIO_SHA256 = "8f8131d879037c3ed620331615606e8ba2e2e830578a1373b0648375c73f8324"   # its PROVENANCE.json entry


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if (detail and not ok) else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
# Samples built here
# ---------------------------------------------------------------------------

def pdf_with(path, kind=None, user_password=None, owner_password=None, author="Alice"):
    """kind: None | 'filled_sig' (a signature field with a value) | 'empty_sig'
    (a placeholder waiting to be signed) | 'certified' (/Perms /DocMDP)."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject, ByteStringObject, NumberObject
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_metadata({"/Author": author, "/Title": "Sample"})
    if kind:
        field = DictionaryObject({NameObject("/FT"): NameObject("/Sig"), NameObject("/T"): TextStringObject("Signature1"),
                                  NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                                  NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4)})
        if kind in ("filled_sig", "certified"):
            sig = DictionaryObject({NameObject("/Type"): NameObject("/Sig"), NameObject("/Filter"): NameObject("/Adobe.PPKLite"),
                                    NameObject("/SubFilter"): NameObject("/adbe.pkcs7.detached"),
                                    NameObject("/Contents"): ByteStringObject(b"\x00" * 64)})
            field[NameObject("/V")] = w._add_object(sig)
        fref = w._add_object(field)
        w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([fref]),
                                                                     NameObject("/SigFlags"): NumberObject(3)})
        if kind == "certified":
            w._root_object[NameObject("/Perms")] = DictionaryObject({NameObject("/DocMDP"): field[NameObject("/V")]})
    if user_password is not None or owner_password is not None:
        w.encrypt(user_password=user_password or "", owner_password=owner_password)
    with open(path, "wb") as f:
        w.write(f)


def docx_file(path, author, text="Body."):
    import docx
    d = docx.Document()
    d.add_paragraph(text)
    d.core_properties.author = author
    d.save(str(path))


def docx_signed(src, dst):
    """The package with an _xmlsignatures/ part -- presence is what is read."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("_xmlsignatures/sig1.xml", '<?xml version="1.0"?><Signature xmlns="http://www.w3.org/2000/09/xmldsig#"/>')


def docx_encrypted(src, dst, password="pw"):
    """A real password-to-open package (ECMA-376 agile encryption, msoffcrypto)."""
    from msoffcrypto.format.ooxml import OOXMLFile
    with open(src, "rb") as f, open(dst, "wb") as out:
        OOXMLFile(f).encrypt(password, out)


def cfb(path, streams):
    """A minimal, valid OLE2 compound file (CFB v3, 512-byte sectors): one FAT
    sector, one directory sector, the named streams padded past the 4,096-byte
    mini-stream cutoff so each lives in ordinary sectors."""
    sect, end, free, fatsect, nostream = 512, 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD, 0xFFFFFFFF
    items = sorted(streams.items(), key=lambda kv: (len(kv[0]), kv[0].upper()))
    datas = [data.ljust(max(4096, len(data)), b"\0") for _n, data in items]
    counts = [(len(d) + sect - 1) // sect for d in datas]
    fat, starts, nxt = [fatsect, end], [], 2
    for n in counts:
        starts.append(nxt)
        fat += [nxt + i + 1 for i in range(n - 1)] + [end]
        nxt += n
    fat += [free] * (sect // 4 - len(fat))

    def entry(name, typ, start, size, child=nostream, right=nostream):
        raw = name.encode("utf-16-le") + b"\0\0"
        return (raw.ljust(64, b"\0") + struct.pack("<HBB", len(raw), typ, 1) + struct.pack("<III", nostream, right, child)
                + b"\0" * 36 + struct.pack("<IQ", start, size))
    dirs = [entry("Root Entry", 5, end, 0, child=1)]
    for i, ((name, _d), start) in enumerate(zip(items, starts)):
        dirs.append(entry(name, 2, start, len(datas[i]), right=i + 2 if i + 1 < len(items) else nostream))
    header = (bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, end, 0, end, 0) + struct.pack("<I", 0) + struct.pack("<I", free) * 108)
    with open(path, "wb") as f:
        f.write(header + struct.pack("<%dI" % (sect // 4), *fat) + b"".join(dirs).ljust(sect, b"\0"))
        for d, n in zip(datas, counts):
            f.write(d.ljust(n * sect, b"\0"))


def build_corpus(base: Path):
    docs = base / "Docs"
    docs.mkdir(parents=True)
    pdf_with(docs / "plain.pdf")
    shutil.copyfile(docs / "plain.pdf", docs / "plain_copy.pdf")           # an exact-duplicate group (S1, S2)
    pdf_with(docs / "legacy.pdf", author="Lee")                            # its result will read as B16 wrote it
    pdf_with(docs / "filled_sig.pdf", "filled_sig")
    pdf_with(docs / "empty_sig.pdf", "empty_sig")
    pdf_with(docs / "certified.pdf", "certified")
    pdf_with(docs / "locked.pdf", user_password="secret", owner_password="owner")
    pdf_with(docs / "owner_only.pdf", owner_password="owner")
    have_portfolio = PORTFOLIO.is_file() and hashlib.sha256(PORTFOLIO.read_bytes()).hexdigest() == PORTFOLIO_SHA256
    if have_portfolio:
        shutil.copyfile(PORTFOLIO, docs / "portfolio.pdf")
    docx_file(docs / "plain.docx", "Alice")
    docx_file(docs / "fresh.docx", "Frank", "A file nobody has decided anything about.")
    docx_signed(docs / "plain.docx", docs / "signed.docx")
    docx_encrypted(docs / "plain.docx", docs / "encrypted.docx")
    cfb(docs / "plain.doc", {"WordDocument": b"W" * 5000})
    cfb(docs / "signed.doc", {"WordDocument": b"W" * 5000, "_signatures": b"S" * 100})
    (docs / "rtf.doc").write_bytes(b"{\\rtf1\\ansi {\\info{\\author Rita}}Hello}")
    return docs, have_portfolio


# ---------------------------------------------------------------------------
# 1 + 2. Scope, and the evidence through the real analyzers
# ---------------------------------------------------------------------------

def test_project(tmp: Path):
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, DUPLICATES, ANALYSIS
    from Phase2.core import connect
    from Phase3 import evidence as E, store as S

    app_root = tmp / "MetaViewRoot"
    (app_root / "Projects").mkdir(parents=True)
    corpus = tmp / "MetaViewCorpus"
    docs, have_portfolio = build_corpus(corpus)
    project_dir = app_root / "Projects" / "MetaView"
    outs = [RunWorker(app_root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(docs)], project_name="MetaView")).run(),
            RunWorker(app_root, project_dir, RunRequest(DUPLICATES, "Find My Duplicates")).run(),
            RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["pdf", "office"])).run()]
    conn = connect(project_dir, write=True)
    st = S.DecisionStore(conn, actor_id="tester", session_id="session:metaview")
    by = {r[1].replace("/", "\\").rsplit("\\", 1)[-1]: str(r[0]) for r in conn.execute("SELECT file_path_id, relative_path FROM file_path")}

    section("1. Review fix R1: the decision scope is named")
    red = st.record_decision("redundant_location", LOCATION, by["plain.pdf"])
    note = st.record_metadata_decision("project_metadata_set", by["plain.pdf"], value={"field": "note", "value": "scope test"})
    kinds = lambda ds: {d.kind for d in ds}                                  # noqa: E731
    check("active_for(..., SCOPE_RETENTION) -- the default, as in B16 -- excludes the metadata decision",
          kinds(st.active_for(LOCATION, by["plain.pdf"], S.SCOPE_RETENTION)) == {"redundant_location"}
          and kinds(st.active_for(LOCATION, by["plain.pdf"])) == {"redundant_location"})
    check("SCOPE_METADATA includes it and only it; SCOPE_ALL has both",
          kinds(st.active_for(LOCATION, by["plain.pdf"], S.SCOPE_METADATA)) == {"project_metadata_set"}
          and kinds(st.active_for(LOCATION, by["plain.pdf"], S.SCOPE_ALL)) == {"redundant_location", "project_metadata_set"}
          and str(note["decision_id"]) in {d.decision_id for d in st.active(S.SCOPE_METADATA)}
          and str(note["decision_id"]) not in {d.decision_id for d in st.active(S.SCOPE_RETENTION)})
    check("an unknown scope is refused", PC.refused(lambda: st.active("everything")))
    check("the product's call sites name their scope (review.py, imagegame.py)",
          "self.store.active(SCOPE_RETENTION)" in (SCRIPTS / "Phase3" / "review.py").read_text(encoding="utf-8")
          and "active_for(VISUAL_PAIR, p.pair_id, SCOPE_RETENTION)" in (SCRIPTS / "Phase3" / "imagegame.py").read_text(encoding="utf-8"))
    st.withdraw(red["decision_id"])
    st.withdraw(note["decision_id"])

    section("2. Signature and encryption evidence, through the real analyzers, the store and the plan")
    check("Pre-Scan, Find My Duplicates, and the PDF and Office analyzers through the worker", all(o.ok for o in outs),
          str([(o.status, o.message) for o in outs]))

    def latest(name):
        return conn.execute(
            "SELECT ar.* FROM analyzer_result ar JOIN file_state fs ON fs.current_observation_id = ar.file_observation_id "
            " WHERE fs.file_path_id = ? ORDER BY ar.analyzer_result_id DESC LIMIT 1", (int(by[name]),)).fetchone()
    sig = {n: latest(n)["signature_state"] for n in by if latest(n) is not None}
    check("schema 15: analyzer_result.signature_state is a column, written by both analyzers",
          conn.execute("PRAGMA user_version").fetchone()[0] == 15
          and "signature_state" in [r[1] for r in conn.execute("PRAGMA table_info(analyzer_result)")])
    check("PDF: a filled signature field -> signed; /Perms /DocMDP -> certified; an EMPTY placeholder -> unsigned; a plain "
          "PDF -> unsigned", (sig["filled_sig.pdf"], sig["certified.pdf"], sig["empty_sig.pdf"], sig["plain.pdf"]) ==
          ("signed", "certified", "unsigned", "unsigned"), str(sig))
    if have_portfolio:
        check("a real signed file (openpreserve's digitally_signed_3D_Portfolio.pdf, hash as its provenance records): a "
              "portfolio that embeds a certified document reads 'signed' -- presence blocks", sig["portfolio.pdf"] == "signed")
    else:
        print("  SKIP  the third-party signed portfolio (C:\\FOTest sample not present)")
    check("Office: a .docx with an _xmlsignatures/ part -> signed; a plain .docx -> unsigned",
          (sig["signed.docx"], sig["plain.docx"]) == ("signed", "unsigned"))
    check("legacy OLE2: a _signatures stream -> signed; none -> unsigned; RTF saved as .doc -> not_applicable",
          (sig["signed.doc"], sig["plain.doc"], sig["rtf.doc"]) == ("signed", "unsigned", "not_applicable"))
    locked, enc = latest("locked.pdf"), latest("encrypted.docx")
    check("a PDF needing a password and an encrypted .docx: the analyzers' shared wording, no password tried, nothing read",
          locked["status"] == "error" and "password-protected document (an encrypted PDF" in locked["error_message"]
          and enc["status"] == "error" and "password-protected document (an encrypted Office package" in enc["error_message"])
    check("an owner-password PDF opens and records IsEncrypted True",
          json.loads(latest("owner_only.pdf")["detail_json"])["IsEncrypted"] == "True")
    # A result as B16 wrote it: no signature_state.
    conn.execute("UPDATE analyzer_result SET signature_state = NULL WHERE analyzer_result_id = ?",
                 (latest("legacy.pdf")["analyzer_result_id"],))
    conn.commit()

    wanted = ["plain.pdf", "legacy.pdf", "filled_sig.pdf", "empty_sig.pdf", "certified.pdf", "locked.pdf", "owner_only.pdf",
              "plain.docx", "signed.docx", "encrypted.docx", "plain.doc", "signed.doc", "rtf.doc"] + (["portfolio.pdf"] if have_portfolio else [])
    for n in wanted:
        st.record_metadata_decision("desired_source_metadata_set", by[n], "creator", "Thomas")
    keep = st.record_decision("must_keep_location", LOCATION, by["plain_copy.pdf"])
    red = st.record_decision("redundant_location", LOCATION, by["plain.pdf"])
    with PC.ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs"]) as writes:
        ev = E.load_evidence(conn)
        proj = R.resolve_all(ev)
        draft = PL.build(ev, proj, RT.route_all(ev, proj, ev.now))
        blocked = MD.blocked_by_evidence(ev.metadata.questions)
    qs = {q.location_id: q for q in ev.metadata.questions.values()}
    it = {i.target_ref: i for i in draft.items if i.action_kind == META}
    got = {n: (it[by[n]].state, it[by[n]].blocked_reason) for n in wanted}
    expect = {"plain.pdf": (UNSUPPORTED, "writer_unsupported"), "empty_sig.pdf": (UNSUPPORTED, "writer_unsupported"),
              "plain.docx": (UNSUPPORTED, "writer_unsupported"), "rtf.doc": (UNSUPPORTED, "writer_unsupported"),
              "filled_sig.pdf": (BLOCKED, "signed_or_certified"), "certified.pdf": (BLOCKED, "signed_or_certified"),
              "signed.docx": (BLOCKED, "signed_or_certified"), "signed.doc": (BLOCKED, "signed_or_certified"),
              "locked.pdf": (BLOCKED, "encrypted"), "owner_only.pdf": (BLOCKED, "encrypted"), "encrypted.docx": (BLOCKED, "encrypted"),
              "legacy.pdf": (BLOCKED_EV, "source_not_signed_or_certified"), "plain.doc": (BLOCKED_EV, "source_not_encrypted")}
    if have_portfolio:
        expect["portfolio.pdf"] = (BLOCKED, "signed_or_certified")
    check("every outcome through the store and the plan: signed / certified -> signed_or_certified; encrypted or "
          "password-protected -> encrypted; unsigned and unencrypted -> unsupported_action / writer_unsupported",
          {n: got[n] for n in expect if expect[n][1] in ("writer_unsupported", "signed_or_certified", "encrypted")}
          == {n: v for n, v in expect.items() if v[1] in ("writer_unsupported", "signed_or_certified", "encrypted")}, str(got))
    check("a result as B16 wrote it (no signature_state) is UNKNOWN -- never 'unsigned' -- and blocks (P3-A91)",
          qs[by["legacy.pdf"]].signed_or_certified is None and got["legacy.pdf"] == expect["legacy.pdf"])
    check("a legacy OLE2 .doc: its encryption is unknown (its summary can be read while its content is encrypted), and "
          "blocks; RTF saved as .doc: neither check applies", got["plain.doc"] == expect["plain.doc"]
          and MD.integrity(qs[by["rtf.doc"]])[MD.SIGNED_KEY][0] == MD.integrity(qs[by["rtf.doc"]])[MD.ENCRYPTED_KEY][0] == "not_applicable")
    check("the evidence-level projection answers 'encrypted' too, with no plan",
          {n for n in wanted if blocked.get(qs[by[n]].question_id) == "encrypted"} == {"locked.pdf", "owner_only.pdf", "encrypted.docx"})
    check("the encryption check sits between the signature and hard-link checks, hard, class integrity",
          [k for k, _s, st_ in REG.ACTION_PRECONDITIONS[META] if st_ == REG.STAGE_EVIDENCE][3:6] ==
          ["source_not_signed_or_certified", "source_not_encrypted", "source_not_hardlinked"]
          and REG.PRECONDITIONS["source_not_encrypted"] == "integrity")
    check("reading the evidence and building the plan read no source file", not reads.hits and not writes.violations,
          str(reads.hits[:2] + writes.violations[:2]))

    section("3. Plan notes S1 / S2, and B16's fingerprint for a revision without metadata")
    m_plain = it[by["plain.pdf"]]
    rm_plain = next(i for i in draft.items if i.action_kind == REMOVE and i.target_ref == by["plain.pdf"])
    check("S1: the metadata item on plain.pdf (in an exact-duplicate group) says it would leave the group -- a note in its "
          "estimated effect, not a precondition, not a block",
          any("will leave exact-duplicate group" in n for n in m_plain.estimated_effect.get("notes", []))
          and not any("group" in c.key for c in m_plain.preconditions) and "Note: 1 metadata update(s) on files in "
          "exact-duplicate groups" in " ".join(draft.lines()))
    ov_m, ov_r = m_plain.condition(PL.OVERLAP_KEY), rm_plain.condition(PL.OVERLAP_KEY)
    check("S2: plain.pdf is both removed and given a desired Creator -- a WARNING on both items, each naming the other",
          ov_m is not None and ov_r is not None and ov_m.severity == ov_r.severity == "warning"
          and ov_m.params["other_item"] == rm_plain.item_no and ov_r.params["other_item"] == m_plain.item_no)
    check("...warnings never block: the removal stays ready; neither item reordered or dropped (retention items first, "
          "numbered as before)", rm_plain.state == READY and rm_plain.item_no < m_plain.item_no
          and [i.item_no for i in draft.items] == list(range(1, len(draft.items) + 1))
          and "Warning: 1 metadata update(s) on a location this plan also removes" in " ".join(draft.lines()))
    others = [i for i in draft.items if i.action_kind == REMOVE and i.target_ref != by["plain.pdf"]]
    check("no other item carries either note", all(i.condition(PL.OVERLAP_KEY) is None for i in others)
          and all(not i.estimated_effect.get("notes") for i in draft.items if i.action_kind != META))
    st.withdraw(keep["decision_id"])
    st.withdraw(red["decision_id"])
    test_b16_fingerprint(tmp)
    conn.close()
    return app_root, corpus, docs, project_dir, by


def test_b16_fingerprint(tmp: Path):
    """The same hand-built retention model, fingerprinted by the phase3-b16
    code (materialized from git) and by this build."""
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + '/Database')\n"
             "import p3_regression as B\nfrom Phase3 import plans as PL, registry as G\n"
             "L = [B.loc('1', r'C:\\A\\a.bin', content='C1', phys='P1', size=900), B.loc('2', r'C:\\B\\a.bin', content='C1', phys='P2', size=900),\n"
             "     B.loc('3', r'C:\\C\\a.bin', content='C1', phys='P3', size=900), B.loc('4', r'C:\\D\\b.bin', content='C4', phys='P4', size=50),\n"
             "     B.loc('5', r'C:\\E\\b.bin', content='C4', phys='P5', size=50)]\n"
             "D = [B.dec('D1', 'canonical_location', G.GROUP, 'C1', '1'), B.dec('D2', 'redundant_location', G.LOCATION, '2'),\n"
             "     B.dec('D3', 'must_keep_location', G.LOCATION, '4'), B.dec('D4', 'redundant_location', G.LOCATION, '5')]\n"
             "ev, proj, rt = B.routed(L, D)\nd = PL.build(ev, proj, rt)\nprint(PL.fingerprint(d.items), len(d.items))\n")
    out = {}
    try:
        tar = subprocess.run(["git", "-C", str(SCRIPTS.parent), "archive", "--format=tar", "phase3-b16", "Scripts"],
                             capture_output=True, check=True).stdout
        b16 = tmp / "b16"
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(b16)
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  the phase3-b16 cross-check (git archive unavailable: {exc})")
        return
    for label, scripts in (("b16", b16 / "Scripts"), ("b17", SCRIPTS)):
        r = subprocess.run([sys.executable, "-c", probe, str(scripts)], capture_output=True, text=True, cwd=str(scripts),
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        out[label] = r.stdout.strip() or r.stderr.strip()[-300:]
    check("a revision without metadata items fingerprints EXACTLY as the phase3-b16 code fingerprints it (same model, "
          "both builds run)", out["b16"] == out["b17"] and len(out["b16"].split()[0]) == 64, str(out))


# ---------------------------------------------------------------------------
# 4. The window
# ---------------------------------------------------------------------------

def _button(widget, text):
    for w in widget.winfo_children():
        try:
            if str(w.cget("text")) == text:
                return w
        except Exception:                                           # noqa: BLE001
            pass
        found = _button(w, text)
        if found is not None:
            return found
    return None


def _label(widget, text):
    """Is a widget showing exactly this text somewhere under `widget`?"""
    for w in widget.winfo_children():
        try:
            if str(w.cget("text")) == text:
                return True
        except Exception:                                           # noqa: BLE001
            pass
        if _label(w, text):
            return True
    return False


def test_gui(app_root, corpus, docs, project_dir, by):
    section("4. The Metadata page, headless: entry points, actions, provenance, the queue, zero source access")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from tkinter import messagebox
    from Phase2 import gui
    from Phase2.runner import RunRequest, RunWorker, PRESCAN, ANALYSIS
    from Phase3 import metadata_view as MV, review as RV

    asked, answers = [], []
    real = {n: getattr(messagebox, n) for n in ("askyesno", "showinfo", "showwarning", "showerror")}
    messagebox.askyesno = lambda *a, **k: (asked.append(("yesno", a[1] if len(a) > 1 else "", k.get("default"))),
                                           answers.pop(0) if answers else True)[1]
    for n in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, n, lambda *a, _n=n, **k: asked.append((_n, a[0] if a else "", a[1] if len(a) > 1 else "")))
    fresh = int(by["fresh.docx"])
    try:
        with PC.ReadGuard(corpus) as reads, BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            conn = app.conn
            st = RV.store_for(app)
            qcount = lambda: conn.execute("SELECT COUNT(*) FROM p3_metadata_question").fetchone()[0]   # noqa: E731
            before_q = qcount()

            # Entry points.
            nav = next(b for b in app.nav_buttons if str(b.cget("text")) == "Metadata")
            nav.invoke()
            app.update()
            check("entry 1: the side panel's Metadata opens the Metadata Review queue",
                  isinstance(app.metadata_page, MV.MetadataQueuePage))
            app.show_files()
            app.update()
            app.show_file_detail({"path.id": fresh, "path.file_name": "fresh.docx"})
            _button(app.detail_host, "Metadata for this file").invoke()
            app.update()
            page = app.metadata_page
            check("entry 2: the Details pane's 'Metadata for this file' opens that file's page",
                  isinstance(page, MV.MetadataFilePage) and page.file_path_id == fresh and page.file_var.get().endswith("fresh.docx"))
            check("the file page: every semantic field, the source section's label, the project metadata label",
                  set(page.tree.get_children()) == set(REG.METADATA_FIELDS)
                  and _label(app.content, MV.SOURCE_LABEL) and _label(app.content, REG.PROJECT_METADATA_LABEL))
            row = page.tree.item("creator", "values")
            check("the creator row: observed 'Frank' (Known), capability read_only, blockers name the missing writer, "
                  "Undecided, no expected action yet", row[1] == "'Frank'" and row[2] == "Known" and row[4] == "read_only"
                  and "no certified writer" in row[5] and row[6] == "Undecided" and row[7] == "")
            check("viewing a file created no question row", qcount() == before_q)

            # Actions.
            def pick(field):
                page = app.metadata_page
                page.tree.selection_set(field)
                page._field_chosen()
                return page
            page = pick("creator")
            page.value_var.set("Thomas")
            res = page.save_desired()
            app.update()
            op = conn.execute("SELECT o.* FROM p3_operation o JOIN p3_decision d ON d.operation_id = o.operation_id "
                              "WHERE d.decision_id = ?", (res["decision_id"],)).fetchone()
            drow = conn.execute("SELECT * FROM p3_decision WHERE decision_id = ?", (res["decision_id"],)).fetchone()
            check("Save desired value: desired_source_metadata_set on the metadata question, with provenance (a person's "
                  "'decision' operation, the dashboard, this window's session, a command id)",
                  drow["decision_kind"] == "desired_source_metadata_set" and drow["target_kind"] == MQ
                  and drow["origin_kind"] == "explicit_human" and op["kind"] == "decision" and op["actor_kind"] == "explicit_human"
                  and op["agent_kind"] == "dashboard" and op["session_id"] == app.p3_session_id and op["command_id"].startswith("cmd:"))
            row = app.metadata_page.tree.item("creator", "values")
            check("...the row now shows the desired value, 'Desired value recorded', and the plan item it makes: blocked, "
                  "writer_unsupported", row[3] == "'Thomas'" and row[6] == "Desired value recorded"
                  and row[7] == "Update Source Metadata -- blocked: writer_unsupported")
            n_dec = conn.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0]
            page = pick("content_created")
            page.value_var.set("yesterday")
            check("invalid input (a date that is not a date) is refused with the reason; nothing recorded",
                  page.save_desired() is None and asked[-1][0] == "showwarning" and "YYYY-MM-DD" in asked[-1][2]
                  and conn.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0] == n_dec)
            page = pick("title")
            answers.append(False)
            check("Clear asks first (its own confirmation, default No); answered No, nothing is recorded",
                  page.clear_field() is None and asked[-1][0] == "yesno" and asked[-1][2] == "no"
                  and conn.execute("SELECT COUNT(*) FROM p3_decision").fetchone()[0] == n_dec)
            answers.append(True)
            res = page.clear_field()
            check("...answered Yes: desired_source_metadata_clear, and the row says Clear requested",
                  res and conn.execute("SELECT decision_kind FROM p3_decision WHERE decision_id = ?", (res["decision_id"],)).fetchone()[0]
                  == "desired_source_metadata_clear" and app.metadata_page.tree.item("title", "values")[6] == "Clear requested")
            page = pick("description")
            res = page.leave_unchanged()
            check("Leave source unchanged: leave_source_metadata_unchanged; the expected action says it is never a plan item",
                  conn.execute("SELECT decision_kind FROM p3_decision WHERE decision_id = ?", (res["decision_id"],)).fetchone()[0]
                  == "leave_source_metadata_unchanged" and app.metadata_page.tree.item("description", "values")[7]
                  == "none (never a plan item)")
            page = pick("keywords")
            dlg = page.defer()
            dlg.choice.set("snooze_until_evidence_change")
            dlg._confirm()
            app.update()
            ev_row = conn.execute("SELECT * FROM p3_review_event WHERE target_kind = ? ORDER BY review_event_id DESC LIMIT 1",
                                  (MQ,)).fetchone()
            check("Defer...: Build 3's deferral on the metadata question, trigger 'evidence change', question named in the same "
                  "operation; the row says Deferred", ev_row["event_kind"] == "deferred" and ev_row["return_kind"] == "evidence_change"
                  and conn.execute("SELECT operation_id FROM p3_metadata_question WHERE question_id = ?",
                                   (int(ev_row["target_ref"]),)).fetchone()[0] == ev_row["operation_id"]
                  and app.metadata_page.tree.item("keywords", "values")[6] == "Deferred")
            entry = app.metadata_page.undo()
            check("Undo ends the deferral (a restore event; the deferral row stays)", entry[0] == "deferral"
                  and app.metadata_page.tree.item("keywords", "values")[6] == "Undecided"
                  and conn.execute("SELECT event_kind FROM p3_review_event WHERE target_kind = ? ORDER BY review_event_id DESC LIMIT 1",
                                   (MQ,)).fetchone()[0] == "restored")
            entry = app.metadata_page.undo()
            check("Undo again withdraws the Leave unchanged (a withdrawal; the decision row stays in the history)",
                  entry[0] == "decision" and conn.execute("SELECT COUNT(*) FROM p3_decision_withdrawal WHERE decision_id = ?",
                                                          (entry[1],)).fetchone()[0] == 1
                  and "leave unchanged" in app.metadata_page.history_text.get("1.0", "end")
                  and "withdrawn" in app.metadata_page.history_text.get("1.0", "end"))
            page = app.metadata_page
            page.tag_var.set("tax")
            page.add_tag()
            page = app.metadata_page
            page.tag_var.set("2024")
            page.add_tag()
            page = app.metadata_page
            page.tags_list.selection_set(0)
            page.remove_tag()
            page = app.metadata_page
            page.project_vars["note"].set("check the owner")
            page.save_project("note")
            page = app.metadata_page
            pm = conn.execute("SELECT value_json FROM p3_decision WHERE decision_kind = 'project_metadata_set' AND target_ref = ? "
                              "ORDER BY decision_id", (str(fresh),)).fetchall()
            check("tags added and removed, a note set: each a project_metadata_set on the file location, the page showing "
                  "tags ['2024'] and the note", [json.loads(r[0]) for r in pm] == [
                      {"field": "tags", "value": ["tax"]}, {"field": "tags", "value": ["tax", "2024"]},
                      {"field": "tags", "value": ["2024"]}, {"field": "note", "value": "check the owner"}]
                  and list(page.tags_list.get(0, "end")) == ["2024"] and page.project_vars["note"].get() == "check the owner")

            # An unknown field never offers Clear as the default.
            app.show_metadata_review(file_path_id=int(by["encrypted.docx"]))
            page = pick("creator")
            check("an encrypted .docx: its creator is Unknown (not read); Return means Save, never Clear -- Clear is not "
                  "the default button, and the hint says unknown is no reason to clear",
                  page.tree.item("creator", "values")[2].startswith("Unknown") and page.default_action() == "save"
                  and str(page.clear_button.cget("default")) != "active" and str(page.save_button.cget("default")) == "active"
                  and "not a reason to clear" in page.hint_var.get())

            # Entry 3: a plan's metadata item.
            app.show_plan()
            app.update()
            pp = app.plan_page
            pp.name_var.set("Metadata plan")
            pp.build_revision()
            app.update()
            iid = next(str(i.item_no) for i in pp.items if i.action_kind == META and i.target_ref == str(fresh))
            pp.items_tree.selection_set(iid)
            pp._item_chosen()
            pp.metadata_button.invoke()
            app.update()
            check("entry 3: a plan's Update Source Metadata item opens its file's Metadata page",
                  isinstance(app.metadata_page, MV.MetadataFilePage) and app.metadata_page.file_path_id == fresh)
            check("project metadata never made a plan item", not [i for i in pp.items if i.action_kind != META and
                                                                   i.target_ref == str(fresh)])

            # The queue: a named, undecided question; Left Unchanged; a deferral; filters.
            q_copy = st.metadata_question(fresh, "copyright", create=True)
            app.show_metadata_review()
            q = app.metadata_page
            in_queue = lambda qid: any(r.question_id == str(qid) for r in q.rows)   # noqa: E731
            check("the queue lists a question a person named and has not decided",
                  in_queue(q_copy) and q.count_var.get()[0].isdigit())
            app.show_metadata_review(file_path_id=fresh)
            page = pick("copyright")
            page.leave_unchanged()
            page = pick("keywords")
            dlg = page.defer()
            dlg.choice.set("snooze_until_evidence_change")
            dlg._confirm()
            app.show_metadata_review()
            q = app.metadata_page
            q_kw = conn.execute("SELECT question_id FROM p3_metadata_question WHERE file_path_id = ? AND semantic_field = 'keywords'",
                                (fresh,)).fetchone()[0]
            check("Left Unchanged leaves the queue; an open deferral stays out too", not in_queue(q_copy) and not in_queue(q_kw))
            q.field_var.set(REG.METADATA_FIELDS["title"].label)
            q.apply_filters()
            check("the field filter narrows the list to that field", all(r.semantic_field == "title" for r in q.shown))
            q.field_var.set(MV.ALL)
            q.blocker_var.set("Encrypted")
            q.apply_filters()
            check("the blocker filter shows only encrypted files' questions", all("encrypted" in r.blocked_by_evidence for r in q.shown))
            app.destroy()
        check("the window read no source file (every page, every action)", not reads.hits, str(reads.hits[:3]))
        check("no Python-level write outside the project and log folders", not guard.violations, str(guard.violations[:3]))

        # The file changes on disk and is analyzed again (Phase 1/2 reading it, outside the guards).
        docx_file(docs / "fresh.docx", "Frank", "A file nobody had decided anything about -- now edited, and longer.")
        outs = [RunWorker(app_root, project_dir, RunRequest(PRESCAN, "Pre-Scan")).run(),
                RunWorker(app_root, project_dir, RunRequest(ANALYSIS, "Analyze documents", analyzer_keys=["office"])).run()]
        check("rescanned and analyzed again", all(o.ok for o in outs))
        with PC.ReadGuard(corpus) as reads2, BASE.WriteGuard([project_dir, app_root / "Logs", gui.APP_ROOT / "Logs"]) as guard2:
            app = gui.Phase2App(None)
            app.withdraw()
            app.open_project(project_dir)
            app.show_metadata_review()
            q = app.metadata_page
            rows = {r.question_id: r for r in q.rows}
            check("after the re-analysis, Left Unchanged returns (needs revalidation, R8 §53) and the deferral 'until the "
                  "evidence changes' has come back", str(q_copy) in rows and rows[str(q_copy)].review_state == MD.NEEDS_REVALIDATION
                  and str(q_kw) in rows and rows[str(q_kw)].returned)
            check("the desired Creator needs revalidation too", any(r.semantic_field == "creator" and r.location_id == str(fresh)
                                                                   and r.review_state == MD.NEEDS_REVALIDATION for r in q.rows))
            app.show_metadata_review(file_path_id=fresh)
            check("the file page says so on the row", app.metadata_page.tree.item("copyright", "values")[6] == "Needs revalidation")
            app.destroy()
        check("the second window session read and wrote no source file", not reads2.hits and not guard2.violations)
        check("no error dialog", not [a for a in asked if a[0] == "showerror"], str(asked[-3:]))
    finally:
        for n, fn in real.items():
            setattr(messagebox, n, fn)


def test_empty_queue(tmp: Path):
    section("4b. An empty queue says 'Currently clear'")
    try:
        import tkinter as tk
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception as exc:                                        # noqa: BLE001
        print(f"  SKIP  no display ({exc})")
        return
    from Phase2 import gui
    from Phase2.runner import RunRequest, RunWorker, PRESCAN
    root = tmp / "EmptyRoot"
    (root / "Projects").mkdir(parents=True)
    src = tmp / "EmptyCorpus"
    src.mkdir()
    (src / "a.txt").write_text("hello", encoding="utf-8")
    RunWorker(root, None, RunRequest(PRESCAN, "Pre-Scan", source_roots=[str(src)], project_name="Empty")).run()
    app = gui.Phase2App(None)
    app.withdraw()
    app.open_project(root / "Projects" / "Empty")
    app.show_metadata_review()
    check("no question needs anyone: 'Currently clear' (B9's wording), never 'complete'",
          app.metadata_page.count_var.get().startswith("Currently clear") and not app.metadata_page.tree.get_children())
    app.destroy()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="p3metaview_"))
    try:
        app_root, corpus, docs, project_dir, by = test_project(tmp)
        test_gui(app_root, corpus, docs, project_dir, by)
        test_empty_queue(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{'=' * 70}\nP3 METADATA VIEW (B17): {passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        print("\nFailures:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  {name}: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
