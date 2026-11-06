//! greetd IPC client (sonata2/greetd.py): the login manager Sonata's
//! login screen runs under.
//!
//! Protocol (greetd-ipc(7)): over the unix socket in $GREETD_SOCK, each
//! message is a 32-bit native-endian length followed by that much JSON.
//!
//! ```text
//! create_session {username}           -> auth_message | success | error
//! post_auth_message_response {resp}   -> auth_message | success | error
//! start_session {cmd, env}            -> success | error
//! cancel_session                      -> success
//! ```
//!
//! Calls block (PAM may take ~2 s on a wrong password): run them off the
//! GTK thread. `Fake` answers like greetd for previews and tests (password
//! "sonata").

use serde_json::{json, Value};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::Path;

#[derive(Clone, Debug, PartialEq)]
pub struct Error {
    /// "auth_error" (wrong password) or "error"
    pub kind: String,
    pub description: String,
}

impl Error {
    pub fn new(kind: &str, description: &str) -> Self {
        Self { kind: kind.into(), description: description.into() }
    }

    pub fn is_auth(&self) -> bool {
        self.kind == "auth_error"
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.description)
    }
}

impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Error::new("error", &e.to_string())
    }
}

pub type Reply = Result<Value, Error>;

/// greetd, or a stand-in for it.
pub trait Greetd: Send {
    fn create_session(&mut self, username: &str) -> Reply;
    /// None: an info or error prompt acknowledged
    fn answer(&mut self, response: Option<&str>) -> Reply;
    fn start_session(&mut self, cmd: &[String], env: &[String]) -> Reply;
    fn cancel(&mut self);
    fn close(&mut self) {}
}

pub struct Client {
    sock: Option<UnixStream>,
}

/// Zeros over a buffer that held the password.
fn wipe(buf: &mut [u8]) {
    for b in buf.iter_mut() {
        // (volatile: never optimised away as a dead store)
        unsafe { std::ptr::write_volatile(b, 0) };
    }
}

impl Client {
    /// `path` None: $GREETD_SOCK.
    pub fn connect(path: Option<&Path>) -> Result<Self, Error> {
        let env = std::env::var_os("GREETD_SOCK").filter(|v| !v.is_empty());
        let Some(path) = path.map(|p| p.as_os_str().to_owned()).or(env) else {
            // not under greetd: an error the login screen shows
            return Err(Error::new("error", "Not running under greetd (GREETD_SOCK is not set)"));
        };
        Ok(Self { sock: Some(UnixStream::connect(path)?) })
    }

    fn call(&mut self, msg: &Value) -> Reply {
        let Some(sock) = self.sock.as_mut() else { return Err(Error::new("error", "greetd closed the connection")) };
        let mut data = serde_json::to_vec(msg).map_err(|e| Error::new("error", &e.to_string()))?;
        let mut out = (data.len() as u32).to_ne_bytes().to_vec();
        out.extend_from_slice(&data);
        let sent = sock.write_all(&out);
        wipe(&mut data);
        wipe(&mut out);
        sent?;
        let mut head = [0u8; 4];
        read_exact(sock, &mut head)?;
        let mut body = vec![0u8; u32::from_ne_bytes(head) as usize];
        read_exact(sock, &mut body)?;
        let reply: Value = serde_json::from_slice(&body).map_err(|e| Error::new("error", &e.to_string()))?;
        if reply.get("type").and_then(Value::as_str) == Some("error") {
            let s = |k: &str, d: &str| reply.get(k).and_then(Value::as_str).unwrap_or(d).to_string();
            return Err(Error { kind: s("error_type", "error"), description: s("description", "") });
        }
        Ok(reply)
    }
}

/// greetd going away mid-call is an error the screen shows, never an endless read.
fn read_exact(sock: &mut UnixStream, buf: &mut [u8]) -> Result<(), Error> {
    sock.read_exact(buf).map_err(|e| match e.kind() {
        std::io::ErrorKind::UnexpectedEof => Error::new("error", "greetd closed the connection"),
        _ => e.into(),
    })
}

impl Greetd for Client {
    fn create_session(&mut self, username: &str) -> Reply {
        self.call(&json!({"type": "create_session", "username": username}))
    }

    fn answer(&mut self, response: Option<&str>) -> Reply {
        let mut msg = json!({"type": "post_auth_message_response", "response": response});
        let r = self.call(&msg);
        if let Some(Value::String(s)) = msg.get_mut("response") {
            // SAFETY: zeros are valid UTF-8
            wipe(unsafe { s.as_bytes_mut() });
        }
        r
    }

    fn start_session(&mut self, cmd: &[String], env: &[String]) -> Reply {
        self.call(&json!({"type": "start_session", "cmd": cmd, "env": env}))
    }

    fn cancel(&mut self) {
        let _ = self.call(&json!({"type": "cancel_session"}));
    }

    fn close(&mut self) {
        self.sock.take();
    }
}

/// greetd stand-in: any user, password "sonata".
#[derive(Default)]
pub struct Fake {
    pub started: Option<(Vec<String>, Vec<String>)>,
}

impl Greetd for Fake {
    fn create_session(&mut self, _username: &str) -> Reply {
        Ok(json!({"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password: "}))
    }

    fn answer(&mut self, response: Option<&str>) -> Reply {
        if response != Some("sonata") {
            std::thread::sleep(std::time::Duration::from_secs(1));
            return Err(Error::new("auth_error", "Authentication failed"));
        }
        Ok(json!({"type": "success"}))
    }

    fn start_session(&mut self, cmd: &[String], env: &[String]) -> Reply {
        self.started = Some((cmd.to_vec(), env.to_vec()));
        Ok(json!({"type": "success"}))
    }

    fn cancel(&mut self) {}
}

/// Authenticate `username`. Prompts other than the password (an info
/// message) are acknowledged; a second secret prompt (2FA) isn't
/// supported yet and fails.
pub fn login(client: &mut dyn Greetd, username: &str, password: &str) -> Result<(), Error> {
    // a wrong password leaves greetd's session half-made: always start clean,
    // and cancel on any failure (else the next try answers the old session)
    client.cancel();
    let r = login_steps(client, username, password);
    if r.is_err() {
        client.cancel();
    }
    r
}

fn login_steps(client: &mut dyn Greetd, username: &str, password: &str) -> Result<(), Error> {
    let mut reply = client.create_session(username)?;
    let mut answered = false;
    while reply.get("type").and_then(Value::as_str) == Some("auth_message") {
        match reply.get("auth_message_type").and_then(Value::as_str) {
            Some("secret") if !answered => {
                reply = client.answer(Some(password))?;
                answered = true;
            }
            Some("info") | Some("error") => reply = client.answer(None)?,
            // asked again: the password was wrong
            Some("secret") => return Err(Error::new("auth_error", "Authentication failed")),
            _ => {
                client.cancel();
                let msg = reply.get("auth_message").and_then(Value::as_str).unwrap_or("Unsupported login step");
                return Err(Error::new("error", msg));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::sync::{Arc, Mutex};

    /// A unix-socket server speaking greetd's framing, one scripted reply per request.
    struct FakeServer {
        _dir: tempfile::TempDir,
        path: std::path::PathBuf,
        got: Arc<Mutex<Vec<Value>>>,
        thread: Option<std::thread::JoinHandle<()>>,
    }

    impl FakeServer {
        fn new(replies: Vec<Value>, close_after: Option<usize>) -> Self {
            let dir = tempfile::tempdir().unwrap();
            let path = dir.path().join("greetd.sock");
            let listener = UnixListener::bind(&path).unwrap();
            let got: Arc<Mutex<Vec<Value>>> = Arc::default();
            let g = got.clone();
            let thread = std::thread::spawn(move || {
                let (mut conn, _) = listener.accept().unwrap();
                let mut replies = replies.into_iter();
                loop {
                    let mut head = [0u8; 4];
                    if conn.read_exact(&mut head).is_err() {
                        return;
                    }
                    let mut body = vec![0u8; u32::from_ne_bytes(head) as usize];
                    conn.read_exact(&mut body).unwrap();
                    g.lock().unwrap().push(serde_json::from_slice(&body).unwrap());
                    if close_after.is_some_and(|n| g.lock().unwrap().len() >= n) {
                        return;
                    }
                    let data = serde_json::to_vec(&replies.next().unwrap()).unwrap();
                    conn.write_all(&(data.len() as u32).to_ne_bytes()).unwrap();
                    conn.write_all(&data).unwrap();
                }
            });
            Self { _dir: dir, path, got, thread: Some(thread) }
        }

        fn got(&mut self) -> Vec<Value> {
            if let Some(t) = self.thread.take() {
                t.join().unwrap();
            }
            self.got.lock().unwrap().clone()
        }
    }

    /// greetd client stand-in recording calls (no socket).
    struct Scripted {
        replies: Vec<Reply>,
        calls: Vec<String>,
    }

    impl Scripted {
        fn new(replies: Vec<Reply>) -> Self {
            Self { replies: replies.into_iter().rev().collect(), calls: Vec::new() }
        }

        fn next(&mut self) -> Reply {
            self.replies.pop().expect("a scripted reply")
        }
    }

    impl Greetd for Scripted {
        fn create_session(&mut self, username: &str) -> Reply {
            self.calls.push(format!("create {username}"));
            self.next()
        }
        fn answer(&mut self, response: Option<&str>) -> Reply {
            self.calls.push(format!("answer {response:?}"));
            self.next()
        }
        fn start_session(&mut self, _cmd: &[String], _env: &[String]) -> Reply {
            self.next()
        }
        fn cancel(&mut self) {
            self.calls.push("cancel".into());
        }
    }

    fn secret() -> Reply {
        Ok(json!({"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"}))
    }

    #[test]
    fn client_frames_json_with_native_length() {
        let mut srv = FakeServer::new(vec![json!({"type": "auth_message", "auth_message_type": "secret", "auth_message": "Password:"})], None);
        let mut c = Client::connect(Some(&srv.path)).unwrap();
        let reply = c.create_session("vini").unwrap();
        c.close();
        assert_eq!(reply["auth_message_type"], "secret");
        assert_eq!(srv.got(), [json!({"type": "create_session", "username": "vini"})]);
    }

    #[test]
    fn error_reply_raises_with_its_type() {
        let mut srv = FakeServer::new(vec![json!({"type": "error", "error_type": "auth_error", "description": "nope"})], None);
        let mut c = Client::connect(Some(&srv.path)).unwrap();
        let err = c.answer(Some("bad")).unwrap_err();
        c.close();
        assert_eq!((err.kind.as_str(), err.to_string().as_str()), ("auth_error", "nope"));
        assert_eq!(srv.got(), [json!({"type": "post_auth_message_response", "response": "bad"})]);
    }

    #[test]
    fn acknowledging_a_prompt_sends_null() {
        let mut srv = FakeServer::new(vec![json!({"type": "success"})], None);
        let mut c = Client::connect(Some(&srv.path)).unwrap();
        c.answer(None).unwrap();
        c.close();
        assert_eq!(srv.got(), [json!({"type": "post_auth_message_response", "response": null})]);
    }

    #[test]
    fn closed_connection_is_a_greetd_error() {
        let srv = FakeServer::new(vec![], Some(1));
        let mut c = Client::connect(Some(&srv.path)).unwrap();
        let err = c.start_session(&["sonata".into()], &[]).unwrap_err();
        assert_eq!(err.kind, "error");
    }

    #[test]
    fn a_whole_login_over_the_socket() {
        let mut srv = FakeServer::new(vec![json!({"type": "success"}), secret().unwrap(), json!({"type": "success"}), json!({"type": "success"})], None);
        let mut c = Client::connect(Some(&srv.path)).unwrap();
        login(&mut c, "vini", "pw").unwrap();
        c.start_session(&["sonata-session".into()], &["XDG_SESSION_TYPE=wayland".into()]).unwrap();
        c.close();
        let got = srv.got();
        let types: Vec<&str> = got.iter().map(|m| m["type"].as_str().unwrap()).collect();
        assert_eq!(types, ["cancel_session", "create_session", "post_auth_message_response", "start_session"]);
        assert_eq!(got[2]["response"], "pw");
        assert_eq!(got[3]["cmd"], json!(["sonata-session"]));
    }

    #[test]
    fn login_acknowledges_info_then_answers_the_password() {
        let mut c = Scripted::new(vec![Ok(json!({"type": "auth_message", "auth_message_type": "info", "auth_message": "Hi"})), secret(), Ok(json!({"type": "success"}))]);
        login(&mut c, "vini", "pw").unwrap();
        assert_eq!(c.calls, ["cancel", "create vini", "answer None", "answer Some(\"pw\")"]);
    }

    #[test]
    fn wrong_password_cancels_the_half_made_session() {
        let mut c = Scripted::new(vec![secret(), Err(Error::new("auth_error", "Authentication failed"))]);
        let err = login(&mut c, "vini", "bad").unwrap_err();
        assert!(err.is_auth());
        assert_eq!(c.calls.last().unwrap(), "cancel");
    }

    #[test]
    fn second_secret_prompt_is_a_wrong_password() {
        let mut c = Scripted::new(vec![secret(), secret()]);
        assert_eq!(login(&mut c, "vini", "pw").unwrap_err().kind, "auth_error");
    }

    #[test]
    fn unsupported_prompt_fails_cleanly() {
        let mut c = Scripted::new(vec![Ok(json!({"type": "auth_message", "auth_message_type": "visible", "auth_message": "OTP:"}))]);
        let err = login(&mut c, "vini", "pw").unwrap_err();
        assert_eq!((err.kind.as_str(), err.description.as_str()), ("error", "OTP:"));
        assert!(c.calls[1..].contains(&"cancel".to_string()));
    }

    #[test]
    fn fake_accepts_only_sonata() {
        let mut f = Fake::default();
        login(&mut f, "x", "sonata").unwrap();
        assert!(login(&mut f, "x", "wrong").unwrap_err().is_auth());
    }

    #[test]
    fn missing_socket_variable_is_an_error_the_greeter_handles() {
        // (GREETD_SOCK is unset by the test command; never the real greetd)
        if std::env::var_os("GREETD_SOCK").is_none() {
            assert_eq!(Client::connect(None).err().unwrap().kind, "error");
        }
    }
}
