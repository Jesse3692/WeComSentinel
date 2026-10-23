import json,sqlite3
from pathlib import Path
from app.config import Settings
from app.security import parse_xml
s=Settings();p=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
 row=db.execute('select event_type,quantity,resolution,snapshot from business_events where id=1806').fetchone()
backup=sorted(Path('backups').glob('deploy-*/monitor.db'))[-1]
with sqlite3.connect(backup.resolve().as_uri()+'?mode=ro',uri=True) as db:
 old=db.execute('select event_type,quantity,resolution,snapshot from business_events where id=1806').fetchone()
parsed=parse_xml('<xml><MemChangeList><Item><![CDATA[test-only-not-enqueued]]></Item></MemChangeList></xml>')
print(json.dumps({'target_event_unchanged':row==old,'target_member_type':json.loads(row[3])['member_candidates'][0]['member_type'],'target_has_avatar':bool(json.loads(row[3])['member_candidates'][0].get('media_id')),'deployed_parser_preserves_text':parsed['MemChangeList']==['test-only-not-enqueued'],'deployed_conflict_guard':'callback_snapshot_conflict' in Path('app/worker.py').read_text(),'backup_exists':backup.exists()}))
