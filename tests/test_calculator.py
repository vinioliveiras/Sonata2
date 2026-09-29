"""Calculator arithmetic (python3 -m unittest tests.test_calculator)."""
import unittest

from sonata2.calculator.engine import Engine


def run(keys):
    e = Engine()
    for k in keys:
        e.press(k)
    return e.display()


class EngineTest(unittest.TestCase):
    def test_cases(self):
        for keys, shown in (("12+3×4=", "24"), ("2+3×", "3"), ("2×3+", "6"), ("5÷0=", "Error"),
                            ("200+10%=", "220"), ("2+3==", "8"), ("1÷3=", "0.333333333333"),
                            ("1234567×1000=", "1,234,567,000"), ("99999999×99999999=", "9.9999998e15"),
                            ("5±", "-5"), ("0.1+0.2=", "0.3"), ("12⌫", "1"), ("2×+3=", "5"), ("50%", "0.5"),
                            ("7C", "0")):
            self.assertEqual(run(keys), shown, keys)

    def test_clear_label(self):
        e = Engine()
        self.assertEqual(e.clear_label, "AC")
        e.press("5")
        self.assertEqual(e.clear_label, "C")


if __name__ == "__main__":
    unittest.main()
