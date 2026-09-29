"""Password check through PAM (ctypes, no extra package), for the lock
screen. Same idea as python-pam: one conversation that answers the
password prompt. Run it off the GTK thread (a wrong password makes PAM
wait ~2 s on purpose)."""
import ctypes
import ctypes.util
import os
from ctypes import CFUNCTYPE, POINTER, Structure, byref, c_char_p, c_int, c_size_t, c_void_p, cast, sizeof

PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
SERVICES = ("sonata-lock", "system-local-login", "login", "system-auth", "other")


class PamMessage(Structure):
    _fields_ = [("msg_style", c_int), ("msg", c_char_p)]


class PamResponse(Structure):
    _fields_ = [("resp", c_char_p), ("resp_retcode", c_int)]


CONV = CFUNCTYPE(c_int, c_int, POINTER(POINTER(PamMessage)), POINTER(POINTER(PamResponse)), c_void_p)


class PamConv(Structure):
    _fields_ = [("conv", CONV), ("appdata_ptr", c_void_p)]


_lib = None


def _pam():
    global _lib
    if _lib is None:
        path = ctypes.util.find_library("pam")
        if not path:
            return None
        _lib = ctypes.CDLL(path)
        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        _lib._calloc = libc.calloc
        _lib._calloc.restype = c_void_p
        _lib._calloc.argtypes = [c_size_t, c_size_t]
        _lib._strdup = libc.strdup
        _lib._strdup.restype = c_void_p
        _lib._strdup.argtypes = [c_char_p]
        _lib.pam_start.restype = c_int
        _lib.pam_start.argtypes = [c_char_p, c_char_p, POINTER(PamConv), POINTER(c_void_p)]
        _lib.pam_authenticate.restype = c_int
        _lib.pam_authenticate.argtypes = [c_void_p, c_int]
        _lib.pam_setcred.restype = c_int
        _lib.pam_setcred.argtypes = [c_void_p, c_int]
        _lib.pam_end.restype = c_int
        _lib.pam_end.argtypes = [c_void_p, c_int]
    return _lib


def available() -> bool:
    return _pam() is not None


def _service() -> str:
    for s in SERVICES:
        if os.path.exists(os.path.join("/etc/pam.d", s)) or os.path.exists(os.path.join("/usr/lib/pam.d", s)):
            return s
    return "login"


def authenticate(user: str, password: str) -> bool:
    lib = _pam()
    if lib is None:
        return False
    pw = password.encode()

    @CONV
    def conv(n, msgs, resp, _data):
        # PAM frees the responses: allocate them with libc
        arr = lib._calloc(n, sizeof(PamResponse))
        if not arr:
            return 5          # PAM_BUF_ERR
        resp[0] = cast(arr, POINTER(PamResponse))
        for i in range(n):
            if msgs[i].contents.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                resp[0][i].resp = cast(lib._strdup(pw), c_char_p)
                resp[0][i].resp_retcode = 0
        return 0

    handle = c_void_p()
    c = PamConv(conv, None)
    if lib.pam_start(_service().encode(), user.encode(), byref(c), byref(handle)) != 0:
        return False
    rc = lib.pam_authenticate(handle, 0)
    if rc == 0:
        lib.pam_setcred(handle, 0x8)          # PAM_REINITIALIZE_CRED (refresh Kerberos etc.)
    lib.pam_end(handle, rc)
    return rc == 0
