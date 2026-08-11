"""Unit tests for real skill bundle installation, supporting files, and security boundaries."""

from pathlib import Path

import pytest

from aiaddons.agents.claude_code import ClaudeCodeAdapter
from aiaddons.core.execution.engine import ExecutionEngine
from aiaddons.core.execution.models import ExecutionStatus
from aiaddons.core.installer.engine import InstallationEngine
from aiaddons.core.installer.models import (
    AddSkillOperation,
    InstallationPlan,
    RollbackMetadata,
)
from aiaddons.core.models.agent import Scope
from aiaddons.core.models.manifest import (
    IntegrationManifest,
    IntegrationType,
    SourceSpec,
    SourceType,
)


@pytest.fixture
def real_skill_manifest(tmp_path: Path) -> tuple[IntegrationManifest, Path]:
    """Create a real skill bundle on disk and return manifest + source directory."""
    src_dir = tmp_path / "skill_source"
    src_dir.mkdir(parents=True, exist_ok=True)

    skill_md = src_dir / "SKILL.md"
    skill_md.write_text("# Test Skill\n\nDetailed test skill instructions.", encoding="utf-8")

    supp_py = src_dir / "rules.py"
    supp_py.write_text("# Supporting rules\nRULE = True\n", encoding="utf-8")

    manifest = IntegrationManifest.model_validate(
        {
            "id": "test-skill",
            "name": "Test Skill Addon",
            "version": "1.0.0",
            "description": "Test skill for agent",
            "license": "MIT",
            "category": "developer-tools",
            "integration_type": "skill",
            "target_agents": ["claude-code"],
            "supported_scopes": ["workspace"],
            "source": {
                "source_type": "local",
                "path": "skill_source",
            },
            "trust": {
                "verification_status": "verified",
                "publisher": {"name": "Test Team"},
            },
            "handler_spec": {
                "skill": {
                    "skill_file": "SKILL.md",
                    "supporting_files": ["rules.py"],
                }
            },
        }
    )
    return manifest, src_dir


def test_real_skill_bundle_installation(
    tmp_path: Path, real_skill_manifest: tuple[IntegrationManifest, Path]
) -> None:
    """Verify real skill bundle installation copies SKILL.md and supporting files."""
    manifest, src_dir = real_skill_manifest
    adapter = ClaudeCodeAdapter()
    agent = adapter.detect(project_path=tmp_path)
    agent.installed = True

    engine = InstallationEngine()
    plan = engine.generate_plan(manifest, agent, Scope.WORKSPACE)

    dest_dir = tmp_path / "dest_root"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for op in plan.planned_operations:
        op.target_root = str(dest_dir)
        if isinstance(op, AddSkillOperation):
            op.source_dir = str(src_dir)

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(plan, dry_run=False)

    assert res.status == ExecutionStatus.SUCCESS

    installed_skill = dest_dir / ".claude/skills/test-skill/SKILL.md"
    installed_supp = dest_dir / ".claude/skills/test-skill/rules.py"

    assert installed_skill.exists()
    assert "Detailed test skill instructions" in installed_skill.read_text(encoding="utf-8")
    assert installed_supp.exists()
    assert "RULE = True" in installed_supp.read_text(encoding="utf-8")


def test_missing_skill_file_in_source_raises_error(tmp_path: Path) -> None:
    """Verify missing SKILL.md in source directory raises InstallationError and rolls back."""
    src_dir = tmp_path / "empty_source"
    src_dir.mkdir(parents=True, exist_ok=True)

    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    op = AddSkillOperation(
        description="Deploy malformed skill",
        target_root=str(target_root),
        skill_name="Test Skill",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/bad-skill",
        supporting_files=[],
        source_dir=str(src_dir),
    )

    plan = InstallationPlan(
        addon_id="bad-skill",
        addon_name="Bad Skill",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=SourceSpec(source_type=SourceType.LOCAL, path="empty_source"),
        planned_operations=[op],
        rollback_info=RollbackMetadata(reversible=True, rollback_operations=[]),
    )

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(plan, dry_run=False)

    assert res.status == ExecutionStatus.ROLLED_BACK
    assert "missing from source directory" in (res.error_message or "")


def test_supporting_file_path_traversal_rejection(tmp_path: Path) -> None:
    """Verify supporting file path containing path traversal is rejected during validation."""
    with pytest.raises(ValueError, match="Path traversal violation"):
        AddSkillOperation(
            description="Traversal skill",
            target_root=str(tmp_path),
            skill_name="Bad Skill",
            skill_file="SKILL.md",
            destination_dir=".claude/skills/bad-skill",
            supporting_files=["../../secret.txt"],
        )


def test_skill_symlink_escape_rejection(tmp_path: Path) -> None:
    """Verify skill file that is a symlink resolving outside source directory is rejected."""
    src_dir = tmp_path / "sym_source"
    src_dir.mkdir(parents=True, exist_ok=True)

    secret_outside = tmp_path / "outside_secret.txt"
    secret_outside.write_text("SECRET", encoding="utf-8")

    sym_link = src_dir / "SKILL.md"
    try:
        sym_link.symlink_to(secret_outside)
    except OSError:
        pytest.skip("Symlinks not supported on this platform/privilege level")

    target_root = tmp_path / "target"
    target_root.mkdir(parents=True, exist_ok=True)

    op = AddSkillOperation(
        description="Deploy symlink skill",
        target_root=str(target_root),
        skill_name="Symlink Skill",
        skill_file="SKILL.md",
        destination_dir=".claude/skills/sym-skill",
        supporting_files=[],
        source_dir=str(src_dir),
    )

    plan = InstallationPlan(
        addon_id="sym-skill",
        addon_name="Symlink Skill",
        addon_version="1.0.0",
        target_agent="claude-code",
        target_agent_name="Claude Code",
        target_scope=Scope.WORKSPACE,
        integration_type=IntegrationType.SKILL,
        source=SourceSpec(source_type=SourceType.LOCAL, path="sym_source"),
        planned_operations=[op],
        rollback_info=RollbackMetadata(reversible=True, rollback_operations=[]),
    )

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(plan, dry_run=False)

    assert res.status == ExecutionStatus.ROLLED_BACK
    assert "Security violation" in (res.error_message or "")

    exec_engine = ExecutionEngine()
    res = exec_engine.execute_plan(plan, dry_run=False)

    assert res.status == ExecutionStatus.ROLLED_BACK
    assert "Security violation" in (res.error_message or "")
