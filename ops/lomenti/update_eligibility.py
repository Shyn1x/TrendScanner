"""Atomic update of Lomenti eligibility only. Requires administrator privileges."""
import fcntl,hashlib,json,os,pathlib,pwd,shutil,subprocess,sys,time
EXPECTED='fd64f9d2e70acf547831ef556d34861a80570115e6d83270ab61769b5aed19a9'
BASE=pathlib.Path('/home/runner/lomenti-eligibility-update')
TARGET=pathlib.Path('/opt/lomenti-b2b-collector/call_eligibility.py')
def run(*args):
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
def main():
    if os.geteuid()!=0: raise RuntimeError('Administrator required')
    source=BASE/'source/call_eligibility.py'
    proof=json.loads((BASE/'preflight.json').read_text())
    if not proof.get('success') or hashlib.sha256(source.read_bytes()).hexdigest()!=EXPECTED:
        raise RuntimeError('Verified source mismatch')
    with open('/run/lock/lomenti-eligibility-update.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        backup=pathlib.Path('/var/backups/lomenti')
        backup.mkdir(mode=0o700,parents=True,exist_ok=True)
        previous=backup/('call_eligibility-'+str(int(time.time()))+'.py')
        shutil.copy2(TARGET,previous)
        run('systemctl','stop','lomenti-collector.timer')
        replaced=False
        try:
            deadline=time.monotonic()+1800
            while time.monotonic()<deadline:
                state=subprocess.run(['systemctl','show','lomenti-collector.service','--value','-p','ActiveState'],capture_output=True,text=True).stdout.strip()
                if state not in ('active','activating','deactivating'): break
                time.sleep(3)
            else: raise RuntimeError('Lomenti still busy')
            temporary=TARGET.with_suffix('.next')
            temporary.write_bytes(source.read_bytes())
            os.chown(temporary,0,pwd.getpwnam('lomenti').pw_gid);os.chmod(temporary,0o640)
            os.replace(temporary,TARGET);replaced=True
            run('systemctl','start','lomenti-collector.service')
            activated={'success':True,'sha256':EXPECTED,'activated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'service_result':'success','release':'e90dff3e1a0b919bc21861bf5a97b55799dd3748'}
            stamp=BASE/'activated.json';stamp.write_text(json.dumps(activated));os.chown(stamp,0,0);os.chmod(stamp,0o644)
            print('LOMENTI_ELIGIBILITY_UPDATED; SERVICE_TEST_OK')
        except Exception:
            if replaced:
                shutil.copy2(previous,TARGET);os.chown(TARGET,0,pwd.getpwnam('lomenti').pw_gid);os.chmod(TARGET,0o640)
            raise
        finally:
            run('systemctl','start','lomenti-collector.timer')
            print('LOMENTI_TIMER_RESTORED; TrendScanner untouched')
if __name__=='__main__':
    try: main()
    except Exception as error:
        print('LOMENTI_UPDATE_FAILED:',type(error).__name__);sys.exit(1)
