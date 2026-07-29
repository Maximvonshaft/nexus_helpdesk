#!/usr/bin/env python3
"""Render the canonical non-secret runtime configuration used by activation evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.activation_runtime_configuration import (  # noqa: E402
    activation_runtime_configuration_digest,
    canonical_activation_runtime_configuration,
)


class ConfigurationRenderError(ValueError):
    pass


def _parse_env(paths: list[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ConfigurationRenderError(f"env_file_invalid:{path.name}")
        for number, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in raw:
                raise ConfigurationRenderError(
                    f"env_line_invalid:{path.name}:{number}"
                )
            key, value = raw.split("=", 1)
            key = key.strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key):
                raise ConfigurationRenderError(
                    f"env_key_invalid:{path.name}:{number}"
                )
            values[key] = value.strip()
    return values


def render(values: dict[str, str]) -> dict[str, object]:
    profile = str(values.get("PRODUCTION_PROFILE") or "").strip().lower()
    configuration = canonical_activation_runtime_configuration(
        profile=profile,
        environment=values,
    )
    digest = activation_runtime_configuration_digest(
        profile=profile,
        environment=values,
    )
    return {
        "schema": "nexus.activation-runtime-configuration-receipt.v1",
        "profile": profile,
        "configuration_digest": digest,
        "configuration": configuration,
        "contains_secrets": False,
    }


def _input_values(args: argparse.Namespace) -> dict[str, str]:
    if args.environment:
        if args.env_file:
            raise ConfigurationRenderError("activation_input_modes_conflict")
        return dict(os.environ)
    if not args.env_file:
        raise ConfigurationRenderError("activation_input_required")
    return _parse_env(args.env_file)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", action="append", type=Path)
    parser.add_argument("--environment", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        payload = render(_input_values(args))
    except (ConfigurationRenderError, OSError, UnicodeError, ValueError) as exc:
        print(f"activation_configuration_error:{exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
