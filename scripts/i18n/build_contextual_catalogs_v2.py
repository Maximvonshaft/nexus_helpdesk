from __future__ import annotations

import argparse
from pathlib import Path

from contextual_catalog_contract_v2 import LOCALES
from contextual_catalog_finalize_v2 import finalize
from contextual_catalog_generation_v2 import generate
from contextual_catalog_prepare_v2 import prepare

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--inventory", type=Path, required=True)
    prepare_parser.add_argument("--base-overrides", type=Path, required=True)
    prepare_parser.add_argument("--critical-contract", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    prepare_parser.add_argument("--product-head", required=True)
    prepare_parser.set_defaults(handler=prepare)

    generate_parser = sub.add_parser("generate")
    generate_parser.add_argument("--locale", choices=LOCALES, required=True)
    generate_parser.add_argument("--input", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--batch-items", type=int, default=8)
    generate_parser.add_argument("--max-new-tokens", type=int, default=320)
    generate_parser.add_argument("--review-passes", type=int, default=1)
    generate_parser.set_defaults(handler=generate)

    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--inventory", type=Path, required=True)
    finalize_parser.add_argument("--base-overrides", type=Path, required=True)
    finalize_parser.add_argument("--critical-contract", type=Path, required=True)
    finalize_parser.add_argument("--preparation-metadata", type=Path, required=True)
    finalize_parser.add_argument("--generated-dir", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--product-head", required=True)
    finalize_parser.set_defaults(handler=finalize)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
