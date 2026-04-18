from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .utils.time import format_utc


class TenantAPIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('*', when_used='json', check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class TenantRead(TenantAPIModel):
    id: int
    slug: str
    name: str
    status: str


class TenantMembershipRead(TenantAPIModel):
    id: int
    tenant_id: int
    user_id: int
    membership_role: str
    is_default: bool
    is_active: bool


class TenantOptionRead(TenantAPIModel):
    tenant: TenantRead
    membership_role: str
    is_default: bool = False


class TenantCreate(BaseModel):
    slug: str
    name: str
    external_ref: Optional[str] = None


class TenantAIProfileRead(TenantAPIModel):
    id: int
    tenant_id: int
    display_name: str
    brand_name: Optional[str] = None
    role_prompt: Optional[str] = None
    tone_style: Optional[str] = None
    forbidden_claims: list[str] = Field(default_factory=list)
    escalation_policy: Optional[str] = None
    signature_style: Optional[str] = None
    language_policy: Optional[str] = None
    system_prompt_overrides: Optional[str] = None
    system_context: dict[str, Any] = Field(default_factory=dict)
    enable_auto_reply: bool = True
    enable_auto_summary: bool = True
    enable_auto_classification: bool = True
    allowed_actions: list[str] = Field(default_factory=list)
    default_model_key: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TenantAIProfileUpsert(BaseModel):
    display_name: str
    brand_name: Optional[str] = None
    role_prompt: Optional[str] = None
    tone_style: Optional[str] = None
    forbidden_claims: list[str] = Field(default_factory=list)
    escalation_policy: Optional[str] = None
    signature_style: Optional[str] = None
    language_policy: Optional[str] = None
    system_prompt_overrides: Optional[str] = None
    system_context: dict[str, Any] = Field(default_factory=dict)
    enable_auto_reply: bool = True
    enable_auto_summary: bool = True
    enable_auto_classification: bool = True
    allowed_actions: list[str] = Field(default_factory=list)
    default_model_key: Optional[str] = None


class TenantKnowledgeEntryRead(TenantAPIModel):
    id: int
    tenant_id: int
    title: str
    category: str
    content: str
    source_type: str
    source_ref: Optional[str] = None
    priority: int
    is_active: bool
    tags_json: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TenantKnowledgeEntryCreate(BaseModel):
    title: str
    category: str = 'faq'
    content: str
    source_type: str = 'manual'
    source_ref: Optional[str] = None
    priority: int = 100
    is_active: bool = True
    tags_json: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class TenantKnowledgeEntryUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None
    tags_json: Optional[list[str]] = None
    metadata_json: Optional[dict[str, Any]] = None


class TenantContextRead(TenantAPIModel):
    tenant: TenantRead
    membership_role: str
    ai_profile: Optional[TenantAIProfileRead] = None
    knowledge_entry_count: int = 0
