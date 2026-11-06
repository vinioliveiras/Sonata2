//! Who can log in, and to what (shell/greeter.py's users() and
//! sessions()): the accounts AccountsService lists (else /etc/passwd's
//! people), and the Wayland sessions installed. The login screen itself
//! isn't ported yet; the welcome screen asks whether it had a link row.

use gio::prelude::*;

pub const SESSION_DIRS: [&str; 2] = ["/usr/local/share/wayland-sessions", "/usr/share/wayland-sessions"];
/// Wayfire's own entries: Sonata's session runs it
pub const HIDDEN_SESSIONS: [&str; 2] = ["wayfire", "wayfire-uwsm"];

#[derive(Clone, Debug, PartialEq)]
pub struct User {
    pub name: String,
    pub real: String,
    pub icon: String,
}

fn accounts() -> Option<Vec<User>> {
    let bus = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE).ok()?;
    let paths = bus.call_sync(Some("org.freedesktop.Accounts"), "/org/freedesktop/Accounts", "org.freedesktop.Accounts", "ListCachedUsers", None, Some(glib::VariantTy::new("(ao)").ok()?), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE).ok()?;
    let paths: Vec<glib::variant::ObjectPath> = paths.child_value(0).get()?;
    let mut found = Vec::new();
    for p in paths {
        let Ok(props) = bus.call_sync(Some("org.freedesktop.Accounts"), p.as_str(), "org.freedesktop.DBus.Properties", "GetAll", Some(&("org.freedesktop.Accounts.User",).to_variant()), Some(glib::VariantTy::new("(a{sv})").ok()?), gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE) else { continue };
        let props = glib::VariantDict::new(Some(&props.child_value(0)));
        let flag = |k: &str| props.lookup::<bool>(k).ok().flatten().unwrap_or(false);
        if flag("SystemAccount") || flag("Locked") {
            continue;
        }
        let s = |k: &str| props.lookup::<String>(k).ok().flatten().unwrap_or_default();
        found.push(User { name: s("UserName"), real: s("RealName"), icon: s("IconFile") });
    }
    Some(found)
}

/// People in /etc/passwd: uid 1000-59999 with a shell they can log in with.
pub fn passwd_users(text: &str) -> Vec<User> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split(':').collect();
            let uid: u32 = f.get(2)?.parse().ok()?;
            let shell = f.get(6)?;
            ((1000..60000).contains(&uid) && !shell.ends_with("nologin") && !shell.ends_with("false")).then(|| User { name: f[0].into(), real: f.get(4).unwrap_or(&"").split(',').next().unwrap_or("").into(), icon: String::new() })
        })
        .collect()
}

pub fn users() -> Vec<User> {
    let mut found = accounts().unwrap_or_default();
    if found.is_empty() {
        found = passwd_users(&std::fs::read_to_string("/etc/passwd").unwrap_or_default());
    }
    found.retain(|u| !u.name.is_empty());
    found.sort_by_key(|u| u.real.to_lowercase());
    found
}

/// The session entries the login screen offers (their file names, no ".desktop").
pub fn session_keys(dirs: &[&str]) -> Vec<String> {
    let mut seen = Vec::new();
    for d in dirs {
        let Ok(rd) = std::fs::read_dir(d) else { continue };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for n in names {
            let Some(key) = n.strip_suffix(".desktop") else { continue };
            if seen.iter().any(|s: &String| s == key) || HIDDEN_SESSIONS.contains(&key) {
                continue;
            }
            let kf = glib::KeyFile::new();
            if kf.load_from_file(std::path::Path::new(d).join(&n), glib::KeyFileFlags::NONE).is_err() {
                continue;
            }
            let hidden = |k: &str| kf.boolean("Desktop Entry", k).unwrap_or(false);
            if hidden("Hidden") || hidden("NoDisplay") || kf.string("Desktop Entry", "Exec").is_err() {
                continue;
            }
            seen.push(key.to_string());
        }
    }
    seen
}

pub fn sessions() -> Vec<String> {
    session_keys(&SESSION_DIRS)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn people_in_passwd() {
        let text = "root:x:0:0::/root:/bin/bash\nvini:x:1000:1000:Vini Oliveira,,,:/home/vini:/usr/bin/fish\nnobody:x:65534:65534:Nobody:/:/usr/bin/nologin\nsvc:x:1001:1001::/:/bin/false\n";
        assert_eq!(passwd_users(text), [User { name: "vini".into(), real: "Vini Oliveira".into(), icon: String::new() }]);
    }

    #[test]
    fn sessions_without_wayfire_and_hidden_ones() {
        let d = tempfile::tempdir().unwrap();
        let w = |n: &str, extra: &str| std::fs::write(d.path().join(n), format!("[Desktop Entry]\nName=X\nExec=x\n{extra}")).unwrap();
        w("sonata.desktop", "");
        w("wayfire.desktop", "");
        w("gnome.desktop", "NoDisplay=true\n");
        w("plasma.desktop", "");
        std::fs::write(d.path().join("broken.desktop"), "[Desktop Entry]\nName=Y\n").unwrap();
        assert_eq!(session_keys(&[d.path().to_str().unwrap()]), ["plasma", "sonata"]);
    }
}
