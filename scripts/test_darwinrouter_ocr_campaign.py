import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch

import darwinrouter_ocr_campaign as c

class OCRCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.dir=Path(self.temp.name)
        self.task=c.suite_task_catalog('ocr')[0]
        self.result={'TaskID':'actual','PreviousTaskIDs':[],'Text':json.dumps(self.task['ocr_grader']['expected']),'Turns':1,'FinishReason':'stop'}
        self.output={'result':self.result,'finished_at':c.now()}
        self.meta={'domain':'ocr','profile':c.PROFILE,'model_id':'vision-model','provider_id':'local','privacy':'local_only','messages':[{'role':'user','content':self.task['prompt']+' '+self.task['image_sha256']}]}
        self.history=[{'kind':'task.started','data':self.meta},{'kind':'task.completed','data':{}}]
        self.delivery={'stage':'dispatch','model':'vision-model','provider':'local','image_sha256':self.task['image_sha256'],'advertised':['vision']}
    def write(self):
        (self.dir/'host-result.json').write_text(json.dumps(self.output))
        (self.dir/'image-delivery.jsonl').write_text(json.dumps(self.delivery)+'\n')
    def validate(self):
        self.write()
        with patch.object(c,'events',return_value=self.history),patch.object(c,'current_feedback',return_value=[]):
            return c.validate_completion(self.task,self.dir,'unused')
    def test_verified_image_and_completed_task_grade(self):
        *_,grade,ok=self.validate();self.assertTrue(ok);self.assertEqual(grade['verdict'],'pass')
    def test_partial_correct_answer_after_failed_execution_is_infrastructure(self):
        self.output['error']='provider unavailable';self.history[-1]['kind']='task.failed'
        *_,grade,ok=self.validate();self.assertFalse(ok);self.assertNotEqual(grade['verdict'],'pass')
    def test_wrong_image_never_gets_quality_verdict(self):
        self.delivery['image_sha256']='other'
        with self.assertRaisesRegex(RuntimeError,'image dispatch'):self.validate()
    def test_missing_native_advertisement_never_gets_quality_verdict(self):
        self.delivery['advertised']=['completion']
        with self.assertRaisesRegex(RuntimeError,'image dispatch'):self.validate()
    def test_wrong_profile_never_gets_quality_verdict(self):
        self.meta['profile']='default'
        with self.assertRaisesRegex(RuntimeError,'domain/profile'):self.validate()
    def test_failed_sdk_lineage_feedback_aborts(self):
        with patch.object(c,'current_feedback',return_value=[{'ID':'bad'}]):
            with self.assertRaisesRegex(RuntimeError,'failed lineage'):c.verify_zero_feedback('unused',['previous'])
    def test_infrastructure_never_records_feedback(self):
        record={'row':{'darwin_task_id':'actual','previous_task_ids':[],'status':'error'}}
        with patch.object(c,'current_feedback',return_value=[]),patch.object(c,'record_feedback') as writer:
            self.assertIsNone(c.ensure_feedback({'database':'unused'},record));writer.assert_not_called()
    def test_existing_correct_head_does_not_repeat_feedback(self):
        row={'darwin_task_id':'actual','previous_task_ids':[],'status':'ok','model':'vision-model','resolved_provider':'local'}
        head={'ID':'head','Key':{'Model':'vision-model','Provider':'local','Domain':'ocr','Profile':c.PROFILE},'ExecutionSucceeded':True,'Checks':[{'Source':'user_feedback','Passed':True}]}
        with patch.object(c,'current_feedback',return_value=[head]),patch.object(c,'record_feedback') as writer:
            receipt=c.ensure_feedback({'database':'unused'},{'row':row,'grading':{'verdict':'pass'}})
            self.assertEqual(receipt['head'],'head');writer.assert_not_called()
            head['Key']['Model']='other'
            with self.assertRaisesRegex(RuntimeError,'ownership'):c.ensure_feedback({'database':'unused'},{'row':row,'grading':{'verdict':'pass'}})
    def test_ambiguous_launch_never_dispatches_again(self):
        manifest={'config':'unused','host':'unused','config_sha256':'hash','host_sha256':'hash','ocr_provenance':{},'source_hashes':{}}
        c.save(self.dir/'manifest.json',manifest)
        d=self.dir/'attempts'/self.task['id']/'attempt-1'
        c.append(self.dir/'attempt-launches.jsonl',{'attempt_dir':str(d),'event':'launch_intent'})
        with patch.object(c,'sha',return_value='hash'),patch.object(c,'ocr_provenance',return_value={}),patch.object(c,'suite_task_catalog',return_value=[self.task]),patch.object(c.subprocess,'Popen') as dispatch:
            with self.assertRaisesRegex(RuntimeError,'ambiguous launch'):c.run(self.dir)
            dispatch.assert_not_called()

    def admission_fixture(self):
        d=self.dir/'attempts'/self.task['id']/'attempt-1';d.mkdir(parents=True)
        out={'result':{'TaskID':'','PreviousTaskIDs':None,'Turns':0,'Text':''},'error':'task admission failed\nlocal resource capacity unavailable'}
        c.save(d/'host-result.json',out)
        c.append(self.dir/'attempt-launches.jsonl',{'attempt_dir':str(d),'event':'launch_intent','at':'2026-09-29T00:00:00+00:00'})
        db=self.dir/'tasks.db'
        with sqlite3.connect(db) as connection:connection.execute('CREATE TABLE events(sequence INTEGER,body BLOB)')
        connection.close()
        return {'campaign_dir':str(self.dir),'database':str(db)},d,out

    def test_capacity_deferral_preserves_result_and_outer_attempt_budget(self):
        m,d,out=self.admission_fixture();before=c.sha(d/'host-result.json')
        self.assertTrue(c.capacity_deferral(m,self.task,d))
        next_dir=c.attempt_location(self.dir,self.task['id'],1)
        self.assertEqual(next_dir.name,'attempt-1-admission-1');self.assertFalse(next_dir.exists())
        self.assertEqual(c.sha(d/'host-result.json'),before)
        self.assertTrue(c.capacity_deferral(m,self.task,d));self.assertEqual(len(c.rows(self.dir/'admission-deferrals.jsonl')),1)
        out['error']+=' changed';c.save(d/'host-result.json',out)
        with self.assertRaisesRegex(RuntimeError,'deferral binding'):c.attempt_location(self.dir,self.task['id'],1)

    def test_capacity_deferral_rejects_possible_dispatch(self):
        m,d,out=self.admission_fixture();out['result']['TaskID']='dispatched';c.save(d/'host-result.json',out)
        with self.assertRaisesRegex(RuntimeError,'execution evidence'):c.capacity_deferral(m,self.task,d)

    def test_capacity_deferral_checks_durable_database_not_just_host_result(self):
        m,d,out=self.admission_fixture()
        event={'time':'2026-09-29T00:01:00Z','data':{'domain':'ocr','profile':c.PROFILE,'messages':[{'content':self.task['image_sha256']}]}}
        with sqlite3.connect(m['database']) as connection:connection.execute('INSERT INTO events VALUES(1,?)',(json.dumps(event),))
        connection.close()
        with self.assertRaisesRegex(RuntimeError,'matching durable task'):c.capacity_deferral(m,self.task,d)

if __name__=='__main__':unittest.main()
