import json,sqlite3
from pathlib import Path
from cryptography.fernet import Fernet
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();c=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
r=c.execute('select callback_id,snapshot from business_events where id=1806').fetchone();snap=json.loads(r[1]);blob=c.execute('select payload_encrypted from callback_inbox where id=?',(r[0],)).fetchone()[0];payload=json.loads(Fernet(s.payload_key.encode()).decrypt(blob))
v=payload.get('MemChangeList');print(json.dumps({'mem_change_list_type':type(v).__name__,'mem_change_items':len(v) if isinstance(v,list) else None,'mem_change_item_keys':[sorted(x) if isinstance(x,dict) else type(x).__name__ for x in v] if isinstance(v,list) else None,'mem_change_empty_dicts':isinstance(v,list) and all(x=={} for x in v),'snapshot_keys':sorted(snap),'version_fields_present':[k for k in payload if 'Ver' in k]}))
checks=[]
for b in sorted(Path('backups').glob('*/monitor.db')):
 try:
  db=sqlite3.connect(b.resolve().as_uri()+'?mode=ro',uri=True)
  row=db.execute('select snapshot from business_events where id=1806').fetchone()
  if row:
   x=json.loads(row[0]);ms=x.get('member_candidates',[]);orig=next((m for m in x.get('_before_members',[]) or [] if ms and m.get('userid')==ms[0].get('member_id')),None)
   cb=db.execute('select payload_encrypted from callback_inbox where id=?',(r[0],)).fetchone()
   typ=None
   if cb:
    vv=json.loads(Fernet(s.payload_key.encode()).decrypt(cb[0])).get('MemChangeList');typ='empty_dicts' if isinstance(vv,list) and all(z=={} for z in vv) else 'other'
   checks.append({'backup':b.parent.name,'candidate_type':[m.get('member_type') for m in ms],'before_type':(orig or {}).get('type'),'callback_list_shape':typ})
  db.close()
 except Exception:pass
print(json.dumps({'backup_evidence':checks},ensure_ascii=False))
