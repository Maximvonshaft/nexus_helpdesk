from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def test_knowledge_runtime_readiness_probe_has_no_tracking_lookup_or_secret_sample_contract():
    script = (PROJECT / "scripts" / "nexus_knowledge_runtime_v2_readiness_probe.sh").read_text(encoding="utf-8")

    assert "probe_knowledge_readiness.py" in script
    assert 'exec python "${ROOT_DIR}/backend/scripts/probe_knowledge_readiness.py" "$@"' in script
    assert "SPEEDAF" not in script
    assert "waybill" not in script.lower()
    assert "caller" not in script.lower()
