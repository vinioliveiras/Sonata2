"""Settings > App Icons: every app's icon -- Sonata's artwork, the package's
own, a picture of your choice or a theme icon -- and the frame's shape,
for all apps or one (icons.json, icons.py). The Dock and Launchpad follow
live. Built from Sonata's kit: Settings rows, ui.panel popovers,
ui.controls buttons and pop-ups."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import apps, icons, ui  # noqa: E402

SOURCE_TITLES = {"auto": "Sonata (default)", "package": "The app's own", "file": "Custom picture",
                 "theme": "Theme icon"}
BATCH = 24             # rows built per idle step: a long list doesn't freeze the window
ROW_ICON = 32

ui.register("""
.ai-panel { padding: 4px 4px 8px 4px; }
.ai-preview { margin: 6px 0 10px 0; }
.ai-form { margin: 0 12px; }
.ai-label { color: %(label_secondary)s; font-size: %(text_body)s; }
.ai-file { color: %(label_secondary)s; font-size: %(text_small)s; }
.ai-buttons { margin: 14px 12px 2px 12px; }
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
        self._focus = None

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
        reset = Adw.ActionRow(title="Reset app icons", use_markup=False,
                              subtitle="Forget what you set for single apps; the shape and icons above stay")
        rb = ui.controls.push_button("Reset\u2026", self.ask_reset_all, style="destructive")
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
        if self._focus in self.rows:
            self.focus(self._focus)
        return bool(self._pending)

    def focus(self, did: str) -> None:
        """Open one app's form (Launchpad's "Change Icon…"); its row may
        still be on its way (the list is built a little at a time)."""
        did = did.removesuffix(".desktop")
        row = self.rows.get(did)
        if row is None:
            self._focus = did
            return
        self._focus = None
        GLib.idle_add(lambda: (row.grab_focus(), self.edit(row), False)[2])

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
        return ui.dialog.alert("Reset app icons?",
                               "The pictures, theme icons, sizes and shapes you set for single apps are "
                               "forgotten. The shape and icons chosen for all apps stay.",
                               [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
                               lambda rid: rid == "reset" and self.reset_all(), parent=self.settings)

    def reset_all(self) -> None:
        """Every app back to the choice for all apps (Vini: only what was set
        per app goes; the shape and the icons' source stay)."""
        from .. import config
        config.update("icons", apps={})
        icons.forget_prefs()
        self.refresh()

    def set_app(self, row, **values) -> None:
        icons.set_app_pref(row.did, **values)
        self._refresh_row(row)

    # -- one app's panel (ui.panel popover) -------------------------------------------------------
    PANEL_W = 300
    LABEL_W = 70

    def edit(self, row) -> Gtk.Popover:
        """A form like macOS' (labels right-aligned, controls in one column of
        one width); rows that only apply to one kind of icon slide in/out."""
        pref = icons.app_pref(row.info)
        default_shape = icons.prefs()["shape"]
        preview = Gtk.Image(pixel_size=72, css_classes=["ai-preview"], halign=Gtk.Align.CENTER)
        # no row spacing: a hidden row (collapsed Revealer) must leave no gap; rows pad themselves
        grid = Gtk.Grid(column_spacing=10, row_spacing=0, css_classes=["ai-form"])
        self._form_row = 0

        def add(label, widget, reveal=False):
            """A form row; reveal: in a Revealer (shown when it applies)."""
            lab = Gtk.Label(label=label, xalign=1, width_chars=1, css_classes=["ai-label"])
            lab.set_size_request(self.LABEL_W, -1)
            widget.set_hexpand(True)
            for w in (lab, widget):
                w.set_margin_top(4)
                w.set_margin_bottom(4)
            if not reveal:
                grid.attach(lab, 0, self._form_row, 1, 1)
                grid.attach(widget, 1, self._form_row, 1, 1)
                self._form_row += 1
                return None
            box = Gtk.Box(spacing=10)
            box.append(lab)
            box.append(widget)
            rv = Gtk.Revealer(child=box, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                              transition_duration=ui.tokens.ms(180))
            grid.attach(rv, 0, self._form_row, 2, 1)
            self._form_row += 1
            return rv

        srcs = list(icons.SOURCES)
        source = ui.controls.popup_button([SOURCE_TITLES[x] for x in srcs], srcs.index(pref["source"]),
                                          lambda i: (self.set_app(row, source=srcs[i]), redraw()))
        add("Icon", source)
        pick_box = Gtk.Box(spacing=8)
        choose = ui.controls.push_button("Choose\u2026", lambda: self._pick_file(row, redraw))
        file_lbl = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["ai-file"])
        pick_box.append(file_lbl)
        pick_box.append(choose)
        file_rv = add("Picture", pick_box, reveal=True)
        name = Gtk.Entry(text=pref["name"], placeholder_text="e.g. firefox", css_classes=["ai-name"])
        name.connect("activate", lambda e: (self.set_app(row, name=e.get_text().strip() or None,
                                                          source="theme"), redraw()))
        name_rv = add("Name", name, reveal=True)
        cur_scale = pref["scale"] if pref["scale"] is not None else icons.default_scale()
        size = ui.controls.slider(cur_scale * 100, None, lower=icons.SCALE_RANGE[0] * 100,
                                  upper=icons.SCALE_RANGE[1] * 100, default=icons.default_scale() * 100)
        size.connect("value-changed", lambda sl: self._size_changed(row, sl.get_value(), redraw))
        size_rv = add("Size", size, reveal=True)
        shapes = [None] + list(icons.SHAPES)
        own_shape = (icons.prefs()["apps"].get(row.did) or {}).get("shape")
        shape = ui.controls.popup_button(
            [f"Default ({icons.SHAPE_TITLES[default_shape]})"] + [icons.SHAPE_TITLES[x] for x in icons.SHAPES],
            shapes.index(own_shape) if own_shape in shapes else 0,
            lambda i: (self.set_app(row, shape=shapes[i]), redraw()))
        add("Shape", shape)

        def redraw():
            icons.set_image(preview, icons.app_icon(row.info))
            p = icons.app_pref(row.info)
            file_rv.set_reveal_child(p["source"] == "file")
            file_lbl.set_label(p["path"].rsplit("/", 1)[-1] if p["path"] else "No picture")
            name_rv.set_reveal_child(p["source"] == "theme")
            size_rv.set_reveal_child(icons.plated(row.info))
        btns = Gtk.Box(spacing=8, css_classes=["ai-buttons"], homogeneous=True)
        btns.append(ui.controls.push_button("Use Default", lambda: (
            icons.set_app_pref(row.did, source=None, path=None, name=None, shape=None, scale=None),
            self._refresh_row(row), pop.popdown())))
        btns.append(ui.controls.push_button("Done", lambda: pop.popdown(), style="default"))
        col = ui.panel.column(ui.panel.header(row.info.get_display_name()), preview, grid, btns)
        col.add_css_class("ai-panel")
        pop = ui.panel.popup(row, col, gap=4, width=self.PANEL_W)
        pop.source, pop.shape, pop.choose, pop.name, pop.preview = source, shape, choose, name, preview   # (tests)
        pop.size, pop.file_rv, pop.name_rv, pop.size_rv = size, file_rv, name_rv, size_rv
        redraw()
        row.panel = pop
        return pop

    def _size_changed(self, row, value, then) -> None:
        """Saved (and the icon drawn again) once the slider pauses."""
        if getattr(self, "_size_src", 0):
            GLib.source_remove(self._size_src)

        def save():
            self._size_src = 0
            v = icons.clamp_scale(value / 100)
            self.set_app(row, scale=None if abs(v - icons.default_scale()) < 0.005 else v)
            then()
            return False
        self._size_src = GLib.timeout_add(150, save)

    def _pick_file(self, row, then) -> None:
        def chosen(path):
            self.set_app(row, source="file", path=path)
            then()
        pick_picture(self.settings, chosen)


def pick_picture(parent, on_path) -> None:
    """A picture through the Open panel (Sonata's own, via the portal):
    on_path(path) when one was chosen. (App Icons; the New Web App form.)"""
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
            on_path(f.get_path())
    dlg.open(parent, None, done)
