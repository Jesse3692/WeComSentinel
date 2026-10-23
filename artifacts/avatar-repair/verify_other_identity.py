import asyncio,json,re
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Tenant
from app.platforms import Platforms,PlatformError
async def main():
 s=Settings();engine,sessions=make_database(s);p=Platforms(s)
 try:
  async with sessions() as db:
   r=await db.get(BusinessEvent,1806)
   t=await db.get(Tenant,r.tenant_id)
   m=r.snapshot['member_candidates'][0]
  try:
   d=await p.employee(t,m['member_id'])
   print(json.dumps({'employee_api':'success','returned_same_userid':d.get('userid')==m['member_id'],'has_avatar':bool(d.get('avatar')),'has_thumb_avatar':bool(d.get('thumb_avatar')),'snapshot_member_type':m.get('member_type')}))
  except PlatformError as e:print(json.dumps({'employee_api':'failed','codes':re.findall(r'-?\d+',str(e))[:1]}))
 finally:await p.close();await engine.dispose()
asyncio.run(main())
