from __future__ import annotations

import subprocess
from pathlib import Path


def test_speedaf_uat_smoke_redacts_order_query_pii_fields():
    script = Path("scripts/smoke/smoke_speedaf_mcp_contract.sh")
    source = script.read_text(encoding="utf-8")
    start = source.index("redact() {")
    end = source.index("\n}\n\n{", start) + 3
    redactor = source[start:end]
    sample = '{"raw_preview":"{\\"acceptAddress\\":\\"Schi 13, 80\\",\\"acceptMobile\\":\\"41790000000\\",\\"waybillCode\\":\\"CH123456789\\",\\"acceptName\\":\\"uu\\",\\"status\\":\\"1\\"}"}'
    result = subprocess.run(["bash", "-c", redactor + "\nredact"], input=sample, text=True, capture_output=True, check=True)
    sanitized = result.stdout
    assert "Schi 13" not in sanitized
    assert "uu" not in sanitized
    assert "41790000000" not in sanitized
    assert "CH123456789" not in sanitized
    assert "[ADDRESS-REDACTED]" in sanitized
    assert "[NAME-REDACTED]" in sanitized
    assert "[PHONE-REDACTED]" in sanitized
    assert "[WAYBILL-REDACTED]" in sanitized
