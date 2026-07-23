from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

SUPPORTED_CUSTOMER_LANGUAGES = {
    "zh",
    "en",
    "de",
    "fr",
    "es",
    "pt",
    "it",
    "tr",
    "ar",
    "ru",
    "hi",
    "id",
    "vi",
    "th",
}

_TRACKING_REFERENCE_RE = re.compile(r"(?=.*\d)[A-Z0-9._-]{8,48}", re.I)


@dataclass(frozen=True)
class CustomerLanguageDecision:
    language: str | None
    source: str
    confidence: float


def normalize_customer_language(value: str | None) -> str | None:
    cleaned = str(value or "").strip().lower()
    if cleaned in SUPPORTED_CUSTOMER_LANGUAGES:
        return cleaned
    aliases = {
        "zh-cn": "zh",
        "zh_hans": "zh",
        "zh-hans": "zh",
        "cn": "zh",
        "chinese": "zh",
        "english": "en",
        "german": "de",
        "deutsch": "de",
    }
    return aliases.get(cleaned)


def detect_customer_language(text: str | None, *, explicit: str | None = None) -> CustomerLanguageDecision:
    explicit_language = normalize_customer_language(explicit)
    value = str(text or "").strip()
    reference_only = bool(value and _TRACKING_REFERENCE_RE.fullmatch(value))
    if explicit_language and not reference_only:
        return CustomerLanguageDecision(explicit_language, "explicit", 1.0)
    if not value or reference_only:
        return CustomerLanguageDecision(None, "empty_or_reference_only", 0.0)
    if any("\u4e00" <= ch <= "\u9fff" for ch in value):
        return CustomerLanguageDecision("zh", "script", 0.98)
    if any("\u0600" <= ch <= "\u06ff" for ch in value):
        return CustomerLanguageDecision("ar", "script", 0.98)
    if any("\u0400" <= ch <= "\u04ff" for ch in value):
        return CustomerLanguageDecision("ru", "script", 0.98)
    return _detect_latin_language(value)


def resolve_conversation_language(
    text: str | None,
    *,
    previous_customer_messages: Iterable[str | None] = (),
) -> CustomerLanguageDecision:
    """Resolve the latest reliable customer language without blocking language switches."""
    current = detect_customer_language(text)
    if current.language:
        return current
    for previous in reversed(tuple(previous_customer_messages)):
        decision = detect_customer_language(previous)
        if decision.language:
            return CustomerLanguageDecision(
                decision.language,
                f"conversation_history:{decision.source}",
                decision.confidence,
            )
    return current


def _detect_latin_language(text: str) -> CustomerLanguageDecision:
    words = re.findall(r"[a-zA-ZäöüÄÖÜßàâçéèêëîïôùûüÿñáíóúãõ]+", text)
    if not words:
        return CustomerLanguageDecision(None, "no_language_signal", 0.0)
    lowered = [word.lower() for word in words]
    joined = f" {' '.join(lowered)} "

    if re.search(r"[äöüß]", joined):
        return CustomerLanguageDecision("de", "latin_diacritic", 0.96)

    scores = {
        "de": _score_markers(
            joined,
            (
                " der ",
                " die ",
                " das ",
                " ist ",
                " sind ",
                " und ",
                " oder ",
                " bitte ",
                " hallo ",
                " kannst ",
                " können ",
                " koennen ",
                " welche ",
                " welcher ",
                " welchen ",
                " zustand ",
                " sendung ",
                " paket ",
                " schauen ",
                " nicht ",
                " angekommen ",
            ),
        ),
        "en": _score_markers(
            joined,
            (
                " the ",
                " is ",
                " are ",
                " where ",
                " what ",
                " which ",
                " can ",
                " could ",
                " please ",
                " hello ",
                " hi ",
                " thanks ",
                " thank ",
                " need ",
                " help ",
                " human ",
                " review ",
                " support ",
                " agent ",
                " speak ",
                " want ",
                " parcel ",
                " package ",
                " shipment ",
                " delivery ",
                " tracking ",
                " order ",
            ),
        ),
        "fr": _score_markers(joined, (" le ", " la ", " les ", " est ", " bonjour ", " colis ", " livraison ", " suivi ")),
        "es": _score_markers(joined, (" el ", " la ", " los ", " está ", " esta ", " hola ", " paquete ", " entrega ", " seguimiento ")),
        "it": _score_markers(joined, (" il ", " lo ", " la ", " ciao ", " pacco ", " consegna ", " tracciamento ")),
        # Single-letter articles such as "a" and "o" are not language evidence:
        # they occur in English and several other supported languages. Portuguese
        # must win from language-specific words or diacritics, never from an article.
        "pt": _score_markers(
            joined,
            (
                " olá ",
                " ola ",
                " pacote ",
                " entrega ",
                " rastreamento ",
                " preciso ",
                " ajuda ",
                " humano ",
                " agente ",
                " por favor ",
                " obrigado ",
                " obrigada ",
                " meu ",
                " minha ",
                " não ",
                " nao ",
                " quero ",
                " falar ",
            ),
        ),
    }
    best_language, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0:
        return CustomerLanguageDecision(best_language, "latin_marker", min(0.95, 0.62 + best_score * 0.08))
    if len(lowered) <= 2 and all(re.fullmatch(r"[a-z]+", word) for word in lowered):
        return CustomerLanguageDecision("en", "short_latin_default", 0.55)
    return CustomerLanguageDecision(None, "ambiguous_latin", 0.0)


def _score_markers(joined: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in joined)
