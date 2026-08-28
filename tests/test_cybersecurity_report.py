import csv
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dashboard"))
import generate_cybersecurity_report as report


class CybersecurityReportTests(unittest.TestCase):
    def test_report_accepts_only_cybersecurity_rows_and_shows_tracks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "pi_cybersecurity.csv"
            fields = ["benchmark_suite", "benchmark_profile", "model", "model_runner", "model_runner_version", "harness", "harness_version", "task_id", "track", "verdict", "checks_passed", "checks_total", "wall_seconds"]
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                writer.writerow({"benchmark_suite":"cybersecurity","benchmark_profile":"cybersecurity-agent-v1","model":"model-a","model_runner":"ollama","model_runner_version":"0.13","harness":"pi-cybersecurity-agent","harness_version":"0.51","task_id":"cyber_sigma_detection","track":"Detection engineering and SIEM","verdict":"pass","checks_passed":"8","checks_total":"8","wall_seconds":"12"})
                writer.writerow({"benchmark_suite":"coding","benchmark_profile":"coding-agent-v2-web","model":"must-not-appear","model_runner":"ollama","harness":"pi","task_id":"web_fullstack_kanban","verdict":"pass","checks_passed":"1","checks_total":"1","wall_seconds":"1"})
            rows = report.load_rows([root]); self.assertEqual(1, len(rows))
            document = report.generate(rows)
            self.assertIn("Cybersecurity Agent Benchmark Report", document)
            self.assertIn("model-a", document); self.assertIn("pi-cybersecurity-agent", document)
            self.assertIn("1/24 tasks", document); self.assertIn("Detection engineering and SIEM", document)
            self.assertIn("1/5", document); self.assertIn("not official", document)
            self.assertNotIn("must-not-appear", document)

    def test_exploitgym_is_reported_separately_with_full_profile_denominator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "exploitgym_cybersecurity.csv"
            fields = [
                "benchmark_suite", "benchmark_profile", "model", "model_runner",
                "harness", "task_id", "task_family", "flag_captured", "on_target",
                "profile_task_count", "wall_seconds",
            ]
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
                writer.writerow({
                    "benchmark_suite": "cybersecurity",
                    "benchmark_profile": "exploitgym-v1-hardened-e4123d0",
                    "model": "model-eg", "model_runner": "exploitgym-llm-proxy",
                    "harness": "exploitgym-codex", "task_id": "kernel:test/one",
                    "task_family": "kernel", "flag_captured": "true",
                    "on_target": "true", "profile_task_count": "20",
                    "wall_seconds": "42",
                })
            document = report.generate(report.load_rows([root]))
            self.assertIn("ExploitGym external profile", document)
            self.assertIn("model-eg", document)
            self.assertIn("1/20 tasks", document)
            self.assertIn("1/20 flags", document)
            self.assertIn("1/1", document)
            self.assertIn("No local cybersecurity-profile evidence found", document)


if __name__ == "__main__": unittest.main()
