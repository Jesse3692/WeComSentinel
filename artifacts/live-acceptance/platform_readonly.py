import asyncio,json,re,sqlite3,time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from app.config import Settings
from app.platforms import Platforms,PlatformError
from scripts.check_callbacks import safe_error
async def main():
 s=Settings();dbpath=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
 with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  real_ids=[r[0] for r in db.execute("select distinct tenant_id from business_events where coalesce(json_extract(snapshot,'$.demo'),0)!=1")]
  tenants=[SimpleNamespace(**dict(r)) for r in db.execute('select * from tenants') if r['enabled'] and r['id'] in real_ids]
  profiles=[dict(r) for r in db.execute('select * from profiles')]
  jobs=[dict(r) for r in db.execute('select * from processing_jobs') if r['tenant_id'] in real_ids and r['status']=='failed']
 p=Platforms(s);reports=[]
 try:
  for t in tenants:
   report={'tenant_id':t.id,'current_groups':[],'customer_probe_results':[],'employee_probe_results':[]}
   try:
    batch=await p.wecom(t,'externalcontact/groupchat/list','POST',body={'limit':100,'cursor':''})
    report['group_list_success']=True;report['group_list_has_more']=bool(batch.get('next_cursor'))
    customers=set();employees=set()
    for n,g in enumerate(batch.get('group_chat_list',[]),1):
     detail=await p.group(t,g['chat_id']);members=detail.get('member_list',[])
     row={'alias':'G'+str(n),'member_count':len(members),'external_members':sum(str(m.get('type'))=='2' for m in members),'internal_members':sum(str(m.get('type'))=='1' for m in members)}
     report['current_groups'].append(row)
     customers.update(m['userid'] for m in members if str(m.get('type'))=='2' and m.get('userid'))
     employees.update(m['userid'] for m in members if str(m.get('type'))=='1' and m.get('userid'))
    for kind,identities in [('customer',customers),('employee',employees)]:
     for identity in sorted(identities)[:5]:
      try:
       result=await (p.customer(t,identity) if kind=='customer' else p.employee(t,identity));data=result.get('external_contact',{}) if kind=='customer' else result
       cached=next((json.loads(r['data']) for r in profiles if r['tenant_id']==t.id and r['kind']==kind and r['external_id']==identity),{})
       report[kind+'_probe_results'].append({'success':True,'avatar_provided':bool(data.get('avatar')),'thumb_avatar_provided':bool(data.get('thumb_avatar')),'existing_cached_media':bool(cached.get('media_id')),'existing_pending':bool(cached.get('avatar_refresh_pending'))})
      except PlatformError as exc:report[kind+'_probe_results'].append({'success':False,'code':str(exc) if re.fullmatch(r'platform_error_-?\d+',str(exc)) else 'transport_or_configuration_error'})
   except PlatformError as exc:report['group_list_success']=False;report['error']=str(exc) if re.fullmatch(r'platform_error_-?\d+',str(exc)) else 'transport_or_configuration_error'
   reports.append(report)
  errors=Counter(j['last_error'] if j.get('last_error') in ('group_member_avatar_incomplete','group_member_profile_incomplete','profile_incomplete','contact_identity_fields_missing','customer_relation_add_way_missing') or re.fullmatch(r'platform_error_-?\d+',j.get('last_error') or '') else 'redacted_other_error' for j in jobs)
  print(json.dumps({'mode':'real platform read-only calls; no profile/event writes','tenants':reports,'failed_job_errors':dict(errors)}))
 finally:await p.close()
asyncio.run(main())
