from __future__ import annotations

from app.main import app
from scripts.qualification.route_authority import qualification_payload


def test_fastapi_method_and_normalized_path_are_unique() -> None:
    payload = qualification_payload(app)
    assert payload["duplicates"] == [], payload["duplicates"]
    assert payload["status"] == "pass"
