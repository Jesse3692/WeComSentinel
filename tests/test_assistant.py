import json
from datetime import date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

import test_system
from app import assistant, assistant_api
from app.api import TZ
from app.config import Settings
from app.models import Audit, BusinessEvent, Grant, Profile, Session, Tenant
from test_system import insert_event, login

system = test_system.system


async def ask(client, **kwargs):
    return await client.post("/api/v1/assistant/chat", json={"message": "对比当前区间", **kwargs})


async def test_assistant_identity_csrf_scope_and_disabled(system):
    app, client, _ = system
    assert (await ask(client)).status_code == 401
    foreign = await insert_event(app, tenant=2)
    await login(client, "user")
    assert (await ask(client, tenant_id="2")).status_code == 403
    assert (await ask(client, event_id=foreign)).status_code == 404
    csrf = client.headers.pop("x-csrf-token")
    assert (await ask(client)).status_code == 403
    client.headers["x-csrf-token"] = csrf
    app.state.settings.assistant_enabled = False
    assert (await ask(client)).status_code == 404


async def test_assistant_metrics_directions_and_fresh_snapshot(system):
    app, client, _ = system
    day = datetime.now(TZ).date()
    stamp = int(datetime.combine(day, datetime.min.time(), TZ).timestamp() * 1000) + 1000
    await insert_event(app, kind="group_left", quantity=7, stamp=stamp)
    await insert_event(app, kind="group_left", quantity=3, stamp=stamp - 86400000)
    await insert_event(app, kind="group_left", quantity=900, stamp=stamp, tenant=2)
    await insert_event(app, kind="group_change_unknown", quantity=4, stamp=stamp)
    for direction, quantity in (
        ("customer_deleted_employee", 2),
        ("employee_deleted_customer", 3),
        (None, 1),
    ):
        eid = await insert_event(app, kind="contact_deleted", quantity=quantity, stamp=stamp)
        async with app.state.sessions() as db:
            (await db.get(BusinessEvent, eid)).deletion_direction = direction
            await db.commit()
    await login(client, "user")
    response = await ask(client, start_date=str(day), end_date=str(day))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    card = next(row for row in data["cards"] if row["type"] == "group_left")
    assert card == {"type": "group_left", "value": 7, "delta": 4}
    assert data["deletion_directions"] == {
        "customer_deleted_employee": 2,
        "employee_deleted_customer": 3,
        "unknown": 1,
    }
    assert data["unresolved_count"] == 4
    assert data["mode"] == "rules"
    assert "内部员工" in data["answer"] and "独立客户" in data["answer"]
    assert all(
        [
            (await client.get(f"/api/v1/events/{row['event_id']}")).status_code == 200
            for row in data["sources"]
        ]
    )
    # An assistant query bypasses the dashboard TTL cache, without mutating that cache.
    app.state.report_cache.ttl_ms = 30000
    url = f"/api/v1/dashboard?start_date={day}&end_date={day}"
    before = (await client.get(url)).json()["data"]
    await insert_event(app, kind="contact_added", quantity=5, stamp=stamp)
    fresh = (await ask(client, start_date=str(day), end_date=str(day))).json()["data"]
    assert fresh["cards"][0]["value"] == before["cards"][0]["value"] + 5
    assert (await client.get(url)).json()["data"]["cards"] == before["cards"]


async def test_event_explanation_keeps_inferred_and_internal_boundaries(system):
    app, client, _ = system
    inferred = await insert_event(app, kind="group_left", quantity=2)
    internal = await insert_event(app, kind="group_change_unknown")
    async with app.state.sessions() as db:
        (await db.get(BusinessEvent, inferred)).resolution = "inferred"
        row = await db.get(BusinessEvent, internal)
        row.resolution = "inferred"
        row.snapshot = {
            "classification_reason": "internal_members",
            "group_owner_id": "owner-is-not-operator",
        }
        await db.commit()
    await login(client, "user")
    first = (await ask(client, event_id=inferred)).json()["data"]
    assert "已计入外部客户主动退群" in first["answer"]
    assert "不代表平台逐人确认" in first["answer"]
    second = (await ask(client, event_id=internal)).json()["data"]
    assert "内部员工" in second["answer"] and "不计入" in second["answer"]
    assert "操作人未提供" in second["answer"]


async def test_model_receives_only_sanitized_facts_after_db_release(system, monkeypatch):
    app, client, _ = system
    event_id = await insert_event(app, kind="group_left")
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, event_id)
        row.customer_id = "private-customer-id"
        row.snapshot = {
            "customer_name": "private-customer-name",
            "_secret": "private-payload",
            "classification_reason": "inject-system-instructions",
        }
        await db.commit()
    app.state.settings.assistant_base_url = "http://model.invalid/v1"

    async def model(settings, message, facts, history):
        assert app.state.engine.pool.checkedout() == 0
        payload = json.dumps(facts)
        assert "private-" not in payload and "inject-system" not in payload
        assert "员工一" not in payload and "客户一" not in payload
        return "模型解释：成员身份请以原始记录为准。", 40

    monkeypatch.setattr(assistant_api, "generate", model)
    await login(client, "user")
    response = await ask(client, event_id=event_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "model"
    async with app.state.sessions() as db:
        audit = await db.scalar(select(Audit).where(Audit.action == "assistant.query"))
        assert audit.actor_user_id == 2 and "tokens=40" in audit.target
        assert "private" not in audit.target


async def test_model_failure_falls_back_without_exposing_provider_error(system, monkeypatch):
    app, client, _ = system
    app.state.settings.assistant_base_url = "http://model.invalid/v1"

    async def broken(*args):
        raise assistant.ModelUnavailable("upstream secret or token")

    monkeypatch.setattr(assistant_api, "generate", broken)
    await login(client)
    response = await ask(client)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "rules" and "模型暂不可用" in data["warning"]
    assert "secret" not in response.text


@pytest.mark.parametrize("change", ["grant", "logout"])
async def test_no_delivery_after_inflight_authorization_change(system, monkeypatch, change):
    app, client, _ = system
    app.state.settings.assistant_base_url = "http://model.invalid/v1"

    async def model(*args):
        async with app.state.sessions() as db:
            if change == "grant":
                await db.execute(delete(Grant).where(Grant.user_id == 2))
            else:
                for session in (await db.scalars(select(Session))).all():
                    session.revoked = True
            await db.commit()
        return "must-not-be-delivered", 1

    monkeypatch.setattr(assistant_api, "generate", model)
    await login(client, "user")
    response = await ask(client)
    assert response.status_code == (403 if change == "grant" else 401)
    assert "must-not-be-delivered" not in response.text
    assert not app.state.assistant_active


async def test_limits_validation_and_date_resolution(system):
    app, client, _ = system
    await login(client)
    for body in (
        {"message": " "},
        {"message": "x" * 2001},
        {"message": "近367天"},
        {"message": "2026-02-30"},
        {"message": "x", "history": ["a"] * 7},
    ):
        assert (await client.post("/api/v1/assistant/chat", json=body)).status_code == 422
    app.state.assistant_attempts[1] = [assistant_api.now_ms()] * 10
    assert (await ask(client)).status_code == 429
    assert not app.state.assistant_active
    today = datetime.now(TZ).date()
    first, last, *_ = assistant.resolve_range("本周", None, None)
    assert first == today - timedelta(days=today.weekday()) and last == today
    assert assistant.resolve_range("2026-10-01 至 2026-10-03", None, None)[:2] == (
        date(2026, 10, 1),
        date(2026, 10, 3),
    )


async def test_vllm_adapter_no_auth_header_and_reasoning_not_exposed(monkeypatch):
    original = httpx.AsyncClient

    def handler(request):
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == "qwen35-122b-local"
        assert [row["role"] for row in body["messages"]] == ["system", "user"]
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "<think>private reasoning</think>查询结果"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 12},
            },
        )

    monkeypatch.setattr(
        assistant.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    settings = Settings(_env_file=None, assistant_base_url="http://model.invalid/v1")
    assert await assistant.generate(settings, "问题", {}, []) == ("查询结果", 12)


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "<think>unfinished"}}]},
        {"choices": [{"message": {"content": "truncated"}, "finish_reason": "length"}]},
    ],
)
async def test_vllm_bad_output_rejected(monkeypatch, payload):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        assistant.httpx,
        "AsyncClient",
        lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
            **kwargs,
        ),
    )
    with pytest.raises(assistant.ModelUnavailable):
        await assistant.generate(
            Settings(_env_file=None, assistant_base_url="http://model.invalid/v1"), "问题", {}, []
        )


async def test_group_catalog_uses_real_snapshots_without_model_or_event_date_filter(
    system, monkeypatch
):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add_all(
            [
                Profile(
                    tenant_id=1,
                    kind="group",
                    external_id="chat-a",
                    name="测试服务群",
                    synced_at=1,
                    data={"owner": "private-owner", "member_list": [{"phone": "private-phone"}]},
                ),
                Profile(
                    tenant_id=2, kind="group", external_id="chat-a", name="foreign-private-group"
                ),
                Profile(
                    tenant_id=1, kind="customer", external_id="customer", name="private-customer"
                ),
            ]
        )
        await db.commit()
    # Events mentioning another group do not establish that it exists in the saved group list.
    await insert_event(app, kind="group_left")
    app.state.settings.assistant_base_url = "http://model.invalid/v1"

    async def unexpected(*args, **kwargs):
        pytest.fail("Group catalog must not call the model or unrelated dashboard")

    monkeypatch.setattr(assistant_api, "generate", unexpected)
    monkeypatch.setattr(assistant_api, "dashboard_report", unexpected)
    await login(client, "user")
    response = await ask(
        client, message="当前主体中有哪些群", start_date="2000-01-01", end_date="2000-01-02"
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["result_type"] == "groups" and data["mode"] == "rules"
    assert data["groups"]["total"] == 1
    assert data["groups"]["items"] == [
        {
            "id": "chat-a",
            "tenant_id": 1,
            "tenant_name": "主体一",
            "name": "测试服务群",
            "synced_at": 1,
        }
    ]
    assert data["groups"]["date_filtered"] is False and "不受页面日期" in data["answer"]
    assert "private-" not in response.text and not data["cards"] and not data["sources"]
    assert data["usage_tokens"] == 0
    assert (await ask(client, message="当前主体有哪些群", tenant_id="2")).status_code == 403


async def test_group_catalog_pagination_filter_and_shared_endpoint(system):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add_all(
            [
                Profile(
                    tenant_id=1,
                    kind="group",
                    external_id=f"chat-{index}",
                    name=f"测试群{index:02d}",
                )
                for index in range(5)
            ]
        )
        db.add(Profile(tenant_id=1, kind="group", external_id="special", name="VIP_100%群"))
        await db.commit()
    await login(client, "user")
    first = (await ask(client, message="查看群列表", group_limit=2)).json()["data"]["groups"]
    second = (
        await ask(
            client,
            message="下一页",
            query_type="groups",
            group_limit=2,
            group_offset=first["next_offset"],
        )
    ).json()["data"]["groups"]
    assert first["total"] == 6 and len(first["items"]) == len(second["items"]) == 2
    assert {row["id"] for row in first["items"]}.isdisjoint(row["id"] for row in second["items"])
    options = (await client.get("/api/v1/groups?limit=2&offset=2")).json()["data"]
    assert [row["id"] for row in options] == [row["id"] for row in second["items"]]
    for kwargs in (
        {"group_keyword": "_100%"},
        {"message": "群名包含“_100%”的群"},
        {"message": "群名称包含_100%的群"},
    ):
        data = (await ask(client, **{"message": "查看群列表", **kwargs})).json()["data"]
        assert data["groups"]["total"] == 1 and data["groups"]["items"][0]["id"] == "special"
    beyond = (await ask(client, message="查看群列表", group_offset=100)).json()["data"]
    assert not beyond["groups"]["items"] and "超出范围" in beyond["answer"]


async def test_group_catalog_all_authorized_tenants_duplicates_and_disabled_scope(system):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add(
            Tenant(
                id=3,
                corp_id="disabled",
                name="停用主体",
                enabled=False,
                credential_ref="TEST",
                callback_key="disabled",
            )
        )
        await db.flush()
        db.add_all(
            [
                Profile(tenant_id=tid, kind="group", external_id="same-id", name="同名群")
                for tid in (1, 2, 3)
            ]
        )
        await db.commit()
    await login(client)
    groups = (await ask(client, message="当前主体中有哪些群")).json()["data"]["groups"]
    assert groups["total"] == 2 and {row["tenant_id"] for row in groups["items"]} == {1, 2}
    assert (await ask(client, message="查看群列表", tenant_id="3")).status_code == 403


async def test_empty_group_catalog_and_query_validation(system):
    _, client, _ = system
    await login(client, "user")
    data = (await ask(client, message="当前主体有几个群")).json()["data"]
    assert data["groups"]["total"] == 0 and "不代表企业微信中没有群" in data["answer"]
    for kwargs in (
        {"group_offset": -1},
        {"group_limit": 101},
        {"group_keyword": "x" * 101},
        {"query_type": "sql"},
    ):
        assert (await ask(client, message="查看群列表", **kwargs)).status_code == 422


async def test_group_catalog_rechecks_grants_before_delivery(system, monkeypatch):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add(
            Profile(tenant_id=1, kind="group", external_id="secret", name="must-not-be-delivered")
        )
        await db.commit()
    catalog = assistant_api.group_catalog

    async def revoke(db, *args):
        data = await catalog(db, *args)
        await db.execute(delete(Grant).where(Grant.user_id == 2))
        await db.commit()
        return data

    monkeypatch.setattr(assistant_api, "group_catalog", revoke)
    await login(client, "user")
    response = await ask(client, message="当前主体中有哪些群")
    assert response.status_code == 403 and "must-not-be-delivered" not in response.text


@pytest.mark.parametrize(
    "question, expected",
    [
        ("当前主体中有哪些群", True),
        ("有哪些客户群？", True),
        ("查看所有群聊", True),
        ("当前主体有几个群", True),
        ("当前有哪些群退群最多", False),
        ("群里有哪些成员", False),
        ("解释这条群变更", False),
        ("生成本周周报", False),
    ],
)
def test_group_list_intent_boundaries(question, expected):
    assert assistant.is_group_list_question(question) is expected
