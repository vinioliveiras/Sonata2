"""Entry point: `python3 -m sonata2 dock [--preview] [--dark|--light] [--label N]`.

--preview shows the Dock over a sample wallpaper in a normal window
(development, screenshots); --label N keeps the name label of the N-th tile
visible."""
import argparse
import sys

from .shell import layer

APP_ID = "io.github.vinioliveiras.sonata2.dock"


def main() -> int:
    p = argparse.ArgumentParser(prog="sonata2")
    p.add_argument("component", choices=["dock"])
    p.add_argument("--preview", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dark", action="store_true")
    g.add_argument("--light", action="store_true")
    p.add_argument("--label", type=int, default=-1)
    args = p.parse_args()

    if not args.preview:
        layer.ensure_preload()   # may re-exec this process

    import gi
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from .shell import dock
    from .style import apply_theme

    from gi.repository import GLib
    GLib.set_prgname(APP_ID)   # Wayland app_id, also without a session bus
    app = Adw.Application(application_id=APP_ID)

    def activate(app):
        if args.dark or args.light:
            apply_theme("dark" if args.dark else "light")
        dock.load_css()
        cfg = dock.load_config()
        from gi.repository import Gdk
        from .wl.toplevels import ToplevelManager
        manager = ToplevelManager(Gdk.Display.get_default(), ignore_app_ids={APP_ID})
        if args.preview:
            from .shell.preview import PreviewWindow
            win = PreviewWindow(app, dock.Dock(cfg, manager))
            dock.follow_theme(win, cfg)
        else:
            win = dock.DockWindow(app, cfg, manager)
        win.present()
        if args.label >= 0:
            d = win.dock
            tiles = list(d.tiles.values()) + [d.trash]
            if args.label < len(tiles):
                GLib.timeout_add(300, lambda: (tiles[args.label].label.popup(), False)[1])

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
