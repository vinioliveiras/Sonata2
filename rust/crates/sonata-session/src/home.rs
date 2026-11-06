//! Sonata's folder: the session's scripts (tools/), the Wayfire config
//! defaults (config/wayfire.ini) and data/ -- for `restart` (the session's
//! Wayfire config again) and `doctor`.

use std::path::{Path, PathBuf};

fn is_home(p: &Path) -> bool {
    p.join("tools").join("wayfire-config.sh").is_file() && p.join("config").join("wayfire.ini").is_file()
}

/// `$SONATA_REPO` (tools/session-env.sh sets it for the session), else the
/// folder install.sh puts next to this binary (`<prefix>/share/sonata2-rs`),
/// else the clone this binary was built from (development).
pub fn sonata_home() -> Option<PathBuf> {
    let env = std::env::var_os("SONATA_REPO").filter(|v| !v.is_empty()).map(PathBuf::from);
    let installed = std::env::current_exe().ok().and_then(|e| Some(e.parent()?.parent()?.join("share").join("sonata2-rs")));
    let clone = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
    env.into_iter().chain(installed).chain([clone]).find(|p| is_home(p))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clone_is_a_sonata_folder() {
        assert!(is_home(&PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")));
        assert!(!is_home(Path::new("/")));
    }
}
