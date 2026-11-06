//! Tabs and windows from the menu bar, for any app (shell/apptabs.py; Vini:
//! "New Tab" in the menu bar like macOS -- File > New Tab, Window > Show
//! Previous / Next Tab).
//!
//! Apps keep their own tabs; the menu bar presses the app's own shortcut
//! once its menu has closed and the keyboard is back on the app. Which
//! shortcuts depends on the kind of app: terminals use Ctrl+Shift (Ctrl+T,
//! Ctrl+W belong to the shell), the rest Ctrl.
//!
//! The pressing itself is wl/vkeyboard.py (a virtual keyboard with real
//! keycodes: wtype's made-up ones reached Wayfire as Ctrl+Shift+Esc), not
//! ported yet -- there is no Wayland client crate in the workspace. So
//! press() takes the presser: the menu bar passes its own once it has one.

pub const TERMINALS: [&str; 16] = [
    "terminal", "konsole", "kitty", "alacritty", "foot", "ghostty", "wezterm", "xterm", "tilix", "terminator", "kgx", "console", "ptyxis", "blackbox", "rio", "warp",
];
/// terminals with no tabs of their own
pub const NO_TABS: [&str; 4] = ["alacritty", "foot", "xterm", "rio"];
/// apps with tabs: browsers, file managers, editors (Sonata's Files, TextEdit, Terminal among them)
pub const TABBED: [&str; 24] = [
    "firefox", "chrome", "chromium", "brave", "vivaldi", "microsoft-edge", "librewolf", "zen-", "opera", "floorp", "waterfox", "epiphany", "falkon", "sonata2.files", "nautilus", "dolphin", "thunar", "nemo", "textedit", "texteditor", "gedit", "kate", "sublime", "zed",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    /// Ctrl+Shift shortcuts
    Terminal,
    /// Ctrl shortcuts
    Tabbed,
}

/// (key, modifiers), the names vkeyboard knows ("Page_Down", "ctrl").
pub type Combo = (&'static str, &'static [&'static str]);

const CTRL: &[&str] = &["ctrl"];
const CTRL_SHIFT: &[&str] = &["ctrl", "shift"];

/// The actions the menu bar offers.
pub const ACTIONS: [&str; 5] = ["new_window", "new_tab", "close_tab", "next_tab", "prev_tab"];

/// SHORTCUTS: each kind's (action, combo); None is an app with no tabs known.
pub fn shortcuts(kind: Option<Kind>) -> &'static [(&'static str, Combo)] {
    match kind {
        Some(Kind::Terminal) => &[
            ("new_window", ("n", CTRL_SHIFT)),
            ("new_tab", ("t", CTRL_SHIFT)),
            ("close_tab", ("w", CTRL_SHIFT)),
            ("next_tab", ("Page_Down", CTRL)),
            ("prev_tab", ("Page_Up", CTRL)),
        ],
        Some(Kind::Tabbed) => &[
            ("new_window", ("n", CTRL)),
            ("new_tab", ("t", CTRL)),
            ("close_tab", ("w", CTRL)),
            ("next_tab", ("Tab", CTRL)),
            ("prev_tab", ("Tab", CTRL_SHIFT)),
        ],
        None => &[("new_window", ("n", CTRL))],
    }
}

pub fn is_terminal(app_id: &str) -> bool {
    let a = app_id.to_lowercase();
    TERMINALS.iter().any(|t| a.contains(t))
}

/// Terminal (Ctrl+Shift shortcuts), Tabbed (Ctrl), or None.
pub fn kind(app_id: &str) -> Option<Kind> {
    let a = app_id.to_lowercase();
    if is_terminal(&a) {
        return if NO_TABS.iter().any(|t| a.contains(t)) { None } else { Some(Kind::Terminal) };
    }
    let flat = a.replace(['-', '_'], "");
    if a == "zen" || a == "app.zen_browser.zen" || TABBED.iter().any(|t| a.contains(t) || flat.contains(t)) {
        return Some(Kind::Tabbed);
    }
    None
}

/// (key, modifiers) for an action in this app, or None.
pub fn keys(app_id: &str, action: &str) -> Option<Combo> {
    let k = kind(app_id);
    if k.is_none() && is_terminal(app_id) {
        // a terminal without tabs still opens windows, the terminal's way
        return (action == "new_window").then_some(("n", CTRL_SHIFT));
    }
    shortcuts(k).iter().find(|(a, _)| *a == action).map(|(_, c)| *c)
}

/// Press the app's shortcut for `action` (the app has the keyboard) with
/// `presser(key, modifiers)` -- vkeyboard.press in the Python menu bar.
pub fn press(app_id: &str, action: &str, presser: impl Fn(&str, &[&str]) -> bool) -> bool {
    match keys(app_id, action) {
        Some((key, mods)) => presser(key, mods),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// tests/test_window_menu.py: test_kinds.
    #[test]
    fn kinds() {
        assert_eq!(kind("io.github.vinioliveiras.sonata2.terminal"), Some(Kind::Terminal));
        assert_eq!(kind("org.gnome.Console"), Some(Kind::Terminal));
        assert_eq!(kind("kitty"), Some(Kind::Terminal));
        assert_eq!(kind("foot"), None); // a terminal without tabs
        for a in ["firefox", "google-chrome", "brave-browser", "io.github.vinioliveiras.sonata2.files", "io.github.vinioliveiras.sonata2.textedit", "org.gnome.TextEditor", "org.gnome.Nautilus", "zen", "microsoft-edge"] {
            assert_eq!(kind(a), Some(Kind::Tabbed), "{a}");
        }
        for a in ["org.gnome.Calculator", "steam", "io.github.vinioliveiras.sonata2.notes", "zenity", ""] {
            assert_eq!(kind(a), None, "{a}");
        }
        assert_eq!(keys("kitty", "new_tab"), Some(("t", CTRL_SHIFT))); // Ctrl+T is the shell's
        assert_eq!(keys("firefox", "new_tab"), Some(("t", CTRL)));
        assert_eq!(keys("foot", "new_window"), Some(("n", CTRL_SHIFT)));
        assert_eq!(keys("foot", "new_tab"), None);
        assert_eq!(keys("org.gnome.Calculator", "new_window"), Some(("n", CTRL)));
    }

    /// tests/test_vkeyboard.py: every shortcut is a key vkeyboard knows.
    #[test]
    fn every_menu_bar_shortcut_is_a_known_key() {
        const KNOWN: [&str; 6] = ["n", "t", "w", "Tab", "Page_Down", "Page_Up"];
        for k in [Some(Kind::Terminal), Some(Kind::Tabbed), None] {
            for (action, (key, mods)) in shortcuts(k) {
                assert!(ACTIONS.contains(action));
                assert!(KNOWN.contains(key), "{key}");
                assert!(mods.iter().all(|m| ["ctrl", "shift"].contains(m)));
            }
        }
    }

    #[test]
    fn press_uses_the_presser() {
        let got = std::cell::RefCell::new(Vec::new());
        assert!(press("kitty", "close_tab", |k, m| {
            got.borrow_mut().push((k.to_string(), m.join("+")));
            true
        }));
        assert!(!press("org.gnome.Calculator", "new_tab", |_, _| true));
        assert_eq!(*got.borrow(), vec![("w".to_string(), "ctrl+shift".to_string())]);
    }
}
