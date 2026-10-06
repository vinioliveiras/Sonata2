"""Review tests for the UI kit (sonata2/ui) and Settings (sonata2/settings):
token/colour maths, formatting, menu model building, alert defaults,
controls, theme registration and Settings helpers.

Run: xvfb-run -a python3 -m unittest tests.test_review_ui"""
import gc
import json
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.ui import fmt, glass as G, tokens as T  # noqa: E402

Adw.init()
ui.setup()


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def walk(widget, cls):
    """Every descendant of `widget` that is a `cls`."""
    out = []
    c = widget.get_first_child()
    while c is not None:
        if isinstance(c, cls):
            out.append(c)
        out += walk(c, cls)
        c = c.get_next_sibling()
    return out


class TempConfig(unittest.TestCase):
    """Each test gets its own empty config folder (never the real one)."""

    def setUp(self):
        self._dir = tempfile.mkdtemp()
        p = mock.patch.object(config, "CONFIG_DIR", self._dir)
        p.start()
        self.addCleanup(p.stop)
        T._radii_cache.update(mtime=None, value=dict(T.RADIUS_DEFAULTS), set={})
        self.addCleanup(T._radii_cache.update, mtime=None, value=dict(T.RADIUS_DEFAULTS), set={})

    def write(self, name, text):
        with open(os.path.join(self._dir, name + ".json"), "w", encoding="utf-8") as f:
            f.write(text)


# -- tokens: colour and size maths ----------------------------------------------------------
class TokenMathTests(unittest.TestCase):
    def test_wayfire_color(self):
        """Wayfire's #rrggbbaa: hex gets an opaque alpha, rgba() keeps its
        alpha, premultiplied scales the channels (pixdecor's blending)."""
        self.assertEqual(T.wayfire_color("#112233"), "#112233ff")
        self.assertEqual(T.wayfire_color("rgba(255, 0, 128, 0.5)"), "#ff008080")
        self.assertEqual(T.wayfire_color("rgba(200, 100, 50, 0.5)", premultiplied=True), "#64321980")
        self.assertEqual(T.wayfire_color("rgb(1, 2, 3)"), "#010203ff")

    def test_over_composites(self):
        """over(): alpha 0 gives the base, alpha 1 the colour, 0.5 the mean."""
        self.assertEqual(T.over("rgba(0, 0, 0, 0)", "#ffffff"), "rgb(255, 255, 255)")
        self.assertEqual(T.over("rgba(10, 20, 30, 1)", "#ffffff"), "rgb(10, 20, 30)")
        self.assertEqual(T.over("rgba(0, 0, 0, 0.5)", "#c8c8c8"), "rgb(100, 100, 100)")

    def test_title_bars_opaque_and_glass_kept(self):
        """Title bars are the old glass flattened over window_bg (opaque);
        the glass survives as titlebar_glass for "Glass title bars"."""
        for pal in (T.LIGHT, T.DARK):
            for k in ("titlebar_bg", "titlebar_bg_inactive"):
                self.assertTrue(pal[k].startswith("rgb("), pal[k])
                self.assertTrue(pal[k.replace("_bg", "_glass")].startswith("rgba("))

    def test_light_and_dark_have_the_same_tokens(self):
        """Every colour token exists in both appearances (a missing one is a
        KeyError in a CSS template only in one mode)."""
        self.assertEqual(set(T.LIGHT), set(T.DARK))

    def test_ms_scales_and_never_zero(self):
        """ms(): designed durations at ANIMATION_SPEED; a positive one never
        rounds to 0 (GTK would skip the animation), 0/negative stay 0."""
        self.assertEqual(T.ms(0), 0)
        self.assertEqual(T.ms(-5), 0)
        self.assertEqual(T.ms(0.1), 1)
        self.assertEqual(T.ms(130), round(130 / T.ANIMATION_SPEED))

    def test_button_layout_sides(self):
        """GNOME/pixdecor button-layout for buttons on the left and on the right."""
        self.assertEqual(T.button_layout(dict(T.FRAME, buttons_side="left")), "close,minimize,maximize:")
        self.assertEqual(T.button_layout(dict(T.FRAME, buttons_side="right")), ":maximize,minimize,close")

    def test_radius_tokens_scale_with_the_user_radii(self):
        """Small radii follow their group's ratio; square corners give 0 px everywhere."""
        zero = T.radius_tokens({"window": 0, "dock": 0, "menu": 0})
        self.assertTrue(all(v == "0px" for v in zero.values()), zero)
        big = T.radius_tokens({"window": 20, "dock": 30, "menu": 14})
        self.assertEqual((big["r_button"], big["r_menu_row"], big["r_label"], big["r_dialog"]),
                         ("10px", "8px", "12px", "24px"))

    def test_named_accent_selected_is_darker_in_light(self):
        """Light mode: the selected-row colour of a named accent is the accent darkened."""
        t = T.accent_tokens("green", False)
        self.assertEqual(t["accent"], T.ACCENTS["green"][0])
        self.assertEqual(t["accent_selected"], T._darker(T.ACCENTS["green"][0]))
        self.assertEqual(T.accent_tokens("nope", True), {})


class UserRadiiTests(TempConfig):
    def test_corrupt_or_odd_files_fall_back(self):
        """A broken appearance.json, or "radius" not a dict, never raises: defaults."""
        for text in ("{not json", "[]", '{"radius": 5}', '{"radius": {"window": "big"}}'):
            self.write("appearance", text)
            os.utime(os.path.join(self._dir, "appearance.json"), (time.time(), time.time() + hash(text) % 1000))
            self.assertEqual(T.user_radii(), T.RADIUS_DEFAULTS, text)

    def test_reread_when_the_file_changes(self):
        """The cache follows the file's mtime: a new value is seen at once."""
        self.write("appearance", json.dumps({"radius": {"dock": 4}}))
        self.assertEqual(T.user_radii()["dock"], 4)
        self.write("appearance", json.dumps({"radius": {"dock": -9}}))
        os.utime(os.path.join(self._dir, "appearance.json"), (1, 1))
        self.assertEqual(T.user_radii()["dock"], T.RADIUS_RANGE["dock"][0])      # clamped


# -- fmt ------------------------------------------------------------------------------------
class FmtTests(unittest.TestCase):
    def test_size_units(self):
        """Finder's decimal units at the boundaries."""
        self.assertEqual(fmt.size(0), "Zero bytes")
        self.assertEqual(fmt.size(-3), "Zero bytes")
        self.assertEqual(fmt.size(999), "999 bytes")
        self.assertEqual(fmt.size(1000), "1 KB")
        self.assertEqual(fmt.size(1_400_000), "1.4 MB")
        self.assertEqual(fmt.size(5 * 1000 ** 5), "5000.0 TB")              # no unit past TB

    def test_size_rounding_never_shows_1000_kb(self):
        """fmt.size moves up a unit when rounding would show "1000 KB"/"1000.0 MB"."""
        self.assertEqual(fmt.size(999_500), "1.0 MB")
        self.assertEqual(fmt.size(999_990_000), "1.0 GB")

    def test_eta_phrases(self):
        """Finder's remaining-time phrases, rounded to 5 s / minutes / hours."""
        self.assertEqual(fmt.eta(0), "About 5 seconds")
        self.assertEqual(fmt.eta(11), "About 15 seconds")
        self.assertEqual(fmt.eta(75), "About a minute")
        self.assertEqual(fmt.eta(600), "About 10 minutes")
        self.assertEqual(fmt.eta(3600), "About an hour")
        self.assertEqual(fmt.eta(3 * 3600), "About 3 hours")

    def test_date_sections(self):
        """List sections: Pinned, Today, Yesterday, 7/30 days, months, years."""
        now = time.mktime((2026, 6, 20, 12, 0, 0, 0, 0, -1))
        day = 86400
        self.assertEqual(fmt.date_section(now, pinned=True, now=now)[2], "Pinned")
        self.assertEqual(fmt.date_section(now + 60, now=now)[2], "Today")            # clock skew: still today
        self.assertEqual(fmt.date_section(now - day, now=now)[2], "Yesterday")
        self.assertEqual(fmt.date_section(now - 5 * day, now=now)[2], "Previous 7 Days")
        self.assertEqual(fmt.date_section(now - 20 * day, now=now)[2], "Previous 30 Days")
        march = fmt.date_section(time.mktime((2026, 3, 2, 12, 0, 0, 0, 0, -1)), now=now)
        jan = fmt.date_section(time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1)), now=now)
        self.assertLess(march, jan)                                                     # newer month first
        self.assertEqual(fmt.date_section(time.mktime((2024, 5, 1, 0, 0, 0, 0, 0, -1)), now=now)[2], "2024")


# -- colours of glass and the picker ----------------------------------------------------------
class ColourHelperTests(unittest.TestCase):
    def test_blur_offset_clamped(self):
        """Blur strength 0..100 maps onto Wayfire's kawase_offset range, clamped."""
        self.assertEqual(G.blur_offset(-20), G.OFFSET_RANGE[0])
        self.assertEqual(G.blur_offset(500), G.OFFSET_RANGE[1])
        self.assertEqual(G.blur_offset(50), 4.5)

    def test_picker_mix(self):
        """Picker shades: 0 keeps the hue, + tints to white, - shades to black."""
        from sonata2.ui import colorpicker as C
        self.assertEqual(C._mix("#336699", 0.0), "#336699")
        self.assertEqual(C._mix("#000000", 1.0), "#ffffff")
        self.assertEqual(C._mix("#ffffff", -1.0), "#000000")
        flat = [c for col in C.palette() for c in col]
        self.assertTrue(all(C.parse_hex(c) == c for c in flat))                  # every swatch a valid colour


# -- menu model -------------------------------------------------------------------------------
class MenuModelTests(unittest.TestCase):
    def test_build_actions_and_states(self):
        """Items become "m.<name>" actions: plain ones call back with no
        args, check items with the new state, disabled ones are disabled,
        submenus nest and closable rows become custom slots."""
        M = ui.menu
        got = []
        group, customs = Gio.SimpleActionGroup(), []
        model = M._build([
            [M.Item("Open", lambda: got.append("open")), M.Item("Off", None, enabled=False)],
            [M.Item("Keep", lambda on: got.append(on), checked=True),
             M.Item("Sub", submenu=[[M.Item("Inner", lambda: got.append("inner"))]])],
            [M.Item("Window", lambda: None, on_close=lambda: None)],
        ], group, customs=customs)
        self.assertEqual(model.get_n_items(), 3)                                 # one per section
        self.assertFalse(group.lookup_action("i0_1").get_enabled())
        self.assertTrue(group.lookup_action("i1_0").get_state().get_boolean())
        group.activate_action("i0_0", None)
        group.activate_action("i1_0", None)
        group.activate_action("i1_1_0_0", None)
        self.assertEqual(got, ["open", False, "inner"])
        self.assertEqual([n for n, _ in customs], ["i2_0"])
        self.assertIsNone(group.lookup_action("i2_0"))                          # its own row, no action

    def test_closed_menu_leaves_open_set(self):
        """A menu counts in OPEN while shown and leaves it on close (an
        auto-hiding Dock waits on it)."""
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(50)
        pop = ui.menu.popup(anchor, [[ui.menu.Item("A", lambda: None)]])
        self.assertIn(pop, ui.menu.OPEN)
        pop.popdown()
        settle(50)
        self.assertNotIn(pop, ui.menu.OPEN)
        win.destroy()


    def test_menu_actions_live_on_the_menu(self):
        """The menu's actions hang on the menu, not its anchor (no leftover group or clash)."""
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(50)
        got = []
        pop = ui.menu.popup(anchor, [[ui.menu.Item("A", lambda: got.append(1))]])
        self.assertFalse(anchor.activate_action("m.i0_0", None))
        self.assertTrue(pop.activate_action("m.i0_0", None))
        self.assertEqual(got, [1])
        pop.popdown()
        settle(50)
        win.destroy()


# -- alerts -----------------------------------------------------------------------------------
class DialogTests(unittest.TestCase):
    """Alert logic with libadwaita's dialog replaced by a recorder
    (Adw.AlertDialog.set_content_width crashes under this xvfb; the other
    suites mock ui.dialog.alert too)."""

    def setUp(self):
        self.made = []

        def fake(**kw):
            d = mock.MagicMock(name="AlertDialog")
            d.get_root.return_value = None
            d.kw = kw
            self.made.append(d)
            return d
        p1 = mock.patch.object(ui.dialog, "_MODERN", True)
        p2 = mock.patch.object(ui.dialog.Adw, "AlertDialog", side_effect=fake, create=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def responses(self, d):
        return [c.args[0] for c in d.add_response.call_args_list]

    def test_breakable_long_words(self):
        """Long unbroken words get zero-width breaks (an alert shook without
        them); normal text and None are safe."""
        self.assertEqual(ui.dialog._breakable("short words here"), "short words here")
        self.assertEqual(ui.dialog._breakable(None), "")
        out = ui.dialog._breakable("a" * 20)
        self.assertIn("\u200b", out)
        self.assertEqual(out.replace("\u200b", ""), "a" * 20)

    def test_default_response(self):
        """Return: the "default"-styled response; without one, the first
        non-destructive (Cancel) -- never the destructive one."""
        d = ui.dialog.alert("H", "B", [("cancel", "Cancel", ""), ("ok", "OK", "default")])
        d.set_default_response.assert_called_once_with("ok")
        self.assertEqual(self.responses(d), ["cancel", "ok"])
        d = ui.dialog.alert("H", "B", [("cancel", "Cancel", ""), ("erase", "Erase", "destructive")])
        d.set_default_response.assert_called_once_with("cancel")
        d.set_close_response.assert_called_once_with("cancel")
        d.set_response_appearance.assert_called_once_with("erase", Adw.ResponseAppearance.DESTRUCTIVE)

    def test_escape_never_picks_the_destructive_response(self):
        """Escape gives "cancel", never a destructive response listed first."""
        d = ui.dialog.alert("Save?", "", [("discard", "Don't Save", "destructive"), ("cancel", "Cancel", ""),
                                          ("save", "Save", "default")])
        d.set_close_response.assert_called_once_with("cancel")

    def test_check_box_reports_its_state(self):
        """check=...: on_response gets (response, checked)."""
        got = []
        d = ui.dialog.alert("H", "B", [("cancel", "Cancel", ""), ("ok", "OK", "default")],
                            lambda *a: got.append(a), check="Apply to All")
        box = d.set_extra_child.call_args.args[0]
        self.assertIsInstance(box, Gtk.CheckButton)
        handler = d.connect.call_args.args[1]
        box.set_active(True)
        handler(d, "ok")
        self.assertEqual(got, [("ok", True)])

    def test_ask_text_ok_off_while_empty(self):
        """ask_text opened with an empty text starts with OK off."""
        d = ui.dialog.ask_text("New Folder", "", "Create", lambda _t: None)
        d.set_response_enabled.assert_any_call("ok", False)

    def test_ask_text_strips_and_ignores_blank(self):
        """ask_text: on_done gets the stripped text, nothing for blanks or
        Cancel; the field is the kit's and OK follows its text."""
        got = []
        d = ui.dialog.ask_text("Rename", "  old  ", "Rename", got.append)
        answer = d.connect.call_args.args[1]
        d.entry.set_text("  new name ")
        answer(d, "ok")
        d.entry.set_text("   ")
        d.set_response_enabled.assert_called_with("ok", False)
        answer(d, "ok")
        d.entry.set_text("x")
        answer(d, "cancel")
        self.assertEqual(got, ["new name"])
        self.assertTrue(d.entry.has_css_class("sonata-field"))
        settle(20)

    def test_mount_question_answers(self):
        """Mount questions: a digit picks that choice; closing aborts."""
        from sonata2.ui import mountop as M
        op = M.MountOperation(None)
        replies = []
        op.connect("reply", lambda _o, r: replies.append(r))
        op.emit("ask-question", "Disk busy\nApps use it", ["Unmount Anyway", "Cancel"])
        d = self.made[-1]
        self.assertEqual(d.kw["heading"], "Disk busy")
        d.set_default_response.assert_called_once_with("1")            # the last choice: Return
        d.connect.call_args.args[1](d, "1")
        self.assertEqual((replies, op.get_choice()), ([Gio.MountOperationResult.HANDLED], 1))
        op.emit("ask-question", "Again", ["A", "B"])
        self.made[-1].connect.call_args.args[1](self.made[-1], "close")
        self.assertEqual(replies[-1], Gio.MountOperationResult.ABORTED)


    def test_escape_skips_destructive_without_cancel(self):
        """No "cancel": Escape gives the first non-destructive response."""
        d = ui.dialog.alert("Erase?", "", [("erase", "Erase", "destructive"), ("keep", "Keep", "")])
        d.set_close_response.assert_called_once_with("keep")

    def test_mount_question_escape_aborts(self):
        """Mount questions: Escape is "close" (aborts), never the first choice ("Unmount Anyway")."""
        from sonata2.ui import mountop as M
        op = M.MountOperation(None)
        op.emit("ask-question", "Disk busy", ["Unmount Anyway", "Cancel"])
        self.made[-1].set_close_response.assert_called_once_with("close")

    def test_mount_password_fields_are_the_kits(self):
        """Mount password prompt: name and password are ui.controls text fields; Return confirms."""
        from sonata2.ui import mountop as M
        op = M.MountOperation(None)
        F = Gio.AskPasswordFlags
        op.emit("ask-password", "Unlock", "me", "", F.NEED_USERNAME | F.NEED_PASSWORD)
        box = self.made[-1].set_extra_child.call_args.args[0]
        fields = [w for w in walk(box, Gtk.Widget) if isinstance(w, (Gtk.Entry, Gtk.PasswordEntry))]
        self.assertEqual(len(fields), 2)
        for f in fields:
            self.assertTrue(f.has_css_class("sonata-field"))
            self.assertTrue(f.get_property("activates-default"))
        self.assertEqual(fields[0].get_text(), "me")
        settle(20)


# -- controls ---------------------------------------------------------------------------------
class ControlsTests(unittest.TestCase):
    def test_is_release(self):
        """A slider is "let go" on left-button up, touch end or an arrow key up only."""
        E = Gdk.EventType
        C = ui.controls
        self.assertTrue(C.is_release(E.BUTTON_RELEASE, button=1))
        self.assertFalse(C.is_release(E.BUTTON_RELEASE, button=3))
        self.assertTrue(C.is_release(E.TOUCH_END))
        self.assertTrue(C.is_release(E.KEY_RELEASE, keyval=Gdk.KEY_Left))
        self.assertFalse(C.is_release(E.KEY_RELEASE, keyval=Gdk.KEY_a))
        self.assertFalse(C.is_release(E.BUTTON_PRESS, button=1))

    def test_module_slider_clamps_and_signals_changes_only(self):
        """Control Center slider: values clamp to its range; "value-changed"
        only when the value really changes."""
        s = ui.controls.slider(50, style="module")
        got = []
        s.connect("value-changed", lambda sl: got.append(sl.get_value()))
        s.set_value(150)
        s.set_value(100)
        s.set_value(-4)
        self.assertEqual(got, [100.0, 0.0])

    def test_text_field_kinds_and_callbacks(self):
        """text_field: a password field when secret; on_change/on_activate get the text."""
        got = []
        e = ui.controls.text_field("a", "Name", on_change=lambda t: got.append(("c", t)),
                                   on_activate=lambda t: got.append(("a", t)))
        self.assertIsInstance(e, Gtk.Entry)
        e.set_text("ab")
        e.emit("activate")
        self.assertEqual(got[-2:], [("c", "ab"), ("a", "ab")])
        p = ui.controls.text_field("pw", secret=True)
        self.assertIsInstance(p, Gtk.PasswordEntry)
        self.assertTrue(p.has_css_class("sonata-field"))
        self.assertEqual(p.get_text(), "pw")

    def test_text_area_placeholder_and_submit(self):
        """TextArea: placeholder hides with text; Return submits, Shift+Return doesn't."""
        got = []
        ta = ui.controls.TextArea("Message", on_submit=got.append)
        self.assertTrue(ta.placeholder.get_visible())
        ta.set_text("hi")
        self.assertFalse(ta.placeholder.get_visible())
        self.assertFalse(ta._key(None, Gdk.KEY_Return, 0, Gdk.ModifierType.SHIFT_MASK))
        self.assertTrue(ta._key(None, Gdk.KEY_Return, 0, 0))
        self.assertEqual(got, ["hi"])
        settle(30)


    def test_module_slider_honours_default(self):
        """slider(style="module", default=...) gets the double-click reset too."""
        with mock.patch.object(ui.controls, "reset_on_double_click") as reset:
            s = ui.controls.slider(10, style="module", default=30)
        reset.assert_called_once_with(s, 30)

    def test_push_button_takes_widget_props(self):
        """push_button(..., valign=...) passes Gtk.Button properties (Settings' row buttons)."""
        b = ui.controls.push_button("Go", style="default", valign=Gtk.Align.CENTER)
        self.assertEqual(b.get_valign(), Gtk.Align.CENTER)
        self.assertTrue(b.has_css_class("sonata-button") and b.has_css_class("default"))


# -- theme ------------------------------------------------------------------------------------
class ThemeTests(unittest.TestCase):
    def test_register_same_key_replaces(self):
        """Registering a key again replaces its CSS (no pile of templates)."""
        T_ = ui.theme
        n = len(T_._templates)
        T_.register(".review-x { color: %(label)s; }", key="review-test")
        T_.register(".review-x { color: %(accent)s; }", key="review-test")
        self.assertEqual(len(T_._templates), n + 1)
        self.assertIn("%(accent)s", T_._templates["review-test"][0])
        del T_._templates["review-test"]

    def test_value_parsers(self):
        """px/shadow/rgba read tokens as numbers and colours."""
        self.assertEqual(ui.theme.px("control_h"), 22.0)
        dx, dy, blur, col = ui.theme.shadow("shadow_menu")
        self.assertEqual((dx, dy, blur), (0.0, 6.0, 18.0))
        self.assertGreater(col.alpha, 0)
        self.assertGreater(ui.theme.rgba("accent").alpha, 0.99)

    def test_animation_speed_in_css(self):
        """CSS durations are scaled to ANIMATION_SPEED; words ending in digits aren't touched."""
        sub = lambda s: ui.theme._MS.sub(lambda m: f"{T.ms(float(m.group(1)))}ms", s)      # noqa: E731
        self.assertEqual(sub("transition: opacity 130ms;"), f"transition: opacity {T.ms(130)}ms;")
        self.assertEqual(sub("anim-x2ms"), "anim-x2ms")

    def test_on_change_listeners_released_with_their_widget(self):
        """A ModuleSlider (Control Center, rebuilt on each open) used to stay in
        theme._listeners forever, kept alive and redrawn on every appearance
        change: bound methods are held weakly now, and off_change() works."""
        ui.theme._notify_listeners()
        n = len(ui.theme._listeners)
        class Drawn:                                       # a widget's queue_draw, held weakly
            def queue_draw(self):
                pass
        for _ in range(3):
            ui.theme.on_change(Drawn().queue_draw)
        gc.collect()
        ui.theme._notify_listeners()                       # prunes the dead ones
        self.assertEqual(len(ui.theme._listeners), n)
        calls = []
        h = ui.theme.on_change(lambda: calls.append(1))
        ui.theme._notify_listeners()
        ui.theme.off_change(h)
        ui.theme._notify_listeners()
        self.assertEqual(calls, [1])


# -- Settings helpers -------------------------------------------------------------------------
from sonata2.settings import app as S  # noqa: E402


class SettingsHelperTests(TempConfig):
    def test_sections_and_parts(self):
        """Merged parts open their section; _Pages finds a section by a part's id."""
        self.assertEqual(S.section_of("wallpaper"), "displays")
        self.assertEqual(S.section_of("wifi"), "wifi")
        self.assertEqual(S.parts_of("about"), ("about", "sonataupdate", "updates"))
        self.assertEqual(S.parts_of("wifi"), ("wifi",))
        pages = S._Pages()
        pages["displays"] = "page"
        self.assertIn("wallpaper", pages)
        self.assertEqual(pages.get("wallpaper"), "page")
        self.assertEqual(pages.pop("wallpaper"), "page")
        self.assertNotIn("displays", pages)
        ids = {s[0] for s in S.SECTIONS}
        self.assertTrue(set(S.PARTS) <= ids)
        for sec, parts in S.PARTS.items():                 # a part is never another sidebar section
            self.assertFalse((set(parts) - {sec}) & ids, sec)

    def test_merged_sections_find_parts_words_and_files(self):
        """Search finds a merged section by its parts' words; it rebuilds
        when any part's config changes."""
        self.assertIn("background", S.KEYWORDS["displays"])
        self.assertIn("Software Update", S.KEYWORDS["about"])
        self.assertIn("nightshift", S.PAGE_CONFIGS["displays"])
        self.assertIn("system", S.PAGE_CONFIGS["displays"])                       # from wallpaper

    def test_clock_format_and_nearest(self):
        """Date & Time's two switches make the menu bar format; nearest()
        snaps a system value onto an option."""
        self.assertEqual(S.clock_format(False, True), "%a %H:%M")
        self.assertEqual(S.clock_format(True, False), "%a %-d %b  %-I:%M %p")
        self.assertEqual(S.nearest([(1.0, ""), (1.25, ""), (2.0, "")], 1.7), 2.0)
        self.assertEqual(S._speed("-1"), 0)
        self.assertEqual(S._speed("1"), 100)
        self.assertEqual(S._speed("fast"), 50)

    def test_rows_quiet_and_unknown_values(self):
        """show_quietly never calls on_change; combo_row with an unknown
        value shows the first option; a choice reports its value."""
        got = []
        sw = S.switch_row("A & B", False, got.append)
        self.assertEqual(sw.get_title(), "A & B")                                  # plain text, not markup
        S.show_quietly(sw, True)
        self.assertTrue(sw.get_active())
        cb = S.combo_row("C", [("x", "X"), ("y", "Y")], "nope", got.append)
        self.assertEqual(cb.get_selected(), 0)
        S.show_quietly(cb, "y")
        S.show_quietly(cb, "missing")                                              # ignored
        self.assertEqual(cb.get_selected(), 1)
        self.assertEqual(got, [])
        cb.set_selected(0)
        sw.set_active(False)
        self.assertEqual(got, ["x", False])

    def test_slider_row_fixed_width(self):
        """Slider rows: one width for every slider, reset value on double-click wired."""
        row = S.slider_row("Speed", 30, 0, 100, lambda _v: None, ends=("Slow", "Fast"), default=50)
        self.assertEqual(row.slider.get_size_request()[0], S.SLIDER_W)
        self.assertFalse(row.slider.get_hexpand())
        self.assertEqual(row.slider.get_value(), 30)

    def test_clear_group(self):
        """_clear_group removes every row added to a preferences group."""
        g = S.group("G")
        for i in range(3):
            g.add(Adw.ActionRow(title=str(i)))
        S._clear_group(g)
        self.assertEqual(walk(g, Adw.ActionRow), [])

    def test_save_survives_a_non_dict_file(self):
        """Settings._save writes through config.update even over a null/[] file."""
        self.write("dock", "null")
        S.Settings._save(None, "dock", "autohide", True)
        self.assertEqual(config.load("dock", {"autohide": False})["autohide"], True)

    class _Live:
        GLASS_LIVE_MS = S.Settings.GLASS_LIVE_MS
        _save_live = S.Settings._save_live
        _flush_live = S.Settings._flush_live
        _live_tick = S.Settings._live_tick

    def test_slider_saves_are_throttled(self):
        """A dragged slider writes at most every GLASS_LIVE_MS, the last value
        wins, and the Dock's other keys (pins) stay."""
        self.write("dock", json.dumps({"pinned": ["a.desktop"]}))
        f = self._Live()
        with mock.patch.object(config, "update", wraps=config.update) as upd:
            for v in range(40, 80):
                f._save_live("dock", "icon_size", v)
            self.assertEqual(upd.call_count, 0)
            settle(f.GLASS_LIVE_MS + 80)
            self.assertEqual(upd.call_count, 1)
        self.assertEqual(config.load("dock", {"pinned": None, "icon_size": 0}),
                         {"pinned": ["a.desktop"], "icon_size": 79})

    def test_slider_save_flushed_on_close(self):
        """Closing Settings writes a slider's pending value at once."""
        f = self._Live()
        f._save_live("nightshift", "warmth", 70)
        f._flush_live()
        self.assertEqual(config.load("nightshift", {"warmth": 0})["warmth"], 70)
        settle(S.Settings.GLASS_LIVE_MS + 50)                  # its timer was removed: nothing else runs

    def test_settings_builds_with_the_kit(self):
        """Settings and the kit's prompts use ui.controls (text_field, push_button,
        dialog.ask_text): no raw Gtk.Entry or hand-made sonata-button."""
        import inspect
        from sonata2.settings import appicons_page, shortcuts_page
        from sonata2.ui import mountop
        for mod in (S, appicons_page, shortcuts_page, mountop):
            src = inspect.getsource(mod)
            self.assertNotRegex(src, r"Gtk\.(Password)?Entry\(", mod.__name__)
            self.assertNotIn('css_classes=["sonata-button"', src, mod.__name__)
        self.assertFalse(hasattr(S.Settings, "_ask_text"))

    def test_save_writes_only_into_the_temp_dir(self):
        """_save keeps the other keys and writes to the config folder in use."""
        self.write("topbar", json.dumps({"a": 1}))
        S.Settings._save(None, "topbar", "b", 2)
        with open(os.path.join(self._dir, "topbar.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1, "b": 2})

    def test_shortcut_keys_off(self):
        """A shortcut without keys shows "Off"."""
        from sonata2.settings import shortcuts_page as P
        box = P._keys("")
        self.assertEqual([w.get_label() for w in walk(box, Gtk.Label)], ["Off"])

    def test_appicons_describe(self):
        """App Icons rows: the source, plus the shape when it isn't the default."""
        from sonata2 import icons
        from sonata2.settings import appicons_page as P
        self.assertEqual(P.describe({"source": "auto", "shape": "squircle"}, "squircle"), "Sonata (default)")
        other = next(s for s in icons.SHAPES if s != "squircle")
        self.assertIn(icons.SHAPE_TITLES[other], P.describe({"source": "file", "shape": other}, "squircle"))


class SettingsWindowTests(TempConfig):
    def test_search_matches_every_word(self):
        """Sidebar search: every typed word must match the title or keywords."""
        w = S.Settings(None, "appearance")
        try:
            self.assertTrue(w._matches("displays", "Displays", "night shift"))
            self.assertTrue(w._matches("displays", "Displays", "wallpaper"))
            self.assertFalse(w._matches("displays", "Displays", "night bluetooth"))
            w.search.set_text("wallpaper")
            w._filter_sections()
            visible = [r.sid for r in w.rows.values() if r.get_visible()]
            self.assertIn("displays", visible)
            self.assertNotIn("wifi", visible)
        finally:
            w.destroy()

    def test_unavailable_style_choice_goes_back(self):
        """Style "Windows 11 (coming later)" saves mac and the pop-up shows Sonata again."""
        w = S.Settings(None, "appearance")
        w.present()
        settle(150)
        try:
            row = next(r for r in walk(w.pages["appearance"], Adw.ComboRow) if r.get_title() == "Style")
            row.set_selected(1)
            settle(50)
            self.assertEqual(config.load("appearance", {"theme": ""})["theme"], "mac")
            self.assertEqual(row.get_selected(), 0)
        finally:
            w.destroy()

    def test_wallpaper_page_released_when_rebuilt(self):
        """A rebuilt Displays page releases the wallpaper picture and its dark-mode handler."""
        w = S.Settings(None, "displays")
        w.present()
        settle(200)
        try:
            pic = next(p for p in walk(w.pages["displays"], Gtk.Picture) if p.has_css_class("st-wall"))
            gone = []
            pic.connect("destroy", lambda *_: gone.append(True))
            del pic
            w.select("appearance", from_sidebar=True)
            w.rebuild_page("displays")
            settle(200)
            gc.collect()
            settle(50)
            self.assertEqual(gone, [True])
        finally:
            w.destroy()


    def test_dock_sliders_save_through_the_throttle(self):
        """Dock > Size goes through _save_live (not a write per pixel)."""
        with mock.patch.object(S.Settings, "_save_live") as live:
            w = S.Settings(None, "dock")
            w.present()
            settle(150)
            try:
                row = next(r for r in walk(w.pages["dock"], Adw.ActionRow)
                           if r.get_title() == "Size" and hasattr(r, "slider"))
                row.slider.set_value(60)
                live.assert_called_with("dock", "icon_size", 60)
            finally:
                w.destroy()

    def test_app_icon_name_kept_without_return(self):
        """App Icons: the typed theme-icon name is saved when the field loses focus."""
        from sonata2 import icons
        from sonata2.settings import appicons_page as P

        class Info:
            def get_id(self): return "org.test.App.desktop"
            def get_icon(self): return Gio.ThemedIcon.new("text-editor")
            def get_name(self): return "Test"
            def get_display_name(self): return "Test App"
            def get_executable(self): return "testapp"
            def get_commandline(self): return "testapp"
            def get_startup_wm_class(self): return None
        icons.forget_prefs()
        with mock.patch.object(P, "app_list", return_value=[("org.test.App", Info())]):
            w = S.Settings(None, "appicons")
            w.present()
            w.select("appicons", from_sidebar=True)
            settle(300)
            try:
                page = w.appicons_page
                row = page.rows["org.test.App"]
                pop = page.edit(row)
                settle(50)
                pop.name.set_text("firefox")
                focus = next(c for c in pop.name.observe_controllers() if isinstance(c, Gtk.EventControllerFocus))
                focus.emit("leave")
                icons.forget_prefs()
                p = icons.app_pref(row.info)
                self.assertEqual((p["name"], p["source"]), ("firefox", "theme"))
                self.assertTrue(pop.name.has_css_class("sonata-field"))
                pop.popdown()
                settle(50)
            finally:
                w.destroy()
                icons.forget_prefs()


# -- animations -------------------------------------------------------------------------------
class AnimationTests(TempConfig):
    """The main actions of the kit and Settings animate."""

    def test_menus_and_panels_open_with_the_kit_animation(self):
        """Menus and panels are popovers; "motion-open" animates every popover's contents."""
        css = ui.theme._templates["motion-open"][0]
        self.assertRegex(css, r"popover > contents \{ animation: sonata-open")
        win = Gtk.Window()
        anchor = Gtk.Button(label="x")
        win.set_child(anchor)
        win.present()
        settle(50)
        try:
            m = ui.menu.popup(anchor, [[ui.menu.Item("A", lambda: None)]])
            p = ui.panel.popup(anchor, Gtk.Label(label="p"))
            self.assertIsInstance(m, Gtk.Popover)
            self.assertIsInstance(p, Gtk.Popover)
            m.popdown()
            p.popdown()
            settle(50)
        finally:
            win.destroy()

    def test_settings_page_switch_is_instant(self):
        """Choosing another section shows it at once (Vini: no animation; it
        used to cross-fade)."""
        w = S.Settings(None, "appearance")
        w.present()
        settle(200)
        try:
            with mock.patch.object(ui.transition.CrossFade, "play") as play, \
                    mock.patch.object(ui.transition.CrossFade, "capture") as capture:
                w.select("dock", from_sidebar=True)
            play.assert_not_called()
            capture.assert_not_called()
            self.assertIs(w.content.get_visible_child(), w.pages[w.current])
        finally:
            w.destroy()

    def test_alerts_are_libadwaita_dialogs(self):
        """Alerts are Adw.AlertDialog (libadwaita animates them in and out)."""
        made = []

        def fake(**kw):
            d = mock.MagicMock(name="AlertDialog")
            d.get_root.return_value = None
            made.append(d)
            return d
        with mock.patch.object(ui.dialog, "_MODERN", True), \
                mock.patch.object(ui.dialog.Adw, "AlertDialog", side_effect=fake, create=True):
            ui.dialog.alert("H", "", [("cancel", "Cancel", "")])
        made[0].present.assert_called_once()

    def test_switches_animate(self):
        """The kit's switch eases its colour (CSS transition); Settings' SwitchRows are libadwaita's."""
        css = "".join(t for t, _l in ui.theme._templates.values() if "switch.sonata-switch {" in t)
        self.assertRegex(css, r"switch\.sonata-switch \{[^}]*transition: background-color")
        row = S.switch_row("X", False, lambda on: None)
        self.assertIsInstance(row, Adw.SwitchRow)

    def test_app_icon_form_rows_slide(self):
        """App Icons form: rows that apply slide in (Gtk.Revealer SLIDE_DOWN)."""
        import inspect
        from sonata2.settings import appicons_page as P
        self.assertIn("RevealerTransitionType.SLIDE_DOWN", inspect.getsource(P.AppIconsPage.edit))


if __name__ == "__main__":
    unittest.main()
