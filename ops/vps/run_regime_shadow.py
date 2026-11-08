"""Independent scheduled Bybit 4h regime-shadow runner; never submits orders."""
from __future__ import annotations
import fcntl
import json
from pathlib import Path
import time
from runtime import atomic_json, run_command

def main():
    base = Path.home() / "trendscanner-regime-shadow"
    runtime = Path.home() / "trendscanner-runtime"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (base / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("REGIME_SHADOW_ALREADY_RUNNING")
            return
        workspace = (runtime / "current/lower").resolve(strict=True)
        config = json.loads((runtime / "credentials.json").read_text())
        script = base / "code/ops/vps/regime_donchian_shadow.py"
        args = [str(workspace / ".venv/bin/python"), str(script), "--collect"]
        code, output = run_command(args, workspace, "collect_4h",
                                   {"DATABASE_URL": config["DATABASE_URL"]})
        state = {"checked_at": int(time.time()), "success": code == 0,
                 "returncode": code, "message": output[-3000:]}
        atomic_json(base / "status.json", state)
        print(output, flush=True)
        raise SystemExit(code)

if __name__ == "__main__":
    main()
