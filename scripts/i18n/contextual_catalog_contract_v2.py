from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
REQUESTED_REVISION: str | None = "cdbee75f17c01a7cc42f958dc650907174af0554"
APPROVED_LICENSE = "apache-2.0"
BASE_OVERRIDES_SHA256 = "ed6d9db7e85aeac51fda2c0babbbfa132d6fca430959b423d7e6a48fd3a42d9c"
EXPECTED_BASE_COUNTS = {"en": 655, "de": 655, "cnr": 655}
LOCALES = ("en", "de", "cnr")

CJK_RE = re.compile(r"[\u3400-\u9fff]")
CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")
MOJIBAKE_RE = re.compile(r"[\uFFFD\u00e8\u00c8\u00e6\u00c6\u0153\u0152]|b\u203a|\u041f\u0440\u043e\u0432\u203a")
CNR_EKAVIAN_RE = re.compile(
    r"(?i)\b(?:slede|posled|vreme|zahtev|uspe|neuspe|re\u0161en|re\u0161enj|ovde|gde|"
    r"obave\u0161ten|bezbed|promen|re\u010di?|ve\u0161ta\u010d|sedi\u0161t)"
)
PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}|%(?:\d+\$)?[sdif]|\{[A-Za-z_][A-Za-z0-9_]*\}")
REPEATED_GARBAGE_RE = re.compile(r"([A-Za-z])\1{12,}")
MARKER_RE = re.compile(r"(?:ZXPH\d+ZX|NXS\d+|[\u27e6\u27e7\uff3b\uff3d])", re.IGNORECASE)
MODEL_WRAPPER_RE = re.compile(
    r"(?i)^(?:here(?:'s| is)\b|the translation\b|english\s+translation\b|"
    r"german\s+translation\b|montenegrin\s+translation\b|crnogorski\s+translation\b)"
)

FORBIDDEN_PATTERNS = {
    "en": re.compile(
        r"(?ix)\bbonobo\b|\bevolution\b|temporary\s+folder|synchroni[sz]ing\s+folder|"
        r"other\s+organis(?:er|or)|copy\s+contacts|mail\s+component|cms\s+message|"
        r"\bchile\b|cannot\s+initialise|could\s+not\s+initialise|"
        r"can\s+not\s+(?:open|get|delete)\s+(?:message|folder)|no\s+time\s+at\s+all|"
        r"^organisation$|^_"
    ),
    "de": re.compile(
        r"(?ix)\bbonobo\b|\bevolution\b|tempor\u00e4rer\s+ordner|ordner\s+synchronisieren|"
        r"sonstiger\s+veranstalter|mail-komponente|\bchile\b|"
        r"nachricht\s+kann\s+nicht\s+ge\u00f6ffnet|keine\s+zeit\.?$|^organisation$|^_"
    ),
    "cnr": re.compile(
        r"(?ix)\bbonobo\b|prenesem\s+kalendar|kopiraj\s+kontakte|cms\s+poruku|"
        r"snimi\s+kao|^_postavke|^_snimi|broj\s+ra[\u00e8\u010d]una|tehni[\u00e8\u010d]ka|\bklijent|^_"
    ),
}

LOCALE_SPECS = {
    "en": {
        "name": "English",
        "requirements": (
            "Use concise, natural enterprise-software English. Use Account for \u8d26\u53f7, never account number; "
            "Ticket for \u5de5\u5355, Waybill for \u8fd0\u5355, Customer for \u5ba2\u6237, User for \u7528\u6237, Email for \u90ae\u4ef6, "
            "Team for \u56e2\u961f, Queue for \u961f\u5217, Permission for \u6743\u9650 and Sign in for \u767b\u5f55."
        ),
    },
    "de": {
        "name": "German",
        "requirements": (
            "Use concise, professional German for enterprise software. Use Konto for \u8d26\u53f7, never Benutzer; "
            "Ticket for \u5de5\u5355, Frachtbrief for \u8fd0\u5355, Kunde/Kunden- for \u5ba2\u6237, Benutzer for \u7528\u6237, "
            "E-Mail for \u90ae\u4ef6, Team for \u56e2\u961f, Warteschlange for \u961f\u5217, Berechtigung for \u6743\u9650 and anmelden for \u767b\u5f55."
        ),
    },
    "cnr": {
        "name": "Montenegrin (Crnogorski)",
        "requirements": (
            "Use standard contemporary Montenegrin in Latin script only, with Ijekavian forms. Never use Cyrillic, "
            "mojibake, klijent, or Serbian Ekavian forms such as zahtev, vreme, slede\u0107i, poslednji, uspe\u0161an, "
            "neuspe\u0161an, re\u0161enje, bezbedan or obave\u0161ten. Use nalog for \u8d26\u53f7, tiket for \u5de5\u5355, "
            "tovarni list with natural Montenegrin case inflection for \u8fd0\u5355, korisnik for both \u5ba2\u6237 and \u7528\u6237 "
            "according to the product authority, e-po\u0161ta for \u90ae\u4ef6, tim for \u56e2\u961f, red for \u961f\u5217, "
            "dozvola/dozvole for \u6743\u9650, revizor for \u5ba1\u8ba1\u5458 and prijava/prijaviti se for \u767b\u5f55. "
            "Prefer zahtjev, sljede\u0107i, posljednji, vrijeme, uspjelo, neuspjelo, rje\u0161enje, bezbjedan and obavije\u0161ten."
        ),
    },
}

TERM_RULES = {
    "\u8d26\u53f7": {
        "en": (re.compile(r"(?i)\baccount\b"), re.compile(r"(?i)\baccount\s+number\b")),
        "de": (re.compile(r"(?i)konto"), re.compile(r"(?i)kontonummer")),
        "cnr": (re.compile(r"(?i)\bnalog"), re.compile(r"(?i)\bbroj\s+ra\u010duna\b")),
    },
    "\u5de5\u5355": {
        "en": (re.compile(r"(?i)\bticket\b"), None),
        "de": (re.compile(r"(?i)\bticket"), None),
        "cnr": (re.compile(r"(?i)\btiket"), None),
    },
    "\u8fd0\u5355": {
        "en": (re.compile(r"(?i)\bwaybill\b"), None),
        "de": (re.compile(r"(?i)frachtbrief"), None),
        "cnr": (re.compile(
            r"(?i)(?:\btovarni\s+list\b|\btovarnog\s+lista\b|\btovarnom\s+listu\b|"
            r"\btovarni\s+listovi\b|\btovarnih\s+listova\b|\btovarnim\s+listovima\b)"
        ), None),
    },
    "\u5ba2\u6237": {
        "en": (re.compile(r"(?i)\bcustomer\b"), None),
        "de": (re.compile(r"(?i)\bkund"), None),
        "cnr": (re.compile(r"(?i)\bkorisnik"), None),
    },
    "\u7528\u6237": {
        "en": (re.compile(r"(?i)\buser\b"), None),
        "de": (re.compile(r"(?i)benutzer"), None),
        "cnr": (re.compile(r"(?i)\bkorisnik"), None),
    },
    "\u90ae\u4ef6": {
        "en": (re.compile(r"(?i)\bemail\b"), None),
        "de": (re.compile(r"(?i)e-?mail"), None),
        "cnr": (re.compile(r"(?i)\be-po\u0161t"), None),
    },
    "\u6743\u9650": {
        "en": (re.compile(r"(?i)\bpermission"), None),
        "de": (re.compile(r"(?i)berechtigung"), None),
        "cnr": (re.compile(r"(?i)\bdozvol"), None),
    },
    "\u56e2\u961f": {
        "en": (re.compile(r"(?i)\bteam\b"), None),
        "de": (re.compile(r"(?i)team"), None),
        "cnr": (re.compile(r"(?i)\btim"), None),
    },
    "\u961f\u5217": {
        "en": (re.compile(r"(?i)\bqueue\b"), None),
        "de": (re.compile(r"(?i)warteschlange"), None),
        "cnr": (re.compile(r"(?i)\bred"), None),
    },
    "\u767b\u5f55": {
        "en": (re.compile(r"(?i)\bsign(?:ed|ing)?(?:-|\s+)in\b"), None),
        "de": (re.compile(r"(?i)(?:\b(?:anmeld|angemeld)|\bmeld(?:en|e|et|est)?\b.{0,80}\ban\b)"), None),
        "cnr": (re.compile(r"(?i)\bprijav"), None),
    },
    "\u9000\u51fa\u767b\u5f55": {
        "en": (re.compile(r"(?i)\bsign\s+out\b"), None),
        "de": (re.compile(r"(?i)\babmeld"), None),
        "cnr": (re.compile(r"(?i)\bodjav"), None),
    },
    "\u5ba1\u8ba1\u5458": {
        "en": (re.compile(r"(?i)\bauditor\b"), None),
        "de": (re.compile(r"(?i)\bauditor\b"), None),
        "cnr": (re.compile(r"(?i)\brevizor"), None),
    },
}

PROTECTED_TERMS = (
    "Nexus OSR", "Nexus", "LiveKit", "WhatsApp", "Baileys", "Meta Cloud API", "Meta App", "WABA",
    "Sidecar", "Speedaf", "Provider", "PostgreSQL", "MFA", "TOTP", "DTMF", "Graph API", "API", "URL",
    "UTC", "JSON", "Inbox", "Outbox", "Conversation", "ChannelAccount", "Access Token", "App Review",
    "Advanced Access",
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def placeholders(value: str) -> list[str]:
    return sorted(PLACEHOLDER_RE.findall(value))


def normalize(value: str) -> str:
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def source_core(value: str) -> str:
    value = re.sub(r"\u6b63\u5728|\u5df2|\u4e2d|\u5f53\u524d|\u6682\u65e0|\u5c1a\u65e0|\u8bf7|\u3002|\uff0c|\uff1a|\u2026|\s+", "", value)
    return value


def semantic_reasons(locale: str, source: str, translated: str) -> list[str]:
    reasons: list[str] = []
    for marker, rules in TERM_RULES.items():
        if marker not in source:
            continue
        if marker == "\u767b\u5f55" and "\u9000\u51fa\u767b\u5f55" in source:
            continue
        required, forbidden = rules[locale]
        if not required.search(translated):
            reasons.append(f"term_missing:{marker}")
        if forbidden is not None and forbidden.search(translated):
            reasons.append(f"term_forbidden:{marker}")
    for term in PROTECTED_TERMS:
        if term in source and term not in translated:
            reasons.append(f"protected_term_missing:{term}")
    return reasons


def validation_reasons(locale: str, source: str, translated: str) -> list[str]:
    reasons: list[str] = []
    if not isinstance(translated, str) or not translated.strip():
        return ["empty"]
    translated = normalize(translated)
    if CJK_RE.search(translated):
        reasons.append("cjk_residue")
    if placeholders(source) != placeholders(translated):
        reasons.append("placeholder_mismatch")
    if REPEATED_GARBAGE_RE.search(translated):
        reasons.append("repeated_garbage")
    if MARKER_RE.search(translated):
        reasons.append("marker_residue")
    if "\n" in translated or "\r" in translated:
        reasons.append("multiline_output")
    if MODEL_WRAPPER_RE.search(translated):
        reasons.append("model_wrapper")
    if FORBIDDEN_PATTERNS[locale].search(translated):
        reasons.append("known_corpus_contamination")
    if locale == "cnr":
        if CYRILLIC_RE.search(translated):
            reasons.append("cnr_cyrillic")
        if MOJIBAKE_RE.search(translated):
            reasons.append("cnr_mojibake")
        if CNR_EKAVIAN_RE.search(translated):
            reasons.append("cnr_ekavian")
    if len(translated) > max(80, len(source) * 4 + 20):
        reasons.append("length_explosion")
    reasons.extend(semantic_reasons(locale, source, translated))
    return sorted(set(reasons))


def load_inventory(path: Path) -> tuple[bytes, dict, list[dict], list[str]]:
    raw = path.read_bytes()
    value = json.loads(raw)
    messages = value.get("messages")
    if value.get("schema_version") != 2 or not isinstance(messages, list) or not messages:
        raise RuntimeError("inventory_invalid")
    keys = [str(message["key"]) for message in messages]
    if len(keys) != len(set(keys)):
        raise RuntimeError("inventory_duplicate_keys")
    sources = sorted({str(message["source"]) for message in messages}, key=lambda item: (len(item), item))
    return raw, value, messages, sources


def load_base_overrides(path: Path) -> tuple[bytes, dict[str, dict[str, str]]]:
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    if digest != BASE_OVERRIDES_SHA256:
        raise RuntimeError(f"base_override_digest_mismatch:{digest}")
    value = json.loads(raw)
    if set(value) != set(LOCALES):
        raise RuntimeError("base_override_locale_set_invalid")
    counts = {locale: len(value[locale]) for locale in LOCALES}
    if counts != EXPECTED_BASE_COUNTS:
        raise RuntimeError(f"base_override_counts_invalid:{counts}")
    source_sets = {locale: set(value[locale]) for locale in LOCALES}
    if len({frozenset(items) for items in source_sets.values()}) != 1:
        raise RuntimeError("base_override_source_sets_diverged")
    return raw, value


def load_critical_contract(path: Path) -> tuple[bytes, dict, dict[str, dict[str, str]]]:
    raw = path.read_bytes()
    contract = json.loads(raw)
    if contract.get("schema") != "nexus.i18n-critical-catalog.v1" or not isinstance(contract.get("messages"), dict):
        raise RuntimeError("critical_contract_invalid")
    values: dict[str, dict[str, str]] = {locale: {} for locale in LOCALES}
    for source, per_locale in contract["messages"].items():
        if set(per_locale) != set(LOCALES):
            raise RuntimeError(f"critical_contract_locale_set_invalid:{source}")
        for locale in LOCALES:
            translated = per_locale[locale]
            if not isinstance(translated, str) or not translated.strip():
                raise RuntimeError(f"critical_contract_empty:{locale}:{source}")
            values[locale][source] = normalize(translated)
    return raw, contract, values


def authority_failures(
    label: str,
    values: dict[str, dict[str, str]],
    source_set: set[str],
) -> list[dict]:
    failures: list[dict] = []
    for locale in LOCALES:
        for source, translated in values[locale].items():
            if source not in source_set:
                continue
            reasons = validation_reasons(locale, source, translated)
            if reasons:
                failures.append({
                    "authority": label,
                    "locale": locale,
                    "source": source,
                    "translation": translated,
                    "reasons": reasons,
                })
    return failures


def collision_failures(source_catalog: dict[str, str]) -> list[dict]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for source, translated in source_catalog.items():
        grouped[normalize(translated).casefold()].append(source)
    failures = []
    for translated, sources in grouped.items():
        unique_sources = sorted(set(sources))
        if len(unique_sources) < 3:
            continue
        cores = {source_core(source) for source in unique_sources}
        cores.discard("")
        if len(cores) <= 1:
            continue
        if len(translated) <= 5:
            continue
        failures.append({"translation": translated, "sources": unique_sources})
    return sorted(failures, key=lambda item: (-len(item["sources"]), item["translation"]))


