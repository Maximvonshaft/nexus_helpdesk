from __future__ import annotations

TEXT_DECODING_CANDIDATES = (
    "utf-8-sig",
    "utf-16",
    "gb18030",
    "gbk",
    "utf-16-le",
    "utf-16-be",
)
_ALLOWED_CONTROL_CHARS = {"\t", "\n", "\r", "\f"}


class TextDecodingError(ValueError):
    pass


def decode_text_upload(content: bytes) -> str:
    if not content:
        return ""

    for encoding in TEXT_DECODING_CANDIDATES:
        for candidate in _candidate_byte_sequences(content, encoding):
            try:
                text = candidate.decode(encoding)
            except UnicodeError:
                continue
            if _looks_binary_text(text):
                continue
            return text

    raise TextDecodingError("Uploaded text file must be encoded as UTF-8, UTF-16, GB18030, or GBK")


def is_supported_text_upload(content: bytes) -> bool:
    try:
        decode_text_upload(content)
        return True
    except TextDecodingError:
        return False


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
