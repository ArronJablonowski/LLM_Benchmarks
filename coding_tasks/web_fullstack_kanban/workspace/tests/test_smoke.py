import tempfile
import unittest
from pathlib import Path

from webboard import BoardStore


class BoardStoreTests(unittest.TestCase):
    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as directory:
            store = BoardStore(Path(directory) / "board.db")
            card = store.create("Ship release")
            self.assertEqual("todo", card["column"])
            self.assertEqual([card], store.list())


if __name__ == "__main__":
    unittest.main()
