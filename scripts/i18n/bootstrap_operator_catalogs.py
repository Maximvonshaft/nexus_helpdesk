from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

Locale = Literal["en", "de"]

CJK_RE = re.compile(r"[\u3400-\u9fff]")
ALPHA_RE = re.compile(r"[A-Za-z]")
PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}")
PROTECTED_TERM_RE = re.compile(
    r"Nexus OSR|Nexus|WebChat|WebCall|WhatsApp|LiveKit|Speedaf|PostgreSQL|"
    r"Provider|Runtime|MFA|SLA|API|URL|SMS|Email|PDF|POD|OTP|AI|Voice|Agent",
    re.IGNORECASE,
)
PROTECTED_TOKEN_RE = re.compile(
    rf"({PLACEHOLDER_RE.pattern}|{PROTECTED_TERM_RE.pattern})",
    re.IGNORECASE,
)
REPEATED_GARBAGE_RE = re.compile(r"([A-Za-z])\1{12,}", re.IGNORECASE)

# These are product-language decisions rather than model output. They are kept
# deliberately small and exact; all other copy remains traceable to the pinned
# translation models and is reviewed through the occurrence inventory.
SOURCE_OVERRIDES: dict[Locale, dict[str, str]] = {
    "en": {
        "6 位验证码": "6-digit verification code",
        "工单": "Ticket",
        "队列": "Queue",
        "案例": "Case",
        "坐席": "Agent",
        "两步验证": "Two-factor authentication",
        "账户设置": "Account settings",
        "退出登录": "Sign out",
        "工作范围": "Work scope",
        "主导航": "Main navigation",
        "跳到主要内容": "Skip to main content",
    },
    "de": {
        "6 位验证码": "6-stelliger Bestätigungscode",
        "工单": "Ticket",
        "队列": "Warteschlange",
        "案例": "Fall",
        "坐席": "Agent",
        "两步验证": "Zwei-Faktor-Authentifizierung",
        "账户设置": "Kontoeinstellungen",
        "退出登录": "Abmelden",
        "工作范围": "Arbeitsbereich",
        "主导航": "Hauptnavigation",
        "跳到主要内容": "Zum Hauptinhalt springen",
    },
}


@dataclass(frozen=True)
class Segment:
    value: str
    protected: bool


def split_segments(value: str) -> tuple[Segment, ...]:
    segments: list[Segment] = []
    cursor = 0
    for match in PROTECTED_TOKEN_RE.finditer(value):
        if match.start() > cursor:
            segments.append(Segment(value[cursor : match.start()], False))
        segments.append(Segment(match.group(0), True))
        cursor = match.end()
    if cursor < len(value):
        segments.append(Segment(value[cursor:], False))
    return tuple(segments) or (Segment(value, False),)


def split_boundary_whitespace(value: str) -> tuple[str, str, str]:
    prefix_match = re.match(r"^\s*", value)
    suffix_match = re.search(r"\s*$", value)
    prefix = prefix_match.group(0) if prefix_match else ""
    suffix = suffix_match.group(0) if suffix_match else ""
    end = len(value) - len(suffix) if suffix else len(value)
    core = value[len(prefix) : end]
    return prefix, core, suffix


def should_translate(value: str, source_locale: Literal["zh", "en"]) -> bool:
    if source_locale == "zh":
        return bool(CJK_RE.search(value))
    return bool(ALPHA_RE.search(value))


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def normalize_output(value: str) -> str:
    output = re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", value)
    output = re.sub(r"([（(])\s+", r"\1", output)
    output = re.sub(r"\s+([）)])", r"\1", output)
    output = re.sub(r"[ \t]{2,}", " ", output)
    return output.strip()


def translate_unique_segments(
    values: list[str],
    *,
    model_id: str,
    revision: str | None,
    source_locale: Literal["zh", "en"],
    batch_size: int,
) -> tuple[dict[str, str], dict[str, object]]:
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, revision=revision)
    model.eval()
    model.to("cpu")

    unique_cores = sorted(
        {
            core
            for value in values
            for segment in split_segments(value)
            if not segment.protected
            for _prefix, core, _suffix in [split_boundary_whitespace(segment.value)]
            if core and should_translate(core, source_locale)
        },
        key=lambda value: (len(value), value),
    )
    translated_cores: dict[str, str] = {}

    with torch.inference_mode():
        for source_batch in batches(unique_cores, batch_size):
            encoded = tokenizer(
                source_batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=256,
            )
            generated = model.generate(
                **encoded,
                max_new_tokens=160,
                num_beams=4,
                early_stopping=True,
            )
            decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
            if len(decoded) != len(source_batch):
                raise RuntimeError("translation_batch_cardinality_mismatch")
            for source, translated in zip(source_batch, decoded, strict=True):
                normalized = normalize_output(translated)
                translated_cores[source] = normalized or source

    translations: dict[str, str] = {}
    for value in values:
        parts: list[str] = []
        for segment in split_segments(value):
            if segment.protected:
                parts.append(segment.value)
                continue
            prefix, core, suffix = split_boundary_whitespace(segment.value)
            translated = (
                translated_cores.get(core, core)
                if core and should_translate(core, source_locale)
                else core
            )
            parts.append(f"{prefix}{translated}{suffix}")
        translations[value] = normalize_output("".join(parts))

    metadata = {
        "model_id": model_id,
        "requested_revision": revision,
        "resolved_revision": getattr(model.config, "_commit_hash", None),
        "tokenizer_revision": getattr(tokenizer, "_commit_hash", None),
        "source_locale": source_locale,
        "sources": len(values),
        "translated_segments": len(unique_cores),
    }
    return translations, metadata


def apply_overrides(
    translations: dict[str, str],
    *,
    locale: Locale,
) -> dict[str, str]:
    result = dict(translations)
    for source, translated in SOURCE_OVERRIDES[locale].items():
        if source in result:
            result[source] = translated
    return result


def validate_translation(source: str, translated: str, *, locale: Locale) -> list[str]:
    errors: list[str] = []
    if not translated.strip():
        errors.append("empty")
    if sorted(PLACEHOLDER_RE.findall(source)) != sorted(PLACEHOLDER_RE.findall(translated)):
        errors.append("placeholder_mismatch")
    if CJK_RE.search(translated):
        errors.append("cjk_residue")
    if REPEATED_GARBAGE_RE.search(translated):
        errors.append("repeated_garbage")
    if "ZXPH" in translated.upper() or "ZXTERM" in translated.upper():
        errors.append("marker_residue")
    if len(translated) > max(2400, len(source) * 12):
        errors.append("length_explosion")
    if locale == "de" and translated == source and CJK_RE.search(source):
        errors.append("untranslated")
    return errors


def validate_catalog(
    sources: list[str],
    translations: dict[str, str],
    *,
    locale: Locale,
) -> dict[str, list[str]]:
    failures = {
        source: errors
        for source in sources
        if (errors := validate_translation(source, translations.get(source, ""), locale=locale))
    }
    if failures:
        sample = list(failures.items())[:20]
        raise RuntimeError(f"catalog_validation_failed:{locale}:{sample}")
    return failures


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=48)
    args = parser.parse_args()

    raw_inventory = args.inventory.read_bytes()
    inventory = json.loads(raw_inventory)
    if inventory.get("schema_version") != 2:
        raise RuntimeError("unsupported_i18n_inventory_schema")
    messages = inventory.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RuntimeError("i18n_inventory_empty")

    sources = sorted(
        {str(message["source"]) for message in messages},
        key=lambda value: (len(value), value),
    )
    english_raw, en_metadata = translate_unique_segments(
        sources,
        model_id="Helsinki-NLP/opus-mt-zh-en",
        revision="cf109095479db38d6df799875e34039d4938aaa6",
        source_locale="zh",
        batch_size=args.batch_size,
    )
    english = apply_overrides(english_raw, locale="en")
    validate_catalog(sources, english, locale="en")

    english_values = [english[source] for source in sources]
    german_by_english, de_metadata = translate_unique_segments(
        english_values,
        model_id="Helsinki-NLP/opus-mt-en-de",
        revision=None,
        source_locale="en",
        batch_size=args.batch_size,
    )
    german_raw = {
        source: german_by_english[english[source]]
        for source in sources
    }
    german = apply_overrides(german_raw, locale="de")
    validate_catalog(sources, german, locale="de")

    source_catalogs = {"en": english, "de": german}
    keyed_catalogs = {
        locale: {
            str(message["key"]): translations[str(message["source"])]
            for message in messages
        }
        for locale, translations in source_catalogs.items()
    }

    output = args.output
    write_json(output / "source-en.review.json", english)
    write_json(output / "source-de.review.json", german)
    write_json(output / "catalog-en.review.json", keyed_catalogs["en"])
    write_json(output / "catalog-de.review.json", keyed_catalogs["de"])
    write_json(
        output / "bootstrap-metadata.json",
        {
            "schema_version": 2,
            "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest(),
            "inventory_messages": len(messages),
            "unique_sources": len(sources),
            "english": en_metadata,
            "german": {
                **de_metadata,
                "pivot_locale": "en",
            },
            "overrides": {
                locale: len(values)
                for locale, values in SOURCE_OVERRIDES.items()
            },
            "policy": "machine_bootstrap_requires_human_occurrence_review",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
