"""One-shot session runner.

Spawns an ephemeral session to execute a single prompt. Used by the
scheduler and memory formation to avoid re-using the interactive session.

Delegates to the active AgentBackend (copilot or foundry) via
:func:`_get_backend`.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config.settings import cfg

logger = logging.getLogger(__name__)


async def auto_approve(input_data: dict, invocation: dict) -> dict:
    return {"permissionDecision": "allow"}


def _get_backend():
    """Return a fresh backend instance based on config."""
    backend_name = cfg.agent_backend
    if backend_name == "foundry":
        from .foundry_backend import FoundryBackend
        return FoundryBackend()
    from .copilot_backend import CopilotBackend
    return CopilotBackend()


async def run_one_shot(
    prompt: str,
    *,
    model: str = "gpt-4.1",
    system_message: str = "",
    timeout: float = 300,
    tools: list[Any] | None = None,
) -> str | None:
    backend = _get_backend()
    await backend.start()
    try:
        return await backend.run_one_shot(
            prompt,
            model=model,
            system_message=system_message,
            timeout=timeout,
            tools=tools,
        )
    finally:
        try:
            await backend.stop()
        except Exception:
            logger.debug("Error stopping one-shot backend", exc_info=True)
