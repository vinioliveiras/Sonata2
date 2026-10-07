"""Alerts (macOS Big Sur alert: bold heading, body, buttons at the bottom).

    dialog.alert("Are you sure you want to permanently erase the items in the Trash?",
                 "You can't undo this action.",
                 [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")],
                 on_response=lambda rid: ...)

Default button (Return): the response styled "default"; otherwise the first
non-destructive one (Cancel), as on macOS for destructive alerts.

check="Apply to All" adds a checkbox (Finder's replace/conflict alerts);
on_response then gets (response_id, checked)."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
dialog.alert.sonata-alert .heading-bin > label, dialog.alert.sonata-alert .heading,
window.messagedialog.sonata-alert .heading {
  font-size: %(text_body)s; font-weight: 700; }
dialog.alert.sonata-alert .body, window.messagedialog.sonata-alert .body {
  font-size: %(text_small)s; color: %(label_secondary)s; }
dialog.alert.sonata-alert { font-family: %(font)s; }
dialog.alert.sonata-alert .response-area > button { min-height: 28px; padding-top: 0; padding-bottom: 0; border-radius: 7px; font-weight: 500; }   /* Big Sur buttons */
dialog.alert.sonata-alert > .floating-sheet { border-radius: %(r_dialog)s; }
window.messagedialog.sonata-alert, window.messagedialog.sonata-alert > contents {
  border-radius: %(r_dialog)s; background-color: %(window_bg)s; color: %(label)s;
  font-family: %(font)s;
}
window.messagedialog.sonata-alert .heading { font-size: %(text_body)s; font-weight: 700; }
window.messagedialog.sonata-alert .body { font-size: %(text_small)s; color: %(label_secondary)s; }
/* every alert is its own window: frosted glass like About/Dock */
window.dialog-window.sonata-glass-window { box-shadow: none; }
dialog.alert.sonata-alert.glass, dialog.alert.sonata-alert.glass > * { background: transparent; box-shadow: none; }
window.messagedialog.sonata-alert checkbutton { font-size: %(text_small)s; }
""")

_STYLE = {"destructive": Adw.ResponseAppearance.DESTRUCTIVE,
          "default": Adw.ResponseAppearance.SUGGESTED}


ALERT_WIDTH = 372     # px: the alert's own width, fixed

# libadwaita >= 1.5: AlertDialog (MessageDialog is deprecated since 1.6)
_MODERN = hasattr(Adw, "AlertDialog")
# set_content_width on an alert segfaults with libadwaita 1.5.0 (Ubuntu
# 24.04's; seen in the test run: every alert would take its app down) --
# the alert's own width there
_CONTENT_WIDTH_OK = (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6)


def _breakable(text: str) -> str:
    """Long unbroken words (file names: "04iuiyBZ61YPzdVS4GfRYKM-19.webp")
    get break chances every few letters. Without them the alert's wrapped
    heading couldn't settle on a width and the alert kept shaking."""
    import re
    return re.sub(r"\S{18,}", lambda m: "\u200b".join(m.group(0)[i:i + 8]
                                                        for i in range(0, len(m.group(0)), 8)), text or "")


def alert(heading: str, body: str, responses, on_response=None, parent=None,
          check: str = None, close: str = None, default: str = None):
    """close: the response Escape gives (default: "cancel", else the first
    non-destructive one; it needn't be one of `responses`). default: the one
    Return gives -- chosen on purpose, it may be destructive (macOS' Empty
    Trash: Return empties, Vini); otherwise never a destructive one."""
    chosen = default
    heading, body = _breakable(heading), _breakable(body)
    if _MODERN:
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_css_class("sonata-alert")
        if _CONTENT_WIDTH_OK:
            dlg.set_content_width(ALERT_WIDTH)      # one width: the wrapped text can't make it shake
    else:
        dlg = Adw.MessageDialog(heading=heading, body=body, transient_for=parent,
                                css_classes=["sonata-alert"])
    box = None
    if check:
        box = Gtk.CheckButton(label=check, halign=Gtk.Align.CENTER)
        dlg.set_extra_child(box)
    default = None
    for rid, text, style in responses:
        dlg.add_response(rid, text)
        if style in _STYLE:
            dlg.set_response_appearance(rid, _STYLE[style])
        if style != "destructive":
            default = rid if default is None or style == "default" else default
    default = chosen or default
    if default:
        dlg.set_default_response(default)
    # Escape = "cancel", else the first non-destructive response: never a
    # destructive one (TextEdit's "Don't Save" comes first and must not win)
    close = close or next((rid for rid, _t, _s in responses if rid == "cancel"), None) or \
        next((rid for rid, _t, s in responses if s != "destructive"), None)
    if close:
        dlg.set_close_response(close)
    if on_response:
        if box is not None:
            dlg.connect("response", lambda _d, rid: on_response(rid, box.get_active()))
        else:
            dlg.connect("response", lambda _d, rid: on_response(rid))
    if _MODERN:
        # always its own window, so the compositor's blur shows through its
        # glass (Vini: every alert in glass; inside the parent window it was
        # opaque) -- still in front of its window and modal to it
        owner = parent.get_root() if parent is not None and hasattr(parent, "get_root") else None
        dlg.present(None)
        root = dlg.get_root()
        if root is not None and root is not owner:      # own window: glass
            dlg.add_css_class("glass")
            root.add_css_class("sonata-glass-window")
            if isinstance(owner, Gtk.Window) and hasattr(root, "set_transient_for"):
                root.set_transient_for(owner)
                root.set_modal(True)
            # a fixed-size (min = max) window made the compositor and GTK
            # disagree on its size every frame: the alert "wobbled" sideways
            if hasattr(root, "set_resizable"):
                root.set_resizable(True)
    else:
        dlg.present()
    return dlg


def ask_text(heading: str, text: str, ok: str, on_done, body: str = "", parent=None):
    """One line to type (a new name...): an alert with an entry, the text
    selected, `ok` off while it's empty. on_done(text) with the stripped
    text, only when confirmed. Its own window (parent None) takes the
    keyboard wherever it's asked from -- a Dock's panel can't (Vini)."""
    from gi.repository import GLib
    from . import controls
    entry = controls.text_field(text or "", hexpand=True)              # the kit's field
    entry.set_activates_default(True)

    def answer(rid):
        value = entry.get_text().strip()
        if rid == "ok" and value:
            on_done(value)
    dlg = alert(heading, body, [("cancel", "Cancel", ""), ("ok", ok, "default")], answer, parent=parent)
    dlg.set_extra_child(entry)
    if hasattr(dlg, "set_response_enabled"):
        entry.connect("changed", lambda e: dlg.set_response_enabled("ok", bool(e.get_text().strip())))
        dlg.set_response_enabled("ok", bool(entry.get_text().strip()))     # opened empty: off already

    def focus():
        entry.grab_focus()
        entry.select_region(0, -1)
        return False
    GLib.idle_add(focus)
    dlg.entry = entry                                   # (tests)
    return dlg


def ask_password(heading: str, body: str, on_ok, parent=None, ok: str = "Open", wrong: bool = False):
    """The login password, checked (PAM) before on_ok() runs; a wrong one
    asks again ("Wrong password"). Return in the field checks it without
    closing the alert; the button closes it, then checks."""
    from gi.repository import GLib
    from . import controls
    from .. import pam
    entry = controls.text_field("", "Password", secret=True, hexpand=True)
    hint = Gtk.Label(label="Wrong password" if wrong else
                     ("" if pam.available() else "Passwords can't be checked (PAM missing)"),
                     css_classes=["dim-label", "caption"], visible=wrong or not pam.available())
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.append(entry)
    box.append(hint)
    state = {"busy": False}

    def checked(ok_, dlg, closed):
        state["busy"] = False
        entry.set_sensitive(True)
        if ok_:
            if not closed:
                dlg.close() if hasattr(dlg, "close") else dlg.destroy()
            on_ok()
        elif closed:
            ask_password(heading, body, on_ok, parent, ok, wrong=True)
        else:
            hint.set_label("Wrong password")
            hint.set_visible(True)
            entry.set_text("")
            entry.grab_focus()

    def check(closed):
        pw = entry.get_text()
        if not pw or state["busy"]:
            return
        state["busy"] = True
        entry.set_sensitive(False)
        pam.check_async(pw, lambda r: checked(r, dlg, closed))

    dlg = alert(heading, body, [("cancel", "Cancel", ""), ("ok", ok, "default")],
                lambda rid: rid == "ok" and check(True), parent=parent)
    dlg.set_extra_child(box)
    entry.connect("activate", lambda _e: check(False))
    GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
    dlg.entry, dlg.hint = entry, hint                   # (tests)
    return dlg
