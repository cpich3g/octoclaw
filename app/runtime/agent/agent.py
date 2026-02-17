"""Core agent -- manages AI backend lifecycle and session handling.

Supports two backends (selected via AGENT_BACKEND env var):
- ``copilot`` (default): GitHub Copilot SDK subprocess with full MCP/skills
- ``foundry``: Azure OpenAI direct API via openai SDK + MI auth
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..config.settings import cfg
from ..sandbox import SandboxExecutor, SandboxToolInterceptor
from ..state.mcp_config import McpConfigStore
from .backend import AgentBackend
from .one_shot import auto_approve
from .prompt import build_system_prompt
from .tools import get_all_tools

logger = logging.getLogger(__name__)


def _create_backend() -> AgentBackend:
    """Instantiate the backend selected by ``cfg.agent_backend``."""
    backend_name = cfg.agent_backend
    if backend_name == "foundry":
        from .foundry_backend import FoundryBackend
        return FoundryBackend()
    # Default to copilot
    from .copilot_backend import CopilotBackend
    return CopilotBackend()


class Agent:
    """Manages an AgentBackend + session lifecycle."""

    def __init__(self) -> None:
        self._backend: AgentBackend = _create_backend()
        self._session: Any = None
        self.request_counts: dict[str, int] = {}
        self._sandbox: SandboxExecutor | None = None
        self._interceptor: SandboxToolInterceptor | None = None

    def set_sandbox(self, executor: SandboxExecutor) -> None:
        self._sandbox = executor
        self._interceptor = SandboxToolInterceptor(executor)

    @property
    def has_session(self) -> bool:
        return self._session is not None

    async def start(self) -> None:
        cfg.ensure_dirs()
        logger.info("[agent] starting backend: %s", cfg.agent_backend)
        await self._backend.start()

    async def stop(self) -> None:
        await self._safe_destroy_session()
        await self._backend.stop()

    async def new_session(self) -> Any:
        logger.info("[agent.new_session] destroying old session ...")
        await self._safe_destroy_session()
        session_cfg = self._build_session_config()
        logger.info(
            "[agent.new_session] creating session: model=%s, tools=%d, mcp_servers=%s",
            session_cfg.get("model"),
            len(session_cfg.get("tools", [])),
            list(session_cfg.get("mcp_servers", {}).keys()) if isinstance(session_cfg.get("mcp_servers"), dict) else "N/A",
        )
        self._session = await self._backend.create_session(session_cfg)
        logger.info("[agent.new_session] session created: %s", type(self._session).__name__)
        return self._session

    async def send(
        self,
        prompt: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> str | None:
        logger.info("[agent.send] prompt=%r (len=%d), has_session=%s", prompt[:80], len(prompt), self._session is not None)
        if not self._session:
            logger.info("[agent.send] no session -- creating one")
            await self.new_session()

        model = cfg.copilot_model
        self.request_counts[model] = self.request_counts.get(model, 0) + 1

        try:
            result = await self._backend.send(self._session, prompt, on_delta, on_event)
            logger.info("[agent.send] response complete, text_len=%d", len(result or ""))
            return result
        except Exception as exc:
            if "Session not found" in str(exc):
                logger.info("[agent.send] session expired, creating new session...")
                await self.new_session()
                return await self._backend.send(self._session, prompt, on_delta, on_event)
            raise

    async def list_models(self) -> list[dict]:
        return await self._backend.list_models()

    def _build_session_config(self) -> dict[str, Any]:
        sandbox_active = self._interceptor and self._sandbox and self._sandbox.enabled
        if sandbox_active:
            hooks: dict[str, Any] = {
                "on_pre_tool_use": self._interceptor.on_pre_tool_use,
                "on_post_tool_use": self._interceptor.on_post_tool_use,
            }
        else:
            hooks = {"on_pre_tool_use": auto_approve}

        session_cfg: dict[str, Any] = {
            "model": cfg.copilot_model,
            "streaming": True,
            "tools": get_all_tools(),
            "system_message": {"mode": "replace", "content": build_system_prompt()},
            "hooks": hooks,
            "skill_directories": [str(cfg.builtin_skills_dir), str(cfg.user_skills_dir)],
        }

        if sandbox_active:
            session_cfg["excluded_tools"] = ["create", "view", "edit", "grep", "glob"]

        try:
            session_cfg["mcp_servers"] = McpConfigStore().get_enabled_servers()
        except Exception:
            logger.warning("Failed to load MCP config, using defaults", exc_info=True)
            session_cfg["mcp_servers"] = {
                "playwright": {
                    "type": "local",
                    "command": "npx",
                    "args": ["-y", "@playwright/mcp@latest", "--browser", "chromium", "--headless", "--isolated"],
                    "env": {"PLAYWRIGHT_CHROMIUM_ARGS": "--no-sandbox --disable-setuid-sandbox"},
                    "tools": ["*"],
                },
            }
        return session_cfg

    async def _safe_destroy_session(self) -> None:
        if self._session:
            try:
                await self._backend.destroy_session(self._session)
            except Exception:
                logger.debug("Error destroying session", exc_info=True)
            self._session = None
