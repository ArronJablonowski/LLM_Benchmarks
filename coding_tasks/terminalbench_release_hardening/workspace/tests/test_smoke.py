import tempfile
import unittest
from pathlib import Path

from packsmith import build_archive


class ArchiveSmokeTests(unittest.TestCase):
    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "out.tar.gz"
            result = build_archive(root, output, dry_run=True)
            self.assertTrue(result["dry_run"]); self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
