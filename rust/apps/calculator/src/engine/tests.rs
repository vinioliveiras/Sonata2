//! tests/test_calculator.py, and what the Python engine shows for many more
//! key sequences (generated from sonata2/calculator/engine.py, so the Rust
//! Calculator shows exactly what the Python one does).

use super::*;

/// Every character of `keys` pressed in turn, as the Python tests do.
fn run(keys: &str) -> Engine {
    let mut e = Engine::new();
    for k in keys.chars() {
        e.press(&k.to_string());
    }
    e
}

#[test]
fn shows_what_the_python_calculator_shows() {
    // (keys, display, clear key's label)
    let cases: &[(&str, &str, &str)] = &[
        ("12+3×4=", "24", "AC"),
        ("2+3×", "3", "AC"),
        ("2×3+", "6", "AC"),
        ("5÷0=", "Error", "C"),
        ("200+10%=", "220", "AC"),
        ("2+3==", "8", "AC"),
        ("1÷3=", "0.333333333333", "AC"),
        ("1234567×1000=", "1,234,567,000", "AC"),
        ("99999999×99999999=", "9.9999998e15", "AC"),
        ("5±", "-5", "C"),
        ("0.1+0.2=", "0.3", "AC"),
        ("12⌫", "1", "C"),
        ("2×+3=", "5", "AC"),
        ("50%", "0.5", "AC"),
        ("7C", "0", "AC"),
        ("2÷3=", "0.666666666667", "AC"),
        ("1÷7×7=", "1", "AC"),
        ("10÷4=", "2.5", "AC"),
        ("-", "0", "AC"),
        ("5±±", "5", "C"),
        ("0.000000001×1=", "1e-9", "AC"),
        ("0.0000001×1=", "0.0000001", "AC"),
        ("123456789012345678", "123,456,789,012,345", "C"),
        ("9×9×9×9=====", "43,046,721", "AC"),
        ("999999999999×999999999999=", "9.99999999998e23", "AC"),
        ("1÷3×3=", "1", "AC"),
        ("100−1%=", "99", "AC"),
        ("50×10%=", "5", "AC"),
        ("3.", "3.", "C"),
        ("3.±", "-3.", "C"),
        ("0⌫", "0", "AC"),
        ("5⌫⌫", "0", "AC"),
        ("1.5×2=", "3", "AC"),
        ("2×3×4+1=", "25", "AC"),
        ("8÷2÷2=", "2", "AC"),
        ("1+2×3−4÷2=", "5", "AC"),
        ("5±+3=", "-2", "AC"),
        ("1÷0=7", "7", "C"),
        ("1÷0=C", "0", "AC"),
        ("12345.678", "12,345.678", "C"),
        ("0.5±=", "-0.5", "AC"),
        ("1e", "1", "C"),
        ("..1", "0.1", "C"),
        ("99999999999999×99999999999999×99999999999999×99999999999999×99999999999999×99999999999999=", "1e84", "AC"),
        ("1÷999999999×1÷999999999×1÷999999999=", "1.000000003e-27", "AC"),
        ("7=", "7", "AC"),
        ("7==", "7", "AC"),
        ("+3=", "3", "AC"),
        ("×5=", "0", "AC"),
        ("3+=", "6", "AC"),
        ("3+==", "9", "AC"),
        ("123456789012×10=", "1.23456789012e12", "AC"),
        ("0.1×0.1×0.1×0.1×0.1×0.1×0.1×0.1×0.1=", "1e-9", "AC"),
    ];
    for (keys, shown, label) in cases {
        let e = run(keys);
        assert_eq!(e.display(), *shown, "{keys}");
        assert_eq!(e.clear_label(), *label, "{keys} (clear label)");
    }
}

#[test]
fn clear_label() {
    let mut e = Engine::new();
    assert_eq!(e.clear_label(), "AC");
    e.press("5");
    assert_eq!(e.clear_label(), "C");
}

#[test]
fn ac_key_clears_everything() {
    let mut e = run("12+3");
    e.press("AC");
    assert_eq!(e.display(), "0");
    assert!(e.tokens.is_empty());
}

#[test]
fn the_operator_waiting_for_a_number_is_known() {
    assert_eq!(run("2+").waiting_op(), Some('+'));
    assert_eq!(run("2+3").waiting_op(), None);
    assert_eq!(run("2×+").waiting_op(), Some('+'));
}

#[test]
fn group_and_fmt() {
    assert_eq!(group("1234.5"), "1,234.5");
    assert_eq!(group("-1234567"), "-1,234,567");
    assert_eq!(group("0."), "0.");
    assert_eq!(fmt(&crate::decimal::Dec::parse("-0.00000001").unwrap()), "-0.00000001");
    let f = |s: &str| fmt(&crate::decimal::Dec::parse(s).unwrap());
    assert_eq!(f("123456789012.5"), "123,456,789,012"); // a tie: to even
    assert_eq!(f("123456789013.5"), "123,456,789,014");
    assert_eq!(f("999999999999.5"), "1e12"); // rounds up past 12 digits: scientific
}
