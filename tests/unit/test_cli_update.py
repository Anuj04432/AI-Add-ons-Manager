"""Unit tests for 'aiaddons update' CLI command."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.cli.main import app
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore

runner = CliRunner()


def _mock_claude_agent(workspace_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        workspace_config_path=str(workspace_path / ".claude.json"),
    )


def _create_sample_registry(tmp_path: Path) -> Path:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "a" * 64

    # 1. MCP server 1 (v2.0.0 in registry)
    (reg_dir / "mcp-server-1.json").write_text(
        f"""{{
            "id": "mcp-server-1",
            "name": "MCP Server One",
            "version": "2.0.0",
            "description": "Updated MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-one-v2",
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
                    "package_name": "@modelcontextprotocol/server-one-v2"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. Skill addon (v2.0.0 in registry)
    (reg_dir / "sample-skill.json").write_text(
        """{
            "id": "sample-skill",
            "name": "Sample Skill",
            "version": "2.0.0",
            "description": "Updated agent skill",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "local",
                "path": "."
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Skill Team"}
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md"
                }
            }
        }""",
        encoding="utf-8",
    )

    # 3. Static MCP (v1.0.0 in registry)
    (reg_dir / "static-mcp.json").write_text(
        f"""{{
            "id": "static-mcp",
            "name": "Static MCP Server",
            "version": "1.0.0",
            "description": "Static MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-static",
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
                    "package_name": "@modelcontextprotocol/server-static"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    return reg_dir


def test_cli_update_single_addon_success(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update <addon-id>' updates single add-on to latest registry version."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    # Pre-populate v1.0.0
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"mcp-server-1": {"command": "npx", "args": ["@scope/v1"]}}}
        ),
        encoding="utf-8",
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(
            app, ["update", "mcp-server-1", "--yes", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully updated" in result.stdout
        assert "v1.0.0 to v2.0.0" in result.stdout

        # Verify state store has v2.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec is not None and rec.version == "2.0.0"


def test_cli_update_already_up_to_date(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update <addon-id>' reports no-op cleanly when already up-to-date."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="static-mcp",
            name="Static MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(
            app, ["update", "static-mcp", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "already up to date" in result.stdout
        assert "No changes required" in result.stdout


def test_cli_update_not_installed_error(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update <addon-id>' errors out if add-on is not installed."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(
            app, ["update", "mcp-server-1", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.INVALID_INPUT
        assert "not installed" in result.stdout


def test_cli_update_version_flag_pinned(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update <addon-id> --version <v>' pins to requested version."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"mcp-server-1": {"command": "npx", "args": ["@scope/v1"]}}}
        ),
        encoding="utf-8",
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(
            app,
            [
                "update",
                "mcp-server-1",
                "--version",
                "2.0.0",
                "--yes",
                "--registry",
                str(reg_dir),
            ],
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully updated" in result.stdout


def test_cli_update_all_updates_multiple_addons(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update --all' updates all outdated add-ons."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="sample-skill",
            name="Sample Skill",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.SKILL,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="sample-skill",
            name="Sample Skill",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.SKILL,
        ),
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(
            app, ["update", "--all", "--yes", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Successfully updated 2 add-on(s)" in result.stdout

        # Verify state store records
        rec1 = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec1 is not None and rec1.version == "2.0.0"

        rec2 = state_store.get_record("claude-code", Scope.WORKSPACE, "sample-skill")
        assert rec2 is not None and rec2.version == "2.0.0"


def test_cli_update_dry_run(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update --dry-run' outputs plan without making changes."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(
            app, ["update", "mcp-server-1", "--dry-run", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Update Plan" in result.stdout
        assert "Phase 1: Remove Old Version" in result.stdout
        assert "Phase 2: Install New Version" in result.stdout
        assert "Dry-run mode: No changes were made." in result.stdout

        # Record still at 1.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec is not None and rec.version == "1.0.0"


def test_cli_update_json_output(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify 'aiaddons update --json' produces valid machine-readable JSON."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(
            app,
            ["update", "mcp-server-1", "--dry-run", "--json", "--registry", str(reg_dir)],
        )
        assert result.exit_code == ExitCode.SUCCESS
        data = json.loads(result.stdout)
        assert data["agent"] == "claude-code"
        assert data["scope"] == "workspace"
        assert data["addon_id"] == "mcp-server-1"
        assert data["old_version"] == "1.0.0"
        assert data["new_version"] == "2.0.0"
        assert data["dry_run"] is True
        assert data["success"] is True
        assert len(data["planned_operations"]) > 0


def test_cli_update_user_cancel_prompt(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify that declining the confirmation prompt cancels update without mutating system."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.update.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.update.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-server-1",
            name="MCP Server One",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        # Simulate typing 'n' to confirmation prompt
        result = runner.invoke(
            app, ["update", "mcp-server-1", "--registry", str(reg_dir)], input="n\n"
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Update cancelled" in result.stdout

        # Record still at 1.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec is not None and rec.version == "1.0.0"
