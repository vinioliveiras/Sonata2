//! The window on a fake /proc and /sys (tests/test_activity.py's
//! WindowTest, test_taskmanager_gpus.py's page test, test_tm_steam.py).
//! GTK lives on one thread: every case runs from one #[test], in order;
//! without a display they are skipped.

use crate::pages::{DetailsPage, HistoryPage, Page, ProcessesPage, UsersPage};
use crate::performance::PerformancePage;
use crate::rows::TaskRow;
use crate::window::{app_for, TaskManagerWindow};
use gtk::prelude::*;
use sonata_backend::procfs::{clk_tck, page_size, Proc, Sampler};
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

const MEMINFO: &str = "MemTotal:       8000000 kB\nMemFree:        1000000 kB\nMemAvailable:   6000000 kB\nBuffers:         100000 kB\nCached:         2000000 kB\nSwapTotal:      1000000 kB\nSwapFree:        750000 kB\nAnonPages:      1500000 kB\nKernelStack:      10000 kB\nPageTables:       20000 kB\nSUnreclaim:       30000 kB\nHugePages_Total:      0\n";

/// A /proc and /sys tree in a temp dir, and a clock the test moves.
struct Fake {
    dir: tempfile::TempDir,
    now: Arc<AtomicU64>,
}

impl Fake {
    fn new() -> Self {
        let f = Fake { dir: tempfile::tempdir().unwrap(), now: Arc::new(AtomicU64::new(1000_f64.to_bits())) };
        for b in ["sda", "nvme0n1", "loop0"] {
            std::fs::create_dir_all(f.sys().join("block").join(b)).unwrap();
        }
        f.w_sys("block/sda/size", "1953525168\n");
        f.w_sys("block/sda/queue/rotational", "1\n");
        f.w_sys("block/nvme0n1/device/model", "Fast SSD\n");
        let cpuinfo: String = (0..4).map(|i| format!("processor\t: {i}\nmodel name\t: Test CPU 9000\nphysical id\t: 0\ncore id\t\t: {}\ncpu MHz\t\t: 1000.0\n\n", i / 2)).collect();
        f.w("cpuinfo", &cpuinfo);
        f.w_sys("devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "3500000\n");
        f.w("uptime", "5000.00 4000.00\n");
        f.w("meminfo", MEMINFO);
        f.w("stat", "cpu  100 0 50 800 50 0 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0 0\nbtime 1700000000\n");
        f.w("diskstats", "   8 0 sda 0 0 0 0 0 0 0 0 0 0 0\n   8 0 nvme0n1 0 0 0 0 0 0 0 0 0 0 0\n");
        f.net(0, 0);
        f.process(1, "init", &["/sbin/init"], 1, (0, 0), 1, 100);
        f.process(10, "busy", &["/usr/bin/busy"], 1, (0, 0), 3, 300);
        f.process(20, "idle", &["/usr/bin/idle"], 1, (0, 0), 2, 200);
        f.process(30, "editor", &["/usr/bin/editor"], 1, (0, 0), 2, 200);
        f.process(31, "editor-helper", &["/usr/bin/editor-helper"], 30, (0, 0), 1, 50);
        f
    }
    fn proc(&self) -> PathBuf {
        self.dir.path().join("proc")
    }
    fn sys(&self) -> PathBuf {
        self.dir.path().join("sys")
    }
    fn put(path: PathBuf, text: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    fn w(&self, rel: &str, text: &str) {
        Self::put(self.proc().join(rel), text);
    }
    fn w_sys(&self, rel: &str, text: &str) {
        Self::put(self.sys().join(rel), text);
    }
    fn net(&self, rx: u64, tx: u64) {
        self.w("net/dev", &format!("Inter-|   Receive |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n    lo: 999 10 0 0 0 0 0 0 999 10 0 0 0 0 0 0\n  eth0: {rx} 100 0 0 0 0 0 0 {tx} 50 0 0 0 0 0 0\n"));
    }
    #[allow(clippy::too_many_arguments)]
    fn process(&self, pid: i64, comm: &str, cmdline: &[&str], ppid: i64, ticks: (i64, i64), threads: i64, rss_pages: i64) {
        let rest = format!("S {ppid} 0 0 0 -1 0 0 0 0 0 {} {} 0 0 20 0 {threads} 0 100 0 {rss_pages}", ticks.0, ticks.1);
        self.w(&format!("{pid}/stat"), &format!("{pid} ({comm}) {rest} 0 0 0\n"));
        self.w(&format!("{pid}/cmdline"), &(cmdline.join("\0") + "\0"));
        self.w(&format!("{pid}/status"), &format!("Name:\t{comm}\nVmRSS:\t{} kB\nVmSwap:\t0 kB\n", rss_pages * 4));
    }
    fn kill(&self, pid: i64) {
        std::fs::remove_dir_all(self.proc().join(pid.to_string())).unwrap();
    }
    /// a GPU: its driver (a symlink named like it) and runtime PM state
    fn card(&self, n: u32, driver: &str, state: &str) {
        let dev = self.sys().join(format!("class/drm/card{n}/device"));
        std::fs::create_dir_all(&dev).unwrap();
        let drv = self.dir.path().join("drivers").join(driver);
        std::fs::create_dir_all(&drv).unwrap();
        let _ = std::os::unix::fs::symlink(&drv, dev.join("driver"));
        Self::put(dev.join("power/runtime_status"), &format!("{state}\n"));
    }
    fn sampler(&self) -> Sampler {
        let now = self.now.clone();
        Sampler::with_clock(self.proc(), self.sys(), Box::new(move || f64::from_bits(now.load(Ordering::SeqCst))))
    }
    fn advance(&self, s: f64) {
        let t = f64::from_bits(self.now.load(Ordering::SeqCst)) + s;
        self.now.store(t.to_bits(), Ordering::SeqCst);
    }
}

fn settle(ms: u64) {
    let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
    let ctx = glib::MainContext::default();
    while std::time::Instant::now() < end {
        while ctx.iteration(false) {}
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
}

/// Spins the main loop until `f` holds (or `ms` pass); whether it held.
fn spin(ms: u64, f: impl Fn() -> bool) -> bool {
    let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
    let ctx = glib::MainContext::default();
    while std::time::Instant::now() < end {
        while ctx.iteration(false) {}
        if f() {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    f()
}

fn page<T: 'static>(w: &TaskManagerWindow, id: &str) -> Rc<dyn crate::pages::Page> {
    let p = w.pages.borrow().iter().find(|p| p.id() == id).cloned().unwrap();
    assert!(p.as_any().downcast_ref::<T>().is_some());
    p
}

fn window(app: &gtk::Application, fs: &Fake) -> Rc<TaskManagerWindow> {
    let w = TaskManagerWindow::new(app, Some(fs.sampler()));
    // not shown: the test drives the samples
    w.icons.replace(Some(HashMap::from([("editor".to_string(), (Some(gio::ThemedIcon::new("accessories-text-editor").upcast()), "Editor".to_string()))])));
    w
}

fn names(sorted: &gtk::SortListModel) -> Vec<String> {
    (0..sorted.n_items()).filter_map(|i| crate::widgets::row_of(sorted.item(i))).map(|r| r.name()).collect()
}

fn details_sort_select_filter(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    w.show_page("details");
    let p = page::<DetailsPage>(&w, "details");
    let page = p.as_any().downcast_ref::<DetailsPage>().unwrap();
    w.apply_snapshot(w.sample_now());
    let mut n = names(&page.sorted);
    n.sort();
    assert_eq!(n, ["Editor", "busy", "editor-helper", "idle", "init"]); // an app's name
    let before = w.pid_rows.borrow()[&20].clone();
    fs.process(20, "idle", &["/usr/bin/idle"], 1, (clk_tck() as i64, 0), 2, 200);
    fs.advance(2.0);
    let busy = names(&page.sorted).iter().position(|n| n == "busy").unwrap();
    page.selection.set_selected(busy as u32);
    w.apply_snapshot(w.sample_now());
    assert_eq!(names(&page.sorted)[0], "idle"); // CPU, highest first
    assert_eq!(w.pid_rows.borrow()[&20], before); // rows updated in place
    assert!((before.n("cpu") - 50.0).abs() < 1e-9);
    assert_eq!(page.selected_rows()[0].name(), "busy"); // selection kept across the refresh
    let name_col = page.cols.iter().find(|c| c.0 == "name").unwrap().1.clone();
    page.view.sort_by_column(Some(&name_col), gtk::SortType::Ascending);
    assert_eq!(names(&page.sorted), ["busy", "Editor", "editor-helper", "idle", "init"]);
    // ("init", not the Python test's "ini": the user's name matches too -- "vinicius")
    w.search.set_text("init");
    assert!(spin(1000, || names(&page.sorted) == ["init"])); // the search field waits a moment
    w.search.set_text("");
    assert!(spin(1000, || names(&page.sorted).len() == 5));
    assert_eq!(page.selected_rows()[0].name(), "busy"); // back when the filter lets it
    fs.kill(10);
    fs.advance(2.0);
    w.apply_snapshot(w.sample_now());
    assert!(!names(&page.sorted).contains(&"busy".to_string()));
    assert!(page.selected_rows().is_empty());
    let rows = w.pid_rows.borrow().clone();
    assert!(w.descends(&rows[&31], &rows[&30]));
    assert!(!w.descends(&rows[&30], &rows[&31]));
    w.window.destroy();
}

fn editor_busy(fs: &Fake) {
    fs.process(31, "editor-helper", &["/usr/bin/editor-helper"], 30, (clk_tck() as i64, 0), 1, 50);
    fs.advance(2.0);
}

fn processes_page_groups(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    w.show_page("processes");
    let p = page::<ProcessesPage>(&w, "processes");
    let page = p.as_any().downcast_ref::<ProcessesPage>().unwrap();
    w.apply_snapshot(w.sample_now());
    editor_busy(&fs);
    w.apply_snapshot(w.sample_now());
    assert_eq!(*page.counts.borrow(), [1, 3, 0]); // the fake tree's processes are ours
    let first = page.sorted.item(0).and_downcast::<gtk::TreeListRow>().unwrap();
    let node = first.item().and_downcast::<TaskRow>().unwrap();
    assert_eq!(node.name(), "Editor (2)"); // the app, its processes counted
    assert!((node.n("cpu") - 50.0).abs() < 1e-9); // its processes' total
    assert_eq!(node.n("mem") as u64, 250 * page_size());
    first.set_expanded(true);
    let kids: Vec<String> = (1..3).map(|i| crate::widgets::row_of(page.sorted.item(i)).unwrap().name()).collect();
    assert_eq!(kids, ["editor-helper", "Editor"]); // sorted by CPU inside the app
    page.selection.set_selected(0);
    let mut pids: Vec<i64> = page.selected_rows().iter().map(TaskRow::pid).collect();
    pids.sort();
    assert_eq!(pids, [30, 31]); // End task ends them all
    w.search.set_text("helper");
    assert!(spin(1000, || page.sorted.n_items() == 2)); // the app and its matching process
    assert_eq!(crate::widgets::row_of(page.sorted.item(0)).unwrap().name(), "Editor (2)");
    w.search.set_text("");
    assert!(spin(1000, || page.sorted.n_items() > 2));
    test_app_found_by_its_pid_in_search(page);
    w.window.destroy();
}

/// Searching an app's PID showed its process without the app: the
/// Processes page matched PIDs on process rows only, where pages.py
/// matches an app by its first process's PID (AppNode.pid).
fn test_app_found_by_its_pid_in_search(page: &ProcessesPage) {
    page.search("30"); // (the page's own search: the field's waits a moment)
    let shown = names(&page.sorted);
    assert_eq!(shown.first().map(String::as_str), Some("Editor (2)"));
    page.search("");
}

fn users_history_performance(app: &gtk::Application) {
    let fs = Fake::new();
    fs.net(100, 50);
    let w = window(app, &fs);
    w.apply_snapshot(w.sample_now());
    fs.process(30, "editor", &["/usr/bin/editor"], 1, (clk_tck() as i64, 0), 2, 200);
    fs.advance(2.0);
    w.show_page("users");
    w.apply_snapshot(w.sample_now());
    let users = page::<UsersPage>(&w, "users");
    let store = users.as_any().downcast_ref::<UsersPage>().unwrap().store();
    assert_eq!(store.n_items(), 1);
    assert_eq!(store.item(0).and_downcast::<TaskRow>().unwrap().n("procs"), 5.0);
    w.show_page("history");
    let hist = page::<HistoryPage>(&w, "history");
    let hist = hist.as_any().downcast_ref::<HistoryPage>().unwrap();
    assert_eq!(hist.store().item(0).and_downcast::<TaskRow>().unwrap().name(), "Editor");
    assert!((w.history.borrow().apps["editor"].cpu_time - 1.0).abs() < 1e-9);
    hist.clear();
    assert_eq!(hist.store().n_items(), 0);
    w.show_page("performance");
    let perf = page::<PerformancePage>(&w, "performance");
    let perf = perf.as_any().downcast_ref::<PerformancePage>().unwrap();
    let keys: Vec<String> = perf.resources.borrow().iter().map(|r| r.key.clone()).collect();
    assert_eq!(keys[..4], ["cpu", "memory", "disk:nvme0n1", "disk:sda"]);
    assert!(keys.contains(&"net:eth0".to_string()));
    assert!(!keys.contains(&"net:lo".to_string()));
    perf.list.select_row(perf.row("memory").as_ref());
    assert_eq!(perf.current.borrow().as_deref(), Some("memory"));
    assert_eq!(perf.stat_labels.borrow()["In use"].label(), "1.9 GB");
    assert_eq!(perf.resource("cpu").unwrap().a.values.borrow().len(), 1); // the first sample has no rates
    fs.advance(2.0);
    w.apply_snapshot(w.sample_now());
    assert_eq!(perf.resource("cpu").unwrap().a.values.borrow().len(), 2);
    w.window.destroy();
}

fn end_task(app: &gtk::Application) {
    let mut child = std::process::Command::new("sleep").arg("30").spawn().unwrap();
    let w = TaskManagerWindow::new(app, None);
    w.icons.replace(Some(HashMap::new()));
    w.apply_snapshot(w.sample_now());
    let row = w.pid_rows.borrow()[&(child.id() as i64)].clone();
    w.end_task(vec![row], false);
    let status = (0..100).find_map(|_| {
        std::thread::sleep(std::time::Duration::from_millis(50));
        child.try_wait().ok().flatten()
    });
    use std::os::unix::process::ExitStatusExt;
    assert_eq!(status.and_then(|s| s.signal()), Some(libc::SIGTERM));
    w.window.destroy();
}

fn window_builds_and_samples(app: &gtk::Application) {
    let fs = Fake::new();
    let w = TaskManagerWindow::new(app, Some(fs.sampler()));
    w.window.present();
    assert!(spin(3000, || w.pid_rows.borrow().contains_key(&30))); // icon index + first samples
    assert_eq!(w.window.title().as_deref(), Some("Task Manager"));
    assert!(w.is_sampling());
    let pages: Vec<(&'static str, &'static str)> = w.pages.borrow().iter().map(|p| (p.id(), p.title())).collect();
    for (id, title) in pages {
        // every page builds and shows
        w.show_page(id);
        settle(50);
        assert_eq!(w.title_lbl.label(), title);
    }
    settle(300);
    w.window.set_visible(false); // hidden: sampling pauses
    assert!(spin(1000, || !w.is_sampling()));
    w.window.destroy();
}

/// The GPU Engine column: only the last column fills, it sorts, and an
/// app lists its processes' cards.
fn gpu_engine_column(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    w.show_page("processes");
    let p = page::<ProcessesPage>(&w, "processes");
    let page = p.as_any().downcast_ref::<ProcessesPage>().unwrap();
    let cols = page.view.columns();
    let shown: Vec<gtk::ColumnViewColumn> = (0..cols.n_items()).filter_map(|i| cols.item(i).and_downcast::<gtk::ColumnViewColumn>()).filter(|c| c.is_visible()).collect();
    assert_eq!(shown.last().unwrap().title().as_deref(), Some("GPU Engine"));
    let expands: Vec<bool> = shown.iter().map(|c| c.expands()).collect();
    assert_eq!(expands, [vec![false; shown.len() - 1], vec![true]].concat());
    let mut snap = w.sample_now();
    snap.procs.get_mut(&30).unwrap().gpu_on = "NVIDIA".into();
    snap.procs.get_mut(&31).unwrap().gpu_on = "AMD".into();
    w.apply_snapshot(snap);
    settle(50);
    let node = page.nodes.borrow().get("editor").cloned().unwrap();
    assert_eq!(node.s("gpu_on"), "AMD, NVIDIA"); // the app: all its processes' cards
    page.view.sort_by_column(Some(&page.col("gpu_on")), gtk::SortType::Ascending); // strings sort too
    settle(50);
    w.window.destroy();
}

/// Both GPUs of a hybrid laptop get a row; the sleeping NVIDIA says so and
/// is never woken (its runtime PM is "suspended": nvidia-smi isn't run).
fn both_gpus_rows_sleeping_one_says_so(app: &gtk::Application) {
    let fs = Fake::new();
    fs.card(0, "nvidia", "suspended");
    fs.card(1, "amdgpu", "active");
    fs.w_sys("class/drm/card1/device/gpu_busy_percent", "23\n");
    let w = window(app, &fs);
    w.show_page("performance");
    w.apply_snapshot(w.sample_now());
    fs.advance(2.0);
    for _ in 0..6 {
        // a sleeping card never "goes away"
        w.apply_snapshot(w.sample_now());
        fs.advance(1.0);
    }
    let perf = page::<PerformancePage>(&w, "performance");
    let perf = perf.as_any().downcast_ref::<PerformancePage>().unwrap();
    assert_eq!(perf.resource("gpu:card0").unwrap().subtitle.borrow().as_str(), "NVIDIA  Sleeping");
    assert_eq!(perf.resource("gpu:card1").unwrap().subtitle.borrow().as_str(), "AMD  23%");
    assert_eq!(perf.resource("gpu:card0").unwrap().a.values.borrow().back().copied(), Some(0.0));
    w.show_page("processes");
    w.apply_snapshot(w.sample_now());
    let p = page::<ProcessesPage>(&w, "processes");
    let procs = p.as_any().downcast_ref::<ProcessesPage>().unwrap();
    assert_eq!(procs.col("gpu").title().as_deref(), Some("23%\nGPU")); // None values don't crash max()
    w.window.destroy();
}

/// The Properties panel had no close dot: Python's opens with close=True
/// (every form-like panel and dialog has the windows' close dot), the Rust
/// one without it.
fn test_properties_panel_without_close_dot(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    w.window.present();
    w.show_page("details");
    w.apply_snapshot(w.sample_now());
    let row = w.pid_rows.borrow()[&20].clone();
    w.show_properties(vec![row]);
    let p = page::<DetailsPage>(&w, "details");
    let props = {
        let b = p.buttons();
        let mut c = b.first_child();
        let mut found = None;
        while let Some(x) = c {
            if x.tooltip_text().as_deref() == Some("Properties") {
                found = Some(x.clone());
            }
            c = x.next_sibling();
        }
        found.unwrap()
    };
    let mut pop = None;
    let mut c = props.first_child();
    while let Some(x) = c {
        if let Some(p) = x.downcast_ref::<gtk::Popover>() {
            pop = Some(p.clone());
        }
        c = x.next_sibling();
    }
    let pop = pop.expect("the Properties panel hangs from its button");
    assert!(sonata_ui::window::has_window_buttons(&pop.child().unwrap()));
    pop.popdown();
    settle(400); // (closed: it lets go of its button)
    w.window.destroy();
}

/// Toggling a startup item's switch set Enable / Disable from the toggled
/// row, not the selected one: with "Alpha" (on) selected, switching
/// "Beta" off greyed out Disable although Alpha could still be disabled.
fn test_startup_buttons_follow_the_toggled_row(app: &gtk::Application) {
    let fs = Fake::new();
    // only this test's login items: none of the computer's (/etc/xdg/autostart)
    std::env::set_var("XDG_CONFIG_DIRS", fs.dir.path().join("xdg"));
    let user = sonata_config::config_home().join("autostart");
    std::fs::create_dir_all(&user).unwrap();
    for (f, n) in [("alpha.desktop", "Alpha"), ("beta.desktop", "Beta")] {
        std::fs::write(user.join(f), format!("[Desktop Entry]\nType=Application\nName={n}\nExec={}\n", n.to_lowercase())).unwrap();
    }
    let w = window(app, &fs);
    w.show_page("startup");
    let sp = w.startup.borrow().clone().unwrap();
    let view = sp.view().unwrap();
    let sel = view.model().and_downcast::<gtk::SingleSelection>().unwrap();
    assert!(spin(3000, || sel.n_items() == 2));
    let row_named = |n: &str| (0..sel.n_items()).find_map(|i| sel.item(i).and_downcast::<TaskRow>().filter(|r| r.name() == n)).unwrap();
    let alpha = (0..sel.n_items()).find(|i| sel.item(*i).and_downcast::<TaskRow>().unwrap().name() == "Alpha").unwrap();
    sel.set_selected(alpha);
    sp.set_enabled(&row_named("Beta"), false);
    assert!(spin(3000, || std::fs::read_to_string(user.join("beta.desktop")).unwrap().contains("Hidden=true")));
    settle(100);
    let (enable, disable) = sp.toolbar_buttons();
    assert!(disable.is_sensitive() && !enable.is_sensitive()); // Alpha, the selection, is on
    w.window.destroy();
}

/// A Steam game's process is that game, never the launcher's app
/// (Halloween showed as "Red Dead Redemption": a wine launcher in another
/// game's entry); wine's own processes are no app at all.
fn steam_game_named_by_its_app_id() {
    let idx = HashMap::from([("wine64-preloader".to_string(), (None, "Red Dead Redemption".to_string())), ("wineserver".to_string(), (None, "Red Dead Redemption".to_string()))]);
    let game = Proc { cmdline: "Z:\\Halloween.exe".into(), exe: "wine64-preloader".into(), comm: "Halloween.exe".into(), name: "Halloween.exe".into(), steam: "3219630".into(), ..Default::default() };
    let (key, _, name) = app_for(&game, &idx);
    assert_eq!(key.as_deref(), Some("steam_app_3219630"));
    assert_ne!(name, "Red Dead Redemption");
    let wine = Proc { cmdline: "wineserver".into(), exe: "wineserver".into(), comm: "wineserver".into(), name: "wineserver".into(), ..Default::default() };
    assert_eq!(app_for(&wine, &idx).0, None);
}

/// A GPU that stops reporting (unplugged) leaves the Performance list
/// after a few samples; one missed read keeps it.
fn gone_gpu_leaves_performance_list(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    let p = page::<PerformancePage>(&w, "performance");
    let perf = p.as_any().downcast_ref::<PerformancePage>().unwrap();
    let mut snap = w.sample_now();
    snap.gpus = std::collections::BTreeMap::from([("card7".to_string(), Some(40.0))]);
    perf.update(&snap);
    assert!(perf.resource("gpu:card7").is_some());
    perf.list.select_row(perf.row("gpu:card7").as_ref());
    snap.gpus.clear();
    perf.update(&snap);
    assert!(perf.resource("gpu:card7").is_some()); // one missed read: kept
    for _ in 0..10 {
        perf.update(&snap);
    }
    assert!(perf.resource("gpu:card7").is_none());
    assert!(perf.row("gpu:card7").is_none());
    assert_eq!(perf.current.borrow().as_deref(), Some("cpu"));
    w.window.destroy();
}

/// Switching pages crossfades the content and the page's toolbar buttons.
fn page_switch_crossfades(app: &gtk::Application) {
    let fs = Fake::new();
    let w = window(app, &fs);
    w.window.present();
    settle(300);
    for st in [w.stack(), w.page_buttons()] {
        assert_eq!(st.transition_type(), gtk::StackTransitionType::Crossfade);
        assert!(st.transition_duration() > 0);
    }
    let to = if w.page_id.borrow().as_deref() == Some("performance") { "details" } else { "performance" };
    w.show_page(to);
    assert!(w.stack().is_transition_running());
    w.window.destroy();
    settle(50);
}

#[test]
fn window_on_a_fake_system() {
    steam_game_named_by_its_app_id();
    if gtk::init().is_err() {
        return; // (no display)
    }
    sonata_i18n::use_language("en");
    sonata_ui::setup();
    sonata_ui::css::register_module("activity/widgets.py");
    let app = gtk::Application::new(Some("io.test.activity.rs"), gio::ApplicationFlags::NON_UNIQUE);
    let _ = app.register(gio::Cancellable::NONE);
    details_sort_select_filter(&app);
    processes_page_groups(&app);
    users_history_performance(&app);
    end_task(&app);
    window_builds_and_samples(&app);
    gpu_engine_column(&app);
    both_gpus_rows_sleeping_one_says_so(&app);
    test_properties_panel_without_close_dot(&app);
    test_startup_buttons_follow_the_toggled_row(&app);
    gone_gpu_leaves_performance_list(&app);
    page_switch_crossfades(&app);
}
