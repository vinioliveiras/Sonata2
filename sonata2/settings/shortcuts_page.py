"""Settings > Keyboard Shortcuts: every shortcut Sonata sets up (macOS- and
Windows-style ones side by side), changed by pressing the new keys.

A row shows the current shortcuts as key caps; clicking it records a new
one (it replaces them), and its "…" menu adds another one, turns it off or
puts Sonata's back. While a shortcut is being recorded the desktop's own
shortcuts are held back (keyboard-shortcuts-inhibit), so Super+D can be
pressed without showing the desktop. The logic: sonata2/shortcuts.py."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .. import shortcuts as S, ui  # noqa: E402

ui.register("""
.sc-keys { opacity: 0.9; }
.sc-or { font-size: %(text_small)s; color: %(label_secondary)s; }
.sc-text { font-size: %(text_small)s; color: %(label_secondary)s; }
.sc-capture { font-size: 20px; font-weight: 600; min-height: 40px; }
""", key="settings-shortcuts")


def _keys(binding: str) -> Gtk.Box:
    """The shortcuts as key caps, "or" between them."""
    box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, css_classes=["sc-keys"])
    combos = S.split(binding)
    if not combos:
        box.append(Gtk.Label(label="Off", css_classes=["sc-text"]))
    for i, combo in enumerate(combos):
        if i:
            box.append(Gtk.Label(label="or", css_classes=["sc-or"]))
        accel = S.accelerator(combo)
        box.append(Gtk.ShortcutLabel(accelerator=accel) if accel
                   else Gtk.Label(label=S.describe(combo), css_classes=["sc-text"]))
    return box


class ShortcutsPage:
    def __init__(self, win):
        self.win = win                                  # the Settings window (toasts, dialogs)
        self.rows = {}

    def groups(self) -> list:
        top = Adw.PreferencesGroup(description="Click a shortcut to change it, then press the new keys. "
                                               "Super- and Ctrl-style shortcuts both work.")
        out = [top]
        for name in S.GROUPS:
            g = Adw.PreferencesGroup(title=name)
            for s in (s for s in S.SHORTCUTS if s.group == name):
                g.add(self._row(s))
            out.append(g)
        reset = Adw.PreferencesGroup()
        btn = Gtk.Button(label="Restore Defaults", halign=Gtk.Align.START, css_classes=["sonata-button"])
        btn.connect("clicked", lambda *_: self._reset_all())
        reset.add(btn)
        out.append(reset)
        return out

    # -- rows ---------------------------------------------------------------------------------
    def _row(self, s):
        row = Adw.ActionRow(title=GLib.markup_escape_text(s.title), activatable=True)
        row.connect("activated", lambda *_: self.record(s, add=False))
        row.holder = Gtk.Box(valign=Gtk.Align.CENTER)      # the key caps, before the "…" button
        row.add_suffix(row.holder)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                              tooltip_text="More")
        more.set_create_popup_func(lambda b, s=s: self._menu(b, s))
        row.add_suffix(more)
        self.rows[s.id] = row
        self._fill(s)
        return row

    def _fill(self, s):
        row = self.rows[s.id]
        old = row.holder.get_first_child()
        if old is not None:
            row.holder.remove(old)
        row.holder.append(_keys(S.current(s)))

    def _menu(self, button, s):
        Item = ui.menu.Item
        is_default = S.split(S.current(s)) == S.split(S.default(s))
        pop = ui.menu.popup(button, [
            [Item("Change Shortcut…", lambda: self.record(s, add=False)),
             Item("Add Another Shortcut…", lambda: self.record(s, add=True))],
            [Item("Turn Off", lambda: self._set(s, []), enabled=bool(S.split(S.current(s)))),
             Item("Use the Default", lambda: (S.reset(s), self._fill(s)), enabled=not is_default)]],
            position=Gtk.PositionType.BOTTOM)
        button.set_active(False)
        return pop

    def _set(self, s, combos):
        S.set_binding(s, combos)
        self._fill(s)

    def _reset_all(self):
        def answer(rid):
            if rid == "reset":
                for s in S.SHORTCUTS:
                    S.reset(s)
                    self._fill(s)
                self.win.toast("Shortcuts restored")
        ui.dialog.alert("Restore the default shortcuts?", "Every shortcut you changed goes back to Sonata's.",
                        [("cancel", "Cancel", ""), ("reset", "Restore", "destructive")], answer, parent=self.win)

    # -- recording ------------------------------------------------------------------------------
    def record(self, s, add: bool):
        shown = Gtk.Label(label="…", css_classes=["sc-capture"])
        dlg = ui.dialog.alert(s.title, "Press the new shortcut. Esc cancels.", [("cancel", "Cancel", "")],
                              parent=self.win)
        dlg.set_extra_child(shown)
        surface = self.win.get_surface()
        inhibit = isinstance(surface, Gdk.Toplevel)
        if inhibit:
            surface.inhibit_system_shortcuts(None)          # Super+D reaches us, not the desktop

        def done(*_a):
            if inhibit:
                surface.restore_system_shortcuts()
        dlg.connect("closed" if hasattr(Adw, "AlertDialog") and isinstance(dlg, Adw.AlertDialog) else "response",
                    done)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def pressed(_c, keyval, keycode, state):
            mods = dict(ctrl=bool(state & Gdk.ModifierType.CONTROL_MASK),
                        shift=bool(state & Gdk.ModifierType.SHIFT_MASK),
                        alt=bool(state & Gdk.ModifierType.ALT_MASK),
                        super_=bool(state & Gdk.ModifierType.SUPER_MASK))
            if keyval == Gdk.KEY_Escape and not any(mods.values()):
                return False                                 # the dialog cancels
            combo = S.combo_from_key(keycode, **mods)
            if combo is None:                                # a modifier so far: show it
                shown.set_label("+".join(S.MOD_LABEL[m] for m, on in zip(S.MODS, (
                    mods["ctrl"], mods["alt"], mods["shift"], mods["super_"])) if on) or "…")
                return True
            self._apply(s, combo, add)
            dlg.close() if hasattr(dlg, "close") else dlg.destroy()
            return True
        keys.connect("key-pressed", pressed)
        dlg.add_controller(keys)

    def _apply(self, s, combo, add):
        other = S.owner(combo, but=s)
        if other is not None:                                # one combo, one shortcut
            S.set_binding(other, [c for c in S.split(S.current(other)) if c != combo])
            self._fill(other)
            self.win.toast(f"{S.describe(combo)} was removed from “{other.title}”")
        combos = S.split(S.current(s)) if add else []
        if combo not in combos:
            combos.append(combo)
        self._set(s, combos)
