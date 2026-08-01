from __future__ import annotations

import hashlib
import json
from pathlib import Path

import build_production_catalogs_v1 as builder

PLAIN_OVERRIDES_SHA256 = "ed6d9db7e85aeac51fda2c0babbbfa132d6fca430959b423d7e6a48fd3a42d9c"
CRITICAL_OVERRIDES_SHA256 = "b46f127dea2291617c77cebc16a2a3eebe56bcecaebdc9e79a2df6b7bd6382b8"
EXPECTED_BASE_COUNTS = {"en": 655, "de": 655, "cnr": 655}
EXPECTED_CRITICAL_COUNTS = {"en": 38, "de": 38, "cnr": 38}
CRITICAL_PATH = Path(__file__).with_name("critical_operator_catalog_overrides_v1.json")


def combined_override_digest() -> str:
    authority = f"{PLAIN_OVERRIDES_SHA256}\n{CRITICAL_OVERRIDES_SHA256}\n".encode("ascii")
    return hashlib.sha256(authority).hexdigest()


def read_reviewed_json(path: Path, *, expected_sha256: str, expected_counts: dict[str, int], label: str) -> dict[str, dict[str, str]]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise RuntimeError(f"{label}_digest_mismatch:{digest}")
    values = json.loads(raw)
    if set(values) != set(expected_counts):
        raise RuntimeError(f"{label}_locale_set_invalid")
    counts = {locale: len(entries) for locale, entries in values.items()}
    if counts != expected_counts:
        raise RuntimeError(f"{label}_count_invalid:{counts}")
    return values


def load_reviewed_overrides(path: Path) -> dict[str, dict[str, str]]:
    base = read_reviewed_json(
        path,
        expected_sha256=PLAIN_OVERRIDES_SHA256,
        expected_counts=EXPECTED_BASE_COUNTS,
        label="base_override",
    )
    critical = read_reviewed_json(
        CRITICAL_PATH,
        expected_sha256=CRITICAL_OVERRIDES_SHA256,
        expected_counts=EXPECTED_CRITICAL_COUNTS,
        label="critical_override",
    )
    for locale, entries in critical.items():
        base[locale].update(entries)
    return base


COMBINED_OVERRIDES_SHA256 = combined_override_digest()
builder.OVERRIDES_SHA256 = COMBINED_OVERRIDES_SHA256
builder.load_overrides = load_reviewed_overrides
result = builder.main()

# Record each source authority independently in addition to the deterministic
# combined digest produced by the base builder.
import sys
try:
    output_index = sys.argv.index("--output")
    metadata_path = Path(sys.argv[output_index + 1]) / "production-catalog-metadata.json"
except (ValueError, IndexError) as exc:
    raise RuntimeError("catalog_output_argument_missing") from exc
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
metadata["base_override_sha256"] = PLAIN_OVERRIDES_SHA256
metadata["base_override_counts"] = EXPECTED_BASE_COUNTS
metadata["critical_override_sha256"] = CRITICAL_OVERRIDES_SHA256
metadata["critical_override_counts"] = EXPECTED_CRITICAL_COUNTS
metadata["combined_override_sha256"] = COMBINED_OVERRIDES_SHA256
metadata_path.write_text(
    json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
raise SystemExit(result)
