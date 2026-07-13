from __future__ import annotations

import base64
import binascii
import json
import os
import re
import socket
import sys

from fastapi import HTTPException

from .parser_boundary import PARSER_IDENTITY, PARSER_VERSION

_PROTOCOL_SCHEMA = "nexus.knowledge.parser-boundary.v1"
_REASON_RE = re.compile(r"[^a-z0-9_.:-]+")


class _NetworkDeniedSocket(socket.socket):
    def __new__(cls, *_args, **_kwargs):  # noqa: ANN204
        raise RuntimeError("parser.network_denied")


def _deny_network() -> None:
    socket.socket = _NetworkDeniedSocket  # type: ignore[assignment]
    socket.create_connection = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[assignment]
        RuntimeError("parser.network_denied")
    )


def _safe_reason(value: object, *, fallback: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    text = _REASON_RE.sub("_", text).strip("_")[:120]
    return text or fallback


def _response(
    *,
    status: str,
    reason_code: str,
    body: str | None = None,
    normalized_text: str | None = None,
    safe_findings: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema": _PROTOCOL_SCHEMA,
        "status": status,
        "parser_identity": PARSER_IDENTITY,
        "parser_version": PARSER_VERSION,
        "reason_code": _safe_reason(reason_code, fallback="parser.failed"),
        "body": body if status == "passed" else None,
        "normalized_text": normalized_text if status == "passed" else None,
        "safe_findings": safe_findings or {},
    }


def _parse_request() -> dict[str, object]:
    maximum = int(os.getenv("NEXUS_KNOWLEDGE_PARSER_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024)))
    raw = sys.stdin.buffer.read(maximum * 4 + 1)
    if not raw or len(raw) > maximum * 4:
        raise ValueError("parser.request_budget_exceeded")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != _PROTOCOL_SCHEMA:
        raise ValueError("parser.protocol_mismatch")
    return payload


def _run(payload: dict[str, object]) -> dict[str, object]:
    encoded = payload.get("content_base64")
    filename = str(payload.get("filename") or "upload.bin")[:255]
    mime_type = str(payload.get("mime_type") or "application/octet-stream")[:120]
    max_input = int(payload.get("max_input_bytes") or 0)
    max_output = int(payload.get("max_output_bytes") or 0)
    if not isinstance(encoded, str) or max_input <= 0 or max_output <= 0:
        return _response(status="failed", reason_code="parser.request_invalid")
    try:
        content = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return _response(status="failed", reason_code="parser.base64_invalid")
    if not content or len(content) > max_input:
        return _response(
            status="resource_exceeded",
            reason_code="parser.input_budget_exceeded",
            safe_findings={"input_bytes": len(content)},
        )

    _deny_network()
    from app.services.knowledge_document_service import parse_document_bytes

    try:
        body, normalized = parse_document_bytes(
            content=content,
            filename=filename,
            mime_type=mime_type,
        )
    except HTTPException as exc:
        return _response(
            status="failed",
            reason_code=_safe_reason(exc.detail, fallback="parser.document_rejected"),
            safe_findings={"http_status": int(exc.status_code)},
        )
    except (MemoryError, OverflowError, RecursionError):
        return _response(status="resource_exceeded", reason_code="parser.runtime_resource_exceeded")
    except Exception:
        return _response(status="failed", reason_code="parser.unhandled_failure")

    if not body.strip() or not normalized.strip():
        return _response(status="failed", reason_code="parser.empty_document")
    response = _response(
        status="passed",
        reason_code="parser.passed",
        body=body,
        normalized_text=normalized,
        safe_findings={
            "input_bytes": len(content),
            "body_chars": len(body),
            "normalized_chars": len(normalized),
        },
    )
    encoded_response = json.dumps(response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded_response) > max_output:
        return _response(
            status="resource_exceeded",
            reason_code="parser.output_budget_exceeded",
            safe_findings={"output_bytes": len(encoded_response)},
        )
    return response


def main() -> int:
    try:
        payload = _parse_request()
        result = _run(payload)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        result = _response(
            status="failed",
            reason_code=_safe_reason(exc, fallback="parser.request_invalid"),
        )
    except Exception:
        result = _response(status="failed", reason_code="parser.worker_failure")
    output = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    maximum = int(os.getenv("NEXUS_KNOWLEDGE_PARSER_MAX_OUTPUT_BYTES", str(4 * 1024 * 1024)))
    if len(output) > maximum:
        output = json.dumps(
            _response(status="resource_exceeded", reason_code="parser.output_budget_exceeded"),
            separators=(",", ":"),
        ).encode("utf-8")
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
