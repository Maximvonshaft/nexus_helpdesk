from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


CONFIRM_VALUE = "I_UNDERSTAND_THIS_SENDS_REAL_EMAIL"


def _env(name: str, *, required: bool = False) -> str:
    value = os.getenv(name, "").strip()
    if required and not value:
        raise SystemExit(f"{name} is required")
    return value


def _json_request(base_url: str, path: str, *, method: str = "GET", token: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any] | list[Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(urljoin(base_url + "/", path.lstrip("/")), data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=35) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise SystemExit(f"HTTP {exc.code} from {path}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"Request to {path} failed: {exc.reason}") from exc


def _login(base_url: str) -> str:
    token = _env("NEXUS_ADMIN_TOKEN")
    if token:
        return token
    username = _env("NEXUS_ADMIN_USERNAME", required=True)
    password = _env("NEXUS_ADMIN_PASSWORD", required=True)
    data = _json_request(base_url, "/api/auth/login", method="POST", payload={"username": username, "password": password})
    if not isinstance(data, dict) or not data.get("access_token"):
        raise SystemExit("Login did not return access_token")
    return str(data["access_token"])


def _select_account(base_url: str, token: str) -> dict[str, Any]:
    requested_id = _env("OUTBOUND_EMAIL_ACCOUNT_ID")
    accounts = _json_request(base_url, "/api/admin/outbound-email/accounts", token=token)
    if not isinstance(accounts, list):
        raise SystemExit("Outbound Email account list response is not an array")
    if requested_id:
        for account in accounts:
            if str(account.get("id")) == requested_id:
                return account
        raise SystemExit(f"OUTBOUND_EMAIL_ACCOUNT_ID={requested_id} was not found")
    for account in accounts:
        if account.get("is_active") and account.get("password_configured"):
            return account
    raise SystemExit("No active Outbound Email account with configured password is available")


def _mask_email(value: str) -> str:
    if "@" not in value:
        return "***"
    local, domain = value.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def main() -> int:
    if _env("OUTBOUND_EMAIL_TEST_SEND_CONFIRM") != CONFIRM_VALUE:
        raise SystemExit(f"Refusing to send real email. Set OUTBOUND_EMAIL_TEST_SEND_CONFIRM={CONFIRM_VALUE}.")
    base_url = _env("NEXUS_BASE_URL", required=True).rstrip("/")
    to_address = _env("OUTBOUND_EMAIL_TEST_TO", required=True)
    token = _login(base_url)
    account = _select_account(base_url, token)
    account_id = int(account["id"])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "to_address": to_address,
        "subject": f"NexusDesk Outbound Email production pilot gate {now}",
        "body": (
            "This is a controlled NexusDesk Outbound Email production pilot test-send gate. "
            "It verifies SMTP credentials, routing, and audit-safe health updates."
        ),
    }
    result = _json_request(base_url, f"/api/admin/outbound-email/accounts/{account_id}/test-send", method="POST", token=token, payload=payload)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise SystemExit(f"Outbound Email test-send did not succeed: {json.dumps(result, ensure_ascii=False)[:1000]}")
    if result.get("health_status") != "ok":
        raise SystemExit(f"Outbound Email health_status was not ok: {result.get('health_status')}")
    time.sleep(1)
    refreshed = _json_request(base_url, f"/api/admin/outbound-email/accounts/{account_id}", token=token)
    if not isinstance(refreshed, dict):
        raise SystemExit("Refetched Outbound Email account response is not an object")
    if refreshed.get("health_status") != "ok" or refreshed.get("last_test_status") != "success":
        raise SystemExit(
            "Outbound Email account did not persist successful test status: "
            + json.dumps({
                "health_status": refreshed.get("health_status"),
                "last_test_status": refreshed.get("last_test_status"),
                "last_test_at": refreshed.get("last_test_at"),
            }, ensure_ascii=False)
        )
    print(json.dumps({
        "status": "pass",
        "base_url": base_url,
        "account_id": account_id,
        "to_address": _mask_email(to_address),
        "provider_status": result.get("provider_status"),
        "health_status": refreshed.get("health_status"),
        "last_test_status": refreshed.get("last_test_status"),
        "last_test_at": refreshed.get("last_test_at"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
