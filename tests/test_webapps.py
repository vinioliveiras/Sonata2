"""Sonata's web apps (sonata2/webapps). Vini: our own web apps, made with
"New Web App…" (right-click on Launchpad, the Desktop, the Dock), after
Spider's WhatsApp took the session down.
Run: xvfb-run python3 -m unittest tests.test_webapps"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GdkPixbuf, GLib  # noqa: E402

from sonata2 import config, ui, webapps as W  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def png(side):
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, side, side)
    pix.fill(0x25d366ff)
    return pix.save_to_bufferv("png", [], [])[1]


class StoreTest(unittest.TestCase):
    def setUp(self):
        config.save(W.NAME, W.DEFAULTS)

    def test_addresses(self):
        self.assertEqual(W.normalize_url("web.whatsapp.com"), "https://web.whatsapp.com/")
        self.assertEqual(W.normalize_url(" http://example.org/a?b=1 "), "http://example.org/a?b=1")
        for bad in ("", "hello", "two words.com", "ftp://x.org", "javascript:alert(1)"):
            self.assertIsNone(W.normalize_url(bad), bad)
        self.assertEqual(W.default_name("https://web.whatsapp.com/"), "Whatsapp")
        self.assertEqual(W.default_name("https://www.notion.so/"), "Notion")

    def test_create_writes_its_own_app(self):
        wid = W.create("WhatsApp", "web.whatsapp.com", command="sonata2", fetch=False)
        self.assertRegex(wid, r"^w[0-9a-f]{10}$")
        path = os.path.join(GLib.get_user_data_dir(), "applications", W.desktop_id(wid) + ".desktop")
        text = open(path).read()
        self.assertIn(f"Exec=sonata2 webapp {wid}\n", text)
        self.assertIn(f"StartupWMClass={W.APP_ID_PREFIX}{wid}\n", text)      # the Dock tells them apart
        self.assertIn("Name=WhatsApp\n", text)
        self.assertIn(f"Icon={W.FALLBACK_ICON}\n", text)
        self.assertTrue(W.is_webapp(W.desktop_id(wid) + ".desktop"))
        self.assertEqual(W.id_of(W.desktop_id(wid)), wid)
        self.assertTrue(W.desktop_id(wid).startswith("sonata2-"))           # Sonata's own: no "Move to Trash"

    def test_remove_takes_its_data(self):
        wid = W.create("Site", "example.org", command="sonata2", fetch=False)
        open(os.path.join(W.data_dir(wid), "cookies.sqlite"), "w").close()
        W.remove(wid)
        self.assertIsNone(W.get(wid))
        self.assertFalse(os.path.exists(W.data_dir(wid)))
        self.assertFalse(os.path.exists(os.path.join(GLib.get_user_data_dir(), "applications",
                                                     W.desktop_id(wid) + ".desktop")))

    def test_remove_never_touches_other_paths(self):
        W.remove("../../etc")                                                # no crash, nothing removed

    def test_site_icon_biggest_first_then_favicon(self):
        html = ('<link rel="icon" href="/f16.png" sizes="16x16">'
                '<link rel="apple-touch-icon" href="/touch.png">'
                '<link rel="icon" href="https://cdn.x.org/i192.png" sizes="192x192">')
        self.assertEqual(W.icon_candidates("https://x.org/app", html),
                         ["https://cdn.x.org/i192.png", "https://x.org/touch.png", "https://x.org/f16.png",
                          "https://x.org/favicon.ico"])

    def test_fetch_icon_and_the_entry_follows(self):
        wid = W.create("Site", "example.org", command="sonata2", fetch=False)
        files = {"https://example.org/": b'<link rel="apple-touch-icon" href="/t.png">',
                 "https://example.org/t.png": png(300)}
        self.assertTrue(W.fetch_icon(wid, download=lambda u: files[u]))
        w, h = GdkPixbuf.Pixbuf.get_file_info(W.icon_path(wid))[1:]
        self.assertEqual((w, h), (256, 256))                                 # kept small
        W.write_desktop(wid, "sonata2")
        self.assertIn(f"Icon={W.icon_path(wid)}\n",
                      open(os.path.join(GLib.get_user_data_dir(), "applications",
                                        W.desktop_id(wid) + ".desktop")).read())

    def test_dock_rewrites_them_at_login(self):
        from sonata2 import __main__ as main
        src = open(main.__file__).read()
        self.assertIn("webapps.write_all(self_command())", src[src.index("def run_dock"):])
        self.assertIn('sys.argv[1] == "webapp"', src)


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        from sonata2.webapps import window
        cls.win_mod = window

    def test_links_to_other_sites_leave(self):
        s = self.win_mod.stays_inside
        self.assertTrue(s("https://web.whatsapp.com/", "https://static.whatsapp.com/x"))
        self.assertTrue(s("https://web.whatsapp.com/", "blob:https://web.whatsapp.com/1"))
        self.assertFalse(s("https://web.whatsapp.com/", "https://www.youtube.com/watch"))
        self.assertFalse(s("https://web.whatsapp.com/", "mailto:a@b.c"))
        self.assertEqual(self.win_mod.site("https://www.bbc.co.uk/news"), "bbc.co.uk")

    def test_form_creates_only_with_an_address(self):
        """(Adw.AlertDialog itself crashes in the headless container: a stand-in.)"""
        class Dlg:
            def __init__(self, *_a, **_k):
                self.on, self.enabled, self.child = _a[3], {}, None

            def set_extra_child(self, c):
                self.child = c

            def set_response_enabled(self, rid, on):
                self.enabled[rid] = on
        done = []
        with mock.patch.object(ui.dialog, "alert", side_effect=Dlg), \
                mock.patch.object(W, "create", return_value="w0123456789") as create:
            dlg = self.win_mod.form(done.append)
            self.assertFalse(dlg.enabled["create"])
            url, name = dlg.child.get_child_at(1, 0), dlg.child.get_child_at(1, 1)
            url.set_text("web.whatsapp.com")
            self.assertTrue(dlg.enabled["create"])
            self.assertEqual(name.get_text(), "Whatsapp")                     # suggested from the address
            dlg.on("create")
        create.assert_called_once_with("Whatsapp", "https://web.whatsapp.com/")
        self.assertEqual(done, ["w0123456789"])

    def test_window_has_its_own_storage(self):
        try:
            gi.require_version("WebKit", "6.0")
        except ValueError:
            self.skipTest("WebKitGTK 6 not installed")
        wid = W.create("Local", "http://localhost/", command="sonata2", fetch=False)
        app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.webapp.test")
        app.register(None)
        win = self.win_mod.WebAppWindow(app, wid, W.get(wid))
        settle(300)
        self.assertEqual(win.title_label.get_label(), "Local")
        self.assertFalse(win.back.get_sensitive())
        session = win.view.get_network_session()
        self.assertTrue(session.get_website_data_manager().get_base_data_directory().startswith(W.data_dir(wid)))
        win.destroy()


class MenusTest(unittest.TestCase):
    def test_new_web_app_in_the_three_menus(self):
        root = os.path.dirname(os.path.dirname(W.__file__))
        desk = open(os.path.join(root, "shell", "desktop.py")).read()
        dock = open(os.path.join(root, "shell", "dock_menu.py")).read()
        lp = open(os.path.join(root, "shell", "launchpad.py")).read()
        self.assertIn('Item("New Web App…", _new_webapp)', desk[desk.index("def _background_menu"):])
        self.assertIn('"New Web App…"', dock[dock.index("def divider_menu"):])
        self.assertIn('"New Web App…"', lp[lp.index("def _background_menu"):])
        self.assertIn('"Delete Web App…"', lp[lp.index("def item_menu"):])


if __name__ == "__main__":
    unittest.main()
