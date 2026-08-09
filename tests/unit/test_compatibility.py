"""Unit tests for Phase 4 Compatibility Engine decision logic."""

import pytest

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.compatibility.models import DependencyStatus
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    DependencySpec,
    DependencyType,
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    MCPHandlerSpec,
    MCPRuntime,
    MCPTransport,
    PluginHandlerSpec,
    PublisherClaimSpec,
    SkillHandlerSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
)


@pytest.fixture
def mock_claude_agent() -> AgentDetectionResult:
    """Fixture creating a mocked installed Claude Code agent detection result."""
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="0.2.29",
        executable_path="/usr/local/bin/claude",
        global_config_path="~/.claude.json",
        workspace_config_path=".claude/",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


@pytest.fixture
def mock_codex_agent() -> AgentDetectionResult:
    """Fixture creating a mocked installed Codex agent detection result."""
    return AgentDetectionResult(
        agent_id="codex",
        name="Codex CLI",
        installed=True,
        version="1.0.0",
        executable_path="/usr/local/bin/codex",
        global_config_path="~/.codex/",
        workspace_config_path=".agents/",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL, AgentCapability.PLUGIN],
    )


@pytest.fixture
def mock_uninstalled_agent() -> AgentDetectionResult:
    """Fixture creating a mocked uninstalled agent detection result."""
    return AgentDetectionResult(
        agent_id="uninstalled-agent",
        name="Uninstalled Agent",
        installed=False,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[],
    )


@pytest.fixture
def sample_mcp_manifest() -> IntegrationManifest:
    """Fixture creating a valid MCP server IntegrationManifest."""
    return IntegrationManifest(
        id="github-mcp",
        name="GitHub MCP Server",
        version="1.2.0",
        description="GitHub MCP Server",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code", "codex"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="@scope/github-mcp",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="MCP Team")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                transport=MCPTransport.STDIO,
                runtime=MCPRuntime.NPX,
                package_name="@scope/github-mcp",
            )
        ),
    )


def test_compatible_claude_code(
    sample_mcp_manifest: IntegrationManifest, mock_claude_agent: AgentDetectionResult
) -> None:
    """Test that a valid manifest returns compatible = True for installed Claude Code."""
    engine = CompatibilityEngine()
    result = engine.evaluate(sample_mcp_manifest, mock_claude_agent, Scope.WORKSPACE)

    assert result.compatible is True
    assert result.agent_id == "claude-code"
    assert result.addon_id == "github-mcp"
    assert len(result.missing_requirements) == 0
    assert len(result.unsupported_requirements) == 0


def test_compatible_codex(
    sample_mcp_manifest: IntegrationManifest, mock_codex_agent: AgentDetectionResult
) -> None:
    """Test that a valid manifest returns compatible = True for installed Codex CLI."""
    engine = CompatibilityEngine()
    result = engine.evaluate(sample_mcp_manifest, mock_codex_agent, Scope.GLOBAL)

    assert result.compatible is True
    assert result.agent_id == "codex"
    assert result.requested_scope == Scope.GLOBAL


def test_agent_not_installed(
    sample_mcp_manifest: IntegrationManifest, mock_uninstalled_agent: AgentDetectionResult
) -> None:
    """Test that an uninstalled agent yields compatible = False with actionable reason."""
    engine = CompatibilityEngine()
    result = engine.evaluate(sample_mcp_manifest, mock_uninstalled_agent, Scope.WORKSPACE)

    assert result.compatible is False
    assert result.is_installed_agent is False
    assert any("not installed" in r for r in result.reasons)
    assert any("Agent installation" in req for req in result.missing_requirements)


def test_unsupported_agent_target(mock_claude_agent: AgentDetectionResult) -> None:
    """Test that an add-on targeting only Codex returns compatible = False for Claude Code."""
    codex_only_manifest = IntegrationManifest(
        id="codex-only",
        name="Codex Exclusive Plugin",
        version="1.0.0",
        description="Exclusively for Codex",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["codex"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/codex-only"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Codex Team")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["comp-1"])),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(codex_only_manifest, mock_claude_agent, Scope.WORKSPACE)

    assert result.compatible is False
    assert any("does not list target agent" in r for r in result.reasons)
    assert any("Target agent" in req for req in result.unsupported_requirements)


def test_missing_agent_capability(mock_claude_agent: AgentDetectionResult) -> None:
    """Test that an agent missing required capability returns compatible = False."""
    # Create agent missing PLUGIN capability
    agent_no_plugin = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP],  # Missing PLUGIN
    )

    plugin_manifest = IntegrationManifest(
        id="test-plugin",
        name="Test Plugin",
        version="1.0.0",
        description="Requires plugin capability",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["claude-code"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/test"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Core")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["comp1"])),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(plugin_manifest, agent_no_plugin, Scope.WORKSPACE)

    assert result.compatible is False
    assert any("does not declare capability 'plugin'" in r for r in result.reasons)
    assert any("Agent capability: plugin" in req for req in result.missing_requirements)


def test_incompatible_scope(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Test workspace-only add-on returns compatible = False for global scope."""
    workspace_only_manifest = IntegrationManifest(
        id="workspace-skill",
        name="Workspace Skill",
        version="1.0.0",
        description="Workspace scope only",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["claude-code"],
        supported_scopes=[Scope.WORKSPACE],  # No GLOBAL
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/skill",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Community")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(workspace_only_manifest, mock_claude_agent, Scope.GLOBAL)

    assert result.compatible is False
    assert any("does not support scope 'global'" in r for r in result.reasons)
    assert any("Scope: global" in req for req in result.unsupported_requirements)


def test_missing_and_unknown_dependencies(mock_claude_agent: AgentDetectionResult) -> None:
    """Test evaluating CLI missing dependency and unknown addon dependency."""
    manifest_with_deps = IntegrationManifest(
        id="dep-mcp",
        name="Dep MCP",
        version="1.0.0",
        description="Has missing CLI dep",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code"],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="dep-mcp",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        dependencies=[
            DependencySpec(
                name="nonexistent_binary_tool_xyz_99",
                type=DependencyType.CLI,
                required=True,
            ),
            DependencySpec(name="addon-dep-1", type=DependencyType.ADDON, required=False),
        ],
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(runtime=MCPRuntime.NPX, package_name="dep-mcp")
        ),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(manifest_with_deps, mock_claude_agent, Scope.WORKSPACE)

    assert result.compatible is False
    assert any("nonexistent_binary_tool_xyz_99" in r for r in result.reasons)

    # Check dependency results list
    dep_statuses = {d.name: d.status for d in result.dependencies}
    assert dep_statuses["nonexistent_binary_tool_xyz_99"] == DependencyStatus.MISSING
    assert dep_statuses["addon-dep-1"] == DependencyStatus.UNKNOWN


def test_multiple_agent_evaluation(
    sample_mcp_manifest: IntegrationManifest,
    mock_claude_agent: AgentDetectionResult,
    mock_codex_agent: AgentDetectionResult,
    mock_uninstalled_agent: AgentDetectionResult,
) -> None:
    """Test evaluate_all produces structured results across multiple agents."""
    engine = CompatibilityEngine()
    results = engine.evaluate_all(
        sample_mcp_manifest,
        [mock_claude_agent, mock_codex_agent, mock_uninstalled_agent],
        Scope.WORKSPACE,
    )

    assert len(results) == 3
    assert results[0].agent_id == "claude-code"
    assert results[0].compatible is True

    assert results[1].agent_id == "codex"
    assert results[1].compatible is True

    assert results[2].agent_id == "uninstalled-agent"
    assert results[2].compatible is False
