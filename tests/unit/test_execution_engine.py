"""Unit tests for Phase 5B.1 ExecutionEngine, transaction lifecycle, rollback, and dry-run."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    InstallationTransaction,
    ModifyJsonOperation,
    ModifyYamlOperation,
    RiskLevel,
    RollbackMetadata,
    TransactionPhase,
    WriteFileOperation,
)
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
    MCPRuntime,
    MCPTransport,
    SourceSpec,
    SourceType,
)


@pytest.fixture
def sample_source() -> SourceSpec:
    return SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@test/pkg",
        checksum="sha256:" + "a" * 64,
    )


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
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


def test_execution_engine_structural_operations_success(
    tmp_path: Path, sample_source: SourceSpec
) -> None:
    """Verify execution of valid structural operations."""
    target_root = str(tmp_path)

    op1 = CreateDirectoryOperation(
        description="Create skill dir",
        target_root=target_root,
        directory_path="skills/my-skill",
    )
    op2 = WriteFileOperation(
        description="Write skill file",
        target_root=target_root,
        file_path="skills/my-skill/SKILL.md",
        content="# My Skill",
        content_summary="Skill instructions",
        overwrite=True,
    )
    op3 = ModifyJsonOperation(
        description="Write JSON config",
        target_root=target_root,
        file_path="config.json",
        json_path="settings.skill",
        value={"enabled": True},
        value_summary="{...}",
    )
    op4 = ModifyYamlOperation(
        description="Write YAML config",
        target_root=target_root,
        file_path="config.yaml",
        yaml_path="settings.skill",
        value={"enabled": True},
        value_summary="{...}",
    )

    plan = InstallationPlan(
        addon_id="test-skill",
        addon_name="Test Skill",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=sample_source,
        planned_operations=[op1, op2, op3, op4],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    engine = ExecutionEngine()
    result = engine.execute_plan(plan)

    print(f"DEBUG_ERROR: {result.error_message}")
    assert result.status == ExecutionStatus.SUCCESS
    assert len(result.executed_operations) == 4
    assert (tmp_path / "skills/my-skill/SKILL.md").exists()
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "config.yaml").exists()


def test_transaction_lifecycle_state_transitions(
    tmp_path: Path, sample_source: SourceSpec, sample_agent: AgentDetectionResult
) -> None:
    """Verify transaction state transitions from REQUESTED -> EXECUTING -> VERIFIED -> COMMITTED."""
    target_root = str(tmp_path)
    op = CreateDirectoryOperation(
        description="Create directory",
        target_root=target_root,
        directory_path="test_dir",
    )
    plan = InstallationPlan(
        addon_id="test-skill",
        addon_name="Test Skill",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=sample_source,
        planned_operations=[op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    manifest_data = {
        "id": "test-skill",
        "name": "Test Skill",
        "version": "1.0.0",
        "description": "Test",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "skill",
        "target_agents": ["claude-code"],
        "source": sample_source.model_dump(),
        "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
        "handler_spec": {"skill": {"skill_file": "SKILL.md"}},
    }
    manifest = IntegrationManifest.model_validate(manifest_data)

    tx = InstallationTransaction(
        transaction_id="tx_999",
        phase=TransactionPhase.REQUESTED,
        manifest=manifest,
        agent=sample_agent,
        requested_scope=Scope.WORKSPACE,
        plan=plan,
    )

    engine = ExecutionEngine()
    result = engine.execute_plan(plan, transaction=tx)

    assert result.status == ExecutionStatus.SUCCESS, result.error_message
    assert tx.phase == TransactionPhase.COMMITTED


def test_execution_failure_triggers_automatic_rollback(
    tmp_path: Path, sample_source: SourceSpec
) -> None:
    """Verify that failure during execution automatically rolls back preceding operations."""
    target_root = str(tmp_path)

    op1 = CreateDirectoryOperation(
        description="Create skill dir",
        target_root=target_root,
        directory_path="skills/rollback_dir",
    )
    # op2: write file that fails because target destination is a directory
    (tmp_path / "failing_file.txt").mkdir(parents=True)

    op2 = WriteFileOperation(
        description="Write file failing",
        target_root=target_root,
        file_path="failing_file.txt",
        content="data",
        content_summary="data",
        overwrite=True,
    )

    plan = InstallationPlan(
        addon_id="test-skill",
        addon_name="Test Skill",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=sample_source,
        planned_operations=[op1, op2],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    engine = ExecutionEngine()
    result = engine.execute_plan(plan)

    assert result.status == ExecutionStatus.ROLLED_BACK
    # op1 should be rolled back and the directory removed
    assert not (tmp_path / "skills/rollback_dir").exists()


def test_mcp_operation_execution_in_phase_5b2(tmp_path: Path, sample_source: SourceSpec) -> None:
    """Verify that AddMcpServerOperation executes successfully under Phase 5B.2."""
    target_root = str(tmp_path)
    add_mcp = AddMcpServerOperation(
        description="Add MCP server",
        target_root=target_root,
        server_name="github",
        runtime=MCPRuntime.NPX,
        package_name="@modelcontextprotocol/server-github",
        transport=MCPTransport.STDIO,
        config_path="config.json",
    )
    plan = InstallationPlan(
        addon_id="github-mcp",
        addon_name="GitHub MCP",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.MCP,
        source=sample_source,
        planned_operations=[add_mcp],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    engine = ExecutionEngine()
    result = engine.execute_plan(plan, dry_run=True)

    assert result.status == ExecutionStatus.SUCCESS, result.error_message


def test_dry_run_performs_zero_filesystem_mutation_or_subprocess_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify that dry-run planning performs zero subprocess calls or disk writes."""
    mock_subprocess = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_subprocess)
    monkeypatch.setattr("subprocess.Popen", mock_subprocess)

    source = SourceSpec(
        source_type=SourceType.PACKAGE,
        package_name="@test/pkg",
        checksum="sha256:" + "b" * 64,
    )
    op = CreateDirectoryOperation(
        description="Create dir",
        target_root=str(tmp_path),
        directory_path="dry_run_dir",
    )
    plan = InstallationPlan(
        addon_id="test",
        addon_name="Test",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=source,
        planned_operations=[op],
        risk_level=RiskLevel.LOW,
        reversible=True,
        rollback_info=RollbackMetadata(reversible=True),
    )

    plan.validate_safety()

    mock_subprocess.assert_not_called()
    assert not (tmp_path / "dry_run_dir").exists()

def test_execute_update_plan_uses_explicit_workspace_dir(tmp_path: Path):
    from aiaddons.core.execution.engine import ExecutionEngine
    from aiaddons.core.update.models import UpdatePlan, UpdatePlanItem
    from aiaddons.core.models.agent import Scope
    from aiaddons.state.lockfile import LockfileManager
    from unittest.mock import MagicMock
    from aiaddons.core.models.agent import Scope
    from aiaddons.state.lockfile import LockfileManager
    
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    actual_dir = tmp_path / "actual"
    actual_dir.mkdir()
    
    lockfile_mgr = LockfileManager()
    
    # Initialize engine with the stale/default directory
    engine = ExecutionEngine(
        lockfile_manager=lockfile_mgr,
        workspace_dir=stale_dir,
    )
    
    # We use a mock to bypass strict Pydantic validation while testing ExecutionEngine logic
    from aiaddons.core.models.manifest import SourceSpec, SourceType
    
    source = SourceSpec(source_type=SourceType.LOCAL, path="plugin-dir")
    
    install_plan_mock = MagicMock()
    install_plan_mock.planned_operations = []
    install_plan_mock.target_agent = "claude-code"
    install_plan_mock.target_scope = Scope.WORKSPACE
    install_plan_mock.addon_id = "test-addon"
    install_plan_mock.addon_name = "Test Addon"
    install_plan_mock.integration_type = "plugin"
    install_plan_mock.addon_version = "2.0"
    install_plan_mock.source = source
    install_plan_mock.validate_safety.return_value = None
    
    removal_plan_mock = MagicMock()
    removal_plan_mock.planned_operations = []
    removal_plan_mock.target_agent = "claude-code"
    removal_plan_mock.target_scope = Scope.WORKSPACE
    removal_plan_mock.addon_id = "test-addon"
    removal_plan_mock.addon_version = "1.0"
    removal_plan_mock.source = source
    removal_plan_mock.validate_safety.return_value = None
    
    plan_item = MagicMock()
    plan_item.install_plan = install_plan_mock
    plan_item.removal_plan = removal_plan_mock
    plan_item.addon_id = "test-addon"
    plan_item.target_version = "2.0"
    
    update_plan = MagicMock()
    update_plan.items = [plan_item]
    update_plan.target_agent = "claude-code"
    update_plan.target_scope = Scope.WORKSPACE
    update_plan.is_empty = False
    update_plan.validate_safety.return_value = None
    
    from aiaddons.registry.models import IntegrationManifest
    manifest_data = {
        "id": "test-addon",
        "name": "Test Addon",
        "version": "2.0",
        "description": "Test",
        "license": "MIT",
        "category": "developer-tools",
        "integration_type": "plugin",
        "target_agents": ["claude-code"],
        "source": {"source_type": "local", "path": "."},
        "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
        "handler_spec": {"plugin": {}},
    }
    manifest = IntegrationManifest.model_validate(manifest_data)
    
    tx = MagicMock()
    tx.is_dry_run = False
    tx.transaction_id = "test-tx"
    tx.model_dump_json.return_value = "{}"
    tx.manifest = manifest
    
    engine.acquisition_engine = MagicMock()
    engine.verification_engine = MagicMock()
    
    # Mock successful source acquisition
    acq_res_mock = MagicMock()
    engine.acquisition_engine.acquire_source.return_value = acq_res_mock
    
    # Mock successful verification
    verif_res_mock = MagicMock()
    verif_res_mock.verified = True
    verif_res_mock.status = "SUCCESS"
    engine.verification_engine.verify_plan.return_value = verif_res_mock
    
    # Execute update using the explicitly passed actual_dir
    res = engine.execute_update_plan(
        update_plan=update_plan,
        transaction=tx,
        dry_run=False,
        workspace_dir=actual_dir
    )
    print(f"RESULT STATUS: {res.status}")
    print(f"RESULT ERROR: {res.error_message}")
    
    # Verify that the stale dir's lockfile was NOT created
    stale_lockfile = lockfile_mgr.get_lockfile_path(stale_dir)
    assert not stale_lockfile.exists(), "Bug: Lockfile written to stale workspace_dir!"
    
    # Verify that the actual dir's lockfile WAS created and contains the entry
    actual_lockfile = lockfile_mgr.get_lockfile_path(actual_dir)
    assert actual_lockfile.exists(), "Fix failed: Lockfile not written to explicitly passed workspace_dir!"
    entries = lockfile_mgr.get_entries(actual_dir)
    assert len(entries) == 1
    assert entries[0].addon_id == "test-addon"
    assert entries[0].version == "2.0"
    
    # Execute update using the explicitly passed actual_dir
    engine.execute_update_plan(
        update_plan=update_plan,
        dry_run=False,
        workspace_dir=actual_dir
    )
    
    # Verify that the stale dir's lockfile was NOT created
    stale_lockfile = lockfile_mgr.get_lockfile_path(stale_dir)
    assert not stale_lockfile.exists(), "Bug: Lockfile written to stale workspace_dir!"
    
    # Verify that the actual dir's lockfile WAS created and contains the entry
    actual_lockfile = lockfile_mgr.get_lockfile_path(actual_dir)
    assert actual_lockfile.exists(), "Fix failed: Lockfile not written to explicitly passed workspace_dir!"
    entries = lockfile_mgr.get_entries(actual_dir)
    assert len(entries) == 1
    assert entries[0].addon_id == "test-addon"
    assert entries[0].version == "2.0"
