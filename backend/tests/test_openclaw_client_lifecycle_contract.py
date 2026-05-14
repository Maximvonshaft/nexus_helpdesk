from __future__ import annotations

import asyncio
from pathlib import Path

from app.services import webchat_openclaw_responses_client
from app.services.webchat_openclaw_responses_client import close_openclaw_clients


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_openclaw_clients_are_closed_on_app_shutdown_hook():
    main_source = _read("app/main.py")

    assert "from .services.webchat_openclaw_responses_client import close_openclaw_clients" in main_source
    assert "@app.on_event('shutdown')" in main_source
    assert "async def shutdown_openclaw_clients()" in main_source
    assert "await close_openclaw_clients()" in main_source


def test_openclaw_client_rotation_closes_replaced_clients():
    client_source = _read("app/services/webchat_openclaw_responses_client.py")

    assert "async def close_openclaw_clients()" in client_source
    assert "previous = _CLIENT" in client_source
    assert "previous = _STREAM_CLIENT" in client_source
    assert "_close_replaced_client(previous)" in client_source
    assert "await client.aclose()" in client_source


def test_close_openclaw_clients_clears_cached_clients():
    class FakeAsyncClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    async def run():
        client = FakeAsyncClient()
        stream_client = FakeAsyncClient()
        webchat_openclaw_responses_client._CLIENT = client
        webchat_openclaw_responses_client._CLIENT_KEY = ("a", 1, 1)
        webchat_openclaw_responses_client._STREAM_CLIENT = stream_client
        webchat_openclaw_responses_client._STREAM_CLIENT_KEY = ("b", 2, 2)
        await close_openclaw_clients()
        return client, stream_client

    client, stream_client = asyncio.run(run())
    assert client.closed is True
    assert stream_client.closed is True
    assert webchat_openclaw_responses_client._CLIENT is None
    assert webchat_openclaw_responses_client._CLIENT_KEY is None
    assert webchat_openclaw_responses_client._STREAM_CLIENT is None
    assert webchat_openclaw_responses_client._STREAM_CLIENT_KEY is None
