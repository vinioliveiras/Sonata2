//! layer.centered_picker (shell/layer.py), which sonata-shell's layer.rs
//! doesn't have yet: only these two pickers use it.

use gtk::prelude::*;

/// the pickers' panel sits this far below the middle of the screen (Vini)
pub const PICKER_DROP: i32 = 140;

/// The emoji and clipboard pickers (Vini): `panel` in the middle of the
/// screen, a little lower, and a click anywhere else calls close(). The
/// window covers the screen, see-through (resizable: a fixed-size layer
/// surface was put at the top left by Wayfire, and clicks outside it never
/// reached it). False = no layer shell: a plain window.
pub fn centered_picker(win: &gtk::Window, panel: &gtk::Widget, namespace: &str, close: impl Fn() + 'static) -> bool {
    panel.set_halign(gtk::Align::Center);
    panel.set_valign(gtk::Align::Center);
    panel.set_margin_top(PICKER_DROP * 2); // centred in what's left: PICKER_DROP below the middle
    win.set_resizable(true);
    if !sonata_shell::layer::overlay_fullscreen(win, namespace) {
        panel.set_margin_top(0);
        return false;
    }
    let click = gtk::GestureClick::builder().button(0).build();
    let (w, p) = (win.downgrade(), panel.downgrade());
    click.connect_pressed(move |_, _, x, y| {
        let (Some(win), Some(panel)) = (w.upgrade(), p.upgrade()) else { return };
        if outside(win.pick(x, y, gtk::PickFlags::DEFAULT).as_ref(), &panel) {
            close();
        }
    });
    win.add_controller(click);
    true
}

/// A press on `at` (None: nothing there) is outside the panel.
pub fn outside(at: Option<&gtk::Widget>, panel: &gtk::Widget) -> bool {
    match at {
        None => true,
        Some(w) => !(w == panel || w.is_ancestor(panel)),
    }
}
