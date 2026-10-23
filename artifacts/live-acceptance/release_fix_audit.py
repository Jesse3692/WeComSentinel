import hashlib,json,sqlite3
from pathlib import Path
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 users=list(db.execute("select id,role from app_users where enabled=1 and ding_corp_id=?",(s.ding_corp_id,)))
 real="coalesce(json_extract(snapshot,'$.demo'),0)!=1"
 codes=list(db.execute('select add_way,count(*) n from business_events where tenant_id=3 and event_type=? and '+real+' group by add_way',('contact_added',)))
 report={'business_events':db.execute('select count(*) from business_events').fetchone()[0], 'real_events':db.execute('select count(*) from business_events where '+real).fetchone()[0], 'media_count':db.execute('select count(*) from media_assets').fetchone()[0],'integrity':db.execute('pragma quick_check').fetchone()[0], 'ordinary_grant_counts':[db.execute('select count(*) from user_tenants where user_id=?',(u['id'],)).fetchone()[0] for u in users if u['role']=='user'], 'real_added_source_codes':{str(r['add_way']):r['n'] for r in codes},'app_api_sha256':hashlib.sha256(Path('app/api.py').read_bytes()).hexdigest()}
 media=[]
 for r in db.execute('select relative_path,sha256 from media_assets'):
  f=Path(s.media_dir)/r['relative_path'];media.append({'exists':f.is_file(),'matches_stored_hash':f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest()==r['sha256']})
 report['media_integrity']=media
 print(json.dumps(report))
