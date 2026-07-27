from __future__ import annotations

from dataclasses import dataclass


BAILEYS_SIDECAR_TRANSPORT = "baileys_sidecar"
META_CLOUD_API_TRANSPORT = "meta_cloud_api"
SUPPORTED_WHATSAPP_TRANSPORTS = frozenset(
    {BAILEYS_SIDECAR_TRANSPORT, META_CLOUD_API_TRANSPORT}
)


@dataclass(frozen=True)
class WhatsAppTransportAdapter:
    name: str
    supports_inbound: bool = True
    supports_outbound: bool = True
    supports_status_probe: bool = True
    supports_interactive_binding: bool = False
    provider_managed_credentials: bool = False


_ADAPTERS = {
    BAILEYS_SIDECAR_TRANSPORT: WhatsAppTransportAdapter(
        name=BAILEYS_SIDECAR_TRANSPORT,
        supports_interactive_binding=True,
        provider_managed_credentials=False,
    ),
    META_CLOUD_API_TRANSPORT: WhatsAppTransportAdapter(
        name=META_CLOUD_API_TRANSPORT,
        supports_interactive_binding=True,
        provider_managed_credentials=True,
    ),
}


def normalize_whatsapp_transport(value: str) -> str:
    transport = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "baileys": BAILEYS_SIDECAR_TRANSPORT,
        "linked_device": BAILEYS_SIDECAR_TRANSPORT,
        "linked_device_sidecar": BAILEYS_SIDECAR_TRANSPORT,
        "meta": META_CLOUD_API_TRANSPORT,
        "cloud_api": META_CLOUD_API_TRANSPORT,
        "meta_cloud": META_CLOUD_API_TRANSPORT,
    }
    transport = aliases.get(transport, transport)
    if transport not in SUPPORTED_WHATSAPP_TRANSPORTS:
        raise ValueError("unsupported_whatsapp_transport")
    return transport


def resolve_whatsapp_transport(value: str) -> WhatsAppTransportAdapter:
    return _ADAPTERS[normalize_whatsapp_transport(value)]


def list_whatsapp_transports() -> tuple[WhatsAppTransportAdapter, ...]:
    return tuple(_ADAPTERS[name] for name in sorted(_ADAPTERS))
