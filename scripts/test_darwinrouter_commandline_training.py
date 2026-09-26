import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import darwinrouter_commandline_benchmarks as runner

class TrainingTests(unittest.TestCase):
    def check_run(self, exit_code, grade_error, expected, feedback):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);host=root/'host';host.touch();config=root/'config';config.touch()
            args=argparse.Namespace(host=host,config=config,database=root/'learning.db',darwin='darwin',model='auto',output_dir=root/'out',workspace=root/'work',timeout=30,unload_model=None,tasks=None,run=True,telemetry='none')
            sampler=Mock();sampler.get_since.return_value=[]
            task={'id':'lab','name':'Lab','prompt':'Do the lab'}
            proc=subprocess.CompletedProcess([],exit_code,json.dumps({'TaskID':'task-1'}),'')
            with patch.object(runner,'parse_args',return_value=args),patch.object(runner,'suite_task_catalog',return_value=[task]),patch.object(runner,'create_sampler',return_value=sampler),patch.object(runner,'prepare_workspace',return_value=root),patch.object(runner,'grade_workspace',return_value=({'verdict':'pass','passed':1,'total':1},grade_error)),patch.object(runner.subprocess,'run',return_value=proc) as run,patch.object(runner,'task_metadata',return_value={'model_id':'selected','context_tokens':32768}),patch.object(runner,'record_feedback') as record:
                self.assertEqual(runner.main(),0)
                self.assertEqual(run.call_count,1, 'must not retry contaminated workspace')
                self.assertEqual(record.call_count,feedback)
                row=json.loads((root/'out/darwinrouter_commandline.jsonl').read_text())['row']
                self.assertEqual(row['verdict'],expected)
                self.assertEqual(row['darwin_task_id'],'task-1')
                if feedback:
                    receipt=json.loads((root/'out/feedback.jsonl').read_text())
                    self.assertTrue(receipt['recorded'])
                    self.assertTrue(receipt['accepted'])
                self.assertIn('--database',run.call_args.args[0])
    def test_valid_grade_teaches_selected_task(self): self.check_run(0,'','pass',1)
    def test_host_failure_never_teaches_partial_pass(self): self.check_run(1,'','infrastructure_error',0)
    def test_grader_failure_never_teaches(self): self.check_run(0,'broken grader','grader_error',0)

if __name__=='__main__':unittest.main()
