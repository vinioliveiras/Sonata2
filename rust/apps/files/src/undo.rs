//! Edit > Undo / Redo for Files (files/undo.py; Finder: ⌘Z, ⇧⌘Z).
//!
//! What can be undone, the last first: a rename, a move (put back where it
//! was), a copy or duplicate (the copies go to the Trash), a new folder (to
//! the Trash, while empty), Move to Trash (put back from the Trash). One
//! history for the Files process (every window and tab), like Finder's.
//! Each action's work runs in a thread (file work never blocks a window).

use gio::prelude::*;
use sonata_i18n::{fmt, tr};
use std::cell::RefCell;
use std::sync::Arc;

pub const LIMIT: usize = 50;

pub type Work = Arc<dyn Fn() -> Result<(), glib::Error> + Send + Sync>;

#[derive(Clone)]
pub struct Action {
    pub label: String,
    pub undo: Work,
    pub redo: Option<Work>,
}

#[derive(Default)]
pub struct History {
    pub done: Vec<Action>,
    pub undone: Vec<Action>,
}

impl History {
    pub fn push(&mut self, a: Action) {
        self.done.push(a);
        if self.done.len() > LIMIT {
            self.done.remove(0);
        }
        self.undone.clear();
    }

    pub fn can_undo(&self) -> bool {
        !self.done.is_empty()
    }

    pub fn can_redo(&self) -> bool {
        self.undone.last().is_some_and(|a| a.redo.is_some())
    }

    /// The action to undo (moved to the redo list); the caller runs its
    /// undo in a thread.
    pub fn take_undo(&mut self) -> Option<Action> {
        let a = self.done.pop()?;
        self.undone.push(a.clone());
        Some(a)
    }

    pub fn take_redo(&mut self) -> Option<Action> {
        if !self.can_redo() {
            return None;
        }
        let a = self.undone.pop()?;
        self.done.push(a.clone());
        Some(a)
    }
}

thread_local! {
    pub static HISTORY: RefCell<History> = RefCell::default();
}

pub fn push(a: Action) {
    HISTORY.with(|h| h.borrow_mut().push(a));
}

fn work(f: impl Fn() -> Result<(), glib::Error> + Send + Sync + 'static) -> Work {
    Arc::new(f)
}

fn mv(src: &gio::File, dest: &gio::File) -> Result<(), glib::Error> {
    src.move_(dest, gio::FileCopyFlags::NOFOLLOW_SYMLINKS, gio::Cancellable::NONE, None)?;
    crate::tags::moved_files(&[(src.clone(), dest.clone())]); // the sidebar's Tags follow it back
    Ok(())
}

fn base(f: &gio::File) -> String {
    f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default()
}

pub fn renamed(new: &gio::File, old_name: &str) {
    let (new_name, Some(parent)) = (base(new), new.parent()) else { return };
    let (p1, p2, o1, o2, n1, n2) = (parent.clone(), parent, old_name.to_string(), old_name.to_string(), new_name.clone(), new_name);
    push(Action {
        label: fmt(&tr!("Rename “{name}”"), &[("name", old_name)]),
        undo: work(move || p1.child(&n1).set_display_name(&o1, gio::Cancellable::NONE).map(|_| ())),
        redo: Some(work(move || p2.child(&o2).set_display_name(&n2, gio::Cancellable::NONE).map(|_| ()))),
    });
}

/// pairs: [(where it was, where it is now)].
pub fn moved(pairs: Vec<(gio::File, gio::File)>) {
    if pairs.is_empty() {
        return;
    }
    let label = if pairs.len() > 1 { tr!("Move") } else { fmt(&tr!("Move “{name}”"), &[("name", &base(&pairs[0].0))]) };
    let (a, b) = (pairs.clone(), pairs);
    push(Action {
        label,
        undo: work(move || a.iter().rev().try_for_each(|(was, now)| mv(now, was))),
        redo: Some(work(move || b.iter().try_for_each(|(was, now)| mv(was, now)))),
    });
}

/// The new copies (Copy, Duplicate, Paste, Compress): undone, they go to the Trash.
pub fn copied(created: Vec<gio::File>) {
    if created.is_empty() {
        return;
    }
    let label = if created.len() > 1 { tr!("Copy") } else { fmt(&tr!("Copy “{name}”"), &[("name", &base(&created[0]))]) };
    push(Action { label, undo: work(move || created.iter().try_for_each(|f| f.trash(gio::Cancellable::NONE))), redo: None });
}

pub fn new_folder(child: gio::File) {
    let c2 = child.clone();
    push(Action {
        label: tr!("New Folder"),
        // only while empty: else to the Trash
        undo: work(move || child.delete(gio::Cancellable::NONE).or_else(|_| child.trash(gio::Cancellable::NONE))),
        redo: Some(work(move || c2.make_directory(gio::Cancellable::NONE))),
    });
}

/// Undo: each item back from the Trash to where it was.
pub fn trashed(files: &[gio::File]) {
    let paths: Vec<std::path::PathBuf> = files.iter().filter_map(|f| f.path()).collect();
    if paths.is_empty() {
        return;
    }
    let label = if paths.len() > 1 { tr!("Move to Trash") } else { fmt(&tr!("Move “{name}” to Trash"), &[("name", &base(&files[0]))]) };
    let p2 = paths.clone();
    push(Action {
        label,
        undo: work(move || put_back_paths(&paths)),
        redo: Some(work(move || p2.iter().try_for_each(|p| gio::File::for_path(p).trash(gio::Cancellable::NONE)))),
    });
}

/// The newest Trash item that came from orig_path.
pub fn in_trash(orig_path: &std::path::Path) -> Option<gio::File> {
    let trash = gio::File::for_uri("trash:///");
    let en = trash.enumerate_children("standard::name,trash::orig-path,trash::deletion-date", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok()?;
    let mut best: Option<(gio::File, String)> = None;
    for info in en.flatten() {
        if info.attribute_byte_string("trash::orig-path").as_deref().map(std::path::Path::new) == Some(orig_path) {
            let d = info.attribute_string("trash::deletion-date").map(|s| s.to_string()).unwrap_or_default();
            if best.as_ref().is_none_or(|(_, w)| d >= *w) {
                best = Some((trash.child(info.name()), d));
            }
        }
    }
    best.map(|b| b.0)
}

pub fn put_back_paths(paths: &[std::path::PathBuf]) -> Result<(), glib::Error> {
    for p in paths {
        if let Some(item) = in_trash(p) {
            item.move_(&gio::File::for_path(p), gio::FileCopyFlags::NOFOLLOW_SYMLINKS, gio::Cancellable::NONE, None)?;
        }
    }
    Ok(())
}

/// Run an undo / redo's work off the main loop; on_done(error or None) back on it.
pub fn run(w: Work, on_done: impl FnOnce(Option<glib::Error>) + 'static) {
    glib::spawn_future_local(async move {
        let res = gio::spawn_blocking(move || w()).await;
        on_done(match res {
            Ok(Ok(())) => None,
            Ok(Err(e)) => Some(e),
            Err(_) => Some(glib::Error::new(gio::IOErrorEnum::Failed, "failed")),
        });
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn act(n: usize) -> Action {
        Action { label: n.to_string(), undo: work(|| Ok(())), redo: Some(work(|| Ok(()))) }
    }

    #[test]
    fn redo_cleared_by_a_new_action_and_limit() {
        let mut h = History::default();
        h.push(act(1));
        h.push(act(2));
        assert_eq!(h.take_undo().unwrap().label, "2");
        assert!(h.can_redo());
        h.push(act(3));
        assert!(!h.can_redo());
        for i in 0..80 {
            h.push(act(i));
        }
        assert_eq!(h.done.len(), LIMIT);
        assert_eq!(h.done[0].label, "30");
    }

    #[test]
    fn rename_and_move_undone() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("old.txt"), "x").unwrap();
        let g = gio::File::for_path(d.path());
        let new = g.child("old.txt").set_display_name("new.txt", gio::Cancellable::NONE).unwrap();
        renamed(&new, "old.txt");
        let a = HISTORY.with(|h| h.borrow_mut().take_undo()).unwrap();
        assert_eq!(a.label, "Rename “old.txt”");
        (a.undo)().unwrap();
        assert!(d.path().join("old.txt").exists());
        (a.redo.unwrap())().unwrap();
        assert!(d.path().join("new.txt").exists());
        std::fs::create_dir(d.path().join("dir")).unwrap();
        let (was, now) = (g.child("new.txt"), g.child("dir").child("new.txt"));
        was.move_(&now, gio::FileCopyFlags::NONE, gio::Cancellable::NONE, None).unwrap();
        moved(vec![(was, now)]);
        let a = HISTORY.with(|h| h.borrow_mut().take_undo()).unwrap();
        (a.undo)().unwrap();
        assert!(d.path().join("new.txt").exists());
        // a new folder is removed while empty
        let f = g.child("untitled folder");
        f.make_directory(gio::Cancellable::NONE).unwrap();
        new_folder(f);
        let a = HISTORY.with(|h| h.borrow_mut().take_undo()).unwrap();
        (a.undo)().unwrap();
        assert!(!d.path().join("untitled folder").exists());
    }
}
