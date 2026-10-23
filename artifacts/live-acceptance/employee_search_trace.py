import json,re,sqlite3
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from datetime import datetime
from zoneinfo import ZoneInfo
from app.config import Settings
s=Settings();dbfile=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve();log=Path('uvicorn.log');out={'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'log_exists':log.exists()}
if log.exists():
 with log.open('rb') as f:
  f.seek(max(0,log.stat().st_size-700000));lines=f.read().decode('utf-8',errors='replace').splitlines()
 rows=[]
 for line in lines:
  m=re.search(r'GET (\S*/api/v1/employees\S*) HTTP/[^" ]+"\s+(\d+)',line)
  if not m:continue
  q=parse_qs(urlsplit(m.group(1)).query,keep_blank_values=True);word=q.get('keyword',[''])[0]
  rows.append({'status':int(m.group(2)),'tenant_id':q.get('tenant_id',[''])[0] if q.get('tenant_id',[''])[0] in ('all','1','2','3') else 'redacted','has_keyword': 'keyword' in q,'keyword_empty':word=='','matches_ascii_test':word=='zzzz_no_employee_20261023','matches_impossible_test':word=='\u9a8c\u6536\u4e0d\u5b58\u5728\u5458\u5de520261023','keyword_character_count':len(word)})
 out['recent_employee_requests']=rows[-20:];out['ascii_test_request_count_in_tail']=sum(r['matches_ascii_test'] for r in rows);out['impossible_test_request_count_in_tail']=sum(r['matches_impossible_test'] for r in rows)
with sqlite3.connect(dbfile.as_uri()+'?mode=ro',uri=True) as db:
 query="""WITH identities AS (
 SELECT tenant_id,external_id AS employee_id FROM profiles WHERE kind='employee' AND tenant_id=3
 UNION SELECT tenant_id,employee_id FROM business_events WHERE tenant_id=3 AND employee_id IS NOT NULL)
 SELECT count(*) FROM identities i LEFT JOIN profiles p ON p.tenant_id=i.tenant_id AND p.kind='employee' AND p.external_id=i.employee_id
 WHERE instr(coalesce(nullif(nullif(p.name,''),'\u672a\u77e5'),i.employee_id),?)>0 OR instr(i.employee_id,?)>0"""
 out['actual_database_employee_option_counts']={'blank_keyword':db.execute(query,('','')).fetchone()[0],'impossible_keyword':db.execute(query,('\u9a8c\u6536\u4e0d\u5b58\u5728\u5458\u5de520261023','\u9a8c\u6536\u4e0d\u5b58\u5728\u5458\u5de520261023')).fetchone()[0]}
print(json.dumps(out))
