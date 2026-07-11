#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.provider_runtime.runtime_capabilities import (  # noqa: E402
    CapabilityExpectationError,
    build_capability_url,
    load_capability_expectations_from_env,
    probe_private_ai_runtime_capabilities,
)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _read_token_file() -> str:
    token_file = _env("PRIVATE_AI_RUNTIME_TOKEN_FILE")
    if not token_file:
        raise SystemExit("private_ai_runtime_token_file_missing")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    if not token:
        raise SystemExit("private_ai_runtime_token_file_empty")
    return token


def _post_json(endpoint: str, token: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(
        request,
        timeout=float(_env("PRIVATE_AI_RUNTIME_WARMUP_TIMEOUT_SECONDS", "30")),
    ) as response:
        body = response.read().decode("utf-8", errors="replace")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    decoded = json.loads(body)
    if not isinstance(decoded, dict):
        raise RuntimeError("runtime_warmup_payload_not_object")
    return elapsed_ms, decoded


def _usage(payload: dict) -> dict:
    return {
        "total_duration_ms": int((payload.get("total_duration") or 0) / 1_000_000),
        "load_duration_ms": int((payload.get("load_duration") or 0) / 1_000_000),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "prompt_eval_duration_ms": int(
            (payload.get("prompt_eval_duration") or 0) / 1_000_000
        ),
        "eval_count": payload.get("eval_count"),
        "eval_duration_ms": int((payload.get("eval_duration") or 0) / 1_000_000),
    }


def main() -> int:
    base_url = _env("PRIVATE_AI_RUNTIME_BASE_URL")
    token_file = _env("PRIVATE_AI_RUNTIME_TOKEN_FILE")
    if not base_url or not token_file:
        raise SystemExit("private_ai_runtime_warmup_config_missing")
    try:
        expectations = load_capability_expectations_from_env()
    except CapabilityExpectationError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "capability": {
                        "status": "not_ready",
                        "reason_codes": [exc.reason_code],
                    },
                },
                sort_keys=True,
            )
        )
        return 1

    capability = probe_private_ai_runtime_capabilities(
        base_url=base_url,
        capabilities_path=_env(
            "PRIVATE_AI_RUNTIME_CAPABILITIES_PATH",
            "/v1/capabilities",
        ),
        token_file=token_file,
        expectations=expectations,
        timeout_seconds=float(
            _env("PRIVATE_AI_RUNTIME_CAPABILITY_TIMEOUT_SECONDS", "2")
        ),
    )
    if not capability.ready:
        print(
            json.dumps(
                {"ok": False, "capability": capability.safe_summary()},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    token = _read_token_file()
    model = expectations.generation_model
    direct_path = _env("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/api/chat")
    endpoint = build_capability_url(base_url, direct_path)
    keep_alive = _env("PRIVATE_AI_RUNTIME_OLLAMA_KEEP_ALIVE", "24h")
    cases = [
        {
            "name": "short_general",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Final customer-visible support text only. Same language. "
                        "For greetings or incomplete messages, ask a broad support "
                        "question. No tracking details."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Language: en.\nCustomer: hello\nReply with one short "
                        "same-language support question. Do not ask for tracking, "
                        "order, waybill, parcel, shipment, or reference numbers. "
                        "Text only."
                    ),
                },
            ],
            "options": {
                "temperature": 0.2,
                "top_p": 0.85,
                "num_predict": 24,
                "num_ctx": 1024,
            },
        },
        {
            "name": "trusted_tracking",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Final customer-visible tracking answer only. Same language. "
                        "Use only trusted evidence. Never reveal full tracking number "
                        "or invent status."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Trusted tracking answer. Text only.\nLanguage: en.\n"
                        "Customer: can you please check my parcel parcel ending "
                        "129135\nTrusted facts:\n- Tracking reference: parcel ending "
                        "129135\n- Current status: pending pickup\n- Status meaning: "
                        "pending pickup - Order created and waiting for pickup.\n"
                        "Rules: use only facts; include safe reference, status, and "
                        "meaning; never reveal/reconstruct full number or ask for it "
                        "again; one concise sentence."
                    ),
                },
            ],
            "options": {
                "temperature": 0.2,
                "top_p": 0.85,
                "num_predict": 64,
                "num_ctx": 1024,
            },
        },
    ]
    repeat = max(1, int(_env("PRIVATE_AI_RUNTIME_WARMUP_REPEAT", "2")))
    results = []
    for case in cases:
        elapsed = []
        usages = []
        for _ in range(repeat):
            payload = {
                "model": model,
                "messages": case["messages"],
                "stream": False,
                "options": case["options"],
                "keep_alive": keep_alive,
            }
            elapsed_ms, response = _post_json(endpoint, token, payload)
            elapsed.append(elapsed_ms)
            usages.append(_usage(response))
            time.sleep(0.1)
        results.append(
            {
                "case": case["name"],
                "elapsed_ms": {
                    "min": min(elapsed),
                    "median": int(statistics.median(elapsed)),
                    "max": max(elapsed),
                },
                "last_usage": usages[-1],
            }
        )
    print(
        json.dumps(
            {
                "ok": True,
                "capability": capability.safe_summary(),
                "model": model,
                "results": results,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
