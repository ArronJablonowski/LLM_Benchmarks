import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from darwinrouter_commandline_campaign import Campaign, append


class Simulated(Campaign):
    def __init__(self, path, verdicts=()):
        super().__init__(path)
        self.verdicts = iter(verdicts)
        self.launches = []
        self.feedback = []
    def launch(self, model, task, path, attempt):
        self.launches.append((task, attempt, path))
        path.mkdir(parents=True, exist_ok=True)
        append(path/'darwinrouter_commandline.jsonl', {'row': {'task_id':task, 'verdict':next(self.verdicts), 'darwin_task_id':f'{task}-{attempt}'}})
    def verify_feedback(self, path, row):
        self.feedback.append(row['darwin_task_id'])


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root/'receipt').write_text('{"exit_code":0}')
        for name in ('host','config'): (self.root/name).write_text(name)
        self.manifest = {'total':2, 'phases':['model'], 'tasks':['first','second'], 'models':[{'id':'model','model':'wire-model'}], 'database':'unused', 'timeout_seconds':900,
                         'validation_receipts':[str(self.root/'receipt')]}
        for name in ('host','config'):
            self.manifest[name] = str(self.root/name)
            self.manifest[name+'_sha256'] = hashlib.sha256((self.root/name).read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
    def prior(self, verdict='infrastructure_error'):
        path=self.root/'model';path.mkdir()
        append(path/'darwinrouter_commandline.jsonl', {'row':{'task_id':'first','verdict':verdict,'darwin_task_id':'first-original'}})
        return path/'darwinrouter_commandline.jsonl'
    def test_recovery_preserves_original_and_resume_never_repeats_inference(self):
        original=self.prior(); before=original.read_bytes()
        c=Simulated(self.root,['pass','pass']);c.run()
        self.assertTrue(original.read_bytes().startswith(before))
        self.assertEqual(c.launches[0][:2],('first',2))
        self.assertEqual(c.launches[0][2],self.root/'retries/model/first/attempt-2')
        self.assertEqual(c.feedback,['first-2','second-1'])
        self.assertEqual(c.counts['attempts'],3)
        self.assertEqual(c.counts['infrastructure_attempts'],1)
        again=Simulated(self.root);again.run()
        self.assertEqual(again.launches,[])
        self.assertEqual(again.counts,c.counts)
    def test_exhausted_case_does_not_block_next_task_or_create_feedback(self):
        self.prior()
        c=Simulated(self.root,['infrastructure_error','pass']);c.run()
        self.assertEqual(c.feedback,['second-1'])
        self.assertEqual(c.counts['completed'],2)
        self.assertEqual(c.counts['valid_grades'],1)
        self.assertEqual(json.loads((self.root/'state.json').read_text())['status'],'needs_final_infrastructure_review')
    def test_grader_error_stops_without_repeating_or_teaching(self):
        self.prior('grader_error');c=Simulated(self.root)
        with self.assertRaisesRegex(RuntimeError,'grader'):c.run()
        self.assertEqual(c.launches,[]);self.assertEqual(c.feedback,[])
    def test_feedback_failure_blocks_next_inference(self):
        self.prior('pass');c=Simulated(self.root)
        with patch.object(c,'verify_feedback',side_effect=RuntimeError('feedback mismatch')):
            with self.assertRaisesRegex(RuntimeError,'feedback mismatch'):c.run()
        self.assertEqual(c.launches,[])
    def test_circuit_breaker_stops_systemic_failure_after_three_cases(self):
        self.manifest.update(total=4,tasks=['a','b','c','d'])
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        c=Simulated(self.root,['infrastructure_error']*8)
        with self.assertRaisesRegex(RuntimeError,'three consecutive'):c.run()
        self.assertEqual(len(c.launches),6);self.assertEqual(c.feedback,[])
    def test_missing_canonical_result_never_reuses_uncertain_attempt(self):
        c=Campaign(self.root)
        append(self.root/'attempt-launches.jsonl',dict(model='model',task='first',attempt=1))
        with patch.object(c,'residents',return_value=[]),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'previous launch'):c.launch('model','first',self.root/'model',1)
        launch.assert_not_called()
    def test_modified_config_or_failed_validation_prevents_launch(self):
        c=Simulated(self.root)
        (self.root/'config').write_text('user modified settings')
        with self.assertRaisesRegex(RuntimeError,'config provenance'):c.run()
        self.assertEqual(c.launches,[])
    def test_external_resident_is_not_unloaded_or_disrupted(self):
        c=Campaign(self.root)
        with patch.object(c,'residents',return_value=['external']),patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'resident model'):c.launch('model','first',self.root/'model',1)
        launch.assert_not_called()


if __name__=='__main__':unittest.main()
