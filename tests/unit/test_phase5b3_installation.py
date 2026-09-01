"""Comprehensive unit tests for Phase 5B.3 Full MCP/Skill/Plugin Installation."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationPlanningError,
)
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.external.models import ExternalExecutionResult, ExternalRuntime
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import TransactionPhase
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest
from aiaddons.registry.registry import Registry


@pytest.fixture
def dummy_checksum() -> str:
    return "sha256:" + "a" * 64


@pytest.fixture
def mcp_manifest(dummy_checksum: str) -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub integration for agent",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code", "codex"],
            "supported_scopes": ["global", "workspace"],
            "source": {
                "source_type": "package",
                "package_name": "@modelcontextprotocol/server-github",
                "checksum": dummy_checksum,
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
                    "env_vars": [{"name": "GITHUB_PAT", "required": True, "secret": True}],
                }
            },
        }
    )


@pytest.fixture
def skill_manifest(dummy_checksum: str) -> IntegrationManifest:
    return IntegrationManifest.model_validate(
        {
            "id": "code-reviewer",
            "name": "Code Reviewer Skill",
            "version": "1.0.0",
            "description": "Automated code reviewer",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code", "codex"],
            "supported_scopes": ["global", "workspace"],
            "source": {
                "source_type": "package",
                "package_name": "code-reviewer-skill",
                "checksum": dummy_checksum,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Dev Team"},
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md",
                    "supporting_files": ["rules.py"],
                }
            },
        }
    )


@pytest.fixture
def claude_agent() -> AgentDetectionResult:
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
def codex_agent() -> AgentDetectionResult:
    return AgentDetectionResult(
        agent_id="codex",
        name="OpenAI Codex",
        installed=True,
        version="1.0.0",
        global_config_path="~/.codex/config.json",
        workspace_config_path=".codex/config.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL, AgentCapability.PLUGIN],
    )


# 1. MCP Installation & Update Behavior
def test_mcp_installation_claude_global(
    tmp_path: Path, mcp_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify MCP installation writes to Claude Code config file."""
    config_file = tmp_path / ".claude.json"
    claude_agent.global_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, claude_agent, Scope.GLOBAL)
    plan.planned_operations[0].target_root = str(tmp_path)

    mock_runner = MagicMock()
    mock_runner.execute.return_value = ExternalExecutionResult(
        success=True,
        runtime=ExternalRuntime.NPX,
        executable_path="/usr/bin/npx",
        command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
        return_code=0,
        stdout="Installed",
        stderr="",
        duration=0.1,
    )

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.SUCCESS
    assert config_file.exists()

    content = json.loads(config_file.read_text(encoding="utf-8"))
    assert "mcpServers" in content
    assert "github-mcp" in content["mcpServers"]
    assert content["mcpServers"]["github-mcp"]["command"] == "npx"


def test_mcp_update_behavior_preserves_existing_servers(
    tmp_path: Path, mcp_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify MCP installation updates/replaces target server, preserving other servers."""
    config_file = tmp_path / ".claude.json"
    existing_config = {
        "mcpServers": {"existing-server": {"command": "uvx", "args": ["existing-mcp"]}},
        "unrelated_setting": "true",
    }
    config_file.write_text(json.dumps(existing_config), encoding="utf-8")
    claude_agent.workspace_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, claude_agent, Scope.WORKSPACE)
    plan.planned_operations[0].target_root = str(tmp_path)

    mock_runner = MagicMock()
    mock_runner.execute.return_value = ExternalExecutionResult(
        success=True,
        runtime=ExternalRuntime.NPX,
        executable_path="/usr/bin/npx",
        command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
        return_code=0,
        stdout="OK",
        stderr="",
        duration=0.1,
    )

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.SUCCESS
    content = json.loads(config_file.read_text(encoding="utf-8"))
    assert "existing-server" in content["mcpServers"]
    assert "github-mcp" in content["mcpServers"]
    assert content["unrelated_setting"] == "true"


def test_mcp_installation_codex_workspace(
    tmp_path: Path, mcp_manifest: IntegrationManifest, codex_agent: AgentDetectionResult
) -> None:
    """Verify MCP installation for Codex workspace scope."""
    config_file = tmp_path / ".codex/config.json"
    codex_agent.workspace_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, codex_agent, Scope.WORKSPACE)
    for op in plan.planned_operations:
        op.target_root = str(tmp_path)

    mock_runner = MagicMock()
    mock_runner.execute.return_value = ExternalExecutionResult(
        success=True,
        runtime=ExternalRuntime.NPX,
        executable_path="/usr/bin/npx",
        command_vector=["npx", "-y", "@modelcontextprotocol/server-github"],
        return_code=0,
        stdout="OK",
        stderr="",
        duration=0.1,
    )

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.SUCCESS
    assert config_file.exists()


# 2. Skill Installation & Path Traversal Rejection
def test_skill_installation_success(
    tmp_path: Path, skill_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify skill deployment to conventional directory structure."""
    engine = InstallationEngine()
    plan = engine.generate_plan(skill_manifest, claude_agent, Scope.WORKSPACE)
    for op in plan.planned_operations:
        op.target_root = str(tmp_path)

    exec_engine = ExecutionEngine()
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.SUCCESS
    skill_file = tmp_path / ".claude/skills/code-reviewer/SKILL.md"
    assert skill_file.exists()
    assert "Code Reviewer Skill" in skill_file.read_text(encoding="utf-8")


def test_skill_path_traversal_rejection(
    dummy_checksum: str, claude_agent: AgentDetectionResult
) -> None:
    """Verify that skill specs targeting outside root are rejected immediately."""
    with pytest.raises(Exception, match="Path traversal violation"):
        IntegrationManifest.model_validate(
            {
                "id": "bad-skill",
                "name": "Bad Skill",
                "version": "1.0.0",
                "description": "Exploit attempt",
                "license": "MIT",
                "category": "developer-tools",
                "integration_type": "skill",
                "target_agents": ["claude-code"],
                "source": {
                    "source_type": "package",
                    "package_name": "bad-pkg",
                    "checksum": dummy_checksum,
                },
                "trust": {"verification_status": "verified", "publisher": {"name": "Evil"}},
                "handler_spec": {
                    "skill": {
                        "skill_file": "../../../etc/passwd",
                    }
                },
            }
        )


# 3. Composite Plugin Installation, Cycle Detection & Order
def test_plugin_component_resolution_and_cycle_detection(
    dummy_checksum: str, claude_agent: AgentDetectionResult
) -> None:
    """Verify plugin component cycle detection."""
    m1 = IntegrationManifest.model_validate(
        {
            "id": "plugin-a",
            "name": "Plugin A",
            "version": "1.0.0",
            "description": "Plugin A",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "plugin",
            "target_agents": ["claude-code"],
            "source": {"source_type": "package", "package_name": "p-a", "checksum": dummy_checksum},
            "trust": {"verification_status": "verified", "publisher": {"name": "P"}},
            "handler_spec": {"plugin": {"components": ["plugin-b"]}},
        }
    )
    m2 = IntegrationManifest.model_validate(
        {
            "id": "plugin-b",
            "name": "Plugin B",
            "version": "1.0.0",
            "description": "Plugin B",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "plugin",
            "target_agents": ["claude-code"],
            "source": {"source_type": "package", "package_name": "p-b", "checksum": dummy_checksum},
            "trust": {"verification_status": "verified", "publisher": {"name": "P"}},
            "handler_spec": {"plugin": {"components": ["plugin-a"]}},
        }
    )

    reg = Registry([m1, m2])
    engine = InstallationEngine(registry=reg)

    expected_exc = (IncompatibleAgentError, InstallationPlanningError)
    with pytest.raises(expected_exc, match="Dependency cycle detected"):
        engine.generate_plan(m1, claude_agent, Scope.WORKSPACE)


# 4. Atomic Writes & Malformed Config Handling
def test_malformed_config_handling_triggers_rollback(
    tmp_path: Path, mcp_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify attempting to modify a malformed JSON file fails safely without corrupting data."""
    config_file = tmp_path / ".claude.json"
    config_file.write_text("INVALID JSON {{{", encoding="utf-8")
    claude_agent.workspace_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, claude_agent, Scope.WORKSPACE)
    plan.planned_operations[0].target_root = str(tmp_path)

    exec_engine = ExecutionEngine()
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.ROLLED_BACK
    assert config_file.read_text(encoding="utf-8") == "INVALID JSON {{{"


# 5. Failed Installation & Rollback
def test_failed_external_runner_triggers_rollback(
    tmp_path: Path, mcp_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify that failure during external package resolution triggers atomic rollback."""
    config_file = tmp_path / ".claude.json"
    claude_agent.workspace_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, claude_agent, Scope.WORKSPACE)
    plan.planned_operations[0].target_root = str(tmp_path)

    mock_runner = MagicMock()
    from aiaddons.core.exceptions import ExecutableNotFoundError
    mock_runner.resolve_executable.side_effect = ExecutableNotFoundError(
        "Approved runtime executable 'npx' not found on system PATH."
    )

    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan, dry_run=False)

    assert result.status == ExecutionStatus.ROLLED_BACK
    assert not config_file.exists() or "github-mcp" not in config_file.read_text(encoding="utf-8")


# 6. Dry-Run Guarantee
def test_dry_run_zero_side_effect_guarantee(
    tmp_path: Path, mcp_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify that dry-run execution performs zero filesystem mutations or subprocess calls."""
    config_file = tmp_path / ".claude.json"
    claude_agent.workspace_config_path = str(config_file)

    engine = InstallationEngine()
    plan = engine.generate_plan(mcp_manifest, claude_agent, Scope.WORKSPACE)
    for op in plan.planned_operations:
        op.target_root = str(tmp_path)

    mock_runner = MagicMock()
    exec_engine = ExecutionEngine(external_runner=mock_runner)
    result = exec_engine.execute_plan(plan, dry_run=True)

    assert result.status == ExecutionStatus.SUCCESS
    mock_runner.execute.assert_not_called()
    assert not config_file.exists()


# 7. Transaction State Machine
def test_transaction_state_lifecycle(
    tmp_path: Path, skill_manifest: IntegrationManifest, claude_agent: AgentDetectionResult
) -> None:
    """Verify full state machine transitions: PLANNED -> EXECUTING -> VERIFIED -> COMMITTED."""
    engine = InstallationEngine()
    tx = engine.create_transaction(skill_manifest, claude_agent, Scope.WORKSPACE)
    assert tx.phase == TransactionPhase.PLANNED
    assert tx.plan is not None

    for op in tx.plan.planned_operations:
        op.target_root = str(tmp_path)

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(tx.plan, transaction=tx, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS
    assert str(tx.phase) == TransactionPhase.COMMITTED.value
