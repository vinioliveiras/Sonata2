//! Sonata's app windows (sonata2/ui/window.py): the standard frame and
//! glass background. The CSS is window.py's, same selectors and tokens.

use gtk::prelude::*;

pub(crate) fn register_css() {
    // Materials (Big Sur vibrancy). A window using them keeps a transparent
    // background; the compositor blurs what shows through (Wayfire blur
    // plugin, app_id contains "sonata2" -- "sonata2rs" too).
    crate::theme::register(
        "materials",
        r#"
window.sonata-glass { background: transparent; }
/* GTK's own drop-target outline (Adwaita paints it orange): Sonata marks
   drop targets itself */
*:drop(active), *:drop(active):focus { box-shadow: none; outline: none; }
"#,
    );
    // Standard frame of Sonata's app windows: Big Sur corners and shadow,
    // contents clipped to the corners; maximized/tiled keep the corners
    // without the shadow, fullscreen is square.
    crate::theme::register(
        "frame",
        r#"
window.sonata-window.csd { border-radius: %(r_window)s;
  box-shadow: %(window_shadow)s, 0 0 0 0.5px rgba(0, 0, 0, 0.40),
              inset 0 0 0 0.5px %(highlight)s; }
window.sonata-window.csd:backdrop { box-shadow: %(window_shadow_backdrop)s, 0 0 0 0.5px rgba(0, 0, 0, 0.30); }
window.sonata-window.csd.maximized, window.sonata-window.csd.tiled,
window.sonata-window.csd.tiled-top, window.sonata-window.csd.tiled-left, window.sonata-window.csd.tiled-right,
window.sonata-window.csd.tiled-bottom { box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.40), inset 0 0 0 0.5px %(highlight)s; }
window.sonata-window:not(.csd) { box-shadow: none; }
window.sonata-window paned:not(.vertical) > separator { min-width: 1px; background: %(content_bg)s;
  box-shadow: inset 1px 0 %(separator)s; }
window.sonata-window paned.vertical > separator { min-height: 1px; background: %(content_bg)s;
  box-shadow: inset 0 1px %(separator)s; }
"#,
    );
}

/// Give an app window Sonata's standard frame (call once, any time).
pub fn standard(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    let w = win.upcast_ref::<gtk::Widget>();
    w.add_css_class("sonata-window");
    w.set_overflow(gtk::Overflow::Hidden); // children clipped to the rounded corners
}

/// Present a window that was hidden instead of closed. GTK remembers a
/// minimize as "minimize when shown" and Wayland never tells it the Dock
/// brought the window back, so a hidden window came back minimized.
pub fn show_again(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    if !win.upcast_ref::<gtk::Widget>().is_visible() {
        win.unminimize();
    }
    win.present();
}
