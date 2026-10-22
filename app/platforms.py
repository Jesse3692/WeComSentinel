import asyncio
import logging
import re
import time

import httpx


class PlatformError(Exception):
    """Sanitized platform error: never include URLs carrying access tokens."""


class Platforms:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=10, follow_redirects=False)
        self.tokens = {}
        self.lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def request(self, method, url, **kwargs):
        try:
            response = await self.client.request(method, url, **kwargs)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformError("platform_transport_error") from exc
        if data.get("errcode", 0) != 0:
            # Only log fixed operation names and numeric codes. Platform messages
            # can contain access tokens, login codes and personal information.
            operation = {
                "https://api.dingtalk.com/v1.0/oauth2/accessToken": "application_token",
                "https://oapi.dingtalk.com/topapi/v2/user/getuserinfo": "login_identity",
                "https://oapi.dingtalk.com/topapi/v2/user/get": "user_details",
            }.get(url)
            if operation:
                def numeric_code(value):
                    value = str(value)
                    return value if re.fullmatch(r"-?[0-9]{1,10}", value) else "unavailable"

                code = numeric_code(data["errcode"])
                subcode = numeric_code(data.get("sub_code"))
                logging.getLogger(__name__).warning(
                    "dingtalk_api_error operation=%s code=%s subcode=%s",
                    operation, code, subcode,
                )
                message = f"platform_error_{code}"
                if subcode != "unavailable":
                    message += f"_subcode_{subcode}"
                raise PlatformError(message)
            raise PlatformError(f"platform_error_{data['errcode']}")
        return data

    async def wecom_token(self, tenant):
        key = ("wecom", tenant.id, tenant.credential_ref)
        async with self.lock:
            cached = self.tokens.get(key)
            if cached and cached[1] > time.time():
                return cached[0]
            secret = self.settings.credential(tenant.credential_ref)["secret"]
            if not secret:
                raise PlatformError("wecom_secret_not_configured")
            data = await self.request("GET", "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                                      params={"corpid": tenant.corp_id, "corpsecret": secret})
            self.tokens[key] = (data["access_token"], time.time() + max(1, data.get("expires_in", 7200) - 120))
            return data["access_token"]

    async def wecom(self, tenant, path, method="GET", params=None, body=None):
        token = await self.wecom_token(tenant)
        try:
            return await self.request(method, "https://qyapi.weixin.qq.com/cgi-bin/" + path,
                                      params={**(params or {}), "access_token": token},
                                      **({"json": body} if body is not None else {}))
        except PlatformError as exc:
            if str(exc) not in ("platform_error_40014", "platform_error_42001"):
                raise
            self.tokens.pop(("wecom", tenant.id, tenant.credential_ref), None)
            token = await self.wecom_token(tenant)
            return await self.request(method, "https://qyapi.weixin.qq.com/cgi-bin/" + path,
                                      params={**(params or {}), "access_token": token},
                                      **({"json": body} if body is not None else {}))

    async def employee(self, tenant, user_id):
        return await self.wecom(tenant, "user/get", params={"userid": user_id})

    async def customer(self, tenant, user_id):
        return await self.wecom(tenant, "externalcontact/get", params={"external_userid": user_id})

    async def group(self, tenant, chat_id):
        return (await self.wecom(tenant, "externalcontact/groupchat/get", "POST",
                                 body={"chat_id": chat_id, "need_name": 1}))["group_chat"]

    async def ding_identity(self, code):
        s = self.settings
        if not all([s.ding_corp_id, s.ding_client_id, s.ding_client_secret]):
            raise PlatformError("dingtalk_not_configured")
        async with self.lock:
            cached = self.tokens.get("ding")
            if not cached or cached[1] <= time.time():
                result = await self.request("POST", "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                                            json={"appKey": s.ding_client_id, "appSecret": s.ding_client_secret})
                cached = (result["accessToken"], time.time() + max(1, result.get("expireIn", 7200) - 120))
                self.tokens["ding"] = cached
        result = await self.request("POST", "https://oapi.dingtalk.com/topapi/v2/user/getuserinfo",
                                    params={"access_token": cached[0]}, json={"code": code})
        identity = result["result"]
        detail = await self.request("POST", "https://oapi.dingtalk.com/topapi/v2/user/get",
                                    params={"access_token": cached[0]}, json={"userid": identity["userid"]})
        return {"corp_id": s.ding_corp_id, "user_id": identity["userid"],
                "name": detail["result"].get("name", identity["userid"]),
                "avatar": detail["result"].get("avatar", "")}
