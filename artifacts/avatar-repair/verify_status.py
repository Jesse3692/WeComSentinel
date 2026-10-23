import asyncio,json,re
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Tenant,Profile,Media
from app.platforms import Platforms,PlatformError
from pathlib import Path
async def main():
 s=Settings();engine,sessions=make_database(s);p=Platforms(s)
 try:
  async with sessions() as db:
   rows=[r for r in (await db.scalars(select(BusinessEvent))).all() if not r.snapshot.get('demo')]
   tenants={t.id:t for t in (await db.scalars(select(Tenant))).all()}
   assets=(await db.scalars(select(Media))).all()
   print(json.dumps({'media_records':len(assets),'media_files':sum((Path(s.media_dir)/m.relative_path).is_file() for m in assets),'real_events':[{'id':r.id,'type':r.event_type,'contact_photo':bool(r.snapshot.get('customer_media_id')),'member_types':[m.get('member_type') for m in r.snapshot.get('member_candidates',[])],'member_photos':[bool(m.get('media_id')) for m in r.snapshot.get('member_candidates',[])]} for r in rows]}))
   pairs={(r.tenant_id,m['member_id']) for r in rows for m in r.snapshot.get('member_candidates',[]) if m.get('member_type')==2 and m.get('member_id') and not m.get('media_id')}
  for tid,identity in pairs:
   try:
    d=await p.customer(tenants[tid],identity)
    print(json.dumps({'missing_customer_probe':'success','has_avatar':bool(d.get('external_contact',{}).get('avatar'))}))
   except PlatformError as e:print(json.dumps({'missing_customer_probe':'failed','code':re.findall(r'-?\d+',str(e))[:1]}))
 finally:await p.close();await engine.dispose()
asyncio.run(main())
