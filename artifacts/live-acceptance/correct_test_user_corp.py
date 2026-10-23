import asyncio,json,sqlite3,time
from pathlib import Path
from app.config import Settings
from app.platforms import Platforms
async def main():
 s=Settings();path=Path(s.database_url.removeprefix('sqlite+aiosqlite:///')).resolve()
 with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
  db.row_factory=sqlite3.Row
  rows=list(db.execute("select * from app_users where role='user' and enabled=1 and ding_corp_id!='DEMO' and ding_corp_id!=?",(s.ding_corp_id,)))
  assert len(rows)==1,'expected exactly one misconfigured ordinary user'
  u=dict(rows[0]);oldcorp=db.execute('select corp_id from tenants where id=3').fetchone()[0]
  assert u['ding_corp_id']==oldcorp,'unexpected existing corp'
  assert [r[0] for r in db.execute('select tenant_id from user_tenants where user_id=?',(u['id'],))]==[3],'unexpected grants'
 p=Platforms(s)
 try:
  tok=await p.request('POST','https://api.dingtalk.com/v1.0/oauth2/accessToken',json={'appKey':s.ding_client_id,'appSecret':s.ding_client_secret})
  info=await p.request('POST','https://oapi.dingtalk.com/topapi/v2/user/get',params={'access_token':tok['accessToken']},json={'userid':u['ding_user_id']})
  assert info['result']['userid']==u['ding_user_id'],'identity mismatch'
 finally: await p.close()
 with sqlite3.connect(path) as db:
  db.execute('pragma foreign_keys=on');db.execute('begin immediate')
  assert not db.execute('select 1 from app_users where ding_corp_id=? and ding_user_id=?',(s.ding_corp_id,u['ding_user_id'])).fetchone(),'duplicate identity'
  result=db.execute("update app_users set ding_corp_id=? where id=? and ding_corp_id=? and ding_user_id=? and role='user' and enabled=1",(s.ding_corp_id,u['id'],u['ding_corp_id'],u['ding_user_id']))
  assert result.rowcount==1,'concurrent change'
  assert [r[0] for r in db.execute('select tenant_id from user_tenants where user_id=?',(u['id'],))]==[3],'grants changed'
  db.execute('insert into audit_logs(actor_user_id,action,target,created_at) values(NULL,?,?,?)',('acceptance.user_corp_correct',str(u['id']),int(time.time()*1000)))
  db.commit()
 print(json.dumps({'platform_userid_verified':True,'corrected_users':1,'role':'user','grants_unchanged':True,'only_real_test_tenant':True,'audit_recorded':True}))
asyncio.run(main())
