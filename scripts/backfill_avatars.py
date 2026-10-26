"""Backfill only avatar references for identified, non-demo historical events."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from sqlalchemy import select

from app.config import Settings
from app.db import make_database
from app.models import BusinessEvent, Tenant
from app.platforms import Platforms, PlatformError
from app.worker import Worker


async def run(settings, apply=False):
    engine, sessions = make_database(settings)
    platforms = Platforms(settings)
    worker = Worker(settings, sessions, platforms)
    try:
        async with sessions() as db:
            rows = (await db.scalars(select(BusinessEvent))).all()
            tenants = {
                t.id: t
                for t in (await db.scalars(select(Tenant).where(Tenant.enabled.is_(True)))).all()
            }
        targets = set()
        ids = []
        for row in rows:
            if row.snapshot.get("demo") or row.tenant_id not in tenants:
                continue
            ids.append(row.id)
            if row.employee_id:
                targets.add((row.tenant_id, "employee", row.employee_id))
            if row.customer_id:
                targets.add((row.tenant_id, "customer", row.customer_id))
            members = row.snapshot.get("member_candidates", [])
            if (
                row.snapshot.get("member_evidence") == "snapshot_difference"
                and len(members) == row.quantity
                and row.snapshot.get("classification_reason") != "identity_unresolved"
            ):
                for m in members:
                    kind = {1: "employee", 2: "customer"}.get(m.get("member_type"))
                    if kind and m.get("member_id"):
                        targets.add((row.tenant_id, kind, m["member_id"]))
        summary = {"real_records": len(ids), "identities": len(targets), "apply": apply}
        if not apply:
            return summary
        if not settings.database_url.startswith("sqlite+aiosqlite:///"):
            raise RuntimeError("SQLite backup required before backfill")
        backup = Path("backups") / (
            "avatar-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        )
        backup.mkdir(parents=True, exist_ok=False)
        with sqlite3.connect(settings.database_url.removeprefix("sqlite+aiosqlite:///")) as source:
            with sqlite3.connect(backup / "monitor.db") as destination:
                source.backup(destination)
        photos = {}
        unavailable = {"employee": 0, "customer": 0}
        for tid, kind, identity in sorted(targets):
            try:
                data, _ = await worker.get_profile(tenants[tid], kind, identity, force=True)
                if data.get("media_id"):
                    photos[(tid, kind, identity)] = data["media_id"]
                else:
                    unavailable[kind] += 1
            except PlatformError:
                unavailable[kind] += 1
        patched = 0
        async with sessions() as db:
            for ident in ids:
                row = await db.get(BusinessEvent, ident)
                before = row.snapshot
                snapshot = deepcopy(before)
                for kind, identity, key in [
                    ("employee", row.employee_id, "employee_media_id"),
                    ("customer", row.customer_id, "customer_media_id"),
                ]:
                    media = photos.get((row.tenant_id, kind, identity))
                    if media:
                        snapshot[key] = media
                members = snapshot.get("member_candidates", [])
                if (
                    snapshot.get("member_evidence") == "snapshot_difference"
                    and len(members) == row.quantity
                    and snapshot.get("classification_reason") != "identity_unresolved"
                ):
                    for member in members:
                        kind = {1: "employee", 2: "customer"}.get(member.get("member_type"))
                        media = photos.get((row.tenant_id, kind, member.get("member_id")))
                        if media:
                            member["media_id"] = media
                            member["avatar_state"] = "stored"
                    for member in snapshot.get("_all_member_candidates", []):
                        kind = {1: "employee", 2: "customer"}.get(member.get("member_type"))
                        media = photos.get((row.tenant_id, kind, member.get("member_id")))
                        if media:
                            member["media_id"] = media
                            member["avatar_state"] = "stored"
                if snapshot != before:
                    row.snapshot = snapshot
                    patched += 1
            await db.commit()
        return {
            **summary,
            "patched_records": patched,
            "identities_with_media": len(photos),
            "unavailable": unavailable,
            "backup_created": True,
        }
    finally:
        await platforms.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(Settings(), args.apply)), ensure_ascii=False))
