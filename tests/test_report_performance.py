"""Report-path regressions: dashboard TTL cache and anchored event pages."""

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.models import Media, now_ms
from app.security import decode_cursor, encode_cursor
import test_system
from test_system import insert_event, login

system = test_system.system


def week_window():
    today = date.today()
    return today - timedelta(days=6), today


async def test_dashboard_cache_hides_late_writes_until_expiry(system):
    app, client, _ = system
    app.state.report_cache.ttl_ms = 30_000
    await login(client)
    await insert_event(app, tenant=1, quantity=1)
    first = (await client.get("/api/v1/dashboard")).json()["data"]
    assert first["cards"][0]["value"] == 1
    await insert_event(app, tenant=1, item="two", quantity=1)
    cached = (await client.get("/api/v1/dashboard")).json()["data"]
    # Same tenant/window: served from cache, so the second insert is not visible yet.
    assert cached["cards"][0]["value"] == 1
    # Volatile fields are refreshed on every cache hit.
    assert cached["pending_jobs"] == 0 and cached["updated_at"] >= first["updated_at"]
    # A different query shape is a different cache entry and sees the new row.
    start, end = week_window()
    ranged = (await client.get(f"/api/v1/dashboard?start_date={start}&end_date={end}")).json()[
        "data"
    ]
    assert ranged["cards"][0]["value"] == 2


async def test_dashboard_cache_disabled_keeps_live_reads(system):
    app, client, _ = system
    app.state.report_cache.ttl_ms = 0
    await login(client)
    await insert_event(app, tenant=1)
    first = (await client.get("/api/v1/dashboard")).json()["data"]
    assert first["cards"][0]["value"] == 1
    await insert_event(app, tenant=1, item="two")
    second = (await client.get("/api/v1/dashboard")).json()["data"]
    assert second["cards"][0]["value"] == 2


async def test_event_pages_keep_anchor_and_return_totals(system):
    app, client, _ = system
    await login(client)
    for index in range(25):
        await insert_event(app, tenant=1, item=f"row{index}", quantity=index + 1)
    first = (await client.get("/api/v1/events?limit=20")).json()["data"]
    assert first["total_records"] == 25 and first["total_quantity"] == sum(range(1, 26))
    assert len(first["items"]) == 20 and first["next_cursor"]
    second = (await client.get(f"/api/v1/events?limit=20&cursor={first['next_cursor']}")).json()[
        "data"
    ]
    assert second["total_records"] == 25 and second["total_quantity"] == first["total_quantity"]
    assert len(second["items"]) == 5 and not second["next_cursor"]
    # Returning through the anchor keeps the ID boundary and recomputes totals.
    anchored = (await client.get(f"/api/v1/events?limit=20&cursor={first['page_cursor']}")).json()[
        "data"
    ]
    assert anchored["total_records"] == 25


async def test_legacy_cursor_without_totals_still_returns_correct_totals(system):
    app, client, _ = system
    await login(client)
    for index in range(6):
        await insert_event(app, tenant=1, item=f"old{index}")
    listing = (await client.get("/api/v1/events?limit=4")).json()["data"]
    decoded = decode_cursor(listing["page_cursor"], app.state.settings.secret_key)
    assert "total" in decoded and "quantity" in decoded
    # A cursor from the previous format carries no totals field.
    legacy = encode_cursor(
        {"upper": decoded["upper"], "filter": decoded["filter"], "time": None, "id": None},
        app.state.settings.secret_key,
    )
    response = (await client.get(f"/api/v1/events?limit=4&cursor={legacy}")).json()["data"]
    assert response["total_records"] == 6 and response["total_quantity"] == 6
    assert response["items"][0]["occurred_at"] <= now_ms()


async def test_media_response_is_long_cacheable(system):
    app, client, _ = system
    await login(client)
    media_dir = Path(app.state.settings.media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    (media_dir / "1").mkdir(exist_ok=True)
    (media_dir / "1" / "a").write_bytes(b"\x89PNG\r\n\x1a\nrest")
    async with app.state.sessions() as db:
        db.add(
            Media(
                tenant_id=1,
                sha256="a" * 64,
                relative_path="1/a",
                mime_type="image/png",
                size_bytes=12,
            )
        )
        await db.commit()
        media_id = await db.scalar(select(func.max(Media.id)))
    response = await client.get(f"/api/v1/media/{media_id}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
