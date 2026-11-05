//! Running the system's tools (backend/system.py's _run / _spawn / _read):
//! everything here may block -- call it off the main loop.

use std::io::Read;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub fn which(name: &str) -> Option<std::path::PathBuf> {
    if name.contains('/') {
        let p = std::path::PathBuf::from(name);
        return p.is_file().then_some(p);
    }
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|f| f.is_file()))
}

/// (exit code, output): stdout on success; stdout and stderr on failure
/// (the error to show) -- a warning on success would break the output
/// parsed. 127 when the tool isn't installed.
pub fn run(cmd: &[&str], timeout_s: u64) -> (i32, String) {
    if which(cmd[0]).is_none() {
        return (127, String::new());
    }
    let child = Command::new(cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn();
    let mut child = match child {
        Ok(c) => c,
        Err(e) => return (1, e.to_string()),
    };
    let (mut out, mut err) = (child.stdout.take(), child.stderr.take());
    let t_out = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(o) = out.as_mut() {
            let _ = o.read_to_string(&mut s);
        }
        s
    });
    let t_err = std::thread::spawn(move || {
        let mut s = String::new();
        if let Some(e) = err.as_mut() {
            let _ = e.read_to_string(&mut s);
        }
        s
    });
    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    let status = loop {
        match child.try_wait() {
            Ok(Some(st)) => break Some(st),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                break None;
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(10)),
            Err(_) => break None,
        }
    };
    let (so, se) = (t_out.join().unwrap_or_default(), t_err.join().unwrap_or_default());
    match status {
        None => (1, format!("{} timed out", cmd[0])),
        Some(st) if st.success() => (0, so),
        Some(st) => (st.code().unwrap_or(1), format!("{so}{se}")),
    }
}

pub fn ok(cmd: &[&str], timeout_s: u64) -> bool {
    run(cmd, timeout_s).0 == 0
}

/// Start a program detached (its own session, no output).
pub fn spawn(cmd: &[&str]) {
    use std::os::unix::process::CommandExt;
    if which(cmd[0]).is_some() {
        // waited for off the main loop: a child nobody waits for stays a
        // zombie as long as the process lives -- the menu bar gathered one
        // per sound and per lights lock (GLib.spawn_async reaps its own)
        if let Ok(child) = Command::new(cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn() {
            reap(child);
        }
    }
}

/// `child` waited for in a thread of its own, so it never stays a zombie:
/// a `Child` dropped unwaited keeps its process-table entry as long as
/// this process lives (the menu bar gathered defunct flock and sonata2-rs).
pub fn reap(mut child: std::process::Child) {
    let _ = std::thread::Builder::new().name("reap".into()).spawn(move || {
        let _ = child.wait();
    });
}

/// A small file's text, trimmed ("" when it can't be read).
pub fn read(path: &str) -> String {
    std::fs::read(path).map(|b| String::from_utf8_lossy(&b).trim().to_string()).unwrap_or_default()
}

#[cfg(test)]
mod tests {
    /// The menu bar collected zombies -- a defunct flock or sonata2-rs for
    /// every sound and lights lock it started. What spawn starts is reaped.
    #[test]
    fn test_menu_bar_collected_zombies() {
        super::spawn(&["true"]);
        std::thread::sleep(std::time::Duration::from_millis(300));
        let me = std::process::id().to_string();
        let zombies = std::fs::read_dir("/proc").unwrap().flatten().filter(|e| {
            let stat = std::fs::read_to_string(e.path().join("stat")).unwrap_or_default();
            // pid (comm) state ppid ...
            let after = stat.rsplit_once(") ").map(|x| x.1).unwrap_or("");
            let mut f = after.split_whitespace();
            f.next() == Some("Z") && f.next() == Some(me.as_str())
        }).count();
        assert_eq!(zombies, 0);
    }

    /// A child handed to reap is waited for: no zombie once it ends.
    #[test]
    fn test_reaped_child_leaves_no_zombie() {
        let child = std::process::Command::new("true").spawn().unwrap();
        let pid = child.id();
        super::reap(child);
        let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while std::path::Path::new(&format!("/proc/{pid}")).exists() && std::time::Instant::now() < end {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert!(!std::path::Path::new(&format!("/proc/{pid}")).exists(), "a zombie stays in /proc");
    }

    #[test]
    fn output_and_missing_tools() {
        assert_eq!(super::run(&["sh", "-c", "echo hi; echo warn >&2"], 5), (0, "hi\n".into()));
        let (rc, out) = super::run(&["sh", "-c", "echo out; echo err >&2; exit 3"], 5);
        assert_eq!((rc, out.as_str()), (3, "out\nerr\n"));
        assert_eq!(super::run(&["no-such-tool-here"], 5).0, 127);
        assert_eq!(super::run(&["sleep", "5"], 0).0, 1);
    }
}
