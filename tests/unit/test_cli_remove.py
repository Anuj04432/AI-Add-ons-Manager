"""Unit tests for Typer CLI remove command (aiaddons remove <addon-id>)."""

import json
from pathlib import Path

from typer.testing import CliRunner

from aiaddons.cli.exit_codes import ExitCode
from aiaddons.cli.main import app
from aiaddons.core.installer.models import TransactionPhase
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager

runner = CliRunner()


def _create_sample_registry(tmp_path: Path) -> Path:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
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

    skill_file = reg_dir / "test-skill.json"
    skill_file.write_text(
        """{
            "id": "test-skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "description": "Test skill integration",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["global", "workspace"],
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

    return reg_dir


def _mock_claude_agent(tmp_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        workspace_config_path=str(tmp_path / ".claude.json"),
    )


def test_remove_help() -> None:
    """Verify that `aiaddons remove --help` outputs all standard options."""
    result = runner.invoke(app, ["remove", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout
    assert "--yes" in result.stdout
    assert "--force" in result.stdout
    assert "--json" in result.stdout
    assert "--scope" in result.stdout
    assert "--agent" in result.stdout
    assert "--registry" in result.stdout


def test_remove_not_found_addon(tmp_path: Path, monkeypatch) -> None:
    """Verify error output and exit code 1 when attempting to remove an uninstalled add-on."""
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: InstalledStateStore(store_dir=state_dir),
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["remove", "non-existent-addon"])
    assert result.exit_code == ExitCode.INVALID_INPUT
    assert "Add-on 'non-existent-addon' is not installed on this system." in result.stdout


def test_remove_mcp_server_success(tmp_path: Path, monkeypatch) -> None:
    """Verify clean uninstallation of an MCP server with state store and lockfile cleanup."""
    reg_dir = _create_sample_registry(tmp_path)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github-mcp": {
                        "command": "npx",
                        "args": ["@modelcontextprotocol/server-github"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="github-mcp",
            name="GitHub MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-19T00:00:00Z",
            installed_files=[".claude.json"],
        )
    )

    lockfile_mgr = LockfileManager()
    lockfile_mgr.update_lockfile(
        tmp_path,
        LockfileAddonEntry(
            addon_id="github-mcp",
            name="GitHub MCP Server",
            version="1.0.0",
            integration_type=IntegrationType.MCP,
            target_agent="claude-code",
        ),
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["remove", "github-mcp", "--yes", "--registry", str(reg_dir)]
    )
    assert result.exit_code == 0
    assert "Successfully removed GitHub MCP Server" in result.stdout

    # Verify MCP server entry is removed from config
    new_config = json.loads(config_file.read_text(encoding="utf-8"))
    assert "github-mcp" not in new_config.get("mcpServers", {})

    # Verify removed from state store and lockfile
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp") is None
    assert len(lockfile_mgr.get_entries(tmp_path)) == 0


def test_remove_skill_success(tmp_path: Path, monkeypatch) -> None:
    """Verify clean uninstallation of an Agent Skill directory."""
    reg_dir = _create_sample_registry(tmp_path)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Test Skill\nDescription", encoding="utf-8")

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="test-skill",
            name="Test Skill",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.SKILL,
            installed_at="2026-08-19T00:00:00Z",
            installed_files=[".claude/skills/test-skill/SKILL.md"],
        )
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["remove", "test-skill", "--yes", "--registry", str(reg_dir)]
    )
    assert result.exit_code == 0
    assert "Successfully removed Test Skill" in result.stdout

    # Verify skill directory is removed
    assert not skill_dir.exists()
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "test-skill") is None


def test_remove_drift_detection_warns_and_requires_force(tmp_path: Path, monkeypatch) -> None:
    """Verify that drift is detected and blocks removal unless --force is specified."""
    reg_dir = _create_sample_registry(tmp_path)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    # Empty config file (mcpServers.github-mcp is missing -> drift)
    config_file = tmp_path / ".claude.json"
    config_file.write_text("{}", encoding="utf-8")

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="github-mcp",
            name="GitHub MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-19T00:00:00Z",
            installed_files=[".claude.json"],
        )
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    # 1. Without --force: should fail with exit code 1
    res_no_force = runner.invoke(
        app, ["remove", "github-mcp", "--yes", "--registry", str(reg_dir)]
    )
    assert res_no_force.exit_code == ExitCode.INVALID_INPUT
    assert "Configuration/filesystem drift detected" in res_no_force.stdout
    assert "Use --force to remove anyway" in res_no_force.stdout

    # 2. With --force: should succeed and clean up state database
    res_force = runner.invoke(
        app, ["remove", "github-mcp", "--yes", "--force", "--registry", str(reg_dir)]
    )
    assert res_force.exit_code == 0
    assert "Warning: Configuration/filesystem drift detected" in res_force.stdout
    assert "Successfully removed GitHub MCP Server" in res_force.stdout
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp") is None


def test_remove_plugin_with_shared_child_components(tmp_path: Path, monkeypatch) -> None:
    """Verify that removing a plugin preserves child components shared by another active plugin."""
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)
    wal_dir = state_dir / "transactions"
    wal_dir.mkdir(parents=True, exist_ok=True)

    # Plugin A manifest
    (reg_dir / "plugin-a.json").write_text(
        """{
            "id": "plugin-a",
            "name": "Plugin A",
            "version": "1.0.0",
            "description": "Composite plugin A",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "plugin",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "."},
            "trust": {"verification_status": "verified", "publisher": {"name": "Team"}},
            "handler_spec": {"plugin": {"components": ["shared-mcp", "skill-a"]}}
        }""",
        encoding="utf-8",
    )

    # Plugin B manifest
    (reg_dir / "plugin-b.json").write_text(
        """{
            "id": "plugin-b",
            "name": "Plugin B",
            "version": "1.0.0",
            "description": "Composite plugin B",
            "license": "MIT",
            "category": "workflow",
            "integration_type": "plugin",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "."},
            "trust": {"verification_status": "verified", "publisher": {"name": "Team"}},
            "handler_spec": {"plugin": {"components": ["shared-mcp", "skill-b"]}}
        }""",
        encoding="utf-8",
    )

    # Shared MCP manifest
    (reg_dir / "shared-mcp.json").write_text(
        """{
            "id": "shared-mcp",
            "name": "Shared MCP",
            "version": "1.0.0",
            "description": "Shared MCP server",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "package", "package_name": "shared-pkg"},
            "trust": {"verification_status": "verified", "publisher": {"name": "Team"}},
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "shared-pkg",
                }
            },
        }""",
        encoding="utf-8",
    )

    # Skill A manifest
    (reg_dir / "skill-a.json").write_text(
        """{
            "id": "skill-a",
            "name": "Skill A",
            "version": "1.0.0",
            "description": "Skill A",
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

    # Files on disk
    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps({"mcpServers": {"shared-mcp": {"command": "npx", "args": ["shared-pkg"]}}}),
        encoding="utf-8",
    )

    skill_a_dir = tmp_path / ".claude" / "skills" / "skill-a"
    skill_a_dir.mkdir(parents=True, exist_ok=True)
    (skill_a_dir / "SKILL.md").write_text("# Skill A", encoding="utf-8")

    plugin_a_dir = tmp_path / ".agents" / "plugins" / "plugin-a"
    plugin_a_dir.mkdir(parents=True, exist_ok=True)
    (plugin_a_dir / "plugin.json").write_text(
        json.dumps({"id": "plugin-a", "components": ["shared-mcp", "skill-a"]}),
        encoding="utf-8",
    )

    plugin_b_dir = tmp_path / ".agents" / "plugins" / "plugin-b"
    plugin_b_dir.mkdir(parents=True, exist_ok=True)
    (plugin_b_dir / "plugin.json").write_text(
        json.dumps({"id": "plugin-b", "components": ["shared-mcp", "skill-b"]}),
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="plugin-a",
            name="Plugin A",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.PLUGIN,
            installed_at="2026-08-19T00:00:00Z",
        )
    )
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="plugin-b",
            name="Plugin B",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.PLUGIN,
            installed_at="2026-08-19T00:00:00Z",
        )
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.execution.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.core.installer.engine.TransactionWALManager",
        lambda *args, **kwargs: TransactionWALManager(transactions_dir=wal_dir),
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    # Remove plugin-a without --force: shared-mcp should be skipped and preserved
    res = runner.invoke(app, ["remove", "plugin-a", "--yes", "--registry", str(reg_dir)])
    assert res.exit_code == 0
    assert "Successfully removed Plugin A" in res.stdout

    # Verify skill-a and plugin-a directory removed
    assert not skill_a_dir.exists()
    assert not plugin_a_dir.exists()

    # Verify shared-mcp server STILL present in .claude.json for Plugin B
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    assert "shared-mcp" in cfg.get("mcpServers", {})


def test_remove_dry_run(tmp_path: Path, monkeypatch) -> None:
    """Verify that --dry-run outputs the removal plan without modifying filesystem."""
    reg_dir = _create_sample_registry(tmp_path)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)

    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps({"mcpServers": {"github-mcp": {"command": "npx", "args": ["@mcp/github"]}}}),
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="github-mcp",
            name="GitHub MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-19T00:00:00Z",
            installed_files=[".claude.json"],
        )
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["remove", "github-mcp", "--dry-run", "--registry", str(reg_dir)]
    )
    assert result.exit_code == 0
    assert "GitHub MCP Server (Removal)" in result.stdout
    assert "No changes were made." in result.stdout

    # File and state must remain untouched
    cfg = json.loads(config_file.read_text(encoding="utf-8"))
    assert "github-mcp" in cfg.get("mcpServers", {})
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "github-mcp") is not None


def test_remove_json_output(tmp_path: Path, monkeypatch) -> None:
    """Verify machine-readable JSON output for removal dry-run."""
    reg_dir = _create_sample_registry(tmp_path)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)

    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps({"mcpServers": {"github-mcp": {"command": "npx", "args": ["@mcp/github"]}}}),
        encoding="utf-8",
    )

    state_store = InstalledStateStore(store_dir=state_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="github-mcp",
            name="GitHub MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-19T00:00:00Z",
            installed_files=[".claude.json"],
        )
    )

    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.InstalledStateStore",
        lambda *args, **kwargs: state_store,
    )
    monkeypatch.setattr(
        "aiaddons.cli.commands.remove.AgentDetectionManager.detect_agents",
        lambda self: {"claude-code": _mock_claude_agent(tmp_path)},
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(
        app, ["remove", "github-mcp", "--dry-run", "--json", "--registry", str(reg_dir)]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["addon_id"] == "github-mcp"
    assert data["transaction_status"] == TransactionPhase.PLANNED.name
    assert data["success"] is True
    assert len(data["planned_operations"]) > 0


def test_remove_interrupted_recovery_wal(tmp_path: Path) -> None:
    """Verify that an interrupted removal transaction is detected and recovered via WAL."""
    from aiaddons.core.installer.engine import InstallationEngine
    from aiaddons.registry.registry import Registry

    wal_dir = tmp_path / "wal"
    wal_dir.mkdir(parents=True, exist_ok=True)
    wal_mgr = TransactionWALManager(transactions_dir=wal_dir)

    reg_dir = _create_sample_registry(tmp_path)
    registry, _ = Registry.load_auto(custom_dir=reg_dir)
    manifest = registry.get("github-mcp")
    assert manifest is not None

    agent = _mock_claude_agent(tmp_path)
    engine = InstallationEngine(registry=registry, wal_manager=wal_mgr)
    tx = engine.create_removal_transaction(manifest, agent, Scope.WORKSPACE, registry=registry)
    tx.phase = TransactionPhase.EXECUTING
    wal_mgr.write_transaction(tx)

    interrupted = wal_mgr.list_interrupted_transactions()
    assert len(interrupted) == 1
    assert interrupted[0].transaction_id == tx.transaction_id

    recovered = wal_mgr.recover_interrupted_transaction(tx.transaction_id)
    assert recovered.phase in (TransactionPhase.ROLLED_BACK, TransactionPhase.FAILED)
