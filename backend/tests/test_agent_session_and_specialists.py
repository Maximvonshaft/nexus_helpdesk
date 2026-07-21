from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_agent_sessions.db")

from app import models_agent_runtime  # noqa: E402,F401
from app.db import Base  # noqa: E402
from app.services.agent_runtime.context_compiler import (  # noqa: E402
    compile_agent_context,
)
from app.services.agent_runtime.run_events import start_agent_run  # noqa: E402
from app.services.agent_runtime.session_checkpoints import (  # noqa: E402
    build_checkpoint_summary,
    checkpoint_prompt_projection,
    load_session_checkpoint,
    save_session_checkpoint,
)
from app.services.agent_tool_contracts import (  # noqa: E402
    bootstrap_agent_tool_contracts,
)
from app.services.provider_runtime.output_contracts import (  # noqa: E402
    AGENT_SPECIALIST_OUTPUT_CONTRACT,
    OutputContracts,
)
from app.services.provider_runtime.router import _effective_configuration  # noqa: E402
from app.services.provider_runtime.schemas import ProviderRequest  # noqa: E402
from app.services.webchat_ai_decision_runtime.tool_registry import (  # noqa: E402
    get_tool_contract,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-sessions.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _release_snapshot() -> dict:
    return {
        "source": "deployment",
        "tenant_key": "tenant-a",
        "definition": {"id": 10, "definition_key": "support"},
        "deployment": {
            "id": 20,
            "environment": "production",
            "scope_key": "market:*|channel:webchat|language:*|case:*",
            "canary": False,
        },
        "release": {
            "id": 30,
            "version": 4,
            "manifest_sha256": "a" * 64,
        },
        "resolved": {
            "resources": [
                {
                    "config_type": "runtime_policy",
                    "content": {"provider_timeout_ms": 8000},
                },
                {
                    "config_type": "model_profile",
                    "content": {"model": "qwen"},
                },
            ]
        },
    }


def test_session_checkpoint_is_release_scoped_versioned_and_content_safe(
    db_session,
) -> None:
    run = start_agent_run(
        db_session,
        request_id="checkpoint-run-1",
        session_id="session-1",
        tenant_key="tenant-a",
        channel="webchat",
        environment="production",
        runtime_version="nexus.agent_runtime.v4",
    )
    run.release_id = 30
    first = save_session_checkpoint(
        db_session,
        run=run,
        summary={
            "last_intent": "tracking",
            "last_final_action": "reply",
            "run_status": "succeeded",
            "round_count": 2,
            "handoff_required": False,
            "tool_outcomes": [
                {
                    "tool_name": "knowledge.search",
                    "status": "executed",
                    "ok": True,
                    "error_code": None,
                    "raw_result": "must-not-persist",
                }
            ],
            "customer_message": "must-not-persist",
            "prompt": "must-not-persist",
        },
    )
    second_summary = build_checkpoint_summary(
        intent="general_support",
        final_action="ask_clarifying_question",
        run_status="succeeded",
        round_count=1,
        handoff_required=False,
        tool_calls=[],
        prior_checkpoint=first,
    )
    second = save_session_checkpoint(
        db_session,
        run=run,
        summary=second_summary,
    )
    db_session.commit()

    assert first.version == 1
    assert first.is_active is False
    assert second.version == 2
    assert second.is_active is True
    assert second.summary_json["prior_checkpoint_version"] == 1
    rendered = json.dumps(
        [first.summary_json, second.summary_json],
        ensure_ascii=False,
    )
    assert "must-not-persist" not in rendered
    assert "customer_message" not in rendered
    assert "prompt" not in rendered
    loaded = load_session_checkpoint(
        db_session,
        tenant_key="tenant-a",
        session_id="session-1",
        release_id=30,
    )
    assert loaded is not None
    assert loaded.id == second.id
    assert load_session_checkpoint(
        db_session,
        tenant_key="tenant-a",
        session_id="session-1",
        release_id=31,
    ) is None
    projection = checkpoint_prompt_projection(loaded)
    assert projection is not None
    assert projection["checkpoint_version"] == 2
    assert projection["release_id"] == 30
    assert "summary_sha256" in projection


def test_specialist_context_is_read_only_and_contract_specific() -> None:
    request = ProviderRequest(
        request_id="specialist-1",
        tenant_id="tenant-a",
        tenant_key="tenant-a",
        channel_key="webchat",
        session_id="session-a",
        scenario="agent_specialist",
        body="Review current policy consistency.",
        output_contract=AGENT_SPECIALIST_OUTPUT_CONTRACT,
        timeout_ms=8000,
        metadata={
            "agent_specialist": "policy_reviewer",
            "agent_specialist_evidence_refs": ["knowledge:returns-v4"],
            "agent_release_snapshot": _release_snapshot(),
        },
    )
    compiled = compile_agent_context(
        request,
        max_prompt_chars=4000,
        num_ctx=4096,
        max_output_chars=1200,
    )
    payload = json.loads(compiled.prompt[compiled.prompt.index("{") :])
    assert payload["specialist"] == "policy_reviewer"
    assert payload["constraints"] == {
        "read_only": True,
        "tool_calls_allowed": False,
        "customer_visible": False,
        "action_claims_allowed": False,
    }
    assert "nexus.agent_specialist.v1" in compiled.prompt
    assert "next_action='call_tool'" not in compiled.prompt


def test_specialist_output_contract_accepts_evidence_and_rejects_identifiers() -> None:
    valid = {
        "specialist": "policy_reviewer",
        "summary": "The available policy evidence is internally consistent.",
        "findings": [
            {
                "claim": "The published return rule supports the proposed handling.",
                "confidence": 0.92,
                "evidence_refs": ["knowledge:returns-v4"],
            }
        ],
        "risks": ["A human should review any compensation exception."],
        "recommended_action": "Continue with the published return workflow.",
        "needs_human_review": False,
    }
    parsed = OutputContracts.validate_and_parse(
        AGENT_SPECIALIST_OUTPUT_CONTRACT,
        json.dumps(valid),
    )
    assert parsed["specialist"] == "policy_reviewer"
    leaking = {
        **valid,
        "summary": "Contact customer@example.com about CH020000129135.",
    }
    with pytest.raises(ValueError, match="identifier"):
        OutputContracts.validate_and_parse(
            AGENT_SPECIALIST_OUTPUT_CONTRACT,
            json.dumps(leaking),
        )


def test_specialist_delegate_is_one_read_only_canonical_tool() -> None:
    bootstrap_agent_tool_contracts()
    contract = get_tool_contract("specialist.delegate")
    assert contract is not None
    assert contract.classification == "read"
    assert contract.customer_visible_result is False
    assert contract.controlled_action_required is True
    properties = contract.input_schema["properties"]
    assert "objective" in properties
    assert "task" not in properties
    assert contract.required_permissions == ()


def test_provider_router_preserves_exact_specialist_contract(monkeypatch) -> None:
    monkeypatch.delenv("PROVIDER_RUNTIME_OUTPUT_CONTRACT", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_FALLBACK_PROVIDERS", raising=False)
    monkeypatch.delenv("PROVIDER_RUNTIME_TIMEOUT_MS", raising=False)
    config = _effective_configuration(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": AGENT_SPECIALIST_OUTPUT_CONTRACT,
            "requested_output_contract": AGENT_SPECIALIST_OUTPUT_CONTRACT,
            "rule_found": True,
            "timeout_ms": 12000,
            "canary_percent": 100,
        }
    )
    assert config["output_contract"] == AGENT_SPECIALIST_OUTPUT_CONTRACT
    with pytest.raises(ValueError, match="output_contract_mismatch"):
        _effective_configuration(
            {
                "primary_provider": "private_ai_runtime",
                "fallback_providers": [],
                "output_contract": "nexus.agent_turn.v1",
                "requested_output_contract": AGENT_SPECIALIST_OUTPUT_CONTRACT,
                "rule_found": True,
                "timeout_ms": 12000,
                "canary_percent": 100,
            }
        )
