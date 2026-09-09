"""隱藏測試（DEMO-001）——只有 verifier-reviewer 拿得到，implementer 看不到。

刻意涵蓋公開測試沒測到的 n，用來戳破「查表寫死 1~15」這種取巧實作
（例如 if n == 15: return [硬編碼列表]）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "implementation"))

from fizzbuzz import fizzbuzz  # noqa: E402


class TestFizzBuzzHidden(unittest.TestCase):
    def test_n_17_not_in_public_tests(self):
        result = fizzbuzz(17)
        self.assertEqual(len(result), 17)
        self.assertEqual(result[16], "17")  # 17 不是 3 或 5 的倍數
        self.assertEqual(result[14], "FizzBuzz")  # 第 15 項

    def test_large_n_1000_spot_checks(self):
        result = fizzbuzz(1000)
        self.assertEqual(len(result), 1000)
        self.assertEqual(result[999], "Buzz")  # 1000 是 5 的倍數、非 3 的倍數
        self.assertEqual(result[899], "FizzBuzz")  # 900 是 15 的倍數
        self.assertEqual(result[0], "1")

    def test_n_1_edge_case(self):
        self.assertEqual(fizzbuzz(1), ["1"])

    def test_repeated_calls_are_consistent(self):
        # 行為一致性測試：同輸入應得到同輸出，排除任何隨機/有狀態的取巧實作
        self.assertEqual(fizzbuzz(30), fizzbuzz(30))


if __name__ == "__main__":
    unittest.main()
