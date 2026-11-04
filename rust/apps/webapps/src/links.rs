//! Which links stay in a web app (the same site): its WebKit window
//! (webapps/links.py). (A Chromium web app -- chromeguard.rs -- sends every
//! new tab or window to the default browser: Chromium has no way to open
//! one inside the app.)

use crate::urlsplit;
use std::ffi::{c_char, CStr, CString};

/// The part of a host that is "the same site": web.whatsapp.com -> whatsapp.com.
pub fn site(url: &str) -> String {
    site_with(url, base_domain)
}

pub(crate) fn site_with(url: &str, base: impl Fn(&str) -> Option<String>) -> String {
    let host = urlsplit(url).hostname().unwrap_or_default();
    if host.parse::<std::net::IpAddr>().is_ok() {
        return host; // an IP is a site of its own (no labels to share)
    }
    if let Some(b) = base(&host) {
        return b;
    }
    let parts: Vec<&str> = host.split('.').collect();
    let n = parts.len();
    if n > 2 && parts[n - 2].chars().count() <= 3 && parts[n - 1].chars().count() == 2 {
        return parts[n - 3..].join("."); // example.co.uk
    }
    parts[n.saturating_sub(2)..].join(".")
}

type TldFn = unsafe extern "C" fn(*const c_char, *mut *mut glib::ffi::GError) -> *const c_char;

/// libsoup's public suffix list (WebKit's own): a.github.io and b.github.io
/// are different sites. None without it (or for localhost and the like).
pub fn base_domain(host: &str) -> Option<String> {
    // SAFETY: the symbol has this signature (libsoup 3's soup-tld.h)
    let f: TldFn = unsafe { std::mem::transmute(crate::soup_sym(c"soup_tld_get_base_domain")?) };
    let c = CString::new(host).ok()?;
    let mut err: *mut glib::ffi::GError = std::ptr::null_mut();
    // SAFETY: a valid C string and error slot; the result points into `c`
    unsafe {
        let r = f(c.as_ptr(), &mut err);
        if !err.is_null() {
            glib::ffi::g_error_free(err);
            return None;
        }
        if r.is_null() {
            return None;
        }
        Some(CStr::from_ptr(r).to_string_lossy().into_owned())
    }
}

pub fn stays_inside(app_url: &str, target: &str) -> bool {
    stays_inside_with(app_url, target, base_domain)
}

pub(crate) fn stays_inside_with(app_url: &str, target: &str, base: impl Fn(&str) -> Option<String> + Copy) -> bool {
    if target.is_empty() || ["about:", "blob:", "data:"].iter().any(|p| target.starts_with(p)) {
        return true;
    }
    if !matches!(urlsplit(target).scheme.as_str(), "http" | "https") {
        return false;
    }
    site_with(target, base) == site_with(app_url, base)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn none(_: &str) -> Option<String> {
        None
    }

    #[test]
    fn test_subdomains_are_the_same_site() {
        assert_eq!(site("https://web.whatsapp.com/x"), "whatsapp.com");
        assert_eq!(site("https://WWW.Example.COM/"), "example.com"); // hosts ignore case
        assert_eq!(site("https://www.bbc.co.uk/news"), "bbc.co.uk");
    }

    /// a.github.io and b.github.io belong to different people (the public
    /// suffix list WebKit uses itself).
    #[test]
    fn test_hosted_pages_are_sites_of_their_own() {
        if base_domain("a.github.io").is_none() {
            return; // libsoup 3 not available
        }
        assert_ne!(site("https://a.github.io"), site("https://b.github.io"));
    }

    /// No public suffix list: the last two labels, three for a two-letter
    /// country domain under a short one (example.co.uk).
    #[test]
    fn test_without_libsoup() {
        assert_eq!(site_with("https://web.whatsapp.com", none), "whatsapp.com");
        assert_eq!(site_with("https://news.bbc.co.uk", none), "bbc.co.uk");
        assert_eq!(site_with("https://www.globo.com.br", none), "globo.com.br");
        assert_eq!(site_with("http://localhost:8080", none), "localhost");
    }

    /// An IP address is a site of its own (no labels to share).
    #[test]
    fn test_addresses_and_nothing() {
        assert_eq!(site("http://192.168.0.1:8080/"), "192.168.0.1");
        assert_eq!(site("http://[::1]/"), "::1");
        assert_eq!(site(""), "");
    }

    const APP: &str = "https://web.whatsapp.com/";

    #[test]
    fn test_same_site_stays() {
        assert!(stays_inside(APP, "https://media.whatsapp.com/v/1"));
        assert!(stays_inside(APP, "http://whatsapp.com/"));
        assert!(stays_inside("http://localhost:3000", "http://localhost:4000/"));
        assert!(stays_inside(APP, "https://static.whatsapp.com/x"));
    }

    /// A host that only ends with the same letters is another site.
    #[test]
    fn test_other_sites_leave() {
        for base in [base_domain as fn(&str) -> Option<String>, none] {
            assert!(!stays_inside_with(APP, "https://evilwhatsapp.com/", base));
            assert!(!stays_inside_with(APP, "https://whatsapp.com.evil.net/", base));
            assert!(!stays_inside_with(APP, "https://www.google.com/", base));
            assert!(!stays_inside_with(APP, "https://www.youtube.com/watch", base));
            assert!(!stays_inside_with("http://192.168.0.1", "http://192.168.0.2/", base));
        }
    }

    /// about:blank, blob: and data: are the page's own (downloads, previews,
    /// popups it fills itself).
    #[test]
    fn test_page_internals_stay() {
        for url in ["", "about:blank", "blob:https://web.whatsapp.com/1234", "data:text/plain,hi"] {
            assert!(stays_inside(APP, url), "{url}");
        }
    }

    /// file:, javascript:, mailto: and the like never load in the app.
    #[test]
    fn test_other_schemes_never_open_inside() {
        for url in ["file:///etc/passwd", "javascript:alert(1)", "mailto:a@b.c", "tel:+5511999999999", "ftp://whatsapp.com/x"] {
            assert!(!stays_inside(APP, url), "{url}");
        }
    }
}
