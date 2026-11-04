//! Finding Sonata's own processes by their command lines (what `pgrep -f`
//! did for sonata2/__main__.py's restart and autostart.py), in both
//! shapes a session may run them in:
//!
//!   python3 -m sonata2 <component> [args]     the Python Sonata (`sonata2` launcher)
//!   /path/sonata2-rs <component> [args]       this build (keep.rs: `PORTED`)
//!
//! Read straight from /proc -- never `pgrep -f`, whose pattern also matches
//! the shell that runs it -- and only this user's processes: a restart
//! never signals another user's Sonata (pkill would only have failed).

use std::os::unix::fs::MetadataExt;
use std::path::Path;

/// (pid, argv) of this user's processes under `proc_root` (/proc).
pub fn scan(proc_root: &Path, uid: Option<u32>) -> Vec<(u32, Vec<String>)> {
    let Ok(rd) = std::fs::read_dir(proc_root) else { return Vec::new() };
    let mut out: Vec<(u32, Vec<String>)> = rd
        .flatten()
        .filter_map(|e| {
            let pid: u32 = e.file_name().to_str()?.parse().ok()?;
            if let Some(uid) = uid {
                if e.metadata().ok()?.uid() != uid {
                    return None;
                }
            }
            let raw = std::fs::read(e.path().join("cmdline")).ok()?;
            let argv: Vec<String> = raw.split(|b| *b == 0).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
            let argv = match argv.iter().rposition(|a| !a.is_empty()) {
                Some(last) => argv[..=last].to_vec(),
                None => return None, // a kernel thread, or a zombie
            };
            Some((pid, argv))
        })
        .collect();
    out.sort();
    out
}

/// The real thing: this user's processes, this one left out.
pub fn running() -> Vec<(u32, Vec<String>)> {
    let me = std::process::id();
    let uid = unsafe { libc::getuid() };
    scan(Path::new("/proc"), Some(uid)).into_iter().filter(|(pid, _)| *pid != me).collect()
}

/// What follows `sonata2` on a Sonata command line (`dock`, `keep dock`,
/// `keep-all wallpaper dock,...`), or None for any other process.
pub fn sonata_args(argv: &[String]) -> Option<&[String]> {
    if let Some(i) = argv.windows(2).position(|w| w[0] == "-m" && w[1] == "sonata2") {
        return Some(&argv[i + 2..]);
    }
    let base = Path::new(argv.first()?).file_name()?.to_str()?;
    matches!(base, "sonata2-rs" | "sonata2").then(|| &argv[1..])
}

/// A process running component `name` itself (`sonata2 dock`, `sonata2-rs spotlight --background`).
pub fn is_component(argv: &[String], name: &str) -> bool {
    sonata_args(argv).and_then(|a| a.first()).is_some_and(|a| a == name)
}

/// A `keep <name>` supervisor.
pub fn is_keeper(argv: &[String], name: &str) -> bool {
    sonata_args(argv).is_some_and(|a| a.len() > 1 && a[0] == "keep" && a[1] == name)
}

/// The components a `keep-all` supervises (empty: not one).
pub fn keep_all_names(argv: &[String]) -> Vec<String> {
    let Some(i) = argv.iter().position(|a| a == "keep-all") else { return Vec::new() };
    argv[i + 1..].iter().filter_map(|a| a.split(',').next().filter(|n| !n.is_empty()).map(String::from)).collect()
}

/// A `keep-all` supervisor (of Sonata: its own command line says so).
pub fn is_keep_all(argv: &[String]) -> bool {
    sonata_args(argv).and_then(|a| a.first()).is_some_and(|a| a == "keep-all")
}

/// Kept alive by a supervisor already: `keep <name>`, or a `keep-all` that has it.
pub fn is_kept(argv: &[String], name: &str) -> bool {
    is_keeper(argv, name) || (is_keep_all(argv) && keep_all_names(argv).iter().any(|n| n == name))
}

pub fn pids(procs: &[(u32, Vec<String>)], pred: impl Fn(&[String]) -> bool) -> Vec<u32> {
    procs.iter().filter(|(_, a)| pred(a)).map(|(p, _)| *p).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(s: &str) -> Vec<String> {
        s.split(' ').map(String::from).collect()
    }

    #[test]
    fn both_shapes_of_a_component() {
        assert!(is_component(&v("python3 -m sonata2 dock"), "dock"));
        assert!(is_component(&v("/home/v/.local/bin/sonata2-rs spotlight --background"), "spotlight"));
        assert!(!is_component(&v("python3 -m sonata2 dockx"), "dock"));
        assert!(!is_component(&v("python3 -m sonata2 keep dock"), "dock"));
        assert!(!is_component(&v("vim dock"), "dock"));
        // (pgrep -f "sonata2 dock" matched an editor with that file name, or the shell running pgrep)
        assert!(!is_component(&v("zsh -c pgrep -f sonata2 dock"), "dock"));
        assert!(is_keeper(&v("python3 -m sonata2 keep dock"), "dock"));
        assert!(is_keeper(&v("/usr/bin/sonata2-rs keep polkit"), "polkit"));
        assert!(!is_keeper(&v("python3 -m sonata2 keep docker"), "dock"));
        assert!(is_kept(&v("sonata2-rs keep-all wallpaper polkit,--x"), "polkit"));
        assert!(!is_kept(&v("sonata2-rs keep-all wallpaper"), "polkit"));
    }

    /// tests/test_keep_all.py's test_restart_names: the components a running
    /// `keep-all` supervises, read from its command line.
    #[test]
    fn test_restart_names() {
        assert_eq!(keep_all_names(&v("sh -c sleep sonata2 keep-all dock launchpad,--background")), ["dock", "launchpad"]);
        assert!(keep_all_names(&v("python3 -m sonata2 keep dock")).is_empty());
    }

    #[test]
    fn scan_reads_a_fake_proc() {
        let d = tempfile::tempdir().unwrap();
        for (pid, cmd) in [("11", "python3\0-m\0sonata2\0keep\0dock\0"), ("21", "python3\0-m\0sonata2\0dock\0"), ("30", ""), ("self", "x\0")] {
            std::fs::create_dir(d.path().join(pid)).unwrap();
            std::fs::write(d.path().join(pid).join("cmdline"), cmd).unwrap();
        }
        let procs = scan(d.path(), None);
        assert_eq!(procs.len(), 2); // the empty one (kernel thread) and "self" left out
        assert_eq!(pids(&procs, |a| is_keeper(a, "dock")), [11]);
        assert_eq!(pids(&procs, |a| is_component(a, "dock")), [21]);
        // another user's: never ours
        assert!(scan(d.path(), Some(u32::MAX)).is_empty());
    }
}
