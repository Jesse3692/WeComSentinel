import asyncio,json
from datetime import date
from types import SimpleNamespace
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from fastapi import HTTPException
from app.config import Settings
from app.models import User
from app.api import events,allowed
async def main():
 s=Settings();engine=create_async_engine(s.database_url);sessions=async_sessionmaker(engine,expire_on_commit=False)
 try:
  async with sessions() as db:
   await db.execute(text('PRAGMA query_only=ON'))
   admin=await db.scalar(select(User).where(User.ding_corp_id==s.ding_corp_id,User.role=='admin',User.enabled.is_(True)))
   ordinary=await db.scalar(select(User).where(User.ding_corp_id==s.ding_corp_id,User.role=='user',User.enabled.is_(True)))
   request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=s)))
   counts={}
   for way in ['unknown','other',None]:
    result=await events(request,tenant_id='3',start_date=date(2025,10,23),end_date=date(2026,10,23),event_type='contact_added',employee_id=None,employee_tenant_id=None,add_way=way,group_keyword=None,cursor=None,limit=20,user=admin,db=db)
    counts[way or 'all']=result['data']['total_records']
   try: await allowed(db,ordinary,'all');denied=False
   except HTTPException as exc: denied=exc.status_code==403
   assert counts=={'unknown':1,'other':0,'all':3},'unexpected real source counts'
   assert denied,'zero grants must be denied'
   assert await allowed(db,ordinary,allow_empty=True)==[]
   print(json.dumps({'method':'read-only deployed handler invocation; not authenticated HTTP or DingTalk client','real_added_counts':counts,'zero_grants_business_denied_403':denied,'empty_profile_context_allowed':True}))
 finally: await engine.dispose()
asyncio.run(main())
