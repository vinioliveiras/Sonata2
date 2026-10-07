"""Ask before apps use things (Vini): Settings > Security & Privacy >
"Ask before apps use the camera, microphone, network…", on by default.

On: a newly installed Flatpak app, the first time it opens, shows what
it wants of the microphone, network and home folder (checked; uncheck to
refuse -- Flatpak overrides, appmanage.py); the camera and location are
asked when the app first uses them (Sonata's Access portal, portal.py).
Off: apps get all of it without asking.

Apps from the distro's packages aren't sandboxed: nothing to ask.
Apps already installed when this came are taken as reviewed.

    asking() / set_asking(on)
    review_gate(info, launch)     # apps.py's launch wrapper, after applock
"""
from . import config

NAME = "appperms"
DEFAULTS = {"ask": True, "reviewed": None}
ASKED = ("microphone", "network", "files")        # asked at the first open (overrides)


def _load() -> dict:
    return config.load(NAME, DEFAULTS)


def asking() -> bool:
    return bool(_load().get("ask", True))


def set_asking(on: bool) -> None:
    config.update(NAME, ask=bool(on))


def flatpak_id(info) -> str:
    from .backend import uninstall
    path = (info.get_filename() or "") if hasattr(info, "get_filename") else ""
    if not path:
        return ""
    import os
    return uninstall._flatpak_id(path) or uninstall._flatpak_id(os.path.realpath(path))


def installed_flatpaks() -> list:
    from . import apps
    return sorted({f for f in (flatpak_id(i) for i in apps.scan().values()) if f})


def reviewed() -> list:
    data = _load()
    got = data.get("reviewed")
    if not isinstance(got, list):                   # first time: what's there already counts as seen
        got = installed_flatpaks()
        config.update(NAME, reviewed=got)
    return got


def mark_reviewed(fid: str) -> None:
    got = reviewed()
    if fid not in got:
        config.update(NAME, reviewed=got + [fid])


def review_gate(info, launch) -> bool:
    """True: the review panel is up (launch() runs after it) -- the caller
    does nothing more; False: launch now."""
    fid = flatpak_id(info)
    if not fid or fid in reviewed():
        return False
    mark_reviewed(fid)                             # asked once, whatever the answer
    if not asking():
        return False
    from .backend import appmanage
    from .backend.uninstall import Owner
    owner = Owner("flatpak", fid)
    wants = [(k, t) for k, t, on in appmanage.permissions(owner) if on and k in ASKED]
    if not wants:
        return False
    try:
        from gi.repository import Gdk
        if Gdk.Display.get_default() is None:
            return False
    except Exception:
        return False
    ask(info, owner, wants, launch)
    return True


def ask(info, owner, wants, launch):
    """“X” wants to use: [x] Microphone [x] Network [x] Home Folder -- Open / Don't Open."""
    from gi.repository import Gtk
    from .backend import appmanage, system
    from .ui import dialog
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)
    checks = {}
    for key, title in wants:
        c = Gtk.CheckButton(label=title, active=True)
        checks[key] = c
        box.append(c)

    def answer(rid):
        if rid != "open":
            return
        refused = [k for k, c in checks.items() if not c.get_active()]

        def apply():
            for k in refused:
                appmanage.set_permission(owner, k, False)
        def opened(_r):
            from . import applock
            with applock.trusted():                       # its password (if locked) was asked already
                launch()
        system.run_async(apply, opened)                   # overrides first: the app starts with them
    name = info.get_display_name()
    dlg = dialog.alert(f"“{name}” wants to use", "Uncheck what it shouldn't use. You can change it later "
                                                 "in Settings › Installed Apps.",
                       [("cancel", "Don't Open", ""), ("open", "Open", "default")], answer)
    dlg.set_extra_child(box)
    dlg.checks = checks                               # (tests)
    return dlg


def access_answer(title: str, subtitle: str, body: str, options: dict, done) -> None:
    """The Access portal (camera, location...): done(0) granted, done(1)
    refused. Not asking: granted at once."""
    if not asking():
        done(0)
        return
    from .ui import dialog
    grant = options.get("grant_label") or "Allow"
    deny = options.get("deny_label") or "Don't Allow"
    text = "\n".join(t for t in (subtitle, body) if t) or \
        "You can change this later in Settings › Installed Apps."
    dialog.alert(title or "Allow access?", text, [("deny", deny, ""), ("grant", grant, "default")],
                 lambda rid: done(0 if rid == "grant" else 1), close="deny")
