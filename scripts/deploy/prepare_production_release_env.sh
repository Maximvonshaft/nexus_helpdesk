#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RELEASE_METADATA_ENV="${1:-${RELEASE_METADATA_ENV:-}}"
PROD_ENV="${PROD_ENV:-$ROOT_DIR/deploy/.env.prod}"
OUTPUT_ENV="${OUTPUT_ENV:-$ROOT_DIR/deploy/.env.prod.next}"
APP_HOST_PORT_OVERRIDE="${APP_HOST_PORT_OVERRIDE:-}"

usage() {
  cat <<'EOF'
Usage:
  scripts/deploy/prepare_production_release_env.sh /path/to/release-metadata.env

Environment:
  PROD_ENV=/opt/nexus_helpdesk/deploy/.env.prod
  OUTPUT_ENV=/opt/nexus_helpdesk/deploy/.env.prod.next
  APP_HOST_PORT_OVERRIDE=18086

This script validates the release-metadata artifact, copies the current
production env file, and atomically upserts non-secret release identity fields.
It does not run docker compose, reload nginx, or change public traffic.
EOF
}

if [ -z "$RELEASE_METADATA_ENV" ] || [ "${RELEASE_METADATA_ENV:-}" = "-h" ] || [ "${RELEASE_METADATA_ENV:-}" = "--help" ]; then
  usage
  exit 2
fi
if [ ! -r "$RELEASE_METADATA_ENV" ]; then
  echo "release metadata file is not readable: $RELEASE_METADATA_ENV" >&2
  exit 2
fi
if [ ! -r "$PROD_ENV" ]; then
  echo "production env file is not readable: $PROD_ENV" >&2
  exit 2
fi
if [ "$OUTPUT_ENV" = "$PROD_ENV" ] && [ "${ALLOW_IN_PLACE:-false}" != "true" ]; then
  echo "refusing in-place update; set OUTPUT_ENV to a separate file or ALLOW_IN_PLACE=true" >&2
  exit 2
fi

python - "$RELEASE_METADATA_ENV" "$PROD_ENV" "$OUTPUT_ENV" "$APP_HOST_PORT_OVERRIDE" <<'PY'
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

metadata_path = Path(sys.argv[1])
prod_path = Path(sys.argv[2])
output_path = Path(sys.argv[3])
host_port_override = sys.argv[4]

required_keys = (
    "GIT_SHA",
    "BUILD_TIME",
    "IMAGE_TAG",
    "APP_VERSION",
    "FRONTEND_BUILD_SHA",
)
aliases = {
    "COMMIT_SHA": "GIT_SHA",
    "APP_GIT_SHA": "GIT_SHA",
    "APP_BUILD_TIME": "BUILD_TIME",
    "APP_IMAGE_TAG": "IMAGE_TAG",
    "APP_FRONTEND_BUILD_SHA": "FRONTEND_BUILD_SHA",
}
allowed_keys = set(required_keys) | set(aliases)


def fail(message: str, code: int = 3) -> None:
    print(f"release metadata validation failed: {message}", file=sys.stderr)
    raise SystemExit(code)


raw = metadata_path.read_bytes()
if b"\x00" in raw:
    fail("NUL byte is forbidden")
if b"\r" in raw:
    fail("carriage returns are forbidden")
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError as exc:
    fail(f"metadata is not valid UTF-8: {exc}")

values: dict[str, str] = {}
for line_number, line in enumerate(text.splitlines(), start=1):
    if not line:
        continue
    if "=" not in line:
        fail(f"line {line_number} is not KEY=VALUE")
    key, value = line.split("=", 1)
    if key not in allowed_keys:
        fail(f"unexpected key {key!r}")
    if key in values:
        fail(f"duplicate key {key}")
    if not value:
        fail(f"empty value for {key}")
    if "\\" in value or "\n" in value or "\r" in value:
        fail(f"escape or line break is forbidden in {key}")
    values[key] = value

missing = [key for key in required_keys if key not in values]
if missing:
    fail("missing required keys: " + ", ".join(missing))

for alias, canonical in aliases.items():
    if alias in values and values[alias] != values[canonical]:
        fail(f"alias {alias} does not match {canonical}")

sha_pattern = re.compile(r"[0-9a-f]{40}")
for key in ("GIT_SHA", "FRONTEND_BUILD_SHA"):
    if sha_pattern.fullmatch(values[key]) is None:
        fail(f"{key} must be a lowercase 40-character Git SHA")
if values["FRONTEND_BUILD_SHA"] != values["GIT_SHA"]:
    fail("FRONTEND_BUILD_SHA must match GIT_SHA")

try:
    datetime.strptime(values["BUILD_TIME"], "%Y%m%dT%H%M%SZ")
except ValueError:
    fail("BUILD_TIME must use UTC YYYYMMDDTHHMMSSZ format")

app_version = values["APP_VERSION"]
if (
    len(app_version) > 128
    or re.fullmatch(r"[a-z0-9_][a-z0-9_.-]*-[0-9a-f]{12}", app_version) is None
    or not app_version.endswith("-" + values["GIT_SHA"][:12])
):
    fail("APP_VERSION must be a safe tag ending in the source SHA prefix")

image_tag = values["IMAGE_TAG"]
expected_suffix = f":{app_version}-{values['BUILD_TIME']}"
if not image_tag.endswith(expected_suffix):
    fail("IMAGE_TAG identity does not match APP_VERSION and BUILD_TIME")
image_name = image_tag[: -len(expected_suffix)]
if (
    not image_name
    or len(image_name) > 256
    or re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*", image_name) is None
    or image_name.startswith("/")
    or image_name.endswith(("/", ":"))
    or "//" in image_name
    or ".." in image_name
    or "@" in image_name
):
    fail("IMAGE_TAG contains an invalid OCI image name")

updates = {key: values[key] for key in required_keys}
if host_port_override:
    if re.fullmatch(r"[0-9]{1,5}", host_port_override) is None:
        fail("APP_HOST_PORT_OVERRIDE must be a TCP port", 4)
    port = int(host_port_override)
    if not 1 <= port <= 65535:
        fail("APP_HOST_PORT_OVERRIDE must be between 1 and 65535", 4)
    updates["APP_HOST_PORT"] = str(port)

try:
    prod_text = prod_path.read_text(encoding="utf-8")
except UnicodeDecodeError as exc:
    fail(f"production env is not valid UTF-8: {exc}", 4)
if "\x00" in prod_text:
    fail("production env contains a NUL byte", 4)

ordered_keys = list(required_keys)
if "APP_HOST_PORT" in updates:
    ordered_keys.append("APP_HOST_PORT")

output_lines: list[str] = []
written: set[str] = set()
for line in prod_text.splitlines():
    stripped = line.lstrip()
    key = stripped.split("=", 1)[0] if "=" in stripped else ""
    if key in updates:
        if key not in written:
            output_lines.append(f"{key}={updates[key]}")
            written.add(key)
        continue
    output_lines.append(line)
for key in ordered_keys:
    if key not in written:
        output_lines.append(f"{key}={updates[key]}")

output_path.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(
    prefix=f".{output_path.name}.",
    dir=output_path.parent,
    text=True,
)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(output_lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary_name, output_path)
except BaseException:
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise

print("PRODUCTION_RELEASE_ENV_PREPARED=true")
print(f"output_env={output_path}")
print(f"git_sha={values['GIT_SHA']}")
print(f"image_tag={values['IMAGE_TAG']}")
if host_port_override:
    print(f"app_host_port={updates['APP_HOST_PORT']}")
PY
