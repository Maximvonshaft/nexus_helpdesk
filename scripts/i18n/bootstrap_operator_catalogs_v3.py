from __future__ import annotations

import bootstrap_operator_catalogs_v2 as catalog_builder

catalog_builder.EXTRA_OVERRIDES["en"].update(
    {
        "Provider 回执": "Provider receipt",
        "{{0}} {{1}} 秒": "{{0}} {{1}} seconds",
        "{{0}}/{{1}} 次": "{{0}}/{{1}} times",
        "{{0}} · {{1}} · 队列：{{2}}": "{{0}} · {{1}} · Queue: {{2}}",
    }
)
catalog_builder.EXTRA_OVERRIDES["de"].update(
    {
        "Provider 回执": "Provider-Rückmeldung",
        "{{0}} {{1}} 秒": "{{0}} {{1}} Sekunden",
        "{{0}}/{{1}} 次": "{{0}}/{{1}} Mal",
        "{{0}} · {{1}} · 队列：{{2}}": "{{0}} · {{1}} · Warteschlange: {{2}}",
    }
)


if __name__ == "__main__":
    raise SystemExit(catalog_builder.main())
