from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.provider_runtime.runtime_capabilities import (  # noqa: E402
    CapabilityExpectationError,
    build_capability_url,
    load_capability_expectations_from_env,
    probe_private_ai_runtime_capabilities,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capability-gated, secret-safe smoke probe for the private AI Runtime."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--capabilities-path", default="/v1/capabilities")
    parser.add_argument("--direct-path", default="/api/chat")
    parser.add_argument("--rag-path", default="/api/chat")
    parser.add_argument("--live-health-path", default="/live/health")
    parser.add_argument("--tts-path", default="/voice/tts")
    parser.add_argument(
        "--request-shape",
        choices=["question", "system_input", "messages", "ollama_chat"],
        default="ollama_chat",
    )
    parser.add_argument("--tts-language", default="en")
    parser.add_argument("--tts-voice", default="af_heart")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--include-rag", action="store_true")
    parser.add_argument("--include-live-health", action="store_true")
    parser.add_argument("--include-tts", action="store_true")
    args = parser.parse_args()

    try:
        expectations = load_capability_expectations_from_env()
    except CapabilityExpectationError as exc:
        return _finish(
            [
                {
                    "name": "capability_contract",
                    "ok": False,
                    "reason_codes": [exc.reason_code],
                }
            ]
        )

    capability_result = probe_private_ai_runtime_capabilities(
        base_url=args.base_url,
        capabilities_path=args.capabilities_path,
        token_file=args.token_file,
        expectations=expectations,
        timeout_seconds=min(max(args.timeout, 0.1), 30.0),
    )
    checks: list[dict[str, Any]] = [
        {
            "name": "capability_contract",
            "ok": capability_result.ready,
            "evidence": capability_result.safe_summary(),
        }
    ]
    if not capability_result.ready:
        return _finish(checks)

    token = _read_token(args.token_file)
    generation_model = expectations.generation_model
    checks.append(
        _post_chat(
            args.base_url,
            args.direct_path,
            token,
            generation_model,
            args.timeout,
            name="chat_generation",
            request_shape=args.request_shape,
        )
    )
    if args.include_rag:
        checks.append(
            _post_chat(
                args.base_url,
                args.rag_path,
                token,
                generation_model,
                args.timeout,
                name="chat_generation_with_retrieval",
                request_shape=args.request_shape,
            )
        )
    if args.include_live_health:
        checks.append(
            _get_json(
                args.base_url,
                args.live_health_path,
                args.timeout,
                name="live_health",
            )
        )
    if args.include_tts:
        checks.append(
            _post_tts(
                args.base_url,
                args.tts_path,
                token,
                args.timeout,
                language=args.tts_language,
                voice=args.tts_voice,
            )
        )

    return _finish(checks)


def _finish(checks: list[dict[str, Any]]) -> int:
    ok = all(item.get("ok") is True for item in checks)
    print(
        json.dumps(
            {"ok": ok, "checks": checks},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0 if ok else 1


def _read_token(path: str) -> str:
    value = Path(path).read_text(encoding="utf-8").strip()
    if value.lower().startswith("bearer "):
        value = value.split(None, 1)[1].strip()
    if not value:
        raise SystemExit("token file is empty")
    return value


def _post_chat(
    base_url: str,
    path: str,
    token: str,
    model: str,
    timeout: float,
    *,
    name: str,
    request_shape: str,
) -> dict[str, Any]:
    system = (
        "You are a logistics customer support smoke-test runtime. Return JSON only "
        "with customer_reply, language, intent, handoff_required, and "
        "ticket_should_create."
    )
    prompt = (
        "Smoke test only. Reply with a short safe greeting and do not mention "
        "internal systems."
    )
    payload = _chat_payload(
        model=model,
        system=system,
        prompt=prompt,
        request_shape=request_shape,
    )
    try:
        response = _request_json(
            base_url,
            path,
            timeout,
            token=token,
            payload=payload,
        )
    except Exception as exc:
        return {"name": name, "ok": False, "error": _safe_error(exc)}
    reply_text = _extract_reply_text(response)
    return {
        "name": name,
        "ok": bool(reply_text),
        "model": model,
        "request_shape": request_shape,
        "reply_chars": len(reply_text or ""),
        "response_keys": (
            sorted(response.keys())[:12] if isinstance(response, dict) else []
        ),
    }


def _chat_payload(
    *,
    model: str,
    system: str,
    prompt: str,
    request_shape: str,
) -> dict[str, Any]:
    if request_shape == "question":
        return {"model": model, "question": f"{system}\n{prompt}"}
    if request_shape == "messages":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "response_format": "json",
            "metadata": {"smoke": True},
        }
    if request_shape == "ollama_chat":
        return {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
    return {
        "model": model,
        "system": system,
        "input": prompt,
        "language": "en",
        "response_format": "json",
        "metadata": {"smoke": True},
    }


def _post_tts(
    base_url: str,
    path: str,
    token: str,
    timeout: float,
    *,
    language: str,
    voice: str,
) -> dict[str, Any]:
    payload = {
        "text": "Hello, this is a NexusDesk smoke test.",
        "lang": language,
        "voice": voice,
        "format": "wav",
    }
    try:
        body, content_type = _request_bytes(
            base_url,
            path,
            timeout,
            token=token,
            payload=payload,
        )
    except Exception as exc:
        return {"name": "tts", "ok": False, "error": _safe_error(exc)}
    return {
        "name": "tts",
        "ok": bool(body),
        "bytes": len(body),
        "content_type": content_type.split(";")[0],
    }


def _get_json(
    base_url: str,
    path: str,
    timeout: float,
    *,
    name: str,
) -> dict[str, Any]:
    try:
        response = _request_json(
            base_url,
            path,
            timeout,
            token=None,
            payload=None,
        )
    except Exception as exc:
        return {"name": name, "ok": False, "error": _safe_error(exc)}
    return {
        "name": name,
        "ok": isinstance(response, dict),
        "response_keys": (
            sorted(response.keys())[:12] if isinstance(response, dict) else []
        ),
    }


def _request_json(
    base_url: str,
    path: str,
    timeout: float,
    *,
    token: str | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    body, _content_type = _request_bytes(
        base_url,
        path,
        timeout,
        token=token,
        payload=payload,
    )
    decoded = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(decoded, dict):
        raise ValueError("response_not_object")
    return decoded


def _request_bytes(
    base_url: str,
    path: str,
    timeout: float,
    *,
    token: str | None,
    payload: dict[str, Any] | None,
) -> tuple[bytes, str]:
    data = (
        None
        if payload is None
        else json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        build_capability_url(base_url, path),
        data=data,
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get("content-type", "")


def _extract_reply_text(payload: dict[str, Any]) -> str:
    for key in (
        "customer_reply",
        "reply",
        "response_text",
        "text",
        "answer",
        "output_text",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        return message["content"].strip()
    return ""


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, CapabilityExpectationError):
        return exc.reason_code
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "url_error"
    return exc.__class__.__name__


if __name__ == "__main__":
    sys.exit(main())
