"""Read-only WeCom internal-application diagnostics. No secrets in output."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import dotenv_values

from app.security import decrypt_callback

ROOT = Path(__file__).resolve().parents[1]


def load_config(env_file, ref="ACME", corp_id=None, tenant_id=None):
    values = {**dotenv_values(env_file), **os.environ}
    config = {
        name: str(values.get(f"WECOM_{ref}_{suffix}") or "")
        for name, suffix in (("secret", "SECRET"), ("token", "TOKEN"), ("aes_key", "AES_KEY"))
    }
    config["corp_id"] = corp_id or str(values.get(f"WECOM_{ref}_CORP_ID") or "")
    config["lookup_error"] = ""
    if not config["corp_id"] or tenant_id is not None:
        url = str(values.get("DATABASE_URL") or "sqlite+aiosqlite:///./data/monitor.db")
        if not url.startswith("sqlite+aiosqlite:///"):
            config["lookup_error"] = "无法自动读取主体：仅支持本项目的 SQLite 配置。"
            return config
        path = Path(url.removeprefix("sqlite+aiosqlite:///"))
        if not path.is_absolute():
            path = env_file.parent / path
        if not path.is_file():
            config["lookup_error"] = (
                "未找到主体数据库，请用 --corp-id 或 WECOM_<REF>_CORP_ID 指定企业 ID。"
            )
            return config
        try:
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
                query = "SELECT corp_id FROM tenants WHERE credential_ref = ? AND enabled = 1"
                params = [ref]
                if tenant_id is not None:
                    query += " AND id = ?"
                    params.append(tenant_id)
                rows = db.execute(query, params).fetchall()
            if len(rows) != 1:
                config["lookup_error"] = (
                    "未找到唯一启用主体。请核对 --ref，或同时指定 --tenant-id 与 --ref。"
                )
            elif config["corp_id"] and config["corp_id"] != rows[0][0]:
                config["lookup_error"] = "指定的企业 ID 与数据库主体不一致。"
            else:
                config["corp_id"] = rows[0][0]
        except sqlite3.Error:
            config["lookup_error"] = "无法读取主体表，请先初始化数据库或显式指定企业 ID。"
    return config


def challenge(config):
    message = secrets.token_hex(24).encode()
    key = base64.b64decode(config["aes_key"] + "=", validate=True)
    raw = os.urandom(16) + struct.pack("!I", len(message)) + message + config["corp_id"].encode()
    padding = 32 - len(raw) % 32
    raw += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode()
    timestamp, nonce = str(int(time.time())), secrets.token_hex(8)
    sig = hashlib.sha1(
        "".join(sorted([config["token"], timestamp, nonce, encrypted])).encode()
    ).hexdigest()
    return message, {
        "echostr": encrypted,
        "timestamp": timestamp,
        "nonce": nonce,
        "msg_signature": sig,
    }


def safe_callback_url(value):
    try:
        parsed = urlparse(value)
        return bool(
            parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                )
            )
        )
    except ValueError:
        return False


async def check(config, client, offline=False, callback_url=None):
    steps = []
    private = [config.get(k) for k in ("secret", "token", "aes_key", "corp_id") if config.get(k)]

    def add(name, status, message):
        steps.append({"check": name, "status": status, "message": message})

    if config.get("lookup_error"):
        add("主体选择", "fail", config["lookup_error"])
    valid = {}
    for key, label in (
        ("corp_id", "CorpID"),
        ("secret", "Secret"),
        ("token", "回调 Token"),
        ("aes_key", "EncodingAESKey"),
    ):
        value = config.get(key, "")
        good = bool(
            value and value == value.strip() and "由公司提供" not in value and "复制的" not in value
        )
        if good and key == "aes_key":
            try:
                good = len(value) == 43 and len(base64.b64decode(value + "=", validate=True)) == 32
            except ValueError:
                good = False
        valid[key] = good
        add(
            label,
            "ok" if good else "fail",
            "已配置，基本格式通过（不显示值）。"
            if good
            else (
                "应填写 43 字符、可解码为 32 字节的 EncodingAESKey。"
                if key == "aes_key"
                else "配置缺失、占位值或包含首尾空白。"
            ),
        )

    callback_ready = all(valid[k] for k in ("corp_id", "token", "aes_key")) and not config.get(
        "lookup_error"
    )
    probe = None
    if callback_ready:
        try:
            message, probe = challenge(config)
            decoded = decrypt_callback(
                probe["echostr"],
                config["token"],
                config["aes_key"],
                config["corp_id"],
                probe["timestamp"],
                probe["nonce"],
                probe["msg_signature"],
            )
            if decoded != message:
                raise ValueError()
            for corp, sig in (
                (config["corp_id"], "invalid"),
                ("wrong-receiver", probe["msg_signature"]),
            ):
                try:
                    decrypt_callback(
                        probe["echostr"],
                        config["token"],
                        config["aes_key"],
                        corp,
                        probe["timestamp"],
                        probe["nonce"],
                        sig,
                    )
                except ValueError:
                    continue
                raise AssertionError("Invalid payload was accepted")
            add(
                "本地回调加解密",
                "ok",
                "挑战串往返、错误签名拒绝、接收企业校验通过；尚不代表与企微后台配置一致。",
            )
        except Exception:
            callback_ready = False
            add("本地回调加解密", "fail", "本地加解密或校验失败，请检查 Token、AESKey 与企业 ID。")
    else:
        add("本地回调加解密", "skip", "回调配置不完整或主体不明确。")

    async def call(name, method, path, **kwargs):
        try:
            response = await client.request(
                method, "https://qyapi.weixin.qq.com/cgi-bin/" + path, **kwargs
            )
        except httpx.HTTPError:
            add(name, "fail", "网络、超时或 TLS 错误；未输出可能含密钥的请求 URL 或异常详情。")
            return None
        try:
            data = response.json()
        except ValueError:
            add(name, "fail", f"HTTP {response.status_code}，响应不是 JSON。")
            return None
        if not isinstance(data, dict):
            add(name, "fail", "平台响应格式异常。")
            return None
        code = data.get("errcode")
        if not response.is_success or code not in (0, "0"):
            code_text = str(code)
            safe = (
                code_text
                if re.fullmatch(r"[0-9-]{1,12}", code_text)
                and not any(v in code_text for v in private)
                else "unavailable"
            )
            add(
                name,
                "fail",
                f"HTTP {response.status_code}，企微错误码：{safe}。请核对企业与 Secret、接口权限、使用范围及可信 IP。",
            )
            return None
        return data

    access_token = None
    if not offline and valid["corp_id"] and valid["secret"] and not config.get("lookup_error"):
        data = await call(
            "企业凭据验证",
            "GET",
            "gettoken",
            params={"corpid": config["corp_id"], "corpsecret": config["secret"]},
        )
        if data is not None:
            access_token = data.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                access_token = None
                add("企业凭据验证", "fail", "响应缺少有效 access_token。")
            else:
                private.append(access_token)
                add(
                    "企业凭据验证",
                    "ok",
                    "官方接口成功签发 access_token，CorpID 与 Secret 配对可用；token 不输出、不落盘。",
                )
    else:
        add("企业凭据验证", "skip", "离线模式，或企业凭据缺失／主体不明确，未调用平台。")
    if access_token:
        data = await call(
            "客户联系权限",
            "GET",
            "externalcontact/get_follow_user_list",
            params={"access_token": access_token},
        )
        if data is not None:
            if isinstance(data.get("follow_user"), list):
                add(
                    "客户联系权限",
                    "ok",
                    "客户联系员工列表接口可调用；未显示或保存员工标识。空列表不证明有可用测试员工。",
                )
            else:
                add("客户联系权限", "fail", "响应缺少有效 follow_user 列表。")
        data = await call(
            "客户群列表权限",
            "POST",
            "externalcontact/groupchat/list",
            params={"access_token": access_token},
            json={"limit": 1},
        )
        if data is not None:
            if isinstance(data.get("group_chat_list"), list):
                add(
                    "客户群列表权限",
                    "ok",
                    "客户群列表接口可调用，仅请求最多 1 项，未显示或保存群资料。",
                )
            else:
                add("客户群列表权限", "fail", "响应缺少有效 group_chat_list 列表。")
    else:
        add("客户联系与群权限", "skip", "未取得 access_token，未调用业务接口。")

    if callback_url and callback_ready and not offline:
        if not safe_callback_url(callback_url):
            add(
                "回调 URL 验证",
                "fail",
                "仅允许不带凭据、查询参数或片段的 HTTPS URL，或本机 HTTP 地址。",
            )
        else:
            try:
                response = await client.get(callback_url, params=probe)
                good = response.status_code == 200 and response.content == message
                add(
                    "回调 URL 验证",
                    "ok" if good else "fail",
                    "本机发起的 GET 挑战验证通过；未发送业务事件，不证明企微服务器可达。"
                    if good
                    else f"HTTP {response.status_code}，响应未通过挑战串验证；请检查目标服务、主体与回调配置。",
                )
            except httpx.HTTPError:
                add("回调 URL 验证", "fail", "无法连接回调地址；未输出携带加密挑战串的 URL。")
    else:
        add("回调 URL 验证", "skip", "未提供 --callback-url，或使用离线模式／回调配置不完整。")
    add(
        "真实平台端到端联调",
        "skip",
        "尚未验证企微后台回调保存、公网可达、真实加删及退群事件、员工/客户详情和头像权限。",
    )
    return {
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "status": "failed"
        if any(s["status"] == "fail" for s in steps)
        else "passed_with_unverified_items",
        "steps": steps,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="企业微信内部应用配置检测，不打印凭据或个人资料")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--ref", default="ACME", help="主体凭据引用，例如 ACME")
    parser.add_argument("--corp-id", help="企业 CorpID；不是钉钉 CorpID")
    parser.add_argument("--tenant-id", type=int, help="只读查询本项目主体表，必须与 --ref 对应")
    parser.add_argument("--offline", action="store_true", help="仅检查格式与本地加解密，不发送请求")
    parser.add_argument("--callback-url", help="可选：发起不入库的 GET 回调挑战验证")
    parser.add_argument("--report", type=Path, default=ROOT / "data" / "wecom-check.json")
    args = parser.parse_args()
    if not args.env_file.is_file() or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,40}", args.ref):
        parser.error("请提供存在的配置文件和合法的大写凭据引用")
    config = load_config(args.env_file.resolve(), args.ref, args.corp_id, args.tenant_id)

    async def run():
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            return await check(config, client, args.offline, args.callback_url)

    report = asyncio.run(run())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for step in report["steps"]:
        print(f"[{step['status'].upper()}] {step['check']}: {step['message']}")
    print(
        "结果："
        + (
            "存在失败项。"
            if report["status"] == "failed"
            else "已执行的检查通过；SKIP 项仍未验证。"
        )
    )
    print("脱敏报告已保存。")
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
