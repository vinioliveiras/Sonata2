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

    @unittest.expectedFailure
    def test_size_rounding_never_shows_1000_kb(self):
        # BUG: fmt.size rounds KB after choosing the unit: 999 500 bytes is "1000 KB"
        # (and 999 990 000 bytes "1000.0 MB") instead of moving up to the next unit.
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

    @unittest.expectedFailure
    def test_escape_never_picks_the_destructive_response(self):
        # BUG: dialog.alert sets close_response = responses[0] whatever its style.
        # TextEdit/Preview's "Save changes?" lists ("discard", "Don't Save",
        # "destructive") first, so Escape discards the document; GIO's
        # show-processes choices ("Unmount Anyway", "Cancel") make Escape force it.
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

    @unittest.expectedFailure
    def test_ask_text_ok_off_while_empty(self):
        # BUG: ask_text only disables "ok" on "changed"; opened with an empty
        # text, OK starts enabled (its docstring says off while empty).
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

    @unittest.expectedFailure
    def test_on_change_listeners_released_with_their_widget(self):
        # BUG: theme.on_change has no way to disconnect: every ModuleSlider
        # (Control Center, rebuilt on each open) and glass_class() widget
        # stays in theme._listeners forever, kept alive and redrawn on every
        # appearance change.
        n = len(ui.theme._listeners)
        for _ in range(3):
            ui.controls.slider(10, style="module")
        gc.collect()
        self.assertEqual(len(ui.theme._listeners), n)


# -- Settings helpers -------------------------------------------------------------------------
from sonata2.settings import app as S  # noqa: E402


class SettingsHelperTests(TempConfig):
    def test_sections_and_parts(self):
        """Merged parts open their section; _Pages finds a section by a part's id."""
        self.assertEqual(S.section_of("wallpaper"), "displays")
        self.assertEqual(S.section_of("wifi"), "wifi")
        self.assertEqual(S.parts_of("about"), ("about", "updates"))
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

    @unittest.expectedFailure
    def test_save_survives_a_non_dict_file(self):
        # BUG: Settings._save re-implements config.update without its
        # isinstance(dict) check: a dock.json holding "null" or "[]" makes
        # every Settings switch raise TypeError instead of writing.
        self.write("dock", "null")
        S.Settings._save(None, "dock", "autohide", True)
        self.assertEqual(config.load("dock", {"autohide": False})["autohide"], True)

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

    @unittest.expectedFailure
    def test_unavailable_style_choice_goes_back(self):
        # BUG: Appearance > Style "Windows 11 (coming later)" saves "mac" but the
        # pop-up keeps showing Windows 11 until the page is rebuilt.
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

    @unittest.expectedFailure
    def test_wallpaper_page_released_when_rebuilt(self):
        # BUG: _page_wallpaper disconnects its StyleManager "notify::dark"
        # handler on the picture's "destroy", but that handler's closure keeps
        # the picture alive, so "destroy" never comes: every rebuild of
        # Displays leaves a handler (and refresh()) behind.
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


if __name__ == "__main__":
    unittest.main()
