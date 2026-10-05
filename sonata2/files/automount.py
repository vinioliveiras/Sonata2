"""Every disk mounted and shown (Vini's call): internal partitions (the
Windows one of a dual boot, a data disk...) and anything plugged in are
mounted as soon as Files runs (it's resident from login) and when they
appear. System partitions (EFI, swap, recovery...) never show up here:
the volume monitor (udisks) already leaves them out.

Mounting an internal disk is a system action: install.sh adds a polkit
rule so the logged-in administrator isn't asked for a password each time.
A Windows disk left dirty (Fast Startup, hibernation) is mounted read-only
(rodisk.py); one that can't be mounted at all is left alone and logged."""
from gi.repository import Gio

_monitor = None
_tried = set()


def _mount(vol) -> None:
    if vol.get_mount() is not None or not vol.can_mount():
        return
    key = vol.get_identifier("unix-device") or vol.get_uuid() or vol.get_name()
    if key in _tried:                   # failed once this session: don't insist
        return
    _tried.add(key)

    def done(v, res):
        try:
            v.mount_finish(res)
            _tried.discard(key)
        except Exception as e:          # GLib.Error: hibernated NTFS, cancelled password...
            msg = getattr(e, "message", str(e))
            from . import rodisk
            dev = v.get_identifier("unix-device")
            if dev and rodisk.try_readonly(dev, msg):        # a Windows disk left dirty: shown, read-only
                rodisk.mount_readonly(dev, lambda path, err: print(
                    f"sonata2-files: {v.get_name()} " + ("mounted read-only (dirty)" if path is not None
                                                          else f"couldn't be mounted read-only: {err}"), flush=True))
                return
            print(f"sonata2-files: couldn't mount {v.get_name()}: {msg}", flush=True)
    from .. import ui                    # an encrypted disk plugged in: Sonata's password alert
    vol.mount(Gio.MountMountFlags.NONE, ui.mountop.MountOperation(None), None, done)


def start() -> None:
    global _monitor
    if _monitor is not None:
        return
    _monitor = Gio.VolumeMonitor.get()
    for vol in _monitor.get_volumes():
        _mount(vol)
    _monitor.connect("volume-added", lambda _m, vol: _mount(vol))
