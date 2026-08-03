from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import torch
from huggingface_hub import hf_hub_download, model_info
from transformers import AutoModelForCausalLM, AutoTokenizer

MODEL_ID = "Qwen/Qwen3-1.7B"
REQUESTED_REVISION = None
APPROVED_LICENSE = "apache-2.0"

SOURCES = [
    "发布",
    "已发布",
    "待发布",
    "预发布",
    "保存中…",
    "保存问题",
    "运单",
    "运单号",
    "候选运单",
    "向客户收集运单号",
    "客服工单",
    "关联工单",
    "工单 #",
    "当前任务没有工单",
    "无权创建催派工单",
    "客服状态",
    "公告管理",
    "发布知识",
    "发布版本",
    "发布状态",
    "发布范围",
    "团队负载",
    "回复客户",
    "客户已读",
    "客户消息",
    "客户资料",
    "客户通知",
    "客户确认",
    "客户沟通",
    "客户问题",
    "客户电话",
    "当前账号",
    "该账号",
    "个账号",
    "邮件",
    "审计员",
    "有效权限",
    "缺少客户输入：",
    "无法读取任务状态",
    "无法读取安全审计",
    "无法读取渠道账号",
    "无法读取邮件账号",
    "无法读取系统状态",
    "无法读取运行状态",
    "暂停小范围发布？",
    "开始小范围发布？",
    "取消配置",
    "创建市场",
    "市场名称",
    "用户撤销全部会话",
    "留空保留当前密码",
    "正在加载知识库…",
    "请先选择一条知识",
    "安全配置",
    "安全记录",
    "审批说明",
    "接入状态",
    "接受会话",
    "收件配置",
    "收件密码",
    "发件地址",
    "回复地址",
    "当前密码",
    "初始密码",
    "验证并登录",
    "网络请求失败，请稍后重试",
    "请求超时，请稍后重试",
    "LiveKit 会话凭证不可用",
    "Provider 回执",
    "两步验证操作失败",
    "验证码确认失败",
    "权限规则保存失败",
    "模型配置保存失败",
    "DTMF 发送失败",
    "例如：派送失败处理",
    "无法保存处理方案",
    "附件存储已配置",
    "运营控制面发布",
    "失败 {{0}}",
    "语音 {{0}}",
    "{{0}} 账号",
    "错误：{{0}}",
]

LOCALES = {
    "en": {
        "name": "English",
        "requirements": (
            "Use concise, natural enterprise-software English. Use 'ticket' for 工单, "
            "'waybill' for 运单, 'customer' for 客户, 'user' for internal 用户, "
            "'publish' for 发布 when it is a UI action, and 'email' for 邮件."
        ),
    },
    "de": {
        "name": "German",
        "requirements": (
            "Use concise, professional German for enterprise software. Use 'Ticket' for 工单, "
            "'Frachtbrief' for 运单, 'Kunde' for 客户, 'Benutzer' for internal 用户, "
            "'Veröffentlichen' for the publish action, and 'E-Mail' for 邮件."
        ),
    },
    "cnr": {
        "name": "Montenegrin (Crnogorski)",
        "requirements": (
            "Use standard contemporary Montenegrin in Latin script only, with Ijekavian forms. "
            "Never use Cyrillic or mojibake. Use 'tiket' for 工单, 'tovarni list' for 运单, "
            "'klijent' for external 客户, 'korisnik' for internal 用户, 'objavi' for 发布, "
            "and 'e-pošta' for 邮件. Prefer Montenegrin forms such as 'uspjelo', 'riješen', "
            "'sljedeći', 'posljednji', and 'nijesu' where grammatically appropriate."
        ),
    },
}

CJK_RE = re.compile(r"[\u3400-\u9fff]")
CYRILLIC_RE = re.compile(r"[\u0400-\u052f]")
PLACEHOLDER_RE = re.compile(r"\{\{\d+\}\}|%(?:\d+\$)?[sdif]|\{[A-Za-z_][A-Za-z0-9_]*\}")

SEMANTIC_REQUIREMENTS = {
    "运单": {"en": ["waybill"], "de": ["frachtbrief"], "cnr": ["tovarni list"]},
    "运单号": {"en": ["waybill"], "de": ["frachtbrief"], "cnr": ["tovarni list"]},
    "客服工单": {"en": ["ticket"], "de": ["ticket"], "cnr": ["tiket"]},
    "客户消息": {"en": ["customer"], "de": ["kunde"], "cnr": ["klijent"]},
    "当前账号": {"en": ["account"], "de": ["konto"], "cnr": ["nalog"]},
    "邮件": {"en": ["email"], "de": ["e-mail"], "cnr": ["e-pošta"]},
    "审计员": {"en": ["auditor"], "de": ["auditor"], "cnr": ["revizor"]},
    "有效权限": {"en": ["permission"], "de": ["berechtigung"], "cnr": ["dozvol"]},
    "验证并登录": {"en": ["sign in"], "de": ["anmeld"], "cnr": ["prijav"]},
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def extract_json_object(value: str) -> dict[str, str]:
    start = value.find("{")
    end = value.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError(f"bakeoff_json_missing:{value[:300]}")
    parsed = json.loads(value[start : end + 1])
    if not isinstance(parsed, dict):
        raise RuntimeError("bakeoff_json_not_object")
    return {str(key): str(text).strip() for key, text in parsed.items()}


def placeholders(value: str) -> list[str]:
    return sorted(PLACEHOLDER_RE.findall(value))


def verify_model_license(resolved_revision: str) -> tuple[str, str, str]:
    license_path = Path(
        hf_hub_download(
            repo_id=MODEL_ID,
            filename="LICENSE",
            revision=resolved_revision,
        )
    )
    license_bytes = license_path.read_bytes()
    license_text = license_bytes.decode("utf-8", errors="strict").lower()
    if (
        "apache license" not in license_text
        or "version 2.0, january 2004" not in license_text
        or "http://www.apache.org/licenses/" not in license_text
    ):
        raise RuntimeError("bakeoff_model_license_text_invalid")
    return (
        APPROVED_LICENSE,
        "LICENSE",
        hashlib.sha256(license_bytes).hexdigest(),
    )


def prompt_for(locale: str, items: list[tuple[str, str]]) -> str:
    spec = LOCALES[locale]
    payload = [{"id": item_id, "source": source} for item_id, source in items]
    return (
        "/no_think\n"
        "You are the senior localization reviewer for Nexus OSR, an enterprise logistics "
        "customer-operations platform used by support agents, administrators and operations teams. "
        f"Translate every Chinese UI string into {spec['name']}. {spec['requirements']} "
        "Translate according to logistics/customer-support product meaning, not unrelated literal senses. "
        "Keep product names and technical identifiers such as Nexus OSR, LiveKit, Provider and DTMF unchanged. "
        "Preserve every placeholder exactly, including {{0}}, %s and {name}. "
        "Return exactly one valid JSON object mapping each supplied id to one non-empty translated string. "
        "Do not add explanations, Markdown, source text, comments or extra keys.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def repair_prompt_for(locale: str, items: list[tuple[str, str, str]]) -> str:
    spec = LOCALES[locale]
    payload = [
        {"id": item_id, "source": source, "candidate": candidate}
        for item_id, source, candidate in items
    ]
    return (
        "/no_think\n"
        f"You are repairing {spec['name']} UI translations for Nexus OSR. {spec['requirements']} "
        "The candidate may use the wrong script, contain Chinese, or damage placeholders. "
        "Rewrite each candidate into one concise, correct UI string that preserves the Chinese source meaning. "
        "For Montenegrin, output Latin script only; never output Cyrillic. Preserve every placeholder exactly. "
        "Return exactly one valid JSON object mapping each supplied id to the repaired string, with no extra text.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )


def generate_object(model, tokenizer, prompt: str, expected_ids: set[str]) -> dict[str, str]:
    messages = [
        {"role": "system", "content": "Follow the localization contract exactly."},
        {"role": "user", "content": prompt},
    ]
    rendered = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    encoded = tokenizer(rendered, return_tensors="pt")
    generated = model.generate(
        **encoded,
        max_new_tokens=1400,
        do_sample=False,
        repetition_penalty=1.05,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.eos_token_id,
    )
    answer = tokenizer.decode(
        generated[0, encoded.input_ids.shape[1] :],
        skip_special_tokens=True,
    )
    parsed = extract_json_object(answer)
    if set(parsed) != expected_ids:
        raise RuntimeError(
            f"bakeoff_key_mismatch:missing={sorted(expected_ids - set(parsed))}:"
            f"extra={sorted(set(parsed) - expected_ids)}"
        )
    return parsed


def needs_repair(locale: str, source: str, translated: str) -> bool:
    return bool(
        not translated
        or CJK_RE.search(translated)
        or placeholders(source) != placeholders(translated)
        or (locale == "cnr" and CYRILLIC_RE.search(translated))
    )


def rows_for(indexed, translations):
    return [
        {
            "id": item_id,
            "source": source,
            **{locale: translations[locale][item_id] for locale in LOCALES},
        }
        for item_id, source in indexed
    ]


def validate_semantics(indexed, translations) -> None:
    source_to_id = {source: item_id for item_id, source in indexed}
    failures = []
    for source, per_locale in SEMANTIC_REQUIREMENTS.items():
        item_id = source_to_id[source]
        for locale, required_terms in per_locale.items():
            value = translations[locale][item_id].casefold()
            if not all(term.casefold() in value for term in required_terms):
                failures.append((source, locale, translations[locale][item_id], required_terms))
    if failures:
        raise RuntimeError(f"bakeoff_semantic_contract_failed:{failures}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    info = model_info(MODEL_ID, revision=REQUESTED_REVISION)
    resolved_revision = str(info.sha)
    if not re.fullmatch(r"[0-9a-f]{40}", resolved_revision):
        raise RuntimeError(f"bakeoff_model_revision_invalid:{resolved_revision}")
    license_value, license_evidence, license_evidence_sha256 = verify_model_license(resolved_revision)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=resolved_revision)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        revision=resolved_revision,
        dtype="auto",
        low_cpu_mem_usage=True,
    )
    model.eval()
    model.to("cpu")

    indexed = [(f"s{index:03d}", source) for index, source in enumerate(SOURCES)]
    translations: dict[str, dict[str, str]] = {locale: {} for locale in LOCALES}

    with torch.inference_mode():
        for locale in LOCALES:
            for offset in range(0, len(indexed), args.batch_size):
                group = indexed[offset : offset + args.batch_size]
                translations[locale].update(
                    generate_object(
                        model,
                        tokenizer,
                        prompt_for(locale, group),
                        {item_id for item_id, _source in group},
                    )
                )

        args.output.mkdir(parents=True, exist_ok=True)
        write_json(args.output / "qwen3-quality-bakeoff.raw.json", rows_for(indexed, translations))

        for locale in LOCALES:
            invalid = [
                (item_id, source, translations[locale][item_id])
                for item_id, source in indexed
                if needs_repair(locale, source, translations[locale][item_id])
            ]
            for offset in range(0, len(invalid), 8):
                group = invalid[offset : offset + 8]
                translations[locale].update(
                    generate_object(
                        model,
                        tokenizer,
                        repair_prompt_for(locale, group),
                        {item_id for item_id, _source, _candidate in group},
                    )
                )

    write_json(args.output / "qwen3-quality-bakeoff.repaired.json", rows_for(indexed, translations))

    for item_id, source in indexed:
        for locale in LOCALES:
            translated = translations[locale][item_id]
            if not translated:
                raise RuntimeError(f"bakeoff_empty:{locale}:{item_id}")
            if CJK_RE.search(translated):
                raise RuntimeError(f"bakeoff_cjk_residue:{locale}:{item_id}:{translated}")
            if placeholders(source) != placeholders(translated):
                raise RuntimeError(f"bakeoff_placeholder_mismatch:{locale}:{item_id}:{translated}")
            if locale == "cnr" and CYRILLIC_RE.search(translated):
                raise RuntimeError(f"bakeoff_cyrillic:{item_id}:{translated}")

    validate_semantics(indexed, translations)
    write_json(args.output / "qwen3-quality-bakeoff.json", rows_for(indexed, translations))
    write_json(
        args.output / "qwen3-quality-bakeoff-metadata.json",
        {
            "schema": "nexus.i18n-qwen3-quality-bakeoff.v2",
            "model_id": MODEL_ID,
            "requested_revision": REQUESTED_REVISION,
            "resolved_revision": resolved_revision,
            "license": license_value,
            "license_evidence": license_evidence,
            "license_evidence_sha256": license_evidence_sha256,
            "sample_count": len(SOURCES),
            "source_sha256": hashlib.sha256(
                json.dumps(SOURCES, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "policy": "quality_bakeoff_only_not_product_catalog_authority",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
