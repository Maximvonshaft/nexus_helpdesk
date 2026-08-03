from __future__ import annotations

import argparse
import json
import re

from contextual_catalog_contract_v2 import (
    LOCALES,
    authority_failures,
    load_base_overrides,
    load_critical_contract,
    load_inventory,
    sha256_bytes,
    write_json,
)

def prepare(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", args.product_head):
        raise RuntimeError(f"product_head_invalid:{args.product_head}")
    inventory_raw, _inventory, messages, sources = load_inventory(args.inventory)
    base_raw, base = load_base_overrides(args.base_overrides)
    critical_raw, _critical, critical = load_critical_contract(args.critical_contract)
    source_set = set(sources)
    orphaned_base = sorted(set(base["en"]) - source_set)
    orphaned_critical = sorted(set(critical["en"]) - source_set)
    if orphaned_critical:
        raise RuntimeError(f"critical_contract_sources_missing_from_inventory:{orphaned_critical}")

    active_base = {locale: {s: t for s, t in base[locale].items() if s in source_set} for locale in LOCALES}
    active_critical = {locale: {s: t for s, t in critical[locale].items() if s in source_set} for locale in LOCALES}
    authority_errors = authority_failures("base_override", active_base, source_set)
    authority_errors.extend(authority_failures("critical_contract", active_critical, source_set))
    if authority_errors:
        raise RuntimeError(f"reviewed_authority_validation_failed:{authority_errors[:50]}")

    generation_sources = [
        source for source in sources
        if source not in active_base["en"] and source not in active_critical["en"]
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    generation_input_sha256: dict[str, str] = {}
    for locale in LOCALES:
        input_path = args.output / f"generation-input-{locale}.json"
        write_json(
            input_path,
            [{"id": f"s{index:05d}", "source": source} for index, source in enumerate(generation_sources)],
        )
        generation_input_sha256[locale] = sha256_bytes(input_path.read_bytes())
    write_json(args.output / "active-base-overrides.json", active_base)
    write_json(args.output / "active-critical-overrides.json", active_critical)
    (args.output / "i18n-inventory.json").write_bytes(inventory_raw)
    (args.output / "i18n-critical-catalog.v1.json").write_bytes(critical_raw)
    metadata = {
        "schema": "nexus.i18n-contextual-catalog-preparation.v2",
        "product_inventory_head_sha": args.product_head,
        "inventory_sha256": sha256_bytes(inventory_raw),
        "inventory_messages": len(messages),
        "unique_sources": len(sources),
        "base_override_sha256": sha256_bytes(base_raw),
        "base_override_counts": {locale: len(base[locale]) for locale in LOCALES},
        "active_base_override_counts": {locale: len(active_base[locale]) for locale in LOCALES},
        "orphaned_base_sources": orphaned_base,
        "critical_contract_sha256": sha256_bytes(critical_raw),
        "critical_contract_counts": {locale: len(critical[locale]) for locale in LOCALES},
        "active_critical_counts": {locale: len(active_critical[locale]) for locale in LOCALES},
        "orphaned_critical_sources": orphaned_critical,
        "authority_overlap_counts": {
            locale: len(set(active_base[locale]) & set(active_critical[locale]))
            for locale in LOCALES
        },
        "generated_source_counts": {locale: len(generation_sources) for locale in LOCALES},
        "generation_input_sha256": generation_input_sha256,
    }
    write_json(args.output / "preparation-metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


