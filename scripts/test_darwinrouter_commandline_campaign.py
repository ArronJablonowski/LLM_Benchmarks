import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from darwinrouter_commandline_campaign import Campaign, CapacityDeferred, append, now


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
    def test_grader_revision_is_bound_to_original_valid_record(self):
        path=self.prior('fail')
        record=json.loads(path.read_text())
        digest=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        revision=dict(darwin_task_id='first-original',canonical_sha256=digest,grader_commit='reviewed',grading=dict(verdict='pass',passed=8,total=8))
        append(self.root/'grader-revisions.jsonl',revision)
        c=Simulated(self.root)
        self.assertEqual(c.evidence('model','first')[0][1]['verdict'],'pass')
        self.assertEqual(json.loads(path.read_text())['row']['verdict'],'fail')
        record['row']['verdict']='infrastructure_error'
        path.write_text(json.dumps(record)+'\n')
        with self.assertRaisesRegex(RuntimeError,'invalid grading revision'):c.evidence('model','first')

    def test_exhausted_case_does_not_block_next_task_or_create_feedback(self):
        self.prior()
        c=Simulated(self.root,['infrastructure_error','pass']);c.run()
        self.assertEqual(c.feedback,['second-1'])
        self.assertEqual(c.counts['completed'],2)
        self.assertEqual(c.counts['valid_grades'],1)
        self.assertEqual(json.loads((self.root/'state.json').read_text())['status'],'needs_final_infrastructure_review')
    def test_invalid_fixture_is_excluded_without_changing_canonical_grade(self):
        path=self.prior('fail');before=path.read_bytes();record=json.loads(before)
        digest=hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        invalidation=dict(darwin_task_id='first-original',canonical_sha256=digest,classification='infrastructure_error',reason='contradictory simulator state')
        append(self.root/'evidence-invalidations.jsonl',invalidation)
        c=Simulated(self.root)
        self.assertEqual(c.evidence('model','first')[0][1]['verdict'],'infrastructure_error')
        self.assertEqual(path.read_bytes(),before)
        invalidation['classification']='pass';append(self.root/'evidence-invalidations.jsonl',invalidation)
        with self.assertRaisesRegex(RuntimeError,'invalid evidence invalidation'):c.evidence('model','first')
    def test_maintenance_hold_prevents_any_launch_or_feedback(self):
        self.manifest['maintenance_hold']='invalid feedback withdrawal pending'
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        c=Simulated(self.root,['pass'])
        with self.assertRaisesRegex(RuntimeError,'maintenance hold'):c.run()
        self.assertEqual(c.launches,[]);self.assertEqual(c.feedback,[])
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

    def test_capacity_deferral_consumes_no_attempt_and_allows_other_models_before_auto(self):
        self.manifest.update(phases=['large','small','auto'],tasks=['first'],total=3)
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        class Deferred(Simulated):
            def launch(self, model, task, path, attempt):
                if model == 'large':raise CapacityDeferred('available RAM below reservation')
                super().launch(model, task, path, attempt)
        c=Deferred(self.root,['pass']);c.run()
        self.assertEqual(len(c.launches),1)
        self.assertEqual(c.launches[0][2],self.root/'small')
        self.assertEqual(c.counts['attempts'],1)
        self.assertEqual(c.counts['infrastructure_attempts'],0)
        self.assertFalse((self.root/'large').exists())
        self.assertFalse((self.root/'auto').exists())
        self.assertEqual(json.loads((self.root/'state.json').read_text())['status'],'waiting_capacity')

    def hold_model(self):
        audit = self.root/'provider-audit.json'
        audit.write_text(json.dumps(dict(model='model', status='needs_provider_investigation')))
        self.manifest['model_holds'] = {'model':dict(reason='Repeated invalid provider streams',
            resume_condition='Reviewed provider recovery evidence', audit=audit.name,
            audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest())}
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        return audit

    def test_reviewed_model_hold_keeps_cases_pending_and_other_models_progress(self):
        self.manifest.update(phases=['model','healthy','auto'],total=6)
        audit = self.hold_model()
        c=Simulated(self.root,['pass','pass']);c.run()
        self.assertEqual([p.name for _,_,p in c.launches],['healthy','healthy'])
        self.assertEqual(c.counts['completed'],2)
        self.assertEqual(c.counts['infrastructure_attempts'],0)
        self.assertFalse((self.root/'model').exists())
        self.assertFalse((self.root/'auto').exists())
        self.assertFalse((self.root/'attempt-launches.jsonl').exists())
        state=json.loads((self.root/'state.json').read_text())
        self.assertEqual(state['status'],'waiting_model_recovery')
        self.assertEqual([x['task'] for x in state['deferred']],['first','second'])
        audit.write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'audit changed'):Simulated(self.root).run()
        # Releasing the reviewed hold resumes only previously pending cases.
        self.manifest.pop('model_holds')
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        resumed=Simulated(self.root,['pass']*4);resumed.run()
        self.assertEqual([p.name for _,_,p in resumed.launches],['model','model','auto','auto'])
        self.assertEqual(resumed.counts['completed'],6)

    def test_hold_does_not_hide_later_feedback_failure(self):
        self.prior('pass')
        path=self.root/'model/darwinrouter_commandline.jsonl'
        record=json.loads(path.read_text());record['row']['task_id']='second';path.write_text(json.dumps(record)+'\n')
        self.hold_model();c=Simulated(self.root)
        with patch.object(c,'verify_feedback',side_effect=RuntimeError('feedback mismatch')):
            with self.assertRaisesRegex(RuntimeError,'feedback mismatch'):c.run()
        self.assertEqual(c.launches,[])

    def test_model_hold_blocks_direct_launch_and_requires_matching_audit(self):
        audit=self.hold_model();c=Campaign(self.root)
        with patch('subprocess.Popen') as launch:
            with self.assertRaisesRegex(RuntimeError,'model held'):c.launch('model','first',self.root/'model',1)
        launch.assert_not_called()
        self.assertFalse((self.root/'model').exists())
        audit.write_text(json.dumps(dict(model='another',status='needs_provider_investigation')))
        self.manifest['model_holds']['model']['audit_sha256']=hashlib.sha256(audit.read_bytes()).hexdigest()
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(RuntimeError,'identity or status mismatch'):Simulated(self.root).run()

    def test_model_hold_preserves_retry_slot(self):
        original=self.prior();before=original.read_bytes();self.hold_model()
        c=Simulated(self.root);c.run()
        self.assertEqual(c.launches,[])
        self.assertEqual(original.read_bytes(),before)
        self.assertFalse((self.root/'retries').exists())
        self.manifest.pop('model_holds')
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        resumed=Simulated(self.root,['pass','pass']);resumed.run()
        self.assertEqual(resumed.launches[0][:2],('first',2))

    def test_read_only_capacity_preflight_precedes_launch_intent_and_workspace(self):
        self.manifest.update(capacity_preflight=True,darwin='darwin')
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        c=Campaign(self.root)
        catalog={'models':[dict(id='model',ram_bytes=100)]}
        snapshot=dict(time=now(),TotalRAM=200,AvailableRAM=99,ThermalPressure=False)
        from subprocess import CompletedProcess
        outputs=[CompletedProcess([],0,json.dumps(v),'') for v in [catalog,snapshot]]
        with patch.object(c,'residents',return_value=[]),patch('subprocess.run',side_effect=outputs),patch('subprocess.Popen') as launch:
            with self.assertRaises(CapacityDeferred):c.launch('model','first',self.root/'model',1)
        launch.assert_not_called()
        self.assertFalse((self.root/'model').exists())
        self.assertFalse((self.root/'attempt-launches.jsonl').exists())

    def test_reviewed_exclusions_do_not_retrigger_breaker_but_cannot_be_retried(self):
        self.manifest.update(tasks=['a','b','c','d'],total=4)
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        for task in ['a','b','c']:
            hashes=[]
            for attempt,path in [(1,self.root/'model'),(2,self.root/'retries/model'/task/'attempt-2')]:
                path.mkdir(parents=True,exist_ok=True)
                record={'row':dict(task_id=task,darwin_task_id='',verdict='infrastructure_error')}
                append(path/'darwinrouter_commandline.jsonl',record)
                hashes.append(hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':')).encode()).hexdigest())
            append(self.root/'infrastructure-dispositions.jsonl',dict(model='model',task=task,status='resolved_excluded',reason='Reviewed admission failures without inference or feedback',canonical_sha256s=hashes))
        c=Simulated(self.root,['pass']);c.run()
        self.assertEqual([(t,a) for t,a,_ in c.launches],[('d',1)])
        self.assertEqual(c.counts['attempts'],7)
        self.assertEqual(json.loads((self.root/'state.json').read_text())['status'],'complete')
        path=self.root/'retries/model/a/attempt-2/darwinrouter_commandline.jsonl'
        row=json.loads(path.read_text());row['row']['error']='changed';path.write_text(json.dumps(row)+'\n')
        with self.assertRaisesRegex(RuntimeError,'disposition does not match'):Simulated(self.root).run()


if __name__=='__main__':unittest.main()
