"""Install only the staged Lomenti service; never changes TrendScanner."""
import json, os, pathlib, pwd, shutil, subprocess, sys

def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    if os.geteuid() != 0:
        raise RuntimeError("Run this installer as root")
    stage = pathlib.Path("/home/runner/lomenti-deploy/source")
    ready = json.loads((stage.parent / "preflight.json").read_text())
    if not ready.get("success"):
        raise RuntimeError("Successful preflight is required")
    target = pathlib.Path("/opt/lomenti-b2b-collector")
    if target.exists():
        raise RuntimeError("Target already exists; inspect before replacing")
    try:
        pwd.getpwnam("lomenti")
    except KeyError:
        run("/usr/sbin/useradd", "--system", "--no-create-home", "--home-dir", str(target), "--shell", "/usr/sbin/nologin", "lomenti")
    identity = pwd.getpwnam("lomenti")
    config = json.loads((stage / "credentials.json").read_text())
    url = config["LOMENTI_DATABASE_URL"]
    if "\n" in url or "\r" in url:
        raise RuntimeError("Invalid credential format")
    shutil.copytree(stage, target, ignore=shutil.ignore_patterns("credentials.json", "__pycache__", "*.log"))
    for directory, dirs, files in os.walk(target):
        os.chown(directory, 0, identity.pw_gid)
        os.chmod(directory, 0o750)
        for name in files:
            path = pathlib.Path(directory) / name
            if not path.is_symlink():
                os.chown(path, 0, identity.pw_gid)
                os.chmod(path, 0o750 if os.access(path, os.X_OK) else 0o640)
    artifacts = target / "artifacts"
    artifacts.mkdir(exist_ok=True)
    for directory, dirs, files in os.walk(artifacts):
        os.chown(directory, identity.pw_uid, identity.pw_gid)
        for name in files:
            os.chown(pathlib.Path(directory) / name, identity.pw_uid, identity.pw_gid)
    env = pathlib.Path("/etc/lomenti-b2b-collector.env")
    descriptor = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("LOMENTI_DATABASE_URL=" + json.dumps(url) + "\n")
    units = pathlib.Path("/etc/systemd/system")
    for name in ("lomenti-collector.service", "lomenti-collector.timer"):
        destination = units / name
        if destination.exists():
            raise RuntimeError("Existing Lomenti unit needs review")
        shutil.copyfile(target / "ops/vps" / name, destination)
        os.chmod(destination, 0o644)
    # Ensure one timed verification shortly after first activation as well as hourly.
    dropin = units / "lomenti-collector.timer.d"
    dropin.mkdir(exist_ok=True)
    (dropin / "initial-check.conf").write_text("[Timer]\nOnActiveSec=60s\n")
    # Keep private reports and any exception details out of the public Actions logs.
    service_dropin = units / "lomenti-collector.service.d"
    service_dropin.mkdir(exist_ok=True)
    (service_dropin / "private-logs.conf").write_text(
        "[Service]\nUMask=0077\nStandardOutput=append:/opt/lomenti-b2b-collector/artifacts/service.log\n"
        "StandardError=append:/opt/lomenti-b2b-collector/artifacts/service.log\n")
    run("systemctl", "daemon-reload")
    run("systemctl", "start", "lomenti-collector.service")
    run("systemctl", "enable", "--now", "lomenti-collector.timer")
    print("LOMENTI_SERVICE_TEST_OK; TIMER_ENABLED; TrendScanner configuration untouched")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("LOMENTI_INSTALL_FAILED:", type(error).__name__)
        sys.exit(1)
