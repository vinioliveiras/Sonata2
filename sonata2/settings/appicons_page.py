"""Settings > App Icons: every app's icon -- Sonata's artwork, the package's
own, a picture of your choice or a theme icon -- and the frame's shape,
for all apps or one (icons.json, icons.py). The Dock and Launchpad follow
live. Built from Sonata's kit: Settings rows, ui.panel popovers,
ui.controls buttons and pop-ups."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import apps, icons, ui  # noqa: E402

SOURCE_TITLES = {"auto": "Sonata (default)", "package": "The app's own", "file": "Custom picture",
                 "theme": "Theme icon"}
BATCH = 24             # rows built per idle step: a long list doesn't freeze the window
ROW_ICON = 32

ui.register("""
.ai-panel { padding: 4px 4px 8px 4px; }
.ai-preview { margin: 6px 0 10px 0; }
.ai-line { margin: 4px 10px; }
.ai-line > label { color: %(label)s; font-size: %(text_body)s; }
.ai-buttons { margin: 10px 10px 2px 10px; }
entry.ai-name { min-height: %(control_h)s; }
""", key="appicons")


def app_list() -> list:
    """[(desktop id, info)] of the apps a launcher lists, by name."""
    out = [(did[:-8], info) for did, info in apps.scan().items() if info.should_show()]
    return sorted(out, key=lambda p: p[1].get_display_name().casefold())


def describe(pref: dict, default_shape: str) -> str:
    text = SOURCE_TITLES[pref["source"]]
    if pref["shape"] != default_shape:
        text += " · " + icons.SHAPE_TITLES[pref["shape"]]
    return text


class AppIconsPage:
    def __init__(self, settings):
        self.settings = settings
        self.rows = {}

    def groups(self) -> list:
        from .app import combo_row, group
        p = icons.prefs()
        shape = group("All Apps", "The frame every app icon sits on, and where the icons come from. "
                                  "One app can have its own (below).")
        self.shape_row = combo_row("Icon shape", [(s, icons.SHAPE_TITLES[s]) for s in icons.SHAPES], p["shape"],
                                   self.set_shape)
        shape.add(self.shape_row)
        self.shape_group = shape
        every = Adw.ActionRow(title="All apps' icons", use_markup=False,
                              subtitle="Pictures and theme icons you chose for an app stay")
        btns = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        btns.append(ui.controls.push_button("Sonata's", lambda: self.set_source_all("auto")))
        btns.append(ui.controls.push_button("From Packages", lambda: self.set_source_all("package")))
        every.add_suffix(btns)
        shape.add(every)
        reset = Adw.ActionRow(title="Reset all app icons", use_markup=False,
                              subtitle="Every app back to its package's icon, on the squircle; your choices go")
        rb = ui.controls.push_button("Reset All\u2026", self.ask_reset_all, style="destructive")
        rb.set_valign(Gtk.Align.CENTER)
        reset.add_suffix(rb)
        shape.add(reset)
        lst = group("Apps", "Click an app to change its icon.")
        search = Adw.EntryRow(title="Search apps", use_markup=False)
        search.connect("changed", lambda e: self._filter(e.get_text()))
        lst.add(search)
        self.list_group, self.search = lst, search
        self._pending = app_list()
        GLib.timeout_add(16, self._build_some)        # between frames (an idle can wait behind redraws)
        return [shape, lst]

    # -- the list (built a little at a time) -------------------------------------------------
    def _build_some(self) -> bool:
        batch, self._pending = self._pending[:BATCH], self._pending[BATCH:]
        for did, info in batch:
            self._add_row(did, info)
        if self.search.get_text():
            self._filter(self.search.get_text())
        return bool(self._pending)

    def _add_row(self, did, info):
        row = Adw.ActionRow(title=info.get_display_name(), use_markup=False, activatable=True)
        img = Gtk.Image(pixel_size=ROW_ICON)
        row.add_prefix(img)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
        row.did, row.info, row.image = did, info, img
        row.connect("activated", lambda r: self.edit(r))
        self.rows[did] = row
        self._refresh_row(row)
        self.list_group.add(row)

    def _refresh_row(self, row) -> None:
        icons.set_image(row.image, icons.app_icon(row.info))
        row.set_subtitle(describe(icons.app_pref(row.info), icons.prefs()["shape"]))

    def refresh(self) -> None:
        for row in self.rows.values():
            self._refresh_row(row)

    def _filter(self, text: str) -> None:
        q = text.strip().casefold()
        for row in self.rows.values():
            row.set_visible(not q or q in row.get_title().casefold() or q in row.did.casefold())

    # -- changes ---------------------------------------------------------------------------
    def set_shape(self, shape: str) -> None:
        from .. import config
        config.update("icons", shape=shape)
        icons.forget_prefs()
        self.refresh()

    def set_source_all(self, source: str) -> None:
        """Every app from Sonata's artwork or from its package (Vini); an app
        set the same way loses its own setting, custom pictures stay."""
        from .. import config
        data = config.load("icons", icons.ICON_DEFAULTS)
        apps_ = {}
        for did, p in (data.get("apps") or {}).items():
            p = dict(p)
            if p.get("source") in ("auto", "package"):
                p.pop("source")
            if p:
                apps_[did] = p
        config.update("icons", source=source, apps=apps_)
        icons.forget_prefs()
        self.refresh()

    def ask_reset_all(self):
        """Asked first: custom pictures and per-app shapes are forgotten."""
        return ui.dialog.alert("Reset all app icons?",
                               "Every app shows its package's icon on the squircle again. The pictures, "
                               "theme icons and shapes you chose are forgotten.",
                               [("cancel", "Cancel", ""), ("reset", "Reset All", "destructive")],
                               lambda rid: rid == "reset" and self.reset_all(), parent=self.settings)

    def reset_all(self) -> None:
        """Vini: one click and every app is back to its package's icon."""
        from .. import config
        config.update("icons", shape="squircle", source="package", apps={})
        icons.forget_prefs()
        self.refresh()
        if hasattr(self, "shape_row"):
            from .app import show_quietly
            show_quietly(self.shape_row, "squircle")

    def set_app(self, row, **values) -> None:
        icons.set_app_pref(row.did, **values)
        self._refresh_row(row)

    # -- one app's panel (ui.panel popover) -------------------------------------------------------
    def edit(self, row) -> Gtk.Popover:
        pref = icons.app_pref(row.info)
        default_shape = icons.prefs()["shape"]
        preview = Gtk.Image(pixel_size=72, css_classes=["ai-preview"], halign=Gtk.Align.CENTER)
        icons.set_image(preview, icons.app_icon(row.info))

        def redraw():
            icons.set_image(preview, icons.app_icon(row.info))
            p = icons.app_pref(row.info)
            choose.set_visible(p["source"] == "file")
            name_line.set_visible(p["source"] == "theme")

        srcs = list(icons.SOURCES)
        source = ui.controls.popup_button([SOURCE_TITLES[s] for s in srcs], srcs.index(pref["source"]),
                                          lambda i: (self.set_app(row, source=srcs[i]), redraw()))
        shapes = [None] + list(icons.SHAPES)
        own_shape = (icons.prefs()["apps"].get(row.did) or {}).get("shape")
        shape = ui.controls.popup_button(
            [f"Default ({icons.SHAPE_TITLES[default_shape]})"] + [icons.SHAPE_TITLES[s] for s in icons.SHAPES],
            shapes.index(own_shape) if own_shape in shapes else 0,
            lambda i: (self.set_app(row, shape=shapes[i]), redraw()))

        def line(title, widget):
            box = Gtk.Box(spacing=12, css_classes=["ai-line"])
            box.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            box.append(widget)
            return box

        choose = ui.controls.push_button("Choose Picture…", lambda: self._pick_file(row, redraw))
        choose.set_halign(Gtk.Align.END)
        name = Gtk.Entry(text=pref["name"], placeholder_text="icon-name", css_classes=["ai-name"], width_chars=16)
        name.connect("activate", lambda e: (self.set_app(row, name=e.get_text().strip() or None,
                                                          source="theme"), redraw()))
        name_line = line("Icon name", name)
        btns = Gtk.Box(spacing=8, css_classes=["ai-buttons"], homogeneous=True)
        btns.append(ui.controls.push_button("Use Default", lambda: (
            icons.set_app_pref(row.did, source=None, path=None, name=None, shape=None),
            self._refresh_row(row), pop.popdown())))
        btns.append(ui.controls.push_button("Done", lambda: pop.popdown(), style="default"))
        col = ui.panel.column(ui.panel.header(row.info.get_display_name()), preview,
                              line("Icon", source), choose, name_line, line("Shape", shape), btns)
        col.add_css_class("ai-panel")
        pop = ui.panel.popup(row, col, gap=4)
        pop.source, pop.shape, pop.choose, pop.name, pop.preview = source, shape, choose, name, preview   # (tests)
        redraw()
        row.panel = pop
        return pop

    def _pick_file(self, row, then) -> None:
        """A picture through the Open panel (Sonata's own, via the portal)."""
        dlg = Gtk.FileDialog(title="Choose an Icon", modal=True)
        flt = Gtk.FileFilter(name="Images")
        for pat in ("*.png", "*.svg", "*.jpg", "*.jpeg", "*.webp"):
            flt.add_pattern(pat)
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return                                     # cancelled
            if f is not None and f.get_path():
                self.set_app(row, source="file", path=f.get_path())
                then()
        dlg.open(self.settings, None, done)
