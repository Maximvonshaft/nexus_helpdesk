#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


class GateFailure(RuntimeError):
    pass


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GateFailure(f"failed_to_read_json_file:{path}:{exc}") from exc

    if not isinstance(data, dict):
        raise GateFailure(f"json_file_is_not_object:{path}")

    return data


def _fetch_json_url(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = getattr(resp, "status", None)
    except Exception as exc:
        raise GateFailure(f"failed_to_fetch_url:{url}:{exc}") from exc

    if code is not None and int(code) >= 400:
        raise GateFailure(f"url_returned_http_error:{url}:{code}")

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise GateFailure(f"url_returned_invalid_json:{url}:{exc}") from exc

    if not isinstance(data, dict):
        raise GateFailure(f"url_json_is_not_object:{url}")

    return data


def _docker_container_image(container: str) -> str:
    proc = subprocess.run(
        ["docker", "inspect", "-f", "{{.Config.Image}}", container],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[-500:]
        raise GateFailure(f"docker_inspect_failed:{container}:{stderr}")

    image = (proc.stdout or "").strip()
    if not image:
        raise GateFailure(f"docker_inspect_empty_image:{container}")

    return image


def evaluate_consistency(
    *,
    docker_image: str,
    healthz: dict[str, Any],
    readyz: dict[str, Any],
    require_complete_metadata: bool = False,
) -> dict[str, Any]:
    healthz_image = healthz.get("image_tag")
    readyz_image = readyz.get("image_tag")
    readyz_database = readyz.get("database")
    migration_revision = readyz.get("migration_revision")

    checks = {
        "docker_image_matches_healthz_image_tag": docker_image == healthz_image,
        "healthz_image_tag_matches_readyz_image_tag": healthz_image == readyz_image,
        "readyz_database_ok": readyz_database == "ok",
        "readyz_migration_revision_non_empty": bool(migration_revision),
    }
    if require_complete_metadata:
        checks["healthz_release_metadata_complete"] = healthz.get("release_metadata_complete") is True
        checks["readyz_release_metadata_complete"] = readyz.get("release_metadata_complete") is True

    result = {
        "ok": all(checks.values()),
        "checks": checks,
        "docker_image": docker_image,
        "healthz_image_tag": healthz_image,
        "readyz_image_tag": readyz_image,
        "readyz_database": readyz_database,
        "readyz_migration_revision": migration_revision,
        "healthz_release_metadata_complete": healthz.get("release_metadata_complete"),
        "readyz_release_metadata_complete": readyz.get("release_metadata_complete"),
        "healthz_release_metadata_missing": healthz.get("release_metadata_missing"),
        "readyz_release_metadata_missing": readyz.get("release_metadata_missing"),
    }

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail release if Docker runtime image and /healthz-/readyz metadata drift.",
    )
    parser.add_argument("--container", default="deploy-app-1")
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--healthz-url")
    parser.add_argument("--readyz-url")
    parser.add_argument("--healthz-file")
    parser.add_argument("--readyz-file")
    parser.add_argument("--docker-image")
    parser.add_argument("--evidence-dir", default="")
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--require-complete-metadata", action="store_true")

    args = parser.parse_args()

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    evidence_dir = Path(args.evidence_dir or f"forensics/release_metadata_consistency_gate_{ts}")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        docker_image = args.docker_image or _docker_container_image(args.container)

        if args.healthz_file:
            healthz = _read_json_file(Path(args.healthz_file))
            healthz_source = str(args.healthz_file)
        else:
            healthz_url = args.healthz_url or args.base_url.rstrip("/") + "/healthz"
            healthz = _fetch_json_url(healthz_url, args.timeout_seconds)
            healthz_source = healthz_url

        if args.readyz_file:
            readyz = _read_json_file(Path(args.readyz_file))
            readyz_source = str(args.readyz_file)
        else:
            readyz_url = args.readyz_url or args.base_url.rstrip("/") + "/readyz"
            readyz = _fetch_json_url(readyz_url, args.timeout_seconds)
            readyz_source = readyz_url

        _write_json(
            evidence_dir / "docker_image_truth.json",
            {
                "container": args.container,
                "image": docker_image,
                "source": "argument" if args.docker_image else "docker inspect .Config.Image",
            },
        )
        _write_json(evidence_dir / "healthz_payload.json", {"source": healthz_source, "payload": healthz})
        _write_json(evidence_dir / "readyz_payload.json", {"source": readyz_source, "payload": readyz})

        result = evaluate_consistency(
            docker_image=docker_image,
            healthz=healthz,
            readyz=readyz,
            require_complete_metadata=args.require_complete_metadata,
        )

        _write_json(evidence_dir / "final_assertion_result.json", result)

        final_text = [
            f"RELEASE_METADATA_CONSISTENCY_PASS={str(result['ok']).lower()}",
            f"docker_image={result['docker_image']}",
            f"healthz_image_tag={result['healthz_image_tag']}",
            f"readyz_image_tag={result['readyz_image_tag']}",
            f"readyz_database={result['readyz_database']}",
            f"readyz_migration_revision={result['readyz_migration_revision']}",
            f"healthz_release_metadata_complete={result['healthz_release_metadata_complete']}",
            f"readyz_release_metadata_complete={result['readyz_release_metadata_complete']}",
            f"evidence_dir={evidence_dir}",
        ]
        (evidence_dir / "final_assertion_result.txt").write_text("\n".join(final_text) + "\n", encoding="utf-8")

        print("\n".join(final_text))

        if not result["ok"]:
            return 2

        return 0

    except GateFailure as exc:
        failure = {
            "ok": False,
            "error": str(exc),
            "evidence_dir": str(evidence_dir),
        }
        _write_json(evidence_dir / "final_assertion_result.json", failure)
        (evidence_dir / "final_assertion_result.txt").write_text(
            f"RELEASE_METADATA_CONSISTENCY_PASS=false\nerror={exc}\nevidence_dir={evidence_dir}\n",
            encoding="utf-8",
        )
        print(f"RELEASE_METADATA_CONSISTENCY_PASS=false")
        print(f"error={exc}")
        print(f"evidence_dir={evidence_dir}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
