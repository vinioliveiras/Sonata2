"""Vini: a friend's install failed. Installing from scratch in a clean Arch
(tools/test-install.sh) stopped without a word: localectl had nothing to
answer and, under pipefail, the login-screen step ended the whole install.
Every installer script now says where it stopped, and the fresh install's
doctor no longer warns about what install.sh itself built."""
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


class ScriptsTest(unittest.TestCase):
    def test_a_failing_step_says_where(self):
        for f in ("install.sh", "tools/greeter-setup.sh"):
            src = (ROOT / f).read_text()
            self.assertIn("set -euo pipefail", src, f)
            self.assertRegex(src, r"trap '.*stopped at line \$LINENO.*' ERR", f)

    def test_no_localed_is_not_an_error(self):
        """greeter-setup's keyboard layout with a localectl that fails."""
        src = (ROOT / "tools/greeter-setup.sh").read_text()
        self.assertIn('loc="$(localectl status 2>/dev/null || true)"', src)
        self.assertNotIn('layout="$(localectl status', src)
        # the same lines, run under the same shell options with a failing localectl
        start = src.index('loc="$(localectl')
        block = src[start:src.index("\n", src.index('variant="$(printf', start))]
        d = tempfile.mkdtemp()
        fake = os.path.join(d, "localectl")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\necho 'System has not been booted with systemd' >&2\nexit 1\n")
        os.chmod(fake, 0o755)
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + block + '\necho "layout=${layout:-us}"'],
                           capture_output=True, text=True, env={**os.environ, "PATH": d + ":" + os.environ["PATH"]})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("layout=us", r.stdout)

    def test_install_scripts_parse(self):
        for f in ("install.sh", "tools/greeter-setup.sh", "tools/test-install.sh", "tools/build-wayfire.sh"):
            if (ROOT / f).exists():
                r = subprocess.run(["bash", "-n", str(ROOT / f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f + ": " + r.stderr)


class FedoraTest(unittest.TestCase):
    """tools/test-install.sh on Fedora: "installed", yet Sonata couldn't start
    -- GTK needs the cairo typelib (gobject-introspection), not pulled in;
    and Wayfire 0.10 has no ext-toplevel plugin."""

    def test_fedora_gets_gobject_introspection(self):
        src = (ROOT / "install.sh").read_text()
        line = next(ln for ln in src.splitlines() if "*fedora*" in ln)
        pkgs = src[src.index(line):].split("PKGS=", 1)[1].split("\n", 1)[0]
        self.assertIn("gobject-introspection", pkgs)

    def test_ubuntu_gets_the_layer_shell_library(self):
        """Ubuntu 25.04: gir1.2-gtk4layershell-1.0 doesn't pull libgtk4-layer-shell0
        (doctor: "libgtk4-layer-shell.so.0: cannot open shared object file")."""
        src = (ROOT / "install.sh").read_text()
        line = next(ln for ln in src.splitlines() if "*debian*|*ubuntu*" in ln)
        pkgs = src[src.index(line):].split("PKGS=", 1)[1].split("\n", 1)[0]
        self.assertIn("libgtk4-layer-shell0", pkgs)
        self.assertIn("mod.get_major_version()", src)          # the library itself is checked

    def test_ubuntu_pywayland_gets_its_cffi_backend(self):
        """Ubuntu 25.04: python3-pywayland doesn't pull python3-cffi-backend
        ("No module named pywayland._ffi"); with it, it imports."""
        src = (ROOT / "install.sh").read_text()
        line = next(ln for ln in src.splitlines() if "*debian*|*ubuntu*" in ln)
        pkgs = src[src.index(line):].split("PKGS=", 1)[1].split("\n", 1)[0]
        self.assertIn("python3-cffi-backend", pkgs)

    def test_broken_pywayland_package_gets_pip_and_its_build_tools(self):
        """Ubuntu 25.04's python3-pywayland can't be imported ("No module named
        pywayland._ffi"): pip builds it, so pip, cffi and the Wayland headers first."""
        src = (ROOT / "install.sh").read_text()
        block = src[src.index('if ! python3 -c "import pywayland"'):]
        block = block[:block.index("\n    fi\n")]
        self.assertIn("python3-pip python3-dev python3-cffi libwayland-dev", block)
        self.assertIn("pip install --user", src[src.index('if ! python3 -c "import pywayland"'):][:1500])

    def test_install_stops_when_sonata_cant_start(self):
        src = (ROOT / "install.sh").read_text()
        self.assertIn("missing_python()", src)
        after = src[src.index('still="$(missing_python'):]
        self.assertIn("Sonata can't start without", after[:600])
        self.assertIn("exit 1", after[:600])

    def test_plugins_wayfire_lacks_are_left_out(self):
        home = tempfile.mkdtemp()
        pdir = os.path.join(home, ".local/share/wayfire/plugin-manager/install/lib/wayfire")
        os.makedirs(pdir)
        for p in ("autostart", "move"):
            open(os.path.join(pdir, f"lib{p}.so"), "w").close()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "in.ini"), "w") as f:
            f.write("[core]\nplugins = autostart move no-such-plugin-xyz\n")
        r = subprocess.run(["bash", str(ROOT / "tools/wayfire-config.sh"), os.path.join(d, "in.ini"),
                            os.path.join(d, "out.ini")], capture_output=True, text=True,
                           env={**os.environ, "HOME": home, "XDG_DATA_HOME": os.path.join(home, ".local/share"),
                                "XDG_CONFIG_HOME": os.path.join(home, ".config")})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = open(os.path.join(d, "out.ini")).read()
        line = next(ln for ln in out.splitlines() if ln.startswith("plugins"))
        self.assertIn("autostart", line.split())
        self.assertIn("move", line.split())
        self.assertNotIn("no-such-plugin-xyz", line)


class DoctorTest(unittest.TestCase):
    def test_finds_the_plugins_install_built(self):
        from sonata2 import doctor
        home = tempfile.mkdtemp()
        user = os.path.join(home, ".local/share/wayfire/plugin-manager/install/lib/wayfire")
        own = os.path.join(home, ".local/opt/sonata-wayfire/lib/wayfire")
        for d in (user, own):
            os.makedirs(d)
        with mock.patch.dict(os.environ, {"HOME": home}):
            dirs = doctor._plugin_dirs()
        self.assertIn(user, dirs)                             # pixdecor, sonata-corners: built there
        self.assertIn(own, dirs)

    def test_aur_only_tools_stay_out_of_the_pacman_fix(self):
        """A fresh Arch install's doctor offered "sudo pacman -S wl-screenrec":
        it's only in the AUR."""
        from sonata2 import doctor
        rep = doctor.Report() if hasattr(doctor, "Report") else None
        if rep is None:
            self.skipTest("no Report")
        with mock.patch.object(doctor, "_family", return_value="arch"), \
                mock.patch.object(doctor.shutil, "which",
                                  side_effect=lambda c: None if c == "wl-screenrec" else "/usr/bin/" + c):
            try:
                doctor.check_tools(rep)
            except Exception:
                pass
        text = repr(rep.__dict__)
        self.assertIn("wl-screenrec missing", text)
        self.assertNotIn("pacman -S --needed wl-screenrec", text)

    def test_a_normal_install_is_not_a_warning(self):
        import inspect
        from sonata2 import doctor
        src = inspect.getsource(doctor)
        self.assertNotIn('r.add(WARN, "installed copy', src)


if __name__ == "__main__":
    unittest.main()


class AnyLoginManagerTest(unittest.TestCase):
    """A friend's CachyOS ran Plasma Login (plasmalogin): it wasn't in the list,
    stayed enabled, and enabling greetd failed ("display-manager.service already
    exists"). The one display-manager.service points to is taken over now."""
    def block(self):
        src = (ROOT / "tools/greeter-setup.sh").read_text()
        start = src.index("DMS=")
        end = src.index("\n", src.index("DMS=")) + 1
        fn = src[src.index("DM_LINK="):src.index("if [ \"$ACTION\" = revert ]")]
        return src[start:end] + fn

    def run_it(self, link_target, enabled=""):
        d = tempfile.mkdtemp()
        if link_target:
            unit = os.path.join(d, link_target + ".service")
            open(unit, "w").close()
            os.symlink(unit, os.path.join(d, "display-manager.service"))
        with open(os.path.join(d, "systemctl"), "w") as f:
            f.write(f'#!/bin/sh\n[ "$1" = is-enabled ] && [ "$2" = "{enabled}.service" ] && exit 0\nexit 1\n')
        os.chmod(os.path.join(d, "systemctl"), 0o755)
        script = "set -euo pipefail\n" + self.block() + "\ncurrent_dm || true\n"
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={**os.environ, "PATH": d + ":" + os.environ["PATH"],
                                "DM_LINK": os.path.join(d, "display-manager.service")})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_plasma_login_found(self):
        self.assertEqual(self.run_it("plasmalogin"), "plasmalogin")
        self.assertEqual(self.run_it("some-new-dm"), "some-new-dm")          # any, by its link

    def test_by_name_without_the_link(self):
        self.assertEqual(self.run_it(None, enabled="sddm"), "sddm")
        self.assertEqual(self.run_it(None), "")

    def test_greetd_takes_the_link(self):
        src = (ROOT / "tools/greeter-setup.sh").read_text()
        self.assertIn("systemctl enable --force greetd.service", src)


class NotAsRootTest(unittest.TestCase):
    """A friend ran `sudo ./install.sh`: Sonata went to root's home, the login
    screen found none for him and every login went back to it."""
    def test_refused_as_root(self):
        src = (ROOT / "install.sh").read_text()
        start = src.index("# `sudo ./install.sh` installed Sonata for root")
        block = src[start:src.index("if [ \"$MODE\" = system ]", start)]
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "id"), "w") as f:
            f.write("#!/bin/sh\necho 0\n")
        os.chmod(os.path.join(d, "id"), 0o755)
        env = {**os.environ, "PATH": d + ":" + os.environ["PATH"]}
        for mode, uninstall, code in (("user", 0, 1), ("system", 0, 0), ("user", 1, 0)):
            r = subprocess.run(["bash", "-c", f"set -euo pipefail\nMODE={mode}\nUNINSTALL={uninstall}\n{block}\nexit 0"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, code, (mode, uninstall, r.stderr))
        self.assertIn("without sudo", subprocess.run(["bash", "-c", f"MODE=user\nUNINSTALL=0\n{block}"],
                                                     capture_output=True, text=True, env=env).stderr)


class PixdecorSettingsTest(unittest.TestCase):
    """A friend's laptop: every login went back to the login screen -- Wayfire
    aborted loading pixdecor, built here without its pixdecor.xml (meson put
    it in the system's metadata folder, outside the prefix, and only the
    prefix was copied)."""
    def copy_block(self):
        src = (ROOT / "tools/build-pixdecor.sh").read_text()
        start = src.index('(cd "$pdir/stage$PLUG_PREFIX" && find . -type f)')
        return src[start:src.index('echo "  pixdecor installed', start)]

    def test_xml_lands_next_to_the_plugin(self):
        d = tempfile.mkdtemp()
        prefix = os.path.join(d, "home", ".local", "share", "wayfire", "plugin-manager", "install")
        pdir = os.path.join(d, "build")
        lib = os.path.join(pdir, "stage" + prefix, "lib", "wayfire")
        os.makedirs(lib)
        open(os.path.join(lib, "libpixdecor.so"), "w").write("so")
        meta = os.path.join(pdir, "stage", "usr", "share", "wayfire", "metadata")
        os.makedirs(meta)
        open(os.path.join(meta, "pixdecor.xml"), "w").write("<wayfire/>")
        script = f'set -uo pipefail\npdir="{pdir}"\nPLUG_PREFIX="{prefix}"\n' + self.copy_block()
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(prefix, "lib", "wayfire", "libpixdecor.so")))
        self.assertEqual(open(os.path.join(prefix, "share", "wayfire", "metadata", "pixdecor.xml")).read(), "<wayfire/>")


class SessionSafeModeTest(unittest.TestCase):
    """Wayfire still ending at once: once more without the plugins built here."""
    def test_retry_without_built_plugins(self):
        src = (ROOT / "tools/sonata-session").read_text()
        start = src.index("run_wayfire() {")
        fn = src[start:src.index("\n}\n", start) + 3]
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "wayfire.ini")
        open(cfg, "w").write("[core]\nplugins = autostart pixdecor sonata-corners sonata-resize move sonata-privacy\n")
        with open(os.path.join(d, "wayfire"), "w") as f:      # aborts with pixdecor in its config
            f.write(f'#!/bin/sh\necho "$*" >> {d}/runs\ngrep -q pixdecor "$2" && exit 134\nexit 0\n')
        os.chmod(os.path.join(d, "wayfire"), 0o755)
        script = (f'WAYFIRE=wayfire\nown={d}/own\nlogs={d}\ncfg={cfg}\n' + fn +
                  '\nrun_wayfire\necho "code=$code"\n')
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={**os.environ, "PATH": d + ":" + os.environ["PATH"]})
        self.assertIn("code=0", r.stdout, r.stderr)
        safe = open(os.path.join(d, "wayfire-safe.ini")).read()
        self.assertIn("plugins = autostart decoration resize move\n", safe)
        self.assertTrue(os.path.exists(os.path.join(d, "session.plugins-failed.log")))
        self.assertEqual(len(open(os.path.join(d, "runs")).read().splitlines()), 2)
