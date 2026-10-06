"""Python bindings for a Wayland protocol XML in wl/protocols (pywayland's
scanner), made once into the cache and imported from there.

    mod = scan.load("virtual-keyboard-unstable-v1.xml", "virtual_keyboard_unstable_v1")
"""
import importlib.util
import os

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
        p = Protocol.parse_file(os.path.join(HERE, xml))
        p.output(cache, {i.name: p.name for i in p.interface} | {c: "wayland" for c in CORE})
        with open(path, encoding="utf-8") as f:
            src = f.read()
        with open(path, "w", encoding="utf-8") as f:           # core interfaces from pywayland
            f.write(src.replace("from .wayland import", "from pywayland.protocol.wayland import"))
    spec = importlib.util.spec_from_file_location("sonata2_" + proto, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
