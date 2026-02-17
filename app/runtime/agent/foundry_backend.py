"""Foundry backend -- Azure OpenAI direct API calls via the openai SDK.

Alternative to CopilotBackend. Supports streaming, tool calling,
conversation history, reasoning tokens, and MCP server integration.
Uses DefaultAzureCredential for MI auth or API key.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from ..config.settings import cfg
from .backend import AgentBackend
from .mcp_client import McpClientManager

logger = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 120.0


def _get_openai_client() -> Any:
    """Create an AsyncAzureOpenAI client with MI or API key auth."""
    from openai import AsyncAzureOpenAI

    endpoint = cfg.azure_openai_endpoint
    if not endpoint:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT must be set for the foundry backend"
        )

    api_key = cfg.azure_openai_api_key
    if api_key:
        return AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version="2024-12-01-preview",
        )

    # MI auth via azure-identity
    from azure.identity.aio import DefaultAzureCredential, get_bearer_token_provider

    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    return AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version="2024-12-01-preview",
    )


def _tools_to_openai_format(tools: list[Any]) -> list[dict]:
    """Convert @define_tool decorated functions to OpenAI function calling format."""
    openai_tools: list[dict] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", "unknown")
        description = getattr(tool, "description", "") or ""
        schema = getattr(tool, "parameters_schema", None)

        if schema is None:
            # Try Pydantic model from explicit attribute or type annotations
            params_model = getattr(tool, "params_model", None)
            if params_model is None:
                # Inspect the original function's type hints
                fn = getattr(tool, "__wrapped__", tool)
                hints = getattr(fn, "__annotations__", {})
                for param_name, param_type in hints.items():
                    if param_name != "return" and hasattr(param_type, "model_json_schema"):
                        params_model = param_type
                        break
            if params_model and hasattr(params_model, "model_json_schema"):
                schema = params_model.model_json_schema()
            else:
                schema = {"type": "object", "properties": {}}

        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": schema,
            },
        })
    return openai_tools


class FoundrySession:
    """Conversation session backed by message history, with MCP support."""

    def __init__(
        self,
        model: str,
        system_message: str,
        tools: list[Any],
        mcp_manager: McpClientManager | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.mcp_manager = mcp_manager
        self._tool_map: dict[str, Any] = {}
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", "unknown")
            self._tool_map[name] = t
        self.messages: list[dict[str, Any]] = []
        if system_message:
            self.messages.append({"role": "system", "content": system_message})

        # Combine @define_tool tools + MCP tools
        self.openai_tools = _tools_to_openai_format(tools) if tools else []
        if mcp_manager:
            self.openai_tools.extend(mcp_manager.openai_tools)

    async def call_tool(self, name: str, arguments: str) -> str:
        """Execute a tool by name with JSON arguments string.

        Routes to MCP servers first, then falls back to @define_tool handlers
        and plain callables.
        """
        # MCP tools take priority (they are session-scoped server connections)
        if self.mcp_manager and self.mcp_manager.has_tool(name):
            return await self.mcp_manager.call_tool(name, arguments)

        tool_fn = self._tool_map.get(name)
        if not tool_fn:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            args = json.loads(arguments) if arguments else {}

            # @define_tool SDK tools expose an async .handler(invocation)
            handler = getattr(tool_fn, "handler", None)
            if handler and asyncio.iscoroutinefunction(handler):
                invocation = {
                    "session_id": "foundry",
                    "tool_call_id": f"tc_{name}",
                    "tool_name": name,
                    "arguments": args,
                }
                raw = await handler(invocation)
                return raw.get("textResultForLlm", json.dumps(raw))

            # Fallback: plain callable with Pydantic params
            params_model = getattr(tool_fn, "params_model", None)
            if params_model:
                params = params_model(**args)
                result = tool_fn(params)
            else:
                result = tool_fn(**args) if args else tool_fn()

            if asyncio.iscoroutine(result):
                result = await result

            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as exc:
            logger.error("[foundry] tool %s failed: %s", name, exc)
            return json.dumps({"error": str(exc)})


class FoundryBackend(AgentBackend):
    """Azure OpenAI direct API backend."""

    def __init__(self) -> None:
        self._client: Any = None
        self._deployments_cache: list[dict] | None = None

    async def start(self) -> None:
        self._client = _get_openai_client()
        logger.info("[foundry] Azure OpenAI client initialized (endpoint=%s)", cfg.azure_openai_endpoint)

    async def stop(self) -> None:
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    async def create_session(self, config: dict[str, Any]) -> FoundrySession:
        model = config.get("model", cfg.copilot_model)
        system_msg = config.get("system_message", {})
        system_content = system_msg.get("content", "") if isinstance(system_msg, dict) else ""
        tools = config.get("tools", [])

        # Start MCP servers if configured
        mcp_manager: McpClientManager | None = None
        mcp_servers = config.get("mcp_servers")
        if mcp_servers:
            mcp_manager = McpClientManager()
            try:
                await mcp_manager.start(mcp_servers)
                logger.info("[foundry] MCP: %d tools from %d servers",
                            len(mcp_manager.tool_names),
                            len(mcp_manager._group.sessions) if mcp_manager._group else 0)
            except Exception as exc:
                logger.warning("[foundry] MCP startup failed: %s", exc)
                mcp_manager = None

        return FoundrySession(model, system_content, tools, mcp_manager)

    async def send(
        self,
        session: Any,
        prompt: str,
        on_delta: Callable[[str], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> str | None:
        if not self._client:
            raise RuntimeError("FoundryBackend not started")

        sess: FoundrySession = session
        sess.messages.append({"role": "user", "content": prompt})

        # Tool call loop: keep calling the model until it stops requesting tools
        max_tool_rounds = 10
        for _ in range(max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": sess.model,
                "messages": sess.messages,
                "stream": True,
            }
            if sess.openai_tools:
                kwargs["tools"] = sess.openai_tools

            full_text = ""
            tool_calls: list[dict[str, Any]] = []

            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Reasoning/thinking tokens (Azure OpenAI extra field)
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning and on_event:
                    on_event("reasoning", {"text": reasoning})

                # Text content
                if delta.content:
                    full_text += delta.content
                    if on_delta:
                        on_delta(delta.content)

                # Tool calls (streamed incrementally)
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        while len(tool_calls) <= tc.index:
                            tool_calls.append({"id": "", "name": "", "arguments": ""})
                        entry = tool_calls[tc.index]
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                entry["name"] = tc.function.name
                            if tc.function.arguments:
                                entry["arguments"] += tc.function.arguments

            if not tool_calls:
                # No tools requested — we're done
                sess.messages.append({"role": "assistant", "content": full_text})
                return full_text

            # Execute tool calls
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": full_text or None, "tool_calls": []}
            for tc in tool_calls:
                assistant_msg["tool_calls"].append({
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                })
            sess.messages.append(assistant_msg)

            for tc in tool_calls:
                if on_event:
                    on_event("tool_start", {"tool": tc["name"], "call_id": tc["id"]})
                result = await sess.call_tool(tc["name"], tc["arguments"])
                if on_event:
                    on_event("tool_done", {"tool": tc["name"], "call_id": tc["id"], "result": result[:500]})
                sess.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })

        logger.warning("[foundry] max tool rounds (%d) exceeded", max_tool_rounds)
        return full_text

    async def destroy_session(self, session: Any) -> None:
        sess: FoundrySession = session
        if sess.mcp_manager:
            await sess.mcp_manager.stop()

    async def list_models(self) -> list[dict]:
        """List deployed models via the Azure Resource Manager API.

        Only returns models that are actually deployed and ready.
        Results are cached after the first call.
        """
        if self._deployments_cache is not None:
            return self._deployments_cache

        if not cfg.azure_openai_endpoint or not cfg.azure_subscription_id:
            return []
        try:
            import httpx
            from azure.identity.aio import DefaultAzureCredential

            account = cfg.azure_openai_endpoint.split("//")[1].split(".")[0]
            sub_id = cfg.azure_subscription_id

            cred = DefaultAzureCredential()
            token = await cred.get_token("https://management.azure.com/.default")
            await cred.close()
            headers = {"Authorization": f"Bearer {token.token}"}

            # Find the Cognitive Services account to get its resource group
            find_url = (
                f"https://management.azure.com/subscriptions/{sub_id}"
                f"/resources?$filter=resourceType eq 'Microsoft.CognitiveServices/accounts'"
                f"&api-version=2021-04-01"
            )
            async with httpx.AsyncClient() as http:
                resp = await http.get(find_url, headers=headers, timeout=15)
                resp.raise_for_status()
                resources = resp.json().get("value", [])

            # Match by account name in the resource ID
            resource_id = None
            for r in resources:
                if r.get("name", "").lower() == account.lower():
                    resource_id = r["id"]
                    break

            if not resource_id:
                logger.warning("[foundry] could not find resource for %s", account)
                return []

            # List deployments
            deploy_url = (
                f"https://management.azure.com{resource_id}"
                f"/deployments?api-version=2024-10-01"
            )
            async with httpx.AsyncClient() as http:
                resp = await http.get(deploy_url, headers=headers, timeout=15)
                resp.raise_for_status()
                deployments = resp.json().get("value", [])

            self._deployments_cache = [
                {
                    "id": d["name"],
                    "name": f"{d['name']} ({d['properties']['model']['name']})",
                    "policy": "enabled",
                }
                for d in deployments
                if d.get("properties", {}).get("provisioningState") == "Succeeded"
            ]
            return self._deployments_cache
        except Exception as exc:
            logger.warning("[foundry] failed to list deployments: %s", exc)
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
        if not self._client:
            self._client = _get_openai_client()
        messages: list[dict[str, Any]] = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model or cfg.copilot_model,
                    messages=messages,
                ),
                timeout=timeout,
            )
            return resp.choices[0].message.content if resp.choices else None
        except Exception as exc:
            logger.error("[foundry] one-shot failed: %s", exc)
            return None
