import unittest

from eventer.routing import send_event


class RoutingTests(unittest.TestCase):
    def test_stdout(self):
        self.assertEqual('OUT:{"a":1}', send_event({"a": 1}, "stdout"))


if __name__ == "__main__":
    unittest.main()
