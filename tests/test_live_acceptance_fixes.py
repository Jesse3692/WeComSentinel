"""Regressions for defects observed in real DingTalk acceptance (isolated data)."""

from sqlalchemy import delete

from app.models import BusinessEvent, Grant
import test_system
from test_system import insert_event, login

system = test_system.system


async def test_unknown_source_zero_null_and_other_are_disjoint(system):
    app, client, _ = system
    expected = {}
    for code in [None, "0", "1", "6", "99"]:
        event_id = await insert_event(app)
        async with app.state.sessions() as db:
            row = await db.get(BusinessEvent, event_id)
            row.add_way = code
            await db.commit()
        expected[code] = event_id
    await insert_event(app, tenant=2)
    await login(client, "user")
    for key, codes in [("unknown", [None, "0"]), ("other", ["99"]), ("0", ["0"]), ("6", ["6"])]:
        ids = []
        params = {"add_way": key, "limit": 1}
        while True:
            response = await client.get("/api/v1/events", params=params)
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["total_records"] == len(codes)
            ids.extend(r["id"] for r in data["items"])
            if not data["next_cursor"]:
                break
            params.update(
                cursor=data["next_cursor"], start_date=data["range"][0], end_date=data["range"][1]
            )
        assert set(ids) == {expected[c] for c in codes}
        assert len(ids) == len(set(ids))


async def test_existing_session_after_all_grants_revoked(system, monkeypatch):
    app, client, _ = system
    local = await insert_event(app)
    foreign = await insert_event(app, tenant=2)
    await login(client, "user")
    first = (await client.get("/api/v1/events?limit=1")).json()["data"]
    assert first["items"][0]["id"] == local
    async with app.state.sessions() as db:
        await db.execute(delete(Grant).where(Grant.user_id == 2))
        await db.commit()
    # The session remains identifiable for an explicit no-access screen and logout.
    profile = (await client.get("/api/v1/me")).json()["data"]
    assert profile["tenants"] == [] and profile["last_tenant_id"] is None
    for path in ["dashboard", "events", "employees", "tenants"]:
        for query in ["", "?tenant_id=all", "?tenant_id=1", "?tenant_id=2"]:
            response = await client.get(f"/api/v1/{path}{query}")
            assert response.status_code == 403, (path, query, response.text)
    for event_id in [local, foreign]:
        assert (await client.get(f"/api/v1/events/{event_id}")).status_code == 404
    assert (await client.get("/api/v1/media/1")).status_code == 404
    assert (await client.get("/api/v1/admin/users")).status_code == 403
    assert (
        await client.patch("/api/v1/me/preferences", json={"last_tenant_id": 1})
    ).status_code == 403
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401

    async def no_grants_identity(code):
        return {"corp_id": "DEMO", "user_id": "user", "name": "模拟用户", "avatar": ""}

    monkeypatch.setattr(app.state.platforms, "ding_identity", no_grants_identity)
    response = await client.post("/api/v1/auth/dingtalk", json={"code": "mock-only"})
    assert response.status_code == 403
    assert not response.cookies
    assert (await client.get("/api/v1/me")).status_code == 401
