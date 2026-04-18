from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from ..settings import get_settings

settings = get_settings()
API_KEY = settings.dashscope_api_key


def _fallback_signature(tenant_profile: dict[str, Any] | None) -> str:
    if not tenant_profile:
        return 'Best regards,\nSupport Team'
    signature_style = tenant_profile.get('signature_style') or 'Best regards'
    brand_name = tenant_profile.get('brand_name') or tenant_profile.get('display_name') or 'Support Team'
    return f"{signature_style},\n{brand_name}"


def polish_reply_text(
    original_request: str,
    agent_note: str,
    bulletin_context: str = '',
    tenant_profile: dict[str, Any] | None = None,
    tenant_knowledge_context: str = '',
) -> str:
    """Uses DashScope when configured; otherwise returns a deterministic local fallback."""
    tone_style = (tenant_profile or {}).get('tone_style') or 'professional'
    display_name = (tenant_profile or {}).get('display_name') or 'Support Assistant'
    brand_name = (tenant_profile or {}).get('brand_name') or 'Support Team'
    forbidden_claims = '\n'.join(f'- {item}' for item in ((tenant_profile or {}).get('forbidden_claims') or [])) or '- Do not invent facts'
    escalation_policy = (tenant_profile or {}).get('escalation_policy') or 'Escalate billing, legal, and compensation promises to a human supervisor.'
    language_policy = (tenant_profile or {}).get('language_policy') or 'Reply in the customer language when confidently detected, otherwise use English.'
    role_prompt = (tenant_profile or {}).get('role_prompt') or f'You are {display_name}, the customer service representative for {brand_name}.'
    system_prompt_overrides = (tenant_profile or {}).get('system_prompt_overrides') or ''

    if not API_KEY:
        extra_sections = []
        if bulletin_context:
            extra_sections.append(f"Relevant notice(s):\n{bulletin_context}")
        if tenant_knowledge_context:
            extra_sections.append(f"Tenant knowledge:\n{tenant_knowledge_context}")
        extra = ('\n\n' + '\n\n'.join(extra_sections)) if extra_sections else ''
        signature = _fallback_signature(tenant_profile)
        return f"Dear Customer,\n\nUpdate on your request: {agent_note}{extra}\n\n{signature}"

    prompt = f"""
You are an AI assistant for a multi-tenant customer support platform.
Tenant persona / role:
{role_prompt}

Tone style:
{tone_style}

Language policy:
{language_policy}

Escalation policy:
{escalation_policy}

Forbidden claims:
{forbidden_claims}

Optional system overrides:
{system_prompt_overrides}

A customer submitted the following request:
"{original_request}"

A human customer service agent has processed this and left the following short conclusion/note:
"{agent_note}"

The following market notices or public announcements may apply. Use them only if relevant and do not invent facts beyond them:
"{bulletin_context}"

The following tenant-specific knowledge may apply. Use it only if relevant and do not invent facts beyond it:
"{tenant_knowledge_context}"

Please rewrite the human's short note into a polite, professional, clear, and tenant-branded reply addressed directly to the customer.
Keep it concise but friendly. Do not invent any new facts or tracking numbers not present in the note or the approved knowledge.
Output ONLY the final reply text, nothing else.
"""

    data = {
        'model': 'qwen-max',
        'input': {
            'messages': [
                {'role': 'system', 'content': f'You are a professional customer service AI working for {brand_name}.'},
                {'role': 'user', 'content': prompt},
            ]
        },
        'parameters': {'temperature': 0.2},
    }

    req = urllib.request.Request(
        'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
        data=json.dumps(data).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {API_KEY}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = json.loads(response.read().decode('utf-8'))
                return payload['output']['text'].strip()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                time.sleep(2**attempt)
                continue
            break
        except Exception:
            time.sleep(2**attempt)

    extra_sections = []
    if bulletin_context:
        extra_sections.append(f"Relevant notice(s):\n{bulletin_context}")
    if tenant_knowledge_context:
        extra_sections.append(f"Tenant knowledge:\n{tenant_knowledge_context}")
    extra = ('\n\n' + '\n\n'.join(extra_sections)) if extra_sections else ''
    signature = _fallback_signature(tenant_profile)
    return f"Dear Customer,\n\nUpdate on your request: {agent_note}{extra}\n\n{signature}"
