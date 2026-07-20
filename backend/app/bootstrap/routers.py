from __future__ import annotations

from fastapi import Depends, FastAPI

from ..api.admin import router as admin_router
from ..api.admin_identity import router as admin_identity_router
from ..api.admin_identity_policy import enforce_admin_identity_request_policy
from ..api.admin_mfa import router as admin_mfa_router
from ..api.admin_password_policy import enforce_admin_password_request_policy
from ..api.admin_perf import router as admin_perf_router
from ..api.admin_provider_runtime import router as admin_provider_runtime_router
from ..api.admin_queue import router as admin_queue_router
from ..api.admin_tenant_query_scope import enforce_admin_tenant_query_scope
from ..api.admin_whatsapp_native import router as admin_whatsapp_native_router
from ..api.auth import router as auth_router
from ..api.canonical_integration import router as integration_router
from ..api.canonical_osr_admin import router as osr_admin_router
from ..api.channel_control import router as channel_control_router
from ..api.customers import router as customers_router
from ..api.email import router as email_router
from ..api.files import router as files_router
from ..api.knowledge_items import router as knowledge_items_router
from ..api.lite import router as lite_router
from ..api.lookups import router as lookups_router
from ..api.operator_queue import router as operator_queue_router
from ..api.outbound_channels import router as outbound_channels_router
from ..api.persona_profiles import router as persona_profiles_router
from ..api.speedaf_actions import router as speedaf_actions_router
from ..api.speedaf_cancel import router as speedaf_cancel_router
from ..api.stats import router as stats_router
from ..api.support_conversations import router as support_conversations_router
from ..api.support_intelligence import router as support_intelligence_router
from ..api.ticket_closure import router as ticket_closure_router
from ..api.ticket_perf import router as ticket_perf_router
from ..api.tickets import router as tickets_router
from ..api.webchat import router as webchat_router
from ..api.webchat_events import router as webchat_events_router
from ..api.webchat_live_voice import router as webchat_live_voice_router
from ..api.webchat_voice import router as webchat_voice_router
from ..api.webchat_ws import router as webchat_ws_router
from ..api.whatsapp_native_integration import router as whatsapp_native_integration_router


def _compose_admin_dependencies(*, dependencies: list) -> list:
    """Return the one ordered policy chain for the canonical admin router."""

    return [
        Depends(enforce_admin_tenant_query_scope),
        Depends(enforce_admin_identity_request_policy),
        *dependencies,
    ]


def register_api_routers(app: FastAPI) -> None:
    """Register every supported API router exactly once in deterministic order."""

    for router in (
        admin_perf_router,
        admin_identity_router,
        admin_mfa_router,
        admin_provider_runtime_router,
        admin_whatsapp_native_router,
        ticket_perf_router,
    ):
        app.include_router(router)
    app.include_router(
        admin_router,
        dependencies=_compose_admin_dependencies(
            dependencies=[Depends(enforce_admin_password_request_policy)]
        ),
    )
    for router in (
        admin_queue_router,
        osr_admin_router,
        operator_queue_router,
        outbound_channels_router,
        auth_router,
        channel_control_router,
        files_router,
        integration_router,
        knowledge_items_router,
        lookups_router,
        lite_router,
        customers_router,
        email_router,
        persona_profiles_router,
        stats_router,
        tickets_router,
        ticket_closure_router,
        speedaf_actions_router,
        speedaf_cancel_router,
        support_conversations_router,
        support_intelligence_router,
        webchat_events_router,
        webchat_live_voice_router,
        webchat_ws_router,
        webchat_voice_router,
        whatsapp_native_integration_router,
        webchat_router,
    ):
        app.include_router(router)
