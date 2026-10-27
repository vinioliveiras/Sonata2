"""Vini: every Sonata dialog (alerts: Edit Web App, errors, confirmations)
and the panels that are forms (Change Icon, Get Info, Resize...) have the
windows' close dot, in their look and on their side; it closes like Escape.
Not where a Cancel / Close / Quit button already closes them, nor beside
OK / Done / Later / Not Now / Don't Allow / Revert / Stop, nor on a
one-button alert (Vini: Restart's "Later / Restart" alert had one).
xvfb-run -a python3 -m unittest tests.test_dialog_close"""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import window as W  # noqa: E402


def settle(ms=500):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def find(root, css):
    stack = [root]
    while stack:
        w = stack.pop()
        if css in w.get_css_classes():
            return w
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return None


class AlertCloseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def _alert(self, answers, **kw):
        # every button acts (a disk's "in use" choices, from GIO): the dot is the way out
        dlg = ui.dialog.alert("The disk is in use", "Some apps are still using it.",
                              [("keep", "Unmount Anyway", ""), ("eject", "Eject", "default")], answers.append, **kw)
        settle()
        return dlg, (dlg.get_root() or dlg)

    def test_close_dot_closes_like_escape(self):
        answers = []
        dlg, win = self._alert(answers)
        box = find(win, "traffic-close-only")
        self.assertIsNotNone(box)
        self.assertIsNotNone(find(box, "tl-close"))                  # the windows' own button (look, colours)
        self.assertIn("has-close", dlg.get_css_classes())
        find(box, "tl-close").emit("clicked")
        settle(800)
        self.assertEqual(answers, ["keep"])                          # what Escape answers
        self.assertFalse(win.get_visible())

    def test_on_the_windows_side(self):
        for side, align in (("left", Gtk.Align.START), ("right", Gtk.Align.END)):
            with mock.patch.object(W, "buttons_side", return_value=side):
                answers = []
                dlg, win = self._alert(answers)
                self.assertEqual(find(win, "traffic-close-only").get_halign(), align, side)
                ui.dialog._close(dlg)
                settle(600)

    def test_only_one_close_dot(self):
        """Its close dot alone (no minimize / zoom)."""
        dlg, win = self._alert([])
        box = find(win, "traffic-close-only")
        kids = []
        c = box.get_first_child()
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        self.assertEqual([k.get_css_classes() for k in kids], [["tl-close"]])
        ui.dialog._close(dlg)
        settle(600)


class PanelCloseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_form_panels_only(self):
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(300)
        plain = ui.panel.popup(anchor, Gtk.Label(label="tool"))
        self.assertIsNone(find(plain, "traffic-close-only"))          # menu bar / tool panels: none
        plain.unparent()
        form = ui.panel.popup(anchor, Gtk.Label(label="form"), close=True)
        form.popup()
        settle(300)
        self.assertTrue(form.get_visible())
        find(form, "tl-close").emit("clicked")
        settle(300)
        self.assertFalse(form.get_visible())
        win.destroy()

    def test_never_twice(self):
        """Some already have window buttons (Vini): no second close dot."""
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(200)
        own = Gtk.Box()
        own.append(W.traffic_lights(lambda: None, lambda: None))
        pop = ui.panel.popup(anchor, own, close=True)
        self.assertIsNone(find(pop, "traffic-close-only"))
        win.destroy()
        answers = []
        dlg = ui.dialog.alert("T", "b", [("ok", "OK", "")], answers.append)
        settle()
        ui.dialog._add_close(dlg)                                     # again: still one
        boxes, stack = [], [dlg]
        while stack:
            w = stack.pop()
            if "traffic-close-only" in w.get_css_classes():
                boxes.append(w)
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(len(boxes), 1)
        ui.dialog._close(dlg)
        settle(600)

    def test_none_next_to_cancel_or_quit(self):
        """Vini: a close dot beside Cancel or Quit makes no sense."""
        for responses in ([("cancel", "Cancel", ""), ("create", "Save", "default")],
                          [("no", "Quit", ""), ("yes", "Keep Open", "default")],
                          [("x", "Close", "")]):
            dlg = ui.dialog.alert("T", "b", responses, lambda *_a: None)
            settle()
            self.assertIsNone(find(dlg.get_root() or dlg, "traffic-close-only"), responses)
            self.assertNotIn("has-close", dlg.get_css_classes())
            ui.dialog._close(dlg)
            settle(600)
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(200)
        form = Gtk.Box()
        form.append(Gtk.Button(label="Cancel"))
        form.append(Gtk.Button(label="Resize"))
        self.assertIsNone(find(ui.panel.popup(anchor, form, close=True), "traffic-close-only"))
        win.destroy()

    def test_no_close_dot_on_restart_later_alert(self):
        """Vini: "Restart the computer now?" (Later / Restart) showed a close
        dot though Later already closes it. Same for OK-only, Done, Don't
        Allow, Revert (display settings) and Stop (copy conflicts)."""
        for responses in ([("later", "Later", ""), ("now", "Restart", "default")],
                          [("ok", "OK", "default")],
                          [("only", "Unlock", "default")],
                          [("done", "Done", "default"), ("more", "Show More", "")],
                          [("deny", "Don’t Allow", ""), ("allow", "Allow", "default")],
                          [("revert", "Revert", ""), ("keep", "Keep", "default")],
                          [("keep", "Keep Both", ""), ("stop", "Stop", ""), ("replace", "Replace", "")]):
            self.assertFalse(ui.dialog.needs_close(responses), responses)
            dlg = ui.dialog.alert("T", "b", responses, lambda *_a: None)
            settle()
            self.assertIsNone(find(dlg.get_root() or dlg, "traffic-close-only"), responses)
            ui.dialog._close(dlg)
            settle(600)
        self.assertTrue(ui.dialog.needs_close([("a", "Unmount Anyway", ""), ("b", "Eject", "default")]))

    def test_dismiss_labels(self):
        from sonata2 import i18n
        try:
            for lang in ("en", "pt_BR", "pt_PT"):
                i18n.use(lang)
                for t in (i18n._("Cancel"), i18n._("Quit") + "…", "_Close", i18n._("OK"), i18n._("Later"),
                          i18n._("Not Now"), i18n._("Done"), "Don’t Allow", i18n._("Don't Allow")):
                    self.assertTrue(W.dismiss_label(t), (lang, t))
                for t in ("Save", "", None, i18n._("Delete"), i18n._("Restart"), i18n._("Allow")):
                    self.assertFalse(W.dismiss_label(t), (lang, t))
        finally:
            i18n.use(None)

    def test_forms_ask_for_it(self):
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sonata2")
        for rel, call in (("settings/appicons_page.py", "ui.panel.popup(row, col"),
                          ("activity/window.py", "ui.panel.popup(anchor, box"),
                          ("diskutil/window.py", 'ui.panel.popup(self.btn["info"]'),
                          ("assistant/window.py", "ui.panel.popup(self.settings_btn"),
                          ("calendar/window.py", "ui.panel.popup(widget, box"),
                          ("preview/window.py", "ui.panel.popup(self.adjust_btn")):
            with open(os.path.join(root, rel)) as f:
                src = f.read()
            i = src.index(call)
            self.assertRegex(src[i:i + 200], re.compile(r"close=True", re.S), rel)


if __name__ == "__main__":
    unittest.main()
