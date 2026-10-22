"""Customer group classification: pure policy plus worker/list/dashboard consistency."""
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.event_classification import group_records
from app.models import BusinessEvent, Inbox, Job, Profile, now_ms
from app.worker import enqueue
import test_system
from test_system import insert_event, login

system = test_system.system


async def test_reclassification_removes_stale_remainder_without_double_counting(system):
    app, _, _ = system
    async with app.state.sessions() as db:
        inbox = Inbox(tenant_id=1, dedupe_key="reclassification", payload_encrypted=b"mock")
        db.add(inbox)
        await db.commit()
        callback_id = inbox.id
    members = [{"member_id": "external", "member_type": 2},
               {"member_id": "later-confirmed", "member_type": None}]
    await app.state.worker.write_events(1, callback_id,
        group_records(evidence(members), 2, now_ms(), "chat"), replace_group=True)
    async with app.state.sessions() as db:
        original_id = await db.scalar(select(BusinessEvent.id).where(BusinessEvent.item_key == "group"))
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 2
    members[1]["member_type"] = 2
    await app.state.worker.write_events(1, callback_id,
        group_records(evidence(members), 2, now_ms(), "chat"), replace_group=True)
    async with app.state.sessions() as db:
        rows = (await db.scalars(select(BusinessEvent))).all()
        assert len(rows) == 1
        assert rows[0].id == original_id and rows[0].quantity == 2
        assert rows[0].event_type == "group_left"


def evidence(members, reason="group_left", **values):
    return {"group_name": "客户服务群", "member_evidence": "snapshot_difference",
            "reported_event_type": reason, "member_candidates": members, **values}


def classify(members, reason="group_left", quantity=None, **values):
    snapshot = evidence(members, reason, **values)
    original = deepcopy(snapshot)
    records = group_records(snapshot, len(members) if quantity is None else quantity, 123000, "chat")
    assert snapshot == original, "Classification must not mutate reusable callback evidence"
    assert all(row["quantity"] > 0 for row in records)
    assert sum(row["quantity"] for row in records) == (len(members) if quantity is None else quantity)
    assert len({row["item_key"] for row in records}) == len(records)
    return records


@pytest.mark.parametrize("reason", ["group_left", "group_removed"])
def test_all_external_members_are_in_their_business_category(reason):
    members = [{"member_id": "a", "member_type": 2, "name": "甲"},
               {"member_id": "b", "member_type": 2, "name": "乙"}]
    records = classify(members, reason)
    assert len(records) == 1
    row = records[0]
    assert row["item_key"] == "group" and row["event_type"] == reason
    assert row["quantity"] == 2 and row["resolution"] == "inferred"
    assert row["snapshot"]["member_candidates"] == members
    assert row["snapshot"]["reported_quantity"] == 2
    assert row["snapshot"]["member_evidence"] == "snapshot_difference"


def test_mixed_callback_splits_customer_quantity_from_other_members():
    members = [{"member_id": "external", "member_type": 2, "name": "客户"},
               {"member_id": "staff", "member_type": 1, "name": "员工"},
               {"member_id": "unknown", "member_type": None, "name": "类型未知"}]
    records = classify(members, "group_removed")
    external, other = records
    assert external["item_key"] == "group"
    assert external["event_type"] == "group_removed" and external["quantity"] == 1
    assert external["snapshot"]["member_candidates"] == members[:1]
    assert external["snapshot"]["_all_member_candidates"] == members
    assert other["item_key"] == "group_other"
    assert other["event_type"] == "group_change_unknown" and other["quantity"] == 2
    assert other["snapshot"]["member_candidates"] == members[1:]
    assert all(row["snapshot"]["reported_quantity"] == 3 for row in records)


@pytest.mark.parametrize("member_type,reason", [(1, "internal_members"), (None, "member_type_unknown")])
def test_non_customer_members_do_not_enter_customer_loss_categories(member_type, reason):
    rows = classify([{"member_id": "member", "member_type": member_type, "name": "成员"}])
    assert len(rows) == 1 and rows[0]["item_key"] == "group"
    assert rows[0]["event_type"] == "group_change_unknown"
    assert rows[0]["snapshot"]["classification_reason"] == reason


@pytest.mark.parametrize("member", [
    {"member_id": "external", "member_type": 2, "name": None},
    {"member_type": 2, "name": "旧版本已保存的客户姓名"},
])
def test_identity_can_be_retained_id_or_legacy_meaningful_name(member):
    row = classify([member])[0]
    assert row["event_type"] == "group_left" and row["resolution"] == "inferred"


@pytest.mark.parametrize("member", [
    {"member_type": 2, "name": None}, {"member_type": 2, "name": "未知"},
    {"member_type": 2, "name": "未知客户"}, {"member_type": 2, "name": "成员待核实"},
])
def test_anonymous_or_placeholder_member_is_not_promoted(member):
    row = classify([member])[0]
    assert row["event_type"] == "group_change_unknown"
    assert row["snapshot"]["classification_reason"] == "identity_unresolved"


def test_count_mismatch_does_not_assign_partial_difference_to_callback():
    row = classify([{"member_id": "a", "member_type": 2}], quantity=2)[0]
    assert row["event_type"] == "group_change_unknown"
    assert row["quantity"] == 2
    assert row["snapshot"]["classification_reason"] == "identity_unresolved"


def test_duplicate_ids_are_not_counted_as_two_customers():
    row = classify([{"member_id": "same", "member_type": 2},
                    {"member_id": "same", "member_type": 2}])[0]
    assert row["event_type"] == "group_change_unknown"
    assert row["snapshot"]["classification_reason"] == "identity_unresolved"


@pytest.mark.parametrize("reason", [None, "group_change_unknown", "unsupported"])
def test_known_external_identity_does_not_invent_departure_reason(reason):
    row = classify([{"member_id": "a", "member_type": 2}], reason)[0]
    assert row["event_type"] == "group_change_unknown"
    assert row["snapshot"]["classification_reason"] == "quit_reason_unknown"


def test_non_matching_evidence_does_not_promote_even_with_customer_name():
    row = classify([{"member_id": "a", "member_type": 2}],
                   member_evidence="snapshot_difference_inconclusive")[0]
    assert row["event_type"] == "group_change_unknown"
    assert row["snapshot"]["classification_reason"] == "identity_unresolved"


@pytest.mark.parametrize("scene,category", [("0", "group_left"), ("1", "group_removed")])
async def test_worker_customer_list_and_dashboard_agree_after_mixed_callback_and_replay(system, scene, category):
    app, client, _ = system
    old = [{"userid": "customer-a", "type": 2, "name": "客户甲"},
           {"userid": "customer-b", "type": 2, "name": "客户乙"},
           {"userid": "staff-a", "type": 1, "name": "员工甲"},
           {"userid": "untyped", "type": None, "name": "成员类型未知"},
           {"userid": "owner", "type": 1, "name": "群主"}]
    async with app.state.sessions() as db:
        db.add(Profile(tenant_id=1, kind="group", external_id="same-chat", synced_at=1,
                       name="客户群", data={"name": "客户群", "member_list": old}))
        await db.commit()
    async def group(*args):
        return {"name": "客户群", "member_list": old[-1:]}
    app.state.platforms.group = group
    payload = {"Event": "change_external_chat", "ChangeType": "update", "UpdateDetail": "del_member",
               "ChatId": "same-chat", "CreateTime": str(now_ms() // 1000), "QuitScene": scene, "MemChangeCnt": "4"}
    async with app.state.sessions() as db:
        callback_id = await enqueue(db, app.state.settings, 1, payload)
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        rows = (await db.scalars(select(BusinessEvent).where(BusinessEvent.callback_id == callback_id)
                                 .order_by(BusinessEvent.item_key))).all()
        assert [(row.item_key, row.quantity) for row in rows] == [("group", 2), ("group_other", 2)]
        original_ids = [row.id for row in rows]
        job = await db.scalar(select(Job).where(Job.callback_id == callback_id))
        job.status, job.next_run_at = "retry", 0
        await db.commit()
    await app.state.worker.tick()
    async with app.state.sessions() as db:
        rows = (await db.scalars(select(BusinessEvent).where(BusinessEvent.callback_id == callback_id)
                                 .order_by(BusinessEvent.item_key))).all()
        assert [row.id for row in rows] == original_ids
        assert sum(row.quantity for row in rows) == 4
        assert await db.scalar(select(func.count()).select_from(BusinessEvent)) == 2

    # Foreign tenant data must not leak into either list totals or dashboard aggregates.
    foreign = await insert_event(app, tenant=2, kind=category, quantity=50)
    await login(client, "user")
    listing = (await client.get("/api/v1/events", params={"event_type": category})).json()["data"]
    other = (await client.get("/api/v1/events", params={"event_type": "group_change_unknown"})).json()["data"]
    assert listing["total_records"] == 1 and listing["total_quantity"] == 2
    assert other["total_records"] == 1 and other["total_quantity"] == 2
    item = listing["items"][0]
    assert item["event_type"] == category and item["resolution"] == "inferred"
    assert {m["member_id"] for m in item["snapshot"]["member_candidates"]} == {"customer-a", "customer-b"}
    assert "_all_member_candidates" not in item["snapshot"] and "_before_members" not in item["snapshot"]
    detail = (await client.get(f"/api/v1/events/{item['id']}")).json()["data"]
    assert detail["quantity"] == 2 and detail["event_type"] == category
    dashboard = (await client.get("/api/v1/dashboard")).json()["data"]
    card = next(c for c in dashboard["cards"] if c["type"] == category)
    assert card["value"] == listing["total_quantity"] == 2
    assert sum(day[category] for day in dashboard["trend"]) == 2
    assert dashboard["unresolved_count"] == 2
    if category == "group_left":
        assert dashboard["tops"]["group_left"][0]["count"] == 2
    assert (await client.get(f"/api/v1/events/{foreign}")).status_code == 404
    assert (await client.get("/api/v1/events", params={"event_type": category, "tenant_id": "2"})).status_code == 403
