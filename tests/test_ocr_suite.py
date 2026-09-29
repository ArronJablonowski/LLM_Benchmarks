import base64
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from accuracy_grading import grade_task
from benchmark_tests import suite_task_catalog
from ocr_grading import distance
from vision_benchmark_support import prepare_ocr_assets, task_image_base64
import ollama_standardized_local_benchmarks as direct
import openclaw_18_test_benchmarks as openclaw


class OCRSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = suite_task_catalog("ocr")

    def test_thirty_unique_frozen_images_six_ordered_levels(self):
        self.assertEqual(30, len(self.tasks))
        self.assertEqual([n for n in range(1, 7) for _ in range(5)],
                         [t["difficulty_level"] for t in self.tasks])
        hashes = set()
        for task in self.tasks:
            raw = base64.b64decode(task_image_base64(task))
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual((1200, 1550), (int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')))
            self.assertEqual(task['image_sha256'], hashlib.sha256(raw).hexdigest())
            hashes.add(task['image_sha256'])
        self.assertEqual(30, len(hashes))
        self.assertEqual(18, len(suite_task_catalog('standard')))

    def test_reference_answers_pass_and_each_corrupted_field_fails(self):
        for task in self.tasks:
            expected = task['ocr_grader']['expected']
            self.assertEqual('pass', grade_task(task, 'ok', json.dumps(expected))['verdict'])
            for key in expected:
                wrong = dict(expected); wrong[key] += ' invented'
                grade = grade_task(task, 'ok', json.dumps(wrong))
                self.assertEqual('content_mismatch', grade['verdict'], (task['id'], key))
                self.assertIn(key, grade['failures'])

    def test_strict_schema_rejects_prose_fences_duplicates_extra_fields_types(self):
        task = self.tasks[0]; answer = task['ocr_grader']['expected']; encoded = json.dumps(answer)
        for bad in ['```json\n'+encoded+'\n```', 'Answer: '+encoded, encoded+' trailing',
                    encoded[:-1]+',"room":"205"}', json.dumps(dict(answer, extra='x')),
                    json.dumps(dict(answer, room=205)), '[]', 'null', '{}', '{"room":NaN}']:
            self.assertEqual('content_mismatch', grade_task(task, 'ok', bad)['verdict'], bad)

    def test_only_whitespace_and_unicode_composition_normalized(self):
        task = self.tasks[0]; answer = dict(task['ocr_grader']['expected'])
        answer['body'] = answer['body'].replace(' ', '\n  ')
        self.assertEqual('pass', grade_task(task,'ok',json.dumps(answer))['verdict'])
        answer['body'] = answer['body'].replace('BR-48', 'BR-4B')
        grade = grade_task(task, 'ok', json.dumps(answer))
        self.assertEqual('content_mismatch',grade['verdict'])
        self.assertEqual(1,grade['transcription_metrics']['body']['character_edits'])
        self.assertGreater(grade['transcription_metrics']['body']['wer'],0)
        self.assertEqual(3,distance('kitten','sitting'))

    def test_infrastructure_and_capability_skips_never_pass(self):
        for status in ['timeout', 'error', 'context_overflow']:
            task=self.tasks[0]
            self.assertEqual('fail',grade_task(task,status,json.dumps(task['ocr_grader']['expected']))['verdict'])
        self.assertEqual('skip',grade_task(self.tasks[0],'skip','',skipped=True)['verdict'])

    def test_redaction_requires_abstention_and_table_math_not_injected_text(self):
        form = next(t for t in self.tasks if t['id']=='ocr_l6_02_form')
        self.assertEqual('unreadable',form['ocr_grader']['expected']['authorization'])
        bad=dict(form['ocr_grader']['expected'], authorization='Approved')
        self.assertEqual('content_mismatch',grade_task(form,'ok',json.dumps(bad))['verdict'])
        table=next(t for t in self.tasks if t['id']=='ocr_l6_03_table')
        self.assertEqual(str(13*25+16*23+24*16),table['ocr_grader']['expected']['east_total_usd'])
        bad=dict(table['ocr_grader']['expected'],east_total_usd='9999')
        self.assertEqual('content_mismatch',grade_task(table,'ok',json.dumps(bad))['verdict'])

    def test_image_tampering_and_path_escape_rejected(self):
        task=copy.deepcopy(self.tasks[0]);task['image_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash'): task_image_base64(task)
        missing=copy.deepcopy(self.tasks[0]);missing.pop('image_asset')
        with self.assertRaisesRegex(ValueError,'missing'): task_image_base64(missing)
        task['image_asset']='../../outside.png'
        with self.assertRaisesRegex(ValueError,'inside'): task_image_base64(task)
        task['image_asset']='/tmp/outside.png'
        with self.assertRaisesRegex(ValueError,'inside'): task_image_base64(task)

    def test_assets_are_per_task_and_gateway_attaches_correct_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            tasks=[self.tasks[0],self.tasks[-1]]
            assets=prepare_ocr_assets(tasks,Path(temp))
            self.assertNotEqual(assets[tasks[0]['id']]['sha256'],assets[tasks[1]['id']]['sha256'])
            for task in tasks:
                asset=assets[task['id']]
                cmd=openclaw.build_gateway_image_command('fixture',task,60,None,asset)
                params=json.loads(cmd[cmd.index('--params')+1])
                self.assertEqual(task_image_base64(task),params['attachments'][0]['content'])
                self.assertNotIn('ocr_grader',json.dumps(params))

    def test_direct_payload_has_actual_distinct_images_without_answer_key(self):
        with patch.object(direct,'stream_generate',return_value={'status':'ok','thinking':''}) as send:
            for task in [self.tasks[0],self.tasks[-1]]:
                direct.run_task({'name':'fixture','capabilities':['vision']},task,60,'http://localhost')
                payload=send.call_args.args[1]
                self.assertEqual([task_image_base64(task)],payload['images'])
                self.assertEqual(task['prompt'],payload['prompt'])
                self.assertNotIn('ocr_grader',payload)
                self.assertNotIn('image_asset',payload)
        with patch.object(direct,'stream_generate') as send:
            result=direct.run_task({'name':'text','capabilities':['completion']},self.tasks[0],60,'http://localhost')
            self.assertEqual('skip',result['status']);send.assert_not_called()

    def test_oversized_response_is_bounded(self):
        self.assertEqual('content_mismatch',grade_task(self.tasks[0],'ok','x'*32001)['verdict'])

    def test_deep_json_is_a_format_mismatch_not_a_grader_crash(self):
        self.assertEqual('content_mismatch', grade_task(self.tasks[0], 'ok', '['*1500+']'*1500)['verdict'])

    def test_summary_excludes_infrastructure_and_checks_image_binding(self):
        from summarize_ocr_results import summarize
        records=[]
        for index, status in enumerate(['ok', 'ok', 'error', 'skip']):
            task=self.tasks[index]
            text=json.dumps(task['ocr_grader']['expected']) if index==0 else '{}'
            records.append({'row':{'run_id':'fixture','model':'vision-fixture', 'task_id':task['id'], 'status':status},
                            'grading':grade_task(task,status,text,skipped=status=='skip'),
                            'image_evidence':{'sha256':task['image_sha256']}})
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'results.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in records))
            result=summarize([path])['by_model_and_level'][0]
            self.assertEqual((1,1,1,1),(result['pass'],result['mismatch'],result['infrastructure'],result['skip']))
            self.assertEqual(0.5,result['pass_rate'])
            self.assertEqual(1,result['format_mismatches'])
            self.assertEqual(1,result['transcriptions_scored'])
            with self.assertRaisesRegex(ValueError,'duplicate'):
                summarize([path,path])
            records[0]['image_evidence']['sha256']='invalid'
            path.write_text(''.join(json.dumps(r)+'\n' for r in records))
            with self.assertRaisesRegex(ValueError,'image evidence'):
                summarize([path])

    def test_catalog_is_fresh(self):
        tasks=suite_task_catalog('ocr');tasks[0]['prompt']='mutated'
        self.assertNotEqual('mutated',suite_task_catalog('ocr')[0]['prompt'])

    def test_all_vision_runners_list_ocr_without_contacting_a_provider(self):
        for runner in ['ollama_standardized_local_benchmarks.py','hermes_agent_17_test_benchmarks.py','openclaw_18_test_benchmarks.py']:
            result=subprocess.run([sys.executable,str(ROOT/'scripts'/runner),'--suite','ocr','--list-tasks'],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual(30,len(result.stdout.strip().splitlines()),result.stdout)


if __name__=='__main__': unittest.main()
