import json,sqlite3
from pathlib import Path
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 rows=list(db.execute("select id,role,enabled,ding_corp_id from app_users where ding_corp_id != 'DEMO' order by id"))
 output=[]
 for u in rows:
  grants=list(db.execute('select tenant_id from user_tenants where user_id=?',(u['id'],)))
  output.append({'role':u['role'],'enabled':bool(u['enabled']),'corp_matches_config':u['ding_corp_id']==s.ding_corp_id,'corp_matches_real_admin':any(a['role']=='admin' and a['ding_corp_id']==u['ding_corp_id'] for a in rows),'grant_count':len(grants),'only_real_test_tenant':len(grants)==1 and grants[0][0]==3})
 print(json.dumps({'non_demo_users':output}))
