from __future__ import annotations

import subprocess
import logging

def polish_reply_text(original_request: str, agent_note: str, bulletin_context: str = "") -> str:
    """Uses Gemini CLI to rewrite the human note, with a fallback to a hardcoded string."""
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

    try:
        result = subprocess.run(
            ["gemini", "--prompt", prompt],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            logging.error(f"Gemini CLI failed with code {result.returncode}: {result.stderr}")
    except Exception as exc:
        logging.error(f"Exception calling Gemini CLI: {exc}")

    # Fallback
    extra = f"\n\nRelevant notice(s):\n{bulletin_context}" if bulletin_context else ""
    return f"Dear Customer,\n\nUpdate on your request: {agent_note}{extra}\n\nBest regards,\nSPEEDAF Support"
