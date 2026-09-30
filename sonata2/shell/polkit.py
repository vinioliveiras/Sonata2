"""Sonata's authentication agent (polkit): the password prompt for system
actions -- installing updates, changing the time zone, other users...

macOS's "<App> wants to make changes" sheet: a glass panel in the middle
of the display, a lock, the message the action gives, the account (a
pop-up when several accounts may authorize it) and the password field;
Cancel / OK. A wrong password shakes the field and asks again.

`sonata2 keep polkit` registers it for the session (Wayfire autostart).
Needs polkit's introspection data (Arch: polkit; Debian: gir1.2-polkit-1.0);
without it the session falls back to another agent (tools/sonata-session)."""
import os
import pwd

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Polkit", "1.0")
gi.require_version("PolkitAgent", "1.0")
from gi.repository import Gio, GLib, Gtk, Polkit, PolkitAgent  # noqa: E402,F401

from .. import ui  # noqa: E402
from . import layer  # noqa: E402
from .loginui import avatar, password_field, shake  # noqa: E402

OBJECT_PATH = "/io/github/vinioliveiras/sonata2/PolkitAgent"

ui.register("""
window.sonata-auth { background: rgba(0, 0, 0, 0.18); }
.auth-panel { background: %(panel_material)s; border-radius: 14px; padding: 22px 22px 18px 22px;
  box-shadow: 0 22px 56px rgba(0,0,0,0.35), 0 0 0 0.5px rgba(0,0,0,0.35), inset 0 0 0 0.5px %(highlight)s; }
.auth-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
.auth-text { font-size: %(text_small)s; color: %(label_secondary)s; }
.auth-error { font-size: %(text_small)s; color: %(destructive)s; }
.auth-panel entry { min-height: 28px; }
.auth-panel entry.shake { animation: lk-shake 420ms ease-in-out; }
.auth-panel button { min-width: 76px; }
@keyframes auth-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
.auth-panel { animation: auth-in 220ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="polkit-agent")


def _user(identity) -> str:
    if isinstance(identity, Polkit.UnixUser):
        try:
            return pwd.getpwuid(identity.get_uid()).pw_name
        except KeyError:
            return str(identity.get_uid())
    return identity.to_string()


def _real_name(name: str) -> str:
    try:
        gecos = pwd.getpwnam(name).pw_gecos.split(",")[0]
    except KeyError:
        gecos = ""
    return gecos or name


class AuthDialog:
    """One request: shows, tries passwords, calls done(ok) once."""

    def __init__(self, app, message, identities, cookie, done):
        self.app, self.cookie, self.done_cb = app, cookie, done
        self.identities = list(identities)
        me = GLib.get_user_name()
        names = [_user(i) for i in self.identities]
        self.index = names.index(me) if me in names else 0
        self.session = None
        self.finished = False
        self.win = Gtk.Window(application=app, decorated=False, title="Authenticate",
                              css_classes=["sonata-auth"])
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["auth-panel"],
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=340)
        top = Gtk.Box(spacing=14)
        top.append(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=40, valign=Gtk.Align.START))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        texts.append(Gtk.Label(label="Authentication Required", xalign=0, css_classes=["auth-title"]))
        texts.append(Gtk.Label(label=message or "An application wants to make changes.", xalign=0, wrap=True,
                               max_width_chars=40, css_classes=["auth-text"]))
        texts.append(Gtk.Label(label="Enter the password to allow this.", xalign=0,
                               css_classes=["auth-text"]))
        top.append(texts)
        panel.append(top)
        who = Gtk.Box(spacing=10, margin_top=6)
        who.append(avatar(28, names[self.index], _real_name(names[self.index])))
        if len(names) > 1:
            self.account = Gtk.DropDown.new_from_strings([_real_name(n) for n in names])
            self.account.set_selected(self.index)
            self.account.connect("notify::selected", lambda d, _p: self._pick(d.get_selected()))
            self.account.set_hexpand(True)
            who.append(self.account)
        else:
            who.append(Gtk.Label(label=_real_name(names[self.index]), xalign=0, hexpand=True))
        panel.append(who)
        self.entry = password_field("Password")
        self.entry.remove_css_class("lk-field")          # the login screen's look is for dark backdrops
        self.entry.set_halign(Gtk.Align.FILL)
        self.entry.connect("activate", lambda *_: self._ok())
        panel.append(self.entry)
        self.error = Gtk.Label(xalign=0, css_classes=["auth-error"], visible=False, wrap=True)
        panel.append(self.error)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        cancel = Gtk.Button(label="Cancel", css_classes=["sonata-button"])
        cancel.connect("clicked", lambda *_: self.finish(False))
        self.ok = Gtk.Button(label="OK", css_classes=["sonata-button", "default"])
        self.ok.connect("clicked", lambda *_: self._ok())
        buttons.append(cancel)
        buttons.append(self.ok)
        panel.append(buttons)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, *_: kv == 0xff1b and (self.finish(False), True)[1])
        self.win.add_controller(keys)
        if layer.overlay_fullscreen(self.win, "sonata2-auth"):
            pass                                         # above everything, takes the keyboard
        self.win.present()
        self.entry.grab_focus()

    def _pick(self, i):
        self.index = i
        self._cancel_session()

    def _ok(self):
        if not self.entry.get_text():
            return
        self.ok.set_sensitive(False)
        self.entry.set_sensitive(False)
        self.error.set_visible(False)
        self._cancel_session()
        s = self.session = PolkitAgent.Session.new(self.identities[self.index], self.cookie)
        s.connect("request", lambda sess, _prompt, _echo: sess.response(self.entry.get_text()))
        s.connect("show-error", lambda _s, text: self._say(text))
        s.connect("completed", self._completed)
        s.initiate()

    def _completed(self, _s, gained):
        # a finished session is only let go (cancelling it again is what crashed)
        done_session, self.session = self.session, None
        GLib.idle_add(lambda: (done_session and None, False)[1])       # dropped after the signal returns
        if gained:
            self.finish(True)
            return
        self.ok.set_sensitive(True)
        self.entry.set_sensitive(True)
        self.entry.set_text("")
        self.entry.grab_focus()
        if not self.error.get_visible():
            self._say("Sorry, try again.")
        shake(self.entry)

    def _say(self, text):
        self.error.set_label(text)
        self.error.set_visible(True)

    def _cancel_session(self):
        if self.session is not None:
            s, self.session = self.session, None
            try:
                s.cancel()
            except GLib.Error:
                pass

    def finish(self, ok: bool) -> None:
        if self.finished:
            return
        self.finished = True
        self._cancel_session()
        self.win.destroy()
        self.done_cb(ok)


AGENT_XML = """<node><interface name="org.freedesktop.PolicyKit1.AuthenticationAgent">
  <method name="BeginAuthentication">
    <arg type="s" name="action_id" direction="in"/><arg type="s" name="message" direction="in"/>
    <arg type="s" name="icon_name" direction="in"/><arg type="a{ss}" name="details" direction="in"/>
    <arg type="s" name="cookie" direction="in"/><arg type="a(sa{sv})" name="identities" direction="in"/>
  </method>
  <method name="CancelAuthentication"><arg type="s" name="cookie" direction="in"/></method>
</interface></node>"""
AUTHORITY = ("org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
             "org.freedesktop.PolicyKit1.Authority")


def _identity(kind: str, details: dict):
    """polkit's D-Bus identity -> the object PolkitAgent.Session wants."""
    if kind == "unix-user":
        return Polkit.UnixUser.new(int(details.get("uid", 0)))
    if kind == "unix-group":
        return Polkit.UnixGroup.new(int(details.get("gid", 0)))
    return None


class Agent:
    """The agent over polkit's D-Bus interface (plain Gio: the listener
    class's async vfunc can't be implemented safely from Python). polkitd
    calls BeginAuthentication; the method returns once the dialog is done
    (an error when cancelled), and CancelAuthentication closes it."""

    def __init__(self, app):
        self.app = app
        self.dialogs = {}                 # cookie -> AuthDialog
        self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.reg = self.bus.register_object(OBJECT_PATH, node.interfaces[0], self._call, None, None)

    def _call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "CancelAuthentication":
            (cookie,) = params.unpack()
            dlg = self.dialogs.get(cookie)
            invocation.return_value(None)
            if dlg:
                dlg.finish(False)
            return
        action_id, message, _icon, _details, cookie, identities = params.unpack()
        ids = [i for i in (_identity(kind, det) for kind, det in identities) if i is not None]
        if not ids:
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Failed", "No identity to authenticate")
            return

        def done(ok):
            self.dialogs.pop(cookie, None)
            if ok:
                invocation.return_value(None)
            else:
                invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.Cancelled",
                                             "Authentication was cancelled")
        self.dialogs[cookie] = AuthDialog(self.app, message, ids, cookie, done)

    # test hook: the same entry polkitd uses, without D-Bus
    def begin(self, message, identities, cookie, done):
        self.dialogs[cookie] = AuthDialog(self.app, message, identities, cookie,
                                          lambda ok: (self.dialogs.pop(cookie, None), done(ok)))


def _session_id():
    try:
        subj = Polkit.UnixSession.new_for_process_sync(os.getpid(), None)
        if subj is not None:
            return subj.get_session_id()
    except GLib.Error:
        pass
    return os.environ.get("XDG_SESSION_ID")


def register(app):
    """Register for this login session. Returns the agent (keep it) or None."""
    sid = _session_id()
    if not sid:
        print("sonata2 polkit: no login session found")
        return None
    try:
        agent = Agent(app)
    except GLib.Error as e:
        print(f"sonata2 polkit: no system bus: {e.message}")
        return None
    subject = ("unix-session", {"session-id": GLib.Variant("s", sid)})
    locale = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or "C"
    try:
        agent.bus.call_sync(*AUTHORITY, "RegisterAuthenticationAgent",
                            GLib.Variant("((sa{sv})ss)", (subject, locale, OBJECT_PATH)),
                            None, Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as e:
        print(f"sonata2 polkit: couldn't register: {e.message}")
        return None
    print(f"sonata2 polkit: registered for session {sid}", flush=True)
    return agent
