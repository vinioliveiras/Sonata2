"""Entry point: `python3 -m sonata2 dock [--preview] [--dark|--light] [--label N]`.

--preview shows the Dock over a sample wallpaper in a normal window
(development, screenshots); --label N keeps the name label of the N-th tile
visible; --menu N opens the N-th tile's right-click menu."""
import argparse
import sys

from .shell import layer

APP_ID = "io.github.vinioliveiras.sonata2.dock"


def main() -> int:
    p = argparse.ArgumentParser(prog="sonata2")
    p.add_argument("component", choices=["dock", "gallery"])
    p.add_argument("--preview", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dark", action="store_true")
    g.add_argument("--light", action="store_true")
    p.add_argument("--label", type=int, default=-1)
    p.add_argument("--menu", type=int, default=-1, help="open the N-th tile's menu (screenshots)")
    p.add_argument("--submenu", action="store_true", help="with --menu: also open Options")
    p.add_argument("--magnify", type=float, default=-1,
                   help="screenshots: magnify as if the pointer were at this x")
    args = p.parse_args()

    if not args.preview:
        layer.ensure_preload()   # may re-exec this process

    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from .shell import dock
    from . import ui

    from gi.repository import GLib
    GLib.set_prgname(APP_ID)   # Wayland app_id, also without a session bus
    app = Adw.Application(application_id=APP_ID)

    def activate(app):
        if args.dark or args.light:
            ui.force_appearance("dark" if args.dark else "light")
        if args.component == "gallery":
            from .ui.gallery import GalleryWindow, sample_menu
            ui.setup()
            win = GalleryWindow(app)
            win.present()
            if args.menu >= 0:
                def open_gallery_menu():
                    pop = sample_menu(win.menu_anchor)
                    if args.submenu:
                        GLib.timeout_add(300, lambda: (ui.menu.open_submenu(pop, "Options"), False)[1])
                    return False
                GLib.timeout_add(500, open_gallery_menu)
            return
        cfg = dock.load_config()
        dock.load_css(cfg)
        from gi.repository import Gdk
        from .wl.toplevels import ToplevelManager
        manager = ToplevelManager(Gdk.Display.get_default(), ignore_app_ids={APP_ID})
        if args.preview:
            from .shell.preview import PreviewWindow
            win = PreviewWindow(app, dock.Dock(cfg, manager))
        else:
            win = dock.DockWindow(app, cfg, manager)
        win.present()
        if args.label >= 0:
            d = win.dock
            tiles = list(d.tiles.values()) + [d.trash]
            if args.label < len(tiles):
                GLib.timeout_add(300, lambda: (tiles[args.label].label.popup(), False)[1])
        if args.magnify >= 0:
            def magnify():
                d = win.dock
                d.cfg["magnification"] = True
                d._mag_x, d._mag_strength = args.magnify, 1.0
                d._apply_magnification()
                return False
            GLib.timeout_add(300, magnify)
        if args.menu >= 0:
            from .shell import dock_menu
            d = win.dock
            tiles = d.app_tiles() + [d.trash]
            if args.menu < len(tiles):
                t = tiles[args.menu]
                def open_menu():
                    pop = dock_menu.trash_menu(t) if t is d.trash else dock_menu.app_menu(d, t.key, t)
                    if args.submenu:
                        GLib.timeout_add(300, lambda: (ui.menu.open_submenu(pop, "Options"), False)[1])
                    return False
                GLib.timeout_add(400, open_menu)

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
