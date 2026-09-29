"""Progress: bars, a spinner, a capacity meter and the Finder "Copy"
window that lists running file operations.

    ui.progress.bar(0.4)                 # determinate (None = indeterminate)
    ui.progress.spinner()
    ui.progress.meter(0.72)              # capacity (disks), red when nearly full
    op = ui.progress.start("Copying “a.txt” to “Documents”", on_cancel=cb)
    op.update(done_bytes, total_bytes)   # fills the bar, "12 MB of 140 MB — About 5 seconds"
    op.finish()

The window appears only if an operation is still running after
SHOW_DELAY_MS (quick copies never flash a window), hides when the last one
ends, and closing it leaves the operations running (like Finder)."""
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import fmt, theme, window  # noqa: E402

SHOW_DELAY_MS = 700
FULL = 0.9            # meter turns red above this

theme.register("""
progressbar.sonata-progress > trough { min-height: 6px; border-radius: 3px; background: %(control_off)s;
  border: none; box-shadow: none; }
progressbar.sonata-progress > trough > progress { min-height: 6px; border-radius: 3px;
  background: %(accent)s; border: none; box-shadow: none; }
progressbar.sonata-progress.meter > trough,
progressbar.sonata-progress.meter > trough > progress { min-height: 4px; border-radius: 2px; }
progressbar.sonata-progress.full > trough > progress { background: %(destructive)s; }
spinner.sonata-spinner { color: %(label_secondary)s; }
window.sonata-progress-window { background: %(window_bg)s; color: %(label)s; font-family: %(font)s;
  font-size: %(text_body)s; }
.sp-head { min-height: 28px; }
.sp-head label { font-weight: 700; }
.sp-row { padding: 10px 16px 12px 16px; }
.sp-row + .sp-row { box-shadow: inset 0 1px %(separator)s; }
.sp-title { color: %(label)s; }
.sp-detail { color: %(label_secondary)s; font-size: %(text_small)s; }
button.sp-stop { min-width: 18px; min-height: 18px; padding: 0; border-radius: 999px; border: none;
  box-shadow: none; background: %(control_off)s; color: %(label_secondary)s; -gtk-icon-size: 10px; }
button.sp-stop:hover { background: %(label_tertiary)s; color: %(label)s; }
""", key="progress")


def bar(fraction=None) -> Gtk.ProgressBar:
    """macOS progress bar (6 px, accent fill). None = indeterminate: call
    .pulse() while waiting."""
    b = Gtk.ProgressBar(css_classes=["sonata-progress"], valign=Gtk.Align.CENTER)
    if fraction is not None:
        b.set_fraction(max(0.0, min(1.0, fraction)))
    return b


def spinner(spinning=True) -> Gtk.Spinner:
    return Gtk.Spinner(spinning=spinning, css_classes=["sonata-spinner"])


def meter(fraction: float) -> Gtk.ProgressBar:
    """Capacity meter (4 px): used/total of a disk; red above 90 %."""
    m = bar(fraction)
    m.add_css_class("meter")
    set_meter(m, fraction)
    return m


def set_meter(m: Gtk.ProgressBar, fraction: float) -> None:
    m.set_fraction(max(0.0, min(1.0, fraction)))
    (m.add_css_class if fraction >= FULL else m.remove_css_class)("full")


# -- operations window ----------------------------------------------------------------------
class Operation(Gtk.Box):
    """One row of the progress window. Update it from the GTK thread."""

    def __init__(self, title: str, on_cancel=None):
        super().__init__(spacing=12, css_classes=["sp-row"])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.title = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE,
                               css_classes=["sp-title"])
        self.bar = bar()
        self.detail = Gtk.Label(label="Preparing…", xalign=0, css_classes=["sp-detail"])
        for w in (self.title, self.bar, self.detail):
            col.append(w)
        self.append(col)
        self.on_cancel = on_cancel
        if on_cancel:
            stop = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Stop", valign=Gtk.Align.CENTER,
                              css_classes=["sp-stop"])
            stop.connect("clicked", lambda *_: self._stop())
            self.append(stop)
        self.started = time.monotonic()
        self.done = False

    def set_title(self, text: str) -> None:
        self.title.set_label(text)

    def update(self, done: int, total: int) -> None:
        """Bytes done of total: fills the bar and estimates the time left."""
        if total <= 0:
            self.bar.pulse()
            return
        f = min(1.0, done / total)
        self.bar.set_fraction(f)
        text = f"{fmt.size(done)} of {fmt.size(total)}"
        elapsed = time.monotonic() - self.started
        if f > 0.02 and elapsed > 1.0:
            text += " — " + fmt.eta(elapsed * (1 - f) / f)
        else:
            text += " — Estimating time remaining…"
        self.detail.set_label(text)

    def set_detail(self, text: str) -> None:
        self.detail.set_label(text)

    def _stop(self):
        if self.on_cancel:
            self.on_cancel()
        self.finish()

    def finish(self) -> None:
        if not self.done:
            self.done = True
            _Window.get().remove_op(self)


class _Window(Adw.Window):
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__(title="Copy", default_width=460, resizable=False, hide_on_close=True)
        self.add_css_class("sonata-progress-window")
        window.standard(self)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.CenterBox(css_classes=["sp-head"])
        head.set_start_widget(window.traffic_lights(self.close, self.minimize, None))
        self.head_label = Gtk.Label(label="Copy")
        head.set_center_widget(self.head_label)
        box.append(Gtk.WindowHandle(child=head))
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.rows)
        self.set_content(box)
        self.ops = []

    def add_op(self, op: Operation) -> None:
        self.ops.append(op)
        self.rows.append(op)
        GLib.timeout_add(SHOW_DELAY_MS, lambda: (not op.done and self.present(), False)[1])

    def remove_op(self, op: Operation) -> None:
        if op in self.ops:
            self.ops.remove(op)
            self.rows.remove(op)
        if not self.ops:
            self.set_visible(False)


def start(title: str, on_cancel=None, heading: str = "Copy") -> Operation:
    """Add an operation to the progress window (shown after a short delay)."""
    op = Operation(title, on_cancel)
    w = _Window.get()
    w.set_title(heading)
    w.head_label.set_label(heading)
    w.add_op(op)
    return op
