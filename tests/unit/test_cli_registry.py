"""Unit tests for registry CLI commands (aiaddons list, search, info)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aiaddons.cli.main import app

runner = CliRunner()


@pytest.fixture
def sample_registry_dir(tmp_path: Path) -> Path:
    """Fixture creating a temporary directory containing valid sample registry manifest files."""
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

    (reg_dir / "refactoring-skill.yaml").write_text(
        """
id: refactoring-skill
name: Refactoring Skill
version: 0.8.0
description: Code refactoring guidance skill.
license: Apache-2.0
category: workflow
integration_type: skill
target_agents:
  - codex
supported_scopes:
  - workspace
source:
  source_type: git
  repository: https://github.com/test/refactoring-skill
  commit_sha: 4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d
trust:
  verification_status: community
  publisher:
    name: Community Team
tags:
  - refactoring
handler_spec:
  skill:
    skill_file: SKILL.md
""",
        encoding="utf-8",
    )

    return reg_dir


def test_cli_list(sample_registry_dir: Path) -> None:
    """Test `aiaddons list` command formatting."""
    result = runner.invoke(app, ["list", "--registry-dir", str(sample_registry_dir)])
    assert result.exit_code == 0
    assert "github-mcp" in result.stdout
    assert "refactoring-skill" in result.stdout


def test_cli_list_json(sample_registry_dir: Path) -> None:
    """Test `aiaddons list --json` output."""
    result = runner.invoke(app, ["list", "--json", "--registry-dir", str(sample_registry_dir)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["id"] in ("github-mcp", "refactoring-skill")


def test_cli_list_filter_type(sample_registry_dir: Path) -> None:
    """Test `aiaddons list --type mcp` filtering."""
    result = runner.invoke(
        app, ["list", "--type", "mcp", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 0
    assert "github-mcp" in result.stdout
    assert "refactoring-skill" not in result.stdout


def test_cli_search(sample_registry_dir: Path) -> None:
    """Test `aiaddons search` command."""
    result = runner.invoke(app, ["search", "github", "--registry-dir", str(sample_registry_dir)])
    assert result.exit_code == 0
    assert "github-mcp" in result.stdout
    assert "refactoring-skill" not in result.stdout


def test_cli_search_json(sample_registry_dir: Path) -> None:
    """Test `aiaddons search <query> --json` command."""
    result = runner.invoke(
        app, ["search", "github", "--json", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "github-mcp"


def test_cli_info(sample_registry_dir: Path) -> None:
    """Test `aiaddons info <addon-id>` command."""
    result = runner.invoke(app, ["info", "github-mcp", "--registry-dir", str(sample_registry_dir)])
    assert result.exit_code == 0
    assert "GitHub MCP Server" in result.stdout
    assert "1.2.0" in result.stdout
    assert "MCP Team" in result.stdout
    assert "claude-code" in result.stdout


def test_cli_info_json(sample_registry_dir: Path) -> None:
    """Test `aiaddons info <addon-id> --json` command."""
    result = runner.invoke(
        app, ["info", "github-mcp", "--json", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["id"] == "github-mcp"
    assert parsed["integration_type"] == "mcp"


def test_cli_info_nonexistent(sample_registry_dir: Path) -> None:
    """Test `aiaddons info` for non-existent add-on ID returns non-zero exit code."""
    result = runner.invoke(
        app, ["info", "nonexistent-addon", "--registry-dir", str(sample_registry_dir)]
    )
    assert result.exit_code == 1
    assert "not found in registry" in result.stdout
