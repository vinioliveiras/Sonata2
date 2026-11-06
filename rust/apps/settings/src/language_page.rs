//! Settings > Language & Region (language_page.py, Vini): the system's
//! language and its formats -- as chosen on a first install -- apart from
//! the keyboard, which stays in Keyboard > Input Sources (Portuguese
//! (Brazil) with a Portugal keyboard). Sonata shows itself in the system's
//! language (sonata-i18n). The locales: sonata_system::locales.

use crate::kit::{combo_row, group, show_quietly_combo};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use serde_json::{json, Value};
use sonata_i18n::{gettext, n_, tr};
use sonata_system::locales;
use std::cell::RefCell;
use std::rc::{Rc, Weak};

/// (value, name): "locale" or the first day's number (0 Monday ... 6 Sunday).
pub const WEEK_STARTS: [(&str, &str); 4] = [("locale", n_!("Region's default")), ("6", n_!("Sunday")), ("0", n_!("Monday")), ("5", n_!("Saturday"))];

/// `options` with `value` in it (a locale set elsewhere is still shown).
pub fn with(options: Vec<(String, String)>, value: &str) -> Vec<(String, String)> {
    let mut options = options;
    if !value.is_empty() && !options.iter().any(|(v, _)| v == value) {
        options.push((value.to_string(), value.to_string()));
    }
    options
}

fn owned(list: Vec<(&'static str, &'static str)>) -> Vec<(String, String)> {
    list.into_iter().map(|(v, n)| (v.to_string(), n.to_string())).collect()
}

/// calendar.json's week_start as Python wrote it (a number or "locale") -> its option.
pub fn week_start_option(v: Option<&Value>) -> String {
    let s = match v {
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::String(s)) => s.clone(),
        _ => "locale".into(),
    };
    if WEEK_STARTS.iter().any(|(k, _)| *k == s) {
        s
    } else {
        "locale".into()
    }
}

#[derive(Clone, PartialEq, Debug)]
struct Choice {
    lang: String,
    region: String,
}

pub struct LanguagePage {
    settings: Weak<Settings>,
    me: Weak<LanguagePage>,
    state: RefCell<Choice>,
    pub lang_row: RefCell<Option<adw::ComboRow>>,
    pub region_row: RefCell<Option<adw::ComboRow>>,
    pub sample_row: RefCell<Option<adw::ActionRow>>,
}

impl LanguagePage {
    pub fn new(ctx: &Ctx) -> Rc<Self> {
        Rc::new_cyclic(|me| LanguagePage {
            settings: Rc::downgrade(ctx),
            me: me.clone(),
            state: RefCell::new(Choice { lang: String::new(), region: String::new() }),
            lang_row: RefCell::new(None),
            region_row: RefCell::new(None),
            sample_row: RefCell::new(None),
        })
    }

    pub fn groups(&self) -> Vec<adw::PreferencesGroup> {
        let lang_g = group(&tr!("Language"), &tr!("Sonata, your apps and the login screen use it from the next login."));
        let fmt_g = group(&tr!("Formats"), &tr!("How dates, times, numbers and money are written."));
        let kb_g = group("", "");
        let cur = locales::current();
        let lang = locales::normalise(cur.get("LANG").map(String::as_str).filter(|s| !s.is_empty()).unwrap_or("en_US.UTF-8"));
        let region = locales::normalise(&locales::formats_of(&cur));
        *self.state.borrow_mut() = Choice { lang: lang.clone(), region: region.clone() };
        let me = self.me.clone();
        let lang_row = combo_row(&tr!("Language"), with(owned(locales::languages()), &lang), &lang, move |v: &String| {
            if let Some(p) = me.upgrade() {
                p.set(Some(v), None);
            }
        }, "");
        lang_row.set_enable_search(true);
        lang_g.add(&lang_row);
        let me = self.me.clone();
        let region_row = combo_row(&tr!("Region"), with(owned(locales::regions()), &region), &region, move |v: &String| {
            if let Some(p) = me.upgrade() {
                p.set(None, Some(v));
            }
        }, "");
        region_row.set_enable_search(true);
        fmt_g.add(&region_row);
        let sample = adw::ActionRow::builder().title(tr!("Example")).subtitle("…").build();
        sample.set_subtitle_selectable(true);
        fmt_g.add(&sample);
        let ws = week_start_option(sonata_config::load("calendar", &json!({"week_start": "locale"})).get("week_start"));
        let s = self.settings.clone();
        fmt_g.add(&combo_row(&tr!("First day of the week"), WEEK_STARTS.iter().map(|(v, n)| (v.to_string(), gettext(n))).collect(), &ws, move |v: &String| {
            if let Some(s) = s.upgrade() {
                // the Calendar's own value: a weekday number, or "locale"
                s.save("calendar", "week_start", v.parse::<i64>().map(Value::from).unwrap_or_else(|_| json!(v)));
            }
        }, &tr!("Calendar")));
        let kb = adw::ActionRow::builder().title(tr!("Keyboard")).subtitle(tr!("Your keyboard layout is set apart: Keyboard > Input Sources.")).build();
        let go = gtk::Button::builder().label(tr!("Keyboard…")).valign(gtk::Align::Center).build();
        let s = self.settings.clone();
        go.connect_clicked(move |_| {
            if let Some(s) = s.upgrade() {
                s.select("keyboard");
            }
        });
        kb.add_suffix(&go);
        kb_g.add(&kb);
        *self.lang_row.borrow_mut() = Some(lang_row);
        *self.region_row.borrow_mut() = Some(region_row);
        *self.sample_row.borrow_mut() = Some(sample);
        self.sample();
        vec![lang_g, fmt_g, kb_g]
    }

    fn sample(&self) {
        let region = self.state.borrow().region.clone();
        let row = self.sample_row.borrow().as_ref().map(|r| r.downgrade());
        crate::kit::run_async(move || locales::sample(&region), move |text| {
            if let Some(r) = row.and_then(|r| r.upgrade()) {
                r.set_subtitle(&if text.is_empty() { tr!("Shown once the region's formats are installed (next login).") } else { text });
            }
        });
    }

    /// A new language or region: the system's settings (root: Sonata's
    /// password prompt), then "log out now or later".
    fn set(&self, lang: Option<&str>, region: Option<&str>) {
        let before = self.state.borrow().clone();
        {
            let mut st = self.state.borrow_mut();
            if let Some(l) = lang {
                st.lang = l.to_string();
                if region.is_none() {
                    // formats follow the language they went with
                    st.region = locales::follow_region(&before.lang, l, &before.region);
                }
            }
            if let Some(r) = region {
                st.region = r.to_string();
            }
        }
        let now = self.state.borrow().clone();
        if now == before {
            return; // (put back after a failure: nothing to do)
        }
        let Some(cmd) = locales::command(&now.lang, &now.region) else { return };
        let me = self.me.clone();
        crate::kit::run_async(
            move || std::process::Command::new("pkexec").args(["sh", "-c", &cmd]).output().is_ok_and(|o| o.status.success()),
            move |ok| {
                let Some(p) = me.upgrade() else { return };
                let lang_row = p.lang_row.borrow().clone();
                let region_row = p.region_row.borrow().clone();
                if !ok {
                    *p.state.borrow_mut() = before.clone();
                    if let Some(r) = &lang_row {
                        show_quietly_combo(r, &before.lang);
                    }
                    if let Some(r) = &region_row {
                        show_quietly_combo(r, &before.region);
                    }
                    if let Some(s) = p.settings.upgrade() {
                        s.toast(&tr!("Couldn't change the language"));
                    }
                    return;
                }
                if now.region != before.region {
                    if let Some(r) = &region_row {
                        show_quietly_combo(r, &now.region);
                    }
                }
                p.sample();
                if let Some(s) = p.settings.upgrade() {
                    s.ask_restart("session", &tr!("The new language and formats"));
                }
            },
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_locale_set_elsewhere_is_shown() {
        let opts = vec![("en_US.UTF-8".to_string(), "English".to_string())];
        assert_eq!(with(opts.clone(), "de_DE.UTF-8").len(), 2);
        assert_eq!(with(opts.clone(), "en_US.UTF-8").len(), 1);
        assert_eq!(with(opts, "").len(), 1);
    }

    #[test]
    fn week_start_read_as_written() {
        assert_eq!(week_start_option(Some(&json!(6))), "6");
        assert_eq!(week_start_option(Some(&json!("locale"))), "locale");
        assert_eq!(week_start_option(Some(&json!(3))), "locale");
        assert_eq!(week_start_option(None), "locale");
    }
}
