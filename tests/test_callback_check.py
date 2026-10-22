import json
import sqlite3
from types import SimpleNamespace

from cryptography.fernet import Fernet

from scripts.check_callbacks import event_fields, inspect_callbacks, safe_error


def test_callback_report_is_read_only_and_omits_private_payload(tmp_path):
    path = tmp_path / "monitor.db"
    key = Fernet.generate_key()
    payload = {"Event": "change_external_chat", "ChangeType": "update",
               "UpdateDetail": "del_member", "QuitScene": "1", "CreateTime": "1792630800",
               "ChatId": "private-chat", "UserID": "private-user", "Token": "private-token"}
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE tenants (id INTEGER PRIMARY KEY, enabled INTEGER);
            CREATE TABLE callback_inbox (id INTEGER PRIMARY KEY, tenant_id INTEGER,
                received_at INTEGER, payload_encrypted BLOB);
            CREATE TABLE processing_jobs (callback_id INTEGER, status TEXT, attempts INTEGER,
                last_error TEXT);
            CREATE TABLE business_events (id INTEGER PRIMARY KEY, callback_id INTEGER,
                event_type TEXT, occurred_at INTEGER);
            INSERT INTO tenants VALUES (1, 1);
            INSERT INTO processing_jobs VALUES (1, 'done', 1, NULL);
            INSERT INTO business_events VALUES (1, 1, 'group_change_unknown', 1792630800000);
        """)
        db.execute("INSERT INTO callback_inbox VALUES (1, 1, 1792630800000, ?)",
                   (Fernet(key).encrypt(json.dumps(payload).encode()),))
    before = path.read_bytes()
    report = inspect_callbacks(SimpleNamespace(
        database_url="sqlite+aiosqlite:///" + str(path), payload_key=key.decode(), worker_enabled=True,
    ))
    assert path.read_bytes() == before
    row = report["callbacks"][0]
    assert row["job_status"] == "done" and row["event_count"] == 1
    assert row["UpdateDetail"] == "del_member" and row["QuitScene"] == "1"
    assert row["events"][0]["type"] == "group_change_unknown"
    assert row["events"][0]["time"].endswith("+08:00")
    assert "private-" not in json.dumps(report)


def test_errors_are_redacted():
    assert safe_error("platform_error_48002") == "platform_error_48002"
    assert safe_error("https://example.com?access_token=private") == "other_error_redacted"


def test_unsupported_event_identifiers_are_visible_without_other_payload_fields():
    fields = event_fields({
        "MsgType": "event", "Event": "change_contact", "ChangeType": "delete_user",
        "ChatId": "private-chat", "Content": "private-message", "UserID": "private-user",
    })
    assert fields["Event"] == "change_contact"
    assert fields["ChangeType"] == "delete_user"
    assert fields["UpdateDetail"] == "missing" and fields["has_chat_id"]
    assert "private-" not in json.dumps(fields)
    assert event_fields({"Event": "https://example.com?token=secret"})["Event"] == "redacted"
