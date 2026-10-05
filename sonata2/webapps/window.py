"""A web app's window (WebKitGTK 6) and the "New Web App" form.

`sonata2 webapp <id>`: one process per web app, its own application id
(the Dock's icon and windows), its own cookies and storage. Links to other
sites open in the default browser; a login's redirects stay in the window.
Notifications are allowed (a chat app's whole point); the camera and the
microphone are asked for. Downloads go to Downloads.

`sonata2 webapp new`: the form (name and address), then the web app opens."""
import ipaddress
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
QUIT_DELAY_MS = 2500           # after its window closes: WebKit writes what the site saved
# WebKitGTK decodes a video given to an <img> (or createImageBitmap) frame by
# frame into memory: WhatsApp checks a 44 s mp4 that way -> 50 GB, killed.
# Chrome refuses a video there; so do web apps, and sites go on to <video>.
NO_VIDEO_IMAGES_JS = r"""(() => {
  const videos = new Set();
  const make = URL.createObjectURL;
  URL.createObjectURL = function (obj) {
    const url = make.apply(this, arguments);
    if (obj && typeof obj.type === "string" && obj.type.startsWith("video/")) videos.add(url);
    return url;
  };
  const drop = URL.revokeObjectURL;
  URL.revokeObjectURL = function (url) { videos.delete(url); return drop.apply(this, arguments); };
  const BAD = "data:,";
  const desc = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, "src");
  Object.defineProperty(HTMLImageElement.prototype, "src", {
    configurable: true, enumerable: desc.enumerable, get: desc.get,
    set(v) { desc.set.call(this, videos.has(String(v)) ? BAD : v); },
  });
  const setAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, v) {
    if (this instanceof HTMLImageElement && String(name).toLowerCase() === "src" && videos.has(String(v))) v = BAD;
    return setAttr.call(this, name, v);
  };
  const bitmap = window.createImageBitmap;
  if (bitmap) window.createImageBitmap = function (src) {
    if (src instanceof Blob && src.type.startsWith("video/"))
      return Promise.reject(new DOMException("The source image could not be decoded.", "InvalidStateError"));
    return bitmap.apply(this, arguments);
  };
})();"""


def content_manager(WebKit):
    """The scripts every page of a web app gets (NO_VIDEO_IMAGES_JS)."""
    ucm = WebKit.UserContentManager()
    ucm.add_script(WebKit.UserScript.new(NO_VIDEO_IMAGES_JS, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                         WebKit.UserScriptInjectionTime.START, None, None))
    extra = os.environ.get(DEBUG_JS_ENV)           # a site's problem looked into: a script of one's own
    if extra and os.path.isfile(extra):
        with open(extra, encoding="utf-8") as f:
            ucm.add_script(WebKit.UserScript.new(f.read(), WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                                 WebKit.UserScriptInjectionTime.START, None, None))
    return ucm


DEBUG_JS_ENV = "SONATA_WEBAPP_JS"        # set: that script on every page, the console printed out

ui.register("""
.wa-form { margin-top: 6px; }
.wa-form label { color: %(label_secondary)s; }
.wa-form .wa-icon-note { color: %(label_tertiary)s; }
""", key="webapp")


def site(url: str) -> str:
    """The part of a host that is "the same site": web.whatsapp.com -> whatsapp.com."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return host                                  # an IP is a site of its own (no labels to share)
    except ValueError:
        pass
    base = _base_domain(host)
    if base:
        return base
    parts = host.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:      # example.co.uk
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _base_domain(host: str):
    """libsoup's public suffix list (WebKit's own): a.github.io and b.github.io
    are different sites. None without it (or for localhost and the like)."""
    try:
        gi.require_version("Soup", "3.0")
        from gi.repository import Soup
        return Soup.tld_get_base_domain(host)
    except (ValueError, ImportError, GLib.Error):
        return None


def stays_inside(app_url: str, target: str) -> bool:
    if not target or target.startswith(("about:", "blob:", "data:")):
        return True
    if urllib.parse.urlsplit(target).scheme not in ("http", "https"):
        return False
    return site(target) == site(app_url)


SAFE_SCHEMES = ("http", "https", "mailto", "tel")


def open_outside(win, uri: str) -> None:
    """Web, mail and phone links go to their app; anything else (file://, a
    custom scheme that runs a program...) only after the user says so."""
    scheme = urllib.parse.urlsplit(uri or "").scheme.lower()
    if scheme in SAFE_SCHEMES:
        Gtk.UriLauncher(uri=uri).launch(win, None, None, None)
        return
    name = (getattr(win, "entry", None) or {}).get("name") or "This web app"

    def answer(rid):
        if rid == "open":
            Gtk.UriLauncher(uri=uri).launch(win, None, None, None)
    shown = uri if len(uri) <= 200 else uri[:200] + "…"
    return ui.dialog.alert(f"Open this link outside “{name}”?",
                           f"{shown}\n\nIt opens in another program on this computer.",
                           [("cancel", "Cancel", ""), ("open", "Open", "default")], answer, parent=win)


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif", ".heic": "image/heic",
               ".mp4": "video/mp4", ".webm": "video/webm", ".pdf": "application/pdf"}
PASTE_MAX = 64 * 1024 * 1024          # a pasted file the page gets (bigger: the page's own attach button)


def paste_kind(mimes) -> str:
    """What Ctrl+V should hand the page itself: "image" (a picture, e.g. a
    screenshot), "files" (files copied in Files), or "" -- text, and
    anything else, WebKit pastes by itself."""
    mimes = set(mimes or ())
    if "text/uri-list" in mimes or "application/vnd.portal.files" in mimes:
        return "files"
    if any(m.startswith("image/") for m in mimes) and "text/plain" not in mimes \
            and "text/plain;charset=utf-8" not in mimes:
        return "image"
    return ""


def paste_script(files) -> str:
    """JavaScript that pastes `files` [(name, mime, bytes)] into the focused
    element, as the browser's own paste would: WhatsApp, Telegram, Discord...
    read ClipboardEvent.clipboardData.files. (WebKitGTK hands pages only
    text from the clipboard: pictures never arrived -- Vini.)"""
    import base64
    import json
    items = [{"name": n, "type": t, "b64": base64.b64encode(b).decode("ascii")} for n, t, b in files]
    return ("(function(files){const dt=new DataTransfer();"
            "for(const f of files){const s=atob(f.b64);const a=new Uint8Array(s.length);"
            "for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);"
            "dt.items.add(new File([a],f.name,{type:f.type}));}"
            "const t=document.activeElement||document.body;"
            "t.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));"
            "})(" + json.dumps(items) + ");")


def read_pasted_files(paths) -> list:
    """[(name, mime, bytes)] of the copied files a page can take."""
    out = []
    for path in paths:
        mime = IMAGE_TYPES.get(os.path.splitext(path)[1].lower())
        try:
            if mime and os.path.getsize(path) <= PASTE_MAX:
                with open(path, "rb") as f:
                    out.append((os.path.basename(path), mime, f.read()))
        except OSError:
            pass
    return out


class WebAppWindow(Gtk.ApplicationWindow):
    def __init__(self, app, wid: str, entry: dict):
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit
        self.WebKit = WebKit
        super().__init__(application=app, title=entry["name"], css_classes=["sonata-webapp"])
        ui.window.standard(self)
        self.wid, self.entry = wid, entry
        ui.window.fit_default_size(self, *SIZE)  # never bigger than the display (laptops)
        self.set_size_request(420, 360)
        base = W.data_dir(wid)
        session = WebKit.NetworkSession.new(os.path.join(base, "data"), os.path.join(base, "cache"))
        session.get_cookie_manager().set_persistent_storage(os.path.join(base, "cookies.sqlite"),
                                                            WebKit.CookiePersistentStorage.SQLITE)
        session.get_website_data_manager().set_favicons_enabled(True)
        session.connect("download-started", self._download)
        self.view = WebKit.WebView(network_session=session, user_content_manager=content_manager(WebKit),
                                   vexpand=True, hexpand=True)
        s = self.view.get_settings()
        s.set_enable_developer_extras(False)
        s.set_enable_write_console_messages_to_stdout(bool(os.environ.get(DEBUG_JS_ENV)))
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
        # the title shows once, in the title bar (Vini: it was there and in the toolbar too)
        bar = toolbar.get_child()
        start = bar.get_start_widget()
        self.back, self.forward = start.get_first_child(), start.get_last_child()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(toolbar)
        col.append(self.view)
        self.set_child(col)
        # before the page: WebKit takes Ctrl+V (and the other keys) itself, so a
        # window controller in the usual bubble phase never saw them -- pictures
        # never reached the paste below (Vini: WhatsApp)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.keys = keys
        self._buttons()
        self.connect("close-request", self._close)
        self.view.load_uri(entry["url"])

    def _close(self, *_a) -> bool:
        """Closing: the window goes at once; the app either keeps running
        (the form's "Keep running…": notifications) or quits a moment later,
        so WebKit finishes writing the login (Vini: WhatsApp asked for the
        QR code again -- quitting at once lost what it was saving)."""
        self.set_visible(False)
        if self.entry.get("background"):
            return True
        app = self.get_application()
        if app is not None and not getattr(self, "_quit_src", 0):
            app.hold()
            self._quit_src = GLib.timeout_add(QUIT_DELAY_MS, lambda: (app.release(), app.quit(), False)[2])
        return True

    def reopen(self) -> None:
        """Opened again (its icon) while closing or kept in the background."""
        src = getattr(self, "_quit_src", 0)
        if src:
            GLib.source_remove(src)
            self._quit_src = 0
            self.get_application().release()
        self.present()

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
        elif ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            return self._paste()
        else:
            return False
        return True

    def _paste(self) -> bool:
        """Ctrl+V with a picture or copied files: handed to the page (True);
        text goes on to WebKit's own paste (False)."""
        clip = self.get_clipboard()
        kind = paste_kind(clip.get_formats().get_mime_types() or [])
        if kind == "image":
            def got(c, res):
                try:
                    tex = c.read_texture_finish(res)
                except GLib.Error:
                    tex = None
                self._send_paste([("image.png", "image/png", tex.save_to_png_bytes().get_data())]
                                 if tex is not None else [])
            clip.read_texture_async(None, got)
            return True
        if kind == "files":
            def got(c, res):
                try:
                    stream, _mime = c.read_finish(res)
                    data = stream.read_bytes(1 << 20, None).get_data()     # a list of URIs: small
                    stream.close(None)
                except GLib.Error:
                    data = b""
                from gi.repository import Gio
                uris = [u.strip() for u in data.decode("utf-8", "replace").splitlines()
                        if u.strip().startswith("file://")]
                self._send_paste(read_pasted_files([p for p in (Gio.File.new_for_uri(u).get_path()
                                                                for u in uris) if p]))
            clip.read_async(["text/uri-list"], GLib.PRIORITY_DEFAULT, None, got)
            return True
        return False

    def _send_paste(self, files) -> None:
        """The files to the page; none it can take (a text file, a folder):
        WebKit's own paste, as if we had never stepped in."""
        if files:
            self.view.evaluate_javascript(paste_script(files), -1, None, None, None, None, None)
        else:
            self.view.execute_editing_command(self.WebKit.EDITING_COMMAND_PASTE)

    def _title(self, *_a) -> None:
        self.set_title(self.view.get_title() or self.entry["name"])

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
def form(on_done=None, parent=None, wid=None):
    """Name and address; Create stays off until the address is one. on_done(id).
    wid: edit that web app instead (Vini) -- the same form, filled in."""
    entry0 = W.get(wid) if wid else None
    grid = Gtk.Grid(row_spacing=8, column_spacing=8, css_classes=["wa-form"])
    url = ui.controls.text_field(placeholder="web.whatsapp.com", hexpand=True)     # the kit's fields
    name = ui.controls.text_field(placeholder="Name", hexpand=True)
    for e in (url, name):
        e.set_activates_default(True)
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
    # closing its window keeps it running (a chat's notifications keep coming)
    background = Gtk.CheckButton(label="Keep running when its window is closed")
    grid.attach(background, 1, 3, 1, 1)
    grid.background = background
    if entry0:
        url.set_text(entry0["url"])
        name.set_text(entry0["name"])
        background.set_active(bool(entry0.get("background")))
        state["typed"] = True                                # its name stays as it is
        if os.path.isfile(W.icon_path(wid)):
            icon_img.set_from_file(W.icon_path(wid))

    def answer(rid):
        if rid == "create":
            target = W.normalize_url(url.get_text())
            if target:
                if entry0:
                    W.update(wid, name=name.get_text(), url=target, background=background.get_active())
                    done = wid
                else:
                    done = W.create(name.get_text(), target, background=background.get_active())
                if state["icon"]:
                    W.set_custom_icon(done, state["icon"])
                if on_done:
                    on_done(done)
                return
        if on_done:
            on_done(None)
    if entry0:
        heading, body, ok = "Edit Web App", "Changes show the next time it opens.", "Save"
    else:
        heading, body, ok = "New Web App", "A website in a window of its own, with its own icon and login.", "Create"
    dlg = ui.dialog.alert(heading, body, [("cancel", "Cancel", ""), ("create", ok, "default")], answer,
                          parent=parent)
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


def missing_webkit(app) -> None:
    """Said instead of failing silently (Vini: "the web app doesn't open" --
    webkitgtk-6.0 wasn't installed)."""
    app.hold()
    ui.dialog.alert(*W.MISSING_WEBKIT, [("ok", "OK", "default")], lambda _r: app.release())


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
        if not W.webkit_available():
            missing_webkit(a)
            return
        win = next(iter(a.get_windows()), None)
        if win is not None:
            win.reopen()
        else:
            WebAppWindow(a, wid, entry).present()
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


def edit_webapp(wid: str) -> int:
    """`sonata2 webapp edit <id>`: the form, filled in (Launchpad's and the
    Dock's "Edit Web App…")."""
    if not W.get(wid):
        print(f"sonata2 webapp: no web app {wid}", file=sys.stderr)
        return 1
    return new_webapp(wid)


def new_webapp(wid: str = None) -> int:
    GLib.set_prgname(NEW_APP_ID)
    GLib.set_application_name("New Web App")
    app = Adw.Application(application_id=NEW_APP_ID)

    def activate(a):
        if getattr(a, "_form", None):
            return
        ui.setup()
        if not W.webkit_available():
            a._form = True
            missing_webkit(a)
            return
        a.hold()

        def done(made):
            if made and not wid:                       # a new one opens; an edited one stays as it is
                W.launch(made)
            a.release()
        a._form = form(done, wid=wid)
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


def main(argv) -> int:
    if not argv:
        print("usage: sonata2 webapp new | <id>", file=sys.stderr)
        return 2
    if argv[0] == "new":
        return new_webapp()
    if argv[0] == "edit" and len(argv) > 1:
        return edit_webapp(argv[1])
    return open_webapp(argv[0])
