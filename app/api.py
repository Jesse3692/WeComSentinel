import hmac
import json
import secrets
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select

from .models import Audit, BusinessEvent, Grant, Job, Media, Profile, Session, Tenant, User, now_ms
from .platforms import PlatformError
from .security import decode_cursor, decrypt_callback, digest, encode_cursor, parse_xml, random_token
from .worker import enqueue

router = APIRouter()
TZ = ZoneInfo("Asia/Shanghai")
TYPES = ["contact_added", "contact_deleted", "group_left", "group_removed"]


async def database(request: Request):
    async with request.app.state.sessions() as db:
        yield db


def result(data):
    return {"data": data}


async def identity(request: Request, db=Depends(database)):
    token = request.cookies.get("monitor_session")
    if not token:
        raise HTTPException(401, "请登录")
    session = await db.scalar(select(Session).where(Session.token_hash == digest(token),
                              Session.revoked.is_(False), Session.expires_at > now_ms()))
    user = await db.get(User, session.user_id) if session else None
    if not user or not user.enabled:
        raise HTTPException(401, "登录已过期或账号被停用")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "CSRF 校验失败")
    request.state.session = session
    return user


async def administrator(user=Depends(identity)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


async def allowed(db, user, tenant_id="all"):
    query = select(Tenant.id).where(Tenant.enabled.is_(True))
    if user.role != "admin":
        query = query.join(Grant, Grant.tenant_id == Tenant.id).where(Grant.user_id == user.id)
    ids = list((await db.scalars(query)).all())
    if tenant_id != "all":
        try:
            selected = int(tenant_id)
        except (ValueError, TypeError):
            raise HTTPException(422, "主体参数无效")
        if selected not in ids:
            raise HTTPException(403, "无该主体访问权限")
        return [selected]
    return ids


def period(start_date=None, end_date=None, days=7):
    today = datetime.now(TZ).date()
    end_date = end_date or today
    start_date = start_date or end_date - timedelta(days=days - 1)
    if end_date < start_date or (end_date - start_date).days > 365:
        raise HTTPException(422, "日期范围应为 1 至 366 天")
    start = int(datetime.combine(start_date, time.min, TZ).timestamp() * 1000)
    end = int(datetime.combine(end_date + timedelta(days=1), time.min, TZ).timestamp() * 1000)
    return start_date, end_date, start, end


def event_dict(e):
    return {k: getattr(e, k) for k in ("id", "tenant_id", "event_type", "employee_id", "customer_id",
            "group_id", "operator_id", "deletion_direction", "occurred_at", "add_way", "channel_state",
            "quantity", "resolution", "enrichment_status", "snapshot")}


class LoginInput(BaseModel):
    code: str = Field(min_length=1, max_length=2048)


class DemoInput(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    role: str = Field(default="admin", pattern="^(admin|user)$")


async def start_session(db, settings, user, response):
    token, csrf = random_token(), random_token()
    db.add(Session(user_id=user.id, token_hash=digest(token), csrf=csrf, expires_at=now_ms() + 7_200_000))
    await db.commit()
    response.set_cookie("monitor_session", token, httponly=True, secure=settings.cookie_secure,
                        samesite="lax", max_age=7200, path="/")
    return result({"csrf_token": csrf, "user": {"id": user.id, "name": user.name, "role": user.role}})


@router.get("/api/v1/config")
async def public_config(request: Request):
    s = request.app.state.settings
    return result({"ding_corp_id": s.ding_corp_id, "demo_enabled": s.demo_enabled,
                   "ding_configured": bool(s.ding_client_id and s.ding_client_secret), "app_env": s.app_env})


@router.post("/api/v1/auth/dingtalk")
async def login(body: LoginInput, request: Request, response: Response, db=Depends(database)):
    try:
        data = await request.app.state.platforms.ding_identity(body.code)
    except PlatformError as exc:
        raise HTTPException(503, str(exc))
    user = await db.scalar(select(User).where(User.ding_corp_id == data["corp_id"], User.ding_user_id == data["user_id"]))
    if not user or not user.enabled:
        raise HTTPException(403, "无访问权限，请联系管理员")
    if user.role != "admin" and not await allowed(db, user):
        raise HTTPException(403, "无访问权限，请联系管理员")
    user.name, user.avatar_url = data["name"], data["avatar"]
    return await start_session(db, request.app.state.settings, user, response)


@router.post("/api/v1/auth/demo")
async def demo_login(body: DemoInput, request: Request, response: Response, db=Depends(database)):
    s = request.app.state.settings
    if not s.demo_enabled or s.app_env == "production":
        raise HTTPException(404, "未启用演示")
    key = request.client.host if request.client else "unknown"
    attempts = request.app.state.login_attempts
    recent = [x for x in attempts.get(key, []) if x > now_ms() - 60_000]
    attempts[key] = recent
    if len(recent) >= 10:
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    recent.append(now_ms())
    if not hmac.compare_digest(body.password, s.demo_password):
        raise HTTPException(401, "演示密码错误")
    user = await db.scalar(select(User).where(User.ding_corp_id == "DEMO", User.ding_user_id == body.role))
    if not user:
        raise HTTPException(503, "请先运行 seed-demo 初始化演示数据")
    return await start_session(db, s, user, response)


@router.post("/api/v1/auth/logout")
async def logout(request: Request, response: Response, user=Depends(identity), db=Depends(database)):
    request.state.session.revoked = True
    await db.commit()
    response.delete_cookie("monitor_session", path="/")
    return result({"ok": True})


@router.get("/api/v1/me")
async def me(request: Request, user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user)
    tenants = (await db.scalars(select(Tenant).where(Tenant.id.in_(ids)))).all()
    return result({"id": user.id, "name": user.name, "avatar_url": user.avatar_url, "role": user.role,
                   "last_tenant_id": user.last_tenant_id if user.last_tenant_id in ids else None,
                   "tenants": [{"id": t.id, "name": t.name} for t in tenants],
                   "csrf_token": request.state.session.csrf})


class Preference(BaseModel):
    last_tenant_id: int | None = None


@router.patch("/api/v1/me/preferences")
async def preferences(body: Preference, user=Depends(identity), db=Depends(database)):
    if body.last_tenant_id is not None:
        await allowed(db, user, body.last_tenant_id)
    user.last_tenant_id = body.last_tenant_id
    await db.commit()
    return result({"ok": True})


@router.get("/api/v1/tenants")
async def tenants(user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user)
    return result([{"id": t.id, "name": t.name} for t in (await db.scalars(select(Tenant).where(Tenant.id.in_(ids)))).all()])


@router.get("/api/v1/dashboard")
async def dashboard(tenant_id: str = "all", start_date: date | None = None, end_date: date | None = None,
                    user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user, tenant_id)
    first, last, start, end = period(start_date, end_date)
    base = BusinessEvent.tenant_id.in_(ids)
    async def counts(a, b):
        rows = (await db.execute(select(BusinessEvent.event_type, func.sum(BusinessEvent.quantity))
                .where(base, BusinessEvent.occurred_at >= a, BusinessEvent.occurred_at < b)
                .group_by(BusinessEvent.event_type))).all()
        return dict(rows)
    current, previous = await counts(start, end), await counts(start - (end - start), start)
    # No explicit range means near 30 days for the chart; chosen ranges apply to all widgets.
    tf, tl, ts, te = period(start_date, end_date, days=30)
    day_expr = func.strftime("%Y-%m-%d", BusinessEvent.occurred_at / 1000, "unixepoch", "+8 hours")
    rows = (await db.execute(select(day_expr, BusinessEvent.event_type, func.sum(BusinessEvent.quantity))
            .where(base, BusinessEvent.occurred_at >= ts, BusinessEvent.occurred_at < te)
            .group_by(day_expr, BusinessEvent.event_type))).all()
    lookup = {(d, t): n for d, t, n in rows}
    trend = [{"date": (tf + timedelta(days=i)).isoformat(),
              **{t: lookup.get(((tf + timedelta(days=i)).isoformat(), t), 0) for t in TYPES}}
             for i in range((tl - tf).days + 1)]
    tops = {}
    for t, field, name_key in (("contact_added", BusinessEvent.employee_id, "employee_name"),
                               ("contact_deleted", BusinessEvent.employee_id, "employee_name"),
                               ("group_left", BusinessEvent.group_id, "group_name")):
        rows = (await db.execute(select(BusinessEvent.tenant_id, field,
                func.max(func.json_extract(BusinessEvent.snapshot, f"$.{name_key}")),
                func.sum(BusinessEvent.quantity).label("total"))
                .where(base, BusinessEvent.event_type == t, BusinessEvent.occurred_at >= start,
                       BusinessEvent.occurred_at < end, field.is_not(None))
                .group_by(BusinessEvent.tenant_id, field)
                .order_by(func.sum(BusinessEvent.quantity).desc(), BusinessEvent.tenant_id, field).limit(5))).all()
        tops[t] = [{"tenant_id": tid, "id": eid, "name": name or eid, "count": n} for tid, eid, name, n in rows]
    unresolved = await db.scalar(select(func.coalesce(func.sum(BusinessEvent.quantity), 0)).where(
        base, BusinessEvent.occurred_at >= start, BusinessEvent.occurred_at < end,
        BusinessEvent.resolution != "exact"))
    pending = await db.scalar(select(func.count()).select_from(Job).where(Job.tenant_id.in_(ids), Job.status != "done"))
    return result({"cards": [{"type": t, "value": current.get(t, 0), "delta": current.get(t, 0) - previous.get(t, 0)} for t in TYPES],
                   "trend": trend, "tops": tops, "unresolved_count": unresolved, "pending_jobs": pending,
                   "range": [first.isoformat(), last.isoformat()], "updated_at": now_ms()})


@router.get("/api/v1/events")
async def events(request: Request, tenant_id: str = "all", start_date: date | None = None,
                 end_date: date | None = None, event_type: str | None = None, employee_id: str | None = None,
                 employee_tenant_id: int | None = None,
                 add_way: str | None = None, group_keyword: str | None = Query(None, max_length=100),
                 cursor: str | None = Query(None, max_length=4096), limit: int = Query(20, ge=1, le=100),
                 user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user, tenant_id)
    _, _, start, end = period(start_date, end_date)
    filters = [BusinessEvent.tenant_id.in_(ids), BusinessEvent.occurred_at >= start, BusinessEvent.occurred_at < end]
    if event_type:
        if event_type not in TYPES + ["group_change_unknown"]:
            raise HTTPException(422, "事件类型无效")
        filters.append(BusinessEvent.event_type == event_type)
    if employee_id:
        filters.append(BusinessEvent.employee_id == employee_id)
    if employee_tenant_id is not None:
        if employee_tenant_id not in ids:
            raise HTTPException(403, "无该员工所属主体权限")
        filters.append(BusinessEvent.tenant_id == employee_tenant_id)
    if add_way:
        if add_way == "other":
            filters.append(BusinessEvent.add_way.not_in(["1", "2", "3", "4", "5", "6"]))
        elif add_way == "unknown":
            filters.append(BusinessEvent.add_way.is_(None))
        else:
            filters.append(BusinessEvent.add_way == add_way)
    if group_keyword:
        filters.append(func.instr(func.json_extract(BusinessEvent.snapshot, "$.group_name"), group_keyword) > 0)
    fingerprint = digest(json.dumps([ids, start, end, event_type, employee_id, employee_tenant_id, add_way, group_keyword, user.id]))
    upper = await db.scalar(select(func.coalesce(func.max(BusinessEvent.id), 0)))
    page_filter = []
    if cursor:
        try:
            decoded = decode_cursor(cursor, request.app.state.settings.secret_key)
            if decoded["filter"] != fingerprint:
                raise ValueError()
            upper = decoded["upper"]
            page_filter.append(or_(BusinessEvent.occurred_at < decoded["time"],
                               and_(BusinessEvent.occurred_at == decoded["time"], BusinessEvent.id < decoded["id"])))
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "分页游标已失效，请刷新")
    filters.append(BusinessEvent.id <= upper)
    total, quantity = (await db.execute(select(func.count(), func.coalesce(func.sum(BusinessEvent.quantity), 0)).where(*filters))).one()
    rows = (await db.scalars(select(BusinessEvent).where(*filters, *page_filter)
                            .order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.id.desc()).limit(limit + 1))).all()
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor({"upper": upper, "filter": fingerprint, "time": last.occurred_at, "id": last.id},
                                    request.app.state.settings.secret_key)
    return result({"items": [event_dict(e) for e in rows[:limit]], "next_cursor": next_cursor,
                   "total_records": total, "total_quantity": quantity})


@router.get("/api/v1/events/{event_id}")
async def event_detail(event_id: int, user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user)
    row = await db.scalar(select(BusinessEvent).where(BusinessEvent.id == event_id, BusinessEvent.tenant_id.in_(ids)))
    if not row:
        raise HTTPException(404, "记录不存在")
    return result(event_dict(row))


@router.get("/api/v1/employees")
@router.get("/api/v1/groups")
async def options(request: Request, tenant_id: str = "all", keyword: str = Query("", max_length=100),
                  user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user, tenant_id)
    kind = "employee" if request.url.path.endswith("employees") else "group"
    rows = (await db.scalars(select(Profile).where(Profile.tenant_id.in_(ids), Profile.kind == kind,
                            func.instr(Profile.name, keyword) > 0).order_by(Profile.name).limit(500))).all()
    return result([{"id": r.external_id, "tenant_id": r.tenant_id, "name": r.name} for r in rows])


@router.get("/api/v1/media/{media_id}")
async def media(media_id: int, request: Request, user=Depends(identity), db=Depends(database)):
    ids = await allowed(db, user)
    row = await db.scalar(select(Media).where(Media.id == media_id, Media.tenant_id.in_(ids)))
    if not row:
        raise HTTPException(404, "图片不存在")
    root = Path(request.app.state.settings.media_dir).resolve()
    path = (root / row.relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, media_type=row.mime_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


class TenantInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    corp_id: str = Field(min_length=1, max_length=100)
    credential_ref: str = Field(pattern="^[A-Z][A-Z0-9_]{0,40}$")
    enabled: bool = True


@router.get("/api/v1/admin/tenants")
async def admin_tenants(request: Request, user=Depends(administrator), db=Depends(database)):
    rows = (await db.scalars(select(Tenant).order_by(Tenant.id))).all()
    return result([{"id": t.id, "name": t.name, "corp_id": t.corp_id, "enabled": t.enabled,
                    "credential_ref": t.credential_ref,
                    "callback_url": request.app.state.settings.public_origin + "/callbacks/wecom/" + t.callback_key,
                    "credentials_ready": all(request.app.state.settings.credential(t.credential_ref).values())} for t in rows])


@router.post("/api/v1/admin/tenants")
async def create_tenant(body: TenantInput, user=Depends(administrator), db=Depends(database)):
    if await db.scalar(select(Tenant.id).where(Tenant.corp_id == body.corp_id)):
        raise HTTPException(409, "企业 ID 已存在")
    t = Tenant(**body.model_dump(), callback_key=secrets.token_urlsafe(18))
    db.add(t)
    await db.flush()
    db.add(Audit(actor_user_id=user.id, action="tenant.create", target=str(t.id)))
    await db.commit()
    return result({"id": t.id})


class TenantPatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    credential_ref: str = Field(pattern="^[A-Z][A-Z0-9_]{0,40}$")
    enabled: bool


@router.patch("/api/v1/admin/tenants/{tenant_id}")
async def patch_tenant(tenant_id: int, body: TenantPatch, request: Request, user=Depends(administrator), db=Depends(database)):
    t = await db.get(Tenant, tenant_id)
    if not t:
        raise HTTPException(404, "主体不存在")
    for k, v in body.model_dump().items():
        setattr(t, k, v)
    db.add(Audit(actor_user_id=user.id, action="tenant.update", target=str(t.id)))
    await db.commit()
    request.app.state.platforms.tokens.clear()
    return result({"ok": True})


class UserInput(BaseModel):
    ding_corp_id: str = Field(min_length=1, max_length=100)
    ding_user_id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)


@router.post("/api/v1/admin/users")
async def create_user(body: UserInput, user=Depends(administrator), db=Depends(database)):
    if await db.scalar(select(User.id).where(User.ding_corp_id == body.ding_corp_id, User.ding_user_id == body.ding_user_id)):
        raise HTTPException(409, "用户已存在")
    row = User(**body.model_dump())
    db.add(row)
    await db.flush()
    db.add(Audit(actor_user_id=user.id, action="user.create", target=str(row.id)))
    await db.commit()
    return result({"id": row.id})


@router.get("/api/v1/admin/users")
async def admin_users(user=Depends(administrator), db=Depends(database)):
    rows = (await db.scalars(select(User).order_by(User.id))).all()
    grants = (await db.execute(select(Grant.user_id, Grant.tenant_id))).all()
    return result([{"id": u.id, "name": u.name, "ding_user_id": u.ding_user_id, "role": u.role,
                    "enabled": u.enabled, "tenant_ids": [t for uid, t in grants if uid == u.id]} for u in rows])


class GrantsInput(BaseModel):
    tenant_ids: list[int] = Field(max_length=1000)


@router.put("/api/v1/admin/users/{user_id}/tenants")
async def set_grants(user_id: int, body: GrantsInput, user=Depends(administrator), db=Depends(database)):
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(404, "用户不存在")
    ids = set(body.tenant_ids)
    found = set((await db.scalars(select(Tenant.id).where(Tenant.id.in_(ids)))).all())
    if ids != found:
        raise HTTPException(422, "包含不存在的主体")
    await db.execute(delete(Grant).where(Grant.user_id == user_id))
    db.add_all([Grant(user_id=user_id, tenant_id=i) for i in ids])
    db.add(Audit(actor_user_id=user.id, action="grants.replace", target=f"{user_id}:{sorted(ids)}"))
    await db.commit()
    return result({"ok": True})


@router.get("/api/v1/admin/jobs")
async def jobs(user=Depends(administrator), db=Depends(database)):
    rows = (await db.scalars(select(Job).order_by(Job.id.desc()).limit(100))).all()
    return result([{k: getattr(j, k) for k in ("id", "tenant_id", "status", "attempts", "last_error", "next_run_at")} for j in rows])


@router.post("/api/v1/admin/jobs/{job_id}/retry")
async def retry_job(job_id: int, user=Depends(administrator), db=Depends(database)):
    j = await db.get(Job, job_id)
    if not j:
        raise HTTPException(404, "任务不存在")
    if j.status not in ("failed", "retry"):
        raise HTTPException(409, "任务不需要重试")
    j.status, j.attempts, j.next_run_at, j.lease_until = "pending", 0, now_ms(), None
    db.add(Audit(actor_user_id=user.id, action="job.retry", target=str(job_id)))
    await db.commit()
    return result({"ok": True})


@router.api_route("/callbacks/wecom/{callback_key}", methods=["GET", "POST"])
async def callback(callback_key: str, request: Request, db=Depends(database)):
    tenant = await db.scalar(select(Tenant).where(Tenant.callback_key == callback_key, Tenant.enabled.is_(True)))
    if not tenant:
        raise HTTPException(404, "未知回调配置")
    s = request.app.state.settings
    credentials = s.credential(tenant.credential_ref)
    query = request.query_params
    if not credentials["token"] or not credentials["aes_key"]:
        raise HTTPException(503, "回调凭据未配置")
    try:
        if request.method == "GET":
            encrypted = query["echostr"]
        else:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 1024 * 1024:
                    raise HTTPException(413, "回调体过大")
            encrypted = parse_xml(bytes(raw))["Encrypt"]
        message = decrypt_callback(encrypted, credentials["token"], credentials["aes_key"], tenant.corp_id,
                                   query["timestamp"], query["nonce"], query["msg_signature"])
        if request.method == "GET":
            return PlainTextResponse(message.decode())
        payload = parse_xml(message)
        if payload.get("ToUserName") != tenant.corp_id:
            raise ValueError("receiver")
        if int(payload["CreateTime"]) < 0:
            raise ValueError("time")
        if payload.get("Event") == "change_external_contact" and payload.get("ChangeType") in (
            "add_external_contact", "del_external_contact", "del_follow_user"):
            if not payload.get("UserID") or not payload.get("ExternalUserID"):
                raise ValueError("missing contact identifiers")
        if payload.get("Event") == "change_external_chat" and not payload.get("ChatId"):
            raise ValueError("missing chat identifier")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "回调校验失败")
    await enqueue(db, s, tenant.id, payload)
    return PlainTextResponse("success")
