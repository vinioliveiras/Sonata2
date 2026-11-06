//! Users & Groups (app.py's _page_users and its dialogs): your account
//! (picture, name, password), other users (+ add, administrator, delete)
//! and Login Items. Accounts go through AccountsService
//! (sonata_system::users); polkit asks for an admin password where needed.

use crate::kit::{clear_group, group, login_items, run_async, set_login_item, switch_row};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::gdk;
use sonata_i18n::{fmt, tr};
use sonata_system::users::{self as u_, User};
use std::cell::Cell;
use std::rc::{Rc, Weak};

/// Big Sur Users & Groups.
pub fn page_users(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let me = group(&tr!("Current User"), "");
    let others = group(&tr!("Other Users"), "");
    me.add(&adw::ActionRow::builder().title(tr!("Loading…")).build());
    let (w, m, o) = (Rc::downgrade(ctx), me.clone(), others.clone());
    run_async(u_::users, move |lst| {
        let Some(s) = w.upgrade() else { return };
        for g in [&m, &o] {
            clear_group(g);
        }
        if lst.is_empty() {
            let real = glib::real_name().to_string_lossy().into_owned();
            let name = if real.is_empty() || real == "Unknown" { glib::user_name().to_string_lossy().into_owned() } else { real };
            m.add(&adw::ActionRow::builder().title(name).use_markup(false).subtitle(tr!("AccountsService is not available: accounts can't be changed here")).build());
            o.set_visible(false);
            return;
        }
        for u in &lst {
            (if u.current { &m } else { &o }).add(&user_row(&s, u));
        }
        if !lst.iter().any(|u| !u.current) {
            o.add(&adw::ActionRow::builder().title(tr!("No other users")).build());
        }
        // (libadwaita 1.5: no ButtonRow yet -- an activatable row)
        let add = adw::ActionRow::builder().title(tr!("Add User…")).activatable(true).build();
        add.add_prefix(&gtk::Image::from_icon_name("list-add-symbolic"));
        let w = Rc::downgrade(&s);
        add.connect_activated(move |_| {
            if let Some(s) = w.upgrade() {
                add_user_dialog(&s);
            }
        });
        o.add(&add);
    });
    let items = group(&tr!("Login Items"), &tr!("These apps open automatically when you log in."));
    let login = login_items();
    for (name, title, enabled) in &login {
        let n = name.clone();
        items.add(&switch_row(title, *enabled, move |on| set_login_item(&n, on), ""));
    }
    if login.is_empty() {
        items.add(&adw::ActionRow::builder().title(tr!("No login items")).subtitle(tr!("Use Options > Open at Login in the Dock")).build());
    }
    vec![me, others, items]
}

fn display_name(u: &User) -> String {
    if u.real_name.is_empty() { u.name.clone() } else { u.real_name.clone() }
}

fn user_row(ctx: &Ctx, u: &User) -> adw::ActionRow {
    let kind = if u.admin { tr!("Admin") } else { tr!("Standard") };
    let row = adw::ActionRow::builder().title(display_name(u)).use_markup(false).subtitle(format!("{} · {kind}", u.name)).build();
    let av = adw::Avatar::new(if u.current { 44 } else { 32 }, Some(&display_name(u)), true);
    if !u.icon.is_empty() {
        if let Ok(t) = gdk::Texture::from_filename(&u.icon) {
            av.set_custom_image(Some(&t));
        }
    }
    let pic = gtk::Button::builder().child(&av).css_classes(["flat", "circular"]).valign(gtk::Align::Center).tooltip_text(tr!("Change picture")).build();
    let (w, user) = (Rc::downgrade(ctx), u.clone());
    pic.connect_clicked(move |b| {
        if let Some(s) = w.upgrade() {
            pick_picture(&s, &user, b);
        }
    });
    row.add_prefix(&pic);
    if u.current {
        let (w, user) = (Rc::downgrade(ctx), u.clone());
        let name = sonata_ui::controls::push_button(&tr!("Edit Name…"), move || {
            let Some(s) = w.upgrade() else { return };
            let (w, user, current) = (w.clone(), user.clone(), user.real_name.clone());
            sonata_ui::dialog::ask_text(&tr!("Full name"), &current, &tr!("OK"), move |v| {
                let user = user.clone();
                user_op(&w, move || u_::set_real_name(&user, &v), None);
            }, "", s.parent().as_ref());
        }, "");
        name.set_valign(gtk::Align::Center);
        let (w, user) = (Rc::downgrade(ctx), u.clone());
        let pw = sonata_ui::controls::push_button(&tr!("Change Password…"), move || {
            if let Some(s) = w.upgrade() {
                password_dialog(&s, &user);
            }
        }, "");
        pw.set_valign(gtk::Align::Center);
        row.add_suffix(&name);
        row.add_suffix(&pw);
    } else {
        use sonata_ui::menu::{popup, Item, Options};
        let more = gtk::Button::builder().icon_name("view-more-symbolic").css_classes(["flat"]).valign(gtk::Align::Center).build();
        let (w, user) = (Rc::downgrade(ctx), u.clone());
        more.connect_clicked(move |b| {
            let (w1, u1) = (w.clone(), user.clone());
            let (w2, u2) = (w.clone(), user.clone());
            let (w3, u3) = (w.clone(), user.clone());
            popup(b, vec![
                vec![Item::check(&tr!("Allow user to administer this computer"), user.admin, move |on| {
                    let u = u1.clone();
                    user_op(&w1, move || u_::set_admin(&u, on), None);
                })],
                vec![Item::new(&tr!("Reset Password…"), move || {
                    if let Some(s) = w2.upgrade() {
                        password_dialog(&s, &u2);
                    }
                })],
                vec![Item::new(&tr!("Delete User…"), move || {
                    if let Some(s) = w3.upgrade() {
                        delete_user(&s, &u3);
                    }
                })],
            ], Options::below());
        });
        row.add_suffix(&more);
    }
    row
}

/// An account change off the main loop; then its error (or done_text) told,
/// the page and the sidebar's picture shown again.
fn user_op(w: &Weak<Settings>, work: impl FnOnce() -> Option<String> + Send + 'static, done_text: Option<String>) {
    let w = w.clone();
    run_async(work, move |err| {
        let Some(s) = w.upgrade() else { return };
        if let Some(e) = err {
            s.toast(&e);
        } else if let Some(t) = done_text {
            s.toast(&t);
        }
        s.reload_page("users");
        s.refresh_card();
    });
}

/// Python's str.title() for a picture's file name ("red_bird" -> "Red_Bird").
pub fn title_case(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for c in s.chars() {
        if prev_alpha {
            out.extend(c.to_lowercase());
        } else {
            out.extend(c.to_uppercase());
        }
        prev_alpha = c.is_alphabetic();
    }
    out
}

/// Big Sur picture picker: Sonata's stock pictures, or a file.
fn pick_picture(ctx: &Ctx, u: &User, anchor: &gtk::Button) {
    let pop = gtk::Popover::builder().has_arrow(true).position(gtk::PositionType::Right).build();
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).margin_top(10).margin_bottom(10).margin_start(10).margin_end(10).build();
    col.append(&gtk::Label::builder().label(tr!("Pictures")).xalign(0.0).css_classes(["heading"]).build());
    let grid = gtk::FlowBox::builder().max_children_per_line(6).min_children_per_line(6).selection_mode(gtk::SelectionMode::None).row_spacing(6).column_spacing(6).homogeneous(true).build();
    let use_it: Rc<dyn Fn(String)> = {
        let (w, user, p) = (Rc::downgrade(ctx), u.clone(), pop.downgrade());
        Rc::new(move |path: String| {
            if let Some(p) = p.upgrade() {
                p.popdown();
            }
            let user = user.clone();
            user_op(&w, move || u_::set_picture(&user, &path), Some(tr!("Picture changed")));
        })
    };
    for path in u_::stock_pictures() {
        let Ok(t) = gdk::Texture::from_filename(&path) else { continue };
        let av = adw::Avatar::new(48, None, false);
        av.set_custom_image(Some(&t));
        let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let b = gtk::Button::builder().child(&av).css_classes(["flat", "circular"]).tooltip_text(title_case(&stem)).build();
        let (f, p) = (use_it.clone(), path.to_string_lossy().into_owned());
        b.connect_clicked(move |_| f(p.clone()));
        grid.append(&b);
    }
    col.append(&grid);
    let (w, f, p) = (Rc::downgrade(ctx), use_it.clone(), pop.downgrade());
    let other = sonata_ui::controls::push_button(&tr!("Choose from Files…"), move || {
        if let Some(p) = p.upgrade() {
            p.popdown();
        }
        let Some(s) = w.upgrade() else { return };
        let flt = gtk::FileFilter::new();
        flt.set_name(Some(&tr!("Pictures")));
        flt.add_mime_type("image/*");
        let store = gio::ListStore::new::<gtk::FileFilter>();
        store.append(&flt);
        let dlg = gtk::FileDialog::builder().title(tr!("Choose a picture")).modal(true).filters(&store).build();
        let f = f.clone();
        dlg.open(Some(&s.window), gio::Cancellable::NONE, move |res| {
            if let Some(path) = res.ok().and_then(|file| file.path()) {
                f(path.to_string_lossy().into_owned());
            }
        });
    }, "");
    col.append(&other);
    pop.set_child(Some(&col));
    pop.set_parent(anchor);
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
    pop.popup();
}

fn password_dialog(ctx: &Ctx, u: &User) {
    let bx = gtk::Box::new(gtk::Orientation::Vertical, 6);
    let new = sonata_ui::controls::secret_field("", &tr!("New password"));
    let verify = sonata_ui::controls::secret_field("", &tr!("Verify"));
    verify.set_activates_default(true);
    bx.append(&new);
    bx.append(&verify);
    let (w, user) = (Rc::downgrade(ctx), u.clone());
    let heading = fmt(&tr!("Change the password for “{name}”"), &[("name", &display_name(u))]);
    let dlg = sonata_ui::dialog::alert(&heading, "", &[("cancel", tr!("Cancel"), ""), ("ok", tr!("Change Password"), "default")], move |r| {
        if r != "ok" {
            return;
        }
        let pw = new.text().to_string();
        if pw.is_empty() || pw != verify.text().as_str() {
            if let Some(s) = w.upgrade() {
                s.toast(&tr!("The passwords don't match"));
            }
            return;
        }
        let user = user.clone();
        user_op(&w, move || u_::set_password(&user, &pw, ""), Some(tr!("Password changed")));
    }, ctx.parent().as_ref());
    dlg.set_extra_child(Some(&bx));
}

/// New Account's Create: a valid name and a password, typed twice the same.
pub fn can_create(name: &str, pw: &str, verify: &str) -> bool {
    u_::valid_name(name.trim()) && !pw.is_empty() && pw == verify
}

fn add_user_dialog(ctx: &Ctx) {
    let grid = gtk::Grid::builder().row_spacing(6).column_spacing(8).build();
    let full = sonata_ui::controls::text_field("", "");
    let acct = sonata_ui::controls::text_field("", "");
    let pw = sonata_ui::controls::secret_field("", "");
    let verify = sonata_ui::controls::secret_field("", "");
    for e in [full.upcast_ref::<gtk::Widget>(), acct.upcast_ref(), pw.upcast_ref(), verify.upcast_ref()] {
        e.set_hexpand(true);
    }
    let admin = gtk::CheckButton::with_label(&tr!("Allow user to administer this computer"));
    // the account name follows the full name until it's typed in itself
    let edited = Rc::new(Cell::new(false));
    let (ed, a) = (edited.clone(), acct.downgrade());
    full.connect_changed(move |e| {
        if !ed.get() {
            if let Some(a) = a.upgrade() {
                a.set_text(&u_::short_name(&e.text()));
            }
        }
    });
    acct.connect_changed(move |e| {
        if e.has_focus() {
            edited.set(true);
        }
    });
    let labels = [tr!("Full Name:"), tr!("Account Name:"), tr!("Password:"), tr!("Verify:")];
    let fields: [&gtk::Widget; 4] = [full.upcast_ref(), acct.upcast_ref(), pw.upcast_ref(), verify.upcast_ref()];
    for (r, (label, f)) in labels.iter().zip(fields).enumerate() {
        grid.attach(&gtk::Label::builder().label(label).xalign(1.0).build(), 0, r as i32, 1, 1);
        grid.attach(f, 1, r as i32, 1, 1);
    }
    grid.attach(&admin, 1, 4, 1, 1);
    let w = Rc::downgrade(ctx);
    let (f2, a2, p2, v2, ad2) = (full.clone(), acct.clone(), pw.clone(), verify.clone(), admin.clone());
    let dlg = sonata_ui::dialog::alert(&tr!("New Account"), "", &[("cancel", tr!("Cancel"), ""), ("create", tr!("Create User"), "default")], move |r| {
        if r != "create" {
            return;
        }
        let Some(s) = w.upgrade() else { return };
        let name = a2.text().trim().to_string();
        if !u_::valid_name(&name) {
            s.toast(&tr!("The account name can use lowercase letters, numbers, - and _"));
            return;
        }
        if p2.text() != v2.text() {
            s.toast(&tr!("The passwords don't match"));
            return;
        }
        let real = f2.text().trim().to_string();
        let real = if real.is_empty() { name.clone() } else { real };
        let (is_admin, password) = (ad2.is_active(), p2.text().to_string());
        let done = fmt(&tr!("User “{name}” created"), &[("name", &name)]);
        user_op(&w, move || u_::create_user(&name, &real, is_admin, &password), Some(done));
    }, ctx.parent().as_ref());
    dlg.set_extra_child(Some(&grid));
    // a password is needed (Vini: an account made without one was locked --
    // it never showed on the login screen and couldn't log in)
    let check: Rc<dyn Fn()> = {
        let (d, a, p, v) = (dlg.downgrade(), acct.downgrade(), pw.downgrade(), verify.downgrade());
        Rc::new(move || {
            let (Some(d), Some(a), Some(p), Some(v)) = (d.upgrade(), a.upgrade(), p.upgrade(), v.upgrade()) else { return };
            d.set_response_enabled("create", can_create(&a.text(), &p.text(), &v.text()));
        })
    };
    for e in [acct.upcast_ref::<gtk::Editable>(), pw.upcast_ref(), verify.upcast_ref(), full.upcast_ref()] {
        let c = check.clone();
        e.connect_changed(move |_| c());
    }
    check();
}

fn delete_user(ctx: &Ctx, u: &User) {
    let (w, user) = (Rc::downgrade(ctx), u.clone());
    sonata_ui::dialog::alert(
        &fmt(&tr!("Delete the user “{name}”?"), &[("name", &display_name(u))]),
        &tr!("Their home folder can be kept or deleted."),
        &[("cancel", tr!("Cancel"), ""), ("keep", tr!("Keep Home Folder"), ""), ("delete", tr!("Delete Home Folder"), "destructive")],
        move |r| {
            if r == "keep" || r == "delete" {
                let (user, remove) = (user.clone(), r == "delete");
                user_op(&w, move || u_::delete_user(&user, remove), Some(tr!("User deleted")));
            }
        },
        ctx.parent().as_ref(),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: Settings let him make a user without a password -- the account
    /// was locked: it never showed on the login screen and couldn't log in.
    /// The form's Create stays off without one.
    #[test]
    fn test_form_needs_it() {
        assert!(!can_create("ana", "", ""));
        assert!(!can_create("ana", "a", "b"));
        assert!(!can_create("Ana", "s3cret", "s3cret"));
        assert!(can_create(" ana ", "s3cret", "s3cret"));
    }

    #[test]
    fn picture_names() {
        assert_eq!(title_case("red_bird"), "Red_Bird");
        assert_eq!(title_case("GUITAR"), "Guitar");
    }
}
