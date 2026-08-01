from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import build_production_catalogs_v1 as builder

CRITICAL_OVERRIDES_PATH = Path(__file__).with_name("critical_operator_catalog_overrides_v1.json")
CRITICAL_OVERRIDES_SHA256 = "265727bb339ba3bc1d986b6091468ca93ac34ee83dc2b002ab63d8c8918c08a0"
BASE_LOAD_OVERRIDES = builder.load_overrides


def load_merged_overrides(path: Path) -> dict[str, dict[str, str]]:
    values = BASE_LOAD_OVERRIDES(path)
    raw = CRITICAL_OVERRIDES_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != CRITICAL_OVERRIDES_SHA256:
        raise RuntimeError(f"critical_override_digest_mismatch:{digest}")
    critical = json.loads(raw)
    if set(critical) != {"en", "de", "cnr"}:
        raise RuntimeError("critical_override_locale_set_invalid")
    for locale, entries in critical.items():
        values[locale].update(entries)
    return values


def output_path_from_argv() -> Path:
    try:
        index = sys.argv.index("--output")
        return Path(sys.argv[index + 1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError("catalog_output_argument_missing") from exc


builder.load_overrides = load_merged_overrides
result = builder.main()
metadata_path = output_path_from_argv() / "production-catalog-metadata.json"
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
metadata["base_override_sha256"] = builder.OVERRIDES_SHA256
metadata["critical_override_sha256"] = CRITICAL_OVERRIDES_SHA256
metadata["critical_override_counts"] = {
    locale: len(entries)
    for locale, entries in json.loads(CRITICAL_OVERRIDES_PATH.read_text(encoding="utf-8")).items()
}
metadata_path.write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
raise SystemExit(result)
