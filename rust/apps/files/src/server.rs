//! Finder's Go > Connect to Server (⌘K -- Ctrl+K), files/server.py: a
//! server's address, its shared folder opened like any other. Windows
//! file shares (smb://), SSH (sftp://), WebDAV (dav:// davs://), FTP and
//! NFS, through GIO / gvfs; a name and password are asked for when the
//! server wants them (mountop.rs), and can be kept in the keyring. A
//! connected server shows in the sidebar's Locations with an eject button,
//! like a disk.
//!
//! An address without a kind is a Windows share (Finder's default too):
//! "nas/fotos" -> smb://nas/fotos. The servers used last are listed under
//! the address (a click fills it in, a double-click connects, x forgets it).
//!
//! And Network in the sidebar (when gvfs can browse it): the computers and
//! shares nearby.

use adw::prelude::*;
use gtk::pango;
use serde_json::{json, Value};
use sonata_i18n::{fmt, tr};
use std::collections::HashSet;
use std::rc::Rc;

const SCHEMES: [&str; 9] = ["smb", "sftp", "ssh", "ftp", "ftps", "dav", "davs", "nfs", "afp"];
pub const RECENT_MAX: usize = 10;
pub const NETWORK: &str = "network:///";
const CONFIG: &str = "files-servers";

/// The package that brings this kind of server.
fn needs(scheme: &str) -> &'static str {
    match scheme {
        "smb" => "gvfs-smb",
        "nfs" => "gvfs-nfs",
        "afp" => "gvfs-afp",
        _ => "gvfs",
    }
}

pub fn supported() -> HashSet<String> {
    gio::Vfs::default().supported_uri_schemes().iter().map(|s| s.to_string()).collect()
}

pub fn can_browse_network() -> bool {
    supported().contains("network")
}

/// The uri to mount for what was typed, or None when it isn't an address.
pub fn normalize(text: &str) -> Option<String> {
    let mut text = text.trim().to_string();
    if text.is_empty() {
        return None;
    }
    if let Some(rest) = text.strip_prefix("\\\\") {
        text = format!("smb://{}", rest.replace('\\', "/")); // \\server\share (Windows)
    }
    if !text.contains("://") {
        text = format!("smb://{}", text.trim_start_matches('/'));
    }
    let (scheme, rest) = text.split_once("://")?;
    let mut scheme = scheme.to_lowercase();
    if scheme == "ssh" {
        scheme = "sftp".into();
    }
    if !SCHEMES.contains(&scheme.as_str()) || rest.trim_matches('/').is_empty() {
        return None;
    }
    Some(format!("{scheme}://{rest}"))
}

/// "" when GIO can reach this kind of server; else what's missing.
pub fn why_not(uri: &str) -> String {
    why_not_in(uri, &supported())
}

// ("/\u{2f}" in this file's translated addresses is "//": the i18n
// check reads a line only up to its first "//", taking it for a comment)
fn why_not_in(uri: &str, supported: &HashSet<String>) -> String {
    let scheme = uri.split("://").next().unwrap_or_default();
    if supported.contains(scheme) {
        return String::new();
    }
    fmt(&tr!("Connecting to “{scheme}:/\u{2f}” servers needs the {package} package."), &[("scheme", scheme), ("package", needs(scheme))])
}

// -- the servers used last ----------------------------------------------------------------------
pub fn recent() -> Vec<String> {
    sonata_config::load(CONFIG, &json!({"recent": []})).get("recent").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect()).unwrap_or_default()
}

fn store(list: Vec<String>) {
    let _ = sonata_config::update(CONFIG, &json!({"recent": list}));
}

pub fn remember(uri: &str) {
    let mut list = vec![uri.to_string()];
    list.extend(recent().into_iter().filter(|u| u != uri));
    list.truncate(RECENT_MAX);
    store(list);
}

pub fn forget(uri: &str) {
    store(recent().into_iter().filter(|u| u != uri).collect());
}

// -- connecting ---------------------------------------------------------------------------------
#[derive(Debug, PartialEq)]
enum Outcome {
    Connected,
    /// cancelled (the password prompt closed): nothing to say
    Quiet,
    Failed(String),
}

fn outcome(res: Result<(), glib::Error>) -> Outcome {
    match res {
        Ok(()) => Outcome::Connected,
        Err(e) if e.matches(gio::IOErrorEnum::AlreadyMounted) => Outcome::Connected,
        Err(e) if e.matches(gio::IOErrorEnum::FailedHandled) => Outcome::Quiet,
        Err(e) => Outcome::Failed(e.message().to_string()),
    }
}

/// Mount `uri` (asking for a password if needed); on_done(uri) once its
/// files can be listed, on_error(message).
pub fn connect(uri: &str, parent: Option<&gtk::Window>, on_done: impl FnOnce(&str) + 'static, on_error: impl FnOnce(&str) + 'static) {
    let f = gio::File::for_uri(uri);
    let u = uri.to_string();
    f.mount_enclosing_volume(gio::MountMountFlags::NONE, Some(&crate::mountop::new(parent)), gio::Cancellable::NONE, move |res| match outcome(res) {
        Outcome::Connected => {
            remember(&u);
            on_done(&u);
        }
        Outcome::Quiet => {}
        Outcome::Failed(m) => on_error(&m),
    });
}

/// What's wrong with the typed address ("" when it can be tried).
fn problem(uri: Option<&str>) -> String {
    match uri {
        None => tr!("Type a server address, like smb:/\u{2f}server/share."),
        Some(u) => why_not(u),
    }
}

/// The Connect to Server window: on_connected(uri) once it's mounted.
pub fn dialog(parent: &gtk::Window, on_connected: impl Fn(&str) + 'static) -> adw::AlertDialog {
    let on_connected: Rc<dyn Fn(&str)> = Rc::new(on_connected);
    let bx = gtk::Box::new(gtk::Orientation::Vertical, 8);
    let entry = sonata_ui::controls::text_field("", "smb://server/share");
    entry.set_hexpand(true);
    entry.set_activates_default(true);
    bx.append(&entry);
    bx.append(&gtk::Label::builder().label(tr!("For example: smb:/\u{2f}nas/photos · sftp:/\u{2f}me@server · davs:/\u{2f}cloud.example.com")).xalign(0.0).wrap(true).css_classes(["dim-label", "caption"]).build());
    let servers = recent();
    let p = parent.downgrade();
    let e = entry.clone();
    let answer: Rc<dyn Fn(&str)> = Rc::new(move |rid: &str| {
        let Some(parent) = p.upgrade() else { return };
        if rid != "connect" {
            return;
        }
        let uri = normalize(&e.text());
        let bad = problem(uri.as_deref());
        if !bad.is_empty() {
            sonata_ui::dialog::alert(&tr!("You can’t connect to this server."), &bad, &[("ok", "OK".into(), "default")], |_| {}, Some(parent.upcast_ref()));
            return;
        }
        let uri = uri.unwrap_or_default();
        let (pw, u, done) = (parent.downgrade(), uri.clone(), on_connected.clone());
        connect(&uri, Some(&parent), move |u| done(u), move |m| {
            if let Some(p) = pw.upgrade() {
                sonata_ui::dialog::alert(&fmt(&tr!("There was a problem connecting to the server “{uri}”."), &[("uri", &u)]), m, &[("ok", "OK".into(), "default")], |_| {}, Some(p.upcast_ref()));
            }
        });
    });
    let a = answer.clone();
    let dlg = sonata_ui::dialog::alert(&tr!("Connect to Server"), "", &[("cancel", tr!("Cancel"), ""), ("connect", tr!("Connect"), "default")], move |rid| a(rid), Some(parent.upcast_ref()));
    if !servers.is_empty() {
        bx.append(&gtk::Label::builder().label(tr!("Recent Servers")).xalign(0.0).css_classes(["heading"]).margin_top(4).build());
        let lb = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).css_classes(["boxed-list"]).build();
        for uri in &servers {
            let row = gtk::ListBoxRow::new();
            row.set_widget_name(uri); // (its address, read back on a click)
            let line = gtk::Box::builder().spacing(6).margin_start(8).margin_end(4).margin_top(4).margin_bottom(4).build();
            line.append(&gtk::Image::from_icon_name("network-server-symbolic"));
            line.append(&gtk::Label::builder().label(uri.as_str()).xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::Middle).build());
            let x = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["flat", "circular"]).tooltip_text(tr!("Forget This Server")).valign(gtk::Align::Center).build();
            let (r, l, u) = (row.downgrade(), lb.downgrade(), uri.clone());
            x.connect_clicked(move |_| {
                forget(&u);
                if let (Some(r), Some(l)) = (r.upgrade(), l.upgrade()) {
                    l.remove(&r);
                }
            });
            line.append(&x);
            row.set_child(Some(&line));
            lb.append(&row);
        }
        let e = entry.clone();
        lb.connect_row_selected(move |_, r| {
            if let Some(r) = r {
                e.set_text(&r.widget_name());
            }
        });
        let (e, d) = (entry.clone(), dlg.downgrade());
        lb.connect_row_activated(move |_, r| {
            e.set_text(&r.widget_name());
            if let Some(d) = d.upgrade() {
                d.close(); // (answers "cancel": ignored)
            }
            answer("connect");
        });
        bx.append(&gtk::ScrolledWindow::builder().child(&lb).propagate_natural_height(true).max_content_height(200).hscrollbar_policy(gtk::PolicyType::Never).build());
        entry.set_text(&servers[0]); // the last one, ready
    }
    dlg.set_extra_child(Some(&bx));
    let d = dlg.downgrade();
    entry.connect_changed(move |e| {
        if let Some(d) = d.upgrade() {
            d.set_response_enabled("connect", can_connect(&e.text()));
        }
    });
    dlg.set_response_enabled("connect", can_connect(&entry.text()));
    glib::idle_add_local_once(move || {
        entry.grab_focus();
        entry.select_region(0, -1);
    });
    dlg
}

fn can_connect(text: &str) -> bool {
    !text.trim().is_empty()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_addresses() {
        assert_eq!(normalize("nas/fotos").as_deref(), Some("smb://nas/fotos")); // Finder: smb by default
        assert_eq!(normalize("\\\\nas\\fotos").as_deref(), Some("smb://nas/fotos")); // a Windows path
        assert_eq!(normalize("ssh://me@host").as_deref(), Some("sftp://me@host"));
        assert_eq!(normalize(" davs://cloud.example.com/dav ").as_deref(), Some("davs://cloud.example.com/dav"));
        for bad in ["", "   ", "smb://", "http://example.com", "file:///etc"] {
            assert_eq!(normalize(bad), None, "{bad}");
        }
    }

    #[test]
    fn missing_backend_is_named() {
        let s: HashSet<String> = ["file", "sftp"].iter().map(|s| s.to_string()).collect();
        assert_eq!(why_not_in("sftp://h", &s), "");
        assert!(why_not_in("smb://nas/x", &s).contains("gvfs-smb"));
        assert!(why_not_in("davs://h", &s).contains("gvfs"));
        assert_eq!(problem(None), tr!("Type a server address, like smb:/\u{2f}server/share."));
    }

    #[test]
    fn recent_servers() {
        // (the test run's own XDG_CONFIG_HOME)
        forget("smb://a/x");
        forget("sftp://b");
        assert!(!recent().contains(&"smb://a/x".to_string()));
        for u in ["smb://a/x", "sftp://b", "smb://a/x"] {
            remember(u);
        }
        assert_eq!(recent()[..2], ["smb://a/x".to_string(), "sftp://b".to_string()]);
        forget("smb://a/x");
        assert_eq!(recent()[0], "sftp://b");
        for i in 0..20 {
            remember(&format!("smb://h{i}"));
        }
        assert_eq!(recent().len(), RECENT_MAX);
    }

    /// Already connected counts as connected; a cancelled password says nothing.
    #[test]
    fn connect_outcomes() {
        assert_eq!(outcome(Ok(())), Outcome::Connected);
        assert_eq!(outcome(Err(glib::Error::new(gio::IOErrorEnum::AlreadyMounted, "already"))), Outcome::Connected);
        assert_eq!(outcome(Err(glib::Error::new(gio::IOErrorEnum::FailedHandled, "cancelled"))), Outcome::Quiet);
        assert_eq!(outcome(Err(glib::Error::new(gio::IOErrorEnum::Failed, "refused"))), Outcome::Failed("refused".into()));
        assert!(can_connect(" smb://a ") && !can_connect("  "));
    }

    /// Vini (Files vs Finder): no Connect to Server. Ctrl+K, the menu bar's
    /// Go > Connect to Server and Network in the sidebar.
    #[test]
    fn wiring() {
        assert!(include_str!("window.rs").contains("(\"<Control>k\", |t| { t.connect_to_server(); true })"));
        assert!(include_str!("sidebar.rs").contains("server::NETWORK"));
        assert!(include_str!("lib.rs").contains("connect_to_server()"));
    }
}
