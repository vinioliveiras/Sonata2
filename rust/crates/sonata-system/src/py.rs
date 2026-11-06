//! Python's reading of a value, for settings files the Python Sonata wrote
//! (or a hand edit): bool() and float() as the Python modules apply them,
//! so a file reads the same in both builds.

use serde_json::Value;

/// Python's bool(): null, false, 0, "" and empty lists / objects are false.
pub fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Python's float(): numbers, true / false, and text holding a number
/// ("1.5", " 2 "); None where Python raises.
pub fn float(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Bool(b) => Some(f64::from(u8::from(*b))),
        Value::String(s) => s.trim().parse().ok(),
        _ => None,
    }
}

/// Python's round(x, 1): the nearest tenth of the float's exact value, ties
/// to even -- which is what Rust's formatting does too (a float times ten,
/// rounded, isn't: 0.35 is 0.34999... and rounds down).
pub fn round1(x: f64) -> f64 {
    format!("{x:.1}").parse().unwrap_or(x)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn like_python() {
        for (v, b) in [(json!(null), false), (json!(0), false), (json!(1), true), (json!(""), false), (json!("no"), true), (json!([]), false), (json!({"a": 1}), true), (json!(0.0), false)] {
            assert_eq!(truthy(Some(&v)), b, "{v}");
        }
        assert!(!truthy(None));
        assert_eq!(float(&json!("1.5")), Some(1.5));
        assert_eq!(float(&json!(" 2 ")), Some(2.0));
        assert_eq!(float(&json!(3)), Some(3.0));
        assert_eq!(float(&json!(true)), Some(1.0));
        assert_eq!(float(&json!("x")), None);
        assert_eq!(float(&json!(null)), None);
        // python3 -c "print([round(x, 1) for x in (0.35, 0.25, 0.15, 1.45, 2.675, -0.05)])"
        assert_eq!([0.35, 0.25, 0.15, 1.45, 2.675, -0.05].map(round1), [0.3, 0.2, 0.1, 1.4, 2.7, -0.1]);
    }
}
