//! Security & Privacy > "Require password after the display turns off"
//! (shell/idlelock.py): the menu bar process keeps swayidle running with the
//! matching timeout (ext-idle-notify; Wayfire's idle plugin only blanks the
//! display) and locks before sleep. On by default: the display off, then
//! the lock screen (macOS's "Require password immediately").
//!
//! The same swayidle turns the keyboard's backlight off when the display
//! turns off and back to its level when you come back (brightnessctl
//! saves and restores it; logind lets it write the LED without root). The
//! shell commands themselves live in sonata_shell::lockdisplay, which the
//! lock screen shares; they are the Python ones byte for byte.
//!
//! With an [`IdleWatch`] (input idle that ignores apps' inhibitors) Sonata
//! darkens and locks itself (idlepolicy), and swayidle only locks before
//! sleep.

use crate::idlepolicy::{IdlePolicy, IdleWatch};
use serde_json::{json, Value};
use sonata_shell::lockdisplay;
use std::cell::RefCell;
use std::process::{Child, Command, Stdio};
use std::rc::Rc;

pub const RESPAWN_CHECK_S: u32 = 60;
pub const NAME: &str = "security";

/// lock_after: seconds after the display turns off (0 = immediately), -1 =
/// never. On by default (Vini): locked when the display turns off and
/// before sleep, like macOS. USB protection off until turned on in Settings.
pub fn defaults() -> Value {
    json!({"lock_after": 0, "lock_before_sleep": true, "usb_protection": false})
}

/// swayidle -w waits for its command: `lock` itself only quits on unlock,
/// so every idle timeout / before-sleep that came meanwhile waited in line
/// and locked again right after each unlock (Vini: the password 3 times on
/// waking). lock-wait starts the lock on its own and returns once locked.
pub fn lock_command() -> String {
    format!("{} lock-wait", lockdisplay::quote(&crate::self_exe()))
}

pub use lockdisplay::{kbd_off, kbd_on, mark_locked, serial};

/// Present while the lock screen holds the session (its pid inside).
pub fn marker() -> std::path::PathBuf {
    lockdisplay::locked_marker()
}

/// The lock screen holds the session: its marker names a live process (a
/// lock screen that crashed left it behind).
pub fn is_locked() -> bool {
    let Ok(text) = std::fs::read_to_string(marker()) else { return false };
    let t = text.trim();
    let Ok(pid) = (if t.is_empty() { Ok(0) } else { t.parse::<i32>() }) else { return false };
    pid > 0 && unsafe { libc::kill(pid, 0) } == 0
}

/// `lock-wait`: start the lock screen detached and return once it holds
/// the session (before-sleep: the screen is locked before the machine
/// sleeps), or at once when it already does. 0 locked, 1 not.
pub fn lock_and_wait(argv: &[String], timeout: std::time::Duration) -> i32 {
    lock_and_wait_with(timeout, || {
        use std::os::unix::process::CommandExt;
        // a session of its own: not swayidle's child to wait on
        Command::new(&argv[0]).args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn().map(sonata_backend::run::reap).map_err(|_| ())
    })
}

fn lock_and_wait_with(timeout: std::time::Duration, start: impl FnOnce() -> Result<(), ()>) -> i32 {
    if is_locked() {
        return 0;
    }
    if start().is_err() {
        return 1;
    }
    let end = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < end {
        if is_locked() {
            return 0;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    1
}

/// `sonata2-rs lock-wait`: what swayidle runs (lock this session, return once locked).
pub fn run_lock_wait(_args: &[String]) -> i32 {
    lock_and_wait(&[crate::self_exe(), "lock".into()], std::time::Duration::from_secs(5))
}

/// A keyboard backlight brightnessctl can dim (asus::kbd_backlight...).
pub fn keyboard_light() -> bool {
    let leds = std::fs::read_dir("/sys/class/leds").map(|d| d.flatten().any(|e| e.file_name().to_string_lossy().ends_with("::kbd_backlight"))).unwrap_or(false);
    leds && sonata_backend::run::which("brightnessctl").is_some()
}

/// The swayidle command for these settings ([] for none). `lock`: the
/// command that locks (lock_command()).
pub fn command(cfg: &serde_json::Map<String, Value>, dpms: i64, kbd: bool, lock: &str) -> Vec<String> {
    let mut args: Vec<String> = Vec::new();
    if kbd && dpms > 0 {
        // with the display: off, then back on any input
        args.extend(["timeout".into(), dpms.to_string(), serial(&kbd_off()), "resume".into(), serial(&kbd_on())]);
    }
    let after = cfg.get("lock_after").and_then(crate::py_int).unwrap_or(-1);
    if after >= 0 {
        let base = if dpms > 0 { dpms } else { 600 }; // display never sleeps: count from 10 min idle
        args.extend(["timeout".into(), (base + after).max(5).to_string(), lock.to_string()]);
    }
    if cfg.get("lock_before_sleep").is_some_and(crate::truthy) {
        args.extend(["before-sleep".into(), lock.to_string()]);
    }
    if args.is_empty() {
        return args;
    }
    let mut cmd = vec!["swayidle".to_string(), "-w".into()];
    cmd.extend(args);
    cmd
}

/// The display dark while nobody's there (on) / back (off): the keyboard's
/// light, a black overlay with no pointer, and the panel's backlight (a
/// laptop) or every display off. The session's idle policy and the login
/// screen both use it.
pub fn darken(watch: &dyn IdleWatch, on: bool) {
    lockdisplay::lights(!on); // the keyboard's light with the displays
    lockdisplay::mark_dark(on); // (a lock that follows starts dark)
    if on {
        crate::blackout::show(); // black, no pointer (Vini: it only dimmed)
    } else {
        crate::blackout::hide();
    }
    displays_dark(watch, on);
}

/// The displays powered off on a laptop: every one but its own panel
/// (sonata-shell's, which the lock and login screens use too).
pub use sonata_shell::idlewatch::not_the_panel;

/// The panel's backlight (a laptop) or every display off, and back; the
/// session's darkening and the lock screen's alike.
pub fn displays_dark(watch: &dyn IdleWatch, on: bool) {
    displays_dark_with(watch, on, lockdisplay::has_backlight(), &lockdisplay::dim);
}

/// sonata-shell's rule (the lock and login screens darken the same way),
/// through the session's IdleWatch trait so the tests can hand in a fake.
fn displays_dark_with(watch: &dyn IdleWatch, on: bool, backlight: bool, dim: &dyn Fn(bool)) {
    sonata_shell::idlewatch::displays_dark_with(&|power, only| watch.displays(power, only), on, backlight, dim);
}

/// swayidle, ending with the menu bar: a restart stops the menu bar with
/// SIGTERM (no shutdown runs), and its swayidle stayed behind next to the
/// new one's -- two timers darkening and locking (seen in Vini's session).
pub fn spawn_tied(cmd: &[String]) -> Option<Child> {
    use std::os::unix::process::CommandExt;
    let mut c = Command::new(&cmd[0]);
    c.args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    // SAFETY: prctl is async-signal-safe, the only call between fork and exec
    unsafe {
        c.pre_exec(|| {
            libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
            Ok(())
        });
    }
    c.spawn().ok()
}

/// What IdleLock needs from parts ported elsewhere.
pub struct Hooks {
    /// The compositor never powers the display off itself
    /// (displaysleep.compositor_off: sonata-tweaks).
    pub compositor_off: Box<dyn Fn()>,
    /// The compositor's input idle (wl/idlewatch.py), when it has one.
    pub watch: Option<Rc<dyn IdleWatch>>,
}

struct State {
    proc: Option<Child>,
    cmd: Option<Vec<String>>,
}

/// Keeps swayidle (and the idle policy) in step with security.json and
/// display.json (the menu bar process).
pub struct IdleLock {
    st: RefCell<State>,
    policy: Option<Rc<IdlePolicy>>,
    _mons: RefCell<Vec<gio::FileMonitor>>,
    _check: RefCell<Option<glib::SourceId>>,
}

impl IdleLock {
    /// None on trial: the Python menu bar keeps its swayidle and darkens
    /// the displays; a second one would lock and darken twice.
    pub fn start(hooks: Hooks) -> Option<Rc<Self>> {
        (hooks.compositor_off)(); // Sonata darkens the display itself (displaysleep)
        // input idle: only media keeps it awake (idlepolicy)
        let policy = hooks.watch.map(|w| {
            let w2 = w.clone();
            IdlePolicy::new(
                w,
                Box::new(move |on| darken(&*w2, on)),
                Box::new(|| {
                    use std::os::unix::process::CommandExt;
                    // reaped when the lock screen ends: the menu bar gathered a
                    // defunct flock per idle lock
                    if let Ok(c) = Command::new("sh").args(["-c", &lock_command()]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn() {
                        sonata_backend::run::reap(c);
                    }
                }),
            )
        });
        let me = Rc::new(IdleLock { st: RefCell::new(State { proc: None, cmd: None }), policy, _mons: RefCell::default(), _check: RefCell::default() });
        if !is_locked() {
            // a lock screen that crashed: the display's timeout back
            lockdisplay::unlocked();
            lockdisplay::lights(true); // and the lights, left dark by a restart while locked
            lockdisplay::dim(false); // and the panel's backlight
        }
        let mut mons = Vec::new();
        for name in [NAME, "display"] {
            // the display's timeout changed in Settings: display.json's own watch
            let w = Rc::downgrade(&me);
            mons.extend(sonata_config::watch(name, move || {
                if let Some(me) = w.upgrade() {
                    me.apply();
                }
            }));
        }
        *me._mons.borrow_mut() = mons;
        me.apply();
        let w = Rc::downgrade(&me);
        // swayidle died (crashed, killed): started again. Only a waitpid here.
        let id = glib::timeout_add_seconds_local(RESPAWN_CHECK_S, move || {
            let Some(me) = w.upgrade() else { return glib::ControlFlow::Break };
            let dead = me.st.borrow_mut().proc.as_mut().is_some_and(|p| !matches!(p.try_wait(), Ok(None)));
            if dead {
                me.apply();
            }
            glib::ControlFlow::Continue
        });
        *me._check.borrow_mut() = Some(id);
        Some(me)
    }

    pub fn apply(&self) {
        let dpms = lockdisplay::display_seconds(); // Sonata's own: Wayfire's timeout stays off
        let cfg = sonata_config::load(NAME, &defaults());
        let has_swayidle = sonata_backend::run::which("swayidle").is_some();
        let cmd = if let Some(p) = &self.policy {
            // timeouts here; swayidle only locks before sleep
            p.apply(dpms, cfg.get("lock_after").and_then(crate::py_int).unwrap_or(-1));
            if cfg.get("lock_before_sleep").is_some_and(crate::truthy) && has_swayidle {
                vec!["swayidle".into(), "-w".into(), "before-sleep".into(), lock_command()]
            } else {
                Vec::new()
            }
        } else if has_swayidle {
            command(&cfg, dpms, keyboard_light(), &lock_command())
        } else {
            Vec::new()
        };
        {
            let mut st = self.st.borrow_mut();
            let running = st.proc.as_mut().is_some_and(|p| matches!(p.try_wait(), Ok(None)));
            if st.cmd.as_ref() == Some(&cmd) && (cmd.is_empty() || running) {
                return;
            }
        }
        self.stop();
        let mut st = self.st.borrow_mut();
        if !cmd.is_empty() {
            st.proc = spawn_tied(&cmd);
        }
        st.cmd = Some(cmd);
    }

    pub fn stop(&self) {
        if let Some(mut p) = self.st.borrow_mut().proc.take() {
            if matches!(p.try_wait(), Ok(None)) {
                unsafe { libc::kill(p.id() as i32, libc::SIGTERM) };
                let end = std::time::Instant::now() + std::time::Duration::from_secs(2);
                while matches!(p.try_wait(), Ok(None)) && std::time::Instant::now() < end {
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                let _ = p.kill();
                let _ = p.wait();
            }
        }
    }
}

impl Drop for IdleLock {
    fn drop(&mut self) {
        if let Some(id) = self._check.borrow_mut().take() {
            sonata_ui::cancel(id);
        }
        self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testenv;

    fn cfg(v: Value) -> serde_json::Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    const LOCK: &str = "sonata2-rs lock-wait";

    /// Vini's session: after a menu bar restart two swayidle ran -- the
    /// old menu bar's was left behind. It ends with whoever started it
    /// (here a thread that ends: PDEATHSIG follows the spawning thread).
    #[test]
    fn test_swayidle_left_behind_on_restart() {
        let cmd: Vec<String> = ["sleep", "30"].map(String::from).to_vec();
        let mut child = std::thread::spawn(move || spawn_tied(&cmd).expect("sleep runs")).join().unwrap();
        let mut gone = None;
        for _ in 0..50 {
            if let Ok(Some(st)) = child.try_wait() {
                gone = Some(st);
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        if gone.is_none() {
            let _ = child.kill();
        }
        assert!(gone.is_some(), "still running once its starter was gone");
    }

    #[test]
    fn keyboard_light_follows_the_display() {
        let _e = testenv::env(); // the cache folder in the commands: no other test moves it meanwhile
        let cmd = command(&cfg(json!({"lock_after": -1})), 300, true, LOCK);
        assert_eq!(cmd, ["swayidle", "-w", "timeout", "300", &serial(&kbd_off()), "resume", &serial(&kbd_on())]);
        assert!(kbd_off().contains("set 0")); // level saved before turning off
        assert!(kbd_on().contains("$(cat ")); // and restored on resume
    }

    #[test]
    fn no_keyboard_light() {
        assert!(command(&cfg(json!({"lock_after": -1})), 300, false, LOCK).is_empty());
        assert!(command(&cfg(json!({"lock_after": -1})), 0, true, LOCK).is_empty()); // display never sleeps
    }

    #[test]
    fn lock_kept() {
        let _e = testenv::env(); // the cache folder in the commands: no other test moves it meanwhile
        let cmd = command(&cfg(json!({"lock_after": 5, "lock_before_sleep": true})), 300, true, LOCK);
        assert_eq!(cmd[cmd.len() - 5..], ["timeout", "305", LOCK, "before-sleep", LOCK]);
        assert!(cmd.contains(&serial(&kbd_off())));
    }

    /// Vini: the session never locked on its own -- automatic locking was
    /// off by default. Now on: when the display turns off, and before sleep.
    #[test]
    fn test_lock_by_default() {
        let d = cfg(defaults());
        assert_eq!(d["lock_after"], 0);
        assert_eq!(d["lock_before_sleep"], true);
        assert_eq!(d["usb_protection"], false);
        let cmd = command(&d, 600, false, LOCK);
        assert!(cmd.contains(&"before-sleep".to_string()));
        let i = cmd.iter().position(|a| a == LOCK).unwrap();
        assert_eq!(cmd[i - 1], "600");
    }

    #[test]
    fn swayidle_runs_the_returning_command() {
        assert!(lock_command().ends_with(" lock-wait"));
    }

    /// Vini: on waking, the password was asked 3 times -- swayidle -w waited
    /// for `lock` until unlock, so the idle timeout / before-sleep lock
    /// requests that came meanwhile each locked again after it.
    #[test]
    fn test_lock_wait_returns_once_locked() {
        let _e = testenv::env();
        assert!(!is_locked());
        mark_locked(true);
        assert!(is_locked());
        mark_locked(false);
        assert!(!is_locked());
        mark_locked(false); // twice: no error
        // a stale marker from a crashed lock
        std::fs::write(marker(), "999999999").unwrap();
        assert!(!is_locked());
        std::fs::remove_file(marker()).unwrap();
        // returns once the lock screen engaged
        let t = std::time::Duration::from_secs(2);
        assert_eq!(lock_and_wait_with(t, || { mark_locked(true); Ok(()) }), 0);
        // already locked: starts nothing
        assert_eq!(lock_and_wait_with(t, || panic!("started again")), 0);
        mark_locked(false);
        // never locks: failure
        assert_eq!(lock_and_wait_with(std::time::Duration::from_millis(200), || Ok(())), 1);
        assert_eq!(lock_and_wait_with(t, || Err(())), 1);
    }

    /// A fake brightnessctl: the laptop's keyboard, a USB one and a Caps
    /// Lock light (not a backlight), levels in files.
    fn fake_lights(d: &std::path::Path) -> String {
        for (n, v) in [("asus::kbd_backlight", "3"), ("usb-0003::kbd_backlight", "2"), ("input3::capslock", "1")] {
            std::fs::write(d.join(format!("lvl-{n}")), v).unwrap();
        }
        let dd = d.display();
        let fake = format!(
            "#!/bin/sh\ndev=\"\"; prev=\"\"; last=\"\"; list=\"\"\n\
             for a; do [ \"$prev\" = -d ] && dev=\"$a\"; [ \"$a\" = -l ] && list=1; prev=\"$a\"; last2=\"$last\"; last=\"$a\"; done\n\
             if [ -n \"$list\" ]; then for f in {dd}/lvl-*; do n=${{f##*/lvl-}}; echo \"$n,leds,$(cat \"$f\"),100%,3\"; done; exit 0; fi\n\
             case \"$dev\" in \"*::kbd_backlight\") dev=asus::kbd_backlight;; esac\n\
             if [ \"$last\" = g ]; then cat \"{dd}/lvl-$dev\"; elif [ \"$last2\" = set ]; then echo \"$last\" > \"{dd}/lvl-$dev\"; fi\n"
        );
        let path = d.join("brightnessctl");
        std::fs::write(&path, fake).unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        format!("{}:{}", d.display(), std::env::var("PATH").unwrap_or_default())
    }

    fn sh(cmd: &str, path: &str) {
        Command::new("sh").args(["-c", cmd]).env("PATH", path).status().unwrap();
    }

    fn level(d: &std::path::Path, n: &str) -> String {
        std::fs::read_to_string(d.join(format!("lvl-{n}"))).unwrap().trim().to_string()
    }

    /// Vini: the laptop's keyboard stayed unlit. The idle timeout and the
    /// lock screen both turned it off; the second "off" saved the dark level
    /// over the real one. And USB keyboards stayed lit, only the laptop's
    /// went dark.
    #[test]
    fn test_two_offs_then_on_gives_every_keyboard_light_back() {
        let e = testenv::env();
        let d = e.dir.path().join("cache"); // XDG_CACHE_HOME: the saved levels too
        let path = fake_lights(&d);
        sh(&kbd_off(), &path);
        assert_eq!((level(&d, "asus::kbd_backlight"), level(&d, "usb-0003::kbd_backlight")), ("0".into(), "0".into()));
        assert_eq!(level(&d, "input3::capslock"), "1"); // not a backlight: left alone
        sh(&kbd_off(), &path); // the lock screen too
        sh(&kbd_on(), &path);
        assert_eq!((level(&d, "asus::kbd_backlight"), level(&d, "usb-0003::kbd_backlight")), ("3".into(), "2".into()));
        // a keyboard you turned off stays off
        std::fs::write(d.join("lvl-asus::kbd_backlight"), "0").unwrap();
        sh(&kbd_off(), &path);
        sh(&kbd_on(), &path);
        assert_eq!(level(&d, "asus::kbd_backlight"), "0");
        // a level saved by an older Sonata comes back
        std::fs::create_dir_all(d.join("sonata2")).unwrap();
        std::fs::write(d.join("sonata2").join("lights-before-dark"), "3").unwrap();
        sh(&kbd_on(), &path);
        assert_eq!(level(&d, "asus::kbd_backlight"), "3");
    }

    /// tests/test_idlelock.py: a restart while dark leaves the keyboard at
    /// 0; the next session's start (IdleLock) gives its light back, once.
    #[test]
    fn restart_while_dark_restores_at_the_next_session() {
        let e = testenv::env();
        let d = e.dir.path().join("cache");
        let path = fake_lights(&d);
        sh(&kbd_off(), &path);
        std::fs::write(d.join("lvl-asus::kbd_backlight"), "0").unwrap(); // the restart: still 0
        sh(&kbd_on(), &path); // the next session's start
        assert_eq!(level(&d, "asus::kbd_backlight"), "3");
        sh(&kbd_on(), &path); // nothing saved any more: left as it is
        assert_eq!(level(&d, "asus::kbd_backlight"), "3");
    }

    #[derive(Default)]
    struct Watch(RefCell<Vec<(bool, Vec<bool>)>>);

    impl IdleWatch for Watch {
        fn watch(&self, _s: u32, _i: Rc<dyn Fn()>, _b: Rc<dyn Fn()>) -> u32 {
            1
        }
        fn unwatch(&self, _id: u32) {}
        fn displays(&self, on: bool, only: Option<&dyn Fn(&str) -> bool>) -> bool {
            let picks = only.map(|f| ["eDP-1", "HDMI-A-1", "DP-2", ""].iter().map(|n| f(n)).collect()).unwrap_or_default();
            self.0.borrow_mut().push((on, picks));
            true
        }
    }

    /// Vini: after the idle timeout the laptop's screen came back only by
    /// closing and opening the lid (the NVIDIA driver refused to turn it on
    /// again); plugged-in monitors only went black, the pointer on them.
    /// The laptop's own panel is never powered off (only its backlight);
    /// every other display is.
    #[test]
    fn test_laptop_dims_its_panel_and_turns_monitors_off() {
        let w = Watch::default();
        let dims = RefCell::new(Vec::new());
        let dim = |on: bool| dims.borrow_mut().push(on);
        displays_dark_with(&w, true, true, &dim);
        displays_dark_with(&w, false, true, &dim);
        assert_eq!(*dims.borrow(), [true, false]);
        let calls = w.0.borrow();
        assert_eq!(calls.iter().map(|c| c.0).collect::<Vec<_>>(), [false, true]);
        // eDP-1: never; HDMI and DP: yes; unknown: left alone
        assert_eq!(calls[0].1, [false, true, true, false]);
    }

    #[test]
    fn desktop_still_powers_off() {
        let w = Watch::default();
        let dims = RefCell::new(Vec::new());
        let dim = |on: bool| dims.borrow_mut().push(on);
        displays_dark_with(&w, true, false, &dim);
        displays_dark_with(&w, false, false, &dim);
        assert!(dims.borrow().is_empty());
        assert_eq!(w.0.borrow().iter().map(|c| (c.0, c.1.is_empty())).collect::<Vec<_>>(), [(false, true), (true, true)]);
    }
}
