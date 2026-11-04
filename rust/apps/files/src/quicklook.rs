//! Quick Look (Space) and Get Info (Ctrl+I), macOS Big Sur style
//! (files/quicklook.py).
//!
//! Quick Look: a floating window that previews the selection -- pictures,
//! videos/audio (GTK media), text files, anything else as a big icon with
//! its details -- and follows the selection while it is open. Space or
//! Escape closes it; "Open with <app>" opens the file.
//!
//! Get Info: one small window per item: icon, name, kind, size (folders
//! counted in the background), where, created, modified, Open with and
//! Sharing & Permissions.

use crate::folder::{file_of, is_dir};
use crate::views::{date, kind, set_icon, size};
use adw::prelude::*;
use gtk::{gdk, pango};
use sonata_i18n::{fmt, n_, tr, trn};
use std::cell::RefCell;
use std::rc::Rc;

const TEXT_MAX: usize = 256 * 1024;

fn is_text(ct: &str) -> bool {
    ct.starts_with("text/") || gio::content_type_is_a(ct, "text/plain")
}

/// Quick Look shows it itself (picture, movie, sound, text): a
/// double-click opens it here when no app does.
pub fn previewable(info: &gio::FileInfo) -> bool {
    if is_dir(info) || file_of(info).and_then(|f| f.path()).is_none() {
        return false;
    }
    if info.name().to_string_lossy().ends_with(".desktop") {
        return false; // an app: opening it launches it
    }
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if ["image/", "video/", "audio/"].iter().any(|p| ct.starts_with(p)) {
        return true;
    }
    is_text(&ct) && info.size() as usize <= TEXT_MAX * 8
}

fn fmt_date(info: &gio::FileInfo, attr: &str) -> String {
    let t = if info.has_attribute(attr) { info.attribute_uint64(attr) } else { 0 };
    if t == 0 {
        return "--".into();
    }
    glib::DateTime::from_unix_local(t as i64).ok().and_then(|d| d.format(&tr!("%-d %B %Y at %H:%M")).ok()).map(|s| s.to_string()).unwrap_or_else(|| "--".into())
}

/// Title bar with a centred title and an optional widget at its end.
fn titlebar(win: &adw::Window, title: &gtk::Label, end: Option<&gtk::Widget>) -> gtk::WindowHandle {
    let (handle, bar, _) = sonata_ui::window::glass_toolbar(win, Vec::new());
    bar.set_center_widget(Some(title));
    if let Some(e) = end {
        bar.set_end_widget(Some(e));
    }
    handle
}

pub struct QuickLook {
    pub window: adw::Window,
    title: gtk::Label,
    open_btn: gtk::Button,
    body: gtk::Box,
    info: RefCell<Option<gio::FileInfo>>,
    app: RefCell<Option<gio::AppInfo>>,
}

impl QuickLook {
    pub fn new(parent: &impl IsA<gtk::Window>, on_close: Box<dyn Fn()>) -> Rc<Self> {
        sonata_ui::css::register_module("files/quicklook.py");
        // not transient: Wayfire won't maximize a window with a parent, and
        // Quick Look is its own window on macOS too
        let window = adw::Window::builder().default_width(720).default_height(520).title(tr!("Quick Look")).build();
        if let Some(app) = parent.as_ref().application() {
            window.set_application(Some(&app));
        }
        for c in ["sonata-ql", "sonata-glass-window"] {
            window.add_css_class(c);
        }
        sonata_ui::window::standard(&window);
        let tv = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let open_btn = gtk::Button::builder().css_classes(["ql-open"]).valign(gtk::Align::Center).build();
        let title = gtk::Label::builder().css_classes(["ql-title"]).ellipsize(pango::EllipsizeMode::Middle).build();
        tv.append(&titlebar(&window, &title, Some(open_btn.upcast_ref())));
        let body = gtk::Box::builder().vexpand(true).hexpand(true).build();
        tv.append(&body);
        window.set_content(Some(&tv));
        let ql = Rc::new(QuickLook { window, title, open_btn, body, info: RefCell::default(), app: RefCell::default() });
        let w = Rc::downgrade(&ql);
        ql.open_btn.connect_clicked(move |_| {
            if let Some(q) = w.upgrade() {
                q.open();
            }
        });
        let keys = gtk::EventControllerKey::new();
        let w = ql.window.downgrade();
        keys.connect_key_pressed(move |_, k, _, _| {
            if matches!(k, gdk::Key::space | gdk::Key::Escape) {
                if let Some(w) = w.upgrade() {
                    w.close();
                }
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        ql.window.add_controller(keys);
        let b = ql.body.downgrade();
        ql.window.connect_close_request(move |_| {
            if let Some(b) = b.upgrade() {
                pause(&b);
            }
            on_close();
            glib::Propagation::Proceed
        });
        ql
    }

    pub fn info(&self) -> Option<gio::FileInfo> {
        self.info.borrow().clone()
    }

    pub fn close(&self) {
        self.window.close();
    }

    pub fn show_item(&self, info: &gio::FileInfo) {
        let Some(f) = file_of(info) else { return };
        self.info.replace(Some(info.clone()));
        self.title.set_label(&info.display_name());
        let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
        let app = if is_dir(info) || ct.is_empty() { None } else { gio::AppInfo::default_for_type(&ct, false) };
        self.open_btn.set_label(&match &app {
            Some(a) => fmt(&tr!("Open with {app}"), &[("app", &a.display_name())]),
            None => tr!("Open"),
        });
        self.app.replace(app);
        pause(&self.body);
        while let Some(c) = self.body.first_child() {
            self.body.remove(&c);
        }
        self.body.append(&self.preview(info, &f, &ct));
        self.window.present();
    }

    fn preview(&self, info: &gio::FileInfo, f: &gio::File, ct: &str) -> gtk::Widget {
        let path = f.path();
        if let (Some(path), true) = (&path, ct.starts_with("image/")) {
            let pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Contain).hexpand(true).vexpand(true).margin_start(12).margin_end(12).margin_bottom(12).build();
            let (p, body, file, ps) = (pic.downgrade(), self.body.downgrade(), f.clone(), path.display().to_string());
            // decoded off the main loop (big photos); WebP, AVIF, camera RAW too
            glib::spawn_future_local(async move {
                // pixels cross the thread, the Pixbuf (not Send) stays there
                let tex = gio::spawn_blocking(move || sonata_preview::imageload::pixbuf(&ps).map(|pb| sonata_preview::imageload::pixbuf_rgba(&pb))).await.ok().flatten();
                let (Some(pic), Some(body)) = (p.upgrade(), body.upgrade()) else { return };
                if pic.parent().as_ref() != Some(body.upcast_ref()) {
                    return; // the selection moved on meanwhile
                }
                match tex {
                    Some((data, w, h, _)) => pic.set_paintable(Some(&sonata_preview::imageload::texture_from_rgba(&data, w, h))),
                    None => pic.set_file(Some(&file)),
                }
            });
            return pic.upcast();
        }
        if path.is_some() && (ct.starts_with("video/") || ct.starts_with("audio/")) {
            return gtk::Video::builder().file(f).autoplay(true).hexpand(true).vexpand(true).build().upcast();
        }
        if let (Some(path), true) = (&path, is_text(ct) && info.size() as usize <= TEXT_MAX * 8) {
            let view = gtk::TextView::builder().editable(false).cursor_visible(false).monospace(true).css_classes(["ql-text"]).left_margin(14).right_margin(14).top_margin(10).bottom_margin(10).wrap_mode(gtk::WrapMode::WordChar).build();
            let (v, p) = (view.downgrade(), path.clone());
            glib::spawn_future_local(async move {
                let text = gio::spawn_blocking(move || {
                    use std::io::Read;
                    let mut buf = Vec::new();
                    std::fs::File::open(&p).ok()?.take(TEXT_MAX as u64).read_to_end(&mut buf).ok()?;
                    Some(String::from_utf8_lossy(&buf).into_owned())
                })
                .await
                .ok()
                .flatten();
                if let (Some(v), Some(t)) = (v.upgrade(), text) {
                    v.buffer().set_text(&t);
                }
            });
            return gtk::ScrolledWindow::builder().child(&view).hexpand(true).vexpand(true).build().upcast();
        }
        // anything else: big icon + details, like macOS
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).hexpand(true).vexpand(true).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
        let img = gtk::Image::builder().pixel_size(180).build();
        set_icon(&img, info, false);
        col.append(&img);
        col.append(&gtk::Label::builder().label(info.display_name()).css_classes(["ql-big-name"]).wrap(true).justify(gtk::Justification::Center).build());
        col.append(&gtk::Label::builder().label(format!("{} – {}", kind(info), size(info))).css_classes(["ql-meta"]).build());
        col.append(&gtk::Label::builder().label(fmt(&tr!("Modified {date}"), &[("date", &date(info, "time::modified"))])).css_classes(["ql-meta"]).build());
        col.upcast()
    }

    fn open(&self) {
        let Some(f) = self.info().as_ref().and_then(file_of) else { return };
        let ctx = WidgetExt::display(&self.window).app_launch_context();
        let _ = match self.app.borrow().as_ref() {
            Some(app) => app.launch(&[f], Some(&ctx)),
            None => gio::AppInfo::launch_default_for_uri(&f.uri(), Some(&ctx)),
        };
        self.window.close();
    }
}

/// A playing preview stops when it goes.
fn pause(body: &gtk::Box) {
    if let Some(v) = body.first_child().and_downcast::<gtk::Video>() {
        if let Some(s) = v.media_stream() {
            s.pause();
        }
    }
}

// -- Get Info: Sharing & Permissions (Finder), Open With -------------------------------------
pub const LEVELS: [&str; 3] = [n_!("Read & Write"), n_!("Read only"), n_!("No Access")];
/// the shift of each one's rwx bits
const WHO: [(&str, u32); 3] = [("owner", 6), ("group", 3), ("other", 0)];

/// 0 Read & Write, 1 Read only, 2 No Access.
pub fn perm_level(mode: u32, shift: u32) -> u32 {
    let bits = (mode >> shift) & 0o7;
    if bits & 0o2 != 0 {
        0
    } else if bits & 0o4 != 0 {
        1
    } else {
        2
    }
}

/// `mode` with that one's access changed; a folder's x follows its r, a
/// file keeps whatever x it had (a program stays a program).
pub fn set_level(mode: u32, shift: u32, level: u32, is_dir: bool) -> u32 {
    let old = (mode >> shift) & 0o7;
    let x = if level < 2 { if is_dir { 0o1 } else { old & 0o1 } } else { 0 };
    let new = [0o6, 0o4, 0][level.min(2) as usize] | x;
    (mode & !(0o7 << shift)) | (new << shift)
}

fn user_name(uid: u32) -> String {
    // /etc/passwd: name:x:uid:...
    std::fs::read_to_string("/etc/passwd").ok().and_then(|s| s.lines().find_map(|l| {
        let f: Vec<&str> = l.split(':').collect();
        (f.len() > 2 && f[2] == uid.to_string()).then(|| f[0].to_string())
    })).unwrap_or_else(|| uid.to_string())
}

fn group_name(gid: u32) -> String {
    std::fs::read_to_string("/etc/group").ok().and_then(|s| s.lines().find_map(|l| {
        let f: Vec<&str> = l.split(':').collect();
        (f.len() > 2 && f[2] == gid.to_string()).then(|| f[0].to_string())
    })).unwrap_or_else(|| gid.to_string())
}

/// A Get Info window for that item.
pub fn get_info(parent: &impl IsA<gtk::Window>, info: &gio::FileInfo) -> adw::Window {
    sonata_ui::css::register_module("files/quicklook.py");
    let win = adw::Window::builder().default_width(300).title(fmt(&tr!("{name} Info"), &[("name", &info.display_name())])).build();
    if let Some(app) = parent.as_ref().application() {
        win.set_application(Some(&app));
    }
    win.set_size_request(240, 200);
    for c in ["sonata-info", "sonata-glass-window"] {
        win.add_css_class(c);
    }
    sonata_ui::window::standard(&win);
    let f = file_of(info).unwrap_or_else(|| gio::File::for_path("/"));
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).margin_start(16).margin_end(16).margin_bottom(16).build();
    let top = gtk::Box::new(gtk::Orientation::Horizontal, 10);
    let img = gtk::Image::builder().pixel_size(48).build();
    set_icon(&img, info, false);
    top.append(&img);
    let names = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).build();
    names.append(&gtk::Label::builder().label(info.display_name()).xalign(0.0).css_classes(["gi-name"]).ellipsize(pango::EllipsizeMode::Middle).max_width_chars(28).hexpand(true).build());
    let size_lbl = gtk::Label::builder().label(size(info)).xalign(0.0).css_classes(["gi-val"]).build();
    names.append(&size_lbl);
    top.append(&names);
    col.append(&top);
    col.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
    let grid = gtk::Grid::builder().column_spacing(10).row_spacing(4).build();
    let where_ = f.parent().map(|p| p.path().map(|x| x.display().to_string()).unwrap_or_else(|| p.uri().to_string())).unwrap_or_else(|| "--".into());
    let rows = [(tr!("Kind:"), kind(info)), (tr!("Size:"), size(info)), (tr!("Where:"), where_), (tr!("Created:"), fmt_date(info, "time::created")), (tr!("Modified:"), fmt_date(info, "time::modified"))];
    let mut size_row = None;
    for (r, (k, v)) in rows.iter().enumerate() {
        grid.attach(&gtk::Label::builder().label(k).xalign(1.0).css_classes(["gi-key"]).build(), 0, r as i32, 1, 1);
        let val = gtk::Label::builder().label(v).xalign(0.0).css_classes(["gi-val"]).wrap(true).max_width_chars(30).hexpand(true).natural_wrap_mode(gtk::NaturalWrapMode::None).build();
        grid.attach(&val, 1, r as i32, 1, 1);
        if r == 1 {
            size_row = Some(val);
        }
    }
    col.append(&grid);
    let size_row = size_row.unwrap();
    if !is_dir(info) {
        open_with(&win, &col, info);
    }
    if let Some(p) = f.path() {
        permissions(&win, &col, &p, is_dir(info));
    }
    let outer = gtk::Box::new(gtk::Orientation::Vertical, 0);
    outer.append(&titlebar(&win, &gtk::Label::new(None), None));
    outer.append(&gtk::ScrolledWindow::builder().child(&col).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).propagate_natural_width(true).build());
    win.set_content(Some(&outer));
    if is_dir(info) {
        if let Some(path) = f.path() {
            size_row.set_label(&tr!("Calculating…"));
            let (sr, sl) = (size_row.downgrade(), size_lbl.downgrade());
            glib::spawn_future_local(async move {
                let (total, items) = gio::spawn_blocking(move || count(&path)).await.unwrap_or((0, 0));
                let text = fmt(&trn!("{size} for {n} item", "{size} for {n} items", items), &[("size", &sonata_ui::fmt::size(total)), ("n", &sonata_ui::fmt::count(items as i64))]);
                if let (Some(sr), Some(sl)) = (sr.upgrade(), sl.upgrade()) {
                    sr.set_label(&text);
                    sl.set_label(&sonata_ui::fmt::size(total));
                }
            });
        }
    } else {
        let n = info.size().max(0) as u64;
        size_row.set_label(&fmt(&trn!("{n} byte ({size} on disk)", "{n} bytes ({size} on disk)", n), &[("n", &sonata_ui::fmt::count(n as i64)), ("size", &size(info))]));
    }
    win.present();
    win
}

/// (bytes, items) under a folder.
fn count(p: &std::path::Path) -> (u64, u64) {
    let (mut total, mut items) = (0, 0);
    let mut stack = vec![p.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            items += 1;
            let Ok(m) = e.path().symlink_metadata() else { continue };
            if m.is_dir() {
                stack.push(e.path());
            } else {
                total += m.len();
            }
        }
    }
    (total, items)
}

/// Finder's "Open with:" -- this file, or every file of its kind (Change All).
fn open_with(win: &adw::Window, col: &gtk::Box, info: &gio::FileInfo) {
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if ct.is_empty() {
        return;
    }
    let apps = gio::AppInfo::all_for_type(&ct);
    if apps.is_empty() {
        return;
    }
    let default = gio::AppInfo::default_for_type(&ct, false);
    let cur = apps.iter().position(|a| default.as_ref().is_some_and(|d| d.equal(a))).unwrap_or(0) as u32;
    col.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
    col.append(&gtk::Label::builder().label(tr!("Open with:")).xalign(0.0).css_classes(["gi-key"]).build());
    let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    let names: Vec<String> = apps.iter().map(|a| a.display_name().to_string()).collect();
    let pick = sonata_ui::controls::popup_button(&names, cur, |_| {});
    pick.set_hexpand(true);
    row.append(&pick);
    let (p, w) = (pick.downgrade(), win.downgrade());
    let change = sonata_ui::controls::push_button(&tr!("Change All…"), move || {
        let (Some(p), Some(w)) = (p.upgrade(), w.upgrade()) else { return };
        let Some(app) = apps.get(p.selected() as usize).cloned() else { return };
        change_all(&w, app, &ct);
    }, "");
    row.append(&change);
    col.append(&row);
}

fn change_all(win: &adw::Window, app: gio::AppInfo, ct: &str) {
    let desc = gio::content_type_get_description(ct).to_string();
    let body = if desc.is_empty() { tr!("This change will apply to every such file.") } else { fmt(&tr!("This change will apply to every {kind} file."), &[("kind", &desc)]) };
    let (ct, w) = (ct.to_string(), win.downgrade());
    sonata_ui::dialog::alert(
        &fmt(&tr!("Do you want to change all similar documents to open with “{app}”?"), &[("app", &app.display_name())]),
        &body,
        &[("cancel", tr!("Cancel"), ""), ("change", tr!("Continue"), "default")],
        move |rid| {
            if rid != "change" {
                return;
            }
            if let Err(e) = app.set_as_default_for_type(&ct) {
                sonata_ui::dialog::alert(&tr!("The default app can’t be changed."), e.message(), &[("ok", "OK".into(), "default")], |_| {}, w.upgrade().as_ref().map(|w| w.upcast_ref()));
            }
        },
        Some(win.upcast_ref()),
    );
}

/// Finder's Sharing & Permissions: you, the group, everyone -- Read &
/// Write, Read only or No Access. Only the owner can change them.
fn permissions(win: &adw::Window, col: &gtk::Box, path: &std::path::Path, folder: bool) {
    use std::os::unix::fs::MetadataExt;
    let Ok(st) = std::fs::symlink_metadata(path) else { return };
    let mine = st.uid() == unsafe { libc::getuid() };
    let owner = user_name(st.uid());
    col.append(&gtk::Separator::new(gtk::Orientation::Horizontal));
    col.append(&gtk::Label::builder().label(tr!("Sharing & Permissions:")).xalign(0.0).css_classes(["gi-key"]).build());
    if !mine {
        col.append(&gtk::Label::builder().label(tr!("Only its owner can change them.")).xalign(0.0).css_classes(["gi-val"]).build());
    }
    let grid = gtk::Grid::builder().column_spacing(10).row_spacing(4).build();
    let levels: Vec<String> = LEVELS.iter().map(|l| sonata_i18n::gettext(l)).collect();
    for (r, (who, shift)) in WHO.iter().enumerate() {
        let name = match *who {
            "owner" if mine => fmt(&tr!("{name} (Me)"), &[("name", &owner)]),
            "owner" => owner.clone(),
            "group" => group_name(st.gid()),
            _ => tr!("everyone"),
        };
        grid.attach(&gtk::Label::builder().label(name).xalign(0.0).css_classes(["gi-val"]).hexpand(true).ellipsize(pango::EllipsizeMode::End).build(), 0, r as i32, 1, 1);
        let (p, w, shift) = (path.to_path_buf(), win.downgrade(), *shift);
        let pick = sonata_ui::controls::popup_button(&levels, perm_level(st.mode(), shift), move |lvl| {
            use std::os::unix::fs::PermissionsExt;
            let res = std::fs::symlink_metadata(&p).and_then(|m| std::fs::set_permissions(&p, std::fs::Permissions::from_mode(set_level(m.mode(), shift, lvl, folder) & 0o7777)));
            if let Err(e) = res {
                sonata_ui::dialog::alert(&tr!("The permissions can’t be changed."), &e.to_string(), &[("ok", "OK".into(), "default")], |_| {}, w.upgrade().as_ref().map(|w| w.upcast_ref()));
            }
        });
        pick.set_sensitive(mine);
        grid.attach(&pick, 1, r as i32, 1, 1);
    }
    col.append(&grid);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permission_levels_round_trip() {
        assert_eq!(perm_level(0o644, 6), 0);
        assert_eq!(perm_level(0o644, 3), 1);
        assert_eq!(perm_level(0o640, 0), 2);
        // a folder's x follows its r; a file keeps its own x
        assert_eq!(set_level(0o700, 3, 1, true), 0o750);
        assert_eq!(set_level(0o755, 0, 2, true), 0o750);
        assert_eq!(set_level(0o755, 6, 1, false), 0o555);
        assert_eq!(set_level(0o644, 6, 0, false), 0o644);
    }
}
