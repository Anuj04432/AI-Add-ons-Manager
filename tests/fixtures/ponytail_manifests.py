"""Test fixtures for Ponytail add-on validation."""

import pytest

from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from aiaddons.core.models.manifest import (
    HandlerSpecContainer,
    IntegrationManifest,
    IntegrationType,
    PluginHandlerSpec,
    PublisherClaimSpec,
    SkillHandlerSpec,
    SourceSpec,
    SourceType,
    TrustMetadata,
    VerificationStatus,
)
from aiaddons.registry.registry import Registry

PONYTAIL_REPO_URL = "https://github.com/DietrichGebert/ponytail.git"
PONYTAIL_COMMIT_SHA = "0a4dd63ad4541f4f655c4108a295916f3c1d8fda"
PONYTAIL_VERSION = "4.9.0"


@pytest.fixture
def ponytail_publisher() -> TrustMetadata:
    """Fixture for verified publisher metadata of Dietrich Gebert."""
    return TrustMetadata(
        verification_status=VerificationStatus.VERIFIED,
        publisher=PublisherClaimSpec(
            name="Dietrich Gebert",
            url="https://github.com/DietrichGebert",
            declared_verified=True,
        ),
        allowed_executables=[],
    )


@pytest.fixture
def ponytail_source() -> SourceSpec:
    """Fixture for immutable pinned Ponytail Git source."""
    return SourceSpec(
        source_type=SourceType.GIT,
        url=PONYTAIL_REPO_URL,
        ref=f"v{PONYTAIL_VERSION}",
        commit_sha=PONYTAIL_COMMIT_SHA,
    )


@pytest.fixture
def claude_code_agent() -> AgentDetectionResult:
    """Fixture for detected Claude Code agent without AgentCapability.PLUGIN."""
    return AgentDetectionResult(
        agent_id="claude-code",
        name="Claude Code",
        installed=True,
        version="2.1.229",
        executable_path="/usr/local/bin/claude",
        global_config_path="~/.claude.json",
        workspace_config_path=".claude.json",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


@pytest.fixture
def codex_agent() -> AgentDetectionResult:
    """Fixture for detected OpenAI Codex agent."""
    return AgentDetectionResult(
        agent_id="codex",
        name="OpenAI Codex",
        installed=True,
        version="1.0.0",
        executable_path="/usr/local/bin/codex",
        global_config_path="~/.codex",
        workspace_config_path=".agents",
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        capabilities=[AgentCapability.MCP, AgentCapability.SKILL],
    )


@pytest.fixture
def ponytail_composite_manifest(
    ponytail_source: SourceSpec, ponytail_publisher: TrustMetadata
) -> IntegrationManifest:
    """Fixture for Ponytail composite plugin manifest."""
    return IntegrationManifest(
        id="ponytail",
        name="Ponytail - Lazy Senior Dev Mode",
        version=PONYTAIL_VERSION,
        description=(
            "Lazy senior dev mode. Forces the simplest, shortest solution that actually works."
        ),
        documentation_url="https://github.com/DietrichGebert/ponytail",
        license="MIT",
        category="productivity",
        integration_type=IntegrationType.PLUGIN,
        target_agents=["*"],
        supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
        source=ponytail_source,
        dependencies=[],
        trust=ponytail_publisher,
        tags=["ponytail", "yagni", "productivity", "plugin", "clean-code"],
        handler_spec=HandlerSpecContainer(
            plugin=PluginHandlerSpec(
                components=[
                    "ponytail-skill",
                    "ponytail-review-skill",
                    "ponytail-audit-skill",
                    "ponytail-debt-skill",
                    "ponytail-gain-skill",
                    "ponytail-help-skill",
                ]
            )
        ),
    )


@pytest.fixture
def ponytail_skill_manifests(
    ponytail_source: SourceSpec, ponytail_publisher: TrustMetadata
) -> list[IntegrationManifest]:
    """Fixture for the 6 Ponytail child skill manifests."""
    skills_meta = [
        (
            "ponytail-skill",
            "Ponytail Core Skill",
            "skills/ponytail/SKILL.md",
            ["ponytail", "yagni", "skill"],
        ),
        (
            "ponytail-review-skill",
            "Ponytail Review Skill",
            "skills/ponytail-review/SKILL.md",
            ["ponytail", "code-review", "skill"],
        ),
        (
            "ponytail-audit-skill",
            "Ponytail Audit Skill",
            "skills/ponytail-audit/SKILL.md",
            ["ponytail", "audit", "skill"],
        ),
        (
            "ponytail-debt-skill",
            "Ponytail Debt Ledger Skill",
            "skills/ponytail-debt/SKILL.md",
            ["ponytail", "debt", "skill"],
        ),
        (
            "ponytail-gain-skill",
            "Ponytail Gain Scoreboard Skill",
            "skills/ponytail-gain/SKILL.md",
            ["ponytail", "metrics", "skill"],
        ),
        (
            "ponytail-help-skill",
            "Ponytail Help & Reference Skill",
            "skills/ponytail-help/SKILL.md",
            ["ponytail", "help", "skill"],
        ),
    ]

    manifests = []
    for sid, sname, sfile, stags in skills_meta:
        manifests.append(
            IntegrationManifest(
                id=sid,
                name=sname,
                version=PONYTAIL_VERSION,
                description=f"{sname} for AI coding agents.",
                documentation_url="https://github.com/DietrichGebert/ponytail",
                license="MIT",
                category="productivity",
                integration_type=IntegrationType.SKILL,
                target_agents=["*"],
                supported_scopes=[Scope.GLOBAL, Scope.WORKSPACE],
                source=ponytail_source,
                dependencies=[],
                trust=ponytail_publisher,
                tags=stags,
                handler_spec=HandlerSpecContainer(
                    skill=SkillHandlerSpec(skill_file=sfile)
                ),
            )
        )
    return manifests


@pytest.fixture
def ponytail_populated_registry(
    ponytail_composite_manifest: IntegrationManifest,
    ponytail_skill_manifests: list[IntegrationManifest],
) -> Registry:
    """Fixture for Registry populated with Ponytail plugin and all 6 child skills."""
    all_manifests = [ponytail_composite_manifest] + ponytail_skill_manifests
    return Registry(manifests=all_manifests)
