import asyncio,json
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Profile
async def main():
 s=Settings();engine,sessions=make_database(s)
 try:
  async with sessions() as db:
   rows=[r for r in (await db.scalars(select(BusinessEvent))).all() if not r.snapshot.get('demo')]
   photos=[p for p in (await db.scalars(select(Profile).where(Profile.kind=='customer'))).all() if p.data.get('media_id')]
   result=[]
   for r in rows:
    if r.event_type!='group_change_unknown':continue
    members=r.snapshot.get('member_candidates',[])
    result.append({'event_id':r.id,'resolution':r.resolution,'reason':r.snapshot.get('classification_reason'),'evidence':r.snapshot.get('member_evidence'),'quantity':r.quantity,'members':[{'type':m.get('member_type'),'has_media':bool(m.get('media_id')),'same_id_as_photo_customer':any(p.tenant_id==r.tenant_id and p.external_id==m.get('member_id') for p in photos),'same_name_as_photo_customer':any(p.tenant_id==r.tenant_id and p.data.get('name')==m.get('name') for p in photos)} for m in members]})
   print(json.dumps(result))
 finally:await engine.dispose()
asyncio.run(main())
