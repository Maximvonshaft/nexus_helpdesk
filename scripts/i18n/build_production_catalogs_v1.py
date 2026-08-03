from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

import torch
from huggingface_hub import model_info
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

OVERRIDES_SHA256 = "23b75d4c2fdea1c4ccc51567d5fb9349a992857dc7ddfbd93892abb4614d3aab"
MODEL_SPECS = {
    "en": {
        "model_id": "Helsinki-NLP/opus-mt-zh-en",
        "revision": "cf109095479db38d6df799875e34039d4938aaa6",
        "license_allow": {"cc-by-4.0", "apache-2.0"},
    },
    "de": {
        "model_id": "Helsinki-NLP/opus-mt-en-de",
        "revision": "6183067f769a302e3861815543b9f312c71b0ca4",
        "license_allow": {"cc-by-4.0", "apache-2.0"},
    },
    "cnr": {
        "model_id": "Helsinki-NLP/opus-mt-en-zls",
        "revision": None,
        "license_allow": {"apache-2.0"},
        "prefix": ">>srp_Latn<< ",
    },
}

CJK_RE = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}")
MARKER_RE = re.compile(r"ZXPH(\d+)ZX")
GARBAGE_RE = re.compile(r"([A-Za-z])\1{12,}")
PROTECTED_TOKEN_RE = re.compile(
    r"\{\{\d+\}\}"
    r"|https?://[^\s]+"
    r"|\b(?:Nexus|Nexus OSR|LiveKit|WhatsApp|Baileys|Meta Cloud API|Meta App|WABA|Sidecar|Speedaf|Provider|PostgreSQL|MFA|TOTP|DTMF|API|URL|UTC|ID|Inbox|Outbox|Conversation|ChannelAccount|Access Token|App Review|Advanced Access)\b",
    re.IGNORECASE,
)


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize(value: str) -> str:
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def placeholders(value: str) -> list[str]:
    return sorted(PLACEHOLDER_RE.findall(value))


def protect(value: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    cursor = 0
    parts: list[str] = []
    for index, match in enumerate(PROTECTED_TOKEN_RE.finditer(value)):
        if match.start() > cursor:
            parts.append(value[cursor : match.start()])
        marker = f"ZXPH{index}ZX"
        mapping[marker] = match.group(0)
        parts.append(marker)
        cursor = match.end()
    parts.append(value[cursor:])
    return "".join(parts), mapping


def restore(value: str, mapping: dict[str, str]) -> str | None:
    output = value
    for marker, original in mapping.items():
        if marker not in output:
            return None
        output = output.replace(marker, original)
    if MARKER_RE.search(output):
        return None
    return normalize(output)


def segmented_parts(value: str) -> list[tuple[str, bool]]:
    output: list[tuple[str, bool]] = []
    cursor = 0
    for match in PROTECTED_TOKEN_RE.finditer(value):
        if match.start() > cursor:
            output.append((value[cursor : match.start()], False))
        output.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(value):
        output.append((value[cursor:], False))
    return output


class MarianTranslator:
    def __init__(self, *, model_id: str, revision: str | None, prefix: str = "") -> None:
        self.model_id = model_id
        self.revision = revision
        self.prefix = prefix
        self.tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model_id, revision=revision)
        self.model.eval()
        self.model.to("cpu")

    def translate(self, values: list[str], *, batch_size: int) -> dict[str, str]:
        unique = sorted(set(values), key=lambda item: (len(item), item))
        output: dict[str, str] = {}
        with torch.inference_mode():
            for group in batches(unique, batch_size):
                model_input = [f"{self.prefix}{value}" if self.prefix else value for value in group]
                encoded = self.tokenizer(model_input, return_tensors="pt", padding=True, truncation=True, max_length=384)
                generated = self.model.generate(
                    **encoded,
                    max_new_tokens=256,
                    num_beams=5,
                    early_stopping=True,
                    renormalize_logits=True,
                )
                decoded = self.tokenizer.batch_decode(generated, skip_special_tokens=True)
                if len(decoded) != len(group):
                    raise RuntimeError("translation_batch_cardinality_mismatch")
                for source, translated in zip(group, decoded, strict=True):
                    output[source] = normalize(translated) or source
        return output


def translate_sources(
    sources: list[str],
    *,
    translator: MarianTranslator,
    batch_size: int,
) -> tuple[dict[str, str], list[str]]:
    protected_values: dict[str, tuple[str, dict[str, str]]] = {source: protect(source) for source in sources}
    direct = translator.translate([value for value, _ in protected_values.values()], batch_size=batch_size)
    output: dict[str, str] = {}
    fallback_sources: list[str] = []
    for source, (protected, mapping) in protected_values.items():
        restored = restore(direct[protected], mapping)
        if restored is None or placeholders(restored) != placeholders(source):
            fallback_sources.append(source)
        else:
            output[source] = restored

    segment_cores = []
    for source in fallback_sources:
        for segment, is_protected in segmented_parts(source):
            core = segment.strip()
            if not is_protected and core:
                segment_cores.append(core)
    translated_cores = translator.translate(segment_cores, batch_size=batch_size) if segment_cores else {}
    for source in fallback_sources:
        parts: list[str] = []
        for segment, is_protected in segmented_parts(source):
            if is_protected:
                parts.append(segment)
                continue
            prefix = segment[: len(segment) - len(segment.lstrip())]
            suffix = segment[len(segment.rstrip()) :]
            core = segment.strip()
            parts.append(f"{prefix}{translated_cores.get(core, core)}{suffix}")
        output[source] = normalize("".join(parts))
    return output, fallback_sources


def montenegrinize(value: str) -> str:
    replacements = [
        (r"\bsledeć", "sljedeć"),
        (r"\bposlednj", "posljednj"),
        (r"\bovde\b", "ovdje"),
        (r"\bgde\b", "gdje"),
        (r"\bvreme\b", "vrijeme"),
        (r"\buspeh\b", "uspjeh"),
        (r"\buspešn", "uspješn"),
        (r"\bdešav", "događ"),
        (r"\bnalog za e-poštu\b", "nalog e-pošte"),
    ]
    output = value
    for pattern, replacement in replacements:
        output = re.sub(pattern, replacement, output, flags=re.IGNORECASE)
    return normalize(output)


def load_overrides(path: Path) -> dict[str, dict[str, str]]:
    encoded = path.read_text(encoding="utf-8").strip()
    raw = gzip.decompress(base64.b64decode(encoded))
    digest = hashlib.sha256(raw).hexdigest()
    if digest != OVERRIDES_SHA256:
        raise RuntimeError(f"override_digest_mismatch:{digest}")
    value = json.loads(raw)
    if set(value) != {"en", "de", "cnr"}:
        raise RuntimeError("override_locale_set_invalid")
    return value


def model_evidence(locale: str) -> dict[str, str | None]:
    spec = MODEL_SPECS[locale]
    info = model_info(spec["model_id"], revision=spec.get("revision"))
    license_value = str(getattr(info.card_data, "license", "") or "").strip().lower()
    if license_value not in spec["license_allow"]:
        raise RuntimeError(f"model_license_not_approved:{locale}:{license_value or 'missing'}")
    return {
        "model_id": spec["model_id"],
        "requested_revision": spec.get("revision"),
        "resolved_revision": str(info.sha),
        "license": license_value,
    }


def validate(source_values: list[str], translations: dict[str, str], *, locale: str) -> None:
    failures: list[tuple[str, str]] = []
    for source in source_values:
        translated = translations.get(source)
        if not isinstance(translated, str) or not translated.strip():
            failures.append((source, "empty"))
            continue
        if CJK_RE.search(translated):
            failures.append((source, "cjk_residue"))
        if MARKER_RE.search(translated) or "ZXPH" in translated:
            failures.append((source, "marker_residue"))
        if placeholders(source) != placeholders(translated):
            failures.append((source, "placeholder_mismatch"))
        if GARBAGE_RE.search(translated):
            failures.append((source, "repeated_garbage"))
        if translated == source:
            failures.append((source, "untranslated"))
    if failures:
        raise RuntimeError(f"catalog_validation_failed:{locale}:{failures[:30]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=48)
    args = parser.parse_args()

    raw_inventory = args.inventory.read_bytes()
    inventory = json.loads(raw_inventory)
    messages = inventory.get("messages")
    if inventory.get("schema_version") != 2 or not isinstance(messages, list) or not messages:
        raise RuntimeError("invalid_inventory")
    sources = sorted({str(message["source"]) for message in messages}, key=lambda value: (len(value), value))
    overrides = load_overrides(args.overrides)

    evidence = {locale: model_evidence(locale) for locale in MODEL_SPECS}
    en_spec = MODEL_SPECS["en"]
    en_translator = MarianTranslator(model_id=en_spec["model_id"], revision=en_spec["revision"])
    source_en, fallback_en = translate_sources(sources, translator=en_translator, batch_size=args.batch_size)
    source_en.update({source: text for source, text in overrides["en"].items() if source in source_en})
    validate(sources, source_en, locale="en")

    de_spec = MODEL_SPECS["de"]
    de_translator = MarianTranslator(model_id=de_spec["model_id"], revision=de_spec["revision"])
    source_de_raw, fallback_de = translate_sources(
        [source_en[source] for source in sources], translator=de_translator, batch_size=args.batch_size
    )
    source_de = {source: source_de_raw[source_en[source]] for source in sources}
    source_de.update({source: text for source, text in overrides["de"].items() if source in source_de})
    validate(sources, source_de, locale="de")

    cnr_spec = MODEL_SPECS["cnr"]
    cnr_translator = MarianTranslator(
        model_id=cnr_spec["model_id"], revision=cnr_spec["revision"], prefix=cnr_spec["prefix"]
    )
    source_cnr_raw, fallback_cnr = translate_sources(
        [source_en[source] for source in sources], translator=cnr_translator, batch_size=args.batch_size
    )
    source_cnr = {source: montenegrinize(source_cnr_raw[source_en[source]]) for source in sources}
    source_cnr.update({source: text for source, text in overrides["cnr"].items() if source in source_cnr})
    validate(sources, source_cnr, locale="cnr")

    source_catalogs = {"en": source_en, "de": source_de, "cnr": source_cnr}
    catalogs = {
        locale: {
            str(message["key"]): source_catalog[str(message["source"])]
            for message in messages
        }
        for locale, source_catalog in source_catalogs.items()
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for locale, source_catalog in source_catalogs.items():
        write_json(args.output / f"source-{locale}.review.json", source_catalog)
        write_json(args.output / f"catalog-{locale}.review.json", catalogs[locale])
    write_json(
        args.output / "production-catalog-metadata.json",
        {
            "schema_version": 1,
            "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest(),
            "inventory_messages": len(messages),
            "unique_sources": len(sources),
            "override_sha256": OVERRIDES_SHA256,
            "override_counts": {locale: len(values) for locale, values in overrides.items()},
            "models": evidence,
            "fallback_counts": {"en": len(fallback_en), "de": len(fallback_de), "cnr": len(fallback_cnr)},
            "policy": "production_candidate_requires_human_semantic_review_and_exact_head_acceptance",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
