"""Menu bar tray (StatusNotifierItem) on a private session bus:
    cd /home/claude/sonata2 && timeout 115 env PYTHONPATH=. xvfb-run -a dbus-run-session \\
        /opt/gtk422/bin/python -m unittest tests/test_tray.py
Fake items (with a dbusmenu) are published on their own bus connections, so
closing one makes its name vanish like a quitting app."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import tray  # noqa: E402

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
  <property name="Id" type="s" access="read"/><property name="Title" type="s" access="read"/>
  <property name="Status" type="s" access="read"/><property name="IconName" type="s" access="read"/>
  <property name="IconThemePath" type="s" access="read"/>
  <property name="IconPixmap" type="a(iiay)" access="read"/>
  <property name="AttentionIconName" type="s" access="read"/>
  <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
  <property name="Menu" type="o" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
  <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
  <signal name="NewStatus"><arg type="s"/></signal><signal name="NewIcon"/>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
  <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
    <arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
  <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
    <arg type="v" direction="in"/><arg type="u" direction="in"/></method>
  <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
  <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def wait_for(cond, ms=3000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


def argb(w, h, a, r, g, b):
    return bytes([a, r, g, b]) * (w * h)


def node(i, children=(), **props):
    v = {k.replace("_", "-"): GLib.Variant(t, val) for k, (t, val) in props.items()}
    return GLib.Variant("(ia{sv}av)", (i, v, list(children)))


def sample_layout(submenu_filled=True):
    s, b, i = "s", "b", "i"
    sub = [node(21, label=(s, "Sub _One"))] if submenu_filled else []
    return node(0, [
        node(1, label=(s, "_Open Window")),
        node(2, label=(s, "Disabled"), enabled=(b, False)),
        node(3, type=(s, "separator")),
        node(4, label=(s, "Notifications"), toggle_type=(s, "checkmark"), toggle_state=(i, 1)),
        node(5, label=(s, "Mute"), toggle_type=(s, "checkmark"), toggle_state=(i, 0)),
        node(6, label=(s, "Hidden"), visible=(b, False)),
        node(3, type=(s, "separator")),
        node(20, sub, label=(s, "More"), children_display=(s, "submenu")),
        node(9, label=(s, "Save__As")),
    ])


class FakeItem:
    """An app's StatusNotifierItem + dbusmenu on its own connection."""

    def __init__(self, ident, icon_name="", pixmap=None, status="Active", item_is_menu=False, layout=None):
        addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.conn = Gio.DBusConnection.new_for_address_sync(
            addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        self.props = {"Id": GLib.Variant("s", ident), "Title": GLib.Variant("s", ident.title()),
                      "Status": GLib.Variant("s", status), "IconName": GLib.Variant("s", icon_name),
                      "IconThemePath": GLib.Variant("s", ""),
                      "IconPixmap": GLib.Variant("a(iiay)", pixmap or []),
                      "AttentionIconName": GLib.Variant("s", ""),
                      "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], ident.title(), "Running")),
                      "Menu": GLib.Variant("o", "/MenuBar"), "ItemIsMenu": GLib.Variant("b", item_is_menu)}
        self.layout = layout or sample_layout()
        self.calls, self.events = [], []
        tray._register(self.conn, "/StatusNotifierItem", ITEM_XML, self._item_call, self._get)
        tray._register(self.conn, "/MenuBar", MENU_XML, self._menu_call)
        self.conn.call(tray.WATCHER, tray.WATCHER_PATH, tray.WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", ("/StatusNotifierItem",)), None, 0, 2000, None, None)

    @property
    def key(self):
        return self.conn.get_unique_name() + "/StatusNotifierItem"

    def _get(self, _c, _s, _p, _i, prop):
        return self.props.get(prop)

    def _item_call(self, _c, _s, _p, _i, method, params, inv):
        self.calls.append((method, params.unpack()))
        inv.return_value(None)

    def _menu_call(self, _c, _s, _p, _i, method, params, inv):
        if method == "GetLayout":
            inv.return_value(GLib.Variant.new_tuple(GLib.Variant("u", 1), self.layout))
        elif method == "AboutToShow":
            self.calls.append(("AboutToShow", params.unpack()))
            inv.return_value(GLib.Variant("(b)", (False,)))
        else:
            self.events.append(params.unpack()[:2])
            inv.return_value(None)

    def set_status(self, status):
        self.props["Status"] = GLib.Variant("s", status)
        self.conn.emit_signal(None, "/StatusNotifierItem", tray.ITEM_IFACE, "NewStatus",
                              GLib.Variant("(s)", (status,)))

    def quit(self):
        self.conn.close_sync(None)


class TrayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.host = tray.host()
        wait_for(lambda: cls.host.watcher.owned)

    def test_watcher_registration_and_removal(self):
        it = FakeItem("discord", icon_name="user-available-symbolic")
        self.assertTrue(wait_for(lambda: it.key in self.host.items and self.host.items[it.key].ready))
        self.assertIn(it.key, self.host.watcher.items)
        item = self.host.items[it.key]
        self.assertEqual(item.id, "discord")
        self.assertEqual(item.icon(1), ("name", "user-available-symbolic"))
        self.assertEqual(item.tooltip, "Discord\nRunning")
        # the property as other hosts read it
        got = {}
        self.host.conn.call(tray.WATCHER, tray.WATCHER_PATH, tray.PROPS, "GetAll",
                            GLib.Variant("(s)", (tray.WATCHER,)), None, 0, 2000, None,
                            lambda c, r: got.update(c.call_finish(r).unpack()[0]))
        self.assertTrue(wait_for(lambda: got))
        self.assertIn(it.key, got["RegisteredStatusNotifierItems"])
        self.assertTrue(got["IsStatusNotifierHostRegistered"])
        it.quit()                                  # the app quits: its name vanishes
        self.assertTrue(wait_for(lambda: it.key not in self.host.items))
        self.assertNotIn(it.key, self.host.watcher.items)

    def test_status_passive_hidden_and_box(self):
        box = tray.TrayBox()
        it = FakeItem("steam", icon_name="applications-games-symbolic")
        self.assertTrue(wait_for(lambda: it.key in box.buttons))
        it.set_status("Passive")
        self.assertTrue(wait_for(lambda: it.key not in box.buttons))
        it.set_status("NeedsAttention")
        self.assertTrue(wait_for(lambda: it.key in box.buttons))
        box.set_shown(False)
        self.assertFalse(box.buttons)
        self.assertFalse(box.get_visible())
        box.stop()
        it.quit()

    def test_pixmap_conversion(self):
        pix = [(8, 8, argb(8, 8, 255, 0, 0, 255)), (32, 32, argb(32, 32, 255, 255, 0, 0)),
               (64, 64, argb(64, 64, 255, 0, 255, 0))]
        tex = tray.pixmap_texture(pix, 16)
        self.assertEqual((tex.get_width(), tex.get_height()), (32, 32))       # smallest >= 16
        tex = tray.pixmap_texture(pix, 128)
        self.assertEqual(tex.get_width(), 64)                                  # else the largest
        d = Gdk.TextureDownloader.new(tray.pixmap_texture(pix, 32))
        d.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, _stride = d.download_bytes()
        self.assertEqual(bytes(data.get_data()[:4]), bytes([255, 0, 0, 255]))  # ARGB red -> RGBA red
        self.assertIsNone(tray.pixmap_texture([(4, 4, b"\0" * 10)], 16))       # short data ignored
        # through D-Bus (variant path)
        it = FakeItem("keepass", pixmap=[(16, 16, argb(16, 16, 128, 0, 0, 255))])
        self.assertTrue(wait_for(lambda: it.key in self.host.items and self.host.items[it.key].ready))
        kind, tex = self.host.items[it.key].icon(1)
        self.assertEqual((kind, tex.get_width()), ("paintable", 16))
        it.quit()

    def test_menu_layout_conversion(self):
        clicked = []
        lay = sample_layout().unpack()
        secs = tray.layout_to_sections(lay, clicked.append)
        self.assertEqual([[i.label for i in s] for s in secs],
                         [["Open Window", "Disabled"], ["Notifications", "Mute"], ["More", "Save_As"]])
        self.assertFalse(secs[0][1].enabled)
        self.assertTrue(secs[0][0].enabled)
        self.assertIsNone(secs[0][0].checked)
        self.assertEqual((secs[1][0].checked, secs[1][1].checked), (True, False))
        self.assertEqual([[i.label for i in s] for s in secs[2][0].submenu], [["Sub One"]])
        secs[1][1].on_activate(True)              # checkmark items get the new state
        secs[0][0].on_activate()
        self.assertEqual(clicked, [5, 1])

    def test_click_sends_event(self):
        win = Gtk.Window()
        box = tray.TrayBox()
        win.set_child(box)
        win.present()
        it = FakeItem("telegram", icon_name="mail-unread-symbolic", layout=sample_layout(submenu_filled=False))
        self.assertTrue(wait_for(lambda: it.key in box.buttons and box.buttons[it.key].get_mapped()))
        btn = box.buttons[it.key]
        pops = []
        box.show_menu(btn, self.host.items[it.key], done=pops.append)
        self.assertTrue(wait_for(lambda: pops))
        # AboutToShow(0), then the empty submenu's own
        self.assertIn(("AboutToShow", (0,)), it.calls)
        self.assertIn(("AboutToShow", (20,)), it.calls)
        self.assertTrue(btn.has_css_class("open"))
        pops[0].activate_action("m.i0_0", None)          # "Open Window"
        self.assertTrue(wait_for(lambda: (1, "clicked") in it.events))
        pops[0].popdown()
        self.assertTrue(wait_for(lambda: (0, "closed") in it.events))
        # left-click activates; middle click / scroll
        btn.emit("clicked")
        self.assertTrue(wait_for(lambda: any(c[0] == "Activate" for c in it.calls)))
        self.host.items[it.key].secondary_activate(1, 2)
        box._scroll(self.host.items[it.key], 0, 1)
        self.assertTrue(wait_for(lambda: ("Scroll", (1, "vertical")) in it.calls))
        self.assertIn(("SecondaryActivate", (1, 2)), it.calls)
        box.stop()
        win.destroy()
        it.quit()


if __name__ == "__main__":
    unittest.main()
