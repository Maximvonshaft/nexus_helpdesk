from __future__ import annotations

TEXT_DECODING_CANDIDATES = (
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
)
_ALLOWED_CONTROL_CHARS = {"\t", "\n", "\r", "\f"}


class TextDecodingError(ValueError):
    pass


def decode_text_upload(content: bytes) -> str:
    if not content:
        return ""

    for encoding in _ordered_candidates(content):
        for candidate in _candidate_byte_sequences(content, encoding):
            try:
                text = candidate.decode(encoding)
            except UnicodeError:
                continue
            if _looks_binary_text(text):
                continue
            if _looks_suspiciously_wrong(text):
                continue
            return text

    raise TextDecodingError("Uploaded text file must be encoded as UTF-8, UTF-16, GB18030, or GBK")


def is_supported_text_upload(content: bytes) -> bool:
    try:
        decode_text_upload(content)
        return True
    except TextDecodingError:
        return False


def _ordered_candidates(content: bytes) -> tuple[str, ...]:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return ("utf-16", "utf-8-sig", "utf-16-le", "utf-16-be", "gb18030", "gbk")
    return TEXT_DECODING_CANDIDATES


def _candidate_byte_sequences(content: bytes, encoding: str):
    yield content
    # Docker storage MIME sniffing only reads a sample. Samples can end in the
    # middle of a multibyte sequence, especially for UTF-16 or GB18030. Trimming
    # a tiny suffix prevents false negatives while keeping full parse strict.
    if len(content) > 32 and encoding in {"gb18030", "gbk", "utf-16-le", "utf-16-be"}:
        for trim in (1, 2, 3):
            if len(content) > trim:
                yield content[:-trim]


def _looks_binary_text(text: str) -> bool:
    if not text:
        return False
    if "\x00" in text:
        return True
    bad_controls = 0
    visible = 0
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in _ALLOWED_CONTROL_CHARS:
            bad_controls += 1
        if not ch.isspace():
            visible += 1
    if visible == 0:
        return False
    return bad_controls / max(len(text), 1) > 0.02


def _looks_suspiciously_wrong(text: str) -> bool:
    # A common false positive is decoding GBK/GB18030 bytes as UTF-16LE/BE.
    # That often yields mostly CJK-extension or private-use-looking glyphs with
    # no separators. Reject only very short, separator-free, high-codepoint noise.
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    separators = sum(1 for ch in stripped if ch.isspace() or ch in ",.;:!?，。；：！？、-_/()[]{}")
    high_noise = sum(1 for ch in stripped if ord(ch) >= 0xA000)
    return separators == 0 and high_noise / max(len(stripped), 1) > 0.6
