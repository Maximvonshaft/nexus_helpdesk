from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import torch
from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

Locale = Literal["en", "de"]

CJK_RE = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}")
PROTECTED_TERM_RE = re.compile(
    r"Nexus OSR|Nexus|WebChat|WebCall|WhatsApp|LiveKit|Speedaf|PostgreSQL|"
    r"Provider|Runtime|MFA|SLA|API|URL|SMS|Email|PDF|POD|OTP|AI",
    re.IGNORECASE,
)
PROTECTED_TOKEN_RE = re.compile(
    rf"({PLACEHOLDER_RE.pattern}|{PROTECTED_TERM_RE.pattern})",
    re.IGNORECASE,
)
MARKER_RE = re.compile(r"[⟦［\[]\s*(\d+)\s*[⟧］\]]")
MARKER_RESIDUE_RE = re.compile(r"[⟦⟧］［]|NXS\d+", re.IGNORECASE)
REPEATED_GARBAGE_RE = re.compile(r"([A-Za-z])\1{12,}", re.IGNORECASE)
PUNCTUATION_TRANSLATION = str.maketrans(
    {"，": ",", "。": ".", "：": ":", "；": ";", "？": "?", "！": "!", "（": "(", "）": ")"}
)

MODEL_ID = "facebook/m2m100_418M"
SOURCE_LANGUAGE = "zh"
TARGET_LANGUAGES: dict[Locale, str] = {"en": "en", "de": "de"}

# Product terminology overrides are exact, reviewable decisions. The model handles
# full sentences; these entries remove ambiguity from high-frequency logistics and
# identity concepts whose Chinese labels have several unrelated dictionary senses.
SOURCE_OVERRIDES: dict[Locale, dict[str, str]] = {
    "en": {
        "6 位验证码": "6-digit verification code",
        "工单": "Ticket",
        "队列": "Queue",
        "案例": "Case",
        "坐席": "Agent",
        "坐席与范围": "Agent and work scope",
        "两步验证": "Two-factor authentication",
        "账户": "Account",
        "当前账号": "Current account",
        "账户设置": "Account settings",
        "退出": "Sign out",
        "退出登录": "Sign out",
        "退出所有设备": "Sign out on all devices",
        "退出所有设备？": "Sign out on all devices?",
        "确认退出所有设备": "Confirm sign-out on all devices",
        "工作范围": "Work scope",
        "主导航": "Main navigation",
        "打开主导航": "Open main navigation",
        "跳到主要内容": "Skip to main content",
        "管理员": "Administrator",
        "审计员": "Auditor",
        "客服专员": "Customer service agent",
        "客服": "Customer service",
        "客服状态": "Agent status",
        "运营经理": "Operations manager",
        "组长": "Team lead",
        "邮箱": "Email",
        "登录设备": "Signed-in devices",
        "正在登录…": "Signing in…",
        "登录": "Sign in",
        "账号或密码错误。": "The username or password is incorrect.",
        "验证码、恢复码或登录挑战无效。请重试或重新输入密码。": "The verification code, recovery code or sign-in challenge is invalid. Try again or re-enter your password.",
        "请稍后重试": "Try again later",
        "请重新登录": "Sign in again",
        "暂无": "Not available",
        "未分配": "Unassigned",
        "暂停接线": "Pause availability",
        "开启电话接线": "Enable voice calls",
        "关闭电话接线": "Disable voice calls",
    },
    "de": {
        "6 位验证码": "6-stelliger Bestätigungscode",
        "工单": "Ticket",
        "队列": "Warteschlange",
        "案例": "Fall",
        "坐席": "Agent",
        "坐席与范围": "Agent und Arbeitsbereich",
        "两步验证": "Zwei-Faktor-Authentifizierung",
        "账户": "Konto",
        "当前账号": "Aktuelles Konto",
        "账户设置": "Kontoeinstellungen",
        "退出": "Abmelden",
        "退出登录": "Abmelden",
        "退出所有设备": "Auf allen Geräten abmelden",
        "退出所有设备？": "Auf allen Geräten abmelden?",
        "确认退出所有设备": "Abmeldung auf allen Geräten bestätigen",
        "工作范围": "Arbeitsbereich",
        "主导航": "Hauptnavigation",
        "打开主导航": "Hauptnavigation öffnen",
        "跳到主要内容": "Zum Hauptinhalt springen",
        "管理员": "Administrator",
        "审计员": "Auditor",
        "客服专员": "Kundenservice-Agent",
        "客服": "Kundenservice",
        "客服状态": "Agentenstatus",
        "运营经理": "Betriebsleiter",
        "组长": "Teamleiter",
        "邮箱": "E-Mail",
        "登录设备": "Angemeldete Geräte",
        "正在登录…": "Anmeldung läuft…",
        "登录": "Anmelden",
        "账号或密码错误。": "Benutzername oder Passwort ist falsch.",
        "验证码、恢复码或登录挑战无效。请重试或重新输入密码。": "Der Bestätigungscode, Wiederherstellungscode oder die Anmeldeanforderung ist ungültig. Versuchen Sie es erneut oder geben Sie Ihr Passwort erneut ein.",
        "请稍后重试": "Versuchen Sie es später erneut",
        "请重新登录": "Erneut anmelden",
        "暂无": "Nicht verfügbar",
        "未分配": "Nicht zugewiesen",
        "暂停接线": "Verfügbarkeit pausieren",
        "开启电话接线": "Sprachanrufe aktivieren",
        "关闭电话接线": "Sprachanrufe deaktivieren",
    },
}


@dataclass(frozen=True)
class ProtectedText:
    value: str
    replacements: tuple[str, ...]


def protect_text(value: str) -> ProtectedText:
    replacements: list[str] = []

    def replace(match: re.Match[str]) -> str:
        index = len(replacements)
        replacements.append(match.group(0))
        return f"⟦{index}⟧"

    return ProtectedText(PROTECTED_TOKEN_RE.sub(replace, value), tuple(replacements))


def normalize_output(value: str) -> str:
    output = value.translate(PUNCTUATION_TRANSLATION)
    output = re.sub(r"\s+([,.;:!?])", r"\1", output)
    output = re.sub(r"([([])\s+", r"\1", output)
    output = re.sub(r"\s+([)\]])", r"\1", output)
    output = re.sub(r"[ \t]{2,}", " ", output)
    return output.strip()


def restore_text(value: str, replacements: tuple[str, ...]) -> tuple[str, bool]:
    seen: set[int] = set()

    def restore(match: re.Match[str]) -> str:
        index = int(match.group(1))
        if index >= len(replacements):
            return match.group(0)
        seen.add(index)
        return replacements[index]

    output = MARKER_RE.sub(restore, value)
    complete = seen == set(range(len(replacements))) and not MARKER_RESIDUE_RE.search(output)
    return normalize_output(output), complete


def batches(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def translate_sources(
    sources: list[str],
    *,
    target_locale: Locale,
    batch_size: int,
) -> tuple[dict[str, str], dict[str, object]]:
    tokenizer = M2M100Tokenizer.from_pretrained(MODEL_ID)
    model = M2M100ForConditionalGeneration.from_pretrained(MODEL_ID)
    model.eval()
    model.to("cpu")
    tokenizer.src_lang = SOURCE_LANGUAGE
    forced_bos_token_id = tokenizer.get_lang_id(TARGET_LANGUAGES[target_locale])

    protected = {source: protect_text(source) for source in sources}
    unique_inputs = sorted(
        {row.value for row in protected.values()},
        key=lambda value: (len(value), value),
    )
    raw_outputs: dict[str, str] = {}

    with torch.inference_mode():
        for source_batch in batches(unique_inputs, batch_size):
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
                raw_outputs[source] = normalize_output(translated) or source

    translations: dict[str, str] = {}
    marker_failures: list[str] = []
    for source in sources:
        row = protected[source]
        restored, complete = restore_text(raw_outputs[row.value], row.replacements)
        if not complete:
            marker_failures.append(source)
            # Fail safely: preserve protected tokens and translate the surrounding
            # sentence through a second pass without markers only when no token is
            # present. Sources with damaged protected tokens require an override.
            if row.replacements:
                translations[source] = source
                continue
        translations[source] = restored

    translations.update({
        source: translated
        for source, translated in SOURCE_OVERRIDES[target_locale].items()
        if source in translations
    })

    metadata = {
        "model_id": MODEL_ID,
        "resolved_revision": getattr(model.config, "_commit_hash", None),
        "tokenizer_revision": getattr(tokenizer, "_commit_hash", None),
        "source_language": SOURCE_LANGUAGE,
        "target_language": TARGET_LANGUAGES[target_locale],
        "sources": len(sources),
        "marker_failures": marker_failures,
    }
    return translations, metadata


def protected_terms(value: str) -> list[str]:
    return [match.group(0) for match in PROTECTED_TERM_RE.finditer(value)]


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
    if MARKER_RESIDUE_RE.search(translated):
        errors.append("marker_residue")
    if len(translated) > max(2400, len(source) * 12):
        errors.append("length_explosion")
    for term in protected_terms(source):
        if term.casefold() not in translated.casefold():
            errors.append(f"protected_term_missing:{term}")
    if translated == source and CJK_RE.search(source):
        errors.append("untranslated")
    return errors


def validate_catalog(sources: list[str], translations: dict[str, str], *, locale: Locale) -> None:
    failures = {
        source: errors
        for source in sources
        if (errors := validate_translation(source, translations.get(source, ""), locale=locale))
    }
    if failures:
        raise RuntimeError(f"catalog_validation_failed:{locale}:{list(failures.items())[:20]}")


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
    parser.add_argument("--batch-size", type=int, default=24)
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
    source_catalogs: dict[Locale, dict[str, str]] = {}
    metadata: dict[Locale, dict[str, object]] = {}
    for locale in ("en", "de"):
        translations, locale_metadata = translate_sources(
            sources,
            target_locale=locale,
            batch_size=args.batch_size,
        )
        validate_catalog(sources, translations, locale=locale)
        source_catalogs[locale] = translations
        metadata[locale] = locale_metadata

    keyed_catalogs = {
        locale: {
            str(message["key"]): translations[str(message["source"])]
            for message in messages
        }
        for locale, translations in source_catalogs.items()
    }

    output = args.output
    for locale in ("en", "de"):
        write_json(output / f"source-{locale}.review.json", source_catalogs[locale])
        write_json(output / f"catalog-{locale}.review.json", keyed_catalogs[locale])
    write_json(
        output / "bootstrap-metadata.json",
        {
            "schema_version": 4,
            "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest(),
            "inventory_messages": len(messages),
            "unique_sources": len(sources),
            "model": MODEL_ID,
            "locales": metadata,
            "overrides": {locale: len(values) for locale, values in SOURCE_OVERRIDES.items()},
            "policy": "machine_bootstrap_requires_human_occurrence_review",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
