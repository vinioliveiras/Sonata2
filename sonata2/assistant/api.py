"""The Anthropic Messages API, streamed, without extra dependencies (urllib
in a worker thread; callbacks come back on the GTK main loop).

    job = api.stream(key, model, messages, on_text, on_done, on_error)
    job.cancel()                      # the Stop button

on_text(str) gets each piece of text as it arrives, on_done(blocks,
stop_reason) once at the end (the answer's content blocks: text and
tool_use, with their parsed input), on_error(str) with a readable message
instead of on_done. tools: definitions (tools.py) Claude may call.

The API key comes from keystore.py. The model: the configured one, else the newest
the account offers (GET /v1/models, cached for the process)."""
import json
import threading
import urllib.error
import urllib.request

API = "https://api.anthropic.com/v1"
VERSION = "2023-06-01"
MAX_TOKENS = 8192
TIMEOUT = 60          # s without a byte (the stream sends pings meanwhile)

_models = None        # (key, [(id, display name)] newest first)


def api_key() -> str:
    from . import keystore
    return keystore.cached()


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
    """[(id, display name)] the key can use, newest first (blocking; cached
    per key)."""
    global _models
    if _models is None or _models[0] != key:
        req = urllib.request.Request(API + "/models?limit=100", headers=_headers(key))
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        _models = (key, [(m["id"], m.get("display_name") or m["id"]) for m in data.get("data", []) if m.get("id")])
    return _models[1]


def models_async(key: str, callback) -> None:
    """models() in a thread: callback(list or None on error) on the main loop."""
    from gi.repository import GLib

    def work():
        try:
            found = models(key)
        except Exception:                           # noqa: BLE001
            found = None
        GLib.idle_add(lambda: (callback(found), False)[1])
    threading.Thread(target=work, daemon=True).start()


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


def _blocks(content) -> list:
    return [{"type": "text", "text": content}] if isinstance(content, str) else list(content)


def merge_turns(messages) -> list:
    """The API wants user/assistant turns alternating: a message sent after
    a failed answer joins the one before it. Content is a string or a list
    of blocks (text, tool_use, tool_result)."""
    out = []
    for m in messages:
        if out and out[-1]["role"] == m["role"]:
            prev = out[-1]["content"]
            if isinstance(prev, str) and isinstance(m["content"], str):
                out[-1]["content"] = prev + "\n\n" + m["content"]
            else:
                out[-1]["content"] = _blocks(prev) + _blocks(m["content"])
        else:
            out.append({"role": m["role"], "content": m["content"] if isinstance(m["content"], str)
                        else list(m["content"])})
    return out


def text_of(content) -> str:
    """The text of a message (its text blocks)."""
    if isinstance(content, str):
        return content
    return "\n\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


class Job:
    """One streamed answer. cancel() stops it; no callback runs after."""

    def __init__(self, key, model, messages, on_text, on_done, on_error, system=None, tools=None):
        self.cancelled = False
        self._resp = None
        self._args = (key, model, messages, system, tools)
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
        key, model, messages, system, tools = self._args
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
            if tools:
                body["tools"] = tools
            req = urllib.request.Request(API + "/messages", data=json.dumps(body).encode("utf-8"),
                                         headers=_headers(key), method="POST")
            self._resp = urllib.request.urlopen(req, timeout=TIMEOUT)
            if self.cancelled:
                return
            buf, last = [], [0.0]
            blocks, partial, stop = {}, {}, None       # index -> block, index -> streamed JSON of a tool's input
            for event, data in parse_sse(self._resp):
                if self.cancelled:
                    return
                kind = data.get("type")
                if event == "error" or kind == "error":
                    raise RuntimeError(data.get("error", {}).get("message") or "The answer stopped with an error.")
                if kind == "content_block_start":
                    blk = dict(data.get("content_block") or {})
                    if blk.get("type") in ("text", "tool_use"):
                        blocks[data.get("index", len(blocks))] = blk
                elif kind == "content_block_delta":
                    i, d = data.get("index", 0), data.get("delta", {})
                    if d.get("type") == "text_delta":
                        blk = blocks.setdefault(i, {"type": "text", "text": ""})
                        blk["text"] = blk.get("text", "") + d.get("text", "")
                        buf.append(d.get("text", ""))
                        self._flush(buf, last, on_text)
                    elif d.get("type") == "input_json_delta":
                        partial[i] = partial.get(i, "") + d.get("partial_json", "")
                elif kind == "message_delta":
                    stop = data.get("delta", {}).get("stop_reason") or stop
                elif kind == "message_stop":
                    break
            self._flush(buf, last, on_text, force=True)
            out = []
            for i in sorted(blocks):
                blk = blocks[i]
                if blk["type"] == "tool_use":
                    try:
                        blk["input"] = json.loads(partial[i]) if partial.get(i) else (blk.get("input") or {})
                    except ValueError:
                        blk["input"] = {}
                    out.append({k: blk[k] for k in ("type", "id", "name", "input") if k in blk})
                elif blk.get("text"):
                    out.append({"type": "text", "text": blk["text"]})
            self._post(on_done, out, stop)
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


def stream(key, model, messages, on_text, on_done, on_error, system=None, tools=None) -> Job:
    return Job(key, model, messages, on_text, on_done, on_error, system, tools)
