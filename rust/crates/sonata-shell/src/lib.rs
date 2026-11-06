//! What the shell's surfaces share (sonata2/shell/): gtk4-layer-shell, the
//! login intro, the wallpaper of the moment, and the look of the login,
//! lock and welcome screens, with what those two share: Switch User, the
//! video wallpaper under the blur, the screen corners and the compositor's
//! idle watch.

pub mod authsheet;
pub mod idlewatch;
pub mod intro;
pub mod layer;
pub mod logins;
pub mod pam;
pub mod polkitagent;
pub mod screencorners;
pub mod throttle;
pub mod userswitch;
pub mod lockdisplay;
pub mod lockvideo;
pub mod loginui;
pub mod wallpaper;

/// GTK for a test, false with no display. The first failed gtk::init is
/// remembered: GTK marks itself initialised on that attempt, so a second
/// call answered Ok and the next widget aborted the whole test binary
/// (gdk_display_manager_get before gtk_init) -- every later test lost.
#[cfg(test)]
pub(crate) fn gtk_for_tests() -> bool {
    // (one attempt, under a lock: the tests run on several threads, and two
    // gtk::init at once crashed too)
    static TRIED: std::sync::Mutex<Option<bool>> = std::sync::Mutex::new(None);
    let mut tried = TRIED.lock().unwrap_or_else(|e| e.into_inner());
    match *tried {
        Some(false) => false,
        Some(true) => gtk::is_initialized_main_thread(), // (GTK is one thread's: elsewhere, skipped)
        None => {
            let ok = gtk::init().is_ok();
            *tried = Some(ok);
            ok
        }
    }
}
