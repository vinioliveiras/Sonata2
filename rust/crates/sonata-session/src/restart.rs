//! `restart [dock topbar launchpad wallpaper spotlight]` (sonata2/__main__.py's
//! restart): stop those shell components (all by default) and start them
//! again with the current code -- to see edits live in a running session.
//!
//! Smooth (Vini): the old Dock and menu bar slide away (SIGUSR1, intro.py),
//! the new ones start out of sight and slide in once ready, and the new
//! wallpaper comes up under the old one before it goes -- no black flash.
//!
//! It finds the components and their supervisors in either shape
//! (procs.rs): the Python Sonata's `python3 -m sonata2 keep dock` and this
//! build's `sonata2-rs keep-all ...`, and starts them again under this
//! build's `keep-all`, which runs each one the way keep.rs decides (the
//! ported ones here, the rest through `sonata2`).

use crate::procs;
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};

pub const SHELL_COMPONENTS: [&str; 5] = ["wallpaper", "dock", "topbar", "launchpad", "spotlight"];

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

/// intro.py's RESTART_MARK (sonata-shell's intro.rs has the same): while it
/// exists the new Dock and menu bar wait out of sight.
pub fn restart_mark(runtime: &Path) -> PathBuf {
    runtime.join("sonata2-restarting")
}

/// intro.ready_file: a new Dock or menu bar is ready to slide in.
pub fn ready_file(runtime: &Path, name: &str) -> PathBuf {
    runtime.join(format!("sonata2-restarting.{name}"))
}

/// What a restart does to the world -- the real one, or a test's.
pub trait Host {
    fn procs(&mut self) -> Vec<(u32, Vec<String>)>;
    fn signal(&mut self, pid: u32, sig: i32);
    fn sleep(&mut self, secs: f64);
    fn spawn(&mut self, argv: &[String]);
    fn alive(&mut self, pid: u32) -> bool;
    fn exists(&mut self, path: &Path) -> bool;
    fn reload_wayfire_config(&mut self);
}

struct Real;

impl Host for Real {
    fn procs(&mut self) -> Vec<(u32, Vec<String>)> {
        procs::running()
    }
    fn signal(&mut self, pid: u32, sig: i32) {
        unsafe {
            libc::kill(pid as libc::pid_t, sig);
        }
    }
    fn sleep(&mut self, secs: f64) {
        std::thread::sleep(std::time::Duration::from_secs_f64(secs));
    }
    fn spawn(&mut self, argv: &[String]) {
        use std::os::unix::process::CommandExt;
        let mut c = std::process::Command::new(&argv[0]);
        c.args(&argv[1..]).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        // its own session: it outlives this command and the terminal it ran in
        unsafe {
            c.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        if let Ok(c) = c.spawn() {
            sonata_backend::run::reap(c); // (Restart Sonata from the menu bar: no zombie left in it)
        }
    }
    fn alive(&mut self, pid: u32) -> bool {
        Path::new(&format!("/proc/{pid}")).exists()
    }
    fn exists(&mut self, path: &Path) -> bool {
        path.exists()
    }
    fn reload_wayfire_config(&mut self) {
        let Some(home) = crate::home::sonata_home() else { return };
        let cfg_home = sonata_config::config_home().join("sonata2");
        reload_wayfire_config(&runtime_dir(), &cfg_home, &home, &|cmd| {
            std::process::Command::new(&cmd[0]).args(&cmd[1..]).status().ok().and_then(|s| s.code()).unwrap_or(1)
        });
    }
}

/// The first `plugins = ...` line.
fn plugins_line(text: &str) -> Option<&str> {
    text.lines().find(|l| l.strip_prefix("plugins").is_some_and(|r| r.trim_start().starts_with('=')))
}

/// The session's Wayfire config again from the current defaults (+ your
/// Settings changes): Wayfire watches that file and applies it at once, so
/// blur, animation, key or plugin changes need no new login.
pub fn reload_wayfire_config(run_dir: &Path, cfg_home: &Path, home: &Path, run: &dyn Fn(&[String]) -> i32) {
    let run_cfg = run_dir.join("sonata2-wayfire.ini");
    let script = home.join("tools").join("wayfire-config.sh");
    if !(run_cfg.exists() && script.exists()) {
        return; // not a Sonata session (dev session, preview)
    }
    let (user, mark) = (cfg_home.join("wayfire.ini"), cfg_home.join(".wayfire.ini.installed"));
    let mut src = home.join("config").join("wayfire.ini");
    if user.exists() {
        let same = match (std::fs::read(&user), std::fs::read(&mark)) {
            (Ok(a), Ok(b)) => a == b,
            _ => false,
        };
        if !same {
            src = user; // a copy you edited
        }
    }
    let tmp = run_dir.join("sonata2-wayfire.ini.new");
    let cmd = ["bash".to_string(), script.display().to_string(), src.display().to_string(), tmp.display().to_string()];
    if run(&cmd) != 0 {
        return;
    }
    let Ok(text) = std::fs::read_to_string(&tmp) else { return };
    // the plugin list stays as the session started: Wayfire only reads a
    // new plugin's XML at start, and loading one without it can abort it
    let old = std::fs::read_to_string(&run_cfg).unwrap_or_default();
    let text = match (plugins_line(&old), plugins_line(&text)) {
        (Some(old), Some(_)) => {
            let mut done = false;
            text.split_inclusive('\n')
                .map(|l| {
                    let body = l.strip_suffix('\n').unwrap_or(l);
                    if !done && plugins_line(body) == Some(body) {
                        done = true;
                        format!("{old}{}", &l[body.len()..])
                    } else {
                        l.to_string()
                    }
                })
                .collect()
        }
        _ => text,
    };
    // rewritten in place: Wayfire keeps watching it
    let _ = std::fs::write(&run_cfg, text);
    let _ = std::fs::remove_file(&tmp);
}

/// `restart [names]`.
pub fn restart(args: &[String]) -> i32 {
    let me = std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into());
    restart_with(&mut Real, args, &runtime_dir(), &me)
}

pub fn restart_with(host: &mut dyn Host, args: &[String], runtime: &Path, me: &str) -> i32 {
    if args.is_empty() {
        host.reload_wayfire_config();
    }
    let procs = host.procs();
    if args.is_empty() {
        // the Open/Save panels' process (portal) runs the whole session: it kept
        // old code (looks) after an update. Stopped; D-Bus starts it again, new.
        for pid in procs::pids(&procs, |a| procs::is_component(a, "portal")) {
            host.signal(pid, libc::SIGTERM);
        }
    }
    let mut names: Vec<&str> = args.iter().map(String::as_str).filter(|n| SHELL_COMPONENTS.contains(n)).collect();
    if names.is_empty() {
        names = SHELL_COMPONENTS.to_vec();
    }
    // one restart at a time (a new wallpaper chosen twice quickly: two at once
    // left two wallpapers, or none) -- the Python restart's lock too
    let lock = std::fs::OpenOptions::new().create(true).write(true).truncate(false).open(runtime.join("sonata2-restart.lock"));
    if let Ok(l) = &lock {
        unsafe {
            libc::flock(l.as_raw_fd(), libc::LOCK_EX);
        }
    }
    let children = |n: &str| procs::pids(&procs, |a| procs::is_component(a, n));
    for n in &names {
        // the keepers first, so they don't start it again
        for pid in procs::pids(&procs, |a| procs::is_keeper(a, n)) {
            host.signal(pid, libc::SIGTERM);
        }
    }
    // a `keep-all` supervising only components being restarted goes too; one
    // that also keeps others (polkit, ...) stays: their children end with
    // SIGTERM below, which it takes as "stopped on purpose" (keep's rules)
    for pid in procs::pids(&procs, |a| procs::is_keep_all(a) && procs::keep_all_names(a).iter().all(|k| names.contains(&k.as_str()))) {
        host.signal(pid, libc::SIGTERM);
    }
    let visible: Vec<u32> = ["dock", "topbar"].iter().filter(|n| names.contains(n)).flat_map(|n| children(n)).collect();
    let mark = restart_mark(runtime);
    if !visible.is_empty() {
        for pid in &visible {
            host.signal(*pid, libc::SIGUSR1); // they slide away
        }
        host.sleep(0.35);
        let _ = std::fs::write(&mark, "restarting\n"); // the new ones slide in
    }
    for n in &names {
        if *n != "wallpaper" {
            for pid in children(n) {
                host.signal(pid, libc::SIGTERM);
            }
        }
    }
    host.sleep(0.3);
    // one supervisor for them all (keep-all), as the session starts them
    let specs: Vec<String> = names.iter().map(|n| format!("{n}{}", if ["launchpad", "spotlight"].contains(n) { ",--background" } else { "" })).collect();
    let mut argv = vec![me.to_string()];
    if specs.len() > 1 {
        argv.push("keep-all".into());
        argv.extend(specs);
    } else {
        argv.push("keep".into());
        argv.extend(specs[0].split(',').map(String::from));
    }
    host.spawn(&argv);
    for n in &names {
        println!("restarted {n}");
    }
    let walls = children("wallpaper");
    if names.contains(&"wallpaper") && !walls.is_empty() {
        host.sleep(1.5); // the new wallpaper is up under the old one
        for pid in &walls {
            host.signal(*pid, libc::SIGTERM);
        }
        host.sleep(1.0); // one stuck (GStreamer) can't take SIGTERM in time: gone anyway
        for pid in &walls {
            if host.alive(*pid) {
                host.signal(*pid, libc::SIGKILL);
            }
        }
    }
    if !visible.is_empty() {
        // the Dock and the menu bar slide in together: once both are ready
        let want: Vec<PathBuf> = ["dock", "topbar"].iter().filter(|n| names.contains(n)).map(|n| ready_file(runtime, n)).collect();
        for _ in 0..50 {
            if want.iter().all(|p| host.exists(p)) {
                break;
            }
            host.sleep(0.1);
        }
        for p in std::iter::once(&mark).chain(&want) {
            let _ = std::fs::remove_file(p);
        }
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Debug, PartialEq, Clone)]
    enum Ev {
        Kill(u32, i32),
        Sleep,
        Start(String, bool),
    }

    struct Fake {
        procs: Vec<(u32, Vec<String>)>,
        events: Vec<Ev>,
        mark: PathBuf,
        spawned: Vec<Vec<String>>,
        ready_after: usize,
        asked: usize,
        reloaded: bool,
    }

    impl Host for Fake {
        fn procs(&mut self) -> Vec<(u32, Vec<String>)> {
            self.procs.clone()
        }
        fn signal(&mut self, pid: u32, sig: i32) {
            self.events.push(Ev::Kill(pid, sig));
        }
        fn sleep(&mut self, _secs: f64) {
            self.events.push(Ev::Sleep);
        }
        fn spawn(&mut self, argv: &[String]) {
            let marked = self.mark.exists();
            let i = argv.iter().position(|a| a == "keep-all" || a == "keep").unwrap();
            let specs: Vec<String> = if argv[i] == "keep" { vec![argv[i + 1].clone()] } else { argv[i + 1..].to_vec() };
            for s in specs {
                self.events.push(Ev::Start(s.split(',').next().unwrap().to_string(), marked));
            }
            self.spawned.push(argv.to_vec());
        }
        fn alive(&mut self, _pid: u32) -> bool {
            true
        }
        fn exists(&mut self, _path: &Path) -> bool {
            self.asked += 1;
            self.asked > self.ready_after
        }
        fn reload_wayfire_config(&mut self) {
            self.reloaded = true;
        }
    }

    fn v(s: &str) -> Vec<String> {
        s.split(' ').map(String::from).collect()
    }

    fn fake(dir: &Path) -> Fake {
        let procs = vec![
            (11, v("python3 -m sonata2 keep dock")),
            (12, v("python3 -m sonata2 keep topbar")),
            (13, v("/usr/bin/sonata2-rs keep wallpaper")),
            (14, v("python3 -m sonata2 keep-all dock topbar")),
            (15, v("sonata2-rs keep-all dock polkit")),
            (21, v("python3 -m sonata2 dock")),
            (22, v("python3 -m sonata2 topbar")),
            (23, v("python3 -m sonata2 wallpaper")),
            (31, v("python3 -m sonata2 portal")),
            (41, v("sonata2-rs polkit")),
        ];
        Fake { procs, events: Vec::new(), mark: restart_mark(dir), spawned: Vec::new(), ready_after: 0, asked: 0, reloaded: false }
    }

    /// tests/test_restart_smooth.py's test_choreography: the old Dock and
    /// menu bar slide away first and get time to; the new ones start out of
    /// sight; the old wallpaper goes after the new one is up; the marker is
    /// gone at the end.
    #[test]
    fn test_choreography() {
        let d = tempfile::tempdir().unwrap();
        let mut f = fake(d.path());
        restart_with(&mut f, &v("wallpaper dock topbar"), d.path(), "/x/sonata2-rs");
        let ev = &f.events;
        let at = |e: Ev| ev.iter().position(|x| *x == e).unwrap_or_else(|| panic!("{e:?} missing in {ev:?}"));
        let slide = at(Ev::Kill(21, libc::SIGUSR1));
        at(Ev::Kill(22, libc::SIGUSR1));
        let term = at(Ev::Kill(21, libc::SIGTERM));
        assert!(ev[slide..term].contains(&Ev::Sleep));
        let starts: Vec<&Ev> = ev.iter().filter(|e| matches!(e, Ev::Start(..))).collect();
        assert_eq!(starts.len(), 3);
        assert!(starts.iter().all(|e| matches!(e, Ev::Start(_, true))));
        let wall_start = at(Ev::Start("wallpaper".into(), true));
        assert!(at(Ev::Kill(23, libc::SIGTERM)) > wall_start);
        assert!(!f.mark.exists());
        // the keepers first; a keep-all of only these goes, one that keeps polkit stays
        assert!(at(Ev::Kill(11, libc::SIGTERM)) < slide && at(Ev::Kill(13, libc::SIGTERM)) < slide);
        at(Ev::Kill(14, libc::SIGTERM));
        assert!(!ev.iter().any(|e| matches!(e, Ev::Kill(15, _) | Ev::Kill(41, _) | Ev::Kill(31, _))));
        // under this build's own supervisor, as the session starts them
        assert_eq!(f.spawned, [v("/x/sonata2-rs keep-all wallpaper dock topbar")]);
        assert!(!f.reloaded); // only a full restart reloads Wayfire's config
    }

    /// test_restart_lets_them_in_once_both_are_ready: the marker is removed
    /// once the new Dock and menu bar said they're ready.
    #[test]
    fn test_restart_lets_them_in_once_both_are_ready() {
        let d = tempfile::tempdir().unwrap();
        let mut f = fake(d.path());
        f.ready_after = 3;
        restart_with(&mut f, &v("dock topbar"), d.path(), "sonata2-rs");
        assert!(f.asked > 3);
        assert!(!f.mark.exists());
    }

    #[test]
    fn everything_by_default_one_by_keep() {
        let d = tempfile::tempdir().unwrap();
        let mut f = fake(d.path());
        restart_with(&mut f, &[], d.path(), "me");
        assert!(f.reloaded);
        assert!(f.events.contains(&Ev::Kill(31, libc::SIGTERM))); // the portal, started again by D-Bus
        assert_eq!(f.spawned, [v("me keep-all wallpaper dock topbar launchpad,--background spotlight,--background")]);
        let mut f = fake(d.path());
        restart_with(&mut f, &v("spotlight bogus"), d.path(), "me");
        assert_eq!(f.spawned, [v("me keep spotlight --background")]);
        assert!(!f.events.contains(&Ev::Kill(31, libc::SIGTERM)));
    }

    /// tests/test_review_session.py's test_wayfire_reload_keeps_the_running_plugin_list:
    /// `restart` rewrites the live Wayfire config in place but keeps the
    /// plugins line the session started with (a new plugin can abort Wayfire).
    #[test]
    fn test_wayfire_reload_keeps_the_running_plugin_list() {
        let (run_dir, cfg_home, home) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let run_cfg = run_dir.path().join("sonata2-wayfire.ini");
        std::fs::write(&run_cfg, "[core]\nplugins = a b\nvalue = 1\n").unwrap();
        std::fs::create_dir(home.path().join("tools")).unwrap();
        std::fs::write(home.path().join("tools/wayfire-config.sh"), "").unwrap();
        let fake_run = |cmd: &[String]| {
            std::fs::write(&cmd[3], "[core]\nplugins = a b newplugin\nvalue = 2\n").unwrap();
            0
        };
        reload_wayfire_config(run_dir.path(), cfg_home.path(), home.path(), &fake_run);
        assert_eq!(std::fs::read_to_string(&run_cfg).unwrap(), "[core]\nplugins = a b\nvalue = 2\n");
        assert!(!run_dir.path().join("sonata2-wayfire.ini.new").exists());
    }
}
