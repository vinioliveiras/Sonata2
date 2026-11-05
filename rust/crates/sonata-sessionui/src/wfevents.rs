//! Wayfire's IPC for the menu bar's window helpers (fullscreen.rs,
//! fullscreenfirst.rs): one request at a time through sonata-backend's
//! wfipc, and the event stream (`window-rules/events/watch`, wfipc.py's
//! watch()) read on the main loop. Behind a trait so the tests hand in a
//! fake Wayfire.

use serde_json::{json, Value};
use std::cell::RefCell;
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::rc::Rc;
use std::time::Duration;

/// What the helpers ask of Wayfire.
pub trait Ipc {
    fn available(&self) -> bool;
    fn call(&self, method: &str, data: Value) -> Option<Value>;
    /// `callback(event)` for each of `events`; false when it can't listen.
    fn watch(&self, events: &[&str], callback: Box<dyn Fn(&Value)>) -> bool;
}

/// The real Wayfire ($WAYFIRE_SOCKET). Keeps its event streams open for
/// as long as it lives.
#[derive(Default)]
pub struct WayfireIpc {
    watches: RefCell<Vec<Watch>>,
}

impl Ipc for WayfireIpc {
    fn available(&self) -> bool {
        sonata_backend::wfipc::available()
    }

    fn call(&self, method: &str, data: Value) -> Option<Value> {
        sonata_backend::wfipc::call(method, data)
    }

    fn watch(&self, events: &[&str], callback: Box<dyn Fn(&Value)>) -> bool {
        match watch_events(events, callback) {
            Some(w) => {
                self.watches.borrow_mut().push(w);
                true
            }
            None => false,
        }
    }
}

/// An event stream; dropped, it stops.
pub struct Watch {
    _stream: UnixStream,
    source: Option<glib::SourceId>,
}

impl Drop for Watch {
    fn drop(&mut self) {
        if let Some(s) = self.source.take() {
            s.remove();
        }
    }
}

fn watch_events(events: &[&str], callback: Box<dyn Fn(&Value)>) -> Option<Watch> {
    let mut s = UnixStream::connect(sonata_backend::wfipc::socket()?).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    s.write_all(&sonata_backend::wfipc::frame("window-rules/events/watch", &json!({"events": events}))).ok()?;
    sonata_backend::wfipc::read_message(&mut s)?; // {"result": "ok"}
    let reader = RefCell::new(s.try_clone().ok()?);
    let callback = Rc::new(callback);
    let source = glib::unix_fd_add_local(s.as_raw_fd(), glib::IOCondition::IN | glib::IOCondition::HUP, move |_, cond| {
        if cond.contains(glib::IOCondition::HUP) {
            return glib::ControlFlow::Break; // Wayfire went away
        }
        let Some(msg) = sonata_backend::wfipc::read_message(&mut *reader.borrow_mut()) else { return glib::ControlFlow::Break };
        if msg.get("event").is_some() {
            callback(&msg);
        }
        glib::ControlFlow::Continue
    });
    Some(Watch { _stream: s, source: Some(source) })
}

/// later(ms, work): `work` once, `ms` from now, on the main loop (tests run
/// it when they like).
pub type Later = Rc<dyn Fn(u32, Box<dyn FnOnce()>)>;

pub fn main_loop_later() -> Later {
    Rc::new(|ms, work| {
        glib::timeout_add_local_once(Duration::from_millis(ms as u64), work);
    })
}

/// window-rules/list-views: the windows, or none.
pub fn views(ipc: &dyn Ipc) -> Vec<Value> {
    match ipc.call("window-rules/list-views", json!({})) {
        Some(Value::Array(v)) => v.into_iter().filter(|v| v.is_object()).collect(),
        _ => Vec::new(),
    }
}

/// view.get(key, default) for a string field: the default when it's
/// missing, None when it's there but not a string (Python compares it
/// unequal then).
pub fn str_or<'a>(view: &'a Value, key: &str, default: &'a str) -> Option<&'a str> {
    match view.get(key) {
        None => Some(default),
        Some(v) => v.as_str(),
    }
}

/// A toplevel window (role "toplevel", the default).
pub fn is_toplevel(view: &Value) -> bool {
    str_or(view, "role", "toplevel") == Some("toplevel")
}

/// view.get("parent", -1) in (-1, None): not a dialog of another window.
pub fn no_parent(view: &Value) -> bool {
    match view.get("parent") {
        None | Some(Value::Null) => true,
        Some(p) => p.as_f64() == Some(-1.0),
    }
}
