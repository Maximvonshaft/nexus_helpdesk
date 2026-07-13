from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PARSER_IDENTITY = "nexus.knowledge_document_parser"
PARSER_VERSION = "v1"
_PROTOCOL_SCHEMA = "nexus.knowledge.parser-boundary.v1"


@dataclass(frozen=True)
class ParserBoundaryConfig:
    wall_timeout_seconds: float = 8.0
    cpu_seconds: int = 5
    address_space_bytes: int = 512 * 1024 * 1024
    file_size_bytes: int = 16 * 1024 * 1024
    open_files: int = 32
    max_input_bytes: int = 10 * 1024 * 1024
    max_output_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class ParserBoundaryResult:
    status: str
    parser_identity: str
    parser_version: str
    body: str | None = None
    normalized_text: str | None = None
    reason_code: str = "parser.unknown"
    safe_findings: dict[str, object] | None = None


class ParserBoundaryError(RuntimeError):
    pass


def _resource_limiter(config: ParserBoundaryConfig) -> Callable[[], None] | None:
    if os.name != "posix":
        return None

    def apply_limits() -> None:
        import resource

        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (config.cpu_seconds, config.cpu_seconds))
        resource.setrlimit(
            resource.RLIMIT_AS,
            (config.address_space_bytes, config.address_space_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (config.file_size_bytes, config.file_size_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (config.open_files, config.open_files))
        if hasattr(resource, "RLIMIT_CORE"):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return apply_limits


def _sanitized_environment(config: ParserBoundaryConfig) -> dict[str, str]:
    backend_root = str(Path(__file__).resolve().parents[2])
    current_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(part for part in (backend_root, current_pythonpath) if part)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": pythonpath,
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "APP_ENV": "test",
        "DATABASE_URL": "sqlite://",
        "AUTO_INIT_DB": "false",
        "SEED_DEMO_DATA": "false",
        "STORAGE_BACKEND": "local",
        "MAX_UPLOAD_BYTES": str(config.max_input_bytes),
        "NEXUS_KNOWLEDGE_PARSER_MAX_OUTPUT_BYTES": str(config.max_output_bytes),
    }
    for key in ("LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    finally:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


def parse_document_in_boundary(
    *,
    content: bytes,
    filename: str | None,
    mime_type: str | None,
    config: ParserBoundaryConfig | None = None,
) -> ParserBoundaryResult:
    resolved = config or ParserBoundaryConfig()
    raw = bytes(content)
    if not raw or len(raw) > resolved.max_input_bytes:
        return ParserBoundaryResult(
            status="resource_exceeded",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.input_budget_exceeded",
            safe_findings={"input_bytes": len(raw)},
        )

    request = json.dumps(
        {
            "schema": _PROTOCOL_SCHEMA,
            "filename": str(filename or "upload.bin")[:255],
            "mime_type": str(mime_type or "application/octet-stream")[:120],
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "max_input_bytes": resolved.max_input_bytes,
            "max_output_bytes": resolved.max_output_bytes,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    process = subprocess.Popen(
        [sys.executable, "-I", "-m", "app.knowledge_quarantine.parser_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_sanitized_environment(resolved),
        close_fds=True,
        start_new_session=False,
        preexec_fn=_resource_limiter(resolved),
    )
    try:
        stdout, _stderr = process.communicate(
            input=request,
            timeout=max(0.1, float(resolved.wall_timeout_seconds)),
        )
    except subprocess.TimeoutExpired:
        _terminate(process)
        return ParserBoundaryResult(
            status="timed_out",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.wall_timeout",
            safe_findings={"wall_timeout_seconds": resolved.wall_timeout_seconds},
        )

    if len(stdout) > resolved.max_output_bytes:
        return ParserBoundaryResult(
            status="resource_exceeded",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.output_budget_exceeded",
            safe_findings={"output_bytes": len(stdout)},
        )
    if process.returncode is None:
        _terminate(process)
        return ParserBoundaryResult(
            status="failed",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.missing_exit_status",
        )
    if process.returncode < 0:
        signal_number = abs(process.returncode)
        status = "resource_exceeded" if signal_number in {signal.SIGKILL, signal.SIGXCPU, signal.SIGXFSZ} else "failed"
        return ParserBoundaryResult(
            status=status,
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.resource_signal" if status == "resource_exceeded" else "parser.process_signal",
            safe_findings={"signal": signal_number},
        )
    if not stdout:
        return ParserBoundaryResult(
            status="failed",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.empty_response",
            safe_findings={"exit_code": process.returncode},
        )

    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ParserBoundaryResult(
            status="failed",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.invalid_response",
            safe_findings={"exit_code": process.returncode},
        )
    if not isinstance(payload, dict) or payload.get("schema") != _PROTOCOL_SCHEMA:
        return ParserBoundaryResult(
            status="failed",
            parser_identity=PARSER_IDENTITY,
            parser_version=PARSER_VERSION,
            reason_code="parser.protocol_mismatch",
        )

    status = str(payload.get("status") or "failed")
    if status not in {"passed", "failed", "resource_exceeded"}:
        status = "failed"
    body = payload.get("body") if status == "passed" else None
    normalized = payload.get("normalized_text") if status == "passed" else None
    if body is not None and not isinstance(body, str):
        status, body, normalized = "failed", None, None
    if normalized is not None and not isinstance(normalized, str):
        status, body, normalized = "failed", None, None
    safe_findings = payload.get("safe_findings")
    if not isinstance(safe_findings, dict):
        safe_findings = {}
    safe_findings = {
        str(key)[:80]: value
        for key, value in list(safe_findings.items())[:16]
        if value is None or isinstance(value, (bool, int, float, str))
    }
    safe_findings["exit_code"] = process.returncode
    return ParserBoundaryResult(
        status=status,
        parser_identity=PARSER_IDENTITY,
        parser_version=PARSER_VERSION,
        body=body,
        normalized_text=normalized,
        reason_code=str(payload.get("reason_code") or "parser.failed")[:120],
        safe_findings=safe_findings,
    )
