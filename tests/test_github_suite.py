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
import coding_agent_benchmarks as runner
from generate_github_suite import build_task, build_workflow, command_paths


class GitHubSuiteTests(unittest.TestCase):
    def test_pinned_reference_is_complete_and_separate(self):
        tasks = suite_task_catalog("github")
        manifest = json.loads((ROOT / "scripts/benchmark_tests/github/coverage.json").read_text())
        self.assertGreaterEqual(len(tasks), 200)
        self.assertEqual(
            manifest["commands"] + ["workflow:" + name for name in manifest["workflows"]],
            [task["command_path"] for task in tasks],
        )
        self.assertEqual(len(tasks), len({task["id"] for task in tasks}))
        self.assertFalse({task["id"] for task in tasks} & {
            task["id"] for task in suite_task_catalog("commandline", full=True)
        })
        self.assertTrue(all(task["lab"]["commands"] for task in tasks))
        self.assertTrue(all(
            (build_workflow(task["command_path"].split(":", 1)[1]) if task["command_path"].startswith("workflow:")
             else build_task(task["command_path"])) == task for task in tasks
        ))
        self.assertEqual("gh repo delete", next(task for task in tasks if task["id"] == "gh_repo_delete")["command_path"])

    def test_reference_parser_selects_leaf_commands(self):
        page = "<h2>gh repo &lt;command&gt;</h2><h3>gh repo list [flags]</h3><h3>gh api &lt;endpoint&gt;</h3>"
        self.assertEqual(["gh repo list", "gh api", "gh help", "gh --version"], command_paths(page))

    def test_plan_and_list_do_not_run_or_create_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = ["--suite", "github", "--harness", "pi", "--models-file", str(root / "models.tsv"),
                    "--output-dir", str(root / "out"), "--workspace", str(root / "work")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, runner.main(args))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, runner.main(args + ["--list-tasks"]))
            self.assertEqual(len(suite_task_catalog("github")), len(output.getvalue().splitlines()))
            self.assertEqual([], list(root.iterdir()))

    def test_baseline_fails_and_offline_golden_passes(self):
        task = next(item for item in suite_task_catalog("github") if item["id"] == "gh_issue_close")
        with tempfile.TemporaryDirectory() as directory:
            workspace = runner.prepare_workspace(Path(directory), "fixture", "model", task)
            grader = [sys.executable, str(ROOT / task["grader"]), str(workspace)]
            baseline = subprocess.run(grader, capture_output=True, text=True, check=False)
            self.assertEqual("fail", json.loads(baseline.stdout.splitlines()[-1])["verdict"])
            command = task["grading"]["required_commands"][0]
            completed = subprocess.run([sys.executable, str(workspace / "github_lab.py"), "run", command],
                                       cwd=workspace, capture_output=True, text=True, check=False)
            self.assertEqual(0, completed.returncode)
            (workspace / "answer.json").write_text(json.dumps({
                "commands": [command],
                "findings": task["grading"]["required_findings"],
                "actions": ["Closed the fictional fixture issue in the simulator"],
            }))
            result = subprocess.run(grader, capture_output=True, text=True, check=False)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("pass", json.loads(result.stdout.splitlines()[-1])["verdict"])

    def test_simulator_never_executes_host_command(self):
        task = suite_task_catalog("github")[0]
        with tempfile.TemporaryDirectory() as directory:
            workspace = runner.prepare_workspace(Path(directory), "fixture", "model", task)
            marker = workspace / "must-not-exist"
            result = subprocess.run([sys.executable, str(workspace / "github_lab.py"), "run", f"touch {marker}"],
                                    cwd=workspace, capture_output=True, text=True, check=False)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(marker.exists())

    def test_workflow_enforces_order_and_passes_when_completed(self):
        task = next(item for item in suite_task_catalog("github") if item["id"] == "gh_workflow_pull_request_review")
        with tempfile.TemporaryDirectory() as directory:
            workspace = runner.prepare_workspace(Path(directory), "fixture", "model", task)
            simulator = [sys.executable, str(workspace / "github_lab.py"), "run"]
            commands = task["grading"]["required_commands"]
            premature = subprocess.run(simulator + [commands[-1]], cwd=workspace, capture_output=True, text=True)
            self.assertNotEqual(0, premature.returncode)
            for command in commands:
                result = subprocess.run(simulator + [command], cwd=workspace, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
            (workspace / "answer.json").write_text(json.dumps({
                "commands": commands,
                "findings": task["grading"]["required_findings"],
                "actions": ["Reviewed checks and merged only the fictional fixture PR"],
            }))
            result = subprocess.run([sys.executable, str(ROOT / task["grader"]), str(workspace)],
                                    capture_output=True, text=True)
            self.assertEqual("pass", json.loads(result.stdout.splitlines()[-1])["verdict"])


if __name__ == "__main__":
    unittest.main()
