"""The user's folders (Pictures, Videos, Documents...), found reliably.

GLib reads them from ~/.config/user-dirs.dirs (xdg-user-dirs). When that
file is missing or has no entry -- a Sonata session never ran
xdg-user-dirs-update -- GLib answers the home folder (or nothing), and the
Camera saved into ~/Camera instead of ~/Imagens/Camera. Here: the file's
folder when it is a real one, else an existing folder with a usual name in
some language, else (create=True) one named in the session's language.

    userdirs.special(GLib.UserDirectory.DIRECTORY_PICTURES, create=True)  # "/home/v/Imagens"
"""
import os

from gi.repository import GLib

U = GLib.UserDirectory
# usual names, English first; the first one of the session's language is created when none exists
NAMES = {
    U.DIRECTORY_PICTURES: {"en": "Pictures", "pt": "Imagens", "es": "Imágenes", "fr": "Images", "de": "Bilder",
                           "it": "Immagini", "nl": "Afbeeldingen"},
    U.DIRECTORY_VIDEOS: {"en": "Videos", "pt": "Vídeos", "es": "Vídeos", "fr": "Vidéos", "de": "Videos",
                         "it": "Video", "nl": "Video's"},
    U.DIRECTORY_MUSIC: {"en": "Music", "pt": "Músicas", "es": "Música", "fr": "Musique", "de": "Musik",
                        "it": "Musica", "nl": "Muziek"},
    U.DIRECTORY_DOCUMENTS: {"en": "Documents", "pt": "Documentos", "es": "Documentos", "fr": "Documents",
                            "de": "Dokumente", "it": "Documenti", "nl": "Documenten"},
    U.DIRECTORY_DOWNLOAD: {"en": "Downloads", "pt": "Downloads", "es": "Descargas", "fr": "Téléchargements",
                           "de": "Downloads", "it": "Scaricati", "nl": "Downloads"},
    U.DIRECTORY_DESKTOP: {"en": "Desktop", "pt": "Área de Trabalho", "es": "Escritorio", "fr": "Bureau",
                          "de": "Schreibtisch", "it": "Scrivania", "nl": "Bureaublad"},
}
EXTRA = {U.DIRECTORY_MUSIC: ("Música",)}          # other names people use


def _lang() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var)
        if v and v not in ("C", "POSIX", "C.UTF-8"):
            return v[:2].lower()
    return "en"


def special(kind, create: bool = False, home: str = None) -> str:
    """The folder's path; None when there is none (and create is False)."""
    home = home or GLib.get_home_dir()
    p = GLib.get_user_special_dir(kind) if home == GLib.get_home_dir() else None
    if p and os.path.isdir(p) and os.path.realpath(p) != os.path.realpath(home):
        return p
    names = NAMES.get(kind, {})
    lang = _lang()
    ordered = ([names[lang]] if lang in names else []) + list(names.values()) + list(EXTRA.get(kind, ()))
    for name in ordered:
        path = os.path.join(home, name)
        if os.path.isdir(path):
            return path
    if not create or not names:
        return None
    path = os.path.join(home, names.get(lang, names["en"]))
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path
