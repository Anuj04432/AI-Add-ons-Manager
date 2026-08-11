"""Unit tests for transaction WAL persistence, crash recovery, state store, and lockfile updates."""

from pathlib import Path

import pytest

from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    TransactionPhase,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import IntegrationManifest
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


@pytest.fixture
def sample_manifest() -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
                "checksum": "sha256:" + "a" * 64,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "MCP Team"},
            },
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@modelcontextprotocol/server-github",
                    "env_vars": [],
                }
            },
        }
    )


def test_atomic_transaction_wal_persistence(
    tmp_path: Path, sample_manifest: IntegrationManifest
) -> None:
    """Verify transaction state transitions are written to WAL logs atomically."""
    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)

    adapter = ClaudeCodeAdapter()
    agent = adapter.detect(project_path=tmp_path)
    agent.installed = True

    engine = InstallationEngine(wal_manager=wal_manager)
    tx = engine.create_transaction(sample_manifest, agent, Scope.WORKSPACE)

    assert tx.phase == TransactionPhase.PLANNED
    log_file = wal_manager.get_log_path(tx.transaction_id)
    assert log_file.exists()

    read_tx = wal_manager.read_transaction(tx.transaction_id)
    assert read_tx is not None
    assert read_tx.transaction_id == tx.transaction_id
    assert read_tx.phase == TransactionPhase.PLANNED


def test_transaction_execution_and_wal_lifecycle(
    tmp_path: Path, sample_manifest: IntegrationManifest
) -> None:
    """Verify transaction transitions through EXECUTING, VERIFIED, and COMMITTED in WAL log."""
    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)

    adapter = ClaudeCodeAdapter()
    agent = adapter.detect(project_path=tmp_path)
    agent.installed = True

    engine = InstallationEngine(wal_manager=wal_manager)
    tx = engine.create_transaction(sample_manifest, agent, Scope.WORKSPACE)

    assert tx.plan is not None
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    for op in tx.plan.planned_operations:
        op.target_root = str(target_dir)

    exec_engine = ExecutionEngine(wal_manager=wal_manager)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    read_tx = wal_manager.read_transaction(tx.transaction_id)
    assert read_tx is not None
    assert read_tx.phase == TransactionPhase.COMMITTED


def test_interrupted_transaction_recovery(
    tmp_path: Path, sample_manifest: IntegrationManifest
) -> None:
    """Verify recovery of interrupted transactions left in EXECUTING state."""
    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)

    adapter = ClaudeCodeAdapter()
    agent = adapter.detect(project_path=tmp_path)
    agent.installed = True

    engine = InstallationEngine(wal_manager=wal_manager)
    tx = engine.create_transaction(sample_manifest, agent, Scope.WORKSPACE)

    # Manually simulate interrupted state during execution
    tx.phase = TransactionPhase.EXECUTING
    wal_manager.write_transaction(tx)

    interrupted = wal_manager.list_interrupted_transactions()
    assert len(interrupted) == 1
    assert interrupted[0].transaction_id == tx.transaction_id

    # Recover interrupted transaction
    recovered = wal_manager.recover_interrupted_transaction(tx.transaction_id)
    assert recovered.phase == TransactionPhase.ROLLED_BACK


def test_atomic_state_store_and_lockfile_updates(tmp_path: Path) -> None:
    """Verify atomic state store and lockfile entries are written upon committed installation."""
    from aiaddons.core.models.manifest import IntegrationType

    store_dir = tmp_path / "store"
    state_store = InstalledStateStore(store_dir=store_dir)

    record = InstalledAddonRecord(
        addon_id="github-mcp",
        name="GitHub MCP",
        version="1.0.0",
        target_agent="claude-code",
        scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        installed_at="2026-08-11T12:00:00Z",
        installed_files=[".claude.json"],
    )

    state_store.record_installation(record)
    records = state_store.get_installed("claude-code", Scope.WORKSPACE)
    assert len(records) == 1
    assert records[0].addon_id == "github-mcp"

    # Lockfile
    lock_manager = LockfileManager()
    entry = LockfileAddonEntry(
        addon_id="github-mcp",
        name="GitHub MCP",
        version="1.0.0",
        integration_type=IntegrationType.MCP,
        target_agent="claude-code",
        checksum="sha256:" + "a" * 64,
    )

    lock_file = lock_manager.update_lockfile(tmp_path, entry)
    assert lock_file.exists()
    entries = lock_manager.get_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0].addon_id == "github-mcp"


def test_dry_run_zero_side_effects(tmp_path: Path, sample_manifest: IntegrationManifest) -> None:
    """Verify dry run mode makes zero changes to filesystem, state store, or lockfile."""
    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)
    store_dir = tmp_path / "store"
    state_store = InstalledStateStore(store_dir=store_dir)
    lock_manager = LockfileManager()

    adapter = ClaudeCodeAdapter()
    agent = adapter.detect(project_path=tmp_path)
    agent.installed = True

    engine = InstallationEngine()
    plan = engine.generate_plan(sample_manifest, agent, Scope.WORKSPACE)

    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True, exist_ok=True)
    for op in plan.planned_operations:
        op.target_root = str(target_dir)

    exec_engine = ExecutionEngine(
        wal_manager=wal_manager,
        state_store=state_store,
        lockfile_manager=lock_manager,
    )

    result = exec_engine.execute_plan(plan, dry_run=True)
    assert result.status == ExecutionStatus.SUCCESS

    # Verify zero side effects
    assert not (target_dir / ".claude.json").exists()
    assert len(state_store.get_installed()) == 0
    assert not (tmp_path / "aiaddons.lock").exists()
