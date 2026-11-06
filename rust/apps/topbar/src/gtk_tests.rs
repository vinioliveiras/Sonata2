//! The menu bar's widget tests: each runs alone in a new process of the
//! test binary (GTK keeps the main context of the thread that starts it,
//! which the other tests need), on whatever display the run has (a private
//! one: never the user's session). No display: they pass without looking.

use crate::logo::LogoGlyph;
use gtk::prelude::*;

/// Runs `test` (an #[ignore]d one) alone in a new process of this binary.
pub(crate) fn in_own_process(test: &str) {
    let exe = std::env::current_exe().unwrap();
    let mut cmd = std::process::Command::new(exe);
    cmd.args(["--ignored", "--exact", test, "--test-threads=1"]);
    let _held = crate::testenv::ENV.lock().unwrap_or_else(|e| e.into_inner()); // (no test changing them meanwhile)
    for (k, v) in crate::testenv::original() {
        match v {
            Some(v) => cmd.env(k, v),
            None => cmd.env_remove(k),
        };
    }
    let out = cmd.output().unwrap();
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{test}:\n{text}\n{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("1 passed"), "{test} didn't run:\n{text}");
}

/// GTK on the run's display (before the test's own XDG folders: a
/// Broadway display's socket is in the run's runtime folder), then the
/// test's temporary home. SONATA_GTK_TESTS=1 makes a missing display fail.
fn gtk_up() -> Option<crate::testenv::Env> {
    if gtk::init().is_err() {
        assert!(std::env::var("SONATA_GTK_TESTS").is_err(), "no display for the GTK tests");
        return None;
    }
    let e = crate::testenv::env();
    sonata_ui::setup();
    Some(e)
}

fn settle() {
    let ctx = glib::MainContext::default();
    while ctx.iteration(false) {}
}

/// test_menu_logo.py: the words as wide as the text, at the menus' own
/// size; a shape the logo's size.
#[test]
fn test_logo_as_text_and_shapes() {
    in_own_process("gtk_tests::logo_as_text_and_shapes");
}

#[test]
#[ignore = "GTK: run by test_logo_as_text_and_shapes"]
fn logo_as_text_and_shapes() {
    let Some(_e) = gtk_up() else { return };
    let g = LogoGlyph::new(16);
    assert_eq!(g.kind(), "distro"); // the default: the distro's silhouette
    assert!(g.has_picture());
    g.set_kind("text:custom", "Vini 🎮");
    assert!(g.has_picture());
    let w = g.measure(gtk::Orientation::Horizontal, -1).0;
    assert!(w > 16 * 2, "{w}");
    assert_eq!(w, g.text_size().0); // drawn at the menus' text size, not scaled up
    assert!(g.text_size().1 <= 18);
    g.set_kind("shape:circle", "");
    assert_eq!(g.text(), "");
    assert_eq!(g.measure(gtk::Orientation::Horizontal, -1).0, 16);
    let small = LogoGlyph::new(crate::bar::LOGO_PX);
    small.set_kind("shape:star", "");
    assert_eq!(small.measure(gtk::Orientation::Horizontal, -1).0, 12);
    let apple = LogoGlyph::new(12);
    apple.set_kind("text:custom", "🍎"); // an emoji about the logo's size
    assert!(apple.text_size().0 <= 15 && apple.text_size().1 <= 15, "{:?}", apple.text_size());
}

/// Vini: an icon kept its Light Mode look in Dark Mode until clicked; in
/// the Rust bar only three icons were looked up again, never the logo.
/// The whole bar is: the logo's picture made again too.
#[test]
fn test_logo_kept_its_light_mode_look() {
    in_own_process("gtk_tests::logo_kept_its_light_mode_look");
}

#[test]
#[ignore = "GTK: run by test_logo_kept_its_light_mode_look"]
fn logo_kept_its_light_mode_look() {
    let Some(_e) = gtk_up() else { return };
    let row = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    let g = LogoGlyph::new(12);
    let img = gtk::Image::from_icon_name("sonata-control-center-symbolic");
    row.append(&g);
    row.append(&img);
    let before = g.loads();
    crate::bar::refresh_tree(row.upcast_ref());
    assert_eq!(g.loads(), before + 1);
    assert_eq!(img.icon_name().as_deref(), Some("sonata-control-center-symbolic"));
    g.refresh(false); // a focus change: a redraw, no new picture
    assert_eq!(g.loads(), before + 1);
}

/// The menu bar's panels were bare popovers: a long Wi-Fi list ran off a
/// short display (no scroller), and a row lit up under a pointer that
/// hadn't moved. They're ui.panel.popup's now; Control Center's content
/// still comes out of its kept panel for a new one.
#[test]
fn test_wifi_list_ran_off_the_screen() {
    in_own_process("gtk_tests::wifi_list_ran_off_the_screen");
}

#[test]
#[ignore = "GTK: run by test_wifi_list_ran_off_the_screen"]
fn wifi_list_ran_off_the_screen() {
    let Some(_e) = gtk_up() else { return };
    let win = gtk::Window::new();
    let btn = gtk::Button::with_label("Wi-Fi");
    win.set_child(Some(&btn));
    win.present();
    settle();
    let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
    let pop = crate::kit::panel(&btn, &col, 2, Some(280), true, true);
    assert!(pop.has_css_class("fresh")); // no hover until the pointer moves
    if sonata_ui::window::screen_size_of(&btn).is_some() {
        assert!(col.ancestor(gtk::ScrolledWindow::static_type()).is_some(), "no scroller around a panel's content");
    }
    pop.popdown();
    settle();
    crate::bar::detach_for_tests(col.upcast_ref());
    assert!(col.parent().is_none());
    win.destroy();
}

/// Control Center's Volume Mixer took the rows the apps already shown
/// needed: a new app's row (not slid in yet) counted nothing, so the
/// module grew only at the next change and the new app was cut off.
#[test]
fn test_mixer_module_grew_one_change_late() {
    in_own_process("gtk_tests::mixer_module_grew_one_change_late");
}

#[test]
#[ignore = "GTK: run by test_mixer_module_grew_one_change_late"]
fn mixer_module_grew_one_change_late() {
    use crate::mixer_ui::{AppMixer, Stream};
    use std::rc::Rc;
    let Some(_e) = gtk_up() else { return };
    let shown: crate::mixer_ui::ShownFn = Rc::new(|s: &Stream| (s.name.clone(), gio::ThemedIcon::new("audio-x-generic").upcast()));
    let apply: crate::mixer_ui::ApplyFn = Rc::new(|_: &str, _: Option<i32>, _: Option<bool>, _: &[Stream]| {});
    let pick: crate::mixer_ui::PickFn = Rc::new(|_: &gtk::Button, _: &str, _: &[Stream]| {});
    let m = AppMixer::new(apply, pick, Some(shown));
    let empty = m.content_height();
    assert!(empty > 0);
    let s = |i: i64, key: &str| Stream { index: i, key: key.into(), name: key.into(), icon: String::new(), volume: 50, muted: false, corked: false, pid: 0, sink: -1 };
    m.set_streams(&[s(1, "firefox")]);
    let one = m.content_height();
    m.set_streams(&[s(1, "firefox"), s(2, "spotify")]);
    let two = m.content_height();
    // as soon as the list changes, before the new row has slid in
    assert!(two > one && one > empty, "{empty} {one} {two}");
    assert!(m.widget().measure(gtk::Orientation::Vertical, -1).1 < two); // (what was measured)
    m.set_streams(&[s(2, "spotify")]);
    assert_eq!(m.content_height(), one); // a row sliding out: not counted
}

/// Control Center's Edit Controls: the modules stood still and the x was
/// a plain button -- ui/edit.py's CSS (the jiggle, the badge) was never
/// registered in the menu bar's process.
#[test]
fn test_edit_controls_never_jiggled() {
    let src = include_str!("bar.rs");
    let at = src.find("pub fn register_css()").unwrap();
    let body = &src[at..at + src[at..].find("\n}\n").unwrap()];
    assert!(body.contains("register_module(EDIT_CSS)"));
    assert_eq!(crate::bar::EDIT_CSS, "ui/edit.py");
    let css: String = sonata_ui::css::blocks("ui/edit.py").into_iter().map(|b| b.1).collect();
    assert!(css.contains(".jiggle .sonata-jiggle") && css.contains(".sonata-edit-badge"));
}

/// Vini's Rust session: the microphone / camera dot never showed --
/// Bar::set_inuse was there, nothing called it. Every bar asks for the
/// process's in-use watcher, whose answers go to every bar.
#[test]
fn test_mic_dot_never_shown() {
    let src = include_str!("bar.rs");
    let new_at = src.find("pub fn new(services: Services) -> Rc<Bar>").unwrap();
    let new_body = &src[new_at..new_at + src[new_at..].find("\n    }\n").unwrap()];
    assert!(new_body.contains("bar.watch_inuse();"));
    let at = src.find("pub(crate) fn connect_inuse(").unwrap();
    let body = &src[at..at + src[at..].find("\n}\n").unwrap()];
    assert!(body.contains("add_listener") && body.contains("b.set_inuse(&st.camera, &st.mic)") && body.contains("resume()"));
}

/// A module put back with Add Controls grew in (cc-slot-arriving) -- and,
/// Control Center being kept between openings, again at every opening:
/// the class stayed. Each opening takes it off (refresh, as in Python).
#[test]
fn test_put_back_module_grew_in_at_every_opening() {
    in_own_process("gtk_tests::put_back_module_grew_in_at_every_opening");
}

#[test]
#[ignore = "GTK: run by test_put_back_module_grew_in_at_every_opening"]
fn put_back_module_grew_in_at_every_opening() {
    use std::collections::HashMap;
    let Some(_e) = gtk_up() else { return };
    let widgets: HashMap<String, gtk::Widget> = ["dnd", "darkmode"].iter().map(|m| (m.to_string(), gtk::Box::new(gtk::Orientation::Vertical, 0).upcast())).collect();
    let grid = crate::ccgrid::ModuleGrid::new(widgets, &["dnd".to_string()], crate::ccgrid::WIDTH_MIN);
    grid.add_module("darkmode");
    let slot = grid.slot("darkmode").unwrap();
    assert!(slot.root.has_css_class("cc-slot-arriving"));
    grid.clear_arriving();
    assert!(!slot.root.has_css_class("cc-slot-arriving"));
    let src = include_str!("cc.rs");
    let at = src.find("pub fn refresh(self: &Rc<Self>) -> bool").unwrap();
    assert!(src[at..].contains("self.grid.clear_arriving();"));
}

/// Vini: the menu bar's Now Playing showed no volume control -- only the
/// player. Its panel has Control Center's Sound slider under the player;
/// Control Center's own Now Playing (which sits by Sound) has none.
#[test]
fn test_no_volume_in_menu_bar_now_playing() {
    in_own_process("gtk_tests::no_volume_in_menu_bar_now_playing");
}

#[test]
#[ignore = "GTK: run by test_no_volume_in_menu_bar_now_playing"]
fn no_volume_in_menu_bar_now_playing() {
    let Some(_e) = gtk_up() else { return };
    fn sliders(w: &gtk::Widget) -> usize {
        let mut n = usize::from(w.has_css_class("cc-slider-box"));
        let mut c = w.first_child();
        while let Some(ch) = c {
            n += sliders(&ch);
            c = ch.next_sibling();
        }
        n
    }
    let p = crate::mpris::Players::with_bus(None);
    assert_eq!(sliders(crate::bar::now_playing_module(&p, true).upcast_ref()), 1);
    assert_eq!(sliders(crate::bar::now_playing_module(&p, false).upcast_ref()), 0);
}
