"""Dock badges for apps that send none (Vini: WhatsApp had none), and
WhatsApp's notifications: its own name/icon (not Chrome's), every card the
same size, no Chrome "Settings" button, a click brings it forward or opens it."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gio, GLib, GdkPixbuf, Gtk  # noqa: E402

from sonata2 import badges as B  # noqa: E402
from sonata2.shell import dock as D, notifications as N  # noqa: E402

WEB = "sonata2-webapp-waee32c088c"


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class PureTest(unittest.TestCase):
    def test_title_count(self):
        self.assertEqual(B.title_count(["(3) WhatsApp"]), 3)
        self.assertEqual(B.title_count(["WhatsApp", "(12) WhatsApp", "[2] x"]), 12)
        self.assertEqual(B.title_count(["WhatsApp", "Draft (1)"]), 0)

    def test_counted_title_only_for_web_pages(self):
        self.assertTrue(B.counted_title("chrome-web.whatsapp.com__-Default", WEB))
        self.assertTrue(B.counted_title("x", WEB))
        self.assertFalse(B.counted_title("org.gnome.TextEditor", "org.gnome.TextEditor"))

    def test_allowed_all_and_per_app(self):
        """Vini: a switch for badges in general and per app."""
        self.assertTrue(B.allowed({}, WEB, "WhatsApp"))
        self.assertFalse(B.allowed({"badges": False}, WEB, "WhatsApp"))
        off = {"apps": {WEB + ".desktop": {"name": "WhatsApp", "badge": False}}}
        self.assertFalse(B.allowed(off, WEB, "x"))
        self.assertFalse(B.allowed({"apps": {"zz": {"name": "whatsapp", "badge": False}}}, "k", "WhatsApp"))
        self.assertTrue(B.allowed(off, "discord", "Discord"))

    def test_label(self):
        self.assertEqual([B.label(x) for x in (0, 7, 120, None)], ["", "7", "99+", ""])

    def test_note_counts_by_desktop_or_name_and_seen(self):
        notes = [{"id": 1, "desktop": WEB + ".desktop", "app": "WhatsApp"},
                 {"id": 2, "desktop": "", "app": "whatsapp"},
                 {"id": 3, "desktop": "", "app": "Other"}]
        tiles = {WEB: "WhatsApp", "x": "X"}
        self.assertEqual(B.note_counts(notes, tiles, {}), {WEB: 2})
        self.assertEqual(B.note_counts(notes, tiles, {WEB: 1}), {WEB: 1})
        self.assertEqual(B.newest(notes, tiles, WEB), 2)

    def test_publish_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "n.json")
            note = N.Note(5, "WhatsApp", "", "s", "b", [], WEB)
            B.publish([note], 42, p)
            self.assertEqual(B.load(p), (42, [{"id": 5, "desktop": WEB, "app": "WhatsApp"}]))
            self.assertEqual(B.load(os.path.join(d, "none")), (None, []))

    def test_webapp_of_sender(self):
        root = "/home/v/.local/share/sonata2-data/webapps"
        self.assertEqual(B.webapp_of(["/opt/google/chrome/chrome", f"--user-data-dir={root}/waee32c088c/chromium",
                                      "--app=https://web.whatsapp.com/"]), "waee32c088c")
        self.assertEqual(B.webapp_of(["python3", "-m", "sonata2", "webapp", "abc"]), "abc")
        self.assertEqual(B.webapp_of(["/opt/google/chrome/chrome", "--user-data-dir=/home/v/.config/chrome"]), "")


class SiteTest(unittest.TestCase):
    ENTRIES = {"waee32c088c": {"name": "WhatsApp", "url": "https://web.whatsapp.com/"},
               "g1": {"name": "Gmail", "url": "https://mail.google.com/"}}

    def test_site_line_finds_the_web_app(self):
        """Vini: WhatsApp's notifications went to Chrome (badge 8 on Chrome)."""
        self.assertEqual(B.webapp_for_site("web.whatsapp.com\n\nTava a dormir", self.ENTRIES), "waee32c088c")
        self.assertEqual(B.webapp_for_site('<a href="https://web.whatsapp.com/">web.whatsapp.com</a>\n\nx',
                                           self.ENTRIES), "waee32c088c")
        self.assertEqual(B.webapp_for_site("www.mail.google.com\nhi", self.ENTRIES), "g1")
        self.assertEqual(B.webapp_for_site("Hello there.\nweb.whatsapp.com", self.ENTRIES), "")
        self.assertEqual(B.webapp_for_site("example.com\nhi", self.ENTRIES), "")

    def test_chrome_notification_becomes_whatsapp(self):
        s = N.Notifications.__new__(N.Notifications)
        s.cfg = {"apps": {}}
        s.notes, s._next, s._banners, s.nc, s.listeners = [], 1, {}, None, []
        s._banner = mock.Mock()
        with mock.patch("sonata2.webapps.apps", return_value=self.ENTRIES), \
                mock.patch.object(N.apps, "lookup", return_value=object()), \
                mock.patch.object(N.config, "save"), mock.patch.object(N.config, "load", return_value={"apps": {}}), \
                mock.patch.object(B, "publish"):
            s.notify("Google Chrome", 0, "", "Bruna", "web.whatsapp.com\n\nTava a dormir jaaa",
                     ["default", "", "settings", "Settings"], {"desktop-entry": "google-chrome"}, -1)
        n = s.notes[0]
        self.assertEqual((n.desktop, n.app, n.body), (WEB, "WhatsApp", "Tava a dormir jaaa"))
        self.assertEqual(n.actions, [("default", "")])


class CardTest(unittest.TestCase):
    def test_tidy_web_app(self):
        body, acts = N.tidy("web.whatsapp.com\n\nTava a dormir jaaa", [("default", ""), ("settings", "Settings")], True)
        self.assertEqual(body, "Tava a dormir jaaa")
        self.assertEqual(acts, [("default", "")])

    def test_tidy_browser_keeps_site_drops_settings(self):
        body, acts = N.tidy("example.com\n\nhi", [("settings", "Settings"), ("a", "Reply")], False, browser=True)
        self.assertEqual(body, "example.com\nhi")
        self.assertEqual(acts, [("a", "Reply")])

    def test_tidy_other_apps_keep_actions(self):
        self.assertEqual(N.tidy("a", [("settings", "Settings")], False)[1], [("settings", "Settings")])

    def test_thumbnail_is_a_small_square(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.png")
            GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1200, 1600).savev(p, "png", [], [])
            t = N.thumbnail(p)
            self.assertEqual((t.get_width(), t.get_height()), (2 * N.THUMB, 2 * N.THUMB))
            self.assertIsNone(N.thumbnail(os.path.join(d, "none.png")))

    def test_card_with_big_picture_stays_small(self):
        Gtk.init()
        s = N.Notifications.__new__(N.Notifications)
        s.close, s.invoke = mock.Mock(), mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "big.png")
            GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1200, 1600).savev(p, "png", [], [])
            plain = s.card(N.Note(1, "A", "", "Title", "body", []))
            pic = s.card(N.Note(2, "A", "", "Title", "body", [], image=p))
            h = [c.measure(Gtk.Orientation.VERTICAL, N.BANNER_W)[1] for c in (plain, pic)]
        self.assertLessEqual(h[1], max(h[0], N.THUMB + 30))

    def test_web_sender_named_and_cached(self):
        s = N.Notifications.__new__(N.Notifications)
        conn = mock.Mock()
        conn.call_sync.return_value = GLib.Variant("(u)", (77,))
        root = "/x/webapps"
        with mock.patch.object(B, "cmdline", return_value=["chrome", f"--user-data-dir={root}/wa1/chromium"]), \
                mock.patch.object(N.apps, "lookup", return_value=object()), \
                mock.patch("sonata2.webapps.get", return_value={"name": "WhatsApp"}):
            self.assertEqual(s._webapp_sender(conn, ":1.5"), ("sonata2-webapp-wa1", "WhatsApp"))
            s._webapp_sender(conn, ":1.5")
        conn.call_sync.assert_called_once()

    def test_click_web_app_opens_when_no_window(self):
        s = N.Notifications.__new__(N.Notifications)
        s._conn, s.close = None, mock.Mock()
        n = mock.Mock(actions=[("default", "")], desktop=WEB, app="WhatsApp", id=4)
        with mock.patch.object(N, "bring_forward", return_value=False), \
                mock.patch.object(N.apps, "lookup") as lk:
            s.invoke(n)
        lk.return_value.launch.assert_called_once()
        with mock.patch.object(N, "bring_forward", return_value=True) as bf, \
                mock.patch.object(N.apps, "lookup") as lk:
            s.invoke(n)
        bf.assert_called_once_with(WEB, "WhatsApp")
        lk.assert_not_called()


class DockTest(unittest.TestCase):
    def setUp(self):
        Gtk.init()
        D._SEEN.update(server=None, ids={})
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "notes.json")
        p = mock.patch.object(B, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = dict(D.DEFAULTS, pinned=[], stacks=[], recent=[], folders={})
        D.load_css(self.cfg)
        self.win = Gtk.Window()
        self.dock = D.Dock(self.cfg)
        self.win.set_child(self.dock)
        self.win.present()
        settle()
        self.tile = self.dock._add_tile(WEB, "WhatsApp", Gio.ThemedIcon.new("x"))

    def tearDown(self):
        self.dock.detach()
        self.win.destroy()
        self.tmp.cleanup()

    def notes(self, *ids):
        B.publish([N.Note(i, "WhatsApp", "", "s", "b", [], WEB) for i in ids], 1, self.state)
        self.dock._notes_changed()

    def test_notifications_count_until_app_in_front(self):
        self.notes(1, 2)
        self.assertEqual(self.tile.icon.badge, "2")
        win = mock.Mock(activated=True)
        self.dock.windows = {WEB: [win]}
        self.dock.refresh_badges()                       # WhatsApp in front: seen
        self.assertEqual(self.tile.icon.badge, "")
        self.dock.windows = {WEB: [mock.Mock(activated=False)]}
        self.notes(1, 2, 3)
        self.assertEqual(self.tile.icon.badge, "1")

    def test_title_count_wins_over_notifications(self):
        self.notes(1, 2)
        self.dock.title_counts = {WEB: 5}
        self.dock.refresh_badges()
        self.assertEqual(self.tile.icon.badge, "5")

    def test_app_own_count_wins(self):
        self.notes(1, 2)
        V = GLib.Variant
        self.dock._launcher_update(None, None, None, None, None, V("(sa{sv})", (
            f"application://{WEB}.desktop", {"count": V("x", 0), "count-visible": V("b", False)})))
        self.assertEqual(self.tile.icon.badge, "")

    def test_switches_hide_badges(self):
        self.notes(1, 2)
        self.dock._badge_cfg = {"badges": False}
        self.dock.refresh_badges()
        self.assertEqual(self.tile.icon.badge, "")
        self.dock._badge_cfg = {"apps": {WEB: {"badge": False}}}
        self.dock.refresh_badges()
        self.assertEqual(self.tile.icon.badge, "")
        self.dock._badge_cfg = {}
        self.dock.refresh_badges()
        self.assertEqual(self.tile.icon.badge, "2")

    def test_new_menu_bar_resets_seen(self):
        D._SEEN.update(server=99, ids={WEB: 50})
        self.notes(1)
        self.assertEqual(self.tile.icon.badge, "1")

    def test_badge_pops_in(self):
        settle()
        self.notes(1)
        self.assertLess(self.tile.icon.badge_k, 1.0)
        settle(D.BADGE_POP_MS + 150)
        self.assertAlmostEqual(self.tile.icon.badge_k, 1.0, places=2)


if __name__ == "__main__":
    unittest.main()


class AccentBadgeTest(unittest.TestCase):
    """Vini: the count badge in the accent colour he picked (it was red)."""
    def colors(self, accent, dark):
        from sonata2.ui import theme, tokens
        vals = {**tokens.palette(dark), **tokens.accent_tokens(accent, dark)}
        with mock.patch.object(theme, "values", return_value=vals), \
                mock.patch.object(theme, "is_dark", return_value=dark), mock.patch.dict(theme._parsed, clear=True):
            fill, ink = D.badge_colors()
        hexed = "#%02x%02x%02x" % tuple(round(c * 255) for c in (fill.red, fill.green, fill.blue))
        return hexed, (round(ink.red), round(ink.green), round(ink.blue)), vals

    def test_accent_fill(self):
        for accent in ("blue", "green", "pink", "#7b2cbf"):
            hexed, ink, vals = self.colors(accent, False)
            self.assertEqual(hexed, vals.get("accent_ink", vals["accent"]).lower(), accent)
            self.assertNotEqual(hexed, "#ff3b30")

    def test_black_accent_in_dark_mode_readable(self):
        from sonata2.ui import tokens
        hexed, ink, _v = self.colors("#000000", True)
        self.assertGreaterEqual(tokens.contrast(hexed, tokens.DARK_BG), tokens.INK_MIN)
        self.assertEqual(ink, (1, 1, 1))

    def test_number_reads_on_light_accent(self):
        _h, ink, _v = self.colors("#ffd60a", False)
        self.assertEqual(ink, (0, 0, 0))
        for accent in ("blue", "green", "red"):
            _h, ink, _v = self.colors(accent, False)
            self.assertEqual(ink, (1, 1, 1), accent)

    def test_drawn_with_it(self):
        import inspect
        src = inspect.getsource(D.DockIcon._draw_badge)
        self.assertIn("badge_colors()", src)
        self.assertNotIn("#ff3b30", src)
