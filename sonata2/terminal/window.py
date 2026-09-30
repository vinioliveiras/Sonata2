"""Terminal (macOS Terminal, "Basic" profile): the user's shell in tabbed windows.

VTE draws the terminal (vte4, GTK 4). Colours follow Light/Dark like
macOS's Basic profile: black on white, white on near-black, with the
accent colour for the cursor and a translucent selection. The title bar
reads "folder — process — columns×rows" for the tab in front.

Tabs work like macOS Terminal: each tab is its own terminal and shell
(TerminalTab); the tab strip (equal-width tabs, "+" at the end, an x on
hover, middle-click closes, drag to reorder) shows under the title bar
once a window has two or more tabs. Opening a folder ("Open in Terminal")
adds a tab to the frontmost window.

⌘ is Ctrl+Shift or Super: ⌘C / ⌘V copy and paste, ⌘T new tab (in the
current tab's folder), ⌘N new window, ⌘W close tab, ⌘⇧[ / ⌘⇧] or
Ctrl+Tab / Ctrl+Shift+Tab (and Ctrl+PageUp/PageDown) switch tabs,
⌘1…⌘9 (also Ctrl+1…9) jump to a tab, ⌘+ / ⌘- / ⌘0 text size, ⌘K
clears. Right-click: Copy, Paste, Clear, New Tab, Move Tab to New
Window. Closing a tab or window with something still running asks first.
A shell ending closes its tab (the last tab closes the window).

The terminal widget comes from a factory (`new_terminal`, or the
`factory` argument) so the tab logic can run with a stand-in widget; see
TerminalTab for the small interface it needs."""
import os
import pwd
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

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
/* tab strip: macOS Terminal's, equal-width tabs under the title bar */
.tm-tabs { background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s, inset 0 1px %(separator)s;
           min-height: 26px; }
.tm-tab { background: %(tool_hover)s; box-shadow: inset -1px 0 %(separator)s;
          transition: background %(t_fast)s %(ease_out)s; }
.tm-tab:hover { background: %(separator)s; }
.tm-tab.active, .tm-tab.active:hover { background: %(control_bg)s; box-shadow: inset -1px 0 %(separator)s; }
.tm-tab-label { font-size: %(text_small)s; color: %(label_secondary)s; margin: 0 22px; }
.tm-tab.active .tm-tab-label { color: %(label)s; }
.tm-tab-close, .tm-tab-add { min-width: 16px; min-height: 16px; padding: 0; margin: 0 5px;
                             border-radius: 4px; background: none; box-shadow: none; border: none;
                             color: %(label_secondary)s; -gtk-icon-size: 10px; }
.tm-tab-close { opacity: 0; transition: opacity %(t_fast)s %(ease_out)s; }
.tm-tab:hover .tm-tab-close { opacity: 1; }
.tm-tab-close:hover, .tm-tab-add:hover { background: %(tool_hover)s; color: %(label)s; }
.tm-tab-add { margin: 0 6px; -gtk-icon-size: 12px; }
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


if Vte is not None:
    class SonataVte(Vte.Terminal):
        """Vte.Terminal plus the few calls TerminalTab makes (start,
        apply_look, copy_text, clear)."""

        def __init__(self):
            super().__init__(vexpand=True, hexpand=True)
            self.set_scrollback_lines(config.load("terminal", DEFAULTS)["scrollback"])
            self.set_mouse_autohide(True)
            self.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
            self.set_bold_is_bright(False)

        def start(self, cwd, argv, env, done) -> None:
            def spawned(_t, pid, error, *_a):
                done(None if error else pid, error.message if error else None)
            self.spawn_async(Vte.PtyFlags.DEFAULT, cwd, argv, env, GLib.SpawnFlags.SEARCH_PATH,
                             None, None, -1, None, spawned)

        def apply_look(self, font_size) -> None:
            v = ui.theme.values()
            fg, bg = _rgba(v["term_fg"]), _rgba(v["term_bg"])
            self.set_colors(fg, bg, [_rgba(c) for c in (PALETTE_DARK if ui.is_dark() else PALETTE)])
            self.set_color_cursor(_rgba(v["accent"]))
            self.set_color_cursor_foreground(bg)
            sel = _rgba(v["accent"])
            sel.alpha = 0.35
            self.set_color_highlight(sel)
            self.set_color_highlight_foreground(fg)
            # the whole list (SF Mono, JetBrains Mono, ... monospace): Pango takes the first installed
            families = ",".join(f.strip().strip('"') for f in v["font_mono"].split(","))
            self.set_font(Pango.FontDescription.from_string(f"{families} {font_size}"))

        def copy_text(self) -> None:
            if self.get_has_selection():
                self.copy_clipboard_format(Vte.Format.TEXT)

        def clear(self) -> None:
            self.reset(True, True)
            self.feed_child(b"\x0c")                 # the shell redraws its prompt

    new_terminal = SonataVte
else:
    new_terminal = None      # no VTE: windows show the "needs VTE" message

# every live tab, for appearance changes (ui.theme has no disconnect)
_TABS = weakref.WeakSet()
ui.theme.on_change(lambda *_a: [t.apply_look() for t in list(_TABS)])


class TerminalTab:
    """One tab: a terminal widget and its child process.

    The widget (from the factory) is a Gtk.Widget with: start(cwd, argv,
    env, done(pid, error)), apply_look(font_size), copy_text(),
    paste_clipboard(), clear(), feed(bytes), get_has_selection(),
    get_current_directory_uri(), get_column_count(), get_row_count(),
    get_char_width(), get_char_height(), get_pty() and the signals
    window-title-changed, current-directory-uri-changed, child-exited
    (and, optionally, the column-count property)."""

    def __init__(self, win, factory, cwd=None, command=None):
        self.win = win
        self.factory = factory
        self.pid = None
        self.closing = False
        self.title = ""                     # "folder — process"
        self.term = factory()
        # macOS keeps a small inset around the text
        self.box = Gtk.Box(margin_start=6, margin_end=2, margin_top=4, vexpand=True, hexpand=True)
        self.box.append(self.term)
        self.button = None                  # its tab in the strip (TerminalWindow makes it)
        _TABS.add(self)
        self.apply_look()
        self.term.connect("window-title-changed", lambda *_: self.update_title())
        self.term.connect("current-directory-uri-changed", lambda *_: self.update_title())
        self.term.connect("child-exited", lambda *_: self.win._child_exited(self))
        if hasattr(self.term.props, "column_count"):
            self.term.connect("notify::column-count", lambda *_: self.update_title())
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", self._context_menu)
        self.term.add_controller(click)
        self._spawn(cwd or GLib.get_home_dir(), command)

    # -- process ----------------------------------------------------------------------------
    def _spawn(self, cwd, command) -> None:
        argv = list(command) if command else [user_shell(), "-l"]
        env = [f"{k}={v}" for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")]
        env += ["TERM_PROGRAM=Sonata_Terminal", "COLORTERM=truecolor"]

        def done(pid, error):
            if error:
                self.term.feed(f"\r\nCouldn't start {argv[0]}: {error}\r\n".encode())
            else:
                self.pid = pid
                self.update_title()
        self.term.start(cwd, argv, env, done)

    def busy(self) -> str:
        """The program running in the foreground ("" when the shell waits)."""
        pty = self.term.get_pty()
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

    def cwd(self):
        """The shell's folder: what it reports (OSC 7), else /proc."""
        uri = self.term.get_current_directory_uri()
        if uri:
            return Gio.File.new_for_uri(uri).get_path()
        try:
            return os.readlink(f"/proc/{self.pid}/cwd") if self.pid else None
        except OSError:
            return None

    # -- look -------------------------------------------------------------------------------
    def apply_look(self) -> None:
        self.term.apply_look(self.win.cfg["font_size"])

    def size_for(self, cols, rows):
        cw, ch = self.term.get_char_width() or 8, self.term.get_char_height() or 17
        return int(cols * cw + 12), int(rows * ch + 8)

    def update_title(self) -> None:
        path = self.cwd() or ""
        folder = ("~" if path == GLib.get_home_dir() else os.path.basename(path) or "/") if path else ""
        what = self.busy() or os.path.basename(user_shell())
        title = " — ".join(p for p in (folder or GLib.get_user_name(), what) if p)
        if title != self.title:
            self.title = title
            if self.button:
                self.button.label.set_label(title)
                self.button.set_tooltip_text(title)
        self.win._tab_title_changed(self)

    def window_title(self) -> str:
        size = f"{self.term.get_column_count()}×{self.term.get_row_count()}"
        return " — ".join(p for p in (self.title, size) if p)

    # -- edit -------------------------------------------------------------------------------
    def copy(self) -> None:
        self.term.copy_text()

    def paste(self) -> None:
        self.term.paste_clipboard()

    def clear(self) -> None:
        self.term.clear()

    def _context_menu(self, gesture, _n, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        w = self.win
        sections = [[ui.menu.Item("Copy", self.copy, enabled=self.term.get_has_selection()),
                     ui.menu.Item("Paste", self.paste)],
                    [ui.menu.Item("Clear", self.clear)],
                    [ui.menu.Item("New Tab", lambda: w.new_tab(self.cwd())),
                     ui.menu.Item("Move Tab to New Window", lambda: w.move_to_new_window(self),
                                  enabled=len(w.tabs) > 1)]]
        ui.menu.popup(self.term, sections, at=(x, y), glass=True, passthrough=True)


class _TabButton(Gtk.Box):
    """A tab in the strip: close x on the left (on hover), centred title."""

    def __init__(self, win, tab):
        super().__init__(css_classes=["tm-tab"], hexpand=True)
        self.tab = tab
        over = Gtk.Overlay(hexpand=True)
        self.label = Gtk.Label(label=tab.title or "Terminal", css_classes=["tm-tab-label"],
                               ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True, width_chars=1)
        over.set_child(self.label)
        close = Gtk.Button(icon_name="window-close-symbolic", css_classes=["tm-tab-close"],
                           tooltip_text="Close Tab", halign=Gtk.Align.START, valign=Gtk.Align.CENTER,
                           focusable=False)
        close.connect("clicked", lambda *_: win.close_tab(tab))
        over.add_overlay(close)
        self.append(over)
        press = Gtk.GestureClick(button=0)
        press.connect("pressed", self._pressed)
        self.add_controller(press)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._dragged)
        self.add_controller(drag)
        self.win = win
        self._start_x = None                 # the press, in the tab row's coordinates

    def _pressed(self, gesture, _n, _x, _y) -> None:
        if gesture.get_current_button() == Gdk.BUTTON_MIDDLE:
            self.win.close_tab(self.tab)
        elif gesture.get_current_button() == Gdk.BUTTON_PRIMARY:
            self.win.select_tab(self.tab)

    def _drag_begin(self, _g, x, y) -> None:
        pt = Graphene.Point()
        pt.x, pt.y = x, y
        ok, p = self.compute_point(self.win.tab_row, pt)
        self._start_x = p.x if ok else None

    def _dragged(self, _g, dx, _dy) -> None:
        """Live reorder: the tab moves to the slot under the pointer."""
        row = self.win.tab_row
        width = row.get_width() / max(1, len(self.win.tabs))
        if self._start_x is None or abs(dx) < 4 or width <= 0:
            return
        self.win.move_tab(self.tab, int(max(0.0, self._start_x + dx) // width))


class TerminalWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cwd: str = None, command=None, factory=None, tab: TerminalTab = None):
        super().__init__(application=app, title="Terminal", css_classes=["sonata-terminal"])
        ui.window.standard(self)
        self.cfg = config.load("terminal", DEFAULTS)
        # the title bar is the glass one the compositor draws (pixdecor): no
        # second bar; this one only carries the title label
        self.bar = ui.window.titlebar(self, "Terminal", zoom=True)
        self.bar.add_css_class("tm-bar")
        self.factory = factory or (tab.factory if tab else new_terminal)
        self.tabs = []
        self.current = None
        self._closing = False
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(col)
        if self.factory is None:
            col.append(Gtk.Label(label="Terminal needs VTE for GTK 4 (package vte4 / gir1.2-vte-3.91).",
                                 css_classes=["tm-missing"], vexpand=True, wrap=True))
            self.set_default_size(640, 400)
            return
        self.strip = Gtk.Box(css_classes=["tm-tabs"], visible=False)
        self.tab_row = Gtk.Box(homogeneous=True, hexpand=True)
        self.strip.append(self.tab_row)
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["tm-tab-add"], tooltip_text="New Tab",
                         valign=Gtk.Align.CENTER, focusable=False)
        add.connect("clicked", lambda *_: self.new_tab(self.current and self.current.cwd()))
        self.strip.append(add)
        col.append(self.strip)
        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        col.append(self.stack)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)
        first = self.adopt(tab) if tab else self.new_tab(cwd, command)
        self.set_default_size(*first.size_for(80, 24))
        # the titles follow `cd` and the program in front even without OSC 7
        GLib.timeout_add_seconds(1, self._tick)

    # compatibility: the front tab's terminal and process
    @property
    def term(self):
        return self.current.term if self.current else None

    @property
    def pid(self):
        return self.current.pid if self.current else None

    def _busy(self) -> str:
        return self.current.busy() if self.current else ""

    def _cwd(self):
        return self.current.cwd() if self.current else None

    def _tick(self) -> bool:
        if self._closing:
            return False
        if self.get_mapped():
            for t in self.tabs:
                t.update_title()
        return True

    # -- tabs --------------------------------------------------------------------------------
    def new_tab(self, cwd=None, command=None) -> TerminalTab:
        """A tab after the current one, in front."""
        tab = TerminalTab(self, self.factory, cwd, command)
        self._insert(tab)
        tab.update_title()
        return tab

    def adopt(self, tab: TerminalTab) -> TerminalTab:
        """Take a tab from another window (Move Tab to New Window)."""
        tab.win = self
        tab.apply_look()
        self._insert(tab)
        tab.update_title()
        return tab

    def _insert(self, tab) -> None:
        index = self.tabs.index(self.current) + 1 if self.current in self.tabs else len(self.tabs)
        self.tabs.insert(index, tab)
        tab.button = _TabButton(self, tab)
        self.stack.add_child(tab.box)
        self._rebuild_strip()
        self.select_tab(tab)

    def _rebuild_strip(self) -> None:
        child = self.tab_row.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.tab_row.remove(child)
            child = nxt
        for t in self.tabs:
            self.tab_row.append(t.button)
        self.strip.set_visible(len(self.tabs) > 1)

    def select_tab(self, tab) -> None:
        if tab not in self.tabs:
            return
        if self.current and self.current.button:
            self.current.button.remove_css_class("active")
        self.current = tab
        tab.button.add_css_class("active")
        self.stack.set_visible_child(tab.box)
        tab.term.grab_focus()
        self._tab_title_changed(tab)

    def select_index(self, index: int) -> None:
        if self.tabs:
            self.select_tab(self.tabs[index % len(self.tabs)])

    def cycle(self, step: int) -> None:
        if self.current in self.tabs:
            self.select_index(self.tabs.index(self.current) + step)

    def move_tab(self, tab, index: int) -> None:
        index = max(0, min(index, len(self.tabs) - 1))
        if tab not in self.tabs or self.tabs.index(tab) == index:
            return
        self.tabs.remove(tab)
        self.tabs.insert(index, tab)
        prev = self.tabs[index - 1].button if index else None
        self.tab_row.reorder_child_after(tab.button, prev)

    def close_tab(self, tab) -> None:
        """Close a tab, asking first when something runs in it."""
        busy = tab.busy()
        if not busy:
            self._remove(tab)
            return
        ui.dialog.alert("Do you want to terminate running processes in this tab?",
                        f"Closing this tab will terminate “{busy}”.",
                        [("cancel", "Cancel", ""), ("close", "Terminate", "destructive")],
                        lambda rid: rid == "close" and self._remove(tab), parent=self)

    def _remove(self, tab, keep=False) -> None:
        """Take a tab out; keep=True leaves its process running (moving it)."""
        if tab not in self.tabs:
            return
        if len(self.tabs) == 1 and not keep:
            self._really_close()
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)
        tab.button = None
        self.stack.remove(tab.box)
        if not keep:
            tab.closing = True
            _TABS.discard(tab)
        if self.current is tab:
            self.current = None
            if self.tabs:
                # macOS selects the tab to the right (the left one for the last)
                self.select_tab(self.tabs[min(i, len(self.tabs) - 1)])
        self._rebuild_strip()
        if not self.tabs:
            self._really_close()

    def move_to_new_window(self, tab) -> "TerminalWindow":
        if tab not in self.tabs or len(self.tabs) < 2:
            return None
        self._remove(tab, keep=True)
        win = TerminalWindow(self.get_application(), factory=self.factory, tab=tab)
        win.present()
        return win

    def _child_exited(self, tab) -> None:
        if not self._closing and not tab.closing:
            self._remove(tab)

    def _tab_title_changed(self, tab) -> None:
        if tab is self.current:
            title = tab.window_title()
            self.set_title(title)
            self.bar.title_label.set_label(title)

    # -- look ---------------------------------------------------------------------------------
    def _zoom(self, step: int) -> None:
        size = DEFAULTS["font_size"] if step == 0 else max(7, min(36, self.cfg["font_size"] + step))
        self.cfg["font_size"] = size
        config.update("terminal", font_size=size)
        for t in self.tabs:
            t.apply_look()

    # -- input ----------------------------------------------------------------------------------
    def _key(self, _c, keyval, code, state) -> bool:
        super_ = state & Gdk.ModifierType.SUPER_MASK
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        ctrl_shift = ctrl and shift
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and not super_:                      # Ctrl+Tab and friends
            if k == Gdk.KEY_Tab and not shift:
                self.cycle(1)
                return True
            if k == Gdk.KEY_ISO_Left_Tab or (k == Gdk.KEY_Tab and shift):
                self.cycle(-1)
                return True
            if k in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up) and not shift:
                self.cycle(1 if k == Gdk.KEY_Page_Down else -1)
                return True
        # ⌘1…⌘9 (Super, Ctrl, or Ctrl+Shift by the key's position: 10…18)
        digit = k - Gdk.KEY_1 if Gdk.KEY_1 <= k <= Gdk.KEY_9 else (code - 10 if ctrl_shift and
                                                                  10 <= code <= 18 else -1)
        if digit >= 0 and (super_ or ctrl):
            self.select_index(digit if digit < 8 else -1)      # ⌘9 is the last tab
            return True
        if not (super_ or ctrl_shift):
            if ctrl and k in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_minus, Gdk.KEY_0):
                self._zoom({Gdk.KEY_minus: -1, Gdk.KEY_0: 0}.get(k, 1))
                return True
            return False
        tab = self.current
        act = {Gdk.KEY_c: tab.copy, Gdk.KEY_v: tab.paste,
               Gdk.KEY_n: lambda: TerminalWindow(self.get_application(), tab.cwd(),
                                                 factory=self.factory).present(),
               Gdk.KEY_t: lambda: self.new_tab(tab.cwd()),
               Gdk.KEY_w: lambda: self.close_tab(tab), Gdk.KEY_k: tab.clear,
               Gdk.KEY_braceleft: lambda: self.cycle(-1), Gdk.KEY_bracketleft: lambda: self.cycle(-1),
               Gdk.KEY_braceright: lambda: self.cycle(1), Gdk.KEY_bracketright: lambda: self.cycle(1),
               Gdk.KEY_plus: lambda: self._zoom(1), Gdk.KEY_equal: lambda: self._zoom(1),
               Gdk.KEY_minus: lambda: self._zoom(-1), Gdk.KEY_0: lambda: self._zoom(0)}.get(k)
        if act is None:
            return False
        act()
        return True

    # -- closing ---------------------------------------------------------------------------------
    def _close_request(self, _w) -> bool:
        busy = [b for b in (t.busy() for t in self.tabs) if b] if not self._closing else []
        if not busy:
            self._closing = True
            return False
        names = "”, “".join(dict.fromkeys(busy))
        ui.dialog.alert("Do you want to terminate running processes in this window?",
                        f"Closing this window will terminate “{names}”.",
                        [("cancel", "Cancel", ""), ("close", "Terminate", "destructive")],
                        lambda rid: rid == "close" and self._really_close(), parent=self)
        return True

    def _really_close(self) -> None:
        self._closing = True
        for t in self.tabs:
            t.closing = True
            _TABS.discard(t)
        self.close()


def _folder(p):
    f = Gio.File.new_for_commandline_arg(p)
    cwd = f.get_path()
    if cwd and not os.path.isdir(cwd):
        cwd = os.path.dirname(cwd)
    return cwd


def open_windows(app, paths) -> None:
    """Folders open as new tabs in the frontmost window (a new window when
    none is open); opening the app again with no folder brings its window
    forward."""
    wins = [w for w in app.get_windows() if isinstance(w, TerminalWindow) and not w._closing]
    if not paths:
        if wins:
            wins[0].present()
        else:
            TerminalWindow(app).present()
        return
    # get_windows() lists the most recently focused first
    front = next((w for w in wins if w.tabs), None)
    for p in paths:
        if front:
            front.new_tab(_folder(p))
        else:
            front = TerminalWindow(app, _folder(p))
            if not front.tabs:                                # no VTE: one message window is enough
                front.present()
                return
    front.present()


def terminal_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Terminal\n"
                              "Comment=Use the command line\nIcon=utilities-terminal\n"
                              "Categories=System;TerminalEmulator;\nStartupNotify=true\n"
                              f"Exec={command} terminal %F\n"
                              "Actions=new-window;\n\n[Desktop Action new-window]\nName=New Window\n"
                              f"Exec={command} terminal --new-window\n")
