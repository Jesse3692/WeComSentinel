import hashlib,json,sqlite3,time
from collections import Counter
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from app.config import Settings
from scripts.check_callbacks import safe_error
s=Settings(); path=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
now=int(time.time()*1000)
def stamp(v):return datetime.fromtimestamp(v/1000,ZoneInfo('Asia/Shanghai')).isoformat() if v else None
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
 all_events=db.execute('select * from business_events').fetchall()
 real=[e for e in all_events if not json.loads(e['snapshot']).get('demo')]
 real_tenants=sorted({e['tenant_id'] for e in real})
 tenants=db.execute('select * from tenants').fetchall()
 profiles=db.execute('select * from profiles').fetchall()
 media=db.execute('select * from media_assets').fetchall();media_by_id={m['id']:m for m in media}
 refs=[]
 for e in real:
  snap=json.loads(e['snapshot'])
  refs.extend((e['tenant_id'],snap[k]) for k in ('customer_media_id','employee_media_id') if snap.get(k))
  refs.extend((e['tenant_id'],m['media_id']) for m in snap.get('member_candidates',[]) if m.get('media_id'))
 media_check=[]
 for m in media:
  f=(Path(s.media_dir)/m['relative_path']).resolve();inside=f.is_relative_to(Path(s.media_dir).resolve());present=inside and f.is_file()
  media_check.append({'media_id':m['id'],'tenant_id':m['tenant_id'],'present':present,'size_matches':present and f.stat().st_size==m['size_bytes'],'sha256_matches':present and hashlib.sha256(f.read_bytes()).hexdigest()==m['sha256']})
 jobs=db.execute('select * from processing_jobs').fetchall()
 callbacks={r['id']:r for r in db.execute('select id,tenant_id,received_at,status from callback_inbox')}
 latencies=[(e['created_at']-callbacks[e['callback_id']]['received_at'])/1000 for e in real if e['callback_id'] in callbacks]
 ages=[(callbacks[e['callback_id']]['received_at']-e['occurred_at'])/1000 for e in real if e['callback_id'] in callbacks]
 groups=[p for p in profiles if p['tenant_id'] in real_tenants and p['kind']=='group']
 members=[m for p in groups for m in json.loads(p['data']).get('member_list',[])]
 sessions=db.execute('select expires_at,revoked from auth_sessions').fetchall()
 audits=db.execute('select action,created_at from audit_logs order by id desc limit 20').fetchall()
 def timings(values):return {'n':len(values),'min_seconds':round(min(values),3),'max_seconds':round(max(values),3),'over_60_seconds':sum(v>=60 for v in values)} if values else {'n':0}
 out={'checked_at':stamp(now),'runtime':{'production':s.app_env=='production','demo_disabled':not s.demo_enabled,'secure_cookie_configured':s.cookie_secure,'public_origin_https':s.public_origin.startswith('https://'),'worker_enabled':s.worker_enabled,'dingtalk_configured':bool(s.ding_corp_id and s.ding_client_id and s.ding_client_secret)},
 'database':{'quick_check':db.execute('pragma quick_check').fetchone()[0],'foreign_key_violation_count':len(db.execute('pragma foreign_key_check').fetchall()),'all_event_count':len(all_events),'real_event_count':len(real),'demo_event_count':len(all_events)-len(real),'enabled_tenant_count':sum(t['enabled'] for t in tenants),'enabled_real_tenant_ids':[t['id'] for t in tenants if t['enabled'] and t['id'] in real_tenants],'enabled_demo_event_count':sum(bool(json.loads(e['snapshot']).get('demo')) for e in all_events if any(t['id']==e['tenant_id'] and t['enabled'] for t in tenants)), 'real_by_type':dict(Counter(e['event_type'] for e in real)),'real_enrichment':dict(Counter(e['enrichment_status'] for e in real)), 'real_first_event_at':stamp(min((e['occurred_at'] for e in real),default=None)),'real_latest_event_at':stamp(max((e['occurred_at'] for e in real),default=None)), 'latest_callback_id':max(callbacks,default=0),'latest_event_id':max((e['id'] for e in all_events),default=0)},
 'media':{'files':media_check,'real_reference_count':len(refs),'missing_reference_count':sum(mid not in media_by_id for tid,mid in refs),'cross_tenant_reference_count':sum(mid in media_by_id and media_by_id[mid]['tenant_id']!=tid for tid,mid in refs)},
 'group_cache':{'group_count':len(groups),'external_memberships':sum(str(m.get('type'))=='2' for m in members),'internal_memberships':sum(str(m.get('type'))=='1' for m in members),'cached_member_avatars':sum(bool(m.get('media_id')) for m in members),'avatar_states':dict(Counter(m.get('avatar_state','unrecorded') for m in members)),'oldest_synced_at':stamp(min((p['synced_at'] for p in groups),default=None))},
 'jobs':{'real_tenants_status':dict(Counter(j['status'] for j in jobs if j['tenant_id'] in real_tenants)),'real_tenants_errors':dict(Counter(safe_error(j['last_error']) for j in jobs if j['tenant_id'] in real_tenants and j['last_error']))},
 'historical_timing':{'receive_to_initial_event_insert':timings(latencies),'event_to_receive':timings(ages),'limitation':'created_at is initial insert only; not final enrichment or client visibility'},
 'sessions':{'active_unrevoked':sum(not r['revoked'] and r['expires_at']>now for r in sessions),'latest_expiry_at':stamp(max((r['expires_at'] for r in sessions),default=None))},
 'recent_audit_actions':[{'action':r['action'] if r['action'] in ('login','logout','tenant_create','tenant_update','grant_update') else 'other','at':stamp(r['created_at'])} for r in audits]}
print(json.dumps(out,ensure_ascii=False))
