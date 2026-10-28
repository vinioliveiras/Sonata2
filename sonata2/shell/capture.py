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
from ..i18n import N_, _  # noqa: E402
from . import layer  # noqa: E402

THUMB_MS = 5000
CLIPBOARD_SHOT = "sonata2-clipboard-shot.png"   # a clipboard-only screenshot's one reused file
THUMB_W = 200
# shots_to: pictures | desktop | documents | clipboard | other (shots_dir)
# movies_to: videos | desktop | documents | other (movies_dir); audio: none | system | mic
# shots_copy: also on the clipboard when saved to a folder (Vini: both at once)
DEFAULTS = {"shots_to": "pictures", "shots_dir": "", "shots_copy": False, "movies_to": "videos", "movies_dir": "",
            "timer": 0, "audio": "system", "mode": "display",     # mode: the toolbar's last one (Vini)
            "encoder": ""}                                         # the recorder encoder that worked last

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
/* the recording / screen sharing control over the middle of the menu bar:
   bare, in the menu bar's own text colour (light and dark); the stop button
   is the only colour (Vini) */
window.sonata-rec, window.sonata-rec > contents { background: none; box-shadow: none; }
.rec-pill { background: none; box-shadow: none; color: %(label)s; padding: 0 2px 0 4px; margin: 2px 8px;
  min-height: 20px; font-family: %(font)s; font-size: 12px; font-weight: 600; font-feature-settings: "tnum"; }
/* the dot blinks in two steps: a smooth fade repainted the menu bar on
   every frame for as long as a recording ran (GPU busy the whole time);
   steps(1) changes it twice a cycle, so only two repaints */
.rec-dot { min-width: 7px; min-height: 7px; border-radius: 4px; background: %(label)s;
  animation: rec-blink 1.4s steps(1) infinite; }
@keyframes rec-blink { 50%% { opacity: 0.3; } }
.rec-pill image.share-icon { -gtk-icon-size: 13px; color: %(label)s; }
.rec-pill image.rec-sound { -gtk-icon-size: 12px; opacity: 0.7; }
.rec-pill button { min-width: 14px; min-height: 14px; padding: 0; margin: 3px 0; border-radius: 99px;
  border: none; box-shadow: none; background: %(sys_red)s; color: white;
  transition: opacity 120ms ease-out, transform 120ms ease-out; }
.rec-pill button:hover { opacity: 0.85; }
.rec-pill button:active { transform: scale(0.92); }
.rec-pill button .stop-glyph { min-width: 5px; min-height: 5px; border-radius: 1px; background: white; }
.shot-thumb { border-radius: 6px; box-shadow: 0 0 0 0.5px rgba(0,0,0,0.35), 0 8px 24px rgba(0,0,0,0.35); }
""", key="capture")


def _logs_path(name: str) -> str:
    from .. import logs
    return logs.path(name)


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
    return info.get_name() if info else (app_id or _("Window"))


def _slurp(args, stdin=None):
    r = subprocess.run(["slurp"] + args, input=stdin, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _slurp_async(args, done) -> None:
    """slurp without blocking the menu bar (the frozen screen must draw
    under it); done("x,y wxh" or None)."""
    try:
        proc = Gio.Subprocess.new(["slurp"] + args, Gio.SubprocessFlags.STDOUT_PIPE |
                                  Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        done(None)
        return

    def finished(p, res):
        try:
            _ok, out, _err = p.communicate_utf8_finish(res)
        except GLib.Error:
            out = None
        out = (out or "").strip()
        done(out if p.get_successful() and out else None)
    proc.communicate_utf8_async(None, None, finished)


def _box(geo: str):
    """"x,y wxh" -> (x, y, w, h), None when it isn't one."""
    try:
        xy, wh = geo.split(" ")
        x, y = (float(n) for n in xy.split(","))
        w, h = (float(n) for n in wh.split("x"))
        return x, y, w, h
    except (AttributeError, ValueError):
        return None


class Frozen:
    """Every display as it was when a screenshot started. Picking a portion
    or a window takes the keyboard, and an open menu (the menu bar's, a
    right-click one) closed before it was in the picture (Vini): the
    picture now comes from this, not from the live screen."""

    def __init__(self, pixbuf, outs):
        self.pb = pixbuf
        geos = [o.get("geometry") or {} for o in outs] or [{"width": pixbuf.get_width(),
                                                           "height": pixbuf.get_height()}]
        self.x0 = min(g.get("x", 0) for g in geos)
        self.y0 = min(g.get("y", 0) for g in geos)
        width = max(g.get("x", 0) + g.get("width", 0) for g in geos) - self.x0
        self.scale = pixbuf.get_width() / width if width > 0 else 1.0     # grim: the largest display scale
        self.outs = {o.get("name"): o.get("geometry") or {} for o in outs}

    @classmethod
    def take(cls):
        """None without grim (or when it fails): the live screen then."""
        if not shutil.which("grim"):
            return None
        path = os.path.join(GLib.get_user_runtime_dir() or GLib.get_tmp_dir(), "sonata2-frozen.png")
        try:
            if subprocess.run(["grim", "-l", "0", path], timeout=5).returncode != 0:     # no compression: fast
                return None
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except (OSError, subprocess.SubprocessError, GLib.Error):
            return None
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        return cls(pb, outputs())

    def crop(self, geo: str):
        """The part "x,y wxh" (layout coordinates), None when it's off screen."""
        box = _box(geo)
        if box is None:
            return None
        s = self.scale
        x, y = round((box[0] - self.x0) * s), round((box[1] - self.y0) * s)
        x2, y2 = round((box[0] + box[2] - self.x0) * s), round((box[1] + box[3] - self.y0) * s)
        x, y = max(0, x), max(0, y)
        x2, y2 = min(self.pb.get_width(), x2), min(self.pb.get_height(), y2)
        if x2 <= x or y2 <= y:
            return None
        return self.pb.new_subpixbuf(x, y, x2 - x, y2 - y).copy()

    def output(self, name: str):
        g = self.outs.get(name)
        if not g:
            return None
        return self.crop(f"{g.get('x', 0)},{g.get('y', 0)} {g.get('width', 0)}x{g.get('height', 0)}")


class _FrozenCover:
    """The frozen screen shown under the portion selection, so what one
    selects (an open menu too) is what lands in the picture."""

    def __init__(self, app, frozen: Frozen):
        self.wins = []
        LS = layer.layer_shell()
        display = Gdk.Display.get_default()
        if not LS or display is None:
            return
        mons = display.get_monitors()
        for i in range(mons.get_n_items()):
            mon = mons.get_item(i)
            pb = frozen.output(mon.get_connector())
            if pb is None:
                continue
            w = Gtk.Window(application=app, decorated=False, css_classes=["sonata-capture"])
            w.set_child(Gtk.Picture(paintable=Gdk.Texture.new_for_pixbuf(pb), content_fit=Gtk.ContentFit.FILL,
                                    can_shrink=True))
            LS.init_for_window(w)
            LS.set_namespace(w, "sonata2-capture-frozen")
            LS.set_layer(w, LS.Layer.OVERLAY)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(w, e, True)
            LS.set_exclusive_zone(w, -1)
            LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
            LS.set_monitor(w, mon)
            w.present()
            self.wins.append(w)

    def close(self) -> None:
        for w in self.wins:
            w.destroy()
        self.wins = []


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


# wl-screenrec first when it's there (Vini: the drawing palette must stay out
# of recordings): it captures over ext-image-copy-capture -- sonata-corners'
# capture, without Sonata's controls -- where wf-recorder only knows
# wlr-screencopy (the screen as shown). Its GPU encoder (VA-API), else the CPU.
SCREENREC = ("screenrec", "screenrec-sw")


def encoders(preferred=None) -> list:
    """The encoders to try, in order (the one that worked last time first);
    "screenrec*" are wl-screenrec's, the others wf-recorder's."""
    found = list(SCREENREC) if shutil.which("wl-screenrec") else []
    if shutil.which("wf-recorder") or not found:
        found += [e for e, ok in (("nvenc", os.path.exists("/dev/nvidia0")),
                                  ("vaapi", os.path.exists(RENDER_NODE)), ("x264", True)) if ok]
    if preferred in found:
        # within its own group only: wl-screenrec stays ahead of wf-recorder
        # (a stored "vaapi" -- older settings, or one fallback -- put
        # wf-recorder first, and the drawing palette was in the video again)
        group = [e for e in found if (e in SCREENREC) == (preferred in SCREENREC)]
        start = found.index(group[0])
        found.remove(preferred)
        found.insert(start, preferred)
    return found


def print_mode(last: str) -> str:
    """Print Screen's capture, from the toolbar's last mode: the same, as a
    still (Record a Window -> Capture a Window)."""
    still = last[4:] if last.startswith("rec-") else last
    return still if still in ("display", "window", "area") else "display"


def recorder_command(path: str, geo=None, output=None, audio=None, encoder="x264") -> list:
    """wf-recorder at a constant FPS with a low CPU priority: the game or
    app being recorded goes first. "screenrec*": wl-screenrec instead."""
    if encoder in SCREENREC:
        cmd = (["nice", "-n", "10"] if shutil.which("nice") else []) + ["wl-screenrec", "-f", path]
        if encoder == "screenrec-sw":
            cmd.append("--no-hw")
        if geo:
            cmd += ["-g", geo]
        elif output:
            cmd += ["-o", output]
        if audio:
            cmd += ["--audio", "--audio-device", audio]
        return cmd
    cmd = (["nice", "-n", "10"] if shutil.which("nice") else []) + \
        ["wf-recorder", "-y", "-f", path, "-r", str(FPS)] + ENCODERS.get(encoder, ENCODERS["x264"])
    if geo:
        cmd += ["-g", geo]
    elif output:
        cmd += ["-o", output]
    if audio:
        cmd.append(f"--audio={audio}")          # AAC in the same file
    return cmd


# Microphone and the system's sound at once (Vini): separate tracks in the
# movie. The recorders take one sound device, so the picture is recorded
# silent and FFmpeg records both devices beside it (one AAC track each). At
# the end: the picture copied as it is, a first track with both mixed -- the
# one players play (they play one track: with only the two apart, the
# microphone went unheard) -- then the system's sound and the microphone
# apart, for editing.
TRACK_TITLES = ("System Sound + Microphone", "System Sound", "Microphone")


def tracks_command(path: str, system: str, mic: str) -> list:
    """FFmpeg recording the system's sound and the microphone as two tracks."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "pulse", "-i", system, "-f", "pulse", "-i", mic,
            "-map", "0:a", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", path]


def mux_command(video: str, audio: str, out: str) -> list:
    """The silent movie and its sound in one file: both mixed first (the
    default track), then each apart -- only the mix is encoded again."""
    return ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", video, "-i", audio,
            "-filter_complex", "[1:a:0][1:a:1]amix=inputs=2:duration=longest:normalize=0[mix]",
            "-map", "0:v", "-map", "[mix]", "-map", "1:a:0", "-map", "1:a:1",
            "-c:v", "copy", "-c:a:0", "aac", "-b:a:0", "192k", "-c:a:1", "copy", "-c:a:2", "copy",
            "-disposition:a:0", "default", "-disposition:a:1", "0", "-disposition:a:2", "0",
            *[a for i, t in enumerate(TRACK_TITLES)            # (MP4 names a track by its handler)
              for a in (f"-metadata:s:a:{i}", f"title={t}", f"-metadata:s:a:{i}", f"handler_name={t}")],
            "-movflags", "+faststart", out]


def _hidden_beside(path: str, ext: str) -> str:
    d, n = os.path.split(path)
    return os.path.join(d, "." + os.path.splitext(n)[0] + ext)


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
        """A screenshot: Preview in Markup, to draw on it (Vini); a movie: its player."""
        if self.path:
            open_shot(self.path)
        self._hide()


TOOLBAR_BACK_MS = 450      # a kept toolbar comes back once the editor has opened


def open_shot(path: str) -> bool:
    """A screenshot in Preview's Markup (draw, crop, save); anything else
    (a movie) in its default app. False: it could not be opened."""
    try:
        if path.lower().endswith(".png") and os.path.isfile(path):
            from ..__main__ import self_argv
            GLib.spawn_async(self_argv() + ["preview", "--markup", path], flags=GLib.SpawnFlags.SEARCH_PATH)
        else:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        return True
    except GLib.Error:
        return False


def stop_button(tooltip: str) -> Gtk.Button:
    """The pills' stop button: a white square on red (the pills' only colour)."""
    b = Gtk.Button(tooltip_text=tooltip, valign=Gtk.Align.CENTER)
    b.set_child(Gtk.Box(css_classes=["stop-glyph"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER))
    return b


class BarPill(Gtk.Window):
    """A pill over the middle of the menu bar (macOS' recording / screen
    sharing controls): put `box` (a .rec-pill) in, then show_on(output)."""

    def __init__(self, app, title, namespace):
        super().__init__(application=app, title=title, decorated=False, resizable=False)
        self.add_css_class("sonata-rec")
        self.box = Gtk.Box(spacing=6, css_classes=["rec-pill"], valign=Gtk.Align.CENTER)
        self.set_child(self.box)
        # as tall as the menu bar, the pill centred in it (Vini: with the taller
        # 32 px bar it sat at the top, its own height under the bar's middle)
        from . import menubar_size
        self.set_size_request(-1, menubar_size.height())
        LS = layer.layer_shell()
        self.LS = LS
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, namespace)
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_exclusive_zone(self, -1)           # over the menu bar, not below it
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)

    def show_on(self, output=None):
        if self.LS and output:
            mons = Gdk.Display.get_default().get_monitors()
            for i in range(mons.get_n_items()):
                if mons.get_item(i).get_connector() == output:
                    self.LS.set_monitor(self, mons.get_item(i))
        self.present()


class RecordingControl(BarPill):
    """While recording: a red dot, the time, the sound being recorded and a
    stop button, over the middle of the recorded display's menu bar."""

    def __init__(self, app, owner):
        super().__init__(app, _("Screen Recording"), "sonata2-recording")
        self.owner, self._src, self._t0 = owner, 0, 0
        box = self.box
        box.append(Gtk.Box(css_classes=["rec-dot"], valign=Gtk.Align.CENTER))
        self.time = Gtk.Label(label="0:00")
        box.append(self.time)
        self.sound = Gtk.Image(css_classes=["rec-sound"])
        box.append(self.sound)
        from .livedraw import pen_button                 # draw on the screen (livedraw.py)
        self.output = None
        box.append(pen_button(app, lambda: self.output))
        stop = stop_button(_("Stop Recording"))
        stop.connect("clicked", lambda *_: self.owner.stop_recording())
        box.append(stop)

    def start(self, output, audio: bool, kind=None):
        self.sound.set_visible(audio)
        self.sound.set_from_icon_name("audio-input-microphone-symbolic" if kind in ("mic", "both")
                                      else "audio-volume-high-symbolic")
        self.sound.set_tooltip_text(_("System sound and microphone: mixed, and each on its own track") if kind == "both" else None)
        self.output = output
        self._t0 = GLib.get_monotonic_time()
        self._tick()
        if not self._src:
            self._src = GLib.timeout_add(1000, self._tick)
        self.show_on(output)

    def _tick(self) -> bool:
        s = int((GLib.get_monotonic_time() - self._t0) / 1_000_000)
        self.time.set_label(f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}")
        return True

    def stop(self):
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0
        self.set_visible(False)
        from . import livedraw                           # the recording ended: the drawing goes too
        livedraw.get(self.get_application()).stop()


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
        # kept open (Vini): after Print the toolbar stays -- more shots, other
        # options -- until its close button or Esc; each shot opens in Markup
        self.keep = False
        self._tracks = None            # two sound tracks: {"proc", "video", "audio", "out"} (tracks_command)
        self._replay = None            # instant replay (replay.py), made when first asked for

    @property
    def replay(self):
        if self._replay is None:
            from .replay import Replay
            self._replay = Replay(self)
        return self._replay

    # -- thumbnail --------------------------------------------------------------------------
    def shot_taken(self, path: str, edit: bool = False):
        if edit or self.keep:                  # Print Screen: straight into Markup, no thumbnail
            open_shot(path)
            self.keep = True                   # the toolbar stays for the next ones (Vini)
            self._toolbar_back()
            return
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
        self.toolbar.restore()
        self.toolbar.set_kept(self.keep)
        self.toolbar.present()

    def close_toolbar(self) -> None:
        """Its close button or Esc: gone, and no longer kept."""
        self.keep = False
        if self.toolbar is not None:
            self.toolbar.set_visible(False)

    def _toolbar_back(self) -> None:
        """Kept: the toolbar again once a shot is done (or a choice cancelled),
        after the editor has opened (so it comes above it)."""
        if self.keep and self.recorder is None:
            GLib.timeout_add(TOOLBAR_BACK_MS, lambda: (self.keep and self.recorder is None
                                                        and self.show_toolbar(), False)[1])

    def run(self, mode: str, cfg: dict):
        """mode: screen | area | edit | rec-screen | rec-area."""
        shown = self.toolbar is not None and self.toolbar.get_visible()
        if shown:
            self.toolbar.set_visible(False)
        # the toolbar: let it disappear first; a shortcut: at once (an open menu still there).
        # The timer runs once the display, window or portion is chosen (_run)
        GLib.timeout_add(250 if shown else 0, lambda: (self._run(mode, cfg), False)[1])

    def _run(self, mode, cfg):
        """mode: screen (every display: Super+Shift+3) | display | window | area,
        edit (Print Screen, see below), or rec-display | rec-window | rec-area
        (rec-screen: the focused display). A display or a window is picked
        from a list (thumbnails).

        edit (Vini): Print captures at once and opens the shot in Markup,
        with what the toolbar used last -- its mode (a recording mode: the
        same kind of still), folder and clipboard; no timer, like every
        shortcut. No list to pick from: the display in front, or the window
        in front; a portion is still drawn by hand."""
        edit = mode == "edit"
        if edit:
            mode = print_mode(str(cfg.get("mode") or "display"))
            cfg = dict(cfg, timer=0)
        rec = mode.startswith("rec-")
        what = mode[4:] if rec else mode
        # a picture of a portion / window / display: the screen as it is now,
        # before a list or the selection takes the keyboard (and closes menus).
        # Recording pickers take it too: one grim for every thumbnail.
        outs = outputs() if what in ("display", "window") else []
        picker = not edit and (what == "window" or (what == "display" and len(outs) > 1))
        frozen = Frozen.take() if (not rec and what != "screen") or (rec and picker) else None

        timer = int(cfg.get("timer", 0) or 0)

        def go(geo=None, output=None):
            """What was chosen, captured -- after the timer when there is one
            (Vini: the display / window list came after the countdown; it's
            first now, as on macOS). After a timer the screen is taken then,
            not the one frozen when choosing."""
            if timer > 0:
                GLib.timeout_add(timer * 1000, lambda: (now(geo, output, None), False)[1])
            else:
                now(geo, output, frozen)

        def now(geo, output, still):
            if rec:
                self._record(geo, cfg, output)
            else:
                self._shoot(geo, cfg, output, frozen=still, **({"edit": True} if edit else {}))
        if edit and what == "display":
            go(output=focused_output())
            return
        if edit and what == "window":
            try:
                views = _ipc().call("window-rules/list-views") or []
            except Exception:
                views = []
            wins = window_list(views, outs)
            # the window in front (front ones first); none: its display
            go(geo=wins[0]["geo"]) if wins else go(output=focused_output())
            return
        if what == "display":
            if len(outs) <= 1:
                go(output=(outs[0].get("name") if outs else None) or focused_output())
                return
            from .sharepicker import _display_name, fit_texture
            items = [{"name": _display_name(o.get("name", "")),
                      "texture": fit_texture(frozen.output(o.get("name"))) if frozen else None,
                      "icon": "video-display-symbolic", "value": o.get("name")} for o in outs]
            self._pick(_("Choose a display to record") if rec else _("Choose a display to capture"), items,
                       _("Record") if rec else _("Capture"), lambda out: go(output=out))
        elif what == "window":
            try:
                views = _ipc().call("window-rules/list-views") or []
            except Exception:
                views = []
            wins = window_list(views, outs)
            if not wins:
                return
            from .sharepicker import fit_texture
            items = [{"name": w["title"] or _app_name(w["app"]),
                      "texture": fit_texture(frozen.crop(w["geo"])) if frozen else None,
                      "badge": _app_gicon(w["app"]), "value": w["geo"]} for w in wins]
            self._pick(_("Choose a window to record") if rec else _("Choose a window to capture"), items,
                       _("Record") if rec else _("Capture"), lambda geo: go(geo=geo))
        elif what == "area":
            if not shutil.which("slurp"):
                self._missing("slurp")
                return
            cover = _FrozenCover(self.app, frozen) if frozen is not None else None

            def picked(geo):
                if cover is not None:
                    cover.close()
                if geo is not None:
                    go(geo=geo)
                else:
                    self._toolbar_back()               # Esc on the selection: the kept toolbar again
            # once the frozen screen is up (slurp maps over it)
            GLib.timeout_add(60 if cover is not None and cover.wins else 0,
                             lambda: (_slurp_async([], picked), False)[1])
        else:                                           # screen: all displays; rec-screen: the focused one
            go(output=focused_output() if rec else None)

    def _pick(self, title, items, action, then):
        """The list to pick from; the choice runs once the list has gone."""
        from .sharepicker import Picker

        def done(value):
            if value is not None:
                GLib.timeout_add(250, lambda: (then(value), False)[1])     # not in the picture
            else:
                self._toolbar_back()
        Picker(self.app, title, None, items, action, done).present()

    def _shoot(self, geo, cfg, output=None, frozen=None, edit=False):
        if not shutil.which("grim"):
            self._missing("grim")
            return
        clip_only = cfg.get("shots_to") == "clipboard"
        to_clip = clip_only or bool(cfg.get("shots_copy"))        # in the folder and on the clipboard (Vini)
        # clipboard only: one reused file in the runtime dir (tmpfs, gone at logout), not a new one in /tmp per shot
        path = os.path.join(GLib.get_user_runtime_dir() or GLib.get_tmp_dir(), CLIPBOARD_SHOT) \
            if clip_only else os.path.join(shots_dir(cfg), _name("Screenshot", "png"))
        if frozen is not None:
            pb = frozen.crop(geo) if geo else frozen.output(output) if output else frozen.pb
            if pb is None:
                return
            try:
                pb.savev(path, "png", [], [])
            except GLib.Error:
                return
        else:
            cmd = ["grim"] + (["-g", geo] if geo else ["-o", output] if output else []) + [path]
            if subprocess.run(cmd).returncode != 0:
                return
        from .. import sounds
        sounds.play("screenshot")
        if to_clip and shutil.which("wl-copy"):
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f)
        self.shot_taken(path, **({"edit": True} if edit else {}))   # (stand-ins take the path alone)

    def _record(self, geo, cfg, output=None):
        if not shutil.which("wf-recorder") and not shutil.which("wl-screenrec"):
            self._missing("wf-recorder")
            return
        self.rec_path = os.path.join(movies_dir(cfg), _name("Screen Recording", "mp4"))
        if not geo and not output:
            output = focused_output()
        kind = cfg.get("audio", "system")
        two = bool(kind == "both" and shutil.which("ffmpeg") and audio_device("system") and audio_device("mic"))
        if kind == "both" and not two:
            kind = "system"                          # no FFmpeg / no microphone: the system's sound alone
        audio = None if two else audio_device(kind)
        out = self.rec_path
        if two:                                      # the picture silent, beside it the two tracks
            self.rec_path = _hidden_beside(out, ".video.mp4")
        self._rec = {"geo": geo, "output": output, "audio": audio,
                     "encoders": encoders(config.load("capture", DEFAULTS).get("encoder"))}
        if not self._spawn():
            self.rec_path = out
            return
        if two:
            self._start_tracks(out)
            audio = True
        if self.pill is None:
            self.pill = RecordingControl(self.app, self)
        self.pill.start(output or self._output_at(geo), bool(audio), kind)

    def _spawn(self) -> bool:
        """Start wf-recorder with the next encoder to try."""
        r = self._rec
        r["retry"] = "encoder" in r
        encoder = r["encoders"].pop(0)
        r["encoder"] = encoder
        cmd = recorder_command(self.rec_path, r["geo"], r["output"], r["audio"], encoder)
        try:
            from .. import logs
            log = open(logs.path("recorder.log"),
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
            enc = self._rec.get("encoder")
            if config.load("capture", DEFAULTS).get("encoder") != enc:
                config.update("capture", encoder=enc)          # keep the toolbar's options as stored
            return False
        if self._rec.get("encoders"):
            try:
                os.remove(self.rec_path)                # the failed start's empty file
            except OSError:
                pass
            if self._spawn():
                return False
        self.recorder = None
        self._stop_tracks(discard=True)
        if self.pill is not None:
            self.pill.stop()
        nc = getattr(self.bar, "notifications", None)
        if nc:
            nc.notify("Screen Recording", 0, "dialog-warning", _("Screen recording didn't start"),
                      _("Details in {path}").format(path=_logs_path("recorder.log")), [], {}, -1)
        return False

    def _start_tracks(self, out: str) -> None:
        apath = _hidden_beside(out, ".audio.mka")
        cmd = tracks_command(apath, audio_device("system"), audio_device("mic"))
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            proc = None
        self._tracks = {"proc": proc, "video": self.rec_path, "audio": apath, "out": out}

    def _stop_tracks(self, discard: bool = False):
        """The sound tracks' recording ends (SIGINT: FFmpeg finishes its file).
        discard: the recording didn't happen -- its files go."""
        t, self._tracks = self._tracks, None
        if t is None:
            return None
        if t["proc"] is not None and t["proc"].poll() is None:
            t["proc"].send_signal(2)
        if discard:
            if t["proc"] is not None:
                try:
                    t["proc"].wait(timeout=3)
                except subprocess.TimeoutExpired:
                    t["proc"].kill()
            for f in (t["video"], t["audio"]):
                try:
                    os.remove(f)
                except OSError:
                    pass
            return None
        return t

    def _mux(self, t, done) -> None:
        """Picture and the two tracks in one movie, then done(path) -- off the
        main loop. Without the tracks (FFmpeg stopped at once): the picture."""
        def finished(ok):
            path = t["out"]
            if ok and os.path.exists(path) and os.path.getsize(path) > 0:
                for f in (t["video"], t["audio"]):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
            else:                                      # keep what was recorded: the picture alone
                try:
                    os.replace(t["video"], path)
                except OSError:
                    path = t["video"]
            done(path)

        if not (os.path.exists(t["audio"]) and os.path.getsize(t["audio"]) > 0):
            finished(False)
            return
        try:
            proc = subprocess.Popen(mux_command(t["video"], t["audio"], t["out"]), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            finished(False)
            return

        def check():
            if proc.poll() is None:
                return True
            finished(proc.returncode == 0)
            return False
        GLib.timeout_add(150, check)

    def stop_recording(self):
        """Stop and finish the movie without blocking the menu bar."""
        proc, path = self.recorder, self.rec_path
        if proc is None:
            return
        tracks = self._stop_tracks()
        self.recorder = None
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
            if tracks is not None:                     # two sound tracks: together, then saved
                def tracks_done():
                    if tracks["proc"] is not None and tracks["proc"].poll() is None:
                        return True
                    self._mux(tracks, lambda p: (self._saved(p), self._toolbar_back()))
                    return False
                GLib.timeout_add(100, tracks_done)
                return False
            self._saved(path)
            self._toolbar_back()                       # kept: back after the recording
            return False
        GLib.timeout_add(100, check)

    def _saved(self, path):
        if path and os.path.exists(path) and os.path.getsize(path) > 0:
            self._movie_thumbnail(path)
            nc = getattr(self.bar, "notifications", None)
            if nc:
                nc.notify("Screen Recording", 0, "media-record", _("Screen Recording saved"),
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
            nc.notify("Screenshot", 0, "dialog-warning", _("{tool} is not installed").format(tool=tool),
                      _("Install {tool} to use this capture mode.").format(tool=tool), [], {}, -1)


class _Toolbar(Gtk.Window):
    MODES = (("display", "sonata-capture-screen-symbolic", N_("Capture a Display")),
             ("window", "sonata-capture-window-symbolic", N_("Capture a Window")),
             ("area", "sonata-capture-area-symbolic", N_("Capture Selected Portion")),
             ("rec-display", "sonata-record-screen-symbolic", N_("Record a Display")),
             ("rec-window", "sonata-record-window-symbolic", N_("Record a Window")),
             ("rec-area", "sonata-record-area-symbolic", N_("Record Selected Portion")))

    def __init__(self, app, owner: Capture):
        super().__init__(application=app, title="Screenshot", decorated=False, resizable=False)
        self.add_css_class("sonata-capture")
        self.owner = owner
        self.cfg = config.load("capture", DEFAULTS)
        bar = Gtk.Box(spacing=2, css_classes=["cap-bar"])
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Close"))
        close.connect("clicked", lambda *_: owner.close_toolbar())
        bar.append(close)
        bar.append(Gtk.Box(css_classes=["cap-sep"]))
        self.mode = "display"
        self.mode_buttons = {}
        first = None
        for i, (mode, icon, tip) in enumerate(self.MODES):
            if i == 3:
                bar.append(Gtk.Box(css_classes=["cap-sep"]))
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=_(tip), group=first, active=(mode == "display"))
            b.connect("toggled", lambda b, m=mode: b.get_active() and self._set_mode(m))
            first = first or b
            bar.append(b)
            self.mode_buttons[mode] = b
        bar.append(Gtk.Box(css_classes=["cap-sep"]))
        opts = Gtk.MenuButton(label=_("Options"), direction=Gtk.ArrowType.UP)
        opts.set_create_popup_func(self._options)
        bar.append(opts)
        self.go = Gtk.Button(label=_("Capture"), css_classes=["cap-go"])
        self.go.connect("clicked", lambda *_: self._go())
        bar.append(self.go)
        self.set_child(bar)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (owner.close_toolbar(), True)[1] if k == Gdk.KEY_Escape
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

    def set_kept(self, kept: bool) -> None:
        """Kept open (after Print): it takes the keyboard only when clicked,
        so the editor beside it can be typed in; otherwise at once (Esc /
        Return, macOS)."""
        LS = layer.layer_shell()
        if LS and LS.is_layer_window(self):
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND if kept else LS.KeyboardMode.EXCLUSIVE)

    def _set_mode(self, m):
        self.mode = m
        self.go.set_label(_("Record") if m.startswith("rec") else _("Capture"))
        if self.cfg.get("mode") != m:                 # opened next time on the same one (Vini)
            self.cfg["mode"] = m
            config.update("capture", mode=m)

    def restore(self) -> None:
        """Each time it opens: the last mode, and the options as saved."""
        self.cfg = config.load("capture", DEFAULTS)
        b = self.mode_buttons.get(self.cfg.get("mode")) or self.mode_buttons["display"]
        b.set_active(True)
        self._set_mode(next(m for m, x in self.mode_buttons.items() if x is b))

    def _options(self, button):
        Item = ui.menu.Item
        c = self.cfg

        def setv(k, v):
            c[k] = v
            config.update("capture", **{k: v})             # not a stale copy over the saved encoder

        def places(key, first, extra=()):
            other = c.get(key + "_dir") if c.get(key + "_to") == "other" else ""
            opts = [first, ("desktop", _("Desktop")), ("documents", _("Documents"))] + list(extra)
            items = [Item(label, lambda _on, v=v: setv(key + "_to", v), checked=c.get(key + "_to") == v)
                     for v, label in opts]
            items.append(Item(_("Other: {folder}").format(folder=os.path.basename(other)) if other else _("Other Folder…"),
                              lambda _on: self._pick_folder(key), checked=bool(other)))
            return [items]
        pop = ui.menu.popup(button, [
            [Item(_("Save Screenshots To"), submenu=places("shots", ("pictures", _("Pictures")),
                                                           [("clipboard", _("Clipboard"))])),
             # saved to a folder and copied too (Vini); "Clipboard" above is the clipboard alone
             Item(_("Also Copy Screenshots to Clipboard"), lambda on: setv("shots_copy", bool(on)),
                  checked=bool(c.get("shots_copy")) or c.get("shots_to") == "clipboard",
                  enabled=c.get("shots_to") != "clipboard"),
             Item(_("Save Recordings To"), submenu=places("movies", ("videos", _("Videos"))))],
            [Item(_("Timer: None"), lambda _on: setv("timer", 0), checked=c["timer"] == 0),
             Item(_("Timer: 5 Seconds"), lambda _on: setv("timer", 5), checked=c["timer"] == 5),
             Item(_("Timer: 10 Seconds"), lambda _on: setv("timer", 10), checked=c["timer"] == 10)],
            # recordings: sound (macOS: Options > Microphone)
            [Item(_("Record Without Sound"), lambda _on: setv("audio", "none"), checked=c.get("audio") == "none"),
             Item(_("Record System Sound"), lambda _on: setv("audio", "system"),
                  checked=c.get("audio", "system") == "system"),
             Item(_("Record Microphone"), lambda _on: setv("audio", "mic"), checked=c.get("audio") == "mic"),
             # both at once, each its own track (Vini)
             Item(_("Record System Sound and Microphone"), lambda _on: setv("audio", "both"),
                  checked=c.get("audio") == "both")]],
            position=Gtk.PositionType.TOP)
        button.set_active(False)
        return pop

    def _pick_folder(self, key):
        """Another folder for screenshots (key "shots") or recordings ("movies")."""
        self.set_visible(False)                       # the dialog must not sit under the toolbar
        dialog = Gtk.FileDialog(title=_("Save Screenshots To") if key == "shots" else _("Save Recordings To"),
                                accept_label=_("Choose"))
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
                config.update("capture", **{key + "_to": "other", key + "_dir": folder.get_path()})
            self.present()
        dialog.select_folder(None, None, done)

    def _go(self):
        # run() hides the toolbar and waits for it to go (Vini: Print, then
        # Capture, sometimes did nothing): hidden here first, run() saw no
        # toolbar and started at once -- the selection or the picture while
        # the toolbar still held the keyboard and was still on screen
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
