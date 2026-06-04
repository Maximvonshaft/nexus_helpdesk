from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DomainRuntimeFlags:
    enabled: bool = False
    shadow_mode: bool = True
    retrieval_rerank_enabled: bool = False
    guard_enforcement_enabled: bool = False
    answer_planner_enabled: bool = False
    trace_enabled: bool = True
    eval_strict_mode: bool = True

    @classmethod
    def from_env(cls) -> "DomainRuntimeFlags":
        return cls(
            enabled=_env_bool("DOMAIN_INTELLIGENCE_ENABLED", False),
            shadow_mode=_env_bool("DOMAIN_INTELLIGENCE_SHADOW_MODE", True),
            retrieval_rerank_enabled=_env_bool("DOMAIN_RETRIEVAL_RERANK_ENABLED", False),
            guard_enforcement_enabled=_env_bool("DOMAIN_GUARD_ENFORCEMENT_ENABLED", False),
            answer_planner_enabled=_env_bool("DOMAIN_ANSWER_PLANNER_ENABLED", False),
            trace_enabled=_env_bool("DOMAIN_TRACE_ENABLED", True),
            eval_strict_mode=_env_bool("DOMAIN_EVAL_STRICT_MODE", True),
        )
