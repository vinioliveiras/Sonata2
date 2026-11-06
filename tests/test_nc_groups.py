"""Vini: Notification Center listed every notification as a card of its
own -- ten messages from one chat were ten cards. As on macOS, an app's
several notifications are one stack now: its newest card, the next ones
peeking under it; a click opens it into its cards under a header (the app,
Show Less, an x that clears the group). One notification stays a card;
banners don't change.

    xvfb-run -a python3 -m unittest tests.test_nc_groups
"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import notifications as N  # noqa: E402


def note(i, app="Chat", desktop="", **kw):
    return N.Note(i, app, "", f"Title {i}", f"body {i}", [], desktop, **kw)


def spin(ms=0):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while True:
        while ctx.pending():
            ctx.iteration(False)
        if GLib.get_monotonic_time() >= end:
            break


class LayoutTest(unittest.TestCase):
    def test_one_apps_notifications_were_a_long_list(self):
        """Three from Chat and one from Mail: Chat's are one stack, where
        its newest one is; Mail's stays a card."""
        newest_first = [note(4), note(3, "Mail"), note(2), note(1)]
        units = N.layout(newest_first)
        self.assertEqual([(k, key) for k, key, _w in units], [("stack", "stack:Chat"), ("card", 3)])
        self.assertEqual([n.id for n in units[0][2]], [4, 2, 1])            # newest on top

    def test_single_notification_stays_a_card(self):
        units = N.layout([note(1), note(2, "Mail")])
        self.assertEqual([k for k, _key, _w in units], ["card", "card"])

    def test_grouped_by_desktop_entry_then_name(self):
        """The app's desktop entry groups (as Settings > Notifications keys
        apps); without one, its name; with neither, each on its own."""
        a = note(3, "WhatsApp", "web-wa")
        b = note(2, "Chrome", "web-wa")
        c, d = note(1, ""), N.Note(5, "", "", "x", "", [], "")
        keys = [key for _k, key, _w in N.layout([d, a, b, c])]
        self.assertEqual(keys, [5, "stack:web-wa", 1])

    def test_expanded_group_is_a_header_and_its_cards(self):
        units = N.layout([note(3), note(2), note(1, "Mail")], expanded={"Chat"})
        self.assertEqual([(k, key) for k, key, _w in units],
                         [("head", "head:Chat"), ("card", 3), ("card", 2), ("card", 1)])


class CenterGroupsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        owner = N.Notifications.__new__(N.Notifications)
        owner.notes, owner.listeners, owner._banners, owner._conn, owner.nc = [], [], {}, None, None
        self.changes = 0

        def changed():
            self.changes += 1
            for cb in list(owner.listeners):
                cb()
        owner._changed = changed
        self.owner = owner
        self.c = N._Center(None, owner)

    def tearDown(self):
        self.c.destroy()

    def order(self):
        keys = {id(s): k for k, s in self.c._slots.items()}
        out, x = [], self.c.col.get_first_child()
        while x is not None:
            if id(x) in keys:
                out.append(keys[id(x)])
            x = x.get_next_sibling()
        return out

    def stack(self, key="Chat"):
        return self.c._slots["stack:" + key].get_child()

    def test_stack_shows_two_edges_at_most(self):
        """Under the newest card, one thin edge for two notifications, two
        for three or more (macOS) -- each drawing its own glass."""
        self.owner.notes = [note(1), note(2)]
        self.c._rebuild()
        peeks = [w for w, _k in self.c.col._panels() if w.has_css_class("nt-peek")]
        self.assertEqual(len(peeks), 1)
        self.owner.notes = [note(i) for i in range(1, 6)]
        self.c._rebuild()
        peeks = [w for w, _k in self.c.col._panels() if w.has_css_class("nt-peek")]
        self.assertEqual(len(peeks), 2)
        self.assertEqual(len([w for w, _k in self.c.col._panels() if w.has_css_class("nt-card")]), 1)

    def test_stack_click_opens_it_and_show_less_closes_it(self):
        self.owner.notes = [note(1, "Mail"), note(2), note(3)]
        self.c._rebuild()
        self.assertEqual(self.order(), ["stack:Chat", 1])
        self.c._expand("Chat")                                          # (the stack's click)
        self.assertEqual(self.order(), ["head:Chat", 3, 2, 1])
        head = self.c._slots["head:Chat"].get_child()
        texts = []
        x = head.get_first_child()
        while x is not None:
            texts.append(x.get_label() if hasattr(x, "get_label") else None)
            x = x.get_next_sibling()
        self.assertIn("Chat", texts)
        self.assertIn(N._("Show Less"), texts)
        self.c._expand("Chat", False)
        self.assertEqual(self.order(), ["stack:Chat", 1])

    def test_stack_card_click_expands_not_opens(self):
        """A click on a stack's top card opens the stack, not the app."""
        self.owner.notes = [note(1), note(2)]
        made = {}
        real = N.Notifications.card

        def card(n, banner=False, **kw):
            made.update(kw)
            return real(self.owner, n, banner, **kw)
        self.owner.card = card
        self.c._rebuild()
        with mock.patch.object(self.c, "_expand") as expand:
            made["on_click"]()
        expand.assert_called_once_with("Chat")

    def test_group_x_clears_only_that_app_at_once(self):
        """The header's x clears the app's notifications -- one change, so
        they leave together -- and leaves the others."""
        self.owner.notes = [note(1, "Mail"), note(2), note(3)]
        self.c._rebuild()
        self.c._expand("Chat")
        self.changes = 0
        self.c._slots["head:Chat"].get_child().clear_button.emit("clicked")
        self.assertEqual([n.id for n in self.owner.notes], [1])
        self.assertEqual(self.changes, 1)

    def test_clear_all_is_one_change(self):
        """Clear All: every notification gone in one change (a stack cleared
        one by one turned into a card on its way out)."""
        self.owner.notes = [note(1, "Mail"), note(2), note(3)]
        self.changes = 0
        self.owner.clear()
        self.assertEqual(self.owner.notes, [])
        self.assertEqual(self.changes, 1)

    def test_group_down_to_one_is_a_card_again(self):
        self.owner.notes = [note(1), note(2)]
        self.c._rebuild()
        self.c._expand("Chat")
        self.owner.notes = [note(1)]
        self.c._rebuild()
        self.assertEqual(self.order(), [1])
        self.assertEqual(self.c._expanded, set())                        # (a new one stacks again)
        self.owner.notes.append(note(5))
        self.c._rebuild()
        self.assertEqual(self.order(), ["stack:Chat"])

    def test_new_notification_tops_the_stack(self):
        self.owner.notes = [note(1), note(2)]
        self.c._rebuild()
        old = self.c._slots["stack:Chat"]
        self.owner.notes.append(note(3))
        self.c._rebuild()
        self.assertIsNot(self.c._slots["stack:Chat"], old)
        self.assertIs(self.c._shown["stack:Chat"][0], self.owner.notes[-1])

    def test_closing_an_expanded_card_slides_it_out(self):
        """In an open group, a card's x closes that one notification, which
        slides out as before; the group stays open."""
        one, two, three = note(1), note(2), note(3)
        self.owner.notes = [one, two, three]
        self.c._rebuild()
        self.c._expand("Chat")
        with mock.patch.object(self.c, "get_visible", return_value=True):
            self.owner.notes = [one, three]
            self.c._changed()
        self.assertNotIn(2, self.c._slots)
        self.assertEqual(len(self.c._leaving), 1)
        self.assertIn("head:Chat", self.c._slots)


if __name__ == "__main__":
    unittest.main()
