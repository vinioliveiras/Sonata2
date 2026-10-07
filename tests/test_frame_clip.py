"""Vini: Claude drew itself bigger than its window (after a resize, while
dragging an edge) and the extra showed over the desktop. sonata-corners
cuts every framed window to its frame: outside it, only the decoration's
shadow is drawn -- never the app's own pixels -- and nothing out there
counts as opaque. (The shader is also compiled here when glslangValidator
is installed.)"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

SRC = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()


def shader():
    fs = SRC[SRC.index("static const char *fragment_shader ="):]
    fs = fs[fs.index('R"(') + 3:fs.index(')";')]
    return fs.replace("@builtin_ext@", "").replace("@builtin@", "").replace(
        "precision highp float;",
        "precision highp float;\nuniform sampler2D _tex;\nvec4 get_pixel(vec2 uv){ return texture2D(_tex, uv); }", 1)


class FrameClipTest(unittest.TestCase):
    def test_outside_the_frame_only_the_shadow(self):
        fs = shader()
        part = fs[fs.index("if (p.x < lo.x || p.y < lo.y || p.x > hi.x || p.y > hi.y)"):]
        part = part[:part.index("else if")]
        self.assertIn("c = shadow * k;", part)                 # the app's pixel is dropped

    def test_never_opaque_outside_the_frame(self):
        part = SRC[SRC.index("wf::regionf_t get_opaque_region() const override"):SRC.index("std::string stringify")]
        self.assertIn("region &= wf::regionf_t{f};", part)

    @unittest.skipUnless(shutil.which("glslangValidator"), "no glslangValidator")
    def test_shader_compiles(self):
        path = os.path.join(tempfile.mkdtemp(), "corners.frag")
        with open(path, "w") as f:
            f.write(shader())
        r = subprocess.run(["glslangValidator", path], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
