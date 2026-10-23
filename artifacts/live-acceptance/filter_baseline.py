import json,sqlite3
from pathlib import Path
from collections import Counter
from app.config import Settings
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 db.row_factory=sqlite3.Row
 rows=[r for r in db.execute('select * from business_events') if not json.loads(r['snapshot']).get('demo')]
 added=[r for r in rows if r['event_type']=='contact_added'];deleted=[r for r in rows if r['event_type']=='contact_deleted']
 print(json.dumps({'real_added_records':len(added),'add_way_counts':dict(Counter(str(r['add_way']) if r['add_way'] is not None else 'null' for r in added)),'distinct_added_employee_identities':len({(r['tenant_id'],r['employee_id']) for r in added}),'deleted_direction_counts':dict(Counter(r['deletion_direction'] or 'not_provided' for r in deleted)),'added_real_customer_media_refs':sum(bool(json.loads(r['snapshot']).get('customer_media_id')) for r in added),'added_real_employee_media_refs':sum(bool(json.loads(r['snapshot']).get('employee_media_id')) for r in added)}))
