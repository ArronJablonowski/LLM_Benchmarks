import unittest

from streamlog import process


class PipelineSmokeTests(unittest.TestCase):
    def test_empty(self):
        rows, summary, errors = process([])
        self.assertEqual([], list(rows))
        self.assertEqual({}, summary)
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
