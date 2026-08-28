import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import coh_codex_standard_benchmarks as module  # noqa: E402


class COHCodexStandardTests(unittest.TestCase):
    def test_model_revision_is_domain_separated_and_stable(self):
        value = module.model_revision("gpt-5.6-sol")
        self.assertTrue(value.startswith("sha256:"))
        self.assertEqual(value, module.model_revision("gpt-5.6-sol"))
        self.assertNotEqual(value, module.model_revision("gpt-5.6-terra"))

    def test_parse_provenance_rejects_identity_drift(self):
        good = {"harness_version": "0.1.0", "model": "gpt-5.6-sol",
                "model_revision": module.model_revision("gpt-5.6-sol"),
                "capability_digest": "sha256:a", "binding_digest": "sha256:b",
                "provenance_digest": "sha256:c",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}
        parsed = module.parse_provenance(json.dumps(good), "gpt-5.6-sol", "0.1.0")
        self.assertEqual(parsed["usage"]["total_tokens"], 3)
        good["model"] = "gpt-5.6-terra"
        with self.assertRaises(RuntimeError):
            module.parse_provenance(json.dumps(good), "gpt-5.6-sol", "0.1.0")

    def test_selected_standard_profile_has_228_tasks(self):
        tasks = module.selected_tasks("standard-local", None)
        self.assertEqual(len(tasks), 228)
        self.assertEqual(len({task["id"] for task in tasks}), 228)

    def test_jsonl_record_split_does_not_split_unicode_line_separator(self):
        payload = json.dumps({"assistant_text": "before\u2028after"}, ensure_ascii=False) + "\n"
        records = [json.loads(line) for line in payload.split("\n") if line]
        self.assertEqual(records, [{"assistant_text": "before\u2028after"}])


if __name__ == "__main__":
    unittest.main()
