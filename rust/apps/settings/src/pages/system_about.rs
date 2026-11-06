//! About (app.py's _page_about): the computer, Restart Sonata and the
//! logs, Backup (export / import, sonata_tweaks::backup) and the two
//! resets (sonata_tweaks::factory_reset).

use crate::kit::{group, run_async, switch_row};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use sonata_i18n::{fmt, gettext, tr, trn};
use sonata_tweaks::{backup, factory_reset};
use std::path::{Path, PathBuf};
use std::rc::{Rc, Weak};

// -- logs (sonata2/logs.py) ----------------------------------------------------------------------
// sonata_session::logs has these, but Settings doesn't link sonata-session:
// the one flag file every part of Sonata reads at its start.
fn logs_flag() -> PathBuf {
    sonata_config::config_home().join("sonata2").join("debug-logging")
}

fn logs_verbose() -> bool {
    std::env::var("SONATA_DEBUG").as_deref() == Ok("1") || logs_flag().exists()
}

fn set_logs_verbose(on: bool) {
    let path = logs_flag();
    if on {
        if let Some(d) = path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(&path, "Sonata writes detailed logs while this file exists.\n");
    } else {
        let _ = std::fs::remove_file(&path);
    }
}

fn alert_ok(ctx: &Settings, heading: &str, body: &str) {
    sonata_ui::dialog::alert(heading, body, &[("ok", tr!("OK"), "default")], |_| {}, ctx.parent().as_ref());
}

pub fn page_about(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let hero = group("", "");
    let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).margin_top(8).margin_bottom(8).build();
    let logo = gtk::Image::builder().pixel_size(96).halign(gtk::Align::Center).build();
    sonata_ui::icons::set_logo(&logo); // the distro's logo (os-release LOGO=)
    bx.append(&logo);
    let name = gtk::Label::builder().label("…").css_classes(["st-about-name"]).build();
    let sub = gtk::Label::builder().label("").css_classes(["st-caption"]).build();
    bx.append(&name);
    bx.append(&sub);
    hero.add(&bx);
    let specs = group("", "");
    let (n, s, sp) = (name.downgrade(), sub.downgrade(), specs.downgrade());
    run_async(sonata_backend::system::about, move |a| {
        let (Some(name), Some(sub), Some(specs)) = (n.upgrade(), s.upgrade(), sp.upgrade()) else { return };
        name.set_label(&a.os_name);
        sub.set_label(&tr!("Sonata 2 desktop"));
        let gpus = if a.gpus.is_empty() { "—".to_string() } else { a.gpus.join(", ") };
        for (k, v) in [
            ("Sonata".to_string(), backup::version().to_string()),
            (tr!("Computer"), a.machine),
            (tr!("Processor"), a.cpu),
            (tr!("Memory"), format!("{} GB", a.memory_gb)),
            (tr!("Graphics"), gpus),
            (tr!("Kernel"), a.kernel),
        ] {
            let row = adw::ActionRow::builder().title(k).build();
            row.add_suffix(&gtk::Label::builder().label(v).css_classes(["st-value"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(40).build());
            specs.add(&row);
        }
    });
    let shell = group("Sonata", "");
    let row = adw::ActionRow::builder()
        .title(tr!("Restart Sonata"))
        .subtitle(fmt(&tr!("Reloads the Dock, menu bar, {apps} and wallpaper. Your apps stay open."), &[("apps", &gettext(sonata_desktop::names::APPS))]))
        .build();
    let w = Rc::downgrade(ctx);
    let btn = sonata_ui::controls::push_button(&tr!("Restart"), move || {
        sonata_backend::system::restart_sonata(&[]);
        if let Some(s) = w.upgrade() {
            s.toast(&tr!("Restarting Sonata…"));
        }
    }, "");
    btn.set_valign(gtk::Align::Center);
    row.add_suffix(&btn);
    row.set_activatable_widget(Some(&btn));
    shell.add(&row);
    // logs: off by default (Vini); on, kept in ~/.cache/sonata2
    let w = Rc::downgrade(ctx);
    shell.add(&switch_row(&tr!("Logs"), logs_verbose(), move |on| {
        set_logs_verbose(on);
        if let Some(s) = w.upgrade() {
            s.ask_restart("sonata", &if on { tr!("Logs on") } else { tr!("Logs off") });
        }
    }, &tr!("Keep detailed logs in ~/.cache/sonata2 for finding problems. Off, only errors are logged, in memory, until you log out.")));
    let backup_g = backup_group(ctx);
    let reset = group(&tr!("Reset"), &tr!("Both ask first, then you log out and back in."));
    for (title, subtitle, what) in [
        (tr!("Reset Settings"), tr!("Every Sonata setting back to the defaults. Your Dock and Launchpad apps, folders and your apps' data stay."), "settings"),
        (tr!("Reset Sonata"), tr!("Sonata as just installed: settings, Dock and Launchpad layout, history and caches. Notes, calendars and other app data go to the Trash."), "everything"),
    ] {
        let row = adw::ActionRow::builder().title(title).subtitle(subtitle).use_markup(false).build();
        let w = Rc::downgrade(ctx);
        let rb = sonata_ui::controls::push_button(&tr!("Reset\u{2026}"), move || {
            if let Some(s) = w.upgrade() {
                ask_factory_reset(&s, what);
            }
        }, "destructive");
        rb.set_valign(gtk::Align::Center);
        row.add_suffix(&rb);
        reset.add(&row);
    }
    vec![hero, specs, shell, backup_g, reset]
}

// -- backup --------------------------------------------------------------------------------------
pub fn last_export_text(ts: Option<i64>) -> String {
    let Some(stamp) = ts.filter(|t| *t != 0).and_then(|t| glib::DateTime::from_unix_local(t).ok()) else { return tr!("Last exported: never") };
    let f = |p: &str| stamp.format(p).map(|s| s.to_string()).unwrap_or_default();
    fmt(&tr!("Last exported: {date} at {time}"), &[("date", &f("%-d %b %Y")), ("time", &f("%H:%M"))])
}

/// What Export reads when it runs: the row to update and the data switch.
struct BackupRows {
    export: adw::ActionRow,
    data: adw::SwitchRow,
}

fn backup_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let g = group(&tr!("Backup"), &tr!("Your Sonata settings in one file: to keep, or to set up another computer the same way."));
    let export = adw::ActionRow::builder().title(tr!("Export Settings")).use_markup(false).subtitle(last_export_text(backup::last_export())).build();
    let data = switch_row(&tr!("Include Notes, Calendar and Reminders"), true, |_| {}, &tr!("The data of Sonata's own apps, with the settings"));
    let rows = Rc::new(BackupRows { export: export.clone(), data: data.clone() });
    let (w, r) = (Rc::downgrade(ctx), Rc::downgrade(&rows));
    let b = sonata_ui::controls::push_button(&tr!("Export\u{2026}"), move || {
        if let (Some(s), Some(r)) = (w.upgrade(), r.upgrade()) {
            export_settings(&s, &r);
        }
    }, "");
    b.set_valign(gtk::Align::Center);
    export.add_suffix(&b);
    g.add(&export);
    g.add(&data);
    let imp = adw::ActionRow::builder().title(tr!("Import Settings")).use_markup(false).subtitle(tr!("Replaces your settings with the file's (yours go to the Trash)")).build();
    let w = Rc::downgrade(ctx);
    let ib = sonata_ui::controls::push_button(&tr!("Import\u{2026}"), move || {
        if let Some(s) = w.upgrade() {
            import_settings(&s);
        }
    }, "");
    ib.set_valign(gtk::Align::Center);
    imp.add_suffix(&ib);
    g.add(&imp);
    ctx.keep("backup_rows", rows);
    g
}

/// The chosen file name with the backup's extension.
pub fn with_ext(path: &Path) -> PathBuf {
    let s = path.to_string_lossy();
    if s.ends_with(backup::EXT) {
        path.to_path_buf()
    } else {
        PathBuf::from(format!("{s}{}", backup::EXT))
    }
}

fn export_settings(ctx: &Ctx, rows: &BackupRows) {
    let dlg = gtk::FileDialog::builder().title(tr!("Export Settings")).modal(true).initial_name(backup::default_name()).build();
    let data = rows.data.is_active();
    let (w, row) = (Rc::downgrade(ctx), rows.export.downgrade());
    dlg.save(Some(&ctx.window), gio::Cancellable::NONE, move |res| {
        // (cancelled: nothing)
        let Some(path) = res.ok().and_then(|f| f.path()) else { return };
        let path = with_ext(&path);
        let (w, row) = (w.clone(), row.clone());
        run_async(move || backup::export(&path, data).map_err(|e| e.to_string()), move |res| {
            let Some(s) = w.upgrade() else { return };
            match res {
                Err(e) => alert_ok(&s, &tr!("The settings couldn't be exported."), &e),
                Ok(n) => {
                    if let Some(r) = row.upgrade() {
                        r.set_subtitle(&last_export_text(backup::last_export()));
                    }
                    s.toast(&fmt(&trn!("Settings exported ({n} file)", "Settings exported ({n} files)", n), &[("n", &n.to_string())]));
                }
            }
        });
    });
}

fn import_settings(ctx: &Ctx) {
    let filt = gtk::FileFilter::new();
    filt.set_name(Some(&tr!("Sonata Settings")));
    filt.add_pattern(&format!("*{}", backup::EXT));
    let filters = gio::ListStore::new::<gtk::FileFilter>();
    filters.append(&filt);
    let dlg = gtk::FileDialog::builder().title(tr!("Import Settings")).modal(true).filters(&filters).default_filter(&filt).build();
    let w = Rc::downgrade(ctx);
    dlg.open(Some(&ctx.window), gio::Cancellable::NONE, move |res| {
        let (Some(path), Some(s)) = (res.ok().and_then(|f| f.path()), w.upgrade()) else { return };
        ask_import(&s, &path);
    });
}

fn ask_import(ctx: &Ctx, path: &Path) {
    let Some(m) = backup::read_manifest(path) else {
        let base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        alert_ok(ctx, &tr!("This isn't a Sonata settings file."), &base);
        return;
    };
    let created = m.get("created").and_then(serde_json::Value::as_i64).unwrap_or(0);
    let when = glib::DateTime::from_unix_local(created).ok().and_then(|d| d.format("%-d %b %Y").ok()).map(|s| s.to_string()).unwrap_or_default();
    let body = if super::truthy(m.get("data")) {
        tr!("Its settings, Notes, Calendar and Reminders (Sonata {version}, {date}) replace yours; yours go to the Trash first, so they can be put back. Then you log out and back in.")
    } else {
        tr!("Its settings (Sonata {version}, {date}) replace yours; yours go to the Trash first, so they can be put back. Then you log out and back in.")
    };
    let version = m.get("sonata").and_then(serde_json::Value::as_str).unwrap_or("?").to_string();
    let (w, path) = (Rc::downgrade(ctx), path.to_path_buf());
    sonata_ui::dialog::alert(&tr!("Replace your settings with the file's?"), &fmt(&body, &[("version", &version), ("date", &when)]), &[("cancel", tr!("Cancel"), ""), ("import", tr!("Replace"), "destructive")], move |rid| {
        if rid == "import" {
            import_now(&w, path.clone());
        }
    }, ctx.parent().as_ref());
}

fn import_now(w: &Weak<Settings>, path: PathBuf) {
    let w = w.clone();
    run_async(move || backup::restore(&path), move |ok| {
        let Some(s) = w.upgrade() else { return };
        if ok {
            s.ask_restart("session", &tr!("The imported settings"));
        } else {
            alert_ok(&s, &tr!("The settings couldn't be imported."), "");
        }
    });
}

// -- reset ---------------------------------------------------------------------------------------
fn ask_factory_reset(ctx: &Ctx, what: &'static str) {
    let (title, body) = if what == "everything" {
        (tr!("Reset Sonata?"), tr!("Sonata goes back to how it was just installed: every setting, the Dock and Launchpad layout, history and caches are removed. Notes, calendars and TextEdit data are moved to the Trash."))
    } else {
        (tr!("Reset all settings?"), tr!("Every Sonata setting goes back to the default. Your Dock and Launchpad apps and folders, protected apps and your apps' data stay."))
    };
    let w = Rc::downgrade(ctx);
    sonata_ui::dialog::alert(&title, &body, &[("cancel", tr!("Cancel"), ""), ("reset", tr!("Reset"), "destructive")], move |rid| {
        if let (true, Some(s)) = (rid == "reset", w.upgrade()) {
            factory_reset_now(&s, what);
        }
    }, ctx.parent().as_ref());
}

/// Then a new login: Wayfire's options and the graphics card choice are read there.
fn factory_reset_now(ctx: &Ctx, what: &str) {
    let gone = if what == "everything" { factory_reset::everything() } else { factory_reset::settings_only() };
    println!("sonata2-settings: reset {what}: {} items", gone.len());
    ctx.ask_restart("session", &tr!("The reset"));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn export_names() {
        assert_eq!(with_ext(Path::new("/tmp/a")), PathBuf::from(format!("/tmp/a{}", backup::EXT)));
        let full = format!("/tmp/b{}", backup::EXT);
        assert_eq!(with_ext(Path::new(&full)), PathBuf::from(&full));
        assert_eq!(last_export_text(None), tr!("Last exported: never"));
        assert!(last_export_text(Some(1_700_000_000)).contains("2023"));
    }
}
