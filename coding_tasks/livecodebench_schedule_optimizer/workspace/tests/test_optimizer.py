import unittest

from scheduler import optimize


class OptimizerSmokeTests(unittest.TestCase):
    def test_simple(self):
        jobs = [{"id": "a", "start": 0, "end": 2, "value": 3}, {"id": "b", "start": 2, "end": 3, "value": 4}]
        self.assertEqual({"jobs": ["a", "b"], "total_value": 7}, optimize(jobs))


if __name__ == "__main__":
    unittest.main()
