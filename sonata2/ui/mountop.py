"""Password and question prompts for mounting (an encrypted disk, a network
share, "the disk is busy"), in Sonata's alert instead of GTK's own dialog.

    vol.mount(Gio.MountMountFlags.NONE, ui.mountop.MountOperation(window), None, done)

A Gio.MountOperation: GIO asks through its signals and waits for reply().
Each handler stops the signal's default (it would answer "unhandled" at
once) and answers when the alert closes."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import controls, dialog  # noqa: E402


def split_message(message: str) -> tuple:
    """GIO's "heading\\nbody" -> (heading, body)."""
    lines = [ln.strip() for ln in (message or "").splitlines() if ln.strip()]
    if not lines:
        return "Enter the password", ""
    return lines[0], " ".join(lines[1:])


class MountOperation(Gio.MountOperation):
    def __init__(self, parent: Gtk.Window = None):
        super().__init__()
        self.parent = parent
        self.dialog = None                   # the alert on screen (tests)
        self.connect("ask-password", self._ask_password)
        self.connect("ask-question", self._ask_question)
        self.connect("show-processes", self._show_processes)

    # -- password (encrypted volumes, network shares) -----------------------------------------
    def _ask_password(self, op, message, default_user, default_domain, flags):
        op.stop_emission_by_name("ask-password")
        heading, body = split_message(message)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        user = domain = None
        if flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED:
            anon = Gtk.CheckButton(label="Connect as a guest", halign=Gtk.Align.START)
            box.append(anon)
        else:
            anon = None
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            user = controls.text_field(default_user or "", "Name")
            user.set_activates_default(True)
            box.append(user)
        if flags & Gio.AskPasswordFlags.NEED_DOMAIN:
            domain = controls.text_field(default_domain or "", "Domain")
            domain.set_activates_default(True)
            box.append(domain)
        pw = None
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            pw = controls.text_field(placeholder="Password", secret=True)
            pw.set_property("activates-default", True)
            box.append(pw)
        remember = None
        if flags & Gio.AskPasswordFlags.SAVING_SUPPORTED:
            remember = Gtk.CheckButton(label="Remember this password in my keyring", halign=Gtk.Align.START)
            box.append(remember)

        def answered(rid):
            self.dialog = None
            if rid != "ok":
                op.reply(Gio.MountOperationResult.ABORTED)
                return
            if anon is not None:
                op.set_anonymous(anon.get_active())
            if user is not None:
                op.set_username(user.get_text())
            if domain is not None:
                op.set_domain(domain.get_text())
            if pw is not None:
                op.set_password(pw.get_text())
            op.set_password_save(Gio.PasswordSave.PERMANENTLY if remember is not None and remember.get_active()
                                 else Gio.PasswordSave.NEVER)
            op.reply(Gio.MountOperationResult.HANDLED)
        self.dialog = dialog.alert(heading, body, [("cancel", "Cancel", ""), ("ok", "Unlock" if pw and not user
                                                                               else "Connect", "default")],
                                   answered, parent=self.parent)
        self.dialog.set_extra_child(box)
        first = user or pw
        if first is not None:
            GLib.idle_add(lambda: (first.grab_focus(), False)[1])
        return True

    # -- questions ("the disk contains a hibernated system", ...) ----------------------------------
    def _ask_question(self, op, message, choices):
        op.stop_emission_by_name("ask-question")
        return self._question(op, message, choices)

    def _question(self, op, message, choices):
        heading, body = split_message(message)
        responses = [(str(i), c, "default" if i == len(choices) - 1 else "") for i, c in enumerate(choices)]

        def answered(rid):
            self.dialog = None
            if not rid.isdigit():
                op.reply(Gio.MountOperationResult.ABORTED)
                return
            op.set_choice(int(rid))
            op.reply(Gio.MountOperationResult.HANDLED)
        # Escape aborts: never picks a choice ("Unmount Anyway" comes first)
        self.dialog = dialog.alert(heading, body, responses or [("0", "OK", "default")], answered,
                                   parent=self.parent, close="close")
        return True

    # -- "the disk is in use" while ejecting ---------------------------------------------------------
    def _show_processes(self, op, message, processes, choices):
        op.stop_emission_by_name("show-processes")
        if self.dialog is not None:                  # asked again while open (the list changed)
            return True
        return self._question(op, message, choices)
