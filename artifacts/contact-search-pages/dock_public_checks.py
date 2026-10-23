import hashlib,json,re,socket,ssl,time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import httpx
origin='https://test.nakeshijia.com'
out={'checked_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'http':{},'assets':[],'auth_boundaries':[]}
with httpx.Client(follow_redirects=False,timeout=20) as c:
 r=c.get(origin.replace('https:','http:'));out['http']={'status':r.status_code,'redirects_to_https':r.headers.get('location','').startswith('https://')}
 r=c.get(origin+'/readyz');out['readyz']={'status':r.status_code,'ready':r.json().get('status')=='ready'}
 r=c.get(origin+'/api/v1/config');d=r.json().get('data',{});out['public_config']={'status':r.status_code,'demo_disabled':not d.get('demo_enabled'),'ding_configured':d.get('ding_configured')}
 r=c.get(origin);out['frontend']={'status':r.status_code,'mime':r.headers.get('content-type','')}
 for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"',r.text):
  res=c.get(origin+asset);p=Path('frontend/dist')/asset.lstrip('/');out['assets'].append({'asset':asset,'status':res.status_code,'matches_local':p.exists() and hashlib.sha256(res.content).digest()==hashlib.sha256(p.read_bytes()).digest()})
 for path in ['/api/v1/me','/api/v1/dashboard','/api/v1/events','/api/v1/employees','/api/v1/media/1','/api/v1/admin/tenants','/api/v1/admin/users','/api/v1/admin/jobs']:
  r=c.get(origin+path);out['auth_boundaries'].append({'path':path,'unauthenticated_status':r.status_code})
 r=c.post(origin+'/api/v1/auth/demo',json={'password':'acceptance-invalid-not-a-secret','role':'admin'});out['demo_login_disabled']={'status':r.status_code,'session_cookie_issued':'monitor_session' in r.cookies}
context=ssl.create_default_context()
with socket.create_connection(('test.nakeshijia.com',443),timeout=15) as sock:
 with context.wrap_socket(sock,server_hostname='test.nakeshijia.com') as tls:
  cert=tls.getpeercert();out['tls']={'verified':True,'protocol':tls.version(),'certificate_expiry':cert.get('notAfter')}
Path('artifacts/contact-search-pages/dock-public-checks.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
