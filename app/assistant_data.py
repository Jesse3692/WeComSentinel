"""Bounded, tenant-scoped read tools. Results stay out of the model context."""

import json
import re
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func, or_, select, union

from .api import TZ, business_event_filters, business_event_page, event_details, group_profiles_query
from .assistant import LABELS, ModelUnavailable, is_group_list_question, resolve_range
from .models import Audit, BusinessEvent, Grant, Inbox, Job, Media, Profile, Session, Tenant, User
from .security import decode_cursor, digest, encode_cursor

Capability = Literal["capabilities", "overview", "tenants", "tenant_settings", "employees", "customers",
                     "groups", "group_detail", "group_members", "customer_followers", "events",
                     "event_detail", "statistics", "trend", "rankings", "jobs", "callbacks", "users",
                     "audits", "me", "media"]
ADMIN = {"tenant_settings", "jobs", "callbacks", "users", "audits"}
CAPABILITIES = {
    "capabilities": ("助手能力", "查看可用查询与示例", "你能查询哪些数据"),
    "overview": ("数据概览", "当前授权范围的已保存资料及事件数量", "当前有哪些数据"),
    "tenants": ("授权主体", "已授权并启用的主体", "我有哪些主体权限"),
    "employees": ("员工资料", "员工姓名、标识、头像及有事件但无资料的员工", "当前主体有哪些员工"),
    "customers": ("客户资料", "客户姓名、企业、头像及跟进关系入口，含历史资料", "当前有哪些客户"),
    "groups": ("群资料列表", "已同步的群名称、主体与同步时间", "当前主体中有哪些群"),
    "group_detail": ("群详情", "已保存的群主、建群时间和快照成员数", "查看群详情"),
    "group_members": ("群成员", "最近保存的成员快照，区分内部与外部", "查看群成员"),
    "customer_followers": ("客户跟进关系", "客户快照保存的跟进员工和添加方式", "查看客户跟进员工"),
    "events": ("业务事件", "新增、双向删除、退群、移出、待核实及资料状态", "今天有哪些删除记录"),
    "event_detail": ("事件详情", "打开指定事件的授权详情与识别依据", "查看事件 #1"),
    "statistics": ("区间统计", "按事件筛选统计人次，与上个等长区间比较", "统计近7天客户变化"),
    "trend": ("每日趋势", "按北京时间逐日统计各类事件人次", "查看最近30天趋势"),
    "rankings": ("员工和群排名", "按新增/删除员工、退群/移出群的人次排名", "哪些群退群最多"),
    "media": ("头像资源", "已授权的本地头像类型、大小和图片预览", "查看头像资源"),
    "me": ("我的账号", "当前账号角色、授权范围和本次会话到期时间", "我的账号信息"),
    "tenant_settings": ("主体配置", "管理员查看主体启停和凭据是否配置", "查看主体配置"),
    "jobs": ("处理任务", "管理员查看任务状态、重试次数与错误类别", "有哪些失败任务"),
    "callbacks": ("回调收件箱", "管理员查看接收时间、状态及关联任务", "查看回调接收记录"),
    "users": ("用户与授权", "管理员查看应用账号、角色与主体授权", "查看用户授权"),
    "audits": ("管理审计", "管理员查看操作人、动作及脱敏目标", "最近有哪些管理操作"),
}
EVENT_TYPES = Literal["contact_added", "contact_deleted", "group_left", "group_removed", "group_change_unknown"]
DISPLAY = {**LABELS, "customer_deleted_employee": "客户删除员工", "employee_deleted_customer": "员工删除客户",
           "exact": "明确身份", "inferred": "快照推断", "unresolved": "待核实",
           "pending": "待处理", "partial": "待补全", "complete": "资料齐全",
           "running": "处理中", "retry": "等待重试", "failed": "失败", "done": "已完成"}


class DataQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: Capability
    keyword: str = Field(default="", max_length=100)
    entity_id: str = Field(default="", max_length=200)
    entity_tenant_id: int | None = Field(default=None, gt=0)
    offset: int = Field(default=0, ge=0, le=1000000)
    limit: int = Field(default=20, ge=1, le=100)
    start_date: date | None = None
    end_date: date | None = None
    event_type: EVENT_TYPES | None = None
    deletion_direction: Literal["customer_deleted_employee", "employee_deleted_customer"] | None = None
    resolution: Literal["exact", "inferred", "unresolved"] | None = None
    enrichment_status: Literal["pending", "partial", "complete"] | None = None
    employee_keyword: str = Field(default="", max_length=100)
    customer_keyword: str = Field(default="", max_length=100)
    group_keyword: str = Field(default="", max_length=100)
    employee_id: str = Field(default="", max_length=200)
    customer_id: str = Field(default="", max_length=200)
    group_id: str = Field(default="", max_length=200)
    add_way: str = Field(default="", max_length=20)
    status: Literal["pending", "running", "retry", "failed", "done"] | None = None
    member_type: Literal[1, 2] | None = None
    cursor: str | None = Field(default=None, max_length=4096)


def infer_query(message):
    """Fast paths keep common questions available without a model."""
    q = None
    if re.search(r"密钥|令牌|密码|AESKey|Secret|access.?token|原始.*载荷", message, re.I):
        return DataQuery(capability="capabilities")
    rules = [
        (r"能(力|查|做)|支持.*查询|查询.*范围", "capabilities"),
        (r"有哪些数据|数据概览|数据总览", "overview"),
        (r"我的.*(账号|账户|信息|权限)|我是谁", "me"),
        (r"用户.*授权|应用用户|账号列表|哪些用户", "users"),
        (r"审计|操作日志|管理操作", "audits"), (r"回调|收件箱", "callbacks"),
        (r"任务|处理失败|重试队列", "jobs"),
        (r"主体.*配置|停用.*主体|启停.*主体", "tenant_settings"),
        (r"有哪些主体|主体列表|主体权限|授权主体", "tenants"),
        (r"头像|媒体资源", "media"), (r"群.*成员|群里.*人|群内.*人|群.*(?:有哪些员工|哪些客户|有几个客户|有几个员工|有谁)", "group_members"),
        (r"群.*详情|群主|群.*创建时间", "group_detail"),
        (r"客户.*跟进|跟进.*客户|客户.*归属", "customer_followers"),
        (r"排行|排名|最多", "rankings"), (r"趋势|逐日|每天", "trend"),
        (r"事件\s*#?\s*\d+|记录\s*#\s*\d+", "event_detail"),
        (r"记录|明细|事件|待核实|客户删除员工|员工删除客户", "events"),
        (r"统计|多少人次|数量对比", "statistics"),
        (r"员工.*(列表|资料|有哪些)|哪些员工|有哪些员工|员工名单", "employees"),
        (r"客户.*(列表|资料|有哪些)|哪些客户|有哪些客户|客户名单", "customers"),
    ]
    if is_group_list_question(message):
        q = DataQuery(capability="groups")
    else:
        for pattern, kind in rules:
            if kind == "group_members" and re.search(r"变更|退群|被移|事件|记录|趋势|排名|排行|减少|退出", message):
                continue
            if re.search(pattern, message):
                q = DataQuery(capability=kind)
                break
    if not q:
        return None
    if q.capability == "media" and "员工" in message:
        q.capability = "employees"
    elif q.capability == "media" and "客户" in message:
        q.capability = "customers"
    if q.capability == "event_detail":
        q.entity_id = re.search(r"(?:事件|记录)\s*#?\s*(\d+)", message)[1]
    if q.capability == "group_members":
        q.member_type = 1 if "员工" in message else 2 if "客户" in message or "外部" in message else None
    if any(word in message for word in ("退群", "主动退出")):
        q.event_type = "group_left"
    elif any(word in message for word in ("被移", "踢出")):
        q.event_type = "group_removed"
    elif "待核实" in message:
        q.event_type = "group_change_unknown"
    elif "删除" in message:
        q.event_type = "contact_deleted"
    elif "新增" in message:
        q.event_type = "contact_added"
    for text, direction in (("客户删除员工", "customer_deleted_employee"), ("员工删除客户", "employee_deleted_customer")):
        if text in message:
            q.deletion_direction = direction
    if "推断" in message:
        q.resolution = "inferred"
    if "待补全" in message:
        q.enrichment_status = "partial"
    for word, value in (("失败", "failed"), ("重试", "retry"), ("待处理", "pending"),
                        ("运行中", "running"), ("已完成", "done")):
        if word in message and q.capability in ("jobs", "callbacks"):
            q.status = value
            break
    quoted = re.search(r'[“"\']([^”"\']+)[”"\']', message)
    named = re.search(r"(?:名称|姓名|名字|群名)(?:包含|含有|为|是)[：:\s]*(.+?)(?:的|[？?。！!]|$)", message)
    if quoted or named:
        q.keyword = (quoted or named)[1].strip()[:100]
    if q.capability not in ("events", "statistics", "trend", "rankings"):
        q.event_type = q.deletion_direction = q.resolution = q.enrichment_status = None
    return q


def capabilities(role):
    return [{"id": key, "name": value[0], "description": value[1], "example": value[2],
             "admin_only": key in ADMIN} for key, value in CAPABILITIES.items()
            if role == "admin" or key not in ADMIN]


def timestamp(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return "未提供"
    try:
        return datetime.fromtimestamp(value / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "未提供"


def seconds(value):
    return timestamp(value * 1000) if isinstance(value, (int, float)) else "未提供"


def action(label, capability, **params):
    return {"label": label, "query": DataQuery(capability=capability, **params).model_dump(mode="json")}


def dataset(query, title, columns, items, total, *, note="", source="", ranged=None,
            quantity=None, paginate=True):
    for row in items:
        for key, _ in columns:
            if isinstance(row.get(key), (dict, list, tuple, set)):
                row[key] = "未提供"
    result = {"title": title, "columns": [{"key": key, "label": label} for key, label in columns],
              "items": items, "total": total, "offset": query.offset, "limit": query.limit,
              "note": note, "source": source, "range": ranged,
              "total_quantity": quantity, "next_query": None, "previous_query": None}
    result["page_query"] = query.model_dump(mode="json")
    filter_labels = {"keyword": "关键词", "entity_id": "对象标识", "entity_tenant_id": "对象主体编号",
        "event_type": "事件类型", "employee_keyword": "员工关键词", "customer_keyword": "客户关键词",
        "group_keyword": "群关键词", "employee_id": "员工标识", "customer_id": "客户标识",
        "group_id": "群标识", "add_way": "添加方式", "deletion_direction": "删除方向",
        "resolution": "识别状态", "enrichment_status": "资料状态", "status": "处理状态", "member_type": "成员类型"}
    values = query.model_dump(mode="json")
    result["filters"] = [{"label": label, "value": ({1:"内部员工",2:"外部联系人"}.get(values[key])
                         if key == "member_type" else DISPLAY.get(values[key], values[key]))}
                         for key, label in filter_labels.items() if values[key] not in (None, "")]
    if paginate:
        if query.offset + len(items) < total:
            result["next_query"] = query.model_copy(update={"offset": query.offset + len(items)}).model_dump(mode="json")
        if query.offset:
            result["previous_query"] = query.model_copy(update={"offset": max(0, query.offset - query.limit)}).model_dump(mode="json")
    return result


async def page_rows(db, statement, query):
    total = await db.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    rows = (await db.execute(statement.offset(query.offset).limit(query.limit))).all()
    return total, rows


def scoped(query, ids):
    if query.entity_tenant_id is None:
        return ids
    if query.entity_tenant_id not in ids:
        raise HTTPException(403, "无该资料所属主体权限")
    return [query.entity_tenant_id]


async def tenant_names(db, ids):
    return dict((await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids)))).all())


async def profiles(db, ids, query, kind):
    stmt = (group_profiles_query(ids, query.keyword) if kind == "group" else
            select(Profile).where(Profile.tenant_id.in_(ids), Profile.kind == kind,
                or_(func.instr(func.lower(Profile.name), query.keyword.lower()) > 0,
                    func.instr(func.lower(Profile.external_id), query.keyword.lower()) > 0))
            .order_by(Profile.name, Profile.tenant_id, Profile.external_id))
    if query.entity_id:
        stmt = stmt.where(Profile.external_id == query.entity_id)
    total, rows = await page_rows(db, stmt, query)
    return total, [row[0] for row in rows]


async def profile_catalog(db, ids, query, kind):
    total, rows = await profiles(db, ids, query, kind)
    names = await tenant_names(db, ids)
    items = []
    for row in rows:
        data = row.data if isinstance(row.data, dict) else {}
        item = {"id": row.external_id, "name": row.name or "名称未提供",
                "tenant": names[row.tenant_id], "synced": timestamp(row.synced_at), "actions": []}
        identity = {"entity_id": row.external_id, "entity_tenant_id": row.tenant_id}
        if kind == "customer":
            item.update(company=data.get("corp_name") or "未提供",
                        customer_type={1: "微信客户", 2: "企微客户"}.get(data.get("type"), "未知"),
                        gender={1: "男", 2: "女"}.get(data.get("gender"), "未知"))
            item["actions"].append(action("跟进员工", "customer_followers", **identity))
            item["actions"].append(action("相关事件", "events", customer_id=row.external_id,
                                          entity_tenant_id=row.tenant_id))
        else:
            item["actions"] = [action("群详情", "group_detail", **identity),
                               action("群成员", "group_members", **identity),
                               action("相关事件", "events", group_id=row.external_id,
                                      entity_tenant_id=row.tenant_id)]
        if isinstance(data.get("media_id"), int):
            item["media_id"] = data["media_id"]
        items.append(item)
    columns = [("name", "名称"), ("id", "标识"), ("tenant", "所属主体")]
    if kind == "customer":
        columns += [("customer_type", "客户类型"), ("company", "企业"), ("gender", "性别")]
    columns.append(("synced", "资料同步时间"))
    return dataset(query, "已保存客户资料" if kind == "customer" else "已同步群资料", columns, items, total,
                   note="本地最近保存资料，可能包含历史记录；不按事件日期筛选，不代表平台实时全量名单。", source="profiles")


async def employee_catalog(db, ids, query):
    identities = union(select(Profile.tenant_id, Profile.external_id.label("eid")).where(
        Profile.tenant_id.in_(ids), Profile.kind == "employee"),
        select(BusinessEvent.tenant_id, BusinessEvent.employee_id).where(
            BusinessEvent.tenant_id.in_(ids), BusinessEvent.employee_id.is_not(None))).subquery()
    display = func.coalesce(func.nullif(func.nullif(Profile.name, ""), "未知"), identities.c.eid)
    stmt = select(identities.c.tenant_id, identities.c.eid, display, Profile.synced_at,
                  func.json_extract(Profile.data, "$.media_id"), func.json_extract(Profile.data, "$.position")).outerjoin(
        Profile, and_(Profile.tenant_id == identities.c.tenant_id, Profile.kind == "employee",
                      Profile.external_id == identities.c.eid)).where(or_(
        func.instr(func.lower(display), query.keyword.lower()) > 0,
        func.instr(func.lower(identities.c.eid), query.keyword.lower()) > 0))
    if query.entity_id:
        stmt = stmt.where(identities.c.eid == query.entity_id)
    total, rows = await page_rows(db, stmt.order_by(display, identities.c.tenant_id, identities.c.eid), query)
    names = await tenant_names(db, ids)
    items = [{"name": name, "id": eid, "tenant": names[tid], "synced": timestamp(stamp),
              "position": position or "未提供", "media_id": media_id if isinstance(media_id, int) else None,
              "actions": [action("相关事件", "events", employee_id=eid, entity_tenant_id=tid)]}
             for tid, eid, name, stamp, media_id, position in rows]
    return dataset(query, "已记录员工", [("name", "员工"), ("id", "标识"), ("tenant", "主体"),
                   ("synced", "资料同步"), ("position", "职位")], items, total,
                   note="含事件中出现但资料缺失的员工标识，不等同于当前在职名单；不按事件日期筛选。", source="profiles / business_events")


async def related_profiles(db, ids, kind):
    total, rows = await profiles(db, ids, DataQuery(capability="groups", keyword=kind.keyword,
                           entity_id=kind.entity_id, limit=2), "group" if kind.capability.startswith("group") else "customer")
    return total, rows


async def relationship_data(db, ids, query):
    kind = "group" if query.capability.startswith("group") else "customer"
    total, rows = await related_profiles(db, ids, query)
    if total != 1:
        if query.entity_id and not total:
            raise HTTPException(404, "资料不存在")
        candidates = await profile_catalog(db, ids, query, kind)
        candidates["title"] = "请先选择一个群" if kind == "group" else "请先选择一个客户"
        candidates["note"] = "存在多个匹配或尚未指定对象，请点击对应记录的查询按钮；不会跨主体按同名合并。"
        return candidates
    row = rows[0]
    data = row.data if isinstance(row.data, dict) else {}
    names = await tenant_names(db, ids)
    if query.capability == "group_detail":
        owner = data.get("owner")
        employee = await db.scalar(select(Profile).where(Profile.tenant_id == row.tenant_id,
            Profile.kind == "employee", Profile.external_id == owner)) if owner else None
        items = [{"name": row.name, "id": row.external_id, "tenant": names[row.tenant_id],
                  "owner": employee.name if employee else owner or "未提供",
                  "created": seconds(data.get("create_time")),
                  "members": len(data["member_list"]) if isinstance(data.get("member_list"), list) else "未提供",
                  "synced": timestamp(row.synced_at), "actions": [action("查看成员", "group_members",
                      entity_id=row.external_id, entity_tenant_id=row.tenant_id)]}]
        return dataset(query, "群资料详情", [("name", "群名"), ("id", "群标识"), ("tenant", "主体"),
            ("owner", "群主（非操作人）"), ("created", "建群时间"), ("members", "快照成员数"),
            ("synced", "同步时间")], items, 1, note="本地最近同步快照；群主不能视为退群/移出操作人。",
            source="profiles", paginate=False)
    collection = data.get("member_list" if kind == "group" else "follow_user")
    entries = [entry for entry in collection if isinstance(entry, dict)] if isinstance(collection, list) else []
    profiles_by_id = {(p.kind, p.external_id): p for p in (await db.scalars(select(Profile).where(
        Profile.tenant_id == row.tenant_id, Profile.kind.in_(["employee", "customer"]),
        Profile.external_id.in_([entry.get("userid", "") for entry in entries])))).all()}
    items = []
    for entry in entries:
        if query.member_type is not None and entry.get("type") != query.member_type:
            continue
        eid = entry.get("userid")
        if not isinstance(eid, str):
            continue
        member_kind = "employee" if kind == "customer" or entry.get("type") == 1 else "customer" if entry.get("type") == 2 else "unknown"
        profile = profiles_by_id.get((member_kind, eid))
        item = {"id": eid, "name": profile.name if profile else entry.get("name") or "姓名未提供",
                "type": {"employee": "内部员工", "customer": "外部联系人"}.get(member_kind, "类型未知"),
                "time": seconds(entry.get("join_time" if kind == "group" else "createtime")),
                "method": entry.get("join_scene" if kind == "group" else "add_way", "未提供")}
        if profile and member_kind == "customer":
            item["actions"] = [action("跟进关系", "customer_followers", entity_id=eid, entity_tenant_id=row.tenant_id)]
        items.append(item)
    count = len(items)
    note = f"来源：{names[row.tenant_id]} · {row.name}；资料同步 {timestamp(row.synced_at)}。仅代表已保存快照，不代表当前仍在群/仍有跟进关系。"
    if not isinstance(collection, list):
        note += " 快照未提供名单，不能据此判断名单为空。"
    return dataset(query, "群成员快照" if kind == "group" else "客户跟进员工", [("name", "名称"),
        ("id", "标识"), ("type", "类型"), ("time", "加入/添加时间"), ("method", "来源编码")],
        items[query.offset:query.offset + query.limit], count, note=note, source="profiles")


def event_filters(ids, query, message, first=None, last=None):
    first, last, start, end = resolve_range(message, first or query.start_date, last or query.end_date)
    filters = business_event_filters(ids, start, end, event_type=query.event_type,
        employee_id=query.employee_id, employee_keyword=query.employee_keyword,
        customer_id=query.customer_id, customer_keyword=query.customer_keyword,
        group_id=query.group_id, group_keyword=query.group_keyword, add_way=query.add_way,
        deletion_direction=query.deletion_direction, resolution=query.resolution,
        enrichment_status=query.enrichment_status)
    if query.keyword:
        filters.append(or_(func.instr(func.json_extract(BusinessEvent.snapshot, "$.employee_name"), query.keyword) > 0,
            func.instr(func.json_extract(BusinessEvent.snapshot, "$.customer_name"), query.keyword) > 0,
            func.instr(func.json_extract(BusinessEvent.snapshot, "$.group_name"), query.keyword) > 0))
    return first, last, start, end, filters


async def event_catalog(db, ids, query, request, user, message):
    first, last, _, _, filters = event_filters(ids, query, message)
    query = query.model_copy(update={"start_date": first, "end_date": last})
    shape = query.model_dump(mode="json", exclude={"offset", "cursor"})
    fingerprint = digest(json.dumps([sorted(ids), user.id, shape], sort_keys=True))
    if query.cursor:
        try:
            token = decode_cursor(query.cursor, request.app.state.settings.secret_key)
            if token["filter"] != fingerprint or not isinstance(token["upper"], int) or token["upper"] < 0:
                raise ValueError
            upper = token["upper"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "查询分页已失效，请重新查询")
    else:
        upper = await db.scalar(select(func.coalesce(func.max(BusinessEvent.id), 0)).where(*filters))
        query = query.model_copy(update={"cursor": encode_cursor({"upper": upper, "filter": fingerprint},
                                                   request.app.state.settings.secret_key)})
    filters.append(BusinessEvent.id <= upper)
    events, total, quantity = await business_event_page(
        db, filters, limit=query.limit, offset=query.offset)
    rows = await event_details(db, events)
    names = await tenant_names(db, ids)
    items = [{"id": row["id"], "type": LABELS.get(row["event_type"], "业务事件"),
              "quantity": row["quantity"], "tenant": names[row["tenant_id"]],
              "employee": row["snapshot"].get("employee_name") or row["employee_id"] or "未提供",
              "customer": row["snapshot"].get("customer_name") or row["customer_id"] or "未提供",
              "group": row["snapshot"].get("group_name") or row["group_id"] or "未提供",
              "direction": {"customer_deleted_employee": "客户删除员工", "employee_deleted_customer": "员工删除客户"}.get(row["deletion_direction"], "未提供"),
              "evidence": {"exact": "明确身份", "inferred": "快照推断", "unresolved": "待核实"}.get(row["resolution"], "待核实"),
              "enrichment": "齐全" if row["enrichment_status"] == "complete" else "待补全",
              "occurred": timestamp(row["occurred_at"]), "event_id": row["id"]} for row in rows]
    return dataset(query, "业务事件明细", [("type", "业务分类"), ("quantity", "人次"), ("tenant", "主体"),
        ("employee", "员工"), ("customer", "客户"), ("group", "群"), ("direction", "删除方向"),
        ("evidence", "识别依据"), ("enrichment", "资料状态"), ("occurred", "发生时间")], items, total,
        quantity=quantity, ranged=[str(first), str(last)], source="business_events",
        note="记录条数与涉及人次分别统计；内部/待核实群变更不计入已分类的外部客户流失。翻页固定日期及首屏事件上界。")


async def statistical_data(db, ids, query, message):
    first, last, start, end, filters = event_filters(ids, query, message)
    query = query.model_copy(update={"start_date": first, "end_date": last})
    ranged = [str(first), str(last)]
    if query.capability == "rankings":
        metric = query.event_type or "contact_added"
        field = BusinessEvent.group_id if metric.startswith("group") else BusinessEvent.employee_id
        name_key = "group_name" if metric.startswith("group") else "employee_name"
        stmt = select(BusinessEvent.tenant_id, field,
            func.max(func.json_extract(BusinessEvent.snapshot, f"$.{name_key}")),
            func.sum(BusinessEvent.quantity).label("quantity")).where(
                *filters, BusinessEvent.event_type == metric, field.is_not(None)).group_by(
                    BusinessEvent.tenant_id, field).order_by(func.sum(BusinessEvent.quantity).desc(),
                                                           BusinessEvent.tenant_id, field)
        total, rows = await page_rows(db, stmt, query)
        names = await tenant_names(db, ids)
        items = [{"rank": query.offset + i + 1, "name": name or eid, "id": eid,
                  "tenant": names[tid], "quantity": count} for i, (tid, eid, name, count) in enumerate(rows)]
        return dataset(query, LABELS[metric] + "排名", [("rank", "排名"), ("name", "名称"),
            ("tenant", "主体"), ("quantity", "人次")], items, total, ranged=ranged,
            source="business_events", note="按关系/成员变更人次排名；删除排名包含两个方向，除非指定了删除方向筛选。")
    if query.capability == "trend":
        day = func.strftime("%Y-%m-%d", BusinessEvent.occurred_at / 1000, "unixepoch", "+8 hours")
        rows = (await db.execute(select(day, BusinessEvent.event_type, func.sum(BusinessEvent.quantity))
            .where(*filters).group_by(day, BusinessEvent.event_type))).all()
        lookup = {(day, kind): count for day, kind, count in rows}
        items = [{"date": str(first + timedelta(days=i)), **{kind: lookup.get((str(first + timedelta(days=i)), kind), 0)
                 for kind in LABELS}} for i in range((last - first).days + 1)]
        return dataset(query, "每日事件趋势", [("date", "北京时间日期"), *LABELS.items()],
            items[query.offset:query.offset + query.limit], len(items), ranged=ranged,
            source="business_events", note="每项均为当日人次；其他/待核实可能包含内部员工，单独列示。")
    current = dict((await db.execute(select(BusinessEvent.event_type, func.sum(BusinessEvent.quantity))
        .where(*filters).group_by(BusinessEvent.event_type))).all())
    span = (last - first).days + 1
    # Use the same non-date predicates for the previous equal-length natural-day window.
    previous_filters = event_filters(ids, query, "", first - timedelta(days=span), first - timedelta(days=1))[4]
    previous = dict((await db.execute(select(BusinessEvent.event_type, func.sum(BusinessEvent.quantity))
        .where(*previous_filters).group_by(BusinessEvent.event_type))).all())
    items = [{"type": label, "current": current.get(kind, 0), "previous": previous.get(kind, 0),
              "delta": current.get(kind, 0) - previous.get(kind, 0)} for kind, label in LABELS.items()]
    return dataset(query, "区间事件统计", [("type", "分类"), ("current", "本期人次"),
        ("previous", "上期人次"), ("delta", "变化人次")], items, len(items), ranged=ranged,
        source="business_events", paginate=False,
        note=f"上期 {first - timedelta(days=span)} 至 {first - timedelta(days=1)}。数量不是独立客户数；今天未结束时仍与上一完整区间比较。")


def safe_error(value):
    if not value:
        return "无"
    match = re.search(r"(?:errcode|error_code|code)[\s\"':=]+(-?\d{1,7})", value, re.I)
    return "平台错误码 " + match[1] if match else "处理异常（详细原因请在管理页核对）"


async def administrative_data(db, ids, query, settings, message):
    names = await tenant_names(db, ids)
    ranged = None
    record_times = []
    if query.capability in ("callbacks", "audits") and (query.start_date or query.end_date or re.search(
            r"今天|昨天|本周|上周|(?:近|最近)\s*\d+\s*天|\d{4}-\d{2}-\d{2}", message)):
        first, last, start, end = resolve_range(message, query.start_date, query.end_date)
        query = query.model_copy(update={"start_date": first, "end_date": last})
        ranged = [str(first), str(last)]
        field = Inbox.received_at if query.capability == "callbacks" else Audit.created_at
        record_times = [field >= start, field < end]
    if query.capability == "tenant_settings":
        stmt = select(Tenant).where(func.instr(Tenant.name, query.keyword) > 0).order_by(Tenant.id)
        if query.entity_id:
            stmt = stmt.where(Tenant.id == numeric_id(query.entity_id))
        total, rows = await page_rows(db, stmt, query)
        items = [{"id": t.id, "name": t.name, "corp_id": t.corp_id, "enabled": "启用" if t.enabled else "停用",
                  "credentials": "已填写" if all(settings.credential(t.credential_ref).values()) else "待配置"}
                 for (t,) in rows]
        return dataset(query, "主体配置", [("id", "编号"), ("name", "名称"), ("corp_id", "企业标识"),
            ("enabled", "状态"), ("credentials", "凭据状态")], items, total, source="tenants",
            note="管理员全局主体配置，不按当前主体筛选；凭据状态仅检查非空，不代表平台授权可用。")
    if query.capability == "users":
        stmt = select(User).where(func.instr(User.name, query.keyword) > 0).order_by(User.id)
        if query.entity_id:
            stmt = stmt.where(User.id == numeric_id(query.entity_id))
        total, rows = await page_rows(db, stmt, query)
        all_names = dict((await db.execute(select(Tenant.id, Tenant.name))).all())
        user_ids = [row.id for (row,) in rows]
        grants = (await db.execute(select(Grant.user_id, Grant.tenant_id).where(Grant.user_id.in_(user_ids)))).all()
        items = [{"id": row.id, "name": row.name, "role": "管理员" if row.role == "admin" else "普通用户",
                  "enabled": "启用" if row.enabled else "停用", "grants": "所有启用主体" if row.role == "admin" else
                  "、".join(all_names.get(tid, str(tid)) for uid, tid in grants if uid == row.id) or "无授权"}
                 for (row,) in rows]
        return dataset(query, "用户与主体授权", [("id", "编号"), ("name", "名称"), ("role", "角色"),
            ("enabled", "状态"), ("grants", "主体授权")], items, total, source="app_users / user_tenants",
            note="管理员全局用户管理数据，不按当前主体筛选。不展示登录令牌、CSRF 或会话哈希。")
    if query.capability == "audits":
        stmt = select(Audit).where(*record_times, func.instr(Audit.action, query.keyword) > 0).order_by(Audit.created_at.desc(), Audit.id.desc())
        if query.entity_id:
            stmt = stmt.where(Audit.id == numeric_id(query.entity_id))
        total, rows = await page_rows(db, stmt, query)
        actors = dict((await db.execute(select(User.id, User.name).where(
            User.id.in_([row.actor_user_id for (row,) in rows if row.actor_user_id])))).all())
        actions = {"tenant.create": "新增主体", "tenant.update": "更新主体", "user.create": "创建用户",
                   "grants.replace": "更新主体授权", "job.retry": "重试任务", "assistant.query": "助手查询"}
        items = [{"id": row.id, "actor": actors.get(row.actor_user_id, "未记录"), "action": actions.get(row.action, "其他管理操作"),
                  "target": row.target if re.fullmatch(r"[\d, ]{1,100}", row.target or "") else "已记录（目标详情已脱敏）",
                  "time": timestamp(row.created_at)} for (row,) in rows]
        return dataset(query, "管理审计", [("id", "编号"), ("actor", "操作者"), ("action", "动作"),
            ("target", "目标"), ("time", "时间")], items, total, source="audit_logs", ranged=ranged,
            note="管理员全局操作元数据，不按主体筛选；显式日期按审计发生时间筛选，不展示问题、回答或原始请求上下文。")
    model = Job if query.capability == "jobs" else Inbox
    stmt = select(model).where(model.tenant_id.in_(ids), *record_times).order_by(model.id.desc())
    if query.status:
        stmt = stmt.where(model.status == query.status)
    if query.entity_id:
        stmt = stmt.where(model.id == numeric_id(query.entity_id))
    total, rows = await page_rows(db, stmt, query)
    items = []
    for (row,) in rows:
        item = {"id": row.id, "tenant": names[row.tenant_id], "status": DISPLAY.get(row.status, "未知")}
        if model is Job:
            item.update(callback_id=row.callback_id, attempts=row.attempts,
                        next_attempt=timestamp(row.next_run_at), error=safe_error(row.last_error))
        else:
            job = await db.scalar(select(Job).where(Job.callback_id == row.id, Job.tenant_id == row.tenant_id))
            item.update(received=timestamp(row.received_at), job_id=job.id if job else "未记录",
                        actions=[action("关联任务", "jobs", entity_id=str(job.id), entity_tenant_id=row.tenant_id)] if job else [])
        items.append(item)
    columns = [("id", "编号"), ("tenant", "主体"), ("status", "状态")]
    columns += ([("callback_id", "关联回调"), ("attempts", "尝试次数"), ("next_attempt", "下次执行"), ("error", "错误类别")]
                if model is Job else [("received", "接收时间"), ("job_id", "关联任务")])
    return dataset(query, "处理任务" if model is Job else "回调收件箱", columns, items, total, ranged=ranged,
                   source=model.__tablename__, note="只读状态查询，不执行重试，不展示原始载荷、去重摘要或上游错误原文。回调显式日期按接收时间筛选；任务未保存失败发生时间，不能按失败日期统计。")


def numeric_id(value):
    if not re.fullmatch(r"[1-9]\d{0,17}", value):
        raise HTTPException(422, "记录编号格式不正确")
    return int(value)


async def execute_query(db, ids, query, request, user, session_id, message):
    if query.capability in ADMIN and user.role != "admin":
        raise HTTPException(403, "该能力需要管理员权限")
    ids = scoped(query, ids)
    event_fields = {"event_type", "deletion_direction", "resolution", "enrichment_status",
                    "employee_keyword", "customer_keyword", "group_keyword", "employee_id",
                    "customer_id", "group_id", "add_way", "cursor"}
    if query.capability not in ("events", "statistics", "trend", "rankings") and any(
            getattr(query, field) not in (None, "") for field in event_fields):
        raise HTTPException(422, "该资料查询不支持事件筛选条件，请选择事件明细、统计或趋势")
    if query.status and query.capability not in ("jobs", "callbacks"):
        raise HTTPException(422, "该查询不支持处理任务状态筛选")
    if query.member_type is not None and query.capability != "group_members":
        raise HTTPException(422, "成员类型筛选仅用于群成员快照")
    if (query.start_date or query.end_date) and query.capability not in (
            "events", "statistics", "trend", "rankings", "callbacks", "audits"):
        raise HTTPException(422, "该资料查询不支持日期筛选")
    if query.keyword and query.capability in ("overview", "me", "media", "capabilities", "jobs", "callbacks"):
        raise HTTPException(422, "该查询不支持名称关键词筛选")
    if query.capability == "capabilities":
        rows = capabilities(user.role)
        items = [{**row, "scope": "管理员" if row["admin_only"] else "已授权主体",
                  "actions": [action("查询", row["id"])]} for row in rows]
        return dataset(query, "可查询的数据能力", [("name", "能力"), ("description", "内容"),
            ("scope", "权限"), ("example", "提问示例")], items, len(items), paginate=False,
            note="数据只在当前权限内查询；模型仅解析问题，资料、名单与管理元数据由服务端直接返回。", source="能力清单")
    if query.capability == "overview":
        kinds = dict((await db.execute(select(Profile.kind, func.count()).where(
            Profile.tenant_id.in_(ids)).group_by(Profile.kind))).all())
        events = await db.scalar(select(func.count()).select_from(BusinessEvent).where(BusinessEvent.tenant_id.in_(ids)))
        media = await db.scalar(select(func.count()).select_from(Media).where(Media.tenant_id.in_(ids)))
        items = [{"kind": label, "count": count} for label, count in (
            ("授权启用主体", len(ids)), ("员工资料快照", kinds.get("employee", 0)),
            ("客户资料快照", kinds.get("customer", 0)), ("群资料快照", kinds.get("group", 0)),
            ("历史业务事件记录", events), ("本地头像资源", media))]
        return dataset(query, "已保存数据概览", [("kind", "类别"), ("count", "条数")], items,
                       len(items), paginate=False, source="本地业务库", note="全历史已保存条数，不按事件日期筛选；不等于平台实时人数或群数。")
    if query.capability == "tenants":
        stmt = select(Tenant).where(Tenant.id.in_(ids), func.instr(Tenant.name, query.keyword) > 0).order_by(Tenant.id)
        if query.entity_id:
            stmt = stmt.where(Tenant.id == numeric_id(query.entity_id))
        total, rows = await page_rows(db, stmt, query)
        return dataset(query, "授权主体", [("id", "编号"), ("name", "主体名称"), ("created", "接入时间")],
            [{"id": row.id, "name": row.name, "created": timestamp(row.created_at)} for (row,) in rows],
            total, source="tenants / user_tenants", note="仅显示当前账号已授权且启用的主体。")
    if query.capability == "employees":
        return await employee_catalog(db, ids, query)
    if query.capability in ("customers", "groups"):
        return await profile_catalog(db, ids, query, "customer" if query.capability == "customers" else "group")
    if query.capability in ("group_detail", "group_members", "customer_followers"):
        return await relationship_data(db, ids, query)
    if query.capability == "events":
        return await event_catalog(db, ids, query, request, user, message)
    if query.capability in ("statistics", "trend", "rankings"):
        return await statistical_data(db, ids, query, message)
    if query.capability in ADMIN:
        return await administrative_data(db, ids, query, request.app.state.settings, message)
    if query.capability == "me":
        current, session = await db.get(User, user.id), await db.get(Session, session_id)
        names = await tenant_names(db, ids)
        items = [{"name": current.name, "role": "管理员" if current.role == "admin" else "普通用户",
                  "tenants": "、".join(names.values()), "expires": timestamp(session.expires_at)}]
        return dataset(query, "我的账号", [("name", "姓名"), ("role", "角色"), ("tenants", "当前查询主体"),
            ("expires", "本次登录到期")], items, 1, paginate=False, source="当前登录账号与授权")
    if query.capability == "media":
        stmt = select(Media).where(Media.tenant_id.in_(ids)).order_by(Media.id.desc())
        if query.entity_id:
            stmt = stmt.where(Media.id == numeric_id(query.entity_id))
        total, rows = await page_rows(db, stmt, query)
        names = await tenant_names(db, ids)
        return dataset(query, "本地头像资源", [("id", "编号"), ("tenant", "主体"), ("mime", "图片类型"),
            ("bytes", "字节数")], [{"id": row.id, "tenant": names[row.tenant_id], "mime": row.mime_type,
            "bytes": row.size_bytes, "media_id": row.id} for (row,) in rows], total, source="media_assets",
            note="图片通过已有媒体鉴权接口按需预览；不展示磁盘路径、来源网址或内容摘要。")
    raise HTTPException(422, "该能力需要指定事件编号")


async def plan_query(settings, message, history, role):
    """The model chooses a schema-validated tool; it receives no database rows or credentials."""
    from .assistant import request_completion
    prompt = ("你是只读查询路由器，只返回一个 JSON 对象，不回答问题、不输出 SQL。"
              "从下面能力中选择 capability，并按 schema 填写用户明确说出的筛选字段。"
              "不要猜测名字、ID、日期或其他条件；未明确的字段省略。不得创建、删除、重试或发送消息。"
              "群成员和客户跟进关系需要 keyword(名称)或 entity_id，无法确定对象时留空供用户选择。"
              "资料关键词只放 keyword；事件/统计中员工、群、客户姓名分别放对应 keyword 字段。"
              "日期中的自然语言由服务端解释，不必猜测今天。报告与普通分析请返回 capability=statistics。"
              "\n能力：" + json.dumps(capabilities(role), ensure_ascii=False)
              + "\nJSON schema：" + json.dumps(DataQuery.model_json_schema(), ensure_ascii=False))
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content":
        ("此前用户问题：\n" + "\n".join(history[-3:]) + "\n本次问题：\n" if history else "") + message}]
    try:
        text, _ = await request_completion(settings, messages, max_tokens=600, json_mode=True, timeout=12)
        return DataQuery.model_validate(json.loads(text))
    except (ModelUnavailable, ValueError, ValidationError, TimeoutError):
        return None
