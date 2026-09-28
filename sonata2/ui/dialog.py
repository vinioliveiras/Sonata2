"""Alerts (macOS Big Sur alert: bold heading, body, buttons at the bottom).

    dialog.alert("Are you sure you want to permanently erase the items in the Trash?",
                 "You can't undo this action.",
                 [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")],
                 on_response=lambda rid: ...)

Default button (Return): the response styled "default"; otherwise the first
non-destructive one (Cancel), as on macOS for destructive alerts."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
window.messagedialog.sonata-alert, window.messagedialog.sonata-alert > contents {
  border-radius: %(r_dialog)s; background-color: %(window_bg)s; color: %(label)s;
  font-family: %(font)s;
}
window.messagedialog.sonata-alert .heading { font-size: %(text_body)s; font-weight: 700; }
window.messagedialog.sonata-alert .body { font-size: %(text_small)s; color: %(label_secondary)s; }
""")

_STYLE = {"destructive": Adw.ResponseAppearance.DESTRUCTIVE,
          "default": Adw.ResponseAppearance.SUGGESTED}


def alert(heading: str, body: str, responses, on_response=None, parent=None) -> Adw.MessageDialog:
    dlg = Adw.MessageDialog(heading=heading, body=body, transient_for=parent,
                            css_classes=["sonata-alert"])
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
        dlg.connect("response", lambda _d, rid: on_response(rid))
    dlg.present()
    return dlg
