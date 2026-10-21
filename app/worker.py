import asyncio
import hashlib
import json
import logging
import random

from cryptography.fernet import Fernet
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError

from .media import save_avatar
from .models import BusinessEvent, Inbox, Job, Profile, Tenant, now_ms
from .platforms import PlatformError

logger = logging.getLogger(__name__)


async def enqueue(db, settings, tenant_id, payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = hashlib.sha256(canonical.encode()).hexdigest()
    existing = await db.scalar(select(Inbox).where(Inbox.tenant_id == tenant_id, Inbox.dedupe_key == key))
    if existing:
        return existing.id
    item = Inbox(tenant_id=tenant_id, dedupe_key=key,
                 payload_encrypted=Fernet(settings.payload_key.encode()).encrypt(canonical.encode()))
    db.add(item)
    try:
        await db.flush()
        db.add(Job(tenant_id=tenant_id, callback_id=item.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(Inbox).where(Inbox.tenant_id == tenant_id, Inbox.dedupe_key == key))
        if not existing:
            raise
        return existing.id
    return item.id


class Worker:
    def __init__(self, settings, sessions, platforms):
        self.settings, self.sessions, self.platforms = settings, sessions, platforms
        self.stopping = asyncio.Event()
        self.last_tick = now_ms()

    async def run(self):
        while not self.stopping.is_set():
            self.last_tick = now_ms()
            try:
                processed = await self.tick()
            except Exception:
                logger.exception("worker_tick_failed")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(self.stopping.wait(), 1)
                except TimeoutError:
                    pass

    async def tick(self):
        now = now_ms()
        eligible = or_(and_(Job.status.in_(["pending", "retry"]), Job.next_run_at <= now),
                       and_(Job.status == "running", Job.lease_until < now))
        async with self.sessions() as db:
            job_id = await db.scalar(select(Job.id).where(eligible).order_by(Job.id).limit(1))
            if job_id is None:
                return False
            result = await db.execute(update(Job).where(Job.id == job_id, eligible).values(
                status="running", lease_until=now + 180_000, attempts=Job.attempts + 1))
            await db.commit()
            if not result.rowcount:
                return False
            job = await db.get(Job, job_id)
            inbox = await db.get(Inbox, job.callback_id)
            tenant = await db.get(Tenant, job.tenant_id)
            payload = json.loads(Fernet(self.settings.payload_key.encode()).decrypt(inbox.payload_encrypted))
        error = None
        try:
            if not tenant.enabled:
                raise PlatformError("tenant_disabled")
            async with asyncio.timeout(120):
                complete = await self.normalize(tenant, inbox, payload)
            if not complete:
                error = "profile_incomplete"
        except Exception as exc:
            # Never put upstream request URLs or credentials into database errors.
            error = str(exc) if isinstance(exc, PlatformError) else type(exc).__name__
        async with self.sessions() as db:
            job = await db.get(Job, job_id)
            job.lease_until = None
            job.last_error = error
            job.status = "done" if not error else ("failed" if job.attempts >= 5 else "retry")
            job.next_run_at = now_ms() + min(300_000, 2000 * 2 ** job.attempts) + random.randint(0, 1000)
            inbox = await db.get(Inbox, job.callback_id)
            inbox.status = job.status
            await db.commit()
        return True

    async def cached(self, tenant_id, kind, external_id):
        if not external_id:
            return None
        async with self.sessions() as db:
            return await db.scalar(select(Profile).where(Profile.tenant_id == tenant_id,
                                   Profile.kind == kind, Profile.external_id == external_id))

    async def put_profile(self, tenant_id, kind, external_id, data):
        async with self.sessions() as db:
            row = await db.scalar(select(Profile).where(Profile.tenant_id == tenant_id,
                                  Profile.kind == kind, Profile.external_id == external_id))
            if row is None:
                row = Profile(tenant_id=tenant_id, kind=kind, external_id=external_id)
                db.add(row)
            row.name = data.get("name") or "未知"
            row.data = data
            row.synced_at = now_ms()
            await db.commit()

    async def get_profile(self, tenant, kind, external_id, force=False):
        cached = await self.cached(tenant.id, kind, external_id)
        if not external_id:
            return {}, False
        if cached and not force and cached.synced_at > now_ms() - 300_000:
            if not cached.data.get("avatar") or cached.data.get("media_id"):
                return cached.data, True
        try:
            if kind == "employee":
                result = await self.platforms.employee(tenant, external_id)
            else:
                result = await self.platforms.customer(tenant, external_id)
                result = {**result["external_contact"], "follow_user": result.get("follow_user", [])}
            url = result.get("avatar")
            if url:
                media_id = await save_avatar(self.settings, self.sessions, tenant.id, url)
                result["media_id"] = media_id
            await self.put_profile(tenant.id, kind, external_id, result)
            return result, not url or bool(result.get("media_id"))
        except PlatformError as exc:
            # A removed relationship may no longer be accessible. A snapshot is valid historical data.
            if cached:
                complete = not cached.data.get("avatar") or bool(cached.data.get("media_id"))
                return {**cached.data, "sync_error": str(exc)}, complete
            return {"sync_error": str(exc)}, False

    async def write_events(self, tenant_id, callback_id, records):
        async with self.sessions() as db:
            for record in records:
                item = await db.scalar(select(BusinessEvent).where(BusinessEvent.tenant_id == tenant_id,
                                      BusinessEvent.callback_id == callback_id,
                                      BusinessEvent.item_key == record["item_key"]))
                if item is None:
                    item = BusinessEvent(tenant_id=tenant_id, callback_id=callback_id, **record)
                    db.add(item)
                else:
                    for key, value in record.items():
                        setattr(item, key, value)
            await db.commit()

    async def normalize(self, tenant, inbox, payload):
        change = payload.get("ChangeType")
        occurred = int(payload["CreateTime"]) * 1000
        if payload.get("Event") == "change_external_contact":
            types = {"add_external_contact": "contact_added", "del_external_contact": "contact_deleted",
                     "del_follow_user": "contact_deleted"}
            if change not in types:
                if change == "edit_external_contact":
                    await self.get_profile(tenant, "customer", payload.get("ExternalUserID"), force=True)
                return True
            employee_id, customer_id = payload.get("UserID"), payload.get("ExternalUserID")
            employee, ok1 = await self.get_profile(tenant, "employee", employee_id)
            customer, ok2 = await self.get_profile(tenant, "customer", customer_id)
            relation = next((r for r in customer.get("follow_user", []) if r.get("userid") == employee_id), {})
            direction = {"del_external_contact": "employee_deleted_customer",
                         "del_follow_user": "customer_deleted_employee"}.get(change)
            await self.write_events(tenant.id, inbox.id, [{
                "item_key": "contact", "event_type": types[change], "occurred_at": occurred,
                "employee_id": employee_id, "customer_id": customer_id,
                "deletion_direction": direction, "add_way": str(relation["add_way"]) if "add_way" in relation else None,
                "channel_state": payload.get("State"), "quantity": 1,
                "enrichment_status": "complete" if ok1 and ok2 else "partial",
                "snapshot": {"employee_name": employee.get("name", "未知员工"),
                             "employee_media_id": employee.get("media_id"),
                             "customer_name": customer.get("name", "未知客户"),
                             "customer_media_id": customer.get("media_id"),
                             "customer_type": customer.get("type"), "gender": customer.get("gender"),
                             "company_name": customer.get("corp_name")},
            }])
            if not (ok1 and ok2):
                reasons = []
                if not ok1:
                    reasons.append(employee.get("sync_error", "employee_avatar_incomplete"))
                if not ok2:
                    reasons.append(customer.get("sync_error", "customer_avatar_incomplete"))
                raise PlatformError(",".join(reasons))
            return True
        if payload.get("Event") == "change_external_chat":
            chat_id = payload.get("ChatId")
            previous = await self.cached(tenant.id, "group", chat_id)
            current = None
            group_error = None
            try:
                current = await self.platforms.group(tenant, chat_id)
            except PlatformError as exc:
                group_error = str(exc)
            if payload.get("UpdateDetail") == "del_member":
                reported_type = {"0": "group_left", "1": "group_removed"}.get(str(payload.get("QuitScene")), "group_change_unknown")
                quantity = max(1, int(payload.get("MemChangeCnt", "1")))
                # Aggregate is authoritative only about reported count/reason, never guessed names.
                group = current or (previous.data if previous else {})
                await self.write_events(tenant.id, inbox.id, [{
                    "item_key": "group", "event_type": "group_change_unknown", "occurred_at": occurred,
                    "group_id": chat_id, "quantity": quantity, "resolution": "unresolved",
                    "enrichment_status": "partial",
                    "snapshot": {"group_name": group.get("name", "未知群聊"),
                                 "reported_event_type": reported_type,
                                 "note": "仅有平台报告的变更人数，尚未确认具体成员、是否为外部联系人及操作人；不计入已确认退群或被踢指标。"},
                }])
            if current:
                await self.put_profile(tenant.id, "group", chat_id, current)
            if group_error and change != "dismiss":
                raise PlatformError(group_error)
            return True
        return True
