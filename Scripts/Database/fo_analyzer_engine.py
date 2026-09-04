#!/usr/bin/env python3
r"""
fo_analyzer_engine.py
===================================================================
PRODUCTION CODE — The File Organizer B6.1
===================================================================

Runs the nine supported analyzers in-process under RunCoordinator. The retired
standalone CSV/checkpoint/report analyzer pipeline was removed in B6.1; analyzer
modules now provide per-file analysis functions and declarations only.

The engine owns adapter/dependency selection, per-analyzer isolation, current
file selection, progress, and outcome status. SQLite persistence is supplied by
`fo_analyzer_records` through a result sink. One bad file or analyzer remains
local; a persistence-sink failure is never converted into `completed`.

`no_applicable_files` is a successful, explicit state distinct from failure,
skipped work and an empty result set.
"""

import os
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
for path in (HERE, SCRIPTS):
    if path not in sys.path:
        sys.path.insert(0, path)


#: Outcome vocabulary. R5's, reused rather than restated -- these
#: strings are written into analyzer_run.analysis_status and the
#: verifiers already key off them.
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_NO_APPLICABLE = "no_applicable_files"
STATUS_SKIPPED = "skipped"

#: Third-party imports each analyzer needs, mirroring what the
#: PowerShell wrappers preflighted with Test-PythonPackages. Checked
#: before an analyzer runs so a missing library is reported as that
#: analyzer failing, with the pip hint the wrapper used to print,
#: instead of an ImportError at module-import time taking down the
#: whole runtime.
DEPENDENCY_IMPORTS = {
    "image": (("PIL", "imagehash"), "Pillow imagehash"),
    "pdf": (("pypdf", "pdfplumber"), "pypdf pdfplumber"),
    "office": (("docx", "openpyxl", "pptx", "olefile"), "python-docx openpyxl python-pptx olefile"),
    "raw_image": (("exifread",), "exifread"),
    "audio": (("mutagen",), "mutagen"),
    "video": ((), "ffprobe (part of ffmpeg)"),
    "text": (("chardet",), "chardet"),
    "archive": ((), ""),
    "content_extraction": (("pdfplumber", "docx", "openpyxl", "pptx", "chardet"),
                           "pdfplumber python-docx openpyxl python-pptx chardet"),
}

#: settings.json field each wrapper stamped on success. Kept so the
#: dashboard's "last run" display is unchanged.
SETTINGS_FIELD = {
    "image": "LastImageAnalysisScan",
    "pdf": "LastPDFAnalysisScan",
    "office": "LastOfficeAnalysisScan",
    "raw_image": "LastRawImageAnalysisScan",
    "audio": "LastAudioAnalysisScan",
    "video": "LastVideoAnalysisScan",
    "text": "LastTextFileAnalysisScan",
    "archive": "LastArchiveAnalysisScan",
    "content_extraction": "LastContentExtractionScan",
}

DEFAULT_IMAGE_HASH_SIZE = 8


def openable_path(path):
    r"""The path actually handed to an analyzer.

    On Windows this is the stored path, unchanged: the analyzers apply
    their own \\?\ boundary via file_organizer_common.to_long_path,
    which is the existing helper and the one B4 is required to use
    rather than inventing an analyzer-specific convention.

    THE NON-WINDOWS BRANCH IS VERIFICATION SCAFFOLDING. Paths are stored
    with backslash separators because that is what Windows uses and
    what the database reconstructs, so on a verification machine they
    cannot be opened at all. The separator is translated here -- but
    only after confirming the literal path does not exist, so a genuine
    filename containing a backslash is never silently rewritten.

    It lives HERE, in B4's own module, and not in
    file_organizer_common.to_long_path, because that file is protected
    Alpha code that R5 criterion 33 requires to stay byte-identical to
    R4. Editing it for the convenience of an off-platform test would
    have been exactly the wrong trade: the protection is what proves
    the analyzers still behave as Alpha did, and it caught the attempt.

    The value returned is passed to the analyzer and NOWHERE ELSE --
    every persisted and exported path remains the ordinary stored one.
    """
    if sys.platform == "win32":
        return path
    text = str(path)
    if "\\" in text and not os.path.exists(text):
        return text.replace("\\", "/")
    return text


#: The Win32 extended-length path prefixes. Text that crosses back out
#: of a library must not carry either of them.
EXTENDED_PREFIX = "\\\\?\\"
EXTENDED_UNC_PREFIX = "\\\\?\\UNC\\"


def normalize_diagnostic_text(text):
    r"""Convert extended-length Windows paths in DIAGNOSTIC TEXT back to
    ordinary ones.

    THE PROBLEM THIS SOLVES

    Analyzers receive an ordinary path and apply the \\?\ prefix
    themselves, immediately before opening the file -- which is correct,
    and is what lets a 926-character path be read at all. Some
    third-party libraries then quote that prefixed path back in the
    exception they raise:

        Package not found at '\\?\C:\...\fake.docx'

    Capturing str(exc) verbatim carried an internal file-open
    representation into AnalyzerResult, into the database, into the
    exported CSV and onto the user's screen. The architecture says the
    prefix exists for the duration of one system call; this is where
    that promise was being broken.

    HOW

    By stripping the PREFIX only -- never by trying to find where the
    path ends. A Windows path may contain spaces, quotes and brackets,
    so any attempt to delimit it inside a sentence would eventually
    truncate a real path or swallow real prose. Removing the prefix is
    exact, works for a path embedded anywhere in a larger message,
    handles several paths in one message, and leaves every other
    character untouched.

    UNC first, because \\?\UNC\ starts with \\?\ and the shorter rule
    would otherwise turn \\?\UNC\server\share into UNC\server\share.

        \\?\C:\Folder\File.ext          -> C:\Folder\File.ext
        \\?\UNC\server\share\File.ext   -> \\server\share\File.ext

    Text containing no extended prefix is returned unchanged, including
    text that merely contains a question mark or a backslash.
    """
    if not text:
        return text
    text = str(text)
    if EXTENDED_PREFIX not in text:
        return text
    return text.replace(EXTENDED_UNC_PREFIX, "\\\\").replace(EXTENDED_PREFIX, "")


class AnalyzerEngineError(Exception):
    """The runtime could not start. Never raised for one bad analyzer."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

class FileEntry(object):
    """One inventoried file, as the runtime needs it."""

    __slots__ = ("key", "db_id", "path", "file_name", "size",
                 "is_offline_or_cloud")

    def __init__(self, key, db_id, path, file_name, size=0,
                 is_offline_or_cloud=False):
        self.key = key
        self.db_id = db_id
        self.path = path
        self.file_name = file_name
        self.size = int(size or 0)
        self.is_offline_or_cloud = bool(is_offline_or_cloud)


class AnalyzerResult(object):
    """One analyzer's verdict on one file.

    `fields` is the analyzer's own dict, exactly as its per-file routine
    returned it -- the runtime does not interpret it. `error` is empty
    on success and carries the message on failure, which is the same
    convention used by the persistence specification layer
    already knows how to read.
    """

    __slots__ = ("key", "db_id", "path", "file_name", "fields", "error",
                 "extra")

    def __init__(self, entry, fields=None, error="", extra=None):
        self.key = entry.key
        self.db_id = entry.db_id
        self.path = entry.path
        self.file_name = entry.file_name
        self.fields = dict(fields or {})
        self.error = error or ""
        #: Secondary payload for analyzers that produce more than a row:
        #: archive members, extracted-content references.
        self.extra = extra

    @property
    def ok(self):
        return not self.error


class AnalyzerOutcome(object):
    r"""Everything one analyzer concluded in one run.

    B6: COUNTS ARE ACCUMULATED, NOT DERIVED FROM A RETAINED LIST.

    B4.5 appended every AnalyzerResult to `self.results` and computed
    succeeded/error/skipped by walking that list at the end. B5-E.F007
    found the consequence: `run_all` materialised the entries and held
    every result of every analyzer until the whole pass finished, so
    peak memory was the sum of nine analyzers' complete output on a
    project of any size.

    B6 counts as results are produced and hands each one to a SINK
    immediately (see `AnalyzerEngine.run_one`). When a sink is
    attached, `results` stays empty and memory is bounded by one result
    at a time. When no sink is attached -- the in-memory API, used by
    tests and by small ad-hoc runs -- results are retained exactly as
    before, so the old behaviour is available deliberately rather than
    imposed.

    The counters are authoritative either way, which is what lets the
    two modes report identically.
    """

    def __init__(self, key, label, status=STATUS_COMPLETED, retain=True):
        self.key = key
        self.label = label
        self.status = status
        self.results = []
        self.retain = retain
        self.applicable_count = 0
        self.excluded_count = 0
        self.elapsed_sec = 0.0
        self.failure_reason = ""
        self._succeeded = 0
        self._errors = 0
        self._skipped = 0

    def record(self, result):
        """Count one result, and retain it only if asked to."""
        if result.error == "SkippedCloudOnly":
            self._skipped += 1
        elif result.error:
            self._errors += 1
        else:
            self._succeeded += 1
        if self.retain:
            self.results.append(result)
        return result

    @property
    def succeeded_count(self):
        return self._succeeded

    @property
    def error_count(self):
        return self._errors

    @property
    def skipped_count(self):
        return self._skipped

    @property
    def result_count(self):
        return self._succeeded + self._errors + self._skipped

    def summary(self):
        return {"analyzer": self.key, "status": self.status,
                "applicable": self.applicable_count,
                "succeeded": self.succeeded_count,
                "errors": self.error_count,
                "skipped": self.skipped_count,
                "elapsed_sec": round(self.elapsed_sec, 2)}


# ---------------------------------------------------------------------------
# Analyzer adapters
# ---------------------------------------------------------------------------

class AnalyzerAdapter(object):
    r"""How to select files for one analyzer and analyse one of them.

    An adapter is deliberately thin: it names the module, says which
    extensions apply, and returns the module's own per-file callable.
    Anything thicker would be the analyzer rewrite B4 is not.

    The module is imported LAZILY, inside a try, because most analyzers
    import a third-party library at module scope and exit if it is
    missing. Importing all nine eagerly would make one absent optional
    dependency fatal to the runtime -- which is precisely the coupling
    the per-analyzer isolation rule exists to prevent.
    """

    kind = "simple"

    def __init__(self, key, label, module_name, callable_name,
                 extensions_attr=None, exclude_attr=None,
                 exclude_label="excluded", declared_extensions=None,
                 declared_exclusions=None):
        self.key = key
        self.label = label
        self.module_name = module_name
        self.callable_name = callable_name
        self.extensions_attr = extensions_attr
        self.exclude_attr = exclude_attr
        self.exclude_label = exclude_label
        #: Extension sets used ONLY when the module cannot be imported.
        #: Every analyzer module exits at import time if its third-party
        #: library is missing -- ImageHash.py calls sys.exit(1) at module
        #: scope -- so "which files would this analyzer have claimed?"
        #: has to be answerable without importing it. Otherwise a
        #: machine without Pillow could not even establish that a
        #: project contains no images.
        #:
        #: These duplicate the module constants, and duplication drifts,
        #: so the B4 verifier asserts they are EQUAL to the module's own
        #: sets for every analyzer whose module does import. The risk is
        #: converted into a checked invariant rather than a comment
        #: asking someone to remember.
        self.declared_extensions = set(declared_extensions or ())
        self.declared_exclusions = set(declared_exclusions or ())
        self._module = None
        self._import_error = None

    def module(self):
        r"""The analyzer module, or None if it cannot be imported.

        SystemExit is caught alongside Exception because these modules
        do not raise ImportError when a dependency is missing -- they
        print and call sys.exit(1) at module scope. An uncaught
        SystemExit here would terminate the entire runtime, taking the
        other eight analyzers with it, which is the exact failure this
        design is supposed to make impossible.
        """
        if self._module is None and self._import_error is None:
            try:
                self._module = __import__(self.module_name)
            except SystemExit as exc:
                self._import_error = (
                    "%s exited during import (code %s) -- a required "
                    "package is probably missing."
                    % (self.module_name, exc.code))
            except Exception as exc:                            # noqa: BLE001
                self._import_error = "%s: %s" % (type(exc).__name__, exc)
        return self._module

    def import_error(self):
        self.module()
        return self._import_error

    def extensions(self):
        """Applicable extensions, from the module when it loads."""
        module = self.module()
        if module is not None and self.extensions_attr is not None:
            return set(getattr(module, self.extensions_attr))
        return set(self.declared_extensions)

    def exclusions(self):
        module = self.module()
        if module is not None and self.exclude_attr is not None:
            return set(getattr(module, self.exclude_attr))
        return set(self.declared_exclusions)

    def analyze_fn(self, context):
        module = self.module()
        if module is None:
            raise AnalyzerEngineError(self.import_error() or "module unavailable")
        return getattr(module, self.callable_name)

    def apply(self, result, payload):
        """Fold one per-file return value into an AnalyzerResult."""
        result.fields = dict(payload)
        result.fields.pop("Error", None)


class ArchiveAdapter(AnalyzerAdapter):
    r"""Archive analysis returns (aggregate row, member entries).

    ArchiveAnalysis.py says in its own header that it does not use the
    shared orchestrator because its output shape is two files. That
    remains true here: the aggregate becomes the result row and the
    entries ride along as `extra`, to be persisted as archive_member
    rows. Nothing about how an archive is read changes.
    """

    kind = "archive"

    def apply(self, result, payload):
        aggregate, entries = payload
        result.fields = dict(aggregate)
        result.fields.pop("Error", None)
        result.extra = list(entries or [])


class ExtractionAdapter(AnalyzerAdapter):
    r"""Content extraction needs the folder it writes text files into.

    Its per-file routine is a closure over that folder --
    make_analyze_fn(extract_folder) -- so the adapter builds it per run
    rather than fetching a module-level function.

    The extracted .txt files remain files on disk, exactly as before.
    R5's extracted_content table continues to hold references and
    counts only; B4 does not begin storing text bodies in SQLite.
    """

    kind = "extraction"

    def analyze_fn(self, context):
        module = self.module()
        if module is None:
            raise AnalyzerEngineError(self.import_error() or "module unavailable")
        folder = Path(context["extract_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        return module.make_analyze_fn(folder)

    def apply(self, result, payload):
        result.fields = dict(payload)
        result.fields.pop("Error", None)
        result.extra = {"extracted_file": result.fields.get("ExtractedTextFile", "")}


class ImageAdapter(AnalyzerAdapter):
    r"""Image hashing takes a hash size and excludes RAW files.

    RAW images are not merely absent from IMAGE_EXTENSIONS -- they are
    counted and skipped, so the report can say how many were seen and
    handed to the RAW analyzer instead. That distinction is preserved
    through exclude_attr rather than folded into a plain non-match.
    """

    def analyze_fn(self, context):
        module = self.module()
        if module is None:
            raise AnalyzerEngineError(self.import_error() or "module unavailable")
        function = getattr(module, self.callable_name)
        hash_size = context.get("hash_size", DEFAULT_IMAGE_HASH_SIZE)
        return lambda path: function(path, hash_size=hash_size)


#: The nine analyzers, in the accepted execution order. The order is
#: the one Dashboard's ANALYZER_STAGES has always used and is preserved
#: because the run_stage records and every accepted run log follow it.
#: Declared extension sets, used only when a module will not import.
#: Kept beside the adapters rather than hidden inside them so the
#: verifier can compare each against the module's own constant.
_RAW = {
    ".3fr", ".arw", ".cr2", ".cr3", ".dng", ".erf", ".iiq", ".kdc",
    ".mrw", ".nef", ".nrw", ".orf", ".pef", ".raf", ".raw", ".rw2",
    ".rwl", ".sr2", ".srf", ".srw", ".x3f",
}
_IMAGE = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".bmp", ".tiff", ".tif",
          ".webp", ".ico", ".heic", ".heif", ".avif", ".jp2"}

ADAPTERS = [
    ImageAdapter("image", "Image Analysis", "ImageHash", "compute_hashes",
                 extensions_attr="IMAGE_EXTENSIONS",
                 exclude_attr="RAW_EXTENSIONS", exclude_label="RAW file(s)",
                 declared_extensions=_IMAGE, declared_exclusions=_RAW),
    AnalyzerAdapter("pdf", "PDF Analysis", "PDFAnalysis", "analyze_pdf",
                    extensions_attr="EXTENSIONS",
                    declared_extensions={".pdf"}),
    AnalyzerAdapter("office", "Office Analysis", "OfficeAnalysis",
                    "analyze_office", extensions_attr="EXTENSIONS",
                    declared_extensions={".docx", ".xlsx", ".pptx",
                                         ".doc", ".xls", ".ppt"}),
    AnalyzerAdapter("raw_image", "RAW Image Analysis", "RawImageAnalysis",
                    "analyze_raw", extensions_attr="RAW_EXTENSIONS",
                    declared_extensions=_RAW),
    AnalyzerAdapter("audio", "Audio Analysis", "AudioAnalysis",
                    "analyze_audio", extensions_attr="EXTENSIONS",
                    declared_extensions={".mp3", ".wav", ".flac", ".m4a",
                                         ".aac", ".ogg", ".wma", ".opus",
                                         ".aiff"}),
    AnalyzerAdapter("video", "Video Analysis", "VideoAnalysis",
                    "analyze_video", extensions_attr="EXTENSIONS",
                    declared_extensions={".mp4", ".mkv", ".avi", ".mov",
                                         ".wmv", ".flv", ".webm", ".m4v",
                                         ".mpg", ".mpeg", ".3gp"}),
    AnalyzerAdapter("text", "Text / Markdown Analysis", "TextFileAnalysis",
                    "analyze_text", extensions_attr="EXTENSIONS",
                    declared_extensions={".txt", ".md"}),
    ArchiveAdapter("archive", "Archive Analysis", "ArchiveAnalysis",
                   "analyze_archive", extensions_attr="EXTENSIONS",
                   declared_extensions={".zip", ".7z"}),
    ExtractionAdapter("content_extraction", "Content Extraction",
                      "ContentExtraction", None, extensions_attr="EXTENSIONS",
                      declared_extensions={".pdf", ".docx", ".pptx", ".xlsx",
                                           ".txt", ".md"}),
]

ADAPTER_BY_KEY = {a.key: a for a in ADAPTERS}


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def select_applicable(entries, extensions, exclusions=None):
    r"""Candidate files for one analyzer, plus the excluded count.

    Reproduces find_rows_from_csv's rule exactly, including the order
    of the two tests: exclusions are checked FIRST, so a RAW file is
    counted as excluded rather than simply not matching. Swapping them
    would give the same selection and a different reported count.
    """
    exclusions = exclusions or set()
    applicable = []
    excluded = 0
    for entry in entries:
        suffix = Path(entry.path).suffix.lower()
        if suffix in exclusions:
            excluded += 1
            continue
        if suffix in extensions:
            applicable.append(entry)
    return applicable, excluded


def missing_dependencies(key):
    """Import names an analyzer needs that are not importable here."""
    names, _hint = DEPENDENCY_IMPORTS.get(key, ((), ""))
    missing = []
    for name in names:
        try:
            __import__(name)
        except Exception:                                       # noqa: BLE001
            missing.append(name)
    return missing


def dependency_hint(key):
    return DEPENDENCY_IMPORTS.get(key, ((), ""))[1]


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------

class AnalyzerEngine(object):
    """Runs the analyzers in-process over one run's inventory."""

    def __init__(self, progress=None, logger=None, hash_size=DEFAULT_IMAGE_HASH_SIZE,
                 skip_cloud_only=False):
        self.progress = progress
        self.logger = logger
        self.hash_size = hash_size
        self.skip_cloud_only = skip_cloud_only

    def _log(self, severity, message):
        if self.logger is not None:
            try:
                self.logger(severity, message)
            except Exception:                                   # noqa: BLE001
                pass

    def _report_progress(self, key, done, total):
        if self.progress is not None:
            try:
                self.progress(key, done, total)
            except Exception:                                   # noqa: BLE001
                pass

    def run_all(self, entries, context=None, only=None, sink=None):
        r"""Run every analyzer, in order, isolated from one another.

        Returns a list of AnalyzerOutcome in execution order. Never
        raises for an analyzer-level problem: an analyzer that cannot
        import its library, or whose selection or teardown raises, is
        recorded 'failed' with a reason and the loop continues. That is
        the whole point of running them in one process rather than
        nine -- the isolation has to be explicit, because it is no
        longer being provided by the operating system.
        """
        entries = list(entries)
        context = dict(context or {})
        context.setdefault("hash_size", self.hash_size)
        outcomes = []
        for adapter in ADAPTERS:
            if only and adapter.key not in only:
                continue
            outcomes.append(self.run_one(adapter, entries, context, sink=sink))
        return outcomes

    def run_one(self, adapter, entries, context, sink=None):
        r"""Run a single analyzer. Never raises.

        `sink`, when given, is called as sink(analyzer_key, result) for
        each result as it is produced, and the outcome stops retaining
        results. That is the B5-E.F007 fix: persistence becomes
        incremental and peak memory stops being a function of how many
        applicable files the project has.
        """
        outcome = AnalyzerOutcome(adapter.key, adapter.label,
                                  retain=(sink is None))
        started = time.time()
        try:
            self._run_one_inner(adapter, entries, context, outcome, sink)
        except Exception as exc:                                # noqa: BLE001
            outcome.status = STATUS_FAILED
            outcome.failure_reason = normalize_diagnostic_text(
                "%s: %s" % (type(exc).__name__, exc))
            self._log("ERROR", "%s failed: %s"
                      % (adapter.label, outcome.failure_reason))
        outcome.elapsed_sec = time.time() - started
        return outcome

    def _run_one_inner(self, adapter, entries, context, outcome, sink=None):
        # Selection needs the module (for its extension sets), so an
        # import failure surfaces here and is reported as this
        # analyzer failing rather than as zero applicable files. The
        # two look identical in a result count and mean opposite
        # things.
        # Applicability is decided WITHOUT requiring the module to load.
        # A project with no images is a project with no images whether or
        # not Pillow is installed, and reporting that as an analyzer
        # failure would turn a missing optional dependency into nine
        # scary red stages on a machine that never needed it.
        extensions = adapter.extensions()
        exclusions = adapter.exclusions()
        applicable, excluded = select_applicable(entries, extensions, exclusions)
        outcome.applicable_count = len(applicable)
        outcome.excluded_count = excluded

        if not applicable:
            # A real, successful outcome -- not a failure and not an
            # empty completed run. No results, and no CSV downstream.
            outcome.status = STATUS_NO_APPLICABLE
            self._log("INFO", "%s: no applicable files." % adapter.label)
            return

        # There ARE applicable files, so now the module has to load.
        missing = missing_dependencies(adapter.key)
        if missing or adapter.import_error():
            outcome.status = STATUS_FAILED
            if missing:
                outcome.failure_reason = (
                    "missing Python package(s): %s. Install with: pip install %s"
                    % (", ".join(missing), dependency_hint(adapter.key)))
            else:
                outcome.failure_reason = normalize_diagnostic_text(
                    adapter.import_error())
            self._log("ERROR", "%s: %s" % (adapter.label,
                                           outcome.failure_reason))
            return

        try:
            analyze = adapter.analyze_fn(context)
        except Exception as exc:                                # noqa: BLE001
            outcome.status = STATUS_FAILED
            outcome.failure_reason = normalize_diagnostic_text(
                "%s: %s" % (type(exc).__name__, exc))
            self._log("ERROR", "%s: %s" % (adapter.label,
                                           outcome.failure_reason))
            return

        total = len(applicable)
        for index, entry in enumerate(applicable, start=1):
            result = AnalyzerResult(entry)
            if self.skip_cloud_only and entry.is_offline_or_cloud:
                # run_analysis's own vocabulary for this, preserved so
                # the exported CSV and the persisted status agree with
                # the accepted contract.
                result.error = "SkippedCloudOnly"
            else:
                try:
                    # The analyzer receives an openable path; the result
                    # keeps the ordinary stored one.
                    adapter.apply(result, analyze(openable_path(entry.path)))
                except Exception as exc:                        # noqa: BLE001
                    # Per-file isolation: this row carries the error and
                    # no invented fields, and the next file still runs.
                    #
                    # THE DIAGNOSTIC BOUNDARY. The message is whatever
                    # the library said, minus any \\?\ prefix it quoted
                    # back at us. The failure is preserved exactly --
                    # same file, same wording, still an error -- only the
                    # path representation becomes the ordinary one.
                    result.fields = {}
                    result.extra = None
                    result.error = normalize_diagnostic_text(str(exc))

            # Counted always; retained only when there is no sink.
            outcome.record(result)
            if sink is not None:
                try:
                    sink(adapter.key, result)
                except Exception as exc:                      # noqa: BLE001
                    # A PERSISTENCE FAILURE MUST NOT READ AS SUCCESS.
                    #
                    # An earlier draft logged this and carried on, and a
                    # missing import in the sink then produced a run
                    # that reported `completed` with zero rows written.
                    # That is precisely the false completion B5-G exists
                    # to prevent, manufactured by the error handling
                    # rather than by a crash.
                    #
                    # So the first sink failure fails the ANALYZER. The
                    # analysis itself may have been fine, which is why
                    # the reason says persistence -- but a stage whose
                    # results did not reach the database has not
                    # completed, and must not claim to have.
                    #
                    # Other analyzers are unaffected: run_one catches
                    # this per adapter, so one analyzer's persistence
                    # problem still does not stop the rest.
                    outcome.status = STATUS_FAILED
                    outcome.failure_reason = (
                        "Results could not be persisted: %s"
                        % normalize_diagnostic_text(str(exc)))
                    self._log("ERROR", "%s: %s"
                              % (adapter.label, outcome.failure_reason))
                    raise

            if index % 25 == 0 or index == total:
                self._report_progress(adapter.key, index, total)

        outcome.status = STATUS_COMPLETED
        self._log("INFO", "%s: %d succeeded, %d failed, %d skipped."
                  % (adapter.label, outcome.succeeded_count,
                     outcome.error_count, outcome.skipped_count))
