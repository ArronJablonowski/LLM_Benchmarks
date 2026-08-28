import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_tests import suite_task_catalog
import coding_agent_benchmarks as coding
import ollama_standardized_local_benchmarks as direct


class CodingSuiteTests(unittest.TestCase):
    def test_coding_suite_is_separate_and_complete(self):
        standard = suite_task_catalog("standard")
        tasks = suite_task_catalog("coding")
        self.assertEqual(9, len(tasks))
        self.assertFalse({task["id"] for task in standard} & {task["id"] for task in tasks})
        self.assertFalse({task["prompt"] for task in standard} & {task["prompt"] for task in tasks})
        self.assertTrue(
            {"SWE-bench", "RepoBench", "LiveCodeBench", "BigCodeBench", "FeatureBench", "Terminal-Bench", "WebDev Accessibility", "Web Components", "Full-Stack Web"}
            <= {task["benchmark_origin"] for task in tasks}
        )

    def test_web_extension_covers_frontend_components_and_fullstack(self):
        web_tasks = [task for task in suite_task_catalog("coding") if task["category"].startswith("web_")]
        self.assertEqual(3, len(web_tasks))
        self.assertEqual(
            {"web_frontend_accessibility", "web_component_architecture", "web_fullstack_application"},
            {task["category"] for task in web_tasks},
        )
        self.assertTrue(all(any("tests" in practice for practice in task["best_practices"]) for task in web_tasks))
        self.assertIn("web_runtime_version", coding.FIELDS)

    def test_every_task_has_an_isolated_fixture_and_hidden_grader(self):
        for task in suite_task_catalog("coding"):
            with self.subTest(task=task["id"]):
                fixture = ROOT / task["fixture"]
                grader = ROOT / task["grader"]
                self.assertTrue(fixture.is_dir())
                self.assertTrue(grader.is_file())
                self.assertNotIn(grader.resolve(), fixture.resolve().parents)
                proc = subprocess.run(
                    [sys.executable, str(grader), str(fixture)],
                    text=True, capture_output=True, timeout=30,
                    env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
                )
                self.assertEqual(0, proc.returncode, proc.stderr)
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
                self.assertEqual("fail", payload["verdict"], "broken baseline must not pass")
                self.assertGreater(payload["total"], 0)

    def test_agent_commands_enable_real_coding_tools_and_bound_turns(self):
        direct_agent = coding.harness_command("ollama-direct", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertIn("ollama_workspace_agent.py", " ".join(direct_agent))
        hermes = coding.harness_command("hermes", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertIn("terminal,file,code_execution", hermes)
        self.assertNotIn("--ignore-user-config", hermes)
        self.assertEqual(hermes[hermes.index("--provider") + 1], "custom")
        openclaw = coding.harness_command("openclaw", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertIn("ollama/fixture", openclaw)
        pi = coding.harness_command("pi", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertNotIn("--no-tools", pi)
        goose = coding.harness_command("goose", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertEqual("100", goose[goose.index("--max-turns") + 1])
        openhands = coding.harness_command("openhands", "fixture", "prompt", Path("/tmp/work"), "python")
        self.assertIn("openhands_coding_agent.py", " ".join(openhands))

    def test_listing_and_plan_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ["--suite", "coding", "--harness", "pi", "--models-file", str(root / "missing.tsv"), "--output-dir", str(root / "out"), "--workspace", str(root / "work")]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, coding.main(args + ["--list-tasks"]))
            self.assertEqual(9, len(output.getvalue().strip().splitlines()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, coding.main(args))
            self.assertEqual([], list(root.iterdir()))

    def test_prompt_response_direct_runner_rejects_coding_suite(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            direct.main(["--suite", "coding", "--list-tasks"])

    def test_student_test_counter_includes_python_and_web_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = Path(directory) / "tests"; tests.mkdir()
            (tests / "test_api.py").touch(); (tests / "cart.test.mjs").touch()
            (tests / "helper.txt").touch()
            self.assertEqual(2, coding.count_student_tests(Path(directory)))

    def test_expanded_profile_rejects_legacy_resume_evidence(self):
        with self.assertRaisesRegex(RuntimeError, "new output directory"):
            coding.validate_existing_records([{"row": {"benchmark_profile": "coding-agent-v1"}}])
        coding.validate_existing_records([{"row": {"benchmark_profile": coding.PROFILE}}])


if __name__ == "__main__":
    unittest.main()
