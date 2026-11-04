//! The Anthropic Messages API, streamed (assistant/api.py). Python used
//! urllib in a worker thread; here libsoup reads the stream on the main
//! loop (the system's TLS, no thread, nothing bundled).
//!
//! ```text
//! let job = api::stream(key, model, messages, system, tools, on_text, on_done, on_error);
//! job.cancel();                     // the Stop button
//! ```
//!
//! on_text gets each piece of text as it arrives (batched, ~30 a second),
//! on_done(blocks, stop_reason, usage) once at the end (the answer's
//! content blocks: text and tool_use with their parsed input; usage: the
//! model that answered and its token counts), on_error with a readable
//! message instead. No callback runs after cancel().
//!
//! The model: the configured one, else the newest the account offers
//! (GET /v1/models, cached for the process).

use gio::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use soup::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap};
use std::rc::Rc;

pub const VERSION: &str = "2023-06-01";
pub const MAX_TOKENS: i64 = 8192;
/// s without a byte (the stream sends pings meanwhile)
const TIMEOUT: u32 = 60;
pub const DEFAULT_WINDOW: i64 = 200_000;
/// text handed to the UI at most this often (one relayout per batch)
const FLUSH: std::time::Duration = std::time::Duration::from_millis(33);

thread_local! {
    static API: RefCell<String> = RefCell::new("https://api.anthropic.com/v1".into());
    static SESSION: soup::Session = {
        let s = soup::Session::new();
        s.set_timeout(TIMEOUT);
        s.set_user_agent("Sonata-Assistant");
        s
    };
    /// (key, [(id, display name)] newest first)
    static MODELS: RefCell<Option<(String, Vec<(String, String)>)>> = const { RefCell::new(None) };
    /// model id -> context window (max_input_tokens)
    static WINDOWS: RefCell<HashMap<String, i64>> = RefCell::new(HashMap::new());
}

/// Where requests go (tests point it at a local server).
pub fn set_api(url: &str) {
    API.with(|a| *a.borrow_mut() = url.trim_end_matches('/').to_string());
    MODELS.with(|m| m.borrow_mut().take());
}

fn request(method: &str, path: &str, key: &str, body: Option<&Value>) -> Result<soup::Message, String> {
    let url = API.with(|a| format!("{}{path}", a.borrow()));
    let msg = soup::Message::new(method, &url).map_err(|e| e.to_string())?;
    if let Some(h) = msg.request_headers() {
        h.append("x-api-key", key);
        h.append("anthropic-version", VERSION);
    }
    if let Some(b) = body {
        let bytes = glib::Bytes::from_owned(serde_json::to_vec(b).unwrap_or_default());
        msg.set_request_body_from_bytes(Some("application/json"), Some(&bytes));
    }
    Ok(msg)
}

/// A short message for the chat from an HTTP status and its body.
pub fn http_error_text(code: u32, body: &[u8]) -> String {
    let msg = serde_json::from_slice::<Value>(body).ok().and_then(|v| v.pointer("/error/message").and_then(Value::as_str).map(str::to_string)).unwrap_or_default();
    match code {
        401 => tr!("The API key was not accepted."),
        429 if msg.is_empty() => tr!("Too many requests. Try again in a moment."),
        500 | 529 if msg.is_empty() => tr!("The service is busy. Try again in a moment."),
        _ if msg.is_empty() => sonata_i18n::fmt(&tr!("The request failed (HTTP {code})."), &[("code", &code.to_string())]),
        _ => msg,
    }
}

fn network_error_text() -> String {
    tr!("Could not reach the service. Check your connection.")
}

/// [(id, display name)] the key can use, newest first (cached per key).
pub async fn models(key: &str) -> Result<Vec<(String, String)>, String> {
    if let Some(found) = MODELS.with(|m| m.borrow().as_ref().filter(|(k, _)| k == key).map(|(_, l)| l.clone())) {
        return Ok(found);
    }
    let msg = request("GET", "/models?limit=100", key, None)?;
    let session = SESSION.with(Clone::clone);
    let body = session.send_and_read_future(&msg, glib::Priority::DEFAULT).await.map_err(|_| network_error_text())?;
    if msg.status_code() != 200 {
        return Err(http_error_text(msg.status_code(), &body));
    }
    let data: Value = serde_json::from_slice(&body).map_err(|e| e.to_string())?;
    let found: Vec<&Value> = data.get("data").and_then(Value::as_array).map(|a| a.iter().filter(|m| m.get("id").and_then(Value::as_str).is_some_and(|s| !s.is_empty())).collect()).unwrap_or_default();
    WINDOWS.with(|w| {
        for m in &found {
            if let Some(n) = m.get("max_input_tokens").and_then(Value::as_i64).filter(|n| *n > 0) {
                w.borrow_mut().insert(m["id"].as_str().unwrap().to_string(), n);
            }
        }
    });
    let list: Vec<(String, String)> = found
        .iter()
        .map(|m| {
            let id = m["id"].as_str().unwrap().to_string();
            let name = m.get("display_name").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string).unwrap_or_else(|| id.clone());
            (id, name)
        })
        .collect();
    MODELS.with(|m| *m.borrow_mut() = Some((key.to_string(), list.clone())));
    Ok(list)
}

/// models() without waiting: callback(list, or None on error) on the main loop.
pub fn models_async(key: &str, callback: impl FnOnce(Option<Vec<(String, String)>>) + 'static) {
    let key = key.to_string();
    glib::MainContext::ref_thread_default().spawn_local(async move { callback(models(&key).await.ok()) });
}

/// The model's context window in tokens (from the models list; a usual
/// size until it was read).
pub fn context_window(model: &str) -> i64 {
    WINDOWS.with(|w| w.borrow().get(model).copied().unwrap_or(DEFAULT_WINDOW))
}

/// Server-sent events from a byte stream: (event, data) per event.
#[derive(Default)]
pub struct Sse {
    pending: Vec<u8>,
    event: Option<String>,
    data: Vec<String>,
}

impl Sse {
    pub fn feed(&mut self, bytes: &[u8]) -> Vec<(Option<String>, Value)> {
        self.pending.extend_from_slice(bytes);
        let mut out = Vec::new();
        while let Some(n) = self.pending.iter().position(|b| *b == b'\n') {
            let raw: Vec<u8> = self.pending.drain(..=n).collect();
            let line = String::from_utf8_lossy(&raw);
            self.line(line.trim_end_matches(['\r', '\n']), &mut out);
        }
        out
    }

    /// The end of the stream (a last event without its blank line).
    pub fn finish(&mut self) -> Vec<(Option<String>, Value)> {
        let mut out = Vec::new();
        if !self.pending.is_empty() {
            let raw = std::mem::take(&mut self.pending);
            let line = String::from_utf8_lossy(&raw).into_owned();
            self.line(line.trim_end_matches(['\r', '\n']), &mut out);
        }
        self.line("", &mut out);
        out
    }

    fn line(&mut self, line: &str, out: &mut Vec<(Option<String>, Value)>) {
        if line.is_empty() {
            if !self.data.is_empty() {
                if let Ok(v) = serde_json::from_str(&self.data.join("\n")) {
                    out.push((self.event.clone(), v));
                }
            }
            self.event = None;
            self.data.clear();
        } else if let Some(e) = line.strip_prefix("event:") {
            self.event = Some(e.trim().to_string());
        } else if let Some(d) = line.strip_prefix("data:") {
            self.data.push(d.trim_start().to_string());
        }
    }
}

fn blocks_of(content: &Value) -> Vec<Value> {
    match content {
        Value::String(s) => vec![json!({"type": "text", "text": s})],
        Value::Array(a) => a.clone(),
        _ => Vec::new(),
    }
}

/// Tokens of one call: everything read (cached or not) and written --
/// also the conversation's size in the context window after it.
pub fn total_tokens(usage: &Map<String, Value>) -> i64 {
    ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"].iter().map(|k| usage.get(*k).and_then(Value::as_i64).unwrap_or(0)).sum()
}

/// The API wants user/assistant turns alternating: a message sent after a
/// failed answer joins the one before it. Content is a string or a list of
/// blocks (text, tool_use, tool_result).
pub fn merge_turns(messages: &[Value]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for m in messages {
        let role = m.get("role").cloned().unwrap_or(Value::Null);
        let content = m.get("content").cloned().unwrap_or(json!(""));
        if let Some(prev) = out.last_mut().filter(|p| p["role"] == role) {
            let merged = match (&prev["content"], &content) {
                (Value::String(a), Value::String(b)) => json!(format!("{a}\n\n{b}")),
                (a, b) => Value::Array([blocks_of(a), blocks_of(b)].concat()),
            };
            prev["content"] = merged;
        } else {
            out.push(json!({"role": role, "content": content}));
        }
    }
    out
}

/// The text of a message (its text blocks).
pub fn text_of(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(a) => a.iter().filter(|b| b.get("type").and_then(Value::as_str) == Some("text")).map(|b| b.get("text").and_then(Value::as_str).unwrap_or("")).collect::<Vec<_>>().join("\n\n"),
        _ => String::new(),
    }
}

/// An answer put together from its stream events.
#[derive(Default)]
pub struct Answer {
    blocks: BTreeMap<i64, Map<String, Value>>,
    /// index -> streamed JSON of a tool's input
    partial: HashMap<i64, String>,
    pub stop: Option<String>,
    pub usage: Map<String, Value>,
}

pub enum Step {
    Text(String),
    End,
    More,
}

impl Answer {
    pub fn new(model: &str) -> Self {
        let mut usage = Map::new();
        usage.insert("model".into(), json!(model));
        Self { usage, ..Default::default() }
    }

    fn take_usage(&mut self, u: Option<&Value>) {
        for (k, v) in u.and_then(Value::as_object).into_iter().flatten() {
            if v.is_i64() || v.is_u64() {
                self.usage.insert(k.clone(), v.clone());
            }
        }
    }

    pub fn event(&mut self, event: Option<&str>, data: &Value) -> Result<Step, String> {
        let kind = data.get("type").and_then(Value::as_str).unwrap_or("");
        if event == Some("error") || kind == "error" {
            return Err(data.pointer("/error/message").and_then(Value::as_str).filter(|s| !s.is_empty()).unwrap_or("The answer stopped with an error.").to_string());
        }
        match kind {
            "message_start" => {
                let msg = data.get("message").cloned().unwrap_or(json!({}));
                if let Some(m) = msg.get("model").and_then(Value::as_str).filter(|s| !s.is_empty()) {
                    self.usage.insert("model".into(), json!(m));
                }
                self.take_usage(msg.get("usage"));
            }
            "content_block_start" => {
                let blk = data.get("content_block").and_then(Value::as_object).cloned().unwrap_or_default();
                if matches!(blk.get("type").and_then(Value::as_str), Some("text" | "tool_use")) {
                    let i = data.get("index").and_then(Value::as_i64).unwrap_or(self.blocks.len() as i64);
                    self.blocks.insert(i, blk);
                }
            }
            "content_block_delta" => {
                let i = data.get("index").and_then(Value::as_i64).unwrap_or(0);
                let d = data.get("delta").cloned().unwrap_or(json!({}));
                match d.get("type").and_then(Value::as_str) {
                    Some("text_delta") => {
                        let t = d.get("text").and_then(Value::as_str).unwrap_or("").to_string();
                        let blk = self.blocks.entry(i).or_insert_with(|| json!({"type": "text", "text": ""}).as_object().unwrap().clone());
                        let old = blk.get("text").and_then(Value::as_str).unwrap_or("").to_string();
                        blk.insert("text".into(), json!(old + &t));
                        return Ok(Step::Text(t));
                    }
                    Some("input_json_delta") => self.partial.entry(i).or_default().push_str(d.get("partial_json").and_then(Value::as_str).unwrap_or("")),
                    _ => {}
                }
            }
            "message_delta" => {
                if let Some(s) = data.pointer("/delta/stop_reason").and_then(Value::as_str) {
                    self.stop = Some(s.to_string());
                }
                self.take_usage(data.get("usage"));
            }
            "message_stop" => return Ok(Step::End),
            _ => {}
        }
        Ok(Step::More)
    }

    /// The content blocks: text and tool_use (with its input parsed).
    pub fn finish(mut self) -> (Vec<Value>, Option<String>, Map<String, Value>) {
        let mut out = Vec::new();
        for (i, blk) in std::mem::take(&mut self.blocks) {
            if blk.get("type").and_then(Value::as_str) == Some("tool_use") {
                let input = match self.partial.get(&i).filter(|p| !p.is_empty()) {
                    Some(p) => serde_json::from_str(p).unwrap_or(json!({})),
                    None => blk.get("input").cloned().unwrap_or(json!({})),
                };
                let mut b = Map::new();
                for k in ["type", "id", "name"] {
                    if let Some(v) = blk.get(k) {
                        b.insert(k.into(), v.clone());
                    }
                }
                b.insert("input".into(), input);
                out.push(Value::Object(b));
            } else if let Some(t) = blk.get("text").and_then(Value::as_str).filter(|t| !t.is_empty()) {
                out.push(json!({"type": "text", "text": t}));
            }
        }
        (out, self.stop, self.usage)
    }
}

/// One streamed answer. cancel() stops it; no callback runs after.
pub struct Job {
    handle: RefCell<Option<glib::JoinHandle<()>>>,
    cancelled: Rc<Cell<bool>>,
}

impl Job {
    pub fn cancel(&self) {
        self.cancelled.set(true);
        if let Some(h) = self.handle.take() {
            h.abort(); // drops the future: libsoup's request is cancelled
        }
    }
}

pub struct Request {
    pub key: String,
    pub model: String,
    pub messages: Vec<Value>,
    pub system: String,
    pub tools: Option<Value>,
}

type Done = Box<dyn FnOnce(Vec<Value>, Option<String>, Map<String, Value>)>;

pub fn stream(req: Request, on_text: impl Fn(&str) + 'static, on_done: impl FnOnce(Vec<Value>, Option<String>, Map<String, Value>) + 'static, on_error: impl FnOnce(String) + 'static) -> Rc<Job> {
    let cancelled = Rc::new(Cell::new(false));
    let job = Rc::new(Job { handle: RefCell::new(None), cancelled: cancelled.clone() });
    let on_done: Done = Box::new(on_done);
    let handle = glib::MainContext::ref_thread_default().spawn_local(async move {
        let res = run(req, &on_text, &cancelled).await;
        if cancelled.get() {
            return;
        }
        match res {
            Ok((blocks, stop, usage)) => on_done(blocks, stop, usage),
            Err(e) => on_error(e),
        }
    });
    job.handle.replace(Some(handle));
    job
}

async fn run(req: Request, on_text: &dyn Fn(&str), cancelled: &Cell<bool>) -> Result<(Vec<Value>, Option<String>, Map<String, Value>), String> {
    let mut model = req.model.clone();
    if model.is_empty() {
        model = models(&req.key).await?.first().map(|m| m.0.clone()).ok_or("No model is available for this API key.")?;
    }
    let mut body = json!({"model": model, "max_tokens": MAX_TOKENS, "stream": true, "messages": merge_turns(&req.messages)});
    if !req.system.is_empty() {
        body["system"] = json!(req.system);
    }
    if let Some(t) = req.tools.as_ref().filter(|t| t.as_array().is_some_and(|a| !a.is_empty())) {
        body["tools"] = t.clone();
    }
    let msg = request("POST", "/messages", &req.key, Some(&body))?;
    let session = SESSION.with(Clone::clone);
    let input = session.send_future(&msg, glib::Priority::DEFAULT).await.map_err(|_| network_error_text())?;
    let read = |input: gio::InputStream| async move { input.read_bytes_future(8192, glib::Priority::DEFAULT).await };
    if msg.status_code() != 200 {
        let mut all = Vec::new();
        while let Ok(b) = read(input.clone()).await {
            if b.is_empty() {
                break;
            }
            all.extend_from_slice(&b);
        }
        return Err(http_error_text(msg.status_code(), &all));
    }
    let mut sse = Sse::default();
    let mut answer = Answer::new(&model);
    let mut buf = String::new();
    let mut last = std::time::Instant::now() - FLUSH;
    'read: loop {
        let bytes = read(input.clone()).await.map_err(|_| network_error_text())?;
        let events = if bytes.is_empty() { sse.finish() } else { sse.feed(&bytes) };
        for (event, data) in events {
            if cancelled.get() {
                return Err(String::new());
            }
            match answer.event(event.as_deref(), &data)? {
                Step::Text(t) => {
                    buf.push_str(&t);
                    if last.elapsed() >= FLUSH {
                        on_text(&std::mem::take(&mut buf));
                        last = std::time::Instant::now();
                    }
                }
                Step::End => break 'read,
                Step::More => {}
            }
        }
        if bytes.is_empty() {
            break;
        }
    }
    let _ = input.close_future(glib::Priority::DEFAULT).await;
    if !buf.is_empty() {
        on_text(&buf);
    }
    Ok(answer.finish())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Read, Write};

    fn ev(kind: &str, data: Value) -> String {
        format!("event: {kind}\ndata: {data}\n\n")
    }

    /// A one-request HTTP server on 127.0.0.1: (base url, the request body it got).
    fn serve(status: &str, body: String) -> (String, std::sync::mpsc::Receiver<String>) {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/v1", l.local_addr().unwrap());
        let (tx, rx) = std::sync::mpsc::channel();
        let status = status.to_string();
        std::thread::spawn(move || {
            let (s, _) = l.accept().unwrap();
            let mut r = BufReader::new(s.try_clone().unwrap());
            let mut len = 0;
            loop {
                let mut line = String::new();
                r.read_line(&mut line).unwrap();
                if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                    len = v.trim().parse().unwrap();
                }
                if line == "\r\n" {
                    break;
                }
            }
            let mut got = vec![0; len];
            r.read_exact(&mut got).unwrap();
            let _ = tx.send(String::from_utf8(got).unwrap()); // some tests never look
            let mut s = s;
            write!(s, "HTTP/1.1 {status}\r\ncontent-type: text/event-stream\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}", body.len()).unwrap();
        });
        (url, rx)
    }

    fn wait<T>(cell: &Rc<RefCell<Option<T>>>) {
        let ctx = glib::MainContext::ref_thread_default();
        let start = std::time::Instant::now();
        while cell.borrow().is_none() && start.elapsed().as_secs() < 10 {
            ctx.iteration(true);
        }
    }

    type Outcome = Rc<RefCell<Option<Result<(Vec<Value>, Option<String>, Map<String, Value>), String>>>>;

    /// Each test on its own thread with its own main loop context.
    fn ask(url: &str, model: &str) -> (Outcome, Rc<RefCell<String>>) {
        glib::MainContext::new().with_thread_default(|| ask_here(url, model)).unwrap()
    }

    fn ask_here(url: &str, model: &str) -> (Outcome, Rc<RefCell<String>>) {
        set_api(url);
        let out: Outcome = Rc::default();
        let text = Rc::new(RefCell::new(String::new()));
        let (o1, o2, t) = (out.clone(), out.clone(), text.clone());
        let req = Request { key: "k".into(), model: model.into(), messages: vec![json!({"role": "user", "content": "hi"})], system: String::new(), tools: None };
        let _job = stream(req, move |s| t.borrow_mut().push_str(s), move |b, s, u| *o1.borrow_mut() = Some(Ok((b, s, u))), move |e| *o2.borrow_mut() = Some(Err(e)));
        wait(&out);
        (out, text)
    }

    #[test]
    fn streams_text_and_tool_use() {
        sonata_i18n::use_language("en");
        let body = [
            ev("message_start", json!({"type": "message_start", "message": {"model": "m-1", "usage": {"input_tokens": 10, "output_tokens": 1}}})),
            ev("content_block_start", json!({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})),
            ev("content_block_delta", json!({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}})),
            ev("content_block_delta", json!({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}})),
            ev("content_block_start", json!({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}})),
            ev("content_block_delta", json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"path\": "}})),
            ev("content_block_delta", json!({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"/x\"}"}})),
            ev("message_delta", json!({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}})),
            ev("message_stop", json!({"type": "message_stop"})),
        ]
        .concat();
        let (url, got) = serve("200 OK", body);
        let (out, text) = ask(&url, "m-1");
        let (blocks, stop, usage) = out.borrow_mut().take().unwrap().unwrap();
        assert_eq!(*text.borrow(), "Hello");
        assert_eq!(blocks, vec![json!({"type": "text", "text": "Hello"}), json!({"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "/x"}})]);
        assert_eq!(stop.as_deref(), Some("tool_use"));
        assert_eq!(usage["model"], "m-1");
        assert_eq!(total_tokens(&usage), 17);
        let sent: Value = serde_json::from_str(&got.recv().unwrap()).unwrap();
        assert_eq!(sent["stream"], true);
        assert_eq!(sent["messages"], json!([{"role": "user", "content": "hi"}]));
        assert!(sent.get("tools").is_none());
    }

    #[test]
    fn http_error_is_readable() {
        sonata_i18n::use_language("en");
        let (url, _) = serve("401 Unauthorized", "{}".into());
        assert_eq!(ask(&url, "m").0.borrow_mut().take().unwrap().unwrap_err(), "The API key was not accepted.");
        let (url, _) = serve("400 Bad Request", json!({"error": {"message": "bad thing"}}).to_string());
        assert_eq!(ask(&url, "m").0.borrow_mut().take().unwrap().unwrap_err(), "bad thing");
        assert_eq!(http_error_text(503, b""), "The request failed (HTTP 503).");
        assert_eq!(http_error_text(529, b""), "The service is busy. Try again in a moment.");
    }

    #[test]
    fn stream_error_event() {
        let body = ev("error", json!({"type": "error", "error": {"message": "Overloaded"}}));
        let (url, _) = serve("200 OK", body);
        assert_eq!(ask(&url, "m").0.borrow_mut().take().unwrap().unwrap_err(), "Overloaded");
    }

    #[test]
    fn unreachable_service() {
        sonata_i18n::use_language("en");
        let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port(); // closed again
        assert_eq!(ask(&format!("http://127.0.0.1:{port}/v1"), "m").0.borrow_mut().take().unwrap().unwrap_err(), "Could not reach the service. Check your connection.");
    }

    #[test]
    fn consecutive_same_role_merged() {
        let m = merge_turns(&[json!({"role": "user", "content": "a"}), json!({"role": "user", "content": "b"}), json!({"role": "assistant", "content": [{"type": "text", "text": "c"}]}), json!({"role": "assistant", "content": "d"})]);
        assert_eq!(m, vec![json!({"role": "user", "content": "a\n\nb"}), json!({"role": "assistant", "content": [{"type": "text", "text": "c"}, {"type": "text", "text": "d"}]})]);
        assert_eq!(text_of(&m[1]["content"]), "c\n\nd");
    }

    #[test]
    fn sse_parser() {
        let mut p = Sse::default();
        let mut got = p.feed(b"event: a\ndata: {\"x\":\r\ndata: 1}\r\n\r\n: comment\ndata: broken\n\nevent: b\nda");
        got.extend(p.feed(b"ta: {\"y\": 2}"));
        got.extend(p.finish());
        assert_eq!(got, vec![(Some("a".into()), json!({"x": 1})), (Some("b".into()), json!({"y": 2}))]);
    }
}
