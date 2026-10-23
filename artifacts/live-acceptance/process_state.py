import json,os
from pathlib import Path
root=Path.cwd().resolve();items=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  if (p/'cwd').resolve()!=root or b'uvicorn' not in (p/'cmdline').read_bytes():continue
  status={k:v.strip() for k,v in (line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)}
  attached=False
  for fd in (p/'fd').iterdir():
   try:
    if 'monitor.db' in os.readlink(fd):attached=True
   except OSError:pass
  items.append({'pid':int(p.name),'state':status.get('State'),'threads':int(status.get('Threads','0')),'database_files_open':attached})
 except OSError:pass
print(json.dumps({'process_state':items}))
