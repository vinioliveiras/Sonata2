"""Asking before uninstalling an app, then doing it (Dock's Trash, Launchpad).

    ask(info, parent=None)
The question names what goes: the package (and the ones no longer needed
with it), the Flatpak, or just the shortcut. Root steps ask for the
password with Sonata's own prompt (polkit). A notification says when it's
done, or why it couldn't be."""
from gi.repository import Gio, GLib

from .. import ui
from ..backend import system
from ..backend import uninstall as U
from ..i18n import _, ngettext

MAX_LISTED = 8


def _notify(summary: str, body: str = "") -> None:
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", ("Sonata", 0, "user-trash-full", summary, body, [], {}, -1)),
                 None, Gio.DBusCallFlags.NONE, 2000, None, None)
    except GLib.Error:
        pass


def ask_webapp(wid: str, parent=None, done=None):
    """A web app: deleted with its login and data (Launchpad, the Dock's
    Trash, Settings > Apps -- Vini: web apps can be uninstalled too)."""
    from .. import webapps
    entry = webapps.get(wid) or {}

    def answer(rid):
        if rid != "delete":
            return
        webapps.remove(wid)                # its entry goes: Launchpad and the Dock drop it
        from .. import sounds
        sounds.play("trash")
        if done:
            done(True)
    return ui.dialog.alert(_("Delete “{name}”?").format(name=entry.get('name') or _("this web app")),
                           _("Its login and everything it saved on this computer are deleted too."),
                           [("cancel", _("Cancel"), ""), ("delete", _("Delete"), "destructive")], answer,
                           parent=parent)


def ask(info, parent=None, done=None) -> None:
    from .. import webapps
    if webapps.is_webapp(info.get_id() or ""):
        ask_webapp(webapps.id_of(info.get_id()), parent, done)
        return
    name = info.get_display_name()

    def looked(res):
        o, also = res or (None, [])
        if o is None:
            ui.dialog.alert(_("“{name}” can't be uninstalled here.").format(name=name),
                            _("It's part of the system or of Sonata, or its package couldn't be found."),
                            [("ok", _("OK"), "default")], parent=parent)
            return
        if o.kind == "shortcut":
            body = _("Its shortcut is moved to the Trash.")
        elif o.kind == "flatpak":
            body = _("The Flatpak app {name} is removed. Its settings and data stay in your home folder.").format(name=o.name)
        else:
            body = _("The package “{name}” is removed.").format(name=o.name)
            if also:
                listed = ", ".join(also[:MAX_LISTED])
                if len(also) > MAX_LISTED:
                    more = len(also) - MAX_LISTED
                    listed = ngettext("{list} and {n} more", "{list} and {n} more", more).format(list=listed, n=more)
                body += " " + _("No longer needed, these go too: {list}.").format(list=listed)
        ui.dialog.alert(_("Uninstall “{name}”?").format(name=name), body,
                        [("cancel", _("Cancel"), ""), ("go", _("Uninstall"), "destructive")],
                        lambda rid: rid == "go" and _uninstall(o, name, done), parent=parent)
    system.run_async(lambda: (lambda o: (o, o.also() if o else []))(U.owner(info)), looked)


def _uninstall(o, name, done) -> None:
    from .. import sounds
    if o.kind == "shortcut":
        try:
            Gio.File.new_for_path(o.name).trash(None)
            sounds.play("trash")
            if done:
                done(True)
        except GLib.Error as e:
            _notify(_("“{name}” couldn't be removed").format(name=name), e.message)
        return
    from ..backend.updates import Runner
    tail = []

    def line(t):
        tail.append(t)
        del tail[:-6]

    def finished(ok, _i):
        if ok:
            sounds.play("trash")
            _notify(_("“{name}” was uninstalled").format(name=name))
        else:
            _notify(_("“{name}” couldn't be uninstalled").format(name=name), "\n".join(tail[-3:]))
        if done:
            done(ok)
    Runner(o.steps(), line, lambda _f: None, finished).start()
