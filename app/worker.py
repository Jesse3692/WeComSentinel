import asyncio
import hashlib
import json
import logging
import random

from cryptography.fernet import Fernet
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from .media import save_avatar
from .event_classification import group_records
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
                # An avatar failure must not discard an already identified person's name/ID.
                try:
                    result["media_id"] = await save_avatar(self.settings, self.sessions, tenant.id, url)
                except (PlatformError, OSError, ValueError):
                    result["sync_error"] = "avatar_unavailable"
            await self.put_profile(tenant.id, kind, external_id, result)
            return result, not url or bool(result.get("media_id"))
        except PlatformError as exc:
            # A removed relationship may no longer be accessible. A snapshot is valid historical data.
            if cached:
                complete = not cached.data.get("avatar") or bool(cached.data.get("media_id"))
                return {**cached.data, "sync_error": str(exc)}, complete
            return {"sync_error": str(exc)}, False

    async def write_events(self, tenant_id, callback_id, records, replace_group=False):
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
            if replace_group:
                await db.execute(delete(BusinessEvent).where(
                    BusinessEvent.tenant_id == tenant_id, BusinessEvent.callback_id == callback_id,
                    BusinessEvent.item_key.in_(["group", "group_other"]),
                    BusinessEvent.item_key.not_in([record["item_key"] for record in records])))
            await db.commit()

    async def group_snapshot(self, tenant, inbox, payload, previous, current):
        """Retain snapshot evidence independently of the customer business category."""
        before = previous.data if previous else {}
        current = current or {}
        async with self.sessions() as db:
            existing = await db.scalar(select(BusinessEvent).where(
                BusinessEvent.tenant_id == tenant.id, BusinessEvent.callback_id == inbox.id,
                BusinessEvent.item_key == "group"))
            snapshot = dict(existing.snapshot) if existing else {}
        if "_all_member_candidates" in snapshot:
            snapshot["member_candidates"] = snapshot.pop("_all_member_candidates")
        name = current.get("name") or before.get("name") or snapshot.get("group_name")
        snapshot["group_name"] = name or None
        snapshot["group_name_source"] = (
            "group_details" if current.get("name") else "cached" if name else "not_provided")
        snapshot["reported_event_type"] = {"0": "group_left", "1": "group_removed"}.get(
            str(payload.get("QuitScene")), "group_change_unknown")
        owner_id = current.get("owner") or before.get("owner")
        if owner_id:
            owner = await self.cached(tenant.id, "employee", owner_id)
            snapshot["group_owner_id"] = owner_id
            snapshot["group_owner_name"] = owner.name if owner else None
        # A current group list cannot identify a past departure by itself.
        # Retain the original baseline across API failures and delayed group updates.
        # Never replace it with a profile fetched after this event on a retry.
        if "_before_members" not in snapshot and not existing:
            snapshot["_before_members"] = (before.get("member_list") if previous
                and previous.synced_at <= int(payload["CreateTime"]) * 1000 else None)
        if not snapshot.get("member_candidates"):
            snapshot["member_candidates"] = []
            snapshot["member_evidence"] = "no_before_snapshot"
            old_members, new_members = snapshot.get("_before_members"), current.get("member_list")
            if isinstance(old_members, list) and not isinstance(new_members, list):
                snapshot["member_evidence"] = "current_snapshot_unavailable"
            if isinstance(old_members, list) and isinstance(new_members, list):
                old = {m["userid"]: m for m in old_members if m.get("userid")}
                new = {m["userid"]: m for m in new_members if m.get("userid")}
                removed = old.keys() - new.keys()
                count = max(1, int(payload.get("MemChangeCnt", "1")))
                snapshot["member_evidence"] = "snapshot_difference_inconclusive"
                if len(removed) == count and not (new.keys() - old.keys()):
                    for member_id in sorted(removed):
                        member = old[member_id]
                        member_type = str(member.get("type", ""))
                        kind = {"1": "employee", "2": "customer"}.get(member_type)
                        profile = await self.cached(tenant.id, kind, member_id) if kind else None
                        data = profile.data if profile else {}
                        snapshot["member_candidates"].append({
                            "member_id": member_id,
                            "name": data.get("name") or (profile.name if profile and profile.name != "未知" else None) or member.get("name") or None,
                            "member_type": int(member_type) if member_type in ("1", "2") else None,
                            "customer_type": data.get("type") if kind == "customer" else None,
                            "gender": data.get("gender"), "company_name": data.get("corp_name"),
                            "media_id": data.get("media_id"),
                        })
                    snapshot["member_evidence"] = "snapshot_difference"
        # Missing profile fields do not invalidate the member difference. Retry enrichment
        # using retained IDs, without trying to infer the removed members again.
        for member in snapshot.get("member_candidates", []):
            kind = {1: "employee", 2: "customer"}.get(member.get("member_type"))
            if kind and member.get("member_id") and not member.get("name"):
                data, _ = await self.get_profile(tenant, kind, member["member_id"])
                member["name"] = data.get("name") or member.get("name")
                if kind == "customer":
                    member.update(customer_type=data.get("type"), gender=data.get("gender"),
                                  company_name=data.get("corp_name"), media_id=data.get("media_id"))
        return snapshot

    async def normalize(self, tenant, inbox, payload):
        change = payload.get("ChangeType")
        occurred = int(payload["CreateTime"]) * 1000
        if payload.get("Event") == "change_external_contact":
            types = {"add_external_contact": "contact_added", "del_external_contact": "contact_deleted",
                     "del_follow_user": "contact_deleted"}
            if change not in types:
                if change == "edit_external_contact":
                    _, complete = await self.get_profile(tenant, "customer", payload.get("ExternalUserID"), force=True)
                    return complete
                return True
            employee_id, customer_id = payload.get("UserID"), payload.get("ExternalUserID")
            if not employee_id or not customer_id:
                raise PlatformError("contact_identity_fields_missing")
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
                "snapshot": {"employee_name": employee.get("name") or employee_id,
                             "employee_media_id": employee.get("media_id"),
                             "customer_name": customer.get("name") or customer_id,
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
            if change not in ("create", "update", "dismiss"):
                return True
            chat_id = payload.get("ChatId")
            if not chat_id:
                raise PlatformError("group_chat_id_missing")
            previous = await self.cached(tenant.id, "group", chat_id)
            current = None
            group_error = None
            try:
                current = await self.platforms.group(tenant, chat_id)
            except PlatformError as exc:
                group_error = str(exc)
            if change == "update" and payload.get("UpdateDetail") == "del_member":
                try:
                    quantity = int(payload["MemChangeCnt"])
                    if quantity <= 0:
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    raise PlatformError("group_member_count_invalid") from None
                snapshot = await self.group_snapshot(tenant, inbox, payload, previous, current)
                await self.write_events(tenant.id, inbox.id,
                    group_records(snapshot, quantity, occurred, chat_id), replace_group=True)
            if current:
                await self.put_profile(tenant.id, "group", chat_id, current)
            if group_error and change != "dismiss":
                raise PlatformError(group_error)
            if change == "update" and payload.get("UpdateDetail") == "del_member":
                if snapshot.get("member_evidence") in ("current_snapshot_unavailable", "snapshot_difference_inconclusive"):
                    raise PlatformError("group_snapshot_not_conclusive")
                if any(not m.get("name") for m in snapshot.get("member_candidates", [])):
                    raise PlatformError("group_member_profile_incomplete")
            return True
        return True
