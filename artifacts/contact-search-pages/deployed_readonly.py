import asyncio,json
from datetime import date
from types import SimpleNamespace
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.config import Settings
from app.models import User
from app.api import events
async def main():
 s=Settings();engine=create_async_engine(s.database_url);sessions=async_sessionmaker(engine,expire_on_commit=False)
 try:
  async with sessions() as db:
   await db.execute(text('PRAGMA query_only=ON'))
   admin=await db.scalar(select(User).where(User.ding_corp_id==s.ding_corp_id,User.role=='admin',User.enabled.is_(True)))
   request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=s)))
   async def read(kind,keyword='',cursor=None,limit=20,employee_id=None):
    response=await events(request,tenant_id='3',start_date=date(2025,10,23),end_date=date(2026,10,23),event_type=kind,employee_id=employee_id,employee_tenant_id=None,employee_keyword=keyword,add_way=None,group_keyword=None,cursor=cursor,limit=limit,user=admin,db=db)
    return response['data']
   report={}
   for kind in ['contact_added','contact_deleted']:
    whole=await read(kind);sample=whole['items'][0]
    by_id=await read(kind,sample['employee_id']);by_name=await read(kind,sample['snapshot']['employee_name']);none=await read(kind,'zzzz_no_employee_20261023')
    assert sample['id'] in [r['id'] for r in by_id['items']]
    assert sample['id'] in [r['id'] for r in by_name['items']]
    assert none['total_records']==0
    pages=[];page=await read(kind,limit=1)
    while True:
     pages.append(page)
     if not page['next_cursor']:break
     page=await read(kind,cursor=page['next_cursor'],limit=1)
    ids=[r['id'] for p in pages for r in p['items']]
    assert len(ids)==len(set(ids))==whole['total_records']
    assert set(ids)=={r['id'] for r in whole['items']}
    previous_ok=True
    for p in reversed(pages):
     same=await read(kind,cursor=p['page_cursor'],limit=1)
     previous_ok=previous_ok and [r['id'] for r in same['items']]==[r['id'] for r in p['items']]
    assert previous_ok
    report[kind]={'total':whole['total_records'],'name_search_matches':by_name['total_records'],'id_search_matches':by_id['total_records'],'impossible_search_matches':none['total_records'],'read_only_pages_at_limit_1':len(pages),'unique_complete_and_previous_stable':True}
   print(json.dumps({'method':'production read-only handler invocation, real data; not authenticated HTTP or DingTalk client','results':report}))
 finally:await engine.dispose()
asyncio.run(main())
