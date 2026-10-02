"""Feedbacker (like macOS Feedback Assistant): report a problem with Sonata.

One small window:
- Monitoring: Sonata's detailed logs on / off (the same switch as Settings >
  About > Detailed Logs). While it is off a note says to turn it on, log
  out and back in, and make the problem happen again: errors-only logs
  rarely show what went wrong.
- Describe the Problem: a title and what happened.
- Reports: they are saved as .zip files in ~/Sonata Reports; "Show in
  Files" opens that folder so the report can be copied.
- Save Report / Report on GitHub…: the second also opens a pre-filled
  GitHub issue in the browser and shows the .zip to attach.

Saving or reporting with monitoring off asks first (Turn On Monitoring /
Save Anyway).

After a crash (tools/sonata-session notes it, with the kernel's lines),
the next login opens Feedbacker (autostart.py): it says so at the top,
saves a report at once and fills in the title; describe what you were
doing and use Report on GitHub…"""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import report  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.feedback"
WIDTH, HEIGHT = 560, 650

ui.register("""
window.sonata-feedback { background: %(window_bg)s; }
.fb-text { min-height: 130px; padding: 10px 12px; }      /* libadwaita's .card: the rows' look */
.fb-text textview, .fb-text text { background: transparent; }
.fb-bottom { padding: 10px 16px 14px 16px; }
""", key="feedback")


def reveal(path: str) -> None:
    """Files shows the item selected in its folder (FileManager1), else the folder opens."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)

    def fallback():
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(folder).get_uri(), None)
        except GLib.Error:
            pass

    def done(bus, res):
        try:
            bus.call_finish(res)
        except GLib.Error:
            fallback()
    if os.path.isdir(path):
        fallback()
        return
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        fallback()
        return
    bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1",
             "ShowItems", GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")), None,
             Gio.DBusCallFlags.NONE, 3000, None, done)


class FeedbackWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        if not GLib.get_application_name():
            GLib.set_application_name("Feedbacker")
        super().__init__(application=app, title="Feedbacker", css_classes=["sonata-feedback"])
        ui.window.standard(self)
        self.set_default_size(WIDTH, HEIGHT)
        self.set_size_request(480, 520)
        self.busy = False
        self.toasts = Adw.ToastOverlay()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # no title bar of its own: the compositor draws the glass one (pixdecor)
        page = Adw.PreferencesPage(vexpand=True)

        # the last session crashed (tools/sonata-session left a note): a report at once
        self.crash = report.crash()
        self.crash_row = None
        if self.crash:
            grp = Adw.PreferencesGroup()
            # what took it down, from its logs, and how often lately (Vini: crashes, with data)
            kind = report.note_crash(self.crash)
            self.crash_cause = report.describe(kind) + (f" {report.history_text()}." if report.history_text() else "")
            self.crash_row = Adw.ActionRow(title="Sonata quit unexpectedly", use_markup=False,
                                           subtitle=report.crash_text(self.crash) + " " + self.crash_cause +
                                           " Saving a report with the logs…")
            self.crash_row.set_subtitle_lines(0)
            self.crash_row.add_prefix(Gtk.Image(icon_name="dialog-error-symbolic", css_classes=["error"]))
            grp.add(self.crash_row)
            self._reopen_row(grp)
            page.add(grp)

        mon = Adw.PreferencesGroup()
        self.monitor_row = Adw.SwitchRow(title="Monitoring", use_markup=False, active=report.monitoring(),
                                         subtitle="Keeps detailed logs so problems can be traced")
        self.monitor_row.connect("notify::active", self._monitor_changed)
        mon.add(self.monitor_row)
        self.off_note = Adw.ActionRow(title="Monitoring is off", use_markup=False,
                                      subtitle="Turn it on, log out and back in, then make the problem "
                                               "happen again before saving a report.")
        self.off_note.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic", css_classes=["warning"]))
        self.off_note.set_subtitle_lines(0)
        mon.add(self.off_note)
        page.add(mon)

        desc = Adw.PreferencesGroup(title="Describe the Problem")
        self.title_row = Adw.EntryRow(title="Title", use_markup=False)
        desc.add(self.title_row)
        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                 top_margin=2, bottom_margin=2)
        frame = Gtk.ScrolledWindow(child=self.text, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                   css_classes=["fb-text", "card"], margin_top=12)
        self.text_hint = Gtk.Label(label="What happened, what you were doing, and how to make it happen again.",
                                   xalign=0, wrap=True, css_classes=["dim-label", "caption"], margin_top=6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(frame)
        box.append(self.text_hint)
        desc.add(box)
        page.add(desc)

        where = Adw.PreferencesGroup(title="Reports")
        row = Adw.ActionRow(title="Saved in your home folder", subtitle="~/" + report.FOLDER, use_markup=False)
        show = Gtk.Button(label="Show in Files", valign=Gtk.Align.CENTER, css_classes=["sonata-button"])
        show.connect("clicked", lambda _b: reveal(report.folder()))
        row.add_suffix(show)
        where.add(row)
        page.add(where)
        col.append(page)

        bottom = Gtk.Box(spacing=8, css_classes=["fb-bottom"])
        self.spinner = Gtk.Spinner(visible=False)
        bottom.append(self.spinner)
        bottom.append(Gtk.Box(hexpand=True))
        self.save_btn = ui.controls.push_button("Save Report", lambda: self.submit(False))
        self.github_btn = ui.controls.push_button("Report on GitHub…", lambda: self.submit(True), style="default")
        bottom.append(self.save_btn)
        bottom.append(self.github_btn)
        col.append(bottom)
        self.toasts.set_child(col)
        self.set_child(self.toasts)
        self._update_note()
        if self.crash:
            self.title_row.set_text("Sonata quit unexpectedly")
            self.text.get_buffer().set_text(report.crash_text(self.crash) + "\n\nWhat I was doing just before: ")
            self._auto = True
            self._create(False)                  # no question about monitoring: errors are logged anyway

    # -- monitoring ------------------------------------------------------------------
    def _reopen_row(self, grp) -> None:
        """The apps that were open before the crash, one click away (Vini)."""
        from .. import open_apps
        ids = open_apps.before_crash()
        self.reopen_row = None
        if not ids:
            return
        shown = open_apps.names(ids)
        more = f" and {len(shown) - 4} more" if len(shown) > 4 else ""
        row = Adw.ActionRow(title="Reopen your apps", use_markup=False,
                            subtitle=", ".join(shown[:4]) + more)
        row.set_subtitle_lines(0)

        def reopen():
            n = open_apps.reopen(ids)
            row.set_subtitle(f"Reopened {n} app{'s' if n != 1 else ''}.")
            btn.set_sensitive(False)
        btn = ui.controls.push_button("Reopen", reopen, style="default")
        btn.set_valign(Gtk.Align.CENTER)
        row.add_suffix(btn)
        grp.add(row)
        self.reopen_row, self.reopen_button = row, btn

    def _update_note(self):
        self.off_note.set_visible(not self.monitor_row.get_active())

    def _monitor_changed(self, row, _p):
        report.set_monitoring(row.get_active())
        self._update_note()
        if row.get_active():
            self.toast("Monitoring on: log out and back in, then make the problem happen again")

    def toast(self, text: str, button: str = None, on_button=None) -> None:
        t = Adw.Toast(title=GLib.markup_escape_text(text), timeout=4)
        if button:
            t.set_button_label(button)
            t.connect("button-clicked", lambda *_a: on_button())
        self.toasts.add_toast(t)

    # -- saving ----------------------------------------------------------------------
    def description(self) -> tuple:
        buf = self.text.get_buffer()
        return self.title_row.get_text(), buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def submit(self, github: bool) -> None:
        if self.busy:
            return
        if report.monitoring():
            self._create(github)
            return

        def answered(rid):
            if rid == "on":
                self.monitor_row.set_active(True)
            elif rid == "anyway":
                self._create(github)
        ui.dialog.alert("Monitoring Is Off",
                        "Without monitoring the logs only hold errors, so this report may not show what went "
                        "wrong. Turn monitoring on, log out and back in, make the problem happen again, "
                        "then save the report.",
                        (("cancel", "Cancel", ""), ("anyway", "Save Anyway", ""),
                         ("on", "Turn On Monitoring", "default")), answered, parent=self)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        self.save_btn.set_sensitive(not busy)
        self.github_btn.set_sensitive(not busy)

    def _create(self, github: bool) -> None:
        title, text = self.description()
        self._set_busy(True)

        def work():
            try:
                path, err = report.create(title, text), None
            except OSError as e:
                path, err = None, e
            url = report.issue_url(title, text, os.path.basename(path)) if path and github else None
            GLib.idle_add(self._created, path, err, url)
        threading.Thread(target=work, daemon=True).start()

    def _created(self, path, err, url):
        self._set_busy(False)
        if getattr(self, "_auto", False):            # the crash report, made on opening
            self._auto = False
            if path and not err:
                report.clear_crash()                 # the next login doesn't open Feedbacker again
                self.crash_row.set_subtitle(report.crash_text(self.crash) + " " + self.crash_cause +
                                            f" The logs are saved in “{os.path.basename(path)}”.")
                show = Gtk.Button(label="Show", valign=Gtk.Align.CENTER, css_classes=["sonata-button"])
                show.connect("clicked", lambda _b: reveal(path))
                self.crash_row.add_suffix(show)
                return False
        if err or not path:
            self.toast(f"Couldn't save the report: {getattr(err, 'strerror', None) or err}")
            return False
        if url:
            try:
                Gio.AppInfo.launch_default_for_uri(url, None)
            except GLib.Error:
                pass
            reveal(path)
            self.toast("Attach the report shown in Files to the GitHub issue")
        else:
            self.toast(f"Saved “{os.path.basename(path)}”", "Show", lambda: reveal(path))
        return False


def open_windows(app, paths=()) -> None:
    """One Feedbacker window; opening it again brings it forward."""
    win = next((w for w in app.get_windows() if isinstance(w, FeedbackWindow)), None)
    (win or FeedbackWindow(app)).present()


def feedback_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Feedbacker\n"
                              "Comment=Report a problem with Sonata\nIcon=sonata-feedback\n"
                              "Categories=System;Utility;\nKeywords=bug;report;problem;feedback;logs;crash;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} feedback\n")
