from __future__ import annotations


def customer_visible_fallback(language: str | None, body: str | None) -> str:
    """Return the sole deterministic customer-visible terminal fallback."""

    hint = str(language or "").strip().lower()
    customer_body = str(body or "")
    if hint.startswith("zh") or any("\u4e00" <= char <= "\u9fff" for char in customer_body):
        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"
    if hint.startswith("de"):
        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."
    return "Sorry, I could not complete that request right now. Please try again or ask for human support."
