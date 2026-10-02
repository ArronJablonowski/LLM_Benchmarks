import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import darwinrouter_coding_benchmarks as coding
import darwinrouter_standard_benchmarks as standard


class DarwinFeedbackTests(unittest.TestCase):
    def test_coding_lost_acknowledgement_checks_durable_history(self):
        history = [{"TaskID": "task", "Checks": [{"Source": "user_feedback", "Passed": False}]}]
        with patch.object(coding.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("darwin", 150),
            subprocess.CompletedProcess([], 0, json.dumps(history), ""),
        ]) as run:
            coding.record_feedback("darwin", Path("test.db"), "task", False)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[1].args[0][1:3], ["feedback", "show"])

    def test_coding_timeout_during_history_can_retry(self):
        with patch.object(coding.subprocess, "run", side_effect=[
            subprocess.TimeoutExpired("darwin", 150),
            subprocess.TimeoutExpired("darwin", 150),
            subprocess.CompletedProcess([], 0, "", ""),
        ]) as run, patch.object(coding.time, "sleep"):
            coding.record_feedback("darwin", Path("test.db"), "task", True)
        self.assertEqual(run.call_count, 3)

    def test_coding_recovery_rejects_conflicting_or_malformed_history(self):
        histories = [
            [{"TaskID": "other", "Checks": [{"Source": "user_feedback", "Passed": True}]}],
            [{"TaskID": "task", "Checks": [
                {"Source": "user_feedback", "Passed": True},
                {"Source": "user_feedback", "Passed": False},
            ]}],
            ["invalid"],
            {"invalid": "history"},
        ]
        for history in histories:
            with self.subTest(history=history), patch.object(coding.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 1, "", "rejected"),
                subprocess.CompletedProcess([], 0, json.dumps(history), ""),
            ] * 3), patch.object(coding.time, "sleep"):
                with self.assertRaisesRegex(RuntimeError, "rejected"):
                    coding.record_feedback("darwin", Path("test.db"), "task", True)

    def test_standard_feedback_only_uses_valid_grades_and_resumes_unicode(self):
        task = {"id": "one", "category": "coding", "name": "Test", "prompt": "test"}
        for verdict in ("pass", "content_mismatch", "fail", "grader_error", "ungraded"):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as tmp:
                calls = []

                def request(url, token, **kwargs):
                    calls.append((url, kwargs))
                    if url.endswith("/v1/health"):
                        return 200, {"ready": True}
                    if url.endswith("/v1/tasks"):
                        return 200, {"task_id": "task", "text": "before\u2028after", "route_estimated_cost": 0}
                    return 200, {}

                sampler = Mock()
                sampler.get_since.return_value = []
                with patch.dict(standard.os.environ, {"DARWIN_TEST_TOKEN": "x" * 32}), \
                     patch.object(standard, "suite_task_catalog", return_value=[task]), \
                     patch.object(standard, "create_sampler", return_value=sampler), \
                     patch.object(standard, "request_json", side_effect=request), \
                     patch.object(standard, "grade_task", return_value={"verdict": verdict}):
                    args = ["--output-dir", tmp, "--token-env", "DARWIN_TEST_TOKEN", "--run"]
                    self.assertEqual(standard.main(args), 0)
                    # A literal Unicode line separator in a JSON string must
                    # not split one preserved observation into two JSON rows.
                    self.assertEqual(standard.main(args), 0)
                feedback = [kw for url, kw in calls if url.endswith("/v1/feedback")]
                self.assertEqual(len(feedback), int(verdict in ("pass", "content_mismatch", "fail")))
                if feedback:
                    self.assertEqual(feedback[0]["payload"]["outcome"], "accepted" if verdict == "pass" else "rejected")
                self.assertEqual(sum(url.endswith("/v1/tasks") for url, _ in calls), 1)
                row = json.loads((Path(tmp) / "darwinrouter_standard.jsonl").read_text())["row"]
                self.assertEqual(row["feedback_recorded"], str(verdict in ("pass", "content_mismatch", "fail")).lower())


if __name__ == "__main__":
    unittest.main()
