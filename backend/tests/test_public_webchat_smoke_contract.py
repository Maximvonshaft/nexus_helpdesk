from pathlib import Path

SCRIPT = Path("scripts/smoke/public_webchat_smoke.py")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_public_webchat_smoke_contract_strings_present():
    text = _text()
    for marker in (
        "/healthz",
        "/readyz",
        "/webchat/demo/",
        "/api/webchat/init",
        "release_metadata_complete",
        "reply_starts_json",
        "PUBLIC_WEBCHAT_SMOKE_PASS=true",
    ):
        assert marker in text


def test_public_webchat_smoke_summary_contains_release_metadata_fields():
    text = _text()
    for marker in ("healthz", "readyz", "release_metadata_complete", "reply_starts_json", "reply_source", "reply_elapsed_ms", "errors"):
        assert marker in text


def test_public_webchat_smoke_does_not_use_secret_headers():
    text = _text()
    forbidden = ["Auth" + "orization", "Bear" + "er ", "OPEN" + "AI_API_KEY", "AI_RUNTIME" + "_TOKEN"]
    assert not any(marker in text for marker in forbidden)
