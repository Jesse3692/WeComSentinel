"""Concurrent downloads share immutable media without unique-key failures."""
import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media import save_avatar
from app.models import Media
import test_system

system = test_system.system
AVATAR = b"\x89PNG\r\n\x1a\nconcurrent-avatar"


def mock_download(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("app.media.httpx.AsyncClient", client)


async def test_same_avatar_concurrent_downloads_share_one_row(system, monkeypatch):
    app, _, _ = system
    requested = 0
    all_requested = asyncio.Event()

    async def handler(request):
        nonlocal requested
        requested += 1
        if requested == 3:
            all_requested.set()
        await all_requested.wait()
        return httpx.Response(200, content=AVATAR)

    mock_download(monkeypatch, handler)
    # Force all legacy "SELECT then INSERT" readers to observe the missing row.
    # The atomic implementation inserts first and never takes this branch.
    original_scalar = AsyncSession.scalar
    missing_reads = 0
    all_missing = asyncio.Event()

    async def scalar(self, statement, *args, **kwargs):
        nonlocal missing_reads
        value = await original_scalar(self, statement, *args, **kwargs)
        columns = getattr(statement, "column_descriptions", [])
        if columns and columns[0].get("entity") is Media and value is None:
            missing_reads += 1
            if missing_reads == 3:
                all_missing.set()
            await all_missing.wait()
        return value

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    async with asyncio.timeout(10):
        ids = await asyncio.gather(*(save_avatar(app.state.settings, app.state.sessions,
            1, "https://wx.qlogo.cn/shared-avatar") for _ in range(3)))
    assert isinstance(ids[0], int) and ids == [ids[0]] * 3
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Media)) == 1
        row = await db.get(Media, ids[0])
    path = Path(app.state.settings.media_dir) / row.relative_path
    assert path.read_bytes() == AVATAR
    assert list(path.parent.iterdir()) == [path]


async def test_identical_avatar_is_isolated_by_tenant_and_not_rewritten(system, monkeypatch):
    app, _, _ = system
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=AVATAR))
    first = await save_avatar(app.state.settings, app.state.sessions, 1,
                              "https://wx.qlogo.cn/shared-avatar")
    checksum = hashlib.sha256(AVATAR).hexdigest()
    first_path = Path(app.state.settings.media_dir) / "1" / checksum
    original_stat = first_path.stat()
    again = await save_avatar(app.state.settings, app.state.sessions, 1,
                              "https://wx.qlogo.cn/shared-avatar")
    second = await save_avatar(app.state.settings, app.state.sessions, 2,
                               "https://wx.qlogo.cn/shared-avatar")
    assert first == again and first != second
    assert first_path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert first_path.read_bytes() == AVATAR
    assert (Path(app.state.settings.media_dir) / "2" / checksum).read_bytes() == AVATAR
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Media)) == 2


async def test_failed_file_publish_cleans_temporary_file_without_media_row(system, monkeypatch):
    app, _, _ = system
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=AVATAR))

    def failed_replace(*args):
        raise OSError("fixture_file_publish_failed")

    monkeypatch.setattr("app.media.os.replace", failed_replace)
    with pytest.raises(OSError, match="fixture_file_publish_failed"):
        await save_avatar(app.state.settings, app.state.sessions, 1,
                          "https://wx.qlogo.cn/shared-avatar")
    assert list((Path(app.state.settings.media_dir) / "1").iterdir()) == []
    async with app.state.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Media)) == 0
