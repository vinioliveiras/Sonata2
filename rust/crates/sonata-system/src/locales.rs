//! Language & Region (backend/locales.py; Settings): the system's language
//! and its formats -- dates, times, numbers, money, measures, paper -- as on
//! a first install, apart from the keyboard (Vini: Portuguese (Brazil) with a
//! Portugal keyboard). Written with localectl (every program, the login
//! screen too), the locale generated first when the system lacks it
//! (/etc/locale.gen + locale-gen). It takes effect at the next login.

use std::collections::{BTreeMap, HashSet};

pub const FORMAT_VARS: [&str; 5] = ["LC_TIME", "LC_NUMERIC", "LC_MONETARY", "LC_MEASUREMENT", "LC_PAPER"];

/// (locale, the language's own name): Sonata's translations first
pub const LANGUAGES: [(&str, &str); 17] = [
    ("en_US.UTF-8", "English (United States)"), ("en_GB.UTF-8", "English (United Kingdom)"),
    ("pt_BR.UTF-8", "Português (Brasil)"), ("pt_PT.UTF-8", "Português (Portugal)"),
    ("es_ES.UTF-8", "Español (España)"), ("es_MX.UTF-8", "Español (México)"),
    ("fr_FR.UTF-8", "Français"), ("de_DE.UTF-8", "Deutsch"), ("it_IT.UTF-8", "Italiano"),
    ("nl_NL.UTF-8", "Nederlands"), ("pl_PL.UTF-8", "Polski"), ("ru_RU.UTF-8", "Русский"),
    ("uk_UA.UTF-8", "Українська"), ("tr_TR.UTF-8", "Türkçe"), ("ja_JP.UTF-8", "日本語"),
    ("zh_CN.UTF-8", "中文 (简体)"), ("ko_KR.UTF-8", "한국어"),
];
/// formats by region (the same codes: a locale's formats are its region's)
pub const REGIONS: [(&str, &str); 15] = [
    ("en_US.UTF-8", "United States"), ("en_GB.UTF-8", "United Kingdom"), ("en_IE.UTF-8", "Ireland"),
    ("pt_BR.UTF-8", "Brasil"), ("pt_PT.UTF-8", "Portugal"), ("es_ES.UTF-8", "España"),
    ("es_MX.UTF-8", "México"), ("es_AR.UTF-8", "Argentina"), ("fr_FR.UTF-8", "France"),
    ("de_DE.UTF-8", "Deutschland"), ("it_IT.UTF-8", "Italia"), ("nl_NL.UTF-8", "Nederland"),
    ("pl_PL.UTF-8", "Polska"), ("ja_JP.UTF-8", "日本"), ("zh_CN.UTF-8", "中国"),
];

pub type Settings = BTreeMap<String, String>;

pub fn valid(loc: &str) -> bool {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^[A-Za-z]{2,3}(_[A-Za-z]{2})?(\.UTF-8)?(@[a-z]+)?$").unwrap()).is_match(loc)
}

pub fn parse(text: &str) -> Settings {
    let mut out = Settings::new();
    for line in text.lines() {
        let mut line = line.trim();
        if let Some(rest) = line.strip_prefix("System Locale:") {
            line = rest.trim();
        }
        if let Some((k, v)) = line.split_once('=').filter(|_| !line.starts_with('#')) {
            let k = k.trim();
            if k == "LANG" || k.starts_with("LC_") {
                out.insert(k.into(), v.trim().trim_matches('"').into());
            }
        }
    }
    out
}

/// The system's locale settings: its file (/etc/locale.conf; Debian's
/// /etc/default/locale) -- read directly, the page opens without waiting
/// for a program -- else localectl.
pub fn current() -> Settings {
    for path in ["/etc/locale.conf", "/etc/default/locale"] {
        if let Ok(text) = std::fs::read_to_string(path) {
            let got = parse(&text);
            if !got.is_empty() {
                return got;
            }
        }
    }
    let (rc, out) = sonata_backend::run::run(&["localectl", "status"], 5);
    if rc == 0 {
        let got = parse(out.split("VC Keymap:").next().unwrap_or("")); // (not the keyboard's lines)
        if !got.is_empty() {
            return got;
        }
    }
    Settings::from([("LANG".into(), std::env::var("LANG").unwrap_or_else(|_| "en_US.UTF-8".into()))])
}

/// Locales the system can use now ("pt_BR.UTF-8"), normalised.
pub fn installed() -> HashSet<String> {
    for cmd in [["localectl", "list-locales"], ["locale", "-a"]] {
        let (rc, out) = sonata_backend::run::run(&cmd, 5);
        if rc == 0 && !out.trim().is_empty() {
            return out.split_whitespace().map(normalise).collect();
        }
    }
    HashSet::new()
}

/// "pt_BR.utf8" -> "pt_BR.UTF-8" (locale -a's spelling -> localectl's).
pub fn normalise(name: &str) -> String {
    let (base, enc) = name.split_once('.').unwrap_or((name, ""));
    let (enc, m) = enc.split_once('@').unwrap_or((enc, ""));
    let mut out = base.to_string();
    if enc.to_lowercase().replace('-', "") == "utf8" {
        out.push_str(".UTF-8");
    } else if !enc.is_empty() {
        out.push('.');
        out.push_str(enc);
    }
    if !m.is_empty() {
        out.push('@');
        out.push_str(m);
    }
    out
}

pub fn languages() -> Vec<(&'static str, &'static str)> {
    LANGUAGES.to_vec()
}

pub fn regions() -> Vec<(&'static str, &'static str)> {
    REGIONS.to_vec()
}

/// The formats region in effect: LC_TIME's, else the language's.
pub fn formats_of(settings: &Settings) -> String {
    settings.get("LC_TIME").or(settings.get("LANG")).filter(|s| !s.is_empty()).cloned().unwrap_or_else(|| "en_US.UTF-8".into())
}

fn lang_of(loc: &str) -> &str {
    loc.split('_').next().unwrap_or("").split('.').next().unwrap_or("")
}

/// The region after a new language: the new language's when the region
/// went with the old one (Vini switched Portuguese to English and dates
/// stayed Portuguese), else kept -- English with Portugal's formats, picked
/// on purpose, stays.
pub fn follow_region(old_lang: &str, new_lang: &str, region: &str) -> String {
    if lang_of(region) != lang_of(old_lang) || lang_of(new_lang) == lang_of(old_lang) {
        return region.into();
    }
    if REGIONS.iter().any(|(c, _)| *c == new_lang) {
        return new_lang.into();
    }
    REGIONS.iter().find(|(c, _)| lang_of(c) == lang_of(new_lang)).map(|(c, _)| c.to_string()).unwrap_or_else(|| region.into())
}

/// The script (run as root) that sets the language and the formats: the
/// missing locales generated first where the system generates them
/// (/etc/locale.gen), then localectl. None for an unsafe name.
pub fn command(lang: &str, formats: &str) -> Option<String> {
    let gen = std::path::Path::new("/etc/locale.gen").exists() && sonata_backend::run::which("locale-gen").is_some();
    command_with(lang, formats, &installed(), gen)
}

pub fn command_with(lang: &str, formats: &str, have: &HashSet<String>, can_generate: bool) -> Option<String> {
    if !(valid(lang) && valid(formats)) {
        return None;
    }
    let mut need: Vec<&str> = vec![];
    for x in [lang, formats] {
        if !have.contains(x) && !need.contains(&x) {
            need.push(x);
        }
    }
    let mut steps = Vec::new();
    if !need.is_empty() && can_generate {
        for loc in &need {
            // uncomment "pt_BR.UTF-8 UTF-8", or add it when the file doesn't list it
            steps.push(format!(
                "(grep -q '^{loc} UTF-8' /etc/locale.gen || sed -i 's/^# *{} UTF-8/{loc} UTF-8/' /etc/locale.gen; grep -q '^{loc} UTF-8' /etc/locale.gen || echo '{loc} UTF-8' >> /etc/locale.gen)",
                regex::escape(loc)
            ));
        }
        steps.push("locale-gen".into());
    }
    let mut vars = vec![format!("LANG={lang}")];
    if formats != lang {
        vars.extend(FORMAT_VARS.iter().map(|v| format!("{v}={formats}")));
    }
    steps.push(format!("localectl set-locale {}", vars.join(" ")));
    Some(steps.join(" && "))
}

/// How dates, times and numbers read in `loc` (empty when it isn't
/// installed yet): the formats preview.
pub fn sample(loc: &str) -> String {
    if !valid(loc) {
        return String::new();
    }
    let out = |args: &[&str]| {
        std::process::Command::new(args[0]).args(&args[1..]).env("LC_ALL", loc).stdin(std::process::Stdio::null()).stderr(std::process::Stdio::null()).output().map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default()
    };
    let date = out(&["date", "+%A, %x · %X"]);
    let num = out(&["printf", "%'.2f", "1234567.89"]);
    if date.is_empty() { String::new() } else { format!("{date} · {num}") }
}

#[cfg(test)]
mod tests {
    use super::*;

    const STATUS: &str = "   System Locale: LANG=pt_BR.UTF-8\n                  LC_TIME=pt_PT.UTF-8\n       VC Keymap: br\n      X11 Layout: pt\n";

    #[test]
    fn reads_the_settings() {
        let s = |pairs: &[(&str, &str)]| pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect::<Settings>();
        assert_eq!(parse("LANG=pt_BR.UTF-8\nLC_TIME=\"pt_PT.UTF-8\"\n# LC_X=y\n"), s(&[("LANG", "pt_BR.UTF-8"), ("LC_TIME", "pt_PT.UTF-8")]));
        let cur = parse(STATUS.split("VC Keymap:").next().unwrap()); // (not the keyboard's lines)
        assert_eq!(cur, s(&[("LANG", "pt_BR.UTF-8"), ("LC_TIME", "pt_PT.UTF-8")]));
        assert_eq!(formats_of(&cur), "pt_PT.UTF-8");
        assert_eq!(formats_of(&s(&[("LANG", "pt_BR.UTF-8")])), "pt_BR.UTF-8");
    }

    #[test]
    fn normalise_and_safe_names() {
        assert_eq!(normalise("pt_BR.utf8"), "pt_BR.UTF-8");
        assert_eq!(normalise("en_US.UTF-8"), "en_US.UTF-8");
        assert_eq!(normalise("C"), "C");
        assert!(valid("pt_BR.UTF-8"));
        for bad in ["", "pt_BR.UTF-8; rm -rf /", "$(id)", "pt_BR.UTF-8 && x", "../x"] {
            assert!(!valid(bad), "{bad}");
            assert_eq!(command_with(bad, "pt_BR.UTF-8", &HashSet::new(), false), None);
        }
        assert_eq!(sample("x; rm"), "");
    }

    #[test]
    fn command_sets_language_and_formats_apart() {
        let have: HashSet<String> = ["pt_BR.UTF-8".into(), "pt_PT.UTF-8".into()].into();
        let cmd = command_with("pt_BR.UTF-8", "pt_PT.UTF-8", &have, true).unwrap();
        let vars: Vec<String> = FORMAT_VARS.iter().map(|v| format!("{v}=pt_PT.UTF-8")).collect();
        assert_eq!(cmd, format!("localectl set-locale LANG=pt_BR.UTF-8 {}", vars.join(" ")));
        assert!(!cmd.contains("XKB")); // the keyboard is not touched
        assert_eq!(command_with("pt_BR.UTF-8", "pt_BR.UTF-8", &have, true).unwrap(), "localectl set-locale LANG=pt_BR.UTF-8");
    }

    #[test]
    fn missing_locale_generated_first() {
        let cmd = command_with("pt_PT.UTF-8", "pt_PT.UTF-8", &["en_US.UTF-8".to_string()].into(), true).unwrap();
        assert!(cmd.contains("/etc/locale.gen"));
        assert!(cmd.find("locale-gen\u{20}&&").unwrap() < cmd.find("localectl set-locale").unwrap());
        assert!(cmd.contains("pt_PT.UTF-8 UTF-8"));
        assert!(cmd.contains(r"s/^# *pt_PT\.UTF\-8 UTF-8/"));
    }

    #[test]
    fn lists_and_regions() {
        assert!(languages().iter().any(|(c, _)| *c == "pt_BR.UTF-8") && languages().iter().any(|(c, _)| *c == "pt_PT.UTF-8"));
        assert!(regions().iter().any(|(c, _)| *c == "pt_PT.UTF-8"));
        // Vini: dates stayed Portuguese after switching to English
        assert_eq!(follow_region("pt_BR.UTF-8", "en_US.UTF-8", "pt_BR.UTF-8"), "en_US.UTF-8");
        assert_eq!(follow_region("en_US.UTF-8", "de_DE.UTF-8", "pt_PT.UTF-8"), "pt_PT.UTF-8");
        assert_eq!(follow_region("pt_BR.UTF-8", "pt_PT.UTF-8", "pt_BR.UTF-8"), "pt_BR.UTF-8");
        assert_eq!(follow_region("pt_PT.UTF-8", "es_ES.UTF-8", "pt_PT.UTF-8"), "es_ES.UTF-8");
        assert_eq!(follow_region("pt_PT.UTF-8", "ko_KR.UTF-8", "pt_PT.UTF-8"), "pt_PT.UTF-8"); // no Korea
    }
}
