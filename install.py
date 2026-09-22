"""Creates the environment and runs the guided setup; no manual file editing."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent


def bootstrap():
    if sys.version_info<(3,10):raise SystemExit('Python 3.10 or later is required. On Windows, run INSTALL.bat.')
    target=ROOT/'.venv';python=target/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('Creating the agent environment...',flush=True)
        try:venv.EnvBuilder(with_pip=True).create(target)
        except Exception:
            if sys.platform.startswith('linux') and shutil.which('apt-get') and shutil.which('sudo'):
                print('Ubuntu needs the venv component. You may be asked for your Linux password.',flush=True)
                subprocess.run(['sudo','apt-get','update'],check=True)
                subprocess.run(['sudo','apt-get','install','-y','python3-venv','python3-pip'],check=True)
                venv.EnvBuilder(with_pip=True).create(target)
            else:raise
    print('Installing the package dependencies...',flush=True)
    subprocess.run([str(python),'-m','pip','install','--timeout','120','--retries','3','-r',str(ROOT/'requirements.txt')],check=True)
    return subprocess.call([str(python),str(ROOT/'install.py'),'--configure'],cwd=ROOT)


if __name__=='__main__':
    try:
        if '--configure' in sys.argv:
            from tn_agent.setup import run
            run();raise SystemExit(0)
        raise SystemExit(bootstrap())
    except KeyboardInterrupt:print('\nSetup cancelled. You can run it again.');raise SystemExit(2)
    except Exception as e:print('\nSetup could not be completed: '+str(e),file=sys.stderr);raise SystemExit(2)
