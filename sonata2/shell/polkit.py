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

from ..i18n import _  # noqa: E402
from . import layer  # noqa: E402,F401
from .authsheet import PasswordSheet  # noqa: E402

OBJECT_PATH = "/io/github/vinioliveiras/sonata2/PolkitAgent"

# (the look: authsheet, shared with every prompt for the login password)


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
        self.sheet = PasswordSheet(_("Authentication Required"), message or _("An application wants to make changes."),
                                   lambda _pw: self._ok(), lambda: self.finish(False),
                                   detail=_("Enter the password to allow this."), app=app, names=names,
                                   real_names=[_real_name(n) for n in names], index=self.index, on_pick=self._pick)
        self.win, self.entry, self.error, self.ok = self.sheet.win, self.sheet.entry, self.sheet.error, self.sheet.ok
        self.who, self.names = self.sheet.who, names
        self.account = getattr(self.sheet, "account", None)

    @property
    def face(self):
        return self.sheet.face

    def _pick(self, i):
        self.index = i
        self._cancel_session()

    def _ok(self):
        self._cancel_session()
        s = self.session = PolkitAgent.Session.new(self.identities[self.index], self.cookie)
        s.connect("request", lambda sess, _prompt, _echo: sess.response(self.entry.get_text()))
        s.connect("show-error", lambda _s, text: self.sheet.say(text))
        s.connect("completed", self._completed)
        s.initiate()

    def _completed(self, _s, gained):
        # a finished session is only let go (cancelling it again is what crashed)
        done_session, self.session = self.session, None
        GLib.idle_add(lambda: (done_session and None, False)[1])       # dropped after the signal returns
        if gained:
            self.finish(True)
            return
        self.sheet.wrong(None if self.error.get_visible() else _("Sorry, try again."))

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
        self.sheet.close()
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
    # (no id given: skipped -- never a default of 0, which would mean root)
    try:
        if kind == "unix-user" and "uid" in details:
            return Polkit.UnixUser.new(int(details["uid"]))
        if kind == "unix-group" and "gid" in details:
            return Polkit.UnixGroup.new(int(details["gid"]))
    except (TypeError, ValueError):
        pass
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
        # only polkitd may ask for passwords: track its unique bus name
        try:                              # known before any request (the watch reports it later)
            self.polkitd = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                              "org.freedesktop.DBus", "GetNameOwner",
                                              GLib.Variant("(s)", (AUTHORITY[0],)), None,
                                              Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except GLib.Error:
            self.polkitd = None
        self.watch = Gio.bus_watch_name_on_connection(
            self.bus, AUTHORITY[0], Gio.BusNameWatcherFlags.NONE,
            lambda _c, _n, owner: setattr(self, "polkitd", owner),
            lambda *_a: setattr(self, "polkitd", None))
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        self.reg = self.bus.register_object(OBJECT_PATH, node.interfaces[0], self._call, None, None)

    def _call(self, _conn, sender, _path, _iface, method, params, invocation):
        if sender is None or sender != self.polkitd:          # anyone else on the bus could fake a prompt
            invocation.return_dbus_error("org.freedesktop.PolicyKit1.Error.NotAuthorized",
                                         "Only polkitd may call the authentication agent")
            return
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
    from .. import logs
    logs.verbose() and print(f"sonata2 polkit: registered for session {sid}", flush=True)
    return agent
