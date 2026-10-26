"""Preserve callback member IDs and reject conflicting snapshot attribution."""

import json
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from app.security import parse_xml
from app.worker import enqueue
from app.models import Inbox, Tenant
import test_system

system = test_system.system


def test_member_cdata_list_and_nested_fields_preserved():
    payload = parse_xml(
        "<xml><Event>change_external_chat</Event><MemChangeList><Item><![CDATA[customer-id]]></Item><Item><![CDATA[other-id]]></Item></MemChangeList><List><Item><UserID>staff</UserID></Item></List></xml>"
    )
    assert payload["MemChangeList"] == ["customer-id", "other-id"]
    assert payload["List"] == [{"UserID": "staff"}]


async def test_member_ids_survive_encrypted_inbox(system):
    app, _, _ = system
    payload = parse_xml(
        "<xml><MemChangeList><Item><![CDATA[customer-id]]></Item></MemChangeList></xml>"
    )
    async with app.state.sessions() as db:
        ident = await enqueue(db, app.state.settings, 1, payload)
        row = await db.get(Inbox, ident)
        stored = json.loads(
            Fernet(app.state.settings.payload_key.encode()).decrypt(row.payload_encrypted)
        )
        assert stored["MemChangeList"] == ["customer-id"]


@pytest.mark.parametrize(
    "ids,matched",
    [(["staff"], True), (["customer"], False), (["staff", "staff"], False), ([""], False)],
)
async def test_explicit_ids_must_agree_with_snapshot_not_names(system, monkeypatch, ids, matched):
    app, _, _ = system
    async with app.state.sessions() as db:
        tenant = await db.get(Tenant, 1)

    async def profile(*args):
        return {"name": "同名", "media_id": 1}, True

    monkeypatch.setattr(app.state.worker, "get_profile", profile)
    previous = SimpleNamespace(
        synced_at=1, data={"member_list": [{"userid": "staff", "name": "同名", "type": 1}]}
    )
    snapshot = await app.state.worker.group_snapshot(
        tenant,
        SimpleNamespace(id=-1),
        {"CreateTime": "1000", "MemChangeCnt": "1", "QuitScene": "0", "MemChangeList": ids},
        previous,
        {"member_list": []},
    )
    assert bool(snapshot["member_candidates"]) == matched
    if not matched:
        assert snapshot["member_evidence"] == "callback_snapshot_conflict"
    else:
        assert snapshot["member_candidates"][0]["member_type"] == 1
