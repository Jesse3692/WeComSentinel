import hashlib
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from .models import Media


async def save_avatar(settings, sessions, tenant_id, url):
    """Only Tencent image origins, no redirects or caller-provided arbitrary URLs."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.port not in (None, 443):
        return None
    if not any(host == d or host.endswith("." + d) for d in ("qpic.cn", "qlogo.cn")):
        return None
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        return None
    except httpx.HTTPError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    elif data.startswith((b"GIF87a", b"GIF89a")):
        mime = "image/gif"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        return None
    checksum = hashlib.sha256(data).hexdigest()
    async with sessions() as db:
        existing = await db.scalar(select(Media).where(Media.tenant_id == tenant_id, Media.sha256 == checksum))
        if existing:
            return existing.id
        relative = f"{tenant_id}/{checksum}"
        path = Path(settings.media_dir) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        item = Media(tenant_id=tenant_id, sha256=checksum, relative_path=relative,
                     mime_type=mime, size_bytes=len(data))
        db.add(item)
        await db.commit()
        return item.id
