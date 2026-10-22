"""Inspect recent callback processing using read-only SQLite and redacted output."""
import argparse
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from app.config import Settings


def event_fields(payload):
    # Show protocol identifiers even for events the worker does not support.
    # Never output arbitrary content, IDs, messages, or the complete payload.
    fields = {}
    for key in ("MsgType", "Event", "ChangeType", "UpdateDetail", "QuitScene"):
        value = payload.get(key)
        if value is None or value == "":
            fields[key] = "missing"
        elif isinstance(value, (str, int)) and re.fullmatch(r"[A-Za-z0-9_]{1,80}", str(value)):
            fields[key] = str(value)
        else:
            fields[key] = "redacted"
    fields["has_chat_id"] = bool(payload.get("ChatId"))
    return fields


def safe_error(value):
    if not value:
        return None
    if value in {"tenant_disabled", "profile_incomplete", "wecom_secret_not_configured",
                 "platform_transport_error", "TimeoutError", "ValueError", "KeyError",
                 "TypeError", "IntegrityError", "OperationalError", "contact_identity_fields_missing",
                 "group_chat_id_missing", "group_member_count_invalid", "group_snapshot_not_conclusive",
                 "group_member_profile_incomplete", "avatar_unavailable"}:
        return value
    if re.fullmatch(r"platform_error_-?[0-9]{1,10}", value):
        return value
    return "other_error_redacted"


def stamp(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000, ZoneInfo("Asia/Shanghai")).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return "unknown"


def inspect_callbacks(settings, limit=10):
    prefix = "sqlite+aiosqlite:///"
    if not settings.database_url.startswith(prefix):
        raise ValueError("Only the project's SQLite database is supported")
    path = Path(settings.database_url.removeprefix(prefix)).resolve()
    if not path.is_file():
        raise ValueError("Database not found; run from the active server project directory")
    cipher = Fernet(settings.payload_key.encode())
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("""
            SELECT i.id, i.tenant_id, i.received_at, i.payload_encrypted,
                   t.enabled, j.status, j.attempts, j.last_error,
                   (SELECT count(*) FROM business_events e WHERE e.callback_id=i.id) AS event_count
            FROM callback_inbox i
            JOIN tenants t ON t.id=i.tenant_id
            LEFT JOIN processing_jobs j ON j.callback_id=i.id
            ORDER BY i.id DESC LIMIT ?
        """, (limit,)).fetchall()
        output = []
        for row in rows:
            item = {
                "callback_id": row["id"], "tenant_id": row["tenant_id"],
                "tenant_enabled": bool(row["enabled"]), "received_at": stamp(row["received_at"]),
                "job_status": row["status"], "attempts": row["attempts"],
                "last_error": safe_error(row["last_error"]), "event_count": row["event_count"],
            }
            try:
                payload = json.loads(cipher.decrypt(row["payload_encrypted"]))
                if not isinstance(payload, dict):
                    raise ValueError("Expected an object")
                item.update(event_fields(payload))
            except Exception:
                item["payload_check"] = "decrypt_or_parse_failed"
            else:
                item["payload_check"] = "parsed"
                try:
                    item["event_time"] = stamp(int(payload.get("CreateTime")) * 1000)
                except (TypeError, ValueError, OverflowError):
                    item["event_time"] = "missing_or_invalid"
            events = db.execute(
                "SELECT event_type, occurred_at FROM business_events WHERE callback_id=? ORDER BY id",
                (row["id"],),
            ).fetchall()
            known_types = {"contact_added", "contact_deleted", "group_left", "group_removed",
                           "group_change_unknown"}
            item["events"] = [{
                "type": e["event_type"] if e["event_type"] in known_types else "unknown",
                "time": stamp(e["occurred_at"]),
            } for e in events]
            output.append(item)
    return {"worker_enabled": settings.worker_enabled, "callbacks": output}


def main():
    parser = argparse.ArgumentParser(description="只读检查最近回调、任务和事件；不输出密钥或人员资料")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="1-100")
    args = parser.parse_args()
    try:
        report = inspect_callbacks(Settings(), args.limit)
    except Exception:
        print("检查失败：确认当前目录、DATABASE_URL、PAYLOAD_KEY 及数据库表结构与运行服务一致。")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
