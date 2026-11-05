//! Assistant conversations (assistant/store.py), saved locally in the app's
//! data folder: index.json ({"chats": [{id, title, created, modified}]},
//! the sidebar) and chats/<id>.json (with "messages": [{role, content}],
//! content a string or the API's blocks). Only the index is read at
//! start; a conversation's messages when it is opened. Every write is
//! atomic.

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

/// a chat is titled after its first message, cut here
const TITLE_LEN: usize = 60;

pub type Chat = Map<String, Value>;

/// The app's data (shared with the Python Assistant only once final:
/// on trial its own folder, so two Assistants never write the same files).
pub fn data_dir() -> PathBuf {
    sonata_config::own_data_dir("assistant")
}

fn write(path: &Path, data: &Value) {
    if let Some(d) = path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = sonata_config::atomic_write(path, sonata_config::to_python_json_utf8(data, 1).as_bytes(), true);
}

fn read(path: &Path) -> Map<String, Value> {
    std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default()
}

/// The first line of a message, shortened: the chat's title.
pub fn title_from(text: &str) -> String {
    let mut line = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if line.chars().count() > TITLE_LEN {
        let cut: String = line.chars().take(TITLE_LEN).collect();
        let head = cut.rsplit_once(' ').map(|x| x.0).unwrap_or(&cut);
        line = format!("{}…", head.trim_end_matches([',', '.', ';', ':']));
    }
    if line.is_empty() { "New Chat".into() } else { line }
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub struct Store {
    pub folder: PathBuf,
    index_path: PathBuf,
    pub chats: Vec<Chat>,
}

impl Store {
    pub fn open(folder: PathBuf) -> Self {
        let index_path = folder.join("index.json");
        let chats = read(&index_path).get("chats").and_then(Value::as_array).map(|a| a.iter().filter_map(|c| c.as_object().filter(|c| c.get("id").and_then(Value::as_str).is_some_and(|s| !s.is_empty())).cloned()).collect()).unwrap_or_default();
        Self { folder, index_path, chats }
    }

    fn chat_path(&self, cid: &str) -> PathBuf {
        self.folder.join("chats").join(format!("{cid}.json"))
    }

    fn save_index(&self) {
        write(&self.index_path, &json!({"chats": self.chats}));
    }

    pub fn get(&self, cid: &str) -> Option<&Chat> {
        self.chats.iter().find(|c| c.get("id").and_then(Value::as_str) == Some(cid))
    }

    /// A chat that is not saved until its first message.
    pub fn new_chat(&self) -> Chat {
        let id: String = glib::uuid_string_random().replace('-', "").chars().take(12).collect();
        let t = now();
        json!({"id": id, "title": "New Chat", "created": t, "modified": t, "messages": []}).as_object().cloned().unwrap_or_default()
    }

    pub fn load(&self, cid: &str) -> Chat {
        let mut out = self.get(cid).cloned().unwrap_or_default();
        let data = read(&self.chat_path(cid));
        for (k, v) in &data {
            if k != "messages" {
                out.insert(k.clone(), v.clone());
            }
        }
        let msgs: Vec<Value> = data.get("messages").and_then(Value::as_array).map(|a| a.iter().filter(|m| m.get("role").and_then(Value::as_str).is_some_and(|r| r == "user" || r == "assistant") && m.get("content").is_some_and(|c| c.is_string() || c.is_array())).cloned().collect()).unwrap_or_default();
        out.insert("id".into(), json!(cid));
        out.insert("messages".into(), Value::Array(msgs));
        out
    }

    /// Write the chat and bring it to the top of the index.
    pub fn save(&mut self, chat: &mut Chat) {
        chat.insert("modified".into(), json!(now()));
        let untitled = chat.get("title").and_then(Value::as_str).is_none_or(|t| t.is_empty() || t == "New Chat");
        if untitled {
            let first = chat.get("messages").and_then(Value::as_array).and_then(|m| m.iter().find(|m| m["role"] == "user" && m["content"].is_string())).and_then(|m| m["content"].as_str()).unwrap_or("").to_string();
            chat.insert("title".into(), json!(title_from(&first)));
        }
        let cid = chat.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        write(&self.chat_path(&cid), &Value::Object(chat.clone()));
        let meta: Chat = ["id", "title", "created", "modified"].iter().filter_map(|k| chat.get(*k).map(|v| (k.to_string(), v.clone()))).collect();
        self.chats.retain(|c| c.get("id").and_then(Value::as_str) != Some(&cid));
        self.chats.insert(0, meta);
        self.save_index();
    }

    pub fn rename(&mut self, cid: &str, title: &str) {
        let title = title.split_whitespace().collect::<Vec<_>>().join(" ");
        if title.is_empty() || self.get(cid).is_none() {
            return;
        }
        if let Some(meta) = self.chats.iter_mut().find(|c| c.get("id").and_then(Value::as_str) == Some(cid)) {
            meta.insert("title".into(), json!(title));
        }
        let path = self.chat_path(cid);
        let mut data = read(&path);
        if !data.is_empty() {
            data.insert("title".into(), json!(title));
            write(&path, &Value::Object(data));
        }
        self.save_index();
    }

    pub fn delete(&mut self, cid: &str) {
        self.chats.retain(|c| c.get("id").and_then(Value::as_str) != Some(cid));
        let _ = std::fs::remove_file(self.chat_path(cid));
        self.save_index();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn titles_from_the_first_message() {
        assert_eq!(title_from("  Hello   there\nfriend "), "Hello there friend");
        assert_eq!(title_from(""), "New Chat");
        let long = "word ".repeat(20);
        let t = title_from(&long);
        assert!(t.ends_with('…') && t.chars().count() <= 61);
    }

    /// tests/test_review_apps.py: titles are one line, cut at a word with
    /// an ellipsis (a word longer than the cut is cut inside); blank is New Chat.
    #[test]
    fn test_title_from_long_and_blank() {
        assert_eq!(title_from(&"x".repeat(100)), format!("{}…", "x".repeat(60)));
        assert_eq!(title_from("  \n "), "New Chat");
    }

    /// tests/test_review_apps.py: bad messages in a chat file (a role the
    /// API doesn't take, junk, content that is neither text nor blocks)
    /// are skipped; a blank rename changes nothing.
    #[test]
    fn test_load_drops_malformed_messages_and_rename_ignores_blank() {
        let d = tempfile::tempdir().unwrap();
        let mut s = Store::open(d.path().to_path_buf());
        let mut c = s.new_chat();
        c.insert("messages".into(), json!([{"role": "user", "content": "hello there"}]));
        s.save(&mut c);
        let id = c["id"].as_str().unwrap().to_string();
        let path = d.path().join("chats").join(format!("{id}.json"));
        let mut data: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        data["messages"].as_array_mut().unwrap().extend([json!({"role": "system", "content": "x"}), json!("junk"), json!({"role": "user", "content": 5})]);
        std::fs::write(&path, data.to_string()).unwrap();
        let mut s2 = Store::open(d.path().to_path_buf());
        assert_eq!(s2.load(&id)["messages"], json!([{"role": "user", "content": "hello there"}]));
        s2.rename(&id, "   ");
        assert_eq!(Store::open(d.path().to_path_buf()).get(&id).unwrap()["title"], "hello there");
    }

    /// The Python Assistant's files: an index whose entries lack an id (or
    /// aren't objects) is read without them, and a file that isn't an
    /// object reads as empty -- never a crash or a lost index.
    #[test]
    fn python_written_index_read_the_same() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("index.json"), r#"{"chats": [{"id": "a1", "title": "T", "created": 1.5, "modified": 2.5}, {"title": "no id"}, "x", {"id": ""}]}"#).unwrap();
        let s = Store::open(d.path().to_path_buf());
        assert_eq!(s.chats.len(), 1);
        assert_eq!(s.load("a1")["title"], "T");
        std::fs::write(d.path().join("index.json"), "[1, 2]").unwrap();
        assert!(Store::open(d.path().to_path_buf()).chats.is_empty());
    }

    #[test]
    fn chats_saved_listed_renamed_deleted() {
        let d = tempfile::tempdir().unwrap();
        let mut s = Store::open(d.path().to_path_buf());
        let mut c = s.new_chat();
        c.get_mut("messages").unwrap().as_array_mut().unwrap().push(json!({"role": "user", "content": "Plan a trip"}));
        s.save(&mut c);
        let id = c["id"].as_str().unwrap().to_string();
        assert_eq!(s.get(&id).unwrap()["title"], "Plan a trip");
        let again = Store::open(d.path().to_path_buf());
        let loaded = again.load(&id);
        assert_eq!(loaded["messages"].as_array().unwrap().len(), 1);
        s.rename(&id, "  Trip  ");
        assert_eq!(Store::open(d.path().to_path_buf()).load(&id)["title"], "Trip");
        s.delete(&id);
        assert!(Store::open(d.path().to_path_buf()).get(&id).is_none());
    }
}
