"""Unit tests for Phase 6 HealthCheckEngine diagnostic checks."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from aiaddons.agents.base import BaseAgentAdapter
from aiaddons.agents.manager import AgentDetectionManager
from aiaddons.core.health.engine import HealthCheckEngine
from aiaddons.core.health.models import HealthStatus
from aiaddons.core.installer.models import (
    InstallationTransaction,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
)
from aiaddons.registry.cache import RegistryCacheManager
from aiaddons.registry.models import RegistryIndex
from aiaddons.state.lockfile import LockfileAddonEntry, LockfileManager
from aiaddons.state.store import InstalledAddonRecord, InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


def _create_sample_manifest(addon_id: str = "test-addon") -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": addon_id,
            "name": "Test Addon",
            "version": "1.0.0",
            "description": "A test addon for health check.",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["global", "workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@test/pkg",
                "checksum": "sha256:" + "a" * 64,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Test Publisher"},
                "allowed_executables": ["npx"],
            },
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/pkg",
                    "env_vars": [],
                }
            },
        }
    )


class MockAgentAdapter(BaseAgentAdapter):
    def __init__(self, agent_id: str, name: str, config_dir: Path) -> None:
        self.agent_id = agent_id
        self.name = name
        self.config_dir = config_dir
        self.capabilities = [AgentCapability.MCP, AgentCapability.SKILL]

    def detect(self, project_path: Path | None = None) -> AgentDetectionResult:
        cfg = self.config_dir / ".claude.json"
        return AgentDetectionResult(
            agent_id=self.agent_id,
            name=self.name,
            installed=True,
            version="1.0.0",
            executable_path="/usr/bin/claude",
            global_config_path=str(cfg),
            workspace_config_path=str(cfg),
            config_path=str(cfg),
            capabilities=self.capabilities,
        )

    def supports_capability(self, capability: AgentCapability) -> bool:
        return capability in self.capabilities

    def get_config_path(self, scope: Scope, project_path: Path | None = None) -> Path:
        return self.config_dir / ".claude.json"

    def get_skill_directory(self, scope: Scope, project_path: Path | None = None) -> Path:
        return self.config_dir / "skills"


def test_healthy_system_all_pass(tmp_path: Path) -> None:
    """Verify that a fully populated and healthy environment passes all checks."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup valid registry cache
    cache_mgr = RegistryCacheManager(cache_dir=store_dir / "registry")
    manifest = _create_sample_manifest("github-mcp")
    index = RegistryIndex(schema_version="1.0", manifests=[manifest])
    cache_mgr.save_cache(index)

    # 2. Setup agent config
    claude_cfg = workspace_dir / ".claude.json"
    claude_cfg.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github-mcp": {
                        "command": "npx",
                        "args": ["-y", "@test/pkg"],
                        "env": {"API_KEY": "${API_KEY}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    # 3. Setup installed state
    state_store = InstalledStateStore(store_dir=store_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="github-mcp",
            name="GitHub MCP",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at=datetime.now(UTC).isoformat(),
            installed_files=[str(claude_cfg)],
        )
    )

    # 4. Setup lockfile consistent with workspace state
    lock_mgr = LockfileManager()
    lock_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="github-mcp",
            name="GitHub MCP",
            version="1.0.0",
            integration_type=IntegrationType.MCP,
            target_agent="claude-code",
        ),
    )

    # 5. Setup adapter
    adapter = MockAgentAdapter("claude-code", "Claude Code", workspace_dir)
    agent_mgr = AgentDetectionManager(adapters=[adapter])

    engine = HealthCheckEngine(
        store_dir=store_dir,
        workspace_dir=workspace_dir,
        agent_manager=agent_mgr,
        registry_url="https://registry.aiaddons.dev/index.json",
    )

    # Mock runtime lookups to simulate available runtimes
    with (
        patch("aiaddons.core.health.engine.find_executable", return_value=Path("/usr/bin/tool")),
        patch("aiaddons.core.health.engine.run_version_command", return_value="1.0.0"),
    ):
        report = engine.run_all_checks()

    assert report.overall_status == HealthStatus.PASS
    assert report.summary["FAIL"] == 0
    assert report.summary["WARN"] == 0
    assert report.summary["PASS"] > 0


def test_missing_registry_cache_warns(tmp_path: Path) -> None:
    """Verify missing registry cache produces a WARN status with remediation."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    engine = HealthCheckEngine(store_dir=store_dir, workspace_dir=workspace_dir)
    items = engine.check_registry()

    exists_items = [i for i in items if i.check_id == "registry_cache_exists"]
    assert len(exists_items) == 1
    assert exists_items[0].status == HealthStatus.WARN
    assert "aiaddons registry update" in (exists_items[0].remediation or "")


def test_malformed_registry_cache_fails(tmp_path: Path) -> None:
    """Verify malformed JSON in registry cache produces a FAIL status."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    reg_dir = store_dir / "registry"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "cache.json").write_text("{corrupted-json...", encoding="utf-8")

    engine = HealthCheckEngine(store_dir=store_dir, workspace_dir=workspace_dir)
    items = engine.check_registry()

    valid_items = [i for i in items if i.check_id == "registry_cache_valid"]
    assert len(valid_items) == 1
    assert valid_items[0].status == HealthStatus.FAIL


def test_insecure_http_registry_url_fails(tmp_path: Path) -> None:
    """Verify insecure remote HTTP URL configured for registry fails."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"

    engine = HealthCheckEngine(
        store_dir=store_dir,
        workspace_dir=workspace_dir,
        registry_url="http://insecure-remote-registry.com/index.json",
    )
    items = engine.check_registry()

    url_items = [i for i in items if i.check_id == "registry_url_secure"]
    assert len(url_items) == 1
    assert url_items[0].status == HealthStatus.FAIL


def test_malformed_state_file_fails(tmp_path: Path) -> None:
    """Verify malformed state.json produces a FAIL status."""
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "state.json").write_text("invalid json content", encoding="utf-8")

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_installed_state()

    valid_items = [i for i in items if i.check_id == "state_file_valid"]
    assert len(valid_items) == 1
    assert valid_items[0].status == HealthStatus.FAIL


def test_invalid_records_in_state_fails(tmp_path: Path) -> None:
    """Verify invalid record structure in state.json produces a FAIL status."""
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    db_json = {
        "version": "1.0",
        "records": {
            "bad:entry": {
                "addon_id": "bad;injection",
                "name": "Bad Addon",
                "version": "1.0.0",
                "target_agent": "claude-code",
                "scope": "workspace",
                "integration_type": "mcp",
                "installed_at": "2026-08-15T00:00:00Z",
                "installed_files": [],
            }
        },
    }
    (store_dir / "state.json").write_text(json.dumps(db_json), encoding="utf-8")

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_installed_state()

    records_items = [i for i in items if i.check_id == "state_records_valid"]
    assert len(records_items) == 1
    assert records_items[0].status == HealthStatus.FAIL


def test_malformed_lockfile_fails(tmp_path: Path) -> None:
    """Verify malformed YAML in aiaddons.lock produces a FAIL status."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "aiaddons.lock").write_text(": [invalid yaml", encoding="utf-8")

    engine = HealthCheckEngine(workspace_dir=workspace_dir)
    items = engine.check_lockfile()

    valid_items = [i for i in items if i.check_id == "lockfile_valid"]
    assert len(valid_items) == 1
    assert valid_items[0].status == HealthStatus.FAIL


def test_inconsistent_lockfile_warns(tmp_path: Path) -> None:
    """Verify lockfile containing an uninstalled add-on produces a WARN status."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    lock_mgr = LockfileManager()
    lock_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="uninstalled-addon",
            name="Uninstalled Addon",
            version="1.0.0",
            integration_type=IntegrationType.MCP,
            target_agent="claude-code",
        ),
    )

    engine = HealthCheckEngine(store_dir=store_dir, workspace_dir=workspace_dir)
    items = engine.check_lockfile()

    cons_items = [i for i in items if i.check_id == "lockfile_state_consistency"]
    assert len(cons_items) == 1
    assert cons_items[0].status == HealthStatus.WARN


def test_malformed_wal_file_fails(tmp_path: Path) -> None:
    """Verify corrupted WAL transaction log produces a FAIL status."""
    store_dir = tmp_path / "store"
    tx_dir = store_dir / "transactions"
    tx_dir.mkdir(parents=True, exist_ok=True)
    (tx_dir / "tx_corrupted.json").write_text("{corrupt wal", encoding="utf-8")

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_transactions()

    valid_items = [i for i in items if i.check_id == "transactions_wal_valid"]
    assert len(valid_items) == 1
    assert valid_items[0].status == HealthStatus.FAIL


def test_interrupted_transaction_warns(tmp_path: Path) -> None:
    """Verify interrupted transaction left in EXECUTING phase produces a WARN status."""
    store_dir = tmp_path / "store"
    tx_dir = store_dir / "transactions"
    wal_mgr = TransactionWALManager(transactions_dir=tx_dir)

    sample_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        capabilities=[AgentCapability.MCP],
    )
    tx = InstallationTransaction(
        transaction_id="tx_interrupted_123",
        manifest=_create_sample_manifest("test-mcp"),
        agent=sample_agent,
        requested_scope=Scope.WORKSPACE,
        phase=TransactionPhase.EXECUTING,
    )
    wal_mgr.write_transaction(tx)

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_transactions()

    interrupted_items = [i for i in items if i.check_id == "transactions_interrupted"]
    assert len(interrupted_items) == 1
    assert interrupted_items[0].status == HealthStatus.WARN
    assert "tx_interrupted_123" in str(interrupted_items[0].diagnostic_details)


def test_orphaned_staging_directory_warns(tmp_path: Path) -> None:
    """Verify staging directory from completed transaction produces a WARN status."""
    store_dir = tmp_path / "store"
    staging_root = store_dir / "staging"
    tx_dir = store_dir / "transactions"
    wal_mgr = TransactionWALManager(transactions_dir=tx_dir)

    sample_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        capabilities=[AgentCapability.SKILL],
    )
    # Write committed transaction
    tx = InstallationTransaction(
        transaction_id="tx_done_456",
        manifest=_create_sample_manifest("test-skill"),
        agent=sample_agent,
        requested_scope=Scope.WORKSPACE,
        phase=TransactionPhase.COMMITTED,
    )
    wal_mgr.write_transaction(tx)

    # Orphaned folder remains in staging
    orphaned_folder = staging_root / "tx_done_456"
    orphaned_folder.mkdir(parents=True, exist_ok=True)

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_staging()

    orphaned_items = [i for i in items if i.check_id == "staging_orphaned"]
    assert len(orphaned_items) == 1
    assert orphaned_items[0].status == HealthStatus.WARN
    assert "tx_done_456" in str(orphaned_items[0].diagnostic_details)


def test_unsafe_staging_path_fails(tmp_path: Path) -> None:
    """Verify staging directory with unsafe name produces a FAIL status."""
    store_dir = tmp_path / "store"
    staging_root = store_dir / "staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    (staging_root / "bad;name").mkdir(parents=True, exist_ok=True)

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_staging()

    safety_items = [i for i in items if i.check_id == "staging_path_safety"]
    assert len(safety_items) == 1
    assert safety_items[0].status == HealthStatus.FAIL


def test_missing_runtime_warns(tmp_path: Path) -> None:
    """Verify unavailable runtimes return WARN status with clear remediation."""
    engine = HealthCheckEngine(store_dir=tmp_path)

    with patch("aiaddons.core.health.engine.find_executable", return_value=None):
        items = engine.check_runtimes()

    python_item = next(i for i in items if i.check_id == "runtime_python")
    assert python_item.status == HealthStatus.PASS  # Python is running the test

    git_item = next(i for i in items if i.check_id == "runtime_git")
    assert git_item.status == HealthStatus.WARN
    assert "Install git" in (git_item.remediation or "")


def test_inconsistent_installed_mcp_fails(tmp_path: Path) -> None:
    """Verify state claiming MCP installed but missing from agent config fails."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    state_store = InstalledStateStore(store_dir=store_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="missing-mcp",
            name="Missing MCP",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at=datetime.now(UTC).isoformat(),
        )
    )

    # Empty agent config
    claude_cfg = workspace_dir / ".claude.json"
    claude_cfg.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    adapter = MockAgentAdapter("claude-code", "Claude Code", workspace_dir)
    agent_mgr = AgentDetectionManager(adapters=[adapter])

    engine = HealthCheckEngine(
        store_dir=store_dir, workspace_dir=workspace_dir, agent_manager=agent_mgr
    )
    items = engine.check_consistency()

    mcp_items = [i for i in items if i.check_id == "consistency_missing-mcp_mcp"]
    assert len(mcp_items) == 1
    assert mcp_items[0].status == HealthStatus.FAIL


def test_inconsistent_installed_skill_fails(tmp_path: Path) -> None:
    """Verify state claiming Skill installed but missing on disk fails."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    state_store = InstalledStateStore(store_dir=store_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="missing-skill",
            name="Missing Skill",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.SKILL,
            installed_at=datetime.now(UTC).isoformat(),
        )
    )

    adapter = MockAgentAdapter("claude-code", "Claude Code", workspace_dir)
    agent_mgr = AgentDetectionManager(adapters=[adapter])

    engine = HealthCheckEngine(
        store_dir=store_dir, workspace_dir=workspace_dir, agent_manager=agent_mgr
    )
    items = engine.check_consistency()

    skill_items = [i for i in items if i.check_id == "consistency_missing-skill_skill"]
    assert len(skill_items) == 1
    assert skill_items[0].status == HealthStatus.FAIL


def test_security_check_path_traversal_fails(tmp_path: Path) -> None:
    """Verify path traversal in installed record files fails security check."""
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    db_json = {
        "version": "1.0",
        "records": {
            "test:item": {
                "addon_id": "test-addon",
                "name": "Test",
                "version": "1.0.0",
                "target_agent": "claude-code",
                "scope": "workspace",
                "integration_type": "mcp",
                "installed_at": "2026-08-15T00:00:00Z",
                "installed_files": ["../../etc/passwd"],
            }
        },
    }
    (store_dir / "state.json").write_text(json.dumps(db_json), encoding="utf-8")

    engine = HealthCheckEngine(store_dir=store_dir)
    items = engine.check_security()

    path_items = [i for i in items if i.check_id == "security_paths"]
    assert len(path_items) == 1
    assert path_items[0].status == HealthStatus.FAIL


def test_read_only_guarantee_no_mutations(tmp_path: Path) -> None:
    """Verify HealthCheckEngine is 100% read-only and mutates zero files."""
    store_dir = tmp_path / "store"
    workspace_dir = tmp_path / "workspace"
    store_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Populate files
    cache_mgr = RegistryCacheManager(cache_dir=store_dir / "registry")
    cache_mgr.save_cache(RegistryIndex(schema_version="1.0", manifests=[]))

    state_store = InstalledStateStore(store_dir=store_dir)
    state_store.record_installation(
        InstalledAddonRecord(
            addon_id="existing-mcp",
            name="Existing MCP",
            version="1.0.0",
            target_agent="claude-code",
            scope=Scope.WORKSPACE,
            integration_type=IntegrationType.MCP,
            installed_at="2026-08-15T00:00:00Z",
        )
    )

    lock_mgr = LockfileManager()
    lock_mgr.update_lockfile(
        workspace_dir,
        LockfileAddonEntry(
            addon_id="existing-mcp",
            name="Existing MCP",
            version="1.0.0",
            integration_type=IntegrationType.MCP,
            target_agent="claude-code",
        ),
    )

    # Compute snapshot hashes of all created files
    def _snapshot() -> dict[str, str]:
        hashes: dict[str, str] = {}
        for p in sorted(tmp_path.rglob("*")):
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                hashes[str(p.relative_to(tmp_path))] = h
        return hashes

    before_snapshot = _snapshot()
    assert len(before_snapshot) > 0

    engine = HealthCheckEngine(store_dir=store_dir, workspace_dir=workspace_dir)
    engine.run_all_checks()

    after_snapshot = _snapshot()
    assert before_snapshot == after_snapshot
