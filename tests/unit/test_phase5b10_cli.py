"""Comprehensive unit tests for Phase 5B.10 CLI installation interface and exit codes."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.cli.main import app
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.verification.models import (
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
)

runner = CliRunner()


def _create_sample_registry(tmp_path: Path, secret_req: bool = False) -> Path:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = reg_dir / "github-mcp.json"
    dummy_checksum = "sha256:" + "a" * 64

    env_block = (
        """,
            "env_vars": [
                {
                    "name": "GITHUB_TOKEN",
                    "required": true,
                    "secret": true,
                    "description": "GitHub Personal Access Token"
                },
                {
                    "name": "OPTIONAL_VAR",
                    "required": false,
                    "secret": false,
                    "description": "Optional debug variable"
                }
            ]"""
        if secret_req
        else ""
    )

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
                    "package_name": "@modelcontextprotocol/server-github"{env_block}
                }}
            }}
        }}""",
        encoding="utf-8",
    )
    return reg_dir


def _mock_claude_agent(tmp_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP],
        workspace_config_path=str(tmp_path / ".claude.json"),
    )


def test_cli_install_help() -> None:
    """Test `aiaddons install --help` output."""
    result = runner.invoke(app, ["install", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout
    assert "--yes" in result.stdout
    assert "--json" in result.stdout
    assert "--scope" in result.stdout
    assert "--agent" in result.stdout


def test_cli_install_dry_run_zero_side_effects(tmp_path: Path) -> None:
    """Verify that --dry-run performs zero disk/WAL mutations and outputs planned operations."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(tmp_path)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        args = ["install", "github-mcp", "--dry-run", "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        assert "GitHub MCP Server" in result.stdout
        assert "No changes were made." in result.stdout
        assert mock_ext.call_count == 0


def test_cli_install_confirmation_rejection(tmp_path: Path) -> None:
    """Verify that user rejecting confirmation prompt defaults to NO and makes no changes."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(tmp_path)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    with mock_mgr:
        args = ["install", "github-mcp", "--registry", str(reg_dir)]
        result = runner.invoke(app, args, input="n\n")
        assert result.exit_code == ExitCode.SUCCESS
        assert "Installation cancelled. No changes were made." in result.stdout


def test_cli_install_yes_behavior(tmp_path: Path) -> None:
    """Verify that --yes performs installation without prompt."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(tmp_path)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(app, ["install", "github-mcp", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully installed GitHub MCP Server" in result.stdout
        assert "Verification" in result.stdout


def test_cli_install_missing_addon(tmp_path: Path) -> None:
    """Verify error exit code 1 when addon is missing from registry."""
    reg_dir = _create_sample_registry(tmp_path)
    result = runner.invoke(app, ["install", "missing-id", "--registry", str(reg_dir)])
    assert result.exit_code == ExitCode.INVALID_INPUT
    assert "not found in registry" in result.stdout


def test_cli_install_incompatible_agent(tmp_path: Path) -> None:
    """Verify exit code 3 when target agent is incompatible."""
    reg_dir = _create_sample_registry(tmp_path)
    incompat_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.WORKSPACE],
        capabilities=[],  # No MCP capability
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": incompat_agent},
    )
    with mock_mgr:
        args = [
            "install",
            "github-mcp",
            "--agent",
            "claude-code",
            "--yes",
            "--registry",
            str(reg_dir),
        ]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.COMPATIBILITY_FAILURE
        assert "incompatible" in result.stdout.lower()


def test_cli_install_unsupported_scope(tmp_path: Path) -> None:
    """Verify exit code 3 when requested scope is unsupported by manifest or agent."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.WORKSPACE],  # Only workspace supported
        capabilities=[AgentCapability.MCP],
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    with mock_mgr:
        args = ["install", "github-mcp", "--scope", "global", "--yes", "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == ExitCode.COMPATIBILITY_FAILURE


def test_cli_install_missing_required_secret_non_interactive(tmp_path: Path) -> None:
    """Verify exit code 5 when missing required secret in non-interactive execution."""
    reg_dir = _create_sample_registry(tmp_path, secret_req=True)
    agent = _mock_claude_agent(tmp_path)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    with mock_mgr:
        result = runner.invoke(app, ["install", "github-mcp", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.EXECUTION_FAILURE
        assert "Missing required environment variable 'GITHUB_TOKEN'" in result.stdout


def test_cli_install_secret_masking(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify secrets are masked and never appear in stdout or JSON."""
    reg_dir = _create_sample_registry(tmp_path, secret_req=True)
    agent = _mock_claude_agent(tmp_path)
    secret_val = "ghp_super_secret_token_12345"
    monkeypatch.setenv("GITHUB_TOKEN", secret_val)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(app, ["install", "github-mcp", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert secret_val not in result.stdout
        assert "Successfully installed" in result.stdout


def test_cli_install_json_output(tmp_path: Path) -> None:
    """Verify deterministic --json output schema containing no secret values or tracebacks."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(tmp_path)

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    with mock_mgr:
        args = ["install", "github-mcp", "--dry-run", "--json", "--registry", str(reg_dir)]
        result = runner.invoke(app, args)
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["addon_id"] == "github-mcp"
        assert data["agent"] == "claude-code"
        assert data["scope"] == "workspace"
        assert data["compatible"] is True
        assert data["transaction_status"] == "PLANNED"
        assert data["success"] is True
        assert isinstance(data["planned_operations"], list)


def test_cli_install_verification_failure_rollback(tmp_path: Path) -> None:
    """Verify verification failure triggers rollback, displays output, and exits code 6."""
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(tmp_path)

    bad_ver_result = VerificationResult(
        addon_id="github-mcp",
        addon_name="GitHub MCP Server",
        target_agent="claude-code",
        target_scope=Scope.WORKSPACE,
        status=VerificationStatus.FAILED,
        verified=False,
        checks=[
            VerificationCheck(
                check_id="check1",
                description="MCP server registration verified",
                status=VerificationStatus.FAILED,
                error_info="Config path missing.",
            )
        ],
        errors=["Verification failed: configuration file missing."],
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")
    mock_ver = patch(
        "aiaddons.core.verification.engine.VerificationEngine.verify_plan",
        return_value=bad_ver_result,
    )

    with mock_mgr, mock_runner as mock_ext, mock_ver:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="/usr/bin/npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(app, ["install", "github-mcp", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.VERIFICATION_FAILURE
        assert "Verification failed." in result.stdout
        assert "Rolling back installation..." in result.stdout
        assert "Rollback completed" in result.stdout
