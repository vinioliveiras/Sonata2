//! Empties old Trash items (sonata2/trash_cleanup.py; Settings > Security &
//! Privacy: "Remove items from the Trash after 30 days"; GNOME's
//! housekeeping does this elsewhere).
//!
//! Reads the freedesktop Trash of the home folder: each item in files/ has
//! an info/<name>.trashinfo with its DeletionDate. Items deleted more than
//! `days` ago go for good. The menu bar runs it at login and every 6 hours,
//! off the main loop, only while the setting is on (org.gnome.desktop.privacy
//! remove-old-trash-files / old-files-age, the keys Settings writes).

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;

pub const EVERY_S: u32 = 6 * 3600;
/// The first look: once login has settled.
pub const FIRST_S: u32 = 60;
const PRIVACY: &str = "org.gnome.desktop.privacy";

pub fn trash_dir() -> PathBuf {
    crate::util::data_home().join("Trash")
}

/// Days since 1970-01-01 of a proleptic Gregorian date (Howard Hinnant's
/// days_from_civil): dates compared as plain numbers, like Python's naive
/// datetimes.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (m + if m > 2 { -3 } else { 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// A local wall-clock time as seconds on that same naive scale.
fn naive(y: i64, mo: i64, d: i64, h: i64, mi: i64, s: f64) -> f64 {
    (days_from_civil(y, mo, d) * 86400 + h * 3600 + mi * 60) as f64 + s
}

/// The local wall-clock time of a Unix time, on the naive scale.
fn local_naive(epoch: i64) -> f64 {
    let t = epoch as libc::time_t;
    let mut tm: libc::tm = unsafe { std::mem::zeroed() };
    unsafe {
        libc::localtime_r(&t, &mut tm);
    }
    naive(tm.tm_year as i64 + 1900, tm.tm_mon as i64 + 1, tm.tm_mday as i64, tm.tm_hour as i64, tm.tm_min as i64, tm.tm_sec as f64)
}

/// Now, local, on the naive scale (datetime.now()).
pub fn now() -> f64 {
    let d = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    local_naive(d.as_secs() as i64) + d.subsec_nanos() as f64 / 1e9
}

fn num(s: &str, digits: usize) -> Option<i64> {
    (s.len() == digits && s.bytes().all(|b| b.is_ascii_digit())).then(|| s.parse().ok()).flatten()
}

/// datetime.fromisoformat for a DeletionDate, on the naive local scale:
/// "2026-10-01T12:00:00" (the spec's form), with or without seconds or a
/// fraction, a date alone, or with an offset ("...Z" from some tools, or
/// "+01:00") -- that one turned into local time like the others.
pub fn parse_date(text: &str) -> Option<f64> {
    let text = text.trim();
    let (date, time) = match text.find(['T', ' ']) {
        Some(i) => (&text[..i], &text[i + 1..]),
        None => (text, ""),
    };
    let mut dp = date.split('-');
    let (y, mo, d) = (num(dp.next()?, 4)?, num(dp.next()?, 2)?, num(dp.next()?, 2)?);
    if dp.next().is_some() || !(1..=12).contains(&mo) || !(1..=31).contains(&d) {
        return None;
    }
    // the offset, if any: Z, +HH:MM, -HH:MM (+HHMM too)
    let (clock, offset) = if let Some(c) = time.strip_suffix('Z') {
        (c, Some(0))
    } else if let Some(i) = time.rfind(['+', '-']) {
        let o = time[i + 1..].replace(':', "");
        let (oh, om) = (num(o.get(..2)?, 2)?, if o.len() > 2 { num(o.get(2..4)?, 2)? } else { 0 });
        let sign = if time.as_bytes()[i] == b'-' { -1 } else { 1 };
        (&time[..i], Some(sign * (oh * 3600 + om * 60)))
    } else {
        (time, None)
    };
    let (mut h, mut mi, mut s) = (0, 0, 0.0);
    if !clock.is_empty() {
        let mut cp = clock.split(':');
        h = num(cp.next()?, 2)?;
        mi = num(cp.next()?, 2)?;
        if let Some(sec) = cp.next() {
            let (whole, frac) = sec.split_once('.').unwrap_or((sec, ""));
            s = num(whole, 2)? as f64;
            if !frac.is_empty() {
                if !frac.bytes().all(|b| b.is_ascii_digit()) {
                    return None;
                }
                s += format!("0.{frac}").parse::<f64>().ok()?;
            }
        }
        if cp.next().is_some() || h > 23 || mi > 59 || s >= 60.0 {
            return None;
        }
    }
    let wall = naive(y, mo, d, h, mi, s);
    Some(match offset {
        None => wall,
        // as Unix time, then the local wall clock of that moment (the spec's naive form)
        Some(off) => {
            let epoch = wall - off as f64;
            local_naive(epoch.floor() as i64) + epoch.fract()
        }
    })
}

pub fn deletion_date(info: &Path) -> Option<f64> {
    let bytes = std::fs::read(info).ok()?;
    let text = String::from_utf8_lossy(&bytes);
    let line = text.lines().find(|l| l.starts_with("DeletionDate="))?;
    parse_date(&line["DeletionDate=".len()..])
}

/// Remove items deleted more than `days` days ago; returns how many.
/// `root`: the Trash (default: the home folder's); `now`: on the naive
/// local scale (default: now).
pub fn purge(days: i64, root: Option<&Path>, now: Option<f64>) -> usize {
    let root = root.map(Path::to_path_buf).unwrap_or_else(trash_dir);
    let now = now.unwrap_or_else(self::now);
    let (info_dir, files_dir) = (root.join("info"), root.join("files"));
    let Ok(names) = std::fs::read_dir(&info_dir) else { return 0 };
    let mut gone = 0;
    for entry in names.flatten() {
        let name = entry.file_name();
        let Some(item) = name.to_str().and_then(|n| n.strip_suffix(".trashinfo")) else { continue };
        let Some(when) = deletion_date(&entry.path()) else { continue };
        if now - when < (days * 86400) as f64 {
            continue;
        }
        let path = files_dir.join(item);
        // a trashed link goes itself: the folder it points to stays
        let removed = match std::fs::symlink_metadata(&path) {
            Ok(m) if m.is_dir() => std::fs::remove_dir_all(&path),
            Ok(_) => std::fs::remove_file(&path),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e),
        };
        if removed.is_ok() && std::fs::remove_file(entry.path()).is_ok() {
            gone += 1;
        }
    }
    gone
}

/// The setting's days (Settings writes them; 30 when unreadable), or None
/// while "Remove items from the Trash after 30 days" is off.
fn wanted_days() -> Option<i64> {
    if crate::prefs_write::get(PRIVACY, "remove-old-trash-files") != "true" {
        return None;
    }
    let days = crate::prefs_write::get(PRIVACY, "old-files-age");
    let days = if days.is_empty() { Ok(30) } else { days.parse::<i64>() };
    Some(days.unwrap_or(30).max(1))
}

/// The menu bar's timer: purge when the setting is on. Started once by the
/// main display's menu bar (shell/topbar.py) and kept for its life; dropping
/// it (or `stop`) ends the timers. Each look runs in a thread.
pub struct Housekeeping {
    srcs: Rc<RefCell<Vec<glib::SourceId>>>,
}

impl Housekeeping {
    pub fn start() -> Option<Self> {
        let srcs: Rc<RefCell<Vec<glib::SourceId>>> = Rc::default();
        let first = glib::timeout_add_seconds_local(FIRST_S, {
            let srcs = Rc::downgrade(&srcs);
            move || {
                if let Some(s) = srcs.upgrade() {
                    s.borrow_mut().remove(0); // (fired: never removed again)
                }
                run();
                glib::ControlFlow::Break
            }
        });
        let every = glib::timeout_add_seconds_local(EVERY_S, || {
            run();
            glib::ControlFlow::Continue
        });
        srcs.borrow_mut().extend([first, every]);
        Some(Housekeeping { srcs })
    }

    pub fn stop(&self) {
        for s in self.srcs.borrow_mut().drain(..) {
            sonata_ui::cancel(s);
        }
    }
}

impl Drop for Housekeeping {
    fn drop(&mut self) {
        self.stop();
    }
}

fn run() {
    std::thread::Builder::new()
        .name("housekeeping".into())
        .spawn(|| {
            if let Some(days) = wanted_days() {
                purge(days, None, None);
            }
        })
        .ok();
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn at(text: &str) -> f64 {
        parse_date(text).unwrap()
    }

    fn item(root: &Path, name: &str, when: &str) {
        fs::create_dir_all(root.join("info")).unwrap();
        fs::write(root.join("info").join(format!("{name}.trashinfo")), format!("[Trash Info]\nPath=/x/{name}\nDeletionDate={when}\n")).unwrap();
    }

    /// "Remove items from the Trash after 30 days" did nothing in Sonata.
    #[test]
    fn test_old_trash_items_are_removed() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        fs::create_dir_all(root.join("files/olddir")).unwrap();
        fs::write(root.join("files/olddir/inside"), "x").unwrap();
        let now = at("2026-10-01T12:00:00");
        for (name, when) in [("old.txt", "2026-08-22T12:00:00"), ("olddir", "2026-08-31T12:00:00"), ("new.txt", "2026-09-28T12:00:00")] {
            let p = root.join("files").join(name);
            if !p.exists() {
                fs::write(&p, "").unwrap();
            }
            item(root, name, when);
        }
        assert_eq!(purge(30, Some(root), Some(now)), 2);
        let names = |dir: &str| {
            let mut v: Vec<String> = fs::read_dir(root.join(dir)).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
            v.sort();
            v
        };
        assert_eq!(names("files"), ["new.txt"]);
        assert_eq!(names("info"), ["new.txt.trashinfo"]);
    }

    /// A trashed link to a folder goes; the folder it pointed to stays.
    #[test]
    fn test_symlink_in_trash_is_removed_not_followed() {
        let (d, keep) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        fs::write(keep.path().join("precious.txt"), "x").unwrap();
        let root = d.path();
        fs::create_dir_all(root.join("files")).unwrap();
        item(root, "link", "2020-01-01T00:00:00");
        std::os::unix::fs::symlink(keep.path(), root.join("files/link")).unwrap();
        assert_eq!(purge(30, Some(root), Some(at("2026-01-01T00:00:00"))), 1);
        assert!(keep.path().join("precious.txt").exists());
        assert!(fs::symlink_metadata(root.join("files/link")).is_err());
    }

    /// A DeletionDate with an offset ("...Z") is compared like the others.
    #[test]
    fn test_timezone_in_deletion_date_does_not_stop_the_purge() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        fs::create_dir_all(root.join("files")).unwrap();
        for (name, when) in [("a", "2020-01-01T00:00:00Z"), ("b", "2020-01-01T00:00:00")] {
            fs::write(root.join("files").join(name), "x").unwrap();
            item(root, name, when);
        }
        assert_eq!(purge(30, Some(root), Some(at("2026-01-01T00:00:00"))), 2);
    }

    #[test]
    fn dates_read_like_fromisoformat() {
        assert_eq!(at("1970-01-02T00:00:00"), 86400.0);
        assert_eq!(at("2026-10-01T12:00"), at("2026-10-01 12:00:00"));
        assert_eq!(at("2026-10-01"), at("2026-10-01T00:00:00"));
        assert_eq!(at("2026-10-01T12:00:00.5"), at("2026-10-01T12:00:00") + 0.5);
        assert_eq!(at("2024-03-01T00:00:00") - at("2024-02-28T00:00:00"), 2.0 * 86400.0); // a leap year
        // the same moment written with two offsets is the same local time
        assert_eq!(at("2026-10-01T12:00:00+02:00"), at("2026-10-01T10:00:00Z"));
        for bad in ["", "yesterday", "2026-13-01T00:00:00", "2026-10-01T25:00:00", "2026-1-01"] {
            assert_eq!(parse_date(bad), None, "{bad}");
        }
    }

    #[test]
    fn unreadable_dates_and_missing_items_are_safe() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        item(root, "nodate", "soon");
        item(root, "gone", "2020-01-01T00:00:00"); // its file already gone: the info goes too
        assert_eq!(purge(30, Some(root), Some(at("2026-01-01T00:00:00"))), 1);
        assert!(root.join("info/nodate.trashinfo").exists());
        assert_eq!(purge(30, Some(&root.join("nothing")), None), 0);
    }
}
