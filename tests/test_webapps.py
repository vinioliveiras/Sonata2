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
        with mock.patch.object(W, "theme_icon", return_value=None):          # (no icon pack's icon here)
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
        create.assert_called_once_with("Whatsapp", "https://web.whatsapp.com/", background=False)
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
        self.assertEqual(win.get_title(), "Local")
        self.assertIsNone(win.view.get_parent().get_first_child().get_child().get_center_widget())  # once (Vini)
        self.assertFalse(win.back.get_sensitive())
        session = win.view.get_network_session()
        self.assertTrue(session.get_website_data_manager().get_base_data_directory().startswith(W.data_dir(wid)))
        win.destroy()


class IconChoiceTest(unittest.TestCase):
    """Vini: choose a web app's icon -- in the form, and later from its
    Launchpad menu (Settings > App Icons, the same picker)."""

    def setUp(self):
        config.save(W.NAME, W.DEFAULTS)
        config.save("icons", {})

    def test_custom_icon_copied_and_used(self):
        from sonata2 import icons
        wid = W.create("Site", "example.org", command="sonata2", fetch=False)
        pic = os.path.join(tempfile.mkdtemp(), "mine.png")
        with open(pic, "wb") as f:
            f.write(png(64))
        dest = W.set_custom_icon(wid, pic)
        self.assertTrue(dest.startswith(W.data_dir(wid)))                    # a copy: the original may move
        os.remove(pic)
        self.assertEqual(config.load("icons", icons.ICON_DEFAULTS)["apps"][W.desktop_id(wid)], {"source": "file", "path": dest})
        W.remove(wid)
        self.assertNotIn(W.desktop_id(wid), config.load("icons", icons.ICON_DEFAULTS).get("apps", {}))   # forgotten with it
        icons.forget_prefs()

    def test_form_uses_the_chosen_picture(self):
        from sonata2.webapps import window
        from sonata2.settings import appicons_page

        class Dlg:
            def __init__(self, *a, **_k):
                self.on, self.child = a[3], None

            def set_extra_child(self, c):
                self.child = c

            def set_response_enabled(self, *_a):
                pass
        pic = os.path.join(tempfile.mkdtemp(), "mine.png")
        with open(pic, "wb") as f:
            f.write(png(64))
        with mock.patch.object(ui.dialog, "alert", side_effect=Dlg), \
                mock.patch.object(appicons_page, "pick_picture", side_effect=lambda _p, cb: cb(pic)), \
                mock.patch.object(W, "create", return_value="w0123456789"), \
                mock.patch.object(W, "set_custom_icon") as custom:
            dlg = window.form()
            dlg.child.get_child_at(1, 0).set_text("example.org")
            dlg.child.icon_box.get_last_child().emit("clicked")
            dlg.on("create")
        custom.assert_called_once_with("w0123456789", pic)

    def test_change_icon_opens_that_apps_form(self):
        lp = open(os.path.join(os.path.dirname(W.__file__), "..", "shell", "launchpad.py")).read()
        self.assertIn('"appicons/" + item', lp[lp.index("def item_menu"):])
        from sonata2.settings.appicons_page import AppIconsPage
        page = AppIconsPage.__new__(AppIconsPage)
        page.rows, page._focus = {}, None
        page.focus("sonata2-webapp-w0123456789.desktop")
        self.assertEqual(page._focus, "sonata2-webapp-w0123456789")         # waits for its row
        row = mock.Mock()
        page.rows["sonata2-webapp-w0123456789"] = row
        page.edit = mock.Mock()
        page.focus("sonata2-webapp-w0123456789")
        settle()
        page.edit.assert_called_once_with(row)
        self.assertIsNone(page._focus)


class ThemeIconTest(unittest.TestCase):
    """Vini: the icon pack's icon first, when it has one with the web app's name."""

    def setUp(self):
        config.save(W.NAME, W.DEFAULTS)

    def test_names_tried(self):
        self.assertEqual(W.icon_names("WhatsApp", "https://web.whatsapp.com/")[:1], ["whatsapp"])
        self.assertIn("google-calendar", W.icon_names("Google Calendar", "https://calendar.google.com/"))
        self.assertIn("googlecalendar", W.icon_names("Google Calendar", "https://calendar.google.com/"))

    def test_pack_icon_wins_over_the_sites(self):
        theme = mock.Mock(has_icon=lambda n: n == "whatsapp")
        with mock.patch("gi.repository.Gtk.IconTheme.get_for_display", return_value=theme):
            wid = W.create("WhatsApp", "web.whatsapp.com", command="sonata2", fetch=False)
        self.assertEqual(W.get(wid)["theme_icon"], "whatsapp")
        open(W.icon_path(wid), "wb").write(png(64))                            # the site's icon too
        self.assertIn("Icon=whatsapp\n", W.desktop_text(wid, W.get(wid), "sonata2"))

    def test_no_pack_icon_the_sites(self):
        with mock.patch("gi.repository.Gtk.IconTheme.get_for_display",
                        return_value=mock.Mock(has_icon=lambda n: False)):
            wid = W.create("Odd Site", "odd.example", command="sonata2", fetch=False)
        self.assertNotIn("theme_icon", W.get(wid))
        self.assertIn(f"Icon={W.FALLBACK_ICON}\n", W.desktop_text(wid, W.get(wid), "sonata2"))


class DataPlaceTest(unittest.TestCase):
    def test_never_in_sonatas_own_folder(self):
        """Vini's dev install links ~/.local/share/sonata2 to the git clone:
        a web app's login landed in the repository."""
        wid = "w00000000aa"
        self.assertTrue(W.data_dir(wid).startswith(os.path.join(GLib.get_user_data_dir(), "sonata2-data")))
        from sonata2 import userdata
        self.assertIn("webapps", userdata.APPS)

    def test_old_place_moved_and_the_icon_follows(self):
        from sonata2 import icons, userdata
        config.save("icons", {})
        new = os.path.join(userdata.root(), "webapps")
        import shutil
        shutil.rmtree(new, ignore_errors=True)
        old = os.path.join(GLib.get_user_data_dir(), "sonata2", "webapps", "w00000000bb")
        os.makedirs(old)
        open(os.path.join(old, "custom-icon.png"), "wb").write(png(64))
        icons.set_app_pref(W.desktop_id("w00000000bb"), source="file", path=os.path.join(old, "custom-icon.png"))
        config.save(W.NAME, {"apps": {"w00000000bb": {"name": "Old", "url": "https://old.example/"}}})
        W.write_all("sonata2")
        moved = os.path.join(new, "w00000000bb", "custom-icon.png")
        self.assertTrue(os.path.exists(moved))
        self.assertEqual(config.load("icons", icons.ICON_DEFAULTS)["apps"][W.desktop_id("w00000000bb")]["path"],
                         moved)


class EditTest(unittest.TestCase):
    """Vini: a web app made can be edited (Launchpad's and the Dock's menus)."""

    def setUp(self):
        config.save(W.NAME, W.DEFAULTS)

    def test_update_keeps_its_login(self):
        with mock.patch.object(W, "theme_icon", return_value=None):
            wid = W.create("Zap", "web.whatsapp.com", command="sonata2", fetch=False)
            open(os.path.join(W.data_dir(wid), "cookies.sqlite"), "w").close()
            self.assertTrue(W.update(wid, name="WhatsApp", url="https://web.whatsapp.com/?x=1", background=True,
                                     command="sonata2"))
        e = W.get(wid)
        self.assertEqual((e["name"], e["url"], e["background"]), ("WhatsApp", "https://web.whatsapp.com/?x=1", True))
        self.assertTrue(os.path.exists(os.path.join(W.data_dir(wid), "cookies.sqlite")))   # same data
        self.assertIn("Name=WhatsApp\n", open(os.path.join(GLib.get_user_data_dir(), "applications",
                                                             W.desktop_id(wid) + ".desktop")).read())
        W.update(wid, background=False, command="sonata2")
        self.assertNotIn("background", W.get(wid))
        self.assertFalse(W.update("w0000000000", name="x"))

    def test_form_filled_in_and_saves(self):
        from sonata2.webapps import window
        with mock.patch.object(W, "theme_icon", return_value=None):
            wid = W.create("Zap", "web.whatsapp.com", command="sonata2", fetch=False)

        class Dlg:
            def __init__(self, *a, **_k):
                self.heading, self.on, self.ok, self.child = a[0], a[3], a[2][1][1], None

            def set_extra_child(self, c):
                self.child = c

            def set_response_enabled(self, *_a):
                pass
        with mock.patch.object(ui.dialog, "alert", side_effect=Dlg), \
                mock.patch.object(W, "update") as update, mock.patch.object(W, "create") as create:
            dlg = window.form(wid=wid)
            self.assertEqual((dlg.heading, dlg.ok), ("Edit Web App", "Save"))
            url, name = dlg.child.get_child_at(1, 0), dlg.child.get_child_at(1, 1)
            self.assertEqual((url.get_text(), name.get_text()), ("https://web.whatsapp.com/", "Zap"))
            name.set_text("WhatsApp")
            dlg.child.background.set_active(True)
            dlg.on("create")
        update.assert_called_once_with(wid, name="WhatsApp", url="https://web.whatsapp.com/", background=True)
        create.assert_not_called()

    def test_menus_offer_it(self):
        root = os.path.dirname(os.path.dirname(W.__file__))
        lp = open(os.path.join(root, "shell", "launchpad.py")).read()
        dock = open(os.path.join(root, "shell", "dock_menu.py")).read()
        self.assertIn('"Edit Web App…"', lp[lp.index("def item_menu"):])
        self.assertIn('"Edit Web App…"', dock[dock.index("def app_menu"):])


class CloseTest(unittest.TestCase):
    """Vini: WhatsApp asked for the QR code again -- the app quit while WebKit
    was still writing the login."""

    def test_quits_a_moment_after_the_window_goes(self):
        from sonata2.webapps import window
        win = mock.Mock(entry={"name": "W", "url": "https://x.org/"}, _quit_src=0)
        app = win.get_application.return_value
        with mock.patch("gi.repository.GLib.timeout_add", return_value=7) as later:
            self.assertTrue(window.WebAppWindow._close(win))
        win.set_visible.assert_called_once_with(False)
        self.assertEqual(later.call_args[0][0], window.QUIT_DELAY_MS)
        app.quit.assert_not_called()
        later.call_args[0][1]()
        app.quit.assert_called_once()

    def test_kept_running_in_the_background(self):
        from sonata2.webapps import window
        win = mock.Mock(entry={"name": "W", "url": "https://x.org/", "background": True}, _quit_src=0)
        with mock.patch("gi.repository.GLib.timeout_add") as later:
            window.WebAppWindow._close(win)
        later.assert_not_called()

    def test_opened_again_while_closing(self):
        from sonata2.webapps import window
        win = mock.Mock(_quit_src=7)
        with mock.patch("gi.repository.GLib.source_remove") as remove:
            window.WebAppWindow.reopen(win)
        remove.assert_called_once_with(7)
        win.get_application.return_value.release.assert_called_once()
        win.present.assert_called_once()


class DockMatchTest(unittest.TestCase):
    def test_window_finds_its_entry(self):
        """Vini: a new web app's window had no icon or name in the Dock."""
        from sonata2 import apps
        info = mock.Mock()
        with mock.patch.object(apps, "lookup", side_effect=lambda d: info if d == "sonata2-webapp-w0123456789"
                               else None):
            self.assertEqual(apps.match_app_id(W.APP_ID_PREFIX + "w0123456789"), "sonata2-webapp-w0123456789")

    def test_index_rebuilt_for_an_app_installed_since(self):
        from sonata2 import apps
        apps._INDEX, apps._INDEX_AT = {}, 0.0
        with mock.patch.object(apps, "lookup", return_value=None), \
                mock.patch.object(apps, "_build_index", return_value={"newapp": "new-app"}):
            self.assertEqual(apps.match_app_id("newapp"), "new-app")
        dock = open(os.path.join(os.path.dirname(apps.__file__), "shell", "dock.py")).read()
        self.assertIn("apps.refresh()", dock[dock.index("def apps_changed"):][:200])
        apps.refresh()


class MissingWebKitTest(unittest.TestCase):
    def test_says_so_instead_of_nothing(self):
        """Vini: "the web app doesn't open" -- webkitgtk-6.0 wasn't installed."""
        from sonata2.webapps import window
        app = mock.Mock()
        with mock.patch.object(ui.dialog, "alert") as alert:
            window.missing_webkit(app)
        self.assertIn("WebKitGTK 6", alert.call_args[0][0])
        self.assertIn("webkitgtk-6.0", alert.call_args[0][1])
        app.hold.assert_called_once()
        alert.call_args[0][3]("ok")
        app.release.assert_called_once()
        src = open(window.__file__).read()
        self.assertEqual(src.count("if not W.webkit_available():"), 2)          # opening one, and the form
        inst = open(os.path.join(os.path.dirname(W.__file__), "..", "..", "install.sh")).read()
        self.assertIn("webkitgtk-6.0", inst)


class DeleteAskTest(unittest.TestCase):
    def test_asked_like_move_to_trash(self):
        """Vini: the question came inside Launchpad, without the glass: it
        closes Launchpad first and the alert gets its own (glass) window."""
        from sonata2.shell import launchpad as L
        pad = mock.Mock()
        pad.close_launchpad.side_effect = lambda then=None: then and then()
        with mock.patch.object(ui.dialog, "alert") as alert, \
                mock.patch.object(W, "get", return_value={"name": "WhatsApp"}):
            L.Launchpad.ask_delete_webapp(pad, "sonata2-webapp-w0123456789")
        pad.close_launchpad.assert_called_once()
        self.assertIn("WhatsApp", alert.call_args[0][0])
        self.assertNotIn("parent", alert.call_args[1])
        self.assertEqual(len(alert.call_args[0]), 4)                   # no parent: its own glass window


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
