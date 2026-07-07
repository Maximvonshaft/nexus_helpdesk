from app.services.ai_reply_v3_rollout import AI_REPLY_CONTRACT_V2, AI_REPLY_CONTRACT_V3, AIReplyV3RolloutConfig, choose_reply_contract_version


def test_v3_flag_disabled_keeps_v2_default():
    cfg = AIReplyV3RolloutConfig(enabled=False, handoff_enabled=True)
    assert choose_reply_contract_version(reply_type="handoff_notice", channel="webchat", config=cfg) == AI_REPLY_CONTRACT_V2


def test_v3_handoff_notice_enabled():
    cfg = AIReplyV3RolloutConfig(enabled=True, channels=("webchat",), handoff_enabled=True)
    assert choose_reply_contract_version(reply_type="handoff_notice", channel="webchat", config=cfg) == AI_REPLY_CONTRACT_V3


def test_v3_clarifying_question_without_sources_allowed():
    cfg = AIReplyV3RolloutConfig(enabled=True, channels=("webchat",), greeting_enabled=True)
    assert choose_reply_contract_version(reply_type="clarifying_question", channel="webchat", used_sources=[], config=cfg) == AI_REPLY_CONTRACT_V3
