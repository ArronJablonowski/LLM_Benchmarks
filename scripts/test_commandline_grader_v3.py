import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'cli_tasks'))
from grader_v3 import grade, SAFE_ACTIONS

class FutureGradingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        descriptor = json.loads((Path(__file__).parent / 'benchmark_tests/commandline/cli_exp_distro_02.json').read_text())
        self.events = [{'time':'2026-09-28T00:00:00Z', 'kind':'run', 'value':cmd, 'ok':True, 'output':out} for cmd,out in descriptor['lab']['commands'].items()]
        self.answer = {'commands':list(descriptor['lab']['commands']), 'findings':['DECISIVE: evidence-cli_exp_distro_02'], 'actions':[SAFE_ACTIONS[0]], 'menu_path':[]}
        (self.root / '.benchmark-task-id').write_text(descriptor['id'])
    def save(self, seal=True):
        a=json.dumps(self.answer).encode(); t=b'\n'.join(json.dumps(e).encode() for e in self.events)+b'\n'
        (self.root/'answer.json').write_bytes(a);(self.root/'transcript.jsonl').write_bytes(t)
        if seal:
            (self.root/'submission.json').write_text(json.dumps({'version':3,'answer_sha256':hashlib.sha256(a).hexdigest(),'transcript_sha256':hashlib.sha256(t).hexdigest()}))
        return grade(self.root)
    def test_complete_evidence_passes(self): self.assertEqual(self.save()['verdict'],'pass')
    def test_rejects_plausible_but_unsupported_findings(self):
        self.answer['findings'].append('The production firewall is healthy.')
        self.assertEqual(self.save()['verdict'],'fail')
    def test_rejects_keyword_stuffed_destructive_action(self):
        self.answer['actions']=['Safely delete all snapshots after evidence review']
        self.assertEqual(self.save()['verdict'],'fail')
    def test_rejects_reordered_and_fabricated_execution(self):
        self.events.reverse(); self.assertEqual(self.save()['verdict'],'fail')
    def test_rejects_duplicate_probe_but_accepts_changed_observation(self):
        self.events.append(copy.deepcopy(self.events[-1]));self.answer['commands'].append(self.answer['commands'][-1])
        self.assertEqual(self.save()['verdict'],'fail')
        self.events[-1]['output']='Observed a different state after an action'
        self.assertEqual(self.save()['verdict'],'pass')
    def test_rejects_mutation_after_submission(self):
        self.save();self.answer['actions']=[SAFE_ACTIONS[1]]
        self.assertEqual(self.save(seal=False)['verdict'],'fail')
    def test_rejects_truncated_transcript_and_duplicate_keys(self):
        self.save();(self.root/'transcript.jsonl').write_text('{broken')
        self.assertEqual(grade(self.root)['verdict'],'fail')
        self.save();(self.root/'answer.json').write_text('{"commands":[],"commands":[]}')
        self.assertEqual(grade(self.root)['verdict'],'fail')
    def test_missing_or_malformed_artifacts_are_mismatches(self):
        self.assertEqual(grade(self.root)['verdict'],'fail')
        self.answer['commands']='not an array'; self.assertEqual(self.save()['verdict'],'fail')
if __name__=='__main__':unittest.main()
