#!/usr/bin/env python3
"""Run operator-assisted WhatsApp UAT against an exact Nexus candidate.

External operator actions remain explicit: bind the real accounts, cause a real
inbound replay, send a canonical Inbox reply, and read the message on the real
recipient device. This command never fabricates those facts. It retrieves the
server-side durable evidence, performs a controlled connection restart for both
transports, and emits the signed production evidence artifact.
"""

from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from whatsapp_e2e_evidence import EvidenceError, compile_evidence


_PLAN_SCHEMA = "nexus.whatsapp-live-uat-plan.v1"
_TRANSPORTS = ("meta_cloud_api", "baileys_sidecar")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class LiveUatError(ValueError):
    pass


class AdminClient(Protocol):
    def get(self, path: str, *, query: dict[str, str] | None = None) -> dict[str, Any]:
        ...

    def post(self, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class LiveUatOptions:
    timeout_seconds: int = 180
    poll_seconds: float = 2.0
    require_media: bool = False


class NexusAdminClient:
    def __init__(self, *, base_url: str, token: str, timeout_seconds: int = 20) -> None:
        normalized = base_url.rstrip("/")
        parsed = urllib.parse.urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise LiveUatError("base_url_https_required")
        if any(char in normalized for char in "\r\n\x00"):
            raise LiveUatError("base_url_invalid")
        if not token or any(char in token for char in "\r\n\x00"):
            raise LiveUatError("admin_token_invalid")
        self.base_url = normalized
        self.token = token
        self.timeout_seconds = max(5, min(int(timeout_seconds), 120))
        self.context = ssl.create_default_context()

    def get(self, path: str, *, query: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("GET", path, query=query)

    def post(self, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload=payload)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/") or any(char in path for char in "\r\n\x00"):
            raise LiveUatError("api_path_invalid")
        url = self.base_url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=False, safe="")
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "NexusDesk-WhatsApp-Live-UAT/1",
        }
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
                context=self.context,
            ) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise LiveUatError(f"api_http_error:{exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LiveUatError("api_transport_error") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise LiveUatError("api_response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveUatError("api_response_invalid") from exc
        if not isinstance(value, dict):
            raise LiveUatError("api_response_not_object")
        return value


def run_live_uat(
    plan: dict[str, Any],
    *,
    client: AdminClient,
    expected_source_sha: str,
    expected_image_digest: str,
    signing_key: bytes,
    options: LiveUatOptions,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if plan.get("schema") != _PLAN_SCHEMA:
        raise LiveUatError("uat_plan_schema_invalid")
    candidate = _mapping(plan.get("candidate"), "uat_candidate_missing")
    if candidate.get("source_sha") != expected_source_sha:
        raise LiveUatError("uat_candidate_source_sha_mismatch")
    normalized_digest = _normalize_digest(expected_image_digest)
    if _normalize_digest(candidate.get("image_digest")) != normalized_digest:
        raise LiveUatError("uat_candidate_image_digest_mismatch")
    transports = _mapping(plan.get("transports"), "uat_transports_missing")
    if set(transports) != set(_TRANSPORTS):
        raise LiveUatError("uat_dual_transports_required")

    observed: dict[str, Any] = {}
    for transport in _TRANSPORTS:
        transport_plan = _mapping(
            transports[transport],
            f"uat_{transport}_plan_invalid",
        )
        observed[transport] = _run_transport(
            transport,
            transport_plan,
            client=client,
            options=options,
        )

    observation = {
        "schema": "nexus.whatsapp-live-observation.v1",
        "candidate": {
            "source_sha": expected_source_sha,
            "image_digest": normalized_digest,
        },
        "observed_at": _now(),
        "transports": observed,
    }
    evidence = compile_evidence(
        observation,
        expected_source_sha=expected_source_sha,
        expected_image_digest=normalized_digest,
        signing_key=signing_key,
        require_media=options.require_media,
    )
    return observation, evidence


def _run_transport(
    transport: str,
    plan: dict[str, Any],
    *,
    client: AdminClient,
    options: LiveUatOptions,
) -> dict[str, Any]:
    connection_id = _positive_int(
        plan.get("connection_id"),
        f"uat_{transport}_connection_id_invalid",
    )
    if plan.get("inbound_idempotent_replay") is not True:
        raise LiveUatError(f"uat_{transport}_real_inbound_replay_required")
    base_path = f"/api/admin/whatsapp/connections/{connection_id}"
    before = client.get(base_path)
    _require_transport_state(before, transport=transport, require_connected=True)
    initiated_at = _now()
    client.post(base_path + "/restart")
    after, reconnected_at = _poll_connected(
        client,
        path=base_path,
        transport=transport,
        timeout_seconds=options.timeout_seconds,
        poll_seconds=options.poll_seconds,
    )
    before_session = _nonnegative_int(
        before.get("session_generation"),
        f"uat_{transport}_session_generation_invalid",
    )
    after_session = _nonnegative_int(
        after.get("session_generation"),
        f"uat_{transport}_session_generation_invalid",
    )
    if before_session != after_session:
        raise LiveUatError(f"uat_{transport}_restart_required_reauthentication")

    query = {
        "inbound_provider_message_id": _safe_id(
            plan.get("inbound_provider_message_id"),
            f"uat_{transport}_inbound_provider_id_invalid",
        ),
        "outbound_provider_message_id": _safe_id(
            plan.get("outbound_provider_message_id"),
            f"uat_{transport}_outbound_provider_id_invalid",
        ),
    }
    media_inbound = plan.get("media_inbound_provider_message_id")
    media_outbound = plan.get("media_outbound_provider_message_id")
    if options.require_media or media_inbound is not None or media_outbound is not None:
        query["media_inbound_provider_message_id"] = _safe_id(
            media_inbound,
            f"uat_{transport}_media_inbound_provider_id_invalid",
        )
        query["media_outbound_provider_message_id"] = _safe_id(
            media_outbound,
            f"uat_{transport}_media_outbound_provider_id_invalid",
        )
    facts = client.get(base_path + "/uat-evidence", query=query)
    if facts.get("transport") != transport:
        raise LiveUatError(f"uat_{transport}_server_transport_mismatch")
    inbound = _mapping(facts.get("inbound"), f"uat_{transport}_inbound_missing")
    outbound = _mapping(facts.get("outbound"), f"uat_{transport}_outbound_missing")
    result: dict[str, Any] = {
        "transport": transport,
        "connection_id": connection_id,
        "account_id": _safe_id(
            facts.get("account_id"),
            f"uat_{transport}_account_id_invalid",
        ),
        "phone_suffix": _phone_suffix(facts.get("phone_suffix"), transport),
        "binding": _binding(facts.get("binding"), transport),
        "inbound": {
            "provider_message_id": _safe_id(
                inbound.get("provider_message_id"),
                f"uat_{transport}_inbound_provider_id_invalid",
            ),
            "received_at": _timestamp(
                inbound.get("received_at"),
                f"uat_{transport}_inbound_timestamp_invalid",
            ),
            "stored": inbound.get("stored") is True,
            "idempotent_replay": True,
        },
        "outbound": _outbound(outbound, transport),
        "restart": {
            "initiated_at": initiated_at,
            "reconnected_at": reconnected_at,
            "credentials_persisted": True,
            "listener_active": after.get("listener_state") == "active",
            "reconnected_without_reauthentication": True,
            "desired_generation": _nonnegative_int(
                after.get("desired_generation"),
                f"uat_{transport}_desired_generation_invalid",
            ),
            "observed_generation": _nonnegative_int(
                after.get("observed_generation"),
                f"uat_{transport}_observed_generation_invalid",
            ),
        },
    }
    if "media" in facts:
        media = _mapping(facts["media"], f"uat_{transport}_media_missing")
        media_inbound_facts = _mapping(
            media.get("inbound"),
            f"uat_{transport}_media_inbound_missing",
        )
        result["media"] = {
            "inbound": {
                "provider_message_id": _safe_id(
                    media_inbound_facts.get("provider_message_id"),
                    f"uat_{transport}_media_inbound_provider_id_invalid",
                ),
                "asset_id": _positive_int(
                    media_inbound_facts.get("asset_id"),
                    f"uat_{transport}_media_asset_id_invalid",
                ),
                "attachment_id": _positive_int(
                    media_inbound_facts.get("attachment_id"),
                    f"uat_{transport}_media_attachment_id_invalid",
                ),
                "scan_status": media_inbound_facts.get("scan_status"),
                "storage_status": media_inbound_facts.get("storage_status"),
                "sha256": media_inbound_facts.get("sha256"),
                "byte_size": _positive_int(
                    media_inbound_facts.get("byte_size"),
                    f"uat_{transport}_media_byte_size_invalid",
                ),
            },
            "outbound": _outbound(
                _mapping(
                    media.get("outbound"),
                    f"uat_{transport}_media_outbound_missing",
                ),
                transport,
            ),
        }
    return result


def _poll_connected(
    client: AdminClient,
    *,
    path: str,
    transport: str,
    timeout_seconds: int,
    poll_seconds: float,
) -> tuple[dict[str, Any], str]:
    deadline = time.monotonic() + max(10, min(timeout_seconds, 900))
    delay = max(0.25, min(float(poll_seconds), 10.0))
    while time.monotonic() < deadline:
        value = client.get(path)
        try:
            _require_transport_state(value, transport=transport, require_connected=True)
            return value, _now()
        except LiveUatError:
            time.sleep(delay)
    raise LiveUatError(f"uat_{transport}_restart_timeout")


def _require_transport_state(
    value: dict[str, Any],
    *,
    transport: str,
    require_connected: bool,
) -> None:
    if value.get("transport") != transport:
        raise LiveUatError(f"uat_{transport}_connection_transport_mismatch")
    if require_connected and not (
        value.get("observed_state") == "connected"
        and value.get("authentication_state") == "linked"
        and value.get("listener_state") == "active"
        and value.get("desired_generation") == value.get("observed_generation")
    ):
        raise LiveUatError(f"uat_{transport}_connection_not_ready")


def _binding(value: Any, transport: str) -> dict[str, Any]:
    row = _mapping(value, f"uat_{transport}_binding_missing")
    return {
        "observed_state": row.get("observed_state"),
        "authentication_state": row.get("authentication_state"),
        "listener_state": row.get("listener_state"),
        "desired_generation": _nonnegative_int(
            row.get("desired_generation"),
            f"uat_{transport}_desired_generation_invalid",
        ),
        "observed_generation": _nonnegative_int(
            row.get("observed_generation"),
            f"uat_{transport}_observed_generation_invalid",
        ),
    }


def _outbound(value: dict[str, Any], transport: str) -> dict[str, Any]:
    if value.get("status") != "read":
        raise LiveUatError(f"uat_{transport}_outbound_not_read")
    return {
        "provider_message_id": _safe_id(
            value.get("provider_message_id"),
            f"uat_{transport}_outbound_provider_id_invalid",
        ),
        "sent_at": _timestamp(
            value.get("sent_at"),
            f"uat_{transport}_outbound_sent_at_invalid",
        ),
        "delivered_at": _timestamp(
            value.get("delivered_at"),
            f"uat_{transport}_outbound_delivered_at_invalid",
        ),
        "read_at": _timestamp(
            value.get("read_at"),
            f"uat_{transport}_outbound_read_at_invalid",
        ),
    }


def _load_private_file(path: Path, code: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise LiveUatError(code)
    value = path.read_bytes().strip()
    if not value:
        raise LiveUatError(code)
    return value


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LiveUatError(code)
    return value


def _positive_int(value: Any, code: str) -> int:
    parsed = _nonnegative_int(value, code)
    if parsed <= 0:
        raise LiveUatError(code)
    return parsed


def _nonnegative_int(value: Any, code: str) -> int:
    if isinstance(value, bool):
        raise LiveUatError(code)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LiveUatError(code) from exc
    if parsed < 0:
        raise LiveUatError(code)
    return parsed


def _safe_id(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 255
        or any(char in normalized for char in "\r\n\x00")
    ):
        raise LiveUatError(code)
    return normalized


def _phone_suffix(value: Any, transport: str) -> str:
    normalized = str(value or "")
    if len(normalized) != 4 or not normalized.isdigit():
        raise LiveUatError(f"uat_{transport}_phone_suffix_invalid")
    return normalized


def _timestamp(value: Any, code: str) -> str:
    normalized = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveUatError(code) from exc
    if parsed.tzinfo is None:
        raise LiveUatError(code)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_digest(value: Any) -> str:
    normalized = str(value or "").strip().lower().removeprefix("sha256:")
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise LiveUatError("uat_image_digest_invalid")
    return "sha256:" + normalized


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--admin-token-file", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-source-sha", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    parser.add_argument("--signing-key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--observation-output", type=Path)
    parser.add_argument("--require-media", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    try:
        if not args.plan.is_file() or args.plan.is_symlink():
            raise LiveUatError("uat_plan_file_invalid")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if not isinstance(plan, dict):
            raise LiveUatError("uat_plan_root_invalid")
        token = _load_private_file(
            args.admin_token_file,
            "admin_token_file_invalid",
        ).decode("utf-8")
        signing_key = _load_private_file(
            args.signing_key_file,
            "signing_key_file_invalid",
        )
        client = NexusAdminClient(
            base_url=args.base_url,
            token=token,
        )
        observation, evidence = run_live_uat(
            plan,
            client=client,
            expected_source_sha=args.expected_source_sha,
            expected_image_digest=args.expected_image_digest,
            signing_key=signing_key,
            options=LiveUatOptions(
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                require_media=args.require_media,
            ),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if args.observation_output:
            args.observation_output.parent.mkdir(parents=True, exist_ok=True)
            args.observation_output.write_text(
                json.dumps(observation, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        EvidenceError,
        LiveUatError,
    ) as exc:
        print(f"whatsapp_live_uat_error:{exc}")
        return 2
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "source_sha": evidence["candidate"]["source_sha"],
                "transports": list(evidence["transports"]),
                "media_required": evidence["requirements"]["media_required"],
                "contains_secrets": False,
                "contains_full_phone_numbers": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
