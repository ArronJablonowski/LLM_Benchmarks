#!/usr/bin/env python3
"""Durable, sequential automatic OCR campaign through the DarwinRouter SDK.

Resume only this wrapper. Launches without committed host results are ambiguous
and block, never silently re-dispatch. Reconciliation never repeats inference.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request

from benchmark_tests import suite_task_catalog
from accuracy_grading import grade_task
from vision_benchmark_support import ocr_provenance
from darwinrouter_coding_benchmarks import record_feedback

ROOT = Path(__file__).resolve().parents[1]
PROFILE = 'ocr-progressive-v1'

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def rows(path): return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()] if Path(path).exists() else []
def save(path, value, exclusive=False):
    path = Path(path)
    data = json.dumps(value, indent=2, sort_keys=True) + '\n'
    if exclusive:
        with path.open('x') as f: f.write(data); f.flush(); os.fsync(f.fileno())
    else:
        temp = path.with_suffix(path.suffix + '.tmp')
        with temp.open('w') as f: f.write(data); f.flush(); os.fsync(f.fileno())
        temp.replace(path)
def append(path, value):
    with Path(path).open('a') as f:
        f.write(json.dumps(value, sort_keys=True) + '\n'); f.flush(); os.fsync(f.fileno())

def database(path): return closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=15))
def events(db, task_id):
    with database(db) as c:
        return [json.loads(r[0]) for r in c.execute('SELECT body FROM events WHERE task_id=? ORDER BY sequence', (task_id,))]
def current_feedback(db, task_id):
    with database(db) as c:
        found = c.execute('SELECT e.id,e.body,h.current_id,r.body FROM evaluations e LEFT JOIN evaluation_heads h ON h.base_id=e.id LEFT JOIN evaluation_revisions r ON r.id=h.current_id WHERE e.task_id=?', (task_id,)).fetchall()
    result=[]
    for base,body,head,revision in found:
        if not head or (head != base and revision is None): raise RuntimeError('missing current evaluation head')
        result.append(json.loads(body if head == base else revision))
    return result
def verify_zero_feedback(db, ids):
    for task_id in ids:
        if current_feedback(db, task_id): raise RuntimeError('quality feedback on failed lineage: ' + task_id)

def capacity_deferral(manifest, task, attempt_dir):
    """Prove zero dispatch before separating an admission hold from attempts."""
    d=Path(attempt_dir);out=json.loads((d/'host-result.json').read_text());result=out['result']
    if 'local resource capacity unavailable' not in out.get('error',''):return False
    if result.get('TaskID') or result.get('PreviousTaskIDs') or result.get('Turns') or result.get('Text') or rows(d/'events.jsonl') or rows(d/'image-delivery.jsonl') or (d/'record.json').exists():
        raise RuntimeError('capacity failure contains possible execution evidence; cannot defer')
    campaign=Path(manifest['campaign_dir'])
    launch=next(x for x in rows(campaign/'attempt-launches.jsonl') if x.get('attempt_dir')==str(d) and x['event']=='launch_intent')
    start=datetime.fromisoformat(launch['at'])
    with database(manifest['database']) as db:
        starts=[json.loads(r[0]) for r in db.execute("SELECT body FROM events WHERE sequence=1 AND json_extract(body,'$.data.domain')='ocr' AND json_extract(body,'$.data.profile')=?",(PROFILE,))]
    for event in starts:
        if datetime.fromisoformat(event['time'].replace('Z','+00:00'))>=start and any(task['image_sha256'] in message.get('content','') for message in event['data'].get('messages',[])):
            raise RuntimeError('admission hold has a matching durable task; investigate')
    ledger=campaign/'admission-deferrals.jsonl'
    prior=[x for x in rows(ledger) if x['attempt_dir']==str(d)]
    if prior:
        if len(prior)!=1 or prior[0]['host_result_sha256']!=sha(d/'host-result.json'):raise RuntimeError('admission evidence drift')
        return True
    base=d.name.split('-admission-')[0]
    index=1+sum(Path(x['attempt_dir']).parent==d.parent and Path(x['attempt_dir']).name.split('-admission-')[0]==base for x in rows(ledger))
    next_dir=d.parent/f'{base}-admission-{index}'
    if next_dir.exists():raise RuntimeError('admission resume path already exists')
    append(ledger,{'at':now(),'task_id':task['id'],'attempt_dir':str(d),'next_attempt_dir':str(next_dir),'host_result_sha256':sha(d/'host-result.json'),'disposition':'capacity_deferred_before_dispatch','durable_tasks':0,'quality_feedback':0,'inference_attempt_consumed':False})
    return True

def attempt_location(campaign, task_id, attempt):
    base=Path(campaign)/'attempts'/task_id/f'attempt-{attempt}';d=base
    for index in range(1,101):
        found=[x for x in rows(Path(campaign)/'admission-deferrals.jsonl') if x['attempt_dir']==str(d)]
        if not found:return d
        if len(found)!=1 or found[0]['host_result_sha256']!=sha(d/'host-result.json'):raise RuntimeError('invalid admission deferral binding')
        expected=base.with_name(base.name+f'-admission-{index}')
        if found[0]['next_attempt_dir']!=str(expected):raise RuntimeError('invalid admission resume path')
        d=expected
    raise RuntimeError('too many admission holds; investigate capacity')

def validate_completion(task, attempt_dir, db):
    """Bind successful execution, provider image handoff and exact task identity."""
    d=Path(attempt_dir); output=json.loads((d/'host-result.json').read_text()); result=output['result']
    task_id=result.get('TaskID'); previous=result.get('PreviousTaskIDs') or []
    if not isinstance(previous,list) or any(not isinstance(x,str) for x in previous): raise RuntimeError('invalid SDK lineage')
    verify_zero_feedback(db, previous)
    if not task_id: raise RuntimeError('no durable task identity; investigate admission before resuming')
    history=events(db, task_id)
    first=next((e['data'] for e in history if e['kind']=='task.started'),None)
    if not first or first.get('domain')!='ocr' or first.get('profile')!=PROFILE: raise RuntimeError('wrong task domain/profile')
    prompts=[x.get('content','') for x in first.get('messages',[]) if x.get('role')=='user']
    if len(prompts)!=1 or task['image_sha256'] not in prompts[0] or task['prompt'] not in prompts[0]: raise RuntimeError('image/prompt identity mismatch')
    if first.get('privacy')!='local_only':raise RuntimeError('OCR task escaped local-only policy')
    terminal=[e for e in history if e['kind'] in ('task.completed','task.failed','task.canceled')]
    if len(terminal)!=1: raise RuntimeError('missing or ambiguous terminal event')
    ok=not output.get('error') and terminal[0]['kind']=='task.completed'
    if not ok: verify_zero_feedback(db, [task_id])
    delivery=rows(d/'image-delivery.jsonl')
    dispatch=[x for x in delivery if x['stage']=='dispatch' and x['model']==first['model_id'] and x['provider']==first['provider_id']]
    if ok and (len(dispatch)!=1 or dispatch[0]['image_sha256']!=task['image_sha256'] or not set(dispatch[0]['advertised']) & {'vision','image','ocr'}):
        raise RuntimeError('missing/ambiguous qualified image dispatch; no quality feedback')
    if ok and (result.get('FinishReason')!='stop' or result.get('Turns')!=1): raise RuntimeError('unexpected OCR completion contract')
    grading=grade_task(task, 'ok' if ok else 'error', result.get('Text',''))
    if ok and grading['verdict'] not in ('pass','content_mismatch'): raise RuntimeError('invalid OCR grading verdict')
    return output, result, first, grading, ok

def ensure_feedback(manifest, record):
    row=record['row'];task_id=row['darwin_task_id'];db=manifest['database']
    failed=row['previous_task_ids']+([task_id] if row['status']!='ok' else [])
    verify_zero_feedback(db, failed)
    if row['status']!='ok': return None
    accepted=record['grading']['verdict']=='pass'
    heads=current_feedback(db,task_id)
    if not heads:
        record_feedback(manifest['darwin'],Path(db),task_id,accepted)
        heads=current_feedback(db,task_id)
    if len(heads)!=1: raise RuntimeError('ambiguous quality heads')
    head=heads[0]
    expected={'Model':row['model'],'Provider':row['resolved_provider'],'Domain':'ocr','Profile':PROFILE}
    if head.get('Key')!=expected or not head.get('ExecutionSucceeded') or not head.get('Checks') or not all(x.get('Source')=='user_feedback' and x.get('Passed') is accepted for x in head['Checks']):
        raise RuntimeError('feedback ownership/verdict mismatch')
    return {'task_id':task_id,'head':head['ID'],'accepted':accepted,'verified_at':now()}

def reconcile_attempt(manifest,task,attempt_dir):
    path=Path(attempt_dir)/'record.json'
    output,result,metadata,grade,ok=validate_completion(task,attempt_dir,manifest['database'])
    launches=rows(Path(manifest['campaign_dir'])/'attempt-launches.jsonl')
    launch=next(x for x in launches if x['attempt_dir']==str(attempt_dir) and x['event']=='launch_intent')
    wall=(datetime.fromisoformat(output['finished_at'])-datetime.fromisoformat(launch['at'])).total_seconds()
    record={'row':{'run_id':Path(manifest['campaign_dir']).name,'task_id':task['id'],'task_name':task['name'],'benchmark_profile':PROFILE,'harness':'darwinrouter-sdk-ocr','configured_model':'auto','model':metadata['model_id'],'resolved_provider':metadata['provider_id'],'darwin_task_id':result['TaskID'],'previous_task_ids':result.get('PreviousTaskIDs') or [],'status':'ok' if ok else 'error','verdict':grade['verdict'] if ok else 'infrastructure_error','wall_seconds':round(wall,3),'context_tokens':metadata.get('context_tokens')},'grading':grade,'image_evidence':{'sha256':task['image_sha256'],'delivery_sha256':sha(Path(attempt_dir)/'image-delivery.jsonl') if (Path(attempt_dir)/'image-delivery.jsonl').exists() else None},'host_result_sha256':sha(Path(attempt_dir)/'host-result.json'),'attempt_dir':str(attempt_dir),'error':output.get('error')}
    if path.exists():
        if json.loads(path.read_text())!=record: raise RuntimeError('canonical OCR evidence drift')
    else: save(path,record,exclusive=True)
    receipt=ensure_feedback(manifest,record)
    if receipt:
        receipt['canonical_sha256']=sha(path)
        ledger=Path(manifest['campaign_dir'])/'feedback-verifications.jsonl'
        if not any(x.get('task_id')==receipt['task_id'] and x.get('head')==receipt['head'] for x in rows(ledger)):append(ledger,receipt)
    return record

def snapshot(campaign):
    campaign=Path(campaign);m=json.loads((campaign/'manifest.json').read_text())
    records=[json.loads(p.read_text()) for p in sorted((campaign/'attempts').glob('*/*/record.json'))]
    final={r['row']['task_id']:r for r in records if r['row']['status']=='ok'}
    counts=Counter(r['grading']['verdict'] for r in final.values());heads=[]
    for r in records:
        rr=r['row'];verify_zero_feedback(m['database'],rr['previous_task_ids']+([rr['darwin_task_id']] if rr['status']!='ok' else []))
        if rr['status']=='ok':heads.extend(current_feedback(m['database'],rr['darwin_task_id']))
    state=json.loads((campaign/'state.json').read_text()) if (campaign/'state.json').exists() else {}
    active=None
    if state.get('status')=='running' and state.get('attempt_dir'):
        local=rows(Path(state['attempt_dir'])/'events.jsonl')
        if local:
            started=[e for e in local if e['kind']=='task.started']
            if started:
                start=started[-1];history=[e for e in local if e['task_id']==start['task_id']]
                if not any(e['kind'] in ('task.completed','task.failed','task.canceled') for e in history):
                    active={'task_id':start['task_id'],'model':start['data'].get('model_id'),'provider':start['data'].get('provider_id'),'context_tokens':start['data'].get('context_tokens'),'last_event':history[-1]['kind'],'last_event_at':history[-1]['time'],'elapsed_seconds':round((datetime.now(timezone.utc)-datetime.fromisoformat(start['time'].replace('Z','+00:00'))).total_seconds(),1),'lab':state.get('task_id')}
    times=[r['row']['wall_seconds'] for r in final.values()]
    excluded=rows(campaign/'infrastructure-dispositions.jsonl')
    result={'at':now(),'campaign_dir':str(campaign),'status':state.get('status','prepared'),'total':30,'terminal':len(final)+len(excluded),'valid':len(final),'pass':counts['pass'],'mismatch':counts['content_mismatch'],'reviewed_infrastructure_exclusions':len(excluded),'outer_attempts_completed':len(records),'infrastructure_attempts':sum(r['row']['status']!='ok' for r in records),'internal_recovery_attempts':sum(len(r['row']['previous_task_ids']) for r in records),'accepted':sum(all(c.get('Passed') for c in h['Checks']) for h in heads),'rejected':sum(not all(c.get('Passed') for c in h['Checks']) for h in heads),'missing_feedback':len(final)-len(heads),'active':active,'timing':{'samples':len(times),'scope':'pooled automatic OCR phase','min_seconds':min(times) if times else None,'max_seconds':max(times) if times else None,'campaign_eta':None,'note':'No reliable campaign ETA yet; model selection and image difficulty can change.'},'state':state}
    result['capacity_admission_deferrals']=len(rows(campaign/'admission-deferrals.jsonl'))
    save(campaign/'latest.json',result)
    text=f"# DarwinRouter OCR campaign\n\nUpdated {result['at']}. Domain `ocr`; evidence profile `{PROFILE}`.\n\n| Status | Count |\n|---|---:|\n"
    for key in ['terminal','valid','pass','mismatch','reviewed_infrastructure_exclusions','outer_attempts_completed','infrastructure_attempts','internal_recovery_attempts','accepted','rejected','missing_feedback']:text+=f"| {key.replace('_',' ')} | {result[key]} |\n"
    text+='\n'+(json.dumps(active,indent=2) if active else 'No active inference identity verified at this sample.')+'\n\nImages are sent through a trusted DarwinRouter SDK adapter. Fresh native OCR/vision/image advertisements gate routing and image dispatch. GLM-OCR remains operator-excluded. Scores are routing policy scores, not pass probabilities. This is automatic selection across progressively harder synthetic English document images, not an exhaustive per-model or held-out evaluation. Exact JSON/field grading is unchanged; infrastructure failures receive no quality feedback. The public daemon API remains text-only.\n'
    (campaign/'report.md').write_text(text)
    return result

def run(campaign):
    with (Path(campaign)/'campaign.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        run_locked(campaign)

def run_locked(campaign):
    campaign=Path(campaign);m=json.loads((campaign/'manifest.json').read_text());tasks=suite_task_catalog('ocr')
    if sha(m['config'])!=m['config_sha256'] or sha(m['host'])!=m['host_sha256'] or ocr_provenance(tasks)!=m['ocr_provenance']:raise RuntimeError('campaign provenance drift')
    if any(sha(ROOT/'scripts'/name)!=expected for name,expected in m['source_hashes'].items()):raise RuntimeError('campaign source drift')
    save(campaign/'supervisor.json',{'pid':os.getpid(),'at':now(),'script':str(Path(__file__).resolve())})
    consecutive=0
    for task in tasks:
        dispositions=[x for x in rows(campaign/'infrastructure-dispositions.jsonl') if x['task_id']==task['id']]
        if dispositions:
            if len(dispositions)!=1:raise RuntimeError('duplicate infrastructure disposition')
            expected={str(attempt_location(campaign,task['id'],n)/'record.json'):sha(attempt_location(campaign,task['id'],n)/'record.json') for n in (1,2)}
            if dispositions[0].get('canonical_hashes')!=expected:raise RuntimeError('invalid infrastructure disposition binding')
            for n in (1,2):
                reviewed=reconcile_attempt(m,task,attempt_location(campaign,task['id'],n))
                if reviewed['row']['status']=='ok':raise RuntimeError('valid evidence cannot be infrastructure-excluded')
            continue
        for attempt in (1,2):
            d=attempt_location(campaign,task['id'],attempt)
            if (d/'host-result.json').exists():
                if capacity_deferral(m,task,d):
                    save(campaign/'state.json',{'status':'waiting_capacity','at':now(),'task_id':task['id'],'attempt_dir':str(d)});snapshot(campaign);return
                r=reconcile_attempt(m,task,d)
            else:
                if d.exists() or any(x.get('attempt_dir')==str(d) for x in rows(campaign/'attempt-launches.jsonl')):raise RuntimeError('ambiguous launch; investigate without repeating inference: '+str(d))
                d.parent.mkdir(parents=True,exist_ok=True)
                append(campaign/'attempt-launches.jsonl',{'at':now(),'event':'launch_intent','attempt_dir':str(d),'task_id':task['id'],'attempt':attempt,'image_sha256':task['image_sha256'],'host':m['host'],'host_sha256':m['host_sha256'],'manifest_sha256':sha(campaign/'manifest.json')})
                command=[m['host'],'--config',m['config'],'--database',m['database'],'--attempt-dir',str(d),'--image',str(ROOT/task['image_asset']),'--sha256',task['image_sha256'],'--prompt',task['prompt']]
                # No automatic watchdog re-dispatch; the host owns a bounded deadline.
                with (d.parent/(d.name+'.stdout')).open('x') as out,(d.parent/(d.name+'.stderr')).open('x') as err:
                    p=subprocess.Popen(command,cwd=ROOT,stdout=out,stderr=err)
                    append(campaign/'attempt-launches.jsonl',{'at':now(),'event':'spawned','pid':p.pid,'attempt_dir':str(d)})
                    save(campaign/'state.json',{'status':'running','at':now(),'pid':os.getpid(),'runner_pid':p.pid,'task_id':task['id'],'attempt':attempt,'attempt_dir':str(d)})
                    code=p.wait(timeout=960)
                if not (d/'host-result.json').exists():raise RuntimeError(f'host exited {code} without durable result: {d}')
                if capacity_deferral(m,task,d):
                    save(campaign/'state.json',{'status':'waiting_capacity','at':now(),'task_id':task['id'],'attempt_dir':str(d)});snapshot(campaign);return
                r=reconcile_attempt(m,task,d)
            snapshot(campaign)
            if r['row']['status']=='ok':consecutive=0;break
            if attempt==2:
                consecutive+=1
                # Exhausted cases require explicit evidence review by the monitor.
                save(campaign/'state.json',{'status':'needs_infrastructure_review','at':now(),'task_id':task['id'],'attempt_dir':str(d)})
                raise RuntimeError('two infrastructure attempts exhausted; review both, never launch a third')
    save(campaign/'state.json',{'status':'execution_complete_pending_final_audit','at':now(),'pid':os.getpid()})
    snapshot(campaign)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('campaign',type=Path);p.add_argument('--poll',action='store_true');p.add_argument('--init',action='store_true')
    p.add_argument('--host',type=Path);p.add_argument('--config',type=Path);p.add_argument('--database',type=Path);p.add_argument('--darwin',type=Path)
    a=p.parse_args();a.campaign=a.campaign.resolve()
    if a.init:
        if not all((a.host,a.config,a.database,a.darwin)):p.error('--init requires --host --config --database --darwin')
        a.campaign.mkdir(parents=True,exist_ok=False)
        tasks=suite_task_catalog('ocr')
        manifest={'created_at':now(),'campaign_dir':str(a.campaign),'domain':'ocr','profile':PROFILE,'configured_model':'auto','tasks':[t['id'] for t in tasks],'host':str(a.host.resolve()),'host_sha256':sha(a.host),'config':str(a.config.resolve()),'config_sha256':sha(a.config),'database':str(a.database.resolve()),'darwin':str(a.darwin.resolve()),'ocr_provenance':ocr_provenance(tasks),'source_hashes':{name:sha(ROOT/'scripts'/name) for name in ['darwinrouter_ocr_host.go','darwinrouter_ocr_campaign.py','darwinrouter_coding_benchmarks.py','accuracy_grading.py']},'operator_excluded_models':['local-glm-ocr'],'capability_rule':'fresh Ollama OCR/image/vision advertisement, rechecked using admitted policy transport','context_tokens':32768,'image_reserve_tokens':16384,'max_output_tokens':4096,'max_outer_attempts_per_case':2}
        save(a.campaign/'manifest.json',manifest,exclusive=True)
        save(a.campaign/'state.json',{'status':'prepared','at':now()})
        print(json.dumps(manifest,indent=2));return
    if a.poll:print(json.dumps(snapshot(a.campaign),indent=2));return
    try:run(a.campaign)
    except BlockingIOError:
        raise SystemExit('campaign lock is held; existing supervisor remains authoritative')
    except Exception as e:
        save(a.campaign/'state.json',{'status':'blocked_for_investigation','at':now(),'error':str(e),'pid':os.getpid()})
        snapshot(a.campaign);raise
if __name__=='__main__':main()
