from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
SECURITY_ROOT = Path(__file__).resolve().parents[1]
if str(SECURITY_ROOT) not in sys.path:
    sys.path.insert(0, str(SECURITY_ROOT))

from validate_release_image_evidence import validate_evidence_set  # noqa: E402


class CandidateImageRuntimeContractTests(unittest.TestCase):
    def test_candidate_app_healthcheck_uses_python_not_curl(self) -> None:
        compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(
            encoding="utf-8"
        )

        app_section = compose.split("  app-candidate:", 1)[1].split(
            "  worker-outbound-candidate:", 1
        )[0]
        self.assertNotIn('"curl"', app_section)
        self.assertIn("urllib.request.urlopen", app_section)
        self.assertIn("http://127.0.0.1:8080/readyz", app_section)
        self.assertIn("assert response.status == 200", app_section)

    def test_failed_artifact_scan_quarantines_all_candidate_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "artifacts" / "release-image"
            artifact_dir.mkdir(parents=True)
            artifact_dir.joinpath("artifact-scan-exit-code").write_text(
                "1\n", encoding="utf-8"
            )
            unsafe_summary = "Bear" + "er unsafe-value-that-must-not-be-uploaded\n"
            artifact_dir.joinpath("candidate-build-summary.txt").write_text(
                unsafe_summary,
                encoding="utf-8",
            )
            artifact_dir.joinpath("image.safe.cdx.json").write_text(
                '{"unsafe":"payload"}\n', encoding="utf-8"
            )
            output = artifact_dir / "structured-evidence-scan.json"

            result = validate_evidence_set(
                sbom=artifact_dir / "image.safe.cdx.json",
                sbom_summary=artifact_dir / "image.safe.cdx.json.summary.json",
                raw_digests=artifact_dir / "raw-evidence-digests.json",
                vulnerabilities=artifact_dir / "vulnerability-summary.json",
                licenses=artifact_dir / "license-summary.json",
                manifest=artifact_dir / "release-image-manifest.json",
                output=output,
            )

            self.assertEqual(result, 1)
            self.assertEqual(
                {path.name for path in artifact_dir.iterdir()},
                {
                    "release-image-quarantine.json",
                    "structured-evidence-scan.json",
                },
            )
            quarantine = json.loads(
                (artifact_dir / "release-image-quarantine.json").read_text()
            )
            self.assertEqual(quarantine["status"], "quarantined")
            self.assertFalse(quarantine["unsafe_artifacts_uploaded"])
            self.assertEqual(quarantine["reason"], "artifact_scan_not_clean")


if __name__ == "__main__":
    unittest.main()
