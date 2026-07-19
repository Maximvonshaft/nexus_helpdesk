from __future__ import annotations

import re
import unicodedata


SUPPORTED_TTS_LANGUAGES = frozenset({"de", "en", "es", "fr", "it", "ja", "ko", "pt", "ru", "zh"})
FALLBACK_REPROMPT_EN = "Sorry, I didn't catch that clearly. Please say it again."


def normalize_language(value: str | None) -> str:
    language = str(value or "").strip().lower().replace("_", "-")
    aliases = {"d": "de", "a": "en", "b": "en", "ch": "zh"}
    return aliases.get(language, language.split("-", 1)[0])


def transcript_quality_reason(text: str, language: str | None) -> str | None:
    """Reject obvious cross-script ASR hallucinations before they reach the LLM."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return "empty"
    if len(normalized) > 2_000:
        return "too_long"

    letters = [char for char in normalized if unicodedata.category(char).startswith("L")]
    if not letters:
        return "no_letters"
    target = normalize_language(language)
    han = sum("\u4e00" <= char <= "\u9fff" for char in letters)
    kana = sum(("\u3040" <= char <= "\u30ff") for char in letters)
    hangul = sum("\uac00" <= char <= "\ud7af" for char in letters)
    latin = sum("LATIN" in unicodedata.name(char, "") for char in letters)
    total = len(letters)

    if target == "zh" and (han < 2 or han / total < 0.55):
        return "script_mismatch"
    if target == "ja" and (han + kana < 2 or (han + kana) / total < 0.55):
        return "script_mismatch"
    if target == "ko" and (hangul < 2 or hangul / total < 0.55):
        return "script_mismatch"
    if target in SUPPORTED_TTS_LANGUAGES - {"zh", "ja", "ko"} and (han + kana + hangul) / total > 0.2:
        return "script_mismatch"
    return None


def split_tts_chunks(text: str, *, max_chars: int = 180) -> list[str]:
    """Make short, speakable chunks without dropping or reordering answer text."""
    source = " ".join(str(text or "").split())
    if not source:
        return []
    limit = max(40, min(int(max_chars), 300))
    clauses = [part.strip() for part in re.split(r"(?<=[.!?。！？;；:：])\s*", source) if part.strip()]
    chunks: list[str] = []
    current = ""
    for clause in clauses:
        if len(clause) > limit:
            words = re.findall(r"\S+\s*", clause)
            pieces: list[str] = []
            part = ""
            for word in words:
                if part and len(part) + len(word) > limit:
                    pieces.append(part.strip())
                    part = word
                else:
                    part += word
            if part:
                pieces.append(part.strip())
        else:
            pieces = [clause]
        for piece in pieces:
            if current and len(current) + 1 + len(piece) > limit:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks
