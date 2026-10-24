"""Authenticated assistant endpoints. No conversations or personal data are persisted."""

import asyncio
import re
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from .api import allowed, dashboard_report, database, group_profiles_query, identity, result
from .assistant import (
    LABELS, ModelUnavailable, explain_event, generate, is_group_list_question, resolve_range, rule_answer,
)
from .assistant_data import (
    DataQuery, capabilities, execute_query, infer_query, numeric_id, plan_query,
)
from .models import Audit, BusinessEvent, Tenant, now_ms

router = APIRouter()


class AssistantInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    tenant_id: str = Field(default="all", max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    event_id: int | None = Field(default=None, gt=0)
    history: list[str] = Field(default_factory=list, max_length=6)
    query_type: Literal["auto", "groups"] = "auto"
    group_keyword: str = Field(default="", max_length=100)
    group_offset: int = Field(default=0, ge=0, le=1000000)
    group_limit: int = Field(default=20, ge=1, le=100)
    query: DataQuery | None = None
    context_query: DataQuery | None = None

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value):
        if not value.strip():
            raise ValueError("empty message")
        return value.strip()

    @field_validator("history")
    @classmethod
    def limited_history(cls, value):
        if any(len(item) > 2000 for item in value):
            raise ValueError("history too long")
        return value


@router.get("/api/v1/assistant/config")
async def assistant_config(request: Request, user=Depends(identity), db=Depends(database)):
    await allowed(db, user)
    settings = request.app.state.settings
    return result({"enabled": settings.assistant_enabled,
                   "mode": "model" if settings.assistant_base_url else "rules",
                   "model": settings.assistant_model if settings.assistant_base_url else None,
                   "capabilities": capabilities(user.role)})


@router.post("/api/v1/assistant/chat")
async def chat(body: AssistantInput, request: Request, user=Depends(identity), db=Depends(database)):
    settings = request.app.state.settings
    if not settings.assistant_enabled:
        raise HTTPException(404, "助手未启用")
    user_id, role = user.id, user.role
    ids = await allowed(db, user, body.tenant_id)
    session_id = request.state.session.id
    # Release identity's read transaction before any network request.
    await db.rollback()
    state = request.app.state
    recent = [stamp for stamp in state.assistant_attempts.get(user_id, []) if stamp > now_ms() - 60000]
    if user_id in state.assistant_active or len(recent) >= 10 or len(state.assistant_active) >= 4:
        raise HTTPException(429, "助手请求较多，请稍后再试")
    if len(state.assistant_attempts) > 1024:
        state.assistant_attempts = {key: stamps for key, stamps in state.assistant_attempts.items()
                                    if stamps and stamps[-1] > now_ms() - 60000}
    state.assistant_attempts[user_id] = recent + [now_ms()]
    state.assistant_active.add(user_id)
    try:
        return result(await answer(body, request, SimpleNamespace(id=user_id, role=role), ids, session_id))
    finally:
        state.assistant_active.discard(user_id)


async def reauthorize(request, user, tenant_id, original_ids, session_id):
    """Do not deliver an in-flight answer after logout, revocation or role changes."""
    from .models import Session, User
    async with request.app.state.sessions() as db:
        current = await db.get(User, user.id)
        session = await db.get(Session, session_id)
        if (not current or not current.enabled or not session or session.revoked
                or session.expires_at <= now_ms()):
            raise HTTPException(401, "登录已过期，请重新登录")
        ids = await allowed(db, current, tenant_id)
        if sorted(ids) != sorted(original_ids) or current.role != user.role:
            raise HTTPException(403, "授权范围已变化，请重新提问")


async def answer(body, request, user, ids, session_id):
    query = body.query
    # Do not forward accidentally pasted credentials to either model endpoint.
    if re.search(r"密钥|令牌|密码|AESKey|Secret|access.?token", body.message, re.I):
        query = DataQuery(capability="capabilities")
    if query is None and body.context_query and body.message.strip() in ("下一页", "上一页", "继续", "再看看"):
        delta = -body.context_query.limit if body.message.strip() == "上一页" else body.context_query.limit
        query = body.context_query.model_copy(update={"offset": max(0, body.context_query.offset + delta)})
    if query is None and (body.query_type == "groups" or is_group_list_question(body.message)):
        return await group_answer(body, request, user, ids, session_id)
    # Preserve report/model analysis and explicit event explanation, while routing all data tools.
    if query is None and not body.event_id and not any(word in body.message for word in ("周报", "日报", "报告", "对比当前区间", "解释识别依据")):
        query = infer_query(body.message)
        if query and body.context_query and body.context_query.entity_id and re.search(r"这个|该群|该客户|这位", body.message):
            context = body.context_query
            if context.capability.startswith("group") and query.capability.startswith("group"):
                query = query.model_copy(update={"entity_id": context.entity_id,
                                                 "entity_tenant_id": context.entity_tenant_id})
            elif context.capability.startswith("customer") and query.capability == "customer_followers":
                query = query.model_copy(update={"entity_id": context.entity_id,
                                                 "entity_tenant_id": context.entity_tenant_id})
        complex_question = bool(re.search(r'[“"\']|包含|含有|头像|的.*(?:记录|趋势|统计|资料|成员|客户)', body.message))
        if request.app.state.settings.assistant_base_url and (query is None or (complex_question and not query.entity_id)):
            try:
                async with asyncio.timeout(12):
                    planned = await plan_query(request.app.state.settings, body.message, body.history, user.role)
                query = planned or query
            except TimeoutError:
                pass
    if query:
        if query.capability == "event_detail":
            if not query.entity_id:
                return await data_answer(body, query.model_copy(update={"capability": "events"}), request, user, ids, session_id)
            body = body.model_copy(update={"event_id": numeric_id(query.entity_id)})
            if query.entity_tenant_id is not None and query.entity_tenant_id not in ids:
                raise HTTPException(403, "无该事件所属主体权限")
            if query.entity_tenant_id is not None:
                body = body.model_copy(update={"tenant_id": str(query.entity_tenant_id)})
                ids = [query.entity_tenant_id]
        else:
            return await data_answer(body, query, request, user, ids, session_id)
    first, last, start, end = resolve_range(body.message, body.start_date, body.end_date)
    sources = []
    event_fact = None
    async with request.app.state.sessions() as db:
        data = (await dashboard_report(request, body.tenant_id, first, last, user, db,
                                       use_cache=False))["data"]
        directions = dict((await db.execute(select(BusinessEvent.deletion_direction,
            func.sum(BusinessEvent.quantity)).where(BusinessEvent.tenant_id.in_(ids),
            BusinessEvent.event_type == "contact_deleted", BusinessEvent.occurred_at >= start,
            BusinessEvent.occurred_at < end).group_by(BusinessEvent.deletion_direction))).all())
        deletion = {key: directions.get(key, 0) for key in
                    ("customer_deleted_employee", "employee_deleted_customer")}
        deletion["unknown"] = sum(value for key, value in directions.items() if key not in deletion)
        if body.event_id:
            event = await db.scalar(select(BusinessEvent).where(BusinessEvent.id == body.event_id,
                                   BusinessEvent.tenant_id.in_(ids)))
            if not event:
                raise HTTPException(404, "记录不存在")
            # Deliberately exclude names, external IDs, media, payloads and arbitrary snapshot text.
            event_fact = {key: getattr(event, key) for key in ("id", "event_type", "quantity",
                          "resolution", "deletion_direction", "enrichment_status", "occurred_at")}
            reason = event.snapshot.get("classification_reason")
            event_fact["classification_reason"] = reason if reason in (
                "internal_members", "member_type_unknown", "identity_unresolved", "quit_reason_unknown") else None
            event_fact["operator_known"] = bool(event.operator_id)
            sources = [{"event_id": event.id, "label": f"事件 #{event.id} · {LABELS.get(event.event_type, '事件')}"}]
        else:
            events = (await db.scalars(select(BusinessEvent).where(BusinessEvent.tenant_id.in_(ids),
                      BusinessEvent.occurred_at >= start, BusinessEvent.occurred_at < end)
                      .order_by(BusinessEvent.occurred_at.desc(), BusinessEvent.id.desc()).limit(5))).all()
            sources = [{"event_id": event.id, "label": f"事件 #{event.id} · {LABELS.get(event.event_type, '事件')}"}
                       for event in events]
    span = (last - first).days + 1
    facts = {"range": data["range"], "previous_range": [(first - timedelta(days=span)).isoformat(),
             (first - timedelta(days=1)).isoformat()], "tenant_count": len(ids),
             "metrics": data["cards"], "deletion_directions": deletion,
             "unresolved_count": data["unresolved_count"], "event": event_fact,
             "rankings": {kind: [{"label": f"对象{index + 1}", "count": row["count"]}
                           for index, row in enumerate(rows)] for kind, rows in data["tops"].items()}}
    text, mode, warning, tokens = rule_answer(body.message, facts), "rules", "", 0
    if request.app.state.settings.assistant_base_url:
        try:
            async with asyncio.timeout(request.app.state.settings.assistant_timeout_seconds):
                text, tokens = await generate(request.app.state.settings, body.message, facts, body.history)
            mode = "model"
        except (ModelUnavailable, TimeoutError):
            warning = "模型暂不可用或回答不完整，已返回规则查询结果。"
    await reauthorize(request, user, body.tenant_id, ids, session_id)
    async with request.app.state.sessions() as db:
        db.add(Audit(actor_user_id=user.id, action="assistant.query",
                     target=f"mode={mode};tenants={','.join(map(str, ids))};tokens={tokens}"))
        await db.commit()
    return {"answer": text, "mode": mode, "warning": warning, "range": data["range"],
            "previous_range": facts["previous_range"], "cards": data["cards"],
            "deletion_directions": deletion, "unresolved_count": data["unresolved_count"],
            "rankings": data["tops"] if any(word in body.message for word in ("排行", "最多", "排名")) else {},
            "sources": sources, "sources_label": "事件依据" if body.event_id else "区间近期事件样本（最多 5 条）",
            "event_id": body.event_id, "event_explanation": explain_event(event_fact) if event_fact else None,
            "event_occurred_at": event_fact["occurred_at"] if event_fact else None,
            "updated_at": now_ms(), "usage_tokens": tokens}


async def data_answer(body, query, request, user, ids, session_id):
    if query.capability in ("events", "statistics", "trend", "rankings"):
        query = query.model_copy(update={"start_date": query.start_date or body.start_date,
                                         "end_date": query.end_date or body.end_date})
    async with request.app.state.sessions() as db:
        data = await execute_query(db, ids, query, request, user, session_id, body.message)
    await reauthorize(request, user, body.tenant_id, ids, session_id)
    async with request.app.state.sessions() as db:
        db.add(Audit(actor_user_id=user.id, action="assistant.query",
                     target=f"mode=rules;query={query.capability};tenants={','.join(map(str, ids))};tokens=0"))
        await db.commit()
    page_query = data["page_query"]
    answer = f"{data['title']}：共 {data['total']} 条，当前返回 {len(data['items'])} 条。"
    if data.get("total_quantity") is not None:
        answer += f"涉及 {data['total_quantity']} 人次。"
    return {"answer": answer, "result_type": "data", "dataset": data, "query": page_query,
            "mode": "rules", "warning": "", "cards": [], "rankings": {}, "sources": [],
            "event_id": None, "range": data.get("range") or [], "previous_range": [],
            "updated_at": now_ms(), "usage_tokens": 0}


async def group_catalog(db, ids, keyword="", offset=0, limit=20):
    """Read local group snapshots only; do not call the platform or send names to a model."""
    query = group_profiles_query(ids, keyword)
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await db.scalars(query.offset(offset).limit(limit))).all()
    names = dict((await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids)))).all())
    return {"items": [{"id": row.external_id, "tenant_id": row.tenant_id,
                       "tenant_name": names[row.tenant_id], "name": row.name or "群名称未提供",
                       "synced_at": row.synced_at} for row in rows],
            "total": total, "offset": offset, "limit": limit, "keyword": keyword,
            "has_more": offset + len(rows) < total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
            "source": "profiles", "date_filtered": False}


async def group_answer(body, request, user, ids, session_id):
    keyword = body.group_keyword.strip()
    # A supported explicit natural-language filter; all other keyword filters use the UI field.
    if not keyword:
        import re
        match = re.search(r"群名(?:称)?(?:包含|含有)[：:\s]*(.+?)(?:的(?:客户)?群(?:列表)?|[？?。！!]|$)", body.message)
        if match:
            keyword = match[1].strip().strip('“”\"\'').strip()
            if len(keyword) > 100:
                raise HTTPException(422, "群名称关键词最多 100 字符")
    async with request.app.state.sessions() as db:
        catalog = await group_catalog(db, ids, keyword, body.group_offset, body.group_limit)
    await reauthorize(request, user, body.tenant_id, ids, session_id)
    total, offset, count = catalog["total"], catalog["offset"], len(catalog["items"])
    if total == 0:
        text = ("当前授权主体下没有匹配的已同步群资料。" if keyword else
                "当前授权主体下尚未保存群资料，请先由管理员检查资料同步；这不代表企业微信中没有群。")
    elif not count:
        text = f"本地已同步群资料共 {total} 个，当前页已超出范围，请返回上一页。"
    else:
        text = f"当前授权主体下{'匹配' if keyword else '已同步'}的群共 {total} 个，当前显示第 {offset + 1}～{offset + count} 个。"
    text += "\n群列表不受页面日期筛选限制，按本地最近同步资料展示，可能与企业微信实时状态存在差异。"
    async with request.app.state.sessions() as db:
        db.add(Audit(actor_user_id=user.id, action="assistant.query",
                     target=f"mode=rules;query=groups;tenants={','.join(map(str, ids))};tokens=0"))
        await db.commit()
    return {"answer": text, "result_type": "groups", "groups": catalog,
            "query": DataQuery(capability="groups", keyword=keyword, offset=body.group_offset,
                               limit=body.group_limit).model_dump(mode="json"),
            "mode": "rules", "warning": "", "cards": [], "rankings": {}, "sources": [],
            "event_id": None, "updated_at": now_ms(), "usage_tokens": 0}
