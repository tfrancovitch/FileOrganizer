r"""Similar Images -- the Image Game (Phase 3, Build 5), inside the one window.

One visual candidate pair at a time (A/B Compare; Survey Mode is a later
build): two large previews side by side, zoom and pan linked by default
(unlink, and re-sync, on request), Fit and 100% -- full resolution is
always one key away, never thumbnails only -- and each image's evidence
under it: path, resolution, format, size, modified time, the capture date
the file itself records, and the candidate's measurement named by its
method ("Perceptual hash (pHash) -- Hamming distance 6 of 64 bits"), never
a made-up universal percentage.

The decisions are the research's, and relationship and retention stay two
rows even when one key records both:

    A  Equivalent -> Prefer Left      equivalent_copy + canonical_location (left)
    L  Equivalent -> Prefer Right     equivalent_copy + canonical_location (right)
    E  Equivalent -> Keep Both        equivalent_copy + keep_both
    V  Related Variant -> Keep Both   related_variant + keep_both
    D  Different                      different (withdraws any retention on the pair)
    [  ]  Canonical: Left / Right     canonical_location alone (with Keep Both: the representative)
    W  Defer... / Restore      S  Skip      U / Ctrl+Z  Undo the last action
    F  Fit   H  100%   + / -  zoom   K  link / unlink   Y  re-sync   X  swap sides
    N / P (or the arrow keys in the list)  next / previous pair

Left and right are presentation only: every row stores the actual file
location ids, and X swaps the sides without changing anything recorded.

Undo withdraws what the last action recorded (one operation, one Undo); a
decision it replaced is not revived -- the rule since Build 1. Progress is
the decision record itself, so a review resumes where it stopped: the
queue holds exactly the pairs no decision has settled.

Byte-identical pairs are not in the queue: the exact-duplicate review
already answers them. They can still be opened here ("Exact copies" in
Show, or "Compare images..." on the Duplicate decisions page) to choose the
group's canonical by eye -- recorded on the exact-duplicate group, where
that decision lives.

Nothing on this page writes a source file. A preview is a read: the file is
opened read-only through Pillow and decoded in memory. A cloud-only file
(checked live from its attributes, not only from the last scan) is never
opened -- the product does not download a file to show it.

Build 6 (B12) adds the bulk path, for a collection whose look-alikes come in
crowds (a folder of variations of one prompt): a check column on the list
(click it, or Space), and Bulk action..., which previews one relationship
action over the checked pairs, the pairs the Show list holds, or those of
them with both images in one folder -- frozen at commit, never a standing
rule -- and records, pair by pair, the same rows a key would. Batches...
lists every batch with Undo. The letter keys still act on the pair in view
only; a check never changes what a key does.
"""
from __future__ import annotations

import csv
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from Phase2 import visual as V
from Phase2.core import utc_now

from . import bulk as BK
from . import evidence as E
from . import resolve as R
from . import routing as RT
from .model import active_decisions
from .pairs import EQUIVALENT, RELATED, DIFFERENT, resolve_pairs
from .registry import (LOCATION, GROUP, VISUAL_PAIR, DECISION_KINDS, SCOPE_EXPLICIT_SELECTION, SCOPE_QUERY_SNAPSHOT,
                       DISP_DECIDED, bulk_actions_for)
from .review import store_for, run_detector, DeferDialog, BatchesDialog, human_bytes, CHECKED, UNCHECKED
from .store import new_command_id

#: The extensions the image analyzer hashes (ImageHash.IMAGE_EXTENSIONS) --
#: repeated here so this module does not import Pillow to know a name.
PREVIEWABLE = {".jpg", ".jpeg", ".jfif", ".png", ".gif", ".bmp", ".tiff", ".tif",
               ".webp", ".ico", ".heic", ".heif", ".avif", ".jp2"}

#: Show: the routes, then the lenses. "Visual Similarity Review" is the
#: pairs' primary queue. The filter itself is a pure function in bulk.py (a
#: snapshot batch freezes exactly what it lists -- Build 6); these names
#: stay here for the page.
PAIR_FILTERS = BK.PAIR_ROUTE_FILTERS
LENS_STRONG = BK.LENS_STRONG
LENS_CROSS_ROOT = BK.LENS_CROSS_ROOT
LENS_ACROSS_FOLDERS = BK.LENS_ACROSS_FOLDERS
LENS_EXACT = BK.LENS_EXACT
LENS_ALL = BK.LENS_ALL
LENSES = BK.PAIR_LENSES
FILTERS = BK.PAIR_FILTERS
filter_pairs = BK.filter_pairs

KEYMAP = (
    ("a", "equivalent_left", "Equivalent -> Prefer Left"),
    ("l", "equivalent_right", "Equivalent -> Prefer Right"),
    ("e", "equivalent_both", "Equivalent -> Keep Both"),
    ("v", "related_both", "Related Variant -> Keep Both"),
    ("d", "different", "Different"),
    ("bracketleft", "canonical_left", "Canonical: Left"),
    ("bracketright", "canonical_right", "Canonical: Right"),
    ("w", "defer", "Defer... / Restore"),
    ("s", "skip", "Skip"),
    ("u", "undo", "Undo"),
    ("f", "fit", "Fit"),
    ("h", "actual", "100%"),
    ("plus", "zoom_in", "Zoom in"),
    ("equal", "zoom_in", "Zoom in"),
    ("minus", "zoom_out", "Zoom out"),
    ("k", "toggle_link", "Link / unlink"),
    ("y", "resync", "Re-sync"),
    ("x", "swap", "Swap sides"),
    ("n", "next_pair", "Next pair"),
    ("p", "previous_pair", "Previous pair"),
)
#: The list shows a window of this many pairs around the one in view; the
#: queue is ordered (closest first), and a real collection can hold tens of
#: thousands of pairs -- drawing them all on every keystroke would not do.
LIST_WINDOW = 500

KEY_LEGEND = ("Keys: A / L equivalent, prefer left / right · E equivalent, keep both · V related variant, keep both · "
              "D different · [ ] canonical left / right · W defer · S skip · U undo · F fit · H 100% · + / - zoom · "
              "K link · Y re-sync · X swap sides · N / P next / previous")

RELATIONSHIP_WORDS = {EQUIVALENT: "Equivalent copy", RELATED: "Related variant", DIFFERENT: "Different"}


def _file_name(path):
    return str(path or "").replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1]


def _folder(path):
    s = str(path or "").replace("/", "\\").rstrip("\\")
    return s.rsplit("\\", 1)[0] if "\\" in s else s


def _short(text, limit=54):
    text = str(text or "")
    return text if len(text) <= limit else text[:18] + "..." + text[-(limit - 21):]


def is_previewable(path):
    return os.path.splitext(str(path or ""))[1].lower() in PREVIEWABLE


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def show_similar(app, pair_id=None, compare=None):
    """Similar images: the candidate pairs, one at a time. `compare` =
    (left file_path_id, right file_path_id) opens two images on request --
    the voluntary comparison of an exact-duplicate pair."""
    if app.conn is None:
        app.show_welcome()
        return
    app.clear()
    app.header("Similar images",
               "Pairs of images whose perceptual hashes are close -- look, then say what they are to each other. "
               "Each answer is about this pair only; nothing is inferred for a third image. Nothing is deleted, moved "
               "or renamed here.", back=True)
    page = SimilarImagesPage(app)
    app.similar_page = page
    page.build(app.content)
    page.generate_if_needed()
    page.detect()
    if compare is not None:
        page.reload(select=None)
        page.open_compare(*compare)
    else:
        page.reload(select=pair_id)


class ImageView:
    """One side of the comparison: a canvas that shows any region of the
    full-resolution image at any scale. The image is decoded once, in memory;
    a small pyramid of reduced copies keeps a zoomed-out view quick."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.image = None
        self.levels = {}
        self.message = "No image."
        self.scale = 1.0
        self.cx = self.cy = 0.5
        self.fitted = True
        self.photo = None
        self.capture = None

    # -- loading ------------------------------------------------------------------

    def clear(self, message):
        self.image, self.levels, self.photo, self.capture = None, {}, None, None
        self.message = message
        self.render()

    def load(self, facts):
        """Decode the file for display, read-only. Returns None, or why not."""
        self.image, self.levels, self.capture = None, {}, None
        if facts is None:
            self.clear("This file is not in the evidence.")
            return self.message
        if facts.get("state") != "present":
            self.clear(f"Not present at the last scan ({facts.get('state')}).")
            return self.message
        path = facts["path"]
        try:
            import win_meta
            st = os.stat(path)
            attrs = getattr(st, "st_file_attributes", 0)
            if facts.get("is_offline_or_cloud") or win_meta.is_cloud_placeholder(attrs):
                self.clear("Cloud-only -- no preview. The product never downloads a file to show it.")
                return self.message
        except FileNotFoundError:
            self.clear("The file is not there now (moved or deleted since the last scan).")
            return self.message
        except OSError as exc:
            self.clear(f"The file cannot be read: {exc}")
            return self.message
        try:
            from PIL import Image, ImageOps
            try:
                from pillow_heif import register_heif_opener
                register_heif_opener()
            except ImportError:
                pass
            from file_organizer_common import to_long_path
            with Image.open(to_long_path(path)) as img:
                img.load()
                try:
                    exif = img.getexif()
                    self.capture = (exif.get_ifd(0x8769).get(36867) or exif.get(306) or None)
                except Exception:                                   # noqa: BLE001
                    self.capture = None
                shown = ImageOps.exif_transpose(img)
                if shown.mode not in ("RGB", "RGBA", "L"):
                    shown = shown.convert("RGBA" if "A" in shown.getbands() or shown.mode == "P" else "RGB")
                self.image = shown.copy()
        except Exception as exc:                                    # noqa: BLE001
            self.clear(f"The image could not be decoded: {exc}")
            return self.message
        self.levels = {1: self.image}
        self.message = ""
        return None

    @property
    def size(self):
        return self.image.size if self.image is not None else (1, 1)

    def _canvas_size(self):
        w = max(int(self.canvas.winfo_width()), 2)
        h = max(int(self.canvas.winfo_height()), 2)
        return w, h

    def fit_scale(self):
        w, h = self._canvas_size()
        iw, ih = self.size
        return min(w / iw, h / ih)

    # -- the view -----------------------------------------------------------------

    def fit(self):
        self.fitted = True
        self.scale = self.fit_scale()
        self.cx = self.cy = 0.5

    def actual(self):
        self.fitted = False
        self.scale = 1.0

    def relative_zoom(self):
        return self.scale / max(self.fit_scale(), 1e-9)

    def set_relative(self, rel, cx, cy):
        self.fitted = abs(rel - 1.0) < 1e-6 and abs(cx - 0.5) < 1e-6 and abs(cy - 0.5) < 1e-6
        self.scale = max(self.fit_scale() * rel, 1e-4)
        self.cx, self.cy = cx, cy
        self._clamp()

    def zoom_at(self, factor, x=None, y=None):
        if self.image is None:
            return
        w, h = self._canvas_size()
        x = w / 2 if x is None else x
        y = h / 2 if y is None else y
        iw, ih = self.size
        # The image point under (x, y) stays under it.
        px = self.cx * iw + (x - w / 2) / self.scale
        py = self.cy * ih + (y - h / 2) / self.scale
        self.scale = min(max(self.scale * factor, self.fit_scale() * 0.25), 32.0)
        self.fitted = False
        self.cx = (px - (x - w / 2) / self.scale) / iw
        self.cy = (py - (y - h / 2) / self.scale) / ih
        self._clamp()

    def pan(self, dx, dy):
        if self.image is None:
            return
        iw, ih = self.size
        self.fitted = False
        self.cx -= dx / self.scale / iw
        self.cy -= dy / self.scale / ih
        self._clamp()

    def _clamp(self):
        self.cx = min(max(self.cx, 0.0), 1.0)
        self.cy = min(max(self.cy, 0.0), 1.0)

    def render(self):
        c = self.canvas
        c.delete("all")
        w, h = self._canvas_size()
        if self.image is None:
            c.create_text(w // 2, h // 2, text=self.message, width=max(w - 20, 50), fill="#555", justify="center")
            return
        if self.fitted:
            self.scale = self.fit_scale()
        from PIL import Image, ImageTk
        iw, ih = self.size
        s = self.scale
        x0, y0 = self.cx * iw - w / 2 / s, self.cy * ih - h / 2 / s
        bx0, by0 = max(0.0, x0), max(0.0, y0)
        bx1, by1 = min(float(iw), x0 + w / s), min(float(ih), y0 + h / s)
        if bx1 <= bx0 or by1 <= by0:
            return
        f = 1
        while f < 32 and s * f * 2 <= 1.0:
            f *= 2
        level = self.levels.get(f)
        if level is None:
            level = self.image.reduce(f) if f > 1 else self.image
            self.levels[f] = level
        out_w, out_h = max(1, round((bx1 - bx0) * s)), max(1, round((by1 - by0) * s))
        resample = Image.NEAREST if s >= 2.0 else Image.BILINEAR
        region = level.resize((out_w, out_h), resample, box=(bx0 / f, by0 / f, bx1 / f, by1 / f))
        self.photo = ImageTk.PhotoImage(region)
        c.create_image(round((bx0 - x0) * s), round((by0 - y0) * s), image=self.photo, anchor="nw")
        c.create_text(6, h - 6, anchor="sw", fill="#fff", text=f"{s * 100:.0f}%", font=("Segoe UI", 8))


class SimilarImagesPage:
    def __init__(self, app):
        self.app = app
        self.store = store_for(app)
        self.ev = self.proj = self.routing = None
        self.filter_var = tk.StringVar(value=FILTERS[0])
        self.selected = None                    # the pair shown (its pair_id), or None in an on-request comparison
        self.left_id = self.right_id = None     # presentation only
        self.compare_only = False               # two images opened on request, not a candidate pair
        self.linked = tk.BooleanVar(value=True)
        self.focus_side = 0
        self.undo_stack = []                    # [(pair_id, [decision ids the action recorded], label)]
        self.status_var = tk.StringVar(value="")
        self.facts = {}
        self.generation = None
        self.checked = set()                    # pair ids a person has checked (Build 6)

    # -- layout ---------------------------------------------------------------

    def build(self, parent):
        tools = ttk.Frame(parent)
        tools.pack(fill="x", pady=(0, 6))
        self.summary_var = tk.StringVar(value="")
        ttk.Label(tools, textvariable=self.summary_var, font=("Segoe UI", 9, "bold"), wraplength=620,
                  justify="left").pack(side="left", fill="x", expand=True)
        ttk.Button(tools, text="Find similar images again", command=self.regenerate).pack(side="right")
        cb = ttk.Combobox(tools, textvariable=self.filter_var, values=FILTERS, state="readonly", width=32)
        cb.pack(side="right", padx=(0, 10))
        ttk.Label(tools, text="Show:").pack(side="right", padx=(0, 4))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_list())

        panes = tk.PanedWindow(parent, orient="horizontal", sashrelief="raised", sashwidth=6, bd=0)
        panes.pack(fill="both", expand=True)
        left = ttk.Frame(panes)
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, minsize=260, width=390)
        panes.add(right, minsize=560)
        self._build_list(left)
        self._build_compare(right)
        self._pack_views()
        self._bind_keys()

    def _build_list(self, host):
        host.rowconfigure(0, weight=3)
        host.rowconfigure(5, weight=1)
        host.columnconfigure(0, weight=1)
        cols = ("check", "status", "measure", "left", "right")
        self.table = ttk.Treeview(host, columns=cols, show="headings", selectmode="browse")
        for key, heading, width in (("check", "\u2713", 26), ("status", "Status", 84), ("measure", "Bits", 36),
                                    ("left", "Image", 116), ("right", "Look-alike", 116)):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, minwidth=26 if key == "check" else 40, stretch=False,
                              anchor="center" if key == "check" else "e" if key == "measure" else "w")
        # The check column (Build 6): click it, or Space on the focused row.
        # A check never changes what a letter key does -- the letters act on
        # the pair in view; the bulk path is the dialog and its preview.
        self.table.bind("<Button-1>", self._table_click, add="+")
        self.table.bind("<KeyPress-space>", lambda e: (self.toggle_check(), "break")[1])
        sy = ttk.Scrollbar(host, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=sy.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        self.table.bind("<<TreeviewSelect>>", lambda e: self._pair_selected())
        for tag, colour in (("conflict", "#8a1c1c"), ("revalidate", "#7a3e00"), ("blocked", "#555"), ("deferred", "#7a5700"),
                            ("resolved", "#1f6244")):
            self.table.tag_configure(tag, foreground=colour)
        self.count_var = tk.StringVar(value="")
        ttk.Label(host, textvariable=self.count_var, foreground="#444", wraplength=320, justify="left").grid(row=1, column=0, sticky="w", pady=(4, 0))
        bar = ttk.Frame(host)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.checked_var = tk.StringVar(value="Nothing checked")
        ttk.Label(bar, textvariable=self.checked_var, font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(bar, text="Check all shown", command=self.check_all_shown).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Clear", command=self.clear_checks).pack(side="left", padx=(4, 0))
        bar2 = ttk.Frame(host)
        bar2.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.bulk_button = ttk.Button(bar2, text="Bulk action...", command=self.bulk_action)
        self.bulk_button.pack(side="left")
        ttk.Button(bar2, text="Batches...", command=self.open_batches).pack(side="left", padx=(6, 0))
        ttk.Label(host, text="Decisions on this pair", font=("Segoe UI", 10, "bold")).grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.history = ttk.Treeview(host, columns=("when", "what", "state"), show="headings", height=6, selectmode="none")
        for key, heading, width in (("when", "When (UTC)", 120), ("what", "What", 160), ("state", "State", 70)):
            self.history.heading(key, text=heading)
            self.history.column(key, width=width, minwidth=40, stretch=False, anchor="w")
        self.history.tag_configure("inactive", foreground="#888")
        self.history.grid(row=5, column=0, columnspan=2, sticky="nsew")

    def _build_compare(self, host):
        self.compare_host = host
        self.title = ttk.Label(host, text="Select a pair.", font=("Segoe UI", 11, "bold"), wraplength=900, justify="left")
        self.title.pack(anchor="w")
        self.measure_label = ttk.Label(host, text="", foreground="#333", wraplength=900, justify="left")
        self.measure_label.pack(anchor="w", pady=(2, 4))
        self.notice = ttk.Label(host, text="", style="Warn.TLabel", wraplength=900, justify="left")

        bar = ttk.Frame(host)
        bar.pack(fill="x", pady=(0, 4))
        self.view_bar = bar
        for text, cmd in (("Fit (F)", self.fit), ("100% (H)", self.actual), ("Zoom + ", self.zoom_in), ("Zoom -", self.zoom_out),
                          ("Re-sync (Y)", self.resync), ("Swap sides (X)", self.swap)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Checkbutton(bar, text="Link zoom and pan (K)", variable=self.linked).pack(side="left", padx=(8, 0))

        views = ttk.Frame(host)
        self.views_frame = views
        views.columnconfigure(0, weight=1, uniform="side")
        views.columnconfigure(1, weight=1, uniform="side")
        views.rowconfigure(1, weight=1)
        self.captions, self.canvases, self.views, self.evidence = [], [], [], []
        for side in (0, 1):
            cap = ttk.Label(views, text="Left" if side == 0 else "Right", font=("Segoe UI", 10, "bold"))
            cap.grid(row=0, column=side, sticky="w", padx=(0 if side == 0 else 6, 0))
            canvas = tk.Canvas(views, background="#2b2b2b", highlightthickness=1, highlightbackground="#999", width=360, height=160,
                               takefocus=1)
            canvas.grid(row=1, column=side, sticky="nsew", padx=(0 if side == 0 else 6, 0))
            view = ImageView(canvas)
            canvas.bind("<Configure>", lambda e, v=view: v.render())
            canvas.bind("<MouseWheel>", lambda e, s=side: self._wheel(s, e))
            canvas.bind("<ButtonPress-1>", lambda e, s=side: self._press(s, e))
            canvas.bind("<B1-Motion>", lambda e, s=side: self._drag(s, e))
            ev_label = ttk.Label(views, text="", foreground="#333", justify="left", wraplength=440)
            ev_label.grid(row=2, column=side, sticky="nw", padx=(0 if side == 0 else 6, 0), pady=(4, 0))
            self.captions.append(cap)
            self.canvases.append(canvas)
            self.views.append(view)
            self.evidence.append(ev_label)
        self._views_packed = False

        # Packed from the bottom up, so the previews give way -- never the actions.
        legend = ttk.Label(host, text=KEY_LEGEND, foreground="#666", wraplength=900, justify="left")
        legend.pack(side="bottom", anchor="w", pady=(2, 0))
        status = ttk.Label(host, textvariable=self.status_var, foreground="#1f6244", wraplength=900, justify="left")
        status.pack(side="bottom", anchor="w", pady=(4, 0))
        # Text follows the pane's width (a long line wraps; it is never cut off).
        wrapped = (self.title, self.measure_label, self.notice, legend, status)
        host.bind("<Configure>", lambda e: [w.configure(wraplength=max(e.width - 16, 200)) for w in wrapped], add="+")
        # A grid, four to a row, so a narrow window squeezes the buttons
        # instead of cutting the last ones off.
        acts = ttk.Frame(host)
        acts.pack(side="bottom", fill="x", pady=(8, 0))
        self.buttons = {}
        layout = (("equivalent_left", "Equivalent -> Prefer Left (A)", self.equivalent_left),
                  ("equivalent_right", "Equivalent -> Prefer Right (L)", self.equivalent_right),
                  ("equivalent_both", "Equivalent -> Keep Both (E)", self.equivalent_both),
                  ("related_both", "Related Variant -> Keep Both (V)", self.related_both),
                  ("different", "Different (D)", self.different),
                  ("canonical_left", "Canonical: Left ([)", self.canonical_left),
                  ("canonical_right", "Canonical: Right (])", self.canonical_right),
                  ("defer", "Defer... (W)", self.defer),
                  ("skip", "Skip (S)", self.skip), ("undo", "Undo (U)", self.undo),
                  ("open_group", "Open in Duplicate decisions", self.open_group))
        for i, (key, text, cmd) in enumerate(layout):
            b = ttk.Button(acts, text=text, command=cmd)
            b.grid(row=i // 4, column=i % 4, sticky="ew", padx=(0, 6), pady=(0, 4))
            self.buttons[key] = b
        for col in range(4):
            acts.columnconfigure(col, weight=1, uniform="act")

    def _pack_views(self):
        if not self._views_packed:
            self.views_frame.pack(fill="both", expand=True)
            self._views_packed = True

    def _bind_keys(self):
        """The page's own keymap, on the pair list and both canvases (the
        widgets that hold focus here -- there is no text entry on this page)."""
        widgets = [self.table] + self.canvases
        for key, action, _label in KEYMAP:
            for w in widgets:
                w.bind(f"<KeyPress-{key}>", lambda e, a=action: self.key(a))
                if len(key) == 1:
                    w.bind(f"<KeyPress-{key.upper()}>", lambda e, a=action: self.key(a))
        for w in widgets:
            w.bind("<Control-z>", lambda e: self.key("undo"))
            w.bind("<Control-Z>", lambda e: self.key("undo"))
        for w in self.canvases:
            w.bind("<Down>", lambda e: self.key("next_pair"))
            w.bind("<Up>", lambda e: self.key("previous_pair"))

    def key(self, action):
        """One keystroke's action (the tests drive this; the bindings call it)."""
        getattr(self, action)()
        return "break"

    # -- data -----------------------------------------------------------------

    def generate_if_needed(self):
        """Compare the image hashes if the pairs are older than the newest
        image analysis (a project analyzed by an earlier build, say)."""
        try:
            self.generation = V.ensure_candidates(self.app.conn)
        except Exception as exc:                                    # noqa: BLE001
            self.generation = {"error": str(exc)}

    def regenerate(self):
        try:
            counts = V.generate_candidates(self.app.conn)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Similar images", f"The images could not be compared.\n\n{exc}", parent=self.app)
            return
        self.generation = counts
        self.detect()
        self.reload(select=self.selected)
        self.status_var.set(f"Compared {counts['images_considered']:,} images: {counts['pairs_current']:,} candidate pairs "
                            f"({counts['pairs_new']:,} new, {counts['pairs_retired']:,} no longer alike).")

    def detect(self):
        self.detector_result = run_detector(self.app.conn, self.store)

    def reload(self, select=None):
        """Everything from the database: evidence, record, projection, routes."""
        try:
            ev = E.load_evidence(self.app.conn)
            self.ev = ev
            self.proj = R.resolve_all(ev)
            self.routing = RT.route_all(ev, self.proj, ev.now)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Similar images", str(exc), parent=self.app)
            return
        self._summarize()
        self.refresh_list(select=select if select is not None else self.selected)

    def refresh_record(self, select=None):
        """After a decision, a deferral, a skip or an undo: the evidence has not
        moved, only the record has. Re-read the decisions and events and
        re-derive the pairs alone -- the same functions, less of the model --
        so a keystroke answers at once even with tens of thousands of pairs."""
        try:
            ev = self.ev
            ev.decisions = E.load_decisions(self.app.conn)
            ev.events = E.load_events(self.app.conn)
            ev.now = utc_now()
            active = active_decisions(ev.decisions)
            before = {p.pair_id: p for p in self.proj.pairs}
            self.proj.pairs = resolve_pairs(ev, R._index_decisions(active), active, previous=before)
            self.routing.pairs, self.routing.pair_counts = RT.route_pairs(ev, self.proj.pairs, ev.now,
                                                                          previous=self.routing.pairs)
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Similar images", str(exc), parent=self.app)
            return
        self._summarize(fresh=False)
        self.refresh_list(select=select if select is not None else self.selected)

    def _summarize(self, fresh=True):
        c = self.routing.pair_counts
        if fresh or getattr(self, "_status", None) is None:
            self._status = V.status(self.app.conn)       # the generation does not change with a decision
        st = self._status
        if not st.get("generation"):
            text = ("No image has been analyzed yet -- run Analyze on Pictures from the project summary."
                    if not st.get("images") else "The images have not been compared yet.")
        else:
            reviewed = sum(1 for p in self.proj.pairs if p.decision_ids)
            parts = [f"{c.get('pairs', 0):,} candidate pairs", f"{reviewed:,} reviewed", f"{c.get(RT.UNRESOLVED, 0):,} in the queue"]
            for route in (RT.CONFLICT, RT.NEEDS_REVALIDATION, RT.BLOCKED, RT.DEFERRED, RT.READY_FOR_PLAN):
                if c.get(route):
                    parts.append(f"{c[route]:,} {RT.STATUS_WORDS[route].lower()}")
            if c.get(RT.EXACT_CONTENT):
                n = c[RT.EXACT_CONTENT]
                parts.append(f"{n:,} byte-identical pair{'s' if n != 1 else ''} set aside")
            gen = st["generation"]
            parts.append(f"from {gen['images_considered']:,} images, pHash within {gen['max_distance']} of {gen['hash_bits']} bits")
            if gen.get("capped_images"):
                parts.append(f"{gen['capped_images']:,} images with more look-alikes than the {gen['neighbor_cap']} kept")
            text = " · ".join(parts)
        self.summary_var.set(text)

    def _route(self, p):
        return self.routing.pairs[p.pair_id]

    def refresh_list(self, select=None):
        pairs = filter_pairs(self.proj, self.routing, self.ev, self.filter_var.get())
        self.shown = [p.pair_id for p in pairs]
        self._shown_pairs = pairs
        anchor = select if select in self.shown else None
        self._draw_window(self.shown.index(anchor) if anchor is not None else 0)
        show = self.filter_var.get()
        if not pairs and show == RT.PAIR_QUEUE_WORD:
            c = self.routing.pair_counts
            elsewhere = ", ".join(f"{c[r]:,} {RT.STATUS_WORDS[r].lower()}" for r in RT.ROUTE_ORDER if r != RT.UNRESOLVED and c.get(r))
            self.count_var.set("Currently clear -- no pair awaits a decision" + (f" ({elsewhere})" if elsewhere else "") + ".")
        elif not pairs:
            self.count_var.set(f"Nothing here ({len(self.proj.pairs):,} pairs in all).")
        else:
            text = f"{len(pairs):,} of {len(self.proj.pairs):,} pairs shown"
            if len(pairs) > LIST_WINDOW:
                text += f" (listed {LIST_WINDOW:,} at a time, around the pair in view; N / P move through them all)"
            self.count_var.set(text)
        if self.compare_only:
            self._show_current()
            return
        if select is not None and select not in self.shown and self.proj.pair(select) is not None:
            # The action moved this pair off the route shown; keep it in view.
            self.show_pair(select, keep_sides=True)
            return
        target = select if select in self.shown else (self.shown[0] if self.shown else None)
        if target is not None:
            self._select_row(target)
            self.show_pair(target, keep_sides=(target == self.selected))
        else:
            self.show_pair(None)

    def _draw_window(self, index):
        """List the shown pairs from a little before `index`, LIST_WINDOW of them."""
        self.table.delete(*self.table.get_children())
        start = max(0, min(index - 50, len(self.shown) - LIST_WINDOW))
        self._window = (start, start + LIST_WINDOW)
        for p in self._shown_pairs[start:start + LIST_WINDOW]:
            r = self._route(p)
            tag = {RT.CONFLICT: "conflict", RT.NEEDS_REVALIDATION: "revalidate", RT.BLOCKED: "blocked",
                   RT.DEFERRED: "deferred", RT.READY_FOR_PLAN: "resolved", RT.RESOLVED: "resolved"}.get(r.primary, "")
            a, b = self.ev.locations.get(p.a_id), self.ev.locations.get(p.b_id)
            measure = "" if p.distance is None else f"{p.distance}"
            self.table.insert("", "end", iid=p.pair_id, tags=(tag,), values=[
                CHECKED if p.pair_id in self.checked else UNCHECKED,
                r.word, measure, _file_name(a.path) if a else p.a_id, _file_name(b.path) if b else p.b_id])
        self._update_checked()

    # -- the checked set and the bulk path (Build 6) -------------------------------

    def _table_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell" and self.table.identify_column(event.x) == "#1":
            row = self.table.identify_row(event.y)
            if row:
                self._toggle(row)
                return "break"
        return None

    def toggle_check(self):
        """Space: check or uncheck the pair in focus."""
        row = self.table.focus() or (self.table.selection()[0] if self.table.selection() else None)
        if row:
            self._toggle(row)

    def _toggle(self, pair_id):
        if pair_id in self.checked:
            self.checked.discard(pair_id)
        else:
            self.checked.add(pair_id)
        if self.table.exists(pair_id):
            self.table.set(pair_id, "check", CHECKED if pair_id in self.checked else UNCHECKED)
        self._update_checked()

    def check_all_shown(self):
        """Every pair the Show list holds -- all of it, not just the rows drawn."""
        self.checked |= set(getattr(self, "shown", []))
        self._redraw_checks()

    def clear_checks(self):
        self.checked.clear()
        self._redraw_checks()

    def _redraw_checks(self):
        for row in self.table.get_children():
            self.table.set(row, "check", CHECKED if row in self.checked else UNCHECKED)
        self._update_checked()

    def _update_checked(self):
        n = len(self.checked)
        shown = set(getattr(self, "shown", []))
        self.checked_var.set("Nothing checked" if not n else
                             f"{n:,} checked" + (f" ({len(self.checked & shown):,} shown)" if (self.checked - shown) else ""))

    def bulk_action(self):
        """One decision over many pairs, after a preview (Build 6)."""
        if self.filter_var.get() in BK.NO_PAIR_BULK and not self.checked:
            messagebox.showinfo("Bulk action", "Exact copies are answered by the Duplicate decisions page: no relationship "
                                "batch is offered over them.", parent=self.app)
            return
        self.bulk_dialog = PairBulkDialog(self.app, self, self.store, self._bulk_done)

    def _bulk_done(self):
        self.checked.clear()
        self.undo_stack.clear()                  # a batch is undone from Batches..., as a whole
        self.refresh_record(select=self.selected)

    def open_batches(self):
        self.batches_dialog = BatchesDialog(self.app, self.store, lambda: self.refresh_record(select=self.selected))

    def _select_row(self, pair_id):
        """Select a shown pair's row, bringing it into the listed window first."""
        if pair_id not in self.shown:
            return
        if not self.table.exists(pair_id):
            self._draw_window(self.shown.index(pair_id))
        self.table.selection_set(pair_id)
        self.table.see(pair_id)

    def _pair_selected(self):
        sel = self.table.selection()
        if sel and (sel[0] != self.selected or self.compare_only):
            self.compare_only = False
            self.show_pair(sel[0])

    # -- showing a pair ----------------------------------------------------------

    def show_pair(self, pair_id, keep_sides=False):
        p = self.proj.pair(pair_id) if pair_id is not None else None
        if p is None:
            self.selected = None
            self.left_id = self.right_id = None
            self._show_current()
            return
        if not (keep_sides and self.selected == pair_id and {self.left_id, self.right_id} == set(p.members)):
            self.left_id, self.right_id = p.a_id, p.b_id
            self._load_sides()
        self.selected = pair_id
        self._show_current()

    def open_compare(self, left_id, right_id):
        """Two images, on request (the voluntary exact-duplicate comparison).
        If they are a candidate pair, that pair is shown; otherwise they are
        compared with no pair decision on offer."""
        left_id, right_id = str(left_id), str(right_id)
        for p in self.proj.pairs:
            if set(p.members) == {left_id, right_id}:
                self.compare_only = False
                if p.pair_id not in self.shown:
                    self.filter_var.set(LENS_EXACT if p.exact_content else LENS_ALL)
                    self.refresh_list(select=p.pair_id)
                self._select_row(p.pair_id)
                self.selected = p.pair_id
                self.left_id, self.right_id = left_id, right_id
                self._load_sides()
                self._show_current()
                return
        self.compare_only = True
        self.selected = None
        self.table.selection_remove(*self.table.selection())
        self.left_id, self.right_id = left_id, right_id
        self._load_sides()
        self._show_current()

    def _load_sides(self):
        self.facts = {}
        for side, lid in ((0, self.left_id), (1, self.right_id)):
            facts = V.location_facts(self.app.conn, lid) if lid is not None else None
            self.facts[lid] = facts
            self.views[side].load(facts)
            self.views[side].fitted = True
        self.app.update_idletasks()
        for v in self.views:
            v.fit()
            v.render()

    def _group_of_pair(self):
        """The exact-duplicate group both images belong to, if they do."""
        a, b = self.ev.locations.get(str(self.left_id)), self.ev.locations.get(str(self.right_id))
        if a is None or b is None or not a.in_current_group or a.content_id is None or a.content_id != b.content_id:
            return None
        return self.proj.group(a.content_id)

    def _show_current(self):
        p = self.proj.pair(self.selected) if self.selected is not None else None
        self.notice.pack_forget()
        self.history.delete(*self.history.get_children())
        if p is None and not self.compare_only:
            self.title.configure(text="Select a pair." if self.proj.pairs else "No candidate pairs.")
            self.measure_label.configure(text="")
            for side in (0, 1):
                self.views[side].clear("")
                self.evidence[side].configure(text="")
                self.captions[side].configure(text="Left" if side == 0 else "Right")
            self._set_buttons(None)
            return
        group = self._group_of_pair()
        if p is not None:
            route = self._route(p)
            pair = self.ev.pairs.get(p.pair_id)
            rel = RELATIONSHIP_WORDS.get(p.relationship, "No decision yet")
            self.title.configure(text=f"Pair #{p.pair_id} -- {route.word} -- {rel}")
            self.measure_label.configure(text=V.describe(p.method, p.distance, pair.bits if pair else None,
                                                         (pair.detail or {}).get("ahash_distance") if pair else None,
                                                         (pair.detail or {}).get("dhash_distance") if pair else None,
                                                         pair.score if pair else None)
                                         + (" · the two images are byte-identical (exact duplicates)" if p.exact_content else ""))
            lines = []
            for c in route.conditions:
                if c.kind in ("ready", "kept", "in_progress", "unreviewed", RT.EXACT_CONTENT):
                    continue
                lines.append(f"{'Conflict' if c.route == RT.CONFLICT else RT.STATUS_WORDS.get(c.route, 'Note')}: {c.detail}")
            if route.deferral is not None and route.deferral_fired:
                lines.append(f"Returned to the queue: {route.deferral_fired_why}.")
            lines.extend(f"Note: {n}" for n in p.notes)
            if p.exact_content:
                lines.append("These two are exact duplicates: the Duplicate decisions page already answers them. Choose the "
                             "group's canonical by eye here with [ or ] if you like -- it is recorded on the exact-duplicate group.")
            if lines:
                self.notice.configure(text="\n".join(lines))
                self.notice.pack(fill="x", pady=(0, 4), before=self.view_bar)
            self._fill_history(p)
        else:
            self.title.configure(text="Compared on request -- not a candidate pair")
            self.measure_label.configure(text="These two images were opened by hand; the hashes did not pair them (or they are "
                                              "not both analyzed images). No pair decision is on offer.")
            if group is not None:
                self.notice.configure(text="Both are copies in one exact-duplicate group: [ or ] sets the group's canonical.")
                self.notice.pack(fill="x", pady=(0, 4), before=self.view_bar)
        for side, lid in ((0, self.left_id), (1, self.right_id)):
            self.captions[side].configure(text=("Left" if side == 0 else "Right") + self._role(p, lid, group))
            self.evidence[side].configure(text=self._evidence_text(lid))
        self._set_buttons(p)

    def _role(self, p, lid, group):
        if p is not None and not p.exact_content:
            if p.redundant_candidate == lid:
                return " -- redundant candidate"
            if p.canonical == lid:
                return " -- canonical (preferred)"
            if lid in p.keeper_set:
                return " -- kept"
            return ""
        if group is not None:
            v = group.verdict(str(lid))
            if group.canonical == str(lid):
                return " -- the group's canonical"
            if v is not None and v.status == R.REDUNDANT:
                return " -- redundant candidate (exact group)"
        return ""

    def _evidence_text(self, lid):
        f = self.facts.get(lid)
        if f is None:
            return "Not in the evidence."
        view = self.views[0] if lid == self.left_id else self.views[1]
        w, h = f.get("width_px"), f.get("height_px")
        if (w is None or h is None) and view.image is not None:
            w, h = view.image.size
        bits = [f"{w:,} x {h:,} px" if w and h else "resolution unknown", f.get("format") or "format unknown",
                human_bytes(f.get("size_bytes"))]
        modified = (f.get("modified_utc") or "")[:19].replace("T", " ")
        lines = [_file_name(f["path"]), _short(_folder(f["path"])), " · ".join(bits),
                 f"modified {modified} UTC" if modified else "modified time unknown",
                 f"capture date {view.capture} (as the file records it)" if view.capture else "no capture date recorded in the file"]
        state = f.get("state")
        if state != "present" or f.get("is_offline_or_cloud"):
            lines.append(f"state: {state}" + (", cloud-only" if f.get("is_offline_or_cloud") else ""))
        return "\n".join(lines)

    def _fill_history(self, p):
        rows = []
        for d in self.store.history_for(VISUAL_PAIR, p.pair_id):
            spec = DECISION_KINDS.get(d["decision_kind"])
            what = spec.label if spec else d["decision_kind"]
            if d["decision_kind"] == "canonical_location":
                loc = self.ev.locations.get(str(d["value"]))
                what += f": {_file_name(loc.path) if loc else d['value']}"
            state = "active" if d["active"] else "withdrawn" if d["withdrawn"] else "superseded"
            rows.append((d["occurred_utc"], 0, int(d["decision_id"]), what, state, d["active"]))
        for e in self.store.review_events_for(VISUAL_PAIR, p.pair_id):
            what = {"deferred": "Deferred", "skipped": "Skipped", "restored": "Restored", "needs_revalidation": "Detector: evidence changed",
                    "blocked": "Detector: blocked"}.get(e["event_kind"], e["event_kind"])
            rows.append((e["occurred_utc"], 1, int(e["review_event_id"]), what, "event", False))
        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        for when, _k, _i, what, state, active in rows:
            self.history.insert("", "end", values=[when[:19].replace("T", " "), what, state], tags=(() if active else ("inactive",)))

    def _set_buttons(self, p):
        decidable = p is not None and not p.exact_content and not self.compare_only
        for key in ("equivalent_left", "equivalent_right", "equivalent_both", "related_both", "different"):
            self.buttons[key].configure(state="normal" if decidable else "disabled")
        group = self._group_of_pair() if (self.left_id is not None and self.ev is not None) else None
        canon = decidable or group is not None
        self.buttons["canonical_left"].configure(state="normal" if canon else "disabled")
        self.buttons["canonical_right"].configure(state="normal" if canon else "disabled")
        parked = p is not None and self._route(p).has(RT.DEFERRED)
        self.buttons["defer"].configure(state="normal" if p is not None else "disabled",
                                        text="Restore (W)" if parked else "Defer... (W)")
        self.buttons["skip"].configure(state="normal" if p is not None else "disabled")
        self.buttons["undo"].configure(state="normal" if (self.undo_stack or (p is not None and p.decision_ids)) else "disabled")
        self.buttons["open_group"].configure(state="normal" if group is not None else "disabled")

    # -- the view (linked by default) ---------------------------------------------

    def _targets(self, side=None):
        if self.linked.get():
            return self.views
        return [self.views[self.focus_side if side is None else side]]

    def _redraw(self):
        for v in self.views:
            v.render()

    def fit(self):
        for v in self._targets():
            v.fit()
        self._redraw()

    def actual(self):
        for v in self._targets():
            v.actual()
        self._redraw()

    def zoom_in(self):
        for v in self._targets():
            v.zoom_at(1.5)
        self._redraw()

    def zoom_out(self):
        for v in self._targets():
            v.zoom_at(1 / 1.5)
        self._redraw()

    def toggle_link(self):
        self.linked.set(not self.linked.get())
        if self.linked.get():
            self.resync()
        self.status_var.set("Zoom and pan are linked." if self.linked.get() else
                            "Zoom and pan are unlinked: each side moves on its own (Y re-syncs).")

    def resync(self):
        """The right side takes the left side's view: the same region, in
        each image's own coordinates (so a resized copy lines up)."""
        left, right = self.views
        right.set_relative(left.relative_zoom(), left.cx, left.cy)
        right.fitted = left.fitted
        self._redraw()

    def swap(self):
        """Presentation only: nothing recorded changes."""
        if self.left_id is None:
            return
        self.left_id, self.right_id = self.right_id, self.left_id
        self.views[0], self.views[1] = self.views[1], self.views[0]
        for side, v in enumerate(self.views):
            v.canvas = self.canvases[side]
            self.canvases[side].bind("<Configure>", lambda e, vv=v: vv.render())
        self.app.update_idletasks()
        for v in self.views:
            if v.fitted:
                v.fit()
        self._redraw()
        self._show_current()

    def _wheel(self, side, event):
        self.focus_side = side
        factor = 1.25 if event.delta > 0 else 1 / 1.25
        for v in self._targets(side):
            v.zoom_at(factor, event.x, event.y)
        self._redraw()
        return "break"

    def _press(self, side, event):
        self.focus_side = side
        self._last = (event.x, event.y)
        self.canvases[side].focus_set()

    def _drag(self, side, event):
        lx, ly = getattr(self, "_last", (event.x, event.y))
        dx, dy = event.x - lx, event.y - ly
        self._last = (event.x, event.y)
        if not self.linked.get():
            self.views[side].pan(dx, dy)
            self.views[side].render()
            return
        # Linked: the same region of each image moves together -- the
        # dragged side's displacement, in each image's own coordinates.
        src = self.views[side]
        if src.image is None:
            return
        ncx = -dx / src.scale / src.size[0]
        ncy = -dy / src.scale / src.size[1]
        for v in self.views:
            if v.image is not None:
                v.fitted = False
                v.cx, v.cy = v.cx + ncx, v.cy + ncy
                v._clamp()
        self._redraw()

    # -- navigation -----------------------------------------------------------------

    def _step(self, delta):
        if not self.shown:
            return
        if self.selected in self.shown:
            i = self.shown.index(self.selected) + delta
        else:
            i = 0 if delta > 0 else len(self.shown) - 1
        i = max(0, min(i, len(self.shown) - 1))
        target = self.shown[i]
        self.compare_only = False
        self._select_row(target)
        self.show_pair(target)
        self.table.focus(target)

    def next_pair(self):
        self._step(1)

    def previous_pair(self):
        self._step(-1)

    # -- decisions ------------------------------------------------------------------

    def _current(self):
        if self.compare_only or self.selected is None:
            return None
        return self.proj.pair(self.selected)

    def _after(self, pair_id, advance):
        """Re-derive; then, after a decision, the next pair in the list the
        person was working through (the one that followed this pair)."""
        before = list(getattr(self, "shown", []))
        nxt = None
        if advance and pair_id in before:
            i = before.index(pair_id)
            rest = before[i + 1:] + before[:i]
            nxt = rest[0] if rest else None
        self.refresh_record(select=pair_id)
        if advance and nxt is not None and nxt in self.shown and nxt != pair_id:
            self.compare_only = False
            self._select_row(nxt)
            self.show_pair(nxt)
        self.table.focus_set()                  # a decision changes no evidence: the strip stays as it is

    def _record(self, label, decisions, withdraw=()):
        p = self._current()
        if p is None:
            return False
        withdraw = [w for w in withdraw if w]
        try:
            if decisions:
                result = self.store.record_decisions(decisions, withdraw=withdraw, command_id=new_command_id())
                recorded = result["decision_ids"]
            elif withdraw:
                self.store.withdraw_many(withdraw, reason=f"{label}: retention does not apply", command_id=new_command_id())
                recorded = []
            else:
                self.status_var.set(f"Pair #{p.pair_id} already says that ({label}).")
                self._after(p.pair_id, advance=True)
                return True
        except ValueError as exc:
            messagebox.showwarning(label, str(exc), parent=self.app)
            return False
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror(label, f"Nothing was recorded.\n\n{exc}", parent=self.app)
            return False
        if recorded:
            self.undo_stack.append((p.pair_id, recorded, label))
        self.status_var.set(f"Pair #{p.pair_id}: {label} -- recorded ({len(recorded)} decision{'s' if len(recorded) != 1 else ''}). U undoes it.")
        self._after(p.pair_id, advance=True)
        return True

    def _fast(self, label, relationship, retention):
        """A fast action: the relationship row and, where it applies, the
        retention row -- two decisions, one operation. On a pair whose
        evidence moved, every row is recorded afresh (bound to the evidence
        as it is now); otherwise a row that already says the same is not
        repeated."""
        p = self._current()
        if p is None or p.exact_content:
            return
        rebind = self._route(p).has(RT.NEEDS_REVALIDATION)
        pid = p.pair_id
        decisions, withdraw = [], []
        if rebind or p.relationship != relationship:
            decisions.append((relationship, VISUAL_PAIR, pid, True))
        if retention == "keep_both":
            if rebind or not p.keep_both:
                decisions.append(("keep_both", VISUAL_PAIR, pid, [p.a_id, p.b_id]))
        elif retention in ("left", "right"):
            lid = self.left_id if retention == "left" else self.right_id
            if rebind or p.canonical != lid:
                decisions.append(("canonical_location", VISUAL_PAIR, pid, lid))
            if p.keep_both:
                withdraw.append(p.keep_both_decision_id)
        elif relationship == DIFFERENT:
            withdraw += [p.canonical_decision_id, p.keep_both_decision_id]
        self._record(label, decisions, withdraw)

    def equivalent_left(self):
        self._fast("Equivalent -> Prefer Left", EQUIVALENT, "left")

    def equivalent_right(self):
        self._fast("Equivalent -> Prefer Right", EQUIVALENT, "right")

    def equivalent_both(self):
        self._fast("Equivalent -> Keep Both", EQUIVALENT, "keep_both")

    def related_both(self):
        self._fast("Related Variant -> Keep Both", RELATED, "keep_both")

    def different(self):
        self._fast("Different", DIFFERENT, None)

    def _canonical(self, side):
        lid = self.left_id if side == "left" else self.right_id
        if lid is None:
            return
        p = self._current()
        word = "Left" if side == "left" else "Right"
        if p is not None and not p.exact_content:
            if p.canonical == lid:
                self.status_var.set(f"Pair #{p.pair_id}: the {word.lower()} image is already the canonical.")
                return
            self._record(f"Canonical: {word}", [("canonical_location", VISUAL_PAIR, p.pair_id, lid)])
            return
        # An exact-duplicate pair, or two images opened on request: the
        # canonical is the exact-duplicate GROUP's decision.
        group = self._group_of_pair()
        if group is None:
            return
        if group.canonical == str(lid):
            self.status_var.set(f"The {word.lower()} image is already the group's canonical.")
            return
        v = group.verdict(str(lid))
        marks = [int(d["decision_id"]) for d in self.store.history_for(LOCATION, str(lid))
                 if d["active"] and d["decision_kind"] == "redundant_location"]
        if v is not None and v.status == R.REDUNDANT and not messagebox.askyesno(
                "Canonical", "This copy is marked a redundant candidate. The canonical must be a keeper, so that mark "
                "will be withdrawn.\n\nContinue?", parent=self.app):
            return
        try:
            result = self.store.record_decision("canonical_location", GROUP, group.group_id, str(lid),
                                                command_id=new_command_id(), withdraw=marks)
        except ValueError as exc:
            messagebox.showwarning("Canonical", str(exc), parent=self.app)
            return
        self.undo_stack.append((None, [result["decision_id"]], f"Group canonical: {word}"))
        self.status_var.set(f"The {word.lower()} image is now the exact-duplicate group's canonical. U undoes it.")
        keep = (self.left_id, self.right_id, self.compare_only, self.selected)
        self.reload(select=self.selected)
        if keep[2]:
            self.compare_only = True
            self.left_id, self.right_id = keep[0], keep[1]
        self._show_current()

    def canonical_left(self):
        self._canonical("left")

    def canonical_right(self):
        self._canonical("right")

    def undo(self):
        """Withdraw what the last action recorded (one operation's rows); on a
        pair with no action this session, the pair's latest operation. A
        decision the action replaced is not revived."""
        entry = None
        while self.undo_stack and entry is None:
            pid, ids, label = self.undo_stack.pop()
            active = {d.decision_id for d in self.store.active()}
            ids = [i for i in ids if str(i) in active]
            if ids:
                entry = (pid, ids, label)
        if entry is None:
            p = self._current()
            if p is None or not p.decision_ids:
                self.status_var.set("Nothing to undo.")
                return
            active = [d for d in self.store.active() if d.target_kind == VISUAL_PAIR and d.target_ref == p.pair_id]
            if not active:
                self.status_var.set("Nothing to undo.")
                return
            last_op = max(int(d.operation_id) for d in active)
            entry = (p.pair_id, [int(d.decision_id) for d in active if int(d.operation_id) == last_op], "the latest action on this pair")
        pid, ids, label = entry
        try:
            self.store.withdraw_many(ids, reason=f"undo: {label}", command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Undo", str(exc), parent=self.app)
            return
        self.status_var.set(f"Undone: {label} ({len(ids)} decision{'s' if len(ids) != 1 else ''} withdrawn; "
                            "a decision it replaced is not revived).")
        if pid is not None:
            self.compare_only = False
            self.refresh_record(select=pid)
            self._select_row(pid)
            self.show_pair(pid, keep_sides=True)
        else:
            self.reload(select=self.selected)
        self.table.focus_set()

    def defer(self):
        """Defer with a return trigger -- or, on a parked pair, Restore."""
        p = self._current()
        if p is None:
            return
        route = self._route(p)
        if route.has(RT.DEFERRED):
            refers_to = route.deferral.review_event_id if route.deferral is not None else None
            try:
                self.store.restore(VISUAL_PAIR, p.pair_id, refers_to=refers_to, reason="restored by hand", command_id=new_command_id())
            except ValueError as exc:
                messagebox.showwarning("Restore", str(exc), parent=self.app)
                return
            self._after(p.pair_id, advance=False)
            return
        self.defer_dialog = DeferDialog(self.app, None, self.store, lambda: self._after(p.pair_id, advance=True),
                                        target_kind=VISUAL_PAIR, target_ref=p.pair_id)

    def skip(self):
        """Skip: an audit row, nothing else; on to the next pair."""
        p = self._current()
        if p is None:
            return
        try:
            self.store.skip(VISUAL_PAIR, p.pair_id, command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Skip", str(exc), parent=self.app)
            return
        self._step(1)

    def open_group(self):
        group = self._group_of_pair()
        if group is not None:
            self.app.show_decide(content_id=group.group_id)


# ---------------------------------------------------------------------------
# Bulk over pairs (Build 6): one decision, many pairs -- after a preview
# ---------------------------------------------------------------------------

class PairBulkDialog:
    """A relationship decision over many pairs, the research's frozen
    query-result batch: the checked pairs, the pairs the Show list holds now,
    or those of them with both images in one folder -- frozen at commit, a
    later match not included. Never a policy: nothing here applies itself to
    pairs found later. Each pair gets the same two rows a key records on it;
    what stands is preserved, conflicts and blocked pairs are left out and
    counted; nothing is recorded without a preview, and the commit records
    exactly what the preview showed."""

    def __init__(self, app, page, store, on_done):
        self.app, self.page, self.store, self.on_done = app, page, store, on_done
        self.preview = None
        self.previewed = None
        self.actions = bulk_actions_for([VISUAL_PAIR])
        win = tk.Toplevel(app)
        self.win = win
        win.title("Bulk action -- similar images")
        win.transient(app)
        win.grab_set()
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="One decision, many pairs -- after a preview", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Each pair in scope gets exactly what one key records on it. A pair you have already answered "
                              "differently, deferred, or that is in conflict or blocked is left alone and counted. The batch covers "
                              "the pairs listed now; a pair found later is not included -- this is never a standing rule. Nothing "
                              "here renames, moves or deletes a file.", wraplength=660, justify="left", foreground="#444").pack(anchor="w", pady=(2, 10))
        ttk.Label(frame, text="Action:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.action_var = tk.StringVar(value=self.actions[0].key)
        for a in self.actions:
            ttk.Radiobutton(frame, text=a.label, variable=self.action_var, value=a.key, command=self._inputs_changed).pack(anchor="w")
        self.desc = ttk.Label(frame, text="", wraplength=660, justify="left", foreground="#444")
        self.desc.pack(anchor="w", pady=(2, 8))

        ttk.Label(frame, text="Apply to:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.scope_var = tk.StringVar(value="checked" if page.checked else "folder")
        self.scope_buttons = {}
        for key in ("checked", "shown", "folder"):
            b = ttk.Radiobutton(frame, text="", variable=self.scope_var, value=key, command=self._inputs_changed)
            b.pack(anchor="w")
            self.scope_buttons[key] = b
        frow = ttk.Frame(frame)
        frow.pack(fill="x", padx=(22, 0), pady=(2, 0))
        ttk.Label(frow, text="Folder:").pack(side="left")
        self.folder_var = tk.StringVar(value=self._pair_folder() or "")
        ttk.Entry(frow, textvariable=self.folder_var, width=56).pack(side="left", padx=(6, 4))
        ttk.Button(frow, text="Browse...", command=self._browse).pack(side="left")
        ttk.Button(frow, text="This pair's folder", command=self._use_pair_folder).pack(side="left", padx=(4, 0))
        self.folder_var.trace_add("write", lambda *a: self._inputs_changed())
        self.sub_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Include subfolders (both images anywhere beneath the folder; off: both directly in it)",
                        variable=self.sub_var, command=self._inputs_changed).pack(anchor="w", padx=(22, 0))

        prow = ttk.Frame(frame)
        prow.pack(fill="x", pady=(10, 2))
        self.preview_button = ttk.Button(prow, text="Preview", command=self.run_preview)
        self.preview_button.pack(side="left")
        self.save_button = ttk.Button(prow, text="Save the preview list...", command=self.save_list, state="disabled")
        self.save_button.pack(side="left", padx=(8, 0))
        self.preview_text = tk.Text(frame, height=13, width=92, wrap="word", state="disabled", background="#f6f6f6", relief="flat")
        self.preview_text.pack(fill="both", expand=True)
        ttk.Label(frame, text="Note (optional):", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))
        self.note = tk.Text(frame, height=2, width=92, wrap="word")
        self.note.pack(fill="x")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(14, 0))
        self.ok = ttk.Button(row, text="Record", command=self._confirm, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="right", padx=(0, 8))
        self._inputs_changed()

    # -- state ------------------------------------------------------------------

    def _action(self):
        for a in self.actions:
            if a.key == self.action_var.get():
                return a
        return self.actions[0]

    def _pair_folder(self):
        lid = self.page.left_id
        loc = self.page.ev.locations.get(str(lid)) if (lid is not None and self.page.ev is not None) else None
        return _folder(loc.path) if loc else None

    def _use_pair_folder(self):
        folder = self._pair_folder()
        if folder:
            self.folder_var.set(folder)
            self.scope_var.set("folder")
            self._inputs_changed()

    def _browse(self):
        path = filedialog.askdirectory(parent=self.win, initialdir=self.folder_var.get() or None, title="Choose a folder")
        if path:
            self.folder_var.set(path.replace("/", "\\"))

    def _query(self):
        scope = self.scope_var.get()
        if scope == "folder":
            return BK.pair_snapshot_query(self.page.filter_var.get(), self.folder_var.get().strip(), bool(self.sub_var.get()))
        return BK.pair_snapshot_query(self.page.filter_var.get())

    def _key(self):
        return (self.action_var.get(), self.scope_var.get(), self.page.filter_var.get(), self.folder_var.get().strip(),
                bool(self.sub_var.get()), tuple(sorted(self.page.checked)))

    def _inputs_changed(self):
        self.desc.configure(text=self._action().description)
        page = self.page
        show = page.filter_var.get()
        n_checked = len(page.checked)
        n_shown = len(getattr(page, "shown", []))
        no_bulk = show in BK.NO_PAIR_BULK
        self.scope_buttons["checked"].configure(text=f"{n_checked:,} checked pair{'s' if n_checked != 1 else ''}",
                                                state="normal" if n_checked else "disabled")
        self.scope_buttons["shown"].configure(
            text=f"{n_shown:,} pair{'s' if n_shown != 1 else ''} listed now (Show: {show}) -- frozen at commit",
            state="normal" if n_shown and not no_bulk else "disabled")
        self.scope_buttons["folder"].configure(
            text=f"the pairs listed now (Show: {show}) with both images in this folder -- frozen at commit",
            state="normal" if n_shown and not no_bulk else "disabled")
        if self.scope_var.get() == "checked" and not n_checked:
            self.scope_var.set("folder")
        if self.previewed != self._key():
            self.preview, self.previewed = None, None
            self._show("Preview to see, pair by pair, what would be recorded, what already holds, what an explicit decision "
                       "keeps, what would conflict and what the evidence blocks -- with examples.")
            self.ok.configure(state="disabled", text="Record")
            self.save_button.configure(state="disabled")

    def _show(self, text):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    # -- preview, save, commit ---------------------------------------------------------

    def run_preview(self):
        page = self.page
        scope = self.scope_var.get()
        try:
            if scope == "folder" and not self.folder_var.get().strip():
                raise ValueError("choose the folder both images must be in")
            if scope == "checked":
                pv = BK.preview_pairs(page.ev, page.proj, page.routing, self._action().key, SCOPE_EXPLICIT_SELECTION,
                                      selection=sorted(page.checked), conn=self.app.conn)
            else:
                pv = BK.preview_pairs(page.ev, page.proj, page.routing, self._action().key, SCOPE_QUERY_SNAPSHOT,
                                      self._query(), conn=self.app.conn)
        except ValueError as exc:
            messagebox.showwarning("Bulk action", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Bulk action", f"The preview failed.\n\n{exc}", parent=self.win)
            return
        self.preview, self.previewed = pv, self._key()
        self._show("\n".join(pv.lines()))
        self.save_button.configure(state="normal" if pv.candidates else "disabled")
        n = pv.decision_count
        if n:
            self.ok.configure(state="normal", text=f"Record {n:,} decision{'s' if n != 1 else ''} "
                                                   f"on {pv.counts[DISP_DECIDED]:,} pair{'s' if pv.counts[DISP_DECIDED] != 1 else ''}")
        else:
            self.ok.configure(state="disabled", text="Nothing to record")

    def save_list(self):
        """Every pair in the preview with what would become of it and why, as CSV
        in the project's Exports folder (formula-safe cells)."""
        if self.preview is None:
            return
        exports = self.app.project_dir / "Exports"
        exports.mkdir(exist_ok=True)
        path = filedialog.asksaveasfilename(parent=self.win, title="Save the preview list", initialdir=str(exports),
                                            initialfile="BulkPreview.csv", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        self.saved_path = self.write_list(path)

    def write_list(self, path):
        from Phase2.gui import csv_cell
        with open(path, "w", encoding="utf-8-sig", newline="") as handle:
            w = csv.writer(handle)
            for row in BK.preview_table(self.preview):
                w.writerow([csv_cell(x) for x in row])
        return path

    def _confirm(self):
        if self.preview is None or self.previewed != self._key():
            self._inputs_changed()
            messagebox.showinfo("Bulk action", "Preview first; the batch records exactly what the preview shows.", parent=self.win)
            return
        note = self.note.get("1.0", "end").strip() or None
        try:
            result = self.store.commit_bulk(self.preview, note=note, command_id=new_command_id())
        except ValueError as exc:
            self.preview, self.previewed = None, None
            self.ok.configure(state="disabled", text="Record")
            messagebox.showwarning("Bulk action", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Bulk action", f"The batch was not recorded.\n\n{exc}", parent=self.win)
            return
        self.result = result
        self.win.destroy()
        c = self.preview.counts
        left = len(self.preview.candidates) - c[DISP_DECIDED]
        messagebox.showinfo("Bulk action",
                            f"Batch #{result['batch_id']} recorded: {result['decisions']:,} decision(s) on {c[DISP_DECIDED]:,} "
                            f"pair(s) of {result['members']:,}" + (f"; {left:,} left as they were (see Batches...)" if left else "")
                            + ".\n\nUndo the whole batch any time from Batches...", parent=self.app)
        self.on_done()
