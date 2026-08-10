"""Unit tests for Typer CLI install command in Phase 5A."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope

runner = CliRunner()


def test_cli_install_without_dry_run() -> None:
    """Verify that invoking `aiaddons install <id>` without --dry-run reports non-implementation."""
    result = runner.invoke(app, ["install", "github-mcp"])
    assert result.exit_code == 0
    assert "Real installation is not implemented yet. Use --dry-run." in result.stdout


def test_cli_install_nonexistent_addon(tmp_path: Path) -> None:
    """Verify error output when attempting dry-run for an unknown add-on ID."""
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()

    args = ["install", "unknown-addon", "--dry-run", "--registry", str(reg_dir)]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "Add-on 'unknown-addon' not found in registry" in result.stdout


def test_cli_install_with_dry_run(tmp_path: Path) -> None:
    """Verify formatted output for dry-run installation plan."""
    # Write a test manifest into temporary registry directory
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir()
    manifest_file = reg_dir / "github-mcp.json"
    dummy_checksum = "sha256:" + "a" * 64
    manifest_file.write_text(
        f"""{{
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["global", "workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
                "checksum": "{dummy_checksum}"
            }},
            "trust": {{
                "verification_status": "verified",
                "publisher": {{"name": "MCP Team"}}
            }},
            "handler_spec": {{
                "mcp": {{
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@modelcontextprotocol/server-github"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    mock_detected_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP],
    )

    with patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": mock_detected_agent},
    ):
        cmd_args = ["install", "github-mcp", "--dry-run", "--registry", str(reg_dir)]
        result = runner.invoke(app, cmd_args)
        assert result.exit_code == 0
        assert "GitHub MCP Server" in result.stdout
        assert "Target:" in result.stdout
        assert "Claude Code" in result.stdout
        assert "Plan:" in result.stdout
        assert "No changes were made." in result.stdout
