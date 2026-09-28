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
  --page ID                                                    (settings)"""
import argparse
import json
import os
import sys

from .shell import layer

APP_IDS = {"dock": "io.github.vinioliveiras.sonata2.dock",
           "settings": "io.github.vinioliveiras.sonata2.settings",
           "wallpaper": "io.github.vinioliveiras.sonata2.wallpaper",
           "launchpad": "io.github.vinioliveiras.sonata2.launchpad",
           "topbar": "io.github.vinioliveiras.sonata2.topbar",
           "gallery": "io.github.vinioliveiras.sonata2.gallery"}
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    launchpad.launchpad_desktop_file(sys.executable, REPO)   # Launchpad as a Dock app
    from .settings.app import settings_desktop_file
    settings_desktop_file(sys.executable, REPO)             # System Settings in Launchpad
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
    manager = ToplevelManager(Gdk.Display.get_default(), ignore_app_ids=set(APP_IDS.values()))
    if args.preview:
        from .shell.preview import PreviewWindow
        win = PreviewWindow(app, dock.Dock(cfg, manager))
    else:
        win = dock.DockWindow(app, cfg, manager)
    win.present()
    d = win.dock
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


def run_wallpaper(app, args, ui):
    from .shell.wallpaper import WallpaperWindow
    WallpaperWindow(app).present()


def run_topbar(app, args, ui):
    from .shell import topbar
    win = topbar.TopBarWindow(app, preview=args.preview)
    win.present()
    if args.menu >= 0:
        _later(600, lambda: win.bar.open_menu(args.menu))


def main() -> int:
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
    p.add_argument("--page", default="", help="settings: section to open (wifi, dock, ...)")
    args = p.parse_args()

    if not args.preview:
        layer.ensure_preload()   # may re-exec this process

    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib
    from . import ui

    app_id = APP_IDS[args.component]
    GLib.set_prgname(app_id)     # Wayland app_id, also without a session bus
    app = Adw.Application(application_id=app_id)
    state = {}

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
        else:
            run_topbar(app, args, ui)

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
