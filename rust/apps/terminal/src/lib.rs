//! Terminal (terminal/window.py; macOS Terminal, "Basic" profile): the
//! user's shell in tabbed windows.
//!
//! VTE draws the terminal. Colours follow Light/Dark like macOS's Basic
//! profile, the accent for the cursor and a translucent selection. The
//! title reads "folder — process — columns×rows" for the tab in front.
//! Tabs work like macOS Terminal's: each its own shell; the strip (equal
//! tabs, "+" at the end, an x on hover, middle-click closes, drag to
//! reorder) shows once a window has two (or always: Settings). Opening a
//! folder adds a tab to the frontmost window.
//!
//! ⌘ is Ctrl+Shift or Super: ⌘C / ⌘V, ⌘T new tab (in the current tab's
//! folder), ⌘N new window, ⌘W close tab, ⌘⇧[ / ⌘⇧] or Ctrl+Tab (and
//! Ctrl+PageUp/PageDown) switch tabs, ⌘1…⌘9 jump, ⌘+ / ⌘- / ⌘0 text
//! size, ⌘K clears. Closing with something still running asks first.
//!
//! Its own binary (sonata2-rs-terminal): only it needs libvte.

use gtk::prelude::*;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use glib::translate::IntoGlib;
use vte4::prelude::*;

const FONT_SIZE: i64 = 10;
const SCROLLBACK: i64 = 10000;
/// never smaller (Vini: no smallest size; a terminal of a few letters)
const MIN_SIZE: (i32, i32) = (420, 260);
/// ANSI colours (regular, then bright): macOS Terminal's "Basic" in Light
/// Mode; brighter ones in Dark Mode, readable on the dark page
const PALETTE_DARK: [&str; 16] = ["#4d4d4d", "#ff6b6b", "#5fd068", "#e5c07b", "#5c9dff", "#d38aea", "#56c8d8", "#d0d0d0", "#7f7f7f", "#ff8787", "#7ee787", "#f2d68a", "#82b4ff", "#e2a8f2", "#7fd9e6", "#ffffff"];
const PALETTE: [&str; 16] = ["#000000", "#990000", "#00a600", "#999900", "#0000b2", "#b200b2", "#00a6b2", "#bfbfbf", "#666666", "#e50000", "#00d900", "#e5e500", "#0000ff", "#e500e5", "#00e5e5", "#e5e5e5"];

pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.terminal"
}

fn cfg() -> serde_json::Map<String, serde_json::Value> {
    sonata_config::load("terminal", &serde_json::json!({"font_size": FONT_SIZE, "scrollback": SCROLLBACK}))
}

fn rgba(css: &str) -> gtk::gdk::RGBA {
    gtk::gdk::RGBA::parse(css).unwrap_or(gtk::gdk::RGBA::BLACK)
}

/// The user's login shell (the password database), else $SHELL.
pub fn user_shell() -> String {
    let p = unsafe { libc::getpwuid(libc::getuid()) };
    if !p.is_null() {
        let sh = unsafe { std::ffi::CStr::from_ptr((*p).pw_shell) }.to_string_lossy().into_owned();
        if !sh.is_empty() {
            return sh;
        }
    }
    std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".into())
}

/// "folder — process": `~` for home, the user's name for no folder.
pub fn tab_title(path: &str, home: &str, busy: &str, shell: &str, user: &str) -> String {
    let folder = if path.is_empty() {
        String::new()
    } else if path == home {
        "~".into()
    } else {
        std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "/".into())
    };
    let what = if busy.is_empty() { std::path::Path::new(shell).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default() } else { busy.to_string() };
    [if folder.is_empty() { user.to_string() } else { folder }, what].into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" — ")
}

/// The window's text size after ⌘+ / ⌘- / ⌘0.
pub fn zoomed(size: i64, step: i32) -> i64 {
    if step == 0 { FONT_SIZE } else { (size + step as i64).clamp(7, 36) }
}

thread_local! {
    /// every live tab, for appearance changes
    static TABS: RefCell<Vec<Weak<Tab>>> = RefCell::default();
    /// every open window, held until GTK destroys it: everything else
    /// (its keys, tabs, buttons, close-request) only holds it weakly
    static WINDOWS: RefCell<Vec<Rc<TermWindow>>> = RefCell::default();
}

/// One tab: a terminal and its child process.
pub struct Tab {
    term: vte4::Terminal,
    bx: gtk::Box,
    pid: Cell<Option<i32>>,
    closing: Cell<bool>,
    title: RefCell<String>,
    button: RefCell<Option<(gtk::Box, gtk::Label)>>,
    win: RefCell<Weak<TermWindow>>,
}

impl Tab {
    fn new(win: &Rc<TermWindow>, cwd: Option<String>, command: Option<Vec<String>>) -> Rc<Self> {
        let term = vte4::Terminal::builder().vexpand(true).hexpand(true).build();
        term.set_scrollback_lines(cfg().get("scrollback").and_then(|v| v.as_i64()).unwrap_or(SCROLLBACK));
        term.set_mouse_autohide(true);
        term.set_cursor_blink_mode(vte4::CursorBlinkMode::On);
        term.set_bold_is_bright(false);
        // macOS keeps a small inset around the text
        let bx = gtk::Box::builder().margin_start(6).margin_end(2).margin_top(4).vexpand(true).hexpand(true).build();
        bx.append(&term);
        let tab = Rc::new(Self { term, bx, pid: Cell::new(None), closing: Cell::new(false), title: RefCell::default(), button: RefCell::default(), win: RefCell::new(Rc::downgrade(win)) });
        TABS.with(|t| {
            let mut t = t.borrow_mut();
            t.retain(|w| w.strong_count() > 0);
            t.push(Rc::downgrade(&tab));
        });
        tab.apply_look();
        let w = Rc::downgrade(&tab);
        tab.term.connect_window_title_changed(move |_| {
            if let Some(t) = w.upgrade() {
                t.update_title();
            }
        });
        let w = Rc::downgrade(&tab);
        tab.term.connect_current_directory_uri_changed(move |_| {
            if let Some(t) = w.upgrade() {
                t.update_title();
            }
        });
        let w = Rc::downgrade(&tab);
        tab.term.connect_notify_local(Some("column-count"), move |_, _| {
            if let Some(t) = w.upgrade() {
                t.update_title();
            }
        });
        let w = Rc::downgrade(&tab);
        tab.term.connect_child_exited(move |_, _| {
            if let Some(t) = w.upgrade() {
                if let Some(win) = t.win() {
                    win.child_exited(&t);
                }
            }
        });
        let click = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
        let w = Rc::downgrade(&tab);
        click.connect_pressed(move |g, _, x, y| {
            g.set_state(gtk::EventSequenceState::Claimed);
            if let Some(t) = w.upgrade() {
                t.context_menu(x, y);
            }
        });
        tab.term.add_controller(click);
        tab.spawn(cwd.unwrap_or_else(|| glib::home_dir().display().to_string()), command);
        tab
    }

    fn win(&self) -> Option<Rc<TermWindow>> {
        self.win.borrow().upgrade()
    }

    fn spawn(self: &Rc<Self>, cwd: String, command: Option<Vec<String>>) {
        let argv = command.unwrap_or_else(|| vec![user_shell(), "-l".into()]);
        let mut env: Vec<String> = std::env::vars().filter(|(k, _)| k != "COLUMNS" && k != "LINES").map(|(k, v)| format!("{k}={v}")).collect();
        env.extend(["TERM_PROGRAM=Sonata_Terminal".into(), "COLORTERM=truecolor".into()]);
        let argv_ref: Vec<&str> = argv.iter().map(String::as_str).collect();
        let env_ref: Vec<&str> = env.iter().map(String::as_str).collect();
        let w = Rc::downgrade(self);
        let first = argv[0].clone();
        self.term.spawn_async(vte4::PtyFlags::DEFAULT, Some(&cwd), &argv_ref, &env_ref, glib::SpawnFlags::SEARCH_PATH, || {}, -1, gio::Cancellable::NONE, move |res| {
            let Some(t) = w.upgrade() else { return };
            match res {
                Ok(pid) => {
                    t.pid.set(Some(pid.0));
                    t.update_title();
                }
                Err(e) => {
                    let msg = sonata_i18n::fmt(&tr!("Couldn't start {command}: {error}"), &[("command", &first), ("error", e.message())]);
                    t.term.feed(format!("\r\n{msg}\r\n").as_bytes());
                }
            }
        });
    }

    /// The program running in the foreground ("" when the shell waits).
    fn busy(&self) -> String {
        let (Some(pty), Some(pid)) = (self.term.pty(), self.pid.get()) else { return String::new() };
        use std::os::fd::AsRawFd;
        let fg = unsafe { libc::tcgetpgrp(pty.fd().as_raw_fd()) };
        if fg < 0 || fg == pid {
            return String::new();
        }
        std::fs::read_to_string(format!("/proc/{fg}/comm")).map(|s| s.trim().to_string()).unwrap_or_else(|_| tr!("a process"))
    }

    /// The shell's folder: what it reports (OSC 7), else /proc.
    fn cwd(&self) -> Option<String> {
        if let Some(uri) = self.term.current_directory_uri() {
            return gio::File::for_uri(&uri).path().map(|p| p.display().to_string());
        }
        std::fs::read_link(format!("/proc/{}/cwd", self.pid.get()?)).ok().map(|p| p.display().to_string())
    }

    fn apply_look(&self) {
        let size = self.win().map(|w| w.font_size.get()).unwrap_or(FONT_SIZE);
        let v = sonata_ui::values();
        let get = |k: &str| v.get(k).cloned().unwrap_or_default();
        let (fg, bg) = (rgba(&get("term_fg")), rgba(&get("term_bg")));
        let pal: Vec<gtk::gdk::RGBA> = (if sonata_ui::is_dark() { PALETTE_DARK } else { PALETTE }).iter().map(|c| rgba(c)).collect();
        let pal_ref: Vec<&gtk::gdk::RGBA> = pal.iter().collect();
        self.term.set_colors(Some(&fg), Some(&bg), &pal_ref);
        let accent = rgba(&get("accent"));
        self.term.set_color_cursor(Some(&accent));
        self.term.set_color_cursor_foreground(Some(&bg));
        let mut sel = accent;
        sel.set_alpha(0.35);
        self.term.set_color_highlight(Some(&sel));
        self.term.set_color_highlight_foreground(Some(&fg));
        // the whole list (SF Mono, JetBrains Mono, ... monospace): Pango takes the first installed
        let families: Vec<String> = get("font_mono").split(',').map(|f| f.trim().trim_matches('"').to_string()).collect();
        self.term.set_font(Some(&gtk::pango::FontDescription::from_string(&format!("{} {size}", families.join(",")))));
    }

    fn size_for(&self, cols: i64, rows: i64) -> (i32, i32) {
        let (cw, ch) = (self.term.char_width(), self.term.char_height());
        let (cw, ch) = (if cw > 0 { cw } else { 8 }, if ch > 0 { ch } else { 17 });
        ((cols * cw as i64 + 12) as i32, (rows * ch as i64 + 8) as i32)
    }

    fn update_title(&self) {
        let home = glib::home_dir().display().to_string();
        let title = tab_title(&self.cwd().unwrap_or_default(), &home, &self.busy(), &user_shell(), &glib::user_name().to_string_lossy());
        if *self.title.borrow() != title {
            self.title.replace(title.clone());
            if let Some((b, label)) = self.button.borrow().as_ref() {
                label.set_label(&title);
                b.set_tooltip_text(Some(&title));
            }
        }
        if let Some(w) = self.win() {
            w.tab_title_changed(self);
        }
    }

    fn window_title(&self) -> String {
        let size = format!("{}×{}", self.term.column_count(), self.term.row_count());
        [self.title.borrow().clone(), size].into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" — ")
    }

    fn copy(&self) {
        if self.term.has_selection() {
            self.term.copy_clipboard_format(vte4::Format::Text);
        }
    }

    fn paste(&self) {
        self.term.paste_clipboard();
    }

    fn clear(&self) {
        self.term.reset(true, true);
        self.term.feed_child(b"\x0c"); // the shell redraws its prompt
    }

    fn context_menu(self: &Rc<Self>, x: f64, y: f64) {
        use sonata_ui::menu::{popup, Item, Options};
        let Some(w) = self.win() else { return };
        let (t1, t2, t3, t4, t5) = (self.clone(), self.clone(), self.clone(), self.clone(), self.clone());
        let (w1, w2) = (w.clone(), w.clone());
        let sections = vec![
            vec![Item::new(&tr!("Copy"), move || t1.copy()).enabled(self.term.has_selection()), Item::new(&tr!("Paste"), move || t2.paste())],
            vec![Item::new(&tr!("Clear"), move || t3.clear())],
            vec![Item::new(&tr!("New Tab"), move || { w1.new_tab(t4.cwd(), None); }), Item::new(&tr!("Move Tab to New Window"), move || w2.move_to_new_window(&t5)).enabled(w.tabs.borrow().len() > 1)],
        ];
        popup(&self.term, sections, Options::at(x, y));
    }
}

pub struct TermWindow {
    pub win: gtk::ApplicationWindow,
    font_size: Cell<i64>,
    tabs: RefCell<Vec<Rc<Tab>>>,
    current: RefCell<Option<Rc<Tab>>>,
    strip: gtk::Box,
    tab_row: gtk::Box,
    stack: gtk::Stack,
    closing: Cell<bool>,
    _monitors: RefCell<Vec<gio::FileMonitor>>,
}

impl TermWindow {
    pub fn new(app: &gtk::Application, cwd: Option<String>, command: Option<Vec<String>>, tab: Option<Rc<Tab>>) -> Rc<Self> {
        let win = gtk::ApplicationWindow::builder().application(app).title(tr!("Terminal").as_str()).css_classes(["sonata-terminal"]).build();
        sonata_ui::window::standard(&win);
        win.set_size_request(MIN_SIZE.0, MIN_SIZE.1);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        win.set_child(Some(&col));
        let strip = gtk::Box::builder().css_classes(["tm-tabs"]).visible(false).build();
        let tab_row = gtk::Box::builder().homogeneous(true).hexpand(true).build();
        strip.append(&tab_row);
        let add = gtk::Button::builder().icon_name("list-add-symbolic").css_classes(["tm-tab-add"]).tooltip_text(tr!("New Tab").as_str()).valign(gtk::Align::Center).focusable(false).build();
        strip.append(&add);
        col.append(&strip);
        let stack = gtk::Stack::builder().vexpand(true).hexpand(true).build();
        col.append(&stack);
        let size = cfg().get("font_size").and_then(|v| v.as_i64()).unwrap_or(FONT_SIZE);
        let me = Rc::new(Self { win, font_size: Cell::new(size), tabs: RefCell::default(), current: RefCell::default(), strip, tab_row, stack, closing: Cell::new(false), _monitors: RefCell::default() });
        WINDOWS.with(|w| w.borrow_mut().push(me.clone()));
        me.win.connect_destroy(|gone| {
            // (taken out first, dropped after: its own drop may close more)
            // (try_with: at exit the list itself may be going)
            let mine: Vec<Rc<TermWindow>> = WINDOWS
                .try_with(|w| {
                    let Ok(mut w) = w.try_borrow_mut() else { return Vec::new() };
                    let (gone_now, keep): (Vec<_>, Vec<_>) = w.drain(..).partition(|x| x.win.upcast_ref::<gtk::Window>() == gone.upcast_ref::<gtk::Window>());
                    *w = keep;
                    gone_now
                })
                .unwrap_or_default();
            drop(mine);
        });
        let weak = Rc::downgrade(&me);
        me._monitors.replace(sonata_ui::window::follow_tab_bar(move || {
            if let Some(m) = weak.upgrade() {
                m.show_strip();
            }
        }));
        let weak = Rc::downgrade(&me);
        add.connect_clicked(move |_| {
            if let Some(m) = weak.upgrade() {
                let cwd = m.current().and_then(|t| t.cwd());
                m.new_tab(cwd, None);
            }
        });
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let weak = Rc::downgrade(&me);
        keys.connect_key_pressed(move |_, key, code, state| match weak.upgrade() {
            Some(m) if m.key(key, code, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        me.win.add_controller(keys);
        let weak = Rc::downgrade(&me);
        me.win.connect_close_request(move |_| match weak.upgrade() {
            Some(m) => m.close_request(),
            None => glib::Propagation::Proceed,
        });
        let first = match tab {
            Some(t) => me.adopt(t),
            None => me.new_tab(cwd, command),
        };
        let (w, h) = first.size_for(80, 24);
        sonata_ui::window::remember_size(me.win.upcast_ref(), "terminal", w, h);
        // the titles follow `cd` and the program in front even without OSC 7
        let weak = Rc::downgrade(&me);
        glib::timeout_add_seconds_local(1, move || {
            let Some(m) = weak.upgrade() else { return glib::ControlFlow::Break };
            if m.closing.get() {
                return glib::ControlFlow::Break;
            }
            if m.win.is_mapped() {
                let tabs = m.tabs.borrow().clone();
                for t in tabs {
                    t.update_title();
                }
            }
            glib::ControlFlow::Continue
        });
        me
    }

    fn current(&self) -> Option<Rc<Tab>> {
        self.current.borrow().clone()
    }

    // -- tabs ------------------------------------------------------------------------------
    /// A tab after the current one, in front.
    pub fn new_tab(self: &Rc<Self>, cwd: Option<String>, command: Option<Vec<String>>) -> Rc<Tab> {
        let tab = Tab::new(self, cwd, command);
        self.insert(&tab);
        tab.update_title();
        tab
    }

    /// Take a tab from another window (Move Tab to New Window).
    fn adopt(self: &Rc<Self>, tab: Rc<Tab>) -> Rc<Tab> {
        tab.win.replace(Rc::downgrade(self));
        tab.apply_look();
        self.insert(&tab);
        tab.update_title();
        tab
    }

    fn insert(self: &Rc<Self>, tab: &Rc<Tab>) {
        let index = {
            let tabs = self.tabs.borrow();
            match self.current() {
                Some(c) => tabs.iter().position(|t| Rc::ptr_eq(t, &c)).map(|i| i + 1).unwrap_or(tabs.len()),
                None => tabs.len(),
            }
        };
        self.tabs.borrow_mut().insert(index, tab.clone());
        tab.button.replace(Some(self.tab_button(tab)));
        self.stack.add_child(&tab.bx);
        self.rebuild_strip();
        self.select_tab(tab);
    }

    /// A tab in the strip: close x on the left (on hover), centred title.
    fn tab_button(self: &Rc<Self>, tab: &Rc<Tab>) -> (gtk::Box, gtk::Label) {
        let b = gtk::Box::builder().css_classes(["tm-tab"]).hexpand(true).build();
        let over = gtk::Overlay::builder().hexpand(true).build();
        let t = tab.title.borrow().clone();
        let label = gtk::Label::builder().label(if t.is_empty() { "Terminal" } else { &t }).css_classes(["tm-tab-label"]).ellipsize(gtk::pango::EllipsizeMode::Middle).hexpand(true).width_chars(1).build();
        over.set_child(Some(&label));
        let close = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["tm-tab-close"]).tooltip_text(tr!("Close Tab").as_str()).halign(gtk::Align::Start).valign(gtk::Align::Center).focusable(false).build();
        let (w, tw) = (Rc::downgrade(self), Rc::downgrade(tab));
        close.connect_clicked(move |_| {
            if let (Some(m), Some(t)) = (w.upgrade(), tw.upgrade()) {
                m.close_tab(&t);
            }
        });
        over.add_overlay(&close);
        b.append(&over);
        let press = gtk::GestureClick::builder().button(0).build();
        let (w, tw) = (Rc::downgrade(self), Rc::downgrade(tab));
        press.connect_pressed(move |g, _, _, _| {
            let (Some(m), Some(t)) = (w.upgrade(), tw.upgrade()) else { return };
            match g.current_button() {
                gtk::gdk::BUTTON_MIDDLE => m.close_tab(&t),
                gtk::gdk::BUTTON_PRIMARY => m.select_tab(&t),
                _ => {}
            }
        });
        b.add_controller(press);
        // live reorder: the tab moves to the slot under the pointer
        let drag = gtk::GestureDrag::new();
        let start_x: Rc<Cell<Option<f64>>> = Rc::default();
        let (w, bw, sx) = (Rc::downgrade(self), b.downgrade(), start_x.clone());
        drag.connect_drag_begin(move |_, x, y| {
            let (Some(m), Some(b)) = (w.upgrade(), bw.upgrade()) else { return };
            sx.set(b.compute_point(&m.tab_row, &gtk::graphene::Point::new(x as f32, y as f32)).map(|p| p.x() as f64));
        });
        let (w, tw) = (Rc::downgrade(self), Rc::downgrade(tab));
        drag.connect_drag_update(move |_, dx, _| {
            let (Some(m), Some(t)) = (w.upgrade(), tw.upgrade()) else { return };
            let width = m.tab_row.width() as f64 / m.tabs.borrow().len().max(1) as f64;
            let Some(x0) = start_x.get() else { return };
            if dx.abs() < 4.0 || width <= 0.0 {
                return;
            }
            m.move_tab(&t, ((x0 + dx).max(0.0) / width) as usize);
        });
        b.add_controller(drag);
        (b, label)
    }

    fn rebuild_strip(&self) {
        while let Some(c) = self.tab_row.first_child() {
            self.tab_row.remove(&c);
        }
        for t in self.tabs.borrow().iter() {
            if let Some((b, _)) = t.button.borrow().as_ref() {
                self.tab_row.append(b);
            }
        }
        self.show_strip();
    }

    fn show_strip(&self) {
        self.strip.set_visible(sonata_ui::window::tab_bar_shown(self.tabs.borrow().len()));
    }

    fn select_tab(&self, tab: &Rc<Tab>) {
        if !self.tabs.borrow().iter().any(|t| Rc::ptr_eq(t, tab)) {
            return;
        }
        if let Some(c) = self.current() {
            if let Some((b, _)) = c.button.borrow().as_ref() {
                b.remove_css_class("active");
            }
        }
        self.current.replace(Some(tab.clone()));
        if let Some((b, _)) = tab.button.borrow().as_ref() {
            b.add_css_class("active");
        }
        self.stack.set_visible_child(&tab.bx);
        tab.term.grab_focus();
        self.tab_title_changed(tab);
    }

    fn select_index(&self, index: i64) {
        let tabs = self.tabs.borrow().clone();
        if !tabs.is_empty() {
            self.select_tab(&tabs[index.rem_euclid(tabs.len() as i64) as usize]);
        }
    }

    fn cycle(&self, step: i64) {
        let i = self.current().and_then(|c| self.tabs.borrow().iter().position(|t| Rc::ptr_eq(t, &c)));
        if let Some(i) = i {
            self.select_index(i as i64 + step);
        }
    }

    fn move_tab(&self, tab: &Rc<Tab>, index: usize) {
        let mut tabs = self.tabs.borrow_mut();
        let index = index.min(tabs.len().saturating_sub(1));
        let Some(from) = tabs.iter().position(|t| Rc::ptr_eq(t, tab)) else { return };
        if from == index {
            return;
        }
        let t = tabs.remove(from);
        tabs.insert(index, t);
        let prev = if index > 0 { tabs[index - 1].button.borrow().as_ref().map(|b| b.0.clone()) } else { None };
        if let Some((b, _)) = tab.button.borrow().as_ref() {
            self.tab_row.reorder_child_after(b, prev.as_ref());
        }
    }

    fn terminate_alert(self: &Rc<Self>, heading: String, body: String, then: impl Fn(&Rc<Self>) + 'static) {
        let weak = Rc::downgrade(self);
        sonata_ui::dialog::alert(&heading, &body, &[("cancel", tr!("Cancel"), ""), ("close", tr!("Terminate"), "destructive")], move |rid| {
            if let (Some(m), "close") = (weak.upgrade(), rid) {
                then(&m);
            }
        }, Some(self.win.upcast_ref()));
    }

    /// Close a tab, asking first when something runs in it.
    fn close_tab(self: &Rc<Self>, tab: &Rc<Tab>) {
        let busy = tab.busy();
        if busy.is_empty() {
            self.remove(tab, false);
            return;
        }
        let t = Rc::downgrade(tab);
        self.terminate_alert(tr!("Do you want to terminate running processes in this tab?"), sonata_i18n::fmt(&tr!("Closing this tab will terminate “{name}”."), &[("name", &busy)]), move |m| {
            if let Some(t) = t.upgrade() {
                m.remove(&t, false);
            }
        });
    }

    /// Take a tab out; keep leaves its process running (moving it).
    fn remove(self: &Rc<Self>, tab: &Rc<Tab>, keep: bool) {
        let Some(i) = self.tabs.borrow().iter().position(|t| Rc::ptr_eq(t, tab)) else { return };
        if self.tabs.borrow().len() == 1 && !keep {
            self.really_close();
            return;
        }
        self.tabs.borrow_mut().remove(i);
        tab.button.replace(None);
        self.stack.remove(&tab.bx);
        if !keep {
            tab.closing.set(true);
        }
        if self.current().is_some_and(|c| Rc::ptr_eq(&c, tab)) {
            self.current.replace(None);
            let next = {
                let tabs = self.tabs.borrow();
                // macOS selects the tab to the right (the left one for the last)
                (!tabs.is_empty()).then(|| tabs[i.min(tabs.len() - 1)].clone())
            };
            if let Some(n) = next {
                self.select_tab(&n);
            }
        }
        self.rebuild_strip();
        if self.tabs.borrow().is_empty() {
            self.really_close();
        }
    }

    fn move_to_new_window(self: &Rc<Self>, tab: &Rc<Tab>) {
        if self.tabs.borrow().len() < 2 {
            return;
        }
        self.remove(tab, true);
        if let Some(app) = self.win.application() {
            TermWindow::new(&app, None, None, Some(tab.clone())).win.present();
        }
    }

    fn child_exited(self: &Rc<Self>, tab: &Rc<Tab>) {
        if !self.closing.get() && !tab.closing.get() {
            self.remove(tab, false);
        }
    }

    fn tab_title_changed(&self, tab: &Tab) {
        if self.current().is_some_and(|c| std::ptr::eq(c.as_ref(), tab)) {
            self.win.set_title(Some(&tab.window_title()));
        }
    }

    fn zoom(&self, step: i32) {
        let size = zoomed(self.font_size.get(), step);
        self.font_size.set(size);
        let _ = sonata_config::update("terminal", &serde_json::json!({"font_size": size}));
        for t in self.tabs.borrow().iter() {
            t.apply_look();
        }
    }

    // -- input -----------------------------------------------------------------------------------
    fn key(self: &Rc<Self>, keyval: gtk::gdk::Key, code: u32, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType as M};
        let (sup, ctrl, shift) = (state.contains(M::SUPER_MASK), state.contains(M::CONTROL_MASK), state.contains(M::SHIFT_MASK));
        let ctrl_shift = ctrl && shift;
        let k = keyval.to_lower();
        if ctrl && !sup {
            // Ctrl+Tab and friends
            if k == Key::Tab && !shift {
                self.cycle(1);
                return true;
            }
            if k == Key::ISO_Left_Tab || (k == Key::Tab && shift) {
                self.cycle(-1);
                return true;
            }
            if (k == Key::Page_Down || k == Key::Page_Up) && !shift {
                self.cycle(if k == Key::Page_Down { 1 } else { -1 });
                return true;
            }
        }
        // ⌘1…⌘9 (Super, Ctrl, or Ctrl+Shift by the key's position: 10…18)
        let (kv, one, nine) = (k.into_glib(), Key::_1.into_glib(), Key::_9.into_glib());
        let digit = if (one..=nine).contains(&kv) { (kv - one) as i64 } else if ctrl_shift && (10..=18).contains(&code) { code as i64 - 10 } else { -1 };
        if digit >= 0 && (sup || ctrl) {
            self.select_index(if digit < 8 { digit } else { -1 }); // ⌘9 is the last tab
            return true;
        }
        if !(sup || ctrl_shift) {
            if ctrl && [Key::plus, Key::equal, Key::minus, Key::_0].contains(&k) {
                self.zoom(match k {
                    Key::minus => -1,
                    Key::_0 => 0,
                    _ => 1,
                });
                return true;
            }
            return false;
        }
        let Some(tab) = self.current() else { return false };
        match k {
            Key::c => tab.copy(),
            Key::v => tab.paste(),
            Key::n => {
                if let Some(app) = self.win.application() {
                    TermWindow::new(&app, tab.cwd(), None, None).win.present();
                }
            }
            Key::t => {
                self.new_tab(tab.cwd(), None);
            }
            Key::w => self.close_tab(&tab),
            Key::k => tab.clear(),
            Key::braceleft | Key::bracketleft => self.cycle(-1),
            Key::braceright | Key::bracketright => self.cycle(1),
            Key::plus | Key::equal => self.zoom(1),
            Key::minus => self.zoom(-1),
            Key::_0 => self.zoom(0),
            _ => return false,
        }
        true
    }

    // -- closing ----------------------------------------------------------------------------------
    fn close_request(self: &Rc<Self>) -> glib::Propagation {
        let mut busy: Vec<String> = Vec::new();
        if !self.closing.get() {
            for b in self.tabs.borrow().iter().map(|t| t.busy()).filter(|b| !b.is_empty()) {
                if !busy.contains(&b) {
                    busy.push(b);
                }
            }
        }
        if busy.is_empty() {
            self.closing.set(true);
            return glib::Propagation::Proceed;
        }
        let names = busy.join("”, “");
        self.terminate_alert(tr!("Do you want to terminate running processes in this window?"), sonata_i18n::fmt(&tr!("Closing this window will terminate “{names}”."), &[("names", &names)]), |m| m.really_close());
        glib::Propagation::Stop
    }

    fn really_close(&self) {
        self.closing.set(true);
        for t in self.tabs.borrow().iter() {
            t.closing.set(true);
        }
        self.win.close();
    }
}

fn folder_of(p: &str) -> Option<String> {
    let path = gio::File::for_commandline_arg(p).path()?;
    Some(if path.is_dir() { path } else { path.parent()?.to_path_buf() }.display().to_string())
}

fn open_windows(app: &gtk::Application, paths: &[String]) {
    // the app's windows, the most recently focused first
    let order: Vec<gtk::Window> = app.windows();
    let mut wins: Vec<Rc<TermWindow>> = WINDOWS.with(|w| w.borrow().iter().filter(|x| !x.closing.get()).cloned().collect());
    wins.sort_by_key(|w| order.iter().position(|o| o == w.win.upcast_ref::<gtk::Window>()).unwrap_or(usize::MAX));
    if paths.is_empty() {
        match wins.first() {
            Some(w) => w.win.present(),
            None => TermWindow::new(app, None, None, None).win.present(),
        }
        return;
    }
    let mut front = wins.into_iter().find(|w| !w.tabs.borrow().is_empty());
    for p in paths {
        match &front {
            Some(w) => {
                w.new_tab(folder_of(p), None);
            }
            None => front = Some(TermWindow::new(app, folder_of(p), None, None)),
        }
    }
    if let Some(w) = front {
        w.win.present();
    }
}

fn setup() {
    sonata_ui::setup();
    sonata_ui::css::register_module("terminal/window.py");
    // every live tab follows the appearance
    sonata_ui::on_change(|| {
        let tabs: Vec<Rc<Tab>> = TABS.with(|t| t.borrow().iter().filter_map(|w| w.upgrade()).collect());
        for t in tabs {
            t.apply_look();
        }
    });
}

/// `sonata2-rs terminal [--new-window] [--exec COMMAND] [FOLDER...]`.
pub fn run(args: &[String]) -> glib::ExitCode {
    let exec = args.iter().position(|a| a == "--exec").and_then(|i| args.get(i + 1)).cloned();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let ready = Rc::new(Cell::new(false));
    let start = move || {
        if !ready.replace(true) {
            setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
        }
    };
    let argv0 = std::env::args().next().unwrap_or_default();
    if let Some(cmd) = exec {
        // a window of its own for the command
        let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::NON_UNIQUE).build();
        app.connect_activate(move |app| {
            start();
            let sh = format!("{cmd}; echo; printf '[Process completed]'; stty -echo; read _");
            TermWindow::new(app, None, Some(vec!["sh".into(), "-c".into(), sh]), None).win.present();
        });
        return app.run_with_args(&[argv0]);
    }
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
    let act = gio::SimpleAction::new("new-window", None);
    let (a, s) = (app.clone(), start.clone());
    act.connect_activate(move |_, _| {
        s();
        TermWindow::new(&a, None, None, None).win.present();
    });
    app.add_action(&act);
    let s = start.clone();
    app.connect_open(move |app, files, _| {
        s();
        let paths: Vec<String> = files.iter().filter_map(|f| f.path()).map(|p| p.display().to_string()).collect();
        open_windows(app, &paths);
    });
    let new_window = args.iter().any(|a| a == "--new-window");
    app.connect_activate(move |app| {
        start();
        if new_window {
            app.activate_action("new-window", None);
        } else {
            open_windows(app, &[]);
        }
    });
    if new_window && app.register(gio::Cancellable::NONE).is_ok() && app.is_remote() {
        // the desktop entry's New Window: the running Terminal makes it
        app.activate_action("new-window", None);
        return glib::ExitCode::SUCCESS;
    }
    let mut argv = vec![argv0];
    argv.extend(args.iter().filter(|a| !a.starts_with("--")).cloned());
    app.run_with_args(&argv)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_read_folder_then_process() {
        assert_eq!(tab_title("/home/vini", "/home/vini", "", "/usr/bin/fish", "vini"), "~ — fish");
        assert_eq!(tab_title("/home/vini/code", "/home/vini", "vim", "/usr/bin/fish", "vini"), "code — vim");
        assert_eq!(tab_title("/", "/home/vini", "", "/bin/bash", "vini"), "/ — bash");
        assert_eq!(tab_title("", "/home/vini", "", "/bin/bash", "vini"), "vini — bash");
    }

    fn settle(ms: u64) {
        let end = std::time::Instant::now() + std::time::Duration::from_millis(ms);
        let ctx = glib::MainContext::default();
        while std::time::Instant::now() < end {
            ctx.iteration(false);
            std::thread::sleep(std::time::Duration::from_millis(2));
        }
    }

    fn names(w: &TermWindow) -> Vec<String> {
        w.tabs.borrow().iter().map(|t| t.cwd().and_then(|c| std::path::Path::new(&c).file_name().map(|n| n.to_string_lossy().into_owned())).unwrap_or_default()).collect()
    }

    fn index_of(w: &TermWindow, t: &Rc<Tab>) -> usize {
        w.tabs.borrow().iter().position(|x| Rc::ptr_eq(x, t)).unwrap()
    }

    fn is_current(w: &TermWindow, i: usize) -> bool {
        let t = w.tabs.borrow()[i].clone();
        w.current().is_some_and(|c| Rc::ptr_eq(&c, &t))
    }

    fn sh() -> Option<Vec<String>> {
        Some(vec!["/bin/sh".into()])
    }

    /// The Rust Terminal let go of its windows as soon as they were made:
    /// ⌘T, the "+" and the tabs' buttons did nothing, `exit` left the
    /// window open and closing one with a program running didn't ask
    /// (everything held the window weakly). With it, tests/test_terminal.py's
    /// TabsTest, real terminals running sh (one test: GTK lives on one thread).
    #[test]
    fn test_terminal_windows_forgot_their_tabs() {
        use gtk::gdk::{Key, ModifierType as M};
        if (gtk::is_initialized() && !gtk::is_initialized_main_thread()) || gtk::init().is_err() {
            return; // no display
        }
        setup();
        let _ = sonata_config::update("appearance", &serde_json::json!({"always_show_tabs": false})); // (the one-tab cases)
        let app = gtk::Application::builder().application_id("io.test.terminal").flags(gio::ApplicationFlags::NON_UNIQUE).build();
        app.register(gio::Cancellable::NONE).expect("registered");
        let dirs: Vec<tempfile::TempDir> = (0..3).map(|i| tempfile::Builder::new().prefix(&format!("tm{i}_")).tempdir().unwrap()).collect();
        let dir = |i: usize| dirs[i].path().display().to_string();
        let base = |i: usize| dirs[i].path().file_name().unwrap().to_string_lossy().into_owned();
        let w = TermWindow::new(&app, Some(dir(0)), sh(), None);
        w.win.present();
        settle(300);

        // one tab: no strip; the title names its folder
        assert_eq!(w.tabs.borrow().len(), 1);
        assert!(!w.strip.get_visible());
        assert!(w.current().unwrap().pid.get().is_some());
        assert!(w.win.title().unwrap().contains(&base(0)));
        // the strip always, when chosen (Settings > Appearance)
        let _ = sonata_config::update("appearance", &serde_json::json!({"always_show_tabs": true}));
        settle(300); // (the change is watched)
        assert!(w.strip.get_visible());
        let _ = sonata_config::update("appearance", &serde_json::json!({"always_show_tabs": false}));
        settle(300);
        assert!(!w.strip.get_visible());

        // add and switch: a new tab goes right after the current one
        let b = w.new_tab(Some(dir(1)), sh());
        settle(200);
        assert!(w.strip.get_visible());
        assert!(is_current(&w, 1));
        assert!(b.button.borrow().as_ref().unwrap().0.has_css_class("active"));
        assert!(!w.tabs.borrow()[0].button.borrow().as_ref().unwrap().0.has_css_class("active"));
        assert!(w.win.title().unwrap().contains(&base(1)));
        w.select_index(0);
        let c = w.new_tab(Some(dir(2)), sh());
        settle(200);
        assert_eq!(index_of(&w, &c), 1);
        assert_eq!(names(&w), [base(0), base(2), base(1)]);
        w.cycle(1);
        assert!(is_current(&w, 2));
        w.cycle(1);
        assert!(is_current(&w, 0));
        w.cycle(-1);
        assert!(is_current(&w, 2));
        assert_eq!(w.stack.visible_child().as_ref(), Some(b.bx.upcast_ref()));

        // reorder: the strip follows; clamped to the end
        w.move_tab(&b, 0);
        assert_eq!(names(&w), [base(1), base(0), base(2)]);
        let order = |w: &TermWindow| {
            let mut v = Vec::new();
            let mut ch = w.tab_row.first_child();
            while let Some(x) = ch {
                ch = x.next_sibling();
                v.push(x);
            }
            v
        };
        let buttons = |w: &TermWindow| w.tabs.borrow().iter().map(|t| t.button.borrow().as_ref().unwrap().0.clone().upcast::<gtk::Widget>()).collect::<Vec<_>>();
        assert_eq!(order(&w), buttons(&w));
        w.move_tab(&b, 5);
        assert_eq!(names(&w), [base(0), base(2), base(1)]);
        assert_eq!(order(&w), buttons(&w));

        // closing the current tab selects the one to its right
        w.select_index(1);
        w.close_tab(&c);
        assert_eq!(names(&w), [base(0), base(1)]);
        assert!(is_current(&w, 1));

        // ⌘ keys: Super, or Ctrl+Shift; plain Ctrl+W / Ctrl+T are the shell's
        assert!(w.key(Key::t, 0, M::SUPER_MASK));
        assert!(w.key(Key::T, 0, M::CONTROL_MASK | M::SHIFT_MASK));
        settle(200);
        assert_eq!(w.tabs.borrow().len(), 4);
        assert!(w.key(Key::_1, 0, M::SUPER_MASK));
        assert!(is_current(&w, 0));
        assert!(w.key(Key::_9, 0, M::CONTROL_MASK)); // ⌘9: the last tab
        assert!(is_current(&w, 3));
        assert!(w.key(Key::Tab, 0, M::CONTROL_MASK));
        assert!(is_current(&w, 0));
        assert!(w.key(Key::ISO_Left_Tab, 0, M::CONTROL_MASK | M::SHIFT_MASK));
        assert!(is_current(&w, 3));
        assert!(w.key(Key::braceleft, 0, M::SUPER_MASK | M::SHIFT_MASK));
        assert!(is_current(&w, 2));
        assert!(w.key(Key::braceright, 0, M::CONTROL_MASK | M::SHIFT_MASK));
        assert!(is_current(&w, 3));
        assert!(w.key(Key::exclam, 11, M::CONTROL_MASK | M::SHIFT_MASK)); // Ctrl+Shift+2 by position
        assert!(is_current(&w, 1));
        assert!(!w.key(Key::w, 0, M::CONTROL_MASK));
        assert!(!w.key(Key::t, 0, M::CONTROL_MASK));
        assert_eq!(w.tabs.borrow().len(), 4);
        // (the new tabs run the login shell: leave just the two sh ones)
        let all = w.tabs.borrow().clone();
        for t in all.iter().skip(1).filter(|t| !Rc::ptr_eq(t, &b)) {
            w.remove(t, false);
        }
        assert_eq!(w.tabs.borrow().len(), 2);

        // Move Tab to New Window: the same process, in a window of its own
        let pid = b.pid.get();
        w.move_to_new_window(&b);
        settle(200);
        assert_eq!(w.tabs.borrow().len(), 1);
        assert!(!w.strip.get_visible());
        let w2 = b.win().unwrap();
        assert!(!Rc::ptr_eq(&w2, &w));
        assert_eq!(w2.tabs.borrow().len(), 1);
        assert_eq!(b.pid.get(), pid); // the same process, not a new one
        assert!(w2.win.title().unwrap().contains(&base(1)));
        w2.move_to_new_window(&b); // a lone tab stays
        assert!(Rc::ptr_eq(&b.win().unwrap(), &w2));
        w2.really_close();

        // folders open as tabs of the front window; no folder: only raises
        open_windows(&app, &[dir(1), dir(2)]);
        settle(300);
        assert_eq!(w.tabs.borrow().len(), 3);
        assert_eq!(names(&w)[1..], [base(1), base(2)]);
        open_windows(&app, &[]);
        assert_eq!(w.tabs.borrow().len(), 3);

        // the shell ending closes its tab; the last one closes the window
        let all = w.tabs.borrow().clone();
        w.child_exited(&all[2]);
        w.child_exited(&all[1]);
        let first = all[0].clone();
        assert_eq!(w.tabs.borrow().len(), 1);
        assert!(!w.strip.get_visible());
        w.child_exited(&first);
        settle(100);
        assert!(w.closing.get());
        assert!(!app.windows().iter().any(|x| x == w.win.upcast_ref::<gtk::Window>()));
    }

    #[test]
    fn text_size_steps() {
        assert_eq!(zoomed(10, 1), 11);
        assert_eq!(zoomed(36, 1), 36);
        assert_eq!(zoomed(7, -1), 7);
        assert_eq!(zoomed(20, 0), FONT_SIZE);
    }
}
