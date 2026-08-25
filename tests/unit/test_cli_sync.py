"""Unit tests for 'aiaddons sync' CLI command (Phase 6E)."""

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

    # 1. MCP server 1
    (reg_dir / "mcp-server-1.json").write_text(
        f"""{{
            "id": "mcp-server-1",
            "name": "MCP Server One",
            "version": "1.0.0",
            "description": "First MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-one",
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
                    "package_name": "@modelcontextprotocol/server-one"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. Skill addon
    (reg_dir / "sample-skill.json").write_text(
        """{
            "id": "sample-skill",
            "name": "Sample Skill",
            "version": "1.0.0",
            "description": "Sample agent skill",
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

    # 3. Mismatched MCP v2.0.0
    (reg_dir / "mismatched-mcp.json").write_text(
        f"""{{
            "id": "mismatched-mcp",
            "name": "Mismatched MCP",
            "version": "2.0.0",
            "description": "Updated MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-mismatched",
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
                    "package_name": "@modelcontextprotocol/server-mismatched"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    return reg_dir


def test_cli_sync_missing_lockfile_error(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify that running sync when no lockfile exists outputs a clear error."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    agent = _mock_claude_agent(workspace_dir)
    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync"])
        assert result.exit_code == ExitCode.INVALID_INPUT
        assert "No lockfile found" in result.stdout


def test_cli_sync_already_in_sync(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify sync when environment matches lockfile reports clean with no changes."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    rec = InstalledAddonRecord(
        addon_id="mcp-server-1",
        name="MCP Server One",
        version="1.0.0",
        target_agent="claude-code",
        scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        installed_at="2026-08-25T12:00:00Z",
    )
    state_store.record_installation(rec)

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

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "in sync with the lockfile" in result.stdout
        assert "No changes required" in result.stdout


def test_cli_sync_installs_missing_addons(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify sync installs missing add-ons from lockfile."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Sample Skill", encoding="utf-8")
    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()
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
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(
            app, ["sync", "--yes", "--registry", str(reg_dir)]
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Synchronization completed successfully" in result.stdout
        assert "mcp-server-1" in result.stdout
        assert "sample-skill" in result.stdout


def test_cli_sync_reports_unmanaged_without_pruning_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Verify sync detects unmanaged add-ons and reports them without auto-removing."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()

    # Pre-populate state store with mcp-server-1 and extra-unmanaged
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
            addon_id="extra-unmanaged",
            name="Extra Unmanaged",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    # Lockfile only specifies mcp-server-1
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

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Unmanaged locally" in result.stdout
        assert "extra-unmanaged" in result.stdout
        assert "--prune" in result.stdout

        # Verify extra-unmanaged is STILL in installed state store
        assert state_store.get_record("claude-code", Scope.WORKSPACE, "extra-unmanaged") is not None


def test_cli_sync_prune_removes_unmanaged_with_confirmation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Verify sync with --prune removes unmanaged add-ons."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()

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
            addon_id="extra-unmanaged",
            name="Extra Unmanaged",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mcp-server-1": {"command": "npx", "args": ["@scope/pkg1"]},
                    "extra-unmanaged": {"command": "npx", "args": ["@scope/pkg2"]},
                }
            }
        ),
        encoding="utf-8",
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

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--prune", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Pruned:" in result.stdout
        assert "extra-unmanaged" in result.stdout

        # Verify extra-unmanaged is removed from state store and config
        assert state_store.get_record("claude-code", Scope.WORKSPACE, "extra-unmanaged") is None
        cfg = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
        assert "extra-unmanaged" not in cfg["mcpServers"]


def test_cli_sync_reports_version_mismatch_without_updating_by_default(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Verify sync detects version mismatches without updating by default."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()

    # Installed v1.0.0
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mismatched-mcp",
            name="Mismatched MCP",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    # Lockfile has v2.0.0
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="mismatched-mcp",
            name="Mismatched MCP",
            version="2.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Version mismatch" in result.stdout
        assert "installed: 1.0.0, expected: 2.0.0" in result.stdout
        assert "--update" in result.stdout

        # Verify version remains 1.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mismatched-mcp")
        assert rec is not None
        assert rec.version == "1.0.0"


def test_cli_sync_update_flag_updates_mismatched_version(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Verify sync with --update updates version-mismatched add-ons."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()

    # Installed v1.0.0
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mismatched-mcp",
            name="Mismatched MCP",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"mismatched-mcp": {"command": "npx", "args": ["@scope/v1"]}}}
        ),
        encoding="utf-8",
    )

    # Lockfile has v2.0.0
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="mismatched-mcp",
            name="Mismatched MCP",
            version="2.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-mismatched"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(app, ["sync", "--update", "--yes", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Updated:" in result.stdout
        assert "mismatched-mcp" in result.stdout

        # Verify state store now has version 2.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mismatched-mcp")
        assert rec is not None
        assert rec.version == "2.0.0"


def test_cli_sync_dry_run_produces_plan_without_mutations(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Verify sync --dry-run outputs plan and diff without touching disk."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()
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

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--dry-run", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        assert "Missing to install" in result.stdout
        assert "MCP Server One" in result.stdout
        assert "Dry-run mode: No changes were made." in result.stdout

        # Verify no .claude.json was created
        assert not (workspace_dir / ".claude.json").exists()


def test_cli_sync_json_output(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify sync --json produces structured machine-readable JSON."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    lockfile_mgr = LockfileManager()
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

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )

    with mock_mgr:
        result = runner.invoke(app, ["sync", "--dry-run", "--json", "--registry", str(reg_dir)])
        assert result.exit_code == ExitCode.SUCCESS
        data = json.loads(result.stdout)
        assert data["agent"] == "claude-code"
        assert data["scope"] == "workspace"
        assert data["dry_run"] is True
        assert "diff" in data
        assert len(data["diff"]["missing"]) == 1
        assert data["diff"]["missing"][0]["addon_id"] == "mcp-server-1"


def test_cli_sync_stack_file_flag(tmp_path: Path, monkeypatch: Any) -> None:
    """Verify sync with explicit --file pointing to a stack file."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    monkeypatch.chdir(workspace_dir)

    reg_dir = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    monkeypatch.setattr(
        "aiaddons.cli.commands.sync.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )

    stack_file = workspace_dir / "custom-stack.yaml"
    stack_file.write_text(
        """version: "1.0"
addons:
  - id: mcp-server-1
    version: "1.0.0"
""",
        encoding="utf-8",
    )

    mock_mgr = patch(
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        return_value={"claude-code": agent},
    )
    mock_runner = patch("aiaddons.core.execution.external.runner.ExternalRunner.execute")

    with mock_mgr, mock_runner as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        result = runner.invoke(
            app,
            [
                "sync",
                "--file",
                str(stack_file),
                "--yes",
                "--registry",
                str(reg_dir),
            ],
        )
        assert result.exit_code == ExitCode.SUCCESS
        assert "Synchronization completed successfully" in result.stdout
        assert "mcp-server-1" in result.stdout
