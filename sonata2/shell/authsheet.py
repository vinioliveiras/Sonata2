"""The password sheet: every prompt for the login password looks and works
the same (Vini: like the one installing updates asks). macOS' "<App> wants
to make changes": a glass panel in the middle of the display over a light
dim, a lock, a title and a message, the account (a pop-up when several may
answer), the password field and Cancel / OK; a wrong password shakes the
field and asks again.

It's a window of its own that takes the keyboard -- above everything with
the layer shell, else a small window over the app. Asking from inside the
Dock's folder panel never got the keys: the Dock takes no keyboard, so
the field could be clicked but not typed in (Vini).

PasswordSheet is the look (polkit's agent checks the password its own
way); ask() checks the login password with PAM."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from ..i18n import _  # noqa: E402
from . import layer  # noqa: E402
from .loginui import avatar, password_field, shake  # noqa: E402

ui.register("""
window.sonata-auth { background: rgba(0, 0, 0, 0.18); }
window.sonata-auth.in-app { background: none; }
.auth-panel { background: %(panel_material)s; border-radius: calc(%(r_dialog)s * 1.17); padding: 22px 22px 18px 22px;
  box-shadow: 0 22px 56px rgba(0,0,0,0.35), 0 0 0 0.5px rgba(0,0,0,0.35), inset 0 0 0 0.5px %(highlight)s; }
.auth-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
.auth-text { font-size: %(text_small)s; color: %(label_secondary)s; }
.auth-error { font-size: %(text_small)s; color: %(destructive)s; }
.auth-panel entry { min-height: 28px; }
.auth-panel entry.shake { animation: lk-shake 420ms ease-in-out; }
.auth-panel button { min-width: 76px; }
@keyframes auth-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
.auth-panel { animation: auth-in 220ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="auth-sheet")

ESCAPE = 0xff1b


class PasswordSheet:
    """One prompt. on_submit(password) when OK / Return is pressed with a
    password (the field waits: wrong() or close() next); on_cancel() on
    Cancel / Esc. names: the accounts that may answer (default: this one),
    index the one picked first, on_pick(i) when another is chosen."""

    def __init__(self, title, text, on_submit, on_cancel=None, *, detail=None, ok=None, app=None,
                 parent=None, names=None, real_names=None, index=0, on_pick=None, hint=""):
        self.on_submit, self.on_cancel, self.on_pick = on_submit, on_cancel, on_pick
        self.finished = False
        self.names = names or [GLib.get_user_name()]
        self.real_names = real_names or [GLib.get_real_name() or n for n in self.names]
        self.index = index
        self.win = Gtk.Window(decorated=False, title=title, css_classes=["sonata-auth"])
        if app is not None:
            self.win.set_application(app)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["auth-panel"],
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=340)
        top = Gtk.Box(spacing=14)
        top.append(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=40, valign=Gtk.Align.START))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.append(Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=40, css_classes=["auth-title"]))
        for line in (text, detail):
            if line:
                texts.append(Gtk.Label(label=line, xalign=0, wrap=True, max_width_chars=40,
                                       css_classes=["auth-text"]))
        top.append(texts)
        panel.append(top)
        who = Gtk.Box(spacing=10, margin_top=6)
        self.who = who
        self.face = avatar(28, self.names[index], self.real_names[index])
        who.append(self.face)
        if len(self.names) > 1:
            self.account = ui.controls.popup_button(self.real_names, index, self._pick)
            self.account.set_hexpand(True)
            who.append(self.account)
        else:
            who.append(Gtk.Label(label=self.real_names[index], xalign=0, hexpand=True))
        panel.append(who)
        self.entry = password_field(_("Password"))
        self.entry.remove_css_class("lk-field")          # the login screen's look is for dark backdrops
        self.entry.set_halign(Gtk.Align.FILL)
        self.entry.connect("activate", lambda *_: self._ok())
        panel.append(self.entry)
        self.error = Gtk.Label(label=hint, xalign=0, css_classes=["auth-error"], visible=bool(hint), wrap=True)
        panel.append(self.error)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        cancel = ui.controls.push_button(_("Cancel"), self.cancel)
        self.ok = ui.controls.push_button(ok or _("OK"), self._ok, "default")
        buttons.append(cancel)
        buttons.append(self.ok)
        panel.append(buttons)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, *_: kv == ESCAPE and (self.cancel(), True)[1])
        self.win.add_controller(keys)
        if not layer.overlay_fullscreen(self.win, "sonata2-auth"):    # above everything, takes the keyboard
            self.win.add_css_class("in-app")             # no layer shell (an app): a small window over it
            self.win.set_modal(True)
            if parent is not None:
                self.win.set_transient_for(parent)
        self.win.present()
        self.entry.grab_focus()

    def _pick(self, i):
        self.index = i
        # the picture follows the chosen account
        face = avatar(28, self.names[i], self.real_names[i])
        self.who.insert_child_after(face, self.face)
        self.who.remove(self.face)
        self.face = face
        if self.on_pick:
            self.on_pick(i)

    def _ok(self):
        pw = self.entry.get_text()
        if not pw or self.finished or not self.entry.get_sensitive():
            return
        self.ok.set_sensitive(False)
        self.entry.set_sensitive(False)
        self.error.set_visible(False)
        self.on_submit(pw)

    def say(self, text) -> None:
        self.error.set_label(text)
        self.error.set_visible(True)

    def wrong(self, text=None) -> None:
        """Ask again: the field empties and shakes, the reason under it."""
        if self.finished:
            return
        self.ok.set_sensitive(True)
        if text or not self.error.get_visible():
            self.say(text or _("Wrong password"))
        shake(self.entry)

    def close(self) -> None:
        if not self.finished:
            self.finished = True
            self.win.destroy()

    def cancel(self) -> None:
        if self.finished:
            return
        self.close()
        if self.on_cancel:
            self.on_cancel()


def ask(title, text, on_ok, *, ok=None, parent=None, app=None, with_password=False, on_cancel=None):
    """The login password, checked (PAM) before on_ok() runs -- on_ok(password)
    with with_password (it's needed past the check: a locked folder's key).
    A wrong one asks again in the same sheet."""
    from .. import pam
    sheet = None

    def checked(right, pw):
        if sheet.finished:
            return
        if right:
            sheet.close()
            on_ok(pw) if with_password else on_ok()
        else:
            sheet.wrong(_("Wrong password"))

    sheet = PasswordSheet(title, text, lambda pw: pam.check_async(pw, lambda r: checked(r, pw)), on_cancel,
                          ok=ok, parent=parent, app=app,
                          hint="" if pam.available() else _("Passwords can't be checked (PAM missing)"))
    return sheet
