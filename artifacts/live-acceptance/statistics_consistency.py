import json,sqlite3
from pathlib import Path
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();today=datetime.now(ZoneInfo('Asia/Shanghai')).date();last=today;first=last-timedelta(days=6)
start=int(datetime.combine(first,time.min,ZoneInfo('Asia/Shanghai')).timestamp()*1000);end=int(datetime.combine(last+timedelta(days=1),time.min,ZoneInfo('Asia/Shanghai')).timestamp()*1000)
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 real_ids=[r[0] for r in db.execute("select distinct tenant_id from business_events where coalesce(json_extract(snapshot,'$.demo'),0)!=1")]
 reports=[]
 for tid in real_ids:
  rows=db.execute('select id,event_type,quantity,employee_id,group_id,occurred_at from business_events where tenant_id=? and occurred_at>=? and occurred_at<? order by occurred_at desc,id desc',(tid,start,end)).fetchall()
  metrics={};daily={};tops={}
  for kind in ['contact_added','contact_deleted','group_left','group_removed']:
   selected=[r for r in rows if r[1]==kind];metrics[kind]=sum(r[2] for r in selected)
   days={}
   for r in selected:
    d=datetime.fromtimestamp(r[5]/1000,ZoneInfo('Asia/Shanghai')).date();days[str(d)]=days.get(str(d),0)+r[2]
   daily[kind]=sum(days.values())
   if kind!='group_removed':
    grouped={}
    for r in selected:
     key=r[4] if kind=='group_left' else r[3]
     if key:grouped[key]=grouped.get(key,0)+r[2]
    tops[kind]=sorted(grouped.values(),reverse=True)[:5]
  reports.append({'tenant_id':tid,'range':[str(first),str(last)],'metrics':metrics,'daily_totals':daily,'metrics_equal_daily_totals':metrics==daily,'top_counts_only':tops,'real_page_sample':{'records':len(rows),'max_records_per_event_type':max((sum(r[1]==k for r in rows) for k in set(r[1] for r in rows)),default=0),'unique_ids':len({r[0] for r in rows})==len(rows)}})
 print(json.dumps({'mode':'read-only SQL baseline on actual production events; not client API performance test','statistics':reports}))
