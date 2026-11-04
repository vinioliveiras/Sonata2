//! Wayfire IPC (sonata2/wl/wfipc.py; plugins `ipc` + `ipc-rules`): the
//! socket in $WAYFIRE_SOCKET, messages framed as a 4-byte little-endian
//! length + JSON. One request per connection (short, blocking).

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::Duration;

pub fn socket() -> Option<String> {
    std::env::var("WAYFIRE_SOCKET").ok().filter(|p| !p.is_empty() && std::path::Path::new(p).exists())
}

pub fn available() -> bool {
    socket().is_some()
}

pub fn frame(method: &str, data: &Value) -> Vec<u8> {
    let body = json!({"method": method, "data": data}).to_string().into_bytes();
    let mut out = (body.len() as u32).to_le_bytes().to_vec();
    out.extend(body);
    out
}

pub fn read_message(r: &mut impl Read) -> Option<Value> {
    let mut len = [0u8; 4];
    r.read_exact(&mut len).ok()?;
    let mut body = vec![0u8; u32::from_le_bytes(len) as usize];
    r.read_exact(&mut body).ok()?;
    serde_json::from_slice(&body).ok()
}

/// One request; None without Wayfire or on any error.
pub fn call(method: &str, data: Value) -> Option<Value> {
    let path = socket()?;
    let mut s = UnixStream::connect(path).ok()?;
    s.set_read_timeout(Some(Duration::from_secs(2))).ok()?;
    s.set_write_timeout(Some(Duration::from_secs(2))).ok()?;
    s.write_all(&frame(method, &data)).ok()?;
    read_message(&mut s)
}

/// The output with the focus ("eDP-1"...).
pub fn focused_output() -> Option<String> {
    call("window-rules/get-focused-output", json!({}))?.get("info")?.get("name")?.as_str().map(String::from)
}

#[cfg(test)]
mod tests {
    #[test]
    fn frames_round_trip() {
        let f = super::frame("window-rules/list-views", &serde_json::json!({}));
        assert_eq!(u32::from_le_bytes([f[0], f[1], f[2], f[3]]) as usize, f.len() - 4);
        let mut r = std::io::Cursor::new(f);
        assert_eq!(super::read_message(&mut r).unwrap()["method"], "window-rules/list-views");
        std::env::remove_var("WAYFIRE_SOCKET");
        assert!(super::call("x", serde_json::json!({})).is_none());
    }
}
