from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import signal
import time
from dataclasses import asdict
from multiprocessing.connection import Connection
from typing import Any

from .contracts import ParserBudget, ParserOutcome

_PARSER_NAME = "nexus-bounded-knowledge-parser"
_PARSER_VERSION = "1"


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _safe_failure(*, reason: str, input_sha256: str, input_bytes: int, elapsed_ms: int, status: str = "failed") -> ParserOutcome:
    return ParserOutcome(
        status=status,
        reason=reason,
        parser_name=_PARSER_NAME,
        parser_version=_PARSER_VERSION,
        input_sha256=input_sha256,
        input_bytes=input_bytes,
        elapsed_ms=elapsed_ms,
    ).validated()


def _apply_resource_limits(budget: ParserBudget) -> None:
    if os.name != "posix":
        return
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_AS, (budget.address_space_bytes, budget.address_space_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (budget.cpu_seconds, budget.cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (budget.max_output_bytes, budget.max_output_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (budget.max_open_files, budget.max_open_files))


def _safe_parse(payload: dict[str, Any], budget: ParserBudget) -> ParserOutcome:
    from app.knowledge_quarantine.policy import classify_prompt_risk
    from app.services.knowledge_document_service import _extract_text

    content = bytes(payload["content"])
    input_hash = _sha256(content)
    started = time.monotonic()
    text = _extract_text(
        file_name=str(payload["file_name"]),
        mime_type=str(payload["mime_type"]),
        content=content,
        max_upload_bytes=budget.max_input_bytes,
    )
    if len(text) > budget.max_extracted_chars:
        raise ValueError("extracted_text_budget_exceeded")
    status, reasons = classify_prompt_risk(text)
    encoded_text = text.encode("utf-8", errors="strict")
    return ParserOutcome(
        status="success",
        reason="knowledge_parser.complete",
        parser_name=_PARSER_NAME,
        parser_version=_PARSER_VERSION,
        input_sha256=input_hash,
        input_bytes=len(content),
        extracted_text_sha256=_sha256(encoded_text),
        extracted_chars=len(text),
        prompt_risk_status=status,
        prompt_risk_reasons=reasons,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    ).validated()


def _test_sleep(payload: dict[str, Any], budget: ParserBudget) -> ParserOutcome:
    time.sleep(max(1.0, budget.wall_seconds * 10))
    content = bytes(payload["content"])
    return _safe_failure(
        reason="knowledge_parser.test_sleep_completed",
        input_sha256=_sha256(content),
        input_bytes=len(content),
        elapsed_ms=0,
    )


def _child_main(
    connection: Connection,
    payload: dict[str, Any],
    budget_data: dict[str, Any],
    entry_name: str,
    allow_test_entry: bool,
) -> None:
    started = time.monotonic()
    content = bytes(payload.get("content") or b"")
    input_hash = _sha256(content)
    try:
        budget = ParserBudget(**budget_data).validated()
        _apply_resource_limits(budget)
        if entry_name == "knowledge_safe_parse":
            outcome = _safe_parse(payload, budget)
        elif entry_name == "test_sleep" and allow_test_entry:
            outcome = _test_sleep(payload, budget)
        else:
            outcome = _safe_failure(
                reason="knowledge_parser.entry_forbidden",
                input_sha256=input_hash,
                input_bytes=len(content),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
    except MemoryError:
        outcome = _safe_failure(
            status="resource_limited",
            reason="knowledge_parser.memory_limit",
            input_sha256=input_hash,
            input_bytes=len(content),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except BaseException as exc:  # subprocess boundary must return bounded evidence
        error_type = type(exc).__name__.lower()
        reason = (
            "knowledge_parser.resource_limit"
            if error_type in {"memoryerror", "recursionerror"}
            else "knowledge_parser.parse_failed"
        )
        outcome = _safe_failure(
            status="resource_limited" if reason.endswith("resource_limit") else "failed",
            reason=reason,
            input_sha256=input_hash,
            input_bytes=len(content),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    try:
        encoded = json.dumps(outcome.as_safe_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > ParserBudget(**budget_data).validated().max_output_bytes:
            encoded = json.dumps(
                _safe_failure(
                    reason="knowledge_parser.output_budget",
                    input_sha256=input_hash,
                    input_bytes=len(content),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                ).as_safe_dict(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        connection.send_bytes(encoded)
    except BaseException:
        pass
    finally:
        connection.close()


def run_isolated_knowledge_inspection(
    *,
    content: bytes,
    file_name: str,
    mime_type: str,
    budget: ParserBudget | None = None,
    entry_name: str = "knowledge_safe_parse",
    allow_test_entry: bool = False,
) -> ParserOutcome:
    resolved_budget = (budget or ParserBudget()).validated()
    raw = bytes(content)
    input_hash = _sha256(raw)
    if not raw or len(raw) > resolved_budget.max_input_bytes:
        return _safe_failure(
            reason="knowledge_parser.input_budget",
            input_sha256=input_hash,
            input_bytes=len(raw),
            elapsed_ms=0,
        )
    resolved_name = str(file_name or "").strip()
    resolved_mime = str(mime_type or "").strip().lower()
    if not resolved_name or len(resolved_name) > 255 or not resolved_mime or len(resolved_mime) > 120:
        return _safe_failure(
            reason="knowledge_parser.input_identity_invalid",
            input_sha256=input_hash,
            input_bytes=len(raw),
            elapsed_ms=0,
        )

    context = mp.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    payload = {
        "content": raw,
        "file_name": resolved_name,
        "mime_type": resolved_mime,
    }
    process = context.Process(
        target=_child_main,
        args=(child, payload, asdict(resolved_budget), entry_name, allow_test_entry),
        daemon=True,
    )
    started = time.monotonic()
    process.start()
    child.close()
    process.join(timeout=resolved_budget.wall_seconds)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2.0)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=2.0)
        parent.close()
        return _safe_failure(
            status="timed_out",
            reason="knowledge_parser.wall_timeout",
            input_sha256=input_hash,
            input_bytes=len(raw),
            elapsed_ms=elapsed_ms,
        )

    try:
        if parent.poll(0.2):
            encoded = parent.recv_bytes(maxlength=resolved_budget.max_output_bytes)
            payload_value = json.loads(encoded.decode("utf-8"))
            if not isinstance(payload_value, dict):
                raise ValueError("outcome_not_object")
            return ParserOutcome(
                status=payload_value.get("status"),
                reason=payload_value.get("reason"),
                parser_name=payload_value.get("parser_name"),
                parser_version=payload_value.get("parser_version"),
                input_sha256=payload_value.get("input_sha256"),
                input_bytes=payload_value.get("input_bytes"),
                extracted_text_sha256=payload_value.get("extracted_text_sha256"),
                extracted_chars=payload_value.get("extracted_chars", 0),
                prompt_risk_status=payload_value.get("prompt_risk_status", "pending"),
                prompt_risk_reasons=tuple(payload_value.get("prompt_risk_reasons") or ()),
                elapsed_ms=payload_value.get("elapsed_ms", elapsed_ms),
            ).validated()
    except (EOFError, OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        pass
    finally:
        parent.close()

    exit_code = process.exitcode
    status = "resource_limited" if exit_code is not None and exit_code < 0 and -exit_code in {
        signal.SIGKILL,
        signal.SIGXCPU,
        signal.SIGSEGV,
    } else "failed"
    return _safe_failure(
        status=status,
        reason="knowledge_parser.child_no_result",
        input_sha256=input_hash,
        input_bytes=len(raw),
        elapsed_ms=elapsed_ms,
    )
