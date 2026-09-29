#!/usr/bin/env python3
"""Make Sonata's symbolic icons fill-only (strokes -> outlines, circles/rects
-> paths). GTK recolours symbolic icons by forcing `fill` on every shape,
so a stroked shape with fill="none" turns into a solid blob (seen on GTK
4.20: the Spotlight and Dark Mode icons became dots). Keeps opacity and
class="success|error|warning". Needs picosvg (pip install picosvg).
  tools/symbolic-fill.py FILE_OR_DIR...   (rewrites in place)"""
import os
import re
import sys
import xml.etree.ElementTree as ET

from picosvg.svg import SVG

SHAPES = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}


def _classes(path):
    """class attribute of each drawable shape, in document order."""
    root = ET.parse(path).getroot()
    return [el.get("class") for el in root.iter() if el.tag.split("}")[-1] in SHAPES]


def convert(path):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r"<metadata>.*?</metadata>", "", src, flags=re.S)       # provenance blobs
    src = re.sub(r'\s+xmlns:c2pa="[^"]*"', "", src)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    classes = _classes(tmp)
    out = SVG.parse(tmp).topicosvg().tostring()
    os.remove(tmp)
    root = ET.fromstring(out)
    paths = [el for el in root.iter() if el.tag.split("}")[-1] == "path"]
    if len(paths) == len(classes):
        for el, cls in zip(paths, classes):
            if cls:
                el.set("class", cls)
    elif any(classes):
        print("warning: shape count changed, classes dropped:", path)
    ET.register_namespace("", "http://www.w3.org/2000/svg")
    for el in list(root):
        if el.tag.split("}")[-1] == "defs" and not len(el):
            root.remove(el)
    with open(path, "w", encoding="utf-8") as f:
        f.write(ET.tostring(root, encoding="unicode") + "\n")


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        files = [os.path.join(arg, n) for n in sorted(os.listdir(arg)) if n.endswith(".svg")] \
            if os.path.isdir(arg) else [arg]
        for p in files:
            convert(p)
    print("ok")
