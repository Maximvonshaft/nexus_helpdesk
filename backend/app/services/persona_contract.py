from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersonaContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="nexus.persona.v1", pattern=r"^nexus\.persona\.v1$")
    brand_name: str | None = Field(default=None, max_length=160)
    assistant_name: str | None = Field(default=None, max_length=160)
    role_label: str | None = Field(default=None, max_length=200)
    identity_statement: str | None = Field(default=None, max_length=4000)
    identity_answer_rule: str | None = Field(default=None, max_length=4000)
    tone: str | None = Field(default=None, max_length=1000)
    handoff_boundary: str | None = Field(default=None, max_length=4000)
    capabilities: list[str] = Field(default_factory=list, max_length=50)
    guardrails: list[str] = Field(default_factory=list, max_length=80)
    disallowed_identity_claims: list[str] = Field(default_factory=list, max_length=50)

    @field_validator(
        "brand_name",
        "assistant_name",
        "role_label",
        "identity_statement",
        "identity_answer_rule",
        "tone",
        "handoff_boundary",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any):
        if value is None:
            return None
        cleaned = " ".join(str(value).strip().split())
        return cleaned or None

    @field_validator("capabilities", "guardrails", "disallowed_identity_claims", mode="before")
    @classmethod
    def normalize_lists(cls, value: Any):
        if value in (None, ""):
            return []
        if not isinstance(value, (list, tuple, set, frozenset)):
            raise ValueError("persona_list_required")
        result: list[str] = []
        for item in value:
            cleaned = " ".join(str(item or "").strip().split())
            if cleaned and cleaned not in result:
                result.append(cleaned[:1000])
        return result


def validate_persona_content(value: Any) -> dict[str, Any]:
    try:
        parsed = PersonaContent.model_validate(value or {})
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "persona_content_invalid", "message": str(exc)[:1000]},
        ) from exc
    return parsed.model_dump(mode="json", exclude_none=True)
