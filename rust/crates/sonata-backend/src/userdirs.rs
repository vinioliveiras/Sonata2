//! The user's folders (Pictures, Videos, Documents...), found reliably
//! (sonata2/userdirs.py). GLib reads them from user-dirs.dirs; when that
//! file is missing or has no entry -- a Sonata session never ran
//! xdg-user-dirs-update -- GLib answers the home folder, and the Camera
//! saved into ~/Camera instead of ~/Imagens/Camera. Here: the file's folder
//! when it is a real one, else an existing folder with a usual name in
//! some language, else (create) one named in the session's language.

use std::path::{Path, PathBuf};

pub use glib::UserDirectory;

/// usual names, English first; the session's language's is made when none exists
fn names(kind: UserDirectory) -> &'static [(&'static str, &'static str)] {
    match kind {
        UserDirectory::Pictures => &[("en", "Pictures"), ("pt", "Imagens"), ("es", "Imágenes"), ("fr", "Images"), ("de", "Bilder"), ("it", "Immagini"), ("nl", "Afbeeldingen")],
        UserDirectory::Videos => &[("en", "Videos"), ("pt", "Vídeos"), ("es", "Vídeos"), ("fr", "Vidéos"), ("de", "Videos"), ("it", "Video"), ("nl", "Video's")],
        UserDirectory::Music => &[("en", "Music"), ("pt", "Músicas"), ("es", "Música"), ("fr", "Musique"), ("de", "Musik"), ("it", "Musica"), ("nl", "Muziek")],
        UserDirectory::Documents => &[("en", "Documents"), ("pt", "Documentos"), ("es", "Documentos"), ("fr", "Documents"), ("de", "Dokumente"), ("it", "Documenti"), ("nl", "Documenten")],
        UserDirectory::Downloads => &[("en", "Downloads"), ("pt", "Downloads"), ("es", "Descargas"), ("fr", "Téléchargements"), ("de", "Downloads"), ("it", "Scaricati"), ("nl", "Downloads")],
        UserDirectory::Desktop => &[("en", "Desktop"), ("pt", "Área de Trabalho"), ("es", "Escritorio"), ("fr", "Bureau"), ("de", "Schreibtisch"), ("it", "Scrivania"), ("nl", "Bureaublad")],
        _ => &[],
    }
}

/// other names people use
fn extra(kind: UserDirectory) -> &'static [&'static str] {
    match kind {
        UserDirectory::Music => &["Música"],
        _ => &[],
    }
}

/// The session's language, two letters ("en" for C).
pub fn lang() -> String {
    for var in ["LC_ALL", "LC_MESSAGES", "LANG"] {
        if let Ok(v) = std::env::var(var) {
            if !v.is_empty() && !["C", "POSIX", "C.UTF-8"].contains(&v.as_str()) {
                return v.chars().take(2).collect::<String>().to_lowercase();
            }
        }
    }
    "en".into()
}

/// The folder in `home` by its usual names (the language's first), made when `create`.
pub fn find_in(home: &Path, kind: UserDirectory, lang: &str, create: bool) -> Option<PathBuf> {
    let ns = names(kind);
    let mut ordered: Vec<&str> = ns.iter().filter(|(l, _)| *l == lang).map(|(_, n)| *n).take(1).collect();
    ordered.extend(ns.iter().map(|(_, n)| *n));
    ordered.extend(extra(kind));
    if let Some(p) = ordered.iter().map(|n| home.join(n)).find(|p| p.is_dir()) {
        return Some(p);
    }
    if !create || ns.is_empty() {
        return None;
    }
    let name = ns.iter().find(|(l, _)| *l == lang).or(ns.first()).map(|(_, n)| *n)?;
    let p = home.join(name);
    std::fs::create_dir_all(&p).ok()?;
    Some(p)
}

/// The folder's path; None when there is none (and `create` is false).
pub fn special(kind: UserDirectory, create: bool) -> Option<PathBuf> {
    let home = glib::home_dir();
    if let Some(p) = glib::user_special_dir(kind) {
        let real = |x: &Path| std::fs::canonicalize(x).unwrap_or_else(|_| x.to_path_buf());
        if p.is_dir() && real(&p) != real(&home) {
            return Some(p);
        }
    }
    find_in(&home, kind, &lang(), create)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn found_by_any_usual_name_made_in_the_language() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(find_in(d.path(), UserDirectory::Pictures, "pt", false), None);
        std::fs::create_dir(d.path().join("Pictures")).unwrap();
        assert_eq!(find_in(d.path(), UserDirectory::Pictures, "pt", false), Some(d.path().join("Pictures")));
        std::fs::create_dir(d.path().join("Imagens")).unwrap();
        assert_eq!(find_in(d.path(), UserDirectory::Pictures, "pt", false), Some(d.path().join("Imagens"))); // the language's first
        assert_eq!(find_in(d.path(), UserDirectory::Videos, "pt", true), Some(d.path().join("Vídeos")));
        assert!(d.path().join("Vídeos").is_dir());
        assert_eq!(find_in(d.path(), UserDirectory::Music, "xx", true), Some(d.path().join("Music")));
    }
}
