//! Settings > Apps (apps_page.py, Vini): every app; click one for its own
//! page -- how big it is and its data, what it may use (Flatpak apps:
//! camera, microphone, network, location, background, notifications, home
//! folder; packaged ones: what Sonata keeps from them when it opens them),
//! Lock App, Clear Data and Uninstall. The sizes and permissions come from
//! sonata_system::appmanage (backend/appmanage.py).

use crate::applist::{AppList, AppRow};
use crate::kit::{clear_group, group, loading_row, quietly, switch_row};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::{gio, glib};
use sonata_desktop::{applock, badges};
use sonata_i18n::{fmt, gettext, n_, tr};
use sonata_system::{appmanage, uninstall::Owner};
use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::{Rc, Weak};

pub const KIND: [(&str, &str); 3] = [("flatpak", "Flatpak"), ("package", n_!("Package")), ("shortcut", n_!("Shortcut"))];
pub const HEAD_ICON: i32 = 56;

pub fn size_text(n: u64) -> String {
    if n > 0 {
        glib::format_size(n).to_string()
    } else {
        tr!("Zero KB")
    }
}

/// A path under your home as ~/...
pub fn home_path(p: &std::path::Path) -> String {
    let home = glib::home_dir();
    match p.strip_prefix(&home) {
        Ok(rest) if !rest.as_os_str().is_empty() => format!("~/{}", rest.display()),
        _ => p.display().to_string(),
    }
}

fn listed(dirs: &[PathBuf], sep: &str) -> String {
    dirs.iter().map(|d| home_path(d)).collect::<Vec<_>>().join(sep)
}

/// What is read off the main loop for one app.
pub struct Looked {
    pub owner: Option<Owner>,
    pub size: Option<u64>,
    pub dirs: Vec<PathBuf>,
    pub data: u64,
    pub perms: Vec<(&'static str, String, bool)>,
}

/// The app open on its own page.
pub struct OpenApp {
    pub info: gio::AppInfo,
    pub did: String,
    pub owner: RefCell<Option<Owner>>,
    pub dirs: RefCell<Vec<PathBuf>>,
    pub hero: adw::ActionRow,
    pub perms: adw::PreferencesGroup,
    pub perm_rows: RefCell<Vec<(&'static str, adw::SwitchRow)>>,
    pub clear: adw::ActionRow,
    pub lock: adw::SwitchRow,
    pub clear_btn: gtk::Button,
    pub rm_btn: gtk::Button,
}

pub struct AppsPage {
    ctx: Weak<Settings>,
    me: Weak<AppsPage>,
    pub list: RefCell<Option<Rc<AppList>>>,
    /// the open app (tests)
    pub app: RefCell<Option<Rc<OpenApp>>>,
}

impl AppsPage {
    pub fn new(ctx: &Ctx) -> Rc<Self> {
        Rc::new_cyclic(|me| AppsPage { ctx: Rc::downgrade(ctx), me: me.clone(), list: RefCell::new(None), app: RefCell::new(None) })
    }

    pub fn groups(&self) -> Vec<adw::PreferencesGroup> {
        let w = self.me.clone();
        let list = AppList::new(&tr!("Installed Apps"), &tr!("Click one for its size, data and permissions."), Rc::new(move |row: &AppRow| {
            if let Some(p) = w.upgrade() {
                p.open(row);
            }
        }), None);
        let g = list.group.clone();
        *self.list.borrow_mut() = Some(list);
        vec![g]
    }

    pub fn focus(&self, did: &str) {
        if let Some(l) = self.list.borrow().clone() {
            l.focus(did);
        }
    }

    fn is_current(&self, app: &Rc<OpenApp>) -> bool {
        self.app.borrow().as_ref().is_some_and(|a| Rc::ptr_eq(a, app))
    }

    // -- one app -------------------------------------------------------------------------------------
    pub fn open(&self, row: &AppRow) {
        let Some(ctx) = self.ctx.upgrade() else { return };
        let info = row.info.clone();
        let name = info.display_name().to_string();
        let head = group("", "");
        let hero = adw::ActionRow::builder().title(&name).use_markup(false).subtitle("…").build();
        let img = gtk::Image::builder().pixel_size(HEAD_ICON).build();
        sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(&info));
        hero.add_prefix(&img);
        hero.add_css_class("st-app-hero");
        head.add(&hero);
        let perms = group(&tr!("Permissions"), "");
        perms.add(&loading_row(&tr!("Checking permissions…"))); // Vini: they take a moment
        let manage = group("", "");
        let id = info.id().map(|s| s.to_string()).unwrap_or_default();
        let (me, inf) = (self.me.clone(), info.clone());
        let lock = switch_row(&tr!("Lock App"), applock::locked(&id), move |on| {
            if let Some(p) = me.upgrade() {
                p.lock(&inf, on);
            }
        }, &tr!("Ask for your password when it opens"));
        manage.add(&lock);
        let clear = adw::ActionRow::builder().title(tr!("Clear Data")).use_markup(false).subtitle("…").build();
        let me = self.me.clone();
        let clear_btn = sonata_ui::controls::push_button(&tr!("Clear…"), move || {
            if let Some(p) = me.upgrade() {
                p.ask_clear();
            }
        }, "");
        clear_btn.set_valign(gtk::Align::Center);
        clear_btn.set_sensitive(false);
        clear.add_suffix(&clear_btn);
        manage.add(&clear);
        let rm = adw::ActionRow::builder().title(tr!("Uninstall")).use_markup(false).build();
        let me = self.me.clone();
        let rm_btn = sonata_ui::controls::push_button(&tr!("Uninstall…"), move || {
            if let Some(p) = me.upgrade() {
                p.ask_uninstall();
            }
        }, "destructive");
        rm_btn.set_valign(gtk::Align::Center);
        rm_btn.set_sensitive(false);
        rm.add_suffix(&rm_btn);
        manage.add(&rm);
        let gfx = graphics(&info);
        let app = Rc::new(OpenApp { info: info.clone(), did: row.did.clone(), owner: RefCell::new(None), dirs: RefCell::new(Vec::new()), hero, perms: perms.clone(), perm_rows: RefCell::new(Vec::new()), clear, lock, clear_btn, rm_btn });
        *self.app.borrow_mut() = Some(app.clone());
        ctx.push_detail(&name, &[head, perms, gfx, manage]);
        // (an AppInfo can't go to another thread: the worker reads its own copy)
        let file = info.downcast_ref::<gio::DesktopAppInfo>().and_then(|d| d.filename());
        let me = self.me.clone();
        crate::kit::run_async(move || file.and_then(|f| gio::DesktopAppInfo::from_filename(f)).map(|i| look(i.upcast_ref())), move |res| {
            if let (Some(p), Some(res)) = (me.upgrade(), res) {
                p.looked(&app, res);
            }
        });
    }

    fn looked(&self, app: &Rc<OpenApp>, res: Looked) {
        if !self.is_current(app) {
            return; // another app opened meanwhile
        }
        let o = res.owner.clone();
        *app.owner.borrow_mut() = o.clone();
        *app.dirs.borrow_mut() = res.dirs.clone();
        let id = app.info.id().map(|s| s.to_string()).unwrap_or_default();
        let web = badges::is_webapp(&id);
        let mut bits = vec![if web {
            tr!("Web App")
        } else if let Some(o) = &o {
            KIND.iter().find(|(k, _)| *k == o.kind).map(|(_, t)| gettext(t)).unwrap_or_default()
        } else {
            tr!("Part of Sonata")
        }];
        if let Some(size) = res.size.filter(|s| *s > 0) {
            bits.push(fmt(&tr!("{size} app"), &[("size", &size_text(size))]));
        }
        bits.push(fmt(&tr!("{size} data"), &[("size", &size_text(res.data))]));
        app.hero.set_subtitle(&bits.into_iter().filter(|b| !b.is_empty()).collect::<Vec<_>>().join(" · "));
        clear_group(&app.perms);
        if !res.perms.is_empty() {
            for (key, title, on) in &res.perms {
                let (me, a, key2) = (self.me.clone(), Rc::downgrade(app), *key);
                let row = switch_row(title, *on, move |v| {
                    if let (Some(p), Some(a)) = (me.upgrade(), a.upgrade()) {
                        p.set_permission(&a, key2, v);
                    }
                }, "");
                app.perms.add(&row);
                app.perm_rows.borrow_mut().push((key, row));
            }
            if o.as_ref().is_some_and(|o| o.kind == "flatpak") {
                app.perms.set_description(Some(&tr!("Changes take effect the next time the app opens.")));
            } else {
                // a packaged app: Sonata keeps it out when it opens it
                let mut text = tr!("Applied when the app is opened from Sonata (the Dock, Apps, search) the next time. Home Folder off: it gets a folder of its own instead of yours.");
                if res.perms.iter().any(|(k, _, _)| *k == "location") {
                    text = format!("{text} {}", tr!("Location changes at once (asks for your password)."));
                }
                if !sonata_desktop::sandbox::available() {
                    text = format!("{text} {}", tr!("Needs bubblewrap (./install.sh installs it)."));
                }
                app.perms.set_description(Some(&text));
            }
        } else {
            let note = adw::ActionRow::builder()
                .title(if web { tr!("Asked by the site") } else { tr!("Part of Sonata") })
                .use_markup(false)
                .subtitle(if web { tr!("The site asks before it uses the camera, microphone or location.") } else { tr!("Sonata's own apps can use what Sonata uses.") })
                .build();
            note.set_subtitle_lines(0);
            app.perms.add(&note);
        }
        app.clear.set_subtitle(&if res.dirs.is_empty() { tr!("No data of its own found") } else { listed(&res.dirs, ", ") });
        app.clear_btn.set_sensitive(!res.dirs.is_empty());
        app.rm_btn.set_sensitive(o.is_some() || web); // (web apps: Vini)
    }

    // -- changes -------------------------------------------------------------------------------------
    fn lock(&self, info: &gio::AppInfo, on: bool) {
        let id = info.id().map(|s| s.to_string()).unwrap_or_default();
        if on {
            applock::set_locked(&id, true);
            return;
        }
        // unlocking asks the password: off only when right
        let Some(app) = self.app.borrow().clone() else { return };
        quietly(&app.lock, || app.lock.set_active(true));
        let row = app.lock.downgrade();
        applock::ask_unlock(info, Some(Box::new(move || {
            if let Some(r) = row.upgrade() {
                quietly(&r, || r.set_active(false));
            }
        })));
    }

    pub fn set_permission(&self, app: &Rc<OpenApp>, key: &'static str, on: bool) {
        let owner = app.owner.borrow().clone();
        let id = app.info.id().map(|s| s.to_string());
        let (me, a) = (self.me.clone(), Rc::downgrade(app));
        crate::kit::run_async(move || appmanage::set_permission(owner.as_ref(), key, on, id.as_deref()), move |ok| {
            if ok {
                return;
            }
            let Some(p) = me.upgrade() else { return };
            if let Some(ctx) = p.ctx.upgrade() {
                ctx.toast(&tr!("Couldn't change that permission"));
            }
            // (Location: the password cancelled)
            let Some(a) = a.upgrade().filter(|a| p.is_current(a)) else { return };
            let row = a.perm_rows.borrow().iter().find(|(k, _)| *k == key).map(|(_, r)| r.clone());
            if let Some(row) = row {
                quietly(&row, || row.set_active(!on));
            }
        });
    }

    pub fn ask_clear(&self) -> Option<adw::AlertDialog> {
        let ctx = self.ctx.upgrade()?;
        let app = self.app.borrow().clone()?;
        let name = app.info.display_name().to_string();
        let dirs = listed(&app.dirs.borrow(), "\n");
        let (me, a) = (self.me.clone(), app.clone());
        Some(sonata_ui::dialog::alert(
            &fmt(&tr!("Clear “{name}”’s data?"), &[("name", &name)]),
            &fmt(&tr!("Its sign-ins, settings and cache go to the Trash (you can put them back from there):\n{listed}\nQuit the app first."), &[("listed", &dirs)]),
            &[("cancel", tr!("Cancel"), ""), ("clear", tr!("Clear Data"), "destructive")],
            move |rid| {
                if let (true, Some(p)) = (rid == "clear", me.upgrade()) {
                    p.clear(&a);
                }
            },
            ctx.parent().as_ref(),
        ))
    }

    pub fn clear(&self, app: &Rc<OpenApp>) {
        let dirs = app.dirs.borrow().clone();
        let (me, a) = (self.me.clone(), app.clone());
        crate::kit::run_async(move || appmanage::clear_data(&dirs), move |failed| {
            let Some(p) = me.upgrade() else { return };
            if !failed.is_empty() {
                if let Some(ctx) = p.ctx.upgrade() {
                    ctx.toast(&tr!("Some of its data couldn't be moved to the Trash"));
                }
            }
            a.dirs.borrow_mut().retain(|d| d.exists());
            if !p.is_current(&a) {
                return;
            }
            let dirs = a.dirs.borrow().clone();
            a.clear.set_subtitle(&if dirs.is_empty() { tr!("No data of its own found") } else { listed(&dirs, ", ") });
            a.clear_btn.set_sensitive(!dirs.is_empty());
            let sub = a.hero.subtitle().map(|s| s.to_string()).unwrap_or_default();
            let sub = sub.rsplit_once(" · ").map(|(head, _)| head.to_string()).unwrap_or(sub);
            a.hero.set_subtitle(&format!("{sub} · {}", fmt(&tr!("{size} data"), &[("size", &size_text(appmanage::folder_size(&dirs)))])));
        });
    }

    pub fn ask_uninstall(&self) {
        let (Some(ctx), Some(app)) = (self.ctx.upgrade(), self.app.borrow().clone()) else { return };
        let Some(info) = app.info.downcast_ref::<gio::DesktopAppInfo>() else { return };
        let (me, did) = (self.me.clone(), app.did.clone());
        sonata_dock::uninstall_ui::ask(info, ctx.parent().as_ref(), Some(Rc::new(move |ok| {
            let Some(p) = me.upgrade().filter(|_| ok) else { return };
            if let Some(ctx) = p.ctx.upgrade() {
                ctx.pop_detail(None);
            }
            let list = p.list.borrow().clone();
            if let Some(l) = list {
                l.remove(&did);
            }
        })));
    }
}

/// Blocking: who owns it, its size, its folders and their size, permissions.
pub fn look(info: &gio::AppInfo) -> Looked {
    let o = sonata_system::uninstall::owner(info);
    let dirs = appmanage::data_dirs(info, o.as_ref());
    let id = info.id().map(|s| s.to_string());
    Looked { size: appmanage::app_size(o.as_ref()), data: appmanage::folder_size(&dirs), perms: appmanage::permissions(o.as_ref(), id.as_deref()), dirs, owner: o }
}

/// Vulkan / WebGPU for this app (browsergpu; also in its right-click menu).
pub fn graphics(info: &gio::AppInfo) -> adw::PreferencesGroup {
    use sonata_tweaks::browsergpu as b;
    let g = group(&tr!("Graphics"), &b::note());
    let (vk_ok, wg_ok) = b::supports(info);
    let vulkan_row: Rc<RefCell<Option<adw::SwitchRow>>> = Rc::new(RefCell::new(None));
    let sync = {
        let (r, info) = (vulkan_row.clone(), info.clone());
        Rc::new(move || {
            if let Some(row) = r.borrow().as_ref() {
                quietly(row, || row.set_active(b::vulkan(&info)));
                row.set_sensitive(!b::vulkan_forced(&info));
            }
        })
    };
    if vk_ok {
        let (i, s) = (info.clone(), sync.clone());
        let row = switch_row(&tr!("Use Vulkan"), b::vulkan(info), move |on| {
            b::set_vulkan(&i, on);
            s();
        }, &b::vulkan_note(info));
        g.add(&row);
        *vulkan_row.borrow_mut() = Some(row);
    }
    if wg_ok {
        let (i, s) = (info.clone(), sync.clone());
        g.add(&switch_row(&tr!("Allow WebGPU"), b::webgpu(info), move |on| {
            b::set_webgpu(&i, on);
            s();
        }, &b::webgpu_note(info)));
    }
    sync();
    g
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_under_home() {
        let home = glib::home_dir();
        assert_eq!(home_path(&home.join(".config/x")), "~/.config/x");
        assert_eq!(home_path(std::path::Path::new("/opt/x")), "/opt/x");
        assert_eq!(size_text(0), tr!("Zero KB"));
    }
}
