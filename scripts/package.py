"""Package an allowlist: never include wallets, journals, user config, or reports."""
from pathlib import Path
import hashlib
import zipfile
ROOT=Path(__file__).resolve().parents[1]
TOP=['agent.py','install.py','config.example.txt','requirements.txt','assets.json','INSTALL.bat',
     'START.bat','install_windows.ps1','install.sh','start.sh','README.md','DEPLOYMENT.md',
     'DELIVERY_STATUS.md','PROJECT.md','RELEASE_NOTES.md','THIRD_PARTY_LICENSES.txt','render.yaml',
     'package.json','package-lock.json','.gitignore','TEST_RESULTS.txt']
DIRS=['tn_agent','wallet','server','tests','scripts','.github','presentation']

def build():
    out=ROOT/'dist';out.mkdir(exist_ok=True);target=out/'Trading_News_Agent_v4.zip'
    files=[ROOT/x for x in TOP]
    for name in DIRS:
        files.extend(p for p in (ROOT/name).rglob('*') if p.is_file() and not any(x in p.relative_to(ROOT).parts for x in ['__pycache__','data','.pytest_cache']) and p.suffix in {'.py','.js','.mjs','.html','.css','.json','.env','.md','.yml','.txt','.srt','.mp4'})
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path in sorted(set(files)):
            if path.name.endswith('.pyc') or path.name=='.env':continue
            z.write(path,'trading_news_agent/'+str(path.relative_to(ROOT)))
        z.writestr('trading_news_agent/config.txt',(ROOT/'config.example.txt').read_text())
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    (out/'SHA256SUMS.txt').write_text(digest+'  '+target.name+'\n')
    print(target);print(digest)
if __name__=='__main__':build()
