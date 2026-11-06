//! Users & Groups through AccountsService (backend/users.py;
//! org.freedesktop.Accounts on the system bus) -- what GNOME and KDE use:
//! list, create, delete accounts, set the picture, the full name, the
//! account type and the password. Changes to other accounts (and
//! creating/deleting) are authorized by polkit (an admin password prompt).
//! Blocking: call through run_async.

use glib::prelude::*;
use glib::Variant;
use sonata_i18n::tr;
use std::path::PathBuf;

pub const BUS: &str = "org.freedesktop.Accounts";
pub const PATH: &str = "/org/freedesktop/Accounts";
pub const IFACE: &str = "org.freedesktop.Accounts";
pub const USER_IFACE: &str = "org.freedesktop.Accounts.User";
pub const PICTURE_PX: i32 = 256;

#[derive(Clone, Debug, PartialEq)]
pub struct User {
    pub path: String,
    pub uid: i64,
    pub name: String,
    pub real_name: String,
    pub icon: String,
    pub admin: bool,
    pub current: bool,
}

fn bus() -> Result<gio::DBusConnection, glib::Error> {
    gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE)
}

fn call(path: &str, iface: &str, method: &str, args: Option<Variant>, flags: gio::DBusCallFlags, timeout: i32) -> Result<Variant, glib::Error> {
    bus()?.call_sync(Some(BUS), path, iface, method, args.as_ref(), None, flags, timeout, gio::Cancellable::NONE)
}

pub fn available() -> bool {
    call(PATH, "org.freedesktop.DBus.Peer", "Ping", None, gio::DBusCallFlags::NONE, 2000).is_ok()
}

fn props(path: &str) -> Result<std::collections::HashMap<String, Variant>, glib::Error> {
    let v = call(path, "org.freedesktop.DBus.Properties", "GetAll", Some((USER_IFACE,).to_variant()), gio::DBusCallFlags::NONE, 3000)?;
    let d = v.child_value(0);
    Ok((0..d.n_children())
        .filter_map(|i| {
            let e = d.child_value(i);
            let val = e.child_value(1);
            Some((e.child_value(0).str()?.to_string(), val.as_variant().unwrap_or(val)))
        })
        .collect())
}

fn object_paths(v: &Variant) -> Vec<String> {
    (0..v.n_children()).filter_map(|i| v.child_value(i).str().map(String::from)).collect()
}

pub fn users() -> Vec<User> {
    let list = || -> Result<(Vec<String>, String), glib::Error> {
        let paths = object_paths(&call(PATH, IFACE, "ListCachedUsers", None, gio::DBusCallFlags::NONE, 3000)?.child_value(0));
        let me = call(PATH, IFACE, "FindUserById", Some((unsafe { libc::getuid() } as i64,).to_variant()), gio::DBusCallFlags::NONE, 3000)?.child_value(0).str().unwrap_or("").to_string();
        Ok((paths, me))
    };
    let Ok((mut paths, me)) = list() else { return vec![] };
    if !paths.contains(&me) {
        paths.insert(0, me.clone());
    }
    let mut out: Vec<User> = paths
        .into_iter()
        .filter_map(|p| {
            let pr = props(&p).ok()?;
            let s = |k: &str| pr.get(k).and_then(|v| v.str().map(String::from)).unwrap_or_default();
            if pr.get("SystemAccount").and_then(|v| v.get::<bool>()).unwrap_or(false) {
                return None;
            }
            let icon = s("IconFile");
            Some(User {
                uid: pr.get("Uid").and_then(|v| v.get::<u64>()).map(|u| u as i64).unwrap_or(-1),
                name: s("UserName"),
                real_name: s("RealName"),
                icon: if !icon.is_empty() && std::path::Path::new(&icon).is_file() { icon } else { String::new() },
                admin: pr.get("AccountType").and_then(|v| v.get::<i32>()) == Some(1),
                current: p == me,
                path: p,
            })
        })
        .collect();
    out.sort_by_key(|u| (!u.current, if u.real_name.is_empty() { u.name.clone() } else { u.real_name.to_lowercase() }));
    out
}

/// None on success, else the error message.
fn call_user(path: &str, method: &str, args: Variant) -> Option<String> {
    call(path, USER_IFACE, method, Some(args), gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION, 120000).err().map(|e| e.message().to_string())
}

/// Sonata's own user pictures (tools/gen-avatars.py): data/avatars,
/// found as Sonata's other data (next to the installed binary, else this
/// clone's). It was looked for next to the catalogs, a folder the installed
/// build doesn't have: Settings > Users showed no pictures to pick.
pub fn avatars_dir() -> PathBuf {
    sonata_config::data_path("avatars")
}

pub fn stock_pictures() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(avatars_dir()).map(|d| d.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "png")).collect()).unwrap_or_default();
    out.sort();
    out
}

/// The picture cropped to a centred square, 256 px, as a PNG in /tmp:
/// accounts-daemon (root) refuses big files and can't read some home
/// folders, so it always gets a small copy it can read.
fn square_png(file_path: &str) -> Result<PathBuf, glib::Error> {
    use gtk::gdk_pixbuf::{InterpType, Pixbuf};
    let pb = Pixbuf::from_file(file_path)?;
    let pb = pb.apply_embedded_orientation().unwrap_or(pb);
    let side = pb.width().min(pb.height());
    let pb = pb.new_subpixbuf((pb.width() - side) / 2, (pb.height() - side) / 2, side, side);
    let pb = pb.scale_simple(PICTURE_PX, PICTURE_PX, InterpType::Hyper).ok_or_else(|| glib::Error::new(gio::IOErrorEnum::Failed, "scale"))?;
    let out = tempfile::Builder::new().prefix("sonata-picture-").suffix(".png").tempfile_in("/tmp").map_err(|e| glib::Error::new(gio::IOErrorEnum::Failed, &e.to_string()))?;
    let (_f, path) = out.keep().map_err(|e| glib::Error::new(gio::IOErrorEnum::Failed, &e.to_string()))?;
    pb.savev(&path, "png", &[])?;
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644));
    Ok(path)
}

pub fn set_picture(user: &User, file_path: &str) -> Option<String> {
    let small = match square_png(file_path) {
        Ok(p) => p,
        Err(e) => return Some(tr!("This picture can't be used: {error}").replace("{error}", e.message())),
    };
    let err = call_user(&user.path, "SetIconFile", (small.to_string_lossy().to_string(),).to_variant());
    if err.is_none() && user.current {
        // ~/.face too (lock screen, other apps)
        let _ = std::fs::copy(&small, glib::home_dir().join(".face"));
    }
    let _ = std::fs::remove_file(&small);
    err
}

pub fn set_real_name(user: &User, name: &str) -> Option<String> {
    call_user(&user.path, "SetRealName", (name,).to_variant())
}

pub fn set_admin(user: &User, admin: bool) -> Option<String> {
    call_user(&user.path, "SetAccountType", (if admin { 1i32 } else { 0 },).to_variant())
}

/// SHA-512 crypt (what /etc/shadow uses), via openssl.
pub fn crypt(password: &str) -> Option<String> {
    // a stuck openssl must not hang the caller
    let (rc, out) = crate::run_input(&["openssl", "passwd", "-6", "-stdin"], password, 10);
    (rc == 0).then(|| out.trim().to_string()).filter(|h| !h.is_empty())
}

pub fn set_password(user: &User, password: &str, hint: &str) -> Option<String> {
    let Some(hashed) = crypt(password) else { return Some(tr!("openssl is needed to set passwords")) };
    call_user(&user.path, "SetPassword", (hashed, hint).to_variant())
}

pub fn create_user(name: &str, real_name: &str, admin: bool, password: &str) -> Option<String> {
    create_user_with(name, real_name, admin, password, &|args| call(PATH, IFACE, "CreateUser", Some(args), gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION, 120000), &|u, pw| set_password(u, pw, ""))
}

/// create_user with how the account is created and its password set (tests).
pub fn create_user_with(name: &str, real_name: &str, admin: bool, password: &str, create: &dyn Fn(Variant) -> Result<Variant, glib::Error>, set_pw: &dyn Fn(&User, &str) -> Option<String>) -> Option<String> {
    if password.is_empty() {
        // without one the account is locked: no login, not on the login screen
        return Some(tr!("A password is needed"));
    }
    let path = match create((name, real_name, if admin { 1i32 } else { 0 }).to_variant()) {
        Ok(v) => v.child_value(0).str().unwrap_or("").to_string(),
        Err(e) => return Some(e.message().to_string()),
    };
    let u = User { path, uid: -1, name: name.into(), real_name: real_name.into(), icon: String::new(), admin, current: false };
    set_pw(&u, password)
}

pub fn delete_user(user: &User, remove_files: bool) -> Option<String> {
    call(PATH, IFACE, "DeleteUser", Some((user.uid, remove_files).to_variant()), gio::DBusCallFlags::ALLOW_INTERACTIVE_AUTHORIZATION, 120000).err().map(|e| e.message().to_string())
}

pub fn valid_name(name: &str) -> bool {
    let mut c = name.chars();
    c.next().is_some_and(|f| f.is_ascii_lowercase() || f == '_') && name.len() <= 32 && c.all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '_' || x == '-')
}

/// macOS derives the account name from the full name ("Ana Souza" -> "anasouza").
pub fn short_name(real_name: &str) -> String {
    let s: String = glib::normalize(real_name, glib::NormalizeMode::All).chars().filter(|c| c.is_ascii()).collect::<String>().to_lowercase().chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    let s = if !s.is_empty() && !s.starts_with(|c: char| c.is_ascii_digit()) { s } else { format!("user{s}") };
    s.chars().take(32).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn backend_refuses_no_password_and_always_sets_one() {
        let created = RefCell::new(0);
        let create = |_: Variant| -> Result<Variant, glib::Error> {
            *created.borrow_mut() += 1;
            Ok((glib::variant::ObjectPath::try_from("/org/freedesktop/Accounts/User1001".to_string()).unwrap(),).to_variant())
        };
        let pw: RefCell<Vec<(String, String)>> = RefCell::default();
        let set_pw = |u: &User, p: &str| {
            pw.borrow_mut().push((u.path.clone(), p.into()));
            None
        };
        assert_eq!(create_user_with("ana", "Ana", false, "", &create, &set_pw), Some(tr!("A password is needed")));
        assert_eq!(*created.borrow(), 0);
        assert_eq!(create_user_with("ana", "Ana", false, "s3cret", &create, &set_pw), None);
        assert_eq!(*pw.borrow(), [("/org/freedesktop/Accounts/User1001".to_string(), "s3cret".to_string())]);
    }

    #[test]
    fn names() {
        assert_eq!(short_name("Ana Souza"), "anasouza");
        assert_eq!(short_name("José Conceição"), "joseconceicao");
        assert_eq!(short_name("42 Bob"), "user42bob");
        assert_eq!(short_name("日本"), "user");
        assert!(valid_name("ana_s-1") && valid_name("_x"));
        assert!(!valid_name("Ana") && !valid_name("1ana") && !valid_name("") && !valid_name(&"a".repeat(33)));
    }

    /// The installed build looked for the stock pictures in
    /// share/sonata2-rs/data/avatars (next to its copied catalogs), which
    /// doesn't exist: the picture picker was empty.
    #[test]
    fn test_stock_pictures_missing_when_installed() {
        assert_eq!(avatars_dir(), sonata_config::data_path("avatars"));
        assert!(!stock_pictures().is_empty());
    }

    /// A stuck openssl hung the caller: it is given up on.
    #[test]
    fn stuck_tool_gives_up() {
        assert_eq!(crate::run_input(&["sleep", "5"], "", 0).0, 1);
        assert_eq!(crate::run_input(&["cat"], "pw", 5), (0, "pw".into()));
    }
}
