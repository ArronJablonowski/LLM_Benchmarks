import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import coh_ollama_benchmarks as runner  # noqa: E402


class CohOllamaBenchmarksTests(unittest.TestCase):
    def test_command_binds_model_prompt_and_limits(self):
        command = runner.command(Path("/tmp/coh"), "fixture:latest", "prompt", 1800, 8192)
        self.assertEqual("fixture:latest", command[command.index("--model") + 1])
        self.assertEqual("prompt", command[command.index("--prompt") + 1])
        self.assertEqual("1800s", command[command.index("--timeout") + 1])

    def test_provenance_requires_exact_model_digest(self):
        payload = {
            "harness_version": "0.1.0", "model": "fixture:latest",
            "model_revision": "sha256:" + "a" * 64,
            "capability_digest": "sha256:" + "b" * 64,
            "binding_digest": "sha256:" + "c" * 64,
            "provenance_digest": "sha256:" + "d" * 64,
            "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        }
        parsed = runner.parse_provenance(
            json_line(payload), "fixture:latest", "a" * 64, "0.1.0"
        )
        self.assertEqual(3, parsed["usage"]["total_tokens"])
        with self.assertRaises(RuntimeError):
            runner.parse_provenance(json_line(payload), "fixture:latest", "e" * 64, "0.1.0")


def json_line(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
