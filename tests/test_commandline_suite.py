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
import coding_agent_benchmarks as project_runner


class CommandLineSuiteTests(unittest.TestCase):
    def test_suite_is_separate_complete_and_progressive(self):
        tasks = suite_task_catalog("commandline")
        self.assertEqual(20, len(tasks))
        standard = {task["id"] for task in suite_task_catalog("standard")}
        self.assertFalse(standard & {task["id"] for task in tasks})
        self.assertEqual("easy", tasks[0]["difficulty"])
        self.assertEqual("expert", tasks[-1]["difficulty"])
        self.assertEqual(
            {"Linux", "macOS", "Windows"},
            {task["platform"] for task in tasks if task["difficulty"] == "easy"},
        )

    def test_firewall_and_incident_response_coverage(self):
        tasks = suite_task_catalog("commandline")
        ids = {task["id"] for task in tasks}
        self.assertTrue(any("pfsense" in task_id for task_id in ids))
        self.assertTrue(any("openwrt" in task_id for task_id in ids))
        self.assertEqual(4, sum("incident_response" in task_id for task_id in ids))
        self.assertTrue(all(task["lab"]["commands"] for task in tasks))

    def test_workspace_injects_scenario_and_hidden_task_id(self):
        task = suite_task_catalog("commandline")[0]
        with tempfile.TemporaryDirectory() as directory:
            workspace = project_runner.prepare_workspace(
                Path(directory), "fixture", "model", task
            )
            self.assertEqual(task["lab"], json.loads((workspace / "scenario.json").read_text()))
            self.assertEqual(task["id"], (workspace / ".benchmark-task-id").read_text().strip())

    def test_simulator_never_executes_host_commands(self):
        task = suite_task_catalog("commandline")[0]
        with tempfile.TemporaryDirectory() as directory:
            workspace = project_runner.prepare_workspace(
                Path(directory), "fixture", "model", task
            )
            marker = workspace / "must-not-exist"
            proc = subprocess.run(
                [sys.executable, str(workspace / "terminal_lab.py"), "run", f"touch {marker}"],
                text=True, capture_output=True, cwd=workspace, check=False,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertFalse(marker.exists())

    def test_plan_and_listing_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = [
                "--suite", "commandline", "--harness", "pi",
                "--models-file", str(root / "models.tsv"),
                "--output-dir", str(root / "out"), "--workspace", str(root / "work"),
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, project_runner.main(args + ["--list-tasks"]))
            self.assertEqual(20, len(output.getvalue().strip().splitlines()))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, project_runner.main(args))
            self.assertEqual([], list(root.iterdir()))

    def test_three_path_wrapper_routes_commandline_runner(self):
        wrapper = (ROOT / "ops/run_ollama_project_three_path_campaign.sh").read_text()
        self.assertIn("commandline", wrapper)
        self.assertTrue((ROOT / "scripts/commandline_agent_benchmarks.py").is_file())

    def test_broken_baseline_fails_hidden_grader(self):
        task = suite_task_catalog("commandline")[0]
        with tempfile.TemporaryDirectory() as directory:
            workspace = project_runner.prepare_workspace(
                Path(directory), "fixture", "model", task
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / task["grader"]), str(workspace)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual("fail", payload["verdict"])

    def test_golden_easy_workflow_passes_hidden_grader(self):
        task = suite_task_catalog("commandline")[0]
        commands = ["uname -a", "df -h", "ps aux --sort=-%cpu"]
        with tempfile.TemporaryDirectory() as directory:
            workspace = project_runner.prepare_workspace(
                Path(directory), "fixture", "model", task
            )
            for command in commands:
                subprocess.run(
                    [sys.executable, str(workspace / "terminal_lab.py"), "run", command],
                    cwd=workspace, check=True, capture_output=True, text=True,
                )
            (workspace / "answer.json").write_text(json.dumps({
                "commands": commands,
                "findings": ["The /var disk is full", "python3 is the top CPU process"],
                "actions": ["Preserve logs, identify safe cleanup candidates, then recheck usage"],
                "menu_path": [],
            }), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / task["grader"]), str(workspace)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual("pass", payload["verdict"])


if __name__ == "__main__":
    unittest.main()
