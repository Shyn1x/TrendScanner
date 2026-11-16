"""Read-only execution latency root-cause summary, no credentials printed."""
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time

def distribution(x):
    if not x:return None
    v=sorted(x)
    return {"n":len(v),"median_seconds":round(statistics.median(v),2),
            "p90_seconds":round(v[(9*len(v)+9)//10-1],2),
            "max_seconds":round(v[-1],2)}
def audit(root,conn):
    conn.read_only=True
    with conn.cursor() as cur:
        cur.execute("""SELECT signal_close_ms,submitted_ms,status
                       FROM bybit_demo_strategy_trades
                       WHERE executor_version='bybit-demo-v1-1h-long-12bar-stop150'
                       AND timeframe='1h' AND direction='LONG'""")
        rows=cur.fetchall()
    submitted=[(int(s)-int(c))/1000 for c,s,status in rows if s is not None]
    now=int(time.time())
    task_files={}
    for name in ("demo","collect_1h","collect_15m","collect_4h","outcomes"):
        logs=[]
        for p in (root/"logs").glob(name+"-*.log"):
            timestamp=p.stem.removeprefix(name+"-")
            if timestamp.isdecimal() and 0<=now-int(timestamp)<=3*86400:
                logs.append((int(timestamp),p))
        logs.sort()
        # Log files are already redacted by runtime; extract ONLY scheduling metadata.
        run_duration=[]
        status=Counter()
        for started,p in logs:
            # status.json is last run only; older stdout logs never emitted.
            content=p.read_text(errors="replace")
            status["timeout"]+=int("TASK TIMEOUT" in content)
            status["failure_text"]+=int("FAIL:" in content)
            status["runs"]+=1
        task_files[name]=dict(status)
    state=json.loads((root/"state/status.json").read_text())
    tasks={}
    for key in ("demo","collect_1h","collect_15m","collect_4h","outcomes"):
        t=state.get("tasks",{}).get(key,{})
        tasks[key]={"ok":t.get("ok"),"last_returncode":t.get("returncode"),
                    "last_run_duration_seconds":(t["finished_at"]-t["attempted_at"])
                     if t.get("finished_at") and t.get("attempted_at") else None}
    # Keep identities, raw timestamps, positions and PnL private.
    return {"journal_rows":len(rows),"submission_delay_from_close":distribution(submitted),
            "submitted_4min_or_later":sum(x>=240 for x in submitted),
            "submitted_10min_or_later":sum(x>=600 for x in submitted),
            "submitted_30min_or_later":sum(x>=1800 for x in submitted),
            "journal_missing_submission":sum(s is None for c,s,status in rows),
            "last_runtime_tasks":tasks,"log_run_counts_last_72h":task_files,
            "design_finding":"Serial scheduler: collect_1h then collect_15m before demo; 4h and outcomes run after demo and can block subsequent cycles via global flock; demo only due every five minutes",
            "important":"Submission lag vs signal close is scheduler/collector/planning delay, not exchange order-to-fill latency. Runtime logs provide only operational counters."}
def main():
    root=Path.home()/"trendscanner-runtime"
    config=json.loads((root/"credentials.json").read_text())
    workspace=(root/"current/lower").resolve(strict=True)
    sys.path.insert(0,str(workspace/"artifacts/trendscanner"))
    from bybit_demo_executor import _connect
    with _connect(config["DATABASE_URL"]) as conn:
        report=audit(root,conn)
    print(json.dumps(report,sort_keys=True))
if __name__=="__main__":main()
