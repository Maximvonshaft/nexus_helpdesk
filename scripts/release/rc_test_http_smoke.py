#!/usr/bin/env python3
"""Run the isolated RC HTTP journey against the real browser origin."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    merged = {"Accept": "application/json", **(headers or {})}
    if payload is not None:
        merged["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers=merged,
        method=method,
    )
    response_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = dict(response.headers.items())
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        body = exc.read()
    if status not in expected:
        raise RuntimeError(f"{method} {path} returned {status}: {body[:400]!r}")
    content_type = response_headers.get("Content-Type", "")
    if "application/json" in content_type or (body and body[:1] in (b"{", b"[")):
        return status, json.loads(body.decode("utf-8"))
    return status, body.decode("utf-8", errors="replace")


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    origin = args.origin.rstrip("/")
    username = os.environ["RC_TEST_ADMIN_USERNAME"]
    password = os.environ["RC_TEST_ADMIN_PASSWORD"]
    tenant_key = os.getenv("RC_TEST_TENANT_KEY", "rc-test").strip()
    channel_key = os.getenv("RC_TEST_CHANNEL_KEY", "website").strip()

    _, health = _request(base_url, "/healthz")
    _, ready = _request(base_url, "/readyz")
    if health.get("status") != "ok" or ready.get("status") != "ready":
        raise RuntimeError("health/readiness not ready")
    for payload in (health, ready):
        if payload.get("git_sha") != args.source_sha:
            raise RuntimeError("runtime git_sha mismatch")
        if payload.get("frontend_build_sha") != args.source_sha:
            raise RuntimeError("frontend build SHA mismatch")
        if payload.get("image_tag") != args.image_tag:
            raise RuntimeError("image tag mismatch")
    if ready.get("migration_revision") != args.migration_head:
        raise RuntimeError("readiness migration revision does not match exact Alembic head")

    args.evidence_dir.joinpath("healthz.json").write_text(
        json.dumps(health, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.evidence_dir.joinpath("readyz.json").write_text(
        json.dumps(ready, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    status, demo_html = _request(
        base_url,
        "/webchat/demo/",
        headers={"Accept": "text/html"},
    )
    if status != 200 or "data-auto-open=\"true\"" not in demo_html:
        raise RuntimeError("public WebChat demo did not render the canonical widget")

    status, login_html = _request(base_url, "/login", headers={"Accept": "text/html"})
    if status != 200 or "<html" not in login_html.lower():
        raise RuntimeError("login SPA route did not render")

    _request(
        base_url,
        "/api/auth/login",
        method="POST",
        payload={"username": username, "password": "intentionally-wrong-password"},
        expected=(401,),
    )
    _, login = _request(
        base_url,
        "/api/auth/login",
        method="POST",
        payload={"username": username, "password": password},
    )
    admin_token = login.get("access_token")
    if not isinstance(admin_token, str) or len(admin_token) < 20:
        raise RuntimeError("admin login did not return a token")

    web_headers = {"Origin": origin}
    _, initialized = _request(
        base_url,
        "/api/webchat/init",
        method="POST",
        headers=web_headers,
        payload={
            "tenant_key": "default",
            "channel_key": channel_key,
            "visitor_name": "RC HTTP Synthetic Visitor",
            "origin": origin,
            "page_url": origin + "/webchat/demo/",
        },
    )
    conversation_id = initialized.get("conversation_id")
    visitor_token = initialized.get("visitor_token")
    if not isinstance(conversation_id, str) or not conversation_id.startswith("wc_"):
        raise RuntimeError("invalid WebChat conversation id")
    if not isinstance(visitor_token, str) or len(visitor_token) < 20:
        raise RuntimeError("invalid WebChat visitor token")

    message_text = f"RC HTTP synthetic message {args.source_sha[:12]}"
    visitor_headers = {
        "Origin": origin,
        "X-Webchat-Visitor-Token": visitor_token,
    }
    _, sent = _request(
        base_url,
        f"/api/webchat/conversations/{conversation_id}/messages",
        method="POST",
        headers=visitor_headers,
        payload={"body": message_text, "client_message_id": "rc0-http-smoke-1"},
    )
    if not isinstance(sent, dict):
        raise RuntimeError("WebChat send response invalid")
    _, polled = _request(
        base_url,
        f"/api/webchat/conversations/{conversation_id}/messages",
        headers=visitor_headers,
    )
    messages = polled.get("messages") if isinstance(polled, dict) else None
    if not isinstance(messages, list) or not any(
        isinstance(item, dict)
        and item.get("direction") == "visitor"
        and item.get("body") == message_text
        for item in messages
    ):
        raise RuntimeError("visitor message was not persisted")

    _, admin_conversations = _request(
        base_url,
        "/api/webchat/admin/conversations?limit=50",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if not isinstance(admin_conversations, list) or not any(
        isinstance(item, dict) and item.get("conversation_id") == conversation_id
        for item in admin_conversations
    ):
        raise RuntimeError("operator API cannot read the synthetic conversation")

    summary = {
        "schema": "nexus.osr.rc-test-http-smoke.v2",
        "source_sha": args.source_sha,
        "origin": origin,
        "tenant_key": tenant_key,
        "channel_key": channel_key,
        "conversation_id": conversation_id,
        "message_fingerprint": message_text,
        "health": "pass",
        "readiness": "pass",
        "exact_migration_head": "pass",
        "public_demo": "pass",
        "invalid_login_rejected": "pass",
        "operator_login": "pass",
        "webchat_init_send_poll": "pass",
        "operator_conversation_read": "pass",
    }
    args.evidence_dir.joinpath("http-core-smoke.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--image-tag", required=True)
    parser.add_argument("--migration-head", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    run(args)
    print("RC_HTTP_CORE_SMOKE=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
