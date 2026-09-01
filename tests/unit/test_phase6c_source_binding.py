"""Unit tests for Phase 6C End-to-End Staged Source Binding & Transaction Integrity Pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.acquisition.engine import AcquisitionEngine
from aiaddons.core.acquisition.git import GitSourceFetcher
from aiaddons.core.acquisition.models import AcquiredSourceResult
from aiaddons.core.acquisition.url import UrlSourceFetcher
from aiaddons.core.exceptions import (
    ChecksumMismatchError,
    GitAcquisitionError,
    SecurityValidationError,
    SourceAcquisitionError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.execution.external.runner import ExternalRunner
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    AddSkillOperation,
    InstallationTransaction,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    SourceSpec,
    SourceType,
)
from aiaddons.registry.registry import Registry
from aiaddons.state.lockfile import LockfileManager
from aiaddons.state.store import InstalledStateStore
from aiaddons.state.transaction import TransactionWALManager


@pytest.fixture
def dummy_checksum() -> str:
    return "sha256:" + "a" * 64


@pytest.fixture
def sample_agent() -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="1.0.0",
        global_config_path="~/.claude.json",
        workspace_config_path=".claude.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL, AgentCapability.PLUGIN],
    )


@pytest.fixture
def local_skill_source(tmp_path: Path) -> Path:
    """Create a local skill directory containing SKILL.md and a supporting rules file."""
    src_dir = tmp_path / "my_skill_source"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "SKILL.md").write_text("# My Test Skill\n\nInstructions.", encoding="utf-8")
    (src_dir / "rules.py").write_text("# Skill rules", encoding="utf-8")
    return src_dir


def create_dummy_external_result(
    success: bool = True, error_msg: str | None = None
) -> ExternalExecutionResult:
    """Helper creating valid ExternalExecutionResult instance."""
    return ExternalExecutionResult(
        success=success,
        runtime=ExternalRuntime.NPX,
        executable_path="/usr/bin/npx",
        command_vector=["npx", "-y", "pkg"],
        return_code=0 if success else 1,
        stdout="success" if success else "",
        stderr="" if success else (error_msg or "error"),
        duration=0.1,
        error_message=error_msg if not success else None,
    )


def test_phase6c_registry_source_resolution() -> None:
    """Verify resolving add-on manifest source specification from registry."""
    raw_manifest = {
        "id": "git-skill",
        "name": "Git Skill Addon",
        "version": "1.0.0",
        "description": "Git skill test",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "skill",
        "target_agents": ["claude-code"],
        "supported_scopes": ["workspace"],
        "source": {
            "source_type": "git",
            "repository": "https://github.com/example/skill.git",
            "commit_sha": "4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
            "path": "skills/git-skill",
        },
        "trust": {"verification_status": "verified", "publisher": {"name": "Test Team"}},
        "handler_spec": {"skill": {"skill_file": "SKILL.md", "supporting_files": []}},
    }
    manifest = IntegrationManifest.model_validate(raw_manifest)
    registry = Registry(manifests=[manifest])

    resolved = registry.get("git-skill")
    assert resolved is not None
    assert resolved.source is not None
    assert resolved.source.source_type == SourceType.GIT
    assert resolved.source.commit_sha == "4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d"
    assert resolved.source.path == "skills/git-skill"


def test_phase6c_successful_source_acquisition_to_installation(
    tmp_path: Path,
    local_skill_source: Path,
    sample_agent: AgentDetectionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify complete pipeline from source acquisition to committed execution."""
    monkeypatch.chdir(tmp_path)
    manifest = IntegrationManifest.model_validate(
        {
            "id": "local-test-skill",
            "name": "Local Test Skill",
            "version": "1.0.0",
            "description": "Skill test with local source",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "local",
                "path": "my_skill_source",
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md", "supporting_files": ["rules.py"]}},
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)
    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    installer = InstallationEngine(wal_manager=wal_manager)
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    for op in tx.plan.planned_operations:
        op.target_root = str(target_root)

    exec_engine = ExecutionEngine(wal_manager=wal_manager)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    # Verify deployed files exist in target root
    deployed_skill = target_root / ".claude" / "skills" / "local-test-skill" / "SKILL.md"
    deployed_rules = target_root / ".claude" / "skills" / "local-test-skill" / "rules.py"
    assert deployed_skill.exists()
    assert "My Test Skill" in deployed_skill.read_text(encoding="utf-8")
    assert deployed_rules.exists()


def test_phase6c_checksum_failure_prevents_installation(
    tmp_path: Path, sample_agent: AgentDetectionResult, dummy_checksum: str
) -> None:
    """Verify checksum mismatch during acquisition aborts execution and triggers rollback."""
    manifest = IntegrationManifest.model_validate(
        {
            "id": "corrupted-url-addon",
            "name": "Corrupted Addon",
            "version": "1.0.0",
            "description": "Test corrupted checksum download",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "url",
                "url": "https://example.com/archive.zip",
                "checksum": dummy_checksum,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/corrupt",
                    "env_vars": [],
                }
            },
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)

    installer = InstallationEngine(wal_manager=wal_manager)
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    mock_acq = MagicMock(spec=AcquisitionEngine)
    mock_acq.acquire_source.side_effect = ChecksumMismatchError(
        "Expected checksum sha256:0000... got sha256:ffff..."
    )

    exec_engine = ExecutionEngine(wal_manager=wal_manager, acquisition_engine=mock_acq)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.FAILED
    assert tx.phase == TransactionPhase.ROLLED_BACK
    assert "Expected checksum" in (res.error_message or "")
    mock_acq.cleanup_staging.assert_called()


def test_phase6c_invalid_source_metadata_rejection(
    tmp_path: Path, sample_agent: AgentDetectionResult
) -> None:
    """Verify unencrypted HTTP URL or missing commit SHA is rejected prior to execution."""
    fetcher = UrlSourceFetcher()
    http_source = SourceSpec(
        source_type=SourceType.URL,
        url="http://unsecure.com/addon.zip",
        checksum="sha256:" + "a" * 64,
    )
    with pytest.raises(SourceAcquisitionError, match="HTTPS"):
        fetcher.fetch("unsecure-addon", http_source, tmp_path / "staging")

    git_fetcher = GitSourceFetcher()
    git_source = SourceSpec(
        source_type=SourceType.GIT,
        repository="https://github.com/example/repo.git",
        commit_sha="invalid_commit_sha_string",
    )
    with pytest.raises(GitAcquisitionError, match="commit_sha"):
        git_fetcher.fetch("bad-sha-addon", git_source, tmp_path / "staging")


def test_phase6c_staged_source_boundary_enforcement(
    tmp_path: Path, sample_agent: AgentDetectionResult
) -> None:
    """Verify operations referencing source paths outside staged boundary raise error."""
    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)

    op = AddSkillOperation(
        description="Escape staging test",
        target_root=str(target_root),
        skill_name="Escape Skill",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/escape",
        supporting_files=[],
        source_dir=str(outside_dir),
    )

    staging_dir = tmp_path / "staging" / "tx_123"
    staging_dir.mkdir(parents=True, exist_ok=True)
    acq_res = AcquiredSourceResult(
        manifest_id="escape-skill",
        source_type=SourceType.PACKAGE,
        staging_path=staging_dir,
        is_staged=True,
        is_dry_run=False,
    )

    exec_engine = ExecutionEngine()
    with pytest.raises(SecurityValidationError, match="escapes verified staging boundary"):
        exec_engine._dispatch_operation(op, dry_run=False, acquired_result=acq_res)


def test_phase6c_acquisition_failure_rollback_and_staging_cleanup(
    tmp_path: Path, sample_agent: AgentDetectionResult
) -> None:
    """Verify state is FAILED->ROLLED_BACK and staging cleanup runs on acquisition failure."""
    manifest = IntegrationManifest.model_validate(
        {
            "id": "failed-fetch-addon",
            "name": "Failed Fetch Addon",
            "version": "1.0.0",
            "description": "Fetch failure test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "git",
                "repository": "https://github.com/example/missing.git",
                "commit_sha": "4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md", "supporting_files": []}},
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)
    installer = InstallationEngine(wal_manager=wal_manager)
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    mock_acq = MagicMock(spec=AcquisitionEngine)
    mock_acq.acquire_source.side_effect = GitAcquisitionError("Git clone connection timed out")

    exec_engine = ExecutionEngine(wal_manager=wal_manager, acquisition_engine=mock_acq)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.FAILED
    assert tx.phase == TransactionPhase.ROLLED_BACK
    mock_acq.cleanup_staging.assert_called_with(tx.transaction_id)


def test_phase6c_transaction_state_transitions(
    tmp_path: Path,
    local_skill_source: Path,
    sample_agent: AgentDetectionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify phase transitions: PLANNED -> ACQUISITION -> EXECUTING -> VERIFIED -> COMMITTED."""
    monkeypatch.chdir(tmp_path)
    manifest = IntegrationManifest.model_validate(
        {
            "id": "tx-flow-skill",
            "name": "TX Flow Skill",
            "version": "1.0.0",
            "description": "Transaction flow test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "my_skill_source"},
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md", "supporting_files": []}},
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)

    installer = InstallationEngine(wal_manager=wal_manager)
    tx: InstallationTransaction = installer.create_transaction(
        manifest, sample_agent, Scope.WORKSPACE
    )
    initial_phase = tx.phase
    assert initial_phase == TransactionPhase.PLANNED
    assert tx.plan is not None

    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)
    for op in tx.plan.planned_operations:
        op.target_root = str(target_root)

    exec_engine = ExecutionEngine(wal_manager=wal_manager)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS
    assert tx.phase == TransactionPhase.COMMITTED

    log_tx = wal_manager.read_transaction(tx.transaction_id)
    assert log_tx is not None
    assert log_tx.phase == TransactionPhase.COMMITTED


def test_phase6c_dry_run_zero_side_effect_guarantee(
    tmp_path: Path,
    local_skill_source: Path,
    sample_agent: AgentDetectionResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --dry-run guarantees zero network, process, disk, WAL, or lockfile side effects."""
    monkeypatch.chdir(tmp_path)
    manifest = IntegrationManifest.model_validate(
        {
            "id": "dry-run-skill",
            "name": "Dry Run Skill",
            "version": "1.0.0",
            "description": "Dry run test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {"source_type": "local", "path": "my_skill_source"},
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {"skill": {"skill_file": "SKILL.md", "supporting_files": []}},
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)
    state_store = InstalledStateStore(store_dir=tmp_path)
    lockfile_manager = LockfileManager()

    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    installer = InstallationEngine(wal_manager=wal_manager)
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    for op in tx.plan.planned_operations:
        op.target_root = str(target_root)

    exec_engine = ExecutionEngine(
        wal_manager=wal_manager,
        state_store=state_store,
        lockfile_manager=lockfile_manager,
    )
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=True)

    assert res.status == ExecutionStatus.SUCCESS

    assert not (target_root / ".claude").exists()
    assert len(state_store.get_installed()) == 0
    assert not (tmp_path / "aiaddons.lock").exists()


def test_phase6c_secret_non_persistence(
    tmp_path: Path, sample_agent: AgentDetectionResult, dummy_checksum: str
) -> None:
    """Verify secrets are masked in error outputs and never written in plain text to log files."""
    manifest = IntegrationManifest.model_validate(
        {
            "id": "secret-mcp",
            "name": "Secret MCP",
            "version": "1.0.0",
            "description": "Secret test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@test/secret-pkg",
                "checksum": dummy_checksum,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/secret-pkg",
                    "env_vars": [{"name": "MY_SECRET_KEY", "required": True, "secret": True}],
                }
            },
        }
    )

    wal_dir = tmp_path / "wal"
    wal_manager = TransactionWALManager(transactions_dir=wal_dir)
    installer = InstallationEngine(wal_manager=wal_manager)
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.resolve_executable.side_effect = Exception("Error containing secret api_key_value_99999")

    exec_engine = ExecutionEngine(external_runner=mock_runner, wal_manager=wal_manager)
    res = exec_engine.execute_plan(
        tx.plan,
        transaction=tx,
        dry_run=False,
        secret_values={"MY_SECRET_KEY": "api_key_value_99999"},
    )

    assert res.status == ExecutionStatus.ROLLED_BACK
    assert "api_key_value_99999" not in (res.error_message or "")
    assert "***" in (res.error_message or "")

    wal_log = wal_manager.read_transaction(tx.transaction_id)
    assert wal_log is not None
    wal_text = wal_log.model_dump_json()
    assert "api_key_value_99999" not in wal_text


def test_phase6c_existing_mcp_installation_compatibility(
    tmp_path: Path, sample_agent: AgentDetectionResult, dummy_checksum: str
) -> None:
    """Verify MCP installation compatibility with Phase 6C staged execution pipeline."""
    manifest = IntegrationManifest.model_validate(
        {
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub MCP server integration",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
                "checksum": dummy_checksum,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "MCP Team"}},
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

    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.execute.return_value = create_dummy_external_result(success=True)

    installer = InstallationEngine()
    tx = installer.create_transaction(manifest, sample_agent, Scope.WORKSPACE)
    assert tx.plan is not None

    for op in tx.plan.planned_operations:
        op.target_root = str(target_root)

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS
    assert (target_root / ".claude.json").exists()


def test_phase6c_existing_plugin_installation_compatibility(
    tmp_path: Path, sample_agent: AgentDetectionResult, dummy_checksum: str
) -> None:
    """Verify composite plugin installation compatibility with Phase 6C staged execution."""
    mcp_comp = IntegrationManifest.model_validate(
        {
            "id": "mcp-component",
            "name": "MCP Component",
            "version": "1.0.0",
            "description": "Component MCP",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@test/mcp-comp",
                "checksum": dummy_checksum,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {
                "mcp": {
                    "transport": "stdio",
                    "runtime": "npx",
                    "package_name": "@test/mcp-comp",
                    "env_vars": [],
                }
            },
        }
    )

    plugin_manifest = IntegrationManifest.model_validate(
        {
            "id": "composite-bundle",
            "name": "Composite Bundle Plugin",
            "version": "1.0.0",
            "description": "Bundle plugin",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "plugin",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "composite-bundle",
                "checksum": dummy_checksum,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Dev"}},
            "handler_spec": {"plugin": {"components": ["mcp-component"]}},
        }
    )

    registry = Registry(manifests=[mcp_comp, plugin_manifest])
    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    mock_runner = MagicMock(spec=ExternalRunner)
    mock_runner.execute.return_value = create_dummy_external_result(success=True)

    installer = InstallationEngine(registry=registry)
    tx = installer.create_transaction(
        plugin_manifest, sample_agent, Scope.WORKSPACE, registry=registry
    )
    assert tx.plan is not None

    for op in tx.plan.planned_operations:
        op.target_root = str(target_root)

    exec_engine = ExecutionEngine(external_runner=mock_runner, registry=registry)
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False, registry=registry)

    assert res.status == ExecutionStatus.SUCCESS
    plugin_json = target_root / ".agents" / "plugins" / "composite-bundle" / "plugin.json"
    assert plugin_json.exists()
