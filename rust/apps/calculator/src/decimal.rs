//! The little of Python's `decimal` the Calculator needs: exact + − ×,
//! division correctly rounded, and rounding to a number of significant
//! digits (ROUND_HALF_EVEN, Python's default) -- so the Rust Calculator
//! shows exactly what the Python one shows.

use num_bigint::{BigInt, Sign};
use num_traits::{One, Signed, Zero};

/// Python's default context limits: beyond them, Overflow (shown as Error).
pub const EMAX: i64 = 999_999;
pub const EMIN: i64 = -999_999;

#[derive(Clone, Debug, PartialEq)]
pub enum MathError {
    DivisionByZero,
    Overflow,
}

/// `m × 10^e`.
#[derive(Clone, Debug)]
pub struct Dec {
    m: BigInt,
    e: i64,
}

fn pow10(n: u64) -> BigInt {
    num_traits::pow(BigInt::from(10u8), n as usize)
}

fn digits(m: &BigInt) -> i64 {
    if m.is_zero() {
        1
    } else {
        m.abs().to_str_radix(10).len() as i64
    }
}

impl Dec {
    pub fn zero() -> Self {
        Dec { m: BigInt::zero(), e: 0 }
    }

    pub fn from_i64(n: i64) -> Self {
        Dec { m: BigInt::from(n), e: 0 }
    }

    /// "12", "-0.5", "3.", ".25" (what Decimal(str) takes from the Calculator).
    pub fn parse(text: &str) -> Option<Self> {
        let (neg, body) = match text.strip_prefix('-') {
            Some(b) => (true, b),
            None => (false, text),
        };
        let (whole, frac) = body.split_once('.').unwrap_or((body, ""));
        if whole.is_empty() && frac.is_empty() {
            return None;
        }
        if !whole.bytes().chain(frac.bytes()).all(|b| b.is_ascii_digit()) {
            return None;
        }
        let all = format!("{whole}{frac}");
        let mut m: BigInt = if all.is_empty() { BigInt::zero() } else { all.parse().ok()? };
        if neg {
            m = -m;
        }
        Some(Dec { m, e: -(frac.len() as i64) })
    }

    pub fn is_zero(&self) -> bool {
        self.m.is_zero()
    }

    /// The exponent of the most significant digit (Decimal.adjusted).
    pub fn adjusted(&self) -> i64 {
        self.e + digits(&self.m) - 1
    }

    pub fn neg(&self) -> Self {
        Dec { m: -self.m.clone(), e: self.e }
    }

    /// At most `prec` significant digits, ROUND_HALF_EVEN; then the
    /// context's exponent limits.
    pub fn round(&self, prec: u32) -> Result<Self, MathError> {
        let n = digits(&self.m);
        let mut out = if n <= prec as i64 {
            self.clone()
        } else {
            let drop = (n - prec as i64) as u64;
            let div = pow10(drop);
            let (sign, mag) = (self.m.sign(), self.m.abs());
            let (mut q, r) = (&mag / &div, &mag % &div);
            let twice: BigInt = r * 2;
            let odd = (&q % 2u8) == BigInt::one();
            if twice > div || (twice == div && odd) {
                q += 1u8;
            }
            let mut e = self.e + drop as i64;
            if digits(&q) > prec as i64 {
                q /= 10u8; // 999.5 -> 1000: the digit dropped is a 0
                e += 1;
            }
            Dec { m: if sign == Sign::Minus { -q } else { q }, e }
        };
        if out.m.is_zero() {
            out.e = 0;
            return Ok(out);
        }
        let adj = out.adjusted();
        if adj > EMAX {
            return Err(MathError::Overflow);
        }
        if adj < EMIN - prec as i64 {
            return Ok(Dec::zero()); // underflow: Python's subnormal rounds away to 0 too
        }
        Ok(out)
    }

    pub fn add(&self, other: &Dec, prec: u32) -> Result<Dec, MathError> {
        let e = self.e.min(other.e);
        let a = &self.m * pow10((self.e - e) as u64);
        let b = &other.m * pow10((other.e - e) as u64);
        Dec { m: a + b, e }.round(prec)
    }

    pub fn sub(&self, other: &Dec, prec: u32) -> Result<Dec, MathError> {
        self.add(&other.neg(), prec)
    }

    pub fn mul(&self, other: &Dec, prec: u32) -> Result<Dec, MathError> {
        Dec { m: &self.m * &other.m, e: self.e + other.e }.round(prec)
    }

    /// Correctly rounded to `prec` digits: enough quotient digits, plus a
    /// sticky digit for a remainder, so a tie is a tie only when exact.
    pub fn div(&self, other: &Dec, prec: u32) -> Result<Dec, MathError> {
        if other.m.is_zero() {
            return Err(MathError::DivisionByZero);
        }
        if self.m.is_zero() {
            return Ok(Dec::zero());
        }
        let k = (prec as i64 + 2 + digits(&other.m) - digits(&self.m)).max(0);
        let num = self.m.abs() * pow10(k as u64);
        let den = other.m.abs();
        let (q, r) = (&num / &den, &num % &den);
        let q = q * 10u8 + if r.is_zero() { 0u8 } else { 1u8 };
        let neg = (self.m.sign() == Sign::Minus) != (other.m.sign() == Sign::Minus);
        let m = if neg { -q } else { q };
        let mut out = Dec { m, e: self.e - other.e - k - 1 }.round(prec)?;
        // an exact quotient keeps no more trailing zeros than Python's
        // "ideal exponent" (10 ÷ 4 is 2.5, not 2.500...0)
        let ideal = self.e - other.e;
        while out.e < ideal && !out.m.is_zero() && (&out.m % 10u8).is_zero() {
            out.m /= 10u8;
            out.e += 1;
        }
        Ok(out)
    }

    /// Plain notation, no exponent ('f' format): "1200", "-0.05".
    pub fn to_plain(&self) -> String {
        let neg = self.m.sign() == Sign::Minus;
        let ds = self.m.abs().to_str_radix(10);
        let body = if self.e >= 0 {
            format!("{ds}{}", "0".repeat(self.e as usize))
        } else {
            let point = ds.len() as i64 + self.e;
            if point > 0 {
                format!("{}.{}", &ds[..point as usize], &ds[point as usize..])
            } else {
                format!("0.{}{ds}", "0".repeat((-point) as usize))
            }
        };
        if neg { format!("-{body}") } else { body }
    }

    /// The significant digits without trailing zeros, and the sign:
    /// what the scientific notation shows ("9.9999998").
    pub fn mantissa(&self) -> String {
        let neg = self.m.sign() == Sign::Minus;
        let ds = self.m.abs().to_str_radix(10);
        let ds = ds.trim_end_matches('0');
        let ds = if ds.is_empty() { "0" } else { ds };
        let body = if ds.len() > 1 { format!("{}.{}", &ds[..1], &ds[1..]) } else { ds.to_string() };
        if neg { format!("-{body}") } else { body }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> Dec {
        Dec::parse(s).unwrap()
    }

    #[test]
    fn rounding_is_half_even() {
        assert_eq!(d("2.5").round(1).unwrap().to_plain(), "2");
        assert_eq!(d("3.5").round(1).unwrap().to_plain(), "4");
        assert_eq!(d("-2.5").round(1).unwrap().to_plain(), "-2");
        assert_eq!(d("999.5").round(3).unwrap().to_plain(), "1000");
        assert_eq!(d("1.25").round(2).unwrap().to_plain(), "1.2");
        assert_eq!(d("1.2501").round(2).unwrap().to_plain(), "1.3");
    }

    #[test]
    fn division_is_correctly_rounded() {
        assert_eq!(d("1").div(&d("3"), 5).unwrap().to_plain(), "0.33333");
        assert_eq!(d("2").div(&d("3"), 5).unwrap().to_plain(), "0.66667");
        assert_eq!(d("1").div(&d("8"), 2).unwrap().to_plain(), "0.12"); // 0.125: an exact tie, to even
        assert_eq!(d("-10").div(&d("4"), 34).unwrap().to_plain(), "-2.5");
        assert_eq!(d("1").div(&d("0"), 34).unwrap_err(), MathError::DivisionByZero);
    }

    #[test]
    fn overflow_past_pythons_emax() {
        let big = Dec { m: BigInt::from(9), e: EMAX };
        assert_eq!(big.mul(&d("10"), 34).unwrap_err(), MathError::Overflow);
    }

    #[test]
    fn parse_takes_what_the_calculator_types() {
        assert_eq!(d("0.").to_plain(), "0");
        assert_eq!(d("-0.50").to_plain(), "-0.50");
        assert_eq!(d(".25").to_plain(), "0.25");
        assert!(Dec::parse("-").is_none());
        assert!(Dec::parse("1e5").is_none());
    }
}
