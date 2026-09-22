#!/usr/bin/env python3
"""Run repository-level coding tasks through DarwinRouter's native SDK host."""
from __future__ import annotations

import argparse, csv, hashlib, json, os, subprocess, time
from pathlib import Path

from benchmark_tests import suite_task_catalog
from coding_agent_benchmarks import prepare_workspace, grade_workspace, fingerprint_tree, count_student_tests
from platform_support import create_sampler

FIELDS = ["run_id","benchmark_profile","harness","model","task_id","task_name","status","verdict","checks_passed","checks_total","wall_seconds","exit_code","files_changed","student_test_files","feedback_recorded","context_tokens","max_gpu_temp_c","max_host_temp_c","max_host_memory_used_bytes","max_host_memory_pct","max_gpu_usage_pct","sample_count","error"]

def maximum(samples, key):
    values=[x.get(key) for x in samples if x.get(key) is not None]
    return max(values) if values else ""

def record_feedback(darwin, database, task_id, accepted):
    proc=subprocess.run([darwin,"feedback","--db",str(database),"--task",task_id,"--outcome","accepted" if accepted else "rejected","--attempt-cost","0"],text=True,capture_output=True,timeout=30)
    if proc.returncode:raise RuntimeError(proc.stderr.strip() or "feedback failed")

def stop_model(name):
    subprocess.run(["ollama","stop",name],capture_output=True,timeout=30,check=False)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        ps=subprocess.run(["ollama","ps"],text=True,capture_output=True,timeout=10)
        if ps.returncode==0 and name not in ps.stdout:return
        time.sleep(2)
    raise RuntimeError(f"model remained resident: {name}")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host",type=Path,required=True);p.add_argument("--config",type=Path,required=True)
    p.add_argument("--models",nargs="+",default=["local-muse-glimmer","local-qwen3-coder","auto"])
    p.add_argument("--ollama-model",action="append",default=[]);p.add_argument("--output-dir",type=Path,required=True);p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--darwin",default="/Users/aj_lobster/DarwinRouter/bin/darwin")
    p.add_argument("--timeout",type=int,default=1800);p.add_argument("--tasks",nargs="*");p.add_argument("--run",action="store_true")
    args=p.parse_args(); tasks=suite_task_catalog("coding")
    if args.tasks:
        wanted=set(args.tasks);tasks=[t for t in tasks if t["id"] in wanted]
        if wanted-{t["id"] for t in tasks}:raise SystemExit("unknown coding task")
    total=len(args.models)*len(tasks);print(f"Suite: coding (coding-agent-v2-web); harness: DarwinRouter; observations: {total}",flush=True)
    if not args.run:return 0
    args.output_dir.mkdir(parents=True,exist_ok=True);args.workspace.mkdir(parents=True,exist_ok=True)
    database=(args.output_dir/"darwinrouter-coding.db").resolve()
    jsonl=args.output_dir/"darwinrouter_coding.jsonl";csv_path=args.output_dir/"darwinrouter_coding.csv"
    records=[json.loads(x) for x in jsonl.read_text().splitlines()] if jsonl.exists() else []
    completed={(r["row"]["model"],r["row"]["task_id"]) for r in records};run_id=records[0]["row"]["run_id"] if records else time.strftime("%Y%m%d_%H%M%S")
    sampler=create_sampler("auto",interval_ms=1000);sampler.start()
    try:
      for model in args.models:
       for task in tasks:
        if (model,task["id"]) in completed:continue
        print(f"[{len(completed)+1}/{total}] DarwinRouter {model} :: {task['id']}",flush=True)
        for name in args.ollama_model:stop_model(name)
        work=prepare_workspace(args.workspace,"darwinrouter",model,task);before=fingerprint_tree(work)
        prompt=task["prompt"]+"\n\nWork only inside: "+str(work)
        command=[str(args.host),"--config",str(args.config),"--database",str(database),"--workspace",str(work),"--model",model,"--prompt",prompt,"--timeout",f"{args.timeout}s"]
        start_sample=sampler.snapshot_len();started=time.monotonic();proc=None
        for attempt in range(5):
            proc=subprocess.run(command,text=True,capture_output=True,timeout=args.timeout+60,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
            if proc.returncode==0:break
            if attempt<4:
                for name in args.ollama_model:stop_model(name)
                print(f"  -> host retry {attempt+2}/5",flush=True);time.sleep(min(5*(attempt+1),20))
        wall=round(time.monotonic()-started,3);samples=sampler.get_since(start_sample)
        grading,grader_error=grade_workspace(task,work);after=fingerprint_tree(work);changed=sorted(set(before)|set(after));changed=[x for x in changed if before.get(x)!=after.get(x)]
        stdout=(proc.stdout or "").strip();error=((proc.stderr or "").strip()+("; "+grader_error if grader_error else ""))[:3000]
        task_id="";context_tokens=""
        try:
            result=json.loads(stdout.splitlines()[-1]);task_id=result.get("TaskID") or result.get("task_id") or "";usage=result.get("Usage") or result.get("usage") or {};context_tokens=usage.get("InputTokens") or usage.get("input_tokens") or ""
        except Exception:result={}
        feedback=False
        if task_id:
            try:record_feedback(args.darwin,database,task_id,grading.get("verdict")=="pass");feedback=True
            except Exception as exc:error=(error+f"; feedback: {exc}")[:3000]
        row={"run_id":run_id,"benchmark_profile":"coding-agent-v2-web","harness":"darwinrouter","model":model,"task_id":task["id"],"task_name":task["name"],"status":"ok" if proc.returncode==0 else "error","verdict":grading.get("verdict","grader_error"),"checks_passed":grading.get("passed",0),"checks_total":grading.get("total",0),"wall_seconds":wall,"exit_code":proc.returncode,"files_changed":len(changed),"student_test_files":count_student_tests(work),"feedback_recorded":str(feedback).lower(),"context_tokens":context_tokens,"max_gpu_temp_c":maximum(samples,"gpu_temp_c"),"max_host_temp_c":maximum(samples,"host_temp_c"),"max_host_memory_used_bytes":maximum(samples,"host_memory_used_bytes"),"max_host_memory_pct":maximum(samples,"host_memory_pct"),"max_gpu_usage_pct":maximum(samples,"gpu_usage_pct"),"sample_count":len(samples),"error":error}
        record={"row":row,"darwin_response":result,"grading":grading,"changed_files":changed,"telemetry_samples":samples}
        with jsonl.open("a") as f:f.write(json.dumps(record)+"\n")
        records.append(record);completed.add((model,task["id"]));
        with csv_path.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();[w.writerow({k:r["row"].get(k,"") for k in FIELDS}) for r in records]
        print(f"  -> {row['status']} {row['verdict']} {row['checks_passed']}/{row['checks_total']} wall={wall}s feedback={feedback}",flush=True)
        for name in args.ollama_model:stop_model(name)
    finally:sampler.stop()
    return 0

if __name__=="__main__":raise SystemExit(main())
