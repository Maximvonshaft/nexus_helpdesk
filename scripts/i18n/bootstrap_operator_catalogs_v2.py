from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from huggingface_hub import model_info

import bootstrap_operator_catalogs as base

APPROVED_MODEL_LICENSES = {"mit"}

EXTRA_OVERRIDES: dict[base.Locale, dict[str, str]] = {
    "en": {
        "账号": "Account",
        "密码": "Password",
        "显示密码": "Show password",
        "隐藏密码": "Hide password",
        "验证码或恢复码": "Verification code or recovery code",
        "重新输入密码": "Re-enter password",
        "验证并登录": "Verify and sign in",
        "正在验证…": "Verifying…",
        "当前密码": "Current password",
        "新密码": "New password",
        "确认新密码": "Confirm new password",
        "修改密码": "Change password",
        "更新密码并重新登录": "Update password and sign in again",
        "正在更新…": "Updating…",
        "取消": "Cancel",
        "确认": "Confirm",
        "返回登录": "Return to sign in",
        "无法读取账户": "Unable to load account",
        "正在加载账户…": "Loading account…",
        "密码修改失败": "Password change failed",
        "退出失败": "Sign-out failed",
        "已启用": "Enabled",
        "未启用": "Not enabled",
        "姓名": "Name",
        "角色": "Role",
        "团队": "Team",
        "上次登录": "Last sign-in",
        "密码更新": "Password updated",
    },
    "de": {
        "账号": "Konto",
        "密码": "Passwort",
        "显示密码": "Passwort anzeigen",
        "隐藏密码": "Passwort ausblenden",
        "验证码或恢复码": "Bestätigungs- oder Wiederherstellungscode",
        "重新输入密码": "Passwort erneut eingeben",
        "验证并登录": "Bestätigen und anmelden",
        "正在验证…": "Überprüfung läuft…",
        "当前密码": "Aktuelles Passwort",
        "新密码": "Neues Passwort",
        "确认新密码": "Neues Passwort bestätigen",
        "修改密码": "Passwort ändern",
        "更新密码并重新登录": "Passwort aktualisieren und erneut anmelden",
        "正在更新…": "Aktualisierung läuft…",
        "取消": "Abbrechen",
        "确认": "Bestätigen",
        "返回登录": "Zur Anmeldung zurückkehren",
        "无法读取账户": "Konto konnte nicht geladen werden",
        "正在加载账户…": "Konto wird geladen…",
        "密码修改失败": "Passwortänderung fehlgeschlagen",
        "退出失败": "Abmeldung fehlgeschlagen",
        "已启用": "Aktiviert",
        "未启用": "Nicht aktiviert",
        "姓名": "Name",
        "角色": "Rolle",
        "团队": "Team",
        "上次登录": "Letzte Anmeldung",
        "密码更新": "Passwort aktualisiert",
    },
}


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def protected_segments(value: str):
    cursor = 0
    for match in base.PROTECTED_TOKEN_RE.finditer(value):
        if match.start() > cursor:
            yield value[cursor : match.start()], False
        yield match.group(0), True
        cursor = match.end()
    if cursor < len(value):
        yield value[cursor:], False


def boundary_whitespace(value: str) -> tuple[str, str, str]:
    prefix = re.match(r"^\s*", value).group(0)
    suffix = re.search(r"\s*$", value).group(0)
    end = len(value) - len(suffix) if suffix else len(value)
    return prefix, value[len(prefix) : end], suffix


def model_translate(values: list[str], *, locale: base.Locale, batch_size: int) -> dict[str, str]:
    tokenizer = base.M2M100Tokenizer.from_pretrained(base.MODEL_ID)
    model = base.M2M100ForConditionalGeneration.from_pretrained(base.MODEL_ID)
    tokenizer.src_lang = base.SOURCE_LANGUAGE
    model.eval()
    model.to("cpu")
    forced_bos_token_id = tokenizer.get_lang_id(base.TARGET_LANGUAGES[locale])
    unique = sorted(set(values), key=lambda item: (len(item), item))
    output: dict[str, str] = {}
    with base.torch.inference_mode():
        for source_batch in batches(unique, batch_size):
            encoded = tokenizer(
                source_batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=256,
            )
            generated = model.generate(
                **encoded,
                forced_bos_token_id=forced_bos_token_id,
                max_new_tokens=192,
                num_beams=4,
                early_stopping=True,
            )
            decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
            if len(decoded) != len(source_batch):
                raise RuntimeError("translation_batch_cardinality_mismatch")
            for source, translated in zip(source_batch, decoded, strict=True):
                output[source] = base.normalize_output(translated) or source
    return output


def segmented_fallback(
    sources: list[str],
    *,
    locale: base.Locale,
    batch_size: int,
) -> dict[str, str]:
    translatable_cores = [
        core
        for source in sources
        for segment, protected in protected_segments(source)
        if not protected
        for _prefix, core, _suffix in [boundary_whitespace(segment)]
        if core and base.CJK_RE.search(core)
    ]
    translated_cores = model_translate(
        translatable_cores,
        locale=locale,
        batch_size=batch_size,
    )
    output: dict[str, str] = {}
    for source in sources:
        parts: list[str] = []
        for segment, protected in protected_segments(source):
            if protected:
                parts.append(segment)
                continue
            prefix, core, suffix = boundary_whitespace(segment)
            parts.append(f"{prefix}{translated_cores.get(core, core)}{suffix}")
        output[source] = base.normalize_output("".join(parts))
    return output


def model_license_evidence() -> dict[str, str]:
    info = model_info(base.MODEL_ID)
    license_value = str(getattr(info.card_data, "license", "") or "").strip().lower()
    if license_value not in APPROVED_MODEL_LICENSES:
        raise RuntimeError(f"model_license_not_approved:{license_value or 'missing'}")
    return {
        "model_id": base.MODEL_ID,
        "license": license_value,
        "resolved_revision": str(info.sha),
    }


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

    license_evidence = model_license_evidence()
    sources = sorted(
        {str(message["source"]) for message in messages},
        key=lambda value: (len(value), value),
    )
    source_catalogs: dict[base.Locale, dict[str, str]] = {}
    locale_metadata: dict[base.Locale, dict[str, object]] = {}

    for locale in ("en", "de"):
        translations, metadata = base.translate_sources(
            sources,
            target_locale=locale,
            batch_size=args.batch_size,
        )
        marker_failures = [str(value) for value in metadata.get("marker_failures", [])]
        if marker_failures:
            translations.update(
                segmented_fallback(
                    marker_failures,
                    locale=locale,
                    batch_size=args.batch_size,
                )
            )
        translations.update(
            {
                source: translated
                for source, translated in EXTRA_OVERRIDES[locale].items()
                if source in translations
            }
        )
        base.validate_catalog(sources, translations, locale=locale)
        source_catalogs[locale] = translations
        locale_metadata[locale] = {
            **metadata,
            "segmented_fallbacks": len(marker_failures),
        }

    keyed_catalogs = {
        locale: {
            str(message["key"]): translations[str(message["source"])]
            for message in messages
        }
        for locale, translations in source_catalogs.items()
    }

    for locale in ("en", "de"):
        write_json(args.output / f"source-{locale}.review.json", source_catalogs[locale])
        write_json(args.output / f"catalog-{locale}.review.json", keyed_catalogs[locale])
    write_json(
        args.output / "bootstrap-metadata.json",
        {
            "schema_version": 5,
            "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest(),
            "inventory_messages": len(messages),
            "unique_sources": len(sources),
            "model": license_evidence,
            "locales": locale_metadata,
            "base_overrides": {
                locale: len(values) for locale, values in base.SOURCE_OVERRIDES.items()
            },
            "extra_overrides": {
                locale: len(values) for locale, values in EXTRA_OVERRIDES.items()
            },
            "policy": "machine_bootstrap_requires_human_occurrence_review",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
