//! A Chromium web app's links (webapps/chromeguard.py; Vini: a link opened a
//! whole browser window with tabs, out of Sonata's look): no tabs or
//! windows of its own -- a new tab or window goes to the default browser
//! and closes; login popups (a window the page keeps a hand on, e.g. "Sign
//! in with Google") stay.
//!
//! The browser runs with --remote-debugging-pipe: DevTools' protocol over
//! two pipes (no port anyone else could reach), messages are JSON ended by
//! a NUL. Sonata only watches which pages open (Target.setDiscoverTargets).
//!
//! Its window's title (Vini: WhatsApp's window read "web.whatsapp.com" --
//! its address -- with neither its name nor its unread count, while a tab
//! of it read "(10) WhatsApp"): Chrome titles the window from the current
//! history entry, and a page that moves within itself (pushState:
//! WhatsApp) leaves a new entry without a title until the title changes
//! again. The app's page is watched (Page.enable on its own session) and
//! after each such move, and each load, its own title is set again (the
//! same text, nudged), so Chrome takes it -- the page's real name and
//! count, nothing made up.
//!
//! ```ignore
//! let mut guard = Guard::new(app_url, write);   // write(&[u8]): to the browser
//! guard.feed(data);                             // bytes from the browser
//! ```

use crate::urlsplit;
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};

const KEEP_SCHEMES: [&str; 2] = ["devtools:", "chrome-extension:"];

type Write = Box<dyn Fn(&[u8])>;
type Text = Box<dyn Fn(&str)>;

pub struct Guard {
    pub app_url: String,
    pub name: String,
    /// on_title(title): the app's page's (badges.rs)
    pub on_title: Option<Text>,
    write: Write,
    open_outside: Text,
    pub log: Text,
    /// the web app's own page (the first one)
    pub main: Option<String>,
    pub pages: HashMap<String, Map<String, Value>>,
    handled: HashSet<String>,
    buf: Vec<u8>,
    id: i64,
    /// attached to the main page (for its title)
    pub session: Option<String>,
    title_tries: HashMap<String, u32>,
    /// ids of the NUDGEs sent (their answers: the page's title)
    asked: HashSet<i64>,
    own_logged: u32,
    last_title: Option<String>,
    titles_logged: u32,
}

impl Guard {
    /// per address shown as title (never a loop with the page)
    pub const TITLE_TRIES: u32 = 3;
    /// the page's own title, set again (a change Chrome hears: zero-width
    /// space, then back); it answers with that title, to know when the page
    /// has none
    pub const NUDGE: &'static str =
        "(() => { const t = document.title; if (t) { document.title = t + '\\u200b'; document.title = t; } return t; })()";
    pub const TITLES_LOGGED: u32 = 5;

    pub fn new(app_url: &str, write: impl Fn(&[u8]) + 'static) -> Self {
        let mut g = Guard {
            app_url: app_url.into(),
            name: String::new(),
            on_title: None,
            write: Box::new(write),
            open_outside: Box::new(open_default),
            log: Box::new(|_| {}),
            main: None,
            pages: HashMap::new(),
            handled: HashSet::new(),
            buf: Vec::new(),
            id: 0,
            session: None,
            title_tries: HashMap::new(),
            asked: HashSet::new(),
            own_logged: 0,
            last_title: None,
            titles_logged: 0,
        };
        g.send("Target.setDiscoverTargets", json!({"discover": true}), None);
        g
    }

    pub fn with_open_outside(mut self, f: impl Fn(&str) + 'static) -> Self {
        self.open_outside = Box::new(f);
        self
    }

    pub fn with_log(mut self, f: impl Fn(&str) + 'static) -> Self {
        self.log = Box::new(f);
        self
    }

    pub fn with_name(mut self, name: &str) -> Self {
        self.name = name.into();
        self
    }

    pub fn with_on_title(mut self, f: impl Fn(&str) + 'static) -> Self {
        self.on_title = Some(Box::new(f));
        self
    }

    pub fn send(&mut self, method: &str, params: Value, session: Option<&str>) -> i64 {
        self.id += 1;
        let mut msg = json!({"id": self.id, "method": method, "params": if params.is_null() { json!({}) } else { params }});
        if let Some(s) = session {
            msg["sessionId"] = json!(s);
        }
        let mut data = msg.to_string().into_bytes();
        data.push(0);
        (self.write)(&data);
        self.id
    }

    pub fn feed(&mut self, data: &[u8]) {
        self.buf.extend_from_slice(data);
        while let Some(at) = self.buf.iter().position(|&b| b == 0) {
            let msg: Vec<u8> = self.buf.drain(..=at).collect();
            // (anything else than an object: not a message; the watch goes on)
            if let Ok(Value::Object(m)) = serde_json::from_slice::<Value>(&msg[..msg.len() - 1]) {
                self.message(&m);
            }
        }
    }

    pub fn message(&mut self, msg: &Map<String, Value>) {
        if let Some(id) = msg.get("id").and_then(Value::as_i64) {
            if self.asked.remove(&id) {
                let title = msg.get("result").and_then(|r| r.get("result")).and_then(|r| r.get("value")).cloned();
                self.own_title(title.as_ref().and_then(Value::as_str).unwrap_or(""));
                return;
            }
        }
        let method = msg.get("method").and_then(Value::as_str).unwrap_or("");
        let params = msg.get("params").cloned().unwrap_or(Value::Null);
        if method == "Target.attachedToTarget" {
            let tid = params.get("targetInfo").and_then(|i| i.get("targetId")).and_then(Value::as_str);
            let sid = params.get("sessionId").and_then(Value::as_str).filter(|s| !s.is_empty());
            if let (Some(tid), Some(sid)) = (tid, sid) {
                if Some(tid) == self.main.as_deref() {
                    self.session = Some(sid.to_string());
                    self.send("Page.enable", Value::Null, Some(sid)); // its moves and loads, below
                    self.retitle();
                }
            }
            return;
        }
        if method == "Page.navigatedWithinDocument" || method == "Page.loadEventFired" {
            if self.session.is_some() && msg.get("sessionId").and_then(Value::as_str) == self.session.as_deref() {
                self.retitle(); // a new history entry: its title again
            }
            return;
        }
        if method != "Target.targetCreated" && method != "Target.targetInfoChanged" {
            return;
        }
        let Some(info) = params.get("targetInfo").and_then(Value::as_object).cloned() else { return };
        if info.get("type").and_then(Value::as_str) != Some("page") {
            return;
        }
        let tid = info.get("targetId").and_then(Value::as_str).unwrap_or("").to_string();
        if self.main.is_none() {
            self.main = Some(tid.clone()); // the --app window's page
            self.send("Target.attachToTarget", json!({"targetId": tid, "flatten": true}), None); // (its title: above)
        }
        if Some(tid.as_str()) == self.main.as_deref() {
            if let Some(title) = info.get("title").and_then(Value::as_str) {
                if let Some(f) = &self.on_title {
                    f(title);
                }
                if self.last_title.as_deref() != Some(title) && self.titles_logged < 5 {
                    // (the first few: to compare with the window's)
                    self.last_title = Some(title.to_string());
                    self.titles_logged += 1;
                    let url = info.get("url").and_then(Value::as_str).unwrap_or("");
                    (self.log)(&format!("page title: {:?} at {}", cut(title, 80), cut(url, 80)));
                }
            }
            self.check_title(&info);
            return;
        }
        if self.handled.contains(&tid) {
            return;
        }
        self.pages.insert(tid.clone(), info.clone());
        let url = info.get("url").and_then(Value::as_str).unwrap_or("").to_string();
        if KEEP_SCHEMES.iter().any(|s| url.starts_with(s)) {
            return;
        }
        if info.get("canAccessOpener").is_some_and(truthy) {
            return; // a popup the page uses (a login): it stays
        }
        if url.is_empty() || url == "about:blank" {
            return; // not navigated yet: its address comes next
        }
        self.handled.insert(tid.clone());
        self.send("Target.closeTarget", json!({"targetId": tid}), None);
        if ["http:", "https:", "mailto:"].iter().any(|s| url.starts_with(s)) {
            (self.log)(&format!("opened outside: {}", cut(&url, 120)));
            (self.open_outside)(&url);
        } else {
            let what = if url.starts_with("chrome:") { "tab" } else { "page" };
            (self.log)(&format!("closed a new {what}: {}", cut(&url, 60)));
        }
    }

    fn check_title(&mut self, info: &Map<String, Value>) {
        let url = info.get("url").and_then(Value::as_str).unwrap_or("").to_string();
        let Some(title) = info.get("title") else { return };
        let title = title.as_str().unwrap_or("");
        if !(url.starts_with("http:") || url.starts_with("https:")) || !is_address(title, &url) {
            return;
        }
        let tries = self.title_tries.get(&url).copied().unwrap_or(0);
        if tries >= Self::TITLE_TRIES {
            return;
        }
        self.title_tries.insert(url, tries + 1);
        self.retitle();
    }

    /// The page's own title set again, so Chrome takes it for the window
    /// (its answer: own_title).
    fn retitle(&mut self) {
        if let Some(s) = self.session.clone() {
            let id = self.send("Runtime.evaluate", json!({"expression": Self::NUDGE, "returnByValue": true}), Some(&s));
            self.asked.insert(id);
        }
    }

    /// What the page's title really is. Empty (Vini: WhatsApp's window
    /// opened as "web.whatsapp.com" and stayed so -- the page never gave
    /// Chrome a title): the web app's own name, the one its user gave it,
    /// until the page sets one ("(3) WhatsApp" then wins).
    fn own_title(&mut self, title: &str) {
        if self.own_logged < Self::TITLES_LOGGED {
            self.own_logged += 1;
            (self.log)(&format!("page's own title: {}", py_repr(cut(title, 80))));
        }
        if title.is_empty() && !self.name.is_empty() {
            if let Some(s) = self.session.clone() {
                let expr = format!("if (!document.title) document.title = {}", Value::String(self.name.clone()));
                self.send("Runtime.evaluate", json!({"expression": expr}), Some(&s));
            }
        }
    }
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn cut(s: &str, n: usize) -> &str {
    s.char_indices().nth(n).map_or(s, |(i, _)| &s[..i])
}

/// Python's repr of a plain string, as the log lines had it ('' and 'x').
fn py_repr(s: &str) -> String {
    if s.contains('\'') && !s.contains('"') {
        format!("\"{s}\"")
    } else {
        format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

/// Chrome's title for a page without one: its address (or nothing).
pub fn is_address(title: &str, url: &str) -> bool {
    let title = title.trim().trim_end_matches('/');
    if title.is_empty() {
        return true;
    }
    let host = urlsplit(url).hostname().unwrap_or_default();
    let bare = url.split_once("://").map_or(url, |(_, b)| b).trim_end_matches('/');
    [host.as_str(), url.trim_end_matches('/'), bare, host.strip_prefix("www.").unwrap_or(&host)].contains(&title)
}

/// In the default browser (or mail app): Chrome when it's the default. A
/// browser started from here goes to a scope of its own (appscope): it
/// stayed in the web app's, under its memory cap, and lived and died with it.
pub fn open_default(url: &str) {
    let ctx = gio::AppLaunchContext::new();
    sonata_desktop::appscope::watch(&ctx, "");
    if let Err(e) = gio::AppInfo::launch_default_for_uri(url, Some(&ctx)) {
        println!("sonata2 webapp: can't open {url}: {}", e.message());
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::rc::Rc;

    const URL: &str = "https://web.whatsapp.com/";

    struct T {
        g: Guard,
        sent: Rc<RefCell<Vec<Vec<u8>>>>,
        opened: Rc<RefCell<Vec<String>>>,
    }

    impl T {
        fn new(app: &str, name: &str) -> T {
            let sent: Rc<RefCell<Vec<Vec<u8>>>> = Rc::default();
            let opened: Rc<RefCell<Vec<String>>> = Rc::default();
            let (s, o) = (sent.clone(), opened.clone());
            let g = Guard::new(app, move |b| s.borrow_mut().push(b.to_vec()))
                .with_open_outside(move |u| o.borrow_mut().push(u.to_string()))
                .with_name(name);
            T { g, sent, opened }
        }
        fn msgs(&self) -> Vec<Value> {
            self.sent.borrow().iter().map(|m| serde_json::from_slice(&m[..m.len() - 1]).unwrap()).collect()
        }
        fn feed(&mut self, ms: &[Value]) {
            let mut data = Vec::new();
            for m in ms {
                data.extend(m.to_string().into_bytes());
                data.push(0);
            }
            self.g.feed(&data);
        }
        fn attach(&mut self) {
            self.feed(&[info("A", URL, "WhatsApp", "Target.targetCreated"), json!({"method": "Target.attachedToTarget", "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}})]);
        }
        fn answer(&mut self, value: &str) -> Vec<Value> {
            let ask = self.msgs().last().cloned().unwrap();
            assert_eq!(ask["method"], "Runtime.evaluate");
            assert_eq!(ask["params"]["returnByValue"], true);
            let n = self.sent.borrow().len();
            self.feed(&[json!({"id": ask["id"], "sessionId": "S1", "result": {"result": {"type": "string", "value": value}}})]);
            self.msgs()[n..].to_vec()
        }
    }

    fn info(tid: &str, url: &str, title: &str, method: &str) -> Value {
        json!({"method": method, "params": {"targetInfo": {"targetId": tid, "type": "page", "url": url, "title": title}}})
    }

    fn target(tid: &str, url: &str, opener: bool, kind: &str) -> Value {
        json!({"method": "Target.targetCreated", "params": {"targetInfo": {"targetId": tid, "type": kind, "url": url, "canAccessOpener": opener}}})
    }

    #[test]
    fn test_watches_the_app_page() {
        let mut t = T::new(URL, "WhatsApp");
        t.feed(&[info("A", URL, "WhatsApp", "Target.targetCreated")]);
        let last = t.msgs().last().cloned().unwrap();
        assert_eq!(last["method"], "Target.attachToTarget");
        assert_eq!(last["params"], json!({"targetId": "A", "flatten": true}));
        t.feed(&[json!({"method": "Target.attachedToTarget", "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}})]);
        let m = t.msgs();
        let methods: Vec<(String, String)> = m[m.len() - 2..].iter().map(|m| (m["method"].as_str().unwrap().into(), m["sessionId"].as_str().unwrap_or("").into())).collect();
        assert_eq!(methods, [("Page.enable".into(), "S1".into()), ("Runtime.evaluate".into(), "S1".into())]);
    }

    /// pushState left a history entry without a title: Chrome showed the address.
    #[test]
    fn test_title_set_again_after_a_move_within_the_page() {
        let mut t = T::new(URL, "WhatsApp");
        t.attach();
        let n = t.sent.borrow().len();
        t.feed(&[json!({"method": "Page.navigatedWithinDocument", "sessionId": "S1", "params": {"frameId": "A", "url": format!("{URL}chat")}})]);
        let last = t.msgs().last().cloned().unwrap();
        assert_eq!(t.sent.borrow().len(), n + 1);
        assert_eq!((last["method"].as_str(), last["sessionId"].as_str()), (Some("Runtime.evaluate"), Some("S1")));
        assert!(last["params"]["expression"].as_str().unwrap().contains("document.title = t"));
        t.feed(&[json!({"method": "Page.navigatedWithinDocument", "sessionId": "OTHER", "params": {}})]);
        assert_eq!(t.sent.borrow().len(), n + 1); // another page's: no
    }

    /// Vini: no hard-coded name -- the page's own title is set again.
    #[test]
    fn test_nothing_made_up() {
        let mut t = T::new(URL, "WhatsApp");
        t.attach();
        let expr = t.msgs().last().unwrap()["params"]["expression"].as_str().unwrap().to_string();
        assert!(!expr.contains("WhatsApp"));
        assert!(expr.contains("if (t)"));
        assert!(expr.contains("t + '\\u200b'")); // the JavaScript escape, not the character
    }

    /// Vini: WhatsApp's window opened as "web.whatsapp.com" and stayed so
    /// (its log: the page never gave Chrome a title). The web app's own name
    /// stands in, only while the page has none.
    #[test]
    fn test_page_without_a_title_gets_the_apps_name() {
        let mut t = T::new(URL, "WhatsApp");
        let logged: Rc<RefCell<Vec<String>>> = Rc::default();
        let l = logged.clone();
        t.g.log = Box::new(move |m| l.borrow_mut().push(m.to_string()));
        t.attach();
        let sent = t.answer("");
        assert_eq!(sent.len(), 1);
        assert_eq!(sent[0]["params"]["expression"], "if (!document.title) document.title = \"WhatsApp\"");
        assert_eq!(sent[0]["sessionId"], "S1");
        assert!(logged.borrow().contains(&"page's own title: ''".to_string()));
    }

    #[test]
    fn test_page_with_a_title_keeps_it() {
        let mut t = T::new(URL, "WhatsApp");
        t.attach();
        assert!(t.answer("(3) WhatsApp").is_empty()); // its own title stays
    }

    #[test]
    fn test_no_name_nothing_made_up() {
        let mut t = T::new(URL, "");
        t.attach();
        assert!(t.answer("").is_empty());
    }

    #[test]
    fn test_address_title_tried_a_few_times_only() {
        let mut t = T::new(URL, "WhatsApp");
        t.attach();
        for _ in 0..10 {
            t.feed(&[info("A", URL, "web.whatsapp.com", "Target.targetInfoChanged")]);
        }
        let evals = t.msgs().iter().filter(|m| m["method"] == "Runtime.evaluate").count() as u32;
        assert!(evals <= 1 + Guard::TITLE_TRIES);
    }

    #[test]
    fn test_other_pages_untouched() {
        let mut t = T::new(URL, "WhatsApp");
        t.feed(&[info("A", URL, "WhatsApp", "Target.targetCreated"), json!({"method": "Target.attachedToTarget", "params": {"sessionId": "X", "targetInfo": {"targetId": "B"}}})]);
        assert!(t.g.session.is_none());
    }

    #[test]
    fn test_is_address() {
        for t in ["", "web.whatsapp.com", "web.whatsapp.com/", URL, "https://web.whatsapp.com"] {
            assert!(is_address(t, URL), "{t}");
        }
        for t in ["WhatsApp", "(2) WhatsApp", "whatsapp"] {
            assert!(!is_address(t, URL), "{t}");
        }
        assert!(is_address("instagram.com", "https://www.instagram.com/"));
    }

    #[test]
    fn test_guard_reports_the_page_title() {
        let got: Rc<RefCell<Vec<String>>> = Rc::default();
        let g2 = got.clone();
        let mut g = Guard::new(URL, |_| {}).with_open_outside(|_| {}).with_on_title(move |t| g2.borrow_mut().push(t.into()));
        for m in [info("A", URL, "WhatsApp", "Target.targetCreated"), info("A", URL, "(2) WhatsApp", "Target.targetInfoChanged"), info("B", "https://x.org/", "(9) X", "Target.targetInfoChanged")] {
            let mut d = m.to_string().into_bytes();
            d.push(0);
            g.feed(&d);
        }
        assert_eq!(*got.borrow(), ["WhatsApp", "(2) WhatsApp"]);
    }

    /// Vini: WhatsApp Web stayed on its loading screen for ever, after Sonata
    /// began running a watcher of its own in the page. The web app's page
    /// gets nothing of the sort: Sonata only reads it.
    #[test]
    fn test_nothing_run_in_the_page_on_every_load() {
        let mut t = T::new(URL, "WhatsApp");
        t.feed(&[info("A", URL, "", "Target.targetCreated"), json!({"method": "Target.attachedToTarget", "params": {"sessionId": "S1", "targetInfo": {"targetId": "A", "type": "page"}}}), json!({"method": "Page.loadEventFired", "params": {}, "sessionId": "S1"})]);
        let methods: HashSet<String> = t.msgs().iter().map(|m| m["method"].as_str().unwrap().to_string()).collect();
        for m in ["Runtime.enable", "Runtime.addBinding", "Page.addScriptToEvaluateOnNewDocument"] {
            assert!(!methods.contains(m));
        }
    }

    #[test]
    fn test_new_tabs_go_to_the_default_browser() {
        let mut t = T::new(URL, "");
        assert_eq!(t.msgs()[0]["method"], "Target.setDiscoverTargets");
        t.feed(&[target("A", URL, false, "page")]); // the app's own page
        t.feed(&[target("B", "about:blank", false, "page")]); // a new tab, no address yet
        assert!(t.opened.borrow().is_empty());
        t.feed(&[json!({"method": "Target.targetInfoChanged", "params": {"targetInfo": {"targetId": "B", "type": "page", "url": "https://example.org/a"}}})]);
        assert_eq!(*t.opened.borrow(), ["https://example.org/a"]);
        let last = t.msgs().last().cloned().unwrap();
        assert_eq!((last["method"].clone(), last["params"].clone()), (json!("Target.closeTarget"), json!({"targetId": "B"})));
        t.feed(&[target("C", "chrome://newtab/", false, "page")]); // Ctrl+T: just closed
        assert_eq!(t.msgs().last().unwrap()["params"], json!({"targetId": "C"}));
        assert_eq!(t.opened.borrow().len(), 1);
    }

    #[test]
    fn test_login_popups_and_workers_stay() {
        let mut t = T::new(URL, "");
        t.feed(&[target("A", URL, false, "page"), target("P", "https://accounts.google.com/o/oauth2", true, "page"), target("W", "https://web.whatsapp.com/sw.js", false, "service_worker")]);
        assert!(t.opened.borrow().is_empty());
        assert!(!t.msgs().iter().any(|m| m["method"] == "Target.closeTarget")); // nothing closed
    }

    #[test]
    fn test_split_messages() {
        let mut t = T::new(URL, "");
        let mut data = target("A", URL, false, "page").to_string().into_bytes();
        data.push(0);
        data.extend(target("B", "https://x.org/", false, "page").to_string().into_bytes());
        data.push(0);
        t.g.feed(&data[..17]);
        t.g.feed(&data[17..]);
        assert_eq!(*t.opened.borrow(), ["https://x.org/"]);
    }

    /// Review: a message that isn't an object raised out of the pipe's watch
    /// (the guard stopped); a handled tab isn't closed or opened twice; the
    /// app's own page is never closed, wherever it goes.
    #[test]
    fn test_odd_messages_and_repeats() {
        let mut t = T::new(URL, "");
        t.g.feed(b"[1,2]\0\"x\"\0{bad\0");
        t.feed(&[target("A", URL, false, "page")]);
        t.feed(&[json!({"method": "Target.targetInfoChanged", "params": {"targetInfo": {"targetId": "A", "type": "page", "url": "https://elsewhere.org/"}}})]);
        t.feed(&[target("B", "https://x.org/", false, "page")]);
        t.feed(&[json!({"method": "Target.targetInfoChanged", "params": {"targetInfo": {"targetId": "B", "type": "page", "url": "https://x.org/2"}}})]);
        t.feed(&[target("M", "mailto:a@b.c", false, "page")]);
        assert_eq!(*t.opened.borrow(), ["https://x.org/", "mailto:a@b.c"]);
        let closed: Vec<String> = t.msgs().iter().filter(|m| m["method"] == "Target.closeTarget").map(|m| m["params"]["targetId"].as_str().unwrap().to_string()).collect();
        assert_eq!(closed, ["B", "M"]);
    }
}
