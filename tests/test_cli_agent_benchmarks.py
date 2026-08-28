import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cli_agent_benchmarks as runner  # noqa: E402
import openai_compatible_benchmarks as openai_runner  # noqa: E402


class CliAgentBenchmarksTests(unittest.TestCase):
    def test_pi_command_disables_state_and_tools(self):
        command = runner.harness_command("pi", "fixture:latest", "prompt", Path("/tmp/config"))
        self.assertIn("--no-session", command)
        self.assertIn("--no-tools", command)
        self.assertIn("--no-context-files", command)
        self.assertEqual("prompt", command[-1])

    def test_goose_command_disables_profile_and_bounds_turns(self):
        command = runner.harness_command("goose", "fixture:latest", "prompt", Path("/tmp/config"))
        self.assertIn("--no-session", command)
        self.assertIn("--no-profile", command)
        self.assertEqual("1", command[command.index("--max-turns") + 1])

    def test_openhands_command_uses_isolated_workspace(self):
        command = runner.harness_command(
            "openhands", "fixture:latest", "prompt", Path("/tmp/config"),
            "/tmp/openhands-python", Path("/tmp/openhands-workspace"),
        )
        self.assertEqual("/tmp/openhands-python", command[0])
        self.assertEqual("fixture:latest", command[command.index("--model") + 1])
        self.assertEqual(
            "/tmp/openhands-workspace", command[command.index("--workspace") + 1]
        )

    def test_timeout_cannot_exceed_suite_limit(self):
        args = runner.parse_args([
            "--harness", "pi", "--models-file", "models.tsv",
            "--output-dir", "out", "--workspace", "work", "--timeout", "1800",
        ])
        self.assertEqual(1800, args.timeout)
        self.assertEqual("standard", args.suite)

    def test_named_standard_suite_is_accepted_by_generic_runners(self):
        cli_args = runner.parse_args([
            "--suite", "standard", "--harness", "pi",
            "--models-file", "models.tsv", "--output-dir", "out",
            "--workspace", "work",
        ])
        self.assertEqual("standard", cli_args.suite)
        api_args = openai_runner.parse_args([
            "--suite", "standard", "--endpoint", "http://127.0.0.1:1",
            "--model", "fixture", "--model-digest", "digest",
            "--model-runner", "fixture", "--runner-version", "1",
            "--output-dir", "out", "--server-pid", "1",
        ])
        self.assertEqual("standard", api_args.suite)

    def test_openai_request_is_non_streaming_and_deterministic(self):
        command = openai_runner.request_command(
            "http://127.0.0.1:8000/v1/chat/completions", "fixture", "prompt", 1800
        )
        payload = command[command.index("--data-binary") + 1]
        self.assertIn('"stream": false', payload)
        self.assertIn('"temperature": 0', payload)

    def test_openai_response_extracts_assistant_content(self):
        raw = '{"choices":[{"message":{"content":"BENCH_OK"}}]}'
        self.assertEqual("BENCH_OK", openai_runner.response_text(raw))


if __name__ == "__main__":
    unittest.main()
