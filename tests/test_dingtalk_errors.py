from types import SimpleNamespace

import httpx
import pytest

from app.platforms import PlatformError, Platforms


@pytest.mark.parametrize("failure_operation", ["login_identity", "user_details"])
async def test_login_error_identifies_operation_without_leaking_secrets(caplog, failure_operation):
    settings = SimpleNamespace(
        ding_corp_id="test-corp",
        ding_client_id="test-key",
        ding_client_secret="private-secret",
    )
    platforms = Platforms(settings)
    await platforms.client.aclose()

    def handler(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "private-token"})
        operation = "login_identity" if request.url.path.endswith("getuserinfo") else "user_details"
        if operation == failure_operation:
            return httpx.Response(
                200,
                json={
                    "errcode": 88,
                    "sub_code": "60011",
                    "errmsg": "private-token private-secret private-code private-user",
                    "sub_msg": "private-token private-code",
                },
            )
        return httpx.Response(200, json={"errcode": 0, "result": {"userid": "private-user"}})

    platforms.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(PlatformError, match="^platform_error_88_subcode_60011$") as exc:
            await platforms.ding_identity("private-code")
        assert f"operation={failure_operation} code=88 subcode=60011" in caplog.text
        output = caplog.text + str(exc.value)
        for secret in ("private-token", "private-secret", "private-code", "private-user"):
            assert secret not in output
    finally:
        await platforms.close()


async def test_unexpected_platform_codes_are_not_echoed(caplog):
    platforms = Platforms(None)
    await platforms.client.aclose()
    platforms.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "errcode": "private-token",
                    "sub_code": "private-code",
                    "errmsg": "private-secret",
                },
            ),
        )
    )
    try:
        with pytest.raises(PlatformError, match="^platform_error_unavailable$") as exc:
            await platforms.request("POST", "https://oapi.dingtalk.com/topapi/v2/user/getuserinfo")
        assert "private-" not in caplog.text + str(exc.value)
    finally:
        await platforms.close()
