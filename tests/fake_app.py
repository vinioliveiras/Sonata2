"""A tiny GTK window with a given app_id, for Dock tests:
    python3 tests/fake_app.py org.gnome.Console"""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

GLib.set_prgname(sys.argv[1])   # app_id even without a session bus
app = Gtk.Application(application_id=sys.argv[1], flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", lambda a: Gtk.ApplicationWindow(application=a, title=sys.argv[1]).present())
app.run([sys.argv[0]])
