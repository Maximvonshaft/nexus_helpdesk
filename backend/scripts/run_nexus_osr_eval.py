from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals.nexus_osr import evaluate_dataset, load_dataset, write_artifacts  # noqa: E402

DEFAULT_DATASET = BACKEND / "evals" / "nexus_osr" / "datasets" / "m7-governed-eval-v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the read-only Nexus OSR M7 governed evaluation dataset."
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET), help="Governed dataset JSON path.")
    parser.add_argument("--output-dir", help="Optional bounded/redacted artifact directory.")
    parser.add_argument("--max-artifact-bytes", type=int, default=64 * 1024)
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on case or coverage failure.")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    report = evaluate_dataset(dataset)
    if args.output_dir:
        report["artifact_manifest"] = write_artifacts(
            report,
            args.output_dir,
            max_artifact_bytes=args.max_artifact_bytes,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if args.strict and not report["run"]["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
