//! What Sonata calls its parts on screen (sonata2/names.py). Only the words
//! people see: code, settings keys, commands (`sonata2 launchpad`), D-Bus
//! actions and .desktop ids keep their technical names, so nothing
//! depending on them changes.
//!
//! They stay English here (a desktop entry's Name, a window title matched);
//! shown, they're translated: `sonata_i18n::gettext(names::APPS)`.

use sonata_i18n::n_;

/// The full-screen app grid (code: launchpad).
pub const APPS: &str = n_!("Apps");
/// The same apps in a panel (code: launchpad_window).
pub const APPS_MENU: &str = n_!("Apps Menu");
/// Search anything (code: spotlight).
pub const SEARCH: &str = n_!("Search");
/// Every window at once (code: mission / Wayfire scale).
pub const OVERVIEW: &str = n_!("Overview");
/// Files' search scope: everywhere.
pub const THIS_COMPUTER: &str = n_!("This Computer");

#[cfg(test)]
mod tests {
    #[test]
    fn names_are_translated() {
        // the same English words as names.py, so its translations apply
        // (the test runs in pt_BR: LANG in the test command)
        for n in [super::APPS, super::APPS_MENU, super::SEARCH, super::OVERVIEW, super::THIS_COMPUTER] {
            assert!(!n.is_empty());
            let _ = sonata_i18n::gettext(n);
        }
    }
}
