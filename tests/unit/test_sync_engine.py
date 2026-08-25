"""Unit tests for SyncEngine domain reconciliation logic (Phase 6E)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from filelock import FileLock

from aiaddons.core.exceptions import (
    LockfileNotFoundError,
    ManifestValidationError,
    StateLockTimeoutError,
    SyncError,
    SyncVerificationError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationType
from aiaddons.core.sync.engine import SyncEngine
from aiaddons.core.sync.models import (
    SyncDiff,
    SyncPlan,
    SyncResult,
    SyncStatus,
    SyncTargetSpec,
)
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

    # 1. MCP server v1.0.0
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

    # 2. Skill v1.0.0
    (reg_dir / "skill-addon.json").write_text(
        """{
            "id": "skill-addon",
            "name": "Skill Addon",
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

    # 3. Mismatched server v2.0.0 in registry
    (reg_dir / "mismatched-mcp.json").write_text(
        f"""{{
            "id": "mismatched-mcp",
            "name": "Mismatched MCP Server",
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

    reg, _ = Registry.from_directory(reg_dir)
    return reg


def test_sync_engine_clean_when_already_in_sync(tmp_path: Path) -> None:
    """Verify SyncEngine detects clean in-sync state when all lockfile items match installed store."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    state_dir.mkdir(parents=True, exist_ok=True)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Pre-populate state and lockfile
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

    lockfile_entry = LockfileAddonEntry(
        addon_id="mcp-server-1",
        name="MCP Server One",
        version="1.0.0",
        target_agent="claude-code",
        integration_type=IntegrationType.MCP,
        installed_at="2026-08-25T12:00:00Z",
    )
    lockfile_mgr.update_lockfile(workspace_dir, lockfile_entry)

    sync_engine = SyncEngine(state_store=state_store, lockfile_manager=lockfile_mgr)
    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code")
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code")

    assert diff.is_clean is True
    assert len(diff.missing) == 0
    assert len(diff.extra) == 0
    assert len(diff.mismatched) == 0
    assert len(diff.synced) == 1
    assert diff.synced[0].addon_id == "mcp-server-1"


def test_sync_engine_detects_missing_addons(tmp_path: Path) -> None:
    """Verify SyncEngine detects missing add-ons in lockfile that are not installed locally."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    lockfile_entry = LockfileAddonEntry(
        addon_id="mcp-server-1",
        name="MCP Server One",
        version="1.0.0",
        target_agent="claude-code",
        integration_type=IntegrationType.MCP,
    )
    lockfile_mgr.update_lockfile(workspace_dir, lockfile_entry)

    sync_engine = SyncEngine(state_store=state_store, lockfile_manager=lockfile_mgr)
    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code")
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code")

    assert diff.is_clean is False
    assert len(diff.missing) == 1
    assert diff.missing[0].addon_id == "mcp-server-1"
    assert diff.missing[0].status == SyncStatus.MISSING
    assert len(diff.extra) == 0
    assert len(diff.mismatched) == 0


def test_sync_engine_detects_extra_unmanaged_addons(tmp_path: Path) -> None:
    """Verify SyncEngine detects extra add-ons installed locally that are not in lockfile."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Lockfile has mcp-server-1
    lockfile_entry = LockfileAddonEntry(
        addon_id="mcp-server-1",
        name="MCP Server One",
        version="1.0.0",
        target_agent="claude-code",
        integration_type=IntegrationType.MCP,
    )
    lockfile_mgr.update_lockfile(workspace_dir, lockfile_entry)

    # Installed store has mcp-server-1 AND extra-unmanaged
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
            name="Extra Addon",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )

    sync_engine = SyncEngine(state_store=state_store, lockfile_manager=lockfile_mgr)
    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code")
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code")

    assert diff.is_clean is False
    assert len(diff.missing) == 0
    assert len(diff.synced) == 1
    assert len(diff.extra) == 1
    assert diff.extra[0].addon_id == "extra-unmanaged"
    assert diff.extra[0].status == SyncStatus.EXTRA


def test_sync_engine_detects_version_mismatch(tmp_path: Path) -> None:
    """Verify SyncEngine detects version mismatches between lockfile and local store."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Lockfile has version 2.0.0
    lockfile_entry = LockfileAddonEntry(
        addon_id="mismatched-mcp",
        name="Mismatched MCP",
        version="2.0.0",
        target_agent="claude-code",
        integration_type=IntegrationType.MCP,
    )
    lockfile_mgr.update_lockfile(workspace_dir, lockfile_entry)

    # Local store has version 1.0.0
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

    sync_engine = SyncEngine(state_store=state_store, lockfile_manager=lockfile_mgr)
    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code")
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code")

    assert diff.is_clean is False
    assert len(diff.missing) == 0
    assert len(diff.extra) == 0
    assert len(diff.mismatched) == 1
    assert diff.mismatched[0].addon_id == "mismatched-mcp"
    assert diff.mismatched[0].installed_version == "1.0.0"
    assert diff.mismatched[0].expected_version == "2.0.0"
    assert diff.mismatched[0].status == SyncStatus.MISMATCHED


def test_sync_engine_install_missing_execution(tmp_path: Path, monkeypatch) -> None:
    """Verify SyncEngine executes installation of missing add-ons in batch mode."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    (workspace_dir / "SKILL.md").write_text("# Skill Addon", encoding="utf-8")
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Lockfile specifies mcp-server-1 and skill-addon
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
            addon_id="skill-addon",
            name="Skill Addon",
            version="1.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.SKILL,
        ),
    )

    sync_engine = SyncEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_runner:
        mock_runner.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-one"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code", registry)
        diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code", registry=registry)
        assert len(diff.missing) == 2

        plan = sync_engine.generate_sync_plan(diff, agent, Scope.WORKSPACE, registry=registry)
        result = sync_engine.execute_sync(
            plan=plan,
            expected_specs=expected_specs,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )

        assert result.success is True
        assert result.in_sync is True
        assert len(result.installed_addons) == 2
        assert "mcp-server-1" in result.installed_addons
        assert "skill-addon" in result.installed_addons

        # Verify state store records
        installed = state_store.get_installed("claude-code", Scope.WORKSPACE)
        assert len(installed) == 2


def test_sync_engine_prune_unmanaged(tmp_path: Path, monkeypatch) -> None:
    """Verify SyncEngine with prune=True removes unmanaged add-ons."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Pre-populate state store with mcp-server-1 and extra-mcp
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
            addon_id="extra-mcp",
            name="Extra MCP Server",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-25T12:00:00Z",
        )
    )
    # Config file with both
    (workspace_dir / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mcp-server-1": {"command": "npx", "args": ["@scope/pkg1"]},
                    "extra-mcp": {"command": "npx", "args": ["@scope/extra"]},
                }
            }
        ),
        encoding="utf-8",
    )

    # Lockfile contains only mcp-server-1
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

    sync_engine = SyncEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
    )

    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code", registry)
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code", registry=registry)
    assert len(diff.extra) == 1

    # Plan with prune=True
    plan = sync_engine.generate_sync_plan(diff, agent, Scope.WORKSPACE, prune=True, registry=registry)
    assert len(plan.to_prune) == 1

    result = sync_engine.execute_sync(
        plan=plan,
        expected_specs=expected_specs,
        target_agent=agent,
        workspace_dir=workspace_dir,
        registry=registry,
        dry_run=False,
    )

    assert result.success is True
    assert result.in_sync is True
    assert result.pruned_addons == ["extra-mcp"]

    # Verify state store no longer has extra-mcp
    assert state_store.get_record("claude-code", Scope.WORKSPACE, "extra-mcp") is None
    # Verify config no longer has extra-mcp
    cfg = json.loads((workspace_dir / ".claude.json").read_text(encoding="utf-8"))
    assert "extra-mcp" not in cfg["mcpServers"]
    assert "mcp-server-1" in cfg["mcpServers"]


def test_sync_engine_update_version_mismatch(tmp_path: Path, monkeypatch) -> None:
    """Verify SyncEngine with update=True reconciles version mismatches."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)
    monkeypatch.chdir(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir)
    lockfile_mgr = LockfileManager()

    # Old version 1.0.0 installed locally
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="mismatched-mcp",
            name="Mismatched MCP Server",
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

    # Lockfile has version 2.0.0 (which registry also provides)
    lockfile_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="mismatched-mcp",
            name="Mismatched MCP Server",
            version="2.0.0",
            target_agent="claude-code",
            integration_type=IntegrationType.MCP,
        ),
    )

    sync_engine = SyncEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
    )

    with patch("aiaddons.core.execution.external.runner.ExternalRunner.execute") as mock_runner:
        mock_runner.return_value = ExternalExecutionResult(
            success=True,
            runtime=ExternalRuntime.NPX,
            executable_path="npx",
            command_vector=["npx", "-y", "@modelcontextprotocol/server-mismatched"],
            return_code=0,
            stdout="OK",
            stderr="",
            duration=0.1,
        )

        expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code", registry)
        diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code", registry=registry)
        assert len(diff.mismatched) == 1

        plan = sync_engine.generate_sync_plan(
            diff, agent, Scope.WORKSPACE, update=True, registry=registry
        )
        assert len(plan.to_update) == 1

        result = sync_engine.execute_sync(
            plan=plan,
            expected_specs=expected_specs,
            target_agent=agent,
            workspace_dir=workspace_dir,
            registry=registry,
            dry_run=False,
        )

        assert result.success is True
        assert result.in_sync is True
        assert result.updated_addons == ["mismatched-mcp"]

        # Verify state store now has version 2.0.0
        rec = state_store.get_record("claude-code", Scope.WORKSPACE, "mismatched-mcp")
        assert rec is not None
        assert rec.version == "2.0.0"


def test_sync_engine_file_locking_protection(tmp_path: Path) -> None:
    """Verify SyncEngine raises StateLockTimeoutError when state or lockfile is locked by another process."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / ".aiaddons"
    registry = _create_sample_registry(tmp_path)
    agent = _mock_claude_agent(workspace_dir)

    state_store = InstalledStateStore(store_dir=state_dir, lock_timeout=0.1)
    lockfile_mgr = LockfileManager(lock_timeout=0.1)

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

    sync_engine = SyncEngine(
        state_store=state_store,
        lockfile_manager=lockfile_mgr,
        registry=registry,
    )

    expected_specs = sync_engine.load_expected_specs(None, workspace_dir, "claude-code", registry)
    diff = sync_engine.compute_diff(expected_specs, workspace_dir, "claude-code", registry=registry)
    plan = sync_engine.generate_sync_plan(diff, agent, Scope.WORKSPACE, registry=registry)

    # Hold the lockfile lock externally
    lock_file = lockfile_mgr.get_lock_path(workspace_dir)
    holding_lock = FileLock(str(lock_file), is_singleton=False)
    holding_lock.acquire()

    try:
        with pytest.raises(StateLockTimeoutError):
            sync_engine.execute_sync(
                plan=plan,
                expected_specs=expected_specs,
                target_agent=agent,
                workspace_dir=workspace_dir,
                registry=registry,
                dry_run=False,
            )
    finally:
        holding_lock.release()
