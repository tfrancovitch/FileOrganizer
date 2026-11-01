r"""Move to... and Rename... (B20, P3.R12): the minimum UI for organizing intent, inside the one window.

No new window, no new page, no Organize list (cut from B20): two dialogs reached from the Files page --
the row's context menu (Move to..., Rename...) and the buttons Move checked... and Move listed... -- and the
current intent shown in the dialog itself (with Withdraw) and on the Plan page. The pattern is the Metadata
page's: every action goes through the decision store; nothing here opens, previews, stats or lists a source
file (the evidence is the walk the project already holds), and nothing ever moves or renames one.

Every surface says so, in the same words: "Stored in the project; never moves the file."
The dialogs also say "No source files will change."

Tk is used from the main thread only.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from . import bulk as BK
from . import evidence as E
from . import organize as OG
from .model import path_key
from .registry import ORGANIZE_LABEL, LOCATION, SCOPE_EXPLICIT_SELECTION, SCOPE_QUERY_SNAPSHOT, SCOPE_ORGANIZE, SCOPE_REMOVAL
from .review import store_for
from .store import new_command_id

NO_CHANGE = "No source files will change."
BRACES = "Braces are taken literally; templates are not available (they are a later round)."


def source_roots(conn):
    return [(int(r[0]), str(r[1])) for r in conn.execute(
        "SELECT source_root_id, root_path FROM source_root WHERE project_id = 1 AND is_active = 1 ORDER BY root_ordinal, root_path")]


def intent_lines(store, conn, fid):
    """What the project already holds for this file, in words: [(text, decision_id)]."""
    out = []
    roots = dict(source_roots(conn))
    for d in store.active_for(LOCATION, fid, scope="organize"):
        if d.kind == "desired_location":
            v = d.value
            out.append((f"Desired location: {roots.get(v['destination_root_id'], 'root ' + str(v['destination_root_id']))}"
                        f"\\{v['destination_folder']}".rstrip("\\") + f"   (decision #{d.decision_id})", d.decision_id))
        else:
            out.append((f"Desired name: {d.value['name']}   (decision #{d.decision_id})", d.decision_id))
    return out


def find_companions(conn, store, fid):
    """Sidecars found by NAME beside this file, from stored paths only: [(file_path_id or None, name, link)].
    An ambiguous one (a RAW and a JPEG share its stem) is listed with id None and never ticked."""
    loc = E.location_any(conn, fid)
    if loc is None or not loc.rel_path:
        return []
    facts = store._organize_facts()
    folder, me = OG.split_rel(loc.rel_path)
    names = [n for spelled in OG.folder_index(facts).files(OG.full_key(loc.root_key, folder)).values() for n in spelled]
    row = conn.execute("SELECT source_root_id FROM file_state WHERE file_path_id = ?", (int(fid),)).fetchone()
    out = []
    for l in OG.detect(names):
        mine = (not l.ambiguous and path_key(l.primary) == path_key(me)) or \
            (l.ambiguous and path_key(me) in {path_key(c) for c in l.candidates})
        if not mine:
            continue
        sid = None
        if not l.ambiguous and row is not None:
            r = conn.execute("SELECT file_path_id FROM file_path WHERE source_root_id = ? AND relative_path = ?",
                             (row[0], OG.join_rel(folder, l.sidecar))).fetchone()
            sid = str(r[0]) if r is not None else None
        out.append((sid, l.sidecar, l))
    return out


class OrganizeDialog:
    """Move to... / Rename... for ONE file, or Move checked... / Move listed... for many (a frozen batch)."""

    def __init__(self, app, ids, mode="move", on_done=None, listed=False, query=None):
        self.app, self.ids, self.mode, self.on_done = app, [str(i) for i in ids], mode, on_done or (lambda: None)
        self.listed, self.query = listed, query
        self.store = store_for(app)
        self.conn = app.conn
        self.single = len(self.ids) == 1
        self.preview = None
        self.companions = []                       # [(id, name, link, BooleanVar)]
        self.win = tk.Toplevel(app)
        title = ("Rename..." if mode == "rename" else ("Move to..." if self.single else
                                                        ("Move listed..." if listed else "Move checked...")))
        self.win.title(title)
        self.win.transient(app)
        f = ttk.Frame(self.win, padding=12)
        f.pack(fill="both", expand=True)
        self.frame = f
        self.heading = tk.StringVar()
        ttk.Label(f, textvariable=self.heading, font=("Segoe UI", 11, "bold"), wraplength=620, justify="left").pack(anchor="w")
        ttk.Label(f, text=ORGANIZE_LABEL, foreground="#444").pack(anchor="w", pady=(2, 8))
        self.intent_frame = ttk.Frame(f)
        self.intent_frame.pack(fill="x")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 2))
        self.root_var, self.folder_var, self.name_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.confirm_ext = tk.BooleanVar(value=False)
        self.roots = source_roots(self.conn)
        if mode == "move":
            ttk.Label(row, text="Source root:").pack(side="left")
            self.root_box = ttk.Combobox(row, textvariable=self.root_var, values=[p for _i, p in self.roots], state="readonly", width=44)
            self.root_box.pack(side="left", padx=(4, 10))
            if self.roots:
                self.root_box.current(0)
            row2 = ttk.Frame(f)
            row2.pack(fill="x", pady=2)
            ttk.Label(row2, text="Folder in that root (typed as you want it; \\ between folders; empty = the root):").pack(side="left")
            self.folder_entry = ttk.Entry(f, textvariable=self.folder_var, width=70)
            self.folder_entry.pack(fill="x", pady=2)
            self.folder_entry.bind("<KeyRelease>", lambda e: self.refresh())
            self.root_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        else:
            ttk.Label(row, text="New name:").pack(side="left")
            self.name_entry = ttk.Entry(row, textvariable=self.name_var, width=60)
            self.name_entry.pack(side="left", padx=4)
            self.name_entry.bind("<KeyRelease>", lambda e: self.refresh())
            self.ext_check = ttk.Checkbutton(f, text="Yes, the extension changes. Renaming does not convert the file; it changes how it opens.",
                                             variable=self.confirm_ext, command=self.refresh)
            self.ext_check.pack(anchor="w")
        self.comp_frame = ttk.LabelFrame(f, text="Companions found by name beside it (ticked: recorded with it, each as its own decision)")
        self.text = tk.Text(f, height=11, width=86, wrap="word", state="disabled", relief="flat", background="#fbfbfb")
        self.text.pack(fill="both", expand=True, pady=6)
        ttk.Label(f, text=NO_CHANGE, foreground="#444").pack(anchor="w")
        brow = ttk.Frame(f)
        brow.pack(fill="x", pady=(8, 0))
        self.ok = ttk.Button(brow, text="Record", command=self.record, state="disabled")
        self.ok.pack(side="right")
        ttk.Button(brow, text="Cancel", command=self.win.destroy).pack(side="right", padx=(0, 6))
        if not self.single:
            self.preview_button = ttk.Button(brow, text="Preview", command=self.run_preview)
            self.preview_button.pack(side="left")
        self._intent()
        self._companions()
        self.refresh()

    # -- what the dialog shows -------------------------------------------------------------------

    def _name(self, fid):
        loc = E.location_any(self.conn, fid)
        return loc.rel_path.rsplit("\\", 1)[-1] if loc is not None and loc.rel_path else f"location {fid}"

    def _intent(self):
        for w in self.intent_frame.winfo_children():
            w.destroy()
        if not self.single:
            n = len(self.ids)
            self.heading.set(f"Record one destination folder for exactly the {n:,} {'listed' if self.listed else 'checked'} file(s)")
            return
        self.heading.set(f"{'Rename' if self.mode == 'rename' else 'Move'}: {self._name(self.ids[0])}")
        lines = intent_lines(self.store, self.conn, self.ids[0])
        self.intent_rows = []
        ttk.Label(self.intent_frame, text="Current intent for this file:" if lines else "No destination or name is recorded for this file.",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        for text, did in lines:
            r = ttk.Frame(self.intent_frame)
            r.pack(fill="x")
            ttk.Label(r, text=text).pack(side="left")
            ttk.Button(r, text="Withdraw", command=lambda d=did: self.withdraw(d)).pack(side="left", padx=8)
            self.intent_rows.append(text)

    def _companions(self):
        for w in self.comp_frame.winfo_children():
            w.destroy()
        self.companions = []
        if not self.single:
            return
        found = find_companions(self.conn, self.store, self.ids[0])
        if not found:
            self.comp_frame.pack_forget()
            return
        self.comp_frame.pack(fill="x", pady=4, before=self.text)
        for sid, name, link in found:
            var = tk.BooleanVar(value=sid is not None and not link.ambiguous)
            if sid is None or link.ambiguous:
                ttk.Label(self.comp_frame, text=f"{name}  -- shared by {', '.join(link.candidates)}: ambiguous, so not ticked. Give it a "
                                                "destination yourself, and every file that shares it the same one.").pack(anchor="w")
            else:
                ttk.Checkbutton(self.comp_frame, text=name, variable=var, command=self.refresh).pack(anchor="w")
            self.companions.append((sid, name, link, var))

    def _say(self, text, ok):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        self.text.configure(state="disabled")
        self.ok.configure(state="normal" if ok else "disabled")

    def ticked(self):
        return [(sid, name, link) for sid, name, link, var in self.companions if sid is not None and var.get()]

    def _dest(self):
        root_id = next((i for i, p in self.roots if p == self.root_var.get()), None)
        return root_id, self.folder_var.get().replace("/", "\\")

    def refresh(self):
        """Say what recording would store -- and why not, in words -- without recording anything."""
        notes = []
        if self.mode == "move":
            folder = self.folder_var.get()
            if "{" in folder or "}" in folder:
                notes.append(BRACES)
            if not self.single:
                self._say("Press Preview to see exactly what would be recorded, bucket by bucket.", False)
                return
            root_id, folder = self._dest()
            if root_id is None:
                self._say("Choose a source root.", False)
                return
            try:
                canonical, changed, creates = self.store.destination_preview(root_id, folder)
            except ValueError as exc:
                self._say("Not recorded: " + str(exc), False)
                return
            for typed, seen in changed:
                notes.append(f"'{typed}' will be saved as '{seen}' (the folder's observed spelling).")
            if creates:
                notes.append("Folders that would be created: " + "; ".join(creates))
            facts = self.store._organize_facts()
            root_key = facts.root_ids.get(root_id)
            for fid in [self.ids[0]] + [c[0] for c in self.ticked()]:
                loc = E.location_any(self.conn, fid)
                if loc is None or root_key is None:
                    continue
                key = OG.full_key(root_key, OG.join_rel(canonical, loc.rel_path.rsplit("\\", 1)[-1]))
                occ = OG.occupancy(facts, key, own_key=loc.path_key, root_key=root_key, folder=canonical)
                if occ.state == "occupied":
                    notes.append(f"{self._name(fid)}: {occ.detail}. You can still record it; the plan will block it.")
                elif occ.state == "unknown":
                    notes.append(f"{self._name(fid)}: {occ.detail}.")
            if root_key is not None and root_key != (E.location_any(self.conn, self.ids[0]).root_key):
                vol = (facts.volumes.get(E.location_any(self.conn, self.ids[0]).root_key), facts.volumes.get(root_key))
                if vol[0] and vol[1] and vol[0] != vol[1]:
                    notes.append("That root is on another volume: a cross-volume move is not supported yet (the plan will say so).")
            self._say("Will be recorded: a desired location "
                      f"{canonical or 'the root'} for this file" + (f" and {len(self.ticked())} companion(s)" if self.ticked() else "")
                      + ".\n" + "\n".join(notes) + "\n" + ORGANIZE_LABEL + ".", True)
        else:
            name = self.name_var.get()
            if "{" in name or "}" in name:
                notes.append(BRACES)
            loc = E.location_any(self.conn, self.ids[0])
            if not name:
                self._say("Type the new name.", False)
                return
            why = OG.name_problem(name)
            cur = loc.rel_path.rsplit("\\", 1)[-1] if loc is not None else ""
            if why is None and name == cur:
                why = "that is already the file's name"
            if why is None and OG.extension_of(name) != OG.extension_of(cur) and not self.confirm_ext.get():
                why = "the extension changes: tick the box to confirm it"
            if why:
                self._say("Not recorded: " + why, False)
                return
            for sid, cname, link in self.ticked():
                notes.append(f"{cname}  ->  {OG.mapped_sidecar_name(link, cur, name)}")
            self._say(f"Will be recorded: a desired name for this file: {name}.\n" + "\n".join(notes) + "\n" + ORGANIZE_LABEL + ".", True)

    # -- actions ---------------------------------------------------------------------------------

    def withdraw(self, decision_id):
        try:
            self.store.withdraw(decision_id, "withdrawn from the Move / Rename dialog", command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Withdraw", str(exc), parent=self.win)
            return
        self._intent()
        self.refresh()
        self.on_done()

    def run_preview(self):
        root_id, folder = self._dest()
        if root_id is None:
            self._say("Choose a source root.", False)
            return None
        try:
            self.preview = BK.preview_organize(self.conn, self.ids, root_id, folder,
                                               SCOPE_QUERY_SNAPSHOT if self.listed else SCOPE_EXPLICIT_SELECTION,
                                               BK.organize_query(self.ids, listed=self.listed, filters=self.query) if self.listed else None)
        except ValueError as exc:
            self._say("Not previewed: " + str(exc), False)
            return None
        folder_notice = BRACES + "\n" if ("{" in folder or "}" in folder) else ""
        self._say(folder_notice + "\n".join(self.preview.lines()), self.preview.decision_count > 0)
        self.ok.configure(text=f"Record {self.preview.decision_count:,} decision(s)" if self.preview.decision_count else "Nothing to record")
        return self.preview

    def record(self):
        try:
            if not self.single:
                if self.preview is None or not self.preview.decision_count:
                    return None
                res = self.store.commit_bulk(self.preview, note="Move batch from the Files page", command_id=new_command_id())
            elif self.mode == "move":
                root_id, folder = self._dest()
                res = self.store.record_organize(self.ids[0], destination_root_id=root_id, destination_folder=folder,
                                                 companions=[(sid, None) for sid, _n, _l in self.ticked()], command_id=new_command_id())
            else:
                name = self.name_var.get()
                cur = self._name(self.ids[0])
                res = self.store.record_organize(self.ids[0], name=name, extension_change_confirmed=self.confirm_ext.get(),
                                                 companions=[(sid, OG.mapped_sidecar_name(link, cur, name)) for sid, _n, link in self.ticked()],
                                                 command_id=new_command_id())
        except ValueError as exc:
            messagebox.showwarning("Not recorded", str(exc), parent=self.win)
            return None
        except Exception as exc:                                    # noqa: BLE001
            messagebox.showerror("Not recorded", f"Nothing was recorded.\n\n{exc}", parent=self.win)
            return None
        self.win.destroy()
        self.on_done()
        return res


def open_move(app, ids, listed=False, query=None, on_done=None):
    ids = [str(i) for i in ids if i is not None]
    if not ids:
        messagebox.showinfo("Move", "Nothing is " + ("listed." if listed else "checked: select files in the table first."), parent=app)
        return None
    app.organize_dialog = OrganizeDialog(app, ids, "move", on_done, listed=listed, query=query)
    return app.organize_dialog


def open_rename(app, fid, on_done=None):
    app.organize_dialog = OrganizeDialog(app, [str(fid)], "rename", on_done)
    return app.organize_dialog
