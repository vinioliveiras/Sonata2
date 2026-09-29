"""Terminal (macOS Terminal, "Basic" profile): the user's shell in a window.

VTE draws the terminal (vte4, GTK 4). Colours follow Light/Dark like
macOS's Basic profile: black on white, white on near-black, with the
accent colour for the cursor and a translucent selection. The title bar
reads "folder — process — columns×rows". ⌘ is Ctrl+Shift or Super:
⌘C / ⌘V copy and paste, ⌘N new window, ⌘W close, ⌘+ / ⌘- / ⌘0 text
size, ⌘K clears. Right-click: Copy, Paste, Clear. Closing a window with
something still running asks first. The shell ending closes the window."""
import os
import pwd

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte  # noqa: E402
except (ValueError, ImportError):
    Vte = None

from .. import config, ui  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.terminal"
DEFAULTS = {"font_size": 10, "scrollback": 10000}

ui.register("""
window.sonata-terminal { background: %(term_bg)s; }
.tm-bar { background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.tm-missing { color: %(label_secondary)s; }
""", key="terminal")

# ANSI colours (regular, then bright): macOS Terminal's "Basic" in Light
# Mode; brighter ones in Dark Mode, readable on the dark page
PALETTE_DARK = ("#4d4d4d", "#ff6b6b", "#5fd068", "#e5c07b", "#5c9dff", "#d38aea", "#56c8d8", "#d0d0d0",
                "#7f7f7f", "#ff8787", "#7ee787", "#f2d68a", "#82b4ff", "#e2a8f2", "#7fd9e6", "#ffffff")
PALETTE = ("#000000", "#990000", "#00a600", "#999900", "#0000b2", "#b200b2", "#00a6b2", "#bfbfbf",
           "#666666", "#e50000", "#00d900", "#e5e500", "#0000ff", "#e500e5", "#00e5e5", "#e5e5e5")


def _rgba(css: str) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.parse(css)
    return c


def user_shell() -> str:
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash"
    except KeyError:
        return os.environ.get("SHELL", "/bin/bash")


class TerminalWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cwd: str = None, command=None):
        super().__init__(application=app, title="Terminal", css_classes=["sonata-terminal"])
        ui.window.standard(self)
        self.cfg = config.load("terminal", DEFAULTS)
        self.bar = ui.window.titlebar(self, "Terminal", zoom=True)
        self.bar.add_css_class("tm-bar")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # the title bar is the glass one the compositor draws (pixdecor): no second bar
        self._closing = False
        self.pid = None
        if Vte is None:
            col.append(Gtk.Label(label="Terminal needs VTE for GTK 4 (package vte4 / gir1.2-vte-3.91).",
                                 css_classes=["tm-missing"], vexpand=True, wrap=True))
            self.term = None
            self.set_child(col)
            self.set_default_size(640, 400)
            return
        self.term = Vte.Terminal(vexpand=True, hexpand=True)
        self.term.set_scrollback_lines(self.cfg["scrollback"])
        self.term.set_mouse_autohide(True)
        self.term.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        self.term.set_bold_is_bright(False)
        box = Gtk.Box(margin_start=6, margin_end=2, margin_top=4, vexpand=True)   # macOS keeps a small inset
        box.append(self.term)
        col.append(box)
        self.set_child(col)
        self._apply_look()
        ui.theme.on_change(self._apply_look)
        self.set_default_size(*self._size_for(80, 24))
        self.term.connect("window-title-changed", lambda *_: self._update_title())
        self.term.connect("current-directory-uri-changed", lambda *_: self._update_title())
        self.term.connect("child-exited", lambda *_: self._really_close())
        self.term.connect("notify::column-count", lambda *_: self._update_title())
        self._input()
        self.connect("close-request", self._close_request)
        self._spawn(cwd or GLib.get_home_dir(), command)
        # the title follows `cd` and the program in front even without OSC 7
        GLib.timeout_add_seconds(1, lambda: (self._update_title(), not self._closing)[1])

    # -- process ----------------------------------------------------------------------------
    def _spawn(self, cwd, command) -> None:
        argv = list(command) if command else [user_shell(), "-l"]
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")]
        env += ["TERM_PROGRAM=Sonata_Terminal", "COLORTERM=truecolor"]

        def spawned(_t, pid, error, *_a):
            if error:
                self.term.feed(f"\r\nCouldn't start {argv[0]}: {error.message}\r\n".encode())
            else:
                self.pid = pid
        self.term.spawn_async(Vte.PtyFlags.DEFAULT, cwd, argv, env, GLib.SpawnFlags.SEARCH_PATH,
                              None, None, -1, None, spawned)

    def _busy(self) -> str:
        """The program running in the foreground ("" when the shell waits)."""
        pty = self.term.get_pty() if self.term else None
        if not pty or not self.pid:
            return ""
        try:
            fg = os.tcgetpgrp(pty.get_fd())
        except OSError:
            return ""
        if fg in (self.pid, -1):
            return ""
        try:
            with open(f"/proc/{fg}/comm", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "a process"

    # -- look ---------------------------------------------------------------------------------
    def _apply_look(self, *_a) -> None:
        if not self.term:
            return
        v = ui.theme.values()
        fg, bg = _rgba(v["term_fg"]), _rgba(v["term_bg"])
        self.term.set_colors(fg, bg, [_rgba(c) for c in (PALETTE_DARK if ui.is_dark() else PALETTE)])
        self.term.set_color_cursor(_rgba(v["accent"]))
        self.term.set_color_cursor_foreground(bg)
        sel = _rgba(v["accent"])
        sel.alpha = 0.35
        self.term.set_color_highlight(sel)
        self.term.set_color_highlight_foreground(fg)
        # the whole list (SF Mono, JetBrains Mono, ... monospace): Pango takes the first installed
        families = ",".join(f.strip().strip('"') for f in v["font_mono"].split(","))
        font = Pango.FontDescription.from_string(f"{families} {self.cfg['font_size']}")
        self.term.set_font(font)

    def _size_for(self, cols, rows):
        cw, ch = self.term.get_char_width() or 8, self.term.get_char_height() or 17
        return int(cols * cw + 12), int(rows * ch + 8)

    def _zoom(self, step: int) -> None:
        size = DEFAULTS["font_size"] if step == 0 else max(7, min(36, self.cfg["font_size"] + step))
        self.cfg["font_size"] = size
        config.update("terminal", font_size=size)
        self._apply_look()

    def _update_title(self) -> None:
        if not self.term:
            return
        path = self._cwd() or ""
        folder = ("~" if path == GLib.get_home_dir() else os.path.basename(path) or "/") if path else ""
        what = self._busy() or os.path.basename(user_shell())
        size = f"{self.term.get_column_count()}×{self.term.get_row_count()}"
        title = " — ".join(p for p in (folder or GLib.get_user_name(), what, size) if p)
        self.set_title(title)
        self.bar.title_label.set_label(title)

    # -- input ----------------------------------------------------------------------------------
    def _input(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._context_menu)
        self.term.add_controller(click)

    def _key(self, _c, keyval, _code, state) -> bool:
        super_ = state & Gdk.ModifierType.SUPER_MASK
        ctrl_shift = (state & Gdk.ModifierType.CONTROL_MASK) and (state & Gdk.ModifierType.SHIFT_MASK)
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        k = Gdk.keyval_to_lower(keyval)
        if not (super_ or ctrl_shift):
            if ctrl and k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_minus, Gdk.KEY_0):
                self._zoom({Gdk.KEY_minus: -1, Gdk.KEY_0: 0}.get(k, 1))
                return True
            return False
        act = {Gdk.KEY_c: self.copy, Gdk.KEY_v: self.paste,
               Gdk.KEY_n: lambda: TerminalWindow(self.get_application(), self._cwd()).present(),
               Gdk.KEY_t: lambda: TerminalWindow(self.get_application(), self._cwd()).present(),
               Gdk.KEY_w: self.close, Gdk.KEY_k: self.clear,
               Gdk.KEY_plus: lambda: self._zoom(1), Gdk.KEY_equal: lambda: self._zoom(1),
               Gdk.KEY_minus: lambda: self._zoom(-1), Gdk.KEY_0: lambda: self._zoom(0)}.get(k)
        if act is None:
            return False
        act()
        return True

    def _cwd(self):
        """The shell's folder: what it reports (OSC 7), else /proc."""
        uri = self.term.get_current_directory_uri() if self.term else None
        if uri:
            return Gio.File.new_for_uri(uri).get_path()
        try:
            return os.readlink(f"/proc/{self.pid}/cwd") if self.pid else None
        except OSError:
            return None

    def copy(self) -> None:
        if self.term.get_has_selection():
            self.term.copy_clipboard_format(Vte.Format.TEXT)

    def paste(self) -> None:
        self.term.paste_clipboard()

    def clear(self) -> None:
        self.term.reset(True, True)
        self.term.feed_child(b"\x0c")                 # the shell redraws its prompt

    def _context_menu(self, gesture, _n, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        sections = [[ui.menu.Item("Copy", self.copy, enabled=self.term.get_has_selection()),
                     ui.menu.Item("Paste", self.paste)], [ui.menu.Item("Clear", self.clear)]]
        ui.menu.popup(self.term, sections, at=(x, y), glass=True, passthrough=True)

    # -- closing ---------------------------------------------------------------------------------
    def _close_request(self, _w) -> bool:
        busy = self._busy() if not self._closing else ""
        if not busy:
            return False
        ui.dialog.alert("Do you want to terminate running processes in this window?",
                        f"Closing this window will terminate “{busy}”.",
                        [("cancel", "Cancel", ""), ("close", "Terminate", "destructive")],
                        lambda rid: rid == "close" and self._really_close(), parent=self)
        return True

    def _really_close(self) -> None:
        self._closing = True
        self.close()


def open_windows(app, paths) -> None:
    """A window per folder asked for (or one in the home folder); opening
    the app again with no folder brings its window forward."""
    wins = [w for w in app.get_windows() if isinstance(w, TerminalWindow)]
    if not paths and wins:
        wins[0].present()
        return
    for p in paths or [None]:
        cwd = None
        if p:
            f = Gio.File.new_for_commandline_arg(p)
            cwd = f.get_path()
            if cwd and not os.path.isdir(cwd):
                cwd = os.path.dirname(cwd)
        TerminalWindow(app, cwd).present()


def terminal_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Terminal\n"
                              "Comment=Use the command line\nIcon=utilities-terminal\n"
                              "Categories=System;TerminalEmulator;\nStartupNotify=true\n"
                              f"Exec={command} terminal %F\n")
