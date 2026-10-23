import asyncio,json
from pathlib import Path
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Profile
async def main():
 s=Settings();engine,sessions=make_database(s)
 try:
  async with sessions() as db:
   rows=[r for r in (await db.scalars(select(BusinessEvent))).all() if not r.snapshot.get('demo')]
   contact=next((r for r in rows if r.event_type=='contact_added' and r.snapshot.get('customer_media_id')),None)
   profiles=(await db.scalars(select(Profile))).all()
  result=[]
  for r in rows:
   if not r.event_type.startswith('group'):continue
   for m in r.snapshot.get('member_candidates',[]):
    if m.get('member_type')!=2:continue
    ident=m.get('member_id')
    found=[]
    for p in profiles:
     if p.tenant_id!=r.tenant_id:continue
     if p.external_id==ident:found.append({'kind':p.kind,'has_avatar':bool(p.data.get('avatar')),'has_media':bool(p.data.get('media_id'))})
     for member in p.data.get('member_list',[]):
      if member.get('userid')==ident:found.append({'kind':'group_member_cache','has_avatar':bool(member.get('avatar')),'has_media':bool(member.get('media_id'))})
    result.append({'event_id':r.id,'type':r.event_type,'same_customer_as_added':bool(contact and r.tenant_id==contact.tenant_id and ident==contact.customer_id),'has_media':bool(m.get('media_id')),'candidate_photo_url':any(bool(m.get(k)) for k in ('avatar','avatar_url','pic_url')),'cached_sources':found})
  print(json.dumps(result))
 finally:await engine.dispose()
asyncio.run(main())
