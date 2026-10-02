"""The Anthropic Messages API, streamed, without extra dependencies (urllib
in a worker thread; callbacks come back on the GTK main loop).

    job = api.stream(key, model, messages, on_text, on_done, on_error)
    job.cancel()                      # the Stop button

on_text(str) gets each piece of text as it arrives, on_done() once at the
end, on_error(str) with a readable message instead of on_done.

The API key: the ANTHROPIC_API_KEY environment variable for now (Settings
come in the next step). The model: the configured one, else the newest
the account offers (GET /v1/models, cached for the process)."""
import json
import os
import threading
import urllib.error
import urllib.request

API = "https://api.anthropic.com/v1"
VERSION = "2023-06-01"
MAX_TOKENS = 8192
TIMEOUT = 60          # s without a byte (the stream sends pings meanwhile)

_models = None        # [(id, display name)], newest first


def api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _headers(key: str) -> dict:
    return {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json",
            "user-agent": "Sonata-Assistant"}


def _error_text(e: Exception) -> str:
    """A short message for the chat from an HTTP or network error."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except (ValueError, OSError, AttributeError):
            msg = ""
        if e.code == 401:
            return "The API key was not accepted."
        if e.code == 429:
            return msg or "Too many requests. Try again in a moment."
        if e.code in (500, 529) and not msg:
            return "The service is busy. Try again in a moment."
        return msg or f"The request failed (HTTP {e.code})."
    if isinstance(e, urllib.error.URLError):
        return "Could not reach the service. Check your connection."
    return str(e) or e.__class__.__name__


def models(key: str) -> list:
    """[(id, display name)] the key can use, newest first (blocking)."""
    global _models
    if _models is None:
        req = urllib.request.Request(API + "/models?limit=100", headers=_headers(key))
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        _models = [(m["id"], m.get("display_name") or m["id"]) for m in data.get("data", []) if m.get("id")]
    return _models


def parse_sse(lines):
    """Yield (event, data dict) from the lines of a server-sent event stream."""
    event, data = None, []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n") if isinstance(raw, bytes) else raw.rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield event, json.loads("\n".join(data))
                except ValueError:
                    pass
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        try:
            yield event, json.loads("\n".join(data))
        except ValueError:
            pass


def merge_turns(messages) -> list:
    """The API wants user/assistant turns alternating: a message sent after
    a failed answer joins the one before it."""
    out = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + m["content"]
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


class Job:
    """One streamed answer. cancel() stops it; no callback runs after."""

    def __init__(self, key, model, messages, on_text, on_done, on_error, system=None):
        self.cancelled = False
        self._resp = None
        self._args = (key, model, messages, system)
        self._cb = (on_text, on_done, on_error)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def cancel(self) -> None:
        self.cancelled = True
        r = self._resp
        if r is not None:
            try:
                r.close()           # wakes the blocked read
            except Exception:       # noqa: BLE001  (already closed)
                pass

    def _post(self, fn, *a) -> None:
        from gi.repository import GLib

        def call():
            if not self.cancelled:
                fn(*a)
            return False
        GLib.idle_add(call)

    def _run(self) -> None:
        key, model, messages, system = self._args
        on_text, on_done, on_error = self._cb
        try:
            if not model:
                found = models(key)
                if not found:
                    raise RuntimeError("No model is available for this API key.")
                model = found[0][0]
            body = {"model": model, "max_tokens": MAX_TOKENS, "stream": True,
                    "messages": merge_turns(messages)}
            if system:
                body["system"] = system
            req = urllib.request.Request(API + "/messages", data=json.dumps(body).encode("utf-8"),
                                         headers=_headers(key), method="POST")
            self._resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            if self.cancelled:
                return
            buf, last = [], [0.0]
            for event, data in parse_sse(self._resp):
                if self.cancelled:
                    return
                if event == "error" or data.get("type") == "error":
                    raise RuntimeError(data.get("error", {}).get("message") or "The answer stopped with an error.")
                if data.get("type") == "content_block_delta" and data.get("delta", {}).get("type") == "text_delta":
                    buf.append(data["delta"].get("text", ""))
                    self._flush(buf, last, on_text)
                elif data.get("type") == "message_stop":
                    break
            self._flush(buf, last, on_text, force=True)
            self._post(on_done)
        except Exception as e:                      # noqa: BLE001  (shown in the chat)
            if not self.cancelled:
                self._post(on_error, _error_text(e))
        finally:
            if self._resp is not None:
                try:
                    self._resp.close()
                except Exception:                   # noqa: BLE001
                    pass

    def _flush(self, buf, last, on_text, force=False) -> None:
        """Hand text to the UI at most ~30 times a second (one relayout per batch)."""
        import time
        now = time.monotonic()
        if buf and (force or now - last[0] >= 0.033):
            self._post(on_text, "".join(buf))
            buf.clear()
            last[0] = now


def stream(key, model, messages, on_text, on_done, on_error, system=None) -> Job:
    return Job(key, model, messages, on_text, on_done, on_error, system)
