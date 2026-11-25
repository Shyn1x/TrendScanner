#!/usr/bin/env python3
"""Monitor EVERY GitHub Actions run in TrendScanner, including future workflows.

Run every 2 minutes from VPS cron/systemd timer. Read-only unless
GITHUB_MONITOR_TOKEN exists; when present, post one GitHub issue per terminal
run, and persist seen run IDs locally to prevent repeats.
No trading credentials are read; no orders are sent.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "Shyn1x/TrendScanner"
BASE = "https://api.github.com/repos/" + REPO
STATE_VERSION = 1
MAX_TRACK = 5000
USER_AGENT = "TrendScanner-All-Workflow-Status-Monitor"

def request(path, token=None, method="GET", body=None):
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, headers=headers, method=method, data=data)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.load(resp)

def get_runs(token):
    runs = []
    for page in range(1, 4):
        query = urllib.parse.urlencode({"per_page":100,"page":page})
        result = request("/actions/runs?" + query,token)
        portion = result.get("workflow_runs",[])
        runs.extend(portion)
        if len(portion)<100:break
    return runs

def read_state(path):
    if not path.exists(): return {"version": STATE_VERSION, "seen": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != STATE_VERSION or not isinstance(data.get("seen"),list):
        raise ValueError("INVALID_MONITOR_STATE")
    return data

def save_state(path,state):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".monitor_",dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(state,f,indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def describe(run):
    name=run.get("name") or run.get("path") or "GitHub workflow"
    branch=run.get("head_branch") or "unknown"
    return {"id":int(run["id"]),"name":name,"branch":branch,
            "status":run.get("status"),
            "conclusion":run.get("conclusion"),
            "url":run.get("html_url"),"ended":run.get("updated_at")}

def notify(run,token):
    d=describe(run)
    title=f"CI finished [{d['conclusion']}]: {d['name']} (run {d['id']})"
    body=(f"@Shyn1x\n\n"
          f"**Workflow:** {d['name']}\n\n"
          f"**Branch:** {d['branch']}\n\n"
          f"**Result:** {d['conclusion']}\n\n"
          f"**Run:** {d['url']}\n\n"
          "This is a GitHub Actions status notification, not a trading performance report.")
    return request("/issues",token,method="POST",body={"title":title,"body":body})

def poll(state_path, token, notify_enabled, bootstrap):
    state=read_state(state_path)
    seen=set(int(i) for i in state["seen"])
    runs=get_runs(token)
    completed=sorted((x for x in runs if x.get("status")=="completed"),
                     key=lambda x:int(x["id"]))
    if bootstrap and not state_path.exists():
        state["seen"] = [int(x["id"]) for x in completed][-MAX_TRACK:]
        save_state(state_path,state)
        print(json.dumps({"initialized":len(state["seen"]), "notifications":0}))
        return
    notified=0
    for run in completed:
        rid=int(run["id"])
        if rid in seen:continue
        # On missing token, record ONLY if this is explicitly a read-only monitor;
        # otherwise preserve unseen runs until authentication becomes available.
        if notify_enabled:
            if not token:
                raise RuntimeError("GITHUB_MONITOR_TOKEN_REQUIRED_FOR_NOTIFICATIONS")
            notify(run,token)
        seen.add(rid)
        notified+=1
    state["seen"] = sorted(seen)[-MAX_TRACK:]
    save_state(state_path,state)
    print(json.dumps({"runs_scanned":len(runs),"completed_found":len(completed),
                      "new_completed":notified,"notifications":notified if notify_enabled else 0}))

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--state",type=Path,default=Path.home()/".local/state/trendscanner/all_workflow_monitor.json")
    p.add_argument("--read-only",action="store_true",help="never create issues")
    p.add_argument("--bootstrap",action="store_true",help="mark existing runs as read, without notifying")
    a=p.parse_args()
    token=os.getenv("GITHUB_MONITOR_TOKEN") or None
    if not a.read_only and not a.bootstrap and not token:
        p.error("GITHUB_MONITOR_TOKEN is required for notifications")
    poll(a.state,token,not a.read_only and not a.bootstrap,a.bootstrap)

if __name__=="__main__":
    main()
