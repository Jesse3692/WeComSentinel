import sqlite3,time
from pathlib import Path
from app.config import Settings
settings=Settings();dbfile=Path(settings.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();deadline=time.monotonic()+35
while time.monotonic()<deadline:
 with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True) as conn:
  newer=conn.execute('select count(*) from auth_sessions n join auth_sessions b on b.id=17 and b.user_id=n.user_id where n.id>17 and n.revoked=0').fetchone()[0]
 if newer:break
 time.sleep(2)

exec("import json,sqlite3,time\nfrom pathlib import Path\nfrom datetime import datetime\nfrom zoneinfo import ZoneInfo\nfrom app.config import Settings\ns=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();now=int(time.time()*1000)\ndef stamp(v):return datetime.fromtimestamp(v/1000,ZoneInfo('Asia/Shanghai')).isoformat()\nwith sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:\n rows=db.execute('select id,expires_at,revoked from auth_sessions order by id desc limit 8').fetchall()\n print(json.dumps({'checked_at':stamp(now),'sessions':[{'session_id':r[0],'expires_at':stamp(r[1]),'revoked':bool(r[2]),'active':not r[2] and r[1]>now} for r in rows]}))\n")
