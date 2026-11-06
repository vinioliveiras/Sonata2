//! Feedbacker's reports (feedback/report.py): one .zip in ~/Sonata Reports
//! with what a bug report needs -- your description, Sonata's logs,
//! `sonata2 doctor` and the system's versions. Nothing leaves the computer:
//! "Report on GitHub" opens a pre-filled issue in the browser and you
//! attach the .zip yourself.
//!
//! The logs are the session's (the Python Sonata runs it: its logs and
//! crash notes); on trial the Rust parts' own logs go in too (logs/rust/).

use sonata_i18n::{n_, tr, trn};
use std::io::Write;
use std::path::{Path, PathBuf};

pub const FOLDER: &str = "Sonata Reports";
const ISSUES: &str = "https://github.com/vinioliveiras/sonata2/issues/new";
/// browsers and GitHub cut longer links
const URL_MAX: usize = 6000;
/// characters of the title kept in the link
const TITLE_MAX: usize = 200;
const LOG_EXT: [&str; 2] = [".log", ".txt"];
const PACKAGES: [&str; 8] = ["mesa", "wayfire", "linux-cachyos", "linux", "gtk4", "libadwaita", "nvidia-utils", "nvidia-open-dkms"];
/// written by tools/sonata-session: "<epoch> <exit code>"
const CRASH: &str = "last-crash";
/// one line per crash: "<epoch> <kind> <exit code>"
const HISTORY: &str = "crashes.log";

/// Sonata's version (the VERSION file).
pub fn version() -> String {
    sonata_config::version().to_string()
}

/// ~/Sonata Reports (created).
pub fn folder() -> PathBuf {
    let p = glib::home_dir().join(FOLDER);
    let _ = std::fs::create_dir_all(&p);
    p
}

fn log_dir() -> PathBuf {
    sonata_session::logs::session_log_dir()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Crash {
    pub time: i64,
    pub code: i64,
}

pub fn parse_crash(text: &str) -> Option<Crash> {
    let mut p = text.split_whitespace();
    Some(Crash { time: p.next()?.parse().ok()?, code: p.next()?.parse().ok()? })
}

/// The last session's crash, if it had one.
pub fn crash() -> Option<Crash> {
    parse_crash(&std::fs::read_to_string(log_dir().join(CRASH)).ok()?)
}

/// Reported: the next login doesn't open Feedbacker again.
pub fn clear_crash() {
    let _ = std::fs::remove_file(log_dir().join(CRASH));
}

fn signal_name(n: i64) -> Option<&'static str> {
    const NAMES: [&str; 15] = ["SIGHUP", "SIGINT", "SIGQUIT", "SIGILL", "SIGTRAP", "SIGABRT", "SIGBUS", "SIGFPE", "SIGKILL", "SIGUSR1", "SIGSEGV", "SIGUSR2", "SIGPIPE", "SIGALRM", "SIGTERM"];
    NAMES.get(usize::try_from(n - 1).ok()?).copied()
}

/// "Sonata quit unexpectedly at 04:16 on Oct 03 (exit code 134, SIGABRT).";
/// local: in the session's language (shown), else English (the report's files).
pub fn crash_text(c: &Crash, local: bool) -> String {
    let why = if c.code > 128 { signal_name(c.code - 128).map(|s| format!(", {s}")).unwrap_or_default() } else { String::new() };
    let t = glib::DateTime::from_unix_local(c.time).ok();
    if local {
        let when = t.and_then(|t| t.format(&tr!("%H:%M on %b %d")).ok()).map(|s| s.to_string()).unwrap_or_default();
        return sonata_i18n::fmt(&tr!("Sonata quit unexpectedly at {when} (exit code {code}{why})."), &[("when", &when), ("code", &c.code.to_string()), ("why", &why)]);
    }
    const MONTHS: [&str; 12] = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    let when = t.map(|t| format!("{:02}:{:02} on {} {:02}", t.hour(), t.minute(), MONTHS[(t.month() - 1).clamp(0, 11) as usize], t.day_of_month())).unwrap_or_default();
    format!("Sonata quit unexpectedly at {when} (exit code {}{why}).", c.code)
}

// -- what took the session down (from the logs it left) ----------------------------------------
/// kind -> what Feedbacker says; the first that matches wins (most specific first)
pub const KINDS: [(&str, &str, &[&str]); 7] = [
    ("nvidia-memory", n_!("The NVIDIA card ran out of memory (an app filled it)."), &["NV_ERR_NO_MEMORY", "Failed to allocate NVKMS memory"]),
    ("amd-reset", n_!("The AMD graphics card stopped and was reset (a driver bug)."), &["amdgpu", "ring gfx", "timeout"]),
    ("gpu-reset", n_!("The graphics card was reset."), &["GPU reset"]),
    ("nvidia-pcie", n_!("The NVIDIA card's connection had errors (PCIe)."), &["BadTLP", "Xid"]),
    ("gpu-buffer", n_!("The graphics card refused a buffer."), &["gbm_bo_create failed", "Failed to allocate auxilliary"]),
    ("wayfire-abort", n_!("Wayfire stopped on an internal error."), &["Fatal error(SIGABRT)", "dassert"]),
    ("wayfire-segfault", n_!("Wayfire crashed (segmentation fault)."), &["Fatal error(SIGSEGV)", "wayfire["]),
];

/// The crash's kind from the kernel's lines and Wayfire's log.
pub fn classify(kernel: &str, session: &str) -> &'static str {
    let text = format!("{kernel}\n{session}");
    for (kind, _, marks) in KINDS {
        match kind {
            "amd-reset" => {
                if kernel.contains("amdgpu") && (kernel.contains("ring gfx") || kernel.contains("page fault")) && (kernel.contains("timeout") || session.contains("GPU reset")) {
                    return kind;
                }
            }
            "wayfire-segfault" => {
                if session.contains("Fatal error(SIGSEGV)") || (kernel.contains("segfault at") && kernel.contains("wayfire[")) {
                    return kind;
                }
            }
            _ => {
                if marks.iter().any(|m| text.contains(m)) {
                    return kind;
                }
            }
        }
    }
    "unknown"
}

pub fn describe(kind: &str) -> String {
    sonata_i18n::gettext(KINDS.iter().find(|k| k.0 == kind).map(|k| k.1).unwrap_or(n_!("The cause isn't in the logs.")))
}

fn read_log(name: &str, limit: u64) -> String {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut f) = std::fs::File::open(log_dir().join(name)) else { return String::new() };
    let size = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(size.saturating_sub(limit)));
    let mut b = Vec::new();
    let _ = f.read_to_end(&mut b);
    String::from_utf8_lossy(&b).into_owned()
}

/// [(epoch, kind, code)], oldest first; only the last `days` when given.
pub fn parse_history(text: &str, days: Option<f64>, now: f64) -> Vec<(i64, String, i64)> {
    let mut out: Vec<(i64, String, i64)> = text
        .lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split_whitespace().collect();
            (p.len() >= 3 && p[0].chars().all(|c| c.is_ascii_digit()) && !p[0].is_empty()).then(|| (p[0].parse().unwrap_or(0), p[1].to_string(), p[2].parse().unwrap_or(0)))
        })
        .collect();
    if let Some(d) = days {
        let limit = now - d * 86400.0;
        out.retain(|h| h.0 as f64 >= limit);
    }
    out
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub fn history(days: Option<f64>) -> Vec<(i64, String, i64)> {
    parse_history(&std::fs::read_to_string(log_dir().join(HISTORY)).unwrap_or_default(), days, now())
}

/// This crash's kind, written once into the history.
pub fn note_crash(c: &Crash) -> String {
    if let Some(h) = history(None).into_iter().find(|h| h.0 == c.time) {
        return h.1;
    }
    let session = Some(read_log("session.old.log", 4 << 20)).filter(|s| !s.is_empty()).unwrap_or_else(|| read_log("session.log", 4 << 20));
    let kind = classify(&read_log("kernel-at-crash.log", 4 << 20), &session);
    let path = log_dir().join(HISTORY);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{} {kind} {}", c.time, c.code);
        sonata_session::logs::trim(&path, None);
    }
    kind.to_string()
}

/// "3 crashes in the last 7 days: 2 AMD reset, 1 NVIDIA memory".
pub fn history_text_of(h: &[(i64, String, i64)], days: f64) -> String {
    if h.is_empty() {
        return String::new();
    }
    let short = |k: &str| match k {
        "nvidia-memory" => tr!("NVIDIA memory"),
        "amd-reset" => tr!("AMD reset"),
        "gpu-reset" => tr!("GPU reset"),
        "nvidia-pcie" => "NVIDIA PCIe".into(),
        "gpu-buffer" => tr!("refused buffer"),
        "wayfire-abort" => tr!("Wayfire error"),
        "wayfire-segfault" => tr!("Wayfire crash"),
        "unknown" => tr!("unknown"),
        other => other.to_string(),
    };
    let mut counts: Vec<(String, usize)> = Vec::new();
    for (_, kind, _) in h {
        match counts.iter_mut().find(|c| c.0 == *kind) {
            Some(c) => c.1 += 1,
            None => counts.push((kind.clone(), 1)),
        }
    }
    counts.sort_by(|a, b| b.1.cmp(&a.1)); // (stable: the first seen first among equals, as Python's sort)
    let parts = counts.iter().map(|(k, n)| format!("{n} {}", short(k))).collect::<Vec<_>>().join(", ");
    sonata_i18n::fmt(&trn!("{n} crash in the last {days} days: {parts}", "{n} crashes in the last {days} days: {parts}", h.len()), &[("n", &h.len().to_string()), ("days", &(days as i64).to_string()), ("parts", &parts)])
}

pub fn history_text() -> String {
    history_text_of(&history(Some(7.0)), 7.0)
}

pub fn monitoring() -> bool {
    sonata_session::logs::verbose()
}

pub fn set_monitoring(on: bool) {
    sonata_session::logs::set_verbose(on);
}

fn run(cmd: &[&str], timeout: u64) -> String {
    let (_, out) = sonata_backend::run::run(cmd, timeout);
    out.trim().to_string()
}

/// Short facts for the issue and system.txt (no names, no paths).
pub fn system_info() -> Vec<(&'static str, String)> {
    let pkgs = if sonata_backend::run::which("pacman").is_some() {
        let mut cmd = vec!["pacman", "-Q"];
        cmd.extend(PACKAGES);
        // (pacman exits 1 when one isn't installed: the rest are still listed)
        let out = std::process::Command::new("pacman").args(&cmd[1..]).stderr(std::process::Stdio::null()).output().map(|o| String::from_utf8_lossy(&o.stdout).into_owned()).unwrap_or_default();
        out.lines().map(|l| l.split_whitespace().collect::<Vec<_>>().join(" ")).collect::<Vec<_>>().join(", ")
    } else {
        String::new()
    };
    let gpus: Vec<String> = run(&["lspci"], 5).lines().filter(|l| l.contains("VGA") || l.contains("3D controller") || l.contains("Display controller")).map(|l| l.rsplit_once(": ").map(|x| x.1).unwrap_or(l).to_string()).collect();
    let about = sonata_backend::system::about();
    let distro = std::fs::read_to_string("/etc/os-release").ok().and_then(|t| t.lines().find_map(|l| l.strip_prefix("PRETTY_NAME=").map(|v| v.trim().trim_matches('"').to_string()))).unwrap_or_else(|| "Linux".into());
    vec![
        ("Sonata", version()),
        ("System", distro),
        ("Kernel", about.kernel),
        ("Desktop", std::env::var("XDG_CURRENT_DESKTOP").unwrap_or_default()),
        ("GPU", gpus.join("; ")),
        ("Monitoring", if monitoring() { "on" } else { "off" }.into()),
        ("Packages", pkgs),
    ]
}

/// doctor.txt: the checks and the last session's errors (report_text, as
/// report.py puts in), from this build's own doctor, in-process.
fn doctor_text() -> String {
    sonata_session::doctor::report_text(&sonata_session::doctor::build())
}

/// The text without terminal colour codes (ESC [ ... m).
pub fn regex_lite_strip(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\x1b' && chars.peek() == Some(&'[') {
            for d in chars.by_ref() {
                if d.is_ascii_alphabetic() {
                    break;
                }
            }
            continue;
        }
        out.push(c);
    }
    out
}

fn log_files(dir: &Path) -> Vec<PathBuf> {
    let mut names: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.is_file() && p.file_name().is_some_and(|n| { let n = n.to_string_lossy(); n != "doctor.txt" && LOG_EXT.iter().any(|e| n.ends_with(e)) })).collect())
        .unwrap_or_default();
    names.sort();
    names
}

/// "Sonata Report 2026-10-03 at 04.16.10.zip"
pub fn report_name(t: &glib::DateTime) -> String {
    t.format("Sonata Report %Y-%m-%d at %H.%M.%S.zip").map(|s| s.to_string()).unwrap_or_else(|_| "Sonata Report.zip".into())
}

fn zip_time(path: &Path) -> zip::DateTime {
    use std::os::unix::fs::MetadataExt;
    let t = std::fs::metadata(path).ok().and_then(|m| glib::DateTime::from_unix_local(m.mtime()).ok());
    // a log dated before 1980 (clock reset) is stored as 1980, not refused
    t.and_then(|t| zip::DateTime::from_date_and_time(t.year().max(1980) as u16, t.month() as u8, t.day_of_month() as u8, t.hour() as u8, t.minute() as u8, t.seconds() as u8).ok()).unwrap_or_default()
}

/// Write ~/Sonata Reports/Sonata Report <date>.zip into `dir`; its path.
pub fn create_in(dir: &Path, title: &str, description: &str, doctor: &str, info: &[(&str, String)], logs: &[(String, PathBuf)], crash: Option<Crash>) -> std::io::Result<PathBuf> {
    let now = glib::DateTime::now_local().map_err(|_| std::io::Error::other("no clock"))?;
    let path = dir.join(report_name(&now));
    let tmp = PathBuf::from(format!("{}.part", path.display()));
    let write = || -> zip::result::ZipResult<()> {
        let mut z = zip::ZipWriter::new(std::fs::File::create(&tmp)?);
        let opts = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        z.start_file("description.txt", opts)?;
        z.write_all(format!("{}\n\n{}\n", title.trim(), description.trim()).as_bytes())?;
        z.start_file("system.txt", opts)?;
        let mut sys: String = info.iter().map(|(k, v)| format!("{k}: {v}\n")).collect();
        if let Some(c) = crash {
            sys.push_str(&format!("Crash: {}\n", crash_text(&c, false)));
        }
        z.write_all(sys.as_bytes())?;
        z.start_file("doctor.txt", opts)?;
        z.write_all(doctor.as_bytes())?;
        for (name, p) in logs {
            if let Ok(data) = std::fs::read(p) {
                z.start_file(name.as_str(), opts.last_modified_time(zip_time(p)))?;
                z.write_all(&data)?;
            }
        }
        z.finish()?;
        Ok(())
    };
    match write() {
        Ok(()) => {
            std::fs::rename(&tmp, &path)?;
            Ok(path)
        }
        Err(e) => {
            let _ = std::fs::remove_file(&tmp); // never a .part left behind
            Err(std::io::Error::other(e))
        }
    }
}

/// The report, with the session's logs (and on trial the Rust parts' own).
pub fn create(title: &str, description: &str, info: &[(&str, String)]) -> std::io::Result<PathBuf> {
    let mut logs: Vec<(String, PathBuf)> = log_files(&log_dir()).into_iter().map(|p| (format!("logs/{}", p.file_name().unwrap_or_default().to_string_lossy()), p)).collect();
    let own = sonata_session::logs::log_dir();
    if own != log_dir() {
        logs.extend(log_files(&own).into_iter().map(|p| (format!("logs/rust/{}", p.file_name().unwrap_or_default().to_string_lossy()), p)));
    }
    create_in(&folder(), title, description, &doctor_text(), info, &logs, crash())
}

/// What a failed save says: the system's own words ("Permission denied"),
/// as report.py's toast shows an OSError's strerror -- not Rust's
/// "(os error 13)" after them.
pub fn error_text(e: &std::io::Error) -> String {
    let text = e.to_string();
    match (e.raw_os_error(), text.rfind(" (os error ")) {
        (Some(_), Some(i)) => text[..i].to_string(),
        _ => text,
    }
}

/// Python's urlencode (quote_plus): letters, digits and "-_.~" stay, a space is "+".
pub fn quote_plus(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(b as char),
            b' ' => out.push('+'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn chars_prefix(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map(|(i, _)| &s[..i]).unwrap_or(s)
}

/// A GitHub "new issue" link with the title, description and versions.
pub fn issue_url(title: &str, description: &str, report_name: &str, info: &[(&str, String)]) -> String {
    let mut title = title.trim().to_string();
    if title.chars().count() > TITLE_MAX {
        // a pasted log as the title: the link stays short
        title = format!("{}…", chars_prefix(&title, TITLE_MAX).trim_end());
    }
    let facts = info.iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("- **{k}**: {v}")).collect::<Vec<_>>().join("\n");
    let attach = if report_name.is_empty() { String::new() } else { format!("\n\n**Report:** please drag `{report_name}` (in ~/{FOLDER}) here.") };
    let what = if description.trim().is_empty() { "(describe the problem)" } else { description.trim() };
    let mut body = format!("### What happened\n\n{what}\n\n### System\n\n{facts}{attach}\n");
    let url = |b: &str| format!("{ISSUES}?title={}&body={}", quote_plus(if title.is_empty() { "Bug report" } else { &title }), quote_plus(b));
    let mut desc = description.to_string();
    while url(&body).len() > URL_MAX && desc.chars().count() > 200 {
        desc = chars_prefix(&desc, desc.chars().count() * 3 / 4).to_string();
        body = format!("### What happened\n\n{}…\n\n(full text in the report)\n\n### System\n\n{facts}{attach}\n", desc.trim());
    }
    while url(&body).len() > URL_MAX && !body.is_empty() {
        // still too long (many packages, odd characters)
        body = chars_prefix(&body, body.chars().count() * 3 / 4).to_string();
    }
    url(&body)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crash_notes_and_their_kind() {
        assert_eq!(parse_crash("1759457770 134\n"), Some(Crash { time: 1759457770, code: 134 }));
        assert_eq!(parse_crash("garbage"), None);
        assert!(crash_text(&Crash { time: 0, code: 134 }, false).ends_with("(exit code 134, SIGABRT)."));
        assert!(crash_text(&Crash { time: 0, code: 1 }, false).ends_with("(exit code 1)."));
        assert_eq!(classify("NVRM: NV_ERR_NO_MEMORY", ""), "nvidia-memory");
        assert_eq!(classify("amdgpu: ring gfx timeout", ""), "amd-reset");
        assert_eq!(classify("amdgpu: something", ""), "unknown"); // amdgpu alone isn't a reset
        assert_eq!(classify("", "EE: Fatal error(SIGSEGV)"), "wayfire-segfault");
        assert_eq!(classify("wayfire[123]: segfault at 0", ""), "wayfire-segfault");
        assert_eq!(classify("", "dassert failed"), "wayfire-abort");
    }

    #[test]
    fn the_last_week_of_crashes() {
        sonata_i18n::use_language("en");
        let h = parse_history("100 amd-reset 134\n200 amd-reset 134\nbad line\n300 nvidia-memory 1\n", Some(7.0), 300.0 + 86400.0 * 7.0 - 150.0);
        assert_eq!(h.len(), 2); // the first is older than a week
        assert_eq!(history_text_of(&h, 7.0), "2 crashes in the last 7 days: 1 AMD reset, 1 NVIDIA memory");
        assert_eq!(history_text_of(&[], 7.0), "");
    }

    #[test]
    fn links_like_pythons_urlencode() {
        assert_eq!(quote_plus("a b/c?é"), "a+b%2Fc%3F%C3%A9");
        let info = vec![("Sonata", "0.22".to_string()), ("GPU", String::new())];
        let u = issue_url("Crash", "It broke", "r.zip", &info);
        assert!(u.starts_with("https://github.com/vinioliveiras/sonata2/issues/new?title=Crash&body="));
        assert!(u.contains("%2A%2ASonata%2A%2A%3A+0.22") && !u.contains("GPU"));
        let long = "x".repeat(20000);
        assert!(issue_url(&"t".repeat(300), &long, "", &info).len() <= 6000);
    }

    #[test]
    fn a_report_holds_its_files() {
        let d = tempfile::tempdir().unwrap();
        let log = d.path().join("session.log");
        std::fs::write(&log, "hello").unwrap();
        let p = create_in(d.path(), " Title ", "What\n", "doctor ok\n", &[("Sonata", "x".into())], &[("logs/session.log".into(), log)], Some(Crash { time: 0, code: 139 })).unwrap();
        let mut z = zip::ZipArchive::new(std::fs::File::open(&p).unwrap()).unwrap();
        let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
        assert_eq!(names, ["description.txt", "system.txt", "doctor.txt", "logs/session.log"]);
        let mut s = String::new();
        std::io::Read::read_to_string(&mut z.by_name("system.txt").unwrap(), &mut s).unwrap();
        assert!(s.starts_with("Sonata: x\nCrash: Sonata quit unexpectedly") && s.contains("SIGSEGV"));
        assert!(!std::fs::read_dir(d.path()).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().ends_with(".part")));
    }

    // tests/test_crash_kinds.py's logs: what took the session down that day
    const AMD: &str = "kernel: amdgpu 0000:36:00.0: [gfxhub] page fault (src_id:0 ring:24 vmid:4 pasid:1589)\n\
        kernel: amdgpu 0000:36:00.0: ring gfx_0.0.0 timeout, signaled seq=1153010, emitted seq=1153011\n\
        kernel: amdgpu 0000:36:00.0: Ring gfx_0.0.0 reset succeeded";
    const AMD_WF: &str = "EE [GLES2] GL_CONTEXT_LOST in context lost\nEE [render/gles2/pass.c:315] GPU reset (guilty)";
    const NV_OOM: &str = "kernel: NVRM: GPU0 nvAssertOkFailedNoLog: Assertion failed: Out of memory [NV_ERR_NO_MEMORY]";
    const NV_WF: &str = "EE [render/allocator/gbm.c:89] gbm_bo_create failed: Invalid argument\n\
        EE [src/render.cpp:465] Failed to allocate auxilliary buffer!\n\
        EE [src/api/wayfire/dassert.hpp:26] Cannot run a render pass without a valid target!";

    #[test]
    fn test_todays_crashes() {
        sonata_i18n::use_language("en");
        assert_eq!(classify(AMD, AMD_WF), "amd-reset");
        assert_eq!(classify(NV_OOM, NV_WF), "nvidia-memory"); // the memory, not the buffer
        assert_eq!(classify("", NV_WF), "gpu-buffer");
        assert_eq!(classify("", "EE [src/src/main.cpp:144] Fatal error: Fatal error(SIGABRT)"), "wayfire-abort");
        assert_eq!(classify("kernel: wayfire[2150]: segfault at 21 ip 0 error 4", ""), "wayfire-segfault");
        assert_eq!(classify("", ""), "unknown");
        assert!(describe("amd-reset").contains("AMD"));
    }

    #[test]
    fn test_noted_once_with_history() {
        sonata_i18n::use_language("en");
        let dir = log_dir();
        std::fs::create_dir_all(&dir).unwrap();
        for n in [HISTORY, "kernel-at-crash.log", "session.old.log"] {
            let _ = std::fs::remove_file(dir.join(n));
        }
        let write = |n: &str, t: &str| std::fs::write(dir.join(n), t).unwrap();
        write("kernel-at-crash.log", AMD);
        write("session.old.log", AMD_WF);
        let t = now() as i64;
        let c = Crash { time: t - 60, code: 1 };
        assert_eq!(note_crash(&c), "amd-reset");
        assert_eq!(note_crash(&c), "amd-reset"); // once
        assert_eq!(history(None).len(), 1);
        write("kernel-at-crash.log", NV_OOM);
        write("session.old.log", NV_WF);
        note_crash(&Crash { time: t - 30, code: 134 });
        let mut f = std::fs::OpenOptions::new().append(true).open(dir.join(HISTORY)).unwrap();
        writeln!(f, "{} amd-reset 1", t - 30 * 86400).unwrap(); // a month ago: not counted
        assert_eq!(history_text(), "2 crashes in the last 7 days: 1 AMD reset, 1 NVIDIA memory");
    }

    /// A report that couldn't be saved said "Permission denied (os error
    /// 13)"; the Python Feedbacker says "Permission denied".
    #[test]
    fn test_save_error_said_os_error_number() {
        assert_eq!(error_text(&std::io::Error::from_raw_os_error(13)), std::io::Error::from_raw_os_error(13).to_string().split(" (os error").next().unwrap());
        assert!(!error_text(&std::io::Error::from_raw_os_error(13)).contains("os error"));
        assert_eq!(error_text(&std::io::Error::other("no clock")), "no clock");
    }

    #[test]
    fn terminal_colours_taken_out() {
        assert_eq!(regex_lite_strip("\x1b[32mok\x1b[0m done"), "ok done");
    }

    #[test]
    fn the_version_is_sonatas() {
        assert_eq!(version(), std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../VERSION")).unwrap().trim());
    }
}
