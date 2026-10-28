"""Locked folders in Files (Vini): Lock Folder… encrypts a folder with
gocryptfs -- its files unreadable without the password, in any app, the
terminal too. The login password is the key (asked and checked first, like
Lock App). The folder itself stays where it was: closed, it is empty (a mount
point); opened with the password, gocryptfs shows the files in it again.

    ~/Docs/Secret/                     the folder (empty while closed)
    ~/Docs/.Secret.sonata-vault/       its encrypted files (gocryptfs.conf ...)

    available()                        gocryptfs and fusermount are installed
    is_vault(path); is_open(path)
    lock(path, password)               encrypt it (files copied in, checked, then
                                       the originals removed) -> (ok, error)
    open(path, password) -> (ok, error)   ("wrong" for a wrong password)
    close(path) -> bool
    unlock(path, password)             decrypt it for good (back to a plain folder)

Every step that could lose a file copies first and checks what was copied
before anything is removed; a failure leaves the originals where they were.
The password goes to gocryptfs through a file only this user can read, in
the runtime directory (tmpfs), removed at once.
"""
import builtins
import contextlib
import os
import shutil
import subprocess
import tempfile

SUFFIX = ".sonata-vault"
WRONG = "wrong"               # open's / unlock's error for a wrong password (gocryptfs: exit 12)
MOUNT_WAIT_S = 20


def available() -> bool:
    return bool(shutil.which("gocryptfs") and (shutil.which("fusermount3") or shutil.which("fusermount")))


def cipher_dir(path: str) -> str:
    path = os.path.abspath(path.rstrip("/"))
    parent, name = os.path.split(path)
    return os.path.join(parent, "." + name + SUFFIX)


def is_vault(path: str) -> bool:
    return bool(path) and os.path.isfile(os.path.join(cipher_dir(path), "gocryptfs.conf"))


def is_open(path: str) -> bool:
    return is_vault(path) and os.path.ismount(os.path.abspath(path.rstrip("/")))


def can_lock(path: str) -> bool:
    """A folder of the user's own, not a vault yet, not a mount point, not
    their home or a system folder."""
    if not path or not os.path.isdir(path) or os.path.islink(path):
        return False
    path = os.path.abspath(path.rstrip("/"))
    home = os.path.expanduser("~")
    return (path.startswith(home + os.sep) and not is_vault(path) and not os.path.ismount(path)
            and os.access(os.path.dirname(path), os.W_OK) and not path.endswith(SUFFIX))


@contextlib.contextmanager
def _passfile(password: str):
    run = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    fd, name = tempfile.mkstemp(prefix="sonata2-vault-", dir=run)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, password.encode())
        os.close(fd)
        yield name
    finally:
        with contextlib.suppress(OSError):
            os.remove(name)


def _run(cmd, timeout=MOUNT_WAIT_S):
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)


def _mount(cipher: str, at: str, password: str):
    with _passfile(password) as pf:
        r = _run(["gocryptfs", "-q", "-passfile", pf, cipher, at])
    if r.returncode == 12:
        return False, WRONG
    if r.returncode != 0:
        return False, (r.stderr or r.stdout or "gocryptfs").strip()
    return True, None


def _umount(at: str) -> bool:
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    if not tool:
        return False
    return _run([tool, "-u", at]).returncode == 0


def _listing(root: str) -> dict:
    """{relative path: size or None for a folder} -- what a copy must hold."""
    out = {}
    for dirpath, dirs, files in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for d in dirs:
            out[os.path.normpath(os.path.join(rel, d))] = None
        for f in files:
            p = os.path.join(dirpath, f)
            out[os.path.normpath(os.path.join(rel, f))] = None if os.path.islink(p) else os.path.getsize(p)
    return out


def _copy_into(src: str, dst: str) -> bool:
    """Everything in src copied into dst (links as links); True when dst then
    holds all of it, at the same sizes."""
    for name in os.listdir(src):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.islink(s):
            shutil.copytree(s, d, symlinks=True, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d, follow_symlinks=False)
    want = _listing(src)
    have = _listing(dst)
    return all(k in have and have[k] == v for k, v in want.items())


def _empty(path: str) -> None:
    for name in os.listdir(path):
        p = os.path.join(path, name)
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


def lock(path: str, password: str):
    """Encrypt the folder: (True, None), or (False, why) with nothing changed."""
    if not available():
        return False, "gocryptfs"
    if not can_lock(path):
        return False, "folder"
    path = os.path.abspath(path.rstrip("/"))
    cipher = cipher_dir(path)
    os.makedirs(cipher)
    with _passfile(password) as pf:
        r = _run(["gocryptfs", "-init", "-q", "-passfile", pf, cipher], timeout=60)
    if r.returncode != 0:
        shutil.rmtree(cipher, ignore_errors=True)
        return False, (r.stderr or "gocryptfs -init").strip()
    stage = tempfile.mkdtemp(prefix="sonata2-vault-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    ok, err = _mount(cipher, stage, password)
    if not ok:
        shutil.rmtree(cipher, ignore_errors=True)
        os.rmdir(stage)
        return False, err
    try:
        copied = _copy_into(path, stage)
    except OSError as e:
        copied, err = False, str(e)
    _umount(stage)
    with contextlib.suppress(OSError):
        os.rmdir(stage)
    if not copied:                               # the originals stay: the vault goes
        shutil.rmtree(cipher, ignore_errors=True)
        return False, err or "copy"
    _empty(path)                                 # only now: they're all in the vault
    return True, None


def open(path: str, password: str):  # noqa: A001  (the vault's verb)
    """Its files shown in the folder again (mounted there)."""
    if is_open(path):
        return True, None
    if not is_vault(path):
        return False, "folder"
    path = os.path.abspath(path.rstrip("/"))
    if os.listdir(path):                         # (something was put in the closed folder)
        return False, "not empty"
    return _mount(cipher_dir(path), path, password)


def close(path: str) -> bool:
    """Its files out of sight again (unmounted). False: in use (a file open)."""
    if not is_open(path):
        return True
    return _umount(os.path.abspath(path.rstrip("/")))


def open_vaults() -> list:
    """The locked folders open now (from /proc/self/mounts)."""
    out = []
    try:
        with builtins.open("/proc/self/mounts") as f:      # (open() here is the vault's)
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "fuse.gocryptfs" and parts[0].endswith(SUFFIX):
                    out.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return out


def close_all() -> None:
    """Every locked folder closed (the session ends, the screen locks)."""
    for path in open_vaults():
        close(path)


def unlock(path: str, password: str):
    """Decrypt it for good: a plain folder again, its vault removed."""
    if not is_vault(path):
        return False, "folder"
    path = os.path.abspath(path.rstrip("/"))
    if is_open(path) and not close(path):
        return False, "busy"
    if os.listdir(path):
        return False, "not empty"
    stage = tempfile.mkdtemp(prefix="sonata2-vault-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
    ok, err = _mount(cipher_dir(path), stage, password)
    if not ok:
        os.rmdir(stage)
        return False, err
    try:
        copied = _copy_into(stage, path)
    except OSError as e:
        copied, err = False, str(e)
    _umount(stage)
    with contextlib.suppress(OSError):
        os.rmdir(stage)
    if not copied:                               # the vault stays: what was copied goes
        _empty(path)
        return False, err or "copy"
    shutil.rmtree(cipher_dir(path), ignore_errors=True)
    return True, None
