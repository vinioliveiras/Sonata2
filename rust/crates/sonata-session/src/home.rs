//! Where the Python Sonata lives (its `REPO`: sonata2/__main__.py), for
//! what the Rust build still takes from it -- the session's Wayfire config
//! and tools/wayfire-config.sh (restart), config/wayfire.ini (doctor), the
//! Python-only login steps (autostart).

use std::path::{Path, PathBuf};

/// `SONATA_HOME="..."` in the launcher install.sh writes (`~/.local/bin/sonata2`).
pub fn home_from_launcher(text: &str) -> Option<PathBuf> {
    text.lines().find_map(|l| l.trim().strip_prefix("SONATA_HOME=")).map(|v| PathBuf::from(v.trim().trim_matches('"').trim_matches('\''))).filter(|p| !p.as_os_str().is_empty())
}

fn is_home(p: &Path) -> bool {
    p.join("sonata2").join("__init__.py").is_file()
}

/// The Python Sonata's folder: the one its launcher names, else where
/// install.sh puts it (for the user, then for everyone), else the clone
/// this binary was built from (development).
pub fn sonata_home() -> Option<PathBuf> {
    let launcher = std::env::var_os("SONATA2_LAUNCHER").map(PathBuf::from).filter(|p| p.is_file()).or_else(|| sonata_backend::run::which("sonata2"));
    let from_launcher = launcher.and_then(|l| std::fs::read_to_string(l).ok()).and_then(|t| home_from_launcher(&t));
    let user = std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share/sonata2"));
    let clone = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    from_launcher.into_iter().chain(user).chain([PathBuf::from("/usr/local/share/sonata2"), clone]).find(|p| is_home(p))
}

/// How to start the Python Sonata (`self_argv()`): its launcher, else this
/// interpreter + that folder.
pub fn python_argv() -> Option<Vec<String>> {
    if let Some(l) = std::env::var("SONATA2_LAUNCHER").ok().filter(|l| Path::new(l).exists()) {
        return Some(vec![l]);
    }
    if let Some(l) = sonata_backend::run::which("sonata2") {
        return Some(vec![l.display().to_string()]);
    }
    let home = sonata_home()?;
    Some(vec!["env".into(), format!("PYTHONPATH={}", home.display()), "python3".into(), "-m".into(), "sonata2".into()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn launcher_names_the_folder() {
        let text = "#!/bin/sh\n# Sonata 2 launcher\nSONATA_HOME=\"/home/v/.local/share/sonata2\"\nexport PYTHONPATH=\"$SONATA_HOME\"\nexec python3 -m sonata2 \"$@\"\n";
        assert_eq!(home_from_launcher(text), Some(PathBuf::from("/home/v/.local/share/sonata2")));
        assert_eq!(home_from_launcher("#!/bin/sh\nexec true\n"), None);
    }
}
