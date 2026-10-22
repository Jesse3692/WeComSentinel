"""Read-only DingTalk credential diagnostics; never print credentials or tokens."""
import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("DING_CORP_ID", "DING_CLIENT_ID", "DING_CLIENT_SECRET", "DING_AGENT_ID")


def load_config(env_file):
    values = dotenv_values(env_file)
    return {key: str(os.environ.get(key, values.get(key)) or "") for key in FIELDS}


async def check(config, client, auth_code=None):
    steps = []
    sensitive = [value for value in config.values() if value]
    if auth_code:
        sensitive.append(auth_code)

    def add(name, status, message):
        steps.append({"check": name, "status": status, "message": message})

    def safe_code(value):
        value = str(value)
        if any(secret in value for secret in sensitive):
            return "redacted"
        return value if re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value) else "unavailable"

    async def call(name, url, **kwargs):
        try:
            response = await client.post(url, **kwargs)
        except httpx.TimeoutException:
            add(name, "fail", "请求超时，请检查网络及代理配置。")
            return None
        except httpx.HTTPError:
            add(name, "fail", "连接失败，请检查网络、代理或 TLS 证书；未输出可能含凭据的异常详情。")
            return None
        try:
            data = response.json()
        except ValueError:
            add(name, "fail", f"HTTP {response.status_code}，响应不是 JSON。")
            return None
        if not isinstance(data, dict):
            add(name, "fail", "响应格式异常。")
            return None
        code = data.get("errcode", data.get("code"))
        if response.is_error or code not in (None, 0, "0"):
            subcode = data.get("sub_code")
            detail = f"，子错误码：{safe_code(subcode)}" if subcode is not None else ""
            add(name, "fail", f"HTTP {response.status_code}，平台错误码：{safe_code(code)}{detail}。请在钉钉后台核对凭据、接口权限及调用限制。")
            return None
        return data

    for key in FIELDS:
        value = config.get(key, "")
        if not value or value.strip() != value or "复制的" in value or "由公司提供" in value:
            add(key, "fail", "未填写有效值，或含首尾空白。")
        elif key == "DING_AGENT_ID" and not value.isascii():
            add(key, "fail", "AgentId 应为后台提供的数字标识。")
        elif key == "DING_AGENT_ID" and not value.isdigit():
            add(key, "fail", "AgentId 应为数字，请勿填写 CorpID、Client ID 或应用名称。")
        else:
            add(key, "ok", "已配置（值不显示）。")
    add("企业与应用归属", "skip", "本检测不通过获取 token 推断 CorpID 或 AgentId 的归属，需与同一应用后台信息核对。")

    valid_credentials = all(config.get(k) and config[k].strip() == config[k]
                            for k in ("DING_CLIENT_ID", "DING_CLIENT_SECRET"))
    if valid_credentials:
        data = await call("应用凭据验证", "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                          json={"appKey": config["DING_CLIENT_ID"], "appSecret": config["DING_CLIENT_SECRET"]})
        token = data.get("accessToken") if data else None
        if data is not None:
            if not isinstance(token, str) or not token:
                add("应用凭据验证", "fail", "响应缺少有效 accessToken，不能认定凭据通过。")
                token = None
            else:
                sensitive.append(token)
                add("应用凭据验证", "ok", "官方接口成功签发应用 accessToken，Client ID 与 Client Secret 配对可用。token 不输出、不落盘。")
        if token and auth_code:
            identity = await call("免登 code 验证", "https://oapi.dingtalk.com/topapi/v2/user/getuserinfo",
                                  params={"access_token": token}, json={"code": auth_code})
            identity_result = identity.get("result") if identity else None
            user_id = identity_result.get("userid") if isinstance(identity_result, dict) else None
            if identity is not None:
                if not isinstance(user_id, str) or not user_id:
                    add("免登 code 验证", "fail", "响应缺少 userid。")
                else:
                    sensitive.append(user_id)
                    add("免登 code 验证", "ok", "一次性 code 已成功换取用户身份，不输出用户信息。")
                    detail = await call("用户详情权限", "https://oapi.dingtalk.com/topapi/v2/user/get",
                                        params={"access_token": token}, json={"userid": user_id})
                    if detail is not None:
                        if isinstance(detail.get("result"), dict) and detail["result"].get("userid") == user_id:
                            add("用户详情权限", "ok", "已成功读取对应用户详情，不输出个人资料。")
                        else:
                            add("用户详情权限", "fail", "响应中缺少匹配的用户详情。")
        elif auth_code:
            add("免登 code 验证", "skip", "应用 token 未取得，未使用一次性 code。")
        else:
            add("免登与用户详情权限", "skip", "未提供钉钉 H5 一次性 code，未验证客户端免登及用户详情权限。")
    else:
        add("应用凭据验证", "skip", "Client ID 或 Client Secret 缺失或无效，未发送请求。")
    add("本系统授权与客户端", "skip", "未测试钉钉工作台可见范围、首页域名、PC/手机容器以及本系统用户授权。")
    failed = any(step["status"] == "fail" for step in steps)
    return {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "status": "failed" if failed else "passed_with_unverified_items",
            "steps": steps}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="读取 .env 并检查钉钉内部应用凭据，不打印密钥或 token")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--with-login", action="store_true", help="隐藏输入一次性 H5 code，并验证免登和用户详情；code 会被使用")
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "dingtalk-check.json")
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error("指定的配置文件不存在")
    config = load_config(args.env_file)
    code = None
    if args.with_login:
        if not os.isatty(0):
            parser.error("--with-login 需要交互式终端，避免将一次性 code 写到命令行或日志")
        code = getpass.getpass("输入钉钉 H5 新获取的一次性 code（不回显）：").strip()
        if not code:
            parser.error("一次性 code 不能为空")

    async def run():
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            return await check(config, client, code)

    report = asyncio.run(run())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for step in report["steps"]:
        print(f"[{step['status'].upper()}] {step['check']}: {step['message']}")
    print("结果：" + ("存在失败项。" if report["status"] == "failed" else "已执行的检查通过；SKIP 项仍未验证。"))
    print("脱敏报告已保存。")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
