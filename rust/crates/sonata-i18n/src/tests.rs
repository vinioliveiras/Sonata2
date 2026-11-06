//! i18n.py's rules (tests/test_i18n.py), on Sonata's real catalogs and on
//! small ones written here.

use super::*;
use std::collections::HashMap;

fn env(pairs: &[(&str, &str)]) -> &'static str {
    let m: HashMap<String, String> = pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    detect_from(|k| m.get(k).cloned())
}

#[test]
fn detect_follows_the_session_locale() {
    assert_eq!(env(&[("LANG", "pt_BR.UTF-8")]), "pt_BR");
    assert_eq!(env(&[("LANG", "pt_PT.UTF-8")]), "pt_PT");
    assert_eq!(env(&[("LANG", "pt")]), "pt_BR"); // plain pt: Brazil's
    assert_eq!(env(&[("LANG", "de_DE.UTF-8")]), "en");
    assert_eq!(env(&[]), "en");
    assert_eq!(env(&[("LC_ALL", "C"), ("LANG", "pt_PT.UTF-8")]), "pt_PT"); // C is skipped
    assert_eq!(env(&[("LC_MESSAGES", "pt_PT@euro"), ("LANG", "en_US.UTF-8")]), "pt_PT");
    assert_eq!(env(&[("LANGUAGE", "pt_BR:pt:en")]), "pt_BR");
    assert_eq!(env(&[("LC_ALL", "en_GB.UTF-8"), ("LANG", "pt_BR.UTF-8")]), "en"); // the first set wins
}

fn small(lang: &str) -> Catalog {
    let dir = tempfile::tempdir().unwrap();
    let d = dir.path().join(lang);
    std::fs::create_dir_all(&d).unwrap();
    std::fs::write(
        d.join("a.json"),
        r#"{"Move to Trash": "Mover para a Lixeira", "{n} item": ["{n} item", "{n} itens"],
            "stopwatch\u0004Reset": "Zerar", "Reset": "Redefinir", "Empty": ""}"#,
    )
    .unwrap();
    std::fs::write(d.join("b.json"), r#"{"Move to Trash": "Mover para o Lixo"}"#).unwrap(); // later file wins
    std::fs::write(d.join("broken.json"), "{oops").unwrap();
    Catalog::load(dir.path(), lang)
}

#[test]
fn gettext_translates_and_falls_back_to_english() {
    let c = small("pt_BR");
    assert_eq!(c.gettext("Move to Trash"), "Mover para o Lixo");
    assert_eq!(c.gettext("Not translated"), "Not translated");
    assert_eq!(c.gettext("Empty"), "Empty"); // an empty translation is no translation
    assert_eq!(c.gettext("{n} item"), "{n} item"); // a plural's singular asked alone
    assert_eq!(c.gettext(""), "");
}

#[test]
fn ngettext_zero_is_singular_in_brazil_only() {
    assert_eq!(small("pt_BR").ngettext("{n} item", "{n} items", 0), "{n} item");
    assert_eq!(small("pt_PT").ngettext("{n} item", "{n} items", 0), "{n} itens");
    assert_eq!(small("pt_PT").ngettext("{n} item", "{n} items", 1), "{n} item");
    let en = Catalog::new("en", HashMap::new());
    assert_eq!(en.ngettext("{n} item", "{n} items", 0), "{n} items");
    assert_eq!(en.ngettext("{n} item", "{n} items", 1), "{n} item");
}

#[test]
fn pgettext_reads_the_context_then_the_plain_string() {
    let c = small("pt_BR");
    assert_eq!(c.pgettext("stopwatch", "Reset"), "Zerar");
    assert_eq!(c.pgettext("settings", "Reset"), "Redefinir");
}

#[test]
fn english_has_no_catalog() {
    let dir = tempfile::tempdir().unwrap();
    assert_eq!(Catalog::load(dir.path(), "en").gettext("Calculator"), "Calculator");
}

#[test]
fn sonatas_own_catalogs_load() {
    // the clone's catalogs (locale/)
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../locale");
    assert_eq!(Catalog::load(&dir, "pt_BR").gettext("Calculator"), "Calculadora");
    assert_eq!(Catalog::load(&dir, "pt_PT").gettext("Calculator"), "Calculadora");
    assert_eq!(Catalog::load(&dir, "pt_PT").gettext("Error"), "Erro");
}

#[test]
fn fmt_fills_named_placeholders() {
    assert_eq!(fmt("Copy {name}", &[("name", "a.txt")]), "Copy a.txt");
    assert_eq!(fmt("{n} of {total}", &[("n", "1"), ("total", "3")]), "1 of 3");
    assert_eq!(fmt("{{literal}} {x}", &[]), "{literal} {x}");
    assert_eq!(fmt("ção {n}", &[("n", "2")]), "ção 2");
}
