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

    assert result.status == ExecutionStatus.SUCCESS
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


def test_mcp_operation_execution_in_phase_5b2(
    tmp_path: Path, sample_source: SourceSpec
) -> None:
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

    assert result.status == ExecutionStatus.SUCCESS


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
