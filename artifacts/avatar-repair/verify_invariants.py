import json,sqlite3,hashlib
from pathlib import Path
from app.config import Settings
s=Settings();dbpath=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
backup=sorted(Path('backups').glob('avatar-*/monitor.db'))[-1]
columns='id,tenant_id,event_type,quantity,occurred_at,resolution,customer_id,employee_id,group_id,operator_id,deletion_direction,add_way'
with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
 after=db.execute('select '+columns+' from business_events order by id').fetchall()
with sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True) as db:
 before=db.execute('select '+columns+' from business_events order by id').fetchall()
print(json.dumps({'business_fields_unchanged':before==after,'event_count_before':len(before),'event_count_after':len(after),'deployed_http_normalization':'normalize_avatar_url' in Path('app/media.py').read_text(),'backfill_script_present':Path('scripts/backfill_avatars.py').is_file()}))
