import json,subprocess,shlex,sys
from pathlib import Path
cfg=json.loads(Path('deploy/sync.local.json').read_text(encoding='utf-8-sig'));root=cfg['remote_dir'].rstrip('/')
code=Path(sys.argv[1]).read_text(encoding='utf-8-sig')
cmd=['ssh','-p',str(cfg.get('ssh_port',22)),'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8']
if cfg.get('identity_file'):cmd+=['-i',str(Path(cfg['identity_file']).expanduser().resolve())]
cmd+=[cfg.get('user','root')+'@'+cfg['host'],'cd '+shlex.quote(root)+' && '+shlex.quote(root+'/.venv/bin/python')+' -']
p=subprocess.run(cmd,input=code,text=True,capture_output=True,timeout=180)
if p.returncode:
 print('Remote operation failed, exit',p.returncode)
 print(p.stderr[-1800:])
else:print(p.stdout)
sys.exit(p.returncode)
