//! File operations with Finder's behaviour (files/ops.py): copy / move
//! (with the progress window and "already exists" alerts), duplicate, new
//! folder, Move to Trash, rename, Put Back, Empty Trash, shortcuts, and the
//! clipboard (interoperable with GNOME/KDE file managers).
//!
//! Copies run in a worker thread with Gio's sync API (one thread per
//! operation); the main loop only receives throttled progress updates.
//! Folders are copied recursively; a move is a rename when source and
//! destination share a filesystem, else copy + delete.

use gtk::prelude::*;
use gtk::gdk;
use sonata_i18n::{fmt, tr};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::mpsc;

const ATTRS: &str = "standard::name,standard::type,standard::size,standard::is-symlink";
const UI_EVERY_S: f64 = 0.1;
const NOFOLLOW: gio::FileQueryInfoFlags = gio::FileQueryInfoFlags::NOFOLLOW_SYMLINKS;
pub const TRASH: &str = "trash:///";
pub const GNOME_MIME: &str = "x-special/gnome-copied-files";

fn no_cancel() -> Option<&'static gio::Cancellable> {
    None
}

pub fn base(f: &gio::File) -> String {
    f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default()
}

// -- names ------------------------------------------------------------------------------------
/// "photo.tar.gz" -> ("photo", ".tar.gz"); dotfiles keep it whole.
pub fn split(name: &str) -> (String, String) {
    if name.starts_with('.') && name.matches('.').count() == 1 {
        return (name.into(), String::new());
    }
    for ext in [".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"] {
        if let Some(b) = name.strip_suffix(ext) {
            return (b.into(), ext.into());
        }
    }
    // os.path.splitext: the last dot, not a leading one
    match name.rfind('.') {
        Some(i) if name[..i].chars().any(|c| c != '.') => (name[..i].into(), name[i..].into()),
        _ => (name.into(), String::new()),
    }
}

/// A name not used in `folder`. style "copy": "a copy.txt", "a copy 2.txt"
/// (Duplicate / Keep Both); "number": "untitled folder 2".
pub fn free_name(folder: &gio::File, name: &str, is_dir: bool, style: &str) -> String {
    let (b, ext) = if is_dir { (name.to_string(), String::new()) } else { split(name) };
    for i in 1..10000 {
        let c = match (style, i) {
            ("number", 1) => name.to_string(),
            ("number", _) => format!("{b} {i}{ext}"),
            (_, 1) => format!("{b} copy{ext}"),
            _ => format!("{b} copy {i}{ext}"),
        };
        if !folder.child(&c).query_exists(no_cancel()) {
            return c;
        }
    }
    name.into()
}

/// (start, end) Finder selects when renaming: the name without extension.
pub fn rename_selection(name: &str, is_dir: bool) -> (i32, i32) {
    let n = name.chars().count() as i32;
    if is_dir {
        return (0, n);
    }
    let b = split(name).0.chars().count() as i32;
    (0, if b > 0 { b } else { n })
}

// -- simple operations ------------------------------------------------------------------------
/// before(child): called with the folder's file before it exists (the
/// desktop puts its spot down first, so the icon appears right there).
pub fn new_folder(folder: &gio::File, on_done: impl FnOnce(gio::File) + 'static, on_error: impl FnOnce(glib::Error) + 'static, before: Option<Box<dyn FnOnce(&gio::File)>>) {
    // in the user's language, like macOS' "pasta sem título"
    let name = free_name(folder, &tr!("untitled folder"), true, "number");
    let child = folder.child(&name);
    if let Some(b) = before {
        b(&child);
    }
    let c = child.clone();
    glib::spawn_future_local(async move {
        match c.make_directory_future(glib::Priority::DEFAULT).await {
            Ok(()) => on_done(c),
            Err(e) => on_error(e),
        }
    });
}

/// What a shortcut to `name` is called ("report.pdf - Shortcut",
/// "relatório.pdf - Atalho"); an app's launcher keeps its name.
pub fn shortcut_name(name: &str) -> String {
    if name.ends_with(".desktop") { name.into() } else { format!("{name}{}", tr!(" - Shortcut")) }
}

/// Shortcuts to `files` in `dest`: links to what they point at -- a
/// shortcut to a shortcut leads to the original. The ones made.
pub fn make_shortcuts(files: &[gio::File], dest: &gio::File) -> Vec<gio::File> {
    let mut made = Vec::new();
    for f in files {
        let Some(p) = f.path() else { continue };
        let target = std::fs::canonicalize(&p).unwrap_or(p.clone());
        let name = shortcut_name(&p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default());
        // numbered after the whole name ("x.pdf - Shortcut 2"), a launcher before ".desktop"
        let child = dest.child(free_name(dest, &name, !name.ends_with(".desktop"), "number"));
        if child.make_symbolic_link(&target, no_cancel()).is_ok() {
            made.push(child);
        }
    }
    made
}

/// A link a person would call a shortcut: its icon gets the arrow.
/// Flatpak, Nix, Snap... entries are links too, but only as part of how
/// they were installed: a link is a shortcut where people keep their own
/// things (the home folder outside its hidden folders, disks plugged in).
/// `mine`: the caller knows the place is the person's (the Desktop).
pub fn is_shortcut(info: &gio::FileInfo, file: Option<&gio::File>, mine: Option<bool>) -> bool {
    if !(info.has_attribute("standard::is-symlink") && info.is_symlink()) {
        return false;
    }
    if let Some(m) = mine {
        return m;
    }
    match file.and_then(|f| f.path()) {
        None => true, // an unknown place (the Trash)
        Some(p) => persons_place(p.parent().unwrap_or(Path::new("/"))),
    }
}

fn realpath(p: &Path) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf())
}

/// `folder` (where a link is) is one where people keep their own things.
/// Both spellings count: as shown and resolved (Silverblue's /home is a
/// link to /var/home; a Documents linked to another disk).
fn persons_place(folder: &Path) -> bool {
    let home = glib::home_dir();
    let bases = [home.clone(), realpath(&home), "/media".into(), "/run/media".into(), "/mnt".into()];
    let real = realpath(folder);
    if sonata_ui::apps::app_dirs().iter().any(|d| real == realpath(d)) {
        return false;
    }
    for d in [folder.to_path_buf(), real] {
        for b in &bases {
            if let Ok(rel) = d.strip_prefix(b) {
                if !rel.components().any(|c| c.as_os_str().to_string_lossy().starts_with('.')) {
                    return true;
                }
            }
        }
    }
    false
}

pub fn rename(f: &gio::File, new_name: &str, on_done: impl FnOnce(gio::File) + 'static, on_error: impl FnOnce(glib::Error) + 'static) {
    let (f, n) = (f.clone(), new_name.to_string());
    glib::spawn_future_local(async move {
        match f.set_display_name_future(&n, glib::Priority::DEFAULT).await {
            Ok(new) => on_done(new),
            Err(e) => on_error(e),
        }
    });
}

/// on_done(trashed files) once every one has been tried (Undo records them).
pub fn trash(files: Vec<gio::File>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>, on_done: Option<Box<dyn FnOnce(Vec<gio::File>)>>) {
    glib::spawn_future_local(async move {
        let mut ok = Vec::new();
        for f in files {
            match f.trash_future(glib::Priority::DEFAULT).await {
                Ok(()) => {
                    if ok.is_empty() {
                        sonata_backend::sounds::play("trash"); // only once something really went
                    }
                    ok.push(f);
                }
                Err(e) => {
                    if let Some(cb) = &on_error {
                        cb(&f, &e);
                    }
                }
            }
        }
        if let (Some(cb), false) = (on_done, ok.is_empty()) {
            cb(ok);
        }
    });
}

pub fn is_trash(uri: &str) -> bool {
    uri.starts_with("trash:")
}

/// Run work(report) in a thread (gvfs / big folders must never block a
/// window); report and on_done reach the main loop.
pub fn in_thread(work: impl FnOnce(&dyn Fn(&gio::File, &glib::Error)) + Send + 'static, on_done: Option<Box<dyn FnOnce()>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let (tx, rx) = mpsc::channel::<Option<(gio::File, glib::Error)>>();
    std::thread::spawn(move || {
        let t = tx.clone();
        work(&move |f: &gio::File, e: &glib::Error| {
            let _ = t.send(Some((f.clone(), e.clone())));
        });
        let _ = tx.send(None);
    });
    let mut on_done = on_done;
    glib::timeout_add_local(std::time::Duration::from_millis(30), move || {
        loop {
            match rx.try_recv() {
                Ok(Some((f, e))) => {
                    if let Some(cb) = &on_error {
                        cb(&f, &e);
                    }
                }
                Ok(None) | Err(mpsc::TryRecvError::Disconnected) => {
                    if let Some(d) = on_done.take() {
                        d();
                    }
                    return glib::ControlFlow::Break;
                }
                Err(mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
            }
        }
    });
}

fn failed(msg: &str) -> glib::Error {
    glib::Error::new(gio::IOErrorEnum::Failed, msg)
}

/// Finder "Put Back": move each item to where it was trashed from.
pub fn put_back(files: Vec<gio::File>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>, on_done: Option<Box<dyn FnOnce()>>) {
    let unknown = tr!("The original location is unknown.");
    in_thread(
        move |report| {
            for f in &files {
                let res = (|| -> Result<(), glib::Error> {
                    let orig = f.query_info("trash::orig-path", NOFOLLOW, no_cancel())?.attribute_byte_string("trash::orig-path").filter(|o| !o.is_empty()).ok_or_else(|| failed(&unknown))?;
                    let mut dest = gio::File::for_path(orig.as_str());
                    let parent = dest.parent();
                    if let Some(p) = parent.as_ref().filter(|p| !p.query_exists(no_cancel())) {
                        p.make_directory_with_parents(no_cancel())?;
                    }
                    if dest.query_exists(no_cancel()) {
                        let p = parent.unwrap();
                        dest = p.child(free_name(&p, &base(&dest), false, "copy"));
                    }
                    f.move_(&dest, gio::FileCopyFlags::NOFOLLOW_SYMLINKS, no_cancel(), None)
                })();
                if let Err(e) = res {
                    report(f, &e);
                }
            }
        },
        on_done,
        on_error,
    );
}

/// Recursive delete (replace, and the second half of a cross-disk move).
pub fn delete(f: &gio::File, cancel: Option<&gio::Cancellable>) -> Result<(), glib::Error> {
    let info = f.query_info(ATTRS, NOFOLLOW, cancel)?;
    if info.file_type() == gio::FileType::Directory {
        for child in f.enumerate_children(ATTRS, NOFOLLOW, cancel)? {
            delete(&f.child(child?.name()), cancel)?;
        }
    }
    f.delete(cancel)
}

fn delete_all(files: &[gio::File], report: &dyn Fn(&gio::File, &glib::Error)) -> usize {
    let mut n = 0;
    for f in files {
        match delete(f, None) {
            Ok(()) => n += 1,
            Err(e) => report(f, &e),
        }
    }
    n
}

/// The Trash's items erased each as a whole: gvfs's trash:/// deletes a
/// trashed folder with everything in it, but refuses anything inside one
/// ("Items in the wastebasket may not be modified") -- going in to delete
/// file by file, Empty Trash failed on every folder (Vini).
pub fn erase_trashed(items: &[gio::File], erase: &dyn Fn(&gio::File) -> Result<(), glib::Error>, report: &dyn Fn(&gio::File, &glib::Error)) -> usize {
    erase_steps(items, erase, report, &|_, _, _| {}, &gio::Cancellable::new())
}

/// Delete Immediately, in a thread (big folders), with the progress window
/// (Vini: deleting something heavy showed nothing while it worked).
pub fn delete_now(files: Vec<gio::File>, on_done: Option<Box<dyn FnOnce()>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let title = files.first().map(|f| if files.len() == 1 { base(f) } else { String::new() }).unwrap_or_default();
    in_thread_with_progress(
        &tr!("Delete Immediately"),
        &title,
        move |report, step, cancel| {
            erase_steps(&files, &|f| delete(f, Some(cancel)), report, step, cancel);
        },
        on_done,
        on_error,
    );
}

/// Erase `items` one by one, saying each step (done, total, the next one's
/// name) and stopping when cancelled. The count erased.
pub fn erase_steps(items: &[gio::File], erase: &dyn Fn(&gio::File) -> Result<(), glib::Error>, report: &dyn Fn(&gio::File, &glib::Error), step: &dyn Fn(u64, u64, String), cancel: &gio::Cancellable) -> usize {
    let total = items.len() as u64;
    let mut n = 0;
    for (i, f) in items.iter().enumerate() {
        if cancel.is_cancelled() {
            break;
        }
        step(i as u64, total, display_name_of(f));
        match erase(f) {
            Ok(()) => n += 1,
            Err(e) if e.matches(gio::IOErrorEnum::Cancelled) => break,
            Err(e) => report(f, &e),
        }
    }
    step(total, total, String::new());
    n
}

/// What the progress window says is being erased: its display name (a
/// trashed item's is its own name, not trash:///'s escaped path).
fn display_name_of(f: &gio::File) -> String {
    f.query_info("standard::display-name", NOFOLLOW, no_cancel()).map(|i| i.display_name().to_string()).unwrap_or_else(|_| base(f))
}

/// in_thread with Sonata's progress window: `work` says its steps (done,
/// total, the item at hand) and watches `cancel` (the window's stop). The
/// window only shows past its delay: quick ones never flash it. One item
/// (or none known yet): the bar pulses.
pub fn in_thread_with_progress(
    heading: &str,
    title: &str,
    work: impl FnOnce(&dyn Fn(&gio::File, &glib::Error), &dyn Fn(u64, u64, String), &gio::Cancellable) + Send + 'static,
    on_done: Option<Box<dyn FnOnce()>>,
    on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>,
) {
    enum M {
        Err(gio::File, glib::Error),
        Step(u64, u64, String),
        Done,
    }
    let cancel = gio::Cancellable::new();
    let (tx, rx) = mpsc::channel::<M>();
    let c2 = cancel.clone();
    std::thread::spawn(move || {
        let (t1, t2) = (tx.clone(), tx.clone());
        work(
            &move |f: &gio::File, e: &glib::Error| {
                let _ = t1.send(M::Err(f.clone(), e.clone()));
            },
            &move |done, total, name| {
                let _ = t2.send(M::Step(done, total, name));
            },
            &c2,
        );
        let _ = tx.send(M::Done);
    });
    let c3 = cancel.clone();
    let op = sonata_ui::progress::start(title, Some(Box::new(move || c3.cancel())), Some(heading));
    let mut on_done = on_done;
    let mut last = (0u64, 0u64);
    glib::timeout_add_local(std::time::Duration::from_millis(30), move || loop {
        match rx.try_recv() {
            Ok(M::Err(f, e)) => {
                if let Some(cb) = &on_error {
                    if !e.matches(gio::IOErrorEnum::Cancelled) {
                        cb(&f, &e);
                    }
                }
            }
            Ok(M::Step(done, total, name)) => {
                last = (done, total);
                if !name.is_empty() {
                    op.set_detail(&name);
                }
                if total > 1 {
                    op.bar.set_fraction(done as f64 / total as f64);
                }
            }
            Ok(M::Done) | Err(mpsc::TryRecvError::Disconnected) => {
                op.finish();
                if let Some(d) = on_done.take() {
                    d();
                }
                return glib::ControlFlow::Break;
            }
            Err(mpsc::TryRecvError::Empty) => {
                if last.1 <= 1 {
                    op.bar.pulse(); // one big item: no steps to count
                }
                return glib::ControlFlow::Continue;
            }
        }
    });
}

pub fn home_trash() -> PathBuf {
    glib::user_data_dir().join("Trash")
}

/// What the home Trash holds as the Trash window shows it: files with
/// their .trashinfo. A file without one (left by a crash or another tool)
/// is invisible there -- the Dock counted it, and the Trash stayed full
/// after Empty Trash.
pub fn trash_items() -> Vec<String> {
    let t = home_trash();
    let Ok(rd) = std::fs::read_dir(t.join("files")) else { return Vec::new() };
    rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| t.join("info").join(format!("{n}.trashinfo")).exists()).collect()
}

/// Empty Trash leaves nothing behind: files with no .trashinfo (never
/// listed, so never erased) and .trashinfo with no file.
pub fn clear_orphans() {
    let t = home_trash();
    let (files, info) = (t.join("files"), t.join("info"));
    for (d, other, strip) in [(&files, &info, false), (&info, &files, true)] {
        let Ok(rd) = std::fs::read_dir(d) else { continue };
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            let partner = if strip { other.join(n.strip_suffix(".trashinfo").unwrap_or(&n)) } else { other.join(format!("{n}.trashinfo")) };
            if partner.symlink_metadata().is_ok() {
                continue;
            }
            let p = e.path();
            let _ = if e.file_type().is_ok_and(|t| t.is_dir()) { std::fs::remove_dir_all(&p) } else { std::fs::remove_file(&p) };
        }
    }
}

/// Listing and deleting both run in the thread (gvfs); the sound plays
/// once something was really erased.
pub fn empty_trash(on_done: Option<Box<dyn FnOnce()>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let erased = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let e2 = erased.clone();
    // the progress window as Delete Immediately (Vini: emptying a heavy
    // Trash showed nothing while it worked)
    in_thread_with_progress(
        &tr!("Empty Trash"),
        "",
        move |report, step, cancel| {
            let t = gio::File::for_uri(TRASH);
            let kids: Result<Vec<gio::File>, glib::Error> = t.enumerate_children("standard::name", NOFOLLOW, no_cancel()).map(|en| en.flatten().map(|i| t.child(i.name())).collect());
            match kids {
                Ok(k) => {
                    e2.store(erase_steps(&k, &|f| f.delete(Some(cancel)), report, step, cancel), std::sync::atomic::Ordering::SeqCst);
                    if !cancel.is_cancelled() {
                        clear_orphans();
                    }
                }
                Err(e) => report(&t, &e),
            }
        },
        Some(Box::new(move || {
            if erased.load(std::sync::atomic::Ordering::SeqCst) > 0 {
                sonata_backend::sounds::play("empty-trash");
            }
            if let Some(d) = on_done {
                d();
            }
        })),
        on_error,
    );
}

// -- copy / move ------------------------------------------------------------------------------
/// A comparable key for `f`: its resolved path (symlinked folders and
/// "a/../b" compare equal), else its URI. item: resolve only the folder it
/// is in (a symlink stays itself, not what it points to).
pub fn real_key(f: &gio::File, item: bool) -> String {
    let Some(p) = f.path() else { return f.uri().trim_end_matches('/').to_string() };
    if item {
        if let (Some(parent), Some(name)) = (p.parent(), p.file_name()) {
            return realpath(parent).join(name).display().to_string();
        }
    }
    realpath(&p).display().to_string()
}

/// `key` is `parent` or below it.
pub fn inside(key: &str, parent: &str) -> bool {
    key == parent || key.starts_with(&format!("{}/", parent.trim_end_matches('/')))
}

/// What a drag and drop onto the folder `dest` really transfers (Finder):
/// nothing when an item is dropped onto itself, or a folder into one of
/// its own subfolders (the whole drop is refused, silently); items already
/// in `dest` are left alone, so a drop back onto their own folder is a no-op.
pub fn drop_plan(files: &[gio::File], dest: &gio::File) -> Vec<gio::File> {
    let d = real_key(dest, false);
    let mut out = Vec::new();
    for f in files {
        if inside(&d, &real_key(f, true)) {
            return Vec::new(); // onto itself / into its own subfolder
        }
        if f.parent().is_some_and(|p| real_key(&p, false) == d) {
            continue; // already there
        }
        out.push(f.clone());
    }
    out
}

enum Msg {
    Progress(u64, u64),
    Ask(String, bool, mpsc::Sender<String>),
    Done(Option<String>, Vec<(gio::File, gio::File)>),
}

struct Worker {
    sources: Vec<gio::File>,
    dest: gio::File,
    mv: bool,
    duplicate: bool,
    cancel: gio::Cancellable,
    tx: mpsc::Sender<Msg>,
    total: u64,
    done_bytes: u64,
    last_ui: std::time::Instant,
    apply_all: Option<String>,
    record: Vec<(gio::File, gio::File)>,
    texts: Texts,
}

/// The messages a worker needs, translated on the main thread.
#[derive(Clone)]
struct Texts {
    contains_moving: String,
    contains_copying: String,
}

struct Stop;

impl From<glib::Error> for StopOr {
    fn from(e: glib::Error) -> Self {
        StopOr::Err(e)
    }
}

enum StopOr {
    Stop(Stop),
    Err(glib::Error),
}

impl Worker {
    fn size(&self, f: &gio::File) -> Result<u64, glib::Error> {
        let info = f.query_info(ATTRS, NOFOLLOW, Some(&self.cancel))?;
        if info.file_type() != gio::FileType::Directory {
            return Ok(info.size().max(0) as u64);
        }
        let mut total = 0;
        for child in f.enumerate_children(ATTRS, NOFOLLOW, Some(&self.cancel))? {
            let child = child?;
            total += if child.file_type() == gio::FileType::Directory { self.size(&f.child(child.name()))? } else { child.size().max(0) as u64 };
        }
        Ok(total)
    }

    fn run(mut self) {
        let res = (|| -> Result<(), StopOr> {
            let mut total = 0;
            for f in &self.sources {
                total += self.size(f)?;
            }
            self.total = total;
            for src in self.sources.clone() {
                if self.cancel.is_cancelled() {
                    break;
                }
                self.one(&src)?;
            }
            Ok(())
        })();
        let err = match res {
            Err(StopOr::Err(e)) if !e.matches(gio::IOErrorEnum::Cancelled) => Some(e.message().to_string()),
            _ => None,
        };
        let _ = self.tx.send(Msg::Done(err, std::mem::take(&mut self.record)));
    }

    fn one(&mut self, src: &gio::File) -> Result<(), StopOr> {
        let name = base(src);
        let info = src.query_info(ATTRS, NOFOLLOW, Some(&self.cancel))?;
        let is_dir = info.file_type() == gio::FileType::Directory;
        let mut target;
        if self.duplicate {
            target = self.dest.child(free_name(&self.dest, &name, is_dir, "copy"));
        } else {
            target = self.dest.child(&name);
            let same = src.equal(&target) || real_key(src, true) == real_key(&target, true);
            if self.mv && same {
                return Ok(()); // moved to where it is: nothing to do
            }
            if is_dir && inside(&real_key(&self.dest, false), &real_key(src, true)) {
                return Ok(()); // a folder into itself: refused, silently
            }
            if same {
                // paste into the same folder = duplicate
                target = self.dest.child(free_name(&self.dest, &name, is_dir, "copy"));
            } else if target.query_exists(Some(&self.cancel)) {
                match self.ask(&name, is_dir).as_str() {
                    "keep" => target = self.dest.child(free_name(&self.dest, &name, is_dir, "copy")),
                    "replace" => {
                        let aside = self.set_aside(src, &target, &name)?;
                        if let Err(e) = self.transfer(src, &target, is_dir, &info) {
                            Self::restore(&target, &aside);
                            return Err(e.into());
                        }
                        delete(&aside, None)?; // only once the new item is in place
                        return Ok(()); // (replaced: not undoable -- the old one is gone)
                    }
                    _ => return Err(StopOr::Stop(Stop)),
                }
            }
        }
        self.transfer(src, &target, is_dir, &info)?;
        self.record.push((src.clone(), target));
        Ok(())
    }

    /// Replace: rename the old item to a hidden temp name in the same
    /// folder (deleting it first lost data when the copy then failed, or
    /// when it contained the item being moved).
    fn set_aside(&self, src: &gio::File, target: &gio::File, name: &str) -> Result<gio::File, glib::Error> {
        if inside(&real_key(src, true), &real_key(target, true)) {
            let why = if self.mv { &self.texts.contains_moving } else { &self.texts.contains_copying };
            return Err(failed(&fmt(why, &[("name", name)])));
        }
        let aside = self.dest.child(free_name(&self.dest, &format!(".{name}.sonata-replaced"), true, "number"));
        target.move_(&aside, gio::FileCopyFlags::NOFOLLOW_SYMLINKS | gio::FileCopyFlags::NO_FALLBACK_FOR_MOVE, Some(&self.cancel), None)?;
        Ok(aside)
    }

    /// Undo set_aside after a failed transfer: drop the partial copy, put
    /// the old item back (else it stays under its hidden name).
    fn restore(target: &gio::File, aside: &gio::File) {
        if target.query_exists(no_cancel()) {
            let _ = delete(target, None);
        }
        let _ = aside.move_(target, gio::FileCopyFlags::NOFOLLOW_SYMLINKS, no_cancel(), None);
    }

    fn transfer(&mut self, src: &gio::File, target: &gio::File, is_dir: bool, info: &gio::FileInfo) -> Result<(), glib::Error> {
        if self.mv {
            match src.move_(target, gio::FileCopyFlags::NOFOLLOW_SYMLINKS | gio::FileCopyFlags::NO_FALLBACK_FOR_MOVE, Some(&self.cancel), None) {
                Ok(()) => {
                    self.done_bytes += if is_dir { self.size(target)? } else { info.size().max(0) as u64 };
                    self.progress(0, 0);
                    return Ok(());
                }
                Err(e) if e.matches(gio::IOErrorEnum::NotSupported) || e.matches(gio::IOErrorEnum::WouldRecurse) => {}
                Err(e) => return Err(e),
            }
        }
        self.copy(src, target, is_dir)?;
        if self.mv {
            delete(src, Some(&self.cancel))?;
        }
        Ok(())
    }

    fn copy(&mut self, src: &gio::File, target: &gio::File, is_dir: bool) -> Result<(), glib::Error> {
        if is_dir {
            target.make_directory(Some(&self.cancel))?;
            for child in src.enumerate_children(ATTRS, NOFOLLOW, Some(&self.cancel))? {
                let child = child?;
                self.copy(&src.child(child.name()), &target.child(child.name()), child.file_type() == gio::FileType::Directory)?;
            }
            return Ok(());
        }
        let start = self.done_bytes;
        let (tx, total) = (self.tx.clone(), self.total);
        let mut last = std::time::Instant::now() - std::time::Duration::from_secs(1);
        let mut cb = move |cur: i64, _tot: i64| {
            if last.elapsed().as_secs_f64() >= UI_EVERY_S {
                last = std::time::Instant::now();
                let _ = tx.send(Msg::Progress(start + cur.max(0) as u64, total));
            }
        };
        src.copy(target, gio::FileCopyFlags::NOFOLLOW_SYMLINKS | gio::FileCopyFlags::ALL_METADATA, Some(&self.cancel), Some(&mut cb))?;
        self.done_bytes = start + src.query_info(ATTRS, NOFOLLOW, no_cancel())?.size().max(0) as u64;
        Ok(())
    }

    fn progress(&mut self, base: u64, cur: u64) {
        if self.last_ui.elapsed().as_secs_f64() >= UI_EVERY_S {
            self.last_ui = std::time::Instant::now();
            let done = if cur > 0 { base + cur } else { self.done_bytes };
            let _ = self.tx.send(Msg::Progress(done, self.total));
        }
    }

    /// Finder's conflict alert, answered on the main thread.
    fn ask(&mut self, name: &str, is_dir: bool) -> String {
        if let Some(a) = &self.apply_all {
            return a.clone();
        }
        let (tx, rx) = mpsc::channel();
        let _ = self.tx.send(Msg::Ask(name.into(), is_dir, tx));
        let answer = rx.recv().unwrap_or_else(|_| "stop|0".into());
        let (rid, all) = answer.split_once('|').unwrap_or((&answer, "0"));
        if all == "1" {
            self.apply_all = Some(rid.to_string());
        }
        rid.to_string()
    }
}

/// Copy (or move) `sources` into the folder `dest`. `parent`: the window
/// for the alerts; on_done() runs on the main loop at the end.
pub fn transfer(sources: Vec<gio::File>, dest: &gio::File, mv: bool, parent: Option<gtk::Widget>, on_done: Option<Box<dyn FnOnce()>>, duplicate: bool) {
    let first = sources.first().map(base).unwrap_or_default();
    let where_ = dest.basename().map(|b| b.to_string_lossy().into_owned()).filter(|b| !b.is_empty() && b != "/").unwrap_or_else(|| dest.uri().to_string());
    let title = if sources.len() == 1 {
        fmt(&if mv { tr!("Moving “{name}” to “{dest}”") } else { tr!("Copying “{name}” to “{dest}”") }, &[("name", &first), ("dest", &where_)])
    } else {
        fmt(&if mv { tr!("Moving {n} items to “{dest}”") } else { tr!("Copying {n} items to “{dest}”") }, &[("n", &sources.len().to_string()), ("dest", &where_)])
    };
    let cancel = gio::Cancellable::new();
    let c = cancel.clone();
    let op = sonata_ui::progress::start(&title, Some(Box::new(move || c.cancel())), Some(&if mv { tr!("Move") } else { tr!("Copy") }));
    let (tx, rx) = mpsc::channel();
    let texts = Texts { contains_moving: tr!("“{name}” can’t be replaced because it contains the item you’re moving."), contains_copying: tr!("“{name}” can’t be replaced because it contains the item you’re copying.") };
    let worker = Worker { sources, dest: dest.clone(), mv, duplicate, cancel, tx, total: 0, done_bytes: 0, last_ui: std::time::Instant::now(), apply_all: None, record: Vec::new(), texts };
    std::thread::spawn(move || worker.run());
    let mut on_done = on_done;
    glib::timeout_add_local(std::time::Duration::from_millis(30), move || loop {
        match rx.try_recv() {
            Ok(Msg::Progress(done, total)) => op.update(done, total),
            Ok(Msg::Ask(name, is_dir, reply)) => {
                let heading = if is_dir { tr!("A folder named “{name}” already exists in this location.") } else { tr!("An item named “{name}” already exists in this location.") };
                let body = if mv { tr!("Do you want to replace it with the one you’re moving?") } else { tr!("Do you want to replace it with the one you’re copying?") };
                sonata_ui::dialog::alert_check(
                    &fmt(&heading, &[("name", &name)]),
                    &body,
                    &[("keep", tr!("Keep Both"), ""), ("stop", tr!("Stop"), ""), ("replace", tr!("Replace"), "default")],
                    &tr!("Apply to All"),
                    false,
                    move |rid, all| {
                        let _ = reply.send(format!("{rid}|{}", if all { 1 } else { 0 }));
                    },
                    parent.as_ref(),
                );
            }
            Ok(Msg::Done(err, record)) => {
                op.finish();
                if !record.is_empty() {
                    // Edit > Undo
                    if mv {
                        crate::tags::moved_files(&record); // the sidebar's Tags follow them
                        crate::undo::moved(record);
                    } else {
                        crate::undo::copied(record.into_iter().map(|(_, t)| t).collect());
                    }
                }
                sonata_backend::sounds::play(if err.is_none() { "done" } else { "error" });
                if let Some(e) = err {
                    sonata_ui::dialog::alert(&tr!("The operation can’t be completed."), &e, &[("ok", "OK".into(), "default")], |_| {}, parent.as_ref());
                }
                if let Some(d) = on_done.take() {
                    d();
                }
                return glib::ControlFlow::Break;
            }
            Err(mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                op.finish();
                return glib::ControlFlow::Break;
            }
        }
    });
}

// -- clipboard --------------------------------------------------------------------------------
const RAW: [&str; 4] = ["image/png", "image/jpeg", "image/gif", "image/webp"];

/// Files for the clipboard or a drag, in every form apps look for:
/// text/uri-list (GTK apps read it as a file list; Chromium/Electron,
/// Qt...), GNOME's copied-files format (Nautilus, Nemo, Dolphin) and, for
/// a single picture, the picture itself. No FileList value on purpose: GTK
/// would then offer the portal's file-transfer format first, and receivers
/// without the document portal refused the drop.
pub fn file_content(files: &[gio::File], cut: bool, with_image: bool) -> gdk::ContentProvider {
    let uris: Vec<String> = files.iter().map(|f| f.uri().to_string()).collect();
    let gnome = format!("{}\n{}", if cut { "cut" } else { "copy" }, uris.join("\n"));
    let mut providers = vec![
        gdk::ContentProvider::for_bytes("text/uri-list", &glib::Bytes::from_owned(format!("{}\r\n", uris.join("\r\n")).into_bytes())),
        gdk::ContentProvider::for_bytes(GNOME_MIME, &glib::Bytes::from_owned(gnome.into_bytes())),
    ];
    if with_image && files.len() == 1 && !cut {
        if let Some((mime, bytes)) = image_data(&files[0]) {
            providers.push(gdk::ContentProvider::for_bytes(&mime, &bytes));
        }
    }
    gdk::ContentProvider::new_union(&providers)
}

/// (mime, bytes) of a picture file as it is (PNG, JPEG, GIF, WebP: no
/// re-encoding, so a drag starts at once); other pictures become PNG.
fn image_data(f: &gio::File) -> Option<(String, glib::Bytes)> {
    let info = f.query_info("standard::content-type,standard::size", gio::FileQueryInfoFlags::NONE, no_cancel()).ok()?;
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if !ct.starts_with("image/") || info.size() > 40 << 20 {
        return None;
    }
    if RAW.contains(&ct.as_str()) {
        return f.load_contents(no_cancel()).ok().map(|(b, _)| (ct, glib::Bytes::from_owned(b.to_vec())));
    }
    gdk::Texture::from_file(f).ok().map(|t| ("image/png".to_string(), t.save_to_png_bytes()))
}

pub fn copy_to_clipboard(widget: &impl IsA<gtk::Widget>, files: &[gio::File], cut: bool) {
    let _ = widget.clipboard().set_content(Some(&file_content(files, cut, true)));
}

fn has_image(formats: &gdk::ContentFormats) -> bool {
    formats.contains_type(gdk::Texture::static_type()) || formats.mime_types().iter().any(|m| m.starts_with("image/"))
}

/// A picture on the clipboard (copied in a browser, a screenshot...)
/// becomes "Pasted Image <date> at <time>.png" in `dest`. False when the
/// clipboard holds no picture.
pub fn paste_image(widget: &impl IsA<gtk::Widget>, dest: &gio::File, done: Option<Box<dyn FnOnce(gio::File)>>, on_error: Option<Box<dyn FnOnce(glib::Error)>>) -> bool {
    let cb = widget.clipboard();
    if !has_image(&cb.formats()) {
        return false;
    }
    let dest = dest.clone();
    glib::spawn_future_local(async move {
        let Ok(Some(tex)) = cb.read_texture_future().await else { return };
        let name = glib::DateTime::now_local().ok().and_then(|d| d.format("Pasted Image %Y-%m-%d at %H.%M.%S.png").ok()).map(|s| s.to_string()).unwrap_or_else(|| "Pasted Image.png".into());
        let target = dest.child(free_name(&dest, &name, false, "number")); // two pastes in one second
        let ok = target.path().is_some_and(|p| tex.save_to_png(p).is_ok());
        if !ok {
            if let Some(e) = on_error {
                e(failed(&tr!("The picture couldn’t be saved.")));
            }
            return;
        }
        if let Some(d) = done {
            d(target);
        }
    });
    true
}

/// callback(files, cut) with what the clipboard holds (files may be empty).
pub fn read_clipboard(widget: &impl IsA<gtk::Widget>, callback: impl FnOnce(Vec<gio::File>, bool) + 'static) {
    let cb = widget.clipboard();
    let formats = cb.formats();
    if formats.contain_mime_type(GNOME_MIME) {
        glib::spawn_future_local(async move {
            let data = match cb.read_future(&[GNOME_MIME], glib::Priority::DEFAULT).await {
                Ok((stream, _)) => stream.read_bytes_future(1 << 20, glib::Priority::DEFAULT).await.ok().map(|b| String::from_utf8_lossy(&b.to_vec()).into_owned()),
                Err(_) => None,
            };
            let lines: Vec<String> = data.unwrap_or_default().lines().filter(|l| !l.is_empty()).map(str::to_string).collect();
            if lines.is_empty() {
                callback(Vec::new(), false);
                return;
            }
            callback(lines[1..].iter().map(|u| gio::File::for_uri(u)).collect(), lines[0].as_str() == "cut");
        });
        return;
    }
    if formats.contains_type(gdk::FileList::static_type()) {
        glib::spawn_future_local(async move {
            let files = cb.read_value_future(gdk::FileList::static_type(), glib::Priority::DEFAULT).await.ok().and_then(|v| v.get::<gdk::FileList>().ok()).map(|l| l.files()).unwrap_or_default();
            callback(files, false);
        });
        return;
    }
    callback(Vec::new(), false);
}

/// Something Paste can put in a folder: files, or a picture.
pub fn clipboard_has_files(widget: &impl IsA<gtk::Widget>) -> bool {
    let f = widget.clipboard().formats();
    f.contain_mime_type(GNOME_MIME) || f.contains_type(gdk::FileList::static_type()) || has_image(&f)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names() {
        let d = tempfile::tempdir().unwrap();
        let g = gio::File::for_path(d.path());
        std::fs::write(d.path().join("a.txt"), "").unwrap();
        std::fs::write(d.path().join("a copy.txt"), "").unwrap();
        assert_eq!(free_name(&g, "a.txt", false, "copy"), "a copy 2.txt");
        assert_eq!(free_name(&g, "b.tar.gz", false, "copy"), "b copy.tar.gz");
        assert_eq!(free_name(&g, "untitled folder", true, "number"), "untitled folder");
        std::fs::create_dir(d.path().join("untitled folder")).unwrap();
        assert_eq!(free_name(&g, "untitled folder", true, "number"), "untitled folder 2");
        assert_eq!(rename_selection("photo.jpeg", false), (0, 5));
        assert_eq!(rename_selection("my.folder", true), (0, 9));
        assert_eq!(split(".bashrc"), (".bashrc".into(), "".into()));
        assert_eq!(split("x.tar.zst"), ("x".into(), ".tar.zst".into()));
    }

    #[test]
    fn drop_plan_no_ops() {
        let d = tempfile::tempdir().unwrap();
        let p = |s: &str| d.path().join(s);
        std::fs::create_dir_all(p("dir/sub")).unwrap();
        std::fs::write(p("a.txt"), "").unwrap();
        std::fs::create_dir(p("other")).unwrap();
        let f = |s: &Path| gio::File::for_path(s);
        let g = f(d.path());
        let (a, dd, sub, other) = (f(&p("a.txt")), f(&p("dir")), f(&p("dir/sub")), f(&p("other")));
        let paths = |v: Vec<gio::File>| v.iter().map(|x| x.path().unwrap()).collect::<Vec<_>>();
        assert!(drop_plan(&[a.clone(), dd.clone()], &g).is_empty()); // already there
        assert!(drop_plan(&[a.clone()], &gio::File::for_path(format!("{}/", d.path().display()))).is_empty()); // other spelling
        assert!(drop_plan(&[dd.clone()], &dd).is_empty()); // onto itself
        assert!(drop_plan(&[dd.clone()], &sub).is_empty()); // into its own subfolder
        assert!(drop_plan(&[a.clone(), dd.clone()], &dd).is_empty()); // selection onto a member
        assert_eq!(paths(drop_plan(&[a.clone(), dd.clone()], &other)), [p("a.txt"), p("dir")]);
        assert!(drop_plan(&[sub.clone()], &dd).is_empty()); // its own parent
        assert_eq!(paths(drop_plan(&[sub.clone()], &g)), [p("dir/sub")]);
        std::os::unix::fs::symlink(d.path(), p("other/link")).unwrap(); // same folder via a symlink
        assert!(drop_plan(&[a], &f(&p("other/link"))).is_empty());
    }

    fn spin(until: impl Fn() -> bool) -> bool {
        let ctx = glib::MainContext::default();
        let end = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !until() && std::time::Instant::now() < end {
            ctx.iteration(false);
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        until()
    }

    /// The worker alone (no progress window: no display in tests).
    fn run_worker(sources: Vec<gio::File>, dest: &gio::File, mv: bool, duplicate: bool) -> (Option<String>, usize) {
        let (tx, rx) = mpsc::channel();
        let texts = Texts { contains_moving: "x".into(), contains_copying: "x".into() };
        let w = Worker { sources, dest: dest.clone(), mv, duplicate, cancel: gio::Cancellable::new(), tx, total: 0, done_bytes: 0, last_ui: std::time::Instant::now(), apply_all: None, record: Vec::new(), texts };
        std::thread::spawn(move || w.run());
        loop {
            match rx.recv().unwrap() {
                Msg::Done(err, rec) => return (err, rec.len()),
                Msg::Ask(_, _, reply) => {
                    let _ = reply.send("stop|0".into());
                }
                Msg::Progress(..) => {}
            }
        }
    }

    #[test]
    fn copy_move_duplicate_and_same_place_is_silent() {
        let d = tempfile::tempdir().unwrap();
        let p = |s: &str| d.path().join(s);
        let f = |s: &str| gio::File::for_path(p(s));
        std::fs::create_dir_all(p("src/sub")).unwrap();
        std::fs::write(p("src/sub/f.bin"), vec![7u8; 300_000]).unwrap();
        std::fs::create_dir(p("dst")).unwrap();
        assert_eq!(run_worker(vec![f("src")], &f("dst"), false, false), (None, 1));
        assert_eq!(std::fs::metadata(p("dst/src/sub/f.bin")).unwrap().len(), 300_000);
        run_worker(vec![f("src")], &gio::File::for_path(d.path()), false, true);
        assert!(p("src copy/sub/f.bin").is_file());
        std::fs::create_dir(p("dst2")).unwrap();
        run_worker(vec![f("src copy")], &f("dst2"), true, false);
        assert!(!p("src copy").exists());
        assert!(p("dst2/src copy/sub/f.bin").is_file());
        // onto the folder it is in, or a folder into itself: nothing, no error
        std::fs::write(p("a.txt"), "").unwrap();
        assert_eq!(run_worker(vec![f("a.txt")], &gio::File::for_path(d.path()), true, false), (None, 0));
        assert_eq!(run_worker(vec![f("src")], &f("src/sub"), true, false), (None, 0));
        assert_eq!(run_worker(vec![f("src")], &f("src/sub"), false, false), (None, 0));
        assert_eq!(std::fs::read_dir(p("src/sub")).unwrap().count(), 1);
        // a conflict answered Stop: nothing replaced
        std::fs::write(p("dst/a.txt"), "old").unwrap();
        run_worker(vec![f("a.txt")], &f("dst"), false, false);
        assert_eq!(std::fs::read_to_string(p("dst/a.txt")).unwrap(), "old");
        let _ = spin(|| true);
    }
}

#[cfg(test)]
mod trash_tests {
    use super::*;

    /// Vini: Empty Trash said it failed whenever a folder was in the Trash
    /// -- it went into the folder, and gvfs refuses anything inside a
    /// trashed one. Each item is erased whole, never what's inside.
    #[test]
    fn test_empty_trash_failed_on_a_folder() {
        let items = [gio::File::for_uri("trash:///Pasta"), gio::File::for_uri("trash:///nota.txt")];
        let asked = std::cell::RefCell::new(Vec::new());
        let erase = |f: &gio::File| {
            asked.borrow_mut().push(f.uri().to_string());
            Ok(())
        };
        let n = erase_trashed(&items, &erase, &|_, _| panic!("no error"));
        assert_eq!(n, 2);
        assert_eq!(*asked.borrow(), ["trash:///Pasta", "trash:///nota.txt"]); // the items, never their insides
    }

    /// Vini: emptying the Trash or deleting something heavy in Files showed
    /// no progress at all. The erasing says each step (for the progress
    /// window's bar and the item's name) and stops at the window's Stop.
    #[test]
    fn test_no_progress_while_deleting() {
        let d = tempfile::tempdir().unwrap();
        let items: Vec<gio::File> = ["a", "b", "c"].iter().map(|n| {
            std::fs::write(d.path().join(n), "x").unwrap();
            gio::File::for_path(d.path().join(n))
        }).collect();
        let steps = std::cell::RefCell::new(Vec::new());
        let cancel = gio::Cancellable::new();
        let n = erase_steps(&items, &|f| f.delete(gio::Cancellable::NONE), &|_, _| panic!("no error"), &|done, total, name| steps.borrow_mut().push((done, total, name)), &cancel);
        assert_eq!(n, 3);
        assert_eq!(*steps.borrow(), vec![(0, 3, "a".to_string()), (1, 3, "b".into()), (2, 3, "c".into()), (3, 3, String::new())]);
        // Stop: nothing more erased after it
        let more: Vec<gio::File> = ["d", "e"].iter().map(|n| {
            std::fs::write(d.path().join(n), "x").unwrap();
            gio::File::for_path(d.path().join(n))
        }).collect();
        let cancel = gio::Cancellable::new();
        let n = erase_steps(&more, &|f| {
            cancel.cancel(); // stopped while the first is erased
            f.delete(gio::Cancellable::NONE)
        }, &|_, _| {}, &|_, _, _| {}, &cancel);
        assert_eq!(n, 1);
        assert!(d.path().join("e").exists());
    }
}
