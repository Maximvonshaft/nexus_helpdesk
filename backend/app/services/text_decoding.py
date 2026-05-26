from __future__ import annotations

TEXT_DECODING_CANDIDATES = (
    "utf-8-sig",
    "gb18030",
    "gbk",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
)
_ALLOWED_CONTROL_CHARS = {"\t", "\n", "\r", "\f"}
_MIN_TEXT_QUALITY_SCORE = 0.62


class TextDecodingError(ValueError):
    pass


def decode_text_upload(content: bytes) -> str:
    if not content:
        return ""

    best_text = ""
    best_score = -1.0
    for encoding in _ordered_candidates(content):
        for candidate in _candidate_byte_sequences(content, encoding):
            try:
                text = candidate.decode(encoding)
            except UnicodeError:
                continue
            if _looks_binary_text(text):
                continue
            score = _text_quality_score(text)
            if score > best_score:
                best_text = text
                best_score = score
            if score >= 0.92:
                return text

    if best_text and best_score >= _MIN_TEXT_QUALITY_SCORE:
        return best_text
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
    # Try East Asian encodings before UTF-16 without BOM. GBK/GB18030 bytes can
    # legally decode as UTF-16LE into unreadable Hangul/private-use-looking text.
    return TEXT_DECODING_CANDIDATES


def _candidate_byte_sequences(content: bytes, encoding: str):
    yield content
    # Docker storage MIME sniffing only reads a sample. Samples can end in the
    # middle of a multibyte sequence, especially for UTF-16 or GB18030. Trimming
    # a tiny suffix prevents false negatives while keeping full parse strict.
    if len(content) > 32 and encoding in {"utf-8-sig", "gb18030", "gbk", "utf-16-le", "utf-16-be"}:
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


def _text_quality_score(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 1.0

    good = 0.0
    bad = 0.0
    high_confidence = 0
    for ch in stripped:
        code = ord(ch)
        if ch.isspace() or ch in ",.;:!?，。；：！？、-_/()[]{}'\"":
            good += 1.0
            high_confidence += 1
        elif 0x20 <= code <= 0x7E:
            good += 1.0
            high_confidence += 1
        elif 0x4E00 <= code <= 0x9FFF:
            good += 1.0
            high_confidence += 1
        elif 0x3400 <= code <= 0x4DBF:
            good += 0.35
        elif 0x00A0 <= code <= 0x024F:
            good += 0.35
        elif 0xAC00 <= code <= 0xD7AF:
            bad += 1.0
        elif 0xE000 <= code <= 0xF8FF:
            bad += 1.0
        elif code < 32 and ch not in _ALLOWED_CONTROL_CHARS:
            bad += 1.0
        elif code >= 0xA000:
            bad += 0.8
        else:
            bad += 0.7

    if high_confidence == 0:
        return 0.0
    total = good + bad
    if total <= 0:
        return 0.0
    return good / total
