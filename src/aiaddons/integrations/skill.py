"""Skill Integration Installer for deploying agent skills safely."""

from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.installer.models import (
    AddSkillOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
    WriteFileOperation,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.base import BaseIntegrationInstaller


def get_skill_target_directory(agent_id: str, scope: Scope) -> str:
    """Determine conventional skill target directory for a given agent and scope."""
    aid = agent_id.lower()
    if scope == Scope.GLOBAL:
        if aid in ("claude-code", "claude"):
            return ".claude/skills"
        if aid == "codex":
            return ".codex/skills"
        return f".aiaddons/skills/{aid}"
    else:
        if aid in ("claude-code", "claude"):
            return ".claude/skills"
        if aid == "codex":
            return ".agents/skills"
        return ".agents/skills"


class SkillInstaller(BaseIntegrationInstaller):
    """Installer for Agent Skill integrations."""

    def supported_types(self) -> list[IntegrationType]:
        return [IntegrationType.SKILL]

    def validate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
    ) -> list[str]:
        errors: list[str] = []
        if manifest.integration_type != IntegrationType.SKILL:
            errors.append(
                f"Invalid integration type '{manifest.integration_type.value}' for SkillInstaller."
            )

        if not manifest.handler_spec.skill:
            errors.append("Missing required 'skill' handler_spec in manifest.")

        return errors

    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
        compatibility: CompatibilityResult,
    ) -> InstallationPlan:
        spec = manifest.handler_spec.skill
        if not spec:
            raise ValueError("Skill handler_spec missing in manifest.")

        target_root = "~" if scope == Scope.GLOBAL else "."

        base_dir = get_skill_target_directory(agent.agent_id, scope)
        dest_dir = f"{base_dir}/{manifest.id}"

        create_dir_op = CreateDirectoryOperation(
            description=(
                f"Create target skill directory '{dest_dir}' for {agent.name} ({scope.value})"
            ),
            target_root=target_root,
            directory_path=dest_dir,
            target_path=dest_dir,
            reversible=True,
        )

        add_skill_op = AddSkillOperation(
            description=f"Deploy skill '{manifest.name}' ({spec.skill_file}) to '{dest_dir}'",
            target_root=target_root,
            skill_name=manifest.id,
            skill_file=spec.skill_file,
            destination_dir=dest_dir,
            supporting_files=spec.supporting_files,
            target_path=dest_dir,
            reversible=True,
        )

        skill_content = f"# Skill: {manifest.name}\n\nSkill instructions for {manifest.name}."
        write_file_op = WriteFileOperation(
            description=f"Write skill file '{spec.skill_file}' inside '{dest_dir}'",
            target_root=target_root,
            file_path=f"{dest_dir}/{spec.skill_file}",
            content=skill_content,
            content_summary=f"Skill instructions for {manifest.name}",
            target_path=f"{dest_dir}/{spec.skill_file}",
            overwrite=True,
            reversible=True,
        )

        rollback_info = RollbackMetadata(
            reversible=True,
            rollback_operations=[
                RollbackOperation(
                    op_type="remove_directory",
                    description=f"Remove deployed skill directory '{dest_dir}'",
                    target_root=target_root,
                    target_path=dest_dir,
                    params={"directory_path": dest_dir},
                )
            ],
            instructions=f"Delete directory '{dest_dir}'.",
        )

        return InstallationPlan(
            addon_id=manifest.id,
            addon_name=manifest.name,
            addon_version=manifest.version,
            target_agent=agent.agent_id,
            target_agent_name=agent.name,
            target_scope=scope,
            integration_type=manifest.integration_type,
            source=manifest.source,
            planned_operations=[create_dir_op, add_skill_op, write_file_op],
            dependencies=compatibility.dependencies,
            warnings=compatibility.warnings,
            risk_level=RiskLevel.LOW,
            reversible=True,
            rollback_info=rollback_info,
        )
