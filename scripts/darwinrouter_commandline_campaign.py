#!/usr/bin/env python3
"""Resume preserved CLI evidence with bounded fresh-workspace recovery."""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def append(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(value) + '\n')
        stream.flush()


class CapacityDeferred(RuntimeError):
    """A read-only preflight deferred work before consuming an attempt."""


class Campaign:
    def __init__(self, directory):
        self.here = Path(directory).resolve()
        self.manifest = json.loads((self.here / 'manifest.json').read_text())
        self.root = Path(__file__).resolve().parents[1]
        self.counts = dict(completed=0, valid_grades=0, pass_count=0, mismatch_count=0,
                           infrastructure_cases=0, infrastructure_attempts=0, attempts=0)
        self.active = {}

    def capacity_preflight(self, model):
        if not self.manifest.get('capacity_preflight') or model == 'auto':
            return
        darwin = self.manifest['darwin']
        catalog = json.loads(subprocess.run([darwin, 'models', 'list', '--config', self.manifest['config']],
                            capture_output=True, text=True, timeout=10, check=True).stdout)
        configured = next(m for m in catalog['models'] if m['id'] == model)
        snapshot = json.loads(subprocess.run([darwin, 'resources'], capture_output=True, text=True,
                              timeout=10, check=True).stdout)
        need, available = configured['ram_bytes'], snapshot['AvailableRAM']
        observed = dt.datetime.fromisoformat(snapshot['time'])
        age = (dt.datetime.now(dt.timezone.utc) - observed).total_seconds()
        if not 0 <= age <= 10 or not 0 < need or not 0 <= available <= snapshot['TotalRAM']:
            raise RuntimeError('invalid capacity preflight data')
        # This is a conservative lower-bound check. The real host still applies
        # its complete resource policy, leases and context accounting.
        if need > available or snapshot.get('ThermalPressure') is True:
            reason = dict(model=model, required_ram_bytes=need, available_ram_bytes=available,
                          thermal_pressure=snapshot.get('ThermalPressure'), at=now(), status='deferred')
            append(self.here / 'capacity-deferrals.jsonl', reason)
            raise CapacityDeferred(json.dumps(reason))
        prior = [r for r in records(self.here / 'capacity-deferrals.jsonl') if r['model'] == model]
        if prior and prior[-1].get('status') == 'deferred':
            append(self.here / 'capacity-deferrals.jsonl', dict(model=model, status='ready', at=now(),
                   required_ram_bytes=need, available_ram_bytes=available))

    def reviewed_exclusion(self, model, task, found):
        dispositions = [r for r in records(self.here / 'infrastructure-dispositions.jsonl')
                        if r['model'] == model and r['task'] == task]
        if not dispositions:
            return False
        decision = dispositions[-1]
        if decision.get('status') != 'resolved_excluded' or not decision.get('reason'):
            raise RuntimeError('invalid infrastructure disposition')
        hashes = []
        for path, row in found:
            original = next(r for r in records(path / 'darwinrouter_commandline.jsonl')
                            if r['row']['task_id'] == task)
            hashes.append(hashlib.sha256(json.dumps(original, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        if len(found) != 2 or found[-1][1]['verdict'] != 'infrastructure_error' or decision.get('canonical_sha256s') != hashes:
            raise RuntimeError('infrastructure disposition does not match canonical evidence')
        return True

    def state(self, status, **extra):
        data = dict(status=status, updated_at=now(), total=self.manifest['total'],
                    **self.counts, **self.active)
        data.update(extra)
        tmp = self.here / 'state.tmp'
        tmp.write_text(json.dumps(data, indent=2) + '\n')
        tmp.replace(self.here / 'state.json')

    def evidence(self, model, task):
        paths = [self.here / model, self.here / 'retries' / model / task / 'attempt-2']
        found = []
        for path in paths:
            rows = [r for r in records(path / 'darwinrouter_commandline.jsonl') if r['row']['task_id'] == task]
            if len(rows) > 1:
                raise RuntimeError('duplicate task evidence: ' + str(path))
            if rows:
                record = rows[0]
                row = dict(record['row'])
                digest = hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                revisions = [r for r in records(self.here / 'grader-revisions.jsonl') if r['darwin_task_id'] == row['darwin_task_id']]
                if revisions:
                    revision = revisions[-1]
                    if revision['canonical_sha256'] != digest or row['verdict'] not in ('pass', 'fail') or revision['grading']['verdict'] not in ('pass', 'fail'):
                        raise RuntimeError('invalid grading revision; preserve original evidence')
                    row.update(verdict=revision['grading']['verdict'], checks_passed=revision['grading']['passed'],
                               checks_total=revision['grading']['total'], grader_revision=revision['grader_commit'])
                invalidations = [r for r in records(self.here / 'evidence-invalidations.jsonl') if r['darwin_task_id'] == row['darwin_task_id']]
                if invalidations:
                    invalidation = invalidations[-1]
                    if invalidation.get('canonical_sha256') != digest or invalidation.get('classification') != 'infrastructure_error' or not invalidation.get('reason'):
                        raise RuntimeError('invalid evidence invalidation; preserve original evidence')
                    row.update(verdict='infrastructure_error', status='error', error=invalidation['reason'], evidence_invalidated=True)
                found.append((path, row))
        return found

    def guard_provenance(self):
        for key in ('host', 'config'):
            actual = hashlib.sha256(Path(self.manifest[key]).read_bytes()).hexdigest()
            if actual != self.manifest[key + '_sha256']:
                raise RuntimeError(key + ' provenance changed; inspect before launch')

    def residents(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:11434/api/ps', timeout=10) as response:
            return [m['name'] for m in json.load(response).get('models', [])]

    def launch(self, model, task, path, attempt):
        self.guard_provenance()
        if self.residents():
            raise RuntimeError('resident model present; inspect ownership before launching')
        if any(r.get('model') == model and r.get('task') == task and r.get('attempt') == attempt for r in records(self.here / 'attempt-launches.jsonl')):
            raise RuntimeError('previous launch lacks canonical result; inspect it before any retry')
        self.capacity_preflight(model)
        path.mkdir(parents=True, exist_ok=True)
        workspace = self.here / 'workspaces' / model if attempt == 1 else path / 'workspaces'
        self.active = dict(model=model, task=task, attempt=attempt, attempt_started_at=now(),
                           output_dir=str(path), deadline_seconds=self.manifest['timeout_seconds'])
        self.state('running')
        cmd = [sys.executable, str(self.root / 'scripts/darwinrouter_commandline_benchmarks.py'),
               '--run', '--host', self.manifest['host'], '--config', self.manifest['config'],
               '--database', self.manifest['database'], '--darwin', self.manifest.get('darwin', '/Users/aj_lobster/DarwinRouter/bin/darwin'),
               '--model', model, '--output-dir', str(path), '--workspace', str(workspace),
               '--timeout', str(self.manifest['timeout_seconds']), '--telemetry', 'none', '--tasks', task]
        owned = {m['id']: m['model'] for m in self.manifest['models']}
        for name in ([owned[model]] if model != 'auto' else list(owned.values())):
            cmd += ['--unload-model', name]
        append(self.here / 'attempt-launches.jsonl', dict(**self.active, event='launch_intent', recorded_at=now()))
        process = subprocess.Popen(cmd, cwd=self.root)
        append(self.here / 'attempt-launches.jsonl', dict(**self.active, pid=process.pid,
               host_sha256=self.manifest['host_sha256'], config_sha256=self.manifest['config_sha256'],
               benchmark_commit=self.manifest.get('benchmark_commit'), darwin_commit=self.manifest.get('darwin_commit')))
        self.state('running', runner_pid=process.pid)
        code = process.wait()
        if code:
            raise RuntimeError(f'runner exited {code}; inspect cleanup/evidence before continuing')

    def verify_feedback(self, path, row):
        task = row['darwin_task_id']
        accepted = row['verdict'] == 'pass'
        darwin = self.manifest.get('darwin', '/Users/aj_lobster/DarwinRouter/bin/darwin')
        def show():
            result = subprocess.run([darwin, 'feedback', 'show', '--db', self.manifest['database'], '--task', task],
                                    capture_output=True, text=True, timeout=150, check=True)
            return json.loads(result.stdout)
        def external(history):
            return [item for item in history if item.get('Checks') and all(c.get('Source') == 'user_feedback' for c in item['Checks'])]
        history = external(show())
        if not history:
            # Repair missing feedback, never repeat inference. CLI record is task-idempotent.
            from darwinrouter_coding_benchmarks import record_feedback
            record_feedback(darwin, self.manifest['database'], task, accepted)
            history = external(show())
        if not history or not all(c.get('Passed') == accepted for c in history[-1]['Checks']):
            raise RuntimeError('durable feedback mismatch; preserve history and investigate')
        ledger = path / 'feedback.jsonl'
        if not any(r.get('darwin_task_id') == task and r.get('recorded') and r.get('accepted') == accepted for r in records(ledger)):
            append(ledger, dict(darwin_task_id=task, accepted=accepted, recorded=True, repaired_at=now()))
        verified = self.here / 'feedback-verifications.jsonl'
        if not any(r.get('task_id') == task and r.get('accepted') == accepted for r in records(verified)):
            append(verified, dict(task_id=task, accepted=accepted, verified_at=now()))

    def run(self):
        if self.manifest.get('maintenance_hold'):
            raise RuntimeError('maintenance hold: ' + self.manifest['maintenance_hold'])
        for receipt in self.manifest['validation_receipts']:
            data = json.loads(Path(receipt).read_text())
            if data.get('exit_code') != 0:
                raise RuntimeError('validation not passed: ' + receipt)
        self.guard_provenance()
        consecutive_errors = 0
        deferred = []
        resolved_exclusions = 0
        for model in self.manifest['phases']:
            # Automatic routing is the final validation phase, after every
            # direct-model case has a result or a reviewed exclusion.
            if model == 'auto' and deferred:
                continue
            for task in self.manifest['tasks']:
                self.active = dict(model=model, task=task)
                found = self.evidence(model, task)
                while not found or (found[-1][1]['verdict'] == 'infrastructure_error' and len(found) < 2):
                    attempt = len(found) + 1
                    path = self.here / model if attempt == 1 else self.here / 'retries' / model / task / 'attempt-2'
                    try:
                        self.launch(model, task, path, attempt)
                    except CapacityDeferred as error:
                        deferred.append(dict(model=model, task=task, reason=str(error)))
                        self.state('capacity_deferred', deferred=deferred)
                        break
                    next_found = self.evidence(model, task)
                    if len(next_found) != len(found) + 1:
                        raise RuntimeError('runner produced no new canonical evidence')
                    found = next_found
                if deferred and deferred[-1]['model'] == model:
                    break
                path, row = found[-1]
                self.counts['attempts'] += len(found)
                self.counts['infrastructure_attempts'] += sum(r['verdict'] == 'infrastructure_error' for _, r in found)
                verdict = row['verdict']
                if verdict in ('pass', 'fail'):
                    self.verify_feedback(path, row)
                    self.counts['valid_grades'] += 1
                    self.counts['pass_count' if verdict == 'pass' else 'mismatch_count'] += 1
                    consecutive_errors = 0
                elif verdict == 'infrastructure_error':
                    self.counts['infrastructure_cases'] += 1
                    if self.reviewed_exclusion(model, task, found):
                        resolved_exclusions += 1
                        consecutive_errors = 0
                    else:
                        consecutive_errors += 1
                    issues = self.here / 'unresolved-infrastructure.jsonl'
                    if not any(r['model'] == model and r['task'] == task for r in records(issues)):
                        append(issues, dict(model=model, task=task, attempts=len(found), recorded_at=now(),
                                            result_paths=[str(p / 'darwinrouter_commandline.jsonl') for p, _ in found]))
                else:
                    raise RuntimeError('grader or unknown error requires investigation: ' + verdict)
                self.counts['completed'] += 1
                self.active = dict(model=model, task=task, last_advancement_at=now())
                self.state('progress')
                if consecutive_errors >= 3:
                    raise RuntimeError('three consecutive cases exhausted retries; investigate infrastructure')
        self.active = {}
        status = 'waiting_capacity' if deferred else ('complete' if self.counts['infrastructure_cases'] == resolved_exclusions else 'needs_final_infrastructure_review')
        self.state(status, deferred=deferred, resolved_excluded_cases=resolved_exclusions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-dir', type=Path, required=True)
    args = parser.parse_args()
    campaign = Campaign(args.campaign_dir)
    with (campaign.here / 'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            campaign.run()
        except Exception as error:
            campaign.state('needs_investigation', error=str(error)[:1000])
            raise


if __name__ == '__main__':
    main()
