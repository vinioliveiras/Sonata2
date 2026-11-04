//! Draw on the screen while recording or sharing it (sonata2/shell/livedraw.py;
//! Vini; also any time: Super+Shift+D). The recording / sharing pill gets
//! a pen: a click turns drawing on and shows the palette under the menu
//! bar, another click (or Esc) gives the pointer back to the apps -- the
//! marks stay until Clear, or fade away by themselves (the palette's clock).
//!
//! ```text
//! let draw = livedraw::get(&app);   // one per process
//! draw.toggle(None);                // on / off (the pill's pen, the shortcut)
//! draw.stop();                      // off and cleared (the recording or sharing ended)
//! ```
//!
//! One transparent surface per display above everything (layer-shell
//! overlay, "sonata2-draw"): it takes the pointer only while drawing is on,
//! otherwise clicks go through it to the apps. The marks are Preview's
//! Markup marks (sonata-preview's markup.rs: the same tools and renderer).
//! The palette is its own surface ("sonata2-draw-palette") so it can be
//! left out of what's captured.

use gtk::{cairo, gdk, glib, prelude::*};
use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
use sonata_i18n::{n_, tr};
use sonata_preview::markup::{MarkupBar, MarkupLayer, TOOLS as MARKUP_TOOLS};
use std::cell::{Cell, RefCell};
use std::f64::consts::PI;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// fading marks: shown this long, then fade out
pub const FADE_AFTER_S: f64 = 3.0;
pub const FADE_S: f64 = 0.8;
/// the palette's tools (their tooltips are Markup's own, already translated)
pub const TOOLS: [(&str, &str); 5] = [("pen", n_!("Pen")), ("hl", n_!("Highlighter")), ("shape", n_!("Shapes")), ("text", n_!("Text")), ("emoji", n_!("Emoji"))];
pub const COLORS: [&str; 5] = ["#ff3b30", "#ffcc00", "#34c759", "#007aff", "#ffffff"];
pub const PALETTE_NS: &str = "sonata2-draw-palette";
pub const OVERLAY_NS: &str = "sonata2-draw";
/// before the palette has been laid out
pub const PALETTE_SIZE: (i32, i32) = (560, 62);
/// the input once more after on/off (surfaces map late)
pub const SETTLE_MS: u64 = 150;

type Rect = (i32, i32, i32, i32);
type Listener = (u64, Rc<dyn Fn(bool)>);
/// start(go): runs before drawing turns on, calls go() when it may
pub type StartFn = Rc<dyn Fn(Box<dyn FnOnce()>)>;

/// Where the pointer comes to the drawing while it's on: the whole display
/// (its own size, known before GTK has laid the window out -- at map it
/// was 0 x 0, and only one pixel took the clicks: "the screen stays
/// clickable", Vini), less the menu bar (the pill's pen turns it off) and
/// the palette. `palette`: the palette's surface size and the palette
/// itself within it, once laid out; `palette_here`: it's on this display.
pub fn input_rects(on: bool, (w, h): (i32, i32), top: i32, palette_here: bool, palette: Option<((i32, i32), Rect)>) -> Vec<Rect> {
    if !on {
        return Vec::new();
    }
    if !palette_here {
        return vec![(0, top, w, h - top)]; // the palette is on another display: all of this one
    }
    let ((pw, _), (fx, fy, fw, fh)) = palette.unwrap_or((PALETTE_SIZE, (0, 0, PALETTE_SIZE.0, PALETTE_SIZE.1)));
    // a hole for the palette itself only: its shadow's margins still draw
    let (hx, hy) = ((w - pw).div_euclid(2) + fx, top + fy);
    [(0, top, w, hy - top), (0, hy, hx, fh), (hx + fw, hy, w - hx - fw, fh), (0, hy + fh, w, h - hy - fh)].into_iter().filter(|r| r.2 > 0 && r.3 > 0).collect()
}

/// The pen, fade and close glyphs (preview/markup.py's; the Rust Markup
/// bar keeps its own glyphs private and has no fade / close).
fn glyph(name: &'static str, size: i32) -> gtk::DrawingArea {
    let area = gtk::DrawingArea::builder().content_width(size).content_height(size).can_target(false).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
    area.set_draw_func(move |a, cr, w, h| {
        let c = a.color();
        cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
        cr.scale(w as f64 / 16.0, h as f64 / 16.0);
        cr.set_line_width(1.5);
        cr.set_line_cap(cairo::LineCap::Round);
        cr.set_line_join(cairo::LineJoin::Round);
        match name {
            "pen" => {
                cr.move_to(3.0, 13.0);
                for (x, y) in [(4.0, 10.0), (11.0, 3.0), (13.0, 5.0), (6.0, 12.0)] {
                    cr.line_to(x, y);
                }
                cr.close_path();
            }
            "fade" => {
                cr.arc(8.0, 9.0, 5.3, 0.0, 2.0 * PI);
                let _ = cr.stroke();
                cr.move_to(8.0, 6.0);
                cr.line_to(8.0, 9.0);
                cr.line_to(10.2, 10.3);
                cr.move_to(6.0, 1.8);
                cr.line_to(10.0, 1.8);
            }
            _ => {
                cr.move_to(4.0, 4.0);
                cr.line_to(12.0, 12.0);
                cr.move_to(12.0, 4.0);
                cr.line_to(4.0, 12.0);
            }
        }
        let _ = cr.stroke();
    });
    area
}

fn now_s() -> f64 {
    glib::monotonic_time() as f64 / 1e6
}

/// One display's drawing surface.
struct Overlay {
    win: gtk::Window,
    monitor: gdk::Monitor,
    layer: Rc<MarkupLayer>,
    /// when each mark was made (fading), in the marks' order
    stamps: RefCell<Vec<f64>>,
    owner: Weak<LiveDraw>,
}

impl Overlay {
    fn new(app: &gtk::Application, monitor: &gdk::Monitor, key: String, owner: &Rc<LiveDraw>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Drawing").decorated(false).css_classes(["sonata-draw"]).build();
        let ww = win.downgrade();
        let layer = MarkupLayer::new(move || ww.upgrade().map(|w| (0.0, 0.0, w.width() as f64, w.height() as f64)), || None);
        let o = Rc::downgrade(owner);
        *layer.on_change.borrow_mut() = Some(Box::new(move || {
            if let Some(d) = o.upgrade() {
                d.changed(Some(&key));
            }
        }));
        win.set_child(Some(&layer.area));
        if sonata_shell::layer::overlay_fullscreen(&win, OVERLAY_NS) {
            win.set_keyboard_mode(KeyboardMode::None);
            win.set_monitor(Some(monitor));
        }
        let ov = Rc::new(Overlay { win, monitor: monitor.clone(), layer, stamps: RefCell::default(), owner: Rc::downgrade(owner) });
        // each mark takes the palette's tool, colour and width as it starts
        // (markup.py's `follow`): before the layer's own drag sees the press
        let legacy = gtk::EventControllerLegacy::new();
        legacy.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(&ov);
        legacy.connect_event(move |_, ev| {
            if ev.event_type() == gdk::EventType::ButtonPress {
                if let Some((ov, d)) = w.upgrade().and_then(|ov| ov.owner.upgrade().map(|d| (ov, d))) {
                    d.sync(&ov.layer);
                }
            }
            glib::Propagation::Proceed
        });
        ov.win.add_controller(legacy);
        let keys = gtk::EventControllerKey::new();
        let o = Rc::downgrade(owner);
        keys.connect_key_pressed(move |_, kv, _, _| match o.upgrade() {
            Some(d) if kv == gdk::Key::Escape => {
                d.set_on(false, None);
                glib::Propagation::Stop
            }
            _ => glib::Propagation::Proceed,
        });
        ov.win.add_controller(keys);
        let w = Rc::downgrade(&ov);
        ov.win.connect_map(move |_| {
            if let Some(ov) = w.upgrade() {
                ov.pointer(ov.owner.upgrade().is_some_and(|d| d.on.get()));
            }
        });
        // Vini: drawing worked with the palette closed and not with it open.
        // The input region was set once per on/off, and went stale when the
        // surface was laid out again (or mapped late): every layout sets it again.
        let w = Rc::downgrade(&ov);
        ov.win.connect_realize(move |win| {
            let Some(surface) = win.surface() else { return };
            let w = w.clone();
            surface.connect_layout(move |_, _, _| {
                let w = w.clone();
                glib::idle_add_local_once(move || {
                    if let Some(ov) = w.upgrade() {
                        ov.pointer(ov.owner.upgrade().is_some_and(|d| d.on.get()));
                    }
                });
            });
        });
        ov
    }

    fn region(&self, on: bool) -> Vec<Rect> {
        let g = self.monitor.geometry();
        let Some(d) = self.owner.upgrade() else { return Vec::new() };
        let here = d.palette_on.borrow().as_ref() == Some(&self.monitor);
        let pal = d.palette.borrow().as_ref().filter(|p| p.win.is_mapped() && p.win.width() > 0).map(|p| ((p.win.width(), p.win.height()), p.frame_rect()));
        input_rects(on, (g.width(), g.height()), crate::capture::menubar_height(), here, pal)
    }

    /// Drawing: the pointer comes here; off: clicks go through to the apps.
    fn pointer(&self, on: bool) {
        if !self.win.is_mapped() {
            return;
        }
        let rects: Vec<(f64, f64, f64, f64)> = self.region(on).into_iter().map(|(x, y, w, h)| (x as f64, y as f64, w as f64, h as f64)).collect();
        sonata_shell::layer::set_input_region(&self.win, &rects);
        if self.win.is_layer_window() {
            self.win.set_keyboard_mode(if on { KeyboardMode::OnDemand } else { KeyboardMode::None });
        }
        // Wayland takes a new input region with the surface's next frame:
        // with nothing to redraw it waited for the next on/off, one step
        // behind (Vini's video: drawing with the palette closed, not with it open)
        self.layer.area.queue_draw();
        self.win.queue_draw();
    }
}

/// The palette: Markup's tools, these colours, and fade / undo / clear / close.
struct Palette {
    win: gtk::Window,
    frame: gtk::Box,
    fade_btn: gtk::Button,
    _bar: Rc<MarkupBar>,
}

impl Palette {
    fn new(app: &gtk::Application, owner: &Rc<LiveDraw>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title("Drawing Tools").decorated(false).resizable(false).css_classes(["sonata-draw-palette"]).build();
        // Preview's Markup bar on the palette's own layer, trimmed to live
        // drawing's tools and colours (sonata-preview's MarkupBar takes no
        // tool / colour / action lists, as markup.py's does)
        let bar = MarkupBar::new(owner.proto.clone(), Box::new(|| {}), Box::new(|| {}), None);
        let children = |w: &gtk::Widget| {
            let mut v = Vec::new();
            let mut c = w.first_child();
            while let Some(x) = c {
                c = x.next_sibling();
                v.push(x);
            }
            v
        };
        let root_kids = children(bar.root.upcast_ref());
        let row = root_kids[0].clone().downcast::<gtk::Box>().expect("the Markup bar's row");
        for extra in &root_kids[1..] {
            extra.set_visible(false); // no Copy / Cancel / Done: nothing to finish
        }
        let mut mk = Vec::new();
        for c in children(row.upcast_ref()) {
            if c.has_css_class("mk-color") {
                c.set_visible(COLORS.iter().any(|k| c.tooltip_text().as_deref() == Some(*k)));
            } else if c.has_css_class("mk") {
                mk.push(c.downcast::<gtk::Button>().expect("a Markup button"));
            }
        }
        // tools (MARKUP_TOOLS' order), width, undo, redo, delete all
        for (b, (name, _)) in mk.iter().zip(MARKUP_TOOLS.iter()) {
            b.set_visible(TOOLS.iter().any(|(t, _)| t == name));
        }
        let (undo_b, redo_b, trash_b) = (&mk[MARKUP_TOOLS.len() + 1], &mk[MARKUP_TOOLS.len() + 2], &mk[MARKUP_TOOLS.len() + 3]);
        for b in [undo_b, redo_b, trash_b] {
            b.set_visible(false); // they'd act on the palette's own layer: live drawing's below
        }
        let take = |b: &gtk::Button| {
            let g = b.child();
            b.set_child(None::<&gtk::Widget>);
            g
        };
        let w = Rc::downgrade(owner);
        let act = move |f: fn(&LiveDraw)| {
            let w = w.clone();
            move |_: &gtk::Button| {
                if let Some(d) = w.upgrade() {
                    f(&d);
                }
            }
        };
        let button = |tip: String, child: Option<gtk::Widget>| {
            let b = gtk::Button::builder().tooltip_text(tip).can_focus(false).css_classes(["mk"]).build();
            b.set_child(child.as_ref());
            row.append(&b);
            b
        };
        let fade_btn = button(tr!("Marks fade away by themselves"), Some(glyph("fade", 16).upcast()));
        fade_btn.connect_clicked(act(|d| d.toggle_fade()));
        button(tr!("Undo"), take(undo_b)).connect_clicked(act(|d| d.undo()));
        button(tr!("Clear the screen"), take(trash_b)).connect_clicked(act(|d| d.clear()));
        button(tr!("Stop drawing (Esc)"), Some(glyph("close", 16).upcast())).connect_clicked(act(|d| d.set_on(false, None)));
        let frame = gtk::Box::builder().css_classes(["draw-palette"]).build();
        frame.append(&bar.root);
        win.set_child(Some(&frame));
        if sonata_shell::layer::available() {
            win.init_layer_shell();
            win.set_namespace(Some(PALETTE_NS));
            win.set_layer(Layer::Overlay);
            win.set_anchor(Edge::Top, true);
            win.set_exclusive_zone(-1);
            win.set_margin(Edge::Top, crate::capture::menubar_height()); // right under the menu bar's pill
            win.set_keyboard_mode(KeyboardMode::None);
        }
        let p = Rc::new(Palette { win, frame, fade_btn, _bar: bar });
        let (w, o) = (Rc::downgrade(&p), Rc::downgrade(owner));
        p.win.connect_map(move |_| {
            let (w, o) = (w.clone(), o.clone());
            glib::timeout_add_local_once(Duration::from_millis(50), move || {
                if let Some(p) = w.upgrade() {
                    p.input();
                }
                if let Some(d) = o.upgrade() {
                    d.regions();
                }
            });
        });
        p
    }

    /// The palette itself (x, y, w, h) inside its surface, without the room its shadow takes.
    fn frame_rect(&self) -> Rect {
        match self.frame.compute_bounds(&self.win) {
            Some(r) if r.width() > 0.0 => (r.x() as i32, r.y() as i32, r.width() as i32 + 1, r.height() as i32 + 1),
            _ => (0, 0, self.win.width(), self.win.height()),
        }
    }

    /// Clicks on the palette only: the shadow's margins let them through
    /// to the drawing (or the apps) under them.
    fn input(&self) {
        if sonata_shell::layer::available() && self.win.surface().is_some() {
            let (x, y, w, h) = self.frame_rect();
            sonata_shell::layer::set_input_region(&self.win, &[(x as f64, y as f64, w as f64, h as f64)]);
        }
    }

    fn show_on(&self, monitor: Option<&gdk::Monitor>, fade: bool) {
        if let Some(m) = monitor {
            if self.win.is_layer_window() {
                self.win.set_monitor(Some(m));
            }
        }
        if fade {
            self.fade_btn.add_css_class("on");
        } else {
            self.fade_btn.remove_css_class("on");
        }
        self.win.present();
    }
}

pub struct LiveDraw {
    app: gtk::Application,
    on: Cell<bool>,
    fade: Cell<bool>,
    /// connector -> Overlay
    overlays: RefCell<Vec<(String, Rc<Overlay>)>>,
    /// the palette's settings live on a layer of no display: every
    /// overlay's layer takes its tool, colour and width from it
    proto: Rc<MarkupLayer>,
    palette: RefCell<Option<Rc<Palette>>>,
    /// fn(on): the pills' pens
    listeners: RefCell<Vec<Listener>>,
    next_listener: Cell<u64>,
    fade_src: RefCell<Option<glib::SourceId>>,
    /// the display showing the palette
    palette_on: RefCell<Option<gdk::Monitor>>,
    /// the overlays in the order their marks were made (Undo)
    history: RefCell<Vec<String>>,
    /// our own undo / clear: not a new mark
    quiet: Cell<bool>,
    me: Weak<LiveDraw>,
}

thread_local! {
    static INSTANCE: RefCell<Option<Rc<LiveDraw>>> = const { RefCell::new(None) };
}

/// The process' one LiveDraw.
pub fn get(app: &gtk::Application) -> Rc<LiveDraw> {
    INSTANCE.with(|i| i.borrow_mut().get_or_insert_with(|| LiveDraw::new(app)).clone())
}

impl LiveDraw {
    fn new(app: &gtk::Application) -> Rc<Self> {
        sonata_ui::css::register_module("shell/livedraw.py");
        Rc::new_cyclic(|me| LiveDraw {
            app: app.clone(),
            on: Cell::new(false),
            fade: Cell::new(false),
            overlays: RefCell::default(),
            proto: MarkupLayer::new(|| None, || None),
            palette: RefCell::new(None),
            listeners: RefCell::default(),
            next_listener: Cell::new(0),
            fade_src: RefCell::new(None),
            palette_on: RefCell::new(None),
            history: RefCell::default(),
            quiet: Cell::new(false),
            me: me.clone(),
        })
    }

    pub fn is_on(&self) -> bool {
        self.on.get()
    }

    pub fn add_listener(&self, f: Rc<dyn Fn(bool)>) -> u64 {
        let id = self.next_listener.get() + 1;
        self.next_listener.set(id);
        self.listeners.borrow_mut().push((id, f));
        id
    }

    pub fn remove_listener(&self, id: u64) {
        self.listeners.borrow_mut().retain(|(i, _)| *i != id);
    }

    fn overlays(&self) -> Vec<Rc<Overlay>> {
        self.overlays.borrow().iter().map(|(_, o)| o.clone()).collect()
    }

    // -- the palette's choices: each display's layer takes them before a mark ----------------------
    fn sync(&self, lay: &MarkupLayer) {
        let p = &self.proto;
        let tool = p.tool.borrow().clone();
        if *lay.tool.borrow() != tool {
            lay.set_tool(&tool);
        }
        *lay.shape.borrow_mut() = p.shape.borrow().clone();
        *lay.emoji.borrow_mut() = p.emoji.borrow().clone();
        *lay.color.borrow_mut() = p.color.borrow().clone();
        lay.width.set(p.width.get());
    }

    // -- on / off -----------------------------------------------------------------------------------
    fn monitor(&self, output: Option<&str>) -> Option<gdk::Monitor> {
        let mons = sonata_shell::layer::monitors();
        mons.iter().find(|m| Some(sonata_shell::layer::connector(m).as_str()) == output).or(mons.first()).cloned()
    }

    /// The overlays' input again (the palette's real size is known now).
    fn regions(&self) {
        for ov in self.overlays() {
            ov.pointer(self.on.get());
        }
    }

    pub fn toggle(&self, output: Option<String>) {
        self.set_on(!self.on.get(), output);
    }

    pub fn set_on(&self, on: bool, output: Option<String>) {
        let Some(me) = self.me.upgrade() else { return };
        self.on.set(on);
        if on {
            for m in sonata_shell::layer::monitors() {
                let mut key = sonata_shell::layer::connector(&m);
                if key.is_empty() {
                    key = format!("{:p}", m.as_ptr());
                }
                let existing = self.overlays.borrow().iter().find(|(k, _)| *k == key).map(|(_, o)| o.clone());
                let ov = existing.unwrap_or_else(|| {
                    let ov = Overlay::new(&self.app, &m, key.clone(), &me);
                    self.overlays.borrow_mut().push((key, ov.clone()));
                    ov
                });
                self.sync(&ov.layer);
                ov.win.present();
                ov.pointer(true);
            }
            let pal = self.palette.borrow_mut().get_or_insert_with(|| Palette::new(&self.app, &me)).clone();
            *self.palette_on.borrow_mut() = self.monitor(output.as_deref());
            pal.show_on(self.palette_on.borrow().as_ref(), self.fade.get());
            self.regions(); // (the overlays above asked before it was placed)
        } else {
            for ov in self.overlays() {
                ov.pointer(false);
                if ov.layer.items.borrow().is_empty() {
                    ov.win.set_visible(false); // nothing drawn: nothing left over the screen
                }
            }
            if let Some(p) = self.palette.borrow().as_ref() {
                p.win.set_visible(false);
            }
        }
        let fns: Vec<_> = self.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
        for f in fns {
            f(on);
        }
        // whatever mapped since
        let w = self.me.clone();
        glib::timeout_add_local_once(Duration::from_millis(SETTLE_MS), move || {
            if let Some(d) = w.upgrade() {
                d.regions();
            }
        });
    }

    /// The recording or sharing ended: off, and the screen clean.
    pub fn stop(&self) {
        self.clear();
        self.set_on(false, None);
    }

    pub fn clear(&self) {
        for ov in self.overlays() {
            if !ov.layer.items.borrow().is_empty() {
                self.quiet.set(true);
                ov.layer.clear(); // (one Undo brings them back)
                self.quiet.set(false);
            }
            if !self.on.get() {
                ov.win.set_visible(false);
            }
        }
    }

    /// The last mark made, on whichever display (it undid one on every display).
    pub fn undo(&self) {
        loop {
            let Some(key) = self.history.borrow_mut().pop() else { return };
            let Some(ov) = self.overlays.borrow().iter().find(|(k, _)| *k == key).map(|(_, o)| o.clone()) else { continue };
            self.quiet.set(true);
            let undone = ov.layer.undo();
            self.quiet.set(false);
            if undone {
                return;
            }
        }
    }

    // -- fading -------------------------------------------------------------------------------------
    pub fn toggle_fade(&self) {
        let fade = !self.fade.get();
        self.fade.set(fade);
        if !fade {
            // off mid-fade: the marks whole again
            for ov in self.overlays() {
                for m in ov.layer.items.borrow_mut().iter_mut() {
                    m.o = 1.0;
                }
                ov.layer.area.queue_draw();
            }
        }
        if let Some(p) = self.palette.borrow().as_ref() {
            if fade {
                p.fade_btn.add_css_class("on");
            } else {
                p.fade_btn.remove_css_class("on");
            }
        }
        if fade {
            let now = now_s();
            for ov in self.overlays() {
                let n = ov.layer.items.borrow().len();
                *ov.stamps.borrow_mut() = vec![now; n];
            }
            self.arm_fade();
        }
    }

    /// A mark was added (or changed): stamped, so it can fade. `key`: the
    /// display it was drawn on (a new step there, for Undo).
    fn changed(&self, key: Option<&str>) {
        if let (Some(k), false) = (key, self.quiet.get()) {
            self.history.borrow_mut().push(k.to_string());
        }
        let now = now_s();
        for ov in self.overlays() {
            let n = ov.layer.items.borrow().len();
            let mut st = ov.stamps.borrow_mut();
            st.truncate(n);
            st.resize(n, now);
        }
        if self.fade.get() {
            self.arm_fade();
        }
    }

    fn arm_fade(&self) {
        if self.fade_src.borrow().is_some() {
            return;
        }
        let w = self.me.clone();
        let src = glib::timeout_add_local(Duration::from_millis(33), move || match w.upgrade() {
            Some(d) if d.fade_tick() => glib::ControlFlow::Continue,
            Some(d) => {
                d.fade_src.borrow_mut().take();
                glib::ControlFlow::Break
            }
            None => glib::ControlFlow::Break,
        });
        *self.fade_src.borrow_mut() = Some(src);
    }

    fn fade_tick(&self) -> bool {
        if !self.fade.get() {
            return false;
        }
        let (now, mut alive) = (now_s(), false);
        for ov in self.overlays() {
            let (items, stamps) = {
                let items = ov.layer.items.borrow();
                let stamps = ov.stamps.borrow();
                let mut keep = (Vec::new(), Vec::new());
                for (i, m) in items.iter().enumerate() {
                    let ts = stamps.get(i).copied().unwrap_or(now);
                    let age = now - ts;
                    if age < FADE_AFTER_S + FADE_S {
                        let mut m = m.clone();
                        m.o = if age < FADE_AFTER_S { 1.0 } else { (1.0 - (age - FADE_AFTER_S) / FADE_S).max(0.0) };
                        keep.0.push(m);
                        keep.1.push(ts);
                    }
                }
                keep
            };
            let any = !items.is_empty();
            let changed = any || items.len() != ov.layer.items.borrow().len();
            if changed {
                *ov.layer.items.borrow_mut() = items;
                *ov.stamps.borrow_mut() = stamps;
                ov.layer.area.queue_draw();
            }
            alive |= any;
            if !any && !self.on.get() {
                ov.win.set_visible(false);
            }
        }
        alive
    }
}

/// The pills' pen: drawing on / off (blue while on).
pub fn pen_button(app: &gtk::Application, output: Rc<dyn Fn() -> Option<String>>) -> gtk::Button {
    pen_button_with(app, output, "rec-pen", 12, None)
}

/// pen_button in another look (the Game Bar's: css, size); start(go) runs
/// before drawing turns on and calls go() when it may (the Game Bar closes
/// first, so the pointer reaches the game).
pub fn pen_button_with(app: &gtk::Application, output: Rc<dyn Fn() -> Option<String>>, css: &str, size: i32, start: Option<StartFn>) -> gtk::Button {
    let b = gtk::Button::builder().css_classes([css]).tooltip_text(tr!("Draw on the screen (Super+Shift+D)")).valign(gtk::Align::Center).can_focus(false).build();
    b.set_child(Some(&glyph("pen", size)));
    let d = get(app);
    let dw = Rc::downgrade(&d);
    let out = output.clone();
    b.connect_clicked(move |_| {
        let Some(d) = dw.upgrade() else { return };
        match &start {
            Some(start) if !d.is_on() => {
                let (dw, out) = (Rc::downgrade(&d), out.clone());
                start(Box::new(move || {
                    if let Some(d) = dw.upgrade() {
                        d.set_on(true, out());
                    }
                }));
            }
            _ => d.toggle(out()),
        }
    });
    // listened to while on screen: a pill's window destroyed unrealizes the
    // pen, which lets go of it (memory review: the shared LiveDraw kept every
    // pen ever made, and its window, through this callback)
    let weak = b.downgrade();
    let follow: Rc<dyn Fn(bool)> = Rc::new(move |on| {
        if let Some(b) = weak.upgrade() {
            if on {
                b.add_css_class("on");
            } else {
                b.remove_css_class("on");
            }
        }
    });
    let id: Rc<Cell<Option<u64>>> = Rc::default();
    let attach = {
        let (d, id, follow) = (Rc::downgrade(&d), id.clone(), follow.clone());
        move || {
            let Some(d) = d.upgrade() else { return };
            if id.get().is_none() {
                id.set(Some(d.add_listener(follow.clone())));
            }
            follow(d.is_on());
        }
    };
    attach();
    b.connect_realize(move |_| attach());
    let dw = Rc::downgrade(&d);
    b.connect_unrealize(move |_| {
        if let (Some(d), Some(i)) = (dw.upgrade(), id.take()) {
            d.remove_listener(i);
        }
    });
    b
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: drawing didn't work, the screen stayed clickable -- the input
    /// region was taken from the window's size at map (0 x 0). It comes from
    /// the display: all of it while drawing (less the menu bar and the
    /// palette), nothing when off.
    #[test]
    fn test_input_region_covers_the_display() {
        let rects = input_rects(true, (1920, 1080), 32, true, None);
        assert!(input_rects(false, (1920, 1080), 32, true, None).is_empty());
        // the palette on another display: all of this one
        assert_eq!(input_rects(true, (1920, 1080), 32, false, None), [(0, 32, 1920, 1080 - 32)]);
        let (pw, ph) = PALETTE_SIZE;
        assert!(rects.contains(&(0, 32 + ph, 1920, 1080 - 32 - ph))); // everything under the palette
        let area: i32 = rects.iter().map(|r| r.2 * r.3).sum();
        assert_eq!(area, 1920 * (1080 - 32) - pw * ph); // all but the menu bar and palette
    }

    /// Only the palette itself is a hole: its shadow's margins still draw.
    #[test]
    fn test_palette_shadow_still_draws() {
        let rects = input_rects(true, (1000, 800), 24, true, Some(((600, 100), (18, 12, 564, 64))));
        let area: i32 = rects.iter().map(|r| r.2 * r.3).sum();
        assert_eq!(area, 1000 * (800 - 24) - 564 * 64);
        assert!(rects.contains(&(0, 24, 1000, 12))); // above the palette: its top margin
    }
}
