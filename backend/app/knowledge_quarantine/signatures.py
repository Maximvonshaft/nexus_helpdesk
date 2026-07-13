from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignatureEvidence:
    declared_mime_type: str | None
    detected_mime_type: str | None
    status: str
    reason: str

    def as_safe_dict(self) -> dict[str, str | None]:
        return {
            "declared_mime_type": self.declared_mime_type,
            "detected_mime_type": self.detected_mime_type,
            "status": self.status,
            "reason": self.reason,
        }


_DECLARED_EQUIVALENTS: dict[str, set[str]] = {
    "application/pdf": {"application/pdf"},
    "image/png": {"image/png"},
    "image/jpeg": {"image/jpeg"},
    "image/webp": {"image/webp"},
    "application/zip": {"application/zip"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {"application/zip"},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"application/zip"},
    "text/plain": {"text/plain"},
    "text/markdown": {"text/plain"},
    "text/csv": {"text/plain"},
    "text/html": {"text/plain"},
}


def _detect(prefix: bytes) -> str | None:
    sample = bytes(prefix[:8192])
    if sample.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if sample.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(sample) >= 12 and sample[:4] == b"RIFF" and sample[8:12] == b"WEBP":
        return "image/webp"
    if sample.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if sample.startswith(b"MZ") or sample.startswith(b"\x7fELF"):
        return "application/x-executable"
    if not sample:
        return None
    for encoding in ("utf-8", "utf-16", "gb18030", "gbk"):
        try:
            sample.decode(encoding)
            return "text/plain"
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def evaluate_file_signature(*, declared_mime_type: str | None, prefix: bytes) -> SignatureEvidence:
    declared = str(declared_mime_type or "").split(";", 1)[0].strip().lower() or None
    detected = _detect(prefix)
    if declared is None:
        return SignatureEvidence(
            declared_mime_type=None,
            detected_mime_type=detected,
            status="unsupported",
            reason="signature.declared_type_missing",
        )
    allowed = _DECLARED_EQUIVALENTS.get(declared)
    if allowed is None:
        return SignatureEvidence(
            declared_mime_type=declared[:120],
            detected_mime_type=detected,
            status="unsupported",
            reason="signature.declared_type_unsupported",
        )
    if detected is None:
        return SignatureEvidence(
            declared_mime_type=declared[:120],
            detected_mime_type=None,
            status="mismatch",
            reason="signature.content_type_unknown",
        )
    if detected not in allowed:
        return SignatureEvidence(
            declared_mime_type=declared[:120],
            detected_mime_type=detected,
            status="mismatch",
            reason="signature.declared_detected_mismatch",
        )
    return SignatureEvidence(
        declared_mime_type=declared[:120],
        detected_mime_type=detected,
        status="match",
        reason="signature.match",
    )
