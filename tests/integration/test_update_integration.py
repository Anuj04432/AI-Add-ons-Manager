"""Integration tests for end-to-end update and version-swap lifecycle."""

import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from aiaddons.cli.main import app
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
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


def test_update_end_to_end_cycle(tmp_path: Path, monkeypatch) -> None:
    """End-to-end integration test:

    1. Pre-install add-ons at version 1.0.0.
    2. Run `aiaddons update <addon-id>`.
    3. Verify state.json, aiaddons.lock, and config file reflect v2.0.0.
    4. Run `aiaddons update --all` and verify remaining outdated add-ons are updated.
    5. Re-run `aiaddons update --all` and verify clean in-sync / up-to-date message.
    """
    workspace_dir = tmp_path / "project"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Cloned Skill v1.0", encoding="utf-8")

    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "d" * 64

    # 1. MCP v2.0.0 in registry
    (reg_dir / "mcp-addon.json").write_text(
        f"""{{
            "id": "mcp-addon",
            "name": "MCP Integration",
            "version": "2.0.0",
            "description": "Updated MCP integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {{
                "source_type": "package",
                "package_name": "@scope/mcp-pkg-v2",
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
                    "package_name": "@scope/mcp-pkg-v2"
                }}
            }}
        }}""",
        encoding="utf-8",
    )

    # 2. Skill v2.0.0 in registry
    (reg_dir / "skill-addon.json").write_text(
        """{
            "id": "skill-addon",
            "name": "Skill Integration",
            "version": "2.0.0",
            "description": "Updated skill integration",
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

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

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
    monkeypatch.setattr(
        "aiaddons.core.update.engine.TransactionWALManager",
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
        "aiaddons.cli.commands.update.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(workspace_dir)},
    )

    # Initial state: both installed at v1.0.0
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mcp-addon",
            name="MCP Integration",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="skill-addon",
            name="Skill Integration",
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
            addon_id="mcp-addon",
            name="MCP Integration",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="skill-addon",
            name="Skill Integration",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.SKILL,
        ),
    )

    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"mcp-addon": {"command": "npx", "args": ["@scope/mcp-pkg-v1"]}}}
        ),
        encoding="utf-8",
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_ext:
        mock_ext.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@scope/mcp-pkg-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        # Step 1: Update mcp-addon individually
        up_res1 = runner.invoke(
            app,
            ["update", "mcp-addon", "--yes", "--registry", str(reg_dir)],
        )
        assert up_res1.exit_code == 0
        assert "Successfully updated MCP Integration (mcp-addon) from v1.0.0 to v2.0.0" in up_res1.stdout

        # Verify state.json
        rec1 = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-addon")
        assert rec1 is not None and rec1.version == "2.0.0"

        # Verify aiaddons.lock
        lock_entries = {e.addon_id: e for e in lockfile_mgr.get_entries(workspace_dir)}
        assert lock_entries["mcp-addon"].version == "2.0.0"
        assert lock_entries["skill-addon"].version == "1.0.0"

        # Verify .claude.json
        cfg = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
        assert "@scope/mcp-pkg-v2" in cfg["mcpServers"]["mcp-addon"]["args"]

        # Step 2: Update remaining outdated add-on via update --all
        up_res2 = runner.invoke(
            app,
            ["update", "--all", "--yes", "--registry", str(reg_dir)],
        )
        assert up_res2.exit_code == 0
        assert "Successfully updated Skill Integration (skill-addon)" in up_res2.stdout

        # Verify skill-addon is now v2.0.0
        rec2 = state_store.get_record("claude-code", Scope.WORKSPACE, "skill-addon")
        assert rec2 is not None and rec2.version == "2.0.0"

        # Step 3: Re-run update --all and verify clean up-to-date message
        up_res3 = runner.invoke(
            app,
            ["update", "--all", "--registry", str(reg_dir)],
        )
        assert up_res3.exit_code == 0
        assert "All installed add-on(s) are already up to date" in up_res3.stdout
        assert "No changes required" in up_res3.stdout
