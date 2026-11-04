//! Volume / brightness HUD, macOS Big Sur style (shell/osd.py): a
//! translucent rounded square in the lower middle of the screen with the
//! symbol and 16 blocks; fades after a moment. Shown by the menu bar
//! process when the media keys are pressed: `sonata2 key volume-up` (and
//! sonata-session's keys.rs) activates the app action "osd" with
//! "kind:level:muted", which install_action() answers.

use gtk::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

pub const SIZE: i32 = 200;
pub const BLOCKS: usize = 16;
pub const SHOW_MS: u64 = 1400;
const FADE_MS: u32 = 180;
const BOTTOM: i32 = 140;

/// (symbol, muted symbol) of a kind; anything else shows as volume.
pub fn icons(kind: &str) -> (&'static str, &'static str) {
    match kind {
        "brightness" => ("display-brightness-symbolic", "display-brightness-symbolic"),
        _ => ("sonata-volume-3-symbolic", "sonata-volume-muted-symbolic"),
    }
}

/// How many of the BLOCKS are lit for `percent` (clamped); none when muted.
pub fn lit(percent: i32, muted: bool) -> usize {
    if muted {
        return 0;
    }
    (percent.clamp(0, 100) as f64 * BLOCKS as f64 / 100.0).round_ties_even() as usize
}

/// The "osd" action's parameter, "volume:40:1": (kind, level, muted).
/// Missing parts are 0, as the Python menu bar reads them.
pub fn parse(param: &str) -> (String, i32, bool) {
    let mut parts = param.split(':');
    let kind = parts.next().unwrap_or("").to_string();
    let level = parts.next().and_then(|l| l.trim().parse().ok()).unwrap_or(0);
    let muted = parts.next() == Some("1");
    (kind, level, muted)
}

pub struct Osd {
    pub window: gtk::Window,
    pub icon: gtk::Image,
    pub blocks: Vec<gtk::Box>,
    pub rev: gtk::Revealer,
    src: RefCell<Option<glib::SourceId>>,
}

impl Osd {
    pub fn new(app: Option<&gtk::Application>) -> Rc<Self> {
        sonata_ui::css::register_module("shell/osd.py");
        let window = gtk::Window::builder().title("OSD").decorated(false).resizable(false).build();
        if let Some(app) = app {
            window.set_application(Some(app));
        }
        window.add_css_class("sonata-osd");
        let card = gtk::Box::new(gtk::Orientation::Vertical, 0);
        card.add_css_class("osd-card");
        glass_class(&card);
        card.set_size_request(SIZE, SIZE);
        let icon = gtk::Image::builder().pixel_size(96).css_classes(["osd-icon"]).vexpand(true).valign(gtk::Align::Center).build();
        card.append(&icon);
        let bar = gtk::Box::builder().spacing(1).halign(gtk::Align::Center).margin_bottom(22).build();
        let blocks: Vec<gtk::Box> = (0..BLOCKS).map(|_| gtk::Box::builder().css_classes(["osd-block"]).build()).collect();
        for b in &blocks {
            bar.append(b);
        }
        card.append(&bar);
        let rev = gtk::Revealer::builder().child(&card).transition_type(gtk::RevealerTransitionType::Crossfade).transition_duration(FADE_MS).build();
        window.set_child(Some(&rev));
        if sonata_shell::layer::available() {
            use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
            window.init_layer_shell();
            window.set_namespace(Some("sonata2-osd"));
            window.set_layer(Layer::Overlay);
            window.set_anchor(Edge::Bottom, true);
            window.set_margin(Edge::Bottom, BOTTOM);
            window.set_keyboard_mode(KeyboardMode::None);
            window.set_exclusive_zone(-1);
        }
        window.set_can_target(false);
        Rc::new(Self { window, icon, blocks, rev, src: RefCell::default() })
    }

    pub fn show_level(self: &Rc<Self>, kind: &str, percent: i32, muted: bool) {
        let (on, off) = icons(kind);
        self.icon.set_icon_name(Some(if muted { off } else { on }));
        let n = lit(percent, muted);
        for (i, b) in self.blocks.iter().enumerate() {
            if i < n {
                b.remove_css_class("off");
            } else {
                b.add_css_class("off");
            }
        }
        self.window.present();
        self.rev.set_reveal_child(true);
        if let Some(s) = self.src.take() {
            s.remove();
        }
        let weak = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(SHOW_MS), move || {
            if let Some(me) = weak.upgrade() {
                me.src.take();
                me.fade();
            }
        });
        self.src.replace(Some(id));
    }

    fn fade(self: &Rc<Self>) {
        self.rev.set_reveal_child(false);
        let weak = Rc::downgrade(self);
        glib::timeout_add_local_once(Duration::from_millis(200), move || {
            if let Some(me) = weak.upgrade() {
                if !me.rev.reveals_child() {
                    me.window.set_visible(false);
                }
            }
        });
    }

    /// Lit blocks now (tests, the menu bar's icon).
    pub fn lit_now(&self) -> usize {
        self.blocks.iter().filter(|b| !b.has_css_class("off")).count()
    }
}

/// Keep a glass surface's "solid" class in step with the setting
/// (theme.glass_class).
fn glass_class(w: &gtk::Box) {
    let sync = |w: &gtk::Box| {
        if sonata_ui::theme::glass() {
            w.remove_css_class("solid");
        } else {
            w.add_css_class("solid");
        }
    };
    sync(w);
    let weak = w.downgrade();
    sonata_ui::theme::on_change(move || {
        if let Some(w) = weak.upgrade() {
            sync(&w);
        }
    });
}

/// The app action "osd" (string "kind:level:muted") on `app`: the HUD,
/// made on first use; `shown(kind, level, muted)` after (the menu bar's
/// icon follows: topbar.py's `_poll_soon`).
pub fn install_action<F: Fn(&str, i32, bool) + 'static>(app: &gtk::Application, shown: F) -> Rc<RefCell<Option<Rc<Osd>>>> {
    let hud: Rc<RefCell<Option<Rc<Osd>>>> = Rc::default();
    let act = gio::SimpleAction::new("osd", Some(glib::VariantTy::STRING));
    let (h, weak_app) = (hud.clone(), app.downgrade());
    act.connect_activate(move |_, param| {
        let Some(param) = param.and_then(|p| p.str().map(str::to_string)) else { return };
        let (kind, level, muted) = parse(&param);
        let osd = h.borrow_mut().get_or_insert_with(|| Osd::new(weak_app.upgrade().as_ref())).clone();
        osd.show_level(&kind, level, muted);
        shown(&kind, level, muted);
    });
    app.add_action(&act);
    hud
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Volume HUD: 16 blocks lit by percent (clamped); muted lights none and shows the muted icon.
    #[test]
    fn test_blocks_follow_level() {
        assert_eq!(lit(50, false), 8);
        assert_eq!(lit(250, false), BLOCKS);
        assert_eq!(lit(-5, false), 0);
        assert_eq!(lit(80, true), 0);
        assert_eq!(lit(3, false), 0);
        assert_eq!(lit(4, false), 1);
        assert_eq!(icons("volume").1, "sonata-volume-muted-symbolic");
        assert_eq!(icons("anything"), icons("volume"));
        assert_eq!(icons("brightness").0, "display-brightness-symbolic");
    }

    /// What `sonata2 key` sends reads back.
    #[test]
    fn the_action_parameter() {
        assert_eq!(parse("volume:40:1"), ("volume".into(), 40, true));
        assert_eq!(parse("brightness:70:0"), ("brightness".into(), 70, false));
        assert_eq!(parse("volume"), ("volume".into(), 0, false));
    }
}
