import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import generate_coding_report as report


class CodingReportTests(unittest.TestCase):
    def test_report_accepts_only_coding_rows_and_keeps_runner_harness_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "pi_coding.csv"
            fields = ["benchmark_suite", "benchmark_profile", "model", "model_runner", "model_runner_version", "harness", "harness_version", "task_id", "category", "verdict", "checks_passed", "checks_total", "wall_seconds"]
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                writer.writerow({"benchmark_suite": "coding", "benchmark_profile": "coding-agent-v2-web", "model": "model-a", "model_runner": "ollama", "model_runner_version": "0.12.0", "harness": "pi-coding-agent", "harness_version": "0.50.0", "task_id": "web_accessible_incident_dashboard", "category": "web_frontend_accessibility", "verdict": "pass", "checks_passed": "5", "checks_total": "5", "wall_seconds": "10"})
                writer.writerow({"benchmark_suite": "standard", "model": "must-not-appear", "model_runner": "ollama", "harness": "pi", "task_id": "exact_reply", "verdict": "pass", "checks_passed": "1", "checks_total": "1", "wall_seconds": "1"})
            rows = report.load_rows([root]); self.assertEqual(1, len(rows))
            document = report.generate(rows)
            self.assertIn("Coding Agent Benchmark Report", document)
            self.assertIn("model-a", document); self.assertIn("pi-coding-agent", document)
            self.assertIn("0.12.0", document); self.assertIn("0.50.0", document)
            self.assertIn("Strengths", document); self.assertIn("Weaknesses / unresolved", document)
            self.assertIn("Web projects", document); self.assertIn("1/3 web projects", document)
            self.assertIn("Coverage", document); self.assertIn("1/9 projects observed", document)
            self.assertNotIn("must-not-appear", document)


if __name__ == "__main__":
    unittest.main()
