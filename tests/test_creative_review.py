import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import generate_creative_review as report


class CreativeReviewTests(unittest.TestCase):
    def test_report_accepts_only_creative_records_and_never_embeds_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); workspace = root / "work"; workspace.mkdir()
            (workspace / "index.html").write_text("<script>alert(1)</script>", encoding="utf-8")
            source = root / "pi_creative.jsonl"
            creative = {
                "row": {"benchmark_suite": "creative", "benchmark_profile": "creative-human-v1", "model": "model-a", "model_runner": "ollama", "harness": "pi-creative-agent", "task_id": "creative_brand_launch_site", "task_name": "Brand site", "creative_medium": "website design", "status": "submitted", "wall_seconds": 12, "workspace": str(workspace), "preview_entry": "index.html"},
                "artifacts": ["index.html"], "changed_artifacts": ["index.html"],
                "review_dimensions": ["originality", "visual craft"],
            }
            standard = {"row": {"benchmark_suite": "standard", "model": "must-not-appear"}}
            source.write_text(json.dumps(creative) + "\n" + json.dumps(standard) + "\n", encoding="utf-8")
            records = report.load_records([root]); self.assertEqual(1, len(records))
            document = report.generate(records)
            self.assertIn("Creative Benchmark Review", document)
            self.assertIn("Human-only evaluation", document)
            self.assertIn("originality", document); self.assertIn("visual craft", document)
            self.assertIn("Export human reviews", document)
            self.assertIn("creative-human-review-v1", document)
            self.assertNotIn("must-not-appear", document)
            self.assertNotIn("<iframe", document)
            self.assertNotIn("alert(1)", document)


if __name__ == "__main__":
    unittest.main()
