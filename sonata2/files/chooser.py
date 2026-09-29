"""Open / Save panels (macOS NSOpenPanel / NSSavePanel), served to every
app through the file chooser portal (sonata2/portal.py).

A Files window in "chooser" mode: the same sidebar (Favorites, pinned
folders, drives), views, search and keyboard. Saving adds a "Save As"
field above the browser, and a bottom bar holds the file type pop-up (the
app's filters) with Cancel and Open/Save.

    ChooserWindow(app, mode="open"|"save"|"folder", title=..., accept_label=...,
                  multiple=False, filters=[(name, [(kind, pattern)])], current_filter=i,
                  folder=uri, name="Untitled.txt", on_done=callback)
    on_done(uris or None, filter_index)       # None: cancelled
"""
import fnmatch

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from .folder import VIRTUAL, file_of, is_dir  # noqa: E402
from .window import FilesWindow  # noqa: E402

ui.register("""
.fc-bottom { padding: 10px 12px; background: %(content_bg)s; box-shadow: inset 0 1px %(separator)s; }
.fc-top { padding: 10px 14px; background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.fc-top label, .fc-bottom label.fc-caption { color: %(label_secondary)s; }
.fc-top entry { min-width: 260px; }
button.fc-accept { min-width: 76px; }
button.fc-cancel { min-width: 76px; }
""", key="chooser")

# kind of a filter rule (portal spec): 0 glob pattern, 1 MIME type
GLOB, MIME = 0, 1


def matches(info, rules) -> bool:
    name = info.get_display_name().casefold()
    ct = info.get_content_type() or ""
    for kind, pattern in rules:
        if kind == GLOB and fnmatch.fnmatch(name, pattern.casefold()):
            return True
        if kind == MIME and ct and (pattern == ct or Gio.content_type_is_a(ct, pattern) or
                                    Gio.content_type_is_mime_type(ct, pattern)):
            return True
    return False


class ChooserWindow(FilesWindow):
    def __init__(self, app, mode="open", title="", accept_label="", multiple=False, filters=None,
                 current_filter=0, folder=None, name="", on_done=None):
        self.mode = mode
        self.multiple = multiple
        self.filters = list(filters or [])
        self.filter_index = min(max(0, current_filter), max(0, len(self.filters) - 1))
        self.on_done = on_done
        self._answered = False
        super().__init__(app, folder)
        self.add_css_class("sonata-chooser")
        self.set_title(title or {"open": "Open", "save": "Save", "folder": "Choose Folder"}[mode])
        self.set_default_size(820, 520)
        content = self.get_content().get_end_child()
        if mode == "save":
            content.prepend(self._save_row(name))
        content.append(self._bottom(accept_label))
        self.connect("close-request", lambda *_: (self._finish(None), False)[1])
        for v in self.views.values():                 # the accept button follows the selection
            if hasattr(v, "selection"):
                v.selection.connect("selection-changed", lambda *_: self._update_accept())
        self._refilter()
        self._update_accept()

    # -- layout --------------------------------------------------------------------
    def _save_row(self, name):
        outer = Gtk.Box(css_classes=["fc-top"])
        row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, hexpand=True)
        outer.append(row)
        row.append(Gtk.Label(label="Save As:"))
        self.name_entry = Gtk.Entry(text=name or "", hexpand=False)
        self.name_entry.connect("activate", lambda *_: self._accept())
        self.name_entry.connect("changed", lambda *_: self._update_accept())
        row.append(self.name_entry)
        # the name is selected up to its extension, ready to type over (macOS)
        stem = (name or "").rfind(".")
        GLib.idle_add(lambda: (self.name_entry.grab_focus(),
                               self.name_entry.select_region(0, stem if stem > 0 else -1), False)[2])
        return outer

    def _bottom(self, accept_label):
        bar = Gtk.Box(spacing=8, css_classes=["fc-bottom"])
        if self.filters:
            bar.append(Gtk.Label(label="Show:" if self.mode != "save" else "Format:", css_classes=["fc-caption"]))
            combo = Gtk.DropDown.new_from_strings([f[0] for f in self.filters])
            combo.set_selected(self.filter_index)
            combo.connect("notify::selected", lambda d, _p: self._set_filter(d.get_selected()))
            bar.append(combo)
        if self.mode in ("save", "folder"):
            new = Gtk.Button(label="New Folder", css_classes=["fc-cancel"])
            new.connect("clicked", lambda *_: self.new_folder())
            bar.append(new)
        bar.append(Gtk.Box(hexpand=True))
        cancel = Gtk.Button(label="Cancel", css_classes=["fc-cancel"])
        cancel.connect("clicked", lambda *_: self._finish(None))
        bar.append(cancel)
        label = accept_label.replace("_", "") if accept_label else \
            {"open": "Open", "save": "Save", "folder": "Choose"}[self.mode]
        self.accept_btn = Gtk.Button(label=label, css_classes=["suggested-action", "fc-accept"])
        self.accept_btn.connect("clicked", lambda *_: self._accept())
        bar.append(self.accept_btn)
        self.set_default_widget(self.accept_btn)
        return bar

    # -- what shows --------------------------------------------------------------------
    def _set_filter(self, i):
        self.filter_index = i
        self._refilter()

    def _match(self, info) -> bool:
        if not super()._match(info):
            return False
        if is_dir(info):
            return True
        if self.mode == "folder":
            return False
        if self.filters and self.mode == "open":
            return matches(info, self.filters[self.filter_index][1])
        return True

    def open_item(self, info) -> None:
        """Double-click: folders open, files are chosen (Open) or named (Save)."""
        if info is None:
            return
        if is_dir(info):
            self.go(info.get_attribute_string("standard::target-uri") or file_of(info).get_uri())
            return
        if self.mode == "open":
            self._accept([info])
        elif self.mode == "save":
            self.name_entry.set_text(info.get_display_name())
            self._accept()

    def _update_accept(self):
        if not hasattr(self, "accept_btn"):
            return
        here = self.location() not in VIRTUAL
        sel = self.view.selected()
        if self.mode == "save":
            ok = here and bool(self.name_entry.get_text().strip())
        elif self.mode == "folder":
            ok = here or any(is_dir(i) for i in sel)
        else:
            ok = bool(sel)
        self.accept_btn.set_sensitive(ok)

    def _loaded(self, uri):
        super()._loaded(uri)
        self._update_accept()

    # -- answer --------------------------------------------------------------------
    def _accept(self, chosen=None):
        sel = chosen or self.view.selected()
        if self.mode == "open":
            files = [i for i in sel if not is_dir(i)]
            if not files and len(sel) == 1:           # a folder selected: go into it (macOS)
                self.open_item(sel[0])
                return
            if not files:
                return
            files = files if self.multiple else files[:1]
            self._finish([file_of(i).get_uri() for i in files])
        elif self.mode == "folder":
            dirs = [i for i in sel if is_dir(i)]
            uris = [file_of(i).get_uri() for i in (dirs if self.multiple else dirs[:1])] or \
                [self.location()]
            self._finish(uris)
        else:
            name = self.name_entry.get_text().strip()
            if not name or self.location() in VIRTUAL:
                return
            dest = Gio.File.new_for_uri(self.location()).get_child_for_display_name(name)
            if dest.query_exists(None):
                ui.dialog.alert(f"“{name}” already exists. Do you want to replace it?",
                                "A file with the same name already exists in this folder. "
                                "Replacing it will overwrite its current contents.",
                                [("cancel", "Cancel", ""), ("replace", "Replace", "destructive")],
                                lambda r: r == "replace" and self._finish([dest.get_uri()]), parent=self)
                return
            self._finish([dest.get_uri()])

    def _finish(self, uris):
        if self._answered:
            return
        self._answered = True
        if self.on_done:
            self.on_done(uris, self.filter_index)
        self.close()
