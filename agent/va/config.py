"""Runtime configuration, read once from environment variables.

Every value has a safe default except the secrets, which must be set as
Railway service variables (never committed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # AI@EC Platform LLM Service
    aiec_base_url: str = field(default_factory=lambda: _env("AIEC_BASE_URL", "https://api.tech.ec.europa.eu/ecgpt/v1"))
    aiec_api_key: str = field(default_factory=lambda: _env("AIEC_API_KEY"))
    assistant_model: str = field(default_factory=lambda: _env("ASSISTANT_MODEL", "gpt-5.1"))
    assistant_reasoning_effort: str = field(default_factory=lambda: _env("ASSISTANT_REASONING_EFFORT", "low"))
    token_budget: int = field(default_factory=lambda: _int("TOKEN_BUDGET", 60000))
    max_agent_steps: int = field(default_factory=lambda: _int("MAX_AGENT_STEPS", 6))

    # TencentDB Agent Memory (memory-core)
    memory_url: str = field(default_factory=lambda: _env("MEMORY_CORE_URL"))
    memory_token: str = field(default_factory=lambda: _env("MEMORY_CORE_TOKEN"))
    memory_service_id: str = field(default_factory=lambda: _env("MEMORY_SERVICE_ID", "default"))
    memory_admin_username: str = field(default_factory=lambda: _env("MEMORY_ADMIN_USERNAME", "admin"))
    memory_admin_user_key: str = field(default_factory=lambda: _env("MEMORY_ADMIN_USER_KEY"))
    memory_team_name: str = field(default_factory=lambda: _env("MEMORY_TEAM_NAME", "EC Virtual Assistant Demo"))
    memory_agent_name: str = field(default_factory=lambda: _env("MEMORY_AGENT_NAME", "ec-virtual-assistant"))
    memory_timeout_s: float = field(default_factory=lambda: float(_env("MEMORY_TIMEOUT_S", "8")))

    # LLM shim used by memory-core (chat/completions -> AI@EC)
    shim_enabled: bool = field(default_factory=lambda: _bool("LLM_SHIM_ENABLED", True))
    shim_token: str = field(default_factory=lambda: _env("LLM_SHIM_TOKEN"))
    shim_plain_model: str = field(default_factory=lambda: _env("SHIM_PLAIN_MODEL", "mistral-small-3.2-24b-instruct-2506"))
    shim_tools_model: str = field(default_factory=lambda: _env("SHIM_TOOLS_MODEL", "gpt-5.1"))

    # Knowledge base
    knowledge_dir: str = field(
        default_factory=lambda: _env(
            "KNOWLEDGE_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "press")
        )
    )
    tool_results_dir: str = field(default_factory=lambda: _env("TOOL_RESULTS_DIR", "/tmp/tool_results"))

    @property
    def memory_enabled(self) -> bool:
        return bool(self.memory_url and self.memory_token)


settings = Settings()
