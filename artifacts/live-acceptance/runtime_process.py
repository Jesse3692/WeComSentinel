import json,os
from pathlib import Path
from app.config import Settings
root=Path.cwd().resolve();found=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  if (p/'cwd').resolve()!=root:continue
  cmd=(p/'cmdline').read_bytes()
  if b'uvicorn' not in cmd:continue
  env={}
  for item in (p/'environ').read_bytes().split(b'\0'):
   if b'=' in item:
    k,v=item.split(b'=',1)
    if k.decode(errors='ignore') in ('APP_ENV','COOKIE_SECURE','DEMO_ENABLED','PUBLIC_ORIGIN','DATABASE_URL','MEDIA_DIR','WORKER_ENABLED'):
     env[k.decode()]=v.decode()
  old={k:os.environ.get(k) for k in env};os.environ.update(env)
  s=Settings();s.validate_runtime()
  found.append({'pid':int(p.name),'production':s.app_env=='production','secure_cookie':s.cookie_secure,'demo_disabled':not s.demo_enabled,'https_origin':s.public_origin.startswith('https://'),'db_same_as_diagnostic':s.database_url==Settings(_env_file='.env').database_url,'worker_enabled':s.worker_enabled})
  for k,v in old.items():
   if v is None:os.environ.pop(k,None)
   else:os.environ[k]=v
 except (OSError,PermissionError):continue
print(json.dumps({'running_processes':found}))
