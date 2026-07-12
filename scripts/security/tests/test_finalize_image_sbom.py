from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finalize_image_sbom import FinalizationError, finalize  # noqa: E402


class FinalizeImageSbomTests(unittest.TestCase):
    def _write(self, root: Path, name: str, payload) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_normalizes_long_names_and_dual_expression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "safe.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "library",
                            "name": "prometheus-client",
                            "version": "0.21.1",
                            "purl": "pkg:pypi/prometheus-client@0.21.1",
                            "licenses": [
                                {"license": {"id": "Apache Software License 2.0"}}
                            ],
                        },
                        {
                            "type": "library",
                            "name": "python-dateutil",
                            "version": "2.9.0.post0",
                            "purl": "pkg:pypi/python-dateutil@2.9.0.post0",
                            "licenses": [{"license": {"id": "Dual License"}}],
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
            output = root / "final.json"
            self.assertEqual(finalize(source, overrides, output), 0)
            components = {
                item["name"]: item
                for item in json.loads(output.read_text())["components"]
            }
            self.assertEqual(
                components["prometheus-client"]["licenses"],
                [{"license": {"id": "Apache-2.0"}}],
            )
            self.assertEqual(
                components["python-dateutil"]["licenses"],
                [{"expression": "BSD-3-Clause OR Apache-2.0"}],
            )

    def test_normalizes_lgpl_as_one_spdx_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "safe.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "library",
                            "name": "psycopg",
                            "version": "3.2.6",
                            "purl": "pkg:pypi/psycopg@3.2.6",
                            "licenses": [
                                {
                                    "license": {
                                        "id": "GNU Lesser General Public License v3 (LGPLv3)"
                                    }
                                }
                            ],
                        }
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
            output = root / "final.json"
            self.assertEqual(finalize(source, overrides, output), 0)
            license_id = json.loads(output.read_text())["components"][0]["licenses"][0][
                "license"
            ]["id"]
            self.assertEqual(license_id, "LGPL-3.0-only")

    def test_exact_override_resolves_missing_metadata_and_unused_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "safe.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "application",
                            "name": "python",
                            "version": "3.11.15",
                            "purl": "pkg:generic/python@3.11.15",
                            "licenses": [],
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
                            "purl": "pkg:generic/python@3.11.15",
                            "license": "PSF-2.0",
                            "source": "https://docs.python.org/3.11/license.html",
                            "reason": "The official runtime license supplies the missing metadata.",
                        }
                    ],
                },
            )
            output = root / "final.json"
            self.assertEqual(finalize(source, overrides, output), 0)
            self.assertEqual(
                json.loads(output.read_text())["components"][0]["licenses"],
                [{"license": {"id": "PSF-2.0"}}],
            )

            source_with_license = self._write(
                root,
                "already.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "application",
                            "name": "other",
                            "version": "1",
                            "purl": "pkg:pypi/other@1",
                            "licenses": [{"license": {"id": "MIT"}}],
                        }
                    ],
                },
            )
            self.assertEqual(
                finalize(source_with_license, overrides, root / "unused.json"), 1
            )

    def test_matching_override_confirms_existing_metadata_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            purl = "pkg:pypi/licensed-lib@1.0.0"
            source = self._write(
                root,
                "licensed.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "library",
                            "name": "licensed-lib",
                            "version": "1.0.0",
                            "purl": purl,
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
                            "purl": purl,
                            "license": "MIT",
                            "source": "https://example.invalid/licensed-lib",
                            "reason": "The override confirms the same authoritative license metadata.",
                        }
                    ],
                },
            )
            output = root / "final.json"
            self.assertEqual(finalize(source, overrides, output), 0)
            self.assertEqual(
                json.loads(output.read_text())["components"][0]["licenses"],
                [{"license": {"id": "MIT"}}],
            )

    def test_override_cannot_replace_existing_license_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._write(
                root,
                "licensed.json",
                {
                    "bomFormat": "CycloneDX",
                    "metadata": {"properties": []},
                    "components": [
                        {
                            "type": "library",
                            "name": "licensed-lib",
                            "version": "1.0.0",
                            "purl": "pkg:pypi/licensed-lib@1.0.0",
                            "licenses": [{"license": {"id": "AGPL-3.0-only"}}],
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
                            "purl": "pkg:pypi/licensed-lib@1.0.0",
                            "license": "MIT",
                            "source": "https://example.invalid/licensed-lib",
                            "reason": "An override must never replace authoritative license metadata.",
                        }
                    ],
                },
            )
            with self.assertRaisesRegex(
                FinalizationError, "override_existing_license_conflict"
            ):
                finalize(source, overrides, root / "final.json")


if __name__ == "__main__":
    unittest.main()
