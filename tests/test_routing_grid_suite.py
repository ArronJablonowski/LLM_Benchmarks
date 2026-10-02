"""Offline positive/negative grading checks; no models or inference servers."""

import copy
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import routing_grid_suite as suite
from benchmark_tests import suite_task_catalog
from routing_grid_benchmarks import main


class RoutingGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = suite.load_catalog()
        cls.by_id = {t["id"]: t for t in cls.tasks}

    def task(self, category, level=1):
        return copy.deepcopy(self.by_id[f"grid_{category}_l{level}"])

    def test_all_unmeasured_cards_have_six_levels_and_separate_scope(self):
        self.assertEqual(60, len(self.tasks))
        self.assertEqual(60, len(suite_task_catalog("routing-grid")))
        for category in suite.CATEGORIES:
            tasks = [t for t in self.tasks if t["category"] == category]
            self.assertEqual(list(range(1, 7)), [t["level"] for t in tasks])
            self.assertEqual(6, len({t["challenge"] for t in tasks}))
            for t in tasks:
                self.assertEqual(category, t["domain"])
                self.assertEqual("benchmark-v1", t["evidence_profile"])
        for category, count in [
            ("standard", 18),
            ("coding", 9),
            ("creative", 6),
            ("commandline", 20),
            ("ocr", 30),
        ]:
            self.assertEqual(count, len(suite_task_catalog(category)))

    def test_exact_answers_and_every_wrong_field(self):
        for t in self.tasks:
            g = t["_oracle"]
            if g["kind"] != "exact_json":
                continue
            answer = g["expected"]
            self.assertEqual("pass", suite.assess(t, json.dumps(answer))["verdict"], t["id"])
            for field in answer:
                wrong = copy.deepcopy(answer)
                wrong[field] = {"invented": "incorrect"}
                self.assertEqual(
                    "content_mismatch",
                    suite.assess(t, json.dumps(wrong))["verdict"],
                    (t["id"], field),
                )

    def test_json_contract_rejects_fences_duplicate_keys_extra_types_and_nan(self):
        t = self.task("reasoning")
        correct = json.dumps(t["_oracle"]["expected"])
        for bad in [
            "```json\n" + correct + "\n```",
            correct + " trailing",
            '{"boxes":34,"boxes":34}',
            '{"boxes":34,"extra":true}',
            '{"boxes":34.0}',
            '{"boxes":"34"}',
            '{"boxes":NaN}',
            '{"boxes":true}',
            "[]",
            "null",
            "{",
            "[" * 2000,
            "x" * 65537,
        ]:
            self.assertEqual("content_mismatch", suite.assess(t, bad)["verdict"], bad[:70])

    def test_independent_reasoning_optima(self):
        t = self.task("reasoning", 4)
        projects = t["inputs"]["projects"]
        feasible = []
        for count in range(len(projects) + 1):
            for selected in itertools.combinations(projects, count):
                if (
                    sum(p["cost"] for p in selected) <= 10
                    and sum(p["staff"] for p in selected) <= 5
                ):
                    feasible.append(
                        (sum(p["value"] for p in selected), [p["id"] for p in selected])
                    )
        best = sorted(feasible, key=lambda x: (-x[0], x[1]))[0]
        self.assertEqual(t["_oracle"]["expected"], dict(projects=best[1], value=best[0]))
        t = self.task("reasoning", 6)
        best = min(
            t["inputs"]["policies"],
            key=lambda p: (
                -min(p["dry"] - p["premium"], p["wet"] - p["premium"]),
                p["premium"],
                p["id"],
            ),
        )
        self.assertEqual(best["id"], t["_oracle"]["expected"]["policy"])
        # Unique precedence and room constraints enumerated independently.
        orders = [
            list(p)
            for p in itertools.permutations("ABCD")
            if p.index("A") < p.index("C") < p.index("B") < p.index("D")
        ]
        self.assertEqual([self.task("reasoning", 2)["_oracle"]["expected"]["order"]], orders)
        assignments = [
            dict(zip(["Ada", "Bo", "Cy"], p))
            for p in itertools.permutations([1, 2, 3])
            if p[0] == 2 and p[1] < p[0]
        ]
        self.assertEqual([self.task("reasoning", 3)["_oracle"]["expected"]], assignments)

    @unittest.skipUnless(sys.version_info >= (3, 11), "bounded SQL requires Python 3.11+")
    def test_sql_equivalent_queries_and_known_answers(self):
        expected = [
            [[101], [102], [104], [105], [107]],
            [["east", 3000], ["west", 1600]],
            [["east", 2700], ["north", 0], ["west", 900]],
            [["east", 101, 1200], ["east", 104, 1200], ["west", 102, 900]],
            [[101, "refunded"], [102, "paid"], [104, "paid"], [105, "refunded"], [107, "paid"]],
            [
                ["2025-01-01", 2100, 300, 1800, 1800],
                ["2025-01-02", 1200, 0, 1200, 3000],
                ["2025-01-03", 1300, 700, 600, 3600],
            ],
        ]
        for level, rows in enumerate(expected, 1):
            t = self.task("data_analysis", level)
            sql = t["_oracle"]["reference_sql"]
            self.assertEqual(rows, suite.query_rows(t["inputs"]["tables"], sql))
            grade = suite.assess(t, json.dumps(dict(sql=sql)))
            self.assertEqual("pass", grade["verdict"], grade)
            self.assertEqual(3, grade["checks_passed"])
        # Different syntax, same behavior is accepted.
        t = self.task("data_analysis")
        self.assertEqual(
            "pass",
            suite.assess(
                t,
                json.dumps(
                    dict(sql="SELECT id FROM orders WHERE status IN ('paid') ORDER BY id ASC")
                ),
            )["verdict"],
        )

    @unittest.skipUnless(sys.version_info >= (3, 11), "bounded SQL requires Python 3.11+")
    def test_sql_rejects_hardcodes_fanout_mutation_and_resource_abuse(self):
        t = self.task("data_analysis")
        for sql in [
            "SELECT 101 UNION ALL SELECT 102 UNION ALL SELECT 104 UNION ALL SELECT 105 UNION ALL SELECT 107",
            "DELETE FROM orders",
            "ATTACH DATABASE '/tmp/grid-must-not-exist.db' AS x",
            "SELECT load_extension('no')",
            "SELECT randomblob(1000000000)",
            "WITH RECURSIVE x(s) AS (SELECT 'x' UNION ALL SELECT s||s FROM x) SELECT length(s) FROM x",
            "SELECT 1; SELECT 2",
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT sum(n) FROM x",
        ]:
            self.assertEqual(
                "content_mismatch", suite.assess(t, json.dumps(dict(sql=sql)))["verdict"], sql
            )
        t = self.task("data_analysis", 3)
        bad = "SELECT c.region, SUM(o.cents-COALESCE(r.cents,0)) FROM customers c JOIN orders o ON c.id=o.customer_id LEFT JOIN refunds r ON r.order_id=o.id WHERE status='paid' GROUP BY c.region ORDER BY c.region"
        self.assertEqual("content_mismatch", suite.assess(t, json.dumps(dict(sql=bad)))["verdict"])

    def test_workflow_effects_retries_and_compensation(self):
        for level in range(1, 7):
            t = self.task("workflow", level)
            calls = t["_oracle"]["reference_calls"]
            grade = suite.assess(t, json.dumps(dict(calls=calls)))
            self.assertEqual("pass", grade["verdict"], grade)
            for i in range(len(calls)):
                self.assertEqual(
                    "content_mismatch",
                    suite.assess(t, json.dumps(dict(calls=calls[:i] + calls[i + 1 :])))["verdict"],
                    (level, i),
                )
            self.assertEqual(
                "content_mismatch",
                suite.assess(t, json.dumps(dict(calls=calls + [calls[-1]])))["verdict"],
            )
        t = self.task("workflow", 4)
        grade = suite.assess(t, json.dumps(dict(calls=t["_oracle"]["reference_calls"])))
        self.assertEqual(
            ["charged", "none", "confirmed"], [r["effect"] for r in grade["simulation_trace"]]
        )
        for category, level in [("workflow", 4), ("workflow", 6)]:
            t = self.task(category, level)
            calls = t["_oracle"]["reference_calls"]
            calls[1]["cents"] = 4000
            self.assertEqual(
                "content_mismatch", suite.assess(t, json.dumps(dict(calls=calls)))["verdict"]
            )
        t = self.task("workflow", 5)
        calls = t["_oracle"]["reference_calls"]
        calls[3]["version"] = True
        self.assertEqual(
            "content_mismatch", suite.assess(t, json.dumps(dict(calls=calls)))["verdict"]
        )

    def test_export_excludes_private_answers_and_real_audio_is_frozen(self):
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "export"
            self.assertEqual(0, main(["--export", str(dest)]))
            self.assertEqual(60, len(list(dest.iterdir())))
            for t in self.tasks:
                p = json.loads((dest / t["id"] / "task.json").read_text())
                self.assertFalse(any(k.startswith("_") for k in p))
                self.assertNotIn("reference_sql", json.dumps(p))
                self.assertNotIn("reference_calls", json.dumps(p))
                self.assertNotIn("transcript", p)
                for a in t["assets"]:
                    path = dest / t["id"] / a["path"]
                    self.assertEqual(a["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                    with wave.open(str(path)) as f:
                        self.assertEqual(
                            (1, 2, 16000), (f.getnchannels(), f.getsampwidth(), f.getframerate())
                        )
                        self.assertGreater(f.getnframes(), 32000)
                    self.assertEqual({}, p["inputs"])
                    for _, spoken in t["_oracle"]["transcript"]:
                        self.assertNotIn(spoken, json.dumps(p))

    def test_capabilities_never_substitute_vision_for_generation_or_tts_for_input(self):
        for c in ("image_generation", "video_generation"):
            self.assertFalse(suite.eligible(self.task(c), ["vision", "image", "ocr", "chat"]))
            self.assertTrue(suite.eligible(self.task(c), [c]))
        self.assertFalse(
            suite.eligible(self.task("audio"), ["text_to_speech", "audio_generation", "chat"])
        )
        self.assertTrue(suite.eligible(self.task("audio"), ["transcription"]))
        for t in self.tasks:
            self.assertFalse(suite.eligible(t, None))
            self.assertFalse(suite.eligible(t, []))

    def test_failed_execution_or_unknown_capability_never_becomes_quality(self):
        for status in [
            "timeout",
            "context_overflow",
            "provider_error",
            "stream_error",
            "admission_error",
            "disqualified",
        ]:
            t = self.task("reasoning")
            result = suite.assess(t, json.dumps(t["_oracle"]["expected"]), status=status)
            self.assertEqual(
                "disqualified" if status == "disqualified" else "infrastructure", result["verdict"]
            )
            self.assertFalse(result["quality_eligible"])
            self.assertFalse(result["feedback_written"])

    def test_text_human_reviews_bound_to_exact_submission_and_rubric(self):
        t = self.task("translation")
        response = "El museo abre a las 09:00. La entrada es gratuita."
        result = suite.assess(t, response)
        self.assertEqual("needs_review", result["verdict"])
        self.assertFalse(result["quality_eligible"])
        review = dict(
            binding=result["review_binding"],
            reviewer="offline-test-reviewer",
            scores={
                d: dict(score=3, evidence="Example evaluator evidence for this dimension.")
                for d in t["_oracle"]["dimensions"]
            },
        )
        self.assertEqual("pass", suite.assess(t, response, review=review)["verdict"])
        review["scores"]["semantic_fidelity"]["score"] = 2
        self.assertEqual("content_mismatch", suite.assess(t, response, review=review)["verdict"])
        self.assertEqual(
            "grader_error", suite.assess(t, response + " Changed.", review=review)["verdict"]
        )
        for bad in [[], {}, dict(review, reviewer=""), dict(review, scores={})]:
            self.assertEqual("grader_error", suite.assess(t, response, review=bad)["verdict"])
        t = self.task("translation", 4)
        self.assertEqual("content_mismatch", suite.assess(t, "再開してください。")["verdict"])
        self.assertEqual(
            "needs_review",
            suite.assess(
                t,
                "バックアップは削除ではなく一時停止されています。Resumeを選択してください。Resetを選択しないでください。",
            )["verdict"],
        )
        self.assertEqual(
            "content_mismatch", suite.assess(self.task("writing"), "Too short.")["verdict"]
        )

    def test_image_must_decode_and_still_requires_semantic_review(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("optional Pillow not installed")
        t = self.task("image_generation")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "output.png"
            response = json.dumps(dict(artifact="output.png"))
            path.write_text("this is not an image")
            self.assertEqual(
                "content_mismatch", suite.assess(t, response, workspace=root)["verdict"]
            )
            Image.new("RGB", (512, 512), "black").save(path)
            result = suite.assess(t, response, workspace=root)
            self.assertEqual("needs_review", result["verdict"])  # a blank image never auto-passes
            review = dict(
                binding=result["review_binding"],
                reviewer="test",
                scores={
                    k: dict(score=0, evidence="Blank image has no requested mug.")
                    for k in t["_oracle"]["dimensions"]
                },
            )
            self.assertEqual(
                "content_mismatch",
                suite.assess(t, response, workspace=root, review=review)["verdict"],
            )
            Image.new("RGB", (512, 512), "red").save(path)
            self.assertEqual(
                "grader_error", suite.assess(t, response, workspace=root, review=review)["verdict"]
            )
            Image.new("RGB", (32, 32)).save(path)
            self.assertEqual(
                "content_mismatch", suite.assess(t, response, workspace=root)["verdict"]
            )
            (root / "alias.png").symlink_to(path)
            self.assertEqual(
                "content_mismatch",
                suite.assess(t, '{"artifact":"alias.png"}', workspace=root)["verdict"],
            )
            self.assertEqual(
                "content_mismatch",
                suite.assess(t, '{"artifact":"../outside.png"}', workspace=root)["verdict"],
            )
            self.assertEqual("grader_error", suite.assess(t, response)["verdict"])

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"), "optional ffmpeg unavailable"
    )
    def test_video_decode_duration_and_human_gate(self):
        t = self.task("video_generation")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / "clip.mp4"
            response = '{"artifact":"clip.mp4"}'
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=512x512:r=12",
                    "-t",
                    "4",
                    "-an",
                    "-c:v",
                    "mpeg4",
                    str(path),
                ],
                check=True,
                timeout=20,
            )
            self.assertEqual("needs_review", suite.assess(t, response, workspace=root)["verdict"])
            path.write_text("fake video")
            self.assertEqual(
                "content_mismatch", suite.assess(t, response, workspace=root)["verdict"]
            )
        with patch("routing_grid_suite.shutil.which", return_value=None):
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "clip.mp4").write_bytes(b"fake")
                self.assertEqual(
                    "grader_error", suite.assess(t, response, workspace=root)["verdict"]
                )

    def test_fixture_tamper_and_fresh_catalog(self):
        for relative in [
            "tasks/grid_research_l1.json",
            "oracles/grid_research_l1.json",
            "assets/audio_l1.wav",
        ]:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d) / "fixtures"
                shutil.copytree(suite.FIXTURES, root)
                with (root / relative).open("ab") as f:
                    f.write(b" ")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    suite.load_catalog(root)
        a = suite.load_catalog()
        a[0]["prompt"] = "mutated"
        self.assertNotEqual(a[0]["prompt"], suite.load_catalog()[0]["prompt"])

    def test_authoring_source_matches_frozen_non_audio_cases(self):
        import generate_routing_grid_fixtures as author

        cases = (
            list(author.research())
            + list(author.data_analysis())
            + list(author.reasoning())
            + list(author.workflow())
            + list(author.translation())
            + list(author.media("image_generation"))
            + list(author.media("video_generation"))
            + list(author.writing_creative("writing"))
            + list(author.writing_creative("creative"))
        )
        self.assertEqual(54, len(cases))
        for task, oracle in cases:
            stored = self.by_id[task["id"]]
            self.assertEqual(task, {k: v for k, v in stored.items() if not k.startswith("_")})
            self.assertEqual(oracle, stored["_oracle"])

    def test_human_checkpoints_for_all_language_tasks(self):
        for category in ("translation", "writing", "creative"):
            for level in range(1, 7):
                t = self.task(category, level)
                g = t["_oracle"]
                # Structural success is deliberately insufficient for a quality pass.
                tokens = list(g["protected"])
                tokens += ["placeholder"] * max(1, g["word_range"][0] - len(tokens))
                candidate = " ".join(tokens)
                self.assertEqual("needs_review", suite.assess(t, candidate)["verdict"], t["id"])
                review = dict(
                    binding=suite.review_binding(t, candidate),
                    reviewer="test",
                    scores={
                        k: dict(score=1, evidence="Placeholder prose does not satisfy the brief.")
                        for k in g["dimensions"]
                    },
                )
                self.assertEqual(
                    "content_mismatch", suite.assess(t, candidate, review=review)["verdict"]
                )

    def test_changed_grader_binding_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "fixtures"
            shutil.copytree(suite.FIXTURES, root)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["grader_sha256"] = "0" * 64
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "grading source hash"):
                suite.load_catalog(root)

    def test_summary_keeps_domains_levels_and_pending_reviews_separate(self):
        a = suite.assess(self.task("reasoning"), '{"boxes":34}')
        b = suite.assess(self.task("translation"), "Una respuesta.")
        rows = suite.summary([a, b])
        self.assertEqual(2, len(rows))
        self.assertEqual(
            {"needs_review": 1}, next(r for r in rows if r["domain"] == "translation")["counts"]
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            suite.summary([a, a])
        with self.assertRaisesRegex(ValueError, "mixed"):
            suite.summary([dict(a, grader_version="old")])


if __name__ == "__main__":
    unittest.main()
