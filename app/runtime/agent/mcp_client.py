"""MCP client manager for the Foundry backend.

Uses the `mcp` Python SDK's :class:ClientSessionGroup to connect to
local (stdio) and remote (SSE/HTTP) MCP servers, discover their tools,
and execute tool calls.  Tool definitions are converted to OpenAI
function-calling format so they can be passed directly to Azure OpenAI.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _mcp_tool_to_openai(tool: Any) -> dict:
    """Convert a single MCP Tool object to OpenAI function calling format."""
    schema = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": schema,
        },
    }


class McpClientManager:
    """Manages connections to multiple MCP servers for a single session.

    Usage::

        mgr = McpClientManager()
        await mgr.start(servers_config)
        openai_tools = mgr.openai_tools      # ready for tools= kwarg
        result = await mgr.call_tool("name", {"arg": "val"})
        await mgr.stop()
    """

    def __init__(self) -> None:
        self._group: Any | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._openai_tools: list[dict] = []
        self._tool_names: set[str] = set()

    @property
    def openai_tools(self) -> list[dict]:
        return list(self._openai_tools)

    @property
    def tool_names(self) -> set[str]:
        return set(self._tool_names)

    def has_tool(self, name: str) -> bool:
        return name in self._tool_names

    async def start(self, servers_config: dict[str, dict[str, Any]]) -> None:
        """Connect to all configured MCP servers and discover their tools.

        *servers_config* uses the OctoClaw format produced by
        :meth:McpConfigStore.get_enabled_servers::

            {"server-name": {"type": "local", "command": "npx", "args": [...], "env": {...}}}
            {"server-name": {"type": "http", "url": "https://..."}}
        """
        if not servers_config:
            return

        from mcp import ClientSessionGroup, StdioServerParameters
        from mcp.client.session_group import SseServerParameters, StreamableHttpParameters

        self._exit_stack = contextlib.AsyncExitStack()
        await self._exit_stack.__aenter__()

        self._group = await self._exit_stack.enter_async_context(ClientSessionGroup())

        for name, config in servers_config.items():
            server_type = config.get("type", "local")
            try:
                if server_type in ("local", "stdio"):
                    params = StdioServerParameters(
                        command=config["command"],
                        args=config.get("args", []),
                        env=config.get("env"),
                    )
                elif server_type == "sse":
                    params = SseServerParameters(
                        url=config["url"],
                        headers=config.get("headers"),
                    )
                elif server_type == "http":
                    # Modern Streamable HTTP transport (e.g. learn.microsoft.com/api/mcp)
                    params = StreamableHttpParameters(
                        url=config["url"],
                        headers=config.get("headers"),
                    )
                else:
                    logger.warning("[mcp] unsupported server type %r for %s", server_type, name)
                    continue

                await self._group.connect_to_server(params)
                logger.info("[mcp] connected to %s (%s)", name, server_type)
            except Exception as exc:
                logger.warning("[mcp] failed to connect to %s: %s", name, exc)

        # Convert discovered tools to OpenAI format
        for tool_name, tool in self._group.tools.items():
            self._openai_tools.append(_mcp_tool_to_openai(tool))
            self._tool_names.add(tool_name)

        logger.info("[mcp] discovered %d tools from %d servers",
                     len(self._tool_names), len(self._group.sessions))

    async def call_tool(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Execute a tool via the MCP protocol."""
        if not self._group or name not in self._tool_names:
            return json.dumps({"error": f"MCP tool not found: {name}"})

        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments else {}

        try:
            result = await self._group.call_tool(name, arguments)
            # CallToolResult has .content (list of TextContent/ImageContent/etc.)
            parts = []
            for item in result.content:
                if hasattr(item, "text"):
                    parts.append(item.text)
                elif hasattr(item, "data"):
                    parts.append(f"[binary data: {getattr(item, 'mimeType', 'unknown')}]")
                else:
                    parts.append(str(item))
            return "\n".join(parts) if parts else "Tool returned no output."
        except Exception as exc:
            logger.error("[mcp] tool %s failed: %s", name, exc)
            return json.dumps({"error": str(exc)})

    async def stop(self) -> None:
        """Disconnect from all MCP servers and clean up."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.warning("[mcp] cleanup error: %s", exc)
            self._exit_stack = None
        self._group = None
        self._openai_tools.clear()
        self._tool_names.clear()
