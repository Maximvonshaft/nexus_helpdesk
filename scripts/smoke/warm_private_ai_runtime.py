#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import statistics
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _read_token() -> str:
    token_file = _env("PRIVATE_AI_RUNTIME_TOKEN_FILE")
    inline = _env("PRIVATE_AI_RUNTIME_TOKEN")
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return inline


def _safe_host(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or "unknown"


def _post_json(endpoint: str, token: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=float(_env("PRIVATE_AI_RUNTIME_WARMUP_TIMEOUT_SECONDS", "30"))) as resp:
        body = resp.read().decode("utf-8", errors="replace")
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
        "prompt_eval_duration_ms": int((payload.get("prompt_eval_duration") or 0) / 1_000_000),
        "eval_count": payload.get("eval_count"),
        "eval_duration_ms": int((payload.get("eval_duration") or 0) / 1_000_000),
    }


def main() -> int:
    base_url = _env("PRIVATE_AI_RUNTIME_BASE_URL").rstrip("/")
    token = _read_token()
    if not base_url or not token:
        raise SystemExit("private_ai_runtime_warmup_config_missing")
    model = _env("PRIVATE_AI_RUNTIME_DIRECT_MODEL", "qwen2.5:3b")
    direct_path = _env("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/api/chat")
    endpoint = urljoin(f"{base_url}/", direct_path.lstrip("/"))
    keep_alive = _env("PRIVATE_AI_RUNTIME_OLLAMA_KEEP_ALIVE", "24h")
    cases = [
        {
            "name": "short_general",
            "messages": [
                {"role": "system", "content": "Final customer-visible support text only. Same language. For greetings or incomplete messages, ask a broad support question. No tracking details."},
                {"role": "user", "content": "Language: en.\nCustomer: hello\nReply with one short same-language support question. Do not ask for tracking, order, waybill, parcel, shipment, or reference numbers. Text only."},
            ],
            "options": {"temperature": 0.2, "top_p": 0.85, "num_predict": 24, "num_ctx": 1024},
        },
        {
            "name": "trusted_tracking",
            "messages": [
                {"role": "system", "content": "Final customer-visible tracking answer only. Same language. Use only trusted evidence. Never reveal full tracking number or invent status."},
                {"role": "user", "content": "Trusted tracking answer. Text only.\nLanguage: en.\nCustomer: can you please check my parcel parcel ending 129135\nTrusted facts:\n- Tracking reference: parcel ending 129135\n- Current status: pending pickup\n- Status meaning: pending pickup - Order created and waiting for pickup.\nRules: use only facts; include safe reference, status, and meaning; never reveal/reconstruct full number or ask for it again; one concise sentence."},
            ],
            "options": {"temperature": 0.2, "top_p": 0.85, "num_predict": 64, "num_ctx": 1024},
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
                "elapsed_ms": {"min": min(elapsed), "median": int(statistics.median(elapsed)), "max": max(elapsed)},
                "last_usage": usages[-1],
            }
        )
    print(json.dumps({"ok": True, "runtime_host": _safe_host(base_url), "model": model, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
