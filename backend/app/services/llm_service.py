from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..settings import get_settings

settings = get_settings()
API_KEY = settings.dashscope_api_key


def polish_reply_text(original_request: str, agent_note: str, bulletin_context: str = "") -> str:
    """Uses DashScope when configured; otherwise returns a deterministic local fallback."""
    if not API_KEY:
        extra = f"\n\nRelevant notice(s):\n{bulletin_context}" if bulletin_context else ""
        return f"Dear Customer,\n\nUpdate on your request: {agent_note}{extra}\n\nBest regards,\nSPEEDAF Support"

    prompt = f"""
You are an AI assistant for SPEEDAF customer support.
A customer submitted the following request:
"{original_request}"

A human customer service agent has processed this and left the following short conclusion/note:
"{agent_note}"

The following market notices or public announcements may apply. Use them only if relevant and do not invent facts beyond them:
"{bulletin_context}"

Please rewrite the human's short note into a polite, professional, and clear reply addressed directly to the customer.
Keep it concise but friendly. Do not invent any new facts or tracking numbers not present in the note.
Output ONLY the final reply text, nothing else.
"""

    data = {
        "model": "qwen-max",
        "input": {
            "messages": [
                {"role": "system", "content": "You are a professional customer service AI."},
                {"role": "user", "content": prompt},
            ]
        },
        "parameters": {"temperature": 0.2},
    }

    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload["output"]["text"].strip()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(2**attempt)
                continue
            break
        except Exception:
            time.sleep(2**attempt)

    extra = f"\n\nRelevant notice(s):\n{bulletin_context}" if bulletin_context else ""
    return f"Dear Customer,\n\nUpdate on your request: {agent_note}{extra}\n\nBest regards,\nSPEEDAF Support"
