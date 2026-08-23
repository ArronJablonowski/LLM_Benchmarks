import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hermes_agent_17_test_benchmarks as hermes
import ollama_standardized_local_benchmarks as direct
import openclaw_18_test_benchmarks as openclaw


class TaskSelectionTests(unittest.TestCase):
    def capture(self, function, arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(arguments)
        self.assertEqual(0, result)
        return output.getvalue().strip().splitlines()

    def assert_single_listing(self, runner):
        lines = self.capture(runner.main, ["--test", "ocrbench_mini", "--list-tests"])
        self.assertEqual(1, len(lines))
        columns = lines[0].split("\t")
        self.assertEqual("ocrbench_mini", columns[0])
        self.assertEqual("vision_ocr", columns[2])

    def test_all_runners_list_and_select_the_same_individual_task(self):
        self.assert_single_listing(direct)
        self.assert_single_listing(hermes)
        self.assert_single_listing(openclaw)

    def test_listing_all_core_tasks_is_consistent(self):
        listings = []
        for runner in (direct, hermes, openclaw):
            lines = self.capture(runner.main, ["--list-tasks"])
            listings.append([line.split("\t", 1)[0] for line in lines])
        self.assertEqual(18, len(listings[0]))
        self.assertEqual(listings[0], listings[1])
        self.assertEqual(listings[0], listings[2])

    def test_unknown_task_fails_before_runtime_contact(self):
        for runner in (direct, hermes):
            with self.subTest(runner=runner.__name__), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(["--test", "does_not_exist", "--list-tasks"])
        with self.assertRaisesRegex(RuntimeError, "Unknown task ids"):
            openclaw.main(["--test", "does_not_exist", "--list-tasks"])

    def test_campaign_wrapper_lists_without_campaign_side_effects(self):
        env = dict(os.environ)
        env["BENCH_REPO_DIR"] = str(ROOT)
        proc = subprocess.run(
            ["bash", str(ROOT / "ops" / "run_standard_three_path_campaign.sh"), "--list-tests"],
            text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(18, len(proc.stdout.strip().splitlines()))
        selected = subprocess.run(
            ["bash", str(ROOT / "ops" / "run_standard_three_path_campaign.sh"),
             "--test", "math500_mini", "--list-tests"],
            text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertEqual(0, selected.returncode, selected.stderr)
        self.assertEqual("math500_mini", selected.stdout.split("\t", 1)[0])

    def test_campaign_wrapper_discovers_managed_openclaw_in_service_path(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            managed_bin = home / ".openclaw" / "bin"
            managed_bin.mkdir(parents=True)
            openclaw_bin = managed_bin / "openclaw"
            openclaw_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            openclaw_bin.chmod(0o755)
            python_probe = home / "python-probe"
            python_probe.write_text(
                "#!/bin/sh\ncommand -v openclaw\n",
                encoding="utf-8",
            )
            python_probe.chmod(0o755)
            env = {
                "HOME": str(home),
                "PATH": "/usr/bin:/bin",
                "BENCH_REPO_DIR": str(ROOT),
                "BENCH_PYTHON": str(python_probe),
            }
            proc = subprocess.run(
                ["bash", str(ROOT / "ops" / "run_standard_three_path_campaign.sh"),
                 "--list-tests"],
                text=True, capture_output=True, timeout=30, env=env,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(str(openclaw_bin), proc.stdout.strip())

    def test_campaign_wrapper_blocks_uncontrolled_openclaw_native_context(self):
        source = (ROOT / "ops" / "run_standard_three_path_campaign.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("terminally_account_openclaw_context_uncontrolled", source)
        self.assertIn("openclaw_native_context > openclaw_safe_context", source)
        self.assertIn("no-verified-per-model-context-control", source)

    def test_campaign_wrapper_reconciles_fully_accounted_completion(self):
        source = (ROOT / "ops" / "run_standard_three_path_campaign.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("all_campaign_paths_accounted", source)
        self.assertIn("completion-reconciliation.log", source)
        self.assertIn("status=0", source)

    def test_campaign_wrapper_rejects_task_marker_mixing(self):
        with tempfile.TemporaryDirectory() as directory:
            campaign = Path(directory)
            markers = campaign / "markers"
            markers.mkdir()
            (markers / "direct-fixture.done").write_text("fixture\n", encoding="utf-8")
            env = dict(os.environ)
            env.update({"BENCH_REPO_DIR": str(ROOT), "BENCH_CAMPAIGN_DIR": str(campaign)})
            proc = subprocess.run(
                ["bash", str(ROOT / "ops" / "run_standard_three_path_campaign.sh"),
                 "--test", "math500_mini"],
                text=True, capture_output=True, timeout=30, env=env,
            )
            self.assertEqual(2, proc.returncode)
            self.assertIn("frozen for task selection all-core", proc.stderr)


if __name__ == "__main__":
    unittest.main()
