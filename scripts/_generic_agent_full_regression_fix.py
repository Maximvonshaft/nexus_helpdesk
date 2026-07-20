from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(
        r"^def [A-Za-z0-9_]+\(",
        text[match.end():],
        flags=re.MULTILINE,
    )
    end = len(text) if next_match is None else match.end() + next_match.start()
    while end > match.start() and text[end - 1] == "\n":
        end -= 1
    return match.start(), end


def remove_function(text: str, name: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


# The dispatcher was physically retired. Its authority guarantees are covered
# by Agent Runtime architecture, Provider Router and bounded-audit tests.
for path in (
    "backend/tests/test_provider_runtime_dispatcher_authority.py",
    "backend/tests/test_webchat_tracking_reply_polish.py",
):
    target = Path(path)
    if target.exists():
        target.unlink()

# Preserve card, idempotency, safety and outbound semantics tests. Remove only
# tests of the deleted business-specific runtime parser.
structured_path = "backend/tests/test_webchat_structured_runtime.py"
structured = read(structured_path)
structured = structured.replace(
    "from app.services.webchat_runtime_output_parser import "
    "RuntimeReplyParseError, parse_runtime_reply_provider_output\n",
    "",
    1,
)
for obsolete in (
    "test_runtime_parser_cleans_mixed_waybill_label",
    "test_runtime_parser_allows_shipment_outcome_only_with_tracking_evidence",
):
    structured = remove_function(structured, obsolete)
write(structured_path, structured)

# TrackingFact remains a Tool backend. Remove only the retired history/policy
# heuristic that selected server-side prefetch before the model turn.
tracking_path = "backend/tests/test_webchat_tracking_fact_mvp.py"
tracking = read(tracking_path)
tracking = tracking.replace(
    "from app.services.webchat_ai_service import "
    "_allows_history_tracking_lookup, _looks_like_service_policy_question\n",
    "",
    1,
)
tracking = remove_function(
    tracking,
    "test_service_policy_question_does_not_inherit_history_tracking_lookup",
)
write(tracking_path, tracking)

# None means that a legacy caller did not supply an allowlist. An explicit empty
# allowlist from Agent Runtime must deny every Tool rather than disable checks.
core_path = "backend/app/services/nexus_osr/tool_execution_service_core.py"
core = read(core_path)
core = replace_once(
    core,
    "    allowed_tool_names: frozenset[str] = frozenset()\n"
    "    granted_permissions: frozenset[str] = frozenset()\n",
    "    allowed_tool_names: frozenset[str] | None = None\n"
    "    granted_permissions: frozenset[str] = frozenset()\n",
    label="tool allowlist type",
)
core = replace_once(
    core,
    "        granted_permissions=(\n"
    "            set(options.granted_permissions)\n"
    "            if options.allowed_tool_names\n"
    "            else None\n"
    "        ),\n",
    "        granted_permissions=(\n"
    "            set(options.granted_permissions)\n"
    "            if options.allowed_tool_names is not None\n"
    "            else None\n"
    "        ),\n",
    label="policy permission enforcement",
)
core = replace_once(
    core,
    "            options.allowed_tool_names\n"
    "            and action.tool_name not in options.allowed_tool_names\n",
    "            options.allowed_tool_names is not None\n"
    "            and action.tool_name not in options.allowed_tool_names\n",
    label="registry allowlist enforcement",
)
core = replace_once(
    core,
    "            options.allowed_tool_names\n"
    "            and not set(contract.required_permissions).issubset(\n",
    "            options.allowed_tool_names is not None\n"
    "            and not set(contract.required_permissions).issubset(\n",
    label="registry permission enforcement",
)
write(core_path, core)

# A Tool result is authoritative only when execution and the enclosing database
# transaction both succeed. Never feed rolled-back success observations to the
# model or let it claim a side effect that did not commit.
service_path = "backend/app/services/agent_runtime/service.py"
service = read(service_path)
old_tool_block = '''        observations = execute_agent_tool_calls(
            db,
            calls=decision.tool_calls,
            context=execution_context,
            allow_high_risk_writes=allow_high_risk_writes,
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
        state.executed_calls.extend(
            {
                "round": round_index,
                "tool_name": call.tool_name,
                "status": observation.status,
                "ok": observation.ok,
                "error_code": observation.error_code,
            }
            for call, observation in zip(decision.tool_calls, observations)
        )
        state.traces.append(
            AgentRoundTrace(
                round_index=round_index,
                next_action="call_tool",
                tool_names=tuple(call.tool_name for call in decision.tool_calls),
                observation_statuses=tuple(
                    item.status for item in observations
                ),
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
            )
        )
        state.observations.extend(observations)
'''
new_tool_block = '''        try:
            observations = execute_agent_tool_calls(
                db,
                calls=decision.tool_calls,
                context=execution_context,
                allow_high_risk_writes=allow_high_risk_writes,
            )
        except Exception:
            _safe_rollback(db)
            observations = _failed_tool_observations(
                decision,
                error_code="tool_execution_failed",
            )
            state.elapsed_ms = _elapsed(started)
            _record_tool_observations(
                state,
                round_index=round_index,
                decision=decision,
                observations=observations,
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
                error_code="tool_execution_failed",
            )
            return _fallback_result(
                request,
                state=state,
                error_code="tool_execution_failed",
                elapsed_ms=state.elapsed_ms,
            )
        try:
            db.commit()
        except Exception:
            _safe_rollback(db)
            observations = _failed_tool_observations(
                decision,
                error_code="tool_transaction_commit_failed",
            )
            state.elapsed_ms = _elapsed(started)
            _record_tool_observations(
                state,
                round_index=round_index,
                decision=decision,
                observations=observations,
                provider=result.provider,
                elapsed_ms=result.elapsed_ms,
                error_code="tool_transaction_commit_failed",
            )
            return _fallback_result(
                request,
                state=state,
                error_code="tool_transaction_commit_failed",
                elapsed_ms=state.elapsed_ms,
            )
        _record_tool_observations(
            state,
            round_index=round_index,
            decision=decision,
            observations=observations,
            provider=result.provider,
            elapsed_ms=result.elapsed_ms,
        )
'''
service = replace_once(
    service,
    old_tool_block,
    new_tool_block,
    label="agent tool transaction boundary",
)
helper_marker = "\n\ndef _authoritative_provider_audit_exists(\n"
helpers = '''

def _failed_tool_observations(
    decision: AIDecision,
    *,
    error_code: str,
) -> list[ToolObservation]:
    return [
        ToolObservation(
            tool_name=call.tool_name,
            ok=False,
            status="failed",
            result={},
            error_code=error_code,
        )
        for call in decision.tool_calls
    ]


def _record_tool_observations(
    state: AgentRunState,
    *,
    round_index: int,
    decision: AIDecision,
    observations: list[ToolObservation],
    provider: str | None,
    elapsed_ms: int,
    error_code: str | None = None,
) -> None:
    state.executed_calls.extend(
        {
            "round": round_index,
            "tool_name": call.tool_name,
            "status": observation.status,
            "ok": observation.ok,
            "error_code": observation.error_code,
        }
        for call, observation in zip(decision.tool_calls, observations)
    )
    state.traces.append(
        AgentRoundTrace(
            round_index=round_index,
            next_action="call_tool",
            tool_names=tuple(call.tool_name for call in decision.tool_calls),
            observation_statuses=tuple(item.status for item in observations),
            provider=provider,
            elapsed_ms=elapsed_ms,
            error_code=error_code,
        )
    )
    state.observations.extend(observations)


def _safe_rollback(db: Session) -> None:
    try:
        db.rollback()
    except Exception:
        pass
'''
service = replace_once(
    service,
    helper_marker,
    helpers + helper_marker,
    label="agent transaction helpers",
)
write(service_path, service)

# Regression: explicit empty authorization must not become unrestricted.
executor_test_path = "backend/tests/test_nexus_osr_tool_execution_service.py"
executor_tests = read(executor_test_path)
empty_allowlist_test = '''


def test_explicit_empty_tool_allowlist_fails_closed(db_session):
    conversation = make_conversation(db_session, public_id="empty_allowlist_wc")
    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "timeline.event.create"}],
        case_context=CaseContext(
            conversation_id=conversation.id,
            channel="webchat",
            country_code="ME",
        ),
        conversation=conversation,
        channel="webchat",
        country_code="ME",
        options=GovernedToolExecutionOptions(
            allowed_tool_names=frozenset(),
            granted_permissions=frozenset(),
        ),
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code in {"tool_not_available", "tool_permission_denied"}
    assert db_session.query(TicketEvent).count() == 0
'''
if "test_explicit_empty_tool_allowlist_fails_closed" not in executor_tests:
    executor_tests = executor_tests.rstrip() + empty_allowlist_test
write(executor_test_path, executor_tests)

# Regression: a successful handler result followed by commit failure must become
# a failed Tool observation and deterministic customer-visible fallback.
agent_test_path = "backend/tests/test_agent_runtime_architecture.py"
agent_tests = read(agent_test_path)
commit_failure_test = '''


class _FailingCommitDb(_Db):
    def __init__(self) -> None:
        self.rolled_back = False

    def commit(self) -> None:
        raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_agent_loop_fails_closed_when_tool_transaction_does_not_commit(monkeypatch) -> None:
    route_calls = 0

    async def route(_self, _request):
        nonlocal route_calls
        route_calls += 1
        return ProviderResult(
            ok=True,
            provider="private_ai_runtime",
            raw_provider="private_ai_runtime",
            reply_source="private_ai_runtime",
            elapsed_ms=3,
            structured_output={
                "customer_reply": None,
                "intent": "shipment_tracking",
                "next_action": "call_tool",
                "handoff_required": False,
                "tool_calls": [
                    {
                        "tool_name": "speedaf.order.query",
                        "arguments": {"tracking_number": "CH020000129135"},
                    }
                ],
            },
            raw_payload_safe_summary={"model": "test"},
        )

    def execute(_db, *, calls, context, allow_high_risk_writes=False):
        del calls, context, allow_high_risk_writes
        return [
            ToolObservation(
                tool_name="speedaf.order.query",
                ok=True,
                status="success",
                result={"status": "in_transit"},
            )
        ]

    db = _FailingCommitDb()
    monkeypatch.setattr(agent_service.ProviderRuntimeRouter, "route", route)
    monkeypatch.setattr(
        agent_service,
        "_authoritative_provider_audit_exists",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(agent_service, "execute_agent_tool_calls", execute)

    result = await agent_service._run_agent_with_db(
        db,
        request=RuntimeAIProviderRequest(
            tenant_key="tenant",
            channel_key="website",
            session_id="session",
            body="Where is CH020000129135?",
            request_id="request",
            metadata={
                "agent_allowed_tools": ["speedaf.order.query"],
                "agent_execution_context": {
                    "granted_permissions": ["speedaf:tracking:read"]
                },
            },
        ),
        started=0.0,
    )

    assert route_calls == 1
    assert db.rolled_back is True
    assert result.ai_generated is False
    assert result.error_code == "tool_transaction_commit_failed"
    assert result.tool_calls == [
        {
            "round": 0,
            "tool_name": "speedaf.order.query",
            "status": "failed",
            "ok": False,
            "error_code": "tool_transaction_commit_failed",
        }
    ]
'''
if "test_agent_loop_fails_closed_when_tool_transaction_does_not_commit" not in agent_tests:
    agent_tests = agent_tests.rstrip() + commit_failure_test
write(agent_test_path, agent_tests)

assert not Path(
    "backend/tests/test_provider_runtime_dispatcher_authority.py"
).exists()
assert not Path("backend/tests/test_webchat_tracking_reply_polish.py").exists()
assert "webchat_runtime_output_parser" not in read(structured_path)
assert "_allows_history_tracking_lookup" not in read(tracking_path)
assert "allowed_tool_names: frozenset[str] | None = None" in read(core_path)
assert "tool_transaction_commit_failed" in read(service_path)
