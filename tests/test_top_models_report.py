import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "dashboard" / "generate_top_models_report.py"
DATA_DIR = ROOT / "data" / "top_models_report"


class TopModelsReportTests(unittest.TestCase):
    def test_generator_builds_self_contained_interactive_report(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "report.html"
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--data-dir",
                    str(DATA_DIR),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            summary = json.loads(result.stdout)
            report = output.read_text(encoding="utf-8")
            manifest = json.loads(output.with_suffix(".manifest.json").read_text())

            self.assertEqual(summary["output"], str(output.resolve()))
            self.assertIn('id="verbose-toggle"', report)
            self.assertIn('id="cloud-toggle"', report)
            self.assertIn('id="ranking-data"', report)
            self.assertIn('id="spark"', report)
            self.assertIn('id="studio"', report)
            self.assertIn('id="mini"', report)
            self.assertIn('data-cloud="true"', report)
            self.assertIn('data-cloud="false"', report)
            self.assertNotIn("<script src=", report)
            self.assertNotIn("<link rel=", report)
            self.assertEqual(set(manifest["hosts"]), {"spark", "studio", "mini"})
            self.assertTrue(all(host["export_sha256"] for host in manifest["hosts"].values()))


if __name__ == "__main__":
    unittest.main()
