//! Locked folders in Files (sonata2/vault.py; Vini): Lock Folder… encrypts
//! a folder with gocryptfs -- its files unreadable without the password, in
//! any app, the terminal too. The login password is the key (asked and
//! checked first, like Lock App). The folder itself stays where it was:
//! closed, it is empty (a mount point); opened with the password, gocryptfs
//! shows the files in it again.
//!
//! ```text
//! ~/Docs/Secret/                     the folder (empty while closed)
//! ~/Docs/.Secret.sonata-vault/       its encrypted files (gocryptfs.conf ...)
//! ```
//!
//! Every step that could lose a file copies first and checks what was
//! copied before anything is removed; a failure leaves the originals where
//! they were. The password goes to gocryptfs through a file only this user
//! can read, in the runtime directory (tmpfs), removed at once.
//!
//! Everything here blocks (gocryptfs, copies): call it off the main loop.

use std::collections::HashMap;
use std::io::Write;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

pub const SUFFIX: &str = ".sonata-vault";
/// open's / unlock's error for a wrong password (gocryptfs: exit 12)
pub const WRONG: &str = "wrong";
const MOUNT_WAIT_S: u64 = 20;

/// (done, why not): the error is one of WRONG, "gocryptfs", "folder",
/// "busy", "not empty", "copy", or what the tool said.
pub type Outcome = Result<(), String>;

/// gocryptfs and fusermount are installed.
pub fn available() -> bool {
    crate::run::which("gocryptfs").is_some() && fusermount().is_some()
}

fn fusermount() -> Option<&'static str> {
    ["fusermount3", "fusermount"].into_iter().find(|t| crate::run::which(t).is_some())
}

/// The folder, absolute, without a trailing slash (os.path.abspath).
fn clean(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    let t = s.trim_end_matches('/');
    let p = if t.is_empty() { PathBuf::from("/") } else { PathBuf::from(t) };
    if p.is_absolute() {
        p
    } else {
        std::env::current_dir().map(|d| d.join(&p)).unwrap_or(p)
    }
}

/// Where its encrypted files are: ".<name>.sonata-vault" beside it.
pub fn cipher_dir(path: &Path) -> PathBuf {
    let p = clean(path);
    let name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    p.parent().unwrap_or(Path::new("/")).join(format!(".{name}{SUFFIX}"))
}

pub fn is_vault(path: &Path) -> bool {
    !path.as_os_str().is_empty() && cipher_dir(path).join("gocryptfs.conf").is_file()
}

/// os.path.ismount: on another device than its parent, or the same inode
/// as it (the root).
fn is_mount(p: &Path) -> bool {
    let Ok(me) = std::fs::symlink_metadata(p) else { return false };
    if me.file_type().is_symlink() {
        return false;
    }
    let Ok(up) = std::fs::metadata(p.join("..")) else { return false };
    me.dev() != up.dev() || me.ino() == up.ino()
}

pub fn is_open(path: &Path) -> bool {
    is_vault(path) && is_mount(&clean(path))
}

/// A folder of the user's own, not a vault yet, not a mount point, not
/// their home or a system folder.
pub fn can_lock(path: &Path) -> bool {
    can_lock_in(path, &glib::home_dir())
}

fn can_lock_in(path: &Path, home: &Path) -> bool {
    if path.as_os_str().is_empty() || !path.is_dir() || path.is_symlink() {
        return false;
    }
    let p = clean(path);
    let writable = p.parent().and_then(|d| std::ffi::CString::new(d.as_os_str().as_encoded_bytes()).ok()).is_some_and(|c| unsafe { libc::access(c.as_ptr(), libc::W_OK) } == 0);
    p.starts_with(home) && p != home && !is_vault(&p) && !is_mount(&p) && writable && !p.to_string_lossy().ends_with(SUFFIX)
}

/// The password in a file only this user can read (the runtime directory:
/// tmpfs), removed when dropped.
struct PassFile(PathBuf);

impl PassFile {
    fn new(password: &str) -> std::io::Result<Self> {
        let dir = runtime_dir();
        for n in 0..100u32 {
            let name = dir.join(format!("sonata2-vault-{}-{}-{n}", std::process::id(), glib::monotonic_time()));
            match std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&name) {
                Ok(mut f) => {
                    let pf = PassFile(name);
                    f.write_all(password.as_bytes())?;
                    return Ok(pf);
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(e) => return Err(e),
            }
        }
        Err(std::io::Error::other("passfile"))
    }
}

impl Drop for PassFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|s| !s.is_empty()).map(PathBuf::from).unwrap_or_else(std::env::temp_dir)
}

/// An empty folder to mount a vault on for a moment (in the runtime directory).
fn stage() -> std::io::Result<PathBuf> {
    let dir = runtime_dir();
    for n in 0..100u32 {
        let p = dir.join(format!("sonata2-vault-{}-{}-{n}", std::process::id(), glib::monotonic_time()));
        match std::fs::create_dir(&p) {
            Ok(()) => {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o700));
                return Ok(p);
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e),
        }
    }
    Err(std::io::Error::other("stage"))
}

fn s(p: &Path) -> String {
    p.to_string_lossy().into_owned()
}

/// gocryptfs's output when it failed, else the tool's name.
fn said(out: &str, tool: &str) -> String {
    let t = out.trim();
    if t.is_empty() { tool.to_string() } else { t.to_string() }
}

fn mount(cipher: &Path, at: &Path, password: &str) -> Outcome {
    let pf = PassFile::new(password).map_err(|e| e.to_string())?;
    let (code, out) = crate::run::run(&["gocryptfs", "-q", "-passfile", &s(&pf.0), &s(cipher), &s(at)], MOUNT_WAIT_S);
    drop(pf);
    match code {
        0 => Ok(()),
        12 => Err(WRONG.into()),
        _ => Err(said(&out, "gocryptfs")),
    }
}

fn umount(at: &Path) -> bool {
    let Some(tool) = fusermount() else { return false };
    crate::run::run(&[tool, "-u", &s(at)], MOUNT_WAIT_S).0 == 0
}

/// {relative path: size, or None for a folder or a link} -- what a copy must hold.
fn listing(root: &Path) -> HashMap<PathBuf, Option<u64>> {
    fn walk(root: &Path, dir: &Path, out: &mut HashMap<PathBuf, Option<u64>>) {
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for e in rd.flatten() {
            let p = e.path();
            let rel = p.strip_prefix(root).map(Path::to_path_buf).unwrap_or_default();
            let Ok(m) = std::fs::symlink_metadata(&p) else { continue };
            if m.is_dir() {
                out.insert(rel, None);
                walk(root, &p, out);
            } else {
                out.insert(rel, (!m.file_type().is_symlink()).then_some(m.len()));
            }
        }
    }
    let mut out = HashMap::new();
    walk(root, root, &mut out);
    out
}

/// One item copied (links as links, times kept like shutil.copy2).
fn copy_item(src: &Path, dst: &Path) -> std::io::Result<()> {
    let m = std::fs::symlink_metadata(src)?;
    if m.file_type().is_symlink() {
        let target = std::fs::read_link(src)?;
        if std::fs::symlink_metadata(dst).is_ok() {
            std::fs::remove_file(dst)?;
        }
        std::os::unix::fs::symlink(target, dst)?;
    } else if m.is_dir() {
        if !dst.is_dir() {
            std::fs::create_dir(dst)?;
        }
        for e in std::fs::read_dir(src)? {
            let e = e?;
            copy_item(&e.path(), &dst.join(e.file_name()))?;
        }
        let _ = std::fs::set_permissions(dst, m.permissions());
    } else {
        std::fs::copy(src, dst)?;
        let times = [libc::timespec { tv_sec: m.atime(), tv_nsec: m.atime_nsec() }, libc::timespec { tv_sec: m.mtime(), tv_nsec: m.mtime_nsec() }];
        if let Ok(c) = std::ffi::CString::new(dst.as_os_str().as_encoded_bytes()) {
            unsafe { libc::utimensat(libc::AT_FDCWD, c.as_ptr(), times.as_ptr(), 0) };
        }
    }
    Ok(())
}

/// Everything in src copied into dst; true when dst then holds all of it,
/// at the same sizes.
fn copy_into(src: &Path, dst: &Path) -> std::io::Result<bool> {
    for e in std::fs::read_dir(src)? {
        let e = e?;
        copy_item(&e.path(), &dst.join(e.file_name()))?;
    }
    let have = listing(dst);
    Ok(listing(src).iter().all(|(k, v)| have.get(k) == Some(v)))
}

fn empty(path: &Path) -> std::io::Result<()> {
    for e in std::fs::read_dir(path)? {
        let p = e?.path();
        let m = std::fs::symlink_metadata(&p)?;
        if m.is_dir() {
            std::fs::remove_dir_all(&p)?;
        } else {
            std::fs::remove_file(&p)?;
        }
    }
    Ok(())
}

fn is_empty_dir(p: &Path) -> bool {
    std::fs::read_dir(p).map(|mut r| r.next().is_none()).unwrap_or(false)
}

/// Encrypt the folder: Ok, or Err(why) with nothing changed.
pub fn lock(path: &Path, password: &str) -> Outcome {
    lock_with(path, password, copy_into)
}

fn lock_with(path: &Path, password: &str, copy: fn(&Path, &Path) -> std::io::Result<bool>) -> Outcome {
    if !available() {
        return Err("gocryptfs".into());
    }
    if !can_lock(path) {
        return Err("folder".into());
    }
    let path = clean(path);
    let cipher = cipher_dir(&path);
    std::fs::create_dir_all(&cipher).map_err(|e| e.to_string())?;
    let init = PassFile::new(password).map(|pf| {
        let r = crate::run::run(&["gocryptfs", "-init", "-q", "-passfile", &s(&pf.0), &s(&cipher)], 60);
        drop(pf);
        r
    });
    match init {
        Ok((0, _)) => {}
        Ok((_, out)) => {
            let _ = std::fs::remove_dir_all(&cipher);
            return Err(said(&out, "gocryptfs -init"));
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&cipher);
            return Err(e.to_string());
        }
    }
    let stage = match stage() {
        Ok(s) => s,
        Err(e) => {
            let _ = std::fs::remove_dir_all(&cipher);
            return Err(e.to_string());
        }
    };
    if let Err(e) = mount(&cipher, &stage, password) {
        let _ = std::fs::remove_dir_all(&cipher);
        let _ = std::fs::remove_dir(&stage);
        return Err(e);
    }
    let copied = copy(&path, &stage);
    umount(&stage);
    let _ = std::fs::remove_dir(&stage);
    match copied {
        Ok(true) => {}
        // the originals stay: the vault goes
        Ok(false) => {
            let _ = std::fs::remove_dir_all(&cipher);
            return Err("copy".into());
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&cipher);
            return Err(e.to_string());
        }
    }
    empty(&path).map_err(|e| e.to_string()) // only now: they're all in the vault
}

/// Its files shown in the folder again (mounted there).
pub fn open(path: &Path, password: &str) -> Outcome {
    if is_open(path) {
        return Ok(());
    }
    if !is_vault(path) {
        return Err("folder".into());
    }
    let path = clean(path);
    if !is_empty_dir(&path) {
        return Err("not empty".into()); // (something was put in the closed folder)
    }
    mount(&cipher_dir(&path), &path, password)
}

/// Its files out of sight again (unmounted). False: in use (a file open).
pub fn close(path: &Path) -> bool {
    if !is_open(path) {
        return true;
    }
    umount(&clean(path))
}

/// Decrypt it for good: a plain folder again, its vault removed.
pub fn unlock(path: &Path, password: &str) -> Outcome {
    if !is_vault(path) {
        return Err("folder".into());
    }
    let path = clean(path);
    if is_open(&path) && !close(&path) {
        return Err("busy".into());
    }
    if !is_empty_dir(&path) {
        return Err("not empty".into());
    }
    let stage = stage().map_err(|e| e.to_string())?;
    if let Err(e) = mount(&cipher_dir(&path), &stage, password) {
        let _ = std::fs::remove_dir(&stage);
        return Err(e);
    }
    let copied = copy_into(&stage, &path);
    umount(&stage);
    let _ = std::fs::remove_dir(&stage);
    match copied {
        Ok(true) => {}
        // the vault stays: what was copied goes
        other => {
            let _ = empty(&path);
            return Err(match other {
                Err(e) => e.to_string(),
                _ => "copy".into(),
            });
        }
    }
    let _ = std::fs::remove_dir_all(cipher_dir(&path));
    Ok(())
}

/// The locked folders open now (gocryptfs mounts of a "<name>.sonata-vault").
pub fn open_vaults(mounts: &str) -> Vec<String> {
    mounts
        .lines()
        .filter_map(|l| {
            let p: Vec<&str> = l.split_whitespace().collect();
            (p.len() >= 3 && p[2] == "fuse.gocryptfs" && p[0].ends_with(SUFFIX)).then(|| p[1].replace("\\040", " "))
        })
        .collect()
}

/// Every locked folder closed (unmounted): the session ends, the screen
/// locks. One in use stays open.
pub fn close_all() {
    let Some(tool) = fusermount() else { return };
    for path in open_vaults(&std::fs::read_to_string("/proc/self/mounts").unwrap_or_default()) {
        crate::run::run(&[tool, "-u", &path], 30);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_vaults_from_the_mount_table() {
        let mounts = "/home/v/.Taxes.sonata-vault /home/v/Taxes fuse.gocryptfs rw 0 0\n/dev/sda1 / ext4 rw 0 0\n/home/v/.My\\040Stuff.sonata-vault /home/v/My\\040Stuff fuse.gocryptfs rw 0 0\nother /x fuse.gocryptfs rw 0 0\n";
        assert_eq!(super::open_vaults(mounts), ["/home/v/Taxes", "/home/v/My Stuff"]);
    }

    /// test_vault.Rules: a folder of the user's own, never the home itself
    /// or anything outside it.
    #[test]
    fn what_can_be_locked() {
        let home = tempfile::tempdir().unwrap();
        let d = home.path().join("Docs").join("Mine");
        std::fs::create_dir_all(&d).unwrap();
        assert!(can_lock_in(&d, home.path()));
        assert!(!can_lock_in(home.path(), home.path())); // not the home itself
        assert!(!can_lock_in(Path::new("/usr"), home.path())); // nothing outside it
        assert!(!can_lock_in(&home.path().join("nope"), home.path()));
        assert_eq!(cipher_dir(&d), home.path().join("Docs").join(format!(".Mine{SUFFIX}")));
        assert_eq!(cipher_dir(Path::new("/h/Mine/")), PathBuf::from("/h/.Mine.sonata-vault"));
        // a vault already: not locked twice
        std::fs::create_dir_all(cipher_dir(&d)).unwrap();
        std::fs::write(cipher_dir(&d).join("gocryptfs.conf"), "{}").unwrap();
        assert!(is_vault(&d) && !is_open(&d));
        assert!(!can_lock_in(&d, home.path()));
    }

    /// A copy is checked file by file (sizes, folders, links) before
    /// anything is removed.
    #[test]
    fn copies_are_checked() {
        let d = tempfile::tempdir().unwrap();
        let (a, b) = (d.path().join("a"), d.path().join("b"));
        std::fs::create_dir_all(a.join("sub")).unwrap();
        std::fs::create_dir(&b).unwrap();
        std::fs::write(a.join("note.txt"), "TOP").unwrap();
        std::fs::write(a.join("sub").join("x.bin"), vec![7u8; 70000]).unwrap();
        std::os::unix::fs::symlink("note.txt", a.join("link")).unwrap();
        assert!(copy_into(&a, &b).unwrap());
        assert_eq!(std::fs::read_link(b.join("link")).unwrap(), PathBuf::from("note.txt"));
        assert_eq!(std::fs::metadata(b.join("sub").join("x.bin")).unwrap().len(), 70000);
        std::fs::write(b.join("note.txt"), "T").unwrap(); // a short copy: not the same
        assert!(listing(&a) != listing(&b));
        empty(&b).unwrap();
        assert!(is_empty_dir(&b));
    }

    fn fuse() -> bool {
        available() && std::fs::OpenOptions::new().read(true).write(true).open("/dev/fuse").is_ok()
    }

    fn secret_folder() -> (tempfile::TempDir, PathBuf) {
        let d = tempfile::tempdir_in(glib::home_dir()).unwrap();
        let p = d.path().join("Secret");
        std::fs::create_dir_all(p.join("sub")).unwrap();
        std::fs::write(p.join("note.txt"), "TOP-SECRET-CONTENT").unwrap();
        std::fs::write(p.join("sub").join("b.bin"), vec![3u8; 70000]).unwrap();
        std::os::unix::fs::symlink("note.txt", p.join("link")).unwrap();
        (d, p)
    }

    fn plain_anywhere(root: &Path) -> bool {
        listing(root).keys().any(|k| std::fs::read(root.join(k)).is_ok_and(|b| b.windows(10).any(|w| w == b"TOP-SECRET")))
    }

    /// test_vault.RealVault, with the real gocryptfs and FUSE when they're
    /// there: lock, a wrong password, open, close, unlock for good.
    #[test]
    fn lock_open_close_unlock() {
        if !fuse() || !glib::home_dir().starts_with(std::env::temp_dir()) {
            return; // (only in a test's own home)
        }
        let (_d, p) = secret_folder();
        assert_eq!(lock(&p, "s3cret-login"), Ok(()));
        assert!(is_vault(&p) && is_empty_dir(&p)); // closed: nothing to read
        assert!(!plain_anywhere(&cipher_dir(&p))); // encrypted on disk
        assert_eq!(open(&p, "wrong-one"), Err(WRONG.into()));
        assert!(!is_open(&p));
        assert_eq!(open(&p, "s3cret-login"), Ok(()));
        assert!(is_open(&p));
        assert_eq!(std::fs::read_to_string(p.join("note.txt")).unwrap(), "TOP-SECRET-CONTENT");
        assert_eq!(std::fs::metadata(p.join("sub").join("b.bin")).unwrap().len(), 70000);
        assert_eq!(std::fs::read_link(p.join("link")).unwrap(), PathBuf::from("note.txt"));
        assert!(open_vaults(&std::fs::read_to_string("/proc/self/mounts").unwrap()).contains(&s(&p)));
        assert!(close(&p));
        assert!(is_empty_dir(&p));
        assert_eq!(unlock(&p, "wrong-one"), Err(WRONG.into()));
        assert!(is_vault(&p)); // nothing changed
        assert_eq!(unlock(&p, "s3cret-login"), Ok(()));
        assert!(!is_vault(&p) && !cipher_dir(&p).exists());
        assert_eq!(std::fs::read_to_string(p.join("note.txt")).unwrap(), "TOP-SECRET-CONTENT");
    }

    /// test_a_failed_copy_loses_nothing: the originals stay where they were.
    #[test]
    fn a_failed_copy_loses_nothing() {
        if !fuse() || !glib::home_dir().starts_with(std::env::temp_dir()) {
            return;
        }
        let (_d, p) = secret_folder();
        assert_eq!(lock_with(&p, "pw", |_, _| Ok(false)), Err("copy".into()));
        assert!(!is_vault(&p) && !cipher_dir(&p).exists());
        assert_eq!(std::fs::read_to_string(p.join("note.txt")).unwrap(), "TOP-SECRET-CONTENT");
    }
}
