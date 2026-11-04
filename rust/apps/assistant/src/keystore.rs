//! The Anthropic API key (assistant/keystore.py), kept in the keyring (the
//! freedesktop Secret Service: GNOME's keyring or KeePassXC), never in a
//! plain file. The ANTHROPIC_API_KEY environment variable is used when the
//! keyring has none. The same entry as the Python Assistant: a key saved in
//! one is the other's too (it is a secret, not settings).
//!
//! Calls are asynchronous: a locked keyring may ask for its password.

use std::cell::RefCell;
use std::collections::HashMap;

pub const SCHEMA_NAME: &str = "io.github.vinioliveiras.sonata2.assistant";
pub const LABEL: &str = "Assistant: Anthropic API key";

thread_local! {
    static CACHE: RefCell<Option<String>> = const { RefCell::new(None) };
}

fn schema() -> libsecret::Schema {
    libsecret::Schema::new(SCHEMA_NAME, libsecret::SchemaFlags::NONE, HashMap::from([("service", libsecret::SchemaAttributeType::String)]))
}

fn attrs() -> HashMap<&'static str, &'static str> {
    HashMap::from([("service", "anthropic-api")])
}

fn env() -> String {
    std::env::var("ANTHROPIC_API_KEY").unwrap_or_default().trim().to_string()
}

/// The last key read or saved ("" if none).
pub fn cached() -> String {
    CACHE.with(|c| c.borrow().clone()).unwrap_or_else(env)
}

/// callback(key or "") on the main loop.
pub fn load(callback: impl FnOnce(String) + 'static) {
    if let Some(k) = CACHE.with(|c| c.borrow().clone()) {
        callback(k);
        return;
    }
    libsecret::password_lookup(Some(&schema()), attrs(), gio::Cancellable::NONE, move |res| {
        // no keyring running: the environment's key
        let key = res.ok().flatten().map(|k| k.trim().to_string()).filter(|k| !k.is_empty()).unwrap_or_else(env);
        CACHE.with(|c| c.replace(Some(key.clone())));
        callback(key);
    });
}

/// callback(ok); "" removes it.
pub fn save(key: &str, callback: impl FnOnce(bool) + 'static) {
    let key = key.trim().to_string();
    let done = move |key: String| {
        move |res: Result<(), glib::Error>| {
            let ok = res.is_ok();
            if ok {
                CACHE.with(|c| c.replace(Some(if key.is_empty() { env() } else { key })));
            }
            callback(ok);
        }
    };
    if key.is_empty() {
        libsecret::password_clear(Some(&schema()), attrs(), gio::Cancellable::NONE, done(key));
    } else {
        libsecret::password_store(Some(&schema()), attrs(), Some(libsecret::COLLECTION_DEFAULT.as_str()), LABEL, &key.clone(), gio::Cancellable::NONE, done(key));
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn environment_fallback() {
        // no keyring read yet: the environment's key
        std::env::set_var("ANTHROPIC_API_KEY", "  sk-env  ");
        assert_eq!(super::cached(), "sk-env");
    }
}
