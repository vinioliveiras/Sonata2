"""Every app in a Settings list: a search row, one row per app (icon,
name, a subtitle, ›), built a little at a time so a long list doesn't
freeze the window. App Icons and Apps use it."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import apps, icons  # noqa: E402

BATCH = 24             # rows built per step
ROW_ICON = 32


def app_list() -> list:
    """[(desktop id, info)] of the apps a launcher lists, by name."""
    out = [(did[:-8], info) for did, info in apps.scan().items() if info.should_show()]
    return sorted(out, key=lambda p: p[1].get_display_name().casefold())


class AppList:
    """.group (an Adw.PreferencesGroup), .rows {desktop id: row}, .search.
    on_activate(row); subtitle(row) -> text (None: no subtitle)."""

    def __init__(self, title, description, on_activate, subtitle=None):
        from .app import group
        self.on_activate, self.subtitle = on_activate, subtitle
        self.rows = {}
        self._focus = None
        self.group = group(title, description)
        self.search = Adw.EntryRow(title="Search apps", use_markup=False)
        self.search.connect("changed", lambda e: self.filter(e.get_text()))
        self.group.add(self.search)
        self._pending = app_list()
        GLib.timeout_add(16, self._build_some)        # between frames (an idle can wait behind redraws)

    def _build_some(self) -> bool:
        batch, self._pending = self._pending[:BATCH], self._pending[BATCH:]
        for did, info in batch:
            self._add_row(did, info)
        if self.search.get_text():
            self.filter(self.search.get_text())
        if self._focus in self.rows:
            self.focus(self._focus)
        return bool(self._pending)

    def focus(self, did: str) -> None:
        """Open one app (its row may still be on its way)."""
        did = did.removesuffix(".desktop")
        row = self.rows.get(did)
        if row is None:
            self._focus = did
            return
        self._focus = None
        GLib.idle_add(lambda: (row.grab_focus(), self.on_activate(row), False)[2])

    def _add_row(self, did, info):
        row = Adw.ActionRow(title=info.get_display_name(), use_markup=False, activatable=True)
        img = Gtk.Image(pixel_size=ROW_ICON)
        row.add_prefix(img)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic", css_classes=["dim-label"]))
        row.did, row.info, row.image = did, info, img
        row.connect("activated", lambda r: self.on_activate(r))
        self.rows[did] = row
        self.refresh_row(row)
        self.group.add(row)

    def refresh_row(self, row) -> None:
        icons.set_image(row.image, icons.app_icon(row.info))
        if self.subtitle is not None:
            row.set_subtitle(self.subtitle(row) or "")

    def refresh(self) -> None:
        for row in self.rows.values():
            self.refresh_row(row)

    def filter(self, text: str) -> None:
        q = text.strip().casefold()
        for row in self.rows.values():
            row.set_visible(not q or q in row.get_title().casefold() or q in row.did.casefold())

    def remove(self, did: str) -> None:
        row = self.rows.pop(did, None)
        if row is not None:
            self.group.remove(row)
