//! Installing an AppImage (files/packages.py's install_appimage; Vini,
//! its right-click): asked first, like opening it; then it's copied into
//! Applications with a launcher of its own (sonata_system::appimages) and
//! shows in the Apps Menu. Installing trusts it too, so the first run
//! doesn't ask again.

use gio::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, n_, tr};
use std::os::unix::fs::MetadataExt;
use std::path::Path;

/// names.APPS_MENU: the same apps in a panel (Launchpad)
const APPS_MENU: &str = n_!("Apps Menu");

pub fn is_appimage(path: &Path) -> bool {
    path.file_name().is_some_and(|n| n.to_string_lossy().to_lowercase().ends_with(".appimage"))
}

/// What makes an AppImage "the same file" for the first-run question: its
/// real path, size and mtime (a replaced file is asked about again). The
/// same key and text as the Python Files, which reads the same list.
pub fn stamp(path: &Path) -> std::io::Result<(String, String)> {
    let st = std::fs::metadata(path)?;
    let real = std::fs::canonicalize(path)?;
    let ns = st.mtime() as i128 * 1_000_000_000 + st.mtime_nsec() as i128;
    Ok((real.to_string_lossy().into_owned(), format!("{}:{ns}", st.size())))
}

fn trusted_list() -> Map<String, Value> {
    sonata_config::load("files", &json!({"trusted_appimages": {}})).get("trusted_appimages").and_then(Value::as_object).cloned().unwrap_or_default()
}

pub fn trusted(path: &Path) -> bool {
    stamp(path).is_ok_and(|(k, s)| trusted_list().get(&k).and_then(Value::as_str) == Some(s.as_str()))
}

pub fn trust(path: &Path) {
    let Ok((k, s)) = stamp(path) else { return };
    let mut list = trusted_list();
    list.insert(k, Value::String(s));
    let _ = sonata_config::update("files", &json!({"trusted_appimages": list}));
}

/// A notification, like the Python Files' (shell/uninstall_ui._notify).
fn notify(summary: &str, body: &str) {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
    let hints = glib::VariantDict::new(None).end();
    let args = glib::Variant::tuple_from_iter([
        "Sonata".to_variant(),
        0u32.to_variant(),
        "user-trash-full".to_variant(),
        summary.to_variant(),
        body.to_variant(),
        Vec::<String>::new().to_variant(),
        hints,
        (-1i32).to_variant(),
    ]);
    bus.call(Some("org.freedesktop.Notifications"), "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify", Some(&args), None, gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE, |_| {});
}

/// Ask, then install off the main loop and say how it went.
pub fn install(path: &Path, parent: Option<&gtk::Widget>) {
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let apps_menu = sonata_i18n::gettext(APPS_MENU);
    let body = fmt(&tr!("It's copied into the Applications folder in your home folder and shows in the {apps_menu}. This app will run with your permissions and can access your files. Only install apps from developers you trust."), &[("apps_menu", &apps_menu)]);
    let (path, p) = (path.to_path_buf(), parent.map(|w| w.downgrade()));
    sonata_ui::dialog::alert(&fmt(&tr!("Install “{name}”?"), &[("name", &name)]), &body, &[("cancel", tr!("Cancel"), ""), ("install", tr!("Install"), "default")], move |rid| {
        if rid != "install" {
            return;
        }
        let (src, p, name, apps_menu) = (path.clone(), p.clone(), name.clone(), apps_menu.clone());
        sonata_system::run_async(move || sonata_system::appimages::install(&src), move |res| match res {
            Ok(done) => {
                trust(&done.appimage);
                notify(&fmt(&tr!("“{name}” was installed"), &[("name", &done.name)]), &fmt(&tr!("It's in the {apps_menu}."), &[("apps_menu", &apps_menu)]));
            }
            Err(e) => {
                let parent = p.as_ref().and_then(|w| w.upgrade());
                sonata_ui::dialog::alert(&fmt(&tr!("“{name}” can’t be installed."), &[("name", &name)]), &e.to_string(), &[("ok", "OK".into(), "default")], |_| {}, parent.as_ref());
            }
        });
    }, parent);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn appimages_by_name() {
        assert!(is_appimage(Path::new("/d/Krita-5.2.AppImage")));
        assert!(is_appimage(Path::new("/d/x.appimage")));
        assert!(!is_appimage(Path::new("/d/x.AppImage.zip")));
    }

    /// Installing trusts the copy; a file changed since is asked about again.
    #[test]
    fn trust_follows_the_file() {
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("Tool.AppImage");
        std::fs::write(&f, b"one").unwrap();
        assert!(!trusted(&f));
        trust(&f);
        assert!(trusted(&f));
        let (key, s) = stamp(&f).unwrap();
        assert_eq!(key, std::fs::canonicalize(&f).unwrap().to_string_lossy());
        assert!(s.starts_with("3:"));
        std::fs::write(&f, b"longer").unwrap();
        assert!(!trusted(&f));
    }

    #[test]
    fn offered_in_the_menu() {
        assert!(include_str!("packages.rs").contains("crate::appimage::install("));
        assert!(include_str!("window.rs").contains("crate::packages::menu_items("));
    }
}
