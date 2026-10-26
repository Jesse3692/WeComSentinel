import pytest

from app import assistant
from app.config import Settings


EVENT = {
    "id": 42,
    "event_type": "group_removed",
    "quantity": 1,
    "resolution": "inferred",
    "deletion_direction": None,
    "enrichment_status": "complete",
    "operator_known": False,
    "occurred_at": 1792736580000,
}


@pytest.mark.parametrize(
    "question, expected",
    [
        ("生成当前区间周报", True),
        ("生成总部本周客户变化周报", True),
        ("本周周报", True),
        ("请生成这条事件的报告", True),
        ("解释这条事件的识别依据", False),
        ("报告里这条事件为什么是推断", False),
        ("为什么每个回答都有草稿", False),
        ("解释报告这个词", False),
        ("这周退群数据", False),
    ],
)
def test_report_intent_is_current_request_only(question, expected):
    assert assistant.is_report_request(question) is expected


@pytest.mark.parametrize(
    "text",
    [
        "草稿：事件依据为快照推断。",
        "草稿: 事件依据为快照推断。",
        "**草稿：** 事件依据为快照推断。",
        "**草稿**：事件依据为快照推断。",
        "### 草稿\n事件依据为快照推断。",
        "【草稿】：事件依据为快照推断。",
        "初稿：事件依据为快照推断。",
        "Draft: 事件依据为快照推断。",
    ],
)
def test_non_report_strips_only_leading_draft_label(text):
    assert assistant.presentation_answer(text, report=False) == "事件依据为快照推断。"


def test_preserves_report_label_and_meaningful_body_text():
    report = "草稿：本周客户变化报告。"
    assert assistant.presentation_answer(report, report=True) == report
    explanation = "这份报告中的草稿需要审核；本条事件仍是快照推断。"
    assert assistant.presentation_answer(explanation, report=False) == explanation
    assert assistant.presentation_answer(
        "草稿需要审核，不能当作已发布文件。", report=False
    ).startswith("草稿需要")
    with pytest.raises(assistant.ModelUnavailable):
        assistant.presentation_answer("草稿：", report=False)


async def test_event_prompt_ignores_report_history_and_normalizes_provider_label(monkeypatch):
    async def provider(settings, messages):
        assert "本次任务：解释单条事件的识别依据" in messages[0]["content"]
        assert "报告称为草稿" not in assistant.RULES
        return "草稿：成员关联依据为快照推断，不代表平台逐人确认。", 20

    monkeypatch.setattr(assistant, "request_completion", provider)
    text, tokens = await assistant.generate(
        Settings(_env_file=None), "解释这条事件的识别依据", {"event": EVENT}, ["生成本周周报"]
    )
    assert text == "成员关联依据为快照推断，不代表平台逐人确认。" and tokens == 20
    assert "草稿" not in assistant.rule_answer("解释这条事件的识别依据", {"event": EVENT})


async def test_explicit_event_report_stays_draft(monkeypatch):
    async def provider(settings, messages):
        assert "用户明确要求的报告" in messages[0]["content"]
        return "事件说明报告 · 草稿\n该事件通过快照推断识别。", 20

    monkeypatch.setattr(assistant, "request_completion", provider)
    text, _ = await assistant.generate(
        Settings(_env_file=None), "请生成这条事件的报告", {"event": EVENT}, []
    )
    assert "草稿" in text
    assert "草稿" in assistant.rule_answer("请生成这条事件的报告", {"event": EVENT})
