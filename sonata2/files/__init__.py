"""Files: Sonata's file manager (macOS Finder look and behaviour).
`sonata2 files [FOLDER...]` opens a window per folder."""
APP_ID = "io.github.vinioliveiras.sonata2.files"


def open_folder(uri: str) -> None:
    """Show a folder in Files (another process); the system's default file
    manager if Files' entry isn't there."""
    from gi.repository import Gio, GLib
    from ..apps import lookup
    info = lookup(APP_ID)
    try:
        if info:
            info.launch_uris([uri], None)
            return
    except GLib.Error:
        pass
    if uri.startswith("sonata:") or not _launch_default(uri):
        _spawn_files(uri)          # only Files knows sonata: places (Recents)


def _launch_default(uri: str) -> bool:
    from gi.repository import Gio, GLib
    try:
        return Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        return False


def _spawn_files(uri: str) -> None:
    """This clone's Files, without its desktop entry (dev session)."""
    import os
    import subprocess
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH")) if p))
    subprocess.Popen([sys.executable, "-m", "sonata2", "files", uri], env=env, start_new_session=True)


def files_desktop_file(command: str) -> str:
    """The Files entry (Launchpad, Dock, "Open With"); named after the app id
    so the Dock matches its windows directly."""
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Files\nComment=Browse your files\n"
                              "Icon=system-file-manager\nCategories=System;FileManager;Utility;\n"
                              "MimeType=inode/directory;x-scheme-handler/trash;\nStartupNotify=true\n"
                              f"Exec={command} files %U\n")
