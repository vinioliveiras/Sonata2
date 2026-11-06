//! Cancelling a timeout or idle that may already be over.
//!
//! glib-rs's `SourceId::remove()` panics when the source is gone: a
//! `*_once` timeout that fired, a repeating one whose closure returned
//! `Break`, or one destroyed elsewhere. A stored id that nobody cleared
//! then takes the whole process down (the menu bar did, with "Source ID
//! ... was not found when attempting to remove it"). The source's own
//! closure should clear the stored id when it ends; where that is not
//! practical, cancel with this instead of `.remove()`.

use gtk::glib;

/// Removes `id`'s source if it is still attached to the default main
/// context; a source that already ended is left alone, without the
/// GLib-CRITICAL and the panic.
pub fn cancel(id: glib::SourceId) {
    if glib::MainContext::default().find_source_by_id(&id).is_some() {
        id.remove();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::rc::Rc;
    use std::sync::Mutex;
    use std::time::Duration;

    // The default main context belongs to one thread at a time, and the
    // test harness runs tests on several.
    static CONTEXT: Mutex<()> = Mutex::new(());

    fn run_until(done: impl Fn() -> bool) {
        let ctx = glib::MainContext::default();
        let start = std::time::Instant::now();
        while !done() && start.elapsed() < Duration::from_secs(5) {
            ctx.iteration(true);
        }
        assert!(done(), "the main loop never got there");
    }

    #[test]
    fn test_menu_bar_crashed_removing_a_finished_timer() {
        //! The menu bar died with "Source ID ... was not found when
        //! attempting to remove it": a once-timeout had fired and its
        //! stored id was removed afterwards.
        let _one = CONTEXT.lock().unwrap_or_else(|e| e.into_inner());
        let ctx = glib::MainContext::default();
        // (GTK started by another test thread owns it: run without a display)
        let Ok(_owner) = ctx.acquire() else { return };
        let fired = Rc::new(Cell::new(false));
        let f = fired.clone();
        let stored: Rc<RefCell<Option<glib::SourceId>>> = Rc::new(RefCell::new(None));
        *stored.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(1), move || f.set(true)));
        run_until(|| fired.get());
        // The owner cancels the (finished) timer later on: no panic.
        cancel(stored.borrow_mut().take().unwrap());
    }

    #[test]
    fn test_menu_bar_crashed_removing_a_timer_that_returned_break() {
        //! Same crash from a repeating timeout whose closure had already
        //! said Break.
        let _one = CONTEXT.lock().unwrap_or_else(|e| e.into_inner());
        let ctx = glib::MainContext::default();
        // (GTK started by another test thread owns it: run without a display)
        let Ok(_owner) = ctx.acquire() else { return };
        let runs = Rc::new(Cell::new(0));
        let r = runs.clone();
        let id = glib::idle_add_local(move || {
            r.set(r.get() + 1);
            glib::ControlFlow::Break
        });
        run_until(|| runs.get() == 1);
        cancel(id);
    }

    #[test]
    fn cancel_still_stops_a_pending_timer() {
        let _one = CONTEXT.lock().unwrap_or_else(|e| e.into_inner());
        let ctx = glib::MainContext::default();
        // (GTK started by another test thread owns it: run without a display)
        let Ok(_owner) = ctx.acquire() else { return };
        let fired = Rc::new(Cell::new(false));
        let f = fired.clone();
        let id = glib::timeout_add_local_once(Duration::from_millis(20), move || f.set(true));
        cancel(id);
        let start = std::time::Instant::now();
        while start.elapsed() < Duration::from_millis(80) {
            ctx.iteration(false);
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(!fired.get());
    }
}
