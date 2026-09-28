"""Entry point: `python3 -m sonata2 dock [--preview] [--dark|--light] [--label N]`.

--preview shows the Dock over a sample wallpaper in a normal window
(development, screenshots); --label N keeps the name label of the N-th tile
visible."""
import argparse
import sys

from .shell import layer


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

    app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.dock")

    def activate(app):
        if args.dark or args.light:
            apply_theme("dark" if args.dark else "light")
        dock.load_css()
        cfg = dock.load_config()
        if args.preview:
            from .shell.preview import PreviewWindow
            win = PreviewWindow(app, dock.Dock(cfg))
            dock.follow_theme(win)
        else:
            win = dock.DockWindow(app, cfg)
        win.present()
        if args.label >= 0:
            d = win.dock
            tiles = list(d.tiles.values()) + [d.trash]
            if args.label < len(tiles):
                from gi.repository import GLib
                GLib.timeout_add(300, lambda: (tiles[args.label].label.popup(), False)[1])

    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
