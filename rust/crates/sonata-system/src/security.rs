//! Security & Privacy's Linux parts (backend/security.py; macOS: Firewall,
//! FileVault).
//!
//! Firewall: ufw or firewalld, whichever is installed; its state read
//! without root, turned on or off through pkexec. Disk encryption: whether
//! the startup disk (and the home folder) sit on a LUKS volume -- shown
//! only: Linux encrypts a disk when it's installed.

use serde_json::Value;
use sonata_backend::run::{ok, run, which};

pub const UFW_CONF: &str = "/etc/ufw/ufw.conf";

fn active(unit: &str) -> bool {
    ok(&["systemctl", "is-active", "--quiet", unit], 3)
}

pub fn ufw_enabled(path: &str) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else { return false };
    text.lines().find(|l| l.trim().to_uppercase().starts_with("ENABLED=")).is_some_and(|l| l.split_once('=').unwrap().1.trim().trim_matches('"').eq_ignore_ascii_case("yes"))
}

/// (kind: "ufw" | "firewalld" | None, on)
pub fn firewall() -> (Option<&'static str>, bool) {
    if which("ufw").is_some() {
        return (Some("ufw"), ufw_enabled(UFW_CONF) && active("ufw"));
    }
    if which("firewall-cmd").is_some() || which("firewalld").is_some() {
        return (Some("firewalld"), active("firewalld"));
    }
    (None, false)
}

/// The root command that turns it on or off (and keeps it so after a restart).
pub fn firewall_command(kind: Option<&str>, on: bool) -> Vec<&'static str> {
    match (kind, on) {
        // ufw enable also enables its service; ufw needs it running to load the rules at boot
        (Some("ufw"), true) => vec!["pkexec", "sh", "-c", "systemctl enable --now ufw && ufw --force enable"],
        (Some("ufw"), false) => vec!["pkexec", "ufw", "disable"],
        (Some("firewalld"), on) => vec!["pkexec", "systemctl", if on { "enable" } else { "disable" }, "--now", "firewalld"],
        _ => vec![],
    }
}

/// Blocking (pkexec asks the password).
pub fn set_firewall(kind: Option<&str>, on: bool) -> bool {
    let cmd = firewall_command(kind, on);
    !cmd.is_empty() && std::process::Command::new(cmd[0]).args(&cmd[1..]).status().is_ok_and(|s| s.success())
}

fn mountpoints(node: &Value) -> Vec<&str> {
    node.get("mountpoints").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default()
}

fn children(node: &Value) -> &[Value] {
    node.get("children").and_then(Value::as_array).map(|a| a.as_slice()).unwrap_or(&[])
}

/// Whether `mount` is on this device and there is a crypt layer at or above it.
fn encrypted_under(node: &Value, mount: &str, crypt: bool) -> bool {
    let crypt = crypt || node.get("type").and_then(Value::as_str) == Some("crypt") || node.get("fstype").and_then(Value::as_str) == Some("crypto_LUKS");
    if mountpoints(node).contains(&mount) {
        return crypt;
    }
    children(node).iter().any(|c| encrypted_under(c, mount, crypt))
}

fn mounted(node: &Value, mount: &str) -> bool {
    mountpoints(node).contains(&mount) || children(node).iter().any(|c| mounted(c, mount))
}

/// [("/", Some(true) | Some(false) | None), ("/home", ...)] (None: not a
/// mount of its own / unknown), from `lsblk -J` (None: asked here).
pub fn encryption(lsblk: Option<&Value>) -> [(&'static str, Option<bool>); 2] {
    let asked;
    let lsblk = match lsblk {
        Some(v) => v,
        None => {
            let (_rc, out) = run(&["lsblk", "-J", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINTS"], 5);
            asked = serde_json::from_str(&out).unwrap_or(Value::Null);
            &asked
        }
    };
    let devs = lsblk.get("blockdevices").and_then(Value::as_array).map(|a| a.as_slice()).unwrap_or(&[]);
    ["/", "/home"].map(|m| {
        let on: Vec<&Value> = devs.iter().filter(|d| mounted(d, m)).collect();
        (m, (!on.is_empty()).then(|| on.iter().any(|d| encrypted_under(d, m, false))))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Vini (security review): Security & Privacy lacked the firewall and
    /// disk encryption.
    #[test]
    fn ufw_conf_and_commands() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("ufw.conf");
        std::fs::write(&p, "# x\nENABLED=yes\n").unwrap();
        assert!(ufw_enabled(p.to_str().unwrap()));
        std::fs::write(&p, "ENABLED=no\n").unwrap();
        assert!(!ufw_enabled(p.to_str().unwrap()));
        assert!(!ufw_enabled("/nonexistent"));
        for kind in ["ufw", "firewalld"] {
            for on in [true, false] {
                assert_eq!(firewall_command(Some(kind), on)[0], "pkexec");
            }
        }
        assert!(firewall_command(None, true).is_empty());
    }

    #[test]
    fn luks() {
        let luks = json!({"blockdevices": [{"name": "nvme0n1", "type": "disk", "fstype": null, "mountpoints": [null],
            "children": [{"name": "p1", "type": "part", "fstype": "vfat", "mountpoints": ["/boot"]},
                         {"name": "p2", "type": "part", "fstype": "crypto_LUKS", "mountpoints": [null],
                          "children": [{"name": "root", "type": "crypt", "fstype": "btrfs", "mountpoints": ["/", "/home"]}]}]}]});
        let plain = json!({"blockdevices": [{"name": "sda", "type": "disk", "mountpoints": [null],
            "children": [{"name": "sda1", "type": "part", "fstype": "ext4", "mountpoints": ["/"]}]}]});
        assert_eq!(encryption(Some(&luks)), [("/", Some(true)), ("/home", Some(true))]);
        assert_eq!(encryption(Some(&plain)), [("/", Some(false)), ("/home", None)]);
        assert_eq!(encryption(Some(&json!({}))), [("/", None), ("/home", None)]);
    }
}
