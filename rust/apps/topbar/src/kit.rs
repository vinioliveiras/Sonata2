//! What the menu bar's menus and Control Center build with that sonata-ui
//! doesn't have yet (ui/panel.py's column, module, toggle, click_anywhere,
//! align_to_start, a fixed width and a kept panel), the background reads
//! (system.run_async) and topbar.py's cache of the last answers. The
//! panel pieces belong in sonata_ui::panel; here until Settings needs them.

use gtk::{glib, prelude::*};
use std::any::Any;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

/// `work` in a thread, `done(result)` back on the main loop.
pub fn run_async<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static, done: impl FnOnce(T) + 'static) {
    glib::spawn_future_local(async move {
        if let Ok(r) = gio::spawn_blocking(work).await {
            done(r);
        }
    });
}

/// Fire and forget (a setting written, a device switched).
pub fn in_thread(work: impl FnOnce() + Send + 'static) {
    let _ = std::thread::Builder::new().name("topbar".into()).spawn(work);
}

/// The Python Sonata's launcher (`sonata2`): Settings, Files and the
/// parts not ported yet are still its.
pub fn python_launcher() -> String {
    std::env::var("SONATA2_LAUNCHER").ok().filter(|l| std::path::Path::new(l).exists()).unwrap_or_else(|| "sonata2".into())
}

/// This binary again (`sonata2-rs <component>`): Spotlight, Task Manager
/// and the lock screen are ported.
pub fn spawn_self(args: &[&str]) {
    let exe = std::env::current_exe().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|_| "sonata2-rs".into());
    let mut cmd = vec![exe.as_str()];
    cmd.extend_from_slice(args);
    sonata_backend::run::spawn(&cmd);
}

/// Start Sonata Settings (its own process; the Python one until it's ported).
pub fn open_settings(page: &str) {
    let l = python_launcher();
    let mut cmd = vec![l.as_str(), "settings"];
    if !page.is_empty() {
        cmd.extend(["--page", page]);
    }
    sonata_backend::run::spawn(&cmd);
}

// -- panels -------------------------------------------------------------------------------

pub fn column(children: &[&gtk::Widget]) -> gtk::Box {
    let b = gtk::Box::new(gtk::Orientation::Vertical, 0);
    for c in children {
        b.append(*c);
    }
    b
}

/// A Control Center module: the rounded tile.
pub fn module(children: &[&gtk::Widget], spacing: i32) -> gtk::Box {
    let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(spacing).css_classes(["panel-module"]).build();
    for c in children {
        b.append(*c);
    }
    b
}

pub fn module_title(text: &str) -> gtk::Label {
    gtk::Label::builder().label(text).xalign(0.0).css_classes(["panel-module-title"]).build()
}

/// Control Center style: round icon toggle + title (+ caption).
#[derive(Clone)]
pub struct Toggle {
    pub root: gtk::Box,
    pub button: gtk::Button,
    pub texts: gtk::Box,
    pub caption: gtk::Label,
}

impl Toggle {
    pub fn new(icon: &str, title: &str, on: bool, on_change: impl Fn(bool) + 'static, caption: &str) -> Toggle {
        let button = gtk::Button::builder().icon_name(icon).css_classes(["panel-toggle"]).valign(gtk::Align::Center).can_focus(false).build();
        if on {
            button.add_css_class("on");
        }
        button.connect_clicked(move |b| {
            let now = !b.has_css_class("on");
            if now {
                b.add_css_class("on");
            } else {
                b.remove_css_class("on");
            }
            on_change(now);
        });
        let root = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        root.append(&button);
        let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).build();
        texts.append(&module_title(title));
        let caption = gtk::Label::builder().label(caption).xalign(0.0).css_classes(["panel-caption"]).visible(!caption.is_empty()).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(16).build();
        texts.append(&caption);
        root.append(&texts);
        Toggle { root, button, texts, caption }
    }

    /// Shown, without calling its callback.
    pub fn set(&self, on: bool, caption: Option<&str>) {
        if on {
            self.button.add_css_class("on");
        } else {
            self.button.remove_css_class("on");
        }
        if let Some(c) = caption {
            self.caption.set_label(c);
            self.caption.set_visible(!c.is_empty());
        }
    }

    pub fn is_on(&self) -> bool {
        self.button.has_css_class("on")
    }
}

/// The whole `area` (a Control Center module) toggles `toggle`, not only
/// its round button (Vini: Do Not Disturb's whole square). A click on the
/// button itself is the button's; none while the grid is being edited.
pub fn click_anywhere(area: &gtk::Box, toggle: &Toggle) {
    let g = gtk::GestureClick::builder().button(1).build();
    let (a, btn) = (area.downgrade(), toggle.button.clone());
    g.connect_released(move |_, _, x, y| {
        let Some(area) = a.upgrade() else { return };
        if let Some(w) = area.pick(x, y, gtk::PickFlags::DEFAULT) {
            if w == *btn.upcast_ref::<gtk::Widget>() || w.is_ancestor(&btn) {
                return;
            }
        }
        let mut p = area.parent();
        while let Some(w) = p {
            if w.downcast_ref::<crate::ccgrid::ModuleGrid>().is_some_and(|g| g.editing()) {
                return; // Edit Controls: the modules don't react
            }
            p = w.parent();
        }
        btn.emit_clicked();
    });
    area.add_controller(g);
}

/// A menu bar panel around `child`, `width` wide whatever its content asks
/// (long names ellipsize: ui/fixed.py), hung below `anchor` from its left
/// edge (macOS menus hang from the title's left edge). keep: not dropped
/// when it closes, to be shown again with reopen() (Control Center: a new
/// panel around the same content cost ~100 ms of GTK work).
///
/// ui/panel.py's popup, as sonata_ui has it: the content scrolls on a short
/// display instead of running off it, a click elsewhere in the bar closes
/// it (a lost popup grab left it open over everything), no row lights up
/// under a pointer that hasn't moved yet. A popover of its own here had
/// none of that: a long Wi-Fi list on a 720 px laptop ran off the screen.
pub fn panel(anchor: &impl IsA<gtk::Widget>, child: &impl IsA<gtk::Widget>, gap: i32, width: Option<i32>, align_start: bool, keep: bool) -> gtk::Popover {
    sonata_ui::panel::popup_with(anchor, child, sonata_ui::panel::Options { position: gtk::PositionType::Bottom, gap, align_start, width, close: false, keep })
}

/// A kept panel shown again, as a new one would open: its opening
/// animation (CSS plays an animation once: its name alternates).
pub fn reopen(pop: &gtk::Popover) {
    sonata_ui::panel::reopen(pop);
}

/// Left-align a popover below `anchor` instead of GTK's centring.
thread_local! {
    /// a panel's width around its content (padding, border, shadow room),
    /// as the last one shown had it
    static FRAME: std::cell::Cell<i32> = const { std::cell::Cell::new(0) };
}

pub fn align_to_start(pop: &gtk::Popover, anchor: &gtk::Widget, gap: i32) {
    // placed before it shows: set only once it was on screen, it opened
    // centred and jumped to the left edge (Vini: "thrown to the right")
    // (a popover measures 0 before it's realized: its content's width, plus
    // the frame the last panel had around its content)
    let content = pop.child().map_or(0, |c| c.measure(gtk::Orientation::Horizontal, -1).1);
    if content > 0 && anchor.width() > 0 {
        let natural = content + FRAME.with(|f| f.get());
        pop.set_offset((natural - anchor.width()) / 2, gap);
    }
    let a = anchor.downgrade();
    pop.connect_map(move |p| {
        let (p, a) = (p.clone(), a.clone());
        glib::idle_add_local_once(move || {
            let Some(a) = a.upgrade() else { return };
            let pw = p.width();
            if let Some(c) = p.child().filter(|_| pw > 0) {
                FRAME.with(|f| f.set((pw - c.width()).max(0)));
            }
            if pw > 0 {
                p.set_offset((pw - a.width()) / 2, gap);
            }
        });
    });
}

/// The rows of a box gone (a list filled again).
pub fn clear(b: &gtk::Box) {
    while let Some(c) = b.first_child() {
        b.remove(&c);
    }
}

// -- last known answers (Wi-Fi networks, Bluetooth devices, Control Center) ------------------
// Filled once at start; a menu shows them at once and asks again, updating
// only what changed (macOS menus never open empty and fill in).

type Waiting = Vec<Box<dyn FnOnce(Rc<dyn Any>)>>;

thread_local! {
    static CACHE: RefCell<HashMap<&'static str, Rc<dyn Any>>> = RefCell::default();
    static INFLIGHT: RefCell<HashMap<&'static str, Waiting>> = RefCell::default();
}

/// The last answer for `name`, if any.
pub fn last<T: 'static>(name: &'static str) -> Option<Rc<T>> {
    CACHE.with(|c| c.borrow().get(name).cloned()).and_then(|v| v.downcast::<T>().ok())
}

pub fn keep<T: 'static>(name: &'static str, value: T) {
    CACHE.with(|c| c.borrow_mut().insert(name, Rc::new(value)));
}

/// One read of `name` at a time: whoever asks while it runs gets the same
/// answer (the menu bar's first seconds asked NetworkManager, BlueZ and
/// rfkill the same things two or three times over).
pub fn read_shared<T: Send + 'static>(name: &'static str, work: impl FnOnce() -> T + Send + 'static, cb: impl FnOnce(Rc<T>) + 'static) {
    let wrapped: Box<dyn FnOnce(Rc<dyn Any>)> = Box::new(move |v| {
        if let Ok(v) = v.downcast::<T>() {
            cb(v)
        }
    });
    let running = INFLIGHT.with(|f| {
        let mut f = f.borrow_mut();
        let first = !f.contains_key(name);
        f.entry(name).or_default().push(wrapped);
        !first
    });
    if running {
        return;
    }
    run_async(work, move |res| {
        let v: Rc<dyn Any> = Rc::new(res);
        for c in INFLIGHT.with(|f| f.borrow_mut().remove(name)).unwrap_or_default() {
            c(v.clone());
        }
    });
}

/// Two names read in one job (the prefetch: Wi-Fi's list and Control
/// Center's states share their `nmcli radio` -- it ran twice at login).
/// A name already being read is left to that read; whoever asks for
/// either meanwhile gets this answer.
pub fn read_two<A: Send + 'static, B: Send + 'static>(a: &'static str, b: &'static str, work: impl FnOnce(bool, bool) -> (Option<A>, Option<B>) + Send + 'static) {
    let claim = |name: &'static str| {
        INFLIGHT.with(|f| {
            let mut f = f.borrow_mut();
            if f.contains_key(name) {
                return false;
            }
            f.insert(name, Vec::new());
            true
        })
    };
    let (want_a, want_b) = (claim(a), claim(b));
    if !want_a && !want_b {
        return;
    }
    run_async(move || work(want_a, want_b), move |(ra, rb)| {
        fn deliver<T: 'static>(name: &'static str, wanted: bool, r: Option<T>) {
            if !wanted {
                return;
            }
            let waiting = INFLIGHT.with(|f| f.borrow_mut().remove(name)).unwrap_or_default();
            let Some(r) = r else { return };
            let v: Rc<dyn Any> = Rc::new(r);
            CACHE.with(|c| c.borrow_mut().insert(name, v.clone()));
            for c in waiting {
                c(v.clone());
            }
        }
        deliver(a, want_a, ra);
        deliver(b, want_b, rb);
    });
}

/// fill() with the last answer at once, then with a new one if it
/// differs. again=false: the last answer is enough (an unseen warm-up).
pub fn cached<T: PartialEq + Send + 'static>(name: &'static str, work: impl FnOnce() -> T + Send + 'static, fill: impl Fn(&T) + 'static, again: bool) {
    if let Some(v) = last::<T>(name) {
        fill(&v);
        if !again {
            return;
        }
    }
    read_shared(name, work, move |res: Rc<T>| {
        let same = last::<T>(name).is_some_and(|v| *v == *res);
        CACHE.with(|c| c.borrow_mut().insert(name, res.clone() as Rc<dyn Any>));
        if !same {
            fill(&res);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    fn spin_until(done: impl Fn() -> bool) {
        let ctx = glib::MainContext::default();
        let t = std::time::Instant::now();
        while !done() && t.elapsed().as_secs() < 5 {
            ctx.iteration(false);
        }
    }

    /// A menu opened twice: the second shows the last answer at once and
    /// is filled again only when the answer changed.
    #[test]
    fn cached_fills_at_once_then_only_on_change() {
        let _e = crate::testenv::env(); // (one test at a time owns the main context)
        let ctx = glib::MainContext::default();
        let _g = ctx.acquire().unwrap();
        let fills = Rc::new(Cell::new(0));
        let f = fills.clone();
        cached("test-a", || 5_i32, move |_| f.set(f.get() + 1), true);
        spin_until(|| fills.get() == 1);
        assert_eq!(fills.get(), 1);
        let f = fills.clone();
        cached("test-a", || 5_i32, move |_| f.set(f.get() + 1), true);
        assert_eq!(fills.get(), 2); // the last answer, at once
        let asked = Rc::new(Cell::new(false));
        let a = asked.clone();
        read_shared("test-a", || 5_i32, move |_| a.set(true));
        spin_until(|| asked.get());
        assert_eq!(fills.get(), 2); // the same answer: not filled again
        let f = fills.clone();
        cached("test-a", || 6_i32, move |v| {
            assert!(*v == 5 || *v == 6);
            f.set(f.get() + 1)
        }, true);
        spin_until(|| fills.get() == 4);
        assert_eq!(fills.get(), 4);
        assert_eq!(*last::<i32>("test-a").unwrap(), 6);
    }

    /// The prefetch asked NetworkManager whether Wi-Fi is on twice (Wi-Fi's
    /// list and Control Center's states, two jobs): one job now, its
    /// answers kept and handed to whoever asked meanwhile.
    #[test]
    fn test_prefetch_asked_the_radio_twice() {
        let _e = crate::testenv::env();
        let ctx = glib::MainContext::default();
        let _g = ctx.acquire().unwrap();
        let runs = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let r = runs.clone();
        read_two("test-w", "test-c", move |a, b| {
            r.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            std::thread::sleep(std::time::Duration::from_millis(30));
            (a.then_some(1_i32), b.then_some("cc".to_string()))
        });
        let got = Rc::new(Cell::new(0));
        let g = got.clone();
        read_shared("test-c", || panic!("read twice"), move |v: Rc<String>| {
            assert_eq!(*v, "cc");
            g.set(g.get() + 1);
        });
        spin_until(|| got.get() == 1 && last::<i32>("test-w").is_some());
        assert_eq!(runs.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert_eq!(*last::<i32>("test-w").unwrap(), 1);
        assert_eq!(*last::<String>("test-c").unwrap(), "cc");
    }

    /// Asked twice while one read runs: one read, both answered.
    #[test]
    fn one_read_at_a_time() {
        let _e = crate::testenv::env(); // (one test at a time owns the main context)
        let ctx = glib::MainContext::default();
        let _g = ctx.acquire().unwrap();
        let runs = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let got = Rc::new(Cell::new(0));
        for _ in 0..2 {
            let (r, g) = (runs.clone(), got.clone());
            read_shared("test-b", move || {
                std::thread::sleep(std::time::Duration::from_millis(50));
                r.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
            }, move |_| g.set(g.get() + 1));
        }
        spin_until(|| got.get() == 2);
        assert_eq!(got.get(), 2);
        assert_eq!(runs.load(std::sync::atomic::Ordering::SeqCst), 1);
    }
}
