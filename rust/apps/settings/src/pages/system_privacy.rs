//! Security & Privacy (app.py's _page_privacy, with the Linux settings
//! behind it: firewall, disk encryption, USB protection, saved passwords)
//! and what every app may use (_permissions_group, shown first in Apps).

use crate::kit::{combo_row, group, install_row, run_async, show_quietly, switch_row, usb_confirm, usb_warning, Install};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use serde_json::{json, Map, Value};
use sonata_backend::system;
use sonata_i18n::{fmt, gettext, tr, trn};
use sonata_system::{location, permstore, security};
use sonata_tweaks::prefs_write;
use std::rc::{Rc, Weak};

fn reload_privacy(w: &Weak<Settings>) -> Rc<dyn Fn()> {
    let w = w.clone();
    Rc::new(move || {
        if let Some(s) = w.upgrade() {
            s.reload_page("privacy");
        }
    })
}

fn has(program: &str) -> bool {
    glib::find_program_in_path(program).is_some()
}

/// Big Sur Security & Privacy, with the Linux settings behind it.
pub fn page_privacy(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let sec = sonata_config::load("security", &sonata_sessionui::idlelock::defaults());
    let w = Rc::downgrade(ctx);
    let gen = group(&tr!("General"), "");
    let idle = has("swayidle");
    if !idle {
        let (row, _) = install_row(&tr!("Automatic locking needs swayidle"), &tr!("Not installed"), system::install_command("swayidle", None).as_deref(), Install {
            ready: Some(Rc::new(|| has("swayidle"))),
            on_ready: Some(reload_privacy(&w)),
            ..Default::default()
        });
        gen.add(&row);
    }
    let opts = vec![(-1i64, tr!("Never")), (0, tr!("Immediately")), (5, tr!("5 seconds")), (60, tr!("1 minute")), (300, tr!("5 minutes")), (900, tr!("15 minutes")), (3600, tr!("1 hour"))];
    let lock_after = sec.get("lock_after").and_then(Value::as_i64).unwrap_or(0);
    let s1 = w.clone();
    let after = combo_row(&tr!("Require password after the display turns off"), opts, &lock_after, move |v: &i64| {
        if let Some(s) = s1.upgrade() {
            s.save("security", "lock_after", json!(v));
        }
    }, "");
    let s2 = w.clone();
    let sleep = switch_row(&tr!("Lock before sleep"), super::truthy(sec.get("lock_before_sleep")), move |on| {
        if let Some(s) = s2.upgrade() {
            s.save("security", "lock_before_sleep", json!(on));
        }
    }, "");
    after.set_sensitive(idle);
    sleep.set_sensitive(idle);
    gen.add(&after);
    gen.add(&sleep);

    const P: &str = "org.gnome.desktop.privacy";
    let priv_ = group(&tr!("Privacy"), "");
    priv_.add(&switch_row(&tr!("Remember recent files"), prefs_write::get(P, "remember-recent-files") != "false", |on| {
        prefs_write::set(P, "remember-recent-files", if on { "true" } else { "false" });
    }, &tr!("Recents in Files and the Open dialogs")));
    let clear = adw::ActionRow::builder().title(tr!("Recent items")).subtitle(tr!("Forget the files opened recently")).build();
    let s3 = w.clone();
    let clear_btn = sonata_ui::controls::push_button(&tr!("Clear"), move || {
        if gtk::RecentManager::default().purge_items().is_ok() {
            if let Some(s) = s3.upgrade() {
                s.toast(&tr!("Recent items cleared"));
            }
        }
    }, "");
    clear_btn.set_valign(gtk::Align::Center);
    clear.add_suffix(&clear_btn);
    priv_.add(&clear);
    priv_.add(&switch_row(&tr!("Remove items from the Trash after 30 days"), prefs_write::get(P, "remove-old-trash-files") == "true", |on| {
        prefs_write::set(P, "remove-old-trash-files", if on { "true" } else { "false" });
        prefs_write::set(P, "old-files-age", "uint32 30");
    }, ""));
    const LOC: &str = "org.gnome.system.location";
    if let Some(loc) = prefs_write::get_in(LOC, "enabled", None, &sonata_config::prefs::load()) {
        priv_.add(&switch_row(&tr!("Location Services"), loc == "true", |on| {
            prefs_write::set(LOC, "enabled", if on { "true" } else { "false" });
        }, &tr!("Apps may ask for your location (GeoClue)")));
    }
    vec![gen, priv_, firewall_group(ctx), encryption_group(), usb_group(ctx, &sec), keyring_group(ctx)]
}

// -- App Permissions (Apps) ----------------------------------------------------------------------
/// macOS' Privacy list: what sandboxed (Flatpak) apps were allowed through
/// the portals; a switch per app (permstore).
pub fn permissions_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let grp = group(&tr!("App Permissions"), &tr!("Apps installed with Flatpak ask before using these. Other apps: open one below to choose what it may use."));
    let ask = switch_row(&tr!("Ask before apps use the camera, microphone, network…"), sonata_tweaks::appperms::asking(), sonata_tweaks::appperms::set_asking, &tr!("A new app asks the first time it opens; off: apps get access without asking"));
    ask.set_subtitle_lines(0);
    grp.add(&ask);
    if location::available() {
        let slot = super::slot::<adw::SwitchRow>();
        let (w, sl) = (Rc::downgrade(ctx), slot.clone());
        let loc = switch_row(&tr!("Location Services"), location::enabled(), move |on| {
            if let Some(r) = sl.borrow().upgrade() {
                set_location(&w, &r, on);
            }
        }, &tr!("Off: no app gets your location, installed from packages or Flatpak (asks for your password)"));
        slot.borrow().set(Some(&loc));
        loc.set_subtitle_lines(0);
        grp.add(&loc);
    }
    for (key, title, entries) in permstore::all_permissions() {
        let sub = if entries.is_empty() { tr!("No app has asked") } else { fmt(&trn!("{n} app", "{n} apps", entries.len()), &[("n", &entries.len().to_string())]) };
        let exp = adw::ExpanderRow::builder().title(gettext(title)).use_markup(false).subtitle(sub).build();
        exp.set_enable_expansion(!entries.is_empty());
        for (app_id, on) in entries {
            let info = sonata_ui::apps::lookup(&app_id);
            let name = info.as_ref().map(|i| i.display_name().to_string()).unwrap_or_else(|| app_id.clone());
            let a = app_id.clone();
            let row = switch_row(&name, on, move |v| {
                permstore::set_allowed(key, &a, v);
            }, "");
            if let Some(icon) = info.as_ref().and_then(|i| i.icon()) {
                row.add_prefix(&gtk::Image::builder().gicon(&icon).pixel_size(24).build());
            }
            exp.add_row(&row);
        }
        grp.add(&exp);
    }
    grp
}

fn set_location(w: &Weak<Settings>, row: &adw::SwitchRow, on: bool) {
    let (w, r) = (w.clone(), row.downgrade());
    run_async(move || location::set_enabled(on), move |ok| {
        if ok {
            return;
        }
        // cancelled or failed: the switch as it is
        if let Some(r) = r.upgrade() {
            show_quietly(&r, !on);
        }
        if let Some(s) = w.upgrade() {
            s.toast(&tr!("Location Services wasn't changed"));
        }
    });
}

// -- Firewall ------------------------------------------------------------------------------------
/// macOS' Firewall: ufw or firewalld, on or off (pkexec asks the password).
fn firewall_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let (kind, on) = security::firewall();
    let grp = group(&tr!("Firewall"), "");
    let w = Rc::downgrade(ctx);
    let Some(kind) = kind else {
        let (row, _) = install_row(&tr!("No firewall installed"), &tr!("Install ufw to turn it on here"), system::install_command("ufw", None).as_deref(), Install {
            ready: Some(Rc::new(|| security::firewall().0.is_some())),
            on_ready: Some(reload_privacy(&w)),
            ..Default::default()
        });
        grp.add(&row);
        return grp;
    };
    let slot = super::slot::<adw::SwitchRow>();
    let sl = slot.clone();
    let row = switch_row(&tr!("Firewall"), on, move |want| {
        let Some(r) = sl.borrow().upgrade() else { return };
        r.set_sensitive(false);
        let (w, rw) = (w.clone(), r.downgrade());
        run_async(move || security::set_firewall(Some(kind), want), move |ok| {
            if let Some(r) = rw.upgrade() {
                r.set_sensitive(true);
                if !ok {
                    // cancelled or failed: the switch shows the truth
                    show_quietly(&r, security::firewall().1);
                }
            }
            if let Some(s) = w.upgrade() {
                s.toast(&if !ok { tr!("Firewall not changed") } else if want { tr!("Firewall on") } else { tr!("Firewall off") });
            }
        });
    }, &fmt(&tr!("Blocks connections from other computers to this one ({firewall})"), &[("firewall", kind)]));
    slot.borrow().set(Some(&row));
    grp.add(&row);
    grp
}

// -- Disk Encryption -----------------------------------------------------------------------------
/// The row's (title, subtitle) for the startup disk and the home folder.
pub fn encryption_text(root: Option<bool>, home: Option<bool>) -> (String, String) {
    match root {
        Some(true) => (tr!("The startup disk is encrypted"), if home == Some(false) { tr!("The home folder is on a disk that isn't encrypted") } else { tr!("Its data can't be read without your disk password (LUKS)") }),
        Some(false) => (tr!("The startup disk isn't encrypted"), tr!("Anyone with the disk can read its files. Linux encrypts a disk when it's installed: choose encryption in the installer.")),
        None => (tr!("Unknown"), tr!("The startup disk couldn't be checked")),
    }
}

/// macOS' FileVault: shown only (Linux encrypts a disk when it's installed).
fn encryption_group() -> adw::PreferencesGroup {
    let enc = security::encryption(None);
    let of = |m: &str| enc.iter().find(|(k, _)| *k == m).and_then(|(_, v)| *v);
    let grp = group(&tr!("Disk Encryption"), "");
    let (title, sub) = encryption_text(of("/"), of("/home"));
    let row = adw::ActionRow::builder().title(title).subtitle(sub).use_markup(false).build();
    row.add_prefix(&gtk::Image::from_icon_name(if of("/") == Some(true) { "object-locked-symbolic" } else { "object-unlocked-symbolic" }));
    row.set_subtitle_lines(0);
    grp.add(&row);
    grp
}

// -- USB protection ------------------------------------------------------------------------------
// usbprotect.py's Settings half (sonata_backend::usbprotect has only the
// lock screen's): USBGuard's state and the command that sets it up.
const USB_BUS: &str = "org.usbguard1";
const USB_PATH: &str = "/org/usbguard1";
const USB_PARAM: &str = "InsertedDevicePolicy";
const USB_DENIED: [&str; 5] = ["NotAuthorized", "AccessDenied", "PermissionDenied", "not authorized", "Not authorized"];
pub const USB_POLKIT_RULE: &str = "/etc/polkit-1/rules.d/70-sonata2-usbguard.rules";
pub const USB_RULE: &str = r#"// Sonata: the lock screen blocks new USB devices (Security & Privacy > USB)
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.usbguard") == 0 && /Parameter$/.test(action.id) &&
        subject.active && subject.local && (subject.isInGroup("wheel") || subject.isInGroup("sudo"))) {
        return polkit.Result.YES;
    }
});"#;
const USB_DAEMON_CONF: &str = "/etc/usbguard/usbguard-daemon.conf";

/// As root: no rules needed (every device allowed while unlocked), the
/// policy changeable by the user's session, the services on now and at boot.
pub fn usb_setup() -> String {
    [
        "set -e".to_string(),
        "touch /etc/usbguard/rules.conf && chmod 600 /etc/usbguard/rules.conf".to_string(),
        format!("sed -i 's/^ImplicitPolicyTarget=.*/ImplicitPolicyTarget=allow/' {USB_DAEMON_CONF}"),
        "mkdir -p /etc/polkit-1/rules.d".to_string(),
        format!("printf '%s\\n' {} > {USB_POLKIT_RULE}", sonata_system::selfupdate::quote(USB_RULE)),
        "systemctl enable usbguard.service usbguard-dbus.service".to_string(),
        "systemctl restart usbguard.service usbguard-dbus.service".to_string(),
        "echo 'USB protection is ready.'".to_string(),
    ]
    .join("\n")
}

pub fn usb_setup_command(family: &str) -> Option<String> {
    let install = system::install_command("usbguard", Some(family))?;
    Some(format!("{install} && sudo sh -c {}", sonata_system::selfupdate::quote(&usb_setup())))
}

fn usb_installed() -> bool {
    ["usbguard", "usbguard-daemon"].iter().any(|p| has(p) || std::path::Path::new("/usr/bin").join(p).exists())
}

/// A D-Bus error -> "denied" (polkit says no) or "off".
pub fn usb_error_state(message: &str) -> &'static str {
    if USB_DENIED.iter().any(|d| message.contains(d)) {
        "denied"
    } else {
        "off"
    }
}

/// missing: not installed; off: its services aren't running; denied:
/// polkit won't let this user change the policy; ready.
fn usb_status() -> &'static str {
    use gio::prelude::*;
    if !sonata_backend::usbprotect::available() {
        return if usb_installed() { "off" } else { "missing" };
    }
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return "off" };
    let call = |method: &str, args: glib::Variant| bus.call_sync(Some(USB_BUS), USB_PATH, USB_BUS, method, Some(&args), glib::VariantTy::new("(s)").ok(), gio::DBusCallFlags::NONE, 2000, gio::Cancellable::NONE);
    let res = call("getParameter", (USB_PARAM,).to_variant()).and_then(|cur| {
        let cur: String = cur.child_value(0).get().unwrap_or_default();
        // set to what it is (changes nothing): the lock screen's own call, allowed or not
        call("setParameter", (USB_PARAM, cur).to_variant())
    });
    match res {
        Ok(_) => "ready",
        Err(e) => usb_error_state(e.message()),
    }
}

/// GNOME's USB protection: devices plugged in while locked are blocked (USBGuard).
fn usb_group(ctx: &Ctx, sec: &Map<String, Value>) -> adw::PreferencesGroup {
    let grp = group("USB", "");
    let w = Rc::downgrade(ctx);
    let s1 = w.clone();
    let row = switch_row(&tr!("Block new USB devices while locked"), false, move |on| {
        if let Some(s) = s1.upgrade() {
            s.save("security", "usb_protection", json!(on));
        }
    }, &tr!("Checking USBGuard…"));
    row.set_subtitle_lines(0);
    row.set_sensitive(false);
    grp.add(&row);
    let protect = super::truthy(sec.get("usb_protection"));
    let (g, r) = (grp.downgrade(), row.downgrade());
    run_async(usb_status, move |state| {
        let (Some(grp), Some(row)) = (g.upgrade(), r.upgrade()) else { return };
        if state == "ready" {
            show_quietly(&row, protect);
            row.set_sensitive(true);
            row.set_subtitle(&tr!("Devices already plugged in keep working"));
            return;
        }
        row.set_subtitle(&tr!("Devices already plugged in keep working, all others are allowed while unlocked"));
        let (title, sub) = match state {
            "missing" => (tr!("Needs USBGuard"), tr!("Not installed")),
            "denied" => (tr!("USBGuard isn't set up"), tr!("This account can't change its policy yet")),
            _ => (tr!("USBGuard isn't set up"), tr!("Installed, but its services aren't running")),
        };
        let text = fmt(&tr!("{reason}. {warning} Set Up turns it on the safe way."), &[("reason", &sub), ("warning", &usb_warning())]);
        let (row, _) = install_row(&title, &text, usb_setup_command(system::package_family()).as_deref(), Install {
            ready: Some(Rc::new(|| usb_status() == "ready")),
            on_ready: Some(reload_privacy(&w)),
            label: Some(tr!("Set Up…")),
            confirm: Some((tr!("Set up USBGuard?"), usb_confirm(), tr!("Open Terminal"))),
        });
        grp.add(&row);
    });
    grp
}

// -- Saved Passwords -----------------------------------------------------------------------------
/// Where saved passwords live (keyring): the login keyring (no prompt after
/// logging in) or KeePassXC (its own password each time).
fn keyring_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let grp = group(&tr!("Saved Passwords"), "");
    let row = if sonata_tweaks::keyring::backend() == "keepassxc" {
        let row = adw::ActionRow::builder().title(tr!("Kept by KeePassXC")).use_markup(false).subtitle(tr!("It asks for its own password at every login")).build();
        let w = Rc::downgrade(ctx);
        let btn = sonata_ui::controls::push_button(&tr!("Use Login Password…"), move || {
            if let Some(s) = w.upgrade() {
                switch_keyring(&s);
            }
        }, "");
        btn.set_valign(gtk::Align::Center);
        row.add_suffix(&btn);
        row
    } else {
        adw::ActionRow::builder().title(tr!("Login keyring")).use_markup(false).subtitle(tr!("Unlocked by your login password: nothing asks after logging in")).build()
    };
    row.set_subtitle_lines(0);
    grp.add(&row);
    grp
}

/// KeePassXC -> the login keyring, with the apps' saved passwords.
fn switch_keyring(ctx: &Ctx) {
    use sonata_tweaks::keyring;
    use std::sync::{Arc, Mutex};
    let entry = sonata_ui::controls::secret_field("", "");
    entry.set_hexpand(true);
    let (w, e) = (Rc::downgrade(ctx), entry.clone());
    let dlg = sonata_ui::dialog::alert(
        &tr!("Use your login password for saved passwords?"),
        &tr!("Apps' saved passwords (Chrome, VS Code, Wi-Fi) move from KeePassXC to the login keyring, which your login unlocks: no password prompt after logging in. KeePassXC stays installed as a password manager. Type your login password:"),
        &[("cancel", tr!("Cancel"), ""), ("switch", tr!("Use Login Password"), "default")],
        move |rid| {
            if rid != "switch" {
                return;
            }
            let Some(s) = w.upgrade() else { return };
            let pw = e.text().to_string();
            s.toast(&tr!("Moving your saved passwords… KeePassXC may ask to unlock its database"));
            // what the move is doing, told from the worker thread: the steps
            // wait here and are shown from the main loop
            let steps: Arc<Mutex<Vec<String>>> = Arc::default();
            let st = steps.clone();
            let tell = {
                let (w, st) = (w.clone(), steps.clone());
                glib::timeout_add_local(std::time::Duration::from_millis(200), move || {
                    let Some(s) = w.upgrade() else { return glib::ControlFlow::Break };
                    for t in std::mem::take(&mut *st.lock().unwrap_or_else(|e| e.into_inner())) {
                        s.toast(&t);
                    }
                    glib::ControlFlow::Continue
                })
            };
            let w = w.clone();
            run_async(move || {
                let ops = keyring::Ops::default();
                let push = |t: &str| st.lock().unwrap_or_else(|e| e.into_inner()).push(t.to_string());
                match keyring::switch_to_gnome(&pw, &push, &ops) {
                    Ok(n) => Ok(fmt(&trn!("{n} saved password moved", "{n} saved passwords moved", n), &[("n", &n.to_string())])),
                    Err(keyring::SwitchError::Permission(e) | keyring::SwitchError::Failed(e)) => Err(fmt(&tr!("Not switched: {error}"), &[("error", &e)])),
                }
            }, move |msg: Result<String, String>| {
                tell.remove();
                let Some(s) = w.upgrade() else { return };
                for t in std::mem::take(&mut *steps.lock().unwrap_or_else(|e| e.into_inner())) {
                    s.toast(&t);
                }
                match &msg {
                    Ok(t) | Err(t) => s.toast(t),
                }
                s.reload_page("privacy");
                if msg.is_ok() {
                    s.ask_restart("session", &tr!("The login keyring"));
                }
            });
        },
        ctx.parent().as_ref(),
    );
    dlg.set_extra_child(Some(&entry));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_whole_command() {
        let c = usb_setup_command("arch").unwrap();
        assert!(c.starts_with("sudo pacman -S --needed usbguard && sudo sh -c "), "{c}");
        let ok = std::process::Command::new("sh").args(["-n", "-c", &c]).status().is_ok_and(|s| s.success());
        assert!(ok);
        assert!(usb_setup_command("").is_none());
    }

    /// The root part, run against a temporary /etc with systemctl faked.
    #[test]
    fn test_setup_script_in_a_fake_root() {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let etc = root.path().join("etc");
        std::fs::create_dir_all(etc.join("usbguard")).unwrap();
        std::fs::write(etc.join("usbguard/usbguard-daemon.conf"), "RuleFile=/etc/usbguard/rules.conf\nImplicitPolicyTarget=block\nPresentDevicePolicy=apply-policy\n").unwrap();
        let bin = root.path().join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let log = root.path().join("systemctl.log");
        std::fs::write(bin.join("systemctl"), format!("#!/bin/sh\necho \"$*\" >> {}\n", log.display())).unwrap();
        std::fs::set_permissions(bin.join("systemctl"), std::fs::Permissions::from_mode(0o755)).unwrap();
        let script = usb_setup().replace("/etc/", &format!("{}/", etc.display()));
        let path = format!("{}:{}", bin.display(), std::env::var("PATH").unwrap_or_default());
        let out = std::process::Command::new("sh").args(["-c", &script]).env("PATH", path).output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let conf = std::fs::read_to_string(etc.join("usbguard/usbguard-daemon.conf")).unwrap();
        assert!(conf.contains("ImplicitPolicyTarget=allow")); // the keyboard is never blocked unlocked
        assert!(conf.contains("PresentDevicePolicy=apply-policy"));
        let mode = std::fs::metadata(etc.join("usbguard/rules.conf")).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600);
        let rule = std::fs::read_to_string(etc.join("polkit-1/rules.d/70-sonata2-usbguard.rules")).unwrap();
        assert_eq!(rule.trim(), USB_RULE.trim());
        let log = std::fs::read_to_string(log).unwrap();
        assert!(log.contains("enable usbguard.service usbguard-dbus.service"));
        assert!(log.contains("restart usbguard.service usbguard-dbus.service"));
    }

    #[test]
    fn test_states() {
        assert_eq!(usb_error_state("GDBus.Error:org.freedesktop.DBus.Error.AccessDenied: Not authorized"), "denied");
        assert_eq!(usb_error_state("Timeout was reached"), "off");
    }

    #[test]
    fn encryption_rows() {
        assert_eq!(encryption_text(Some(true), Some(false)).1, tr!("The home folder is on a disk that isn't encrypted"));
        assert_eq!(encryption_text(Some(true), None).0, tr!("The startup disk is encrypted"));
        assert_eq!(encryption_text(Some(false), None).0, tr!("The startup disk isn't encrypted"));
        assert_eq!(encryption_text(None, None).0, tr!("Unknown"));
    }
}
