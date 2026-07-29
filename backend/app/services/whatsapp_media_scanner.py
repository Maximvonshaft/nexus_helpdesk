from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from .whatsapp_media_settings import get_whatsapp_media_settings


@dataclass(frozen=True)
class MediaScanResult:
    status: str
    engine: str
    signature: str | None = None


class MediaScanError(RuntimeError):
    pass


def scan_whatsapp_media(content: bytes) -> MediaScanResult:
    settings = get_whatsapp_media_settings()
    if not settings.enabled:
        raise MediaScanError("whatsapp_media_disabled")
    if settings.scanner != "clamav":
        raise MediaScanError("whatsapp_media_scanner_unavailable")
    try:
        with socket.create_connection(
            (settings.clamav_host, settings.clamav_port),
            timeout=float(settings.clamav_timeout_seconds),
        ) as client:
            client.settimeout(float(settings.clamav_timeout_seconds))
            client.sendall(b"zINSTREAM\x00")
            offset = 0
            chunk_size = 1024 * 1024
            while offset < len(content):
                chunk = content[offset : offset + chunk_size]
                client.sendall(struct.pack("!I", len(chunk)))
                client.sendall(chunk)
                offset += len(chunk)
            client.sendall(struct.pack("!I", 0))
            response = _read_response(client)
    except (OSError, TimeoutError) as exc:
        raise MediaScanError("whatsapp_media_scanner_unavailable") from exc

    normalized = response.strip().rstrip("\x00")
    if normalized.endswith(" OK"):
        return MediaScanResult(status="clean", engine="clamav")
    if " FOUND" in normalized:
        signature = normalized.rsplit(":", 1)[-1].replace("FOUND", "").strip()
        return MediaScanResult(
            status="infected",
            engine="clamav",
            signature=signature[:160] or "malware_detected",
        )
    raise MediaScanError("whatsapp_media_scanner_protocol_error")


def _read_response(client: socket.socket) -> str:
    chunks: list[bytes] = []
    total = 0
    while total < 8192:
        chunk = client.recv(min(4096, 8192 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\x00" in chunk or b"\n" in chunk:
            break
    if not chunks:
        raise MediaScanError("whatsapp_media_scanner_empty_response")
    try:
        return b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise MediaScanError("whatsapp_media_scanner_invalid_response") from exc
