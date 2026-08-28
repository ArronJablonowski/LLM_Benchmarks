import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_tests import (
    BenchmarkComponentError,
    core_task_catalog,
    list_core_components,
    suite_task_catalog,
)
from benchmark_tests import registry
import hermes_agent_17_test_benchmarks as hermes
import ollama_standardized_local_benchmarks as direct
import openclaw_18_test_benchmarks as openclaw


class BenchmarkComponentTests(unittest.TestCase):
    def test_standard_suite_is_the_existing_core_catalog(self):
        self.assertEqual(core_task_catalog(), suite_task_catalog("standard"))
        self.assertEqual(6, len(suite_task_catalog("coding")))
        with self.assertRaisesRegex(BenchmarkComponentError, "unknown benchmark suite"):
            suite_task_catalog("unknown")

    def test_every_core_test_has_its_own_component(self):
        tasks = core_task_catalog()
        components = list_core_components()
        self.assertEqual(18, len(tasks))
        self.assertEqual({task["id"] for task in tasks}, {path.stem for path in components})
        self.assertEqual([task["id"] for task in tasks], [
            "exact_reply", "simple_reasoning", "coding_micro", "ifeval_exact",
            "ifeval_json", "gsm8k_mini", "math500_mini", "mmlu_pro_security",
            "arc_challenge_mini", "hellaswag_mini", "truthfulqa_mini",
            "humaneval_mini", "mbpp_mini", "bfcl_mini", "ragas_mini",
            "prompt_injection_mini", "cyber_soc_mini", "ocrbench_mini",
        ])

    def test_all_harnesses_consume_fresh_registry_tasks(self):
        expected = core_task_catalog()
        self.assertEqual(expected, direct.TASKS)
        self.assertEqual(expected, hermes.TASKS)
        self.assertEqual(expected, openclaw.TASKS)
        first = core_task_catalog()
        first[0]["prompt"] = "mutated"
        self.assertNotEqual("mutated", core_task_catalog()[0]["prompt"])

    def test_component_id_must_match_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mismatch.json"
            path.write_text(json.dumps({
                "id": "other", "family": "Example", "category": "example",
                "name": "Example", "prompt": "Reply exactly OK",
                "grading": {"kind": "exact", "expected": "OK"},
            }), encoding="utf-8")
            with self.assertRaisesRegex(BenchmarkComponentError, "file name must match"):
                registry._load_descriptor(path)
