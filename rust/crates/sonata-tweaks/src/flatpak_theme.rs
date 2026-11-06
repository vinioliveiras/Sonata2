//! Sonata's look for Flatpak apps too (sonata2/flatpak_theme.py; Settings >
//! Appearance, on by default).
//!
//! Flatpak apps run sandboxed: they don't see the system's themes nor the
//! session's GTK_THEME. Turning this on copies Sonata's GTK themes to
//! ~/.local/share/themes (inside the sandbox's reach) and adds to Flatpak's
//! user overrides for every app read access to that folder (the theme name
//! and Dark Mode come from Sonata's settings portal). Flatpak overrides
//! apply in every session, so a GNOME/KDE session's Flatpak apps look like
//! Sonata's too while it's on; turning it off removes exactly what Sonata
//! added.

use serde_json::json;
use std::path::{Path, PathBuf};

const NAME: &str = "appearance";
const KEY: &str = "flatpak_theme";
pub const FS: &str = "xdg-data/themes:ro";
/// GTK 4 apps' user stylesheet and the file it imports in Sonata's session
pub const EXTRA_FS: [&str; 2] = ["xdg-config/gtk-4.0:ro", "xdg-run/sonata2:ro"];
pub const THEMES: [&str; 2] = ["Sonata-Light", "Sonata-Dark"];

pub fn enabled() -> bool {
    crate::util::truthy(crate::util::load(NAME, json!({KEY: true})).get(KEY))
}

pub fn overrides_path() -> PathBuf {
    crate::util::data_home().join("flatpak/overrides/global")
}

/// Change the overrides file: only a missing file starts empty -- an
/// unreadable or broken one is the user's overrides, never saved over
/// with ours alone.
fn edit(path: &Path, change: impl FnOnce(&glib::KeyFile)) {
    let kf = glib::KeyFile::new();
    if let Err(e) = kf.load_from_file(path, glib::KeyFileFlags::KEEP_COMMENTS) {
        if !e.matches(glib::FileError::Noent) {
            return;
        }
    }
    change(&kf);
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = kf.save_to_file(path);
}

fn filesystems(kf: &glib::KeyFile) -> Vec<String> {
    kf.string("Context", "filesystems").map(|s| s.split(';').filter(|f| !f.is_empty()).map(String::from).collect()).unwrap_or_default()
}

/// (an earlier version forced GTK_THEME: it broke libadwaita apps)
fn drop_gtk_theme(kf: &glib::KeyFile) {
    if kf.string("Environment", "GTK_THEME").is_ok_and(|t| t.starts_with("Sonata-")) {
        let _ = kf.remove_key("Environment", "GTK_THEME");
    }
}

/// The newest file time under a folder, whole seconds (the copy's stamp).
fn version(dir: &Path) -> String {
    fn newest(d: &Path) -> f64 {
        let Ok(rd) = std::fs::read_dir(d) else { return 0.0 };
        rd.flatten()
            .map(|e| {
                let p = e.path();
                if p.is_dir() {
                    newest(&p)
                } else {
                    std::fs::metadata(&p).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map(|d| d.as_secs_f64()).unwrap_or(0.0)
                }
            })
            .fold(0.0, f64::max)
    }
    (newest(dir) as i64).to_string()
}

fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for e in std::fs::read_dir(src)?.flatten() {
        let (s, d) = (e.path(), dst.join(e.file_name()));
        if s.is_dir() { copy_tree(&s, &d)? } else { std::fs::copy(&s, &d).map(|_| ())? }
    }
    Ok(())
}

pub fn copy_themes_to(src: &Path, dst: &Path) {
    for name in THEMES {
        let (s, d) = (src.join(name), dst.join(name));
        let stamp = d.join(".sonata-version");
        let v = version(&s);
        if std::fs::read_to_string(&stamp).is_ok_and(|t| t == v) {
            continue; // already the current copy
        }
        if d.is_dir() {
            let _ = std::fs::remove_dir_all(&d);
        }
        if copy_tree(&s, &d).is_ok() {
            let _ = std::fs::write(&stamp, &v);
        }
    }
}

/// Our filesystems in, an old forced GTK_THEME out.
pub fn add_to(path: &Path) {
    edit(path, |kf| {
        let fs = filesystems(kf);
        let want: Vec<String> = std::iter::once(FS).chain(EXTRA_FS).filter(|f| !fs.iter().any(|x| x == f)).map(String::from).collect();
        if !want.is_empty() {
            kf.set_string("Context", "filesystems", &format!("{};", [fs, want].concat().join(";")));
        }
        drop_gtk_theme(kf);
    });
}

/// Only what Sonata added goes away (your own overrides stay).
pub fn remove_from(path: &Path) {
    if !path.exists() {
        return;
    }
    edit(path, |kf| {
        let fs: Vec<String> = filesystems(kf).into_iter().filter(|f| f != FS && !EXTRA_FS.contains(&f.as_str())).collect();
        if fs.is_empty() {
            let _ = kf.remove_key("Context", "filesystems");
        } else {
            kf.set_string("Context", "filesystems", &format!("{};", fs.join(";")));
        }
        drop_gtk_theme(kf);
        for group in ["Context", "Environment"] {
            if kf.has_group(group) && kf.keys(group).map(|k| k.is_empty()).unwrap_or(true) {
                let _ = kf.remove_group(group);
            }
        }
    });
}

/// On (at login, on toggle, on Dark Mode): themes copied, overrides set.
pub fn apply() -> bool {
    if sonata_backend::run::which("flatpak").is_none() || !enabled() {
        return false;
    }
    copy_themes_to(&sonata_config::data_path("themes"), &crate::util::data_home().join("themes"));
    add_to(&overrides_path());
    true
}

/// Off: only what Sonata added goes away.
pub fn remove() {
    remove_from(&overrides_path());
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overrides_in_and_exactly_out() {
        let t = tempfile::tempdir().unwrap();
        let p = t.path().join("overrides/global");
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(&p, "[Context]\nfilesystems=~/Games;\n\n[Environment]\nGTK_THEME=Sonata-Dark\n").unwrap();
        add_to(&p);
        let text = std::fs::read_to_string(&p).unwrap();
        assert!(text.contains("filesystems=~/Games;xdg-data/themes:ro;xdg-config/gtk-4.0:ro;xdg-run/sonata2:ro;"), "{text}");
        assert!(!text.contains("GTK_THEME"));
        add_to(&p); // once
        assert_eq!(std::fs::read_to_string(&p).unwrap(), text);
        remove_from(&p);
        let text = std::fs::read_to_string(&p).unwrap();
        assert!(text.contains("filesystems=~/Games;") && !text.contains("themes") && !text.contains("[Environment]"), "{text}");
        // a broken file is the user's: never saved over
        std::fs::write(&p, [0xffu8, 0xfe, b'[']).unwrap();
        add_to(&p);
        assert_eq!(std::fs::read(&p).unwrap(), [0xffu8, 0xfe, b'[']);
        // a missing one starts empty
        let q = t.path().join("new/global");
        add_to(&q);
        remove_from(&q);
        assert!(!std::fs::read_to_string(&q).unwrap().contains("Context"));
    }

    #[test]
    fn themes_copied_once() {
        let t = tempfile::tempdir().unwrap();
        let src = t.path().join("src");
        for n in THEMES {
            std::fs::create_dir_all(src.join(n).join("gtk-4.0")).unwrap();
            std::fs::write(src.join(n).join("gtk-4.0/gtk.css"), n).unwrap();
        }
        let dst = t.path().join("dst");
        copy_themes_to(&src, &dst);
        assert_eq!(std::fs::read_to_string(dst.join("Sonata-Dark/gtk-4.0/gtk.css")).unwrap(), "Sonata-Dark");
        std::fs::write(dst.join("Sonata-Dark/mine"), "x").unwrap();
        copy_themes_to(&src, &dst); // the current copy: left alone
        assert!(dst.join("Sonata-Dark/mine").exists());
    }
}
