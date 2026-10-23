import json,os
from pathlib import Path
root=Path.cwd().resolve();sockets={}
for table in ('/proc/net/tcp','/proc/net/tcp6'):
 for line in Path(table).read_text().splitlines()[1:]:
  cols=line.split()
  if cols[3]=='0A':sockets[cols[9]]=int(cols[1].split(':')[1],16)
items=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  if (p/'cwd').resolve()!=root or b'uvicorn' not in (p/'cmdline').read_bytes():continue
  ports=set()
  for f in (p/'fd').iterdir():
   try:
    target=os.readlink(f)
    if target.startswith('socket:[') and target[8:-1] in sockets:ports.add(sockets[target[8:-1]])
   except OSError:pass
  items.append({'pid':int(p.name),'listening_ports':sorted(ports)})
 except OSError:pass
print(json.dumps({'project_uvicorn_processes':items}))
