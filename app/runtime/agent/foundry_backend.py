"""Foundry backend -- Azure OpenAI Responses API via the openai SDK.

Alternative to CopilotBackend. Uses the Responses API for streaming,
native reasoning summaries, function tool calling, and MCP server
integration (via client-side McpClientManager for stdio/SSE servers).
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
API_VERSION = "2025-03-01-preview"


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
            api_version=API_VERSION,
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
        api_version=API_VERSION,
    )


def _tools_to_responses_format(tools: list[Any]) -> list[dict]:
    """Convert @define_tool functions to Responses API FunctionToolParam format.

    Responses API uses a flat structure: {"type": "function", "name": ...,
    "description": ..., "parameters": ...} -- NOT the nested Chat Completions
    format {"type": "function", "function": {"name": ...}}.
    """
    result: list[dict] = []
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", "unknown")
        description = getattr(tool, "description", "") or ""
        schema = getattr(tool, "parameters_schema", None)

        if schema is None:
            params_model = getattr(tool, "params_model", None)
            if params_model is None:
                fn = getattr(tool, "__wrapped__", tool)
                hints = getattr(fn, "__annotations__", {})
                for _, param_type in hints.items():
                    if hasattr(param_type, "model_json_schema"):
                        params_model = param_type
                        break
            if params_model and hasattr(params_model, "model_json_schema"):
                schema = params_model.model_json_schema()
            else:
                schema = {"type": "object", "properties": {}}

        result.append({
            "type": "function",
            "name": name,
            "description": description,
            "parameters": schema,
            "strict": False,
        })
    return result


def _mcp_tools_to_responses_format(mcp_tools: list[dict]) -> list[dict]:
    """Convert McpClientManager's OpenAI-format tools to Responses API format.

    MCP client returns Chat Completions format (nested). We flatten to
    Responses API format.
    """
    result: list[dict] = []
    for t in mcp_tools:
        fn = t.get("function", {})
        result.append({
            "type": "function",
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            "strict": False,
        })
    return result


class FoundrySession:
    """Conversation session using Responses API with previous_response_id chaining."""

    def __init__(
        self,
        model: str,
        system_message: str,
        tools: list[Any],
        mcp_manager: McpClientManager | None = None,
    ) -> None:
        self.model = model
        self.instructions = system_message
        self.mcp_manager = mcp_manager
        self.previous_response_id: str | None = None

        # Build tool map for local execution
        self._tool_map: dict[str, Any] = {}
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", "unknown")
            self._tool_map[name] = t

        # Build Responses API tool definitions
        self.response_tools = _tools_to_responses_format(tools) if tools else []
        if mcp_manager:
            self.response_tools.extend(
                _mcp_tools_to_responses_format(mcp_manager.openai_tools)
            )

    async def call_tool(self, name: str, arguments: str) -> str:
        """Execute a tool by name. Routes MCP first, then local tools."""
        if self.mcp_manager and self.mcp_manager.has_tool(name):
            return await self.mcp_manager.call_tool(name, arguments)

        tool_fn = self._tool_map.get(name)
        if not tool_fn:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            args = json.loads(arguments) if arguments else {}

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
    """Azure OpenAI backend using the Responses API."""

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

        input_items: list[dict[str, Any]] = [
            {"role": "user", "content": prompt},
        ]

        max_tool_rounds = 10
        full_text = ""

        for round_num in range(max_tool_rounds):
            kwargs: dict[str, Any] = {
                "model": sess.model,
                "input": input_items,
                "stream": True,
                "reasoning": {"summary": "auto"},
            }
            if sess.instructions and round_num == 0:
                kwargs["instructions"] = sess.instructions
            if sess.response_tools:
                kwargs["tools"] = sess.response_tools
            if sess.previous_response_id and round_num == 0:
                kwargs["previous_response_id"] = sess.previous_response_id

            full_text = ""
            pending_tool_calls: list[dict[str, str]] = []

            stream = await self._client.responses.create(**kwargs)
            async for event in stream:
                etype = event.type

                if etype == "response.output_text.delta":
                    full_text += event.delta
                    if on_delta:
                        on_delta(event.delta)

                elif etype == "response.reasoning_summary_text.delta":
                    if on_event:
                        on_event("reasoning", {"text": event.delta})

                elif etype == "response.output_item.done":
                    item = event.item
                    if getattr(item, "type", None) == "function_call":
                        pending_tool_calls.append({
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": item.arguments,
                        })

                elif etype == "response.completed":
                    sess.previous_response_id = event.response.id

            if not pending_tool_calls:
                return full_text

            # Execute function tools and build input for next round
            input_items = []
            for tc in pending_tool_calls:
                if on_event:
                    on_event("tool_start", {"tool": tc["name"], "call_id": tc["call_id"]})

                result = await sess.call_tool(tc["name"], tc["arguments"])

                if on_event:
                    on_event("tool_done", {
                        "tool": tc["name"],
                        "call_id": tc["call_id"],
                        "result": result[:500],
                    })

                input_items.append({
                    "type": "function_call",
                    "call_id": tc["call_id"],
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                })
                input_items.append({
                    "type": "function_call_output",
                    "call_id": tc["call_id"],
                    "output": result,
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

            find_url = (
                f"https://management.azure.com/subscriptions/{sub_id}"
                f"/resources?$filter=resourceType eq 'Microsoft.CognitiveServices/accounts'"
                f"&api-version=2021-04-01"
            )
            async with httpx.AsyncClient() as http:
                resp = await http.get(find_url, headers=headers, timeout=15)
                resp.raise_for_status()
                resources = resp.json().get("value", [])

            resource_id = None
            for r in resources:
                if r.get("name", "").lower() == account.lower():
                    resource_id = r["id"]
                    break

            if not resource_id:
                logger.warning("[foundry] could not find resource for %s", account)
                return []

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
        try:
            kwargs: dict[str, Any] = {
                "model": model or cfg.copilot_model,
                "input": prompt,
            }
            if system_message:
                kwargs["instructions"] = system_message
            resp = await asyncio.wait_for(
                self._client.responses.create(**kwargs),
                timeout=timeout,
            )
            # Extract text from output items
            for item in resp.output:
                if getattr(item, "type", None) == "message":
                    for part in item.content:
                        if getattr(part, "type", None) == "output_text":
                            return part.text
            return None
        except Exception as exc:
            logger.error("[foundry] one-shot failed: %s", exc)
            return None
