"""Python bindings for a Wayland protocol XML in wl/protocols (pywayland's
scanner), made once into the cache and imported from there.

    mod = scan.load("virtual-keyboard-unstable-v1.xml", "virtual_keyboard_unstable_v1")
"""
import importlib.util
import os
import shutil
import tempfile

HERE = os.path.join(os.path.dirname(__file__), "protocols")
CORE = ("wl_seat", "wl_output", "wl_pointer", "wl_surface", "wl_keyboard")


def load(xml: str, proto: str):
    import pywayland
    from pywayland.scanner import Protocol
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                         "sonata2", "pywayland-" + pywayland.__version__)
    path = os.path.join(cache, proto + ".py")
    if not os.path.exists(path):
        os.makedirs(cache, exist_ok=True)
        # made in a folder of its own and renamed into place whole: the scanner
        # writes the module in several appends, and a half one left in the
        # cache (killed, or read by another process meanwhile) was imported
        # from then on -- no virtual keyboard or pointer until it was deleted
        work = tempfile.mkdtemp(prefix=".scan-", dir=cache)
        try:
            p = Protocol.parse_file(os.path.join(HERE, xml))
            p.output(work, {i.name: p.name for i in p.interface} | {c: "wayland" for c in CORE})
            out = os.path.join(work, proto + ".py")
            with open(out, encoding="utf-8") as f:
                src = f.read()
            with open(out, "w", encoding="utf-8") as f:            # core interfaces from pywayland
                f.write(src.replace("from .wayland import", "from pywayland.protocol.wayland import"))
            os.replace(out, path)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    spec = importlib.util.spec_from_file_location("sonata2_" + proto, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
