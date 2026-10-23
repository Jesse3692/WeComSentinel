import base64
import hashlib
import hmac
import json
import secrets
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from defusedxml import ElementTree


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def signature(token, timestamp, nonce, encrypted):
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypted])).encode()).hexdigest()


def parse_xml(value: bytes | str) -> dict:
    root = ElementTree.fromstring(value)
    result = {}
    for child in root:
        if len(child):
            # MemChangeList/Item contains CDATA text, not nested key/value pairs.
            # Preserve the exact member identifiers instead of producing [{}].
            if child.tag == "MemChangeList":
                result[child.tag] = [item.text or "" if not len(item) else
                                     dict((x.tag, x.text or "") for x in item) for item in child]
            else:
                result[child.tag] = [dict((x.tag, x.text or "") for x in item) for item in child]
        else:
            result[child.tag] = child.text or ""
    return result


def decrypt_callback(encrypted, token, aes_key, corp_id, timestamp, nonce, supplied_signature):
    if not token or not aes_key:
        raise ValueError("Callback credentials not configured")
    expected = signature(token, timestamp, nonce, encrypted)
    if not hmac.compare_digest(expected, supplied_signature):
        raise ValueError("Invalid signature")
    key = base64.b64decode(aes_key + "=", validate=True)
    if len(key) != 32:
        raise ValueError("Invalid AES key")
    ciphertext = base64.b64decode(encrypted, validate=True)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    data = decryptor.update(ciphertext) + decryptor.finalize()
    pad = data[-1]
    if pad < 1 or pad > 32 or data[-pad:] != bytes([pad]) * pad:
        raise ValueError("Invalid padding")
    data = data[:-pad]
    if len(data) < 20:
        raise ValueError("Invalid payload")
    length = struct.unpack("!I", data[16:20])[0]
    if length > len(data) - 20:
        raise ValueError("Invalid payload length")
    message, receiver = data[20:20 + length], data[20 + length:]
    if not hmac.compare_digest(receiver, corp_id.encode()):
        raise ValueError("Invalid receiver")
    return message


def encode_cursor(data, secret):
    raw = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
    return raw + "." + hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def decode_cursor(value, secret):
    raw, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()):
        raise ValueError("Invalid cursor")
    return json.loads(base64.urlsafe_b64decode(raw))


def random_token():
    return secrets.token_urlsafe(32)
