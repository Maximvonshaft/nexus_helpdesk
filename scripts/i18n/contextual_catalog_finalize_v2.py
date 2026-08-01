from __future__ import annotations

import argparse
import json
import re

from contextual_catalog_contract_v2 import (
    LOCALES,
    collision_failures,
    load_base_overrides,
    load_critical_contract,
    load_inventory,
    read_json,
    sha256_bytes,
    validation_reasons,
    write_json,
)
from contextual_catalog_generation_v2 import (
    APPROVED_LICENSE,
    MODEL_ID,
    REQUESTED_REVISION,
)

def require_preparation_binding(
    preparation: dict,
    *,
    args: argparse.Namespace,
    inventory_raw: bytes,
    messages: list[dict],
    sources: list[str],
    base_raw: bytes,
    base: dict[str, dict[str, str]],
    critical_raw: bytes,
    critical: dict[str, dict[str, str]],
) -> None:
    expected = {
        "schema": "nexus.i18n-contextual-catalog-preparation.v2",
        "product_inventory_head_sha": args.product_head,
        "inventory_sha256": sha256_bytes(inventory_raw),
        "inventory_messages": len(messages),
        "unique_sources": len(sources),
        "base_override_sha256": sha256_bytes(base_raw),
        "base_override_counts": {locale: len(base[locale]) for locale in LOCALES},
        "critical_contract_sha256": sha256_bytes(critical_raw),
        "critical_contract_counts": {locale: len(critical[locale]) for locale in LOCALES},
    }
    failures = {
        key: {"expected": value, "actual": preparation.get(key)}
        for key, value in expected.items()
        if preparation.get(key) != value
    }
    if failures:
        raise RuntimeError(f"preparation_binding_invalid:{failures}")
    if preparation.get("orphaned_critical_sources") != []:
        raise RuntimeError(f"preparation_orphaned_critical_invalid:{preparation.get('orphaned_critical_sources')}")


def finalize(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", args.product_head):
        raise RuntimeError(f"product_head_invalid:{args.product_head}")
    inventory_raw, _inventory, messages, sources = load_inventory(args.inventory)
    base_raw, base = load_base_overrides(args.base_overrides)
    critical_raw, _critical, critical = load_critical_contract(args.critical_contract)
    source_set = set(sources)
    preparation = read_json(args.preparation_metadata)
    require_preparation_binding(
        preparation,
        args=args,
        inventory_raw=inventory_raw,
        messages=messages,
        sources=sources,
        base_raw=base_raw,
        base=base,
        critical_raw=critical_raw,
        critical=critical,
    )

    active_base = {locale: {s: t for s, t in base[locale].items() if s in source_set} for locale in LOCALES}
    active_critical = {locale: {s: t for s, t in critical[locale].items() if s in source_set} for locale in LOCALES}
    expected_generation_sources = {
        source for source in sources
        if source not in active_base["en"] and source not in active_critical["en"]
    }
    expected_generated_counts = {locale: len(expected_generation_sources) for locale in LOCALES}
    if preparation.get("generated_source_counts") != expected_generated_counts:
        raise RuntimeError(
            f"preparation_generated_counts_invalid:expected={expected_generated_counts}:"
            f"actual={preparation.get('generated_source_counts')}"
        )

    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    model_metadata: dict[str, dict] = {}
    source_catalogs: dict[str, dict[str, str]] = {}
    all_failures: list[dict] = []
    collision_report: dict[str, list[dict]] = {}
    model_signatures = set()

    for locale in LOCALES:
        generated_path = args.generated_dir / f"source-{locale}.generated.json"
        metadata_path = args.generated_dir / f"generation-metadata-{locale}.json"
        generated = read_json(generated_path)
        model_meta = read_json(metadata_path)
        if model_meta.get("schema") != "nexus.i18n-contextual-locale-generation.v2":
            raise RuntimeError(f"locale_generation_schema_invalid:{locale}")
        if model_meta.get("locale") != locale or model_meta.get("failure_count") != 0:
            raise RuntimeError(f"locale_generation_not_clean:{locale}:{model_meta}")
        if model_meta.get("model_id") != MODEL_ID:
            raise RuntimeError(f"locale_generation_model_invalid:{locale}:{model_meta.get('model_id')}")
        if model_meta.get("requested_revision") != REQUESTED_REVISION:
            raise RuntimeError(f"locale_generation_requested_revision_invalid:{locale}")
        if REQUESTED_REVISION is not None and model_meta.get("resolved_revision") != REQUESTED_REVISION:
            raise RuntimeError(f"locale_generation_resolved_revision_invalid:{locale}")
        if model_meta.get("license") != APPROVED_LICENSE:
            raise RuntimeError(f"locale_generation_license_invalid:{locale}")
        if model_meta.get("input_sources") != len(expected_generation_sources):
            raise RuntimeError(f"locale_generation_input_count_invalid:{locale}")
        if model_meta.get("input_sha256") != preparation.get("generation_input_sha256", {}).get(locale):
            raise RuntimeError(f"locale_generation_input_digest_invalid:{locale}")
        if model_meta.get("output_sha256") != sha256_bytes(generated_path.read_bytes()):
            raise RuntimeError(f"locale_generation_output_digest_invalid:{locale}")
        generated_keys = set(generated)
        if generated_keys != expected_generation_sources:
            missing = sorted(expected_generation_sources - generated_keys)
            extras = sorted(generated_keys - expected_generation_sources)
            raise RuntimeError(
                f"locale_generation_source_set_invalid:{locale}:missing={missing[:20]}:extras={extras[:20]}"
            )

        signature = tuple(
            model_meta.get(key)
            for key in (
                "model_id", "requested_revision", "resolved_revision", "license",
                "license_evidence", "license_evidence_sha256",
            )
        )
        model_signatures.add(signature)
        model_metadata[locale] = {
            key: model_meta[key]
            for key in (
                "model_id", "requested_revision", "resolved_revision", "license",
                "license_evidence", "license_evidence_sha256",
            )
        }

        catalog = dict(generated)
        catalog.update(active_base[locale])
        catalog.update(active_critical[locale])
        missing = sorted(source_set - set(catalog))
        extras = sorted(set(catalog) - source_set)
        if missing or extras:
            raise RuntimeError(f"source_catalog_coverage_invalid:{locale}:missing={missing[:20]}:extras={extras[:20]}")
        for source in sources:
            reasons = validation_reasons(locale, source, catalog[source])
            if reasons:
                all_failures.append({
                    "locale": locale,
                    "source": source,
                    "translation": catalog[source],
                    "reasons": reasons,
                })
        for source, expected in active_critical[locale].items():
            if catalog[source] != expected:
                all_failures.append({
                    "locale": locale,
                    "source": source,
                    "translation": catalog[source],
                    "reasons": ["critical_contract_mismatch"],
                })
        collisions = collision_failures(catalog)
        collision_report[locale] = collisions
        source_catalogs[locale] = catalog

    if len(model_signatures) != 1:
        raise RuntimeError(f"locale_model_provenance_diverged:{model_metadata}")

    collision_failures_all = [
        {"locale": locale, **collision}
        for locale, collisions in collision_report.items()
        for collision in collisions
    ]
    report = {
        "schema": "nexus.i18n-contextual-catalog-validation.v2",
        "inventory_messages": len(messages),
        "unique_sources": len(sources),
        "validation_failure_count": len(all_failures),
        "validation_failures": all_failures,
        "collision_failure_count": len(collision_failures_all),
        "collision_failures": collision_failures_all,
        "locales": {
            locale: {
                "source_messages": len(source_catalogs[locale]),
                "validation_failures": sum(1 for item in all_failures if item["locale"] == locale),
                "collision_failures": len(collision_report[locale]),
            }
            for locale in LOCALES
        },
    }
    write_json(output / "validation-report.json", report)
    if all_failures:
        raise RuntimeError(f"final_catalog_validation_failed:{all_failures[:50]}")
    if collision_failures_all:
        raise RuntimeError(f"final_catalog_collision_failed:{collision_failures_all[:30]}")

    catalogs = {
        locale: {str(message["key"]): source_catalogs[locale][str(message["source"])] for message in messages}
        for locale in LOCALES
    }
    source_sha: dict[str, str] = {}
    catalog_sha: dict[str, str] = {}
    for locale in LOCALES:
        source_path = output / f"source-{locale}.review.json"
        catalog_path = output / f"catalog-{locale}.review.json"
        write_json(source_path, source_catalogs[locale])
        write_json(catalog_path, catalogs[locale])
        source_sha[locale] = sha256_bytes(source_path.read_bytes())
        catalog_sha[locale] = sha256_bytes(catalog_path.read_bytes())
        report["locales"][locale]["catalog_messages"] = len(catalogs[locale])
    write_json(output / "validation-report.json", report)

    metadata = {
        "schema_version": 2,
        "authority": "qwen3_contextual_static_catalog_v2",
        "policy": "production_candidate_requires_exact_inventory_semantic_contract_and_browser_acceptance",
        "product_inventory_head_sha": args.product_head,
        "inventory_sha256": sha256_bytes(inventory_raw),
        "inventory_messages": len(messages),
        "unique_sources": len(sources),
        "base_override_sha256": sha256_bytes(base_raw),
        "base_override_counts": {locale: len(base[locale]) for locale in LOCALES},
        "active_base_override_counts": preparation["active_base_override_counts"],
        "orphaned_base_sources": preparation["orphaned_base_sources"],
        "critical_contract_sha256": sha256_bytes(critical_raw),
        "critical_contract_counts": {locale: len(critical[locale]) for locale in LOCALES},
        "active_critical_counts": preparation["active_critical_counts"],
        "generated_source_counts": preparation["generated_source_counts"],
        "generation_input_sha256": preparation["generation_input_sha256"],
        "models": model_metadata,
        "source_catalog_sha256": source_sha,
        "catalog_sha256": catalog_sha,
        "validation_report_sha256": sha256_bytes((output / "validation-report.json").read_bytes()),
    }
    write_json(output / "production-catalog-metadata.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


