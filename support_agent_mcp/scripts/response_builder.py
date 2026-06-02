from __future__ import annotations

from typing import Any

from status_mapper import STATUS_MAP, should_escalate
from milestone_engine import analyze_tracking_payload


def build_tracking_reply(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {
            "ok": False,
            "message": "暂时无法获取物流信息，请稍后再试。",
            "status": "",
            "escalate": True,
            "normalized": {},
        }

    analysis = analyze_tracking_payload(data)
    if not analysis or not analysis.get("timeline"):
        return {
            "ok": False,
            "message": "您的包裹目前暂无轨迹更新，请稍后再试或联系客服。\n(No tracking updates yet.)",
            "status": "No Info",
            "escalate": False,
            "normalized": {},
        }

    latest = analysis["timeline"][-1]
    status_code = str(latest.get("action", ""))
    status_meta = STATUS_MAP.get(status_code, {})
    status_label = status_meta.get("label") or latest.get("actionName") or "处理中"

    # 将原先单条判断加上，做双重保险
    legacy_escalate = should_escalate(latest.get("raw", {}))
    final_escalate = legacy_escalate or analysis["risk"]["escalate_required"]

    # 给模型一个结合内外的完整参考文本（外部给客户看，内部留底）
    combined_message = f"{analysis['customer_answer']}\n\n---\n{analysis['internal_summary']}"

    return {
        "ok": True,
        "message": combined_message,
        "status": status_label,
        "escalate": final_escalate,
        "normalized": analysis,
    }
