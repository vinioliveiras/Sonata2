"""Review tests for the system layer: Wayfire config writing, app title bar
settings, Flatpak overrides, Steam libraries, RAW previews, sysfs/wpctl/
wlr-randr parsers, the Wayfire IPC framing, GPU environments, Trash
housekeeping and Software Update's runner. Headless, temp dirs only.
Run: xvfb-run -a python3.12 -m unittest tests.test_review_system -v"""
import datetime as dt
import json
import os
import socket
import struct
import tempfile
import threading
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

from gi.repository import GLib  # noqa: E402

from sonata2 import (fullscreen, gamemode, gpu, gtkstyle, imageload, steamgames,  # noqa: E402
                     titlebars, trash_cleanup, wfconfig)
from sonata2 import flatpak_theme  # noqa: E402
from sonata2.backend import system, updates  # noqa: E402
from sonata2.wl import wfipc  # noqa: E402


def _write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


def _read(path):
    with open(path) as f:
        return f.read()


# -- wfconfig ---------------------------------------------------------------------------------------
class WayfireConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.run = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cfg, "XDG_RUNTIME_DIR": self.run})
        self.env.start()
        self.over = os.path.join(self.cfg, "sonata2", "wayfire-overrides.ini")

    def tearDown(self):
        self.env.stop()

    def test_key_goes_into_its_section_before_the_blank_line(self):
        """A new key lands at the end of its own section (not after the next
        header) and comments elsewhere stay untouched."""
        _write(self.over, "# mine\n[input]\nxkb_layout = us\n\n[blur]\nmode = normal\n")
        self.assertTrue(wfconfig.wayfire_set("input", "xkb_variant", "intl"))
        self.assertEqual(_read(self.over),
                         "# mine\n[input]\nxkb_layout = us\nxkb_variant = intl\n\n[blur]\nmode = normal\n")

    def test_same_key_in_another_section_is_left_alone(self):
        """Only [section]'s key changes; bools are written as true/false."""
        _write(self.over, "[a]\nenabled = false\n[b]\nenabled = false\n")
        wfconfig.wayfire_set("b", "enabled", True)
        self.assertEqual(_read(self.over), "[a]\nenabled = false\n[b]\nenabled = true\n")

    def test_missing_section_and_file_are_created(self):
        """No overrides file yet: it is created with the section."""
        self.assertTrue(wfconfig.wayfire_set("decoration", "font", "Inter 10"))
        self.assertIn("[decoration]\nfont = Inter 10\n", _read(self.over))

    def test_unchanged_value_does_not_rewrite(self):
        """Writing the value already there must not touch the file (Wayfire
        reloads on every write: a display modeset)."""
        _write(self.over, "[input]\nxkb_layout = br\n")
        with mock.patch.object(wfconfig.os, "makedirs", side_effect=AssertionError("wrote")):
            self.assertTrue(wfconfig.wayfire_set("input", "xkb_layout", "br"))

    def test_runtime_copy_wins_and_runtime_set_needs_it(self):
        """wayfire_get reads the live session copy first; runtime_set never
        writes the overrides and fails without a session copy."""
        _write(os.path.join(self.cfg, "sonata2", "wayfire.ini"), "[blur]\nmode = a\n")
        _write(self.over, "[blur]\nmode = b\n# mode = c\n")
        self.assertEqual(wfconfig.wayfire_get("blur", "mode"), "b")
        self.assertFalse(wfconfig.runtime_set("blur", "mode", "x"))
        self.assertEqual(_read(self.over), "[blur]\nmode = b\n# mode = c\n")
        _write(os.path.join(self.run, "sonata2-wayfire.ini"), "[blur]\nmode = live\n")
        self.assertEqual(wfconfig.wayfire_get("blur", "mode"), "live")
        self.assertTrue(wfconfig.runtime_set("blur", "mode", "y"))
        self.assertEqual(wfconfig.wayfire_get("blur", "mode"), "y")
        self.assertIn("mode = b", _read(self.over))


# -- lighter effects / full screen games -------------------------------------------------------------
class LightEffectsEmptyTest(unittest.TestCase):
    def test_option_unset_before_lightening_comes_back(self):
        """An option the config didn't set ("" from wayfire_get) is removed
        again after the game, so Wayfire's default (blur) is back."""
        cfg = {("animate", "open_animation"): "zoom", ("animate", "close_animation"): "zoom",
               ("animate", "minimize_animation"): "squeezimize"}            # no blur_by_default
        light_file = os.path.join(tempfile.mkdtemp(), "light.json")
        with mock.patch.object(gamemode, "LIGHT", light_file):
            light = gamemode.LightEffects(get=lambda s, k, d="": cfg.get((s, k), d),
                                          set_=lambda s, k, v: cfg.__setitem__((s, k), v), wanted=lambda: True)
            light.update(full=True, used=7900, total=8188)
            light.update(full=False)
        self.assertNotEqual(cfg.get(("blur", "blur_by_default")), 'app_id is "sonata2-no-blur"')


class GameTitleTest(unittest.TestCase):
    def view(self, title):
        return {"id": 1, "app-id": "steam_app_870780", "title": title, "pid": -1, "role": "toplevel",
                "type": "toplevel", "parent": -1, "geometry": {"width": 1920, "height": 1080}}

    def test_launcher_titles_are_not_games(self):
        """Launchers / installers under Wine never get forced full screen."""
        self.assertFalse(fullscreen.looks_like_game(self.view("Ubisoft Connect launcher"), (1920, 1080)))
        self.assertTrue(fullscreen.looks_like_game(self.view("Hades II"), (1920, 1080)))

    def test_game_named_like_a_tool_still_goes_full_screen(self):
        """Tool words match whole words only: games named like them count."""
        for title in ("Control", "Crash Bandicoot", "Assassin's Creed Origins"):
            self.assertTrue(fullscreen.looks_like_game(self.view(title), (1920, 1080)), title)
        for title in ("Control Panel", "UnityCrashHandler64.exe", "unins000.exe", "GameInstaller"):
            self.assertFalse(fullscreen.looks_like_game(self.view(title), (1920, 1080)), title)


# -- title bars of other apps ------------------------------------------------------------------------
class TitlebarSettingsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_vscode_setting_inserted_and_replaced(self):
        """VS Code settings.json (JSONC) gets the key with a comma before
        existing keys, and an existing value is replaced in place."""
        p = os.path.join(self.d, "Code", "User", "settings.json")
        _write(p, '{\n    // mine\n    "editor.fontSize": 14\n}\n')
        titlebars._code_setting(p, "native")
        text = _read(p)
        self.assertIn('"window.titleBarStyle": "native",', text)
        self.assertIn('"editor.fontSize": 14', text)
        titlebars._code_setting(p, "custom")
        self.assertEqual(_read(p).count("titleBarStyle"), 1)
        self.assertIn('"window.titleBarStyle": "custom"', _read(p))

    def test_vscode_empty_object_gets_no_trailing_comma(self):
        """A brand-new settings file stays valid JSON."""
        p = os.path.join(self.d, "Code", "User", "settings.json")
        titlebars._code_setting(p, "native")
        self.assertEqual(json.loads(_read(p)), {"window.titleBarStyle": "native"})

    def test_vscode_commented_key_does_not_hide_the_setting(self):
        """A commented-out key is not the setting: the live one is added."""
        p = os.path.join(self.d, "settings.json")
        _write(p, '{\n    // "window.titleBarStyle": "custom",\n    "a": 1\n}\n')
        titlebars._code_setting(p, "native")
        live = [ln for ln in _read(p).splitlines() if "titleBarStyle" in ln and "//" not in ln]
        self.assertEqual(len(live), 1)

    def test_firefox_userjs_lines_toggle_and_user_lines_stay(self):
        """user.js keeps the user's prefs; the title bar line comes and goes,
        the portal line stays, and running twice changes nothing."""
        p = os.path.join(self.d, "user.js")
        _write(p, 'user_pref("a", 1);\n')
        titlebars._mozilla_userjs(p, True)
        on = _read(p)
        self.assertIn(titlebars.MOZ_LINE, on)
        self.assertIn(titlebars.MOZ_PORTAL, on)
        titlebars._mozilla_userjs(p, True)
        self.assertEqual(_read(p), on)
        titlebars._mozilla_userjs(p, False)
        off = _read(p)
        self.assertNotIn(titlebars.MOZ_LINE, off)
        self.assertTrue(off.startswith('user_pref("a", 1);'))
        self.assertIn(titlebars.MOZ_PORTAL, off)

    def test_vesktop_only_when_it_ran_once(self):
        """No settings.json: nothing is created; an existing one is switched."""
        p = os.path.join(self.d, "vesktop", "settings.json")
        titlebars._vesktop_setting(p, True)
        self.assertFalse(os.path.exists(p))
        _write(p, json.dumps({"minimizeToTray": True}))
        titlebars._vesktop_setting(p, True)
        self.assertEqual(json.loads(_read(p)), {"minimizeToTray": True, "titleBar": "system",
                                                "customTitleBar": False})

    def test_chromium_qt_mode_goes_back_to_gtk_and_theme_keeps_classic_frame(self):
        """Qt mode (hangs Chromium here) becomes GTK with Chromium's frame; a
        browser theme keeps Sonata's title bar (custom_chrome_frame False)."""
        p = os.path.join(self.d, "Preferences")
        _write(p, json.dumps({"extensions": {"theme": {"system_theme": 2}}, "other": 1}))
        titlebars._browser_pref(p)
        prefs = json.loads(_read(p))
        self.assertEqual(prefs["extensions"]["theme"]["system_theme"], titlebars.GTK_MODE)
        self.assertIs(prefs["browser"]["custom_chrome_frame"], True)
        self.assertEqual(prefs["other"], 1)
        _write(p, json.dumps({"extensions": {"theme": {"system_theme": 1, "id": "abc"}}}))
        titlebars._browser_pref(p)
        self.assertIs(json.loads(_read(p))["browser"]["custom_chrome_frame"], False)

    def test_chromium_roots_found_electron_apps_skipped(self):
        """Browsers (Local State + Default/, one or two levels deep, Flatpak
        too) are found; Electron apps (no Default/) are not."""
        home, cfg = self.d, os.path.join(self.d, ".config")
        for rel in ("google-chrome", "BraveSoftware/Brave-Browser"):
            _write(os.path.join(cfg, rel, "Local State"), "{}")
            os.makedirs(os.path.join(cfg, rel, "Default"))
        _write(os.path.join(cfg, "Code", "Local State"), "{}")                   # Electron
        fp = os.path.join(home, ".var", "app", "org.chromium.Chromium", "config", "chromium")
        _write(os.path.join(fp, "Local State"), "{}")
        os.makedirs(os.path.join(fp, "Default"))
        with mock.patch.dict(os.environ, {"HOME": home}):
            roots = titlebars.chromium_roots(cfg)
        self.assertEqual(sorted(os.path.relpath(r, home) for r in roots),
                         sorted([".config/google-chrome", ".config/BraveSoftware/Brave-Browser",
                                 ".var/app/org.chromium.Chromium/config/chromium"]))


class GtkCssCleanTest(unittest.TestCase):
    def test_block_removed_user_css_kept_or_file_removed(self):
        """clean() drops only Sonata's marked block; a file with nothing else
        goes away."""
        d = tempfile.mkdtemp()
        p = os.path.join(d, "gtk.css")
        _write(p, f"{gtkstyle.BEGIN}\n@import url('x');\n{gtkstyle.END}\n\nlabel {{ color: red; }}\n")
        self.assertTrue(gtkstyle.clean(p))
        self.assertEqual(_read(p), "label { color: red; }\n")
        self.assertFalse(gtkstyle.clean(p))
        _write(p, f"{gtkstyle.BEGIN}\nx\n{gtkstyle.END}\n")
        self.assertTrue(gtkstyle.clean(p))
        self.assertFalse(os.path.exists(p))


class FlatpakOverridesTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "overrides", "global")
        self.patches = [mock.patch.object(flatpak_theme, "_overrides_path", return_value=self.path),
                        mock.patch.object(flatpak_theme, "_copy_themes"),
                        mock.patch.object(flatpak_theme, "enabled", return_value=True),
                        mock.patch.object(flatpak_theme.shutil, "which", return_value="/usr/bin/flatpak")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _kf(self):
        kf = GLib.KeyFile()
        kf.load_from_file(self.path, GLib.KeyFileFlags.NONE)
        return kf

    def test_apply_adds_ours_keeps_the_users_and_drops_old_gtk_theme(self):
        """The user's own filesystem overrides stay; Sonata's are added once;
        a GTK_THEME an earlier Sonata forced is removed."""
        _write(self.path, "[Context]\nfilesystems=home;\n\n[Environment]\nGTK_THEME=Sonata-Dark\nFOO=1\n")
        self.assertTrue(flatpak_theme.apply())
        flatpak_theme.apply()
        fs = self._kf().get_string("Context", "filesystems").split(";")
        self.assertEqual(fs.count(flatpak_theme.FS), 1)
        self.assertIn("home", fs)
        self.assertNotIn("GTK_THEME", self._kf().get_keys("Environment")[0])
        self.assertEqual(self._kf().get_string("Environment", "FOO"), "1")

    def test_remove_takes_only_what_sonata_added(self):
        """Turning it off leaves the user's overrides and drops emptied groups."""
        flatpak_theme.apply()
        flatpak_theme.remove()
        self.assertFalse(self._kf().has_group("Context"))
        _write(self.path, "[Context]\nfilesystems=home;xdg-data/themes:ro;\n")
        flatpak_theme.remove()
        self.assertEqual(self._kf().get_string("Context", "filesystems"), "home;")


# -- Steam games -------------------------------------------------------------------------------------
class SteamGamesTest(unittest.TestCase):
    def setUp(self):
        self.home, self.data = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "XDG_DATA_HOME": self.data})
        self.env.start()
        steamgames._names.clear()
        self.root = os.path.join(self.home, ".local", "share", "Steam")
        self.other = os.path.join(self.home, "Games", "SteamLibrary")
        os.makedirs(os.path.join(self.other, "steamapps"))
        _write(os.path.join(self.root, "steamapps", "libraryfolders.vdf"),
               f'"libraryfolders"\n{{\n "0"\n {{\n  "path" "{self.root}"\n }}\n "1"\n {{\n'
               f'  "path" "{self.other}"\n }}\n}}\n')

    def tearDown(self):
        self.env.stop()
        steamgames._names.clear()

    def test_every_library_once_and_names_from_manifests(self):
        """Libraries from libraryfolders.vdf (no duplicates); a game's name
        from the appmanifest in any of them; unknown ids are None."""
        libs = steamgames.libraries()
        self.assertEqual(libs, [os.path.join(self.root, "steamapps"), os.path.join(self.other, "steamapps")])
        _write(os.path.join(self.other, "steamapps", "appmanifest_570.acf"), '"AppState"\n{\n "name" "Dota 2"\n}\n')
        self.assertEqual(steamgames.name("570"), "Dota 2")
        self.assertIsNone(steamgames.name("999"))
        self.assertEqual(steamgames.appid("steam_app_570"), "570")
        self.assertIsNone(steamgames.appid("steam_app_x"))

    def test_biggest_shortcut_icon_then_library_cache(self):
        """The largest hicolor shortcut icon wins; without one, Steam's
        librarycache square icon (<sha1>.jpg) is used."""
        cache = os.path.join(self.root, "appcache", "librarycache", "570")
        _write(os.path.join(cache, "header.jpg"), "x")
        _write(os.path.join(cache, "a" * 40 + ".jpg"), "x")
        self.assertEqual(steamgames.icon_path("570"), os.path.join(cache, "a" * 40 + ".jpg"))
        for px in (32, 256, 64):
            _write(os.path.join(self.data, "icons", "hicolor", f"{px}x{px}", "apps", "steam_icon_570.png"), "x")
        self.assertTrue(steamgames.icon_path("570").endswith("/256x256/apps/steam_icon_570.png"))


# -- RAW previews -------------------------------------------------------------------------------------
def _jpeg(payload: bytes, thumb: bytes = b"") -> bytes:
    app1 = b"Exif\x00\x00" + thumb
    sos = b"\xff\xda\x00\x08" + b"\x01\x02\x03\x04\x05\x06"
    return b"\xff\xd8" + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + sos + payload + b"\xff\xd9"


class RawPreviewTest(unittest.TestCase):
    def test_jpeg_end_skips_exif_thumbnail(self):
        """The embedded EXIF thumbnail's EOI must not end the outer JPEG."""
        thumb = _jpeg(b"t" * 10)
        data = _jpeg(b"p" * 50, thumb)
        self.assertEqual(imageload._jpeg_end(data, 0), len(data))
        self.assertEqual(imageload._jpeg_end(b"\xff\xd8\x00\x00\x00", 0), -1)

    def test_largest_embedded_jpeg_is_the_preview(self):
        """A RAW file: the camera's full-size JPEG is chosen over the small
        one before it; too big a file gives nothing."""
        small, big = _jpeg(b"s" * 20), _jpeg(b"b" * 500, _jpeg(b"t" * 5))
        p = os.path.join(tempfile.mkdtemp(), "IMG_1.CR2")
        _write(p, b"II*\x00" + b"\x00" * 30 + small + b"\x00" * 10 + big + b"\x00" * 8, "wb")
        self.assertTrue(imageload.is_raw(p))
        self.assertEqual(imageload.raw_preview(p), big)
        with mock.patch.object(imageload, "MAX_RAW", 10):
            self.assertEqual(imageload.raw_preview(p), b"")


# -- system parsers -----------------------------------------------------------------------------------
class SystemParsersTest(unittest.TestCase):
    def test_battery_and_power_supply_types(self):
        """BAT0 capacity/status; USB-C / Mains supplies count only when online."""
        ps = tempfile.mkdtemp()
        _write(os.path.join(ps, "BAT0", "capacity"), "57\n")
        _write(os.path.join(ps, "BAT0", "status"), "Discharging\n")
        _write(os.path.join(ps, "ucsi-source-psy-USBC000:001", "type"), "USB\n")
        _write(os.path.join(ps, "ucsi-source-psy-USBC000:001", "online"), "0\n")
        self.assertEqual(system.battery(ps), (57, "Discharging"))
        self.assertFalse(system.on_ac(ps))
        _write(os.path.join(ps, "ucsi-source-psy-USBC000:001", "online"), "1\n")
        self.assertTrue(system.on_ac(ps))
        self.assertEqual(system.battery(tempfile.mkdtemp()), (None, ""))

    def test_backlight_prefers_firmware_interface(self):
        """Firmware/platform backlights win over raw ones (like brightnessctl)."""
        bl = tempfile.mkdtemp()
        for name, kind, cur, mx in (("amdgpu_bl1", "raw", "10", "255"), ("acpi_video0", "firmware", "50", "100")):
            _write(os.path.join(bl, name, "type"), kind)
            _write(os.path.join(bl, name, "brightness"), cur)
            _write(os.path.join(bl, name, "max_brightness"), mx)
        with mock.patch.object(system, "BACKLIGHT", bl):
            self.assertEqual(system._backlight(), ("acpi_video0", 50, 100))

    def test_wlr_randr_modes_current_and_scale(self):
        """wlr-randr output: modes (no duplicates), the current one, scale."""
        out = ('eDP-1 "BOE 0x0A1C (eDP-1)"\n  Enabled: yes\n  Modes:\n'
               '    1920x1080 px, 144.003006 Hz (preferred, current)\n    1920x1080 px, 60.000000 Hz\n'
               '    1920x1080 px, 60.000000 Hz\n  Scale: 1.250000\n'
               'HDMI-A-1 "LG"\n  Modes:\n    2560x1440 px, 179.998001 Hz (current)\n')
        with mock.patch.object(system, "_run", return_value=(0, out)):
            ds = system.displays()
        self.assertEqual([d.name for d in ds], ["eDP-1", "HDMI-A-1"])
        self.assertEqual(ds[0].modes, ["1920x1080@144.003006", "1920x1080@60.000000"])
        self.assertEqual(ds[0].current, "1920x1080@144.003006")
        self.assertEqual(ds[0].scale, 1.25)
        self.assertEqual(ds[1].current, "2560x1440@179.998001")

    def test_wpctl_sinks_only_from_audio_section(self):
        """`wpctl status`: the default (*) sink and ids, never Video nodes."""
        out = ("Audio\n ├─ Devices:\n │      40. Built-in Audio  [alsa]\n ├─ Sinks:\n"
               " │  *   52. Speakers  [vol: 0.40]\n │      61. HDMI  [vol: 1.00]\n ├─ Sources:\n"
               " │      70. Mic  [vol: 1.00]\n\nVideo\n ├─ Sinks:\n │      99. Not audio\n")
        with mock.patch.object(system, "_run", return_value=(0, out)):
            sinks = system.audio_sinks()
            sources = system.audio_sources()
        self.assertEqual([(s.id, s.name, s.default) for s in sinks], [(52, "Speakers", True), (61, "HDMI", False)])
        self.assertEqual([s.id for s in sources], [70])

    def test_pactl_ports_monitors_and_duplicate_labels(self):
        """One entry per available port; monitors and the equalizer's own
        sinks are hidden; the same label twice names its device."""
        nodes = [{"name": "alsa.a", "description": "Card A", "active_port": "hp",
                  "ports": [{"name": "spk", "description": "Speakers"},
                            {"name": "hp", "description": "Headphones"},
                            {"name": "hdmi", "description": "HDMI", "availability": "not available"}]},
                 {"name": "alsa.b", "description": "Card B", "ports": [{"name": "spk", "description": "Speakers"},
                                                                     {"name": "x", "description": "Line"}]},
                 {"name": "sonata-eq.x", "description": "EQ", "ports": []},
                 {"name": "alsa.a.monitor", "description": "Monitor", "ports": []}]

        def run(cmd, timeout=10):
            return (0, "alsa.a\n") if cmd[1].startswith("get-default") else (0, json.dumps(nodes))
        with mock.patch.object(system, "_run", side_effect=run):
            devs = system._pactl_devices("sources")
        self.assertEqual([d.key for d in devs], ["alsa.a|spk", "alsa.a|hp", "alsa.b|spk", "alsa.b|x"])
        self.assertEqual(devs[0].name, "Speakers (Card A)")
        self.assertTrue(devs[1].default)
        self.assertFalse(devs[0].default)

    def test_pactl_warning_on_stderr_keeps_the_device_list(self):
        """A warning on stderr of a successful command never reaches the parsed output."""
        done = mock.Mock(returncode=0, stdout='[{"name": "a", "description": "A", "ports": []}]',
                         stderr="W: [pulseaudio] some warning\n")
        with mock.patch.object(system.shutil, "which", return_value="/usr/bin/pactl"), \
                mock.patch.object(system.subprocess, "run", return_value=done):
            self.assertIsNotNone(system._pactl_devices("sinks"))

    def test_display_mode_in_mhz_is_shown_in_hz(self):
        """Wayfire keeps the refresh in mHz; Settings shows Hz like wlr-randr."""
        with mock.patch.object(system, "wayfire_get", return_value="2560x1440@179998"):
            self.assertEqual(system.display_mode_setting("HDMI-A-1"), "2560x1440@179.998")
        with mock.patch.object(system, "wayfire_get", return_value=""):
            self.assertEqual(system.display_mode_setting("HDMI-A-1"), "highrr")
        calls = []
        with mock.patch.object(system, "wayfire_set", side_effect=lambda *a: calls.append(a) or True):
            system.set_display_mode("HDMI-A-1", "2560x1440@179.998")
        self.assertEqual(calls, [("output:HDMI-A-1", "mode", "2560x1440@179998")])

    def test_run_latest_keeps_only_the_newest_waiting_value(self):
        """A slider drag over a slow bus: one job at a time; of the values
        given meanwhile only the last one runs."""
        jobs, ran, got = [], [], []
        with mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: jobs.append((fn, cb, a))):
            for v in (10, 20, 30, 40):
                system.run_latest("ddc-test", ran.append, v, callback=got.append)
            self.assertEqual(len(jobs), 1)
            fn, cb, a = jobs.pop(0)
            cb(fn(*a))
            fn, cb, a = jobs.pop(0)
            cb(fn(*a))
        self.assertEqual(ran, [10, 40])
        self.assertEqual(got, [None])


# -- Wayfire IPC framing -------------------------------------------------------------------------------
class WayfireIpcTest(unittest.TestCase):
    def test_frame_round_trip_and_closed_socket(self):
        """4-byte little-endian length + JSON both ways; a socket closed
        mid-message raises ConnectionError (not a hang)."""
        a, b = socket.socketpair()
        with a, b:
            wfipc._send(a, "window-rules/list-views", {"x": 1})
            self.assertEqual(wfipc._read(b), {"method": "window-rules/list-views", "data": {"x": 1}})
            a.sendall(struct.pack("<I", 100) + b'{"a"')
            a.shutdown(socket.SHUT_WR)
            with self.assertRaises(ConnectionError):
                wfipc._read(b)

    def test_call_against_a_socket_and_without_one(self):
        """call() answers through a real Unix socket; None when Wayfire is
        gone or the socket path is missing."""
        path = os.path.join(tempfile.mkdtemp(), "wf.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(2)

        def serve():
            c, _ = srv.accept()
            with c:
                req = wfipc._read(c)
                body = json.dumps([{"id": 7, "method": req["method"]}]).encode()
                c.sendall(struct.pack("<I", len(body)) + body)
            c, _ = srv.accept()
            c.close()                                  # the second caller gets nothing
        t = threading.Thread(target=serve, daemon=True)
        t.start()
        with mock.patch.dict(os.environ, {"WAYFIRE_SOCKET": path}):
            ipc = wfipc.WayfireIPC()
            self.assertEqual(ipc.call("window-rules/list-views"), [{"id": 7, "method": "window-rules/list-views"}])
            self.assertIsNone(ipc.call("window-rules/list-views"))
        t.join(2)
        srv.close()
        with mock.patch.dict(os.environ, {"WAYFIRE_SOCKET": path + ".gone"}):
            self.assertIsNone(wfipc.WayfireIPC().call("x"))


# -- GPU environment ------------------------------------------------------------------------------------
class DiscreteEnvTest(unittest.TestCase):
    def setUp(self):
        gpu._env = None

    def tearDown(self):
        gpu._env = None

    def test_discrete_env_from_the_card(self):
        """The high-performance card decides (not switcheroo's non-default GPU,
        the integrated one with a MUX in dGPU mode -- Vini): NVIDIA's offload
        set (without the EGL vendor file when it isn't installed), a Mesa
        card's own DRI_PRIME address, else DRI_PRIME=1."""
        nv = gpu.Card("pci-0000_01_00_0", "0000:01:00.0", "nvidia", True, 0)
        apu = gpu.Card("pci-0000_36_00_0", "0000:36:00.0", "amdgpu", False, 1 << 29)
        rx = gpu.Card("pci-0000_03_00_0", "0000:03:00.0", "amdgpu", True, 8 << 30)
        cfg = {"games_gpu": "", "apps_gpu": ""}
        with mock.patch.object(gpu, "_card_list", [apu, nv]), mock.patch.object(gpu.config, "load", return_value=cfg), \
                mock.patch.object(gpu, "_switcheroo", return_value=(True, {"DRI_PRIME": apu.tag})), \
                mock.patch.object(gpu.os.path, "exists", return_value=False):
            env = gpu.discrete_env()
        self.assertEqual(env["__NV_PRIME_RENDER_OFFLOAD"], "1")
        self.assertNotIn("DRI_PRIME", env)
        self.assertNotIn("__EGL_VENDOR_LIBRARY_FILENAMES", env)
        self.assertIn("__EGL_VENDOR_LIBRARY_FILENAMES", gpu.NVIDIA_ENV)        # the constant stays whole
        with mock.patch.object(gpu, "_card_list", [apu, rx]), mock.patch.object(gpu.config, "load", return_value=cfg):
            self.assertEqual(gpu.discrete_env(), {"DRI_PRIME": rx.tag})
        with mock.patch.object(gpu, "_card_list", [apu]), mock.patch.object(gpu.config, "load", return_value=cfg):
            self.assertEqual(gpu.discrete_env(), {"DRI_PRIME": "1"})

    def test_users_no_beats_prefers_non_default_gpu(self):
        """An app whose entry asks for the dGPU stays off it once unchecked."""
        info = mock.Mock()
        info.get_id.return_value = "steam.desktop"
        info.has_key.return_value = True
        info.get_boolean.return_value = True
        with mock.patch.object(gpu.config, "load", return_value={"discrete": [], "integrated": ["steam"]}):
            self.assertFalse(gpu.wants_discrete(info))
        with mock.patch.object(gpu.config, "load", return_value={"discrete": [], "integrated": []}):
            self.assertTrue(gpu.wants_discrete(info))


# -- Trash housekeeping ---------------------------------------------------------------------------------
class TrashPurgeTest(unittest.TestCase):
    def _item(self, root, name, when, make=None):
        _write(os.path.join(root, "info", name + ".trashinfo"),
               f"[Trash Info]\nPath=/x/{name}\nDeletionDate={when}\n")
        if make:
            make(os.path.join(root, "files", name))

    def test_symlink_in_trash_is_removed_not_followed(self):
        """A trashed link to a folder goes; the folder it pointed to stays."""
        root, keep = tempfile.mkdtemp(), tempfile.mkdtemp()
        _write(os.path.join(keep, "precious.txt"), "x")
        os.makedirs(os.path.join(root, "files"))
        self._item(root, "link", "2020-01-01T00:00:00", lambda p: os.symlink(keep, p))
        self.assertEqual(trash_cleanup.purge(30, root, dt.datetime(2026, 1, 1)), 1)
        self.assertTrue(os.path.exists(os.path.join(keep, "precious.txt")))
        self.assertFalse(os.path.lexists(os.path.join(root, "files", "link")))

    def test_timezone_in_deletion_date_does_not_stop_the_purge(self):
        """A DeletionDate with an offset ("...Z") is compared like the others."""
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "files"))
        self._item(root, "a", "2020-01-01T00:00:00Z", lambda p: _write(p, "x"))
        self._item(root, "b", "2020-01-01T00:00:00", lambda p: _write(p, "x"))
        self.assertEqual(trash_cleanup.purge(30, root, dt.datetime(2026, 1, 1)), 2)


# -- Software Update ------------------------------------------------------------------------------------
class RunnerOutputTest(unittest.TestCase):
    def _run(self, script):
        loop, lines, res = GLib.MainLoop(), [], {}
        r = updates.Runner([["sh", "-c", script]], lines.append, lambda f: None,
                           lambda ok, i: (res.update(ok=ok), loop.quit()))
        r.start()
        GLib.timeout_add(5000, loop.quit)
        loop.run()
        r.stop()
        return res.get("ok"), lines

    def test_lines_and_progress_reach_the_log(self):
        """Every output line reaches the log; success ends the run."""
        ok, lines = self._run("echo '(1/2) one'; echo '(2/2) two'")
        self.assertTrue(ok)
        self.assertIn("(2/2) two", lines)

    def test_invalid_utf8_line_does_not_kill_the_update(self):
        """A non-UTF-8 byte in the output never stops reading the pipe."""
        ok, lines = self._run("printf 'caf\\351\\n'; head -c 200000 /dev/zero | tr '\\0' x | fold -w 100; echo; echo end")
        self.assertTrue(ok)
        self.assertIn("end", lines)

# -- fixes without an xfail test ------------------------------------------------------------------------
CPP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "wayfire-plugin", "src", "sonata-corners.cpp")


class WayfireConfigWriteTest(unittest.TestCase):
    def setUp(self):
        self.cfg, self.run = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.cfg, "XDG_RUNTIME_DIR": self.run})
        self.env.start()
        self.live = os.path.join(self.run, "sonata2-wayfire.ini")

    def tearDown(self):
        self.env.stop()

    def test_live_ini_replaced_whole_never_rewritten_in_place(self):
        """The live ini Wayfire reloads is a new file renamed over the old
        (inotify IN_MOVED_TO): never truncated and written in place."""
        _write(self.live, "[blur]\nmode = a\n")
        before = os.stat(self.live).st_ino
        self.assertTrue(wfconfig.runtime_set("blur", "mode", "b"))
        self.assertNotEqual(os.stat(self.live).st_ino, before)
        self.assertEqual(_read(self.live), "[blur]\nmode = b\n")
        self.assertEqual([n for n in os.listdir(self.run) if n.endswith(".tmp")], [])

    def test_writers_take_a_cross_process_lock(self):
        """Settings and the menu bar write the same file: a flock beside it."""
        _write(self.live, "[blur]\nmode = a\n")
        with mock.patch.object(wfconfig.fcntl, "flock") as flock:
            wfconfig.runtime_set("blur", "mode", "c")
        flock.assert_called()

    def test_runtime_set_none_removes_the_key(self):
        """None drops the key so Wayfire's default applies again."""
        _write(self.live, "[blur]\nblur_by_default = x\nmode = a\n")
        self.assertTrue(wfconfig.runtime_set("blur", "blur_by_default", None))
        self.assertEqual(_read(self.live), "[blur]\nmode = a\n")
        self.assertTrue(wfconfig.runtime_set("blur", "missing", None))


class BackendRunTest(unittest.TestCase):
    def test_stderr_kept_when_the_command_fails(self):
        """A failing command still reports its error text (Wi-Fi, VPN import...)."""
        done = mock.Mock(returncode=4, stdout="", stderr="Error: secrets were required\n")
        with mock.patch.object(system.shutil, "which", return_value="/usr/bin/nmcli"), \
                mock.patch.object(system.subprocess, "run", return_value=done):
            self.assertEqual(system._run(["nmcli"]), (4, "Error: secrets were required\n"))


class PactlWatchTest(unittest.TestCase):
    """The one `pactl subscribe` reader: lines on the main loop, respawned when pactl dies."""

    def setUp(self):
        self.bin = tempfile.mkdtemp()
        self.count = os.path.join(self.bin, "count")
        _write(os.path.join(self.bin, "pactl"),
               f"#!/bin/sh\necho x >> {self.count}\nprintf \"Event 'change' on sink #1\\n\\377\\n\"\n")
        os.chmod(os.path.join(self.bin, "pactl"), 0o755)
        self.env = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"]})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_lines_arrive_and_pactl_is_started_again(self):
        from sonata2.backend import pactl_watch
        lines, loop = [], GLib.MainLoop()
        with mock.patch.object(pactl_watch, "RESPAWN_MS", (50, 100)):
            w = pactl_watch.watch(lines.append)
            self.assertIsNotNone(w)
            GLib.timeout_add(1500, loop.quit)
            loop.run()
            w.kill()
        self.assertIn("Event 'change' on sink #1", lines)
        self.assertIn("�", lines)                         # a bad byte doesn't stop it
        self.assertGreaterEqual(len(_read(self.count).split()), 2)
        n = len(_read(self.count).split())
        loop = GLib.MainLoop()
        GLib.timeout_add(400, loop.quit)
        loop.run()
        self.assertEqual(len(_read(self.count).split()), n)    # stopped: never again

    def test_watchers_share_the_reader(self):
        """system, mixer and equalizer have no `pactl subscribe` of their own."""
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sonata2", "backend")
        for name in ("system.py", "mixer.py", "equalizer.py"):
            src = _read(os.path.join(root, name))
            self.assertNotIn('"subscribe"', src, name)
            self.assertIn("watch(", src, name)

    def test_watch_audio_debounces_sink_events(self):
        got, loop = [], GLib.MainLoop()
        with mock.patch("sonata2.backend.pactl_watch.watch", side_effect=lambda cb: cb) as w:
            line = system.watch_audio(lambda: got.append(1))
        w.assert_called_once()
        for _ in range(5):
            line("Event 'change' on sink #3")
        line("Event 'change' on client #9")
        GLib.timeout_add(400, loop.quit)
        loop.run()
        self.assertEqual(got, [1])


class FlatpakBrokenFileTest(FlatpakOverridesTest):
    def test_unreadable_overrides_are_never_replaced(self):
        """A broken overrides file is the user's: left alone, not saved over."""
        _write(self.path, "this is [not a key file\n\x00")
        before = _read(self.path)
        flatpak_theme.apply()
        self.assertEqual(_read(self.path), before)


class TitlebarWriteTest(unittest.TestCase):
    def test_commented_key_after_code_on_a_line_is_ignored(self):
        p = os.path.join(tempfile.mkdtemp(), "settings.json")
        _write(p, '{\n    "a": 1, // "window.titleBarStyle": "custom"\n    "window.titleBarStyle": "custom"\n}\n')
        titlebars._code_setting(p, "native")
        text = _read(p)
        self.assertIn('// "window.titleBarStyle": "custom"', text)
        self.assertIn('    "window.titleBarStyle": "native"', text)
        self.assertEqual([n for n in os.listdir(os.path.dirname(p)) if n != "settings.json"], [])


class SteamUnknownRetryTest(unittest.TestCase):
    def test_unknown_game_looked_up_again_later(self):
        """A game not found (still installing) isn't unknown forever."""
        steamgames._names.clear()
        found = [None]
        with mock.patch.object(steamgames, "libraries", side_effect=lambda: []) as libs, \
                mock.patch.object(steamgames.time, "monotonic", side_effect=lambda: found[0] or 0):
            self.assertIsNone(steamgames.name("42"))
            self.assertIsNone(steamgames.name("42"))
            self.assertEqual(libs.call_count, 1)
            found[0] = steamgames.UNKNOWN_RETRY_S + 1
            steamgames.name("42")
            self.assertEqual(libs.call_count, 2)
        steamgames._names.clear()


class TimeoutsTest(unittest.TestCase):
    def test_gtkstyle_reset_env_has_a_timeout(self):
        import subprocess
        with mock.patch.object(gtkstyle, "in_session", return_value=True), \
                mock.patch.object(gtkstyle.shutil, "which", return_value="/usr/bin/x"), \
                mock.patch.object(gtkstyle.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("x", 5)) as run:
            gtkstyle.reset_env()                      # no exception escapes
        self.assertTrue(all(c.kwargs.get("timeout") for c in run.call_args_list))
        self.assertEqual(run.call_count, 2)

    def test_users_crypt_has_a_timeout(self):
        import subprocess
        from sonata2.backend import users
        with mock.patch.object(users.shutil, "which", return_value="/usr/bin/openssl"), \
                mock.patch.object(users.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 10)) as run:
            self.assertIsNone(users._crypt("pw"))
        self.assertTrue(run.call_args.kwargs.get("timeout"))


class IconCacheTest(unittest.TestCase):
    def test_rendered_svgs_bounded_by_bytes_lru(self):
        """_rendered keeps a few MB of the most recently used textures."""
        from sonata2 import icons
        tex = mock.Mock(get_width=lambda: 256, get_height=lambda: 256)
        icons._rendered.clear()
        icons._rendered_bytes = 0
        with mock.patch.object(icons, "_render_rsvg", return_value=tex):
            for i in range(200):
                icons._rsvg_texture(f"/x/{i}.svg", 256)
                icons._rsvg_texture("/x/0.svg", 256)          # used all the time: kept
        self.assertLessEqual(icons._rendered_bytes, icons._RENDERED_MAX)
        self.assertIn(("/x/0.svg", 256), icons._rendered)
        self.assertNotIn(("/x/1.svg", 256), icons._rendered)
        icons._rendered.clear()
        icons._rendered_bytes = 0

    def test_picture_icon_cached_is_not_decoded(self):
        from sonata2 import icons
        if icons.Gdk.Display.get_default() is None:
            self.skipTest("no display")
        d = tempfile.mkdtemp()
        pic = os.path.join(d, "p.png")
        _write(pic, "x")
        with mock.patch.object(icons, "GENERATED", d), \
                mock.patch.object(icons, "_render_plate", return_value=True), \
                mock.patch.object(icons.Gdk.Texture, "new_from_filename") as dec:
            name = "pic-" + __import__("hashlib").sha1(f"{pic}|squircle|False|None".encode()).hexdigest()[:20]
            stamp = f"picture\n{pic}\n{int(os.path.getmtime(pic))}\n{icons.PLATE_VERSION}\nsquircle\nFalse\nNone"
            _write(os.path.join(d, name + ".src"), stamp)
            _write(os.path.join(d, name + ".png"), "x")
            self.assertIsNotNone(icons.picture_icon(pic))
        dec.assert_not_called()


class RawMappedTest(unittest.TestCase):
    def test_raw_mapped_not_read_and_empty_file_is_fine(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.nef")
        data = b"\x00" * 100 + _jpeg(b"p" * 50) + b"\x00" * 10
        _write(p, data, "wb")
        real_open = open
        with mock.patch("builtins.open", side_effect=lambda *a, **k: real_open(*a, **k)):
            self.assertEqual(imageload.raw_preview(p), _jpeg(b"p" * 50))
        _write(os.path.join(d, "e.nef"), b"", "wb")
        self.assertEqual(imageload.raw_preview(os.path.join(d, "e.nef")), b"")
        self.assertIn("mmap.mmap(", _read(imageload.__file__))
        self.assertNotIn("data = f.read()", _read(imageload.__file__))


class ToplevelBindingsTest(unittest.TestCase):
    def test_generated_bindings_renamed_in_whole(self):
        """Several processes at login: the cached module appears whole or not at all."""
        import importlib.util
        if importlib.util.find_spec("pywayland") is None:
            self.skipTest("no pywayland")
        from sonata2.wl import toplevels
        cache = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}), \
                mock.patch("sonata2.config.atomic_write", wraps=__import__("sonata2.config").config.atomic_write) as aw:
            toplevels._load_protocol()
        aw.assert_called_once()
        folder = os.path.dirname(aw.call_args.args[0])
        self.assertEqual([n for n in os.listdir(folder) if n != "__pycache__"], [toplevels.PROTO + ".py"])
        self.assertIn("from pywayland.protocol.wayland import", _read(aw.call_args.args[0]))


class CornersPluginTest(unittest.TestCase):
    """sonata-corners.cpp can't be built here: its source is checked."""

    def setUp(self):
        self.cpp = _read(CPP)

    def test_opaque_region_corners_from_the_real_radius(self):
        region = self.cpp[self.cpp.index("wf::regionf_t get_opaque_region()"):self.cpp.index("std::string stringify()")]
        self.assertNotIn("const int c = 16;", region)
        self.assertIn("corner_radius()", region)

    def test_unmap_never_destroys_its_running_callback(self):
        unmap = self.cpp[self.cpp.index("src->on_unmap = "):self.cpp.index("view->connect(&src->on_unmap);")]
        self.assertNotIn("auto dead = std::move", unmap)
        self.assertIn("retired.push_back", unmap)
        self.assertIn("free_retired.run_once", unmap)

    def test_options_cached_and_refreshed_on_reload(self):
        render = self.cpp[self.cpp.index("void render(const wf::scene::render_instruction_t"):
                          self.cpp.index("class corners_node_t")]
        self.assertNotIn("option_str(", render)
        self.assertNotIn("from_string", render)
        self.assertIn("wf::signal::connection_t<wf::reload_config_signal> on_reload", self.cpp)
        self.assertIn("wf::get_core().connect(&on_reload);", self.cpp)
        self.assertIn("options_valid = false;", self.cpp)


# -- animations ------------------------------------------------------------------------------------------
class AnimationTests(unittest.TestCase):
    """This area's only animations are Wayfire's window open/close/minimize
    ones, which the lighter effects turn off during a game: they must come
    back afterwards, exactly as configured."""

    def _light(self, cfg, wanted=True):
        light_file = os.path.join(tempfile.mkdtemp(), "light.json")
        p = mock.patch.object(gamemode, "LIGHT", light_file)
        p.start()
        self.addCleanup(p.stop)
        return gamemode.LightEffects(get=lambda s, k, d="": cfg.get((s, k), d),
                                     set_=lambda s, k, v: cfg.__setitem__((s, k), v), wanted=lambda: wanted)

    def test_window_animations_come_back_after_the_game(self):
        cfg = {("animate", "open_animation"): "zoom", ("animate", "close_animation"): "fade",
               ("animate", "minimize_animation"): "squeezimize", ("blur", "blur_by_default"): "x"}
        want = dict(cfg)
        light = self._light(cfg)
        light.update(full=True, used=95, total=100)
        self.assertEqual(cfg[("animate", "open_animation")], "none")
        light.update(full=False)
        self.assertEqual(cfg, want)

    def test_animations_untouched_without_a_full_card_or_the_setting(self):
        cfg = {("animate", "open_animation"): "zoom"}
        self._light(cfg).update(full=True, used=10, total=100)
        self.assertEqual(cfg[("animate", "open_animation")], "zoom")
        self._light(cfg, wanted=False).update(full=True, used=99, total=100)
        self.assertEqual(cfg[("animate", "open_animation")], "zoom")


if __name__ == "__main__":
    unittest.main()
