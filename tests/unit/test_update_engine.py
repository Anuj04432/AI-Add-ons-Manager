"""Unit tests for UpdateEngine domain logic and transactional version-swap guarantees."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock

from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationError,
    InstallationPlanningError,
    StateLockTimeoutError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.core.update.engine import UpdateEngine, is_newer_version
from aiaddons.registry.registry import Registry
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


def _mock_claude_agent(workspace_path: Path) -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
        workspace_config_path=str(workspace_path / ".claude.json"),
    )


def _create_sample_registry(tmp_path: Path) -> Registry:
    reg_dir = tmp_path / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    dummy_checksum = "sha256:" + "a" * 64

    # 1. MCP server v2.0.0 in registry (upgrade from v1.0.0)
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

    # 2. Skill v2.0.0 in registry
    (reg_dir / "skill-addon.json").write_text(
        """{
            "id": "skill-addon",
            "name": "Skill Addon",
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

    # 3. Add-on with same version 1.0.0
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

    reg, _ = Registry.from_directory(reg_dir)
    return reg


def test_is_newer_version() -> None:
    """Verify semantic version comparisons."""
    assert is_newer_version("1.0.0", "1.0.1") is True
    assert is_newer_version("1.0.0", "1.1.0") is True
    assert is_newer_version("1.0.0", "2.0.0") is True
    assert is_newer_version("2.0.0", "1.0.0") is False
    assert is_newer_version("1.0.0", "1.0.0") is False
    assert is_newer_version("1.0.0-alpha.1", "1.0.0") is True


def test_update_to_latest_version_succeeds(tmp_path: Path, monkeypatch) -> None:
    """Verify updating an installed add-on to the latest registry version succeeds."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

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

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_runner:
        mock_runner.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        plan = update_engine.plan_update(
            addon_id="mcp-server-1",
            target_agent=agent,
            scope=Scope.WORKSPACE,
            registry=registry,
        )
        assert not plan.is_empty
        assert len(plan.items) == 1
        assert plan.items[0].current_version == "1.0.0"
        assert plan.items[0].target_version == "2.0.0"

        res = update_engine.execute_update(
            plan=plan,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )

        assert res.success is True
        assert res.updated_addons == ["mcp-server-1"]

        # Verify state store has v2.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec is not None
        assert rec.version == "2.0.0"

        # Verify lockfile has v2.0.0
        entries = lockfile_mgr.get_entries(workspace_dir)
        assert entries[0].version == "2.0.0"

        # Verify .claude.json has v2.0.0 package
        cfg = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
        assert "@modelcontextprotocol/server-one-v2" in cfg["mcpServers"]["mcp-server-1"]["args"]


def test_update_to_pinned_version_succeeds(tmp_path: Path, monkeypatch) -> None:
    """Verify updating to a specific pinned version succeeds."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

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
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"mcp-server-1": {"command": "npx", "args": ["@scope/v1"]}}}
        ),
        encoding="utf-8",
    )

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_runner:
        mock_runner.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        plan = update_engine.plan_update(
            addon_id="mcp-server-1",
            target_agent=agent,
            scope=Scope.WORKSPACE,
            target_version="2.0.0",
            registry=registry,
        )
        assert len(plan.items) == 1
        assert plan.items[0].target_version == "2.0.0"

        res = update_engine.execute_update(
            plan=plan,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )
        assert res.success is True


def test_update_when_already_up_to_date_reports_noop(tmp_path: Path) -> None:
    """Verify that updating an already up-to-date add-on reports no-op cleanly."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Pre-populate static-mcp at v1.0.0 (same as registry)
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

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    plan = update_engine.plan_update(
        addon_id="static-mcp",
        target_agent=agent,
        scope=Scope.WORKSPACE,
        registry=registry,
    )
    assert plan.is_empty is True
    assert len(plan.skipped_items) == 1
    assert plan.skipped_items[0]["reason"] == "Already up to date"

    res = update_engine.execute_update(
        plan=plan,
        target_agent=agent,
        workspace_dir=workspace_dir,
        registry=registry,
    )
    assert res.success is True
    assert res.already_up_to_date is True
    assert len(res.updated_addons) == 0


def test_update_all_updates_multiple_addons_in_one_transaction(tmp_path: Path, monkeypatch) -> None:
    """Verify update --all updates all outdated add-ons in one atomic transaction."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Skill v1.0", encoding="utf-8")

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Installed: mcp-server-1 (v1.0.0), skill-addon (v1.0.0), static-mcp (v1.0.0)
    for aid, itype in [
        ("mcp-server-1", IntegrationType.MCP),
        ("skill-addon", IntegrationType.SKILL),
        ("static-mcp", IntegrationType.MCP),
    ]:
        state_store.record_installation(
            InstalledAddonRecord(
                addon_id=aid,
                name=aid,
                version="1.0.0",
                target_agent="claude-code",
                scope=Scope.WORKSPACE,
                integration_type=itype,
                installed_at="2026-08-25T12:00:00Z",
            )
        )
        lockfile_mgr.update_lockfile(
            workspace_dir,
            LockfileAddonEntry(
                addon_id=aid,
                name=aid,
                version="1.0.0",
                target_agent="claude-code",
                integration_type=itype,
            ),
        )

    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mcp-server-1": {"command": "npx", "args": ["@scope/v1"]},
                    "static-mcp": {"command": "npx", "args": ["@scope/static"]},
                }
            }
        ),
        encoding="utf-8",
    )

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_runner:
        mock_runner.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one-v2"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        plan = update_engine.plan_all_updates(
            target_agent=agent,
            scope=Scope.WORKSPACE,
            registry=registry,
        )
        assert len(plan.items) == 2  # mcp-server-1 and skill-addon (static-mcp is skipped)
        updated_ids = {it.addon_id for it in plan.items}
        assert updated_ids == {"mcp-server-1", "skill-addon"}

        res = update_engine.execute_update(
            plan=plan,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )

        assert res.success is True
        assert set(res.updated_addons) == {"mcp-server-1", "skill-addon"}

        # Verify state store records
        rec1 = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec1 is not None and rec1.version == "2.0.0"

        rec2 = state_store.get_record("claude-code", Scope.WORKSPACE, "skill-addon")
        assert rec2 is not None and rec2.version == "2.0.0"

        rec3 = state_store.get_record("claude-code", Scope.WORKSPACE, "static-mcp")
        assert rec3 is not None and rec3.version == "1.0.0"


def test_update_rollback_to_old_version_on_install_failure(tmp_path: Path, monkeypatch) -> None:
    """CRITICAL TEST: Verify that if new version installation fails midway, the entire swap

    rolls back to the OLD version, leaving the original installation completely intact.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Pre-populate v1.0.0 in state, lockfile, and config
    original_config_content = {
        "mcpServers": {
            "mcp-server-1": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-one@1.0.0"],
            }
        }
    }
    (workspace_dir / ".claude.json").write_text(
        json.dumps(original_config_content, indent=2), encoding="utf-8"
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
            installed_files=[".claude.json"],
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

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    plan = update_engine.plan_update(
        addon_id="mcp-server-1",
        target_agent=agent,
        scope=Scope.WORKSPACE,
        registry=registry,
    )

    # Simulate failure during the installation half (e.g. runner fails on new package runtime)
    with patch("aiaddons.core.execution.external.runner.ExternalRunner.resolve_executable") as mock_runner:
        from aiaddons.core.exceptions import ExecutableNotFoundError
        mock_runner.side_effect = ExecutableNotFoundError("NPM package installation timed out")

        res = update_engine.execute_update(
            plan=plan,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )

        # Execution must fail and report rollback
        assert res.success is False
        assert "failed" in (res.error_message or "").lower()

        # CRITICAL VERIFICATION:
        # 1. State record must STILL be at version 1.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
        assert rec is not None
        assert rec.version == "1.0.0"

        # 2. Lockfile must STILL be at version 1.0.0
        entries = lockfile_mgr.get_entries(workspace_dir)
        assert len(entries) == 1
        assert entries[0].version == "1.0.0"

        # 3. .claude.json must STILL have the original v1.0.0 configuration restored
        cfg = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
        assert "mcp-server-1" in cfg["mcpServers"]
        assert "@modelcontextprotocol/server-one@1.0.0" in cfg["mcpServers"]["mcp-server-1"]["args"]


def test_update_dry_run_produces_plan_without_mutations(tmp_path: Path, monkeypatch) -> None:
    """Verify update dry-run produces plan without altering state, config, or lockfile."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
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
    initial_cfg = {"mcpServers": {"mcp-server-1": {"command": "npx", "args": ["@scope/v1"]}}}
    (workspace_dir / ".claude.json").write_text(json.dumps(initial_cfg), encoding="utf-8")

    update_engine = UpdateEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
        workspace_dir=workspace_dir,
    )

    plan = update_engine.plan_update(
        addon_id="mcp-server-1",
        target_agent=agent,
        scope=Scope.WORKSPACE,
        registry=registry,
    )

    res = update_engine.execute_update(
        plan=plan,
        target_agent=agent,
        workspace_dir=workspace_dir,
        registry=registry,
        dry_run=True,
    )

    assert res.success is True
    assert res.is_dry_run is True

    # Confirm config untouched
    cfg_after = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
    assert cfg_after == initial_cfg

    # Confirm state record untouched
    rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mcp-server-1")
    assert rec is not None and rec.version == "1.0.0"
