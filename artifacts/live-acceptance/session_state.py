import json,sqlite3,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();now=int(time.time()*1000)
def stamp(v):return datetime.fromtimestamp(v/1000,ZoneInfo('Asia/Shanghai')).isoformat()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 rows=db.execute('select id,expires_at,revoked from auth_sessions order by id desc limit 8').fetchall()
 print(json.dumps({'checked_at':stamp(now),'sessions':[{'session_id':r[0],'expires_at':stamp(r[1]),'revoked':bool(r[2]),'active':not r[2] and r[1]>now} for r in rows]}))
