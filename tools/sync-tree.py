#!/usr/bin/env python3
"""sync-tree.py SRC DST: make DST the same as SRC, in place.

install.sh used to delete the installed copy and copy the new one in: the
running Sonata (Dock, menu bar...) then lost the files it had open -- its
bundled font among them, and every Dock name label went blank (Vini) until
it was restarted. Here a file whose content didn't change keeps its inode
(fonts, icons, sounds: nearly all of them); a changed one is replaced with a
rename (the old one stays readable for whoever has it open); what's gone
from SRC is removed."""
import filecmp
import os
import shutil
import sys


def same(a: str, b: str) -> bool:
    if os.path.islink(a) or os.path.islink(b):
        return os.path.islink(a) and os.path.islink(b) and os.readlink(a) == os.readlink(b)
    try:
        return os.path.isfile(b) and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def place(a: str, b: str) -> None:
    tmp = b + ".sonata-new"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    if os.path.islink(a):
        os.symlink(os.readlink(a), tmp)
    else:
        shutil.copy2(a, tmp)
    if os.path.isdir(b) and not os.path.islink(b):
        shutil.rmtree(b)
    os.replace(tmp, b)


def sync(src: str, dst: str) -> dict:
    counts = {"kept": 0, "replaced": 0, "removed": 0}
    os.makedirs(dst, exist_ok=True)
    wanted = set()
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = dst if rel == "." else os.path.join(dst, rel)
        if os.path.lexists(out) and (os.path.islink(out) or not os.path.isdir(out)):
            os.unlink(out)
        os.makedirs(out, exist_ok=True)
        wanted.add(os.path.normpath(out))
        for name in files + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            a, b = os.path.join(root, name), os.path.join(out, name)
            wanted.add(os.path.normpath(b))
            if same(a, b):
                counts["kept"] += 1
            else:
                place(a, b)
                counts["replaced"] += 1
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
    for root, dirs, files in os.walk(dst, topdown=False):
        for name in files + dirs:
            p = os.path.normpath(os.path.join(root, name))
            if p not in wanted:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.unlink(p)
                counts["removed"] += 1
    return counts


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: sync-tree.py SRC DST")
    c = sync(sys.argv[1], sys.argv[2])
    print(f"  {c['replaced']} files updated, {c['kept']} unchanged, {c['removed']} removed")
