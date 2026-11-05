//! The desktop's icons and video wallpaper (sonata2/shell/desktop.py,
//! videowallpaper.py) and the wallpaper windows they live in
//! (shell/wallpaper.py): `sonata2-rs wallpaper [--preview]`.

pub mod grid;
pub mod focus;
pub mod video;
pub mod desktop;
pub mod window;

pub use window::run_wallpaper;

#[cfg(test)]
mod testenv {
    /// Tests that read or write settings take turns (one process environment).
    pub fn lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    type Job = Box<dyn FnOnce() + Send>;

    /// Runs `f` on the one thread GTK was started on (each test has its
    /// own thread, and GTK belongs to the first that starts it); a panic in
    /// `f` fails the test calling it. False: no display, nothing run.
    pub fn on_gtk(f: impl FnOnce() + Send + 'static) -> bool {
        use std::sync::mpsc;
        use std::sync::{Mutex, OnceLock};
        static GTK: OnceLock<Option<Mutex<mpsc::Sender<(Job, mpsc::Sender<bool>)>>>> = OnceLock::new();
        let tx = GTK.get_or_init(|| {
            let (tx, rx) = mpsc::channel::<(Job, mpsc::Sender<bool>)>();
            let (ok_tx, ok_rx) = mpsc::channel();
            std::thread::spawn(move || {
                let ok = gtk::init().is_ok();
                let _ = ok_tx.send(ok);
                if !ok {
                    return;
                }
                for (job, done) in rx {
                    let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(job));
                    let _ = done.send(r.is_ok());
                }
            });
            ok_rx.recv().unwrap_or(false).then(|| Mutex::new(tx))
        });
        let Some(tx) = tx else { return false };
        let (done_tx, done_rx) = mpsc::channel();
        tx.lock().unwrap_or_else(|e| e.into_inner()).send((Box::new(f), done_tx)).expect("the GTK thread");
        assert!(done_rx.recv().unwrap_or(false), "failed on the GTK thread (see its panic above)");
        true
    }
}
