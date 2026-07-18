from __future__ import annotations

import os

# The canonical Actions workflow is fail-closed for deployment and image smoke,
# but the complete backend regression must exercise every feature state under
# explicit test control. Remove inherited deployment switches before test
# modules import process-wide settings singletons.
_DEPLOYMENT_FEATURE_FLAGS = (
    "PROVIDER_RUNTIME_ENABLED",
    "PROVIDER_RUNTIME_TRAFFIC_MODE",
    "PROVIDER_RUNTIME_KILL_SWITCH",
    "PROVIDER_RUNTIME_CANARY_PERCENT",
    "PRIVATE_AI_RUNTIME_ENABLED",
    "WEBCHAT_AI_ENABLED",
    "WEBCHAT_AI_AUTO_REPLY_MODE",
    "WEBCHAT_VOICE_ENABLED",
    "ENABLE_OUTBOUND_DISPATCH",
    "OUTBOUND_PROVIDER",
    "WHATSAPP_NATIVE_ENABLED",
    "WHATSAPP_DISPATCH_MODE",
    "EMAIL_MAILBOX_SYNC_ENABLED",
    "SPEEDAF_MCP_ENABLED",
    "SPEEDAF_TRACK_QUERY_ENABLED",
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED",
    "SPEEDAF_CANCEL_ENABLED",
    "SPEEDAF_VOICE_CALLBACK_ENABLED",
    "OPERATIONS_DISPATCH_MODE",
    "OPERATIONS_DISPATCH_ADAPTER",
)

for _name in _DEPLOYMENT_FEATURE_FLAGS:
    os.environ.pop(_name, None)

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("PYTHONPATH", "backend")
