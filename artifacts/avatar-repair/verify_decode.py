import json,subprocess,shlex,os
from pathlib import Path
cfg=json.loads(Path('deploy/sync.local.json').read_text(encoding='utf-8-sig'));root=cfg['remote_dir'].rstrip('/')
code="""
import json,base64,sqlite3
from pathlib import Path
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
items=[]
for mid,relative,mime in c.execute('select id,relative_path,mime_type from media_assets'):
 path=(Path(s.media_dir)/relative).resolve()
 if path.is_relative_to(Path(s.media_dir).resolve()) and path.is_file():
  items.append({'media_id':mid,'mime':mime,'base64':base64.b64encode(path.read_bytes()).decode()})
print(json.dumps(items))
"""
cmd=['ssh','-p',str(cfg.get('ssh_port',22)),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8']
if cfg.get('identity_file'):cmd+=['-i',str(Path(cfg['identity_file']).expanduser().resolve())]
cmd+=[cfg.get('user','root')+'@'+cfg['host'],'cd '+shlex.quote(root)+' && '+shlex.quote(root+'/.venv/bin/python')+' -']
r=subprocess.run(cmd,input=code,text=True,capture_output=True,timeout=25)
if r.returncode:raise SystemExit('Remote image read failed')
# Image bytes only travel through process stdin; no customer image files or data URLs are logged.
check=subprocess.run(['node','tests/live-avatar-decode.cjs'],input=r.stdout,text=True,capture_output=True,env=os.environ,timeout=30)
print(check.stdout)
if check.returncode:print('Browser image decode failed')
raise SystemExit(check.returncode)
