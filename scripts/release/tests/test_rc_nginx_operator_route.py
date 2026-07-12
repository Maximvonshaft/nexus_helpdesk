from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_CONFIG = REPO_ROOT / "deploy" / "nginx" / "default.conf"


class RcNginxOperatorRouteTests(unittest.TestCase):
    def test_exact_webchat_operator_route_precedes_public_static_prefix(self) -> None:
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        exact = "location = /webchat {"
        public_prefix = "location /webchat/ {"

        self.assertIn(exact, config)
        self.assertIn(public_prefix, config)
        self.assertLess(config.index(exact), config.index(public_prefix))

        exact_block = config.split(exact, 1)[1].split("\n  }", 1)[0]
        self.assertIn("proxy_pass http://nexusdesk_app/index.html;", exact_block)
        self.assertNotIn("return 30", exact_block)


if __name__ == "__main__":
    unittest.main()
