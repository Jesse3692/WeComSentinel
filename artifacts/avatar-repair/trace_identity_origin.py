import asyncio,json
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import select
from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent,Inbox,Profile
async def main():
 s=Settings();engine,sessions=make_database(s)
 try:
  async with sessions() as db:
   row=await db.get(BusinessEvent,1806)
   inbox=await db.get(Inbox,row.callback_id)
   payload=json.loads(Fernet(s.payload_key.encode()).decrypt(inbox.payload_encrypted))
   photo=await db.scalar(select(Profile).where(Profile.tenant_id==row.tenant_id,Profile.kind=='customer',Profile.data['media_id'].as_integer()>0))
   member=row.snapshot['member_candidates'][0]
   previous=row.snapshot.get('_before_members',[])
   target=next((m for m in previous or [] if m.get('userid')==member.get('member_id')),None)
   safe={'target_event':row.id,'callback_id':row.callback_id,'callback_fields':{k:payload.get(k) for k in ['Event','ChangeType','UpdateDetail','QuitScene','MemChangeCnt','CreateTime']},'callback_has_member_ids':any(k in payload for k in ['ExternalUserID','UserID','Members','UserId']),'raw_callback_keys':sorted(payload),'classification_reason':row.snapshot.get('classification_reason'),'snapshot_member_type':member.get('member_type'),'before_snapshot_found':target is not None,'before_member_keys':sorted(target or {}),'before_member_type':(target or {}).get('type'),'same_raw_id_as_photo_customer':bool(photo and (target or {}).get('userid')==photo.external_id),'nonempty_unionid_match':bool(photo and (target or {}).get('unionid') and (target or {}).get('unionid')==photo.data.get('unionid')),'photo_profile_has_unionid':bool(photo and photo.data.get('unionid')),'snapshot_all_members_types':[m.get('member_type') for m in row.snapshot.get('_all_member_candidates',[])]}
   related=(await db.scalars(select(BusinessEvent).where(BusinessEvent.tenant_id==row.tenant_id))).all()
   safe['same_member_other_events']=[{'id':r.id,'type':r.event_type,'member_type':m.get('member_type')} for r in related for m in r.snapshot.get('member_candidates',[]) if m.get('member_id')==member.get('member_id')]
   print(json.dumps(safe))
 finally:await engine.dispose()
asyncio.run(main())
