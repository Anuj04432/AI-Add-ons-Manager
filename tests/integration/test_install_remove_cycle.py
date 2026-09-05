"""Integration tests for the full install -> verify -> remove -> verify lifecycle."""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

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


def test_full_install_remove_cycle_mcp(tmp_path: Path, monkeypatch) -> None:
    """End-to-end integration test: install MCP server fixture -> verify -> remove -> verify."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    # Use actual registry fixture directory
    repo_root = Path(__file__).resolve().parent.parent.parent
    registry_dir = repo_root / "registry" / "addons"

    # Setup isolated state, WAL, and agent detection
    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )
    monkeypatch.chdir(workspace_dir)
    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "ghp_dummy1234567890dummy1234567890dummy123")

    # 1. Install phase
    with (
        patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_ext,
        patch("aiaddons.core.secrets.resolver.getpass.getpass", return_value="ghp_dummy1234567890dummy1234567890dummy123"),
    ):
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
            return_code=0,
            stdout="Installed OK",
            stderr="",
            duration=0.2,
        )

        install_res = runner.invoke(
            app,
            [
                "install",
                "github-mcp",
                "--yes",
                "--scope",
                "workspace",
                "--registry",
                str(registry_dir),
            ],
        )
        assert install_res.exit_code == 0
        assert "Successfully installed GitHub MCP Server" in install_res.stdout

    # 2. Verify on-disk and state persistence after install
    config_file = workspace_dir / ".claude.json"
    assert config_file.exists()
    cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert "github-mcp" in cfg_data["mcpServers"]

    record = state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp")
    assert record is not None
    assert record.addon_id == "github-mcp"

    lock_entries = lockfile_mgr.get_entries(workspace_dir)
    assert len(lock_entries) == 1
    assert lock_entries[0].addon_id == "github-mcp"

    # 3. Removal phase
    remove_res = runner.invoke(
        app,
        [
            "remove",
            "github-mcp",
            "--yes",
            "--scope",
            "workspace",
            "--registry",
            str(registry_dir),
        ],
    )
    assert remove_res.exit_code == 0
    assert "Successfully removed GitHub MCP Server" in remove_res.stdout

    # 4. Verify on-disk and state records after removal
    new_cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert "github-mcp" not in new_cfg_data.get("mcpServers", {})

    assert state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp") is None
    assert len(lockfile_mgr.get_entries(workspace_dir)) == 0


def test_full_install_remove_cycle_skill(tmp_path: Path, monkeypatch) -> None:
    """End-to-end integration test: install Skill -> verify -> remove -> verify."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "SKILL.md").write_text("# Clean Code\nGuidelines", encoding="utf-8")

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "clean-code-skill.json").write_text(
        """{
            "id": "clean-code-skill",
            "name": "Clean Code Skill",
            "version": "1.0.0",
            "description": "Clean code skill guidelines",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "."},
            "trust": {"verification_status": "verified", "publisher": {"name": "Team"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md"}}
        }""",
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: wal_mgr,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )
    monkeypatch.chdir(workspace_dir)

    # 1. Install skill
    install_res = runner.invoke(
        app,
        [
            "install",
            "clean-code-skill",
            "--yes",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
        ],
    )
    assert install_res.exit_code == 0
    assert "Successfully installed Clean Code Skill" in install_res.stdout

    # 2. Verify skill directory exists on disk
    skill_dir = workspace_dir / ".claude" / "skills" / "clean-code-skill"
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "clean-code-skill") is not None
    assert len(lockfile_mgr.get_entries(workspace_dir)) == 1

    # 3. Remove skill
    remove_res = runner.invoke(
        app,
        [
            "remove",
            "clean-code-skill",
            "--yes",
            "--scope",
            "workspace",
            "--registry",
            str(reg_dir),
        ],
    )
    assert remove_res.exit_code == 0
    assert "Successfully removed Clean Code Skill" in remove_res.stdout

    # 4. Verify skill directory is completely gone from disk
    assert not skill_dir.exists()
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "clean-code-skill") is None
    assert len(lockfile_mgr.get_entries(workspace_dir)) == 0
