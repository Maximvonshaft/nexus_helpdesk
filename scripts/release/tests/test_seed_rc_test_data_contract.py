from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SEED_SCRIPT = ROOT / "scripts" / "release" / "seed_rc_test_data.py"
SIDE_EFFECT_SCRIPT = ROOT / "scripts" / "release" / "rc_test_side_effects.py"


class RcSeedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SEED_SCRIPT.read_text(encoding="utf-8")
        cls.side_effect_source = SIDE_EFFECT_SCRIPT.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(SEED_SCRIPT))

    def _seed_function(self) -> ast.FunctionDef:
        for node in self.tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "seed_public_origin_binding":
                return node
        self.fail("seed_public_origin_binding function is missing")

    def test_standalone_helpers_bootstrap_backend_before_app_imports(self) -> None:
        for name, source in (
            ("seed", self.source),
            ("side_effect", self.side_effect_source),
        ):
            with self.subTest(helper=name):
                backend_position = source.index("_BACKEND_ROOT =")
                path_insert_position = source.index("sys.path.insert")
                first_app_import = source.index("from app.")
                self.assertLess(backend_position, path_insert_position)
                self.assertLess(path_insert_position, first_app_import)
                self.assertIn('parents[2] / "backend"', source)
                self.assertIn("_BACKEND_ROOT.is_dir()", source)

    def test_canonical_models_are_registered_before_session_creation(self) -> None:
        function = self._seed_function()
        calls: list[tuple[str, int]] = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                calls.append((node.func.id, node.lineno))
        positions = {name: line for name, line in calls}
        self.assertIn("register_all_models", positions)
        self.assertIn("SessionLocal", positions)
        self.assertLess(positions["register_all_models"], positions["SessionLocal"])

    def test_origin_is_runtime_configurable_and_normalized(self) -> None:
        self.assertIn('"RC_PUBLIC_ORIGIN"', self.source)
        self.assertIn("normalize_public_origin(requested_origin)", self.source)
        self.assertNotIn('origin = "https://rc-test.invalid"', self.source)

    def test_seed_is_idempotent_and_committed(self) -> None:
        self.assertIn("WebchatPublicOriginBinding.normalized_origin == origin", self.source)
        self.assertIn("if binding is None:", self.source)
        self.assertIn("db.commit()", self.source)
        self.assertIn("db.refresh(binding)", self.source)
        self.assertIn("db.rollback()", self.source)
        self.assertIn("db.close()", self.source)

    def test_bounded_configuration_rejects_control_characters(self) -> None:
        self.assertIn('any(char in value for char in "\\r\\n\\x00")', self.source)
        for name in (
            "RC_PUBLIC_ORIGIN",
            "RC_TEST_TENANT_KEY",
            "RC_TEST_CHANNEL_KEY",
            "RC_TEST_DISPLAY_NAME",
        ):
            self.assertIn(f'"{name}"', self.source)

    def test_seed_failure_surface_is_bounded(self) -> None:
        self.assertIn("RC_SEED_FAILED reason=invalid_configuration", self.source)
        self.assertIn("RC_SEED_FAILED reason=database_or_model_boundary", self.source)
        self.assertNotIn("traceback.print_exc", self.source)


if __name__ == "__main__":
    unittest.main()
