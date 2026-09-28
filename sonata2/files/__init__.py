"""Files: Sonata's file manager (macOS Finder look and behaviour).
`sonata2 files [FOLDER...]` opens a window per folder."""
APP_ID = "io.github.vinioliveiras.sonata2.files"


def files_desktop_file(command: str) -> str:
    """The Files entry (Launchpad, Dock, "Open With"); named after the app id
    so the Dock matches its windows directly."""
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Files\nComment=Browse your files\n"
                              "Icon=system-file-manager\nCategories=System;FileManager;Utility;\n"
                              "MimeType=inode/directory;\nStartupNotify=true\n"
                              f"Exec={command} files %U\n")
