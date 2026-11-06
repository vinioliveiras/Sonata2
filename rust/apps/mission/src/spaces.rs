//! Desktops in Mission Control (shell/spaces.py; Vini: Win+Tab shows the
//! windows like Mission Control, and more desktops can be made, as on
//! Windows).
//!
//! Wayfire's `scale` lays the windows out (Super+Tab, Ctrl+Up, F3, three
//! fingers up); while it does, this bar sits centred at the top of the
//! display: one small picture per desktop (the wallpaper, the icons of the
//! apps on it), its name under it -- the current one in a glass pill -- and
//! a "+" beside them, centred on the pictures. Always there while Mission
//! Control is (Vini: not only under the pointer), in room scale keeps free
//! for it ([scale] outer_margin = SCALE_MARGIN): it never covers a window.
//!
//! Click a desktop to go there (Mission Control closes, as on macOS); +
//! adds one; x on a picture (under the pointer) closes one -- its windows
//! go to the desktop before it, and desktop 1's apps close instead (there
//! is none before it). One desktop to start with (core/vwidth = 1); the
//! ones you add are kept in Sonata's Wayfire config.
//!
//! Switching: scale holds the output, so vswitch can't animate a switch
//! while it's open -- going to a desktop closes Mission Control first
//! (scale/toggle), then vswitch slides there; closing a desktop moves the
//! windows with vswitch/send-view and keeps Mission Control open on the
//! right one (sonata/set-workspace, Sonata's plugin; without it, the
//! vswitch way). What talks to Wayfire is plain functions over `Ipc`,
//! tested with a fake one.

use crate::Ipc;
use adw::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, graphene, gsk};
use serde_json::{json, Value};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

pub const MAX_DESKTOPS: i64 = 9;
pub const THUMB_W: i32 = 128;
pub const THUMB_H: i32 = 80;
pub const ICONS_PER_DESKTOP: usize = 5;
/// below the menu bar, and below the bar
pub const BAR_GAP: i32 = 10;
pub const PILL_H: i32 = 22;
pub const ADD_SIZE: i32 = 30;
/// room around a picture for its ring (the bar would clip it)
pub const RING: i32 = 4;
/// a new desktop grows in
pub const APPEAR_MS: u64 = 240;
/// a closed one shrinks away, then the others glide over
pub const VANISH_MS: u64 = 180;
pub const BAR_H: i32 = BAR_GAP + RING + THUMB_H + RING + 4 + PILL_H;
/// what scale keeps free around the windows (config/wayfire.ini [scale]
/// outer_margin): the whole bar and a gap under it, so it never covers a window
pub const SCALE_MARGIN: i32 = BAR_H + BAR_GAP;
/// ui/transition.GLIDE_MS
const GLIDE_MS: u32 = 260;

// -- the arithmetic ---------------------------------------------------------------------

fn num(v: &Value, k: &str) -> f64 {
    v.get(k).and_then(Value::as_f64).unwrap_or(0.0)
}

/// Which desktop (column) a window is on: its centre, from the current
/// one, in display widths.
pub fn desktop_of(view: &Value, out: &Value) -> i64 {
    let (g, og) = (&view["geometry"], &out["geometry"]);
    let width = Some(num(og, "width")).filter(|w| *w != 0.0).unwrap_or(1.0);
    let cur = out["workspace"]["x"].as_i64().unwrap_or(0);
    let centre = num(g, "x") + num(g, "width") / 2.0;
    cur + (centre / width).floor() as i64
}

/// Closing desktop `index` of `count` (views: [(view id, desktop)]):
/// ([(view id, its desktop after)], the desktop to show, the new count).
/// Its windows go to the one before it (the first's: to the next one,
/// which becomes the first); the desktops after it move one to the left.
/// Every window goes straight to where it ends, then the last column goes.
pub fn plan_close(index: i64, count: i64, views: &[(i64, i64)], mut current: i64) -> (Vec<(i64, i64)>, i64, i64) {
    if count <= 1 || !(0..count).contains(&index) {
        return (Vec::new(), current, count);
    }
    let mut moves = Vec::new();
    for &(vid, desk) in views {
        if desk == index {
            moves.push((vid, (index - 1).max(0)));
        } else if desk > index {
            moves.push((vid, desk - 1));
        }
    }
    if current > index || (current == index && index > 0) {
        current -= 1;
    }
    (moves, current.min(count - 2).max(0), count - 1)
}

/// How far down the display the bar starts: the top of the work area
/// (under the menu bar). Wayfire's list-outputs gives "workarea" in the
/// display's own coordinates (get_workarea() is cut from the output's
/// relative geometry) while "geometry" is in the layout's, so the two are
/// never to be subtracted: on a second display at y 201 that put the bar at
/// the very top, over the menu bar. The numbers come as floats (32.0):
/// as_i64 read them as nothing, and the bar sat at 0 on every display.
pub fn bar_top(out: &Value) -> i32 {
    num(&out["workarea"], "y").max(0.0) as i32
}

/// (desktops, the current one) of an output.
pub fn workspace(out: &Value) -> (i64, i64) {
    let ws = &out["workspace"];
    (ws["grid_width"].as_i64().unwrap_or(1).max(1), ws["x"].as_i64().unwrap_or(0))
}

/// Each desktop's app ids (once each, in the order found), on `out`.
pub fn per_desktop(out: &Value, views: &[Value], count: i64) -> Vec<Vec<String>> {
    let mut per = vec![Vec::new(); count.max(0) as usize];
    for v in views.iter().filter(|v| v.get("output-id") == out.get("id")) {
        let d = desktop_of(v, out);
        let aid = v["app-id"].as_str().unwrap_or("").to_string();
        if let Some(list) = per.get_mut(d.max(0) as usize).filter(|_| d >= 0) {
            if !list.contains(&aid) {
                list.push(aid);
            }
        }
    }
    per
}

// -- Wayfire --------------------------------------------------------------------------------

pub fn outputs(ipc: &dyn Ipc) -> Vec<Value> {
    ipc.call("window-rules/list-outputs", json!({})).and_then(|v| v.as_array().cloned()).unwrap_or_default()
}

/// The windows that are on a desktop (not sticky ones, not panels).
pub fn views(ipc: &dyn Ipc) -> Vec<Value> {
    let got = ipc.call("window-rules/list-views", json!({})).and_then(|v| v.as_array().cloned()).unwrap_or_default();
    got.into_iter().filter(|v| v["type"] == "toplevel" && v.get("mapped").and_then(Value::as_bool).unwrap_or(true) && !v.get("sticky").and_then(Value::as_bool).unwrap_or(false)).collect()
}

pub fn find_output(ipc: &dyn Ipc, connector: &str) -> Option<Value> {
    outputs(ipc).into_iter().find(|o| o["name"] == connector)
}

/// That desktop, and out of Mission Control (macOS). Scale closes first:
/// while it holds the output vswitch can't switch (Vini: the desktops did
/// nothing when clicked).
pub fn go(ipc: &dyn Ipc, connector: &str, i: i64) {
    let Some(out) = find_output(ipc, connector) else { return };
    ipc.call("scale/toggle", json!({"output_id": out["id"]})); // (it's open: this closes it)
    if i != workspace(&out).1 {
        ipc.call("vswitch/set-workspace", json!({"x": i, "y": 0, "output-id": out["id"]}));
    }
}

/// Show desktop x, Mission Control staying open on it: Sonata's plugin
/// sets the workspace itself; without it, close and slide.
pub fn switch_here(ipc: &dyn Ipc, out: &Value, x: i64) {
    let got = ipc.call("sonata/set-workspace", json!({"x": x, "y": 0, "output-id": out["id"]}));
    if got.as_ref().and_then(|g| g.get("result")).and_then(Value::as_str) != Some("ok") {
        ipc.call("scale/toggle", json!({"output_id": out["id"]}));
        ipc.call("vswitch/set-workspace", json!({"x": x, "y": 0, "output-id": out["id"]}));
    }
}

/// How many desktops (Sonata's Wayfire config: the ones you add are kept).
pub fn set_count(n: i64) {
    sonata_backend::wfconfig::set("core", "vwidth", &n.clamp(1, MAX_DESKTOPS).to_string());
    sonata_backend::wfconfig::set("core", "vheight", "1");
}

/// What closing a desktop left to do on screen.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Closed {
    Nothing,
    /// the only desktop: its apps asked to close, it stays
    RefreshSoon(u64),
    /// `count` desktops now, `removed` gone (the others glide over)
    Changed { count: i64, removed: i64 },
}

/// Close desktop `index`: its windows to the one before, the ones after
/// one over, the last column gone (set_count).
pub fn close_desktop(ipc: &dyn Ipc, connector: &str, index: i64, set_count: &dyn Fn(i64)) -> Closed {
    let Some(out) = find_output(ipc, connector) else { return Closed::Nothing };
    let (count, current) = (out["workspace"]["grid_width"].as_i64().unwrap_or(1), workspace(&out).1);
    let mut views: Vec<(i64, i64)> = views(ipc).iter().filter(|v| v.get("output-id") == out.get("id")).filter_map(|v| Some((v["id"].as_i64()?, desktop_of(v, &out)))).collect();
    if index == 0 {
        // Desktop 1 has none before it: its apps close (Vini) -- each may
        // still ask to save; one that stays goes on to the next desktop
        for &(vid, desk) in &views {
            if desk == 0 {
                ipc.call("window-rules/close-view", json!({"id": vid}));
            }
        }
        views.retain(|&(_, desk)| desk != 0);
        if count <= 1 {
            return Closed::RefreshSoon(300);
        }
    }
    let (moves, after, new_count) = plan_close(index, count, &views, current);
    if new_count == count {
        return Closed::Nothing;
    }
    for (vid, desk) in moves {
        // every window straight to where it ends
        ipc.call("vswitch/send-view", json!({"view-id": vid, "x": desk, "y": 0}));
    }
    if after != current {
        switch_here(ipc, &out, after); // (before the last column goes: still valid)
    }
    set_count(new_count); // the last column goes, empty now
    Closed::Changed { count: new_count, removed: index }
}

/// Where a window dragged in Mission Control was dropped.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    Desktop(i64),
    /// onto +
    Add,
    None,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dropped {
    Nothing,
    /// onto +: a new desktop, and the window goes there once it exists
    NewDesktop { view: i64 },
    Sent,
}

/// Dropped on a desktop: the window goes there (Vini).
pub fn drop_window(ipc: &dyn Ipc, connector: &str, view_id: i64, target: Target) -> Dropped {
    let Some(out) = find_output(ipc, connector) else { return Dropped::Nothing };
    let Some(view) = views(ipc).into_iter().find(|v| v["id"].as_i64() == Some(view_id)) else { return Dropped::Nothing };
    match target {
        Target::Add => Dropped::NewDesktop { view: view_id },
        Target::Desktop(d) if desktop_of(&view, &out) != d => {
            ipc.call("vswitch/send-view", json!({"view-id": view_id, "x": d, "y": 0}));
            Dropped::Sent
        }
        _ => Dropped::Nothing,
    }
}

/// The entry behind a window's app id, for its icon. A window no entry
/// matches is asked of GIO alone: apps::lookup reads every .desktop file
/// again on a miss (~5 ms each), for each such window at every open --
/// match_app_id has just looked through them all.
pub fn app_info(app_id: &str) -> Option<gio::DesktopAppInfo> {
    match sonata_desktop::appmatch::match_app_id(app_id) {
        Some(did) => sonata_ui::apps::lookup(&did),
        None => gio::DesktopAppInfo::new(&format!("{app_id}.desktop")),
    }
}

/// The name under a picture (macOS: one is just "Desktop").
pub fn desktop_name(i: i64, count: i64) -> String {
    if count > 1 { sonata_i18n::fmt(&tr!("Desktop {n}"), &[("n", &(i + 1).to_string())]) } else { tr!("Desktop") }
}

/// The side of the user's window buttons (ui/window.close_align: Vini,
/// "my buttons are all on the left"): true for the right.
pub fn close_at_end() -> bool {
    // (stored(), not load() with no defaults: load keeps only the keys its
    // defaults have, so buttons_side never got through)
    end_side(&sonata_config::stored("appearance"))
}

/// appearance.json's "buttons_side" is "right" (anything else: macOS's left).
pub fn end_side(appearance: &serde_json::Map<String, Value>) -> bool {
    appearance.get("buttons_side").and_then(Value::as_str) == Some("right")
}

// -- a desktop's picture --------------------------------------------------------------------

mod thumb_imp {
    use super::*;

    #[derive(Default)]
    pub struct Thumb {
        pub texture: RefCell<Option<gdk::Texture>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Thumb {
        const NAME: &'static str = "SonataRsSpacesThumb";
        type Type = super::Thumb;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Thumb {}

    impl WidgetImpl for Thumb {
        fn measure(&self, o: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            let n = if o == gtk::Orientation::Horizontal { THUMB_W } else { THUMB_H };
            (n, n, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let Some(p) = self.texture.borrow().clone() else { return };
            let obj = self.obj();
            let (w, h) = (obj.width() as f32, obj.height() as f32);
            let (pw, ph) = (p.width() as f32, p.height() as f32);
            let k = (w / pw.max(1.0)).max(h / ph.max(1.0));
            let (sw, sh) = (pw * k, ph * k);
            snap.push_rounded_clip(&gsk::RoundedRect::from_rect(graphene::Rect::new(0.0, 0.0, w, h), 8.0));
            snap.append_texture(&p, &graphene::Rect::new((w - sw) / 2.0, (h - sh) / 2.0, sw, sh));
            snap.pop();
        }
    }
}

glib::wrapper! {
    /// A desktop's picture: the wallpaper cropped to THUMB_W x THUMB_H
    /// (cover), rounded -- a fixed size, whatever the wallpaper's (a
    /// Gtk.Picture asks for the image's own proportions and stretches the row).
    pub struct Thumb(ObjectSubclass<thumb_imp::Thumb>) @extends gtk::Widget;
}

impl Thumb {
    pub fn new(texture: Option<gdk::Texture>) -> Self {
        let t: Self = glib::Object::builder().property("css-classes", vec!["sp-thumb".to_string()]).property("overflow", gtk::Overflow::Hidden).build();
        t.imp().texture.replace(texture);
        t
    }
}

// -- a box whose children glide to their new places (the part of ui/transition.py it uses) --

mod glide_imp {
    use super::*;

    pub type Offset = (gtk::Widget, f64, f64, Option<adw::TimedAnimation>);

    #[derive(Default)]
    pub struct GlideBox {
        /// (child, its offset now, its glide)
        pub offsets: RefCell<Vec<Offset>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for GlideBox {
        const NAME: &'static str = "SonataRsSpacesGlideBox";
        type Type = super::GlideBox;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for GlideBox {}
    impl BoxImpl for GlideBox {}

    impl WidgetImpl for GlideBox {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let mut child = obj.first_child();
            while let Some(c) = child {
                let (dx, dy) = obj.offset(&c);
                if dx != 0.0 || dy != 0.0 {
                    snap.save();
                    snap.translate(&graphene::Point::new(dx as f32, dy as f32));
                    obj.snapshot_child(&c, snap);
                    snap.restore();
                } else {
                    obj.snapshot_child(&c, snap);
                }
                child = c.next_sibling();
            }
        }
    }
}

glib::wrapper! {
    pub struct GlideBox(ObjectSubclass<glide_imp::GlideBox>) @extends gtk::Box, gtk::Widget, @implements gtk::Orientable;
}

impl GlideBox {
    pub fn new(spacing: i32) -> Self {
        glib::Object::builder().property("spacing", spacing).build()
    }

    pub fn offset(&self, child: &gtk::Widget) -> (f64, f64) {
        self.imp().offsets.borrow().iter().find(|(w, ..)| w == child).map_or((0.0, 0.0), |(_, x, y, _)| (*x, *y))
    }

    /// `child` drawn (dx, dy) away from its place, gliding home.
    fn start(&self, child: &gtk::Widget, dx: f64, dy: f64, ms: u32) {
        {
            let mut offs = self.imp().offsets.borrow_mut();
            offs.retain(|(w, .., anim)| {
                let keep = w.parent().as_ref() == Some(self.upcast_ref()) && w != child;
                if !keep {
                    if let Some(a) = anim {
                        a.pause();
                    }
                }
                keep
            });
            offs.push((child.clone(), dx, dy, None));
        }
        let (weak, c) = (self.downgrade(), child.clone());
        let target = adw::CallbackAnimationTarget::new(move |v| {
            if let Some(me) = weak.upgrade() {
                if let Some(e) = me.imp().offsets.borrow_mut().iter_mut().find(|(w, ..)| *w == c) {
                    (e.1, e.2) = (dx * v, dy * v);
                }
                me.queue_draw();
            }
        });
        let anim = adw::TimedAnimation::new(child, 1.0, 0.0, sonata_ui::tokens::ms(ms as f64), target);
        anim.set_easing(adw::Easing::EaseOutCubic);
        if let Some(e) = self.imp().offsets.borrow_mut().iter_mut().find(|(w, ..)| w == child) {
            e.3 = Some(anim.clone());
        }
        anim.play();
    }
}

/// Where each widget is drawn now (layout + running glide), in
/// `reference`'s coordinates, with its parent. Call before re-ordering.
pub fn glide_record(widgets: &[gtk::Widget], reference: &gtk::Widget) -> Vec<(gtk::Widget, f64, f64, Option<gtk::Widget>)> {
    widgets
        .iter()
        .filter(|w| w.is_mapped())
        .filter_map(|w| {
            let p = w.compute_point(reference, &graphene::Point::new(0.0, 0.0))?;
            let (dx, dy) = w.parent().and_downcast::<GlideBox>().map_or((0.0, 0.0), |g| g.offset(w));
            Some((w.clone(), p.x() as f64 + dx, p.y() as f64 + dy, w.parent()))
        })
        .collect()
}

/// After re-ordering: every widget recorded that moved (and kept its
/// parent, a GlideBox) slides from its old place to the new one. Runs in
/// the layout phase of the next frame, so the new place is never shown first.
pub fn glide_play(before: Vec<(gtk::Widget, f64, f64, Option<gtk::Widget>)>, reference: &gtk::Widget) {
    let Some(clock) = reference.frame_clock() else { return };
    if before.is_empty() {
        return;
    }
    let id: Rc<RefCell<Option<glib::SignalHandlerId>>> = Rc::default();
    let (id2, r) = (id.clone(), reference.downgrade());
    let handler = clock.connect_layout(move |clock| {
        if let Some(h) = id2.borrow_mut().take() {
            clock.disconnect(h);
        }
        let Some(r) = r.upgrade() else { return };
        for (w, ox, oy, parent) in &before {
            if w.parent() != *parent || !w.is_mapped() {
                continue;
            }
            let (Some(p), Some(g)) = (w.compute_point(&r, &graphene::Point::new(0.0, 0.0)), parent.as_ref().and_then(|p| p.downcast_ref::<GlideBox>())) else { continue };
            let (dx, dy) = (ox - p.x() as f64, oy - p.y() as f64);
            if dx.abs() >= 0.5 || dy.abs() >= 0.5 {
                g.start(w, dx, dy, GLIDE_MS);
            }
        }
    });
    id.replace(Some(handler));
    reference.queue_allocate();
}

// -- the bar --------------------------------------------------------------------------------

type Glide = (i64, Rc<dyn Fn(usize) -> Option<usize>>);

/// One display's desktops bar (shown while scale lays its windows out).
pub struct SpacesBar {
    pub win: gtk::Window,
    row: GlideBox,
    desks: GlideBox,
    add: gtk::Button,
    connector: String,
    pub wallpaper: RefCell<Option<gdk::Texture>>,
    ipc: Rc<dyn Ipc>,
    set_count: Rc<dyn Fn(i64)>,
    count: Cell<i64>,
    monitor: Option<gdk::Monitor>,
    top: Cell<i32>,
    /// (the count it's for, new index -> old index or None): refresh()
    glide: RefCell<Option<Glide>>,
    soon: RefCell<Option<glib::SourceId>>,
    layered: bool,
}

impl SpacesBar {
    /// `host`: a window and overlay to show the bar in (previews) instead
    /// of a layer surface of its own.
    pub fn new(app: &gtk::Application, monitor: Option<gdk::Monitor>, connector: &str, wallpaper: Option<gdk::Texture>, ipc: Rc<dyn Ipc>, set_count: Rc<dyn Fn(i64)>, host: Option<(gtk::Window, gtk::Overlay)>) -> Rc<SpacesBar> {
        let row = GlideBox::new(14);
        row.set_halign(gtk::Align::Center);
        row.set_valign(gtk::Align::Start);
        row.add_css_class("sp-row");
        let desks = GlideBox::new(14);
        row.append(&desks);
        let add = gtk::Button::builder().icon_name("list-add-symbolic").css_classes(["sp-add"]).valign(gtk::Align::Start).tooltip_text(tr!("New Desktop")).can_focus(false).margin_top(RING + (THUMB_H - ADD_SIZE) / 2).build(); // centred on the pictures
        row.append(&add);
        let mut layered = false;
        let win = match host {
            Some((win, over)) => {
                over.add_overlay(&row);
                win
            }
            None => {
                let win = gtk::Window::builder().application(app).title("Desktops").decorated(false).resizable(false).build();
                win.add_css_class("sonata-spaces");
                win.set_child(Some(&row));
                if sonata_shell::layer::available() {
                    use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
                    layered = true;
                    win.init_layer_shell();
                    win.set_namespace(Some("sonata2-spaces"));
                    win.set_layer(Layer::Overlay);
                    // as wide as the display, the row centred in it: a desktop added or
                    // closed doesn't move the surface, so the others glide in place
                    for e in [Edge::Top, Edge::Left, Edge::Right] {
                        win.set_anchor(e, true);
                    }
                    win.set_resizable(true); // (else it kept its own width, at the left)
                    win.set_exclusive_zone(-1);
                    win.set_keyboard_mode(KeyboardMode::None);
                    if let Some(m) = &monitor {
                        win.set_monitor(Some(m));
                    }
                }
                win
            }
        };
        let me = Rc::new(SpacesBar {
            win,
            row,
            desks,
            add,
            connector: connector.to_string(),
            wallpaper: RefCell::new(wallpaper),
            ipc,
            set_count,
            count: Cell::new(1),
            monitor,
            top: Cell::new(0),
            glide: RefCell::default(),
            soon: RefCell::default(),
            layered,
        });
        let weak = Rc::downgrade(&me);
        me.add.connect_clicked(move |_| {
            if let Some(me) = weak.upgrade() {
                me.add_desktop(me.count.get(), None);
            }
        });
        me
    }

    pub fn is_visible(&self) -> bool {
        self.win.is_visible()
    }

    pub fn hide(&self) {
        if self.layered {
            self.win.set_visible(false);
        } else {
            self.row.set_visible(false);
        }
    }

    pub fn show_bar(self: &Rc<Self>) {
        self.refresh();
        if self.layered {
            if let Some(out) = find_output(&*self.ipc, &self.connector) {
                use gtk4_layer_shell::{Edge, LayerShell};
                self.top.set(bar_top(&out));
                self.win.set_margin(Edge::Top, self.top.get());
            }
        }
        self.row.set_visible(true);
        self.row.remove_css_class("opening");
        self.win.present();
        self.row.add_css_class("opening");
    }

    pub fn children(&self) -> Vec<gtk::Widget> {
        let mut out = Vec::new();
        let mut c = self.desks.first_child();
        while let Some(w) = c {
            c = w.next_sibling();
            out.push(w);
        }
        out
    }

    pub fn refresh(self: &Rc<Self>) {
        let out = find_output(&*self.ipc, &self.connector);
        let (count, current) = out.as_ref().map_or((1, 0), workspace);
        let old = self.children();
        let mut glide = None;
        let mut before = Vec::new();
        if self.glide.borrow().as_ref().is_some_and(|(c, _)| *c == count) {
            // (the change it was for is in)
            glide = self.glide.take().map(|g| g.1);
            let mut ws = old.clone();
            ws.push(self.add.clone().upcast());
            before = glide_record(&ws, self.win.upcast_ref()); // (the row re-centres: the window)
        }
        while let Some(c) = self.desks.first_child() {
            self.desks.remove(&c);
        }
        let Some(out) = out else { return };
        self.count.set(count);
        let per = per_desktop(&out, &views(&*self.ipc), count);
        for (i, apps) in per.iter().enumerate() {
            let d = self.desktop(i as i64, i as i64 == current, apps, count);
            self.desks.append(&d);
        }
        self.add.set_visible(count < MAX_DESKTOPS);
        let Some(glide) = glide else { return };
        let was = |w: &gtk::Widget| before.iter().find(|(b, ..)| b == w).map(|(_, x, y, _)| (*x, *y));
        let mut moved = Vec::new();
        if let Some((x, y)) = was(self.add.upcast_ref()) {
            moved.push((self.add.clone().upcast(), x, y, Some(self.row.clone().upcast())));
        }
        for (i, b) in self.children().into_iter().enumerate() {
            match glide(i) {
                None => {
                    // the new one
                    b.add_css_class("appearing");
                    let weak = b.downgrade();
                    glib::timeout_add_local_once(Duration::from_millis(APPEAR_MS + 50), move || {
                        if let Some(b) = weak.upgrade() {
                            b.remove_css_class("appearing");
                        }
                    });
                }
                Some(j) => {
                    if let Some((x, y)) = old.get(j).and_then(was) {
                        // from where the one it replaces was
                        moved.push((b, x, y, Some(self.desks.clone().upcast())));
                    }
                }
            }
        }
        glide_play(moved, self.win.upcast_ref());
    }

    fn desktop(self: &Rc<Self>, i: i64, current: bool, app_ids: &[String], count: i64) -> gtk::Widget {
        let over = gtk::Overlay::builder().halign(gtk::Align::Center).margin_start(RING).margin_end(RING).margin_top(RING).margin_bottom(RING).build();
        over.set_child(Some(&Thumb::new(self.wallpaper.borrow().clone())));
        let icons = gtk::Box::builder().spacing(3).halign(gtk::Align::Center).valign(gtk::Align::End).css_classes(["sp-icons"]).build();
        for aid in app_ids.iter().take(ICONS_PER_DESKTOP) {
            let img = gtk::Image::builder().pixel_size(18).build();
            match app_info(aid) {
                Some(info) => sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(info.upcast_ref())),
                None => img.set_icon_name(Some("application-x-executable")),
            }
            icons.append(&img);
        }
        over.add_overlay(&icons);
        let label = gtk::Label::builder().label(desktop_name(i, count)).css_classes(["sp-name"]).halign(gtk::Align::Center).build();
        crate::glass_class(&label);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&over);
        col.append(&label);
        let b = gtk::Button::builder().child(&col).css_classes(["sp-desk"]).can_focus(false).build();
        if current {
            b.add_css_class("current");
        }
        let weak = Rc::downgrade(self);
        b.connect_clicked(move |_| {
            if let Some(me) = weak.upgrade() {
                go(&*me.ipc, &me.connector, i);
                me.refresh_soon(120); // (Mission Control stays open on a preview)
            }
        });
        let boxed = gtk::Overlay::builder().css_classes(["sp-desk-box"]).build(); // (the x outside the button: its own click)
        boxed.set_child(Some(&b));
        if count > 1 || (i == 0 && !app_ids.is_empty()) {
            // (the first one alone: x closes its apps)
            let end = close_at_end(); // (the side of the user's window buttons: Vini)
            let x = gtk::Button::builder()
                .icon_name("window-close-symbolic")
                .halign(if end { gtk::Align::End } else { gtk::Align::Start })
                .valign(gtk::Align::Start)
                .css_classes(["sp-close"])
                .tooltip_text(tr!("Close Desktop"))
                .can_focus(false)
                .build();
            if end {
                x.add_css_class("end");
            }
            let weak = Rc::downgrade(self);
            x.connect_clicked(move |_| {
                if let Some(me) = weak.upgrade() {
                    me.close_desktop(i);
                }
            });
            boxed.add_overlay(&x);
        }
        boxed.upcast()
    }

    /// The desktop index of the bar's widget `widget` is in, or None.
    fn desktop_at(&self, widget: Option<gtk::Widget>) -> Option<i64> {
        let mut w = widget;
        while let Some(x) = w {
            let parent = x.parent();
            if parent.as_ref() == Some(self.desks.upcast_ref()) {
                return self.children().iter().position(|c| *c == x).map(|i| i as i64);
            }
            w = parent;
        }
        None
    }

    // -- actions ------------------------------------------------------------------------
    /// A new desktop after the `count` there are; then() once Wayfire has it.
    pub fn add_desktop(self: &Rc<Self>, count: i64, then: Option<Box<dyn FnOnce()>>) {
        self.glide.replace(Some((count + 1, Rc::new(move |i| (i < count as usize).then_some(i)))));
        (self.set_count)(count + 1);
        self.refresh_when(count + 1, then);
    }

    /// It shrinks away first (VANISH_MS), then goes; the others glide over.
    pub fn close_desktop(self: &Rc<Self>, index: i64) {
        let boxes = self.children();
        if (0..boxes.len() as i64).contains(&index) && boxes.len() > 1 {
            boxes[index as usize].add_css_class("vanishing");
            boxes[index as usize].set_sensitive(false);
            let weak = Rc::downgrade(self);
            glib::timeout_add_local_once(Duration::from_millis(VANISH_MS), move || {
                if let Some(me) = weak.upgrade() {
                    me.close_now(index);
                }
            });
        } else {
            self.close_now(index);
        }
    }

    fn close_now(self: &Rc<Self>, index: i64) {
        match close_desktop(&*self.ipc, &self.connector, index, &*self.set_count) {
            Closed::Nothing => {}
            Closed::RefreshSoon(ms) => self.refresh_soon(ms),
            Closed::Changed { count, removed } => {
                let r = removed as usize;
                self.glide.replace(Some((count, Rc::new(move |i| Some(if i < r { i } else { i + 1 })))));
                self.refresh_when(count, None);
            }
        }
    }

    /// A window dragged in Mission Control (Sonata's Wayfire tells:
    /// wayfire-scale-sonata-events.patch): the desktops take it; dropped on
    /// one, it goes there (Vini).
    pub fn dragging(self: &Rc<Self>, msg: &Value) {
        if msg["state"] != "drop" {
            self.row.add_css_class("dropping");
            return;
        }
        self.row.remove_css_class("dropping");
        let g = self.monitor.as_ref().map(|m| m.geometry());
        let x = msg["x"].as_f64().unwrap_or(0.0) - g.map_or(0.0, |g| g.x() as f64);
        let y = msg["y"].as_f64().unwrap_or(0.0) - g.map_or(0.0, |g| g.y() as f64) - self.top.get() as f64;
        let picked = if (0.0..=self.win.height() as f64).contains(&y) { self.win.pick(x, y, gtk::PickFlags::DEFAULT) } else { None };
        let on_add = picked.as_ref().is_some_and(|p| p == self.add.upcast_ref::<gtk::Widget>() || p.is_ancestor(&self.add)) && self.add.get_visible();
        let target = if on_add {
            Target::Add
        } else {
            self.desktop_at(picked).map_or(Target::None, Target::Desktop)
        };
        match drop_window(&*self.ipc, &self.connector, msg["view-id"].as_i64().unwrap_or(-1), target) {
            Dropped::Nothing => {}
            Dropped::Sent => self.refresh_soon(200),
            Dropped::NewDesktop { view } => {
                // onto +: a new desktop, and the window goes there (Vini)
                let (new, ipc) = (self.count.get(), self.ipc.clone());
                self.add_desktop(
                    new,
                    Some(Box::new(move || {
                        ipc.call("vswitch/send-view", json!({"view-id": view, "x": new, "y": 0}));
                    })),
                );
            }
        }
    }

    /// Refreshed as soon as Wayfire has read its config again (it has
    /// `count` desktops): the closed one's gap doesn't wait a fixed time.
    /// then(): run first, only once it has them (a window sent to the new one).
    fn refresh_when(self: &Rc<Self>, count: i64, then: Option<Box<dyn FnOnce()>>) {
        const TRIES: u32 = 20;
        let (weak, n, then) = (Rc::downgrade(self), Cell::new(0), RefCell::new(then));
        glib::timeout_add_local(Duration::from_millis(40), move || {
            let Some(me) = weak.upgrade().filter(|me| me.is_visible()) else { return glib::ControlFlow::Break };
            let there = find_output(&*me.ipc, &me.connector).is_some_and(|o| o["workspace"]["grid_width"].as_i64().unwrap_or(1) == count);
            if there || n.get() >= TRIES {
                if let (true, Some(f)) = (there, then.take()) {
                    f();
                }
                me.refresh();
                return glib::ControlFlow::Break;
            }
            n.set(n.get() + 1);
            glib::ControlFlow::Continue
        });
    }

    /// One refresh after a burst of window events (each one rebuilt the
    /// bar, asking Wayfire twice): a new one starts the wait again.
    pub fn refresh_soon(self: &Rc<Self>, ms: u64) {
        if let Some(id) = self.soon.take() {
            sonata_ui::cancel(id);
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(ms), move || {
            if let Some(me) = weak.upgrade() {
                me.soon.replace(None);
                if me.is_visible() {
                    me.refresh();
                }
            }
        });
        self.soon.replace(Some(id));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::FakeIpc;

    fn out(count: i64, current: i64) -> Value {
        json!({"id": 1, "name": "DP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
               "workarea": {"x": 0, "y": 30, "width": 1920, "height": 1050},
               "workspace": {"x": current, "y": 0, "grid_width": count, "grid_height": 1}})
    }

    fn view(vid: i64, x: i64, app: &str) -> Value {
        json!({"id": vid, "type": "toplevel", "mapped": true, "app-id": app, "output-id": 1,
               "geometry": {"x": x, "y": 40, "width": 800, "height": 600}})
    }

    /// tests/test_spaces.py's FakeIPC.
    fn fake(count: i64, current: i64) -> FakeIpc {
        let o = out(count, current);
        FakeIpc::new(move |m, _| match m {
            "window-rules/list-outputs" => Some(json!([o.clone()])),
            "window-rules/list-views" => Some(json!([view(1, -1900, "org.gnome.Nautilus"), view(2, 100, "org.gnome.Calculator"), view(3, 300, "org.gnome.Nautilus"), view(4, 2000, "org.gnome.Nautilus")])),
            _ => Some(json!({"result": "ok"})),
        })
    }

    fn methods(ipc: &FakeIpc) -> Vec<String> {
        ipc.calls().into_iter().map(|(m, _)| m).filter(|m| !m.starts_with("window-rules/list-")).collect()
    }

    /// Vini (laptop at 1920,201 next to the HDMI display): on the laptop the
    /// desktops and the + sat at the very top, over the menu bar -- the work
    /// area (display coordinates) was taken from the display's place in the
    /// layout, 32 - 201 < 0.
    #[test]
    fn test_desktops_bar_under_the_menu_bar_on_a_second_display() {
        let edp = json!({"name": "eDP-1", "geometry": {"x": 1920, "y": 201, "width": 1536, "height": 864},
                         "workarea": {"x": 0, "y": 32, "width": 1536, "height": 832}});
        assert_eq!(bar_top(&edp), 32);
        assert_eq!(bar_top(&out(3, 1)), 30); // the first display: as it was
        assert_eq!(bar_top(&json!({})), 0);
    }

    /// Vini, after the fix above: the desktops and the + still over the
    /// menu bar on both displays -- Wayfire sends the work area as floats
    /// ({"y": 32.0}), which as_i64 read as nothing.
    #[test]
    fn test_desktops_bar_ignored_a_work_area_given_in_floats() {
        let hdmi = json!({"name": "HDMI-A-1", "geometry": {"x": 0.0, "y": 0.0, "width": 1920.0, "height": 1080.0},
                          "workarea": {"x": 0.0, "y": 32.0, "width": 1920.0, "height": 979.0}});
        assert_eq!(bar_top(&hdmi), 32);
    }

    #[test]
    fn test_desktop_of() {
        let o = out(3, 1);
        assert_eq!(desktop_of(&view(1, 100, ""), &o), 1); // on the current one
        assert_eq!(desktop_of(&view(2, -1900, ""), &o), 0); // one to the left
        assert_eq!(desktop_of(&view(3, 1920 + 50, ""), &o), 2); // one to the right
        assert_eq!(desktop_of(&view(4, 1700, ""), &o), 2); // mostly on the next
    }

    #[test]
    fn test_close_middle() {
        let (mut moves, after, count) = plan_close(1, 3, &[(1, 0), (2, 1), (3, 2)], 1);
        moves.sort();
        assert_eq!(moves, [(2, 0), (3, 1)]);
        assert_eq!((after, count), (0, 2));
    }

    #[test]
    fn test_close_first() {
        let (mut moves, after, count) = plan_close(0, 3, &[(1, 0), (2, 1), (3, 2)], 2);
        moves.sort();
        assert_eq!(moves, [(1, 0), (2, 0), (3, 1)]);
        assert_eq!((after, count), (1, 2));
    }

    #[test]
    fn test_close_last_and_nonsense() {
        assert_eq!(plan_close(2, 3, &[(5, 2)], 2), (vec![(5, 1)], 1, 2));
        assert_eq!(plan_close(0, 1, &[(5, 0)], 0), (vec![], 0, 1)); // the only one stays
        assert_eq!(plan_close(7, 3, &[], 0), (vec![], 0, 3));
    }

    #[test]
    fn test_close_before_current() {
        assert_eq!(plan_close(0, 3, &[], 2).1, 1); // still the same desktop
    }

    /// Vini: the desktops covered the windows -- scale keeps the bar's height free.
    #[test]
    fn test_room_for_the_whole_bar() {
        const { assert!(SCALE_MARGIN >= BAR_H + BAR_GAP) };
        const { assert!(BAR_H >= THUMB_H + PILL_H) };
        // config/wayfire.ini's outer_margin is this number (tests/test_spaces.py: ConfigTest)
        let ini = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../config/wayfire.ini")).unwrap();
        let scale = &ini[ini.find("[scale]").unwrap()..];
        let line = scale.lines().find(|l| l.trim_start().starts_with("outer_margin")).unwrap();
        assert_eq!(line.split('=').nth(1).unwrap().trim().parse::<i32>().unwrap(), SCALE_MARGIN);
    }

    #[test]
    fn apps_on_each_desktop() {
        let ipc = fake(3, 1);
        let per = per_desktop(&out(3, 1), &views(&ipc), 3);
        assert_eq!(per, [vec!["org.gnome.Nautilus"], vec!["org.gnome.Calculator", "org.gnome.Nautilus"], vec!["org.gnome.Nautilus"]]);
    }

    #[test]
    fn test_one_desktop_is_just_desktop() {
        assert_eq!(desktop_name(0, 1), tr!("Desktop"));
        assert_eq!(desktop_name(2, 3), sonata_i18n::fmt(&tr!("Desktop {n}"), &[("n", "3")]));
    }

    /// Vini: the desktops did nothing -- vswitch can't switch while scale
    /// holds the output; scale closes first.
    #[test]
    fn test_go_closes_mission_control_then_switches() {
        let ipc = fake(3, 1);
        go(&ipc, "DP-1", 2);
        assert_eq!(methods(&ipc), ["scale/toggle", "vswitch/set-workspace"]);
        assert_eq!(ipc.calls().last().unwrap().1, json!({"x": 2, "y": 0, "output-id": 1}));
        let ipc = fake(3, 1);
        go(&ipc, "DP-1", 1); // the current one: just closes
        assert_eq!(methods(&ipc), ["scale/toggle"]);
    }

    #[test]
    fn test_close_sends_windows_then_switches() {
        let ipc = fake(3, 1);
        let set_to = RefCell::new(Vec::new());
        let done = close_desktop(&ipc, "DP-1", 0, &|n| set_to.borrow_mut().push(n));
        assert_eq!(done, Closed::Changed { count: 2, removed: 0 });
        let calls = ipc.calls();
        let mut sent: Vec<(i64, i64)> = calls.iter().filter(|(m, _)| m == "vswitch/send-view").map(|(_, d)| (d["view-id"].as_i64().unwrap(), d["x"].as_i64().unwrap())).collect();
        sent.sort();
        assert_eq!(sent, [(2, 0), (3, 0), (4, 1)]);
        // (Vini: desktop 1 closes its apps -- none before it to take them)
        let closed: Vec<&Value> = calls.iter().filter(|(m, _)| m == "window-rules/close-view").map(|(_, d)| d).collect();
        assert_eq!(closed, [&json!({"id": 1})]);
        // (Mission Control stays open, on the desktop that was the current one)
        assert_eq!(*calls.last().unwrap(), ("sonata/set-workspace".to_string(), json!({"x": 0, "y": 0, "output-id": 1})));
        assert_eq!(*set_to.borrow(), [2]);
    }

    #[test]
    fn test_close_the_only_desktop_closes_its_apps() {
        let ipc = fake(1, 0);
        let set_to = RefCell::new(Vec::new());
        assert_eq!(close_desktop(&ipc, "DP-1", 0, &|n| set_to.borrow_mut().push(n)), Closed::RefreshSoon(300));
        let mut closed: Vec<i64> = ipc.calls().iter().filter(|(m, _)| m == "window-rules/close-view").map(|(_, d)| d["id"].as_i64().unwrap()).collect();
        closed.sort();
        assert_eq!(closed, [2, 3]); // the ones on it
        assert!(set_to.borrow().is_empty()); // the desktop stays
        assert!(!methods(&ipc).contains(&"vswitch/send-view".to_string()));
    }

    /// An older plugin: the vswitch way (Mission Control closes).
    #[test]
    fn test_close_without_sonatas_plugin() {
        let o = out(3, 1);
        let ipc = FakeIpc::new(move |m, _| match m {
            "window-rules/list-outputs" => Some(json!([o.clone()])),
            "window-rules/list-views" => Some(json!([view(1, -1900, "a"), view(2, 100, "b"), view(3, 300, "a"), view(4, 2000, "a")])),
            "sonata/set-workspace" => Some(json!({"error": "No such method"})),
            _ => Some(json!({"result": "ok"})),
        });
        let set_to = RefCell::new(Vec::new());
        close_desktop(&ipc, "DP-1", 0, &|n| set_to.borrow_mut().push(n));
        let m = methods(&ipc);
        assert_eq!(m[m.len() - 3..], ["sonata/set-workspace", "scale/toggle", "vswitch/set-workspace"]);
        assert_eq!(*set_to.borrow(), [2]);
    }

    #[test]
    fn closing_a_later_desktop_glides_the_ones_after() {
        let ipc = fake(3, 1);
        assert_eq!(close_desktop(&ipc, "DP-1", 2, &|_| {}), Closed::Changed { count: 2, removed: 2 });
        assert_eq!(close_desktop(&fake(3, 1), "DP-9", 2, &|_| {}), Closed::Nothing); // no such display
    }

    /// Vini: hold a window in Mission Control and drag it onto another desktop.
    #[test]
    fn test_a_window_dropped_on_a_desktop_goes_there() {
        let ipc = fake(3, 1);
        assert_eq!(drop_window(&ipc, "DP-1", 2, Target::Desktop(2)), Dropped::Sent);
        assert!(ipc.calls().contains(&("vswitch/send-view".to_string(), json!({"view-id": 2, "x": 2, "y": 0}))));
    }

    /// Vini: drag a window onto + -- a new desktop, and the window goes there.
    #[test]
    fn test_a_window_dropped_on_add_gets_a_new_desktop() {
        let ipc = fake(3, 1);
        assert_eq!(drop_window(&ipc, "DP-1", 2, Target::Add), Dropped::NewDesktop { view: 2 });
        assert!(!methods(&ipc).contains(&"vswitch/send-view".to_string())); // not before it exists
    }

    #[test]
    fn test_dropped_elsewhere_or_on_its_own_desktop_stays() {
        let ipc = fake(3, 1);
        assert_eq!(drop_window(&ipc, "DP-1", 2, Target::Desktop(1)), Dropped::Nothing); // view 2 is on desktop 1
        assert_eq!(drop_window(&ipc, "DP-1", 2, Target::None), Dropped::Nothing);
        assert_eq!(drop_window(&ipc, "DP-1", 99, Target::Desktop(0)), Dropped::Nothing); // gone meanwhile
        assert!(!methods(&ipc).contains(&"vswitch/send-view".to_string()));
    }

    /// Vini keeps his window buttons on the right, and the x on Mission
    /// Control's desktops and windows stayed on the left: close_at_end read
    /// appearance.json through load() with no defaults, which drops every key.
    #[test]
    fn test_close_x_ignored_the_buttons_on_the_right() {
        let dir = sonata_config::write_dir();
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("appearance.json"), r#"{"buttons_side": "right"}"#).unwrap();
        let right = close_at_end();
        std::fs::remove_file(dir.join("appearance.json")).unwrap();
        assert!(right);
        assert!(!close_at_end()); // macOS's left
    }

    /// The delay after Super+Tab: a window no .desktop matches (a terminal
    /// started by hand, a game) had apps::lookup read every .desktop file
    /// again, for each such window at every open.
    #[test]
    fn test_unknown_app_reread_every_desktop_file_at_each_open() {
        assert!(app_info("no.such.app.anywhere").is_none()); // (match_app_id's index, built once)
        let t = std::time::Instant::now();
        let _ = sonata_ui::apps::scan();
        let scan = t.elapsed();
        let t = std::time::Instant::now();
        for _ in 0..5 {
            assert!(app_info("no.such.app.anywhere").is_none());
        }
        let five = t.elapsed();
        if scan >= std::time::Duration::from_millis(1) {
            // (a handful of entries here: nothing to tell apart)
            assert!(five < scan, "5 misses took {five:?}, one scan {scan:?}");
        }
    }

    /// Vini: Mission Control's buttons follow the user's window buttons.
    #[test]
    fn test_close_x_on_the_users_side() {
        let side = |v: Value| end_side(v.as_object().unwrap());
        assert!(side(json!({"buttons_side": "right"})));
        assert!(!side(json!({"buttons_side": "left"})));
        assert!(!side(json!({"buttons_side": 3})));
        assert!(!side(json!({}))); // macOS's left
    }
}
