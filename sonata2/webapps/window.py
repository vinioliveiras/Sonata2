"""A web app's window (WebKitGTK 6) and the "New Web App" form.

`sonata2 webapp <id>`: one process per web app, its own application id
(the Dock's icon and windows), its own cookies and storage. Links to other
sites open in the default browser; a login's redirects stay in the window.
Notifications are allowed (a chat app's whole point); the camera and the
microphone are asked for. Downloads go to Downloads.

`sonata2 webapp new`: the form (name and address), then the web app opens."""
import os
import sys
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import ui, webapps as W  # noqa: E402

NEW_APP_ID = "io.github.vinioliveiras.sonata2.webapps"
SIZE = (1200, 820)

ui.register("""
window.sonata-webapp .wa-title { font-weight: 600; color: %(label)s; }
window.sonata-webapp:backdrop .wa-title { color: %(label_secondary)s; }
.wa-form { margin-top: 6px; }
.wa-form label { color: %(label_secondary)s; }
.wa-form .wa-icon-note { color: %(label_tertiary)s; }
""", key="webapp")


def site(url: str) -> str:
    """The part of a host that is "the same site": web.whatsapp.com -> whatsapp.com."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:      # example.co.uk
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def stays_inside(app_url: str, target: str) -> bool:
    if not target or target.startswith(("about:", "blob:", "data:")):
        return True
    if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
        return False
    return site(target) == site(app_url)


def open_outside(win, uri: str) -> None:
    Gtk.UriLauncher(uri=uri).launch(win, None, None, None)


class WebAppWindow(Gtk.ApplicationWindow):
    def __init__(self, app, wid: str, entry: dict):
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        self.WebKit = WebKit
        super().__init__(application=app, title=entry["name"], css_classes=["sonata-webapp"])
        ui.window.standard(self)
        self.wid, self.entry = wid, entry
        self.set_default_size(*SIZE)
        self.set_size_request(420, 360)
        base = W.data_dir(wid)
        session = WebKit.NetworkSession.new(os.path.join(base, "data"), os.path.join(base, "cache"))
        session.get_cookie_manager().set_persistent_storage(os.path.join(base, "cookies.sqlite"),
                                                            WebKit.CookiePersistentStorage.SQLITE)
        session.get_website_data_manager().set_favicons_enabled(True)
        session.connect("download-started", self._download)
        self.view = WebKit.WebView(network_session=session, vexpand=True, hexpand=True)
        s = self.view.get_settings()
        s.set_enable_developer_extras(False)
        s.set_javascript_can_open_windows_automatically(False)
        s.set_enable_back_forward_navigation_gestures(True)
        self.view.connect("decide-policy", self._policy)
        self.view.connect("create", self._new_window)
        self.view.connect("permission-request", self._permission)
        self.view.connect("notify::title", self._title)
        self.view.connect("notify::favicon", self._favicon)
        self.view.connect("notify::uri", lambda *_a: self._buttons())
        toolbar = ui.window.glass_toolbar(self, start=(("go-previous-symbolic", "Back", self.view.go_back),
                                                       ("go-next-symbolic", "Forward", self.view.go_forward)),
                                          end=(("view-refresh-symbolic", "Reload", self.view.reload),))
        self.title_label = Gtk.Label(label=entry["name"], css_classes=["wa-title"],
                                     ellipsize=3, max_width_chars=60)          # Pango.EllipsizeMode.END
        bar = toolbar.get_child()
        bar.set_center_widget(self.title_label)
        start = bar.get_start_widget()
        self.back, self.forward = start.get_first_child(), start.get_last_child()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(toolbar)
        col.append(self.view)
        self.set_child(col)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._buttons()
        self.view.load_uri(entry["url"])

    def _buttons(self) -> None:
        self.back.set_sensitive(self.view.can_go_back())
        self.forward.set_sensitive(self.view.can_go_forward())

    def _key(self, _c, keyval, _code, state) -> bool:
        from gi.repository import Gdk
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        if (ctrl and keyval in (Gdk.KEY_r, Gdk.KEY_R)) or keyval == Gdk.KEY_F5:
            self.view.reload()
        elif state & Gdk.ModifierType.ALT_MASK and keyval == Gdk.KEY_Left:
            self.view.go_back()
        elif state & Gdk.ModifierType.ALT_MASK and keyval == Gdk.KEY_Right:
            self.view.go_forward()
        elif ctrl and keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
            self.view.set_zoom_level(min(3.0, self.view.get_zoom_level() + 0.1))
        elif ctrl and keyval == Gdk.KEY_minus:
            self.view.set_zoom_level(max(0.3, self.view.get_zoom_level() - 0.1))
        elif ctrl and keyval == Gdk.KEY_0:
            self.view.set_zoom_level(1.0)
        else:
            return False
        return True

    def _title(self, *_a) -> None:
        self.title_label.set_label(self.view.get_title() or self.entry["name"])

    def _favicon(self, *_a) -> None:
        """The page's icon, when we don't have one as good (the site's file
        couldn't be fetched when the web app was made)."""
        tex = self.view.get_favicon()
        if tex is None or tex.get_width() < W.ICON_MIN or os.path.isfile(W.icon_path(self.wid)):
            return
        os.makedirs(W.data_dir(self.wid), exist_ok=True)
        if tex.save_to_png(W.icon_path(self.wid)):
            W.write_desktop(self.wid)

    def _policy(self, _v, decision, kind) -> bool:
        WK = self.WebKit
        if kind != WK.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        action = decision.get_navigation_action()
        uri = action.get_request().get_uri()
        if action.get_navigation_type() == WK.NavigationType.LINK_CLICKED and \
                not stays_inside(self.entry["url"], uri):
            decision.ignore()
            open_outside(self, uri)
            return True
        return False

    def _new_window(self, _v, action):
        uri = action.get_request().get_uri()
        if uri and uri != "about:blank":
            if stays_inside(self.entry["url"], uri):
                self.view.load_uri(uri)
            else:
                open_outside(self, uri)
        return None

    def _permission(self, _v, request) -> bool:
        WK = self.WebKit
        if isinstance(request, WK.NotificationPermissionRequest):
            request.allow()
            return True
        if isinstance(request, WK.UserMediaPermissionRequest):
            what = "the camera and the microphone" if request.props.is_for_video_device and \
                request.props.is_for_audio_device else \
                "the camera" if request.props.is_for_video_device else "the microphone"

            def answer(rid):
                (request.allow if rid == "allow" else request.deny)()
            ui.dialog.alert(f"Allow “{self.entry['name']}” to use {what}?", "",
                            [("deny", "Don't Allow", ""), ("allow", "Allow", "default")], answer, parent=self)
            return True
        request.deny()
        return True

    def _download(self, _s, download) -> None:
        def destination(d, name):
            folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or GLib.get_home_dir()
            base, ext = os.path.splitext(os.path.basename(name or "download"))
            path, n = os.path.join(folder, base + ext), 2
            while os.path.exists(path):
                path, n = os.path.join(folder, f"{base} {n}{ext}"), n + 1
            d.set_destination(path)
            return True
        download.connect("decide-destination", destination)


# -- the "New Web App" form -------------------------------------------------------------------------
def form(on_done=None, parent=None):
    """Name and address; Create stays off until the address is one. on_done(id)."""
    grid = Gtk.Grid(row_spacing=8, column_spacing=8, css_classes=["wa-form"])
    url = Gtk.Entry(hexpand=True, placeholder_text="web.whatsapp.com", activates_default=True)
    name = Gtk.Entry(hexpand=True, placeholder_text="Name", activates_default=True)
    for row, (label, entry) in enumerate((("Address:", url), ("Name:", name))):
        grid.attach(Gtk.Label(label=label, xalign=1), 0, row, 1, 1)
        grid.attach(entry, 1, row, 1, 1)
    # the icon: the site's own unless you choose a picture (Vini); the same
    # picker as Settings > App Icons, where it can be changed later
    state = {"typed": False, "icon": None}
    icon_box = Gtk.Box(spacing=8)
    icon_img = Gtk.Image(icon_name=W.FALLBACK_ICON, pixel_size=32)
    icon_note = Gtk.Label(label="The site's own", xalign=0, hexpand=True, css_classes=["wa-icon-note"])

    def chosen(path):
        state["icon"] = path
        icon_img.set_from_file(path)
        icon_note.set_label(os.path.basename(path))
    from ..settings.appicons_page import pick_picture
    icon_box.append(icon_img)
    icon_box.append(icon_note)
    icon_box.append(ui.controls.push_button("Choose\u2026", lambda: pick_picture(parent, chosen)))
    grid.attach(Gtk.Label(label="Icon:", xalign=1), 0, 2, 1, 1)
    grid.attach(icon_box, 1, 2, 1, 1)
    grid.icon_box = icon_box                                 # (tests)

    def answer(rid):
        if rid == "create":
            target = W.normalize_url(url.get_text())
            if target:
                wid = W.create(name.get_text(), target)
                if state["icon"]:
                    W.set_custom_icon(wid, state["icon"])
                if on_done:
                    on_done(wid)
                return
        if on_done:
            on_done(None)
    dlg = ui.dialog.alert("New Web App", "A website in a window of its own, with its own icon and login.",
                          [("cancel", "Cancel", ""), ("create", "Create", "default")], answer, parent=parent)
    dlg.set_extra_child(grid)

    def changed(*_a):
        target = W.normalize_url(url.get_text())
        dlg.set_response_enabled("create", bool(target))
        if not state["typed"]:
            name.set_text(W.default_name(target) if target else "")
    url.connect("changed", changed)
    name.connect("changed", lambda *_a: state.__setitem__("typed", name.has_focus()))
    changed()
    url.grab_focus()
    return dlg


def open_webapp(wid: str) -> int:
    entry = W.get(wid)
    if not entry:
        print(f"sonata2 webapp: no web app {wid}", file=sys.stderr)
        return 1
    GLib.set_prgname(W.app_id(wid))              # the Wayland app_id = the desktop entry's StartupWMClass
    GLib.set_application_name(entry["name"])
    app = Adw.Application(application_id=W.app_id(wid))

    def activate(a):
        ui.setup()
        win = next(iter(a.get_windows()), None) or WebAppWindow(a, wid, entry)
        win.present()
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


def new_webapp() -> int:
    GLib.set_prgname(NEW_APP_ID)
    GLib.set_application_name("New Web App")
    app = Adw.Application(application_id=NEW_APP_ID)

    def activate(a):
        if getattr(a, "_form", None):
            return
        ui.setup()
        a.hold()

        def done(wid):
            if wid:
                W.launch(wid)
            a.release()
        a._form = form(done)
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


def main(argv) -> int:
    if not argv:
        print("usage: sonata2 webapp new | <id>", file=sys.stderr)
        return 2
    return new_webapp() if argv[0] == "new" else open_webapp(argv[0])
