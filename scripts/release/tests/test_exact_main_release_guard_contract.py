from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GUARD = (ROOT / "scripts/release/require_exact_current_main.sh").read_text(
    encoding="utf-8"
)
PUBLISH = (ROOT / "scripts/release/publish_controlled_image.sh").read_text(
    encoding="utf-8"
)
FINALIZE = (ROOT / "scripts/release/finalize_controlled_candidate.sh").read_text(
    encoding="utf-8"
)


class ExactMainReleaseGuardContractTests(unittest.TestCase):
    def test_guard_revalidates_remote_main_and_tracked_tree(self) -> None:
        for marker in (
            ': "${SOURCE_SHA:?SOURCE_SHA required}"',
            "git rev-parse HEAD",
            "git fetch --no-tags origin main",
            "git rev-parse origin/main",
            "git diff --quiet",
            "git diff --cached --quiet",
            "EXACT_CURRENT_MAIN_VALID=true",
        ):
            self.assertIn(marker, GUARD)

    def test_every_irreversible_release_script_uses_single_guard(self) -> None:
        guard_call = "bash scripts/release/require_exact_current_main.sh"
        self.assertEqual(PUBLISH.count(guard_call), 1)
        self.assertEqual(FINALIZE.count(guard_call), 1)
        self.assertLess(PUBLISH.index(guard_call), PUBLISH.index("docker login ghcr.io"))
        self.assertLess(
            FINALIZE.index(guard_call),
            FINALIZE.index("build_controlled_candidate_manifest.py"),
        )


if __name__ == "__main__":
    unittest.main()
