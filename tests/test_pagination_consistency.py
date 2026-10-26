"""Mutable callback events must not leave paged counts on an older snapshot."""

from sqlalchemy.ext.asyncio import AsyncSession

import test_system
from app.event_classification import group_records
from app.models import BusinessEvent, Inbox, now_ms
from app.security import decode_cursor, encode_cursor
from test_system import insert_event, login

system = test_system.system


async def test_cursor_recounts_existing_events_after_retry_reclassification(system):
    app, client, _ = system
    await insert_event(app, kind="group_left")
    pending_id = await insert_event(app, kind="group_change_unknown", item="group", quantity=3)
    await login(client, "user")
    params = {"event_type": "group_left"}
    first = (await client.get("/api/v1/events", params=params)).json()["data"]
    assert first["total_records"] == first["total_quantity"] == 1
    async with app.state.sessions() as db:
        pending = await db.get(BusinessEvent, pending_id)
    snapshot = {
        "group_name": "客户群",
        "member_evidence": "snapshot_difference",
        "reported_event_type": "group_left",
        "member_candidates": [
            {"member_id": "a", "name": "客户甲", "member_type": 2},
            {"member_id": "b", "name": "客户乙", "member_type": 2},
            {"member_id": "staff", "name": "员工", "member_type": 1},
        ],
    }
    await app.state.worker.write_events(
        1,
        pending.callback_id,
        group_records(snapshot, 3, pending.occurred_at, "chat1"),
        replace_group=True,
    )
    await insert_event(app, tenant=2, kind="group_left", quantity=100)
    await insert_event(app, kind="group_left", quantity=50)
    refreshed = (
        await client.get("/api/v1/events", params={**params, "cursor": first["page_cursor"]})
    ).json()["data"]
    assert refreshed["total_records"] == len(refreshed["items"]) == 2
    assert refreshed["total_quantity"] == sum(row["quantity"] for row in refreshed["items"]) == 3
    assert {row["tenant_id"] for row in refreshed["items"]} == {1}


async def test_cursor_recounts_removed_remainder_without_adding_new_events(system):
    app, client, _ = system
    async with app.state.sessions() as db:
        inbox = Inbox(tenant_id=1, dedupe_key="pagination-remainder", payload_encrypted=b"fixture")
        db.add(inbox)
        await db.flush()
        callback_id = inbox.id
        db.add_all(
            [
                BusinessEvent(
                    tenant_id=1,
                    callback_id=callback_id,
                    item_key=key,
                    event_type=kind,
                    group_id="chat1",
                    occurred_at=now_ms(),
                    quantity=count,
                    snapshot={},
                )
                for key, kind, count in [
                    ("group", "group_left", 2),
                    ("group_other", "group_change_unknown", 1),
                ]
            ]
        )
        await db.commit()
    await login(client)
    first = (await client.get("/api/v1/events")).json()["data"]
    assert first["total_records"] == 2 and first["total_quantity"] == 3
    await app.state.worker.write_events(
        1,
        callback_id,
        [
            {
                "item_key": "group",
                "event_type": "group_left",
                "group_id": "chat1",
                "occurred_at": now_ms(),
                "quantity": 3,
                "snapshot": {},
            }
        ],
        replace_group=True,
    )
    data = (await client.get("/api/v1/events", params={"cursor": first["page_cursor"]})).json()[
        "data"
    ]
    assert data["total_records"] == len(data["items"]) == 1
    assert data["total_quantity"] == data["items"][0]["quantity"] == 3


async def test_empty_later_page_keeps_live_totals(system):
    app, client, _ = system
    ids = [await insert_event(app, kind="group_left") for _ in range(3)]
    await login(client)
    params = {"event_type": "group_left", "limit": 2}
    first = (await client.get("/api/v1/events", params=params)).json()["data"]
    assert first["next_cursor"]
    async with app.state.sessions() as db:
        row = await db.get(BusinessEvent, ids[0])
        row.event_type = "group_change_unknown"
        await db.commit()
    page = (
        await client.get("/api/v1/events", params={**params, "cursor": first["next_cursor"]})
    ).json()["data"]
    assert page["items"] == [] and page["next_cursor"] is None
    assert page["total_records"] == page["total_quantity"] == 2


async def test_old_signed_cursor_totals_are_not_authoritative(system):
    app, client, _ = system
    await insert_event(app, quantity=4)
    await login(client)
    first = (await client.get("/api/v1/events")).json()["data"]
    decoded = decode_cursor(first["page_cursor"], app.state.settings.secret_key)
    decoded.update(total=999, quantity=999)
    old_cursor = encode_cursor(decoded, app.state.settings.secret_key)
    page = (await client.get("/api/v1/events", params={"cursor": old_cursor})).json()["data"]
    assert page["total_records"] == 1 and page["total_quantity"] == 4


async def test_count_and_page_share_snapshot_during_concurrent_write(system, monkeypatch):
    app, client, _ = system
    event_id = await insert_event(app, quantity=2)
    await login(client)
    original_execute = AsyncSession.execute
    changed = False

    async def execute(db, statement, *args, **kwargs):
        nonlocal changed
        result = await original_execute(db, statement, *args, **kwargs)
        sql = str(statement).lower()
        if not changed and "sum(business_events.quantity)" in sql:
            changed = True
            # Commit a writer after the aggregate has executed, before the
            # endpoint continues. A separate page SELECT would see new data.
            async with app.state.sessions() as writer:
                row = await writer.get(BusinessEvent, event_id)
                row.quantity = 7
                await writer.commit()
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute)
    page = (await client.get("/api/v1/events")).json()["data"]
    assert changed
    assert page["total_records"] == 1
    assert page["total_quantity"] == page["items"][0]["quantity"] == 2
    fresh = (await client.get("/api/v1/events")).json()["data"]
    assert fresh["total_quantity"] == fresh["items"][0]["quantity"] == 7
