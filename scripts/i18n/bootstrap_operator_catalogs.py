from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}")
PROTECTED_TERM_RE = re.compile(
    r"Nexus OSR|Nexus|WebChat|WebCall|WhatsApp|LiveKit|Speedaf|PostgreSQL|"
    r"Provider|Runtime|MFA|SLA|API|URL|SMS|Email|PDF|POD|OTP|AI|Voice|Agent",
    re.IGNORECASE,
)
MARKER_RE = re.compile(r"ZX(?:PH|TERM)(\d+)ZX", re.IGNORECASE)


@dataclass(frozen=True)
class ProtectedText:
    value: str
    replacements: tuple[str, ...]


def protect_text(value: str) -> ProtectedText:
    replacements: list[str] = []

    def replace_placeholder(match: re.Match[str]) -> str:
        index = len(replacements)
        replacements.append(match.group(0))
        return f"ZXPH{index}ZX"

    def replace_term(match: re.Match[str]) -> str:
        index = len(replacements)
        replacements.append(match.group(0))
        return f"ZXTERM{index}ZX"

    protected = PLACEHOLDER_RE.sub(replace_placeholder, value)
    protected = PROTECTED_TERM_RE.sub(replace_term, protected)
    return ProtectedText(protected, tuple(replacements))


def restore_text(value: str, replacements: tuple[str, ...]) -> str:
    def restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        return replacements[index] if index < len(replacements) else match.group(0)

    output = MARKER_RE.sub(restore, value)
    output = re.sub(r"\s+([,.;:!?，。；：！？])", r"\1", output)
    output = re.sub(r"([（(])\s+", r"\1", output)
    output = re.sub(r"\s+([）)])", r"\1", output)
    return output.strip()


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def translate_sources(
    sources: list[str],
    *,
    model_id: str,
    batch_size: int,
) -> tuple[dict[str, str], dict[str, object]]:
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
    model.eval()
    model.to("cpu")

    protected_by_source = {source: protect_text(source) for source in sources}
    translations: dict[str, str] = {}
    failures: list[str] = []

    with torch.inference_mode():
        for source_batch in batches(sources, batch_size):
            protected_batch = [protected_by_source[source].value for source in source_batch]
            encoded = tokenizer(
                protected_batch,
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
                restored = restore_text(
                    translated,
                    protected_by_source[source].replacements,
                )
                if not restored:
                    failures.append(source)
                    restored = source
                translations[source] = restored

    metadata = {
        "model_id": model_id,
        "resolved_revision": getattr(model.config, "_commit_hash", None),
        "tokenizer_revision": getattr(tokenizer, "_commit_hash", None),
        "sources": len(sources),
        "failures": failures,
    }
    return translations, metadata


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
    english, en_metadata = translate_sources(
        sources,
        model_id="Helsinki-NLP/opus-mt-zh-en",
        batch_size=args.batch_size,
    )
    german, de_metadata = translate_sources(
        sources,
        model_id="Helsinki-NLP/opus-mt-zh-de",
        batch_size=args.batch_size,
    )

    source_catalogs = {
        "en": english,
        "de": german,
    }
    keyed_catalogs = {
        locale: {
            str(message["key"]): translations[str(message["source"])]
            for message in messages
        }
        for locale, translations in source_catalogs.items()
    }

    output = args.output
    write_json(output / "source-en.raw.json", english)
    write_json(output / "source-de.raw.json", german)
    write_json(output / "catalog-en.raw.json", keyed_catalogs["en"])
    write_json(output / "catalog-de.raw.json", keyed_catalogs["de"])
    write_json(
        output / "bootstrap-metadata.json",
        {
            "schema_version": 1,
            "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest(),
            "inventory_messages": len(messages),
            "unique_sources": len(sources),
            "english": en_metadata,
            "german": de_metadata,
            "policy": "machine_bootstrap_requires_human_occurrence_review",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
