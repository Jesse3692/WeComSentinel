import json,sqlite3
from pathlib import Path
from datetime import datetime,time,timedelta
from zoneinfo import ZoneInfo
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();tz=ZoneInfo('Asia/Shanghai');last=datetime.now(tz).date();first=last-timedelta(days=6);start=int(datetime.combine(first,time.min,tz).timestamp()*1000);end=int(datetime.combine(last+timedelta(days=1),time.min,tz).timestamp()*1000)
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 real=[r[0] for r in db.execute("select distinct tenant_id from business_events where coalesce(json_extract(snapshot,'$.demo'),0)!=1")]
 rows=db.execute("select tenant_id,event_type,count(*),sum(quantity) from business_events where coalesce(json_extract(snapshot,'$.demo'),0)!=1 and occurred_at>=? and occurred_at<? group by tenant_id,event_type",(start,end)).fetchall()
 print(json.dumps({'range':[str(first),str(last)],'actual_database_metrics':[{'tenant_id':r[0],'type':r[1],'records':r[2],'quantity':r[3]} for r in rows],'real_tenant_ids':real}))
