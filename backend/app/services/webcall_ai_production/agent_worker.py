from __future__ import annotations

import logging
import os
import time

from .config import get_webcall_ai_production_settings
from .orchestrator import run_fake_turn

logger = logging.getLogger(__name__)


def health() -> dict[str, object]:
    settings = get_webcall_ai_production_settings()
    return {
        "ok": True,
        "agent_enabled": settings.agent_enabled,
        "provider_profile": settings.provider_profile,
        "stt_provider": settings.stt_provider,
        "llm_provider": settings.llm_provider,
        "tts_provider": settings.tts_provider,
    }


def main() -> None:
    settings = get_webcall_ai_production_settings()
    logger.info("webcall_ai_agent_worker_starting", extra={"agent_enabled": settings.agent_enabled, "provider_profile": settings.provider_profile})
    if not settings.agent_enabled:
        logger.info("webcall_ai_agent_worker_disabled")
        return
    interval = max(1, int(os.getenv("WEBCALL_AI_AGENT_POLL_SECONDS", "5")))
    while True:
        result = run_fake_turn("where is package 123456", language="en")
        logger.info("webcall_ai_agent_worker_heartbeat", extra={"fake_turn": result["response"]})
        time.sleep(interval)


if __name__ == "__main__":
    main()

