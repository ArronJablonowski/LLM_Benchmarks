import contextlib
import io
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_tests import suite_task_catalog
import creative_agent_benchmarks as creative
import ollama_standardized_local_benchmarks as direct


class CreativeSuiteTests(unittest.TestCase):
    def test_creative_suite_is_separate_and_human_only(self):
        standard = suite_task_catalog("standard"); coding = suite_task_catalog("coding"); tasks = suite_task_catalog("creative")
        self.assertEqual(6, len(tasks))
        task_ids = {task["id"] for task in tasks}
        self.assertFalse(task_ids & {task["id"] for task in standard + coding})
        self.assertTrue(all(task["grading"] == {"kind": "human"} for task in tasks))
        self.assertTrue(all("grader" not in task for task in tasks))
        self.assertEqual(
            {"creative_website", "creative_image", "creative_threejs", "creative_motion_web", "creative_nextjs_animation", "creative_microinteractions"},
            {task["category"] for task in tasks},
        )

    def test_every_brief_has_a_fixture_and_human_rubric(self):
        for task in suite_task_catalog("creative"):
            with self.subTest(task=task["id"]):
                self.assertTrue((ROOT / task["fixture"]).is_dir())
                self.assertGreaterEqual(len(task["review_dimensions"]), 5)
                self.assertGreaterEqual(len(task["deliverables"]), 2)
        next_task = next(task for task in suite_task_catalog("creative") if task["category"] == "creative_nextjs_animation")
        manifest = (ROOT / next_task["fixture"] / "package.json").read_text(encoding="utf-8")
        self.assertIn('"next": "16.3.3"', manifest)
        self.assertIn('"motion": "13.1.1"', manifest)

    def test_listing_and_plan_are_read_only_and_explicitly_human(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ["--suite", "creative", "--harness", "pi", "--models-file", str(root / "missing.tsv"), "--output-dir", str(root / "out"), "--workspace", str(root / "work")]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, creative.main(args + ["--list-tasks"]))
            self.assertEqual(6, len(output.getvalue().strip().splitlines()))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, creative.main(args))
            self.assertIn("Human review only", output.getvalue())
            self.assertEqual([], list(root.iterdir()))

    def test_artifact_inventory_ignores_generated_dependency_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "index.html").write_text("ok", encoding="utf-8")
            (root / "node_modules").mkdir(); (root / "node_modules" / "ignored.js").write_text("x", encoding="utf-8")
            (root / ".next").mkdir(); (root / ".next" / "ignored.js").write_text("x", encoding="utf-8")
            self.assertEqual(["index.html"], creative.artifact_inventory(root))

    def test_legacy_profile_cannot_resume_and_no_automated_score_field_exists(self):
        with self.assertRaisesRegex(RuntimeError, "new output directory"):
            creative.validate_existing_records([{"row": {"benchmark_profile": "creative-human-v0"}}])
        self.assertNotIn("verdict", creative.FIELDS)
        self.assertNotIn("score", creative.FIELDS)
        self.assertIn("review_status", creative.FIELDS)

    def test_prompt_response_runner_rejects_creative_suite(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            direct.main(["--suite", "creative", "--list-tasks"])


if __name__ == "__main__":
    unittest.main()
