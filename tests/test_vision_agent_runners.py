import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hermes_agent_17_test_benchmarks as hermes_runner
import openclaw_18_test_benchmarks as openclaw_runner
from ollama_standardized_local_benchmarks import make_text_png_base64
from vision_benchmark_support import materialize_ocr_asset, model_supports_vision


class VisionAgentRunnerTests(unittest.TestCase):
    def test_capability_gate_is_explicit_and_conservative(self):
        self.assertTrue(model_supports_vision({"capabilities": ["completion", "image"]}))
        self.assertTrue(model_supports_vision({"capabilities": ["VISION"]}))
        self.assertFalse(model_supports_vision({"capabilities": ["completion", "thinking"]}))
        self.assertFalse(model_supports_vision({}))

    def test_ocr_asset_is_preserved_with_hash_and_mime(self):
        task = next(task for task in hermes_runner.TASKS if task.get("requires_image"))
        encoded = make_text_png_base64(task["image_text"])
        with tempfile.TemporaryDirectory() as directory:
            evidence = materialize_ocr_asset(task, encoded, Path(directory))
            image = Path(evidence["path"])
            self.assertTrue(image.is_file())
            self.assertEqual("image/png", evidence["mime_type"])
            self.assertEqual(image.stat().st_size, evidence["bytes"])
            self.assertEqual(evidence, materialize_ocr_asset(task, encoded, Path(directory)))

    def test_openclaw_image_command_uses_gateway_attachment(self):
        task = next(task for task in openclaw_runner.TASKS if task.get("requires_image"))
        asset = {
            "path": "/tmp/ocr.png", "mime_type": "image/png",
            "base64": "cG5nLWZpeHR1cmU=",
        }
        command = openclaw_runner.build_gateway_image_command(
            "agent:main:test", task, 60, "high", asset,
        )
        self.assertEqual(["openclaw", "gateway", "call", "agent"], command[:4])
        params = json.loads(command[command.index("--params") + 1])
        self.assertEqual("agent:main:test", params["sessionKey"])
        self.assertEqual("high", params["thinking"])
        self.assertEqual("image", params["attachments"][0]["type"])
        self.assertEqual("cG5nLWZpeHR1cmU=", params["attachments"][0]["content"])

    def test_hermes_ocr_command_enables_only_vision_toolset(self):
        command = hermes_runner._hermes_command(
            Path("/python"), "fixture", "read image", "none", Path("/usage.json"),
            "custom", "vision",
        )
        self.assertEqual("vision", command[command.index("--toolsets") + 1])

    def test_openclaw_local_ollama_omits_unsupported_thinking_level(self):
        model = {
            "name": "thinking-local:latest",
            "family": "fixture",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True,
        }
        capable, cli_value, resolved = openclaw_runner.thinking_request_for_model(model, "auto")
        self.assertTrue(capable)
        self.assertIsNone(cli_value)
        self.assertEqual("provider-default/off", resolved)

    def test_openclaw_external_model_retains_requested_thinking_level(self):
        model = {
            "name": "gpt-fixture",
            "family": "openai",
            "capabilities": ["completion", "thinking"],
            "capabilities_known": True,
            "external": True,
        }
        capable, cli_value, resolved = openclaw_runner.thinking_request_for_model(model, "high")
        self.assertTrue(capable)
        self.assertEqual("high", cli_value)
        self.assertEqual("high", resolved)


if __name__ == "__main__":
    unittest.main()
