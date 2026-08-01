from __future__ import annotations

import hashlib
import json
from pathlib import Path

import build_production_catalogs_v1 as builder

PLAIN_OVERRIDES_SHA256 = "20c415159edb8ddf9a9d15edf6ed6cf91294b062506602875f4108f39b77a209"
EXPECTED_COUNTS = {"en": 578, "de": 578, "cnr": 578}


def load_plain_overrides(path: Path) -> dict[str, dict[str, str]]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PLAIN_OVERRIDES_SHA256:
        raise RuntimeError(f"override_digest_mismatch:{digest}")
    values = json.loads(raw)
    if set(values) != set(EXPECTED_COUNTS):
        raise RuntimeError("override_locale_set_invalid")
    counts = {locale: len(entries) for locale, entries in values.items()}
    if counts != EXPECTED_COUNTS:
        raise RuntimeError(f"override_count_invalid:{counts}")
    return values


builder.OVERRIDES_SHA256 = PLAIN_OVERRIDES_SHA256
builder.load_overrides = load_plain_overrides
raise SystemExit(builder.main())
