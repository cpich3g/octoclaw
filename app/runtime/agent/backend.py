"""Agent backend protocol -- defines the interface for AI provider backends.

Two implementations:
- CopilotBackend: GitHub Copilot SDK (subprocess, full MCP/skills support)
- FoundryBackend: Azure OpenAI direct API (no subprocess, no MCP/skills)
"""

from __future__ import annotations

import abc
from collections.abc import Callable
from typing import Any


class AgentBackend(abc.ABC):
    """Abstract interface for an AI agent backend."""

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialize the backend (connect, authenticate, etc.)."""

    @abc.abstractmethod
    async def stop(self) -> None:
        """Shut down the backend cleanly."""

    @abc.abstractmethod
    async def create_session(self, config: dict[str, Any]) -> Any:
        """Create a new conversation session with the given config."""

    @abc.abstractmethod
    async def send(
        self,
        session: Any,
        prompt: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> str | None:
        """Send a prompt and return the final response text."""

    @abc.abstractmethod
    async def destroy_session(self, session: Any) -> None:
        """Destroy a conversation session."""

    @abc.abstractmethod
    async def list_models(self) -> list[dict]:
        """Return available models as list of dicts with at least 'id' and 'name'."""

    @abc.abstractmethod
    async def run_one_shot(
        self,
        prompt: str,
        *,
        model: str = "",
        system_message: str = "",
        timeout: float = 300,
        tools: list[Any] | None = None,
    ) -> str | None:
        """Run a single prompt in an ephemeral session (for scheduler/memory)."""
