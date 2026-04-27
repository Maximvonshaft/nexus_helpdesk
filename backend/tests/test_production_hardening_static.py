from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_webchat_widget_uses_secure_header_transport():
    content = (ROOT / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")
    unsafe_fragment = "?visitor" + "_" + "token="
    header_name = "X-Webchat" + "-" + "Visitor" + "-" + "Token"
    assert unsafe_fragment not in content
    assert header_name in content
