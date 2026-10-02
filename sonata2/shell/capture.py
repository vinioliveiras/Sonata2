"""Screenshots and screen recording, macOS Big Sur style (menu bar process).

- Thumbnail: after a screenshot, a small picture floats at the bottom
  right for a few seconds (click to open it), like macOS.
- Capture toolbar (Super+Shift+5): capture or record a display (click the
  one you want when there are several), a window (click it) or a
  selected portion; Options: where screenshots go (Pictures, Desktop,
  Documents, Clipboard, another folder) and where recordings go (Videos,
  Desktop, Documents, another folder), each on its own; timer; sound.
- Recording (wf-recorder): a small control at the top of the recorded
  display shows the time and stops it (the menu bar has a stop button
  too); the movie lands in the chosen folder.
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
# shots_to: pictures | desktop | documents | clipboard | other (shots_dir)
# movies_to: videos | desktop | documents | other (movies_dir); audio: none | system | mic
DEFAULTS = {"shots_to": "pictures", "shots_dir": "", "movies_to": "videos", "movies_dir": "",
            "timer": 0, "audio": "system"}

ui.register("""
window.sonata-capture, window.sonata-capture > contents,
window.sonata-shot, window.sonata-shot > contents { background: none; box-shadow: none; }
.cap-bar { background: %(panel_material)s; border-radius: %(r_dialog)s; padding: 6px;
  box-shadow: 0 0 0 0.5px %(hairline)s, 0 10px 30px rgba(0,0,0,0.3); color: %(label)s; font-family: %(font)s; }
.cap-bar button { min-width: 34px; min-height: 30px; padding: 0 6px; border-radius: 7px; border: none;
  background: none; box-shadow: none; color: %(label)s; }
.cap-bar button:hover { background: %(tool_hover)s; }
.cap-bar button:checked { background: alpha(%(label)s, 0.16); }
.cap-bar button.cap-go { padding: 0 12px; font-weight: 600; }
.cap-bar .cap-sep { min-width: 1px; background: %(separator)s; margin: 4px 6px; }
/* the recording control: a pill over the middle of the menu bar */
window.sonata-rec, window.sonata-rec > contents { background: none; box-shadow: none; }
.rec-pill { background: %(panel_material)s; color: %(label)s; border-radius: 99px; padding: 0 2px 0 9px;
  margin: 2px 8px 10px 8px;                                       /* room for the outline and shadow */
  min-height: 20px; box-shadow: 0 0 0 1px %(hairline)s, inset 0 0 0 1px %(highlight)s, 0 2px 8px rgba(0,0,0,0.25); font-family: %(font)s;
  font-size: 12px; font-weight: 600; font-feature-settings: "tnum"; }
.rec-dot { min-width: 8px; min-height: 8px; border-radius: 4px; background: %(sys_red)s;
  animation: rec-blink 1.4s ease-in-out infinite; }
@keyframes rec-blink { 50%% { opacity: 0.3; } }
.rec-pill image.rec-sound { -gtk-icon-size: 12px; opacity: 0.7; }
.rec-pill button { min-width: 18px; min-height: 18px; padding: 0; margin: 1px 0; border-radius: 99px;
  border: none; box-shadow: none; background: alpha(%(label)s, 0.1); color: %(label)s; }
.rec-pill button:hover { background: alpha(%(label)s, 0.2); }
.rec-pill button image { -gtk-icon-size: 12px; }
.shot-thumb { border-radius: 6px; box-shadow: 0 0 0 0.5px rgba(0,0,0,0.35), 0 8px 24px rgba(0,0,0,0.35); }
""", key="capture")


_SPECIAL = {"desktop": GLib.UserDirectory.DIRECTORY_DESKTOP, "documents": GLib.UserDirectory.DIRECTORY_DOCUMENTS,
            "pictures": GLib.UserDirectory.DIRECTORY_PICTURES, "videos": GLib.UserDirectory.DIRECTORY_VIDEOS}


def _dir(where: str, other: str = "") -> str:
    """The folder a capture goes to (made if missing)."""
    if where == "other" and other:
        path = other
    else:
        from .. import userdirs
        path = userdirs.special(_SPECIAL.get(where, _SPECIAL["desktop"]), create=True) or GLib.get_home_dir()
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = GLib.get_home_dir()
    return path


def shots_dir(cfg) -> str:
    return _dir(cfg.get("shots_to", "pictures"), cfg.get("shots_dir", ""))


def movies_dir(cfg) -> str:
    return _dir(cfg.get("movies_to", "videos"), cfg.get("movies_dir", ""))


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


def _ipc():
    from ..wl.wfipc import WayfireIPC
    return WayfireIPC()


def outputs() -> list:
    """[{"id", "name", "geometry"}] of the displays (Wayfire)."""
    try:
        outs = _ipc().call("window-rules/list-outputs")
    except Exception:
        outs = None
    return [o for o in outs if isinstance(o, dict)] if isinstance(outs, list) else []


def window_boxes(views, outs) -> list:
    """"x,y wxh" (layout coordinates) of the windows one can pick."""
    return [w["geo"] for w in window_list(views, outs)]


def window_list(views, outs) -> list:
    """[{"geo": "x,y wxh" (layout coordinates), "title", "app"}] of the
    windows one can pick: mapped app windows on the shown workspace, front
    ones first."""
    where = {o.get("id"): o.get("geometry") or {} for o in outs}
    boxes = []
    for v in sorted((v for v in views if isinstance(v, dict)), key=lambda v: -v.get("last-focus-timestamp", 0)):
        if (v.get("role", "toplevel") != "toplevel" or not v.get("mapped", True) or v.get("minimized")
                or v.get("layer", "workspace") != "workspace"):
            continue
        g, o = v.get("geometry") or {}, where.get(v.get("output-id"))
        w, h = g.get("width", 0), g.get("height", 0)
        if o is None or w <= 0 or h <= 0:
            continue
        x, y = g.get("x", 0), g.get("y", 0)                 # output-local; other workspaces lie outside
        if x + w <= 0 or y + h <= 0 or x >= o.get("width", 0) or y >= o.get("height", 0):
            continue
        boxes.append({"geo": f"{int(o.get('x', 0) + x)},{int(o.get('y', 0) + y)} {int(w)}x{int(h)}",
                      "title": v.get("title") or "", "app": v.get("app-id") or ""})
    return boxes


def _app_info(app_id):
    from .. import apps
    try:
        key = apps.match_app_id(app_id)
        return apps.lookup(key) if key else None
    except Exception:                                  # a list to pick from never fails over an icon
        return None


def _app_gicon(app_id):
    """The app's icon (Dock artwork) for a window's app id, or None."""
    from .. import icons
    info = _app_info(app_id)
    try:
        return icons.app_icon(info) if info else None
    except Exception:
        return None


def _app_name(app_id):
    info = _app_info(app_id)
    return info.get_name() if info else (app_id or "Window")


def _slurp(args, stdin=None):
    r = subprocess.run(["slurp"] + args, input=stdin, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def audio_device(kind: str):
    """PulseAudio / PipeWire name to record: what the speakers play (the
    default output's monitor) or the default microphone. None: no audio."""
    if kind not in ("system", "mic") or not shutil.which("pactl"):
        return None
    try:
        r = subprocess.run(["pactl", "get-default-sink" if kind == "system" else "get-default-source"],
                           capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    name = r.stdout.strip()
    if r.returncode != 0 or not name:
        return None
    return name + ".monitor" if kind == "system" and not name.endswith(".monitor") else name


RENDER_NODE = "/dev/dri/renderD128"
# H.264 that plays everywhere, easiest on the games first: the GPU's own
# encoder (NVIDIA NVENC, then VA-API on AMD / Intel), else x264 on the CPU.
# One that fails to start is skipped (Capture tries the next).
ENCODERS = {
    "nvenc": ["-c", "h264_nvenc", "-x", "nv12", "-p", "preset=p2", "-p", "rc=vbr", "-p", "cq=23"],
    "vaapi": ["-c", "h264_vaapi", "-d", RENDER_NODE],
    "x264": ["-c", "libx264", "-x", "yuv420p", "-p", "preset=superfast", "-p", "crf=23"],
}


def encoders(preferred=None) -> list:
    """The encoders to try, in order (the one that worked last time first)."""
    found = [e for e, ok in (("nvenc", os.path.exists("/dev/nvidia0")),
                             ("vaapi", os.path.exists(RENDER_NODE)), ("x264", True)) if ok]
    if preferred in found:
        found.remove(preferred)
        found.insert(0, preferred)
    return found


def recorder_command(path: str, geo=None, output=None, audio=None, encoder="x264") -> list:
    """wf-recorder at a constant FPS with a low CPU priority: the game or
    app being recorded goes first."""
    cmd = (["nice", "-n", "10"] if shutil.which("nice") else []) + \
        ["wf-recorder", "-y", "-f", path, "-r", str(FPS)] + ENCODERS.get(encoder, ENCODERS["x264"])
    if geo:
        cmd += ["-g", geo]
    elif output:
        cmd += ["-o", output]
    if audio:
        cmd.append(f"--audio={audio}")          # AAC in the same file
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

    def show_shot(self, path: str, open_path: str = None):
        """path: the picture shown; open_path: what a click opens (a movie)."""
        self.path = open_path or path
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


class RecordingControl(Gtk.Window):
    """While recording: a red dot, the time, the sound being recorded and a
    stop button, over the middle of the recorded display's menu bar."""

    def __init__(self, app, owner):
        super().__init__(application=app, title="Screen Recording", decorated=False, resizable=False)
        self.add_css_class("sonata-rec")
        self.owner, self._src, self._t0 = owner, 0, 0
        box = Gtk.Box(spacing=6, css_classes=["rec-pill"], valign=Gtk.Align.CENTER)
        box.append(Gtk.Box(css_classes=["rec-dot"], valign=Gtk.Align.CENTER))
        self.time = Gtk.Label(label="0:00")
        box.append(self.time)
        self.sound = Gtk.Image(css_classes=["rec-sound"])
        box.append(self.sound)
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop Recording",
                          valign=Gtk.Align.CENTER)
        stop.connect("clicked", lambda *_: self.owner.stop_recording())
        box.append(stop)
        self.set_child(box)
        LS = layer.layer_shell()
        self.LS = LS
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-recording")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_exclusive_zone(self, -1)           # over the menu bar, not below it
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

    def start(self, output, audio: bool, kind=None):
        if self.LS and output:
            mons = Gdk.Display.get_default().get_monitors()
            for i in range(mons.get_n_items()):
                if mons.get_item(i).get_connector() == output:
                    self.LS.set_monitor(self, mons.get_item(i))
        self.sound.set_visible(audio)
        self.sound.set_from_icon_name("audio-input-microphone-symbolic" if kind == "mic"
                                      else "audio-volume-high-symbolic")
        self._t0 = GLib.get_monotonic_time()
        self._tick()
        if not self._src:
            self._src = GLib.timeout_add(1000, self._tick)
        self.present()

    def _tick(self) -> bool:
        s = int((GLib.get_monotonic_time() - self._t0) / 1_000_000)
        self.time.set_label(f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}")
        return True

    def stop(self):
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0
        self.set_visible(False)


class Capture:
    """Toolbar + recorder + thumbnail; one per menu bar process."""

    def __init__(self, app, bar):
        self.app, self.bar = app, bar
        self.thumb = None
        self.toolbar = None
        self.recorder = None           # subprocess.Popen while recording
        self.rec_path = None
        self._rec = {}                 # what is being recorded: geo, output, audio, encoders left
        self.pill = None               # the recording control (RecordingControl)

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
        """mode: screen (every display: Super+Shift+3) | display | window | area,
        or rec-display | rec-window | rec-area (rec-screen: the focused display).
        A display or a window is picked from a list (thumbnails)."""
        rec = mode.startswith("rec-")
        what = mode[4:] if rec else mode

        def go(geo=None, output=None):
            (self._record if rec else self._shoot)(geo, cfg, output)
        if what == "display":
            outs = outputs()
            if len(outs) <= 1:
                go(output=(outs[0].get("name") if outs else None) or focused_output())
                return
            from .sharepicker import _display_name, _thumb
            items = [{"name": _display_name(o.get("name", "")), "texture": _thumb(o.get("name")),
                      "icon": "video-display-symbolic", "value": o.get("name")} for o in outs]
            self._pick("Choose a display to " + ("record" if rec else "capture"), items,
                       "Record" if rec else "Capture", lambda out: go(output=out))
        elif what == "window":
            try:
                views = _ipc().call("window-rules/list-views") or []
            except Exception:
                views = []
            wins = window_list(views, outputs())
            if not wins:
                return
            from .sharepicker import thumb_region
            items = [{"name": w["title"] or _app_name(w["app"]), "texture": thumb_region(w["geo"]),
                      "badge": _app_gicon(w["app"]), "value": w["geo"]} for w in wins]
            self._pick("Choose a window to " + ("record" if rec else "capture"), items,
                       "Record" if rec else "Capture", lambda geo: go(geo=geo))
        elif what == "area":
            if not shutil.which("slurp"):
                self._missing("slurp")
                return
            geo = _slurp([])
            if geo is not None:
                go(geo=geo)
        else:                                           # screen: all displays; rec-screen: the focused one
            go(output=focused_output() if rec else None)

    def _pick(self, title, items, action, then):
        """The list to pick from; the choice runs once the list has gone."""
        from .sharepicker import Picker

        def done(value):
            if value is not None:
                GLib.timeout_add(250, lambda: (then(value), False)[1])     # not in the picture
        Picker(self.app, title, None, items, action, done).present()

    def _shoot(self, geo, cfg, output=None):
        if not shutil.which("grim"):
            self._missing("grim")
            return
        to_clip = cfg.get("shots_to") == "clipboard"
        path = os.path.join(GLib.get_tmp_dir() if to_clip else shots_dir(cfg), _name("Screenshot", "png"))
        cmd = ["grim"] + (["-g", geo] if geo else ["-o", output] if output else []) + [path]
        if subprocess.run(cmd).returncode != 0:
            return
        from .. import sounds
        sounds.play("screenshot")
        if to_clip and shutil.which("wl-copy"):
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        self.shot_taken(path)

    def _record(self, geo, cfg, output=None):
        if not shutil.which("wf-recorder"):
            self._missing("wf-recorder")
            return
        self.rec_path = os.path.join(movies_dir(cfg), _name("Screen Recording", "mp4"))
        if not geo and not output:
            output = focused_output()
        audio = audio_device(cfg.get("audio", "system"))
        self._rec = {"geo": geo, "output": output, "audio": audio,
                     "encoders": encoders(config.load("capture", DEFAULTS).get("encoder"))}
        if not self._spawn():
            return
        self.bar.set_recording(True)
        if self.pill is None:
            self.pill = RecordingControl(self.app, self)
        self.pill.start(output or self._output_at(geo), bool(audio), cfg.get("audio"))

    def _spawn(self) -> bool:
        """Start wf-recorder with the next encoder to try."""
        r = self._rec
        r["retry"] = "encoder" in r
        encoder = r["encoders"].pop(0)
        r["encoder"] = encoder
        cmd = recorder_command(self.rec_path, r["geo"], r["output"], r["audio"], encoder)
        try:
            log = open(os.path.join(GLib.get_user_cache_dir(), "sonata2", "recorder.log"),
                       "a" if r.get("retry") else "w", encoding="utf-8")       # every try of this recording
        except OSError:
            log = subprocess.DEVNULL
        try:
            if log is not subprocess.DEVNULL:
                log.write(" ".join(cmd) + "\n")
                log.flush()
            self.recorder = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        except OSError:
            self.recorder = None
            return False
        finally:
            if log is not subprocess.DEVNULL:
                log.close()                    # the child keeps its own copy
        GLib.timeout_add(1500, self._check_started, self.recorder)
        return True

    @staticmethod
    def _output_at(geo):
        """The display holding a recorded window / portion ("x,y wxh")."""
        try:
            x, y = (int(float(n)) for n in geo.split(" ")[0].split(","))
        except (AttributeError, ValueError):
            return None
        for o in outputs():
            g = o.get("geometry") or {}
            if g.get("x", 0) <= x < g.get("x", 0) + g.get("width", 0) and \
                    g.get("y", 0) <= y < g.get("y", 0) + g.get("height", 0):
                return o.get("name")
        return None

    def _check_started(self, proc) -> bool:
        """wf-recorder quitting at once: the next encoder, else say so. One
        that runs is remembered for next time."""
        if proc is not self.recorder:
            return False
        if proc.poll() is None:
            c = config.load("capture", DEFAULTS)
            if c.get("encoder") != self._rec.get("encoder"):
                c["encoder"] = self._rec.get("encoder")
                config.save("capture", c)
            return False
        if self._rec.get("encoders"):
            try:
                os.remove(self.rec_path)                # the failed start's empty file
            except OSError:
                pass
            if self._spawn():
                return False
        self.recorder = None
        self.bar.set_recording(False)
        if self.pill is not None:
            self.pill.stop()
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
        if self.pill is not None:
            self.pill.stop()
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
            self._movie_thumbnail(path)
            nc = getattr(self.bar, "notifications", None)
            if nc:
                nc.notify("Screen Recording", 0, "media-record", "Screen Recording saved",
                          os.path.basename(path), [], {"desktop-entry": "io.github.vinioliveiras.sonata2.files"},
                          -1)

    def _movie_thumbnail(self, path):
        """The floating thumbnail for a recording too (a frame from it)."""
        if not shutil.which("ffmpegthumbnailer"):
            return
        png = os.path.join(GLib.get_tmp_dir(), "sonata2-recording-thumb.png")
        try:
            proc = subprocess.Popen(["ffmpegthumbnailer", "-i", path, "-o", png, "-s", "400", "-t", "10%"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return

        def done():
            if proc.poll() is None:
                return True
            if proc.returncode == 0 and os.path.exists(png):
                if self.thumb is None:
                    self.thumb = Thumbnail(self.app)
                self.thumb.show_shot(png, open_path=path)
            return False
        GLib.timeout_add(100, done)

    def _missing(self, tool):
        nc = getattr(self.bar, "notifications", None)
        if nc:
            nc.notify("Screenshot", 0, "dialog-warning", f"{tool} is not installed",
                      f"Install {tool} to use this capture mode.", [], {}, -1)


class _Toolbar(Gtk.Window):
    MODES = (("display", "sonata-capture-screen-symbolic", "Capture a Display"),
             ("window", "sonata-capture-window-symbolic", "Capture a Window"),
             ("area", "sonata-capture-area-symbolic", "Capture Selected Portion"),
             ("rec-display", "sonata-record-screen-symbolic", "Record a Display"),
             ("rec-window", "sonata-record-window-symbolic", "Record a Window"),
             ("rec-area", "sonata-record-area-symbolic", "Record Selected Portion"))

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
        self.mode = "display"
        first = None
        for i, (mode, icon, tip) in enumerate(self.MODES):
            if i == 3:
                bar.append(Gtk.Box(css_classes=["cap-sep"]))
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=tip, group=first, active=(mode == "display"))
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

        def places(key, first, extra=()):
            other = c.get(key + "_dir") if c.get(key + "_to") == "other" else ""
            opts = [first, ("desktop", "Desktop"), ("documents", "Documents")] + list(extra)
            items = [Item(label, lambda _on, v=v: setv(key + "_to", v), checked=c.get(key + "_to") == v)
                     for v, label in opts]
            items.append(Item(f"Other: {os.path.basename(other)}" if other else "Other Folder…",
                              lambda _on: self._pick_folder(key), checked=bool(other)))
            return [items]
        pop = ui.menu.popup(button, [
            [Item("Save Screenshots To", submenu=places("shots", ("pictures", "Pictures"),
                                                        [("clipboard", "Clipboard")])),
             Item("Save Recordings To", submenu=places("movies", ("videos", "Videos")))],
            [Item("Timer: None", lambda _on: setv("timer", 0), checked=c["timer"] == 0),
             Item("Timer: 5 Seconds", lambda _on: setv("timer", 5), checked=c["timer"] == 5),
             Item("Timer: 10 Seconds", lambda _on: setv("timer", 10), checked=c["timer"] == 10)],
            # recordings: sound (macOS: Options > Microphone)
            [Item("Record Without Sound", lambda _on: setv("audio", "none"), checked=c.get("audio") == "none"),
             Item("Record System Sound", lambda _on: setv("audio", "system"),
                  checked=c.get("audio", "system") == "system"),
             Item("Record Microphone", lambda _on: setv("audio", "mic"), checked=c.get("audio") == "mic")]],
            position=Gtk.PositionType.TOP)
        button.set_active(False)
        return pop

    def _pick_folder(self, key):
        """Another folder for screenshots (key "shots") or recordings ("movies")."""
        self.set_visible(False)                       # the dialog must not sit under the toolbar
        dialog = Gtk.FileDialog(title="Save Screenshots To" if key == "shots" else "Save Recordings To",
                                accept_label="Choose")
        cur = self.cfg.get(key + "_dir")
        if cur and os.path.isdir(cur):
            dialog.set_initial_folder(Gio.File.new_for_path(cur))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                folder = None
            if folder is not None and folder.get_path():
                self.cfg[key + "_to"], self.cfg[key + "_dir"] = "other", folder.get_path()
                config.save("capture", self.cfg)
            self.present()
        dialog.select_folder(None, None, done)

    def _go(self):
        self.set_visible(False)
        self.owner.run(self.mode, self.cfg)


def capture_desktop_file(command: str) -> str:
    """Screenshot in Launchpad and the Apps Menu (Vini): opens the capture
    toolbar, the same as Super+Shift+5."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-screenshot.desktop",
                              "[Desktop Entry]\nType=Application\nName=Screenshot\n"
                              "Comment=Capture or record the screen, a window or a selection\n"
                              "Icon=accessories-screenshot\nCategories=Utility;\n"
                              "Keywords=screenshot;capture;record;screen;recording;print;\n"
                              f"Exec={command} screenshot toolbar\n")
