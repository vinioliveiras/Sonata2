//! Other apps' windows in Sonata's look -- only inside the Sonata session
//! (gtkstyle.py).
//!
//! Apps read the look from Sonata's settings, served by its portal (and a
//! Sonata-only dconf layer): GTK 3 apps and Qt apps take the Sonata GTK
//! theme (gtk-theme: Sonata-Light / Sonata-Dark) and follow Dark Mode live;
//! libadwaita apps keep their own widgets and take Dark Mode and the accent
//! colour. Nothing uses a GTK_THEME variable: forcing a theme that way made
//! libadwaita apps see-through. Another desktop's session never sees any of
//! it.
//!
//! ~/.config/gtk-4.0/gtk.css only gets adwstyle.py's import line (between
//! BEGIN and END): clean() removes the whole block (an earlier version put
//! styles there). reset_env() clears a GTK_THEME an earlier version left in
//! the D-Bus / systemd activation environment.
//!
//! gtkstyle.update (Dark Mode changed: the Flatpak theme follows) is
//! sonata_tweaks::flatpak_theme::apply, called by Settings' set_dark_mode.

use std::path::{Path, PathBuf};

pub const BEGIN: &str = "/* >>> Sonata (written at login by sonata2/gtkstyle.py; edit outside these markers) */";
pub const END: &str = "/* <<< Sonata */";

pub fn theme_name(dark: Option<bool>) -> &'static str {
    let dark = dark.unwrap_or_else(|| {
        use sonata_config::prefs;
        prefs::get(prefs::I, "color-scheme", "", &prefs::load()) == "prefer-dark"
    });
    if dark { "Sonata-Dark" } else { "Sonata-Light" }
}

pub fn in_session() -> bool {
    std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default().to_lowercase().contains("sonata")
}

/// The commands reset_env runs: empty GTK_THEME = no theme forced.
pub const RESET_CMDS: [&[&str]; 2] =
    [&["systemctl", "--user", "unset-environment", "GTK_THEME"], &["dbus-update-activation-environment", "GTK_THEME="]];

/// Blocking (up to 5 s a command): at login, off the main loop.
pub fn reset_env() {
    if !in_session() {
        return;
    }
    // a timeout each: a busy/stuck systemd or D-Bus must not hang the caller
    for cmd in RESET_CMDS {
        crate::run_full(cmd, None, &[], 5);
    }
}

fn user_css() -> PathBuf {
    sonata_config::config_home().join("gtk-4.0").join("gtk.css")
}

/// Remove the block an earlier Sonata put in ~/.config/gtk-4.0/gtk.css.
pub fn clean(path: Option<&Path>) -> bool {
    let path = path.map(Path::to_path_buf).unwrap_or_else(user_css);
    let Ok(text) = std::fs::read_to_string(&path) else { return false };
    let (Some(b), Some(e)) = (text.find(BEGIN), text.find(END)) else { return false };
    let new = format!("{}{}", &text[..b], text[e + END.len()..].trim_start_matches('\n'));
    if new.trim().is_empty() {
        let _ = std::fs::remove_file(&path);
    } else {
        let _ = std::fs::write(&path, new);
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    /// tests/test_review_system.py GtkCssCleanTest: clean() drops only
    /// Sonata's marked block; a file with nothing else goes away.
    #[test]
    fn test_block_removed_user_css_kept_or_file_removed() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("gtk.css");
        std::fs::write(&p, format!("{BEGIN}\n@import url('x');\n{END}\n\nlabel {{ color: red; }}\n")).unwrap();
        assert!(clean(Some(&p)));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "label { color: red; }\n");
        assert!(!clean(Some(&p)));
        std::fs::write(&p, format!("{BEGIN}\nx\n{END}\n")).unwrap();
        assert!(clean(Some(&p)));
        assert!(!p.exists());
    }

    #[test]
    fn theme_names() {
        assert_eq!((theme_name(Some(true)), theme_name(Some(false))), ("Sonata-Dark", "Sonata-Light"));
    }
}
