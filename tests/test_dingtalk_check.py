import json

import httpx

from scripts.check_dingtalk import check

CONFIG = {"DING_CORP_ID":"ding-test-corp", "DING_CLIENT_ID":"test-client",
          "DING_CLIENT_SECRET":"test-secret", "DING_AGENT_ID":"123456"}


async def test_credentials_pass_without_claiming_full_login():
    def handler(request):
        assert request.url.host == "api.dingtalk.com"
        assert json.loads(request.content) == {"appKey":"test-client", "appSecret":"test-secret"}
        return httpx.Response(200, json={"accessToken":"private-token", "expireIn":7200})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client)
    text = json.dumps(report)
    assert report["status"] == "passed_with_unverified_items"
    assert "private-token" not in text and "test-secret" not in text
    assert any(s["status"] == "skip" and "免登" in s["check"] for s in report["steps"])


async def test_missing_config_does_not_send_requests():
    def handler(request):
        raise AssertionError("No request expected")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check({}, client)
    assert report["status"] == "failed"


async def test_error_does_not_echo_secrets_in_platform_response():
    def handler(request):
        return httpx.Response(400, json={"code":"invalid.test-secret", "message":"test-secret private-token"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client)
    assert report["status"] == "failed"
    assert "test-secret" not in json.dumps(report)


async def test_missing_token_is_not_success():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={}))) as client:
        report = await check(CONFIG, client)
    assert report["status"] == "failed"


async def test_optional_login_checks_identity_and_details_without_pii():
    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200,json={"accessToken":"private-token"})
        assert request.url.params["access_token"] == "private-token"
        if request.url.path.endswith("getuserinfo"):
            assert json.loads(request.content)["code"] == "one-time-code"
            return httpx.Response(200,json={"errcode":0,"result":{"userid":"private-user"}})
        return httpx.Response(200,json={"errcode":0,"result":{"userid":"private-user","name":"private-name"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await check(CONFIG, client, "one-time-code")
    text = json.dumps(report)
    assert report["status"] == "passed_with_unverified_items"
    assert all(v not in text for v in ["private-token","private-user","private-name","one-time-code"])
    assert any(s["check"] == "用户详情权限" and s["status"] == "ok" for s in report["steps"])
