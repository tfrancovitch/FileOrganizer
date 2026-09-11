r"""The blocking runner: one long operation at a time, with a way out.

Work is blocking by decision -- "set it and forget it" -- so while a run is in
progress the window shows nothing but the run. Blocking is about attention,
not about being trapped, so the same screen carries a Cancel that actually
works: it sets one event, the coordinator's `should_continue` hook reads it,
and every engine checks the hook between files. A file is either fully
processed or never opened; everything already done is kept; the stage and the
run are recorded as 'cancelled', not 'completed' and not 'failed'.

THE CONNECTION BOUNDARY. `fo_db.connect()` documents a single-writer rule and
runs migrations under BEGIN IMMEDIATE, so the Dashboard must not hold its
write connection while a stage runs. `run_blocking()` closes it, runs the
stage on a worker thread, and reopens it afterwards -- the same order
`launch.py` uses, and the boundary whose absence broke the original P2.11
harness.

Two halves, kept apart on purpose:

  RunWorker    no Tk at all. Given a request and three callbacks, it drives
               RunCoordinator exactly as Dashboard.py does and returns a
               RunOutcome. This is what the headless checks exercise.
  run_blocking the Tk screen: progress, a log, Cancel; marshals the worker's
               callbacks onto the main thread through a queue.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

# Run kinds, spelled as the `run` table spells them, plus the one Phase 2
# operation that builds a derived index rather than collecting evidence.
PRESCAN = "prescan"
DUPLICATES = "duplicate_analysis"
FINGERPRINT = "exhaustive_identity"
ANALYSIS = "content_analysis"
INDEX_TEXT = "index_text"

# Stage keys keep their historical names: they are the accepted identity in
# every run record, and Dashboard.py documents why they must not change.
NEW_PROJECT_STAGE_KEY = "New-Project.ps1"
INVENTORY_STAGE_KEY = "PreliminaryInventory.ps1"
SIZE_CANDIDATE_STAGE_KEY = "PotentialDuplicates.ps1"
TIME_ESTIMATES_STAGE_KEY = "TimeEstimates.ps1"
PARTIAL_HASH_STAGE_KEY = "PartialHash.ps1"
FULL_RUN_STAGE_KEY = "FullHashInventory.ps1"

COMPLETED = "completed"
COMPLETED_WITH_WARNINGS = "completed_with_warnings"
CANCELLED = "cancelled"
FAILED = "failed"


class RunRequest:
    """What to run. `after` names the view to show when it is over."""

    def __init__(self, kind, title, analyzer_keys=None, source_roots=None,
                 project_name=None, after="hub", estimate=None):
        self.kind = kind
        self.title = title
        self.analyzer_keys = list(analyzer_keys or [])
        self.source_roots = list(source_roots or [])
        self.project_name = project_name
        self.after = after
        #: The estimate shown before the run started, kept so the outcome
        #: can be compared against it afterwards.
        self.estimate = estimate


class RunOutcome:
    def __init__(self, status, message, project_dir=None):
        self.status = status
        self.message = message
        self.project_dir = project_dir
        self.elapsed_sec = 0.0
        self.warnings = []

    @property
    def ok(self):
        return self.status in (COMPLETED, COMPLETED_WITH_WARNINGS)

    @property
    def cancelled(self):
        return self.status == CANCELLED


def app_root_for(project_dir):
    """The application root a project lives under, or None.

    RunCoordinator addresses a project as <app root>\\Projects\\<name>, so a
    project can only be run from the Dashboard when it sits in that layout.
    A project opened from anywhere else can still be queried -- it just
    cannot start a run, and the hub says so rather than failing later.
    """
    project_dir = Path(project_dir).resolve()
    if project_dir.parent.name.lower() != "projects":
        return None
    return project_dir.parent.parent


def load_settings(project_dir):
    path = Path(project_dir) / "settings.json"
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


class _NullStage:
    """Stand-in when there is no coordinator, so the worker reads straight."""
    run_stage_id = None
    status = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def record(self, *args, **kwargs):
        return kwargs.get("status")

    def record_skipped(self, *args, **kwargs):
        return "skipped"

    def record_failure(self, *args, **kwargs):
        return "failed"

    def add_event(self, *args, **kwargs):
        return None


class RunWorker:
    """Drives RunCoordinator for one request. No Tk; safe on any thread.

    log(text)                    a line for the person watching
    stage(text, fraction)        the headline and 0..100, or None for unknown
    progress(label, done, total) engine progress, total None when unknown
    """

    def __init__(self, app_root, project_dir, request, stop_event=None,
                 log=None, stage=None, progress=None):
        self.app_root = Path(app_root)
        self.project_dir = Path(project_dir) if project_dir else None
        self.request = request
        self.stop_event = stop_event or threading.Event()
        self._log = log or (lambda text: None)
        self._stage = stage or (lambda text, fraction: None)
        self._progress = progress or (lambda label, done, total: None)
        self.coordinator = None

    # -- plumbing -----------------------------------------------------

    def log(self, text):
        self._log(text)

    def stage(self, text, fraction=None):
        self._stage(text, fraction)

    def should_continue(self):
        return not self.stop_event.is_set()

    def _make_coordinator(self, project_name):
        import RunCoordinator as fo_coordinator
        coordinator = fo_coordinator.RunCoordinator(str(self.app_root), project_name)
        coordinator.should_continue = self.should_continue
        coordinator.progress = self._progress
        self.coordinator = coordinator
        return coordinator

    def _begin(self, run_kind, project_name, run_folder=None,
               tolerate_stage_failures=False):
        coordinator = self._make_coordinator(project_name)
        coordinator.begin_run(run_kind, run_folder=run_folder,
                              tolerate_stage_failures=tolerate_stage_failures)
        return coordinator

    @staticmethod
    def _stage_context(coordinator, key, label):
        if coordinator is None:
            return _NullStage()
        return coordinator.stage(key, label)

    @staticmethod
    def _finish(coordinator, status=None, notes=None):
        if coordinator is not None:
            coordinator.finish(status=status, notes=notes)

    # -- entry --------------------------------------------------------

    def run(self):
        """Run the request to its end. Never raises; a crash is an outcome."""
        started = time.monotonic()
        try:
            if self.request.kind == PRESCAN:
                outcome = self._run_prescan()
            elif self.request.kind == DUPLICATES:
                outcome = self._run_hash(DUPLICATES, PARTIAL_HASH_STAGE_KEY,
                                         "Hash & Duplicate Pass", "run_duplicate_hash")
            elif self.request.kind == FINGERPRINT:
                outcome = self._run_hash(FINGERPRINT, FULL_RUN_STAGE_KEY,
                                         "Full Hash Inventory (every file)",
                                         "run_full_hash_inventory")
            elif self.request.kind == ANALYSIS:
                outcome = self._run_analysis()
            elif self.request.kind == INDEX_TEXT:
                outcome = self._run_index()
            else:
                outcome = RunOutcome(FAILED, f"Unknown run kind: {self.request.kind}")
        except Exception as exc:                                # noqa: BLE001
            # The coordinator's own finally blocks close its run record;
            # this makes sure the person sees the reason too.
            outcome = RunOutcome(FAILED, f"{type(exc).__name__}: {exc}", self.project_dir)
            try:
                if self.coordinator is not None:
                    self.coordinator.finish(status="failed", notes=str(exc)[:500])
            except Exception:                                   # noqa: BLE001
                pass
        outcome.elapsed_sec = time.monotonic() - started
        return outcome

    # -- prescan ------------------------------------------------------

    def _run_prescan(self):
        """Create the project if asked, then the four Pre-Scan stages.

        Mirrors Dashboard._run_new_project_then_core stage for stage, so the
        run record a Dashboard prescan leaves and the one this leaves are the
        same record.
        """
        request = self.request
        creating = bool(request.source_roots) and self.project_dir is None
        project_name = request.project_name or (self.project_dir.name if self.project_dir else None)
        if not project_name:
            return RunOutcome(FAILED, "No project name was given.")

        total_steps = (1 if creating else 0) + 3
        step = 0
        coordinator = self._begin(PRESCAN, project_name)
        try:
            if creating:
                step += 1
                self.stage(f"Step {step} of {total_steps}: Creating project...", 0)
                self.log(f"Creating project '{project_name}' for: "
                         + "; ".join(request.source_roots))
                projects_dir = self.app_root / "Projects"
                with self._stage_context(coordinator, NEW_PROJECT_STAGE_KEY,
                                         "Creating project") as stage:
                    success, stdout, stderr, returncode = coordinator.create_project(
                        str(projects_dir), project_name, list(request.source_roots))
                    stage.record(returncode, stdout, stderr,
                                 status=None if success else "failed")
                if not success:
                    self._finish(coordinator, status="failed", notes="Project creation failed.")
                    return RunOutcome(FAILED, "Failed to create the project.\n\n"
                                      + (stderr or "")[:500])
                self.project_dir = projects_dir / project_name
                try:
                    coordinator.attach_database(project_name)
                except Exception as exc:                        # noqa: BLE001
                    self.log(f"  WARNING: could not attach the run to the database: {exc}")
                self.log(f"Project created: {self.project_dir}")

            settings_path = str(self.project_dir / "settings.json")

            # -- stage: the walk ------------------------------------------
            step += 1
            self.stage(f"Step {step} of {total_steps}: Preliminary Inventory...", None)
            self.log("Walking the source folder(s). Reads no file contents.")
            with self._stage_context(coordinator, INVENTORY_STAGE_KEY,
                                     "Preliminary Inventory") as stage:
                success, stdout, stderr, returncode = coordinator.run_inventory(settings_path)
                run_folder = load_settings(self.project_dir).get("CurrentRun")
                if run_folder:
                    try:
                        coordinator.bind_run_folder(run_folder)
                    except Exception as exc:                    # noqa: BLE001
                        self.log(f"  WARNING: could not bind the run folder: {exc}")
                stopped = coordinator.last_stage_stopped()
                stage.record(returncode, stdout, stderr,
                             status=CANCELLED if stopped else (None if success else "failed"))
                if success:
                    try:
                        coordinator.ingest_errors_txt(stage)
                    except Exception as exc:                    # noqa: BLE001
                        self.log(f"  WARNING: could not ingest errors.txt: {exc}")

            if success:
                self.log("Recording the inventory in the project database...")
                try:
                    persisted = coordinator.record_inventory_ingest() or {}
                    if persisted.get("status") not in (None, "failed", "skipped"):
                        self.log(f"  {persisted.get('rows', 0):,} file(s) recorded in "
                                 f"{persisted.get('elapsed_sec', 0.0):.1f}s.")
                    for warning in persisted.get("warnings") or []:
                        self.log(f"  NOTE: {warning}")
                except Exception as exc:                        # noqa: BLE001
                    self.log("  WARNING: the inventory could not be recorded in the "
                             f"database; the scan itself is unaffected. {exc}")

            if not success:
                self._finish(coordinator, status="failed", notes="Preliminary Inventory failed.")
                return RunOutcome(FAILED, "The inventory scan failed.\n\n" + (stderr or "")[:500],
                                  self.project_dir)
            if stopped:
                self._finish(coordinator, status=CANCELLED,
                             notes="Stopped by the user during the inventory walk.")
                return RunOutcome(CANCELLED,
                                  "Stopped during the walk. What was walked is recorded; "
                                  "the inventory is incomplete and says so.",
                                  self.project_dir)
            self.log("  done.")

            # -- stage: size candidates ------------------------------------
            step += 1
            self.stage(f"Step {step} of {total_steps}: Finding potential duplicates...",
                       (step - 1) * 100.0 / total_steps)
            self.log("Grouping files by size. Reads no file contents.")
            with self._stage_context(coordinator, SIZE_CANDIDATE_STAGE_KEY,
                                     "Finding Potential Duplicates") as stage:
                success, stdout, stderr, returncode = coordinator.run_size_candidates(settings_path)
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed")
            if not success:
                self._finish(coordinator, status="failed",
                             notes="Finding Potential Duplicates failed.")
                return RunOutcome(FAILED, "Size grouping failed.\n\n" + (stderr or "")[:500],
                                  self.project_dir)
            self.log("  done.")

            # -- stage: estimates -------------------------------------------
            step += 1
            self.stage(f"Step {step} of {total_steps}: Preparing time estimates...",
                       (step - 1) * 100.0 / total_steps)
            self.log("Reading a small sample to estimate how long fingerprinting would take.")
            with self._stage_context(coordinator, TIME_ESTIMATES_STAGE_KEY,
                                     "Preparing time estimates") as stage:
                success, stdout, stderr, returncode = coordinator.run_time_estimates(settings_path)
                stage.record(returncode, stdout, stderr,
                             status=None if success else "failed",
                             note=None if success else
                             "Optional stage; the run continued without time estimates.")
            if not success:
                self.log(f"  WARNING: calibration failed, continuing without estimates: "
                         f"{(stderr or '')[:200]}")
            else:
                self.log("  done.")

            self.stage("Pre-Scan complete.", 100)
            status = COMPLETED if success else COMPLETED_WITH_WARNINGS
            self._finish(coordinator, status=None if success else COMPLETED_WITH_WARNINGS)
            return RunOutcome(status, "Pre-Scan complete.", self.project_dir)
        finally:
            self._finish(coordinator)

    # -- hashing ------------------------------------------------------

    def _run_hash(self, run_kind, stage_key, label, method_name):
        settings = load_settings(self.project_dir)
        if not settings.get("CurrentRun"):
            return RunOutcome(FAILED, "This project has no Pre-Scan yet. Run the Pre-Scan first.",
                              self.project_dir)
        settings_path = str(self.project_dir / "settings.json")
        coordinator = self._begin(run_kind, self.project_dir.name,
                                  run_folder=settings.get("CurrentRun"))
        try:
            self.stage(f"Step 1 of 1: {label}...", 0)
            self.log(f"Running the hash engine ({label}).")
            with self._stage_context(coordinator, stage_key, label) as stage:
                success, stdout, stderr, returncode = getattr(coordinator, method_name)(settings_path)
                stopped = coordinator.last_stage_stopped()
                stage.record(returncode, stdout, stderr,
                             status=CANCELLED if stopped else (None if success else "failed"))
            if not success:
                self._finish(coordinator, status="failed", notes=f"{label} failed.")
                return RunOutcome(FAILED, f"{label} failed.\n\n" + (stderr or "")[:500],
                                  self.project_dir)
            for line in (stdout or "").splitlines():
                if line.strip():
                    self.log("  " + line.rstrip())
            if stopped:
                self._finish(coordinator, status=CANCELLED, notes="Stopped by the user.")
                return RunOutcome(CANCELLED,
                                  "Stopped. Every fingerprint taken so far is kept. Files never "
                                  "reached have no verdict, and the project says so.",
                                  self.project_dir)
            self.stage(f"{label} complete.", 100)
            self._finish(coordinator)
            return RunOutcome(COMPLETED, f"{label} complete.", self.project_dir)
        finally:
            self._finish(coordinator)

    # -- analysis -----------------------------------------------------

    def _run_analysis(self):
        import fo_analyzers

        settings = load_settings(self.project_dir)
        if not settings.get("CurrentRun"):
            return RunOutcome(FAILED, "This project has no Pre-Scan yet. Run the Pre-Scan first.",
                              self.project_dir)
        keys = [k for k in self.request.analyzer_keys if k in fo_analyzers.SPEC_BY_KEY]
        if not keys:
            return RunOutcome(FAILED, "No analyzer was selected.", self.project_dir)
        settings_path = str(self.project_dir / "settings.json")

        # One analyzer failing does not stop the others and does not make
        # the run a failure -- the same rule Dashboard.py applies.
        coordinator = self._begin(ANALYSIS, self.project_dir.name,
                                  run_folder=settings.get("CurrentRun"),
                                  tolerate_stage_failures=True)
        try:
            # Categories not selected are recorded as skipped, not left
            # absent: "not run", "ran and found nothing" and "ran and failed"
            # are three answers, and only the first is invisible unless it
            # is written down.
            for spec in fo_analyzers.ANALYZER_SPECS:
                if spec.key not in keys:
                    try:
                        coordinator.skip_stage(spec.script_name, spec.label,
                                               "Not selected for this analysis run.")
                    except Exception as exc:                    # noqa: BLE001
                        self.log(f"  WARNING: could not record skipped stage {spec.key}: {exc}")

            total = len(keys)
            warnings = []
            for index, key in enumerate(keys, start=1):
                spec = fo_analyzers.SPEC_BY_KEY[key]
                self.stage(f"Step {index} of {total}: {spec.label}...",
                           (index - 1) * 100.0 / total)
                self.log(f"Running {spec.label}...")
                with self._stage_context(coordinator, spec.script_name, spec.label) as stage:
                    success, stdout, stderr, returncode = coordinator.run_analyzers(
                        settings_path, [key], stage_handles={key: stage.run_stage_id})
                    stopped = coordinator.last_stage_stopped()
                    if stopped:
                        status = stage.record(returncode, stdout, stderr, status=CANCELLED)
                    else:
                        status = stage.record(returncode, stdout, stderr)
                for line in (stdout or "").splitlines():
                    if line.strip():
                        self.log("  " + line.rstrip())
                if stopped:
                    self._finish(coordinator, status=CANCELLED, notes="Stopped by the user.")
                    return RunOutcome(CANCELLED,
                                      f"Stopped during {spec.label}. Every file already analysed "
                                      "is kept; running it again starts over from the first file.",
                                      self.project_dir)
                if not success:
                    warnings.append(f"{spec.label} failed: {(stderr or '')[:300]}")
                    self.log(f"  WARNING: {spec.label} failed -- continuing with the rest.")
                elif stderr and stderr.strip():
                    warnings.append((stderr or "").strip()[:300])
                    self.log(f"  {stderr.strip()[:300]}")
                elif status == "no_applicable_files":
                    self.log("  done -- no files of this type were found.")
                else:
                    self.log("  done.")

            self.stage("Analysis complete.", 100)
            self._finish(coordinator)
            outcome = RunOutcome(COMPLETED_WITH_WARNINGS if warnings else COMPLETED,
                                 "Analysis complete.", self.project_dir)
            outcome.warnings = warnings
            return outcome
        finally:
            self._finish(coordinator)

    # -- text index (Phase 2, derived from evidence artifacts) ---------

    def _run_index(self):
        """Build the literal text index from extracted-text artifacts.

        Not a coordinator run: nothing is collected from a source file. The
        build reads artifacts under the project's Runs folder and records
        itself in p2_derived_index. All-or-nothing -- a stopped build leaves
        no half index, and the hub keeps offering to build it.
        """
        from .core import connect
        from .fts import FtsManager, FtsUnavailable

        self.stage("Indexing extracted text...", None)
        self.log("Reading extracted-text artifacts from the project's Runs folder. "
                 "No source file is opened.")
        conn = connect(self.project_dir, write=True)
        try:
            manager = FtsManager(conn, self.project_dir)
            try:
                result = manager.rebuild(cancel_check=lambda: self.stop_event.is_set())
            except InterruptedError:
                return RunOutcome(CANCELLED, "Stopped. No index was left behind; build it "
                                  "again when convenient.", self.project_dir)
            except FtsUnavailable as exc:
                return RunOutcome(FAILED, str(exc), self.project_dir)
        finally:
            conn.close()
        self.log(f"  Indexed {result['indexed']:,} distinct document(s), "
                 f"{result['bytes_read']:,} bytes of text.")
        if result["missing_artifacts"]:
            self.log(f"  {result['missing_artifacts']:,} referenced artifact(s) were "
                     "not on disk and could not be indexed.")
        self.stage("Index complete.", 100)
        outcome = RunOutcome(COMPLETED_WITH_WARNINGS if result["missing_artifacts"] else COMPLETED,
                             "Text index built.", self.project_dir)
        if result["missing_artifacts"]:
            outcome.warnings.append(f"{result['missing_artifacts']:,} evidence artifact(s) "
                                    "were missing from disk.")
        return outcome


# ---------------------------------------------------------------------------
# The Tk half
# ---------------------------------------------------------------------------

def run_blocking(app, request, on_done):
    """Own the window for the duration of one run.

    `app` must provide: `content` (the frame to draw in), `clear()`,
    `release_connection()` and `project_dir` (None while creating). The run
    itself happens on a worker thread; every widget update happens here, on
    the Tk thread, via the queue. `on_done(outcome)` is called on the Tk
    thread once the run is over and the connection may be reopened.
    """
    app.release_connection()
    app.clear()
    frame = app.content

    ttk.Label(frame, text=request.title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(frame, text="The window is busy until this finishes or you stop it. "
                          "Everything already done is kept if you stop.",
              style="Sub.TLabel").pack(anchor="w", pady=(2, 12))

    stage_var = tk.StringVar(value="Starting...")
    ttk.Label(frame, textvariable=stage_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")
    detail_var = tk.StringVar(value="")
    ttk.Label(frame, textvariable=detail_var, foreground="#666").pack(anchor="w", pady=(0, 6))

    bar = ttk.Progressbar(frame, orient="horizontal", length=560, mode="determinate")
    bar.pack(anchor="w", pady=(0, 10))
    bar.start(15)
    bar_mode = {"mode": "indeterminate"}
    bar.configure(mode="indeterminate")

    log = tk.Text(frame, height=16, width=100, state="disabled", font=("Consolas", 9))
    log.pack(fill="both", expand=True, pady=(0, 10))

    stop_event = threading.Event()
    cancel_var = tk.StringVar(value="Cancel")
    controls = ttk.Frame(frame)
    controls.pack(anchor="w")

    def request_stop():
        stop_event.set()
        cancel_var.set("Stopping after the current file...")
        cancel_button.configure(state="disabled")
        post(("log", "Stop requested. The current file finishes; nothing after it starts."))

    cancel_button = ttk.Button(controls, textvariable=cancel_var, command=request_stop)
    cancel_button.pack(side="left")

    inbox = queue.Queue()

    def post(item):
        inbox.put(item)

    def worker_main():
        worker = RunWorker(
            app_root_for(app.project_dir) if app.project_dir else app.app_root,
            app.project_dir, request, stop_event=stop_event,
            log=lambda text: post(("log", text)),
            stage=lambda text, fraction: post(("stage", text, fraction)),
            progress=lambda label, done, total: post(("progress", label, done, total)))
        outcome = worker.run()
        post(("done", outcome))

    def set_bar(fraction):
        if fraction is None:
            if bar_mode["mode"] != "indeterminate":
                bar.configure(mode="indeterminate")
                bar.start(15)
                bar_mode["mode"] = "indeterminate"
        else:
            if bar_mode["mode"] != "determinate":
                bar.stop()
                bar.configure(mode="determinate")
                bar_mode["mode"] = "determinate"
            bar["value"] = max(0.0, min(100.0, float(fraction)))

    def poll():
        try:
            while True:
                item = inbox.get_nowait()
                kind = item[0]
                if kind == "log":
                    log.configure(state="normal")
                    log.insert("end", item[1] + "\n")
                    log.see("end")
                    log.configure(state="disabled")
                elif kind == "stage":
                    stage_var.set(item[1])
                    detail_var.set("")
                    set_bar(item[2])
                elif kind == "progress":
                    label, done, total = item[1], item[2], item[3]
                    if total:
                        detail_var.set(f"{_progress_label(label)}: {done:,} of {total:,}")
                        set_bar(done * 100.0 / total)
                    else:
                        detail_var.set(f"{_progress_label(label)}: {done:,} files so far")
                        set_bar(None)
                elif kind == "done":
                    bar.stop()
                    on_done(item[1])
                    return
        except queue.Empty:
            pass
        app.after(150, poll)

    thread = threading.Thread(target=worker_main, daemon=True)
    # Registered on the app so the window's close button can stop the run
    # and wait for the current file rather than killing the daemon thread.
    app.active_run = (stop_event, thread)
    thread.start()
    app.after(150, poll)


_PROGRESS_LABELS = {
    "inventory": "Walking",
    "partial_hash": "Partial hashes",
    "full_hash": "Full hashes",
    "image": "Images", "pdf": "PDFs", "office": "Office documents",
    "raw_image": "RAW images", "audio": "Audio", "video": "Video",
    "text": "Text / Markdown", "archive": "Archives",
    "content_extraction": "Extracting text",
}


def _progress_label(label):
    return _PROGRESS_LABELS.get(label, str(label).replace("_", " ").title())
