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
}
