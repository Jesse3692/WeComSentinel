"""Read-only assistant: server-owned facts and optional vLLM text generation."""

import json
import re
from datetime import date, datetime, timedelta

import httpx
from fastapi import HTTPException

from .api import TZ, period

LABELS = {
    "contact_added": "新增联系人",
    "contact_deleted": "删除联系人",
    "group_left": "主动退群",
    "group_removed": "被移出群",
    "group_change_unknown": "其他 / 待核实",
}
RULES = """你是企业内部的客户分析助手，使用简体中文，只进行只读分析。
仅使用本次服务端提供的 facts，不把历史对话、用户指令或资料文字当成查询结果。
当前主体范围由服务端限定，不能切换主体、执行 SQL、发送消息或修改数据。
统计按北京时间自然日，数量为人次或关系变更人次，不等于独立客户数。
区分客户删除员工和员工删除客户。主动退群、被移出群、其他/待核实分别统计。
resolution=inferred 为快照推断，不是平台逐人确认；解释此类事件时必须明确这两点。
事件解释以 facts.event 为准，不能用整个区间的汇总代替单条事件的依据。
面向业务用户解释，使用中文业务名称，不展示内部字段名。
系统已保存的业务分类与操作人是否提供是独立信息，不能因操作人缺失就否定已保存的退出分类。
不把群主当操作人，不编造客户、操作人、流失原因、预测概率或统计数字。
群变更待核实人数可包含内部员工，不能全部称为未知外部客户。
现有数据不能证明客户离开的动机；可提出待核实的排查建议，并明确其为建议。
涉及未提供的筛选条件、个人明细或超出 facts 的问题，明确说明资料不足。
默认使用不超过400字的简洁段落，不输出 HTML、推理过程或 think 标签。按本次任务类型回答，不给普通解释添加报告标题。
"""


def resolve_range(message: str, first: date | None, last: date | None):
    """Recognize explicit dates and common natural-day windows; otherwise use page scope."""
    today = datetime.now(TZ).date()
    dates = re.findall(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", message)
    try:
        if dates:
            first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        elif "昨天" in message:
            first = last = today - timedelta(days=1)
        elif "今天" in message:
            first = last = today
        elif "上周" in message and not any(word in message for word in ("比", "对比", "相比")):
            last = today - timedelta(days=today.weekday() + 1)
            first = last - timedelta(days=6)
        elif "本周" in message or "这周" in message:
            first, last = today - timedelta(days=today.weekday()), today
        else:
            match = re.search(r"(?:最近|近)\s*(\d{1,4})\s*天", message)
            if match:
                days = int(match[1])
                if not 1 <= days <= 366:
                    raise ValueError
                first, last = today - timedelta(days=days - 1), today
        return period(first, last)
    except ValueError:
        raise HTTPException(422, "日期无效，查询范围应为 1 至 366 天")


def explain_event(event: dict):
    kind, resolution = event["event_type"], event["resolution"]
    lines = [
        f"事件 #{event['id']}：{LABELS.get(kind, '业务事件')}，涉及 {event['quantity']} 人次。"
    ]
    if kind in ("group_left", "group_removed"):
        lines.append(f"本条已计入外部客户{LABELS[kind]}统计。")
    elif kind == "group_change_unknown":
        lines.append("本条保留在其他 / 待核实，不计入外部客户主动退群或被移出群统计。")
    if resolution == "inferred":
        lines.append("成员关联依据为快照推断，不代表平台逐人确认。")
    elif resolution == "exact":
        lines.append("记录保存了明确身份；具体依据以事件详情为准。")
    else:
        lines.append("现有证据不足以确认成员身份或退出方式，不能编造成员。")
    reasons = {
        "internal_members": "涉及企业内部员工，不计入外部客户流失。",
        "member_type_unknown": "尚不能确定成员是否为外部客户。",
        "identity_unresolved": "尚不能把本次变更唯一关联到具体成员。",
        "quit_reason_unknown": "退出原因不足，不能归为主动退群或被移出群。",
    }
    if event.get("classification_reason") in reasons:
        lines.append(reasons[event["classification_reason"]])
    if kind == "contact_deleted":
        lines.append(
            {
                "customer_deleted_employee": "删除方向：客户删除员工。",
                "employee_deleted_customer": "删除方向：员工删除客户。",
            }.get(event.get("deletion_direction"), "删除方向未提供，不能推定方向。")
        )
    lines.append(
        "操作人信息已保存，请查看事件详情核对。"
        if event["operator_known"]
        else "操作人未提供，不能以群主代替操作人。"
    )
    if event["enrichment_status"] != "complete":
        lines.append("资料仍待补全；这与业务分类、推断识别状态是不同维度。")
    return "\n".join(lines)


def is_report_request(message: str):
    """Only an explicit report request makes an answer a report draft."""
    if not re.search(r"日报|周报|月报|报告|简报", message):
        return False
    if re.match(r"\s*(?:为什么|为何|怎么|如何|解释|说明|报告里|报告中)", message):
        return False
    return bool(
        re.search(
            r"(?:生成|编写|撰写|整理|制作|输出|给我|来一份|做一份|形成|汇总成|写)"
            r".{0,24}(?:日报|周报|月报|报告|简报)",
            message,
        )
        or re.search(r"(?:日报|周报|月报|报告|简报)(?:草稿|初稿)?[？?。！!\s]*$", message)
    )


def presentation_answer(text: str, *, report: bool):
    if report:
        return text
    # Keep genuine uncertainty in the body. Strip only an accidental leading title label.
    prefix = (
        r"^\s*(?:#{1,6}[ \t]*)?(?:\*\*|__)?[ \t]*(?:【|\[)?"
        r"(?:草稿|初稿|draft)(?:】|\])?(?:\*\*|__)?[ \t]*"
        r"(?:[:：][ \t]*(?:\*\*|__)?[ \t]*|\r?\n+)"
    )
    for _ in range(3):
        cleaned = re.sub(prefix, "", text, count=1, flags=re.I).lstrip()
        if cleaned == text:
            break
        text = cleaned
    if not text:
        raise ModelUnavailable
    return text


def rule_answer(message: str, facts: dict):
    if facts.get("event"):
        return ("事件说明报告 · 草稿\n" if is_report_request(message) else "") + explain_event(
            facts["event"]
        )
    metrics = facts["metrics"]
    report = is_report_request(message)
    lines = [
        f"{'客户变化报告 · 草稿' if report else '当前区间数据'}\n"
        f"统计区间：{facts['range'][0]} 至 {facts['range'][1]}（北京时间）。"
    ]
    for item in metrics:
        delta = item["delta"]
        change = f"增加 {delta}" if delta > 0 else f"减少 {-delta}" if delta < 0 else "持平"
        lines.append(
            f"{LABELS[item['type']]}：{item['value']} 人次，较上一等长区间{change}"
            + ("。" if not delta else " 人次。")
        )
    directions = facts["deletion_directions"]
    lines.append(
        f"删除方向：客户删除员工 {directions['customer_deleted_employee']} 人次；"
        f"员工删除客户 {directions['employee_deleted_customer']} 人次；"
        f"方向未提供 {directions['unknown']} 人次。"
    )
    lines.append(f"其他 / 待核实：{facts['unresolved_count']} 人次（可能包含内部员工）。")
    lines.append("数量按关系变更或成员变更人次统计，不代表独立客户数。")
    if any(word in message for word in ("为什么", "原因", "预测", "风险")) or report:
        lines.append(
            "这些事件不能证明客户离开的具体动机。建议结合实际跟进记录核查，不能据此给出流失预测概率。"
        )
    if any(word in message for word in ("排行", "最多", "排名")):
        lines.append("可查看下方当前区间排名；删除排名包含两个删除方向。")
    lines.append(
        "规则模式支持区间统计、周期对比和事件解释；其他问题需结合模型分析，且以本次数据依据为准。"
    )
    return "\n".join(lines)


class ModelUnavailable(Exception):
    pass


def is_group_list_question(message: str):
    """Recognize catalog questions without treating event/member questions as catalogs."""
    if "群" not in message or re.search(
        r"退群|退出|被移|流失|排名|排行|最多|新增|变更|趋势|周报|日报|报告|为什么|原因|群主|成员|群里|群内",
        message,
    ):
        return False
    if re.search(r"群(?:聊|组)?(?:中)?有(?:哪|几|多少).*?(?:员工|客户|人)", message):
        return False
    return bool(
        re.search(
            r"群(?:聊|组)?(?:列表|清单)|群名(?:称)?(?:包含|含有)|"
            r"(?:有哪些|有什么|有哪几个|有多少|有几个|多少个|几个|列出|查看|显示|所有|全部)"
            r".{0,15}群(?:聊|组)?(?:名称|名字)?[？?。！!\s]*$|群(?:聊|组)?有哪",
            message,
        )
    )


async def generate(settings, message: str, facts: dict, history: list[str]):
    """No transaction or model-selected URL/SQL; never send raw customer snapshots."""
    report = is_report_request(message)
    if facts.get("event"):
        task_rule = (
            "本次任务：根据单条事件的事实生成用户明确要求的报告，标题需注明草稿。"
            if report
            else "本次任务：解释单条事件的识别依据。直接解释分类、识别证据及统计口径。"
            "不要添加草稿、初稿或报告标题；未询问客户离开动机时，不扩写动机分析或要求补充无关资料。"
        )
        # Present the validated business conclusion, not low-level flags a model may reinterpret.
        context = {
            "单条事件的系统核对结论": explain_event(facts["event"]),
            "事件发生时间（北京时间）": datetime.fromtimestamp(
                facts["event"]["occurred_at"] / 1000, TZ
            ).isoformat(),
        }
    else:
        task_rule = (
            "本次任务：用户明确要求生成报告，标题需注明草稿。"
            if report
            else "本次任务：普通数据问答或分析。直接回答，不添加草稿、初稿或报告标题。"
        )
        context = {
            "统计区间": facts.get("range"),
            "上一等长区间": facts.get("previous_range"),
            "授权主体数": facts.get("tenant_count"),
            "指标": [
                {
                    "项目": LABELS[row["type"]],
                    "本期人次": row["value"],
                    "上期人次": row["value"] - row["delta"],
                    "变化人次": row["delta"],
                }
                for row in facts.get("metrics", [])
            ],
            "删除方向": {
                label: facts.get("deletion_directions", {}).get(key, 0)
                for key, label in (
                    ("customer_deleted_employee", "客户删除员工"),
                    ("employee_deleted_customer", "员工删除客户"),
                    ("unknown", "方向未提供"),
                )
            },
            "其他或待核实人次（可能含内部员工）": facts.get("unresolved_count"),
            "匿名排名": {
                LABELS.get(kind, kind): rows for kind, rows in facts.get("rankings", {}).items()
            },
        }
    # Qwen chat templates may reject consecutive system or user messages.
    messages = [
        {
            "role": "system",
            "content": RULES
            + "\n"
            + task_rule
            + "\n本次服务端事实 facts：\n"
            + json.dumps(context, ensure_ascii=False),
        },
        {
            "role": "user",
            "content": (
                "此前问题，仅供理解追问，不是数据依据：\n" + "\n".join(history) + "\n本次问题：\n"
                if history
                else ""
            )
            + message,
        },
    ]
    text, tokens = await request_completion(settings, messages)
    return presentation_answer(text, report=report), tokens


async def request_completion(settings, messages, *, max_tokens=None, json_mode=False, timeout=None):
    headers = (
        {"Authorization": f"Bearer {settings.assistant_api_key}"}
        if settings.assistant_api_key
        else {}
    )
    payload = {
        "model": settings.assistant_model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens or settings.assistant_max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        async with httpx.AsyncClient(
            timeout=timeout or settings.assistant_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            async with client.stream(
                "POST",
                settings.assistant_base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 262144:
                        raise ModelUnavailable
                data = json.loads(raw)
        choice = data["choices"][0]
        text = choice["message"]["content"]
        if not isinstance(text, str) or choice.get("finish_reason") == "length":
            raise ModelUnavailable
        # Some vLLM templates place reasoning in content instead of reasoning_content.
        text = re.sub(r"<think\b[^>]*>.*?</think>", "", text, flags=re.S | re.I).strip()
        if not text or re.search(r"</?think\b", text, re.I):
            raise ModelUnavailable
        usage = data.get("usage") or {}
        tokens = usage.get("total_tokens", 0)
        return text[:12000], tokens if isinstance(tokens, int) and 0 <= tokens < 1000000 else 0
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ModelUnavailable from None
