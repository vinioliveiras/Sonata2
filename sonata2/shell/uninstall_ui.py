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


def ask(info, parent=None, done=None) -> None:
    name = info.get_display_name()

    def looked(res):
        o, also = res or (None, [])
        if o is None:
            ui.dialog.alert(f"“{name}” can't be uninstalled here.",
                            "It's part of the system or of Sonata, or its package couldn't be found.",
                            [("ok", "OK", "default")], parent=parent)
            return
        if o.kind == "shortcut":
            body = "Its shortcut is moved to the Trash."
        elif o.kind == "flatpak":
            body = f"The Flatpak app {o.name} is removed. Its settings and data stay in your home folder."
        else:
            body = f"The package “{o.name}” is removed."
            if also:
                listed = ", ".join(also[:MAX_LISTED]) + (f" and {len(also) - MAX_LISTED} more"
                                                          if len(also) > MAX_LISTED else "")
                body += f" No longer needed, these go too: {listed}."
        ui.dialog.alert(f"Uninstall “{name}”?", body,
                        [("cancel", "Cancel", ""), ("go", "Uninstall", "destructive")],
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
            _notify(f"“{name}” couldn't be removed", e.message)
        return
    from ..backend.updates import Runner
    tail = []

    def line(t):
        tail.append(t)
        del tail[:-6]

    def finished(ok, _i):
        if ok:
            sounds.play("trash")
            _notify(f"“{name}” was uninstalled")
        else:
            _notify(f"“{name}” couldn't be uninstalled", "\n".join(tail[-3:]))
        if done:
            done(ok)
    Runner(o.steps(), line, lambda _f: None, finished).start()
