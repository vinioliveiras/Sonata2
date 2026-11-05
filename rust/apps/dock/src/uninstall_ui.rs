//! Asking before uninstalling an app, then doing it (shell/uninstall_ui.py:
//! the Dock's Trash, Launchpad).
//!
//! The question names what goes: the package (and the ones no longer
//! needed with it), the Flatpak, or just the shortcut. Root steps ask for
//! the password with Sonata's own prompt (polkit, through pkexec). A
//! notification says when it's done, or why it couldn't be.
//!
//! Who owns the app (backend/uninstall.py) is ported here, its only users
//! being the Dock and Launchpad. A web app is deleted with its login and
//! data (ask_webapp, through the Rust webapps).

use crate::dock::is_protected;
use gtk::prelude::*;
use gtk::{gio, glib};
use sonata_i18n::{tr, trn};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::rc::Rc;
use std::time::Duration;

pub const MAX_LISTED: usize = 8;
/// the launcher of an AppImage installed from Files names its file
pub const APPIMAGE_KEY: &str = "X-Sonata-AppImage";

#[derive(Clone, Debug, PartialEq)]
pub enum Kind {
    Flatpak,
    Package,
    Shortcut,
}

/// What uninstalling the app means.
#[derive(Clone, Debug, PartialEq)]
pub struct Owner {
    pub kind: Kind,
    /// flatpak app id, package name or file path
    pub name: String,
    /// pacman / apt / dnf / zypper / flatpak-user / flatpak-system
    pub manager: String,
}

fn run(cmd: &[&str], timeout_note: &str) -> String {
    if glib::find_program_in_path(cmd[0]).is_none() {
        return String::new();
    }
    match Command::new(cmd[0]).args(&cmd[1..]).env("LC_ALL", "C").output() {
        Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Ok(_) => String::new(),
        Err(e) => {
            eprintln!("sonata2-dock: {timeout_note}: {e}");
            String::new()
        }
    }
}

impl Owner {
    /// Packages removed along (no longer needed); [] when none or unknown.
    pub fn also(&self) -> Vec<String> {
        if self.kind != Kind::Package {
            return Vec::new();
        }
        let out = match self.manager.as_str() {
            "pacman" => run(&["pacman", "-Rs", "--print", "--print-format", "%n", &self.name], "pacman"),
            "apt" => run(&["apt-get", "-s", "remove", "--autoremove", &self.name], "apt-get"),
            _ => return Vec::new(),
        };
        also_from(&self.manager, &out, &self.name)
    }

    /// The commands, one after the other (root through pkexec).
    pub fn steps(&self) -> Vec<Vec<String>> {
        let v = |a: &[&str]| a.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        let n = self.name.as_str();
        match self.kind {
            Kind::Flatpak => vec![v(&["flatpak", "uninstall", "-y", "--noninteractive", if self.manager == "flatpak-user" { "--user" } else { "--system" }, n])],
            Kind::Package => match self.manager.as_str() {
                "pacman" => vec![v(&["pkexec", "pacman", "-Rns", "--noconfirm", n])],
                "apt" => vec![v(&["pkexec", "apt-get", "remove", "-y", "--autoremove", n])],
                "dnf" => vec![v(&["pkexec", "dnf", "remove", "-y", n])],
                "zypper" => vec![v(&["pkexec", "zypper", "--non-interactive", "remove", "--clean-deps", n])],
                _ => Vec::new(),
            },
            Kind::Shortcut => Vec::new(),
        }
    }
}

/// The packages a removal takes along, from the package manager's dry run.
pub fn also_from(manager: &str, out: &str, name: &str) -> Vec<String> {
    let names: Vec<String> = if manager == "pacman" {
        out.lines().map(str::trim).filter(|l| !l.is_empty() && !l.contains(' ')).map(String::from).collect()
    } else {
        out.lines().filter(|l| l.starts_with("Remv ")).filter_map(|l| l.split_whitespace().nth(1)).map(String::from).collect()
    };
    names.into_iter().filter(|n| n != name).collect()
}

/// Flatpak: exports/share/applications/<id>.desktop is a link into
/// flatpak/app/<id>/current/active/export/... -- either spelling counts.
pub fn flatpak_id(path: &str) -> String {
    if path.contains("/flatpak/exports/") && path.ends_with(".desktop") {
        return Path::new(path).file_name().map(|n| n.to_string_lossy().trim_end_matches(".desktop").to_string()).unwrap_or_default();
    }
    let parts: Vec<&str> = path.split('/').collect();
    for i in 0..parts.len().saturating_sub(2) {
        if parts[i] == "flatpak" && parts[i + 1] == "app" {
            return parts[i + 2].to_string();
        }
    }
    String::new()
}

/// Who owns the app's entry (None: Sonata's own, or nobody known).
pub fn owner(id: &str, listed: Option<PathBuf>) -> Option<Owner> {
    if is_protected(id) {
        return None; // Files, Settings, Launchpad...: part of Sonata
    }
    let listed = listed?;
    let path = std::fs::canonicalize(&listed).unwrap_or_else(|_| listed.clone());
    let (ls, ps) = (listed.to_string_lossy().to_string(), path.to_string_lossy().to_string());
    let fp = Some(flatpak_id(&ls)).filter(|f| !f.is_empty()).unwrap_or_else(|| flatpak_id(&ps));
    if !fp.is_empty() {
        let data = glib::user_data_dir();
        let user = path.starts_with(std::fs::canonicalize(&data).unwrap_or(data.clone())) || listed.starts_with(&data);
        return Some(Owner { kind: Kind::Flatpak, name: fp, manager: if user { "flatpak-user" } else { "flatpak-system" }.into() });
    }
    let probes: [(&str, Vec<&str>); 4] = [("pacman", vec!["pacman", "-Qqo", &ps]), ("apt", vec!["dpkg-query", "-S", &ps]), ("dnf", vec!["rpm", "-qf", "--qf", "%{NAME}", &ps]), ("zypper", vec!["rpm", "-qf", "--qf", "%{NAME}", &ps])];
    for (manager, cmd) in probes {
        if ["dnf", "zypper"].contains(&manager) && glib::find_program_in_path(manager).is_none() {
            continue;
        }
        let out = run(&cmd, manager);
        if !out.is_empty() {
            let name = if manager == "apt" { out.split(':').next().unwrap_or("").trim().to_string() } else { out.lines().next().unwrap_or("").trim().to_string() };
            return Some(Owner { kind: Kind::Package, name, manager: manager.into() });
        }
    }
    let apps = glib::user_data_dir().join("applications");
    if path.parent() == Some(std::fs::canonicalize(&apps).unwrap_or(apps).as_path()) {
        return Some(Owner { kind: Kind::Shortcut, name: ps, manager: String::new() });
    }
    None
}

/// The AppImage an installed launcher runs (appimages.file_of), or None.
pub fn appimage_of(desktop_path: &str) -> Option<String> {
    let text = std::fs::read_to_string(desktop_path).ok()?;
    let path = text.lines().find_map(|l| l.strip_prefix(&format!("{APPIMAGE_KEY}=")))?.trim().to_string();
    Path::new(&path).is_file().then_some(path)
}

fn notify(summary: &str, body: &str) {
    sonata_desktop::notify::send(summary, body, "Sonata", "user-trash-full", "", false);
}

fn listed(also: &[String]) -> String {
    let mut text = also.iter().take(MAX_LISTED).cloned().collect::<Vec<_>>().join(", ");
    if also.len() > MAX_LISTED {
        let more = also.len() - MAX_LISTED;
        text = sonata_i18n::fmt(&trn!("{list} and {n} more", "{list} and {n} more", more), &[("list", &text), ("n", &more.to_string())]);
    }
    text
}

pub type Done = Rc<dyn Fn(bool)>;

/// The question's title: the web app by its name, "this web app" when
/// its entry is gone.
pub fn webapp_title(entry: Option<&serde_json::Map<String, serde_json::Value>>) -> String {
    let name = entry.and_then(|e| e.get("name")).and_then(serde_json::Value::as_str).filter(|n| !n.is_empty()).map(String::from).unwrap_or_else(|| tr!("this web app"));
    sonata_i18n::fmt(&tr!("Delete “{name}”?"), &[("name", &name)])
}

/// A web app: deleted with its login and data (Launchpad, the Dock's
/// Trash -- Vini: web apps can be uninstalled too).
pub fn ask_webapp(wid: &str, parent: Option<&gtk::Widget>, done: Option<Done>) {
    let wid = wid.to_string();
    let title = webapp_title(sonata_webapps::get(&wid).as_ref());
    sonata_ui::dialog::alert(&title, &tr!("Its login and everything it saved on this computer are deleted too."), &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")], move |rid| {
        if rid != "delete" {
            return;
        }
        sonata_webapps::remove(&wid); // its entry goes: Launchpad and the Dock drop it
        sonata_backend::sounds::play("trash");
        if let Some(d) = &done {
            d(true);
        }
    }, parent);
}

pub fn ask(info: &gio::DesktopAppInfo, parent: Option<&gtk::Widget>, done: Option<Done>) {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    if sonata_desktop::badges::is_webapp(&id) {
        ask_webapp(&sonata_webapps::id_of(&id), parent, done);
        return;
    }
    let name = sonata_ui::apps::display_name(info);
    let file = sonata_ui::apps::app_filename(info);
    let parent = parent.cloned();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let o = owner(&id, file);
        let also = o.as_ref().map(Owner::also).unwrap_or_default();
        let _ = tx.send((o, also));
    });
    glib::timeout_add_local(Duration::from_millis(50), move || {
        let (o, also) = match rx.try_recv() {
            Ok(v) => v,
            Err(std::sync::mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
            Err(_) => return glib::ControlFlow::Break,
        };
        looked(o, also, &name, parent.as_ref(), done.clone());
        glib::ControlFlow::Break
    });
}

fn looked(o: Option<Owner>, also: Vec<String>, name: &str, parent: Option<&gtk::Widget>, done: Option<Done>) {
    let Some(o) = o else {
        sonata_ui::dialog::alert(&sonata_i18n::fmt(&tr!("“{name}” can't be uninstalled here."), &[("name", name)]), &tr!("It's part of the system or of Sonata, or its package couldn't be found."), &[("ok", tr!("OK"), "default")], |_| {}, parent);
        return;
    };
    let body = match o.kind {
        Kind::Shortcut if appimage_of(&o.name).is_some() => tr!("It and its shortcut are moved to the Trash."),
        Kind::Shortcut => tr!("Its shortcut is moved to the Trash."),
        Kind::Flatpak => sonata_i18n::fmt(&tr!("The Flatpak app {name} is removed. Its settings and data stay in your home folder."), &[("name", &o.name)]),
        Kind::Package => {
            let mut b = sonata_i18n::fmt(&tr!("The package “{name}” is removed."), &[("name", &o.name)]);
            if !also.is_empty() {
                b.push(' ');
                b.push_str(&sonata_i18n::fmt(&tr!("No longer needed, these go too: {list}."), &[("list", &listed(&also))]));
            }
            b
        }
    };
    let name = name.to_string();
    sonata_ui::dialog::alert(&sonata_i18n::fmt(&tr!("Uninstall “{name}”?"), &[("name", &name)]), &body, &[("cancel", tr!("Cancel"), ""), ("go", tr!("Uninstall"), "destructive")], move |rid| {
        if rid == "go" {
            uninstall(&o, &name, done.clone());
        }
    }, parent);
}

fn uninstall(o: &Owner, name: &str, done: Option<Done>) {
    if o.kind == Kind::Shortcut {
        // an AppImage installed from Files: it goes too
        let app = appimage_of(&o.name);
        let res = gio::File::for_path(&o.name).trash(gio::Cancellable::NONE).and_then(|_| match &app {
            Some(a) => gio::File::for_path(a).trash(gio::Cancellable::NONE),
            None => Ok(()),
        });
        match res {
            Ok(()) => {
                sonata_backend::sounds::play("trash");
                if let Some(d) = done {
                    d(true);
                }
            }
            Err(e) => notify(&sonata_i18n::fmt(&tr!("“{name}” couldn't be removed"), &[("name", name)]), e.message()),
        }
        return;
    }
    // the steps one after the other, stopping at the first failure
    // (updates.Runner); the last lines kept for the notification
    let steps = o.steps();
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut tail: Vec<String> = Vec::new();
        let mut ok = true;
        for argv in steps {
            tail.push(format!("$ {}", argv.join(" ")));
            match Command::new(&argv[0]).args(&argv[1..]).env("LC_ALL", "C").output() {
                Ok(out) => {
                    let text = String::from_utf8_lossy(&out.stdout).to_string() + &String::from_utf8_lossy(&out.stderr);
                    tail.extend(text.lines().filter(|l| !l.trim().is_empty()).map(String::from));
                    if !out.status.success() {
                        ok = false;
                        break;
                    }
                }
                Err(e) => {
                    tail.push(e.to_string());
                    ok = false;
                    break;
                }
            }
        }
        let n = tail.len();
        let _ = tx.send((ok, tail[n.saturating_sub(3)..].join("\n")));
    });
    let name = name.to_string();
    glib::timeout_add_local(Duration::from_millis(200), move || {
        let (ok, tail) = match rx.try_recv() {
            Ok(v) => v,
            Err(std::sync::mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
            Err(_) => return glib::ControlFlow::Break,
        };
        if ok {
            sonata_backend::sounds::play("trash");
            notify(&sonata_i18n::fmt(&tr!("“{name}” was uninstalled"), &[("name", &name)]), "");
        } else {
            notify(&sonata_i18n::fmt(&tr!("“{name}” couldn't be uninstalled"), &[("name", &name)]), &tail);
        }
        if let Some(d) = &done {
            d(ok);
        }
        glib::ControlFlow::Break
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flatpak_ids_from_either_spelling() {
        assert_eq!(flatpak_id("/var/lib/flatpak/exports/share/applications/org.gimp.GIMP.desktop"), "org.gimp.GIMP");
        assert_eq!(flatpak_id("/var/lib/flatpak/app/org.gimp.GIMP/current/active/export/share/applications/org.gimp.GIMP.desktop"), "org.gimp.GIMP");
        assert_eq!(flatpak_id("/usr/share/applications/gimp.desktop"), "");
    }

    /// test_uninstall: Sonata's own apps are never uninstalled.
    #[test]
    fn sonatas_own_apps_have_no_owner() {
        assert_eq!(owner("io.github.vinioliveiras.sonata2.files.desktop", Some(PathBuf::from("/x.desktop"))), None);
        assert_eq!(owner("sonata2-launchpad.desktop", Some(PathBuf::from("/x.desktop"))), None);
    }

    /// A web app is asked about by its name; one whose entry is gone
    /// still gets a question that reads.
    #[test]
    fn web_apps_are_asked_by_name() {
        let mut e = serde_json::Map::new();
        e.insert("name".into(), serde_json::json!("WhatsApp"));
        // (in the language the tests run in)
        let ask = |name: &str| sonata_i18n::fmt(&tr!("Delete “{name}”?"), &[("name", name)]);
        assert_eq!(webapp_title(Some(&e)), ask("WhatsApp"));
        assert_eq!(webapp_title(None), ask(&tr!("this web app")));
    }

    #[test]
    fn what_goes_along() {
        assert_eq!(also_from("pacman", "gimp\nbabl\ngegl\n", "gimp"), ["babl", "gegl"]);
        assert_eq!(also_from("apt", "Inst x\nRemv gimp [2.10]\nRemv libgimp [2.10]\n", "gimp"), ["libgimp"]);
        let o = Owner { kind: Kind::Package, name: "gimp".into(), manager: "pacman".into() };
        assert_eq!(o.steps(), vec![vec!["pkexec", "pacman", "-Rns", "--noconfirm", "gimp"]]);
        let f = Owner { kind: Kind::Flatpak, name: "org.gimp.GIMP".into(), manager: "flatpak-user".into() };
        assert!(f.steps()[0].contains(&"--user".to_string()));
        assert!(Owner { kind: Kind::Shortcut, name: "/a".into(), manager: String::new() }.steps().is_empty());
    }

    #[test]
    fn appimage_launchers_name_their_file() {
        let dir = tempfile::tempdir().unwrap();
        let img = dir.path().join("App.AppImage");
        std::fs::write(&img, "x").unwrap();
        let entry = dir.path().join("app.desktop");
        std::fs::write(&entry, format!("[Desktop Entry]\nName=App\n{APPIMAGE_KEY}={}\n", img.display())).unwrap();
        assert_eq!(appimage_of(&entry.to_string_lossy()), Some(img.to_string_lossy().to_string()));
        std::fs::write(&entry, "[Desktop Entry]\nName=App\n").unwrap();
        assert_eq!(appimage_of(&entry.to_string_lossy()), None);
    }
}
