"""Integration tests for batch add-on installation lifecycle."""

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


def test_batch_install_integration_mcp_and_skill(tmp_path: Path, monkeypatch) -> None:
    """End-to-end integration test: install a batch of 2 add-ons (MCP + Skill) and verify all state."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "SKILL.md").write_text("# Batch Skill\nBatch Guidelines", encoding="utf-8")

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "b" * 64

    # 1. MCP manifest
    (reg_dir / "batch-mcp.json").write_text(
        f"""{{
            "id": "batch-mcp",
            "name": "Batch MCP Server",
            "version": "1.0.0",
            "description": "Batch MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/batch-server",
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
                    "package_name": "@modelcontextprotocol/batch-server"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. Skill manifest
    (reg_dir / "batch-skill.json").write_text(
        """{
            "id": "batch-skill",
            "name": "Batch Skill Addon",
            "version": "1.2.0",
            "description": "Batch skill integration",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "."},
            "trust": {"verification_status": "verified", "publisher": {"name": "Skill Team"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md"}}
        }""",
        encoding="utf-8",
    )

    # Setup isolated state, WAL, and agent detection
    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    monkeypatch.setattr(
        "aiaddons.cli.commands.install.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.install.TransactionWALManager",
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
    monkeypatch.chdir(workspace_dir)

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/batch-server"],
            return_code=0,
            stdout="Installed OK",
            stderr="",
            duration=0.15,
        )

        # Execute batch install
        install_res = runner.invoke(
            app,
            [
                "install",
                "batch-mcp",
                "batch-skill",
                "--yes",
                "--scope",
                "workspace",
                "--registry",
                str(reg_dir),
            ],
        )
        assert install_res.exit_code == 0
        assert "Successfully installed 2 add-on(s)" in install_res.stdout

    # Verify MCP server entry in .claude.json
    config_file = workspace_dir / ".claude.json"
    assert config_file.exists()
    cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert "batch-mcp" in cfg_data["mcpServers"]
    assert cfg_data["mcpServers"]["batch-mcp"]["command"] == "npx"

    # Verify Skill directory on disk
    skill_file = workspace_dir / ".claude" / "skills" / "batch-skill" / "SKILL.md"
    assert skill_file.exists()
    assert "# Batch Skill" in skill_file.read_text(encoding="utf-8")

    # Verify state.json records
    mcp_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "batch-mcp")
    assert mcp_rec is not None
    assert mcp_rec.addon_id == "batch-mcp"
    assert mcp_rec.version == "1.0.0"

    skill_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "batch-skill")
    assert skill_rec is not None
    assert skill_rec.addon_id == "batch-skill"
    assert skill_rec.version == "1.2.0"

    # Verify aiaddons.lock entries
    lock_entries = lockfile_mgr.get_entries(workspace_dir)
    assert len(lock_entries) == 2
    lock_ids = {e.addon_id for e in lock_entries}
    assert "batch-mcp" in lock_ids
    assert "batch-skill" in lock_ids
