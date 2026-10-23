import json,sqlite3
from pathlib import Path
from collections import Counter
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 users=[r for r in db.execute('select id,role,enabled,ding_corp_id from app_users') if r['enabled'] and r['ding_corp_id']==s.ding_corp_id]
 enabled=[r[0] for r in db.execute('select id from tenants where enabled=1')]
 grants=list(db.execute('select user_id,tenant_id from user_tenants'))
 print(json.dumps({'enabled_real_dingtalk_users_by_role':dict(Counter(r['role'] for r in users)),'enabled_tenant_count':len(enabled),'real_ordinary_user_grant_counts':[sum(g[0]==u['id'] and g[1] in enabled for g in grants) for u in users if u['role']=='user'],'limitation':'only existing configured accounts; not proof of unauthorized client login rejection'}))
