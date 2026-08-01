from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from contextual_catalog_contract_v2 import (
    APPROVED_LICENSE,
    LOCALE_SPECS,
    MODEL_ID,
    REQUESTED_REVISION,
    normalize,
    read_json,
    sha256_bytes,
    validation_reasons,
    write_json,
)


def translation_prompt(locale: str, source: str) -> str:
    spec = LOCALE_SPECS[locale]
    return (
        "/no_think\n"
        "You are the senior localization reviewer for Nexus OSR, an enterprise logistics customer-operations "
        "platform used by support agents, administrators and operations teams. "
        f"Translate the following Chinese repository-owned static UI string into {spec['name']}. {spec['requirements']} "
        "Translate by logistics/customer-support product meaning, not unrelated literal senses or legacy desktop-software corpora. "
        "Preserve product names, technical identifiers and every placeholder exactly, including {{0}}, %s and {name}. "
        "Return only the translated UI string. Do not add quotes, labels, explanations, Markdown, source text or comments.\n"
        f"SOURCE={source}"
    )


def repair_prompt(locale: str, source: str, candidate: str, reasons: list[str]) -> str:
    spec = LOCALE_SPECS[locale]
    return (
        "/no_think\n"
        f"You are repairing a {spec['name']} UI translation for Nexus OSR. {spec['requirements']} "
        "The candidate violated explicit product contracts. Rewrite it into one concise, semantically correct UI string. "
        "Preserve product names, technical identifiers and every placeholder exactly. For Montenegrin use Latin script only. "
        "Return only the repaired translation, with no quotes, labels, explanations, Markdown or comments.\n"
        f"SOURCE={source}\nCANDIDATE={candidate}\nVIOLATIONS={','.join(reasons)}"
    )


def final_review_prompt(locale: str, source: str, candidate: str) -> str:
    spec = LOCALE_SPECS[locale]
    return (
        "/no_think\n"
        f"You are the final native-language localization QA reviewer for Nexus OSR. {spec['requirements']} "
        "Check the candidate against the exact Chinese source. Correct terminology, grammar, case inflection, "
        "voice, ambiguity, missing meaning, added meaning and unnatural enterprise-software wording. "
        "Do not preserve a candidate term that conflicts with the product glossary. Preserve product names, "
        "technical identifiers and every placeholder exactly. Return only the final concise UI translation, "
        "with no quotes, labels, explanations, Markdown or comments.\n"
        f"SOURCE={source}\nCANDIDATE={candidate}"
    )


def verify_model_license(resolved_revision: str) -> tuple[str, str, str]:
    from huggingface_hub import hf_hub_download

    license_path = Path(hf_hub_download(repo_id=MODEL_ID, filename="LICENSE", revision=resolved_revision))
    license_bytes = license_path.read_bytes()
    license_text = license_bytes.decode("utf-8", errors="strict").lower()
    if (
        "apache license" not in license_text
        or "version 2.0, january 2004" not in license_text
        or "http://www.apache.org/licenses/" not in license_text
    ):
        raise RuntimeError("model_license_text_invalid")
    return APPROVED_LICENSE, "LICENSE", sha256_bytes(license_bytes)


def clean_model_output(value: str) -> str:
    output = value.strip()
    output = re.sub(r"^```(?:text)?\s*", "", output, flags=re.IGNORECASE)
    output = re.sub(r"\s*```$", "", output)
    output = re.sub(r"^(?:translation|translated text|output)\s*:\s*", "", output, flags=re.IGNORECASE)
    if len(output) >= 2 and output[0] == output[-1] and output[0] in {'"', "'"}:
        output = output[1:-1]
    return normalize(output)


def generate(args: argparse.Namespace) -> int:
    import torch
    from huggingface_hub import model_info
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if args.batch_items < 1 or args.max_new_tokens < 1 or args.review_passes < 0:
        raise RuntimeError("generation_limits_invalid")
    locale = args.locale
    input_raw = args.input.read_bytes()
    rows = json.loads(input_raw)
    if not isinstance(rows, list):
        raise RuntimeError("generation_input_invalid")
    items = [(str(row["id"]), str(row["source"])) for row in rows]
    if len({item_id for item_id, _ in items}) != len(items):
        raise RuntimeError("generation_input_duplicate_ids")
    if len({source for _, source in items}) != len(items):
        raise RuntimeError("generation_input_duplicate_sources")

    info = model_info(MODEL_ID, revision=REQUESTED_REVISION)
    resolved_revision = str(info.sha)
    if not re.fullmatch(r"[0-9a-f]{40}", resolved_revision):
        raise RuntimeError(f"model_revision_invalid:{resolved_revision}")
    if REQUESTED_REVISION is not None and resolved_revision != REQUESTED_REVISION:
        raise RuntimeError(f"model_revision_resolution_mismatch:{resolved_revision}")
    license_value, license_evidence, license_evidence_sha256 = verify_model_license(resolved_revision)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=resolved_revision)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        revision=resolved_revision,
        dtype="auto",
        low_cpu_mem_usage=True,
    )
    model.eval()
    model.to("cpu")
    torch.set_num_threads(max(1, os.cpu_count() or 1))
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    def run_prompts(prompt_rows: list[tuple[str, str]]) -> dict[str, str]:
        if not prompt_rows:
            return {}
        prompts = []
        for _item_id, prompt in prompt_rows:
            messages = [
                {"role": "system", "content": "Follow the localization contract exactly."},
                {"role": "user", "content": prompt},
            ]
            prompts.append(
                tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            )
        encoded = tokenizer(
            prompts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=3072,
        )
        try:
            generated_tokens = model.generate(
                **encoded,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                repetition_penalty=1.05,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
            )
        except RuntimeError as exc:
            message = str(exc).casefold()
            if len(prompt_rows) > 1 and ("out of memory" in message or "defaultcpuallocator" in message):
                midpoint = len(prompt_rows) // 2
                output = run_prompts(prompt_rows[:midpoint])
                output.update(run_prompts(prompt_rows[midpoint:]))
                return output
            raise
        prompt_width = encoded.input_ids.shape[1]
        decoded = tokenizer.batch_decode(generated_tokens[:, prompt_width:], skip_special_tokens=True)
        if len(decoded) != len(prompt_rows):
            raise RuntimeError("generation_batch_cardinality_mismatch")
        return {
            item_id: clean_model_output(value)
            for (item_id, _prompt), value in zip(prompt_rows, decoded, strict=True)
        }

    translations: dict[str, str] = {}
    with torch.inference_mode():
        total_batches = (len(items) + args.batch_items - 1) // args.batch_items
        for batch_index, offset in enumerate(range(0, len(items), args.batch_items), start=1):
            group = items[offset : offset + args.batch_items]
            translations.update(
                run_prompts([(item_id, translation_prompt(locale, source)) for item_id, source in group])
            )
            print(json.dumps({
                "event": "generation_progress",
                "locale": locale,
                "batch": batch_index,
                "batches": total_batches,
                "translated": len(translations),
                "total": len(items),
            }), flush=True)

        for review_pass in range(1, args.review_passes + 1):
            print(json.dumps({
                "event": "full_review_pass",
                "locale": locale,
                "attempt": review_pass,
                "sources": len(items),
            }), flush=True)
            reviewed: dict[str, str] = {}
            for offset in range(0, len(items), args.batch_items):
                group = items[offset : offset + args.batch_items]
                reviewed.update(
                    run_prompts([
                        (item_id, final_review_prompt(locale, source, translations[item_id]))
                        for item_id, source in group
                    ])
                )
            translations.update(reviewed)

        for attempt in range(1, 3):
            invalid = [
                (item_id, source, translations[item_id], validation_reasons(locale, source, translations[item_id]))
                for item_id, source in items
                if validation_reasons(locale, source, translations[item_id])
            ]
            if not invalid:
                break
            print(json.dumps({
                "event": "repair_pass",
                "locale": locale,
                "attempt": attempt,
                "invalid": len(invalid),
            }), flush=True)
            repaired: dict[str, str] = {}
            repair_batch_size = max(1, args.batch_items // 2)
            for offset in range(0, len(invalid), repair_batch_size):
                group = invalid[offset : offset + repair_batch_size]
                repaired.update(
                    run_prompts([
                        (item_id, repair_prompt(locale, source, candidate, reasons))
                        for item_id, source, candidate, reasons in group
                    ])
                )
            translations.update(repaired)

    failures = []
    source_output = {}
    for item_id, source in items:
        translated = translations[item_id]
        reasons = validation_reasons(locale, source, translated)
        if reasons:
            failures.append({"id": item_id, "source": source, "translation": translated, "reasons": reasons})
        source_output[source] = translated

    args.output.mkdir(parents=True, exist_ok=True)
    output_path = args.output / f"source-{locale}.generated.json"
    write_json(output_path, source_output)
    write_json(args.output / f"generation-failures-{locale}.json", failures)
    metadata = {
        "schema": "nexus.i18n-contextual-locale-generation.v2",
        "prompt_contract": "nexus.i18n-contextual-single-string.v2",
        "locale": locale,
        "model_id": MODEL_ID,
        "requested_revision": REQUESTED_REVISION,
        "resolved_revision": resolved_revision,
        "license": license_value,
        "license_evidence": license_evidence,
        "license_evidence_sha256": license_evidence_sha256,
        "input_sources": len(items),
        "input_sha256": sha256_bytes(input_raw),
        "batch_items": args.batch_items,
        "max_new_tokens": args.max_new_tokens,
        "review_passes": args.review_passes,
        "output_sha256": sha256_bytes(output_path.read_bytes()),
        "failure_count": len(failures),
    }
    write_json(args.output / f"generation-metadata-{locale}.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        raise RuntimeError(f"contextual_generation_validation_failed:{locale}:{failures[:20]}")
    return 0


