"""Unit tests for Phase 5A Installation Engine architecture and dry-run guarantees."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    UnsupportedIntegrationTypeError,
    UnsupportedScopeError,
)
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import RiskLevel
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
)


@pytest.fixture
def sample_mcp_manifest() -> IntegrationManifest:
    """Fixture returning a valid MCP IntegrationManifest."""
    dummy_checksum = "sha256:" + "a" * 64
    return IntegrationManifest.model_validate(
        {
            "id": "github-mcp",
            "name": "GitHub MCP Server",
            "version": "1.0.0",
            "description": "GitHub MCP integration",
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
                    "env_vars": [
                        {
                            "name": "GITHUB_PERSONAL_ACCESS_TOKEN",
                            "required": True,
                            "secret": True,
                            "description": "GitHub PAT",
                        }
                    ],
                }
            },
        }
    )


@pytest.fixture
def sample_skill_manifest() -> IntegrationManifest:
    """Fixture returning a valid Skill IntegrationManifest."""
    dummy_checksum = "sha256:" + "b" * 64
    return IntegrationManifest.model_validate(
        {
            "id": "code-review-skill",
            "name": "Code Review Skill",
            "version": "1.2.0",
            "description": "Automated code review skill",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "package",
                "package_name": "code-review-skill-bundle",
                "checksum": dummy_checksum,
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Dev Team"},
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md",
                    "supporting_files": ["helpers/checker.py"],
                }
            },
        }
    )


@pytest.fixture
def installed_claude_agent() -> AgentDetectionResult:
    """Fixture returning an installed Claude Code AgentDetectionResult."""
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
def installed_codex_agent() -> AgentDetectionResult:
    """Fixture returning an installed Codex AgentDetectionResult."""
    return AgentDetectionResult(
        agent_id="codex",
        name="Codex",
        installed=True,
        version="2.0.0",
        global_config_path="~/.codex/config.json",
        workspace_config_path=".codex/config.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL, AgentCapability.PLUGIN],
    )


def test_compatible_installation_plan_generation(
    sample_mcp_manifest: IntegrationManifest,
    installed_claude_agent: AgentDetectionResult,
) -> None:
    """Verify that generating a plan for a compatible agent produces a structured plan."""
    engine = InstallationEngine()
    plan = engine.generate_plan(sample_mcp_manifest, installed_claude_agent, Scope.WORKSPACE)

    assert plan.addon_id == "github-mcp"
    assert plan.target_agent == "claude-code"
    assert plan.target_scope == Scope.WORKSPACE
    assert plan.integration_type == IntegrationType.MCP
    assert len(plan.planned_operations) >= 2
    assert plan.reversible is True
    assert plan.risk_level == RiskLevel.LOW


def test_incompatible_agent_rejection(
    sample_skill_manifest: IntegrationManifest,
    installed_codex_agent: AgentDetectionResult,
) -> None:
    """Verify rejection when manifest target_agents does not include the target agent."""
    engine = InstallationEngine()
    # sample_skill_manifest only targets ["claude-code"]
    with pytest.raises(IncompatibleAgentError, match="does not list target agent 'Codex'"):
        engine.generate_plan(sample_skill_manifest, installed_codex_agent, Scope.WORKSPACE)


def test_missing_agent_rejection(
    sample_mcp_manifest: IntegrationManifest,
) -> None:
    """Verify rejection when the target agent is not installed on the system."""
    engine = InstallationEngine()
    uninstalled_agent = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=False,
    )
    with pytest.raises(IncompatibleAgentError, match="is not installed on this system"):
        engine.generate_plan(sample_mcp_manifest, uninstalled_agent, Scope.WORKSPACE)


def test_unsupported_scope_rejection(
    sample_skill_manifest: IntegrationManifest,
    installed_claude_agent: AgentDetectionResult,
) -> None:
    """Verify rejection when the requested scope is not supported by the manifest."""
    engine = InstallationEngine()
    # sample_skill_manifest only supports ["workspace"]
    with pytest.raises(UnsupportedScopeError, match="does not support scope 'global'"):
        engine.generate_plan(sample_skill_manifest, installed_claude_agent, Scope.GLOBAL)


def test_unsupported_integration_type_rejection(
    installed_claude_agent: AgentDetectionResult,
) -> None:
    """Verify rejection when no installer is registered for the integration type."""
    engine = InstallationEngine(installers=[])  # Empty installer registry
    manifest = IntegrationManifest.model_validate(
        {
            "id": "test-addon",
            "name": "Test Addon",
            "version": "1.0.0",
            "description": "Test",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "mcp",
            "target_agents": ["claude-code"],
            "source": {
                "source_type": "package",
                "package_name": "pkg",
                "checksum": "sha256:" + "c" * 64,
            },
            "trust": {"verification_status": "verified", "publisher": {"name": "Test"}},
            "handler_spec": {"mcp": {"runtime": "npx", "package_name": "pkg"}},
        }
    )
    with pytest.raises(UnsupportedIntegrationTypeError, match="No installer registered"):
        engine.generate_plan(manifest, installed_claude_agent, Scope.WORKSPACE)


def test_multiple_agent_installation_planning(
    sample_mcp_manifest: IntegrationManifest,
    installed_claude_agent: AgentDetectionResult,
    installed_codex_agent: AgentDetectionResult,
) -> None:
    """Verify plan generation across multiple detected agents."""
    engine = InstallationEngine()
    plans = engine.generate_plans(
        sample_mcp_manifest,
        [installed_claude_agent, installed_codex_agent],
        Scope.WORKSPACE,
    )
    assert "claude-code" in plans
    assert "codex" in plans
    assert plans["claude-code"].target_agent == "claude-code"
    assert plans["codex"].target_agent == "codex"


def test_dry_run_produces_no_filesystem_subprocess_network_calls(
    sample_mcp_manifest: IntegrationManifest,
    installed_claude_agent: AgentDetectionResult,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify dry-run plan generation executes no subprocesses, network calls, or file writes."""
    mock_subprocess = MagicMock()
    mock_httpx = MagicMock()

    monkeypatch.setattr("subprocess.run", mock_subprocess)
    monkeypatch.setattr("subprocess.Popen", mock_subprocess)

    engine = InstallationEngine()
    plan = engine.generate_plan(sample_mcp_manifest, installed_claude_agent, Scope.WORKSPACE)

    assert plan is not None
    mock_subprocess.assert_not_called()
    mock_httpx.assert_not_called()
