import json,sqlite3,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
def stamp(v):return datetime.fromtimestamp(v/1000,ZoneInfo('Asia/Shanghai')).isoformat() if v else None
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
 rows=db.execute('select * from business_events where id>1813 order by id').fetchall()
 report=[]
 for e in rows:
  if json.loads(e['snapshot']).get('demo'):continue
  snap=json.loads(e['snapshot']);inbox=db.execute('select * from callback_inbox where id=?',(e['callback_id'],)).fetchone();job=db.execute('select status,attempts from processing_jobs where callback_id=?',(e['callback_id'],)).fetchone()
  payload=json.loads(Fernet(s.payload_key.encode()).decrypt(inbox['payload_encrypted']));members=snap.get('member_candidates',[]);before={m.get('userid'):m for m in snap.get('_before_members',[]) or []}
  safe={'event_id':e['id'],'callback_id':e['callback_id'],'type':e['event_type'],'quantity':e['quantity'],'occurred_at':stamp(e['occurred_at']),'received_at':stamp(inbox['received_at']),'created_at':stamp(e['created_at']),'receive_to_insert_seconds':round((e['created_at']-inbox['received_at'])/1000,3),'resolution':e['resolution'],'enrichment':e['enrichment_status'],'job_status':job['status'] if job else None,'evidence':snap.get('member_evidence'),'classification':snap.get('classification_reason'),'reported_quit_scene':str(payload.get('QuitScene','')),'candidate_count':len(members),'candidate_types':[m.get('member_type') for m in members],'media_ids':[m.get('media_id') for m in members], 'cached_before_identity_and_media_match':bool(members) and all(m.get('member_id') in before and str(before[m['member_id']].get('type'))==str(m.get('member_type')) and bool(m.get('media_id')) and before[m['member_id']].get('media_id')==m['media_id'] for m in members),'operator_provided':bool(e['operator_id'])}
  report.append(safe)
 print(json.dumps({'checked_at':stamp(int(time.time()*1000)),'baseline_event_id':1813,'new_real_events':report}))
