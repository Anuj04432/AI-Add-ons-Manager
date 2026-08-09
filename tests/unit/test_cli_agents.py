"""Unit tests for `aiaddons agents` CLI command."""

import json

import pytest
from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult

runner = CliRunner()


def test_cli_agents_rich_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test `aiaddons agents` formatted human-readable output."""
    mock_results = {
        "claude-code": AgentDetectionResult(
            agent_id="claude-code",
            name="Claude Code",
            installed=True,
            version="0.2.1",
            executable_path="/usr/local/bin/claude",
            config_path="/home/user/.claude.json",
            capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        ),
        "codex": AgentDetectionResult(
            agent_id="codex",
            name="OpenAI Codex",
            installed=False,
            capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        ),
    }

    monkeypatch.setattr(
        "aiaddons.cli.commands.agents.detect_agents",
        lambda project_path=None: mock_results,
    )

    result = runner.invoke(app, ["agents"])
    assert result.exit_code == 0
    assert "AI Coding Agents" in result.stdout
    assert "Claude Code" in result.stdout
    assert "0.2.1" in result.stdout
    assert "/usr/local/bin/claude" in result.stdout
    assert "OpenAI Codex" in result.stdout
    assert "Not installed" in result.stdout


def test_cli_agents_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test `aiaddons agents --json` output."""
    mock_results = {
        "claude-code": AgentDetectionResult(
            agent_id="claude-code",
            name="Claude Code",
            installed=True,
            version="0.2.1",
            executable_path="/usr/local/bin/claude",
            capabilities=[AgentCapability.MCP],
        )
    }

    monkeypatch.setattr(
        "aiaddons.cli.commands.agents.detect_agents",
        lambda project_path=None: mock_results,
    )

    result = runner.invoke(app, ["agents", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert "claude-code" in parsed
    assert parsed["claude-code"]["installed"] is True
    assert parsed["claude-code"]["version"] == "0.2.1"
    assert parsed["claude-code"]["executable_path"] == "/usr/local/bin/claude"
