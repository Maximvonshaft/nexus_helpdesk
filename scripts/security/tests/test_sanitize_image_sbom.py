from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sanitize_image_sbom import SbomSanitizationError, sanitize_sbom  # noqa: E402


class SanitizeImageSbomTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_filters_files_and_base_packages_and_strips_contact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "raw.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "type": "file",
                            "name": "/app/customer@example.com/1234567890",
                            "version": "1",
                        },
                        {
                            "type": "library",
                            "publisher": "Debian <maintainer@example.com>",
                            "name": "base-files",
                            "version": "12",
                            "purl": "pkg:deb/debian/base-files@12",
                            "licenses": [{"license": {"id": "GPL-2.0-only"}}],
                        },
                        {
                            "type": "library",
                            "author": "Maintainer <person@example.com>",
                            "name": "aiohttp",
                            "version": "3.14.1",
                            "purl": "pkg:pypi/aiohttp@3.14.1",
                            "licenses": [{"expression": "Apache-2.0 AND MIT"}],
                            "properties": [{"name": "path", "value": "/home/person@example.com"}],
                        },
                    ],
                },
            )
            overrides = self._write(
                root,
                "overrides.json",
                {"schema_version": "nexus_container_license_metadata_overrides_v1", "entries": []},
            )
            output = root / "safe.json"

            code = sanitize_sbom(source, overrides, output)
            payload = json.loads(output.read_text())
            encoded = output.read_text()

            self.assertEqual(code, 0)
            self.assertEqual([item["purl"] for item in payload["components"]], ["pkg:pypi/aiohttp@3.14.1"])
            self.assertNotIn("example.com", encoded)
            self.assertNotIn("/home/", encoded)
            properties = {item["name"]: item["value"] for item in payload["metadata"]["properties"]}
            self.assertEqual(properties["nexus:base-os-package-count"], "1")

    def test_normalizes_common_license_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "raw.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "type": "library",
                            "name": "sample",
                            "version": "1",
                            "purl": "pkg:pypi/sample@1",
                            "licenses": [{"license": {"name": "Apache License 2.0"}}],
                        }
                    ],
                },
            )
            overrides = self._write(root, "overrides.json", {"schema_version": "nexus_container_license_metadata_overrides_v1", "entries": []})
            output = root / "safe.json"

            self.assertEqual(sanitize_sbom(source, overrides, output), 0)
            license_id = json.loads(output.read_text())["components"][0]["licenses"][0]["license"]["id"]
            self.assertEqual(license_id, "Apache-2.0")

    def test_missing_license_requires_exact_evidence_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "raw.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {"type": "library", "name": "missing", "version": "1", "purl": "pkg:pypi/missing@1"}
                    ],
                },
            )
            empty = self._write(root, "empty.json", {"schema_version": "nexus_container_license_metadata_overrides_v1", "entries": []})
            failed = root / "failed.json"
            self.assertEqual(sanitize_sbom(source, empty, failed), 1)
            self.assertEqual(json.loads((root / "failed.json.summary.json").read_text())["unresolved_count"], 1)

            exact = self._write(
                root,
                "exact.json",
                {
                    "schema_version": "nexus_container_license_metadata_overrides_v1",
                    "entries": [
                        {
                            "purl": "pkg:pypi/missing@1",
                            "license": "MIT",
                            "source": "https://example.test/missing-license",
                            "reason": "Upstream package metadata omits its declared MIT license.",
                        }
                    ],
                },
            )
            passed = root / "passed.json"
            self.assertEqual(sanitize_sbom(source, exact, passed), 0)
            self.assertEqual(json.loads(passed.read_text())["components"][0]["licenses"][0]["license"]["id"], "MIT")

    def test_stale_unused_override_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "raw.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "type": "library",
                            "name": "licensed",
                            "version": "1",
                            "purl": "pkg:pypi/licensed@1",
                            "licenses": [{"license": {"id": "MIT"}}],
                        }
                    ],
                },
            )
            overrides = self._write(
                root,
                "overrides.json",
                {
                    "schema_version": "nexus_container_license_metadata_overrides_v1",
                    "entries": [
                        {
                            "purl": "pkg:pypi/unused@9",
                            "license": "MIT",
                            "source": "https://example.test/unused-license",
                            "reason": "This exact override is stale and must not remain silently.",
                        }
                    ],
                },
            )
            output = root / "safe.json"

            self.assertEqual(sanitize_sbom(source, overrides, output), 1)
            summary = json.loads((root / "safe.json.summary.json").read_text())
            self.assertEqual(summary["unused_override_count"], 1)

    def test_duplicate_purl_is_deduplicated_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = {
                "type": "library",
                "name": "same",
                "version": "1",
                "purl": "pkg:pypi/same@1",
                "licenses": [{"license": {"id": "MIT"}}],
            }
            source = self._write(root, "raw.json", {"bomFormat": "CycloneDX", "components": [component, component]})
            overrides = self._write(root, "overrides.json", {"schema_version": "nexus_container_license_metadata_overrides_v1", "entries": []})
            output = root / "safe.json"

            self.assertEqual(sanitize_sbom(source, overrides, output), 0)
            self.assertEqual(len(json.loads(output.read_text())["components"]), 1)


if __name__ == "__main__":
    unittest.main()
