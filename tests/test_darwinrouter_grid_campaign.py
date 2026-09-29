import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import darwinrouter_grid_campaign as c


class GridCampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = next(t for t in c.load_catalog() if t["id"] == "grid_research_l1")
        self.job = dict(
            key="local-worker/" + self.task["id"],
            model="local-worker",
            task=self.task["id"],
            phase="objective_direct",
            domain="research",
            level=1,
        )
        self.m = dict(
            campaign_dir=str(self.root),
            database="unused",
            darwin="unused",
            capabilities=[dict(eligible=True, id="local-worker", provider="p", model="native")],
            jobs=[self.job],
        )
        self.d = c.attempt_dir(self.root, self.job, 1)
        self.d.mkdir(parents=True)
        req = c.request_for(self.task, self.job["model"])
        c.save(self.d.parent / (self.d.name + ".request.json"), req)
        h = c.sha(self.d.parent / (self.d.name + ".request.json"))
        self.output = dict(
            result=dict(
                TaskID="actual",
                PreviousTaskIDs=["failed-first"],
                Text=json.dumps(self.task["_oracle"]["expected"]),
                FinishReason="stop",
                Turns=1,
            ),
            request_sha256=h,
            finished_at="2026-09-29T00:00:00Z",
        )
        self.meta = dict(
            model_id="native",
            provider_id="p",
            domain="research",
            profile="benchmark-v1",
            privacy="local_only",
            context_tokens=32768,
            messages=[
                dict(
                    role="user",
                    content=req["prompt"] + "\n\n[routing-grid request sha256=" + h + "]",
                )
            ],
        )
        self.history = [
            dict(kind="task.started", data=self.meta),
            dict(kind="task.completed", data={}),
        ]
        self.dispatch = dict(
            stage="dispatch",
            model="native",
            provider="p",
            mode="text",
            request_sha256=h,
            advertised=["completion"],
            media_bytes=0,
        )

    def write(self):
        c.save(self.d / "host-result.json", self.output)
        (self.d / "dispatch.jsonl").write_text(json.dumps(self.dispatch) + "\n")

    def validate(self):
        self.write()
        with (
            patch.object(c, "events", return_value=self.history),
            patch.object(c, "verify_zero_feedback"),
        ):
            return c.validate_completion(self.m, self.job, self.task, self.d)

    def test_matched_plan_and_capability_exclusions(self):
        caps = [
            dict(id="text", model="t", provider="p", eligible=True, advertised=["completion"]),
            dict(
                id="audio",
                model="a",
                provider="p",
                eligible=True,
                advertised=["completion", "audio"],
            ),
            dict(id="cloud", eligible=False, advertised=["completion"]),
            dict(id="local-glm-ocr", eligible=False, advertised=["vision"]),
        ]
        jobs, unavailable = c.plan(c.load_catalog(), caps)
        self.assertEqual(138, len(jobs))  # 2*42 text + 6 audio + 48 auto
        self.assertEqual(12, len(unavailable))
        self.assertTrue(all(j["model"] in {"text", "audio", "auto"} for j in jobs))
        self.assertTrue(all(j["model"] != "text" for j in jobs if j["domain"] == "audio"))
        for model in ("text", "audio", "auto"):
            for domain in (
                "research",
                "data_analysis",
                "reasoning",
                "workflow",
                "translation",
                "writing",
                "creative",
            ):
                self.assertEqual(
                    list(range(1, 7)),
                    [j["level"] for j in jobs if j["model"] == model and j["domain"] == domain],
                )
        auto, unavailable = c.plan(c.load_catalog(), caps, False)
        self.assertEqual(48, len(auto))
        self.assertTrue(all(j["model"] == "auto" for j in auto))

    def test_request_never_contains_oracle_or_audio_transcript(self):
        for t in c.load_catalog():
            r = c.request_for(t, "auto")
            self.assertNotIn("_oracle", r["prompt"])
            self.assertNotIn("reference_sql", r["prompt"])
            if t["category"] == "audio":
                self.assertEqual(t["assets"][0]["sha256"], r["audio_sha256"])
                for _, text in t["_oracle"]["transcript"]:
                    self.assertNotIn(text, r["prompt"])

    def test_completed_and_failed_execution_are_separate(self):
        *_, grade, ok = self.validate()
        self.assertTrue(ok)
        self.assertEqual("pass", grade["verdict"])
        self.output["error"] = "context overflow"
        self.history[-1]["kind"] = "task.failed"
        *_, grade, ok = self.validate()
        self.assertFalse(ok)
        self.assertEqual("infrastructure", grade["verdict"])
        self.assertFalse(grade["quality_eligible"])

    def test_scope_identity_and_delivery_guards(self):
        for key, value in [
            ("domain", "general"),
            ("profile", "default"),
            ("privacy", "cloud_allowed"),
            ("context_tokens", 8192),
            ("model_id", "wrong"),
        ]:
            with self.subTest(key=key):
                before = self.meta[key]
                self.meta[key] = value
                with self.assertRaises(RuntimeError):
                    self.validate()
                self.meta[key] = before
        self.dispatch["advertised"] = ["vision"]
        with self.assertRaisesRegex(RuntimeError, "dispatch"):
            self.validate()
        self.dispatch["advertised"] = ["completion"]
        self.dispatch["media_bytes"] = 100
        with self.assertRaisesRegex(RuntimeError, "media"):
            self.validate()

    def test_failed_lineage_check_is_required_before_grading(self):
        self.write()
        with patch.object(c, "verify_zero_feedback", side_effect=RuntimeError("bad lineage")):
            with self.assertRaisesRegex(RuntimeError, "bad lineage"):
                c.validate_completion(self.m, self.job, self.task, self.d)

    def test_feedback_is_idempotent_and_owned_by_actual_model_scope(self):
        self.write()
        with (
            patch.object(c, "events", return_value=self.history),
            patch.object(c, "verify_zero_feedback"),
            patch.object(c, "record_feedback") as writer,
        ):
            head = dict(
                ID="head",
                Key=dict(Model="native", Provider="p", Domain="research", Profile="benchmark-v1"),
                ExecutionSucceeded=True,
                Checks=[dict(Source="user_feedback", Passed=True)],
            )
            with patch.object(c, "current_feedback", return_value=[head]):
                record, grade = c.reconcile(self.m, self.job, self.task, self.d)
                c.reconcile(self.m, self.job, self.task, self.d)
                writer.assert_not_called()
                self.assertEqual(1, len(c.rows(self.root / "feedback-verifications.jsonl")))
                head["Key"]["Domain"] = "wrong"
                with self.assertRaisesRegex(RuntimeError, "ownership"):
                    c.ensure_feedback(self.m, record, grade)
            with patch.object(
                c,
                "current_feedback",
                side_effect=[
                    [],
                    [
                        dict(
                            head,
                            Key=dict(
                                Model="native",
                                Provider="p",
                                Domain="research",
                                Profile="benchmark-v1",
                            ),
                        )
                    ],
                ],
            ):
                c.ensure_feedback(self.m, record, grade)
                writer.assert_called_once()

    def test_pending_review_never_writes_quality_feedback(self):
        record = dict(row=dict(status="completed", darwin_task_id="task", previous_task_ids=[]))
        with (
            patch.object(c, "verify_zero_feedback") as verify,
            patch.object(c, "record_feedback") as writer,
        ):
            self.assertIsNone(c.ensure_feedback(self.m, record, dict(quality_eligible=False)))
            writer.assert_not_called()
            verify.assert_called_with("unused", ["task"])

    def test_canonical_tampering_blocks_without_inference(self):
        self.write()
        with (
            patch.object(c, "events", return_value=self.history),
            patch.object(c, "verify_zero_feedback"),
            patch.object(c, "ensure_feedback", return_value=None),
        ):
            c.reconcile(self.m, self.job, self.task, self.d)
            record = json.loads((self.d / "record.json").read_text())
            record["response"] = "changed"
            c.save(self.d / "record.json", record)
            with self.assertRaisesRegex(RuntimeError, "canonical"):
                c.reconcile(self.m, self.job, self.task, self.d)

    def test_ambiguous_launch_never_replays(self):
        c.save(self.root / "manifest.json", self.m)
        with (
            patch.object(c, "verify_provenance"),
            patch.object(c, "load_catalog", return_value=[self.task]),
            patch.object(c.subprocess, "Popen") as launch,
        ):
            with self.assertRaisesRegex(RuntimeError, "ambiguous launch"):
                c.run(self.root)
            launch.assert_not_called()

    def test_no_third_attempt_after_two_failures(self):
        c.save(self.root / "manifest.json", self.m)
        for n in (1, 2):
            d = c.attempt_dir(self.root, self.job, n)
            d.mkdir(parents=True, exist_ok=True)
            c.save(d / "host-result.json", {})
        with (
            patch.object(c, "verify_provenance"),
            patch.object(c, "load_catalog", return_value=[self.task]),
            patch.object(c, "capacity_deferral", return_value=False),
            patch.object(
                c, "reconcile", return_value=(dict(row=dict(status="infrastructure")), {})
            ) as reconcile,
            patch.object(c, "snapshot"),
            patch.object(c.subprocess, "Popen") as launch,
        ):
            c.run(self.root)
            self.assertEqual(2, reconcile.call_count)
            launch.assert_not_called()
            self.assertEqual(
                "needs_infrastructure_review",
                json.loads((self.root / "state.json").read_text())["status"],
            )
            self.assertFalse(c.attempt_dir(self.root, self.job, 3).exists())

    def test_zero_dispatch_capacity_proof_and_hash_binding(self):
        db = self.root / "db"
        self.m["database"] = str(db)
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE events(sequence INTEGER,body BLOB)")
        self.output["result"] = {}
        self.output["error"] = "local resource capacity unavailable"
        self.write()
        (self.d / "dispatch.jsonl").unlink()
        self.assertTrue(c.capacity_deferral(self.m, self.job, self.d))
        next_dir = c.attempt_dir(self.root, self.job, 1)
        self.assertEqual("attempt-1-admission-1", next_dir.name)
        self.output["error"] += " changed"
        c.save(self.d / "host-result.json", self.output)
        with self.assertRaisesRegex(RuntimeError, "binding"):
            c.attempt_dir(self.root, self.job, 1)

    def test_capacity_hold_with_durable_task_cannot_be_retried(self):
        db = self.root / "db"
        self.m["database"] = str(db)
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE events(sequence INTEGER,body BLOB)")
            con.execute("INSERT INTO events VALUES(1,?)", (json.dumps(self.history[0]),))
        self.output["result"] = {}
        self.output["error"] = "local resource capacity unavailable"
        self.write()
        (self.d / "dispatch.jsonl").unlink()
        with self.assertRaisesRegex(RuntimeError, "durable task"):
            c.capacity_deferral(self.m, self.job, self.d)


if __name__ == "__main__":
    unittest.main()
