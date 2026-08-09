"""Unit tests for registry CLI check command (aiaddons check <addon-id>)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiaddons.cli.main import app

runner = CliRunner()


@pytest.fixture
def sample_registry_dir(tmp_path: Path) -> Path:
    """Fixture creating a temporary directory containing sample registry manifest files."""
    reg_dir = tmp_path / "registry" / "addons"
    reg_dir.mkdir(parents=True, exist_ok=True)

    (reg_dir / "github-mcp.yaml").write_text(
        """
id: github-mcp
name: GitHub MCP Server
version: 1.2.0
description: MCP server for GitHub integration.
license: MIT
category: developer-tools
integration_type: mcp
target_agents:
  - claude-code
  - codex
supported_scopes:
  - global
  - workspace
source:
  source_type: package
  package_name: "@modelcontextprotocol/server-github"
  checksum: "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
trust:
  verification_status: verified
  publisher:
    name: MCP Team
    declared_verified: true
tags:
  - github
  - mcp
handler_spec:
  mcp:
    runtime: npx
    package_name: "@modelcontextprotocol/server-github"
""",
        encoding="utf-8",
    )

    return reg_dir


def test_cli_check_command(sample_registry_dir: Path) -> None:
    """Test `aiaddons check github-mcp` execution."""
    result = runner.invoke(
        app, ["check", "github-mcp", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 0
    assert "Compatibility Evaluation for 'GitHub MCP Server'" in result.stdout
    assert "Requested Scope: workspace" in result.stdout


def test_cli_check_command_json(sample_registry_dir: Path) -> None:
    """Test `aiaddons check github-mcp --json` execution."""
    result = runner.invoke(
        app, ["check", "github-mcp", "--json", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) >= 1
    assert parsed[0]["addon_id"] == "github-mcp"


def test_cli_check_nonexistent_addon(sample_registry_dir: Path) -> None:
    """Test `aiaddons check` for non-existent add-on returns error exit code."""
    result = runner.invoke(
        app, ["check", "nonexistent-addon", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 1
    assert "not found in registry" in result.stdout
