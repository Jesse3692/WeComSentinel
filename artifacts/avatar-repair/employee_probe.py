import asyncio,json
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.platforms import Platforms,PlatformError
from app.models import BusinessEvent,Tenant
async def main():
 s=Settings();engine,sessions=make_database(s);platform=Platforms(s)
 try:
  async with sessions() as db:
   rows=(await db.scalars(select(BusinessEvent).where(BusinessEvent.employee_id.is_not(None)).order_by(BusinessEvent.id.desc()).limit(30))).all()
   pairs={(r.tenant_id,r.employee_id) for r in rows if not r.snapshot.get('demo')}
   tenants={t.id:t for t in (await db.scalars(select(Tenant))).all()}
  for tid,eid in pairs:
   try:
    d=await platform.employee(tenants[tid],eid)
    print(json.dumps({'employee_lookup':'success','has_avatar':bool(d.get('avatar')),'has_thumb_avatar':bool(d.get('thumb_avatar')),'has_name':bool(d.get('name'))}))
   except PlatformError as e:
    import re
    print(json.dumps({'employee_lookup':'failed','platform_code':re.findall(r'\b\d{3,8}\b',str(e))[:1]}))
 finally:await platform.close();await engine.dispose()
asyncio.run(main())
