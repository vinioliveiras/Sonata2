"""The Anthropic API key, kept in the keyring (the freedesktop Secret
Service: GNOME's keyring or KeePassXC, see sonata2/keyring.py), never in a
plain file. The ANTHROPIC_API_KEY environment variable is used when the
keyring has none.

    keystore.load(callback)        # callback(key or "") on the main loop
    keystore.save(key, callback)   # callback(ok: bool); "" removes it
    keystore.cached()              # the last key read or saved ("" if none)

Calls are asynchronous: a locked keyring may ask for its password."""
import os

SCHEMA_NAME = "io.github.vinioliveiras.sonata2.assistant"
ATTRS = {"service": "anthropic-api"}
LABEL = "Assistant: Anthropic API key"

_cache = {"key": None}


def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    return Secret


def _schema(Secret):
    return Secret.Schema.new(SCHEMA_NAME, Secret.SchemaFlags.NONE, {"service": Secret.SchemaAttributeType.STRING})


def _env() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def cached() -> str:
    return _cache["key"] if _cache["key"] is not None else _env()


def load(callback) -> None:
    if _cache["key"] is not None:
        callback(_cache["key"])
        return
    try:
        Secret = _secret()
    except (ImportError, ValueError):
        _cache["key"] = _env()
        callback(_cache["key"])
        return

    def done(_src, res):
        try:
            key = Secret.password_lookup_finish(res) or ""
        except Exception:               # noqa: BLE001  (no keyring running: the environment's key)
            key = ""
        _cache["key"] = key.strip() or _env()
        callback(_cache["key"])
    Secret.password_lookup(_schema(Secret), ATTRS, None, done)


def save(key: str, callback=None) -> None:
    key = key.strip()
    try:
        Secret = _secret()
    except (ImportError, ValueError):
        if callback:
            callback(False)
        return

    def done(_src, res):
        try:
            (Secret.password_clear_finish if not key else Secret.password_store_finish)(res)
            ok = True
        except Exception:               # noqa: BLE001
            ok = False
        if ok:
            _cache["key"] = key or _env()
        if callback:
            callback(ok)
    if key:
        Secret.password_store(_schema(Secret), ATTRS, Secret.COLLECTION_DEFAULT, LABEL, key, None, done)
    else:
        Secret.password_clear(_schema(Secret), ATTRS, None, done)
