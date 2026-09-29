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
from gi.repository import Adw  # noqa: E402

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
window.messagedialog.sonata-alert checkbutton { font-size: %(text_small)s; }
""")

_STYLE = {"destructive": Adw.ResponseAppearance.DESTRUCTIVE,
          "default": Adw.ResponseAppearance.SUGGESTED}


# libadwaita >= 1.5: AlertDialog (MessageDialog is deprecated since 1.6)
_MODERN = hasattr(Adw, "AlertDialog")


def alert(heading: str, body: str, responses, on_response=None, parent=None,
          check: str = None):
    if _MODERN:
        dlg = Adw.AlertDialog(heading=heading, body=body)
        dlg.add_css_class("sonata-alert")
    else:
        dlg = Adw.MessageDialog(heading=heading, body=body, transient_for=parent,
                                css_classes=["sonata-alert"])
    box = None
    if check:
        from gi.repository import Gtk
        box = Gtk.CheckButton(label=check, halign=Gtk.Align.CENTER)
        dlg.set_extra_child(box)
    default = None
    for rid, text, style in responses:
        dlg.add_response(rid, text)
        if style in _STYLE:
            dlg.set_response_appearance(rid, _STYLE[style])
        if style != "destructive":
            default = rid if default is None or style == "default" else default
    if default:
        dlg.set_default_response(default)
        dlg.set_close_response(responses[0][0])
    if on_response:
        if box is not None:
            dlg.connect("response", lambda _d, rid: on_response(rid, box.get_active()))
        else:
            dlg.connect("response", lambda _d, rid: on_response(rid))
    if _MODERN:
        dlg.present(parent)
    else:
        dlg.present()
    return dlg
