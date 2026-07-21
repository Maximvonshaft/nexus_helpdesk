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


context_path = "backend/app/services/ai_runtime_context.py"
context = read(context_path)
context = replace_once(
    context,
    "    channel_payload: dict[str, Any] | None = None,\n"
    "    **_legacy: Any,\n",
    "    channel_payload: dict[str, Any] | None = None,\n",
    label="legacy context kwargs",
)
start = re.search(r"^def build_runtime_context_guard\(", context, flags=re.MULTILINE)
end = re.search(r"^def sanitize_runtime_context\(", context, flags=re.MULTILINE)
if start is None or end is None or end.start() <= start.start():
    raise SystemExit("legacy runtime context guard boundary not found")
context = context[: start.start()].rstrip() + "\n\n\n" + context[end.start():]
write(context_path, context)

legacy_test = Path("backend/tests/test_runtime_context_guard.py")
if not legacy_test.exists():
    raise SystemExit("legacy runtime context guard test missing")
legacy_test.unlink()

knowledge_path = "backend/tests/test_knowledge_runtime_context.py"
knowledge = read(knowledge_path)
old_test = '''def test_runtime_context_ignores_retired_tracking_prefetch_arguments(db_session):
    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Reference CH1200000011425",
        tracking_number="CH1200000011425",
        tracking_fact_evidence_present=True,
    )

    serialized = str(context)
    assert "knowledge_context" not in context
    assert "conversation_state" not in context
    assert "tracking_fact_evidence_present" not in serialized
    assert "locked_facts" not in serialized
'''
new_test = '''def test_runtime_context_has_no_retired_tracking_prefetch_parameters(db_session):
    import inspect

    signature = inspect.signature(build_webchat_runtime_context)
    assert "tracking_number" not in signature.parameters
    assert "tracking_fact_evidence_present" not in signature.parameters
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Reference CH1200000011425",
    )
    serialized = str(context)
    assert "knowledge_context" not in context
    assert "conversation_state" not in context
    assert "tracking_fact_evidence_present" not in serialized
    assert "locked_facts" not in serialized
'''
knowledge = replace_once(
    knowledge,
    old_test,
    new_test,
    label="retired tracking compatibility test",
)
write(knowledge_path, knowledge)

residue_path = "scripts/ci/check_agent_runtime_residue.py"
residue = read(residue_path)
retired_entry = '    ROOT / "backend/tests/test_runtime_context_guard.py",\n'
if retired_entry not in residue:
    residue = residue.replace(
        '    ROOT / "backend/scripts/run_domain_runtime_eval.py",\n',
        '    ROOT / "backend/scripts/run_domain_runtime_eval.py",\n' + retired_entry,
        1,
    )
marker = '    "def build_runtime_context_guard(",\n'
if marker not in residue:
    residue = residue.replace(
        '    "tracking_fact_evidence_present",\n',
        '    "tracking_fact_evidence_present",\n' + marker,
        1,
    )
write(residue_path, residue)

architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
test = '''


def test_generic_context_has_no_legacy_domain_compatibility_surface() -> None:
    source = Path(
        "backend/app/services/ai_runtime_context.py"
    ).read_text(encoding="utf-8")
    signature = source.split("def build_webchat_runtime_context", 1)[1].split(
        ") -> dict[str, Any]:", 1
    )[0]
    assert "**_legacy" not in signature
    assert "tracking_number" not in signature
    assert "tracking_fact_evidence_present" not in signature
    assert "def build_runtime_context_guard(" not in source
    assert not Path("backend/tests/test_runtime_context_guard.py").exists()
'''
if "test_generic_context_has_no_legacy_domain_compatibility_surface" not in architecture:
    architecture = architecture.rstrip() + test
write(architecture_path, architecture)

assert "**_legacy" not in read(context_path)
assert "def build_runtime_context_guard(" not in read(context_path)
assert not legacy_test.exists()
