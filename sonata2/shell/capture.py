"""Screenshots and screen recording, macOS Big Sur style (menu bar process).

- Thumbnail: after a screenshot, a small picture floats at the bottom
  right for a few seconds (click to open it), like macOS.
- Capture toolbar (Super+Shift+5): Capture Entire Screen / Selected
  Portion, Record Entire Screen / Selected Portion, Options (save to
  Desktop, Documents or the Clipboard; 5 or 10 s timer), Capture/Record.
- Recording (wf-recorder): a stop button appears in the menu bar; the
  movie lands in the chosen folder.
Tools: grim, slurp, wf-recorder, wl-copy (optional; missing ones are
reported)."""
import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402
from . import layer  # noqa: E402

THUMB_MS = 5000
THUMB_W = 200
DEFAULTS = {"save_to": "desktop", "timer": 0}

ui.register("""
window.sonata-capture, window.sonata-capture > contents,
window.sonata-shot, window.sonata-shot > contents { background: none; box-shadow: none; }
.cap-bar { background: %(panel_material)s; border-radius: 12px; padding: 6px;
  box-shadow: 0 0 0 0.5px %(hairline)s, 0 10px 30px rgba(0,0,0,0.3); color: %(label)s; font-family: %(font)s; }
.cap-bar button { min-width: 34px; min-height: 30px; padding: 0 6px; border-radius: 7px; border: none;
  background: none; box-shadow: none; color: %(label)s; }
.cap-bar button:hover { background: %(tool_hover)s; }
.cap-bar button:checked { background: alpha(%(label)s, 0.16); }
.cap-bar button.cap-go { padding: 0 12px; font-weight: 600; }
.cap-bar .cap-sep { min-width: 1px; background: %(separator)s; margin: 4px 6px; }
.shot-thumb { border-radius: 6px; box-shadow: 0 0 0 0.5px rgba(0,0,0,0.35), 0 8px 24px rgba(0,0,0,0.35); }
""", key="capture")


def _dir(where: str) -> str:
    kind = {"documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS}.get(where, GLib.UserDirectory.DIRECTORY_DESKTOP)
    return GLib.get_user_special_dir(kind) or GLib.get_home_dir()


FPS = 60                        # a steady 60 fps, not the display's 144/180 Hz


def focused_output():
    """The display with the focus (Wayfire); None: let wf-recorder decide.
    Without -o, wf-recorder asks on the terminal when there are two
    displays, and quits (no terminal)."""
    try:
        from ..wl.wfipc import WayfireIPC
        out = WayfireIPC().call("window-rules/get-focused-output") or {}
        return (out.get("info") or {}).get("name") or None
    except Exception:
        return None


def recorder_command(path: str, geo=None, output=None) -> list:
    """wf-recorder: H.264 in yuv420p (plays everywhere, QuickTime-style
    players included) at a constant FPS, fast enough for games."""
    cmd = ["wf-recorder", "-y", "-f", path, "-r", str(FPS), "-x", "yuv420p",
           "-c", "libx264", "-p", "preset=veryfast", "-p", "crf=20"]
    if geo:
        cmd += ["-g", geo]
    elif output:
        cmd += ["-o", output]
    return cmd


def _name(prefix: str, ext: str) -> str:
    return GLib.DateTime.new_now_local().format(f"{prefix} %Y-%m-%d at %H.%M.%S.{ext}")


class Thumbnail(Gtk.Window):
    """The floating screenshot at the bottom right."""

    def __init__(self, app):
        super().__init__(application=app, title="Screenshot", decorated=False, resizable=False)
        self.add_css_class("sonata-shot")
        # the shadow needs room: margins inside the transparent window
        self.pic = Gtk.Picture(css_classes=["shot-thumb"], margin_start=24, margin_top=24, margin_end=4,
                               margin_bottom=4, overflow=Gtk.Overflow.HIDDEN)
        self.rev = Gtk.Revealer(child=self.pic, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                transition_duration=250)
        self.set_child(self.rev)
        self.path, self._src = None, 0
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self._open())
        self.pic.add_controller(click)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-screenshot")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.BOTTOM, True)
            LS.set_anchor(self, LS.Edge.RIGHT, True)
            LS.set_margin(self, LS.Edge.BOTTOM, 16)
            LS.set_margin(self, LS.Edge.RIGHT, 16)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

    def show_shot(self, path: str):
        self.path = path
        try:        # a small texture: the window takes the picture's natural size
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB_W, THUMB_W, True)
            self.pic.set_paintable(Gdk.Texture.new_for_pixbuf(pb))
        except GLib.Error:
            return
        self.present()
        self.rev.set_reveal_child(True)
        if self._src:
            GLib.source_remove(self._src)
        self._src = GLib.timeout_add(THUMB_MS, self._hide)

    def _hide(self):
        self._src = 0
        self.rev.set_reveal_child(False)
        GLib.timeout_add(260, lambda: (self.set_visible(False), False)[1])
        return False

    def _open(self):
        if self.path:
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(self.path).get_uri(), None)
            except GLib.Error:
                pass
        self._hide()


class Capture:
    """Toolbar + recorder + thumbnail; one per menu bar process."""

    def __init__(self, app, bar):
        self.app, self.bar = app, bar
        self.thumb = None
        self.toolbar = None
        self.recorder = None           # subprocess.Popen while recording
        self.rec_path = None

    # -- thumbnail --------------------------------------------------------------------------
    def shot_taken(self, path: str):
        if self.thumb is None:
            self.thumb = Thumbnail(self.app)
        self.thumb.show_shot(path)

    # -- toolbar ------------------------------------------------------------------------------
    def show_toolbar(self):
        if self.recorder is not None:          # Super+Shift+5 while recording: stop (macOS menu)
            self.stop_recording()
            return
        if self.toolbar is None:
            self.toolbar = _Toolbar(self.app, self)
        self.toolbar.present()

    def run(self, mode: str, cfg: dict):
        """mode: screen | area | rec-screen | rec-area."""
        if self.toolbar is not None:
            self.toolbar.set_visible(False)
        delay = int(cfg.get("timer", 0)) * 1000 + 250        # let the toolbar disappear first
        GLib.timeout_add(delay, lambda: (self._run(mode, cfg), False)[1])

    def _run(self, mode, cfg):
        geo = None
        if mode.endswith("area"):
            if not shutil.which("slurp"):
                self._missing("slurp")
                return
            r = subprocess.run(["slurp"], capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.strip():
                return
            geo = r.stdout.strip()
        if mode.startswith("rec"):
            self._record(geo, cfg)
        else:
            self._shoot(geo, cfg)

    def _shoot(self, geo, cfg):
        if not shutil.which("grim"):
            self._missing("grim")
            return
        to_clip = cfg.get("save_to") == "clipboard"
        path = os.path.join(GLib.get_tmp_dir() if to_clip else _dir(cfg.get("save_to")), _name("Screenshot", "png"))
        cmd = ["grim"] + (["-g", geo] if geo else []) + [path]
        if subprocess.run(cmd).returncode != 0:
            return
        from .. import sounds
        sounds.play("screenshot")
        if to_clip and shutil.which("wl-copy"):
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        self.shot_taken(path)

    def _record(self, geo, cfg):
        if not shutil.which("wf-recorder"):
            self._missing("wf-recorder")
            return
        where = cfg.get("save_to") if cfg.get("save_to") != "clipboard" else "desktop"
        self.rec_path = os.path.join(_dir(where), _name("Screen Recording", "mp4"))
        cmd = recorder_command(self.rec_path, geo, None if geo else focused_output())
        try:
            log = open(os.path.join(GLib.get_user_cache_dir(), "sonata2", "recorder.log"), "w", encoding="utf-8")
        except OSError:
            log = subprocess.DEVNULL
        try:
            self.recorder = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        except OSError:
            self.recorder = None
            return
        finally:
            if log is not subprocess.DEVNULL:
                log.close()                    # the child keeps its own copy
        self.bar.set_recording(True)
        GLib.timeout_add(1500, self._check_started, self.recorder)

    def _check_started(self, proc) -> bool:
        """wf-recorder quitting at once (no output, bad codec): say so."""
        if proc is self.recorder and proc.poll() is not None:
            self.recorder = None
            self.bar.set_recording(False)
            nc = getattr(self.bar, "notifications", None)
            if nc:
                nc.notify("Screen Recording", 0, "dialog-warning", "Screen recording didn't start",
                          "Details in ~/.cache/sonata2/recorder.log", [], {}, -1)
        return False

    def stop_recording(self):
        """Stop and finish the movie without blocking the menu bar."""
        proc, path = self.recorder, self.rec_path
        if proc is None:
            return
        self.recorder = None
        self.bar.set_recording(False)
        proc.send_signal(2)                    # SIGINT: wf-recorder finishes the file
        waited = {"ms": 0}

        def check():
            if proc.poll() is None:
                waited["ms"] += 100
                if waited["ms"] < 8000:
                    return True
                proc.kill()
                proc.wait()
            self._saved(path)
            return False
        GLib.timeout_add(100, check)

    def _saved(self, path):
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            nc = getattr(self.bar, "notifications", None)
            if nc:
                nc.notify("Screen Recording", 0, "media-record", "Screen Recording saved",
                          os.path.basename(path), [], {"desktop-entry": "io.github.vinioliveiras.sonata2.files"},
                          -1)

    def _missing(self, tool):
        nc = getattr(self.bar, "notifications", None)
        if nc:
            nc.notify("Screenshot", 0, "dialog-warning", f"{tool} is not installed",
                      f"Install {tool} to use this capture mode.", [], {}, -1)


class _Toolbar(Gtk.Window):
    MODES = (("screen", "video-display-symbolic", "Capture Entire Screen"),
             ("area", "edit-select-all-symbolic", "Capture Selected Portion"),
             ("rec-screen", "media-record-symbolic", "Record Entire Screen"),
             ("rec-area", "camera-video-symbolic", "Record Selected Portion"))

    def __init__(self, app, owner: Capture):
        super().__init__(application=app, title="Screenshot", decorated=False, resizable=False)
        self.add_css_class("sonata-capture")
        self.owner = owner
        self.cfg = config.load("capture", DEFAULTS)
        bar = Gtk.Box(spacing=2, css_classes=["cap-bar"])
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close")
        close.connect("clicked", lambda *_: self.set_visible(False))
        bar.append(close)
        bar.append(Gtk.Box(css_classes=["cap-sep"]))
        self.mode = "screen"
        first = None
        for i, (mode, icon, tip) in enumerate(self.MODES):
            if i == 2:
                bar.append(Gtk.Box(css_classes=["cap-sep"]))
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip, group=first, active=(mode == "screen"))
            b.connect("toggled", lambda b, m=mode: b.get_active() and self._set_mode(m))
            first = first or b
            bar.append(b)
        bar.append(Gtk.Box(css_classes=["cap-sep"]))
        opts = Gtk.MenuButton(label="Options", direction=Gtk.ArrowType.UP)
        opts.set_create_popup_func(self._options)
        bar.append(opts)
        self.go = Gtk.Button(label="Capture", css_classes=["cap-go"])
        self.go.connect("clicked", lambda *_: self._go())
        bar.append(self.go)
        self.set_child(bar)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.set_visible(False), True)[1] if k == Gdk.KEY_Escape
                     else (self._go(), True)[1] if k == Gdk.KEY_Return else False)
        self.add_controller(keys)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-capture")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.BOTTOM, True)
            LS.set_margin(self, LS.Edge.BOTTOM, 120)
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)   # Esc / Return at once (macOS)

    def _set_mode(self, m):
        self.mode = m
        self.go.set_label("Record" if m.startswith("rec") else "Capture")

    def _options(self, button):
        Item = ui.menu.Item
        c = self.cfg

        def setv(k, v):
            c[k] = v
            config.save("capture", c)
        pop = ui.menu.popup(button, [
            [Item("Save to Desktop", lambda _on: setv("save_to", "desktop"), checked=c["save_to"] == "desktop"),
             Item("Save to Documents", lambda _on: setv("save_to", "documents"),
                  checked=c["save_to"] == "documents"),
             Item("Copy to Clipboard", lambda _on: setv("save_to", "clipboard"),
                  checked=c["save_to"] == "clipboard")],
            [Item("Timer: None", lambda _on: setv("timer", 0), checked=c["timer"] == 0),
             Item("Timer: 5 Seconds", lambda _on: setv("timer", 5), checked=c["timer"] == 5),
             Item("Timer: 10 Seconds", lambda _on: setv("timer", 10), checked=c["timer"] == 10)]],
            position=Gtk.PositionType.TOP)
        button.set_active(False)
        return pop

    def _go(self):
        self.set_visible(False)
        self.owner.run(self.mode, self.cfg)
