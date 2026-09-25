r"""Survey Mode's window (Phase 3, Build 7), opened from the Similar Images page.

One cluster of 3-8 look-alikes at a time, tiled: one Active image (click a
tile), zoom and pan linked across every tile by default, and per image --
Keeper, Canonical, Select, Compare (the Active image against this one, in
the page's own Compare Mode) and Exclude (out of the view; nothing is
recorded). Under the tiles, the cluster's candidate pairs, and a line that
says how many image pairs are NOT candidates: nothing is recorded between
them.

The four group controls are Build 6 batches over the pairs in view
(`survey.preview_group` -> `store.commit_bulk`), so each one previews first,
leaves what stands alone, keeps conflicts and blocked pairs out, records
exactly what the preview showed, and is undone as a whole ("Undo the last
group action", or Batches... on the page). The cluster itself is never
recorded. Nothing here renames, moves or deletes a file.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from Phase2 import visual as V

from . import survey as SV
from .imagegame import ImageView, RELATIONSHIP_WORDS, _file_name, _folder, _short
from .registry import DEFER_CHOICES, DEFER_DISPOSITIONS, VISUAL_PAIR, DISP_DECIDED
from .store import new_command_id

COLUMNS = 4
TILE_W, TILE_H = 250, 190
ACTIVE_COLOUR = "#1c5fa8"


def open_survey(app, page):
    """Survey Mode over the page's Show list, starting at the pair in view's
    cluster when that is a 3-8 image cluster. Returns the window, or None
    (with the reason said) when the list holds no such cluster."""
    show = page.filter_var.get()
    cmap = SV.cluster_map(page.proj, page.routing, page.ev, show)
    survey = cmap.of_kind(SV.SURVEY)
    here = cmap.of_pair.get(page.selected) if page.selected is not None else None
    note = ""
    if here is not None and here.kind == SV.OVERSIZED:
        note = SV.oversized_text(here)
    elif here is not None and here.kind == SV.ISOLATED:
        note = "The pair in view is joined to no other open pair in this list: it is an isolated pair -- Compare Mode is its review."
    if not survey:
        messagebox.showinfo("Survey Mode", cmap.summary() + ("\n\n" + note if note else "")
                            + "\n\nNo cluster of 3-8 images to survey in this list.", parent=app)
        return None
    start = here if (here is not None and here.kind == SV.SURVEY) else survey[0]
    return SurveyWindow(app, page, show, cmap, start, note)


class SurveyWindow:
    def __init__(self, app, page, show, cmap, start, note=""):
        self.app, self.page, self.show, self.cmap = app, page, show, cmap
        self.store = page.store
        self.images, self.excluded = [], []
        self.keepers, self.selected = set(), set()
        self.canonical = None
        self.active = None
        self.views, self.tiles = {}, {}
        self.linked = tk.BooleanVar(value=True)
        self.preview = self.previewed = None
        self.control = None
        self.last_batch = None
        self.history = []                       # clusters shown, as frozensets of image ids (Previous)
        self.visited = set()
        self.adj = SV.adjacency(page.proj)

        win = tk.Toplevel(app)
        self.win = win
        win.title("Survey Mode -- similar images")
        win.transient(app)
        win.geometry("1180x900")
        outer = ttk.Frame(win, padding=10)
        outer.pack(fill="both", expand=True)
        self.title_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.title_var, font=("Segoe UI", 11, "bold"), wraplength=1140, justify="left").pack(anchor="w")
        self.summary_var = tk.StringVar(value=cmap.summary())
        ttk.Label(outer, textvariable=self.summary_var, foreground="#444", wraplength=1140, justify="left").pack(anchor="w", pady=(2, 0))
        ttk.Label(outer, text="The cluster is only a way of looking: each decision is about one candidate pair, and nothing "
                              "is inferred between two images that are not a candidate pair. Nothing here renames, moves or "
                              "deletes a file.", foreground="#666", wraplength=1140, justify="left").pack(anchor="w", pady=(2, 6))

        nav = ttk.Frame(outer)
        nav.pack(fill="x")
        ttk.Button(nav, text="< Previous cluster", command=self.previous_cluster).pack(side="left")
        ttk.Button(nav, text="Next cluster >", command=self.next_cluster).pack(side="left", padx=(4, 12))
        ttk.Button(nav, text="Fit", command=self.fit).pack(side="left")
        ttk.Button(nav, text="Zoom +", command=lambda: self.zoom(1.5)).pack(side="left", padx=(4, 0))
        ttk.Button(nav, text="Zoom -", command=lambda: self.zoom(1 / 1.5)).pack(side="left", padx=(4, 0))
        ttk.Checkbutton(nav, text="Link zoom and pan", variable=self.linked).pack(side="left", padx=(8, 0))
        self.restore_button = ttk.Button(nav, text="Restore excluded", command=self.restore_excluded, state="disabled")
        self.restore_button.pack(side="left", padx=(12, 0))

        self.grid = ttk.Frame(outer)
        self.grid.pack(fill="both", expand=True, pady=(6, 4))

        lower = ttk.Frame(outer)
        lower.pack(fill="x")
        left = ttk.Frame(lower)
        left.pack(side="left", fill="both", expand=True)
        self.edges_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.edges_var, font=("Segoe UI", 9, "bold"), wraplength=560, justify="left").pack(anchor="w")
        self.pairs = ttk.Treeview(left, columns=("pair", "images", "bits", "status", "relationship"), show="headings",
                                  height=6, selectmode="browse")
        for key, heading, width in (("pair", "Pair", 60), ("images", "Images", 250), ("bits", "Bits", 40),
                                    ("status", "Status", 110), ("relationship", "Relationship", 120)):
            self.pairs.heading(key, text=heading)
            self.pairs.column(key, width=width, stretch=key == "images", anchor="w")
        self.pairs.pack(fill="x", pady=(2, 0))
        self.pairs.bind("<Double-1>", lambda e: self.compare_pair())
        ttk.Button(left, text="Compare this pair", command=self.compare_pair).pack(anchor="w", pady=(4, 0))

        right = ttk.Frame(lower, padding=(12, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Group controls -- each previews first, then records pair by pair", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        row = ttk.Frame(right)
        row.pack(fill="x", pady=(2, 0))
        self.control_buttons = {}
        for key, label in SV.CONTROLS.items():
            b = ttk.Button(row, text=label + "...", command=lambda k=key: self.run_preview(k))
            b.pack(side="left", padx=(0, 4))
            self.control_buttons[key] = b
        drow = ttk.Frame(right)
        drow.pack(fill="x", pady=(4, 0))
        ttk.Label(drow, text="Defer Group returns:").pack(side="left")
        self.defer_choices = {DEFER_DISPOSITIONS[d][1]: d for d in DEFER_CHOICES[VISUAL_PAIR]}
        self.defer_var = tk.StringVar(value=next(iter(self.defer_choices)))
        ttk.Combobox(drow, textvariable=self.defer_var, values=list(self.defer_choices), state="readonly", width=34).pack(side="left", padx=(4, 4))
        ttk.Label(drow, text="date (YYYY-MM-DD):").pack(side="left")
        self.date_var = tk.StringVar(value="")
        ttk.Entry(drow, textvariable=self.date_var, width=12).pack(side="left", padx=(4, 0))
        for var in (self.defer_var, self.date_var):
            var.trace_add("write", lambda *a: self._void_preview())
        self.preview_text = tk.Text(right, height=9, width=70, wrap="word", state="disabled", background="#f6f6f6", relief="flat")
        self.preview_text.pack(fill="both", expand=True, pady=(4, 0))
        brow = ttk.Frame(right)
        brow.pack(fill="x", pady=(4, 0))
        self.ok = ttk.Button(brow, text="Record", command=self.record, state="disabled")
        self.ok.pack(side="right")
        self.undo_button = ttk.Button(brow, text="Undo the last group action", command=self.undo_last, state="disabled")
        self.undo_button.pack(side="right", padx=(0, 8))
        ttk.Button(brow, text="Batches...", command=self.page.open_batches).pack(side="left")
        self.status_var = tk.StringVar(value=note)
        ttk.Label(outer, textvariable=self.status_var, foreground="#1f6244", wraplength=1140, justify="left").pack(anchor="w", pady=(6, 0))
        self.show_cluster(start)

    # -- the cluster ------------------------------------------------------------------

    def show_cluster(self, cluster, remember=True):
        key = frozenset(cluster.images)
        if remember and (not self.history or self.history[-1] != key):
            self.history.append(key)
        self.visited.add(key)
        self.cluster = cluster
        self.images = list(cluster.images)
        self.excluded = []
        self.keepers, self.selected = set(), set()
        self.canonical = None
        self.active = self.images[0] if self.images else None
        self._build_tiles()
        self._refresh()

    def _survey_list(self):
        return self.cmap.of_kind(SV.SURVEY)

    def next_cluster(self):
        """The next 3-8 image cluster not yet shown in this window (the list is
        re-derived after every group action: a cluster answered is gone)."""
        for c in self._survey_list():
            if frozenset(c.images) not in self.visited:
                self.show_cluster(c)
                return
        self.status_var.set("No other cluster of 3-8 images is waiting in this list.")

    def previous_cluster(self):
        if len(self.history) < 2:
            self.status_var.set("This is the first cluster shown.")
            return
        self.history.pop()
        want = self.history[-1]
        imgs = sorted(want, key=SV._id_order)
        found = next((c for c in self.cmap.clusters if frozenset(c.images) == want), None)
        self.show_cluster(found or SV.Cluster(0, tuple(imgs), ()), remember=False)

    def _remap(self):
        self.cmap = SV.cluster_map(self.page.proj, self.page.routing, self.page.ev, self.show)
        self.adj = SV.adjacency(self.page.proj)
        self.summary_var.set(self.cmap.summary())

    # -- tiles --------------------------------------------------------------------------

    def _build_tiles(self):
        for child in self.grid.winfo_children():
            child.destroy()
        self.views, self.tiles = {}, {}
        for col in range(COLUMNS):
            self.grid.columnconfigure(col, weight=1, uniform="tile")
        for i, lid in enumerate(self.images):
            box = ttk.Frame(self.grid, padding=3)
            box.grid(row=i // COLUMNS, column=i % COLUMNS, sticky="nsew")
            self.grid.rowconfigure(i // COLUMNS, weight=1)
            canvas = tk.Canvas(box, background="#2b2b2b", width=TILE_W, height=TILE_H, highlightthickness=3,
                               highlightbackground="#999")
            canvas.pack(fill="both", expand=True)
            view = ImageView(canvas)
            facts = V.location_facts(self.app.conn, lid)
            view.load(facts)
            canvas.bind("<Configure>", lambda e, v=view: v.render())
            canvas.bind("<ButtonPress-1>", lambda e, l=lid: self._press(l, e))
            canvas.bind("<B1-Motion>", lambda e, l=lid: self._drag(l, e))
            canvas.bind("<MouseWheel>", lambda e, l=lid: self._wheel(l, e))
            loc = self.page.ev.locations.get(lid)
            w, h = (facts or {}).get("width_px"), (facts or {}).get("height_px")
            if (not w or not h) and view.image is not None:
                w, h = view.image.size
            caption = ttk.Label(box, text="", justify="left", wraplength=TILE_W)
            caption.pack(anchor="w")
            text = [_file_name(loc.path) if loc else lid, _short(_folder(loc.path), 40) if loc else "",
                    f"{w:,} x {h:,} px" if w and h else "resolution unknown"]
            caption.configure(text="\n".join(text))
            controls = ttk.Frame(box)
            controls.pack(anchor="w")
            kv = tk.BooleanVar(value=lid in self.keepers)
            cv = tk.BooleanVar(value=lid == self.canonical)
            sv = tk.BooleanVar(value=lid in self.selected)
            ttk.Checkbutton(controls, text="Keeper", variable=kv, command=lambda l=lid: self.toggle_keeper(l)).pack(side="left")
            ttk.Checkbutton(controls, text="Canonical", variable=cv, command=lambda l=lid: self.toggle_canonical(l)).pack(side="left")
            ttk.Checkbutton(controls, text="Select", variable=sv, command=lambda l=lid: self.toggle_selected(l)).pack(side="left")
            buttons = ttk.Frame(box)                    # a row of its own: never cut off by a narrow tile
            buttons.pack(anchor="w", pady=(2, 0))
            cmp_b = ttk.Button(buttons, text="Compare with Active", command=lambda l=lid: self.compare_with_active(l))
            cmp_b.pack(side="left")
            ttk.Button(buttons, text="Exclude from view", command=lambda l=lid: self.exclude(l)).pack(side="left", padx=(4, 0))
            role = ttk.Label(box, text="", foreground=ACTIVE_COLOUR)
            role.pack(anchor="w")
            self.views[lid] = view
            self.tiles[lid] = {"canvas": canvas, "keeper": kv, "canonical": cv, "selected": sv, "compare": cmp_b, "role": role}
        self.app.update_idletasks()
        for v in self.views.values():
            v.fit()
            v.render()

    def _refresh(self):
        """Titles, roles, the pair list -- after any change of view or record."""
        edges = SV.cluster_edges(self.page.proj, self.images, self.adj)
        self.edges = edges
        gaps = SV.non_edges(self.images, edges, self.page.proj)
        n = self.cluster.number
        where = f"Cluster {n:,} of the list" if n else "A cluster shown earlier"
        self.title_var.set(f"{where} (Show: {self.show}) -- {len(self.images)} image{'s' if len(self.images) != 1 else ''} in view"
                           + (f", {len(self.excluded)} excluded from the view" if self.excluded else "")
                           + f", across {len({_folder(self.page.ev.locations[i].path).lower() for i in self.images if i in self.page.ev.locations})} folders")
        self.edges_var.set(f"{len(edges)} candidate pair{'s' if len(edges) != 1 else ''} among them"
                           + (f"; {len(gaps)} image pair{'s are' if len(gaps) != 1 else ' is'} not a candidate pair -- nothing is "
                              "recorded between them" if gaps else ""))
        self.pairs.delete(*self.pairs.get_children())
        for p in edges:
            r = self.page.routing.pairs.get(p.pair_id)
            a, b = self.page.ev.locations.get(p.a_id), self.page.ev.locations.get(p.b_id)
            self.pairs.insert("", "end", iid=p.pair_id, values=[
                f"#{p.pair_id}", f"{_file_name(a.path) if a else p.a_id} ~ {_file_name(b.path) if b else p.b_id}",
                "" if p.distance is None else p.distance, r.word if r else "", RELATIONSHIP_WORDS.get(p.relationship, "--")])
        for lid, t in self.tiles.items():
            words = []
            if lid == self.active:
                words.append("Active")
            if lid == self.canonical:
                words.append("Canonical")
            elif lid in self.keepers:
                words.append("Keeper")
            if lid in self.selected:
                words.append("Selected")
            t["role"].configure(text=" · ".join(words))
            t["canvas"].configure(highlightbackground=ACTIVE_COLOUR if lid == self.active else "#999")
            t["keeper"].set(lid in self.keepers or lid == self.canonical)
            t["canonical"].set(lid == self.canonical)
            t["selected"].set(lid in self.selected)
            t["compare"].configure(state="disabled" if lid == self.active else "normal")
        self.restore_button.configure(state="normal" if self.excluded else "disabled")
        self.undo_button.configure(state="normal" if self.last_batch else "disabled")
        self._void_preview()

    # -- per-image controls (view state only: nothing is recorded) -----------------------

    def set_active(self, lid):
        if lid in self.images:
            self.active = lid
            self._refresh()

    def toggle_keeper(self, lid):
        if lid == self.canonical:
            self.status_var.set("The Canonical is always a Keeper.")
        elif lid in self.keepers:
            self.keepers.discard(lid)
        else:
            self.keepers.add(lid)
        self._refresh()

    def toggle_canonical(self, lid):
        self.canonical = None if self.canonical == lid else lid
        self._refresh()

    def toggle_selected(self, lid):
        if lid in self.selected:
            self.selected.discard(lid)
        else:
            self.selected.add(lid)
        self._refresh()

    def exclude(self, lid):
        """Out of the view, and so out of every group control's pairs. Records
        nothing: taking an image off the screen is not a judgment about the
        pairs it was in."""
        if lid not in self.images:
            return
        self.images.remove(lid)
        self.excluded.append(lid)
        self.keepers.discard(lid)
        self.selected.discard(lid)
        if self.canonical == lid:
            self.canonical = None
        if self.active == lid:
            self.active = self.images[0] if self.images else None
        self._build_tiles()
        self._refresh()
        self.status_var.set(f"{_file_name(self.page.ev.locations[lid].path) if lid in self.page.ev.locations else lid} is out of "
                            "the view -- nothing was recorded about it.")

    def restore_excluded(self):
        order = {lid: i for i, lid in enumerate(self.cluster.images)}
        self.images = sorted(self.images + self.excluded, key=lambda l: order.get(l, 10 ** 9))
        self.excluded = []
        self._build_tiles()
        self._refresh()

    def compare_with_active(self, lid):
        """The Active image and this one, in the page's Compare Mode: their
        candidate pair if they are one; otherwise the two images side by side
        with no pair decision on offer."""
        if self.active is None or lid == self.active:
            return
        self.page.open_compare(self.active, lid)
        self.app.lift()

    def compare_pair(self):
        sel = self.pairs.selection()
        if not sel:
            return
        p = self.page.proj.pair(sel[0])
        if p is not None:
            self.page.open_compare(p.a_id, p.b_id)
            self.app.lift()

    # -- linked zoom -----------------------------------------------------------------------

    def _others(self, lid):
        return [v for l, v in self.views.items() if l != lid and v.image is not None] if self.linked.get() else []

    def _follow(self, lid):
        src = self.views[lid]
        for v in self._others(lid):
            v.set_relative(src.relative_zoom(), src.cx, src.cy)
            v.fitted = src.fitted
        for v in self.views.values():
            v.render()

    def _press(self, lid, event):
        self._last = (event.x, event.y)
        self.set_active(lid)

    def _drag(self, lid, event):
        lx, ly = getattr(self, "_last", (event.x, event.y))
        self._last = (event.x, event.y)
        self.views[lid].pan(event.x - lx, event.y - ly)
        self._follow(lid)

    def _wheel(self, lid, event):
        self.views[lid].zoom_at(1.25 if event.delta > 0 else 1 / 1.25, event.x, event.y)
        self._follow(lid)
        return "break"

    def zoom(self, factor):
        lid = self.active if self.active in self.views else next(iter(self.views), None)
        if lid is None:
            return
        targets = self.views.values() if self.linked.get() else [self.views[lid]]
        for v in targets:
            v.zoom_at(factor)
        for v in self.views.values():
            v.render()

    def fit(self):
        for v in self.views.values():
            v.fit()
            v.render()

    # -- group controls: a Build 6 batch over the pairs in view ------------------------

    def _key(self, control):
        return (control, tuple(self.images), tuple(sorted(self.keepers)), self.canonical, tuple(sorted(self.selected)),
                self.defer_var.get(), self.date_var.get().strip())

    def _void_preview(self):
        """A preview stands only for the inputs it was made from: the images in
        view, the Keepers, the Canonical, the selection, the deferral."""
        if self.preview is not None and self.previewed == self._key(self.control):
            return
        if self.preview is not None:
            self._show("The view changed: preview again.")
        self.preview = self.previewed = None
        self.ok.configure(state="disabled", text="Record")

    def _show(self, text):
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text)
        self.preview_text.configure(state="disabled")

    def run_preview(self, control):
        page = self.page
        disposition = self.defer_choices.get(self.defer_var.get(), "defer_indefinitely")
        try:
            pv = SV.preview_group(page.ev, page.proj, page.routing, control, self.images, self.show,
                                  keepers=self.keepers, canonical=self.canonical, selected=self.selected,
                                  excluded=self.excluded, disposition=disposition, until=self.date_var.get().strip() or None,
                                  conn=self.app.conn, adj=self.adj)
        except ValueError as exc:
            self.preview = self.previewed = None
            self.ok.configure(state="disabled", text="Record")
            self._show(f"{SV.CONTROLS[control]}: {exc}")
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Survey Mode", f"The preview failed.\n\n{exc}", parent=self.win)
            return
        self.preview, self.previewed, self.control = pv, self._key(control), control
        lines = [SV.CONTROLS[control]] + pv.lines()
        if control == SV.SELECTED_RELATED:
            lines.insert(1, "Pairs covered: those joining a selected image to an image not selected; a pair between two "
                            "selected images is left as it is.")
        self._show("\n".join(lines))
        n, m = pv.decision_count, pv.counts[DISP_DECIDED]
        if pv.event_count:
            self.ok.configure(state="normal", text=f"Record {pv.event_count:,} deferral{'s' if pv.event_count != 1 else ''}")
        elif n:
            self.ok.configure(state="normal", text=f"Record {n:,} decision{'s' if n != 1 else ''} on {m:,} pair{'s' if m != 1 else ''}")
        else:
            self.ok.configure(state="disabled", text="Nothing to record")

    def record(self):
        if self.preview is None or self.previewed != self._key(self.control):
            self._void_preview()
            messagebox.showinfo("Survey Mode", "Preview first; the group control records exactly what the preview shows.",
                                parent=self.win)
            return
        try:
            result = self.store.commit_bulk(self.preview, note=f"Survey Mode: {SV.CONTROLS[self.control]}",
                                            command_id=new_command_id())
        except ValueError as exc:
            self.preview = self.previewed = None
            self.ok.configure(state="disabled", text="Record")
            messagebox.showwarning("Survey Mode", str(exc), parent=self.win)
            return
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Survey Mode", f"Nothing was recorded.\n\n{exc}", parent=self.win)
            return
        self.last_batch = result["batch_id"]
        c = self.preview.counts
        left = len(self.preview.candidates) - c[DISP_DECIDED]
        what = f"{result['events']:,} deferral(s)" if result["events"] else f"{result['decisions']:,} decision(s)"
        self.preview = self.previewed = None
        self.page.undo_stack.clear()
        self.page.refresh_record(select=self.page.selected)
        self._remap()
        self._refresh()
        self._show(f"Batch #{result['batch_id']} recorded: {what} on {c[DISP_DECIDED]:,} pair(s)"
                   + (f"; {left:,} left as they were" if left else "") + ". Undo it here or from Batches...")
        self.status_var.set(f"{SV.CONTROLS[self.control]}: batch #{result['batch_id']} recorded.")

    def undo_last(self):
        if not self.last_batch:
            return
        try:
            u = self.store.undo_batch(self.last_batch, reason="Survey Mode: undo the last group action")
        except ValueError as exc:
            messagebox.showwarning("Survey Mode", str(exc), parent=self.win)
            return
        self.status_var.set(f"Batch #{self.last_batch} undone ({u.get('withdrawn', 0):,} decision(s) withdrawn"
                            + (f", {u.get('restored', 0):,} deferral(s) restored" if u.get("restored") else "")
                            + "; a decision it replaced is not revived).")
        self.last_batch = None
        self.page.refresh_record(select=self.page.selected)
        self._remap()
        self._refresh()
