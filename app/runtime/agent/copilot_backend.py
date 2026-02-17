"""Copilot backend -- wraps GitHub Copilot SDK (CopilotClient subprocess).

Full-featured: supports MCP servers, skills, sandbox interception, and
all Copilot SDK event types. This is the default backend.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from copilot import CopilotClient
from copilot.generated.session_events import SessionEventType

from ..config.settings import cfg
from .backend import AgentBackend
from .event_handler import EventHandler

logger = logging.getLogger(__name__)

MAX_START_RETRIES = 3
RESPONSE_TIMEOUT = 120.0
RETRY_DELAY = 2


class CopilotBackend(AgentBackend):
    """GitHub Copilot SDK backend (subprocess-based)."""

    def __init__(self) -> None:
        self._client: CopilotClient | None = None

    async def start(self) -> None:
        opts: dict[str, Any] = {"log_level": "error"}
        if cfg.github_token:
            opts["github_token"] = cfg.github_token

        for attempt in range(1, MAX_START_RETRIES + 1):
            try:
                logger.info("[copilot] start attempt %d/%d", attempt, MAX_START_RETRIES)
                self._client = CopilotClient(opts)
                await self._client.start()
                logger.info("[copilot] started successfully")
                return
            except TimeoutError as exc:
                if attempt < MAX_START_RETRIES:
                    logger.warning("[copilot] startup timed out (attempt %d/%d)", attempt, MAX_START_RETRIES)
                    await self._safe_stop()
                    await asyncio.sleep(RETRY_DELAY)
                else:
                    raise RuntimeError(
                        f"Could not connect to Copilot CLI after {MAX_START_RETRIES} attempts."
                    ) from exc

    async def stop(self) -> None:
        await self._safe_stop()

    async def create_session(self, config: dict[str, Any]) -> Any:
        if not self._client:
            raise RuntimeError("CopilotBackend not started")
        return await self._client.create_session(config)

    async def send(
        self,
        session: Any,
        prompt: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> str | None:
        handler = EventHandler(on_delta, on_event)
        unsub = session.on(handler)
        try:
            try:
                await session.send({"prompt": prompt})
            except Exception as exc:
                if "Session not found" in str(exc):
                    unsub()
                    raise
                raise
            try:
                await asyncio.wait_for(handler.done.wait(), timeout=RESPONSE_TIMEOUT)
            except TimeoutError:
                logger.warning("[copilot] response timed out after %ss", RESPONSE_TIMEOUT)
                return handler.final_text
        finally:
            unsub()

        if handler.error:
            logger.error("[copilot] session error: %s", handler.error)
            return None
        return handler.final_text

    async def destroy_session(self, session: Any) -> None:
        try:
            await session.destroy()
        except Exception:
            logger.debug("[copilot] error destroying session", exc_info=True)

    async def list_models(self) -> list[dict]:
        if not self._client:
            raise RuntimeError("CopilotBackend not started")
        try:
            models = await self._client.list_models()
            return [
                {
                    "id": m.id,
                    "name": m.name,
                    "policy": m.policy.state if m.policy else "enabled",
                    "billing_multiplier": m.billing.multiplier if m.billing else 1.0,
                    "reasoning_efforts": m.supported_reasoning_efforts,
                }
                for m in models
            ]
        except Exception as exc:
            logger.warning("[copilot] failed to list models: %s", exc)
            return []

    async def run_one_shot(
        self,
        prompt: str,
        *,
        model: str = "",
        system_message: str = "",
        timeout: float = 300,
        tools: list[Any] | None = None,
    ) -> str | None:
        from .one_shot import auto_approve

        opts: dict[str, Any] = {"log_level": "error"}
        if cfg.github_token:
            opts["github_token"] = cfg.github_token

        client = CopilotClient(opts)
        await client.start()
        try:
            session_cfg: dict[str, Any] = {
                "model": model or "gpt-4.1",
                "hooks": {"on_pre_tool_use": auto_approve},
            }
            if system_message:
                session_cfg["system_message"] = {"mode": "append", "content": system_message}
            if tools:
                session_cfg["tools"] = tools
            session = await client.create_session(session_cfg)
            return await self._one_shot_send(session, prompt, timeout)
        finally:
            try:
                await client.stop()
            except Exception:
                logger.debug("[copilot] error stopping one-shot client", exc_info=True)

    async def _one_shot_send(self, session: Any, prompt: str, timeout: float) -> str | None:
        final_text: str | None = None
        done = asyncio.Event()

        def on_event(event: Any) -> None:
            nonlocal final_text
            if event.type == SessionEventType.ASSISTANT_MESSAGE:
                final_text = event.data.content
            elif event.type in (SessionEventType.SESSION_IDLE, SessionEventType.SESSION_ERROR):
                done.set()

        session.on(on_event)
        await session.send({"prompt": prompt})
        await asyncio.wait_for(done.wait(), timeout=timeout)

        try:
            await session.destroy()
        except Exception:
            pass
        return final_text

    async def _safe_stop(self) -> None:
        if self._client:
            try:
                await self._client.stop()
            except Exception:
                logger.debug("[copilot] error stopping client", exc_info=True)
            self._client = None
