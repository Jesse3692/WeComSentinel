import json,sqlite3
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from cryptography.fernet import Fernet
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
def stamp(v):return datetime.fromtimestamp(v/1000,ZoneInfo('Asia/Shanghai')).isoformat() if v else None
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 inbox=db.execute('select * from callback_inbox where id=1832').fetchone();payload=json.loads(Fernet(s.payload_key.encode()).decrypt(inbox['payload_encrypted']))
 job=db.execute('select status,attempts from processing_jobs where callback_id=1832').fetchone()
 departed=db.execute('select group_id,snapshot from business_events where id=1813').fetchone();old=json.loads(departed['snapshot'])['member_candidates'][0]
 profile=db.execute("select data,synced_at from profiles where tenant_id=? and kind='group' and external_id=?",(inbox['tenant_id'],payload.get('ChatId'))).fetchone()
 matches=[m for m in json.loads(profile['data']).get('member_list',[]) if m.get('userid')==old['member_id']]
 print(json.dumps({'callback_id':1832,'received_at':stamp(inbox['received_at']),'event':payload.get('Event'),'change_type':payload.get('ChangeType'),'update_detail':payload.get('UpdateDetail'),'job_status':job['status'],'same_group_as_departure':payload.get('ChatId')==departed['group_id'],'same_member_id_matches':len(matches)==1,'member_type':matches[0].get('type') if matches else None,'media_id':matches[0].get('media_id') if matches else None,'same_media_as_before':bool(matches) and matches[0].get('media_id')==old.get('media_id'),'avatar_state':matches[0].get('avatar_state') if matches else None,'group_synced_at':stamp(profile['synced_at']),'business_events_from_join_callback':db.execute('select count(*) from business_events where callback_id=1832').fetchone()[0],'manual_sync_after_rejoin':False}))
