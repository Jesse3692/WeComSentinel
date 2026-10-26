import asyncio
import hashlib
import json
import logging
import random
from copy import deepcopy

from cryptography.fernet import Fernet
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from .media import save_avatar
from .event_classification import group_records
from .models import BusinessEvent, Inbox, Job, Profile, Tenant, now_ms
from .platforms import PlatformError

logger = logging.getLogger(__name__)


async def enqueue(db, settings, tenant_id, payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    key = hashlib.sha256(canonical.encode()).hexdigest()
    existing = await db.scalar(
        select(Inbox).where(Inbox.tenant_id == tenant_id, Inbox.dedupe_key == key)
    )
    if existing:
        return existing.id
    item = Inbox(
        tenant_id=tenant_id,
        dedupe_key=key,
        payload_encrypted=Fernet(settings.payload_key.encode()).encrypt(canonical.encode()),
    )
    db.add(item)
    try:
        await db.flush()
        db.add(Job(tenant_id=tenant_id, callback_id=item.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(Inbox).where(Inbox.tenant_id == tenant_id, Inbox.dedupe_key == key)
        )
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
        eligible = or_(
            and_(Job.status.in_(["pending", "retry"]), Job.next_run_at <= now),
            and_(Job.status == "running", Job.lease_until < now),
        )
        async with self.sessions() as db:
            job_id = await db.scalar(select(Job.id).where(eligible).order_by(Job.id).limit(1))
            if job_id is None:
                return False
            result = await db.execute(
                update(Job)
                .where(Job.id == job_id, eligible)
                .values(status="running", lease_until=now + 180_000, attempts=Job.attempts + 1)
                .returning(Job.callback_id, Job.tenant_id, Job.attempts, Job.lease_until)
            )
            claim = result.one_or_none()
            await db.commit()
            if claim is None:
                return False
        error = None
        try:
            async with asyncio.timeout(120):
                if claim.attempts > 5:
                    raise PlatformError("processing_attempts_exhausted")
                async with self.sessions() as db:
                    inbox = await db.get(Inbox, claim.callback_id)
                    tenant = await db.get(Tenant, claim.tenant_id)
                    if inbox is None:
                        raise PlatformError("callback_payload_missing")
                    if tenant is None:
                        raise PlatformError("tenant_missing")
                    payload = json.loads(
                        Fernet(self.settings.payload_key.encode()).decrypt(inbox.payload_encrypted)
                    )
                if not tenant.enabled:
                    raise PlatformError("tenant_disabled")
                complete = await self.normalize(tenant, inbox, payload)
            if not complete:
                error = "profile_incomplete"
        except Exception as exc:
            # Never put upstream request URLs or credentials into database errors.
            error = str(exc) if isinstance(exc, PlatformError) else type(exc).__name__
        async with self.sessions() as db:
            status = "done" if not error else ("failed" if claim.attempts >= 5 else "retry")
            result = await db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == "running",
                    Job.attempts == claim.attempts,
                    Job.lease_until == claim.lease_until,
                )
                .values(
                    lease_until=None,
                    last_error=error,
                    status=status,
                    next_run_at=now_ms()
                    + min(300_000, 2000 * 2**claim.attempts)
                    + random.randint(0, 1000),
                )
            )
            # A previous lease holder must not acknowledge a later execution.
            if result.rowcount:
                await db.execute(
                    update(Inbox).where(Inbox.id == claim.callback_id).values(status=status)
                )
            await db.commit()
        return True

    async def cached(self, tenant_id, kind, external_id):
        if not external_id:
            return None
        async with self.sessions() as db:
            return await db.scalar(
                select(Profile).where(
                    Profile.tenant_id == tenant_id,
                    Profile.kind == kind,
                    Profile.external_id == external_id,
                )
            )

    async def reserve_group_capture(self, tenant_id, external_id):
        """Allocate request order without treating an in-flight response as a snapshot."""
        async with self.sessions() as db:
            statement = insert(Profile).values(
                tenant_id=tenant_id,
                kind="group",
                external_id=external_id,
                name="未知",
                data={},
                synced_at=0,
                capture_version=1,
            )
            version = await db.scalar(
                statement.on_conflict_do_update(
                    index_elements=[Profile.tenant_id, Profile.kind, Profile.external_id],
                    set_={"capture_version": Profile.capture_version + 1},
                ).returning(Profile.capture_version)
            )
            await db.commit()
            return version

    async def put_profile(
        self, tenant_id, kind, external_id, data, synced_at=None, capture_version=None
    ):
        captured_at = synced_at if synced_at is not None else now_ms()
        statement = insert(Profile).values(
            tenant_id=tenant_id,
            kind=kind,
            external_id=external_id,
            name=data.get("name") or "未知",
            data=data,
            synced_at=captured_at,
            capture_version=capture_version or 0,
        )
        condition = None
        if kind == "group":
            condition = Profile.synced_at <= captured_at
            if capture_version is not None:
                condition = and_(condition, Profile.capture_version == capture_version)
        statement = statement.on_conflict_do_update(
            index_elements=[Profile.tenant_id, Profile.kind, Profile.external_id],
            set_={
                "name": statement.excluded.name,
                "data": statement.excluded.data,
                "synced_at": statement.excluded.synced_at,
            },
            where=condition,
        )
        async with self.sessions() as db:
            await db.execute(statement)
            await db.commit()

    async def sync_group(self, tenant, chat_id, detail=None, sampled_at=None, capture_version=None):
        """Persist membership before bounded prefetch; never create business events."""
        if detail is None:
            capture_version = await self.reserve_group_capture(tenant.id, chat_id)
            detail = await self.platforms.group(tenant, chat_id)
        sampled_at = sampled_at if sampled_at is not None else now_ms()
        detail = deepcopy(detail)
        previous = await self.cached(tenant.id, "group", chat_id)
        old_members = {
            (m.get("userid"), str(m.get("type"))): m
            for m in (previous.data.get("member_list", []) if previous else [])
        }
        for member in detail.get("member_list", []):
            old = old_members.get((member.get("userid"), str(member.get("type"))), {})
            if not member.get("media_id") and old.get("media_id"):
                member["media_id"] = old["media_id"]
        await self.put_profile(
            tenant.id,
            "group",
            chat_id,
            detail,
            synced_at=sampled_at,
            capture_version=capture_version,
        )
        semaphore = asyncio.Semaphore(3)
        complete = True

        async def cache_member(member):
            nonlocal complete
            kind = {"1": "employee", "2": "customer"}.get(str(member.get("type")))
            identity = member.get("userid")
            if not kind or not identity:
                return
            async with semaphore:
                try:
                    data, ok = await self.get_profile(tenant, kind, identity)
                    media_id = data.get("media_id") or member.get("media_id")
                    if media_id:
                        member["media_id"] = media_id
                    member["avatar_state"] = (
                        "retry" if not ok else "stored" if media_id else "not_provided"
                    )
                    if not ok:
                        complete = False
                except (PlatformError, OSError, ValueError):
                    member["avatar_state"] = "retry"
                    complete = False

        try:
            # Leave time for persisting partial progress inside the worker lease.
            async with asyncio.timeout(80):
                await asyncio.gather(*(cache_member(m) for m in detail.get("member_list", [])))
        except TimeoutError:
            complete = False
        # Preserve the capture timestamp, not the end of slow avatar downloads.
        await self.put_profile(
            tenant.id,
            "group",
            chat_id,
            detail,
            synced_at=sampled_at,
            capture_version=capture_version,
        )
        return detail, complete

    async def get_profile(self, tenant, kind, external_id, force=False):
        cached = await self.cached(tenant.id, kind, external_id)
        if not external_id:
            return {}, False
        if cached and not force and cached.synced_at > now_ms() - 300_000:
            if not cached.data.get("avatar_refresh_pending") and (
                not cached.data.get("avatar") or cached.data.get("media_id")
            ):
                return cached.data, True
        try:
            if kind == "employee":
                result = await self.platforms.employee(tenant, external_id)
            else:
                result = await self.platforms.customer(tenant, external_id)
                result = {
                    **result["external_contact"],
                    "follow_user": result.get("follow_user", []),
                }
            # Preserve usable historical fields for the same tenant/kind/identity.
            # Relationship data must remain fresh, never merge old follow_user here.
            previous = cached.data if cached else {}
            for field in ("name", "type", "gender", "corp_name"):
                if result.get(field) is None or result.get(field) == "":
                    if field in previous:
                        result[field] = previous[field]
            url = result.get("avatar")
            media_id = previous.get("media_id")
            pending = False
            if url and (
                url != previous.get("avatar")
                or not media_id
                or previous.get("avatar_refresh_pending")
            ):
                try:
                    downloaded = await save_avatar(self.settings, self.sessions, tenant.id, url)
                except (PlatformError, OSError, ValueError):
                    downloaded = None
                pending = not bool(downloaded)
                media_id = downloaded or media_id
            if media_id:
                result["media_id"] = media_id
            if pending:
                result["sync_error"] = "avatar_unavailable"
                result["avatar_refresh_pending"] = True
            await self.put_profile(tenant.id, kind, external_id, result)
            return result, not pending
        except PlatformError as exc:
            # A removed relationship may no longer be accessible. A snapshot is valid historical data.
            if cached:
                # A departed contact may no longer be readable, but its historical
                # Tencent avatar URL can still be valid and safely downloaded.
                data = dict(cached.data)
                if data.get("avatar") and (
                    not data.get("media_id") or data.get("avatar_refresh_pending")
                ):
                    try:
                        downloaded = await save_avatar(
                            self.settings, self.sessions, tenant.id, data["avatar"]
                        )
                    except (PlatformError, OSError, ValueError):
                        downloaded = None
                    if downloaded:
                        data["media_id"] = downloaded
                        data.pop("avatar_refresh_pending", None)
                        data.pop("sync_error", None)
                        await self.put_profile(tenant.id, kind, external_id, data)
                complete = not data.get("avatar_refresh_pending") and (
                    not data.get("avatar") or bool(data.get("media_id"))
                )
                return {
                    **data,
                    "sync_error": str(exc),
                    "refresh_failed": force,
                }, complete and not force
            return {"sync_error": str(exc)}, False

    async def write_events(self, tenant_id, callback_id, records, replace_group=False):
        async with self.sessions() as db:
            for record in records:
                item = await db.scalar(
                    select(BusinessEvent).where(
                        BusinessEvent.tenant_id == tenant_id,
                        BusinessEvent.callback_id == callback_id,
                        BusinessEvent.item_key == record["item_key"],
                    )
                )
                if item is None:
                    item = BusinessEvent(tenant_id=tenant_id, callback_id=callback_id, **record)
                    db.add(item)
                else:
                    for key, value in record.items():
                        setattr(item, key, value)
            if replace_group:
                await db.execute(
                    delete(BusinessEvent).where(
                        BusinessEvent.tenant_id == tenant_id,
                        BusinessEvent.callback_id == callback_id,
                        BusinessEvent.item_key.in_(["group", "group_other"]),
                        BusinessEvent.item_key.not_in([record["item_key"] for record in records]),
                    )
                )
            await db.commit()

    async def group_snapshot(self, tenant, inbox, payload, previous, current):
        """Retain snapshot evidence independently of the customer business category."""
        before = previous.data if previous else {}
        current = current or {}
        async with self.sessions() as db:
            existing = await db.scalar(
                select(BusinessEvent).where(
                    BusinessEvent.tenant_id == tenant.id,
                    BusinessEvent.callback_id == inbox.id,
                    BusinessEvent.item_key == "group",
                )
            )
            snapshot = dict(existing.snapshot) if existing else {}
        if "_all_member_candidates" in snapshot:
            snapshot["member_candidates"] = snapshot.pop("_all_member_candidates")
        name = current.get("name") or before.get("name") or snapshot.get("group_name")
        snapshot["group_name"] = name or None
        snapshot["group_name_source"] = (
            "group_details" if current.get("name") else "cached" if name else "not_provided"
        )
        snapshot["reported_event_type"] = {"0": "group_left", "1": "group_removed"}.get(
            str(payload.get("QuitScene")), "group_change_unknown"
        )
        owner_id = current.get("owner") or before.get("owner")
        if owner_id:
            owner = await self.cached(tenant.id, "employee", owner_id)
            snapshot["group_owner_id"] = owner_id
            snapshot["group_owner_name"] = owner.name if owner else None
        # A current group list cannot identify a past departure by itself.
        # Retain the original baseline across API failures and delayed group updates.
        # Never replace it with a profile fetched after this event on a retry.
        if "_before_members" not in snapshot and not existing:
            snapshot["_before_members"] = (
                before.get("member_list")
                if previous and previous.synced_at <= int(payload["CreateTime"]) * 1000
                else None
            )
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
                reported = payload.get("MemChangeList")
                # Only compare complete textual IDs; never interpret nicknames or
                # the empty dictionaries persisted by the legacy XML parser as IDs.
                textual = (
                    isinstance(reported, list)
                    and reported
                    and all(isinstance(x, str) for x in reported)
                )
                compatible = True
                if textual:
                    snapshot["reported_member_ids"] = list(reported)
                    compatible = (
                        len(reported) == count
                        and len(set(reported)) == count
                        and all(reported)
                        and set(reported) == removed
                    )
                    if not compatible:
                        snapshot["member_evidence"] = "callback_snapshot_conflict"
                if len(removed) == count and not (new.keys() - old.keys()) and compatible:
                    for member_id in sorted(removed):
                        member = old[member_id]
                        member_type = str(member.get("type", ""))
                        kind = {"1": "employee", "2": "customer"}.get(member_type)
                        profile = await self.cached(tenant.id, kind, member_id) if kind else None
                        data = profile.data if profile else {}
                        snapshot["member_candidates"].append(
                            {
                                "member_id": member_id,
                                "name": data.get("name")
                                or (profile.name if profile and profile.name != "未知" else None)
                                or member.get("name")
                                or None,
                                "member_type": int(member_type)
                                if member_type in ("1", "2")
                                else None,
                                "customer_type": data.get("type") if kind == "customer" else None,
                                "gender": data.get("gender"),
                                "company_name": data.get("corp_name"),
                                "media_id": data.get("media_id") or member.get("media_id"),
                            }
                        )
                    snapshot["member_evidence"] = "snapshot_difference"
        # Missing profile fields do not invalidate the member difference. Retry enrichment
        # using retained IDs, without trying to infer the removed members again.
        for member in snapshot.get("member_candidates", []):
            kind = {1: "employee", 2: "customer"}.get(member.get("member_type"))
            if (
                kind
                and member.get("member_id")
                and (
                    not member.get("name")
                    or not member.get("media_id")
                    or member.get("avatar_state") == "retry"
                )
            ):
                data, complete = await self.get_profile(tenant, kind, member["member_id"])
                member["name"] = data.get("name") or member.get("name")
                member["avatar_state"] = (
                    "retry"
                    if not complete
                    else "stored"
                    if (data.get("media_id") or member.get("media_id"))
                    else "not_provided"
                )
                member["media_id"] = data.get("media_id") or member.get("media_id")
                if kind == "customer":
                    for target, source in (
                        ("customer_type", "type"),
                        ("gender", "gender"),
                        ("company_name", "corp_name"),
                    ):
                        if data.get(source) is not None and data.get(source) != "":
                            member[target] = data[source]
        return snapshot

    async def normalize(self, tenant, inbox, payload):
        change = payload.get("ChangeType")
        occurred = int(payload["CreateTime"]) * 1000
        if payload.get("Event") == "change_external_contact":
            types = {
                "add_external_contact": "contact_added",
                "del_external_contact": "contact_deleted",
                "del_follow_user": "contact_deleted",
            }
            if change not in types:
                if change == "edit_external_contact":
                    _, complete = await self.get_profile(
                        tenant, "customer", payload.get("ExternalUserID"), force=True
                    )
                    return complete
                return True
            employee_id, customer_id = payload.get("UserID"), payload.get("ExternalUserID")
            if not employee_id or not customer_id:
                raise PlatformError("contact_identity_fields_missing")
            employee, ok1 = await self.get_profile(tenant, "employee", employee_id)
            customer, ok2 = await self.get_profile(
                tenant, "customer", customer_id, force=change == "add_external_contact"
            )
            relation = next(
                (r for r in customer.get("follow_user", []) if r.get("userid") == employee_id), {}
            )
            if change == "add_external_contact" and customer.get("refresh_failed"):
                relation = {}
            if change == "add_external_contact" and "add_way" not in relation:
                ok2 = False
                customer["sync_error"] = "customer_relation_add_way_missing"
            direction = {
                "del_external_contact": "employee_deleted_customer",
                "del_follow_user": "customer_deleted_employee",
            }.get(change)
            await self.write_events(
                tenant.id,
                inbox.id,
                [
                    {
                        "item_key": "contact",
                        "event_type": types[change],
                        "occurred_at": occurred,
                        "employee_id": employee_id,
                        "customer_id": customer_id,
                        "deletion_direction": direction,
                        "add_way": str(relation["add_way"]) if "add_way" in relation else None,
                        "channel_state": payload.get("State"),
                        "quantity": 1,
                        "enrichment_status": "complete" if ok1 and ok2 else "partial",
                        "snapshot": {
                            "employee_name": employee.get("name") or employee_id,
                            "employee_media_id": employee.get("media_id"),
                            "customer_name": customer.get("name") or customer_id,
                            "customer_media_id": customer.get("media_id"),
                            "customer_type": customer.get("type"),
                            "gender": customer.get("gender"),
                            "company_name": customer.get("corp_name"),
                        },
                    }
                ],
            )
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
            capture_version = await self.reserve_group_capture(tenant.id, chat_id)
            current = None
            sampled_at = None
            group_error = None
            try:
                current = await self.platforms.group(tenant, chat_id)
                sampled_at = now_ms()
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
                await self.write_events(
                    tenant.id,
                    inbox.id,
                    group_records(snapshot, quantity, occurred, chat_id),
                    replace_group=True,
                )
            if current:
                if change == "create" or (
                    change == "update" and payload.get("UpdateDetail") == "add_member"
                ):
                    _, complete = await self.sync_group(
                        tenant, chat_id, current, sampled_at, capture_version=capture_version
                    )
                    if not complete:
                        raise PlatformError("group_member_avatar_incomplete")
                else:
                    await self.put_profile(
                        tenant.id,
                        "group",
                        chat_id,
                        current,
                        synced_at=sampled_at,
                        capture_version=capture_version,
                    )
            if group_error and change != "dismiss":
                raise PlatformError(group_error)
            if change == "update" and payload.get("UpdateDetail") == "del_member":
                if snapshot.get("member_evidence") in (
                    "current_snapshot_unavailable",
                    "snapshot_difference_inconclusive",
                ):
                    raise PlatformError("group_snapshot_not_conclusive")
                if any(
                    not m.get("name") or m.get("avatar_state") == "retry"
                    for m in snapshot.get("member_candidates", [])
                ):
                    raise PlatformError("group_member_profile_incomplete")
            return True
        return True
