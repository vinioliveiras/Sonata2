//! tools/wayfire-config.sh's Python steps and their Rust port
//! (`sonata2-rs wayfire-config`) on the same files: the session's copy must
//! come out byte for byte the same, or the Rust session's title bars, keys
//! and options differ from the Python session's. The Python is taken from
//! the script itself (its `<<'PY'` heredocs), so a change there that the
//! Rust misses fails here. Every run is in temporary folders (HOME, XDG
//! config and runtime); without python3 the test says so and passes.

use std::path::{Path, PathBuf};
use std::process::Command;

use sonata_backend::wfconfig;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap()
}

/// The script's Python heredocs, in order.
fn heredocs() -> Vec<String> {
    let script = std::fs::read_to_string(repo().join("tools/wayfire-config.sh")).unwrap();
    let mut out = Vec::new();
    let mut cur: Option<String> = None;
    for line in script.lines() {
        match &mut cur {
            Some(body) if line == "PY" => {
                out.push(std::mem::take(body));
                cur = None;
            }
            Some(body) => {
                body.push_str(line);
                body.push('\n');
            }
            None if line.contains("<<'PY'") => cur = Some(String::new()),
            None => {}
        }
    }
    assert_eq!(out.len(), 2, "wayfire-config.sh's two Python steps");
    out
}

fn python() -> bool {
    Command::new("python3").arg("-c").arg("import sonata2.wfconfig").env("PYTHONPATH", repo()).output().is_ok_and(|o| o.status.success())
}

struct Case {
    name: &'static str,
    base: String,
    appearance: Option<&'static str>,
    overrides: Option<&'static str>,
}

/// A temporary HOME with its XDG folders; appearance.json in it.
fn home(case: &Case) -> tempfile::TempDir {
    let d = tempfile::tempdir().unwrap();
    for sub in ["cfg/sonata2", "run", "data"] {
        std::fs::create_dir_all(d.path().join(sub)).unwrap();
    }
    if let Some(a) = case.appearance {
        std::fs::write(d.path().join("cfg/sonata2/appearance.json"), a).unwrap();
    }
    d
}

/// Both Python steps as the script runs them: the first's failure ignored
/// (`|| true`), the second's exit status returned.
fn run_python(case: &Case, docs: &[String]) -> (Vec<u8>, Vec<u8>, bool, Vec<u8>) {
    let h = home(case);
    let out = h.path().join("out.ini");
    let over = h.path().join("over.ini");
    std::fs::write(&out, &case.base).unwrap();
    let py = |doc: &str, args: &[&Path]| {
        let mut c = Command::new("python3");
        c.arg("-").args(args).env("PYTHONPATH", repo()).env("HOME", h.path()).env("XDG_CONFIG_HOME", h.path().join("cfg"));
        c.env("XDG_RUNTIME_DIR", h.path().join("run")).env("XDG_DATA_HOME", h.path().join("data"));
        for v in ["WAYFIRE_SOCKET", "DBUS_SESSION_BUS_ADDRESS", "WAYLAND_DISPLAY", "DISPLAY", "SONATA2_RS_SESSION", "SONATA2_RS_SIDE_BY_SIDE"] {
            c.env_remove(v);
        }
        c.stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::piped());
        let mut child = c.spawn().unwrap();
        use std::io::Write;
        child.stdin.take().unwrap().write_all(doc.as_bytes()).unwrap();
        child.wait_with_output().unwrap().status.success()
    };
    py(&docs[0], &[&out]);
    let first = std::fs::read(&out).unwrap();
    let mut ok = true;
    if let Some(o) = case.overrides {
        std::fs::write(&over, o).unwrap();
        ok = py(&docs[1], &[&out, &over]);
    }
    (first, std::fs::read(&out).unwrap(), ok, std::fs::read(&over).unwrap_or_default())
}

fn run_rust(case: &Case) -> (Vec<u8>, Vec<u8>, bool, Vec<u8>) {
    let h = home(case);
    let out = h.path().join("out.ini");
    let over = h.path().join("over.ini");
    std::fs::write(&out, &case.base).unwrap();
    let _ = wfconfig::frame_step_file(&out, &h.path().join("cfg/sonata2"), repo().to_str().unwrap());
    let first = std::fs::read(&out).unwrap();
    let mut ok = true;
    if let Some(o) = case.overrides {
        std::fs::write(&over, o).unwrap();
        ok = wfconfig::overrides_step_file(&out, &over).is_ok();
    }
    (first, std::fs::read(&out).unwrap(), ok, std::fs::read(&over).unwrap_or_default())
}

/// config/wayfire.ini as the script's sed leaves it (@SONATA@ filled in).
fn default_ini() -> String {
    std::fs::read_to_string(repo().join("config/wayfire.ini")).unwrap().replace("@SONATA@", repo().to_str().unwrap())
}

const KEPT: &str = "[core]
plugins = alpha autostart command decoration grid resize scale sonata-resize
vwidth = 4
vheight = 1
xwayland_startup_script = my-own-x11-script --flag

[command]
binding_switcher = <super> KEY_TAB
command_switcher = gdbus call switcher next
binding_switcher_back = <super> <shift> KEY_TAB
command_switcher_back = gdbus call switcher prev
binding_switcher_alt = <alt> KEY_TAB
command_switcher_alt = gdbus call switcher next
# binding_old = <super> KEY_TAB
binding = <super> KEY_TAB
command = x
binding_binding = <super> KEY_TAB

[scale]
toggle = <ctrl> KEY_UP | KEY_F3 | swipe up 3
outer_margin = 56

[pixdecor]
right_button_x_offset = 9
button_layout = :close
";

fn cases() -> Vec<Case> {
    let d = default_ini();
    vec![
        Case { name: "default config", base: d.clone(), appearance: None, overrides: None },
        Case { name: "default config, empty overrides", base: d.clone(), appearance: None, overrides: Some("") },
        Case {
            name: "user overrides",
            base: d.clone(),
            appearance: Some(r#"{"dark": true}"#),
            overrides: Some(
                "[input]\nxkb_layout = br\nkb_repeat_rate = 40\n\n[pixdecor]\nright_button_x_offset = 9\nleft_button_spacing = 3\ntitle_font = Mine 11\n\n[decoration]\nbutton_order = close\nfont = Mine\n\n[core]\nvwidth = 3\n# vheight = 9\n\n[new-plugin]\nkey = value with = sign\n= nameless\n",
            ),
        },
        Case { name: "old values to migrate", base: KEPT.into(), appearance: None, overrides: Some("[scale]\nouter_margin = 28\n") },
        Case { name: "old values, no trailing newline", base: KEPT.trim_end().into(), appearance: Some(r#"{"buttons_side": "right"}"#), overrides: None },
        Case {
            name: "buttons on the right, own order, bigger, rounder",
            base: d.clone(),
            appearance: Some(r#"{"buttons_side": "right", "buttons_order": ["minimize", "close", "maximize"], "button_size": 16, "radius": {"window": 13, "dock": 20}, "dark": false}"#),
            overrides: Some("[pixdecor]\nright_button_x_offset = 9\nbutton_layout = close,minimize,maximize:\n"),
        },
        Case {
            name: "buttons on the left, radius 11 (a half to round)",
            base: d.clone(),
            appearance: Some(r#"{"buttons_side": "left", "radius": {"window": 11}, "button_size": 13.5, "dark": true}"#),
            overrides: Some("[pixdecor]\nleft_button_x_offset = 2\n"),
        },
        Case { name: "buttons on the left, radius 13 (8.5: half to even)", base: d.clone(), appearance: Some(r#"{"radius": {"window": 13}}"#), overrides: None },
        Case { name: "dark mode", base: d.clone(), appearance: Some(r#"{"dark": true, "accent": "pink"}"#), overrides: None },
        Case { name: "light mode", base: d.clone(), appearance: Some(r#"{"dark": false}"#), overrides: None },
        Case { name: "radius true, out of range sizes", base: d.clone(), appearance: Some(r#"{"radius": {"window": true}, "button_size": 99}"#), overrides: None },
        Case { name: "radius not an object", base: d.clone(), appearance: Some(r#"{"radius": 5, "button_size": 16, "buttons_side": "right"}"#), overrides: None },
        Case { name: "appearance a list", base: d.clone(), appearance: Some("[1, 2]"), overrides: None },
        Case { name: "appearance broken", base: d.clone(), appearance: Some("{\"radius\": "), overrides: None },
        Case { name: "appearance empty object", base: d.clone(), appearance: Some("{}"), overrides: None },
        Case { name: "big radius", base: d.clone(), appearance: Some(r#"{"radius": {"window": 24.5}, "button_size": 20}"#), overrides: None },
        Case {
            name: "CRLF config and overrides",
            base: d.replace('\n', "\r\n"),
            appearance: None,
            overrides: Some("[input]\r\nxkb_layout = de\r\n[]\r\nlost = 1\r\n"),
        },
        Case {
            name: "unicode whitespace",
            base: "[core]\nplugins =\u{a0}decoration sonata-resize\u{1c}\nxwayland_startup_script = \u{1c}\n[scale]\n\u{a0}toggle = KEY_F3\u{a0}\n".into(),
            appearance: None,
            overrides: Some("[input]\n\u{a0}xkb_layout\u{1c}= us\u{2028}\n"),
        },
        Case {
            name: "screenshot keys kept under their old names",
            base: d.replace("always_binding_", "binding_") + "[command]\nalways_binding_capture = KEY_F9\n",
            appearance: None,
            overrides: Some("[command]\nbinding_screenshot = <super> KEY_F12\nalways_binding_screenshot_area = KEY_F11\nbinding_screenshot_area = KEY_F10\n"),
        },
        Case { name: "empty config", base: String::new(), appearance: None, overrides: Some("[core]\nvwidth = 2\n") },
        Case { name: "only an xwayland line elsewhere", base: "xwayland_startup_script =\n[core]\nxwayland_startup_script =   own\n".into(), appearance: None, overrides: None },
    ]
}

#[test]
fn rust_steps_write_what_the_python_steps_write() {
    if !python() {
        eprintln!("python3 with the sonata2 package not found: golden test skipped");
        return;
    }
    let docs = heredocs();
    let mut failed = Vec::new();
    for case in cases() {
        let py = run_python(&case, &docs);
        let rs = run_rust(&case);
        if py.0 != rs.0 {
            failed.push(format!("{}: frame step differs\n--- python\n{}\n--- rust\n{}", case.name, String::from_utf8_lossy(&py.0), String::from_utf8_lossy(&rs.0)));
        }
        if py.3 != rs.3 {
            failed.push(format!("{}: overrides file migrated differently\n--- python\n{}\n--- rust\n{}", case.name, String::from_utf8_lossy(&py.3), String::from_utf8_lossy(&rs.3)));
        }
        if py.1 != rs.1 || py.2 != rs.2 {
            failed.push(format!("{}: overrides step differs (ok {} / {})\n--- python\n{}\n--- rust\n{}", case.name, py.2, rs.2, String::from_utf8_lossy(&py.1), String::from_utf8_lossy(&rs.1)));
        }
    }
    assert!(failed.is_empty(), "{}", failed.join("\n\n"));
}

/// A wayfire-overrides.ini that isn't UTF-8: the Python step fails (the
/// script then fails and the session uses the plain config), so does Rust.
#[test]
fn broken_overrides_fail_as_in_python() {
    let d = tempfile::tempdir().unwrap();
    let (out, over) = (d.path().join("o.ini"), d.path().join("v.ini"));
    std::fs::write(&out, "[core]\n").unwrap();
    std::fs::write(&over, b"[core]\nx = \xff\n").unwrap();
    assert!(wfconfig::overrides_step_file(&out, &over).is_err());
    assert_eq!(std::fs::read_to_string(&out).unwrap(), "[core]\n");
}
