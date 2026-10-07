"""Settings > Apps (Vini): every app; click one for its own page -- how big
it is and its data, what it may use (Flatpak apps: camera, microphone,
network, location, background, notifications, home folder), Lock App,
Clear Data and Uninstall. Built from Sonata's kit: Settings rows,
ui.dialog alerts, the shared app list (applist.py)."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import applock, ui  # noqa: E402
from ..backend import appmanage, system  # noqa: E402
from ..backend import uninstall as U  # noqa: E402

KIND = {"flatpak": "Flatpak", "package": "Package", "shortcut": "Shortcut"}
HEAD_ICON = 56


def size_text(n) -> str:
    return GLib.format_size(n) if n else "Zero KB"


def home_path(p: str) -> str:
    home = GLib.get_home_dir()
    return "~" + p[len(home):] if p.startswith(home + "/") else p


class AppsPage:
    def __init__(self, settings):
        self.settings = settings
        self.app = None              # the open app: {"info", "owner", "dirs", ...} (tests)

    def groups(self) -> list:
        from .applist import AppList
        self.list = AppList("Installed Apps", "Click one for its size, data and permissions.",
                            self.open)
        self.rows = self.list.rows
        return [self.list.group]

    def focus(self, did: str) -> None:
        self.list.focus(did)

    # -- one app ---------------------------------------------------------------------------------
    def open(self, row) -> None:
        from .app import group, switch_row
        info = row.info
        name = info.get_display_name()
        app = self.app = {"info": info, "did": row.did, "owner": None, "dirs": []}
        head = group()
        hero = Adw.ActionRow(title=name, use_markup=False, subtitle="…")
        img = Gtk.Image(pixel_size=HEAD_ICON)
        from .. import icons
        icons.set_image(img, icons.app_icon(info))
        hero.add_prefix(img)
        hero.add_css_class("st-app-hero")
        head.add(hero)
        perms = group("Permissions")
        perms.add(Adw.ActionRow(title="…", use_markup=False))
        manage = group()
        lock = switch_row("Lock App", applock.locked(info.get_id()), lambda on: self._lock(info, lock, on),
                          subtitle="Ask for your password when it opens")
        manage.add(lock)
        clear = Adw.ActionRow(title="Clear Data", use_markup=False, subtitle="…")
        self.clear_btn = ui.controls.push_button("Clear…", lambda: self.ask_clear(), valign=Gtk.Align.CENTER)
        self.clear_btn.set_sensitive(False)
        clear.add_suffix(self.clear_btn)
        manage.add(clear)
        rm = Adw.ActionRow(title="Uninstall", use_markup=False)
        self.rm_btn = ui.controls.push_button("Uninstall…", lambda: self.ask_uninstall(),
                                              style="destructive", valign=Gtk.Align.CENTER)
        self.rm_btn.set_sensitive(False)
        rm.add_suffix(self.rm_btn)
        manage.add(rm)
        app.update(hero=hero, perms=perms, clear=clear, lock=lock)
        self.settings.push_detail(name, [head, perms, manage])
        system.run_async(self._look, lambda res, a=app: self._looked(a, res), info)

    @staticmethod
    def _look(info):
        """Blocking: who owns it, its size, its folders and their size, permissions."""
        o = U.owner(info)
        dirs = appmanage.data_dirs(info, o)
        return {"owner": o, "size": appmanage.app_size(o), "dirs": dirs,
                "data": appmanage.folder_size(dirs), "perms": appmanage.permissions(o, info)}

    def _looked(self, app, res) -> None:
        if app is not self.app or res is None:          # another app opened meanwhile
            return
        from .app import switch_row, _clear_group
        o = res["owner"]
        app.update(owner=o, dirs=res["dirs"])
        from .. import webapps
        web = webapps.is_webapp(app["info"].get_id() or "")
        bits = ["Web App" if web else KIND.get(o.kind, "") if o else "Part of Sonata"]
        if res["size"]:
            bits.append(size_text(res["size"]) + " app")
        bits.append(size_text(res["data"]) + " data")
        app["hero"].set_subtitle(" · ".join(b for b in bits if b))
        _clear_group(app["perms"])
        if res["perms"]:
            for key, title, on in res["perms"]:
                row = switch_row(title, on, lambda v, k=key: self.set_permission(app, k, v))
                row.key = key
                app["perms"].add(row)
            if o.kind == "flatpak":
                app["perms"].set_description("Changes take effect the next time the app opens.")
            else:                                  # a packaged app: Sonata keeps it out when it opens it
                from .. import sandbox
                app["perms"].set_description(
                    "Applied when the app is opened from Sonata (the Dock, Apps, search) the next time. "
                    "Home Folder off: it gets a folder of its own instead of yours."
                    + ("" if sandbox.available() else " Needs bubblewrap (./install.sh installs it)."))
        else:
            note = Adw.ActionRow(title="Asked by the site" if web else "Part of Sonata", use_markup=False,
                                 subtitle="The site asks before it uses the camera, microphone or location."
                                 if web else "Sonata's own apps can use what Sonata uses.")
            note.set_subtitle_lines(0)
            app["perms"].add(note)
        app["clear"].set_subtitle(", ".join(home_path(d) for d in res["dirs"]) if res["dirs"]
                                  else "No data of its own found")
        self.clear_btn.set_sensitive(bool(res["dirs"]))
        self.rm_btn.set_sensitive(o is not None or web)          # (web apps: Vini)

    # -- changes -----------------------------------------------------------------------------------
    def _lock(self, info, row, on) -> None:
        if on:
            applock.set_locked(info.get_id(), True)
            return
        row.quiet = True                     # unlocking asks the password: off only when right
        row.set_active(True)
        row.quiet = False

        def done():
            row.quiet = True
            row.set_active(False)
            row.quiet = False
        applock.ask_unlock(info, done)

    def set_permission(self, app, key, on) -> None:
        system.run_async(lambda: appmanage.set_permission(app["owner"], key, on, app["info"]),
                         lambda ok: ok or self.settings.toast("Couldn't change that permission"))

    def ask_clear(self):
        app = self.app
        name = app["info"].get_display_name()
        listed = "\n".join(home_path(d) for d in app["dirs"])
        return ui.dialog.alert(f"Clear “{name}”’s data?",
                               f"Its sign-ins, settings and cache go to the Trash (you can put them back "
                               f"from there):\n{listed}\nQuit the app first.",
                               [("cancel", "Cancel", ""), ("clear", "Clear Data", "destructive")],
                               lambda rid: rid == "clear" and self.clear(app), parent=self.settings)

    def clear(self, app) -> None:
        def done(failed):
            if failed:
                self.settings.toast("Some of its data couldn't be moved to the Trash")
            app["dirs"] = [d for d in app["dirs"] if os.path.exists(d)]
            if app is self.app:
                app["clear"].set_subtitle("No data of its own found" if not app["dirs"]
                                          else ", ".join(home_path(d) for d in app["dirs"]))
                self.clear_btn.set_sensitive(bool(app["dirs"]))
                sub = app["hero"].get_subtitle().rsplit(" · ", 1)[0]
                app["hero"].set_subtitle(sub + " · " + size_text(appmanage.folder_size(app["dirs"])) + " data")
        system.run_async(lambda: appmanage.clear_data(app["dirs"]), done)

    def ask_uninstall(self) -> None:
        from ..shell import uninstall_ui
        app = self.app

        def done(ok):
            if ok:
                self.settings.pop_detail()
                self.list.remove(app["did"])
        uninstall_ui.ask(app["info"], parent=self.settings, done=done)
