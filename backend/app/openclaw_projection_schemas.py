from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, field_serializer

from .utils.time import format_utc


class OpenClawProjectionAPIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('*', when_used='json', check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class TenantOpenClawProjectionRead(OpenClawProjectionAPIModel):
    id: int
    tenant_id: int
    openclaw_agent_id: str
    agent_name: str
    workspace_dir: str
    deployment_mode: str
    binding_scope: str
    binding_summary: dict | None = None
    identity_sync_status: str
    knowledge_sync_status: str
    identity_preview: Optional[str] = None
    bootstrap_preview: Optional[str] = None
    last_projected_at: Optional[datetime] = None
    last_projection_error: Optional[str] = None
    is_active: bool
