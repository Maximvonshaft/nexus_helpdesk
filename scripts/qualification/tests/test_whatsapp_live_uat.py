from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


QUALIFICATION = Path(__file__).resolve().parents[1]
if str(QUALIFICATION) not in sys.path:
    sys.path.insert(0, str(QUALIFICATION))
MODULE_PATH = QUALIFICATION / "whatsapp_live_uat.py"
SPEC = importlib.util.spec_from_file_location("whatsapp_live_uat", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


SOURCE_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
TIMES = {
    "received": "2026-07-27T10:00:00Z",
    "sent": "2026-07-27T10:01:00Z",
    "delivered": "2026-07-27T10:02:00Z",
    "read": "2026-07-27T10:03:00Z",
}


class FakeClient:
    def __init__(self, *, change_session: bool = False) -> None:
        self.restarted: set[int] = set()
        self.change_session = change_session

    def get(self, path: str, *, query=None):
        connection_id = int(path.split("/")[5])
        transport = "meta_cloud_api" if connection_id == 1 else "baileys_sidecar"
        if path.endswith("/uat-evidence"):
            return self._facts(connection_id, transport, query or {})
        session_generation = 7
        if self.change_session and connection_id in self.restarted:
            session_generation = 8
        return {
            "id": connection_id,
            "transport": transport,
            "account_id": f"wa-{transport}",
            "observed_state": "connected",
            "authentication_state": "linked",
            "listener_state": "active",
            "desired_generation": 4,
            "observed_generation": 4,
            "session_generation": session_generation,
        }

    def post(self, path: str, *, payload=None):
        connection_id = int(path.split("/")[5])
        assert path.endswith("/restart")
        self.restarted.add(connection_id)
        return {"ok": True}

    def _facts(self, connection_id: int, transport: str, query: dict[str, str]):
        suffix = "1111" if connection_id == 1 else "2222"
        payload = {
            "transport": transport,
            "connection_id": connection_id,
            "account_id": f"wa-{transport}",
            "phone_suffix": suffix,
            "binding": {
                "observed_state": "connected",
                "authentication_state": "linked",
                "listener_state": "active",
                "desired_generation": 4,
                "observed_generation": 4,
                "session_generation": 7,
            },
            "inbound": {
                "provider_message_id": query["inbound_provider_message_id"],
                "received_at": TIMES["received"],
                "stored": True,
                "inbound_message_id": 100 + connection_id,
            },
            "outbound": self._outbound(query["outbound_provider_message_id"]),
            "contains_secrets": False,
            "contains_full_phone_numbers": False,
        }
        if "media_inbound_provider_message_id" in query:
            payload["media"] = {
                "inbound": {
                    "provider_message_id": query["media_inbound_provider_message_id"],
                    "asset_id": 200 + connection_id,
                    "attachment_id": 300 + connection_id,
                    "scan_status": "clean",
                    "storage_status": "available",
                    "sha256": "c" * 64,
                    "byte_size": 128,
                },
                "outbound": self._outbound(
                    query["media_outbound_provider_message_id"]
                ),
            }
        return payload

    @staticmethod
    def _outbound(provider_message_id: str):
        return {
            "provider_message_id": provider_message_id,
            "status": "read",
            "sent_at": TIMES["sent"],
            "delivered_at": TIMES["delivered"],
            "read_at": TIMES["read"],
            "outbound_message_id": 400,
            "outbound_part_id": 401,
            "part_type": "media",
        }


def _plan(*, replay: bool = True):
    return {
        "schema": "nexus.whatsapp-live-uat-plan.v1",
        "candidate": {
            "source_sha": SOURCE_SHA,
            "image_digest": IMAGE_DIGEST,
        },
        "transports": {
            "meta_cloud_api": {
                "connection_id": 1,
                "inbound_provider_message_id": "meta-inbound",
                "inbound_idempotent_replay": replay,
                "outbound_provider_message_id": "meta-outbound",
                "media_inbound_provider_message_id": "meta-media-inbound",
                "media_outbound_provider_message_id": "meta-media-outbound",
            },
            "baileys_sidecar": {
                "connection_id": 2,
                "inbound_provider_message_id": "baileys-inbound",
                "inbound_idempotent_replay": replay,
                "outbound_provider_message_id": "baileys-outbound",
                "media_inbound_provider_message_id": "baileys-media-inbound",
                "media_outbound_provider_message_id": "baileys-media-outbound",
            },
        },
    }


def test_live_uat_builds_signed_dual_transport_media_evidence():
    observation, evidence = MODULE.run_live_uat(
        _plan(),
        client=FakeClient(),
        expected_source_sha=SOURCE_SHA,
        expected_image_digest=IMAGE_DIGEST,
        signing_key=b"u" * 64,
        options=MODULE.LiveUatOptions(
            timeout_seconds=10,
            poll_seconds=0.01,
            require_media=True,
        ),
    )
    assert evidence["status"] == "pass"
    assert set(evidence["transports"]) == {
        "meta_cloud_api",
        "baileys_sidecar",
    }
    assert observation["transports"]["meta_cloud_api"]["phone_suffix"] == "1111"
    assert evidence["contains_secrets"] is False
    assert evidence["contains_full_phone_numbers"] is False


def test_live_uat_rejects_unproven_provider_replay():
    with pytest.raises(MODULE.LiveUatError, match="real_inbound_replay_required"):
        MODULE.run_live_uat(
            _plan(replay=False),
            client=FakeClient(),
            expected_source_sha=SOURCE_SHA,
            expected_image_digest=IMAGE_DIGEST,
            signing_key=b"u" * 64,
            options=MODULE.LiveUatOptions(require_media=True),
        )


def test_live_uat_rejects_restart_that_changes_session_generation():
    with pytest.raises(MODULE.LiveUatError, match="required_reauthentication"):
        MODULE.run_live_uat(
            _plan(),
            client=FakeClient(change_session=True),
            expected_source_sha=SOURCE_SHA,
            expected_image_digest=IMAGE_DIGEST,
            signing_key=b"u" * 64,
            options=MODULE.LiveUatOptions(
                timeout_seconds=10,
                poll_seconds=0.01,
                require_media=True,
            ),
        )
