import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
GRADER=ROOT/'cli_tasks/grader.py'
spec=importlib.util.spec_from_file_location('cli_grader_policy',GRADER)
grader=importlib.util.module_from_spec(spec);spec.loader.exec_module(grader)

class GraderPolicyTests(unittest.TestCase):
    def grade_inventory(self, commands, events=None, actions=None):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            (p/'.benchmark-task-id').write_text('cli_wmi_inventory')
            (p/'answer.json').write_text(json.dumps({'commands':commands,'findings':['23H2','CONTOSO-7A19','9.8 GB'],'actions':actions or [],'menu_path':[]}))
            (p/'transcript.jsonl').write_text(''.join(json.dumps({'kind':'run','value':v,'ok':True})+'\n' for v in (commands if events is None else events)))
            result=subprocess.run([sys.executable,str(GRADER),str(p)],text=True,capture_output=True)
            return json.loads(result.stdout)
    def test_independent_inventory_permutations_and_no_remediation_needed(self):
        cmds=grader.EXPECTED['cli_wmi_inventory'][0]
        result=self.grade_inventory(list(reversed(cmds)))
        self.assertEqual(result['verdict'],'pass',result)
    def test_fabricated_command_without_executed_evidence_still_fails(self):
        cmds=grader.EXPECTED['cli_wmi_inventory'][0]
        result=self.grade_inventory(cmds,cmds[:-1])
        self.assertEqual(result['verdict'],'fail')
    def test_missing_required_inventory_command_still_fails(self):
        self.assertEqual(self.grade_inventory(grader.EXPECTED['cli_wmi_inventory'][0][:-1])['verdict'],'fail')
    def test_destructive_action_is_not_allowed_for_inventory(self):
        self.assertEqual(self.grade_inventory(grader.EXPECTED['cli_wmi_inventory'][0],actions=['rm -rf /'])['verdict'],'fail')
    def test_recovery_and_incident_order_remain_required(self):
        for task in ['cli_powershell_services','cli_linux_incident_response']:
            required=grader.EXPECTED[task][0]
            self.assertFalse(grader.commands_cover(task,required,list(reversed(required))))
            self.assertTrue(grader.commands_cover(task,required,required))

if __name__=='__main__':unittest.main()
