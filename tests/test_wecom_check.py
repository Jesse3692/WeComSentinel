import base64
import json
import sqlite3

import httpx

from app.security import decrypt_callback
from scripts.check_wecom import check, load_config, safe_callback_url

CONFIG = {
    "corp_id": "ww-test-corp",
    "secret": "private-secret",
    "token": "private-callback-token",
    "aes_key": base64.b64encode(bytes(range(32))).decode().rstrip("="),
    "lookup_error": "",
}


async def test_online_permissions_and_no_secret_output():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("gettoken"):
            assert request.url.host == "qyapi.weixin.qq.com"
            assert request.url.params["corpsecret"] == CONFIG["secret"]
            return httpx.Response(200, json={"errcode": 0, "access_token": "private-access-token"})
        assert request.url.params["access_token"] == "private-access-token"
        if request.url.path.endswith("get_follow_user_list"):
            return httpx.Response(200, json={"errcode": 0, "follow_user": ["private-employee"]})
        assert json.loads(request.content) == {"limit": 1}
        return httpx.Response(
            200, json={"errcode": 0, "group_chat_list": [{"chat_id": "private-group"}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client)
    assert len(paths) == 3
    assert report["status"] == "passed_with_unverified_items"
    text = json.dumps(report)
    assert all(
        v not in text
        for v in [*CONFIG.values(), "private-access-token", "private-employee", "private-group"]
        if v
    )


async def test_offline_uses_real_crypto_and_no_network():
    def handler(request):
        raise AssertionError("Offline mode must not send requests")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client, offline=True)
    assert any(s["check"] == "本地回调加解密" and s["status"] == "ok" for s in report["steps"])


async def test_invalid_aes_and_missing_credentials_are_detected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError()))
    ) as client:
        report = await check({"aes_key": "x" * 43}, client)
    assert report["status"] == "failed"
    assert any(s["check"] == "CorpID" and s["status"] == "fail" for s in report["steps"])
    assert any(s["check"] == "本地回调加解密" and s["status"] == "skip" for s in report["steps"])


async def test_platform_error_is_sanitized_and_stops_business_requests():
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(
            200, json={"errcode": 40013, "errmsg": "private-secret private-callback-token"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client)
    assert count == 1 and report["status"] == "failed"
    assert "40013" in json.dumps(report)
    assert "private-secret" not in json.dumps(report)


async def test_token_success_does_not_hide_permission_failure():
    def handler(request):
        if request.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"errcode": 0, "access_token": "private-access-token"})
        return httpx.Response(200, json={"errcode": 48002})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client)
    assert report["status"] == "failed"
    assert any(s["check"] == "企业凭据验证" and s["status"] == "ok" for s in report["steps"])


async def test_callback_get_challenge_only():
    def handler(request):
        if request.url.host == "localhost":
            assert request.method == "GET"
            q = request.url.params
            plain = decrypt_callback(
                q["echostr"],
                CONFIG["token"],
                CONFIG["aes_key"],
                CONFIG["corp_id"],
                q["timestamp"],
                q["nonce"],
                q["msg_signature"],
            )
            return httpx.Response(200, content=plain)
        return httpx.Response(200, json={"errcode": 40013})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(
            CONFIG, client, callback_url="http://localhost:8000/callbacks/wecom/example"
        )
    assert any(s["check"] == "回调 URL 验证" and s["status"] == "ok" for s in report["steps"])


def test_readonly_tenant_selection_and_ambiguity(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WECOM_ACME_CORP_ID", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "DATABASE_URL=sqlite+aiosqlite:///./test.db\nWECOM_ACME_SECRET=secret\n", encoding="utf-8"
    )
    with sqlite3.connect(tmp_path / "test.db") as db:
        db.execute(
            "CREATE TABLE tenants (id INTEGER,corp_id TEXT,credential_ref TEXT,enabled INTEGER)"
        )
        db.execute("INSERT INTO tenants VALUES (1,'corp1','ACME',1)")
    assert load_config(env)["corp_id"] == "corp1"
    with sqlite3.connect(tmp_path / "test.db") as db:
        db.execute("INSERT INTO tenants VALUES (2,'corp2','ACME',1)")
    assert load_config(env)["lookup_error"]
    assert load_config(env, tenant_id=2)["corp_id"] == "corp2"
    assert load_config(env, corp_id="wrong", tenant_id=2)["lookup_error"]


def test_callback_url_constraints():
    assert safe_callback_url("https://example.com/callbacks/wecom/a")
    assert safe_callback_url("http://127.0.0.1:8000/callbacks/wecom/a")
    assert not safe_callback_url("http://example.com/callback")
    assert not safe_callback_url("https://user:password@example.com/callback")
    assert not safe_callback_url("https://example.com/callback?token=secret")
