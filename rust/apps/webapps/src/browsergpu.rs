//! The start of a Chromium web app's command (browsergpu.py's
//! web_app_command and what it needs). browsergpu.py isn't ported as a
//! whole yet (the app menus' Vulkan / WebGPU choices, the flag files they
//! write): only these few read-only lines, so a web app started by the Rust
//! build gets the same browser flags as one the Python build starts.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};

const NAME: &str = "browsergpu";
const MARK: &str = "# Sonata: Vulkan / WebGPU (the app's right-click menu > Options)";
const MARKS: [&str; 2] = [MARK, "# Sonata: WebGPU for browser games (Settings > Displays > Games)"];
const X11: &str = "--ozone-platform=x11";
const WEBGPU: &str = "--enable-unsafe-webgpu";
const FLAGS: [&str; 2] = [X11, WEBGPU];
const FEATURE: &str = "Vulkan";
const OWN: &str = "--enable-features=Vulkan";

/// the program -> its launcher's flag file
fn chromium_bin_file(name: &str) -> Option<&'static str> {
    Some(match name {
        "google-chrome" | "google-chrome-stable" => "chrome-flags.conf",
        "google-chrome-beta" => "chrome-beta-flags.conf",
        "google-chrome-unstable" => "chrome-dev-flags.conf",
        "chromium" => "chromium-flags.conf",
        "brave" | "brave-browser" => "brave-flags.conf",
        "vivaldi" | "vivaldi-stable" => "vivaldi-stable.conf",
        "thorium-browser" => "thorium-flags.conf",
        "microsoft-edge-stable" => "microsoft-edge-stable-flags.conf",
        _ => return None,
    })
}

/// Sonata's own lines for these choices (Vulkan goes in --enable-features).
fn flags(vulkan: bool, webgpu: bool) -> Vec<String> {
    let mut out = Vec::new();
    if vulkan || webgpu {
        out.push(X11.to_string());
    }
    if webgpu {
        out.push(WEBGPU.to_string());
    }
    out
}

/// The flag file without Sonata's block (edit(lines, [], appended): the
/// user's own lines, Vulkan out of their --enable-features if Sonata put it there).
fn without_block(lines: &[String], appended: bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        if MARKS.contains(&lines[i].trim()) {
            i += 1;
            while i < lines.len() && (FLAGS.contains(&lines[i].trim()) || lines[i].trim() == OWN) {
                i += 1;
            }
            continue;
        }
        out.push(lines[i].clone());
        i += 1;
    }
    if appended {
        if let Some(user) = out.iter().position(|l| l.trim().starts_with("--enable-features=")) {
            let names: Vec<String> = out[user].trim().split_once('=').map(|x| x.1).unwrap_or("").split(',').filter(|n| !n.is_empty() && *n != FEATURE).map(String::from).collect();
            out[user] = format!("--enable-features={}", names.join(","));
        }
    }
    out
}

/// The flags in Sonata's block of a flag file ([] = none).
pub(crate) fn block(lines: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    let mut inside = false;
    for ln in lines {
        let s = ln.trim();
        if MARKS.contains(&s) {
            inside = true;
        } else if inside && FLAGS.contains(&s) {
            out.push(s.to_string());
        } else if inside && s != OWN {
            inside = false;
        }
    }
    out
}

fn cfg(cfg_dir: Option<&Path>) -> PathBuf {
    cfg_dir.map(Path::to_path_buf).unwrap_or_else(glib::user_config_dir)
}

fn read_lines(path: &Path) -> Vec<String> {
    std::fs::read_to_string(path).map(|t| t.lines().map(String::from).collect()).unwrap_or_default()
}

/// Chrome keeps only the last --enable-features: `extra` joins theirs.
fn features(mut flags: Vec<String>, extra: &str) -> Vec<String> {
    match flags.iter().position(|f| f.starts_with("--enable-features=")) {
        None => {
            flags.push(format!("--enable-features={extra}"));
            flags
        }
        Some(at) => {
            let mut names: Vec<String> = flags[at].split_once('=').map(|x| x.1).unwrap_or("").split(',').filter(|n| !n.is_empty()).map(String::from).collect();
            if !names.iter().any(|n| n == extra) {
                names.push(extra.into());
            }
            flags[at] = format!("--enable-features={}", names.join(","));
            flags
        }
    }
}

/// shlex.split, enough for a flag file's line (quotes and backslashes);
/// None when a quote is left open.
pub(crate) fn shell_split(line: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut have = false;
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match c {
            '\'' => {
                have = true;
                loop {
                    match chars.next()? {
                        '\'' => break,
                        x => cur.push(x),
                    }
                }
            }
            '"' => {
                have = true;
                loop {
                    match chars.next()? {
                        '"' => break,
                        '\\' => match chars.next()? {
                            x @ ('"' | '\\' | '$' | '`') => cur.push(x),
                            x => {
                                cur.push('\\');
                                cur.push(x);
                            }
                        },
                        x => cur.push(x),
                    }
                }
            }
            '\\' => {
                have = true;
                cur.push(chars.next()?);
            }
            '#' if !have => break, // a comment
            c if c.is_whitespace() => {
                if have {
                    out.push(std::mem::take(&mut cur));
                    have = false;
                }
            }
            c => {
                have = true;
                cur.push(c);
            }
        }
    }
    if have {
        out.push(cur);
    }
    Some(out)
}

fn appended(name: &str) -> bool {
    let data = sonata_config::load(NAME, &json!({"vulkan": {}, "webgpu": {}, "appended": {}}));
    data.get("appended").and_then(|a| a.get(name)).is_some_and(|v| v.as_bool().unwrap_or(false))
}

/// The start of a web app's command: with Sonata's block in the browser's
/// flag file, the browser's own program -- past the launcher, which would
/// add that block -- with the user's own flags from the file. Unknown
/// launchers: as they are.
pub fn web_app_browser(browser: &str, cfg_dir: Option<&Path>) -> Vec<String> {
    let base = Path::new(browser).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let (target, name) = if base == "chromium" {
        // a launcher that is a program, not a script
        ("/usr/lib/chromium/chromium".to_string(), "chromium-flags.conf".to_string())
    } else {
        let Ok(head) = std::fs::read(browser).map(|mut b| {
            b.truncate(8192);
            b
        }) else {
            return vec![browser.into()];
        };
        if !head.starts_with(b"#!") {
            return vec![browser.into()];
        }
        let text = String::from_utf8_lossy(&head);
        let conf = regex::Regex::new(r"([\w.+-]+\.conf)\b").ok().and_then(|r| r.captures(&text).map(|c| c[1].to_string()));
        let exec = regex::Regex::new(r#"(?m)^\s*exec\s+["']?(/[^\s"']+)"#).ok().and_then(|r| r.captures(&text).map(|c| c[1].to_string()));
        match (exec, conf) {
            (Some(x), Some(m)) => (x, m),
            _ => return vec![browser.into()],
        }
    };
    let lines = read_lines(&cfg(cfg_dir).join(&name));
    let runnable = std::fs::metadata(&target).is_ok_and(|m| {
        use std::os::unix::fs::PermissionsExt;
        m.is_file() && m.permissions().mode() & 0o111 != 0
    });
    if block(&lines).is_empty() || !runnable {
        return vec![browser.into()];
    }
    let mut out = vec![target];
    for ln in without_block(&lines, appended(&name)) {
        if !ln.trim().is_empty() && !ln.trim_start().starts_with('#') {
            out.extend(shell_split(&ln).unwrap_or_else(|| ln.split_whitespace().map(String::from).collect()));
        }
    }
    out
}

/// The user's choice for a web app (the app menu's Use Vulkan / Allow WebGPU).
fn choice(what: &str, desktop_id: &str) -> bool {
    let data = sonata_config::load(NAME, &json!({"vulkan": {}, "webgpu": {}, "appended": {}}));
    data.get(what).and_then(|c| c.get(desktop_id)).is_some_and(|v| match v {
        Value::Bool(b) => *b,
        Value::Null => false,
        Value::Number(n) => n.as_f64() != Some(0.0),
        _ => true,
    })
}

/// The browser and the web app's own Vulkan/WebGPU flags; without them a
/// Wayland window without Vulkan even when the browser's file asks for them
/// (an unknown launcher adds that file).
pub fn web_app_command(app: &str, browser: &str, cfg_dir: Option<&Path>) -> Vec<String> {
    let did = crate::desktop_id(app);
    let argv = web_app_browser(browser, cfg_dir);
    let wg = choice("webgpu", &did);
    let vk = choice("vulkan", &did) || wg;
    if vk || wg {
        let mut all = argv;
        all.extend(flags(vk, wg));
        return features(all, FEATURE);
    }
    let base = Path::new(browser).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let file = chromium_bin_file(&base).unwrap_or("-");
    if argv == [browser.to_string()] && !block(&read_lines(&cfg(cfg_dir).join(file))).is_empty() {
        let mut all = argv;
        all.extend(["--ozone-platform=wayland".to_string(), "--disable-features=Vulkan".to_string()]);
        return all;
    }
    argv
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn block_and_its_removal() {
        let lines = s(&["--foo", "--enable-features=A,Vulkan", MARK, X11, WEBGPU, "--bar"]);
        assert_eq!(block(&lines), s(&[X11, WEBGPU]));
        assert_eq!(without_block(&lines, true), s(&["--foo", "--enable-features=A", "--bar"]));
        assert_eq!(without_block(&lines, false), s(&["--foo", "--enable-features=A,Vulkan", "--bar"]));
    }

    #[test]
    fn features_joined() {
        assert_eq!(features(s(&["b", "--enable-features=X"]), "Vulkan"), s(&["b", "--enable-features=X,Vulkan"]));
        assert_eq!(features(s(&["b"]), "Vulkan"), s(&["b", "--enable-features=Vulkan"]));
    }

    #[test]
    fn shell_words() {
        assert_eq!(shell_split(r#"--a="b c" 'd e' f\ g"#), Some(s(&["--a=b c", "d e", "f g"])));
        assert_eq!(shell_split("--a 'open"), None);
    }

    #[test]
    fn unknown_browser_as_it_is() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(web_app_browser("/nonexistent/browser", Some(d.path())), s(&["/nonexistent/browser"]));
    }
}
