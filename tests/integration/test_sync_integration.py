"""Integration tests for workspace synchronization lifecycle (Phase 6E)."""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
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


def test_sync_fresh_clone_lifecycle(tmp_path: Path, monkeypatch) -> None:
    """End-to-end integration test simulating a fresh clone scenario.

    1. A project has a pre-existing aiaddons.lock referencing 2 add-ons (MCP + Skill).
    2. Local machine has an empty state.json and no agent configs.
    3. Run `aiaddons sync`.
    4. Verify both add-ons are installed into configs, filesystem, and state.json.
    5. Re-run `aiaddons sync` and verify it reports already in sync.
    """
    workspace_dir = tmp_path / "cloned_repo"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    (workspace_dir / "SKILL.md").write_text("# Cloned Skill\nSkill instructions", encoding="utf-8")

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "c" * 64

    # 1. MCP manifest in registry
    (reg_dir / "clone-mcp.json").write_text(
        f"""{{
            "id": "clone-mcp",
            "name": "Clone MCP Server",
            "version": "1.0.0",
            "description": "Cloned MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@modelcontextprotocol/clone-server",
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
                    "package_name": "@modelcontextprotocol/clone-server"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. Skill manifest in registry
    (reg_dir / "clone-skill.json").write_text(
        """{
            "id": "clone-skill",
            "name": "Clone Skill Addon",
            "version": "1.0.0",
            "description": "Cloned skill integration",
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

    # Simulate committed aiaddons.lock in cloned repo
    lockfile_mgr = LockfileManager()
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="clone-mcp",
            name="Clone MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
            checksum=dummy_checksum,
        ),
    )
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="clone-skill",
            name="Clone Skill Addon",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.SKILL,
        ),
    )

    # Isolated state store
    state_store = InstalledStateStore(store_dir=state_dir)
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

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
    monkeypatch.setattr(
        "aiaddons.core.sync.engine.TransactionWALManager",
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
        "aiaddons.cli.commands.sync.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )
    monkeypatch.chdir(workspace_dir)

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/clone-server"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        # Step 1: Run sync on fresh clone
        sync_res1 = runner.invoke(
            app,
            [
                "sync",
                "--yes",
                "--registry",
                str(reg_dir),
            ],
        )
        assert sync_res1.exit_code == 0
        assert "Synchronization completed successfully" in sync_res1.stdout
        assert "clone-mcp" in sync_res1.stdout
        assert "clone-skill" in sync_res1.stdout

    # Verify MCP server entry in .claude.json
    config_file = workspace_dir / ".claude.json"
    assert config_file.exists()
    cfg_data = json.loads(config_file.read_text(encoding="utf-8"))
    assert "clone-mcp" in cfg_data["mcpServers"]
    assert cfg_data["mcpServers"]["clone-mcp"]["command"] == "npx"

    # Verify deployed Skill
    skill_file = workspace_dir / ".claude" / "skills" / "clone-skill" / "SKILL.md"
    assert skill_file.exists()
    assert "# Cloned Skill" in skill_file.read_text(encoding="utf-8")

    # Verify state.json
    mcp_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "clone-mcp")
    assert mcp_rec is not None
    assert mcp_rec.version == "1.0.0"

    skill_rec = state_store.get_record("claude-code", Scope.WORKSPACE, "clone-skill")
    assert skill_rec is not None
    assert skill_rec.version == "1.0.0"

    # Step 2: Re-run sync to verify idempotence / in-sync detection
    sync_res2 = runner.invoke(
        app,
        [
            "sync",
            "--registry",
            str(reg_dir),
        ],
    )
    assert sync_res2.exit_code == 0
    assert "in sync with the lockfile" in sync_res2.stdout
    assert "No changes required" in sync_res2.stdout
