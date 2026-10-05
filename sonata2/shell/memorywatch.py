"""The menu bar's side of backend.memguard: every few seconds a look at the
memory (two small files); under pressure the apps in the background give
memory back; the alert only as a last resort (macOS: "Your system has run
out of application memory"), and never again for a while once dismissed.

Vini: the alert must not be a nuisance -- it comes only when the memory is
nearly gone AND the whole system stalls on it for CRITICAL_CHECKS checks in
a row, after the background apps already gave memory back (and
systemd-oomd had its chance), and at most once per ALERT_COOLDOWN_S."""
import time

from gi.repository import GLib, Gtk, Pango

from .. import ui
from ..backend import memguard as MG

CHECK_S = 5
CRITICAL_CHECKS = 3                   # 15 s in a row
ALERT_COOLDOWN_S = 600
ALERT_ROWS = 8


def app_label(scope):
    """(name, Gio icon or None) of an app's scope."""
    did, wid = MG.app_of(scope)
    if wid:
        from .. import webapps
        entry = webapps.get(wid) or {}
        from gi.repository import Gio
        path = webapps.icon_path(wid)
        import os
        icon = Gio.FileIcon.new(Gio.File.new_for_path(path)) if os.path.isfile(path) else None
        return entry.get("name") or "Web App", icon
    if did:
        from .. import apps
        info = apps.lookup(did if did.endswith(".desktop") else did + ".desktop")
        if info is not None:
            return info.get_display_name() or did, info.get_icon()
        return did.rsplit(".", 1)[-1], None
    return scope["unit"], None


def size_text(n: int) -> str:
    gb = n / 1024 ** 3
    return f"{gb:.1f} GB" if gb >= 1 else f"{n / 1024 ** 2:.0f} MB"


class MemoryWatch:
    def __init__(self, app):
        self.app = app
        self.last_reclaim = {}            # scope path -> monotonic time
        self.critical = 0
        self.reclaimed = False
        self.busy = False
        self.alert = None
        self.dismissed_at = None          # (monotonic time can be small right after boot)
        GLib.timeout_add_seconds(CHECK_S, self.tick)

    def tick(self) -> bool:
        mi = MG.meminfo()
        lv = MG.level(mi.get("total", 0), mi.get("available", 0), MG.psi())
        if lv == "ok":
            self.critical, self.reclaimed = 0, False
            return True
        self.critical = self.critical + 1 if lv == "critical" else 0
        if not self.busy:
            self.busy = True
            from ..backend import system
            system.run_async(self._give_back, self._after)
        return True

    def _give_back(self):
        """Background apps give memory back (a thread: memory.reclaim can take a while)."""
        try:
            from ..wl.wfipc import WayfireIPC
            views = WayfireIPC().call("window-rules/list-views") or []
        except Exception:
            views = None
        all_scopes = MG.scopes()
        done = 0
        if views is not None:                       # never guess what's on screen
            now = time.monotonic()
            for s in MG.background(all_scopes, views):
                if now - self.last_reclaim.get(s["path"], 0) < MG.RECLAIM_EVERY_S:
                    continue
                self.last_reclaim[s["path"]] = now
                done += MG.reclaim(s["path"], MG.reclaim_amount(s["memory"]))
        return all_scopes, done

    def _after(self, result):
        self.busy = False
        all_scopes, done = result or ([], 0)
        self.reclaimed = True                       # step 1 had its turn
        if done:
            print(f"sonata2-topbar: memory pressure: {done} background app(s) gave memory back", flush=True)
        if (self.critical >= CRITICAL_CHECKS and self.reclaimed and self.alert is None and all_scopes
                and (self.dismissed_at is None or time.monotonic() - self.dismissed_at > ALERT_COOLDOWN_S)):
            self._show(all_scopes)

    # -- the alert (last resort) --------------------------------------------------------------
    def _show(self, all_scopes):
        rows = sorted(all_scopes, key=lambda s: -s["memory"])[:ALERT_ROWS]
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"])
        for s in rows:
            name, icon = app_label(s)
            row = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
            img = Gtk.Image(pixel_size=24)
            if icon is not None:
                img.set_from_gicon(icon)
            else:
                img.set_from_icon_name("application-x-executable")
            row.append(img)
            row.append(Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            row.append(Gtk.Label(label=size_text(s["memory"]), css_classes=["dim-label", "numeric"]))
            lb.append(row)
        lb.select_row(lb.get_row_at_index(0))

        def answer(rid):
            self.alert = None
            self.dismissed_at = time.monotonic()
            sel = lb.get_selected_row()
            if rid == "quit" and sel is not None:
                MG.kill(rows[sel.get_index()]["path"])
        self.alert = ui.dialog.alert(
            "Your system has run out of application memory.",
            "To avoid problems with your computer, quit any apps you're not using.",
            [("resume", "Resume", ""), ("quit", "Force Quit", "destructive")], answer)
        self.alert.set_extra_child(lb)
