//! Uninstalling an app (backend/uninstall.py: dropped on the Trash, or
//! Launchpad's "Move to Trash").
//!
//! Who owns the app's desktop entry decides how: flatpak (exported by
//! Flatpak: flatpak uninstall), package (owned by the distro's package:
//! pacman -Rns / apt remove / dnf remove / zypper remove) or shortcut (a
//! file in ~/.local/share/applications no package owns: the file goes to
//! the Trash). Sonata's own apps are never uninstalled. Before a package
//! goes, what else would leave with it is asked and shown.

use gio::prelude::*;
use sonata_backend::run::which;
use sonata_desktop::sandbox::flatpak_id_of;
use std::path::Path;

/// Sonata's own apps (apps.PROTECTED, and the Rust build's): part of Sonata
pub const PROTECTED: [&str; 3] = ["io.github.vinioliveiras.sonata2.", "io.github.vinioliveiras.sonata2rs.", "sonata2-"];

#[derive(Clone, Debug, PartialEq)]
pub struct Owner {
    /// flatpak / package / shortcut
    pub kind: String,
    /// flatpak app id, package name or file path
    pub name: String,
    /// pacman / apt / dnf / zypper / flatpak-user / flatpak-system
    pub manager: String,
}

impl Owner {
    pub fn new(kind: &str, name: &str, manager: &str) -> Self {
        Self { kind: kind.into(), name: name.into(), manager: manager.into() }
    }

    /// Packages removed along (no longer needed); [] when none or unknown.
    /// Blocking.
    pub fn also(&self) -> Vec<String> {
        if self.kind != "package" {
            return vec![];
        }
        let cmd: &[&str] = match self.manager.as_str() {
            "pacman" => &["pacman", "-Rs", "--print", "--print-format", "%n", &self.name],
            "apt" => &["apt-get", "-s", "remove", "--autoremove", &self.name],
            _ => return vec![],
        };
        let (_rc, out) = crate::run_c(cmd, 30);
        self.also_from(&out)
    }

    pub fn also_from(&self, out: &str) -> Vec<String> {
        let names: Vec<String> = if self.manager == "pacman" {
            out.lines().map(str::trim).filter(|l| !l.is_empty() && !l.contains(' ')).map(String::from).collect()
        } else {
            out.lines().filter(|l| l.starts_with("Remv ")).filter_map(|l| l.split_whitespace().nth(1).map(String::from)).collect()
        };
        names.into_iter().filter(|n| *n != self.name).collect()
    }

    /// [argv] for updates::Runner (root via pkexec).
    pub fn steps(&self) -> Vec<Vec<String>> {
        let v = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let n = self.name.as_str();
        match (self.kind.as_str(), self.manager.as_str()) {
            ("flatpak", m) => vec![v(&["flatpak", "uninstall", "-y", "--noninteractive", if m == "flatpak-user" { "--user" } else { "--system" }, n])],
            ("package", "pacman") => vec![v(&["pkexec", "pacman", "-Rns", "--noconfirm", n])],
            ("package", "apt") => vec![v(&["pkexec", "apt-get", "remove", "-y", "--autoremove", n])],
            ("package", "dnf") => vec![v(&["pkexec", "dnf", "remove", "-y", n])],
            ("package", "zypper") => vec![v(&["pkexec", "zypper", "--non-interactive", "remove", "--clean-deps", n])],
            _ => vec![],
        }
    }
}

fn run(cmd: &[&str]) -> String {
    let (rc, out) = crate::run_c(cmd, 15);
    if rc == 0 { out.trim().to_string() } else { String::new() }
}

/// The .desktop file behind an app ("" when there is none).
pub fn app_filename(info: &gio::AppInfo) -> String {
    info.downcast_ref::<gio::DesktopAppInfo>().and_then(|d| d.filename()).map(|p| p.to_string_lossy().into_owned()).unwrap_or_default()
}

pub fn owner(info: &gio::AppInfo) -> Option<Owner> {
    let did = info.id().map(|s| s.to_string()).unwrap_or_default();
    owner_of(&did, &app_filename(info), &glib::user_data_dir())
}

/// owner() from the app's id, its desktop file and the user's data folder.
pub fn owner_of(did: &str, listed: &str, user_data: &Path) -> Option<Owner> {
    if PROTECTED.iter().any(|p| did.starts_with(p)) || listed.is_empty() {
        return None; // Files, Settings, Launchpad...: part of Sonata
    }
    let path = std::fs::canonicalize(listed).unwrap_or_else(|_| listed.into());
    // Flatpak: exports/share/applications/<id>.desktop is a link into
    // flatpak/app/<id>/current/active/export/... -- either spelling counts
    let mut fp = flatpak_id_of(listed);
    if fp.is_empty() {
        fp = flatpak_id_of(&path.to_string_lossy());
    }
    let real_data = std::fs::canonicalize(user_data).unwrap_or_else(|_| user_data.into());
    if !fp.is_empty() {
        let user = path.starts_with(&real_data) || Path::new(listed).starts_with(user_data);
        return Some(Owner::new("flatpak", &fp, if user { "flatpak-user" } else { "flatpak-system" }));
    }
    let p = path.to_string_lossy().to_string();
    let cmds: [(&str, Vec<&str>); 4] = [("pacman", vec!["pacman", "-Qqo", &p]), ("apt", vec!["dpkg-query", "-S", &p]), ("dnf", vec!["rpm", "-qf", "--qf", "%{NAME}", &p]), ("zypper", vec!["rpm", "-qf", "--qf", "%{NAME}", &p])];
    for (manager, cmd) in cmds {
        if (manager == "zypper" || manager == "dnf") && which(manager).is_none() {
            continue;
        }
        let out = run(&cmd);
        if !out.is_empty() {
            let name = if manager == "apt" { out.split(':').next().unwrap_or("").trim().to_string() } else { out.lines().next().unwrap_or("").trim().to_string() };
            return Some(Owner::new("package", &name, manager));
        }
    }
    if path.parent() == Some(&real_data.join("applications")) {
        return Some(Owner::new("shortcut", &p, ""));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(folder: &Path, name: &str) -> String {
        std::fs::create_dir_all(folder).unwrap();
        let p = folder.join(format!("{name}.desktop"));
        std::fs::write(&p, "[Desktop Entry]\nType=Application\nName=T\nExec=true\n").unwrap();
        p.to_string_lossy().into_owned()
    }

    #[test]
    fn kinds() {
        let data = tempfile::tempdir().unwrap();
        let d = data.path();
        let o = owner_of("myshortcut.desktop", &entry(&d.join("applications"), "myshortcut"), d).unwrap();
        assert_eq!((o.kind.as_str(), o.steps()), ("shortcut", vec![]));
        let o = owner_of("org.x.App.desktop", &entry(&d.join("flatpak/exports/share/applications"), "org.x.App"), d).unwrap();
        assert_eq!((o.kind.as_str(), o.name.as_str(), o.manager.as_str()), ("flatpak", "org.x.App", "flatpak-user"));
        assert!(o.steps()[0].contains(&"--user".to_string()));
        // system Flatpak: the exported entry is a link into flatpak/app/<id>/...
        let root = tempfile::tempdir().unwrap();
        let real = entry(&root.path().join("flatpak/app/org.gimp.GIMP/current/active/export/share/applications"), "org.gimp.GIMP");
        let exports = root.path().join("flatpak/exports/share/applications");
        std::fs::create_dir_all(&exports).unwrap();
        let link = exports.join("org.gimp.GIMP.desktop");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        let o = owner_of("org.gimp.GIMP.desktop", &link.to_string_lossy(), d).unwrap();
        assert_eq!((o.kind.as_str(), o.name.as_str(), o.manager.as_str()), ("flatpak", "org.gimp.GIMP", "flatpak-system"));
        assert_eq!(flatpak_id_of(&real), "org.gimp.GIMP");
        assert_eq!(Owner::new("package", "foo", "pacman").steps()[0][..3], ["pkexec", "pacman", "-Rns"]);
        assert_eq!(owner_of("io.github.vinioliveiras.sonata2.files.desktop", "/x.desktop", d), None);
    }

    #[test]
    fn what_leaves_with_it() {
        assert_eq!(Owner::new("package", "foo", "pacman").also_from("foo\nlibfoo\nwarning: x y\n"), ["libfoo"]);
        assert_eq!(Owner::new("package", "foo", "apt").also_from("Remv foo [1]\nRemv libfoo [2]\nInst x\n"), ["libfoo"]);
    }
}
