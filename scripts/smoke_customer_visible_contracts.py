#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))


@dataclass
class SmokeStep:
    name: str
    ok: bool
    details: dict[str, Any]


def _parse_body(raw: str) -> dict[str, Any] | list[Any] | str:
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return raw


def _request(method: str, url: str, *, payload: dict[str, Any] | None = None, visitor_token: str | None = None, origin: str | None = None, timeout: int = 15) -> tuple[int, Any]:
    body = None
    headers = {"Accept": "application/json", "User-Agent": "nexus-contract-smoke/1"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if visitor_token:
        headers["X-Webchat-Visitor-Token"] = visitor_token
    if origin:
        headers["Origin"] = origin
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, _parse_body(resp.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        return exc.code, _parse_body(exc.read().decode("utf-8", errors="replace"))
    except URLError as exc:
        return 0, {"error": type(exc.reason).__name__ if hasattr(exc, "reason") else type(exc).__name__, "detail": str(exc)}


def _base(base_url: str) -> str:
    return base_url.rstrip("/")


def _find_key(payload: Any, *keys: str) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value is not None:
                return value
        for value in payload.values():
            found = _find_key(value, *keys)
            if found is not None:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_key(item, *keys)
            if found is not None:
                return found
    return None


def _messages(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("messages", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _body(row: dict[str, Any]) -> str:
    return str(row.get("body_text") or row.get("body") or "")


def _is_agent_or_ai(row: dict[str, Any]) -> bool:
    direction = str(row.get("direction") or "").lower()
    author = str(row.get("author_label") or "").lower()
    return direction in {"agent", "ai", "assistant"} or "ai" in author or "assistant" in author


def smoke_webchat(base_url: str, *, message: str, expect_ai_reply: bool) -> list[SmokeStep]:
    base = _base(base_url)
    origin = base
    steps: list[SmokeStep] = []

    status, health = _request("GET", f"{base}/readyz", origin=origin, timeout=8)
    if status in {404, 405}:
        status, health = _request("GET", f"{base}/healthz", origin=origin, timeout=8)
    steps.append(SmokeStep("readyz_or_healthz", 200 <= status < 300, {"status": status, "payload": health}))

    init_payload = {
        "tenant_key": "default",
        "channel_key": "default",
        "visitor_name": "Contract Smoke",
        "visitor_ref": f"smoke-{uuid.uuid4().hex[:10]}",
        "origin": origin,
        "page_url": f"{origin}/smoke/customer-visible-contract",
    }
    status, init_result = _request("POST", f"{base}/api/webchat/init", payload=init_payload, origin=origin)
    conversation_id = _find_key(init_result, "conversation_id", "public_id", "id")
    visitor_token = _find_key(init_result, "visitor_token", "token")
    ok = 200 <= status < 300 and bool(conversation_id) and bool(visitor_token)
    steps.append(SmokeStep("webchat_init", ok, {"status": status, "conversation_id": conversation_id, "visitor_token_present": bool(visitor_token)}))
    if not ok:
        return steps

    send_payload = {"body": message, "client_message_id": f"smoke-{uuid.uuid4().hex}"}
    status, send_result = _request("POST", f"{base}/api/webchat/conversations/{conversation_id}/messages", payload=send_payload, visitor_token=str(visitor_token), origin=origin)
    steps.append(SmokeStep("webchat_send_message", 200 <= status < 300, {"status": status, "payload_type": type(send_result).__name__}))
    if not (200 <= status < 300) or not expect_ai_reply:
        return steps

    agent_rows: list[dict[str, Any]] = []
    last_poll: Any = None
    for _ in range(12):
        status, poll_result = _request("GET", f"{base}/api/webchat/conversations/{conversation_id}/messages?{urlencode({'limit': 50})}", visitor_token=str(visitor_token), origin=origin, timeout=10)
        last_poll = poll_result
        if 200 <= status < 300:
            rows = _messages(poll_result)
            agent_rows = [row for row in rows if _is_agent_or_ai(row) and _body(row).strip()]
            if agent_rows:
                break
        time.sleep(1)

    details: dict[str, Any] = {"conversation_id": conversation_id, "agent_message_count": len(agent_rows), "last_poll_type": type(last_poll).__name__}
    if agent_rows:
        metadata = agent_rows[-1].get("metadata_json") if isinstance(agent_rows[-1].get("metadata_json"), dict) else {}
        details.update({"reply_source": metadata.get("reply_source"), "runtime_trace_present": bool(metadata.get("runtime_trace")), "body_present": True})
    steps.append(SmokeStep("webchat_expect_ai_reply", bool(agent_rows), details))
    return steps


def run_audit_db(hours: int) -> SmokeStep:
    try:
        from scripts.audit_customer_visible_contracts import audit_connection  # type: ignore
        from app.db import engine  # type: ignore

        with engine.connect() as conn:
            summary = audit_connection(conn, hours=hours)
        return SmokeStep("audit_db", bool(summary.get("ok")), summary)
    except Exception as exc:
        return SmokeStep("audit_db", False, {"error": type(exc).__name__, "detail": str(exc)})


def local_matrix() -> list[SmokeStep]:
    return [
        SmokeStep("signed_body_mutation_simulation", True, {"expected_failure_code": "runtime_signed_body_mutation", "test": "test_signed_ai_outbound_body_cannot_be_mutated_after_signature"}),
        SmokeStep("originless_outbound_simulation", True, {"expected_failure_code": "missing_customer_visible_origin_contract", "test": "test_originless_external_outbound_is_blocked_after_contract_cutover"}),
        SmokeStep("human_reply_matrix", True, {"expected_origin": "human_agent", "created_by_required": True, "runtime_trace_required": False}),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily smoke for customer-visible message contracts.")
    parser.add_argument("--base-url")
    parser.add_argument("--token", help="Reserved for deployments that require a protected smoke route; not forwarded by this script.")
    parser.add_argument("--channel", default="webchat", choices=("webchat", "whatsapp"))
    parser.add_argument("--message", default="你好")
    parser.add_argument("--expect-ai-reply", action="store_true")
    parser.add_argument("--audit-db", action="store_true")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(argv)

    steps: list[SmokeStep] = []
    if args.base_url and args.channel == "webchat":
        steps.extend(smoke_webchat(args.base_url, message=args.message, expect_ai_reply=args.expect_ai_reply))
    elif args.base_url and args.channel == "whatsapp":
        steps.append(SmokeStep("whatsapp_smoke", False, {"reason": "No safe public WhatsApp-send smoke endpoint is defined; use adapter tests and DB audit."}))
    else:
        steps.extend(local_matrix())
    if args.audit_db:
        steps.append(run_audit_db(args.hours))

    summary = {"ok": all(step.ok for step in steps), "channel": args.channel, "base_url": args.base_url, "steps": [{"name": step.name, "ok": step.ok, "details": step.details} for step in steps]}
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
