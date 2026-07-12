from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sanitize_image_sbom import sanitize_sbom  # noqa: E402


class AlpineImageSbomTests(unittest.TestCase):
    def test_apk_packages_contribute_to_base_count_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            apk_purls = {
                "pkg:apk/alpine/alpine-baselayout@3.7.0-r0?arch=x86_64",
                "pkg:apk/alpine/musl@1.2.5-r10?arch=x86_64",
            }
            source = root / "raw.json"
            source.write_text(
                json.dumps(
                    {
                        "bomFormat": "CycloneDX",
                        "components": [
                            {
                                "type": "library",
                                "name": "alpine-baselayout",
                                "version": "3.7.0-r0",
                                "purl": sorted(apk_purls)[0],
                            },
                            {
                                "type": "library",
                                "name": "musl",
                                "version": "1.2.5-r10",
                                "purl": sorted(apk_purls)[1],
                            },
                            {
                                "type": "library",
                                "name": "sample",
                                "version": "1",
                                "purl": "pkg:pypi/sample@1",
                                "licenses": [{"license": {"id": "MIT"}}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            overrides = root / "overrides.json"
            overrides.write_text(
                json.dumps(
                    {
                        "schema_version": "nexus_container_license_metadata_overrides_v1",
                        "entries": [],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "safe.json"

            self.assertEqual(sanitize_sbom(source, overrides, output), 0)
            payload = json.loads(output.read_text())
            properties = {
                entry["name"]: entry["value"]
                for entry in payload["metadata"]["properties"]
            }
            expected_digest = hashlib.sha256(
                "\n".join(sorted(apk_purls)).encode("utf-8")
            ).hexdigest()

            self.assertEqual(properties["nexus:base-os-package-count"], "2")
            self.assertEqual(
                properties["nexus:base-os-purl-set-sha256"],
                "sha256:" + expected_digest,
            )
            self.assertEqual(
                [component["purl"] for component in payload["components"]],
                ["pkg:pypi/sample@1"],
            )


if __name__ == "__main__":
    unittest.main()
