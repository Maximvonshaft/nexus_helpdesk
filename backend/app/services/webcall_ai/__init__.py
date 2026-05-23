"""WebCall AI foundation contracts.

This package exposes guarded config, schemas, persistence helpers, worker
claim lifecycle, deterministic mock turn execution, and mock media boundary
contracts. It does not start a functional AI voice agent.
"""

from .audio_reference_resolver import resolve_audio_reference_for_session
from .config import WebCallAISettings, get_webcall_ai_settings
from .contract_stub_provider import (
    ContractStubSTTProvider,
    ContractStubTTSProvider,
    DisabledSTTProvider,
    DisabledTTSProvider,
)
from .deepgram_stt_provider import DeepgramSTTProvider
from .lifecycle import (
    WEBCALL_AI_STATUS_CLAIMED,
    WEBCALL_AI_STATUS_FAILED,
    WEBCALL_AI_STATUS_PENDING,
    WEBCALL_AI_STATUS_RELEASED,
    WEBCALL_AI_STATUS_SKIPPED,
    claim_webcall_ai_sessions,
    fail_webcall_ai_session,
    heartbeat_webcall_ai_session,
    release_webcall_ai_session,
    is_webcall_ai_session_claimable,
)
from .evidence_builder import WebCallAIEvidenceReport, build_webcall_ai_evidence_report, evidence_report_to_safe_dict
from .handoff_service import mark_webcall_ai_handoff_required
from .media_schemas import (
    MockSTTInput,
    MockSTTResult,
    MockTTSInput,
    MockTTSResult,
    WebCallSTTInput,
    WebCallSTTResult,
    WebCallTTSInput,
    WebCallTTSResult,
)
from .mock_media_provider import MockSTTProvider, MockTTSProvider
from .mock_turn_executor import MockTurnExecutionResult, MockTurnRuntimeFailure, execute_mock_turn_for_claimed_session
from .orchestrator import WebCallAIOrchestratorResult, run_webcall_ai_orchestrator
from .pilot_canary_gate import WebCallAIPilotGateDecision, evaluate_webcall_ai_pilot_gate
from .pilot_closure import WebCallAIPilotClosureResult, run_webcall_ai_pilot_closure_once
from .pilot_fake_tracking import fake_tracking_fact_for_pilot
from .pilot_session_source import resolve_or_create_pilot_voice_session
from .participant_service import (
    ai_participant_identity,
    ensure_ai_participant_record,
    mark_ai_participant_joined,
    mark_ai_participant_left,
)
from .presence_client import (
    FakeNoMediaPresenceClient,
    LiveKitNoMediaPresenceClient,
    WebCallAIPresenceJoinResult,
    WebCallAIPresenceLeaveResult,
    get_webcall_ai_presence_client,
)
from .provider_router import get_stt_provider, get_tts_provider
from .real_media_smoke import WebCallAIRealMediaSmokeResult, run_webcall_ai_real_media_smoke
from .reply_builder import (
    build_handoff_reply,
    build_missing_tracking_reply,
    build_tracking_lookup_disabled_reply,
    build_tracking_reply,
)
from .room_client import (
    FakeWebCallAIRoomClient,
    LiveKitTokenIssuerRoomClient,
    WebCallAIRoomJoinResult,
    WebCallAIRoomLeaveResult,
    build_livekit_token_issuer_client,
)
from .schemas import (
    WebCallAIActionDecision,
    WebCallAIAllowedAction,
    WebCallAIForbiddenAction,
    WebCallAITurnDecision,
    reject_forbidden_action,
)
from .stt_runtime import WebCallSTTRuntimeResult, run_stt_runtime_for_session
from .transcript_writer import (
    CUSTOMER_PARTICIPANT_IDENTITY,
    TranscriptWriteResult,
    write_stt_transcript_segment,
)
from .tts_runtime import WebCallTTSRuntimeResult, run_tts_runtime_for_turn
from .voice_egress_client import (
    FakeAudioReferenceEgressClient,
    LiveKitAudioPublishStubClient,
    WebCallVoiceEgressResult,
    get_webcall_voice_egress_client,
)

__all__ = [
    "WebCallAIActionDecision",
    "WebCallAIAllowedAction",
    "WebCallAIForbiddenAction",
    "WebCallAIOrchestratorResult",
    "WebCallAISettings",
    "WebCallAITurnDecision",
    "WebCallAIEvidenceReport",
    "WebCallAIPilotClosureResult",
    "WebCallAIPilotGateDecision",
    "WebCallAIRealMediaSmokeResult",
    "ContractStubSTTProvider",
    "ContractStubTTSProvider",
    "DisabledSTTProvider",
    "DisabledTTSProvider",
    "DeepgramSTTProvider",
    "MockSTTInput",
    "MockSTTProvider",
    "MockSTTResult",
    "MockTTSInput",
    "MockTTSProvider",
    "MockTTSResult",
    "MockTurnExecutionResult",
    "MockTurnRuntimeFailure",
    "WebCallSTTInput",
    "WebCallSTTResult",
    "WebCallSTTRuntimeResult",
    "WebCallTTSInput",
    "WebCallTTSResult",
    "WebCallTTSRuntimeResult",
    "WebCallVoiceEgressResult",
    "WEBCALL_AI_STATUS_CLAIMED",
    "WEBCALL_AI_STATUS_FAILED",
    "WEBCALL_AI_STATUS_PENDING",
    "WEBCALL_AI_STATUS_RELEASED",
    "WEBCALL_AI_STATUS_SKIPPED",
    "FakeWebCallAIRoomClient",
    "FakeNoMediaPresenceClient",
    "FakeAudioReferenceEgressClient",
    "LiveKitTokenIssuerRoomClient",
    "LiveKitNoMediaPresenceClient",
    "LiveKitAudioPublishStubClient",
    "WebCallAIPresenceJoinResult",
    "WebCallAIPresenceLeaveResult",
    "WebCallAIRoomJoinResult",
    "WebCallAIRoomLeaveResult",
    "ai_participant_identity",
    "build_livekit_token_issuer_client",
    "build_handoff_reply",
    "build_missing_tracking_reply",
    "build_tracking_lookup_disabled_reply",
    "build_tracking_reply",
    "claim_webcall_ai_sessions",
    "CUSTOMER_PARTICIPANT_IDENTITY",
    "fail_webcall_ai_session",
    "execute_mock_turn_for_claimed_session",
    "build_webcall_ai_evidence_report",
    "evidence_report_to_safe_dict",
    "evaluate_webcall_ai_pilot_gate",
    "fake_tracking_fact_for_pilot",
    "get_webcall_ai_settings",
    "get_webcall_ai_presence_client",
    "get_webcall_voice_egress_client",
    "get_stt_provider",
    "get_tts_provider",
    "heartbeat_webcall_ai_session",
    "ensure_ai_participant_record",
    "is_webcall_ai_session_claimable",
    "mark_webcall_ai_handoff_required",
    "mark_ai_participant_joined",
    "mark_ai_participant_left",
    "release_webcall_ai_session",
    "reject_forbidden_action",
    "resolve_audio_reference_for_session",
    "resolve_or_create_pilot_voice_session",
    "run_webcall_ai_pilot_closure_once",
    "run_webcall_ai_real_media_smoke",
    "run_stt_runtime_for_session",
    "run_tts_runtime_for_turn",
    "run_webcall_ai_orchestrator",
    "TranscriptWriteResult",
    "write_stt_transcript_segment",
]
