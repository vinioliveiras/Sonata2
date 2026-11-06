//! Finder's "Rename N Items…" (files/batchrename.py): several items renamed
//! at once.
//!
//! ```text
//!     Replace Text   "IMG_" -> "Trip " in every name
//!     Add Text       "Final " before (or " v2" after) each name
//!     Format         "Trip 1", "Trip 2"... (from a start number), the extension kept
//! ```
//!
//! new_names() is the pure part (the dialog previews the first one);
//! rename() renames them in a thread, skips a name already taken, and the
//! whole batch is one Undo.

use crate::ops;
use adw::prelude::*;
use sonata_i18n::{fmt, n_, tr};
use std::rc::Rc;
use std::sync::{Arc, Mutex};

pub const MODES: [(&str, &str); 3] = [("replace", n_!("Replace Text")), ("add", n_!("Add Text")), ("format", n_!("Format"))];

fn split(name: &str, is_dir: bool) -> (String, String) {
    if is_dir { (name.to_string(), String::new()) } else { ops::split(name) }
}

/// The new name of each of `names` (unchanged where the rule does nothing).
pub fn new_names(names: &[String], mode: &str, a: &str, b: &str, after: bool, start: i64, dirs: &[bool]) -> Vec<String> {
    let width = (start + names.len() as i64 - 1).to_string().len();
    names
        .iter()
        .enumerate()
        .map(|(i, name)| {
            let (base, ext) = split(name, dirs.get(i).copied().unwrap_or(false));
            let new = match mode {
                "replace" if !a.is_empty() => format!("{}{ext}", base.replace(a, b)),
                "add" if after => format!("{base}{a}{ext}"),
                "add" => format!("{a}{base}{ext}"),
                "format" => format!("{} {:0width$}{ext}", if a.is_empty() { "Untitled" } else { a }, start + i as i64),
                _ => name.clone(),
            };
            let t = new.trim();
            if t.is_empty() { name.clone() } else { t.to_string() }
        })
        .collect()
}

/// Why these new names can't be used ("" when they can).
pub fn problems(new: &[String]) -> String {
    if new.iter().any(|n| n.contains('/')) {
        return tr!("A name can’t contain “/”.");
    }
    let set: std::collections::HashSet<&String> = new.iter().collect();
    if set.len() != new.len() {
        return tr!("Two items would get the same name.");
    }
    String::new()
}

/// Rename each file to its new name (a name already taken is skipped);
/// on_done() after. One Undo for the batch.
pub fn rename(files: Vec<gio::File>, new: Vec<String>, on_done: Option<Box<dyn FnOnce()>>, on_error: Option<Rc<dyn Fn(&gio::File, &glib::Error)>>) {
    let pairs: Vec<(gio::File, String)> = files.into_iter().zip(new).filter(|(f, n)| *n != ops::base(f)).collect();
    let done: Arc<Mutex<Vec<(gio::File, String)>>> = Arc::default();
    let d = done.clone();
    let exists_msg = tr!("“{name}” already exists.");
    ops::in_thread(
        move |report| {
            for (f, n) in pairs {
                let Some(parent) = f.parent() else { continue };
                if parent.child(&n).query_exists(gio::Cancellable::NONE) {
                    report(&f, &glib::Error::new(gio::IOErrorEnum::Exists, &fmt(&exists_msg, &[("name", &n)])));
                    continue;
                }
                match f.set_display_name(&n, gio::Cancellable::NONE) {
                    Ok(nf) => d.lock().unwrap().push((nf, ops::base(&f))),
                    Err(e) => report(&f, &e),
                }
            }
        },
        Some(Box::new(move || {
            let done = done.lock().unwrap().clone();
            if !done.is_empty() {
                crate::tags::moved_files(&done.iter().filter_map(|(nf, old)| Some((nf.parent()?.child(old), nf.clone()))).collect::<Vec<_>>());
                let (u, r) = (done.clone(), done.clone());
                crate::undo::push(crate::undo::Action {
                    label: fmt(&tr!("Rename {n} Items"), &[("n", &done.len().to_string())]),
                    undo: Arc::new(move || {
                        for (nf, old) in &u {
                            nf.set_display_name(old, gio::Cancellable::NONE)?;
                        }
                        Ok(())
                    }),
                    redo: Some(Arc::new(move || {
                        for (nf, old) in &r {
                            if let Some(p) = nf.parent() {
                                p.child(old).set_display_name(&ops::base(nf), gio::Cancellable::NONE)?;
                            }
                        }
                        Ok(())
                    })),
                });
            }
            if let Some(d) = on_done {
                d();
            }
        })),
        on_error,
    );
}

/// The "Rename N Items" alert: the rule, its fields, an example.
pub fn dialog(parent: &impl IsA<gtk::Widget>, files: Vec<gio::File>) -> adw::AlertDialog {
    let names: Rc<Vec<String>> = Rc::new(files.iter().map(ops::base).collect());
    let dirs: Rc<Vec<bool>> = Rc::new(files.iter().map(|f| f.path().is_some_and(|p| p.is_dir())).collect());
    let grid = gtk::Grid::builder().row_spacing(8).column_spacing(8).build();
    let labels: Vec<String> = MODES.iter().map(|m| sonata_i18n::gettext(m.1)).collect();
    let mode = sonata_ui::controls::popup_button(&labels, 0, |_| {});
    let a = sonata_ui::controls::text_field("", "");
    a.set_hexpand(true);
    let b = sonata_ui::controls::text_field("", "");
    b.set_hexpand(true);
    let where_ = sonata_ui::controls::popup_button(&[tr!("after name"), tr!("before name")], 0, |_| {});
    let start = sonata_ui::controls::text_field("1", "");
    let la = gtk::Label::builder().xalign(1.0).build();
    let lb = gtk::Label::builder().xalign(1.0).build();
    let ls = gtk::Label::builder().label(tr!("Start at:")).xalign(1.0).build();
    let example = gtk::Label::builder().xalign(0.0).css_classes(["dim-label"]).wrap(true).build();
    grid.attach(&mode, 1, 0, 1, 1);
    grid.attach(&la, 0, 1, 1, 1);
    grid.attach(&a, 1, 1, 1, 1);
    grid.attach(&lb, 0, 2, 1, 1);
    grid.attach(&b, 1, 2, 1, 1);
    grid.attach(&where_, 1, 3, 1, 1);
    grid.attach(&ls, 0, 4, 1, 1);
    grid.attach(&start, 1, 4, 1, 1);
    grid.attach(&example, 1, 5, 1, 1);
    let rule = {
        let (mode, a, b, where_, start, names, dirs) = (mode.clone(), a.clone(), b.clone(), where_.clone(), start.clone(), names.clone(), dirs.clone());
        Rc::new(move || {
            let k = MODES[mode.selected() as usize].0;
            let n = start.text().trim().parse().unwrap_or(1);
            new_names(&names, k, &a.text(), &b.text(), where_.selected() == 0, n, &dirs)
        })
    };
    let (r, nm, fs, par) = (rule.clone(), names.clone(), files.clone(), parent.as_ref().clone());
    let dlg = sonata_ui::dialog::alert(
        &fmt(&tr!("Rename {n} Items"), &[("n", &files.len().to_string())]),
        "",
        &[("cancel", tr!("Cancel"), ""), ("rename", tr!("Rename"), "default")],
        move |rid| {
            if rid != "rename" {
                return;
            }
            let new = r();
            if !problems(&new).is_empty() || new == *nm {
                return;
            }
            let p = par.downgrade();
            rename(fs.clone(), new, None, Some(Rc::new(move |f, e| {
                sonata_ui::dialog::alert(&fmt(&tr!("“{name}” can’t be renamed."), &[("name", &ops::base(f))]), e.message(), &[("ok", "OK".into(), "default")], |_| {}, p.upgrade().as_ref());
            })));
        },
        Some(parent.as_ref()),
    );
    dlg.set_extra_child(Some(&grid));
    let changed = {
        let (mode, d) = (mode.clone(), dlg.downgrade());
        let (la, lb, b2, where2, ls, start2, example) = (la.clone(), lb.clone(), b.clone(), where_.clone(), ls.clone(), start.clone(), example.clone());
        Rc::new(move || {
            let k = MODES[mode.selected() as usize].0;
            la.set_label(&match k {
                "replace" => tr!("Find:"),
                "add" => tr!("Text:"),
                _ => tr!("Name:"),
            });
            lb.set_label(&tr!("Replace with:"));
            lb.set_visible(k == "replace");
            b2.set_visible(k == "replace");
            where2.set_visible(k == "add");
            ls.set_visible(k == "format");
            start2.set_visible(k == "format");
            let new = rule();
            let bad = problems(&new);
            example.set_label(&if bad.is_empty() { fmt(&tr!("Example: {name}"), &[("name", new.first().map(String::as_str).unwrap_or(""))]) } else { bad.clone() });
            if let Some(d) = d.upgrade() {
                d.set_response_enabled("rename", bad.is_empty() && new != *names);
            }
        })
    };
    for dd in [&mode, &where_] {
        let c = changed.clone();
        dd.connect_selected_notify(move |_| c());
    }
    for e in [&a, &b, &start] {
        let c = changed.clone();
        e.connect_changed(move |_| c());
    }
    changed();
    dlg
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(v: &[&str]) -> Vec<String> {
        v.iter().map(|x| x.to_string()).collect()
    }

    #[test]
    fn batch_rename_rules() {
        let names = s(&["IMG_1.jpg", "IMG_2.jpg", "notes"]);
        let dirs = [false, false, true];
        assert_eq!(new_names(&names, "replace", "IMG_", "Trip ", true, 1, &dirs), s(&["Trip 1.jpg", "Trip 2.jpg", "notes"]));
        assert_eq!(new_names(&names, "add", " v2", "", true, 1, &dirs), s(&["IMG_1 v2.jpg", "IMG_2 v2.jpg", "notes v2"]));
        assert_eq!(new_names(&names, "add", "Final ", "", false, 1, &dirs), s(&["Final IMG_1.jpg", "Final IMG_2.jpg", "Final notes"]));
        assert_eq!(new_names(&names, "format", "Trip", "", true, 9, &dirs), s(&["Trip 09.jpg", "Trip 10.jpg", "Trip 11"]));
        assert!(problems(&s(&["a", "a"])).len() > 0);
        assert!(problems(&s(&["a/b"])).len() > 0);
        assert!(problems(&s(&["a", "b"])).is_empty());
    }
}
