#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class SpeedafLookupError(RuntimeError):
    pass

FIXED_IV = bytes([0x12, 0x34, 0x56, 0x78, 0x90, 0xAB, 0xCD, 0xEF])


def now_ms() -> int:
    return int(time.time() * 1000)


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _load_crypto() -> tuple[Any, Any, Any] | None:
    try:
        from Crypto.Cipher import DES  # type: ignore
        from Crypto.Util.Padding import pad, unpad  # type: ignore
        return DES, pad, unpad
    except ModuleNotFoundError:
        return None


def _openssl_des_base64(plain_text: str, key: str) -> str:
    raise SpeedafLookupError("openssl_fallback_disabled_for_security_reasons: pycryptodome required")

def _openssl_des_decrypt_base64(cipher_text_b64: str, key: str) -> str:
    raise SpeedafLookupError("openssl_fallback_disabled_for_security_reasons: pycryptodome required")


def des_encrypt_base64(plain_text: str, key: str) -> str:
    crypto = _load_crypto()
    if crypto is not None:
        DES, pad, _ = crypto
        cipher = DES.new(key.encode("utf-8"), DES.MODE_CBC, FIXED_IV)
        encrypted = cipher.encrypt(pad(plain_text.encode("utf-8"), DES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")
    return _openssl_des_base64(plain_text, key)


def des_decrypt_base64(cipher_text_b64: str, key: str) -> str:
    crypto = _load_crypto()
    if crypto is not None:
        DES, _, unpad = crypto
        cipher = DES.new(key.encode("utf-8"), DES.MODE_CBC, FIXED_IV)
        raw = base64.b64decode(cipher_text_b64)
        decrypted = unpad(cipher.decrypt(raw), DES.block_size)
        return decrypted.decode("utf-8")
    return _openssl_des_decrypt_base64(cipher_text_b64, key)


def env_config() -> dict[str, Any]:
    base_url = os.getenv("SPEEDAF_BASE_URL", "https://apis.speedaf.com/open-api/mcp").rstrip("/")
    app_code = os.getenv("SPEEDAF_APP_CODE")
    secret_key = os.getenv("SPEEDAF_SECRET_KEY")
    customer_code = os.getenv("SPEEDAF_CUSTOMER_CODE", "CH000001")
    
    if not app_code or not secret_key:
        raise SpeedafLookupError("speedaf_config_missing: SPEEDAF_APP_CODE and SPEEDAF_SECRET_KEY must be set in production")
        
    return {
        "base_url": base_url,
        "app_code": app_code,
        "secret_key": secret_key,
        "customer_code": customer_code,
        "platform_source": os.getenv("SPEEDAF_PLATFORM_SOURCE", "PROD345"),
        "timeout": int(os.getenv("SPEEDAF_TIMEOUT", "20")),
    }


def config_presence(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "base_url": cfg["base_url"],
        "app_code_present": bool(cfg["app_code"]),
        "secret_key_present": bool(cfg["secret_key"]),
        "customer_code_present": bool(cfg["customer_code"]),
        "iv_fixed": [0x12, 0x34, 0x56, 0x78, 0x90, 0xAB, 0xCD, 0xEF],
        "timeout": cfg["timeout"],
    }


def validate_runtime_config(cfg: dict[str, Any]) -> None:
    missing = [name for name in ("app_code", "secret_key", "customer_code") if not cfg.get(name)]
    if missing:
        raise SpeedafLookupError(f"speedaf_config_missing:{','.join(missing)}")
    if len(str(cfg["secret_key"])) != 8:
        raise SpeedafLookupError("invalid_speedaf_secret_key_length")


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def build_encrypted_request(business_payload: dict[str, Any], cfg: dict[str, Any], timestamp: str) -> dict[str, Any]:
    validate_runtime_config(cfg)
    payload_json = canonical_json(business_payload)
    sign = md5_hex(timestamp + cfg["secret_key"] + payload_json)
    body_obj = {
        "data": business_payload,
        "sign": sign,
    }
    body_json = canonical_json(body_obj)
    encrypted_body = des_encrypt_base64(body_json, cfg["secret_key"])
    return {
        "timestamp": timestamp,
        "payload_json": payload_json,
        "sign": sign,
        "body_json": body_json,
        "encrypted_body": encrypted_body,
    }


def response_error_payload(parsed: Any) -> dict[str, Any] | None:
    if isinstance(parsed, dict):
        if parsed.get("success") is False:
            error = parsed.get("error") or {}
            if isinstance(error, dict):
                return {
                    "code": str(error.get("code", "")),
                    "message": str(error.get("message", "")),
                    "raw": parsed,
                }
        if "code" in parsed or "msg" in parsed or "message" in parsed:
            return {
                "code": str(parsed.get("code", "")),
                "message": str(parsed.get("msg") or parsed.get("message") or ""),
                "raw": parsed,
            }
    return None


def decrypt_success_payload(parsed: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    encrypted_data = parsed.get("data")
    if not isinstance(encrypted_data, str) or not encrypted_data:
        raise SpeedafLookupError("response_data_missing")
    decrypted_text = des_decrypt_base64(encrypted_data, cfg["secret_key"])
    try:
        decrypted_json = json.loads(decrypted_text)
    except json.JSONDecodeError as exc:
        raise SpeedafLookupError("response_json_decode_failed") from exc

    normalized_decrypted = decrypted_json
    if isinstance(decrypted_json, list):
        normalized_decrypted = {"data": decrypted_json}

    return {
        "response_wrapper": parsed,
        "decrypted_response": normalized_decrypted,
        "decrypted_raw": decrypted_json,
        "decrypted_text": decrypted_text,
    }


def parse_response_text(response_text: str, cfg: dict[str, Any]) -> dict[str, Any]:
    response_text = response_text.strip()
    if not response_text:
        return {"kind": "empty", "raw": ""}

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        return {"kind": "raw", "raw": response_text}

    err = response_error_payload(parsed)
    if err:
        return {"kind": "error", "error": err, "raw": parsed}

    if isinstance(parsed, dict) and parsed.get("success") is True:
        return {"kind": "success", "data": decrypt_success_payload(parsed, cfg), "raw": parsed}

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), str):
        try:
            return {"kind": "success", "data": decrypt_success_payload(parsed, cfg), "raw": parsed}
        except Exception as exc:
            return {"kind": "decrypt_error", "error": {"code": "decrypt_failed", "message": str(exc)}, "raw": parsed}

    return {"kind": "raw_json", "raw": parsed}


def result(*, ok: bool, action: str, data: Any, http_status: int | None = None, error: str | None = None, error_code: str | None = None, error_message: str | None = None, layer: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    out = {
        "ok": ok,
        "source": "speedaf-api",
        "action": action,
        "data": data,
        "meta": {
            "http_status": http_status,
            "timestamp": now_ms(),
        },
    }
    if dry_run:
        out["dry_run"] = True
    if error:
        out["error"] = error
    if error_code:
        out["error_code"] = error_code
    if error_message:
        out["error_message"] = error_message
    if layer:
        out["layer"] = layer
    return out


def endpoint_url(cfg: dict[str, Any], path: str, timestamp: str) -> str:
    query = urllib.parse.urlencode({"appCode": cfg["app_code"], "timestamp": timestamp})
    return f"{cfg['base_url']}{path}?{query}"


def track_query(tracking_no: str, *, dry_run: bool = False, debug: bool = False) -> dict[str, Any]:
    cfg = env_config()
    timestamp = str(now_ms())
    
    # New plain JSON string payload wrapped in data as per documentation
    business_payload_str = canonical_json({
        "waybillCode": tracking_no,
        "callerID": "123456"
    })
    
    body_json = canonical_json({
        "data": business_payload_str
    })
    
    url = endpoint_url(cfg, "/order/query", timestamp)

    if dry_run:
        return result(
            ok=True,
            action="tracking_lookup",
            data={"kind": "dry_run", "tracking_no": tracking_no},
        )

    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as response:
            res_body = response.read().decode("utf-8")
            
            try:
                parsed = json.loads(res_body)
            except json.JSONDecodeError:
                return result(
                    ok=False,
                    action="tracking_lookup",
                    data={"kind": "error", "raw": res_body},
                    error="bad_response",
                    error_message="Invalid JSON response",
                    layer="response_parse",
                )

            # Check new success format
            if parsed.get("success") is False:
                err = parsed.get("error", {})
                return result(
                    ok=False,
                    action="tracking_lookup",
                    data={"kind": "error", "error": err, "raw": parsed},
                    error="api_error",
                    error_code=err.get("code"),
                    error_message=err.get("message"),
                    layer="protocol",
                )
            elif parsed.get("success") is True:
                return result(
                    ok=True,
                    action="tracking_lookup",
                    data={"kind": "success", "raw": parsed},
                )
            else:
                return result(
                    ok=False,
                    action="tracking_lookup",
                    data={"kind": "error", "raw": parsed},
                    error="unknown_response_format",
                    layer="response_parse",
                )


    except urllib.error.HTTPError as exc:
        try:
            res_body = exc.read().decode("utf-8")
            parsed = json.loads(res_body)
        except Exception:
            parsed = {"status": exc.code, "error": exc.reason, "raw": res_body if 'res_body' in locals() else None}
        return result(
            ok=False,
            action="tracking_lookup",
            data={"kind": "error", "raw": parsed},
            http_status=exc.code,
            error="http_error",
            layer="network",
        )
    except urllib.error.URLError as exc:
        return result(
            ok=False,
            action="tracking_lookup",
            data={},
            http_status=None,
            error=f"network_error:{exc.reason}",
            layer="network",
        )

def update_address(tracking_no: str, whatsapp_phone: str, caller_id: str, *, dry_run: bool = False, debug: bool = False) -> dict[str, Any]:
    cfg = env_config()
    timestamp = str(now_ms())
    
    business_payload_str = canonical_json({
        "waybillCode": tracking_no,
        "whatsAppPhone": whatsapp_phone,
        "callerID": caller_id
    })
    
    body_json = canonical_json({
        "data": business_payload_str
    })
    
    url = endpoint_url(cfg, "/order/updateAddress", timestamp)

    if dry_run:
        return result(
            ok=True,
            action="update_address",
            data={"kind": "dry_run", "tracking_no": tracking_no},
        )

    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10.0) as response:
            res_body = response.read().decode("utf-8")
            
            try:
                parsed = json.loads(res_body)
            except json.JSONDecodeError:
                return result(
                    ok=False,
                    action="update_address",
                    data={"kind": "error", "raw": res_body},
                    error="bad_response",
                    error_message="Invalid JSON response",
                    layer="response_parse",
                )

            if parsed.get("success") is False:
                err = parsed.get("error", {})
                return result(
                    ok=False,
                    action="update_address",
                    data={"kind": "error", "error": err, "raw": parsed},
                    error="api_error",
                    error_code=err.get("code"),
                    error_message=err.get("message"),
                    layer="protocol",
                )
            elif parsed.get("success") is True:
                return result(
                    ok=True,
                    action="update_address",
                    data={"kind": "success", "raw": parsed},
                )
            else:
                return result(
                    ok=False,
                    action="update_address",
                    data={"kind": "error", "raw": parsed},
                    error="unknown_response_format",
                    layer="response_parse",
                )
    except urllib.error.HTTPError as exc:
        try:
            res_body = exc.read().decode("utf-8")
            parsed = json.loads(res_body)
        except Exception:
            parsed = {"status": exc.code, "error": exc.reason, "raw": res_body if 'res_body' in locals() else None}
        return result(
            ok=False,
            action="update_address",
            data={"kind": "error", "raw": parsed},
            http_status=exc.code,
            error="http_error",
            layer="network",
        )
    except urllib.error.URLError as exc:
        return result(
            ok=False,
            action="update_address",
            data={},
            http_status=None,
            error=f"network_error:{exc.reason}",
            layer="network",
        )



import logging

# Configure basic audit logger
audit_logger = logging.getLogger("speedaf_audit")
audit_logger.setLevel(logging.INFO)
# Avoid adding multiple handlers if re-imported
if not audit_logger.handlers:
    fh = logging.FileHandler("/tmp/openclaw/speedaf_audit.log")
    fh.setFormatter(logging.Formatter('{"time": "%(asctime)s", "level": "%(levelname)s", "message": %(message)s}'))
    audit_logger.addHandler(fh)

def _call_speedaf_api(path: str, business_payload: dict[str, Any], action_name: str, dry_run: bool) -> dict[str, Any]:
    cfg = env_config()
    timestamp = str(now_ms())
    business_payload_str = canonical_json(business_payload)
    body_json = canonical_json({"data": business_payload_str})
    url = endpoint_url(cfg, path, timestamp)

    # Log destructive operations
    if action_name in ("cancel_order", "create_work_order", "update_address"):
        safe_payload = {**business_payload}
        audit_logger.info(json.dumps({"action": action_name, "payload": safe_payload}))

    if dry_run:
        return result(ok=True, action=action_name, data={"kind": "dry_run", "payload": business_payload})

    req = urllib.request.Request(
        url,
        data=body_json.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=10.0) as response:
                res_body = response.read().decode("utf-8")
                try:
                    parsed = json.loads(res_body)
                except json.JSONDecodeError:
                    return result(ok=False, action=action_name, data={"kind": "error", "raw": res_body}, error="bad_response", error_message="Invalid JSON response", layer="response_parse")

                if parsed.get("success") is False:
                    err = parsed.get("error", {})
                    return result(ok=False, action=action_name, data={"kind": "error", "error": err, "raw": parsed}, error="api_error", error_code=err.get("code"), error_message=err.get("message"), layer="protocol")
                elif parsed.get("success") is True:
                    return result(ok=True, action=action_name, data={"kind": "success", "raw": parsed})
                else:
                    return result(ok=False, action=action_name, data={"kind": "error", "raw": parsed}, error="unknown_response_format", layer="response_parse")
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 and attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            try:
                res_body = exc.read().decode("utf-8")
                parsed = json.loads(res_body)
            except Exception:
                parsed = {"status": exc.code, "error": exc.reason, "raw": res_body if 'res_body' in locals() else None}
            return result(ok=False, action=action_name, data={"kind": "error", "raw": parsed}, http_status=exc.code, error="http_error", layer="network")
        except urllib.error.URLError as exc:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return result(ok=False, action=action_name, data={}, http_status=None, error=f"network_error:{exc.reason}", layer="network")
    
    return result(ok=False, action=action_name, data={}, http_status=None, error="max_retries_exceeded", layer="network")

def cancel_order(tracking_no: str, reason_code: str, caller_id: str, *, dry_run: bool = False, debug: bool = False) -> dict[str, Any]:
    return _call_speedaf_api("/order/cancel", {"waybillCode": tracking_no, "reasonCode": reason_code, "callerID": caller_id}, "cancel_order", dry_run)

def create_work_order(tracking_no: str, work_order_type: str, description: str, caller_id: str, *, dry_run: bool = False, debug: bool = False) -> dict[str, Any]:
    return _call_speedaf_api("/workOrder/create", {"waybillCode": tracking_no, "workOrderType": work_order_type, "description": description, "callerID": caller_id}, "create_work_order", dry_run)

def query_waybills(caller_id: str, country_code: str, *, dry_run: bool = False, debug: bool = False) -> dict[str, Any]:
    return _call_speedaf_api("/order/waybillCode/query", {"callerID": caller_id, "countryCode": country_code}, "query_waybills", dry_run)

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(track_query(sys.argv[1]), indent=2, ensure_ascii=False))
