//! `sonata2 wayfire-config` (frame and overrides steps) on recorded cases:
//! the session's copy must come out byte for byte as it did when these
//! steps were Python (tools/wayfire-config.sh's heredocs), or a user's
//! title bars, keys and options change under them after the upgrade.
//!
//! wfconfig_golden/NN.* were written by the Python steps themselves, the
//! last time both existed (2026-11): `.in.ini` the session's config,
//! `.appearance.json` / `.overrides.ini` the user's files, `.frame.ini`
//! after the frame step, `.out.ini` after the overrides, `.overrides-after.ini`
//! the overrides file as the step left it (old key names migrated), `.ok`
//! whether the overrides step succeeded. One change since: XWayland's
//! XSETTINGS manager is `sonata2-rs xsettings` (the Python wrote its own
//! `env PYTHONPATH=... python3 -m sonata2.xsettings`). Every run is in a
//! temporary folder.

use std::path::{Path, PathBuf};

use sonata_backend::wfconfig;

fn golden() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/wfconfig_golden")
}

fn read(p: &Path) -> Option<Vec<u8>> {
    std::fs::read(p).ok()
}

struct Run {
    frame: Vec<u8>,
    out: Vec<u8>,
    ok: bool,
    overrides_after: Vec<u8>,
}

fn run_rust(n: &str) -> Run {
    let g = golden();
    let h = tempfile::tempdir().unwrap();
    let cfg = h.path().join("cfg/sonata2");
    std::fs::create_dir_all(&cfg).unwrap();
    if let Some(a) = read(&g.join(format!("{n}.appearance.json"))) {
        std::fs::write(cfg.join("appearance.json"), a).unwrap();
    }
    let out = h.path().join("out.ini");
    let over = h.path().join("over.ini");
    std::fs::write(&out, read(&g.join(format!("{n}.in.ini"))).unwrap()).unwrap();
    let _ = wfconfig::frame_step_file(&out, &cfg, "/opt/sonata");
    let frame = std::fs::read(&out).unwrap();
    let mut ok = true;
    if let Some(o) = read(&g.join(format!("{n}.overrides.ini"))) {
        std::fs::write(&over, o).unwrap();
        ok = wfconfig::overrides_step_file(&out, &over).is_ok();
    }
    Run { frame, out: std::fs::read(&out).unwrap(), ok, overrides_after: std::fs::read(&over).unwrap_or_default() }
}

#[test]
fn rust_steps_write_what_the_python_steps_wrote() {
    let g = golden();
    let mut names: Vec<String> = std::fs::read_dir(&g).unwrap().flatten().filter_map(|e| e.file_name().to_str()?.strip_suffix(".in.ini").map(String::from)).collect();
    names.sort();
    assert!(names.len() >= 20, "the recorded cases ({})", names.len());
    let mut failed = Vec::new();
    for n in names {
        let name = std::fs::read_to_string(g.join(format!("{n}.name"))).unwrap_or_default();
        let want = |ext: &str| read(&g.join(format!("{n}.{ext}"))).unwrap();
        let rs = run_rust(&n);
        let text = |b: &[u8]| String::from_utf8_lossy(b).into_owned();
        if rs.frame != want("frame.ini") {
            failed.push(format!("{n} {name}: frame step differs\n--- recorded\n{}\n--- rust\n{}", text(&want("frame.ini")), text(&rs.frame)));
        }
        if rs.overrides_after != want("overrides-after.ini") {
            failed.push(format!("{n} {name}: overrides file migrated differently\n--- recorded\n{}\n--- rust\n{}", text(&want("overrides-after.ini")), text(&rs.overrides_after)));
        }
        let ok = want("ok") == b"true";
        if rs.out != want("out.ini") || rs.ok != ok {
            failed.push(format!("{n} {name}: overrides step differs (ok {ok} / {})\n--- recorded\n{}\n--- rust\n{}", rs.ok, text(&want("out.ini")), text(&rs.out)));
        }
    }
    assert!(failed.is_empty(), "{}", failed.join("\n\n"));
}

/// A wayfire-overrides.ini that isn't UTF-8: the step fails (the script
/// then fails and the session uses the plain config).
#[test]
fn broken_overrides_fail() {
    let d = tempfile::tempdir().unwrap();
    let (out, over) = (d.path().join("o.ini"), d.path().join("v.ini"));
    std::fs::write(&out, "[core]\n").unwrap();
    std::fs::write(&over, b"[core]\nx = \xff\n").unwrap();
    assert!(wfconfig::overrides_step_file(&out, &over).is_err());
    assert_eq!(std::fs::read_to_string(&out).unwrap(), "[core]\n");
}
