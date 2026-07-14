from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / ".github/workflows/rc-test-candidate.yml"


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return content.replace(old, new, 1)


content = PATH.read_text(encoding="utf-8")
content = replace_once(
    content,
    """      - name: Validate registry and release contracts
        run: |
          python - <<'PY'
          from pathlib import Path
          text = Path("docs/ai/remote-skills-registry.yaml").read_text(encoding="utf-8")
          assert text.startswith("schema: nexus.osr.remote-skills-registry.v1\\n")
          assert "name: test_release_candidate_convergence" in text
          assert "auto_upgrade: false" in text
          PY
          python -m py_compile \\
            scripts/release/generate_rc_test_env.py \\
            scripts/release/seed_rc_test_data.py \\
            scripts/release/rc_test_http_smoke.py \\
            scripts/release/rc_test_side_effects.py \\
            scripts/release/build_rc_test_manifest.py \\
            scripts/release/validate_rc_test_manifest.py \\
            scripts/release/validate_rc_test_evidence.py
          python -m unittest discover -s scripts/release/tests
          bash -n scripts/release/run_rc_test_candidate.sh
""",
    """      - name: Validate registry and release contracts
        id: rc-preflight
        run: python scripts/release/rc_preflight.py --artifact-root artifacts/rc-test
""",
    "replace inline RC preflight",
)
content = replace_once(
    content,
    """      - name: Scan failure-only evidence if RC failed
        if: always() && steps.run-rc-test.outcome == 'failure'
""",
    """      - name: Scan failure-only evidence if RC failed
        if: always() && (steps.rc-preflight.outcome == 'failure' || steps.run-rc-test.outcome == 'failure')
""",
    "include preflight failure in evidence scan",
)
content = replace_once(
    content,
    """      - name: Upload bounded RC failure evidence
        if: always() && steps.run-rc-test.outcome == 'failure' && steps.scan-failure-evidence.outcome == 'success'
""",
    """      - name: Upload bounded RC failure evidence
        if: always() && (steps.rc-preflight.outcome == 'failure' || steps.run-rc-test.outcome == 'failure') && steps.scan-failure-evidence.outcome == 'success'
""",
    "include preflight failure in evidence upload",
)
PATH.write_text(content, encoding="utf-8")
