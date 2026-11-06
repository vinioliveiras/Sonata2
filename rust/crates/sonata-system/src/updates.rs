//! Software Update (backend/updates.py; Settings): what can be updated, and
//! updating it in place.
//!
//! Sources, each checked on its own (a slow AUR doesn't hold the others):
//! system (pacman's checkupdates, dnf, apt, zypper), aur (yay or paru) and
//! flatpak. Updating runs in the app, one step per source, with the output
//! streamed to the details log and a progress fraction read from pacman's
//! "(3/12)" lines. Root steps go through pkexec (asked once per update:
//! plan() runs System and AUR as one process, and install.sh's polkit rule
//! keeps the password for it). Sources without an unattended mode (apt,
//! dnf, zypper) update in a terminal.

use gio::prelude::*;
use sonata_backend::run::which;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

#[derive(Clone, Debug, PartialEq, Default)]
pub struct Update {
    pub name: String,
    pub old: String,
    pub new: String,
}

fn up(name: &str, old: &str, new: &str) -> Update {
    Update { name: name.into(), old: old.into(), new: new.into() }
}

#[derive(Clone, Debug)]
pub struct Source {
    pub id: String,
    pub title: String,
    pub list_cmd: Vec<String>,
    /// run in the app (None: terminal only)
    pub install: Option<Vec<String>>,
    /// the same, for a terminal
    pub terminal: String,
    pub parse: fn(&str) -> Vec<Update>,
}

fn v(a: &[&str]) -> Vec<String> {
    a.iter().map(|s| s.to_string()).collect()
}

// One password per update (Vini: it was asked again and again): the AUR
// helper runs pkexec pacman once per step, and pkexec asks every time.
// install.sh adds this rule: after the password, pacman through pkexec
// isn't asked again for a few minutes, for the same caller only.
pub const POLKIT_RULE_FILE: &str = "/etc/polkit-1/rules.d/60-sonata2-updates.rules";

// -- parsing ----------------------------------------------------------------------------------
/// pacman / checkupdates / yay -Qua / paru -Qua: "name old -> new".
pub fn parse_arrow(out: &str) -> Vec<Update> {
    out.lines()
        .filter(|ln| !ln.contains("[ignored]"))
        .filter_map(|ln| {
            let p: Vec<&str> = ln.split_whitespace().collect();
            (p.len() >= 4 && p[2] == "->").then(|| up(p[0], p[1], p[3]))
        })
        .collect()
}

/// apt list --upgradable: "name/suite new arch [upgradable from: old]".
pub fn parse_apt(out: &str) -> Vec<Update> {
    out.lines()
        .filter(|ln| ln.contains('/') && !ln.starts_with("Listing"))
        .map(|ln| {
            let parts: Vec<&str> = ln.split_whitespace().collect();
            let old = ln.rsplit_once("from:").map(|(_, o)| o.trim().trim_matches([' ', ']'])).unwrap_or("");
            up(parts[0].split('/').next().unwrap_or(""), old, parts.get(1).copied().unwrap_or(""))
        })
        .collect()
}

/// dnf check-update / zypper list-updates: the name, then the version.
pub fn parse_columns(out: &str) -> Vec<Update> {
    let mut ups = Vec::new();
    for ln in out.lines() {
        let s = ln.trim();
        if s.is_empty() || ["Last metadata", "Loading", "S |", "--", "Obsoleting"].iter().any(|p| s.starts_with(p)) {
            continue;
        }
        if s.contains('|') {
            // zypper table
            let cols: Vec<&str> = s.split('|').map(str::trim).collect();
            if cols.len() >= 6 {
                ups.push(up(cols[2], cols[3], cols[4]));
            }
            continue;
        }
        let parts: Vec<&str> = s.split_whitespace().collect();
        ups.push(up(parts[0], "", parts.get(1).copied().unwrap_or("")));
    }
    ups
}

/// flatpak remote-ls --updates --columns=name,application,version (tab separated).
pub fn parse_flatpak(out: &str) -> Vec<Update> {
    out.lines()
        .filter_map(|ln| {
            let cols: Vec<&str> = ln.split('\t').collect();
            let app = cols.get(1)?.trim();
            if app.is_empty() || app == "Application ID" {
                return None;
            }
            let name = if cols[0].trim().is_empty() { app } else { cols[0].trim() };
            Some(up(name, "", cols.get(2).map(|c| c.trim()).unwrap_or("")))
        })
        .collect()
}

// -- what is here -----------------------------------------------------------------------------
fn aur_helper() -> Option<&'static str> {
    ["paru", "yay"].into_iter().find(|h| which(h).is_some())
}

pub fn sources() -> Vec<Source> {
    let mut out = Vec::new();
    let src = |id: &str, title: String, list: Vec<String>, install: Option<Vec<String>>, terminal: &str, parse: fn(&str) -> Vec<Update>| Source { id: id.into(), title, list_cmd: list, install, terminal: terminal.into(), parse };
    if which("pacman").is_some() {
        let lst = if which("checkupdates").is_some() { v(&["checkupdates", "--nocolor"]) } else { v(&["pacman", "-Qu"]) };
        out.push(src("system", tr!("System"), lst, Some(v(&["pkexec", "pacman", "-Syu", "--noconfirm"])), "sudo pacman -Syu", parse_arrow));
        if let Some(helper) = aur_helper() {
            let flags: &[&str] = if helper == "paru" { &["--skipreview"] } else { &["--answerdiff", "None", "--answerclean", "None", "--answeredit", "None"] };
            let mut install = v(&[helper, "-Sua", "--noconfirm", "--sudo", "pkexec"]);
            install.extend(v(flags));
            out.push(src("aur", "AUR".into(), v(&[helper, "-Qua"]), Some(install), &format!("{helper} -Sua"), parse_arrow));
        }
    } else if which("dnf").is_some() {
        out.push(src("system", tr!("System"), v(&["dnf", "-q", "check-update"]), None, "sudo dnf upgrade", parse_columns));
    } else if which("apt").is_some() {
        out.push(src("system", tr!("System"), v(&["apt", "list", "--upgradable"]), None, "sudo apt update && sudo apt upgrade", parse_apt));
    } else if which("zypper").is_some() {
        out.push(src("system", tr!("System"), v(&["zypper", "-q", "list-updates"]), None, "sudo zypper update", parse_columns));
    }
    if which("flatpak").is_some() {
        out.push(src("flatpak", "Flatpak".into(), v(&["flatpak", "remote-ls", "--updates", "--columns=name,application,version"]), Some(v(&["flatpak", "update", "-y", "--noninteractive"])), "flatpak update", parse_flatpak));
    }
    // (Sonata itself: About > Sonata Update, apart from the system's -- Vini)
    out
}

/// The steps to run for `srcs`: System and AUR together become one run of
/// the AUR helper (-Syu: the repositories, then the AUR). Every pkexec then
/// comes from that one process, and polkit keeps the password for it:
/// asked once per update, not once per step (Vini).
pub fn plan(srcs: &[Source]) -> Vec<Source> {
    let ids: Vec<&str> = srcs.iter().map(|s| s.id.as_str()).collect();
    let (Some(si), Some(ai)) = (ids.iter().position(|i| *i == "system"), ids.iter().position(|i| *i == "aur")) else { return srcs.to_vec() };
    let aur = &srcs[ai];
    let both = Source { id: "system+aur".into(), title: tr!("System and AUR"), list_cmd: aur.list_cmd.clone(), install: aur.install.as_ref().map(|i| i.iter().map(|a| if a == "-Sua" { "-Syu".to_string() } else { a.clone() }).collect()), terminal: aur.terminal.clone(), parse: aur.parse };
    let mut out: Vec<Source> = srcs.iter().filter(|s| s.id != "system" && s.id != "aur").cloned().collect();
    out.insert(si.min(ai).min(out.len()), both);
    out
}

/// Sonata itself (selfupdate: its GitHub releases). Updated in a terminal:
/// its installer may ask for sudo.
pub fn sonata_source() -> Source {
    let mut list = crate::selfupdate::self_argv();
    list.extend(v(&["self-update", "--check"]));
    Source { id: "sonata".into(), title: "Sonata".into(), list_cmd: list, install: None, terminal: crate::selfupdate::terminal_command(""), parse: parse_arrow }
}

/// The updates of one source; None when the check itself failed (offline,
/// AUR down...). checkupdates exits 2 for "nothing"; dnf 100 for "some".
/// Blocking: run it in a thread.
pub fn check(src: &Source) -> Option<Vec<Update>> {
    let cmd: Vec<&str> = src.list_cmd.iter().map(String::as_str).collect();
    let (rc, out) = crate::run_c(&cmd, 120);
    if rc == 127 || (![0, 2, 100].contains(&rc) && out.trim().is_empty()) {
        return None;
    }
    Some((src.parse)(&out))
}

// -- updating ---------------------------------------------------------------------------------
/// The "(3/12)" of a pacman line: 3/12.
pub fn step_fraction(text: &str) -> Option<f64> {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let m = RE.get_or_init(|| regex::Regex::new(r"\(\s*(\d+)/(\d+)\)").unwrap()).captures(text)?;
    let (a, b): (f64, f64) = (m[1].parse().ok()?, m[2].parse().ok()?);
    (b != 0.0).then(|| a / b)
}

struct Inner {
    steps: Vec<Vec<String>>,
    on_line: Box<dyn Fn(&str)>,
    /// 0..1, or None: pulsing
    on_fraction: Box<dyn Fn(Option<f64>)>,
    /// (ok, the failed step's index or -1)
    on_done: Box<dyn Fn(bool, i64)>,
    index: Cell<i64>,
    proc: RefCell<Option<gio::Subprocess>>,
    cancel: gio::Cancellable,
}

/// Runs the steps (argv) one after the other, stopping at the first
/// failure. Main thread.
#[derive(Clone)]
pub struct Runner(Rc<Inner>);

impl Runner {
    pub fn new(steps: Vec<Vec<String>>, on_line: impl Fn(&str) + 'static, on_fraction: impl Fn(Option<f64>) + 'static, on_done: impl Fn(bool, i64) + 'static) -> Self {
        Self(Rc::new(Inner { steps, on_line: Box::new(on_line), on_fraction: Box::new(on_fraction), on_done: Box::new(on_done), index: Cell::new(-1), proc: RefCell::default(), cancel: gio::Cancellable::new() }))
    }

    pub fn start(&self) {
        self.next();
    }

    pub fn stop(&self) {
        self.0.cancel.cancel();
        if let Some(p) = self.0.proc.borrow().as_ref() {
            p.force_exit();
        }
    }

    fn next(&self) {
        let me = &self.0;
        let i = me.index.get() + 1;
        me.index.set(i);
        let Some(argv) = me.steps.get(i as usize) else {
            (me.on_done)(true, -1);
            return;
        };
        (me.on_fraction)(None);
        (me.on_line)(&format!("$ {}", argv.join(" ")));
        let launcher = gio::SubprocessLauncher::new(gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_MERGE);
        launcher.setenv("LC_ALL", "C", true);
        let args: Vec<&std::ffi::OsStr> = argv.iter().map(std::ffi::OsStr::new).collect();
        let proc = match launcher.spawn(&args) {
            Ok(p) => p,
            Err(e) => {
                (me.on_line)(e.message());
                (me.on_done)(false, i);
                return;
            }
        };
        let Some(out) = proc.stdout_pipe() else { return };
        *me.proc.borrow_mut() = Some(proc.clone());
        let (w1, w2) = (Rc::downgrade(&self.0), Rc::downgrade(&self.0));
        // bytes, split there: a non-UTF-8 byte must not end the read (pacman
        // then blocks or dies of SIGPIPE mid-upgrade)
        crate::linereader::read_lines(
            &out,
            Some(&me.cancel),
            move |text| {
                let Some(me) = w1.upgrade() else { return };
                // progress bars redraw with \r
                for part in text.split('\r').filter(|p| !p.trim().is_empty()) {
                    if let Some(f) = step_fraction(part) {
                        (me.on_fraction)(Some(f));
                    }
                    (me.on_line)(part.trim_end());
                }
            },
            move || {
                let Some(me) = w2.upgrade() else { return };
                let cancel = me.cancel.clone();
                let p = proc.clone();
                p.wait_async(Some(&cancel), move |_| {
                    let r = Runner(me);
                    if r.0.cancel.is_cancelled() {
                        return;
                    }
                    if proc.has_exited() && proc.exit_status() == 0 {
                        r.next();
                    } else {
                        (r.0.on_done)(false, r.0.index.get());
                    }
                });
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(u: &[Update]) -> Vec<(&str, &str, &str)> {
        u.iter().map(|u| (u.name.as_str(), u.old.as_str(), u.new.as_str())).collect()
    }

    #[test]
    fn parsers() {
        assert_eq!(t(&parse_arrow("linux 6.1-1 -> 6.2-1\nfoo 1 -> 2 [ignored]\n")), [("linux", "6.1-1", "6.2-1")]);
        assert_eq!(t(&parse_apt("Listing...\nfirefox/jammy 131 amd64 [upgradable from: 130]\n")), [("firefox", "130", "131")]);
        assert_eq!(parse_flatpak("Spotify\tcom.spotify.Client\t1.2\n")[0].name, "Spotify");
        assert_eq!(t(&parse_columns("Last metadata x\nkernel.x86_64 6.2 updates\n")), [("kernel.x86_64", "", "6.2")]);
        assert_eq!(t(&parse_columns("S | Repository | Name | Current | Available | Arch\nv | Main | vim | 9.0 | 9.1 | x86_64\n")), [("vim", "9.0", "9.1")]);
    }

    /// System and AUR run as one AUR-helper process: polkit keeps the
    /// password for that process, so it is asked once.
    #[test]
    fn one_run_for_system_and_aur() {
        let s = |id: &str, install: &[&str]| Source { id: id.into(), title: id.into(), list_cmd: vec![], install: Some(v(install)), terminal: String::new(), parse: parse_arrow };
        let (sysu, aur, flat) = (s("system", &["pkexec", "pacman", "-Syu", "--noconfirm"]), s("aur", &["paru", "-Sua", "--noconfirm", "--sudo", "pkexec", "--skipreview"]), s("flatpak", &["flatpak", "update", "-y"]));
        let steps = plan(&[sysu.clone(), aur.clone(), flat.clone()]);
        assert_eq!(steps.iter().map(|s| s.id.as_str()).collect::<Vec<_>>(), ["system+aur", "flatpak"]);
        assert_eq!(steps[0].install.as_deref().unwrap(), v(&["paru", "-Syu", "--noconfirm", "--sudo", "pkexec", "--skipreview"]));
        assert_eq!(plan(&[sysu, flat]).iter().map(|s| s.id.as_str()).collect::<Vec<_>>(), ["system", "flatpak"]); // no AUR: as before
        assert_eq!(plan(&[aur]).iter().map(|s| s.id.as_str()).collect::<Vec<_>>(), ["aur"]);
    }

    #[test]
    fn check_reads_and_fails() {
        let s = |cmd: &[&str]| Source { id: "x".into(), title: "x".into(), list_cmd: v(cmd), install: None, terminal: String::new(), parse: parse_arrow };
        assert_eq!(t(&check(&s(&["printf", "linux 6.1-1 -> 6.2-1\\n"])).unwrap()), [("linux", "6.1-1", "6.2-1")]);
        assert_eq!(check(&s(&["false"])).map(|u| u.len()), None);
        assert_eq!(check(&s(&["sh", "-c", "exit 2"])).map(|u| u.len()), Some(0)); // checkupdates: nothing
    }

    #[test]
    fn runner_stops_at_the_first_failure() {
        let (lines, fr, res) = crate::on_main(move || {
            let lines: Rc<RefCell<Vec<String>>> = Rc::default();
            let fr: Rc<RefCell<Vec<Option<f64>>>> = Rc::default();
            let res: Rc<RefCell<Vec<(bool, i64)>>> = Rc::default();
            let (l, f, r) = (lines.clone(), fr.clone(), res.clone());
            let run = Runner::new(vec![v(&["sh", "-c", "echo '( 1/4) a'; echo b"]), v(&["false"]), v(&["echo", "never"])], move |t| l.borrow_mut().push(t.into()), move |x| f.borrow_mut().push(x), move |ok, i| r.borrow_mut().push((ok, i)));
            run.start();
            let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
            while res.borrow().is_empty() && std::time::Instant::now() < end {
                crate::spin(10);
            }
            let out = (lines.borrow().clone(), fr.borrow().clone(), res.borrow().clone());
            out
        });
        assert_eq!(res, [(false, 1)]);
        assert!(lines.contains(&"b".to_string()));
        assert!(fr.contains(&Some(0.25)));
        assert!(!lines.contains(&"never".to_string()));
    }
}
