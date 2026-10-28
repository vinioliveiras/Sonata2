"""Installed apps for the tests that need some (the Dock's default pins,
Launchpad's grid, folders): a fixed set of .desktop files in a temporary
XDG_DATA_HOME, set before Gio reads it. Without it those tests skipped on a
machine (or CI) without these apps -- and regressions in the Dock and
folders went unseen (2026-10-28).

    from tests import fake_apps  # noqa: F401  (first, before sonata2)
"""
import os
import tempfile

APPS = {
    "io.github.vinioliveiras.sonata2.files": "Files",
    "io.github.vinioliveiras.sonata2.preview": "Preview",
    "io.github.vinioliveiras.sonata2.textedit": "TextEdit",
    "io.github.vinioliveiras.sonata2.terminal": "Terminal",
    "io.github.vinioliveiras.sonata2.feedback": "Feedback",
    "thunderbird": "Thunderbird",
    "org.gnome.Calendar": "Calendar",
    "spotify": "Spotify",
    "steam": "Steam",
    "firefox": "Firefox",
    "org.gnome.Calculator": "Calculator",
}
# enough more for Launchpad's second page (its paging, its edges)
APPS.update({f"sonata-test-app-{i:02d}": f"Test App {i:02d}" for i in range(48)})


def install() -> str:
    root = tempfile.mkdtemp(prefix="sonata2-apps-")
    apps = os.path.join(root, "applications")
    os.makedirs(apps)
    for did, name in APPS.items():
        with open(os.path.join(apps, did + ".desktop"), "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec=true\nIcon=application-x-executable\n"
                    "Categories=Utility;\n")
    os.environ["XDG_DATA_HOME"] = root
    return root


ROOT = install()
