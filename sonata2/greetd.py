"""greetd IPC client (the login manager Sonata's login screen runs under).

Protocol (greetd-ipc(7)): over the unix socket in $GREETD_SOCK, each
message is a 32-bit native-endian length followed by that much JSON.

    create_session {username}           -> auth_message | success | error
    post_auth_message_response {resp}   -> auth_message | success | error
    start_session {cmd, env}            -> success | error
    cancel_session                      -> success

Calls block (PAM may take ~2 s on a wrong password): run them off the GTK
thread. `Fake` answers like greetd for previews and tests (password
"sonata")."""
import json
import os
import socket
import struct


class GreetdError(Exception):
    def __init__(self, error_type: str, description: str):
        super().__init__(description)
        self.error_type = error_type          # "auth_error" (wrong password) or "error"


class Client:
    def __init__(self, path: str = None):
        path = path or os.environ.get("GREETD_SOCK")
        if not path:                  # not under greetd: an error the login screen shows, not a KeyError
            raise GreetdError("error", "Not running under greetd (GREETD_SOCK is not set)")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(path)
        except OSError:
            self.sock.close()
            raise

    def _call(self, msg: dict) -> dict:
        data = json.dumps(msg).encode()
        self.sock.sendall(struct.pack("=I", len(data)) + data)
        size = struct.unpack("=I", self._read(4))[0]
        reply = json.loads(self._read(size))
        if reply.get("type") == "error":
            raise GreetdError(reply.get("error_type", "error"), reply.get("description", ""))
        return reply

    def _read(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise GreetdError("error", "greetd closed the connection")
            buf += chunk
        return buf

    def create_session(self, username: str) -> dict:
        return self._call({"type": "create_session", "username": username})

    def answer(self, response) -> dict:
        return self._call({"type": "post_auth_message_response", "response": response})

    def start_session(self, cmd: list, env: list) -> dict:
        return self._call({"type": "start_session", "cmd": cmd, "env": env})

    def cancel(self) -> None:
        try:
            self._call({"type": "cancel_session"})
        except (GreetdError, OSError):
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Fake:
    """greetd stand-in: any user, password "sonata"."""

    def __init__(self, *_a):
        self.started = None

    def create_session(self, username):
        return {"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "}

    def answer(self, response):
        import time
        if response != "sonata":
            time.sleep(1)
            raise GreetdError("auth_error", "Authentication failed")
        return {"type": "success"}

    def start_session(self, cmd, env):
        self.started = (cmd, env)
        return {"type": "success"}

    def cancel(self):
        pass

    def close(self):
        pass


def login(client, username: str, password: str) -> None:
    """Authenticate `username` (raises GreetdError). Prompts other than the
    password (e.g. an info message) are acknowledged; a second secret
    prompt (2FA) isn't supported yet and fails."""
    # a wrong password leaves greetd's session half-made: always start clean,
    # and cancel on any failure (else the next try answers the old session)
    client.cancel()
    try:
        _login(client, username, password)
    except GreetdError:
        client.cancel()
        raise


def _login(client, username, password):
    reply = client.create_session(username)
    answered = False
    while reply.get("type") == "auth_message":
        kind = reply.get("auth_message_type")
        if kind == "secret" and not answered:
            reply = client.answer(password)
            answered = True
        elif kind in ("info", "error"):
            reply = client.answer(None)
        elif kind == "secret":                  # asked again: the password was wrong
            raise GreetdError("auth_error", "Authentication failed")
        else:
            client.cancel()
            raise GreetdError("error", reply.get("auth_message", "Unsupported login step"))
