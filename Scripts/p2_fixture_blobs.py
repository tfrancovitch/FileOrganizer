"""Binary fixtures for the test corpus, read from Resources\\Fixtures.

Every file there is genuine output of the program that owns the format,
kept so the readers are tested against what those programs write, not
against a reader's own idea of the format:

  legacy_memo.doc, legacy_ledger.xls, legacy_deck.ppt
      Word, Excel and PowerPoint 2016 via COM (Documents.Add / SaveAs2(path, 0);
      Workbooks.Add / SaveAs(path, 56); Presentations.Add / SaveAs(path, 1)),
      2026-09-12.
  discovery_schedule.msg
      Outlook 2016 via COM (CreateItem(0) with a .txt attachment, SaveAs(path,
      9)), never saved into a mailbox, 2026-09-13.
  ledger_macro.xlsm, ledger_template.xltx, ledger_macro_template.xltm,
  ledger_open.ods, ledger_xml2003.xml
      Excel 2016 via COM, SaveAs formats 52, 54, 53, 60, 46, 2026-09-13.
  deck_macro.pptm, deck_template.potx, deck_macro_template.potm,
  deck_show.ppsx, deck_macro_show.ppsm, deck_open.odp
      PowerPoint 2016 via COM, SaveAs formats 25, 26, 27, 28, 29, 35, 2026-09-13.
  tika_testPST_variousBodyTypes.pst
      Apache Tika's test mailbox (testPST_variousBodyTypes.pst, Apache
      License 2.0, https://github.com/apache/tika), four messages with
      plain, HTML and RTF bodies. Downloaded 2026-09-13 with the user's
      approval.

Each carries the marker phrase named in MARKERS of
p2_build_acceptance_corpus.py, or is listed there as having none.
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "Resources" / "Fixtures"


def blob(name):
    """The bytes of one fixture file."""
    return (FIXTURES / name).read_bytes()


def available():
    """Names of the fixtures present."""
    return sorted(p.name for p in FIXTURES.iterdir() if p.is_file() and not p.name.endswith(".md"))
