import os
import sys
from pathlib import Path

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexusdesk_openclaw_probe.db')
os.environ.setdefault('OPENCLAW_TRANSPORT', 'mcp')
os.environ.setdefault('OPENCLAW_DEPLOYMENT_MODE', 'local_gateway')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services import openclaw_runtime_service  # noqa: E402
from app.services.openclaw_runtime_service import probe_openclaw_connectivity  # noqa: E402


def test_full_route_probe_reports_l3_when_transcript_method_works(monkeypatch):
    class DummyClient:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return None
        def conversations_list(self, **kwargs):
            return [{'session_key': 'sess-1', 'channel': 'whatsapp', 'recipient': '+1000'}]
        def conversation_messages(self, session_key, **kwargs):
            return [{'id': 'm1', 'body_text': 'hello'}]

    monkeypatch.setattr(openclaw_runtime_service, 'OpenClawMCPClient', DummyClient)
    result = probe_openclaw_connectivity()
    assert result.bridge_started is True
    assert result.conversations_tool_ok is True
    assert result.transcript_read_ok is True
    assert result.same_route_send_ready is True
    assert result.level in {'L3', 'L4'}


def test_full_route_probe_degrades_when_transcript_method_missing(monkeypatch):
    class DummyClient:
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return None
        def conversations_list(self, **kwargs):
            return [{'session_key': 'sess-1'}]

    monkeypatch.setattr(openclaw_runtime_service, 'OpenClawMCPClient', DummyClient)
    result = probe_openclaw_connectivity()
    assert result.bridge_started is True
    assert result.conversations_tool_ok is True
    assert result.sample_session_key == 'sess-1'
    assert result.transcript_read_ok is False
    assert any('Transcript' in warning or 'transcript' in warning for warning in result.warnings)


def test_full_route_probe_reports_disabled_mode(monkeypatch):
    monkeypatch.setattr(openclaw_runtime_service.settings, 'openclaw_deployment_mode', 'disabled')
    result = probe_openclaw_connectivity()
    assert result.bridge_started is False
    assert any('disabled' in warning.lower() for warning in result.warnings)
