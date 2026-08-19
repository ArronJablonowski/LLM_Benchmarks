import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hermes_agent_17_test_benchmarks as hermes_runner


class HermesAgentRunnerTests(unittest.TestCase):
    def model(self):
        return {
            "name":"fixture:latest","digest":"sha256:fixture",
            "requested_num_ctx":8192,
            "treatments":[{
                "treatment_key":"thinking-off","treatment_role":"off",
                "thinking_resolved":"disabled",
            }],
        }

    def record(self):
        return {
            "metadata":{"run_id":"fixture-run","runner_sha256":"old"},
            "row":{
                "run_id":"fixture-run","runner_sha256":"old",
                "context_plan_sha256":"plan","model":"fixture:latest",
                "model_digest":"sha256:fixture","requested_num_ctx":8192,
                "treatment_key":"thinking-off","hermes_reasoning_requested":"none",
                "task_id":"exact_reply","status":"ok","verdict":"pass",
            },
            "assistant_text":"BENCH_OK\n",
            "resource_guard":{
                "watchdog_triggered":False,"memory_recovery_verified":True,
                "watchdog_join_verified":True,"infrastructure_error":"",
            },
        }

    def write(self, records):
        handle=tempfile.NamedTemporaryFile("w",delete=False,encoding="utf-8")
        with handle:
            for record in records:
                handle.write(json.dumps(record)+"\n")
        return Path(handle.name)

    def test_resume_retains_valid_canonical_row(self):
        path=self.write([self.record()])
        try:
            records,rows,completed=hermes_runner._load_resume_records(
                path,"plan",[self.model()],[hermes_runner.TEXT_TASKS[0]]
            )
        finally:
            path.unlink()
        self.assertEqual(1,len(records)); self.assertEqual(1,len(rows))
        self.assertEqual({("fixture:latest","thinking-off","exact_reply")},completed)

    def test_resume_rejects_unsafe_guard_and_duplicates(self):
        unsafe=self.record(); unsafe["resource_guard"]["infrastructure_error"]="bad"
        path=self.write([unsafe])
        try:
            with self.assertRaisesRegex(RuntimeError,"resource_guard"):
                hermes_runner._load_resume_records(
                    path,"plan",[self.model()],[hermes_runner.TEXT_TASKS[0]]
                )
        finally:
            path.unlink()
        duplicate=self.record(); path=self.write([duplicate,duplicate])
        try:
            with self.assertRaisesRegex(RuntimeError,"duplicates"):
                hermes_runner._load_resume_records(
                    path,"plan",[self.model()],[hermes_runner.TEXT_TASKS[0]]
                )
        finally:
            path.unlink()

    def test_resolved_high_is_preserved_for_mistral_style_control(self):
        model=self.model()
        model["treatments"][0].update({
            "treatment_key":"thinking-on","treatment_role":"on",
            "thinking_resolved":"high",
        })
        self.assertEqual("high",hermes_runner._treatments(model)[0]["hermes_reasoning"])


if __name__ == "__main__":
    unittest.main()
