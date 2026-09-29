"""Entry point: `python3 -m sonata2 <dock|launchpad|topbar|gallery> [options]`.

Each shell component is its own process (a crash in one doesn't take the
others down). `launchpad` is single-instance: running it again toggles it.

Development / screenshots:
  --preview           run in a normal window over a sample wallpaper
  --dark / --light    force the appearance
  --set KEY=VALUE     override a Dock setting for this run (not saved)
  --label N / --menu N [--submenu] / --stack / --magnify X   (Dock)
  --search TEXT / --folder / --jiggle / --background           (Launchpad)
  --menu N                                                     (top bar, gallery)
  --page ID                                                    (settings)
  sonata2 files [FOLDER...]                                    (Files)
  sonata2 restart [dock topbar launchpad wallpaper]            (reload edited code)
  sonata2 key volume-up|volume-down|volume-mute|brightness-up|brightness-down  (media keys + HUD)
  sonata2 key play-pause|next|previous                        (media player keys, MPRIS)
  sonata2 keep <component> [args]                             (session: restart it if it crashes)
  sonata2 doctor                                              (is this computer ready for Sonata?)
  sonata2 screenshot [area]                                    (Super+Shift+3 / 4)"""
import argparse
import json
import os
import sys

from .shell import layer

APP_IDS = {"dock": "io.github.vinioliveiras.sonata2.dock",
           "autostart": "io.github.vinioliveiras.sonata2.autostart",
           "settings": "io.github.vinioliveiras.sonata2.settings",
           "wallpaper": "io.github.vinioliveiras.sonata2.wallpaper",
           "launchpad": "io.github.vinioliveiras.sonata2.launchpad",
           "topbar": "io.github.vinioliveiras.sonata2.topbar",
           "gallery": "io.github.vinioliveiras.sonata2.gallery",
           "files": "io.github.vinioliveiras.sonata2.files",
           "lock": "io.github.vinioliveiras.sonata2.lock",
           "spotlight": "io.github.vinioliveiras.sonata2.spotlight"}
# Shell surfaces (never shown as running apps in the Dock); Files and
# Settings are ordinary apps.
SHELL_IDS = {APP_IDS[k] for k in ("dock", "autostart", "wallpaper", "launchpad", "topbar", "gallery", "lock",
                                   "spotlight")}
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def self_command() -> str:
    """How to start Sonata again (desktop entries, autostart): the installed
    `sonata2` launcher, or this interpreter + this clone."""
    launcher = os.environ.get("SONATA2_LAUNCHER")
    if launcher and os.path.exists(launcher):
        return launcher
    return f"env PYTHONPATH={REPO} {sys.executable} -m sonata2"


def _later(ms, fn):
    from gi.repository import GLib
    GLib.timeout_add(ms, lambda: (fn(), False)[1])


def run_gallery(app, args, ui):
    from .ui.gallery import GalleryWindow, sample_menu
    win = GalleryWindow(app)
    win.present()
    if args.menu >= 0:
        def open_menu():
            pop = sample_menu(win.menu_anchor)
            if args.submenu:
                _later(300, lambda: ui.menu.open_submenu(pop, "Options"))
        _later(500, open_menu)


def run_dock(app, args, ui):
    from gi.repository import Gdk
    from .shell import dock, dock_menu, launchpad
    from .wl.toplevels import ToplevelManager
    launchpad.launchpad_desktop_file(self_command())   # Launchpad as a Dock app
    from .settings.app import settings_desktop_file
    settings_desktop_file(self_command())             # System Settings in Launchpad
    from .files import files_desktop_file
    files_desktop_file(self_command())                # Files in Launchpad
    cfg = dock.load_config()
    if not cfg.get("launchpad_added"):                        # once: pin it after Finder
        cfg["launchpad_added"] = True
        if "sonata2-launchpad" not in cfg["pinned"]:
            cfg["pinned"].insert(min(1, len(cfg["pinned"])), "sonata2-launchpad")
        dock.config.save("dock", cfg)
    for kv in args.set:
        k, v = kv.split("=", 1)
        try:
            cfg[k] = json.loads(v)
        except ValueError:
            cfg[k] = v
    dock.load_css(cfg)
    manager = ToplevelManager(Gdk.Display.get_default(), ignore_app_ids=SHELL_IDS)
    if args.preview:
        from .shell.preview import PreviewWindow
        win = PreviewWindow(app, dock.Dock(cfg, manager))
    else:
        win = dock.DockWindow(app, cfg, manager)
    win.present()
    d = win.dock
    from .shell import layer, monitors
    if not args.preview and layer.layer_shell():
        # Settings > Dock > "Show the Dock on every display": a Dock of its own
        # on each other display (same apps; the main one does the extras)
        def create(m):
            if m is monitors.main() or not dock.load_config().get("all_displays"):
                return None
            w = dock.DockWindow(app, dock.load_config(), manager, monitor=m)
            w.present()
            return w

        def destroy(w):
            if w is not None:
                w.dock.detach()
                w.destroy()
        extra = monitors.each(create, destroy)
        monitors.on_main_changed(lambda _m: extra.rebuild())
        state_all = {"on": bool(cfg.get("all_displays"))}

        def cfg_changed():
            on = bool(dock.load_config().get("all_displays"))
            if on != state_all["on"]:
                state_all["on"] = on
                extra.rebuild()
        app._sonata_dock_cfg = dock.config.watch("dock", cfg_changed)
    # Launchpad open: the Dock goes above it (macOS keeps the Dock visible over
    # Launchpad, and apps can be dragged onto it); back below windows after.
    from gi.repository import Gio, GLib as _GLib
    act = Gio.SimpleAction.new("above", _GLib.VariantType.new("b"))
    act.connect("activate", lambda _a, v: win.set_above(v.get_boolean()) if hasattr(win, "set_above") else None)
    app.add_action(act)
    if args.label >= 0:
        tiles = d.all_tiles()
        if args.label < len(tiles):
            _later(300, lambda: tiles[args.label].label.popup())
    if args.stack and d.stacks.tiles():
        _later(400, lambda: d.stacks.open_panel(d.stacks.tiles()[0]))
    if args.magnify >= 0:
        def magnify():
            d.cfg["magnification"] = True
            d._mag_pos, d._mag_strength = args.magnify, 1.0
            d._apply_magnification()
        _later(300, magnify)
    if args.menu >= 0:
        tiles = d.app_tiles() + [d.trash]
        if args.menu < len(tiles):
            t = tiles[args.menu]

            def open_menu():
                pop = dock_menu.trash_menu(t) if t is d.trash else dock_menu.app_menu(d, t.key, t)
                if args.submenu:
                    _later(300, lambda: ui.menu.open_submenu(pop, "Options"))
            _later(400, open_menu)


def run_launchpad(app, args, ui, state):
    from gi.repository import Gdk
    from .shell import launchpad
    win = state.get("win")
    if win is not None:            # second launch: toggle
        win.toggle()
        return
    win = state["win"] = launchpad.Launchpad(app)
    app.hold()                     # stays resident while hidden: instant opening
    if not args.preview:
        # macOS: an app opened any other way (Dock, a shortcut, Spotlight)
        # closes Launchpad -- a new window, or another one activated
        from .wl.toplevels import ToplevelManager
        mgr = state["toplevels"] = ToplevelManager(Gdk.Display.get_default(), ignore_app_ids=SHELL_IDS)
        seen = {"wins": set(mgr.toplevels), "active": {t for t in mgr.toplevels if t.activated}}

        def changed():
            wins = set(mgr.toplevels)
            active = {t for t in wins if t.activated}
            fresh = (wins - seen["wins"]) or (active - seen["active"])
            seen["wins"], seen["active"] = wins, active
            if fresh and win.get_visible() and win.bin.progress > 0.5:
                win.close_launchpad()
        mgr.listeners.append(changed)
    if args.preview:
        from .shell.preview import _wallpaper
        w, h = (int(v) for v in os.environ.get("PREVIEW_SIZE", "1280x800").split("x"))
        win.set_default_size(w, h)
        walls = _wallpaper(w, h, ui.is_dark())
        if walls:
            win.bin.backdrop = Gdk.Texture.new_from_filename(walls[1])
    if args.background:            # login: start resident, hidden
        win.set_visible(False)
        return
    win.open_launchpad()
    if args.search:
        _later(400, lambda: win.search.set_text(args.search))
    if args.folder:
        folders = [it for p in win.model.pages for it in p if launchpad.M.is_folder(it)]
        if folders:
            _later(500, lambda: win._open_folder(folders[0]))
    if args.jiggle:
        _later(400, lambda: win.set_jiggle(True))


def run_settings(app, args, ui, state):
    from .settings.app import Settings
    win = state.get("win")
    if win is None:
        win = state["win"] = Settings(app, args.page or "wifi")
    elif args.page:
        win.select(args.page)
    win.present()


def run_files(app, uris, ui):
    """A new Files window per launch (like Finder's File > New Window)."""
    from .files.window import FilesWindow
    from gi.repository import Gio
    for uri in uris or [None]:
        reveal = None
        if uri:
            f = Gio.File.new_for_uri(uri)
            if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) not in (Gio.FileType.DIRECTORY,
                                                                              Gio.FileType.UNKNOWN):
                reveal, uri = f.get_basename(), f.get_parent().get_uri()   # a file: show it selected
        win = FilesWindow(app, uri)
        win.present()
        if reveal:
            win._select_when_listed(reveal)
    view = os.environ.get("SONATA_PREVIEW_VIEW")        # screenshots: view + selection
    if view:
        win.set_view(view, save=False)
    sel = os.environ.get("SONATA_PREVIEW_SELECT")
    if sel:
        v = win.view
        target = v.columns[0].selection if view == "columns" else v.selection
        _later(800, lambda: [target.select_item(int(i), view == "columns") for i in sel.split(",")])
    menu = os.environ.get("SONATA_PREVIEW_MENU")         # "bg" or an item index
    rename = os.environ.get("SONATA_PREVIEW_RENAME")

    def cell_point(i):
        from gi.repository import Graphene
        v = win.view
        info = v.model.get_item(i)
        box = v._cells.get(info)
        ok, pt = box.compute_point(v.widget, Graphene.Point().init(30, 30))
        return info, pt.x, pt.y
    if menu:
        def show_menu():
            if menu == "bg":
                win._context_menu(win.view, win.view.widget, win.view.widget.get_width() - 120,
                                  win.view.widget.get_height() - 160)
            else:
                _info, x, y = cell_point(int(menu))
                win._context_menu(win.view, win.view.widget, x, y)
        _later(1200, show_menu)
    if rename:
        _later(1200, lambda: win.view.begin_rename(win.view.model.get_item(int(rename)), win._commit_rename))
    if os.environ.get("SONATA_PREVIEW_QL"):
        _later(1300, win.toggle_quicklook)
    if os.environ.get("SONATA_PREVIEW_INFO"):
        _later(1300, win.get_info)
    q = os.environ.get("SONATA_PREVIEW_SEARCH")
    if q:
        _later(600, lambda: (win._open_search(), win.search.set_text(q)))


def _serve_file_manager(app, ui) -> None:
    """Files answers apps' "Show in Folder" (org.freedesktop.FileManager1)."""
    from gi.repository import Gio
    from .files import filemanager1

    def props(uris):
        from .files import folder
        from .files.quicklook import GetInfo
        for u in uris:
            f = Gio.File.new_for_uri(u)
            try:
                info = f.query_info(folder.ATTRS, Gio.FileQueryInfoFlags.NONE, None)
            except Exception:           # noqa: BLE001 -- a missing file: nothing to show
                continue
            info.set_attribute_object("sonata::file", f)
            w = GetInfo(None, info)
            w.set_application(app)
            w.present()
    filemanager1.own(app, lambda uris: run_files(app, uris, ui), lambda uris: run_files(app, uris, ui), props)


def run_spotlight(app, args, ui, state):
    """Single instance, resident: running it again toggles it."""
    from .shell.spotlight import Spotlight
    win = state.get("win")
    if win is not None:
        win.toggle()
        return
    win = state["win"] = Spotlight(app)
    app.hold()
    if args.background:
        return
    win.open_spotlight()
    if args.search:
        _later(300, lambda: win.entry.set_text(args.search))


def run_lock(app, args, ui, state):
    """Lock the session (once: a second `sonata2 lock` does nothing)."""
    if state.get("lock"):
        return
    from . import pam
    from .shell import layer as L
    try:
        import gi
        gi.require_version("Gtk4SessionLock", "1.0")
        from gi.repository import Gtk4SessionLock as SL
        supported = SL.is_supported() and L.layer_shell() is not None
    except (ImportError, ValueError):
        supported = False
    if not supported or not pam.available():
        # never lock without a way to unlock
        print("sonata2: can't lock (needs gtk4-layer-shell >= 1.1 session lock and PAM)", file=sys.stderr)
        return
    from .shell.lock import LockScreen
    app.hold()
    state["lock"] = LockScreen(app)


def run_wallpaper(app, args, ui):
    from .shell.wallpaper import WallpaperWindow
    from .shell import layer, monitors
    if not layer.layer_shell():                     # a plain window (no layer-shell): just one
        WallpaperWindow(app).present()
        return
    app.hold()

    def create(m):
        w = WallpaperWindow(app, m, desktop=m is monitors.main())
        w.present()
        return w
    walls = monitors.each(create, lambda w: w.destroy())
    monitors.on_main_changed(lambda _m: walls.rebuild())     # the desktop icons follow the main display


def run_topbar(app, args, ui):
    from gi.repository import Gio, GLib
    from .shell import layer, monitors, topbar
    main_mon = None if args.preview or not layer.layer_shell() else monitors.main()
    win = topbar.TopBarWindow(app, preview=args.preview, monitor=main_mon)
    win.present()
    if main_mon is not None:
        monitors.ensure_refresh()                # displays run at their highest refresh rate

        def create(m):                           # the other displays: a menu bar each (macOS)
            if m is monitors.main():
                return None
            w = topbar.TopBarWindow(app, monitor=m, manager=win.manager, secondary=True)
            w.present()
            return w

        def destroy(w):
            if w is not None:
                w.bar.stop()
                w.destroy()
        others = monitors.each(create, destroy)

        def main_changed(m):                     # the main bar moves; the others follow
            LS = layer.layer_shell()
            win.set_visible(False)
            LS.set_monitor(win, m)
            win.set_visible(True)
            others.rebuild()
        monitors.on_main_changed(main_changed)
    # HUD for the media keys (`sonata2 key ...` activates this over D-Bus)
    from .shell.osd import OSD
    osd = {}

    def show(_a, param):
        kind, level, muted = (param.get_string().split(":") + ["0", "0"])[:3]
        if "w" not in osd:
            osd["w"] = OSD(app)
        osd["w"].show_level(kind, int(level), muted == "1")
        win.bar._poll_soon()                     # menu bar icon follows
    act = Gio.SimpleAction.new("osd", GLib.VariantType.new("s"))
    act.connect("activate", show)
    app.add_action(act)
    # App switcher (Super+Tab binding -> gdbus -> this action)
    sw = {}

    def switch(_a, param):
        if win.bar.manager is None:
            return
        if "w" not in sw:
            from .shell.switcher import Switcher
            if not hasattr(win.bar, "mru"):
                win.bar.mru = []
            sw["w"] = Switcher(app, win.bar.manager, win.bar.mru)
        sw["w"].mru = getattr(win.bar, "mru", [])
        sw["w"].step(-1 if param.get_string() == "prev" else 1)
    act = Gio.SimpleAction.new("switcher", GLib.VariantType.new("s"))
    act.connect("activate", switch)
    app.add_action(act)
    # Screenshots / recording (sonata2 screenshot ... -> this action):
    # "screen" | "area" | "toolbar" | "stop"
    from .shell.capture import Capture
    win.bar.capture = Capture(app, win.bar)

    def capture(_a, param):
        from . import config
        from .shell.capture import DEFAULTS
        what, cap = param.get_string(), win.bar.capture
        if what == "toolbar":
            cap.show_toolbar()
        elif what == "stop":
            cap.stop_recording()
        else:                          # the keyboard shortcuts never wait for the timer
            cap.run(what, dict(config.load("capture", DEFAULTS), timer=0))
    # Character Viewer (Ctrl+Super+Space)
    emo = {}

    def emoji(*_a):
        if "w" not in emo:
            from .shell.emoji import EmojiPicker
            emo["w"] = EmojiPicker(app)
        emo["w"].open()
    act = Gio.SimpleAction.new("emoji", None)
    act.connect("activate", emoji)
    app.add_action(act)
    act = Gio.SimpleAction.new("capture", GLib.VariantType.new("s"))
    act.connect("activate", capture)
    app.add_action(act)
    if args.menu >= 0:
        _later(600, lambda: win.bar.open_menu(args.menu))


SHELL_COMPONENTS = ("wallpaper", "dock", "topbar", "launchpad", "spotlight")


def _reload_wayfire_config() -> None:
    """The session's Wayfire config again from the current defaults (+ your
    Settings changes): Wayfire watches that file and applies it at once, so
    blur, animation, key or plugin changes need no new login."""
    import subprocess
    run_cfg = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-wayfire.ini")
    script = os.path.join(REPO, "tools", "wayfire-config.sh")
    if not (os.path.exists(run_cfg) and os.path.exists(script)):
        return                                    # not a Sonata session (dev session, preview)
    cfg_home = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "sonata2")
    user, mark = os.path.join(cfg_home, "wayfire.ini"), os.path.join(cfg_home, ".wayfire.ini.installed")
    src = os.path.join(REPO, "config", "wayfire.ini")
    try:
        if os.path.exists(user) and not (os.path.exists(mark) and open(user, "rb").read() == open(mark, "rb").read()):
            src = user                            # a copy you edited
    except OSError:
        pass
    tmp = run_cfg + ".new"
    if subprocess.run(["bash", script, src, tmp], check=False).returncode == 0:
        with open(tmp, encoding="utf-8") as f:            # rewritten in place: Wayfire keeps watching it
            text = f.read()
        # the plugin list stays as the session started: Wayfire only reads a
        # new plugin's XML at start, and loading one without it can abort it
        import re
        with open(run_cfg, encoding="utf-8") as f:
            old = re.search(r"^plugins\s*=.*$", f.read(), re.M)
        if old:
            text = re.sub(r"^plugins\s*=.*$", lambda _m: old.group(0), text, count=1, flags=re.M)
        with open(run_cfg, "w", encoding="utf-8") as f:
            f.write(text)
        os.remove(tmp)


def restart(names) -> int:
    """`sonata2 restart [dock topbar ...]`: stop those shell components (all
    by default) and start them again with the current code -- to see edits
    live in a running Sonata session."""
    import subprocess
    import time
    if not names:
        _reload_wayfire_config()
    names = [n for n in names if n in SHELL_COMPONENTS] or list(SHELL_COMPONENTS)
    for n in names:                       # the keepers first, so they don't start it again
        subprocess.run(["pkill", "-f", "--", rf"-m sonata2 keep {n}( |$)"], check=False)
        subprocess.run(["pkill", "-f", "--", rf"-m sonata2 {n}( |$)"], check=False)
    time.sleep(0.6)
    cmd = self_command().split()
    for n in names:
        extra = ["--background"] if n in ("launchpad", "spotlight") else []
        subprocess.Popen(cmd + ["keep", n] + extra, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"restarted {n}")
    return 0


KEYS = {  # media keys: (what changes, step); macOS uses 16 steps
    "volume-up": ("volume", +6), "volume-down": ("volume", -6), "volume-mute": ("volume", 0),
    "brightness-up": ("brightness", +6), "brightness-down": ("brightness", -6),
    "play-pause": ("media", "PlayPause"), "next": ("media", "Next"), "previous": ("media", "Previous"),
}


def _media(method: str) -> int:
    """Play/pause, next, previous keys: the player that is playing (else
    the first one) over MPRIS."""
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                              None, None, Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
    except GLib.Error:
        return 1
    players = [n for n in names if n.startswith("org.mpris.MediaPlayer2.")]

    def status(n):
        try:
            return bus.call_sync(n, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                 GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
                                 None, Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
        except GLib.Error:
            return ""
    target = next((n for n in players if status(n) == "Playing"), players[0] if players else None)
    if not target:
        return 1
    try:
        bus.call_sync(target, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", method,
                      None, None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error:
        return 1
    return 0


def key(name: str) -> int:
    """`sonata2 key volume-up` (Wayfire key bindings): change the level, then
    ask the menu bar process to show the HUD. No GTK in this process."""
    from .backend import system
    kind, step = KEYS.get(name, (None, 0))
    if kind == "media":
        return _media(step)
    if kind == "volume":
        cur = system.volume() or (0, False)
        if name == "volume-mute":
            system.set_volume(muted=not cur[1])
            level, muted = cur[0], not cur[1]
        else:
            level, muted = max(0, min(100, cur[0] + step)), False
            system.set_volume(level, False)
            from . import sounds
            sounds.play("volume")                      # macOS: feedback when the volume changes
    elif kind == "brightness":
        level, muted = max(1, min(100, (system.brightness() or 50) + step)), False
        system.set_brightness(level)
    else:
        return 2
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(APP_IDS["topbar"], "/" + APP_IDS["topbar"].replace(".", "/"), "org.freedesktop.Application",
                      "ActivateAction", GLib.Variant("(sava{sv})", ("osd", [GLib.Variant("s", f"{kind}:{level}:{int(muted)}")], {})),
                      None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error:
        pass                            # no menu bar: the level still changed
    return 0


def _topbar_action(name: str, param: str) -> bool:
    from gi.repository import Gio, GLib
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync(APP_IDS["topbar"], "/" + APP_IDS["topbar"].replace(".", "/"), "org.freedesktop.Application",
                      "ActivateAction", GLib.Variant("(sava{sv})", (name, [GLib.Variant("s", param)], {})),
                      None, Gio.DBusCallFlags.NONE, 1000, None)
        return True
    except GLib.Error:
        return False


def screenshot(mode: str) -> int:
    """macOS screenshots. The menu bar does it when it runs (floating
    thumbnail, Super+Shift+5 toolbar, recording); otherwise
    "Screenshot 2026-09-29 at 01.52.10.png" on the Desktop and a notification."""
    if _topbar_action("capture", mode):
        return 0
    if mode not in ("screen", "area"):
        return 1
    area = mode == "area"
    import shutil
    import subprocess
    from gi.repository import Gio, GLib
    if not shutil.which("grim"):
        return 1
    desk = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP) or GLib.get_home_dir()
    name = GLib.DateTime.new_now_local().format("Screenshot %Y-%m-%d at %H.%M.%S.png")
    path = os.path.join(desk, name)
    cmd = ["grim"]
    if area:
        if not shutil.which("slurp"):
            return 1
        sel = subprocess.run(["slurp"], capture_output=True, text=True)
        if sel.returncode != 0 or not sel.stdout.strip():
            return 0                            # Escape: cancelled
        cmd += ["-g", sel.stdout.strip()]
    if subprocess.run(cmd + [path]).returncode != 0:
        return 1
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Screenshot", 0, path, "Screenshot", name, [],
                                                       {"desktop-entry": GLib.Variant("s", APP_IDS["files"]),
                                                        "image-path": GLib.Variant("s", path)}, -1)),
                      None, Gio.DBusCallFlags.NONE, 1000, None)
    except GLib.Error:
        pass
    return 0


def _write_env_report() -> None:
    """~/.cache/sonata2/env.txt: library versions of this machine, so the
    development environment can match them (written by the menu bar)."""
    import platform
    import shutil
    import subprocess
    lines = [f"python {platform.python_version()}"]
    try:
        import gi
        lines.append(f"pygobject {gi.__version__}")
        from gi.repository import Adw, GLib, Gtk
        lines += [f"gtk {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
                  f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}",
                  f"glib {GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}"]
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
            from gi.repository import Gtk4LayerShell as L
            lines.append(f"gtk4-layer-shell {L.get_major_version()}.{L.get_minor_version()}.{L.get_micro_version()}")
        except (ValueError, ImportError):
            lines.append("gtk4-layer-shell missing")
    except Exception as e:                        # noqa: BLE001 -- a report, never fatal
        lines.append(f"gi error {e}")
    for mod in ("pywayland", "cairo", "PIL"):
        try:
            m = __import__(mod)
            lines.append(f"{mod} {getattr(m, '__version__', getattr(m, 'version', '?'))}")
        except ImportError:
            lines.append(f"{mod} missing")
    try:
        from . import icons
        lines.append(f"logo {icons.distro_logo()}")
    except Exception as e:                        # noqa: BLE001
        lines.append(f"logo error {e}")
    for tool, arg in (("wayfire", "--version"), ("grim", "-h"), ("wf-recorder", "--version"),
                      ("wlsunset", "-h"), ("swayidle", "-v"), ("wtype", "-h"), ("nmcli", "--version"),
                      ("wpctl", "--version"), ("brightnessctl", "--version"), ("bluetoothctl", "--version")):
        if shutil.which(tool):
            try:
                r = subprocess.run([tool, arg], capture_output=True, text=True, timeout=3)
                first = (r.stdout or r.stderr).strip().splitlines()
                lines.append(f"{tool} {first[0] if first else 'present'}")
            except (OSError, subprocess.SubprocessError):
                lines.append(f"{tool} present")
        else:
            lines.append(f"{tool} missing")
    try:
        with open("/etc/os-release") as f:
            lines.append(next((ln.strip() for ln in f if ln.startswith("PRETTY_NAME=")), "os ?"))
    except OSError:
        pass
    lines.append(f"desktop {os.environ.get('XDG_CURRENT_DESKTOP', '')} session {os.environ.get('XDG_SESSION_DESKTOP', '')}")
    d = os.path.join(os.path.expanduser("~/.cache"), "sonata2")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "env.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def keep(argv) -> int:
    """`sonata2 keep dock` (session autostart): run a shell component and
    start it again if it crashes -- a desktop must never lose its Dock or
    menu bar. Stopped on purpose (exit 0, SIGTERM from `sonata2 restart`,
    logout) it stays stopped; crashing over and over, it gives up."""
    import subprocess
    import time
    cmd = self_command().split() + argv
    # each component logs to ~/.cache/sonata2/<name>.log (the previous run's
    # kept as .old.log) -- what `sonata2 doctor` and bug reports read
    logdir = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "sonata2")
    os.makedirs(logdir, exist_ok=True)
    log_path = os.path.join(logdir, f"{argv[0]}.log")
    if os.path.exists(log_path):
        os.replace(log_path, log_path[:-4] + ".old.log")
    crashes = []
    while True:
        started = time.monotonic()
        with open(log_path, "a", buffering=1) as log:
            log.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(argv)}\n")
            code = subprocess.call(cmd, stdout=log, stderr=subprocess.STDOUT)
        sock = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), os.environ.get("WAYLAND_DISPLAY", "wayland-0"))
        if code in (0, -15, -2, 130, 143) or not os.path.exists(sock):     # on purpose, or the session ended
            return 0
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < 60] + [now]
        print(f"sonata2 keep: {' '.join(argv)} exited with {code}; restarting", file=sys.stderr, flush=True)
        if len(crashes) >= 5:
            print(f"sonata2 keep: {argv[0]} keeps crashing; giving up", file=sys.stderr)
            return 1
        time.sleep(1 if now - started > 10 else 3)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "doctor":
        from . import doctor
        return doctor.main()
    if len(sys.argv) > 2 and sys.argv[1] == "keep":
        return keep(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "screenshot":
        return screenshot(sys.argv[2] if len(sys.argv) > 2 else "screen")
    if len(sys.argv) > 1 and sys.argv[1] == "restart":
        return restart(sys.argv[2:])
    if len(sys.argv) > 2 and sys.argv[1] == "key":
        return key(sys.argv[2])
    p = argparse.ArgumentParser(prog="sonata2")
    p.add_argument("component", choices=list(APP_IDS))
    p.add_argument("--preview", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dark", action="store_true")
    g.add_argument("--light", action="store_true")
    p.add_argument("--label", type=int, default=-1)
    p.add_argument("--menu", type=int, default=-1)
    p.add_argument("--submenu", action="store_true")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--stack", action="store_true")
    p.add_argument("--magnify", type=float, default=-1)
    p.add_argument("--search", default="")
    p.add_argument("--folder", action="store_true")
    p.add_argument("--jiggle", action="store_true")
    p.add_argument("--background", action="store_true", help="launchpad: start hidden (session autostart)")
    p.add_argument("--service", action="store_true", help="files: started by D-Bus (FileManager1), no window")
    p.add_argument("--page", default="", help="settings: section to open (wifi, dock, ...)")
    p.add_argument("path", nargs="*", help="files: folders to open")
    args = p.parse_intermixed_args()

    # Sonata's own UI never takes the GTK theme meant for other apps (the
    # session's GTK_THEME=Sonata-Light/Dark restyles libadwaita widgets):
    # our windows look the same in any session.
    os.environ.pop("GTK_THEME", None)
    # GPU drawing for every Sonata surface (Settings > General > Graphics):
    # OpenGL by default -- GTK's Vulkan path also loads every implicit Vulkan
    # layer installed (overlays, frame generators), slow on hybrid laptops
    if "GSK_RENDERER" not in os.environ:
        from . import config as _cfg
        from .icons import APPEARANCE_DEFAULTS as _AD
        os.environ["GSK_RENDERER"] = {"gl": "gl", "vulkan": "vulkan", "software": "cairo"}.get(
            _cfg.load("appearance", _AD)["renderer"], "gl")
    if args.component == "autostart":       # no GTK needed
        from . import autostart
        autostart.run()
        return 0
    if not args.preview and args.component not in ("files", "settings"):
        layer.ensure_preload()   # may re-exec this process

    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib
    from . import ui

    if args.component == "topbar" and not args.preview:
        GLib.idle_add(lambda: (_write_env_report(), False)[1])
    app_id = APP_IDS[args.component]
    GLib.set_prgname(app_id)     # Wayland app_id, also without a session bus
    app = Adw.Application(application_id=app_id)
    state = {}
    if args.component == "files":
        from gi.repository import Gio
        app.set_flags(Gio.ApplicationFlags.HANDLES_OPEN)

        def start():
            if not state:
                state["ready"] = True
                if args.dark or args.light:
                    ui.force_appearance("dark" if args.dark else "light")
                ui.setup()
                _serve_file_manager(app, ui)
        if args.service:                 # started by D-Bus for FileManager1: no window of its own
            def service(a):
                start()
                a.hold()
                GLib.timeout_add_seconds(30, lambda: (a.release(), False)[1])   # windows keep it alive
            from gi.repository import GLib
            app.connect("activate", service)
        else:
            app.connect("activate", lambda a: (start(), run_files(a, [], ui)))
        app.connect("open", lambda a, files, _n, _h: (start(), run_files(a, [f.get_uri() for f in files], ui)))
        uris = [Gio.File.new_for_commandline_arg(x).get_uri() for x in args.path]
        return app.run([sys.argv[0]] + uris)

    def activate(app):
        if args.dark or args.light:
            ui.force_appearance("dark" if args.dark else "light")
        ui.setup()
        if args.component == "gallery":
            run_gallery(app, args, ui)
        elif args.component == "dock":
            run_dock(app, args, ui)
        elif args.component == "launchpad":
            run_launchpad(app, args, ui, state)
        elif args.component == "settings":
            run_settings(app, args, ui, state)
        elif args.component == "wallpaper":
            run_wallpaper(app, args, ui)
        elif args.component == "spotlight":
            run_spotlight(app, args, ui, state)
        elif args.component == "lock":
            run_lock(app, args, ui, state)
        else:
            run_topbar(app, args, ui)

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
