"""Tests for the Agent class."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.runtime.agent.agent import Agent


class TestAgentInit:
    @patch("app.runtime.agent.agent._create_backend")
    def test_defaults(self, mock_create):
        mock_create.return_value = MagicMock()
        a = Agent()
        assert a._session is None
        assert a.request_counts == {}
        assert not a.has_session

    @patch("app.runtime.agent.agent._create_backend")
    def test_set_sandbox(self, mock_create):
        mock_create.return_value = MagicMock()
        a = Agent()
        mock_executor = MagicMock()
        mock_executor.enabled = True
        a.set_sandbox(mock_executor)
        assert a._sandbox is mock_executor
        assert a._interceptor is not None


class TestAgentLifecycle:
    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent._create_backend")
    async def test_start_success(self, mock_create):
        backend = AsyncMock()
        mock_create.return_value = backend
        a = Agent()
        await a.start()
        backend.start.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent._create_backend")
    async def test_stop(self, mock_create):
        backend = AsyncMock()
        mock_create.return_value = backend
        a = Agent()
        await a.start()
        session = AsyncMock()
        a._session = session
        await a.stop()
        backend.destroy_session.assert_awaited_once_with(session)
        backend.stop.assert_awaited_once()
        assert a._session is None

    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent._create_backend")
    async def test_stop_handles_errors(self, mock_create):
        backend = AsyncMock()
        backend.stop.side_effect = RuntimeError("oops")
        backend.destroy_session.side_effect = RuntimeError("destroy error")
        mock_create.return_value = backend
        a = Agent()
        await a.start()
        a._session = AsyncMock()
        await a.stop()
        assert a._session is None


class TestAgentSession:
    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent.build_system_prompt", return_value="system prompt")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent._create_backend")
    async def test_new_session(self, mock_create, mock_tools, mock_prompt):
        backend = AsyncMock()
        session = AsyncMock()
        backend.create_session.return_value = session
        mock_create.return_value = backend

        a = Agent()
        await a.start()
        result = await a.new_session()
        assert result is session
        assert a.has_session
        backend.create_session.assert_awaited_once()


class TestAgentSend:
    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent.build_system_prompt", return_value="prompt")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent._create_backend")
    async def test_send_creates_session_if_needed(self, mock_create, mock_tools, mock_prompt):
        backend = AsyncMock()
        session = AsyncMock()
        backend.create_session.return_value = session
        backend.send.return_value = "reply"
        mock_create.return_value = backend

        a = Agent()
        await a.start()
        result = await a.send("hello")
        assert result == "reply"
        backend.create_session.assert_awaited_once()
        backend.send.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent.build_system_prompt", return_value="prompt")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent._create_backend")
    async def test_send_session_expired_retry(self, mock_create, mock_tools, mock_prompt):
        backend = AsyncMock()
        session = AsyncMock()
        backend.create_session.return_value = session
        backend.send.side_effect = [RuntimeError("Session not found"), "retry reply"]
        mock_create.return_value = backend

        a = Agent()
        await a.start()
        result = await a.send("hello")
        assert result == "retry reply"
        assert backend.create_session.await_count == 2  # initial + retry


class TestAgentListModels:
    @pytest.mark.asyncio
    @patch("app.runtime.agent.agent._create_backend")
    async def test_list_models(self, mock_create):
        backend = AsyncMock()
        backend.list_models.return_value = [{"id": "gpt-4.1", "name": "GPT-4.1"}]
        mock_create.return_value = backend

        a = Agent()
        await a.start()
        models = await a.list_models()
        assert len(models) == 1
        assert models[0]["id"] == "gpt-4.1"


class TestBuildSessionConfig:
    @patch("app.runtime.agent.agent.build_system_prompt", return_value="sp")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent.McpConfigStore")
    @patch("app.runtime.agent.agent._create_backend")
    def test_basic_config(self, mock_create, MockMcp, mock_tools, mock_prompt):
        mock_create.return_value = MagicMock()
        MockMcp.return_value.get_enabled_servers.return_value = {}
        a = Agent()
        config = a._build_session_config()
        assert config["model"] is not None
        assert config["streaming"] is True
        assert "system_message" in config
        assert "hooks" in config

    @patch("app.runtime.agent.agent.build_system_prompt", return_value="sp")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent.McpConfigStore")
    @patch("app.runtime.agent.agent._create_backend")
    def test_config_with_sandbox(self, mock_create, MockMcp, mock_tools, mock_prompt):
        mock_create.return_value = MagicMock()
        MockMcp.return_value.get_enabled_servers.return_value = {}
        a = Agent()
        executor = MagicMock()
        executor.enabled = True
        a.set_sandbox(executor)
        config = a._build_session_config()
        assert "excluded_tools" in config

    @patch("app.runtime.agent.agent.build_system_prompt", return_value="sp")
    @patch("app.runtime.agent.agent.get_all_tools", return_value=[])
    @patch("app.runtime.agent.agent.McpConfigStore")
    @patch("app.runtime.agent.agent._create_backend")
    def test_mcp_fallback_on_error(self, mock_create, MockMcp, mock_tools, mock_prompt):
        mock_create.return_value = MagicMock()
        MockMcp.return_value.get_enabled_servers.side_effect = RuntimeError("fail")
        a = Agent()
        config = a._build_session_config()
        assert "playwright" in config["mcp_servers"]
