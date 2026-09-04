#!/usr/bin/env python3
r"""
self_check.py
===================================================================
The File Organizer -- Phase 1
Shipped health check
===================================================================

Answers the handful of questions worth asking when the program will
not start, or when a category is unexpectedly unavailable:

    Is Python 3.11 or newer?
    Is Tkinter present, so the dashboard can open a window?
    Which optional analyzer packages are installed?
    Is ffprobe on PATH, for audio and video?
    Can SQLite create a schema-7 database here?
    Are the runtime modules importable?
    Is the installation folder writable?

WHAT THIS IS NOT

Not a test suite. The engineering acceptance suites that proved the
inventory, hash and analyzer engines are not shipped -- they belong to
the development history, and they need fixtures and oracle data no
user has. Keeping them around as "self tests" would ship several
megabytes of evidence about revisions that no longer exist, and would
invite the reading that a green run here means the engines were
verified today. It does not. It means the machine can run them.

Every check is read-only apart from one temporary database, created in
the system temp folder and deleted afterwards. Nothing here touches a
project or a source file.

    python Scripts\self_check.py

Exit code 0 when nothing is wrong, 1 when a REQUIRED check fails.
Optional analyzer packages being absent is reported, not failed: a
project with no audio in it does not need mutagen.
"""

import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(HERE, "Database")
for path in (DATABASE, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)

#: The accepted runtime minimum. Raised to 3.11 in B4.3 to match
#: TheFileOrganizer.bat and the documentation, which had drifted
#: apart -- the launcher accepted 3.9 while the product was only
#: ever validated on 3.11+. Deliberately NOT raised to whatever
#: the development machine happens to run.
MINIMUM_PYTHON = (3, 11)
#: B6.1 requires schema 7; migration 007 completes the current-state
#: projection and A-F reconciliation fields.
REQUIRED_SCHEMA_VERSION = 7

#: import name -> (pip name, what stops working without it)
OPTIONAL_PACKAGES = {
    "PIL": ("Pillow", "image analysis"),
    "imagehash": ("imagehash", "image analysis"),
    "pypdf": ("pypdf", "PDF analysis"),
    "pdfplumber": ("pdfplumber", "PDF analysis and content extraction"),
    "docx": ("python-docx", "Word analysis and content extraction"),
    "openpyxl": ("openpyxl", "Excel analysis and content extraction"),
    "pptx": ("python-pptx", "PowerPoint analysis and content extraction"),
    "chardet": ("chardet", "text analysis and content extraction"),
    "exifread": ("exifread", "RAW image analysis"),
    "mutagen": ("mutagen", "audio analysis"),
    "olefile": ("olefile", "legacy .doc/.xls/.ppt detection"),
    "py7zr": ("py7zr", ".7z archive analysis (ZIP works without it)"),
    "pillow_heif": ("pillow-heif", "HEIC/HEIF image decoding only"),
}

RUNTIME_MODULES = ("fo_db", "fo_scan", "fo_inventory_records", "fo_hash_engine",
                   "fo_hash_records", "fo_analyzer_engine",
                   "fo_analyzer_records", "fo_exports", "fo_project",
                   "fo_estimates", "win_meta")


class Report(object):
    def __init__(self):
        self.required_failures = 0
        self.warnings = 0

    def ok(self, title, detail=""):
        print("  [ OK ] %-38s %s" % (title, detail))

    def fail(self, title, detail=""):
        self.required_failures += 1
        print("  [FAIL] %-38s %s" % (title, detail))

    def warn(self, title, detail=""):
        self.warnings += 1
        print("  [WARN] %-38s %s" % (title, detail))


def check_python(report):
    version = sys.version_info
    text = "%d.%d.%d" % (version.major, version.minor, version.micro)
    if version[:2] >= MINIMUM_PYTHON:
        report.ok("Python version", text)
    else:
        report.fail("Python version",
                    "%s -- Python %d.%d or newer is required"
                    % ((text,) + MINIMUM_PYTHON))


def check_tkinter(report):
    try:
        import tkinter                                          # noqa: F401
        report.ok("Tkinter (the dashboard window)", "available")
    except Exception as exc:                                    # noqa: BLE001
        report.fail("Tkinter (the dashboard window)",
                    "missing: %s" % exc)


def check_runtime_modules(report):
    missing = []
    for name in RUNTIME_MODULES:
        try:
            __import__(name)
        except Exception as exc:                                # noqa: BLE001
            missing.append("%s (%s)" % (name, type(exc).__name__))
    if missing:
        report.fail("Runtime modules import",
                    "%d problem(s): %s" % (len(missing), ", ".join(missing[:3])))
    else:
        report.ok("Runtime modules import",
                  "%d modules" % len(RUNTIME_MODULES))


def check_sqlite(report):
    """Create a real project database in a temp folder, then delete it.

    Checking that `import sqlite3` succeeds would prove very little.
    What matters is whether the migrations actually apply on THIS
    machine and land on the current schema -- which is a different question, and
    the one that fails on a read-only or unusual filesystem.
    """
    report.ok("SQLite library", "sqlite3 %s" % sqlite3.sqlite_version)
    workdir = tempfile.mkdtemp(prefix="fo-selfcheck-")
    try:
        import fo_db
        project = os.path.join(workdir, "HealthCheck")
        os.makedirs(project)
        fo_db.init_project(project, "HealthCheck")
        database = fo_db.ProjectPaths(project).database_file
        conn = sqlite3.connect(database)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if version == REQUIRED_SCHEMA_VERSION and integrity == "ok":
            report.ok("Database creation",
                      "schema %d, integrity ok" % version)
        else:
            report.fail("Database creation",
                        "schema %s, integrity %s" % (version, integrity))
    except Exception as exc:                                    # noqa: BLE001
        report.fail("Database creation", "%s: %s" % (type(exc).__name__, exc))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def check_optional_packages(report):
    missing = []
    for name, (pip_name, purpose) in sorted(OPTIONAL_PACKAGES.items()):
        try:
            __import__(name)
        except Exception:                                       # noqa: BLE001
            missing.append((pip_name, purpose))
    if not missing:
        report.ok("Analyzer packages",
                  "all %d present" % len(OPTIONAL_PACKAGES))
        return
    report.warn("Analyzer packages",
                "%d missing -- affected analyzers/formats will be unavailable"
                % len(missing))
    for pip_name, purpose in missing:
        print("           %-16s needed for %s" % (pip_name, purpose))
    print("           install with:  pip install %s"
          % " ".join(name for name, _ in missing))


def check_ffprobe(report):
    found = shutil.which("ffprobe")
    if found:
        report.ok("ffprobe (audio and video)", found)
    else:
        report.warn("ffprobe (audio and video)",
                    "not on PATH -- audio and video analysis will fail")


def check_writable(report):
    root = os.path.dirname(HERE)
    projects = os.path.join(root, "Projects")
    try:
        os.makedirs(projects, exist_ok=True)
        probe = os.path.join(projects, ".write-probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        report.ok("Projects folder writable", projects)
    except Exception as exc:                                    # noqa: BLE001
        report.fail("Projects folder writable",
                    "%s: %s" % (type(exc).__name__, exc))


def main():
    print("=" * 70)
    print("  THE FILE ORGANIZER -- health check")
    print("  %s" % os.path.dirname(HERE))
    print("=" * 70)
    print()

    report = Report()
    check_python(report)
    check_tkinter(report)
    check_runtime_modules(report)
    check_sqlite(report)
    check_writable(report)
    print()
    check_optional_packages(report)
    check_ffprobe(report)

    print()
    print("=" * 70)
    if report.required_failures:
        print("  %d REQUIRED CHECK(S) FAILED -- the program may not run."
              % report.required_failures)
    elif report.warnings:
        print("  Ready. %d optional component(s) missing -- see above."
              % report.warnings)
    else:
        print("  Ready. Everything the program needs is present.")
    print("=" * 70)
    return 1 if report.required_failures else 0


if __name__ == "__main__":
    sys.exit(main())
