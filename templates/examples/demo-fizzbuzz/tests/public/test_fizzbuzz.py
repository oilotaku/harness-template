"""公開測試（DEMO-001）——會交給 implementer，讓他知道基本輸入輸出格式。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implementation"))

from fizzbuzz import fizzbuzz  # noqa: E402


class TestFizzBuzzPublic(unittest.TestCase):
    def test_standard_sequence_up_to_15(self):
        expected = [
            "1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8", "Fizz", "Buzz",
            "11", "Fizz", "13", "14", "FizzBuzz",
        ]
        self.assertEqual(fizzbuzz(15), expected)

    def test_all_items_are_strings(self):
        for item in fizzbuzz(15):
            self.assertIsInstance(item, str)

    def test_zero_returns_empty_list(self):
        self.assertEqual(fizzbuzz(0), [])

    def test_negative_raises_value_error(self):
        with self.assertRaises(ValueError):
            fizzbuzz(-1)


if __name__ == "__main__":
    unittest.main()
