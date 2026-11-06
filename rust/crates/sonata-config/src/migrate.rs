//! Upgrading to the Rust-only Sonata without losing anything
//! (`sonata2 migrate`, run by install.sh once the new build is in place).
//!
//! What it carries over, and from where:
//!
//! - **The Rust trial's settings**: `~/.config/sonata2-rs/` (what the Rust
//!   build saved while it ran next to the Python Sonata) into
//!   `~/.config/sonata2/`.
//! - **The Rust trial's app data**: `~/.local/share/sonata2-rs-data/<app>`
//!   (TextEdit's and Notes' own copies on trial) into
//!   `~/.local/share/sonata2-data/<app>`.
//! - **App data inside the old install folder** (notes, calendars,
//!   TextEdit's tabs, web apps -- where Sonata kept them before
//!   sonata2-data existed): `<prefix>/share/sonata2/<app>` into
//!   `~/.local/share/sonata2-data/<app>`.
//!
//! Merging never loses a file: one missing on the other side is copied
//! (bytes, permissions and time as they were); the same bytes are left
//! alone; two different files keep the **newer** one -- the one replaced
//! goes to the backup folder first, at the same relative path. Every copy
//! is read back and compared before the next step, and the source is only
//! moved to the backup folder (never deleted) once all of it is merged.
//!
//! What the old install leaves behind -- the Python package folder
//! (`<prefix>/share/sonata2`), its `sonata2` launcher, the "Sonata (Rust
//! preview)" launcher (`sonata-rs-session`) and the trial's loose copies
//! in `<prefix>/share/sonata2-rs` -- is moved to the backup folder too
//! (Python's `__pycache__` folders, a cache, are the only thing dropped).
//! `~/.config/sonata2` and `~/.local/share/sonata2-data` themselves are
//! never moved or rewritten: your settings stay byte for byte.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// The app folders the old install folder may hold (sonata2/userdata.py's
/// APPS, plus the ones added later).
pub const OLD_APP_FOLDERS: [&str; 7] = ["notes", "calendar", "textedit", "webapps", "music", "clock", "assistant"];

/// What one run did, for the installer to print.
#[derive(Debug, Default, PartialEq)]
pub struct Report {
    /// copied where nothing was
    pub copied: Vec<PathBuf>,
    /// replaced by a newer file (the older one is in the backup)
    pub replaced: Vec<PathBuf>,
    /// kept: the file already there was the newer one
    pub kept_newer: Vec<PathBuf>,
    /// moved into the backup folder: (from, to)
    pub set_aside: Vec<(PathBuf, PathBuf)>,
}

impl Report {
    fn add(&mut self, other: Report) {
        self.copied.extend(other.copied);
        self.replaced.extend(other.replaced);
        self.kept_newer.extend(other.kept_newer);
        self.set_aside.extend(other.set_aside);
    }

    pub fn lines(&self) -> Vec<String> {
        let mut out = Vec::new();
        for p in &self.copied {
            out.push(format!("  carried over: {}", p.display()));
        }
        for p in &self.replaced {
            out.push(format!("  updated with a newer copy (the older one is in the backup): {}", p.display()));
        }
        for p in &self.kept_newer {
            out.push(format!("  kept (newer than the trial's copy): {}", p.display()));
        }
        for (from, to) in &self.set_aside {
            out.push(format!("  moved to the backup: {} -> {}", from.display(), to.display()));
        }
        out
    }
}

fn same_bytes(a: &Path, b: &Path) -> io::Result<bool> {
    let (ma, mb) = (fs::metadata(a)?, fs::metadata(b)?);
    if ma.len() != mb.len() {
        return Ok(false);
    }
    Ok(fs::read(a)? == fs::read(b)?)
}

fn mtime(p: &Path) -> std::time::SystemTime {
    fs::metadata(p).and_then(|m| m.modified()).unwrap_or(std::time::UNIX_EPOCH)
}

/// `from` copied to `to` (a new file next to it, then renamed: never half
/// written), with its permissions and time; read back and compared.
fn copy_verified(from: &Path, to: &Path) -> io::Result<()> {
    if let Some(parent) = to.parent() {
        fs::create_dir_all(parent)?;
    }
    let name = to.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = to.with_file_name(format!(".{name}.sonata-migrate.{}", std::process::id()));
    fs::copy(from, &tmp)?;
    let time = mtime(from);
    fs::File::options().write(true).open(&tmp)?.set_modified(time)?;
    if !same_bytes(from, &tmp)? {
        let _ = fs::remove_file(&tmp);
        return Err(io::Error::other(format!("{} didn't copy right", from.display())));
    }
    fs::rename(&tmp, to)
}

/// Move `path` into `backup` at `rel`: a rename, else (another disk) a
/// copy that is compared before the original goes.
pub fn set_aside(path: &Path, backup: &Path, rel: &Path) -> io::Result<PathBuf> {
    let dest = backup.join(rel);
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    let dest = free_name(&dest);
    if fs::rename(path, &dest).is_err() {
        copy_any(path, &dest)?;
        verify_tree(path, &dest)?;
        remove_any(path)?;
    }
    Ok(dest)
}

/// `p`, or `p.1`, `p.2`... when it is taken (two runs the same second).
fn free_name(p: &Path) -> PathBuf {
    if p.symlink_metadata().is_err() {
        return p.to_path_buf();
    }
    (1..).map(|i| PathBuf::from(format!("{}.{i}", p.display()))).find(|c| c.symlink_metadata().is_err()).unwrap()
}

fn copy_any(from: &Path, to: &Path) -> io::Result<()> {
    let meta = from.symlink_metadata()?;
    if meta.file_type().is_symlink() {
        std::os::unix::fs::symlink(fs::read_link(from)?, to)
    } else if meta.is_dir() {
        fs::create_dir_all(to)?;
        for e in fs::read_dir(from)? {
            let e = e?;
            copy_any(&e.path(), &to.join(e.file_name()))?;
        }
        Ok(())
    } else {
        copy_verified(from, to)
    }
}

fn verify_tree(a: &Path, b: &Path) -> io::Result<()> {
    let meta = a.symlink_metadata()?;
    let ok = if meta.file_type().is_symlink() {
        fs::read_link(a)? == fs::read_link(b)?
    } else if meta.is_dir() {
        for e in fs::read_dir(a)? {
            let e = e?;
            verify_tree(&e.path(), &b.join(e.file_name()))?;
        }
        true
    } else {
        same_bytes(a, b)?
    };
    if ok { Ok(()) } else { Err(io::Error::other(format!("{} differs from its copy", a.display()))) }
}

fn remove_any(p: &Path) -> io::Result<()> {
    if p.symlink_metadata()?.is_dir() { fs::remove_dir_all(p) } else { fs::remove_file(p) }
}

/// Every file (and link) under `dir`, relative to it; folders followed,
/// links not.
fn files(dir: &Path) -> io::Result<Vec<PathBuf>> {
    fn walk(base: &Path, dir: &Path, out: &mut Vec<PathBuf>) -> io::Result<()> {
        for e in fs::read_dir(dir)? {
            let e = e?;
            let p = e.path();
            if e.file_type()?.is_dir() {
                walk(base, &p, out)?;
            } else {
                out.push(p.strip_prefix(base).unwrap_or(&p).to_path_buf());
            }
        }
        Ok(())
    }
    let mut out = Vec::new();
    walk(dir, dir, &mut out)?;
    out.sort();
    Ok(out)
}

/// `from` merged into `to` (see the module's notes). `backup` gets the
/// files of `to` a newer one replaced. `from` is left as it was.
pub fn merge_tree(from: &Path, to: &Path, backup: &Path) -> io::Result<Report> {
    let mut r = Report::default();
    if !from.is_dir() {
        return Ok(r);
    }
    for rel in files(from)? {
        let (src, dst) = (from.join(&rel), to.join(&rel));
        let src_link = src.symlink_metadata()?.file_type().is_symlink();
        match dst.symlink_metadata() {
            Err(_) => {
                if src_link {
                    if let Some(parent) = dst.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    std::os::unix::fs::symlink(fs::read_link(&src)?, &dst)?;
                } else {
                    copy_verified(&src, &dst)?;
                }
                r.copied.push(dst);
            }
            Ok(m) if src_link || m.file_type().is_symlink() || m.is_dir() => {
                // a link, or a folder where the other side has a file: both
                // kept -- the trial's goes to the backup with the rest
                r.kept_newer.push(dst);
            }
            Ok(_) => {
                if same_bytes(&src, &dst)? {
                    continue;
                }
                if mtime(&src) > mtime(&dst) {
                    let old = backup.join(&rel);
                    if let Some(parent) = old.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    copy_verified(&dst, &free_name(&old))?;
                    copy_verified(&src, &dst)?;
                    r.replaced.push(dst);
                } else {
                    r.kept_newer.push(dst);
                }
            }
        }
    }
    Ok(r)
}

/// `from` merged into `to`, then (all of it merged, every copy checked)
/// moved into the backup as `name`. Nothing to do when `from` is missing.
fn merge_and_set_aside(from: &Path, to: &Path, backup: &Path, name: &str) -> io::Result<Report> {
    if from.symlink_metadata().is_err() {
        return Ok(Report::default());
    }
    let mut r = merge_tree(from, to, &backup.join("replaced").join(name))?;
    let dest = set_aside(from, backup, Path::new(name))?;
    r.set_aside.push((from.to_path_buf(), dest));
    Ok(r)
}

/// The user's side: the trial's settings and app data into the real
/// folders. `config_home` is $XDG_CONFIG_HOME, `data_home` $XDG_DATA_HOME.
pub fn user(config_home: &Path, data_home: &Path, backup: &Path) -> io::Result<Report> {
    let mut r = Report::default();
    r.add(merge_and_set_aside(&config_home.join("sonata2-rs"), &config_home.join(crate::NAME), backup, "config-sonata2-rs")?);
    r.add(merge_and_set_aside(&data_home.join("sonata2-rs-data"), &data_home.join("sonata2-data"), backup, "data-sonata2-rs-data")?);
    Ok(r)
}

/// True for the Python Sonata's launcher (a script running `python3 -m
/// sonata2`), not for a link to the Rust binary.
pub fn is_python_launcher(p: &Path) -> bool {
    let Ok(m) = p.symlink_metadata() else { return false };
    m.is_file() && fs::read(p).is_ok_and(|b| String::from_utf8_lossy(&b).contains("python3 -m sonata2"))
}

fn drop_pycache(dir: &Path) {
    let Ok(entries) = fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let p = e.path();
        if e.file_type().is_ok_and(|t| t.is_dir()) {
            if p.file_name().is_some_and(|n| n == "__pycache__") {
                let _ = fs::remove_dir_all(&p);
            } else {
                drop_pycache(&p);
            }
        }
    }
}

/// The install's side, in `prefix` (~/.local or /usr/local): the old
/// Python install and the trial's leftovers moved to `backup`, after the
/// app data kept in the old install folder is merged into
/// `data_home/sonata2-data` (None: an install for everyone, whose folder
/// held no one's data).
pub fn install(prefix: &Path, data_home: Option<&Path>, backup: &Path) -> io::Result<Report> {
    let mut r = Report::default();
    let share = prefix.join("share");
    let old = share.join("sonata2");
    let is_python = old.join("sonata2").join("__init__.py").is_file() || old.symlink_metadata().is_ok_and(|m| m.file_type().is_symlink());
    if is_python {
        if let Some(data) = data_home {
            // (a --dev install's link: the clone's app folders, as they're read through it)
            for app in OLD_APP_FOLDERS {
                let from = old.join(app);
                if from.is_dir() {
                    r.add(merge_tree(&from, &data.join("sonata2-data").join(app), &backup.join("replaced").join("data-sonata2-data").join(app))?);
                }
            }
        }
        if !old.symlink_metadata()?.file_type().is_symlink() {
            drop_pycache(&old);
        }
        let dest = set_aside(&old, backup, Path::new("python-install"))?;
        r.set_aside.push((old, dest));
    }
    let bin = prefix.join("bin");
    let launcher = bin.join("sonata2");
    if is_python_launcher(&launcher) {
        let dest = set_aside(&launcher, backup, Path::new("bin/sonata2"))?;
        r.set_aside.push((launcher, dest));
    }
    let preview = bin.join("sonata-rs-session");
    if preview.symlink_metadata().is_ok() {
        let dest = set_aside(&preview, backup, Path::new("bin/sonata-rs-session"))?;
        r.set_aside.push((preview, dest));
    }
    // the trial's copies, loose in its folder (the installer lays them out
    // in data/ now); its locale and config/ are replaced as they are
    let trial = share.join("sonata2-rs");
    for name in ["icons", "sounds", "wallpapers", "avatars"] {
        let p = trial.join(name);
        if p.symlink_metadata().is_ok() {
            let dest = set_aside(&p, backup, &Path::new("rust-trial").join(name))?;
            r.set_aside.push((p, dest));
        }
    }
    Ok(r)
}

/// `$XDG_DATA_HOME/sonata2-backups/upgrade-<when>`: where this run's backup goes.
pub fn backup_dir(data_home: &Path) -> PathBuf {
    let secs = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0);
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    let t = secs as libc::time_t;
    unsafe { libc::localtime_r(&t, &mut tm) };
    let when = format!("{:04}{:02}{:02}-{:02}{:02}{:02}", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec);
    data_home.join("sonata2-backups").join(format!("upgrade-{when}"))
}

fn data_home() -> PathBuf {
    match std::env::var_os("XDG_DATA_HOME") {
        Some(v) if !v.is_empty() => PathBuf::from(v),
        _ => crate::home().join(".local").join("share"),
    }
}

/// `sonata2 migrate user [--backup DIR]` and
/// `sonata2 migrate install --prefix PREFIX [--system] [--backup DIR]`.
pub fn main(args: &[String]) -> i32 {
    let opt = |name: &str| args.iter().position(|a| a == name).and_then(|i| args.get(i + 1)).map(PathBuf::from);
    let data = data_home();
    let result = match args.first().map(String::as_str) {
        Some("user") => {
            let backup = opt("--backup").unwrap_or_else(|| backup_dir(&data));
            user(&crate::config_home(), &data, &backup).map(|r| (r, backup))
        }
        Some("install") => {
            let Some(prefix) = opt("--prefix") else {
                eprintln!("sonata2 migrate install: --prefix PREFIX");
                return 2;
            };
            let system = args.iter().any(|a| a == "--system");
            let backup = opt("--backup").unwrap_or_else(|| if system { backup_dir(&prefix.join("share")) } else { backup_dir(&data) });
            install(&prefix, (!system).then_some(data.as_path()), &backup).map(|r| (r, backup))
        }
        _ => {
            eprintln!("usage: sonata2 migrate user [--backup DIR] | install --prefix PREFIX [--system] [--backup DIR]");
            return 2;
        }
    };
    match result {
        Ok((r, backup)) => {
            for l in r.lines() {
                println!("{l}");
            }
            if backup.exists() {
                println!("  backup: {}", backup.display());
            }
            0
        }
        Err(e) => {
            eprintln!("sonata2 migrate: {e} (nothing was deleted; what was merged so far stays)");
            1
        }
    }
}

#[cfg(test)]
mod tests;
