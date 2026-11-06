//! Graphics memory watch (NVIDIA; sonata2/vram.py): when the card's memory
//! is almost full, Sonata logs who is using it (~/.cache/sonata2/gpu-memory.log)
//! and warns once -- a full card took the whole session down (Vini:
//! WhatsApp's web app loading a video; Wayfire aborts when it can't get a
//! buffer).
//!
//! Only while the card is already awake (NvidiaUsage::awake): asking a
//! sleeping laptop GPU would wake it. The nvidia-smi calls run in a thread.
//! Every look also tells the listeners the card's (used, total): the
//! menu bar feeds it to [`crate::fullscreenfirst::FullscreenFirst::memory`].

use regex::Regex;
use sonata_i18n::{fmt, tr};
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};

pub const EVERY_S: u32 = 15;
/// warn at 85 % ...
pub const WARN: f64 = 0.85;
/// ... and again only after it went back under 70 %
pub const REARM: f64 = 0.70;
pub const LOG: &str = "gpu-memory.log";
pub const USAGE_ARGS: [&str; 2] = ["--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"];

/// (pid, name, MiB)
pub type Proc = (i64, String, u64);

fn proc_line() -> &'static Regex {
    // |    0   N/A  N/A    12345      G   /usr/bin/spider        1650MiB |
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| Regex::new(r"^\|\s+\d+\s+\S+\s+\S+\s+(\d+)\s+[A-Z+]+\s+(.+?)\s+(\d+)\s*MiB\s+\|").unwrap())
}

/// (used MiB, total MiB) from `--query-gpu=memory.used,memory.total
/// --format=csv,noheader,nounits` (the first card), None when unreadable.
pub fn parse_usage(text: &str) -> Option<(u64, u64)> {
    text.lines().find_map(|line| {
        let parts: Vec<&str> = line.split(',').map(str::trim).collect();
        let digits = |p: &str| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit());
        if parts.len() == 2 && parts.iter().all(|p| digits(p)) {
            let (u, t) = (parts[0].parse().ok()?, parts[1].parse().ok()?);
            return (t > 0).then_some((u, t));
        }
        None
    })
}

/// [(pid, name, MiB)] from plain `nvidia-smi`'s process table, biggest first.
pub fn parse_processes(text: &str) -> Vec<Proc> {
    let mut out: Vec<Proc> = text
        .lines()
        .filter_map(|line| {
            let m = proc_line().captures(line)?;
            let path = m[2].split_whitespace().next().unwrap_or("");
            let name = path.rsplit('/').next().unwrap_or(path).to_string();
            Some((m[1].parse().ok()?, name, m[3].parse().ok()?))
        })
        .collect();
    out.sort_by_key(|p| std::cmp::Reverse(p.2)); // stable, like sorted(): ties keep the table's order
    out
}

/// NV_QUERY rows -> "used, total" rows (what parse_usage reads).
pub fn usage_rows(query_out: &str) -> String {
    query_out
        .lines()
        .filter_map(|line| {
            let parts: Vec<&str> = line.split(',').map(str::trim).collect();
            (parts.len() >= 3).then(|| format!("{}, {}", parts[1], parts[2]))
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// A readable name: the process' own name (comm) when the table cut it.
pub fn app_name(pid: i64, fallback: &str) -> String {
    std::fs::read_to_string(format!("/proc/{pid}/comm")).map(|s| s.trim().to_string()).ok().filter(|s| !s.is_empty()).unwrap_or_else(|| fallback.to_string())
}

pub fn log_path() -> PathBuf {
    sonata_session::logs::log_dir().join(LOG)
}

/// The warning's title and text, naming the biggest user.
pub fn warning(procs: &[Proc]) -> (String, String) {
    let mut body = tr!("Closing apps you don't need keeps Sonata from stopping.");
    if let Some((_, name, mib)) = procs.first() {
        let mut size = format!("{:.1}", *mib as f64 / 1024.0);
        if matches!(sonata_i18n::language().as_str(), "pt_BR" | "pt_PT") {
            size = size.replace('.', ",");
        }
        body = fmt(&tr!("{name} is using {size} GB."), &[("name", name), ("size", &size)]) + " " + &body;
    }
    (tr!("Graphics memory almost full"), body)
}

/// What one look found.
#[derive(Clone, Debug, PartialEq)]
pub struct Reading {
    pub used: u64,
    pub total: u64,
    /// nearly full: who uses the card, biggest first
    pub procs: Option<Vec<Proc>>,
    /// (title, text) to tell the user, once until it went back down
    pub warn: Option<(String, String)>,
}

/// run(args): nvidia-smi's output for these arguments.
pub type Run = Arc<dyn Fn(&[&str]) -> String + Send + Sync>;
/// names(pid, name from the table): the name shown (tests: as it is).
pub type Names = Arc<dyn Fn(i64, &str) -> String + Send + Sync>;
/// notify(title, text): false when no notification server answered.
pub type Notify = Rc<dyn Fn(&str, &str) -> bool>;

/// The part that runs off the main loop.
struct Looker {
    run: Run,
    names: Names,
    warned: AtomicBool,
}

impl Looker {
    fn look(&self) -> Option<Reading> {
        let (used, total) = parse_usage(&(self.run)(&USAGE_ARGS))?;
        if (used as f64) < total as f64 * REARM {
            self.warned.store(false, Ordering::SeqCst);
        }
        if (used as f64) < total as f64 * WARN {
            return Some(Reading { used, total, procs: None, warn: None });
        }
        let procs: Vec<Proc> = parse_processes(&(self.run)(&[])).into_iter().map(|(pid, name, mib)| (pid, (self.names)(pid, &name), mib)).collect();
        log(used, total, &procs);
        let warn = (!self.warned.swap(true, Ordering::SeqCst)).then(|| warning(&procs));
        Some(Reading { used, total, procs: Some(procs), warn })
    }
}

fn log(used: u64, total: u64, procs: &[Proc]) {
    use std::io::Write;
    let path = log_path();
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let when = glib::DateTime::now_local().and_then(|t| t.format("%Y-%m-%d %H:%M:%S")).map(|s| s.to_string()).unwrap_or_default();
    let who: Vec<String> = procs.iter().take(8).map(|(pid, n, m)| format!("{n} ({pid}) {m} MiB")).collect();
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{when} {used}/{total} MiB: {}", who.join(", "));
    }
    sonata_session::logs::trim(&path, None);
}

type MemoryListener = Rc<dyn Fn(u64, u64)>;

/// A full NVIDIA card: who, and a warning.
pub struct VramWatch {
    looker: Arc<Looker>,
    notify: Notify,
    awake: Box<dyn Fn() -> bool>,
    busy: Rc<Cell<bool>>,
    listeners: RefCell<Vec<MemoryListener>>,
    timer: RefCell<Option<glib::SourceId>>,
}

impl VramWatch {
    /// The menu bar's, made once and looking every EVERY_S seconds; None
    /// without an NVIDIA card (or nvidia-smi).
    pub fn start(notify: Notify) -> Option<Rc<Self>> {
        let usage = sonata_backend::procfs::NvidiaUsage::new(std::path::Path::new("/sys"));
        let tool = usage.tool.clone()?;
        if usage.devices.is_empty() {
            return None;
        }
        let run: Run = Arc::new(move |args: &[&str]| {
            if args == USAGE_ARGS {
                // the shared reading (the menu bar's figures may have just made it)
                return usage_rows(&sonata_backend::stats::nvidia_read(&tool));
            }
            crate::output_within(&std::iter::once(tool.to_str().unwrap_or("nvidia-smi")).chain(args.iter().copied()).collect::<Vec<_>>(), 5000).unwrap_or_default()
        });
        let me = Rc::new(Self::with(run, notify, Box::new(move || usage.awake()), Arc::new(|pid, n: &str| app_name(pid, n))));
        let w = Rc::downgrade(&me);
        let id = glib::timeout_add_seconds_local(EVERY_S, move || match w.upgrade() {
            Some(me) => {
                me.tick();
                glib::ControlFlow::Continue
            }
            None => glib::ControlFlow::Break,
        });
        *me.timer.borrow_mut() = Some(id);
        Some(me)
    }

    /// With this nvidia-smi, notifier and card (tests hand in fakes); no timer.
    pub fn with(run: Run, notify: Notify, awake: Box<dyn Fn() -> bool>, names: Names) -> Self {
        VramWatch { looker: Arc::new(Looker { run, names, warned: AtomicBool::new(false) }), notify, awake, busy: Rc::default(), listeners: RefCell::default(), timer: RefCell::default() }
    }

    /// `callback(used MiB, total MiB)` on the main loop, every look.
    pub fn connect(&self, callback: impl Fn(u64, u64) + 'static) {
        self.listeners.borrow_mut().push(Rc::new(callback));
    }

    /// One look in a thread, unless one runs already, the card sleeps or
    /// the screen is locked. (Paused only while locked: in a fullscreen
    /// game this reading is what turns blur and animations off when the
    /// game fills the card -- gamemode::LightEffects.)
    pub fn tick(self: &Rc<Self>) -> bool {
        if self.busy.get() || crate::idlelock::is_locked() || !(self.awake)() {
            return false;
        }
        self.busy.set(true);
        let looker = self.looker.clone();
        let handle = gio::spawn_blocking(move || looker.look());
        let w = Rc::downgrade(self);
        glib::MainContext::default().spawn_local(async move {
            let got = handle.await.ok().flatten();
            if let Some(me) = w.upgrade() {
                me.busy.set(false);
                if let Some(r) = got {
                    me.deliver(&r);
                }
            }
        });
        true
    }

    /// One look, here and now (tests; tick() runs it in a thread).
    pub fn check(&self) -> Option<Reading> {
        let r = self.looker.look()?;
        self.deliver(&r);
        Some(r)
    }

    fn deliver(&self, r: &Reading) {
        let ls: Vec<_> = self.listeners.borrow().clone();
        for f in ls {
            f(r.used, r.total);
        }
        if let Some((title, body)) = &r.warn {
            (self.notify)(title, body);
        }
    }
}

impl Drop for VramWatch {
    fn drop(&mut self) {
        if let Some(id) = self.timer.borrow_mut().take() {
            sonata_ui::cancel(id);
        }
    }
}

/// The GPU notices at login (the menu bar's `_gpu_notice`): Sonata started
/// on the other card (the last resort), or crashed on the displays' card
/// last time. Tried every 4 s, up to 6 times, until the notification
/// server answers.
pub fn start_gpu_notices() {
    let tries = Cell::new(0);
    glib::timeout_add_seconds_local(4, move || {
        use sonata_tweaks::gpu;
        tries.set(tries.get() + 1);
        let told = gpu::fallback_notice();
        let again = !told || (!gpu::crash_notice() && gpu::crashed_at().is_some());
        if again && tries.get() < 6 { glib::ControlFlow::Continue } else { glib::ControlFlow::Break }
    });
}

#[cfg(test)]
mod tests {
    //! tests/test_vram.py. Vini: WhatsApp's web app filled the NVIDIA
    //! card's memory and the session went down with no word.
    use super::*;
    use std::sync::Mutex;

    const TABLE: &str = "\
+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|=========================================================================================|
|    0   N/A  N/A            2150      G   wayfire                                 310MiB |
|    0   N/A  N/A           77879      G   /usr/bin/spider                        6900MiB |
|    0   N/A  N/A           68907    C+G   ...opt/google/chrome/chrome             420MiB |
+-----------------------------------------------------------------------------------------+
";

    type Sent = Rc<RefCell<Vec<(String, String)>>>;

    fn watch(used: Arc<Mutex<u64>>, awake: bool) -> (VramWatch, Sent) {
        let sent: Sent = Rc::default();
        let s = sent.clone();
        let run: Run = Arc::new(move |args: &[&str]| if args.is_empty() { TABLE.into() } else { format!("{}, 8188\n", used.lock().unwrap()) });
        let w = VramWatch::with(run, Rc::new(move |t: &str, b: &str| {
            s.borrow_mut().push((t.into(), b.into()));
            true
        }), Box::new(move || awake), Arc::new(|_, n: &str| n.to_string()));
        (w, sent)
    }

    #[test]
    fn parsing() {
        assert_eq!(parse_usage("7100, 8188\n"), Some((7100, 8188)));
        assert_eq!(parse_usage("[N/A], [N/A]"), None);
        let procs = parse_processes(TABLE);
        assert_eq!(procs.iter().map(|p| p.0).collect::<Vec<_>>(), [77879, 68907, 2150]); // biggest first
        assert_eq!((procs[0].1.as_str(), procs[0].2), ("spider", 6900));
        assert_eq!(usage_rows("12, 7100, 8188, 60\n3, 10, 2048, 40"), "7100, 8188\n10, 2048");
    }

    #[test]
    fn quiet_below_the_line() {
        let _e = crate::testenv::env();
        let (w, sent) = watch(Arc::new(Mutex::new(4000)), true);
        let heard = Rc::new(Cell::new((0, 0)));
        let h = heard.clone();
        w.connect(move |u, t| h.set((u, t)));
        assert_eq!(w.check().unwrap().procs, None);
        assert!(sent.borrow().is_empty());
        assert_eq!(heard.get(), (4000, 8188)); // listeners hear every look
    }

    #[test]
    fn warns_once_names_the_app_and_logs() {
        let _e = crate::testenv::env();
        let (w, sent) = watch(Arc::new(Mutex::new(7600)), true);
        assert!(w.check().unwrap().procs.is_some());
        w.check();
        assert_eq!(sent.borrow().len(), 1); // once, not every 15 s
        assert_eq!(sent.borrow()[0].0, "Graphics memory almost full");
        assert!(sent.borrow()[0].1.contains("spider is using 6.7 GB"));
        assert!(std::fs::read_to_string(log_path()).unwrap().contains("spider (77879) 6900 MiB"));
    }

    #[test]
    fn warns_again_after_it_went_down() {
        let _e = crate::testenv::env();
        let used = Arc::new(Mutex::new(7600));
        let (w, sent) = watch(used.clone(), true);
        w.check();
        *used.lock().unwrap() = 3000;
        w.check();
        *used.lock().unwrap() = 7600;
        w.check();
        assert_eq!(sent.borrow().len(), 2);
    }

    #[test]
    fn size_in_portuguese_has_a_comma() {
        let _e = crate::testenv::env();
        sonata_i18n::use_language("pt_BR");
        let (_, body) = warning(&[(1, "spider".into(), 6900)]);
        sonata_i18n::use_language("en");
        assert!(body.starts_with("spider está usando 6,7 GB."), "{body}");
    }

    #[test]
    fn never_wakes_a_sleeping_card() {
        let _e = crate::testenv::env();
        let (w, _) = watch(Arc::new(Mutex::new(7600)), false);
        assert!(!Rc::new(w).tick());
    }
}
