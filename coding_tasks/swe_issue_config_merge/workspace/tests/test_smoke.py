import unittest

from layered_config import merge


class MergeSmokeTests(unittest.TestCase):
    def test_scalar_overlay(self):
        self.assertEqual({"port": 9000}, merge({"port": 8000}, {"port": 9000}))


if __name__ == "__main__":
    unittest.main()
