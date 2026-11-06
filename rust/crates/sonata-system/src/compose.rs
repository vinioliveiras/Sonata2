//! ç on U.S. International (compose.py; Vini: it couldn't be typed). With
//! us(intl), ' is a dead key, and Linux's compose tables make ' + c a "ć"
//! (c acute), where Windows and macOS give "ç". ~/.XCompose -- read by GTK,
//! Qt and Chromium / Electron apps -- gets the two lines that make it
//! "ç" / "Ç", after the locale's own table (include "%L"), so every other
//! combination stays. A file of the user's own keeps its lines: ours are
//! added to it.
//!
//! ```ignore
//! compose::ensure(None)     // at login (autostart); apps read it as they start
//! ```

use std::path::{Path, PathBuf};

pub const MARK: &str = "# Sonata: ' + c types ç on U.S. International (as on Windows and macOS)";
pub const RULES: [&str; 2] = ["<dead_acute> <c> : \"ç\" ccedilla", "<dead_acute> <C> : \"Ç\" Ccedilla"];

pub fn path() -> PathBuf {
    match std::env::var("XCOMPOSEFILE") {
        Ok(p) if !p.is_empty() => PathBuf::from(p),
        _ => glib::home_dir().join(".XCompose"),
    }
}

/// True when the file has the cedilla rules (written now or before).
pub fn ensure(target: Option<&Path>) -> bool {
    let target = target.map(Path::to_path_buf).unwrap_or_else(path);
    let text = match std::fs::read_to_string(&target) {
        Ok(t) => Some(t),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(_) => return false,
    };
    if let Some(t) = &text {
        // ours, or the user's own lines for it
        if RULES.iter().all(|r| t.contains(r.split(" :").next().unwrap_or(r))) {
            return true;
        }
    }
    let out = match &text {
        None => format!("{MARK}\ninclude \"%L\"\n{}\n", RULES.join("\n")),
        Some(t) => {
            let sep = if t.ends_with('\n') || t.is_empty() { "" } else { "\n" };
            let out = format!("{t}{sep}\n{MARK}\n{}\n", RULES.join("\n"));
            // a file without the locale's table would lose the rest
            if t.contains("include") { out } else { format!("include \"%L\"\n{out}") }
        }
    };
    let mut tmp = target.clone().into_os_string();
    tmp.push(".sonata-tmp");
    std::fs::write(&tmp, out).and_then(|_| std::fs::rename(&tmp, &target)).is_ok()
}

#[cfg(test)]
mod tests {
    //! tests/test_compose.py (Vini: ç couldn't be typed on U.S. International)
    use super::*;

    fn file() -> (tempfile::TempDir, PathBuf) {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join(".XCompose");
        (d, f)
    }

    fn read(f: &Path) -> String {
        std::fs::read_to_string(f).unwrap()
    }

    #[test]
    fn test_new_file_keeps_the_locale_table() {
        let (_d, f) = file();
        assert!(ensure(Some(&f)));
        let text = read(&f);
        assert_eq!(text.lines().nth(1), Some("include \"%L\"")); // every other combination stays
        for r in RULES {
            assert!(text.contains(r));
        }
        ensure(Some(&f));
        assert_eq!(read(&f), text); // once: nothing added again
    }

    #[test]
    fn test_users_own_file_keeps_its_lines() {
        let (_d, f) = file();
        std::fs::write(&f, "include \"%L\"\n<Multi_key> <s> <s> : \"ß\"\n").unwrap();
        ensure(Some(&f));
        let text = read(&f);
        assert!(text.starts_with("include \"%L\"\n<Multi_key> <s> <s> : \"ß\"\n"));
        assert_eq!(text.matches("include \"%L\"").count(), 1);
        assert!(text.contains(RULES[0]));
    }

    #[test]
    fn test_a_file_without_include_gets_it() {
        let (_d, f) = file();
        std::fs::write(&f, "<Multi_key> <s> <s> : \"ß\"").unwrap();
        ensure(Some(&f));
        assert!(read(&f).starts_with("include \"%L\"\n"));
    }

    #[test]
    fn test_users_own_cedilla_rule_left_alone() {
        let (_d, f) = file();
        std::fs::write(&f, "include \"%L\"\n<dead_acute> <c> : \"ç\"\n<dead_acute> <C> : \"Ç\"\n").unwrap();
        let before = read(&f);
        ensure(Some(&f));
        assert_eq!(read(&f), before);
    }
}
