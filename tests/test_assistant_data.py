import json
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import delete, select

import test_system
from app import assistant, assistant_api, assistant_data
from app.api import TZ
from app.assistant_data import ADMIN, CAPABILITIES, DataQuery
from app.models import Audit, BusinessEvent, Grant, Inbox, Job, Media, Profile
from test_system import insert_event, login

system = test_system.system


@pytest.fixture
async def populated(system):
    app, client, _ = system
    async with app.state.sessions() as db:
        db.add_all(
            [
                Profile(
                    tenant_id=1,
                    kind="employee",
                    external_id="employee1",
                    name="示例员工",
                    data={"mobile": "never-emit-phone"},
                ),
                Profile(
                    tenant_id=1,
                    kind="customer",
                    external_id="customer1",
                    name="示例客户",
                    data={
                        "type": 1,
                        "gender": 1,
                        "corp_name": "示例公司",
                        "unionid": "never-emit-unionid",
                        "follow_user": [
                            {
                                "userid": "employee1",
                                "createtime": 1000,
                                "add_way": 1,
                                "remark_mobiles": ["never-emit-phone"],
                            }
                        ],
                    },
                ),
                Profile(
                    tenant_id=1,
                    kind="group",
                    external_id="chat1",
                    name="示例客户群",
                    data={
                        "owner": "employee1",
                        "create_time": 1000,
                        "member_list": [
                            {"userid": "employee1", "type": 1, "join_time": 1000},
                            {"userid": "customer1", "type": 2, "join_time": 1001},
                        ],
                        "secret": "never-emit-group-secret",
                    },
                ),
                Profile(
                    tenant_id=2, kind="group", external_id="chat1", name="foreign-private-group"
                ),
                Profile(
                    tenant_id=2,
                    kind="customer",
                    external_id="customer1",
                    name="foreign-private-customer",
                ),
                Media(
                    tenant_id=1,
                    sha256="never-emit-media-digest",
                    relative_path="never-emit-disk-path",
                    mime_type="image/png",
                    size_bytes=12,
                ),
                Audit(actor_user_id=1, action="tenant.create", target="never-emit-audit-secret"),
            ]
        )
        await db.commit()
    for kind, count, direction in (
        ("contact_added", 3, None),
        ("contact_deleted", 2, "customer_deleted_employee"),
        ("contact_deleted", 4, "employee_deleted_customer"),
        ("group_left", 5, None),
        ("group_change_unknown", 6, None),
    ):
        event_id = await insert_event(app, kind=kind, quantity=count)
        async with app.state.sessions() as db:
            row = await db.get(BusinessEvent, event_id)
            row.customer_id = "customer1"
            row.deletion_direction = direction
            row.resolution = "inferred" if kind == "group_left" else "exact"
            row.snapshot = {
                "employee_name": "示例员工",
                "customer_name": "示例客户",
                "group_name": "示例客户群",
                "_private": "never-emit-event-private",
            }
            await db.commit()
    await insert_event(app, tenant=2, quantity=500)
    async with app.state.sessions() as db:
        inbox = Inbox(
            tenant_id=1,
            dedupe_key="never-emit-inbox-digest",
            payload_encrypted=b"never-emit-payload",
            status="failed",
        )
        db.add(inbox)
        await db.flush()
        db.add(
            Job(
                tenant_id=1,
                callback_id=inbox.id,
                status="failed",
                attempts=5,
                last_error="access_token=never-emit-upstream-token",
            )
        )
        await db.commit()
    yield app, client


async def query(client, capability, **kwargs):
    return await client.post(
        "/api/v1/assistant/chat",
        json={"message": "查询", "query": {"capability": capability, **kwargs}},
    )


@pytest.mark.parametrize("capability", list(CAPABILITIES))
async def test_every_registered_capability_is_executable_without_model(
    populated, monkeypatch, capability
):
    app, client = populated

    async def unexpected(*args, **kwargs):
        pytest.fail("Explicit data tools must not send database data to a model")

    monkeypatch.setattr(assistant_api, "generate", unexpected)
    monkeypatch.setattr(assistant_api, "plan_query", unexpected)
    await login(client)
    response = await query(client, capability)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["result_type"] == "data"
    assert "dataset" in data and data["dataset"]["source"]
    assert "never-emit" not in response.text


@pytest.mark.parametrize("capability", sorted(ADMIN))
async def test_admin_tools_rejected_and_not_advertised_to_ordinary_user(populated, capability):
    _, client = populated
    await login(client, "user")
    config = (await client.get("/api/v1/assistant/config")).json()["data"]
    assert not ADMIN.intersection(row["id"] for row in config["capabilities"])
    assert (await query(client, capability)).status_code == 403


async def test_profiles_relationships_identity_namespace_and_safe_fields(populated):
    _, client = populated
    await login(client, "user")
    customers = (await query(client, "customers")).json()["data"]["dataset"]
    assert customers["total"] == 1 and customers["items"][0]["name"] == "示例客户"
    assert customers["items"][0]["company"] == "示例公司"
    followers = (await query(client, "customer_followers", entity_id="customer1")).json()["data"][
        "dataset"
    ]
    assert followers["items"][0]["name"] == "示例员工"
    members = (await query(client, "group_members", entity_id="chat1")).json()["data"]["dataset"]
    assert members["total"] == 2
    assert {row["type"] for row in members["items"]} == {"内部员工", "外部联系人"}
    detail = (await query(client, "group_detail", entity_id="chat1")).json()["data"]["dataset"]
    assert detail["items"][0]["owner"] == "示例员工" and detail["items"][0]["members"] == 2
    assert (
        await query(client, "group_members", entity_id="chat1", entity_tenant_id=2)
    ).status_code == 403
    assert (await query(client, "customers", entity_tenant_id=2)).status_code == 403
    assert "foreign-private" not in json.dumps([customers, followers, members, detail])


async def test_ambiguous_entities_require_selection_and_missing_snapshot_is_not_empty_group(
    populated,
):
    app, client = populated
    await login(client)
    ambiguous = (await query(client, "group_members", entity_id="chat1")).json()["data"]["dataset"]
    assert ambiguous["total"] == 2 and "选择" in ambiguous["title"]
    scoped = (await query(client, "group_members", entity_id="chat1", entity_tenant_id=2)).json()[
        "data"
    ]["dataset"]
    assert not scoped["items"] and "不能据此判断名单为空" in scoped["note"]
    async with app.state.sessions() as db:
        db.add(Profile(tenant_id=1, kind="employee", external_id="customer1", name="同ID内部员工"))
        await db.commit()
    members = (await query(client, "group_members", entity_id="chat1", entity_tenant_id=1)).json()[
        "data"
    ]["dataset"]
    assert (
        next(row["name"] for row in members["items"] if row["type"] == "外部联系人") == "示例客户"
    )


async def test_employee_event_only_ids_and_catalog_pagination(populated):
    app, client = populated
    await insert_event(app, kind="contact_added")
    async with app.state.sessions() as db:
        last = await db.scalar(select(BusinessEvent).order_by(BusinessEvent.id.desc()))
        last.employee_id = "employee-without-profile"
        await db.commit()
    await login(client, "user")
    first = (await query(client, "employees", limit=1)).json()["data"]["dataset"]
    assert first["total"] == 2 and first["next_query"]
    second = await client.post(
        "/api/v1/assistant/chat", json={"message": "翻页", "query": first["next_query"]}
    )
    data = second.json()["data"]["dataset"]
    assert data["items"][0]["id"] != first["items"][0]["id"]
    assert data["previous_query"]


async def test_event_filters_totals_freeze_and_cursor_binding(populated):
    app, client = populated
    await login(client, "user")
    first = (await query(client, "events", event_type="contact_deleted", limit=1)).json()["data"]
    table = first["dataset"]
    assert table["total"] == 2 and table["total_quantity"] == 6
    await insert_event(app, kind="contact_deleted", quantity=99)
    page = (
        await client.post(
            "/api/v1/assistant/chat", json={"message": "下一页", "query": table["next_query"]}
        )
    ).json()["data"]["dataset"]
    assert page["total"] == 2 and page["total_quantity"] == 6
    assert page["items"][0]["id"] != table["items"][0]["id"]
    tampered = {**table["next_query"], "event_type": "group_left"}
    assert (
        await client.post("/api/v1/assistant/chat", json={"message": "查询", "query": tampered})
    ).status_code == 422
    filtered = (
        await query(client, "events", deletion_direction="customer_deleted_employee")
    ).json()["data"]["dataset"]
    assert filtered["total_quantity"] == 2
    assert filtered["items"][0]["direction"] == "客户删除员工"
    inferred = (
        await query(client, "events", resolution="inferred", group_keyword="示例客户群")
    ).json()["data"]["dataset"]
    assert inferred["total_quantity"] == 5 and inferred["items"][0]["evidence"] == "快照推断"
    assert (await query(client, "events", customer_keyword="不存在")).json()["data"]["dataset"][
        "total"
    ] == 0


async def test_stats_trend_rankings_match_scoped_events_and_directions(populated):
    _, client = populated
    await login(client, "user")
    statistics = (await query(client, "statistics")).json()["data"]["dataset"]
    assert next(row["current"] for row in statistics["items"] if row["type"] == "删除联系人") == 6
    assert (
        next(row["current"] for row in statistics["items"] if row["type"] == "其他 / 待核实") == 6
    )
    filtered = (
        await query(client, "statistics", deletion_direction="employee_deleted_customer")
    ).json()["data"]["dataset"]
    assert sum(row["current"] for row in filtered["items"]) == 4
    trend = (await query(client, "trend", limit=100)).json()["data"]["dataset"]
    assert sum(row["contact_added"] for row in trend["items"]) == 3
    rankings = (await query(client, "rankings", event_type="group_left")).json()["data"]["dataset"]
    assert rankings["items"][0]["quantity"] == 5
    assert "foreign-private" not in json.dumps([statistics, filtered, trend, rankings])


async def test_admin_metadata_never_exposes_payloads_paths_tokens_or_errors(populated):
    app, client = populated
    await login(client)
    for capability in ("jobs", "callbacks", "users", "audits", "tenant_settings", "media", "me"):
        response = await query(client, capability)
        assert response.status_code == 200
        assert "never-emit" not in response.text
        assert "payload_encrypted" not in response.text and "token_hash" not in response.text
        assert "csrf" not in json.dumps(response.json()["data"]["dataset"]["items"]).lower()
    jobs = (await query(client, "jobs", status="failed")).json()["data"]["dataset"]
    assert jobs["total"] == 1 and jobs["items"][0]["attempts"] == 5
    async with app.state.sessions() as db:
        assert (await db.scalar(select(Job))).status == "failed"


async def test_query_schema_disallows_sql_urls_and_unsupported_filters(populated):
    _, client = populated
    await login(client)
    for bad in (
        {"capability": "sql"},
        {"capability": "customers", "sql": "select *"},
        {"capability": "customers", "url": "http://example.com"},
        {"capability": "customers", "limit": 1000},
        {"capability": "customers", "start_date": "2026-10-01"},
        {"capability": "employees", "status": "failed"},
    ):
        assert (
            await client.post("/api/v1/assistant/chat", json={"message": "查询", "query": bad})
        ).status_code == 422


async def test_data_delivery_rechecks_grants_and_context_followup(populated, monkeypatch):
    app, client = populated
    await login(client, "user")
    first = (await query(client, "group_detail", entity_id="chat1")).json()["data"]
    follow = await client.post(
        "/api/v1/assistant/chat",
        json={"message": "这个群有哪些成员", "context_query": first["query"]},
    )
    assert follow.status_code == 200 and follow.json()["data"]["dataset"]["total"] == 2
    execute = assistant_api.execute_query

    async def revoke(db, *args):
        data = await execute(db, *args)
        await db.execute(delete(Grant).where(Grant.user_id == 2))
        await db.commit()
        return data

    monkeypatch.setattr(assistant_api, "execute_query", revoke)
    response = await query(client, "customers")
    assert response.status_code == 403 and "示例客户" not in response.text


@pytest.mark.parametrize(
    "message, capability",
    [
        ("当前主体有哪些员工", "employees"),
        ("当前有哪些客户", "customers"),
        ("查看群成员", "group_members"),
        ("查看客户跟进员工", "customer_followers"),
        ("今天有哪些删除记录", "events"),
        ("查看最近30天趋势", "trend"),
        ("哪些群退群最多", "rankings"),
        ("有哪些失败任务", "jobs"),
        ("查看回调接收记录", "callbacks"),
        ("查看用户授权", "users"),
        ("查看审计日志", "audits"),
        ("我的账号信息", "me"),
        ("这个群有哪些员工", "group_members"),
        ("这个群有哪些客户", "group_members"),
    ],
)
def test_natural_query_registry(message, capability):
    assert assistant_data.infer_query(message).capability == capability


async def test_json_router_uses_only_schema_and_questions_not_database(monkeypatch):
    original = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert [item["role"] for item in body["messages"]] == ["system", "user"]
        assert "数据库实际客户" not in request.content.decode()
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"capability":"customers","keyword":"示例"}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        assistant.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
    )
    from app.config import Settings

    settings = Settings(_env_file=None, assistant_base_url="http://model.invalid/v1")
    routed = await assistant_data.plan_query(settings, "名字含示例的客户", [], "user")
    assert routed == DataQuery(capability="customers", keyword="示例")


async def test_statistics_previous_period_and_admin_dates_are_explicit(populated):
    app, client = populated
    today = datetime.now(TZ).date()
    stamp = int((datetime.now(TZ) - timedelta(days=1)).timestamp() * 1000)
    await insert_event(app, quantity=7, stamp=stamp)
    await login(client)
    data = (
        await query(
            client, "statistics", entity_tenant_id=1, start_date=str(today), end_date=str(today)
        )
    ).json()["data"]["dataset"]
    added = next(row for row in data["items"] if row["type"] == "新增联系人")
    assert added["current"] == 3 and added["previous"] == 7 and added["delta"] == -4


@pytest.mark.parametrize(
    "question", ["查询 Secret “never-forward-secret”", "请生成包含密码“never-forward-secret”的报告"]
)
async def test_credential_questions_are_not_forwarded_to_models(populated, monkeypatch, question):
    app, client = populated
    app.state.settings.assistant_base_url = "http://model.invalid/v1"

    async def unexpected(*args, **kwargs):
        pytest.fail("Credentials must not be sent to a model")

    monkeypatch.setattr(assistant_api, "generate", unexpected)
    monkeypatch.setattr(assistant_api, "plan_query", unexpected)
    await login(client)
    response = await client.post("/api/v1/assistant/chat", json={"message": question})
    assert response.status_code == 200
    assert response.json()["data"]["query"]["capability"] == "capabilities"
    assert "never-forward-secret" not in response.text


async def test_group_member_type_filter_does_not_reinterpret_group_list(populated):
    _, client = populated
    await login(client, "user")
    assert not assistant.is_group_list_question("这个群有哪些客户")
    assert assistant_data.infer_query("这个群有哪些员工").member_type == 1
    for value, name in ((1, "示例员工"), (2, "示例客户")):
        data = (await query(client, "group_members", entity_id="chat1", member_type=value)).json()[
            "data"
        ]["dataset"]
        assert data["total"] == 1 and data["items"][0]["name"] == name
