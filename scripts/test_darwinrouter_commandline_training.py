import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
import darwinrouter_commandline_benchmarks as runner

class TrainingTests(unittest.TestCase):
    def test_explicit_expanded_lab_selection_preserves_standard_default(self):
        base=dict(host=Path('/unused/host'),config=Path('/unused/config'),database=Path('/unused/db'),output_dir=Path('/unused/out'),workspace=Path('/unused/work'),run=False)
        for selected, count, profile in [(['cli_exp_linux_04'],1,'expanded'),(None,20,'standard-20')]:
            with self.subTest(selected=selected), patch.object(runner,'parse_args',return_value=argparse.Namespace(**base,tasks=selected)),contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(runner.main(),0)
                self.assertIn(f'tasks: {count}',output.getvalue())
                self.assertIn(profile,output.getvalue())

    def test_fallback_feedback_and_cleanup_follow_actual_task_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);host=root/'host';host.touch();config=root/'config';config.touch()
            args=argparse.Namespace(host=host,config=config,database=root/'learning.db',darwin='darwin',model='auto',output_dir=root/'out',workspace=root/'work',timeout=30,unload_model=['failed-model','winner','external-resident'],tasks=['lab'],run=True,telemetry='none')
            sampler=Mock();sampler.get_since.return_value=[]
            proc=subprocess.CompletedProcess([],0,json.dumps({'TaskID':'success','PreviousTaskIDs':['failed']}),'')
            metadata={'success':{'model_id':'winner','provider_id':'ollama-worker'},'failed':{'model_id':'failed-model','provider_id':'ollama-worker'}}
            with patch.object(runner,'parse_args',return_value=args),patch.object(runner,'suite_task_catalog',return_value=[{'id':'lab','name':'Lab','prompt':'Do the lab'}]),patch.object(runner,'create_sampler',return_value=sampler),patch.object(runner,'prepare_workspace',return_value=root),patch.object(runner,'grade_workspace',return_value=({'verdict':'pass','passed':1,'total':1},'')),patch.object(runner.subprocess,'run',return_value=proc),patch.object(runner,'task_metadata',side_effect=lambda db,task:metadata[task]),patch.object(runner,'record_feedback') as feedback,patch.object(runner,'unload_model',return_value='') as unload:
                self.assertEqual(runner.main(),0)
                feedback.assert_called_once_with('darwin',args.database,'success',True)
                self.assertEqual({call.args[0] for call in unload.call_args_list},{'failed-model','winner'})
                row=json.loads((root/'out/darwinrouter_commandline.jsonl').read_text())['row']
                self.assertEqual(row['previous_task_ids'],['failed'])
                self.assertEqual(row['resolved_model'],'winner')

    def test_campaign_adds_tasks_without_changing_pinned_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);host=root/'host';host.touch();config=root/'config';config.touch()
            args=argparse.Namespace(host=host,config=config,database=root/'learning.db',darwin='darwin',model='auto',output_dir=root/'out',workspace=root/'work',timeout=30,unload_model=[],tasks=['lab-one'],run=True,telemetry='none',grading_version='v3')
            tasks=[{'id':name,'name':name,'prompt':'Do the lab'} for name in ['lab-one','lab-two']]
            sampler=Mock();sampler.get_since.return_value=[]
            with patch.object(runner,'parse_args',return_value=args),patch.object(runner,'suite_task_catalog',return_value=tasks),patch.object(runner,'create_sampler',return_value=sampler),patch.object(runner,'prepare_workspace',return_value=root),patch.object(runner,'grade_workspace',return_value=({'verdict':'pass','passed':1,'total':1},'')),patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps({'TaskID':'fixture'}),'')),patch.object(runner,'task_metadata',return_value={'model_id':'winner','provider_id':'local'}),patch.object(runner,'record_feedback') as feedback:
                self.assertEqual(runner.main(),0)
                args.tasks=['lab-two']
                self.assertEqual(runner.main(),0)
                self.assertEqual(feedback.call_count,2)
                self.assertEqual(len((args.output_dir/'darwinrouter_commandline.jsonl').read_text().splitlines()),2)
                host.write_text('different executable')
                with self.assertRaisesRegex(RuntimeError,'contract changed|host, grader'):
                    runner.main()

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
