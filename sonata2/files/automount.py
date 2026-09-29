"""Every disk mounted and shown (Vini's call): internal partitions (the
Windows one of a dual boot, a data disk...) and anything plugged in are
mounted as soon as Files runs (it's resident from login) and when they
appear. System partitions (EFI, swap, recovery...) never show up here:
the volume monitor (udisks) already leaves them out.

Mounting an internal disk is a system action: install.sh adds a polkit
rule so the logged-in administrator isn't asked for a password each time.
A disk that can't be mounted (Windows hibernated / Fast Startup) is left
alone and logged."""
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
            print(f"sonata2-files: couldn't mount {v.get_name()}: {getattr(e, 'message', e)}", flush=True)
    vol.mount(Gio.MountMountFlags.NONE, Gio.MountOperation(), None, done)


def start() -> None:
    global _monitor
    if _monitor is not None:
        return
    _monitor = Gio.VolumeMonitor.get()
    for vol in _monitor.get_volumes():
        _mount(vol)
    _monitor.connect("volume-added", lambda _m, vol: _mount(vol))
