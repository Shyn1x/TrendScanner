"""Verified multi-file Lomenti update with rollback; no TrendScanner mutations."""
import fcntl,hashlib,json,os,pathlib,pwd,shutil,subprocess,sys,time
BASE=pathlib.Path('/home/runner/lomenti-growth-20261115')
TARGET=pathlib.Path('/opt/lomenti-b2b-collector')
EXPECTED={
'collector.py':'6e7975e048b31286e263d82344392845cb1cee56cb54f5e4cf7218dbe8b6f31b',
'call_eligibility.py':'d9843fadd0987422d96732e45e17dc2c711058b1643987efa2f598b707260de6',
'registry.py':'5cd2ecc94b5842b14c39cf1459c2f5aa644a9f9553af2e002d547faa15706535',
'source_verification.py':'09711b1158a41e8784d475f2c2421dac8d6ef7520ab67ea630d6de8ceb44c52a',
'discovery.py':'6680e2df958ccd482afb99f320e2a2d583a8e57a6a5c7eae273b315d81f8e686'}
RELEASE='265634db8e278c8018a6087decb119336a4ca002'
def run(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def install(source,target):
    tmp=target.with_suffix('.next');tmp.write_bytes(source.read_bytes())
    os.chown(tmp,0,pwd.getpwnam('lomenti').pw_gid);os.chmod(tmp,0o640);os.replace(tmp,target)
def main():
    if os.geteuid()!=0:raise RuntimeError('Administrator required')
    proof=json.loads((BASE/'preflight.json').read_text())
    if not proof.get('success') or proof.get('baseline_lost') or proof.get('hashes')!=EXPECTED:raise RuntimeError('Preflight mismatch')
    for name,digest in EXPECTED.items():
        if hashlib.sha256((BASE/'source'/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Source mismatch')
    trend=pathlib.Path('/home/runner/trendscanner-runtime/ACTIVE');before=trend.exists()
    with open('/run/lock/lomenti-eligibility-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        backup=pathlib.Path('/var/backups/lomenti')/str(int(time.time()));backup.mkdir(mode=0o700,parents=True)
        run('systemctl','stop','lomenti-collector.timer');replaced=[]
        try:
            deadline=time.monotonic()+1800
            while time.monotonic()<deadline:
                state=subprocess.run(['systemctl','show','lomenti-collector.service','--value','-p','ActiveState'],capture_output=True,text=True).stdout.strip()
                if state not in ('active','activating','deactivating'):break
                time.sleep(3)
            else:raise RuntimeError('Lomenti busy')
            for name in EXPECTED:
                if (TARGET/name).exists():shutil.copy2(TARGET/name,backup/name)
                install(BASE/'source'/name,TARGET/name);replaced.append(name)
            run('systemctl','start','lomenti-collector.service')
            audit=json.loads((TARGET/'artifacts/call_eligibility_v3.json').read_text())
            if not audit.get('results') or any(r.get('policy')!='LV_MANUAL_CORPORATE_V3_2026-11-15' for r in audit['results']):raise RuntimeError('Old policy result')
            activation={'success':True,'release':RELEASE,'hashes':EXPECTED,'run_id':audit['run_id'],'counts':audit['counts'],'activated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'service_result':'success','trend_active_before':before,'trend_active_after':trend.exists()}
            if before and not trend.exists():raise RuntimeError('Trend active flag changed')
            stamp=BASE/'activated.json';stamp.write_text(json.dumps(activation));os.chown(stamp,0,0);os.chmod(stamp,0o644)
            print('LOMENTI_RELEASE_UPDATED; SERVICE_TEST_OK; '+json.dumps(audit['counts']))
        except Exception:
            for name in reversed(replaced):
                if (backup/name).exists():install(backup/name,TARGET/name)
                elif (TARGET/name).exists():(TARGET/name).unlink()
            raise
        finally:
            run('systemctl','start','lomenti-collector.timer')
            print('LOMENTI_TIMER_RESTORED; TrendScanner untouched')
if __name__=='__main__':
    try:main()
    except Exception as e:print('LOMENTI_UPDATE_FAILED:',type(e).__name__);sys.exit(1)
