from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finalize_image_sbom import finalize  # noqa: E402
from sanitize_image_sbom import sanitize_sbom  # noqa: E402
from validate_release_image_evidence import validate_sbom  # noqa: E402


class FrontendNpmSbomTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_required_npm_components_join_release_license_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = self._write(
                root,
                "image.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "type": "library",
                            "name": "sample",
                            "version": "1",
                            "purl": "pkg:pypi/sample@1",
                            "licenses": [{"license": {"id": "MIT"}}],
                        },
                        {
                            "type": "library",
                            "name": "musl",
                            "version": "1.2.5-r10",
                            "purl": "pkg:apk/alpine/musl@1.2.5-r10?arch=x86_64",
                        },
                    ],
                },
            )
            frontend = self._write(
                root,
                "frontend.json",
                {
                    "bomFormat": "CycloneDX",
                    "components": [
                        {
                            "type": "library",
                            "name": "@bufbuild/protobuf",
                            "version": "1.10.1",
                            "scope": "required",
                            "purl": "pkg:npm/%40bufbuild/protobuf@1.10.1",
                            "licenses": [
                                {
                                    "license": {
                                        "id": "(Apache-2.0 AND BSD-3-Clause)"
                                    }
                                }
                            ],
                            "properties": [
                                {
                                    "name": "cdx:npm:package:path",
                                    "value": "node_modules/@bufbuild/protobuf",
                                }
                            ],
                        },
                        {
                            "type": "library",
                            "name": "react",
                            "version": "18.3.1",
                            "scope": "required",
                            "purl": "pkg:npm/react@18.3.1",
                            "licenses": [{"license": {"id": "MIT"}}],
                        },
                        {
                            "type": "library",
                            "name": "build-only-tool",
                            "version": "9.0.0",
                            "scope": "optional",
                            "purl": "pkg:npm/build-only-tool@9.0.0",
                            "licenses": [
                                {"license": {"id": "AGPL-3.0-only"}}
                            ],
                            "properties": [
                                {
                                    "name": "cdx:npm:package:development",
                                    "value": "true",
                                }
                            ],
                        },
                    ],
                },
            )
            overrides = self._write(
                root,
                "overrides.json",
                {
                    "schema_version": "nexus_container_license_metadata_overrides_v1",
                    "entries": [],
                },
            )
            preliminary = root / "preliminary.json"
            final = root / "final.json"

            self.assertEqual(
                sanitize_sbom(
                    image,
                    overrides,
                    preliminary,
                    frontend_source=frontend,
                ),
                0,
            )
            preliminary_payload = json.loads(preliminary.read_text())
            preliminary_by_purl = {
                component["purl"]: component
                for component in preliminary_payload["components"]
            }
            self.assertEqual(
                set(preliminary_by_purl),
                {
                    "pkg:pypi/sample@1",
                    "pkg:npm/%40bufbuild/protobuf@1.10.1",
                    "pkg:npm/react@18.3.1",
                },
            )
            properties = {
                item["name"]: item["value"]
                for item in preliminary_payload["metadata"]["properties"]
            }
            self.assertEqual(
                properties["nexus:frontend-runtime-component-count"], "2"
            )
            self.assertEqual(properties["nexus:base-os-package-count"], "1")
            self.assertTrue(
                properties["nexus:frontend-source-sbom-sha256"].startswith(
                    "sha256:"
                )
            )

            self.assertEqual(finalize(preliminary, overrides, final), 0)
            final_payload = json.loads(final.read_text())
            final_by_purl = {
                component["purl"]: component
                for component in final_payload["components"]
            }
            self.assertEqual(
                final_by_purl[
                    "pkg:npm/%40bufbuild/protobuf@1.10.1"
                ]["licenses"],
                [{"expression": "Apache-2.0 AND BSD-3-Clause"}],
            )
            validate_sbom(final_payload)


if __name__ == "__main__":
    unittest.main()
