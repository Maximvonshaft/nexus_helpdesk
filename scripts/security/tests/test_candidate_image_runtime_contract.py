from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]


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


if __name__ == "__main__":
    unittest.main()
