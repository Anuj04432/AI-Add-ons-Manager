"""Unit tests for Phase 4 Compatibility Engine decision logic."""

import pytest

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.compatibility.models import DependencyStatus
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    CLIToolHandlerSpec,
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
    """Test agent missing required MCP capability returns compatible = False."""
    agent_no_mcp = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.SKILL],  # Missing MCP
    )

    mcp_manifest = IntegrationManifest(
        id="test-mcp",
        name="Test MCP",
        version="1.0.0",
        description="Requires MCP capability",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.MCP,
        target_agents=["claude-code"],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="@scope/test-mcp",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Core")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(
                transport=MCPTransport.STDIO,
                runtime=MCPRuntime.NPX,
                package_name="@scope/test-mcp",
            )
        ),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(mcp_manifest, agent_no_mcp, Scope.WORKSPACE)

    assert result.compatible is False
    assert any("does not declare capability 'mcp'" in r for r in result.reasons)
    assert any("Agent capability: mcp" in req for req in result.missing_requirements)


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


def test_mcp_still_requires_mcp_capability(mock_claude_agent: AgentDetectionResult) -> None:
    """MCP integrations must strictly require AgentCapability.MCP."""
    agent_no_mcp = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.SKILL],  # Lacks MCP
    )

    mcp_manifest = IntegrationManifest(
        id="sample-mcp",
        name="Sample MCP",
        version="1.0.0",
        description="MCP server",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.MCP,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="sample-mcp",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(runtime=MCPRuntime.NPX, package_name="sample-mcp")
        ),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(mcp_manifest, agent_no_mcp, Scope.WORKSPACE)
    assert result.compatible is False
    assert any("does not declare capability 'mcp'" in r for r in result.reasons)


def test_skill_still_requires_skill_capability() -> None:
    """SKILL integrations must strictly require AgentCapability.SKILL."""
    agent_no_skill = AgentDetectionResult(
        agent_id="agent-no-skill",
        name="Agent Without Skill",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP],  # Lacks SKILL
    )

    skill_manifest = IntegrationManifest(
        id="sample-skill",
        name="Sample Skill",
        version="1.0.0",
        description="Agent skill",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/skill",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(skill_manifest, agent_no_skill, Scope.WORKSPACE)
    assert result.compatible is False
    assert any("does not declare capability 'skill'" in r for r in result.reasons)


def test_plugin_does_not_require_native_plugin_capability(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """PLUGIN composite add-ons must NOT require AgentCapability.PLUGIN."""
    plugin_manifest = IntegrationManifest(
        id="empty-plugin",
        name="Empty Composite Plugin",
        version="1.0.0",
        description="Composite plugin with no components",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/empty"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=[])),
    )

    engine = CompatibilityEngine()
    result = engine.evaluate(plugin_manifest, mock_claude_agent, Scope.WORKSPACE)
    assert result.compatible is True


def test_python_lint_plugin_compatible_with_claude_code(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """python-lint-plugin is compatible with Claude Code when children are compatible."""
    ruff_cli = IntegrationManifest(
        id="ruff-cli",
        name="Ruff CLI",
        version="0.1.0",
        description="Ruff CLI Tool",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.CLI_TOOL,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="cli/ruff"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Ruff")),
        handler_spec=HandlerSpecContainer(cli_tool=CLIToolHandlerSpec(binary_name="ruff")),
    )

    refactoring_skill = IntegrationManifest(
        id="caveman",
        name="Refactoring Skill",
        version="1.0.0",
        description="Python refactoring skill",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/refactor",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    python_lint_plugin = IntegrationManifest(
        id="python-lint-plugin",
        name="Python Lint Composite Plugin",
        version="1.0.0",
        description="Ruff + Refactoring composite plugin",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/python-lint"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(components=["ruff-cli", "caveman"])
        ),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[ruff_cli, refactoring_skill, python_lint_plugin])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(python_lint_plugin, mock_claude_agent, Scope.WORKSPACE)
    assert result.compatible is True
    assert result.agent_id == "claude-code"


def test_python_lint_plugin_compatible_with_codex(
    mock_codex_agent: AgentDetectionResult,
) -> None:
    """python-lint-plugin is compatible with Codex when all child components are compatible."""
    ruff_cli = IntegrationManifest(
        id="ruff-cli",
        name="Ruff CLI",
        version="0.1.0",
        description="Ruff CLI Tool",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.CLI_TOOL,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="cli/ruff"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Ruff")),
        handler_spec=HandlerSpecContainer(cli_tool=CLIToolHandlerSpec(binary_name="ruff")),
    )

    refactoring_skill = IntegrationManifest(
        id="caveman",
        name="Refactoring Skill",
        version="1.0.0",
        description="Python refactoring skill",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/refactor",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    python_lint_plugin = IntegrationManifest(
        id="python-lint-plugin",
        name="Python Lint Composite Plugin",
        version="1.0.0",
        description="Ruff + Refactoring composite plugin",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/python-lint"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(components=["ruff-cli", "caveman"])
        ),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[ruff_cli, refactoring_skill, python_lint_plugin])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(python_lint_plugin, mock_codex_agent, Scope.WORKSPACE)
    assert result.compatible is True
    assert result.agent_id == "codex"


def test_composite_plugin_incompatible_child_rejected() -> None:
    """Composite plugin with an incompatible child component is rejected with actionable reason."""
    agent_no_mcp = AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.SKILL],  # Lacks MCP capability
    )

    unsupported_mcp = IntegrationManifest(
        id="unsupported-mcp",
        name="Unsupported MCP",
        version="1.0.0",
        description="MCP server requiring MCP capability",
        license="MIT",
        category="dev",
        integration_type=IntegrationType.MCP,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.PACKAGE,
            package_name="unsupported-mcp",
            checksum="sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            mcp=MCPHandlerSpec(runtime=MCPRuntime.NPX, package_name="unsupported-mcp")
        ),
    )

    refactoring_skill = IntegrationManifest(
        id="caveman",
        name="Refactoring Skill",
        version="1.0.0",
        description="Python refactoring skill",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/refactor",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    plugin_manifest = IntegrationManifest(
        id="composite-plugin",
        name="Composite Plugin",
        version="1.0.0",
        description="Skill + MCP plugin",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/composite"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(components=["caveman", "unsupported-mcp"])
        ),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[unsupported_mcp, refactoring_skill, plugin_manifest])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(plugin_manifest, agent_no_mcp, Scope.WORKSPACE)
    assert result.compatible is False
    assert any("unsupported-mcp" in r for r in result.reasons)
    assert any("does not declare capability 'mcp'" in r for r in result.reasons)


def test_composite_plugin_missing_child_dependency_rejected(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Composite plugin referencing a component missing from registry is rejected."""
    plugin_manifest = IntegrationManifest(
        id="broken-plugin",
        name="Broken Plugin",
        version="1.0.0",
        description="Plugin with missing component",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/broken"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(components=["missing-component-xyz"])
        ),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[plugin_manifest])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(plugin_manifest, mock_claude_agent, Scope.WORKSPACE)
    assert result.compatible is False
    assert any("missing-component-xyz" in r for r in result.reasons)
    assert any("not found in registry" in r for r in result.reasons)


def test_nested_composite_plugin_behavior(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Nested composite plugins resolve and evaluate all sub-components recursively."""
    leaf_skill = IntegrationManifest(
        id="leaf-skill",
        name="Leaf Skill",
        version="1.0.0",
        description="Leaf skill",
        license="MIT",
        category="workflow",
        integration_type=IntegrationType.SKILL,
        target_agents=["*"],
        source=SourceSpec(
            source_type=SourceType.GIT,
            repository="https://github.com/test/skill",
            commit_sha="4a2d8f9e1c3b5a7d9e0f2a4b6c8d0e1f2a3b4c5d",
        ),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(skill=SkillHandlerSpec(skill_file="SKILL.md")),
    )

    child_plugin = IntegrationManifest(
        id="child-plugin",
        name="Child Plugin",
        version="1.0.0",
        description="Child composite plugin",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/child"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["leaf-skill"])),
    )

    parent_plugin = IntegrationManifest(
        id="parent-plugin",
        name="Parent Plugin",
        version="1.0.0",
        description="Parent composite plugin",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/parent"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["child-plugin"])),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[leaf_skill, child_plugin, parent_plugin])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(parent_plugin, mock_claude_agent, Scope.WORKSPACE)
    assert result.compatible is True


def test_composite_plugin_cycle_detection(
    mock_claude_agent: AgentDetectionResult,
) -> None:
    """Cyclic component references in composite plugins are detected and rejected."""
    plugin_a = IntegrationManifest(
        id="plugin-a",
        name="Plugin A",
        version="1.0.0",
        description="Cyclic plugin A",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/a"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["plugin-b"])),
    )

    plugin_b = IntegrationManifest(
        id="plugin-b",
        name="Plugin B",
        version="1.0.0",
        description="Cyclic plugin B",
        license="MIT",
        category="tools",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        source=SourceSpec(source_type=SourceType.LOCAL, path="plugins/b"),
        trust=TrustMetadata(publisher=PublisherClaimSpec(name="Test")),
        handler_spec=HandlerSpecContainer(plugin=PluginHandlerSpec(components=["plugin-a"])),
    )

    from aiaddons.registry.registry import Registry

    registry = Registry(manifests=[plugin_a, plugin_b])
    engine = CompatibilityEngine(registry=registry)

    result = engine.evaluate(plugin_a, mock_claude_agent, Scope.WORKSPACE)
    assert result.compatible is False
    assert any("Dependency cycle detected" in r for r in result.reasons)
