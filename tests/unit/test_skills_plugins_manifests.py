"""Validation and schema compliance test suite for Claude Code skills and composite plugins."""

from pathlib import Path
import pytest
import yaml

from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
    SourceType,
    VerificationStatus,
)
from aiaddons.integrations.plugin import resolve_plugin_components
from aiaddons.registry.loader import RegistryLoader
from aiaddons.registry.registry import Registry


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = REPO_ROOT / "registry" / "addons"


@pytest.fixture
def registry_loader() -> RegistryLoader:
    return RegistryLoader()


@pytest.fixture
def loaded_registry() -> Registry:
    reg, _ = Registry.from_directory(MANIFESTS_DIR)
    return reg


# ============================================================================
# 1. Superpowers Composite Plugin & Child Skills Validation
# ============================================================================


def test_superpowers_composite_plugin_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify superpowers plugin manifest loads with valid schema, Git source, and 5 child components."""
    file_path = MANIFESTS_DIR / "superpowers.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "superpowers"
    assert manifest.name == "Superpowers Agentic Skills Framework"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.PLUGIN
    assert manifest.category == "workflow"
    assert manifest.documentation_url == "https://github.com/obra/superpowers"
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.url == "https://github.com/obra/superpowers.git"
    assert manifest.source.commit_sha == "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Jesse Vincent (@obra)"
    assert manifest.handler_spec.plugin is not None

    components = manifest.handler_spec.plugin.components
    assert len(components) == 5
    assert components == [
        "superpowers-brainstorming",
        "superpowers-planning",
        "superpowers-worktree",
        "superpowers-tdd",
        "superpowers-review",
    ]


@pytest.mark.parametrize(
    "child_id,expected_skill_file",
        [
            ("superpowers-brainstorming", "skills/brainstorming/SKILL.md"),
            ("superpowers-planning", "skills/writing-plans/SKILL.md"),
            ("superpowers-worktree", "skills/using-git-worktrees/SKILL.md"),
            ("superpowers-tdd", "skills/test-driven-development/SKILL.md"),
            ("superpowers-review", "skills/receiving-code-review/SKILL.md"),
        ],
)
def test_superpowers_child_skills_validate(
    registry_loader: RegistryLoader, child_id: str, expected_skill_file: str
) -> None:
    """Verify each child skill in superpowers plugin loads and defines the correct SKILL.md path."""
    file_path = MANIFESTS_DIR / f"{child_id}.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == child_id
    assert manifest.integration_type == IntegrationType.SKILL
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.commit_sha == "b36e0829c6d0140e93cfef2ca599b1b07d4a7797"
    assert manifest.source.path == expected_skill_file.split("/SKILL.md")[0]
    assert manifest.handler_spec.skill.skill_file == "SKILL.md"


from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.exceptions import InstallationPlanningError
from aiaddons.core.models.agent import AgentCapability, AgentDetectionResult, Scope
from tests.fixtures.ponytail_manifests import claude_code_agent


def test_superpowers_plugin_component_resolution(
    loaded_registry: Registry,
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify resolve_plugin_components resolves all 5 child skills from registry."""
    superpowers_manifest = loaded_registry.get("superpowers")
    assert superpowers_manifest is not None

    compat_engine = CompatibilityEngine(registry=loaded_registry)
    resolved = resolve_plugin_components(
        manifest=superpowers_manifest,
        registry=loaded_registry,
        agent=claude_code_agent,
        scope=Scope.WORKSPACE,
        compatibility_engine=compat_engine,
    )
    assert len(resolved) == 5
    resolved_ids = [m.id for m in resolved]
    assert resolved_ids == [
        "superpowers-brainstorming",
        "superpowers-planning",
        "superpowers-worktree",
        "superpowers-tdd",
        "superpowers-review",
    ]


def test_superpowers_plugin_detects_dependency_cycles(
    claude_code_agent: AgentDetectionResult,
) -> None:
    """Verify cycle detection raises InstallationPlanningError if plugin components recurse."""
    loader = RegistryLoader()
    sp = loader.load_file(MANIFESTS_DIR / "superpowers.yaml")
    raw_sp = sp.model_dump()
    raw_sp["handler_spec"]["plugin"]["components"] = ["superpowers"]

    cyclic_manifest = IntegrationManifest.model_validate(raw_sp)
    cyclic_reg = Registry(manifests=[cyclic_manifest])
    compat_engine = CompatibilityEngine(registry=cyclic_reg)

    with pytest.raises(InstallationPlanningError, match="Dependency cycle detected"):
        resolve_plugin_components(
            manifest=cyclic_manifest,
            registry=cyclic_reg,
            agent=claude_code_agent,
            scope=Scope.WORKSPACE,
            compatibility_engine=compat_engine,
        )


# ============================================================================
# 2. Standalone Skill Manifests Validation
# ============================================================================


def test_skill_creator_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify skill-creator meta-skill manifest loads with Anthropic source and skill path."""
    file_path = MANIFESTS_DIR / "skill-creator.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "skill-creator"
    assert manifest.name == "Anthropic Skill Creator Meta-Skill"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.SKILL
    assert manifest.category == "developer-tools"
    assert manifest.documentation_url == "https://github.com/anthropics/skills"
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.url == "https://github.com/anthropics/skills.git"
    assert manifest.source.commit_sha == "34040c9c568585f6929bedeaad110ad08f079624"
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Anthropic"
    assert manifest.source.path == "skills/skill-creator"
    assert manifest.handler_spec.skill.skill_file == "SKILL.md"


def test_karpathy_behavioral_skill_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify karpathy-behavioral-skill manifest loads with valid schema and principles."""
    file_path = MANIFESTS_DIR / "karpathy-behavioral-skill.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "karpathy-behavioral-skill"
    assert manifest.name == "Karpathy Behavioral Guidelines Skill"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.SKILL
    assert manifest.category == "productivity"
    assert manifest.documentation_url == "https://github.com/forrestchang/andrej-karpathy-skills"
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.url == "https://github.com/forrestchang/andrej-karpathy-skills.git"
    assert manifest.source.commit_sha == "2c606141936f1eeef17fa3043a72095b4765b9c2"
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Forrest Chang"
    assert manifest.handler_spec.skill is not None
    assert manifest.handler_spec.skill.skill_file == "SKILL.md"


def test_caveman_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify caveman output-compression skill manifest loads with valid schema."""
    file_path = MANIFESTS_DIR / "caveman.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "caveman"
    assert manifest.name == "Caveman Output Compression Skill"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.SKILL
    assert manifest.category == "productivity"
    assert manifest.documentation_url == "https://github.com/JuliusBrussee/caveman"
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.url == "https://github.com/JuliusBrussee/caveman.git"
    assert manifest.source.commit_sha == "15581d14007fd01fb3f132016741962f34936ca2"
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "Julius Brussee"
    assert manifest.handler_spec.skill is not None
    assert manifest.handler_spec.skill.skill_file == "SKILL.md"


def test_vibesec_skill_manifest_validates(registry_loader: RegistryLoader) -> None:
    """Verify vibesec-skill manifest loads with valid schema and security categorization."""
    file_path = MANIFESTS_DIR / "vibesec-skill.yaml"
    assert file_path.exists()

    manifest = registry_loader.load_file(file_path)
    assert manifest.id == "vibesec-skill"
    assert manifest.name == "VibeSec Security & Vulnerability Prevention Skill"
    assert manifest.version == "1.0.0"
    assert manifest.integration_type == IntegrationType.SKILL
    assert manifest.category == "security"
    assert manifest.documentation_url == "https://github.com/BehiSecc/VibeSec-Skill"
    assert manifest.source.source_type == SourceType.GIT
    assert manifest.source.url == "https://github.com/BehiSecc/VibeSec-Skill.git"
    assert manifest.source.commit_sha == "0590993b35ad51961f65a4d01cf1196dfead05bb"
    assert manifest.trust.verification_status == VerificationStatus.VERIFIED
    assert manifest.trust.publisher.name == "BehiSecc"
    assert manifest.handler_spec.skill is not None
    assert manifest.handler_spec.skill.skill_file == "SKILL.md"


# ============================================================================
# 3. Strict Schema Guardrails & Security Rejection
# ============================================================================


@pytest.mark.parametrize(
    "manifest_file",
    [
        "superpowers.yaml",
        "superpowers-brainstorming.yaml",
        "superpowers-planning.yaml",
        "superpowers-worktree.yaml",
        "superpowers-tdd.yaml",
        "superpowers-review.yaml",
        "skill-creator.yaml",
        "karpathy-behavioral-skill.yaml",
        "caveman.yaml",
        "vibesec-skill.yaml",
    ],
)
def test_skill_manifest_rejects_extra_unrecognized_fields(manifest_file: str) -> None:
    """Ensure extra='forbid' rejects any undeclared or invented fields across all skill/plugin manifests."""
    file_path = MANIFESTS_DIR / manifest_file
    raw_dict = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    raw_dict["unauthorized_custom_field"] = "unexpected_field_value"

    with pytest.raises(Exception, match="extra_forbidden|Extra inputs are not permitted"):
        IntegrationManifest.model_validate(raw_dict)


@pytest.mark.parametrize(
    "manifest_file",
    [
        "superpowers-brainstorming.yaml",
        "superpowers-planning.yaml",
        "superpowers-worktree.yaml",
        "superpowers-tdd.yaml",
        "superpowers-review.yaml",
        "skill-creator.yaml",
        "karpathy-behavioral-skill.yaml",
        "caveman.yaml",
        "vibesec-skill.yaml",
    ],
)
def test_skill_manifest_rejects_path_traversal_in_skill_file(manifest_file: str) -> None:
    """Ensure path traversal (..) in skill_file is rejected by security validator."""
    file_path = MANIFESTS_DIR / manifest_file
    raw_dict = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    raw_dict["handler_spec"]["skill"]["skill_file"] = "../../../etc/passwd"

    with pytest.raises(ValueError, match="Path traversal violation"):
        IntegrationManifest.model_validate(raw_dict)
