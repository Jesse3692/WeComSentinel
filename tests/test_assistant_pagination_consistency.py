"""Assistant event pages use one database snapshot for counts and rows."""
from sqlalchemy.ext.asyncio import AsyncSession

import test_system
from app.models import BusinessEvent
from test_system import insert_event, login

system = test_system.system


async def event_page(client, query):
    response = await client.post("/api/v1/assistant/chat", json={
        "message": "查询事件", "query": {"capability": "events", **query}})
    assert response.status_code == 200, response.text
    return response.json()["data"]["dataset"]


async def test_assistant_counts_and_rows_share_snapshot_during_writer(system, monkeypatch):
    app, client, _ = system
    event_id = await insert_event(app, quantity=2)
    await insert_event(app, tenant=2, quantity=100)
    await login(client, "user")
    original_execute = AsyncSession.execute
    changed = False

    async def execute(db, statement, *args, **kwargs):
        nonlocal changed
        result = await original_execute(db, statement, *args, **kwargs)
        if not changed and "sum(business_events.quantity)" in str(statement).lower():
            changed = True
            async with app.state.sessions() as writer:
                row = await writer.get(BusinessEvent, event_id)
                row.quantity = 7
                await writer.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)
    page = await event_page(client, {})
    assert changed and page["total"] == 1
    assert page["total_quantity"] == page["items"][0]["quantity"] == 2
    assert page["items"][0]["tenant"] == "主体一"
    fresh = await event_page(client, {})
    assert fresh["total_quantity"] == fresh["items"][0]["quantity"] == 7


async def test_assistant_next_page_recounts_reclassified_existing_id(system):
    app, client, _ = system
    pending_id = await insert_event(app, kind="group_change_unknown", quantity=2)
    for _ in range(3):
        await insert_event(app, kind="group_left")
    await login(client, "user")
    first = await event_page(client, {"event_type": "group_left", "limit": 2})
    assert first["total"] == first["total_quantity"] == 3
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, pending_id)
        row.event_type = "group_left"
        await db.commit()
    await insert_event(app, kind="group_left", quantity=50)
    await insert_event(app, tenant=2, kind="group_left", quantity=100)
    page = await event_page(client, first["next_query"])
    assert page["total"] == 4 and page["total_quantity"] == 5
    assert len(page["items"]) == 2 and page["next_query"] is None
    assert {row["tenant"] for row in page["items"]} == {"主体一"}
    assert pending_id in {row["id"] for row in page["items"]}


async def test_assistant_empty_later_page_keeps_current_totals(system):
    app, client, _ = system
    ids = [await insert_event(app, kind="group_left") for _ in range(3)]
    await login(client, "user")
    first = await event_page(client, {"event_type": "group_left", "limit": 2})
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, ids[0])
        row.event_type = "group_change_unknown"
        await db.commit()
    page = await event_page(client, first["next_query"])
    assert page["items"] == [] and page["next_query"] is None
    assert page["total"] == page["total_quantity"] == 2
    assert page["previous_query"] is not None
