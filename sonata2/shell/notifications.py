"""Notifications, macOS Big Sur style, for the Sonata session.

- A freedesktop notification server (org.freedesktop.Notifications on the
  session bus) in the menu bar process: apps' notify-send / libnotify /
  portal notifications arrive here. If another daemon (mako, dunst...)
  already owns the name, Sonata leaves it alone.
- Banners: glass cards at the top right, under the menu bar; slide in,
  stay 5 s (critical ones until closed), click = default action, hover
  shows the close button.
- Notification Center (click the clock): the notifications, newest first,
  grouped by app, over the Today widgets (a calendar).
- Do Not Disturb (Control Center; notifications.json "dnd"): no banners at
  all (critical ones too: Chromium/Electron apps mark ordinary messages
  critical), the ones on screen go away when it's turned on; notifications
  still collect in the Notification Center."""
import os
import time
from dataclasses import dataclass, field

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import apps, config, icons, ui  # noqa: E402
from . import layer  # noqa: E402

BANNER_W = 344
BANNER_MS = 5000
TOP_GAP = 30                # under the 24 px menu bar
MAX_BANNERS = 3
DEFAULTS = {"dnd": False, "apps": {}}
# per app (System Preferences > Notifications): key = desktop entry or app name
APP_DEFAULTS = {"name": "", "allow": True, "style": "banners", "center": True, "sound": True}


def app_key(desktop: str, app_name: str) -> str:
    return (desktop or app_name or "").strip()


_LOCKED = {"stamp": None, "ids": set(), "names": set()}


def _locked_ids() -> set:
    """Desktop ids of apps kept in a locked folder: the Dock's locked folders
    and Apps' hidden (locked) ones."""
    ids = set()
    for name, key in (("dock", "folders"), ("launchpad", "pages")):
        data = config.load(name, {key: {} if key == "folders" else []}).get(key) or ({} if key == "folders" else [])
        folders = data.values() if isinstance(data, dict) else [it for page in data for it in page
                                                                 if isinstance(it, dict)]
        for f in folders:
            if isinstance(f, dict) and f.get("locked"):
                ids.update(a for a in f.get("apps", []) if isinstance(a, str))
    ids.update(a for a in config.load("launchpad", {"hidden": []}).get("hidden", []) if isinstance(a, str))
    return ids


def locked(desktop: str, app_name: str) -> bool:
    """A notification from an app in a locked folder: shown nowhere (Vini) --
    the folder keeps its apps private. Matched by the sender's desktop entry,
    else by the app's name."""
    stamp = []
    for name in ("dock", "launchpad"):
        try:
            stamp.append(os.path.getmtime(os.path.join(config.CONFIG_DIR, name + ".json")))
        except OSError:
            stamp.append(0)
    if stamp != _LOCKED["stamp"]:                  # the folders changed: read them again
        ids = _locked_ids()
        names = set()
        for i in ids:
            info = apps.lookup(i)
            if info is not None:
                names.add((info.get_name() or "").casefold())
        _LOCKED.update(stamp=stamp, ids={i.casefold() for i in ids}, names=names - {""})
    d = (desktop or "").casefold()                 # "org.telegram.desktop" is an id itself
    ids = {d, d.removesuffix(".desktop")} - {""}
    return bool((ids & _LOCKED["ids"]) or ((app_name or "").casefold() in _LOCKED["names"]))


def window_of(views, desktop: str, app_name: str):
    """The id of the notifying app's most recently used window in Wayfire's
    list-views, None when it has none."""
    ds = {(desktop or "").casefold(), (desktop or "").casefold().removesuffix(".desktop")} - {""}
    name = (app_name or "").casefold()
    best = None
    for v in views or []:
        if not isinstance(v, dict) or v.get("type", "toplevel") != "toplevel" or not v.get("app-id"):
            continue
        aid = v["app-id"]
        mine = False
        if ds and (aid.casefold() in ds or (apps.match_app_id(aid) or "").casefold() in ds):
            mine = True
        elif not ds and name:
            did = apps.match_app_id(aid)
            info = apps.lookup(did) if did else None
            mine = bool(info and (info.get_name() or "").casefold() == name)
        if mine and (best is None or v.get("last-focus-timestamp", 0) > best.get("last-focus-timestamp", 0)):
            best = v
    return best.get("id") if best else None


def bring_forward(desktop: str, app_name: str) -> bool:
    """Restore and focus the app's window; False when it has none open."""
    try:
        from ..wl.wfipc import WayfireIPC
        ipc = WayfireIPC()
        vid = window_of(ipc.call("window-rules/list-views"), desktop, app_name)
        if vid is None:
            return False
        ipc.call("wm-actions/set-minimized", {"view_id": vid, "state": False})
        ipc.call("window-rules/focus-view", {"id": vid})
        return True
    except Exception:
        return False


def app_settings(cfg: dict, key: str) -> dict:
    return dict(APP_DEFAULTS, **(cfg.get("apps", {}).get(key) or {}))

XML = """
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/><arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/><arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/><arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/><arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" name="id" direction="in"/></method>
    <method name="GetCapabilities"><arg type="as" name="caps" direction="out"/></method>
    <method name="GetServerInformation">
      <arg type="s" name="name" direction="out"/><arg type="s" name="vendor" direction="out"/>
      <arg type="s" name="version" direction="out"/><arg type="s" name="spec_version" direction="out"/>
    </method>
    <signal name="NotificationClosed"><arg type="u" name="id"/><arg type="u" name="reason"/></signal>
    <signal name="ActionInvoked"><arg type="u" name="id"/><arg type="s" name="action_key"/></signal>
  </interface>
</node>"""

ui.register("""
window.sonata-banners, window.sonata-banners > contents,
window.sonata-nc, window.sonata-nc > contents { background: none; box-shadow: none; }
/* a card leaving Notification Center (closed, Clear All): slides right and
   fades while its row collapses */
.nc-slot > * { transition: opacity 220ms %(ease_out)s, transform 220ms %(ease_out)s; }
.nc-slot.leaving > * { opacity: 0; transform: translateX(60px); }
.nt-card { background: %(glass_tint)s; border-radius: calc(%(r_dialog)s * 1.08); padding: 10px 12px 11px 10px;
  color: %(label)s; font-family: %(font)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 8px 22px rgba(0,0,0,0.18); }
.nt-card.solid { background: %(menu_bg)s; }
.nt-title { font-weight: 700; font-size: %(text_body)s; }
.nt-image { border-radius: 5px; }
.nt-body { font-size: %(text_body)s; }
.nt-app { font-size: %(text_small)s; color: %(label_secondary)s; }
.nt-time { font-size: %(text_small)s; color: %(label_secondary)s; }
button.nt-close { min-width: 18px; min-height: 18px; padding: 0; border-radius: 99px; border: none;
  background: %(menu_bg)s; color: %(label_secondary)s; -gtk-icon-size: 9px;
  box-shadow: 0 0 0 0.5px %(hairline)s, 0 1px 3px rgba(0,0,0,0.2); }
button.nt-action { min-height: 22px; padding: 0 10px; border-radius: 6px; border: none; font-size: %(text_small)s;
  background: alpha(%(label)s, 0.08); color: %(label)s; box-shadow: none; }
button.nt-action:hover { background: alpha(%(label)s, 0.14); }
.nc-head { font-weight: 700; font-size: %(text_title)s; color: white; text-shadow: 0 1px 4px rgba(0,0,0,0.45);
  margin: 6px 4px 2px 8px; }
button.nc-clear { min-height: 20px; padding: 0 9px; border-radius: 99px; border: none; font-size: %(text_small)s;
  background: %(glass_tint)s; color: %(label)s; box-shadow: 0 0 0 0.5px %(hairline)s; }
.nc-widget { background: %(glass_tint)s; border-radius: calc(%(r_dialog)s * 1.5); padding: 12px 14px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 8px 22px rgba(0,0,0,0.18); }
.nc-month { color: %(destructive)s; font-weight: 700; font-size: %(text_small)s; margin-bottom: 4px; }
.nc-wd { color: %(label_secondary)s; font-size: 10px; font-weight: 700; }
.nc-day { font-size: %(text_small)s; color: %(label)s; min-width: 22px; min-height: 22px; border-radius: 99px; }
.nc-day.weekend { color: %(label_secondary)s; }
.nc-day.today { background: %(destructive)s; color: white; font-weight: 700; }
.nc-day.has-events { font-weight: 700; }
.nc-day:hover { background: alpha(%(label)s, 0.10); }
.nc-day.today:hover { background: %(destructive)s; }
.nc-event { margin-top: 6px; }
.nc-event-bar { min-width: 3px; border-radius: 2px; margin-right: 8px; }
.nc-event-title { font-size: %(text_small)s; font-weight: 600; color: %(label)s; }
.nc-event-time { font-size: 11px; color: %(label_secondary)s; }
.nc-event-day { font-size: 10px; font-weight: 700; color: %(label_secondary)s; margin-top: 8px; }
""", key="notifications")


@dataclass
class Note:
    id: int
    app: str
    icon: str
    summary: str
    body: str
    actions: list            # [(key, label)]
    desktop: str = ""
    urgency: int = 1
    timeout: int = -1
    image: str = ""          # picture shown on the card's right (screenshots, album art)
    at: float = field(default_factory=time.time)


def _markup(text: str) -> str:
    """Notification bodies may carry simple markup (<b>, <i>, <a>); keep what
    Pango understands, escape the rest."""
    try:
        Pango.parse_markup(text, -1, "\0")
        return text
    except GLib.Error:
        return GLib.markup_escape_text(text)


def _ago(t: float) -> str:
    s = time.time() - t
    if s < 60:
        return "now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return time.strftime("%d %b", time.localtime(t))


class Notifications:
    """Server + banners + Notification Center; one per menu bar process."""

    def __init__(self, app):
        self.app = app
        self.notes = []            # newest last
        self._next = 1
        self._conn = None
        self._banners = {}         # id -> (revealer, timeout source)
        self.cfg = config.load("notifications", DEFAULTS)
        self._mon = config.watch("notifications", self._cfg_changed)
        self.win = _BannerWindow(app)
        self.nc = None
        self.listeners = []        # called when the list changes (NC open)
        Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.Notifications",
                         Gio.BusNameOwnerFlags.DO_NOT_QUEUE, self._bus_acquired, None, None)
        GLib.timeout_add(2500, self._prewarm)

    def _prewarm(self):
        """Notification Center built and drawn once, invisibly, at start:
        its first opening slides in without a hitch."""
        if self.nc is None and layer.layer_shell():
            self.nc = c = _Center(self.app, self)

            def before():
                c._rebuild()
                c.rev.set_transition_duration(0)
                c.rev.set_reveal_child(True)

            def after():
                c.rev.set_reveal_child(False)
                c.rev.set_transition_duration(250)
            layer.prewarm(c, before=before, after=after, delay_ms=1)
        return False

    def _cfg_changed(self):
        self.cfg = config.load("notifications", DEFAULTS)
        if self.dnd:                                # turned on in Settings: banners on screen go too
            self._hide_all_banners()

    @property
    def dnd(self) -> bool:
        return bool(self.cfg.get("dnd"))

    def set_dnd(self, on: bool) -> None:
        self.cfg = config.load("notifications", DEFAULTS)      # what Settings wrote is kept
        self.cfg["dnd"] = bool(on)
        config.save("notifications", self.cfg)
        if on:
            self._hide_all_banners()

    def _hide_all_banners(self):
        for nid in list(self._banners):
            self._hide_banner(nid)

    # -- D-Bus --------------------------------------------------------------------------
    def _bus_acquired(self, conn, _name):
        self._conn = conn
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        conn.register_object("/org/freedesktop/Notifications", node.interfaces[0], self._call, None, None)

    def _call(self, conn, sender, path, iface, method, params, invocation):
        if method == "GetCapabilities":
            invocation.return_value(GLib.Variant("(as)", (["body", "body-markup", "actions", "icon-static",
                                                           "persistence"],)))
        elif method == "GetServerInformation":
            invocation.return_value(GLib.Variant("(ssss)", ("Sonata", "Sonata", "2", "1.2")))
        elif method == "CloseNotification":
            self.close(params.unpack()[0], reason=3)
            invocation.return_value(None)
        elif method == "Notify":
            try:
                nid = self.notify(*params.unpack())
            except Exception:           # always answer: apps wait on the reply (freeze)
                import traceback
                traceback.print_exc()
                nid = self._next
                self._next += 1
            invocation.return_value(GLib.Variant("(u)", (nid,)))

    def notify(self, app_name, replaces, app_icon, summary, body, actions, hints, timeout) -> int:
        nid = replaces if replaces and any(n.id == replaces for n in self.notes) else self._next
        if nid == self._next:
            self._next += 1
        pairs = [(actions[i], actions[i + 1]) for i in range(0, len(actions) - 1, 2)]
        urgency = hints.get("urgency", 1)
        image = hints.get("image-path", "") or ""
        if image.startswith("file://"):
            image = GLib.filename_from_uri(image)[0]
        n = Note(nid, app_name or "", app_icon or "", summary or "", body or "", pairs,
                 hints.get("desktop-entry", "") or "", int(urgency) if isinstance(urgency, int) else 1, timeout,
                 image if image.startswith("/") else "")
        if locked(n.desktop, n.app):                    # its folder is locked: no banner, not in the list
            return nid
        key = app_key(n.desktop, n.app)
        per = app_settings(self.cfg, key)
        if key and key not in self.cfg.get("apps", {}):      # listed in Settings > Notifications
            # from the file, not memory: an older copy saved here turned Do
            # Not Disturb (just set in Settings) back off
            self.cfg = config.load("notifications", DEFAULTS)
            self.cfg.setdefault("apps", {})[key] = dict(APP_DEFAULTS, name=n.app or key)
            config.save("notifications", self.cfg)
        if not per["allow"]:
            return nid
        if per["center"]:
            self.notes = [x for x in self.notes if x.id != nid] + [n]
            self._changed()
        center_open = self.nc is not None and self.nc.get_visible()
        if per["style"] != "none" and not self.dnd and not center_open:
            self._banner(n)
        return nid

    def close(self, nid: int, reason: int = 2) -> None:
        """reason: 1 expired, 2 dismissed, 3 closed by the app."""
        self._hide_banner(nid)
        before = len(self.notes)
        self.notes = [n for n in self.notes if n.id != nid]
        if len(self.notes) != before:
            self._changed()
        if self._conn:
            self._conn.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                                   "NotificationClosed", GLib.Variant("(uu)", (nid, reason)))

    def invoke(self, n: Note, key: str = "default") -> None:
        has_default = any(k == "default" for k, _l in n.actions)
        if self._conn and (key != "default" or has_default):
            self._conn.emit_signal(None, "/org/freedesktop/Notifications", "org.freedesktop.Notifications",
                                   "ActionInvoked", GLib.Variant("(us)", (n.id, key)))
        if key == "default":
            # a click on it: its app comes forward -- its window restored and
            # focused when open, else the app opened (Vini). An app that opens
            # itself for the click (its own "default" action) isn't opened twice.
            if not bring_forward(n.desktop, n.app) and not has_default and n.desktop:
                info = apps.lookup(n.desktop)
                if info:
                    try:
                        info.launch([], None)
                    except GLib.Error:
                        pass
        self.close(n.id, 2)

    def clear(self) -> None:
        for n in list(self.notes):
            self.close(n.id, 2)

    def _changed(self):
        for cb in list(self.listeners):
            cb()

    # -- banners ------------------------------------------------------------------------------
    def _banner(self, n: Note):
        self._hide_banner(n.id, animate=False)
        card = self.card(n, banner=True)
        rev = Gtk.Revealer(child=card, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                           transition_duration=250)
        self.win.box.prepend(rev)
        self.win.show_now()
        GLib.idle_add(lambda: (rev.set_reveal_child(True), False)[1])
        src = 0
        if n.urgency != 2 and n.timeout != 0:
            src = GLib.timeout_add(n.timeout if n.timeout > 0 else BANNER_MS,
                                   lambda: (self._expire(n.id), False)[1])
        self._banners[n.id] = (rev, src)
        while len(self._banners) > MAX_BANNERS:
            self._hide_banner(next(iter(self._banners)))

    def _expire(self, nid):
        entry = self._banners.get(nid)
        if entry:
            self._banners[nid] = (entry[0], 0)
        self._hide_banner(nid)          # stays in the Notification Center

    def _hide_banner(self, nid, animate=True):
        entry = self._banners.pop(nid, None)
        if not entry:
            return
        rev, src = entry
        if src:
            GLib.source_remove(src)

        def gone():
            if rev.get_parent() is not None:
                self.win.box.remove(rev)
            if not self.win.box.get_first_child():
                self.win.set_visible(False)
            return False
        if animate:
            rev.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
            rev.set_reveal_child(False)
            GLib.timeout_add(260, gone)
        else:
            gone()

    # -- a notification card (banner and Notification Center) ---------------------------------
    def card(self, n: Note, banner=False) -> Gtk.Widget:
        over = Gtk.Overlay()
        box = Gtk.Box(spacing=10, css_classes=["nt-card"] + ([] if ui.theme.glass() else ["solid"]))
        box.set_size_request(BANNER_W, -1)
        img = Gtk.Image(pixel_size=36, valign=Gtk.Align.START)
        self._icon(img, n)
        box.append(img)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True)
        top = Gtk.Box(spacing=6)
        top.append(Gtk.Label(label=n.summary or n.app, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                             css_classes=["nt-title"]))
        when = Gtk.Label(label=_ago(n.at), css_classes=["nt-time"], valign=Gtk.Align.START)
        top.append(when)
        col.append(top)
        if n.body:
            col.append(Gtk.Label(label=_markup(n.body), use_markup=True, xalign=0, wrap=True,
                                 wrap_mode=Pango.WrapMode.WORD_CHAR, lines=3 if banner else 5,
                                 ellipsize=Pango.EllipsizeMode.END, max_width_chars=36, css_classes=["nt-body"]))
        acts = [(k, lbl) for k, lbl in n.actions if k != "default" and lbl]
        if acts:
            row = Gtk.Box(spacing=6, margin_top=6)
            for k, lbl in acts[:3]:
                b = Gtk.Button(label=lbl, css_classes=["nt-action"])
                b.connect("clicked", lambda _b, k=k: self.invoke(n, k))
                row.append(b)
            col.append(row)
        box.append(col)
        if n.image:
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, valign=Gtk.Align.CENTER, css_classes=["nt-image"])
            pic.set_filename(n.image)
            pic.set_size_request(64, 40)
            box.append(pic)
        from ..ui.fixed import FixedWidth
        over.set_child(FixedWidth(box, BANNER_W))            # a long title or body never widens it
        click = Gtk.GestureClick()
        click.connect("released", lambda g, *_: g.get_current_button() == 1 and self.invoke(n))
        box.add_controller(click)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["nt-close"], halign=Gtk.Align.START,
                           valign=Gtk.Align.START, margin_start=0, margin_top=0, visible=False)
        close.connect("clicked", lambda *_: self.close(n.id, 2))
        over.add_overlay(close)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: close.set_visible(True))
        hover.connect("leave", lambda *_: close.set_visible(False))
        over.add_controller(hover)
        over.set_margin_start(6)
        over.set_margin_top(6)
        return over

    def _icon(self, img, n: Note):
        info = apps.lookup(n.desktop) if n.desktop else None
        if info is None and n.app:
            info = apps.lookup(n.app.lower().replace(" ", "-")) or apps.lookup(n.app)
        if info:
            icons.set_image(img, icons.app_icon(info))
        elif n.icon.startswith("/") or n.icon.startswith("file://"):
            img.set_from_file(n.icon[7:] if n.icon.startswith("file://") else n.icon)
        elif n.icon:
            img.set_from_icon_name(n.icon)
        else:
            img.set_from_icon_name("dialog-information")

    # -- Notification Center ------------------------------------------------------------------
    def toggle_center(self):
        if self.nc and self.nc.get_visible():
            self.nc.hide_center()
            return
        for nid in list(self._banners):          # the banners move into the Center
            self._hide_banner(nid, animate=False)
        if self.nc is None:
            self.nc = _Center(self.app, self)
        self.nc.show_center()


class _BannerWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title="Notifications", decorated=False, resizable=True)
        self.add_css_class("sonata-banners")
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_end=2, margin_bottom=10)
        self.set_child(self.box)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-notifications")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_anchor(self, LS.Edge.RIGHT, True)
            LS.set_margin(self, LS.Edge.TOP, 2)       # below the menu bar (its exclusive zone)
            LS.set_margin(self, LS.Edge.RIGHT, 8)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

    def show_now(self):
        if not self.get_visible():
            self.set_default_size(1, 1)          # shrink to the content again
            self.present()


class _Center(Gtk.Window):
    """Full-screen transparent layer: the column on the right; a click
    anywhere else (or Escape) closes it."""

    def __init__(self, app, owner: Notifications):
        super().__init__(application=app, title="Notification Center", decorated=False)
        self.add_css_class("sonata-nc")
        self.owner = owner
        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, transition_duration=250,
                                halign=Gtk.Align.END, valign=Gtk.Align.FILL)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_width=True)
        self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=TOP_GAP - 4,
                           margin_end=10, margin_bottom=12, margin_start=6)
        scroller.set_child(self.col)
        self.rev.set_child(scroller)
        self.set_child(self.rev)
        outside = Gtk.GestureClick()
        outside.connect("released", self._clicked)
        self.add_controller(outside)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.hide_center(), True)[1] if k == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-notification-center")
            LS.set_layer(self, LS.Layer.OVERLAY)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, e, True)
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND)
            self.keyboard_mode = LS.KeyboardMode.ON_DEMAND        # (layer.prewarm restores it)
        self._slots = {}             # note id -> Revealer around its card
        self._settle = 0
        owner.listeners.append(self._changed)

    def _clicked(self, g, _n, x, y):
        # only the cards and widgets keep it open: the empty parts of the
        # column (it spans the full height, over the menu bar's clock and
        # icons too) close it like any other click outside
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w.get_parent() is not self.col:
            w = w.get_parent()
        if w is None:
            self.hide_center()

    def show_center(self):
        # already built (at start, and on every change while closed): only a new
        # day needs the calendar redrawn -- opening is then instant
        if getattr(self, "_built_for", None) != self._state_key():
            self._rebuild()
        self.present()
        GLib.idle_add(lambda: (self.rev.set_reveal_child(True), False)[1])

    def hide_center(self):
        self.rev.set_reveal_child(False)
        GLib.timeout_add(260, lambda: (self.set_visible(False), False)[1])

    def _changed(self):
        """Closed notes leave with an animation (staggered for Clear All);
        anything else rebuilds the column at once."""
        ids = {n.id for n in self.owner.notes}
        gone = [nid for nid in self._slots if nid not in ids]
        new = [n.id for n in list(reversed(self.owner.notes))[:40] if n.id not in self._slots]
        if not self.get_visible() or new or not gone:
            self._rebuild()
            return
        for i, nid in enumerate(gone):
            slot = self._slots.pop(nid)

            def leave(slot=slot):
                slot.add_css_class("leaving")
                GLib.timeout_add(120, lambda: (slot.set_reveal_child(False), False)[1])
                return False
            GLib.timeout_add(1 + min(i, 8) * 45, leave)
        if self._settle:
            GLib.source_remove(self._settle)
        wait = 120 + 260 + min(len(gone) - 1, 8) * 45 + 20

        def settle():
            self._settle = 0
            self._rebuild()                     # header ("No Notifications"), Clear All
            return False
        self._settle = GLib.timeout_add(wait, settle)

    def _state_key(self):
        return (tuple((n.id, getattr(n, "time", None)) for n in self.owner.notes),
                GLib.DateTime.new_now_local().format("%Y-%m-%d"), _calendar_stamp())

    def _rebuild(self):
        self._built_for = self._state_key()
        while self.col.get_first_child():
            self.col.remove(self.col.get_first_child())
        notes = list(reversed(self.owner.notes))
        head = Gtk.Box()
        head.append(Gtk.Label(label="Notifications" if notes else "No Notifications", xalign=0, hexpand=True,
                              css_classes=["nc-head"]))
        if notes:
            clear = Gtk.Button(label="Clear All", css_classes=["nc-clear"], valign=Gtk.Align.CENTER)
            clear.connect("clicked", lambda *_: self.owner.clear())
            head.append(clear)
        self.col.append(head)
        self._slots = {}
        for n in notes[:40]:
            slot = Gtk.Revealer(child=self.owner.card(n), reveal_child=True, css_classes=["nc-slot"],
                                transition_type=Gtk.RevealerTransitionType.SLIDE_UP, transition_duration=260)
            self._slots[n.id] = slot
            self.col.append(slot)
        # Today widgets (Big Sur: below the notifications)
        cal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["nc-widget"], margin_start=6,
                      margin_top=8)
        events = _upcoming()
        cal.append(_month(events, self._open_calendar))
        cal.append(_up_next(events))
        # macOS: clicking the Calendar widget opens Calendar
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._open_calendar(None))
        cal.add_controller(click)
        from ..ui.fixed import FixedWidth
        self.col.append(FixedWidth(cal, BANNER_W + 6))        # (its margin_start); an event title never widens it

    def _open_calendar(self, date) -> None:
        """Calendar, on `date` (a datetime.date) in the Day view, or as it was."""
        from ..__main__ import self_command
        args = self_command().split() + ["calendar"]
        if date is not None:
            args.append("sonata-date:" + date.isoformat())
        self.hide_center()
        try:
            GLib.spawn_async(args, flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            pass


def _calendar_folder() -> str:
    from .. import userdata
    return userdata.folder("calendar")


def _calendar_stamp():
    """When Calendar's files last changed (the widget redraws after an edit)."""
    try:
        folder = _calendar_folder()
        return max((os.path.getmtime(os.path.join(folder, n)) for n in os.listdir(folder)), default=0)
    except OSError:
        return 0


def _upcoming(days: int = 31) -> list:
    """This month's and the next days' event occurrences, from Calendar's
    own files (none written here); hidden calendars left out."""
    import datetime as dt
    try:
        from ..calendar import ics, model
        from .. import config
        folder = _calendar_folder()
        if not os.path.isdir(folder) or not any(n.endswith(".ics") for n in os.listdir(folder)):
            return []
        store = model.Store(folder)
        store.load()
        hidden = set(config.load("calendar", {"hidden": []}).get("hidden", []))
        colors = {c.id: model.PALETTE_HEX.get(c.color, "#007aff") for c in store.calendars}
        today = dt.date.today()
        a = dt.datetime(today.year, today.month, 1)
        b = dt.datetime.combine(today, dt.time()) + dt.timedelta(days=days)
        return [(o, colors.get(o.event.calendar, "#007aff"))
                for o in ics.expand(store.visible_events(hidden), a, b)]
    except Exception:           # a broken file must not take the menu bar down
        return []


def _up_next(events) -> Gtk.Widget:
    """Big Sur's Up Next: today's and tomorrow's next events (at most 3)."""
    import datetime as dt
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    now = dt.datetime.now()
    today = now.date()
    soon = [(o, c) for o, c in events if o.end > now and o.start.date() <= today + dt.timedelta(days=1)][:3]
    if not soon:
        box.append(Gtk.Label(label="No more events today", xalign=0, css_classes=["nc-event-day"]))
        return box
    shown_day = None
    for o, color in soon:
        day = o.start.date() if o.start.date() >= today else today
        if day != shown_day:
            shown_day = day
            box.append(Gtk.Label(label="TODAY" if day == today else "TOMORROW", xalign=0,
                                 css_classes=["nc-event-day"]))
        row = Gtk.Box(css_classes=["nc-event"])
        bar = Gtk.Box(css_classes=["nc-event-bar"])
        bar.set_size_request(3, -1)
        prov = Gtk.CssProvider()
        prov.load_from_string(f"box {{ background-color: {color}; }}")
        bar.get_style_context().add_provider(prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        row.append(bar)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        texts.append(Gtk.Label(label=o.event.summary or "New Event", xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               hexpand=True, css_classes=["nc-event-title"]))
        when = "All day" if o.event.all_day else \
            f"{o.start.strftime('%H:%M')} – {o.end.strftime('%H:%M')}"
        texts.append(Gtk.Label(label=when, xalign=0, css_classes=["nc-event-time"]))
        row.append(texts)
        box.append(row)
    return box


def _month(events=(), on_day=None) -> Gtk.Widget:
    """Big Sur Calendar widget (small): month in red capitals, weekday
    initials, this month's days, today in a red circle; days with events
    in bold; a day opens Calendar on it."""
    import calendar as pycal
    import datetime as dt
    now = GLib.DateTime.new_now_local()
    y, m, today = now.get_year(), now.get_month(), now.get_day_of_month()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(Gtk.Label(label=now.format("%B").upper(), xalign=0, css_classes=["nc-month"]))
    grid = Gtk.Grid(column_homogeneous=True, row_spacing=1, column_spacing=2)
    first = pycal.SUNDAY
    cal = pycal.Calendar(firstweekday=first)
    busy = {o.start.day for o, _c in events if (o.start.year, o.start.month) == (y, m)}
    for i, d in enumerate(("S", "M", "T", "W", "T", "F", "S")):
        grid.attach(Gtk.Label(label=d, css_classes=["nc-wd"]), i, 0, 1, 1)
    for r, week in enumerate(cal.monthdayscalendar(y, m), start=1):
        for c, day in enumerate(week):
            if not day:
                continue
            classes = ["nc-day"] + (["today"] if day == today else []) + (["weekend"] if c in (0, 6) else [])
            if day in busy:
                classes.append("has-events")
            lab = Gtk.Label(label=str(day), css_classes=classes, halign=Gtk.Align.CENTER)
            if on_day is not None:
                click = Gtk.GestureClick()
                click.connect("released", lambda g, *_a, d=dt.date(y, m, day): (
                    g.set_state(Gtk.EventSequenceState.CLAIMED), on_day(d)))
                lab.add_controller(click)
            grid.attach(lab, c, r, 1, 1)
    box.append(grid)
    return box
