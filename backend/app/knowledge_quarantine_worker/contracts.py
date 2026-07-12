from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_SAFE_REASON = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ParserBudget:
    max_input_bytes: int = 10 * 1024 * 1024
    max_extracted_chars: int = 200_000
    address_space_bytes: int = 512 * 1024 * 1024
    cpu_seconds: int = 10
    wall_seconds: float = 15.0
    max_output_bytes: int = 16 * 1024
    max_open_files: int = 64

    def validated(self) -> "ParserBudget":
        if isinstance(self.max_input_bytes, bool) or not 1 <= int(self.max_input_bytes) <= 50 * 1024 * 1024:
            raise ValueError("knowledge_parser_max_input_invalid")
        if isinstance(self.max_extracted_chars, bool) or not 1 <= int(self.max_extracted_chars) <= 2_000_000:
            raise ValueError("knowledge_parser_text_budget_invalid")
        if isinstance(self.address_space_bytes, bool) or not 128 * 1024 * 1024 <= int(self.address_space_bytes) <= 2 * 1024 * 1024 * 1024:
            raise ValueError("knowledge_parser_memory_budget_invalid")
        if isinstance(self.cpu_seconds, bool) or not 1 <= int(self.cpu_seconds) <= 120:
            raise ValueError("knowledge_parser_cpu_budget_invalid")
        wall = float(self.wall_seconds)
        if not 0.05 <= wall <= 300.0:
            raise ValueError("knowledge_parser_wall_budget_invalid")
        if isinstance(self.max_output_bytes, bool) or not 1024 <= int(self.max_output_bytes) <= 256 * 1024:
            raise ValueError("knowledge_parser_output_budget_invalid")
        if isinstance(self.max_open_files, bool) or not 16 <= int(self.max_open_files) <= 256:
            raise ValueError("knowledge_parser_open_file_budget_invalid")
        return ParserBudget(
            max_input_bytes=int(self.max_input_bytes),
            max_extracted_chars=int(self.max_extracted_chars),
            address_space_bytes=int(self.address_space_bytes),
            cpu_seconds=int(self.cpu_seconds),
            wall_seconds=wall,
            max_output_bytes=int(self.max_output_bytes),
            max_open_files=int(self.max_open_files),
        )


@dataclass(frozen=True)
class ParserOutcome:
    status: str
    reason: str
    parser_name: str
    parser_version: str
    input_sha256: str
    input_bytes: int
    extracted_text_sha256: str | None = None
    extracted_chars: int = 0
    prompt_risk_status: str = "pending"
    prompt_risk_reasons: tuple[str, ...] = ()
    elapsed_ms: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def validated(self) -> "ParserOutcome":
        if self.status not in {"success", "failed", "timed_out", "resource_limited"}:
            raise ValueError("knowledge_parser_outcome_status_invalid")
        reason = str(self.reason or "").strip().lower()
        if not _SAFE_REASON.fullmatch(reason):
            raise ValueError("knowledge_parser_outcome_reason_invalid")
        parser_name = str(self.parser_name or "").strip()[:80]
        parser_version = str(self.parser_version or "").strip()[:80]
        if not parser_name or not parser_version:
            raise ValueError("knowledge_parser_identity_invalid")
        if not _SHA256.fullmatch(str(self.input_sha256 or "")):
            raise ValueError("knowledge_parser_input_hash_invalid")
        if self.extracted_text_sha256 is not None and not _SHA256.fullmatch(self.extracted_text_sha256):
            raise ValueError("knowledge_parser_text_hash_invalid")
        if self.prompt_risk_status not in {"pending", "clear", "review", "blocked"}:
            raise ValueError("knowledge_parser_prompt_status_invalid")
        reasons = tuple(dict.fromkeys(str(item or "").strip().lower() for item in self.prompt_risk_reasons))
        if any(not _SAFE_REASON.fullmatch(item) for item in reasons):
            raise ValueError("knowledge_parser_prompt_reason_invalid")
        if isinstance(self.input_bytes, bool) or int(self.input_bytes) < 0:
            raise ValueError("knowledge_parser_input_size_invalid")
        if isinstance(self.extracted_chars, bool) or int(self.extracted_chars) < 0:
            raise ValueError("knowledge_parser_text_size_invalid")
        if isinstance(self.elapsed_ms, bool) or int(self.elapsed_ms) < 0:
            raise ValueError("knowledge_parser_elapsed_invalid")
        return ParserOutcome(
            status=self.status,
            reason=reason,
            parser_name=parser_name,
            parser_version=parser_version,
            input_sha256=self.input_sha256,
            input_bytes=int(self.input_bytes),
            extracted_text_sha256=self.extracted_text_sha256,
            extracted_chars=int(self.extracted_chars),
            prompt_risk_status=self.prompt_risk_status,
            prompt_risk_reasons=reasons[:16],
            elapsed_ms=int(self.elapsed_ms),
        )

    def as_safe_dict(self) -> dict[str, Any]:
        value = self.validated()
        return {
            "status": value.status,
            "reason": value.reason,
            "parser_name": value.parser_name,
            "parser_version": value.parser_version,
            "input_sha256": value.input_sha256,
            "input_bytes": value.input_bytes,
            "extracted_text_sha256": value.extracted_text_sha256,
            "extracted_chars": value.extracted_chars,
            "prompt_risk_status": value.prompt_risk_status,
            "prompt_risk_reasons": list(value.prompt_risk_reasons),
            "elapsed_ms": value.elapsed_ms,
        }
