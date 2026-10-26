"""Employee record search and stable previous/next cursors, isolated SQLite."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.models import BusinessEvent, Profile
import test_system
from test_system import insert_event, login

system = test_system.system


@pytest.mark.parametrize("kind", ["contact_added", "contact_deleted"])
async def test_employee_keyword_matches_visible_history_current_name_and_id(system, kind):
    app, client, _ = system
    expected = []
    for tenant, employee, name in [
        (1, "staff-A", "历史姓名"),
        (1, "staff-B", "其他员工"),
        (2, "staff-A", "隐私员工"),
    ]:
        event_id = await insert_event(app, tenant=tenant, kind=kind)
        async with app.state.sessions() as db:
            row = await db.get(BusinessEvent, event_id)
            row.employee_id = employee
            row.snapshot = {**row.snapshot, "employee_name": name}
            await db.commit()
        expected.append(event_id)
    async with app.state.sessions() as db:
        db.add_all(
            [
                Profile(tenant_id=t, kind="employee", external_id="staff-A", name=n)
                for t, n in [(1, "当前姓名"), (2, "外部秘密")]
            ]
        )
        await db.commit()
    await login(client, "user")
    for keyword in ["历史姓名", "当前姓名", "  STAFF-a  "]:
        response = await client.get(
            "/api/v1/events", params={"event_type": kind, "employee_keyword": keyword}
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert [r["id"] for r in data["items"]] == [expected[0]]
        assert data["total_records"] == data["total_quantity"] == 1
    for keyword in ["不存在", "外部秘密", "隐私员工", "%", "_"]:
        data = (
            await client.get(
                "/api/v1/events", params={"event_type": kind, "employee_keyword": keyword}
            )
        ).json()["data"]
        assert data["total_records"] == 0 and data["items"] == []
    all_data = (
        await client.get("/api/v1/events", params={"event_type": kind, "employee_keyword": ""})
    ).json()["data"]
    assert {r["id"] for r in all_data["items"]} == set(expected[:2])
    assert (
        await client.get("/api/v1/events", params={"employee_keyword": "x" * 101})
    ).status_code == 422


@pytest.mark.parametrize("kind", ["contact_added", "contact_deleted"])
async def test_previous_next_keep_snapshot_filter_and_cross_year_range(system, kind):
    app, client, _ = system
    stamp = int(datetime(2025, 12, 31, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
    for n in range(25):
        await insert_event(app, kind=kind, stamp=stamp - n * 1000)
    await insert_event(app, kind=kind, tenant=2, stamp=stamp)
    await login(client, "user")
    params = {
        "event_type": kind,
        "employee_keyword": "员工一",
        "limit": 10,
        "start_date": "2025-12-31",
        "end_date": "2026-01-01",
    }
    first = (await client.get("/api/v1/events", params=params)).json()["data"]
    assert first["total_records"] == 25
    assert first["page_cursor"] and first["next_cursor"]
    second = (
        await client.get("/api/v1/events", params={**params, "cursor": first["next_cursor"]})
    ).json()["data"]
    third = (
        await client.get("/api/v1/events", params={**params, "cursor": second["next_cursor"]})
    ).json()["data"]
    assert [len(p["items"]) for p in [first, second, third]] == [10, 10, 5]
    assert third["next_cursor"] is None
    assert len({r["id"] for p in [first, second, third] for r in p["items"]}) == 25
    await insert_event(app, kind=kind, stamp=stamp + 1000)
    for page in [second, first]:
        previous = (
            await client.get("/api/v1/events", params={**params, "cursor": page["page_cursor"]})
        ).json()["data"]
        assert [r["id"] for r in previous["items"]] == [r["id"] for r in page["items"]]
        assert previous["total_records"] == 25
        assert previous["range"] == ["2025-12-31", "2026-01-01"]
    assert (await client.get("/api/v1/events", params=params)).json()["data"]["total_records"] == 26
    for changes in [
        {"employee_keyword": "另一个"},
        {"event_type": "group_left"},
        {"start_date": "2025-12-30"},
    ]:
        assert (
            await client.get(
                "/api/v1/events", params={**params, **changes, "cursor": first["page_cursor"]}
            )
        ).status_code == 422
