"""CLI Tool Integration Installer for declarative dependency linking."""

from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.installer.models import (
    InstallationPlan,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
    WriteFileOperation,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.base import BaseIntegrationInstaller


class CLIToolInstaller(BaseIntegrationInstaller):
    """Installer for external CLI Tool dependency integrations."""

    def supported_types(self) -> list[IntegrationType]:
        return [IntegrationType.CLI_TOOL]

    def validate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
    ) -> list[str]:
        errors: list[str] = []
        if manifest.integration_type != IntegrationType.CLI_TOOL:
            errors.append(f"Invalid type '{manifest.integration_type.value}' for CLIToolInstaller.")

        if not manifest.handler_spec.cli_tool:
            errors.append("Missing required 'cli_tool' handler_spec in manifest.")

        return errors

    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
        compatibility: CompatibilityResult,
    ) -> InstallationPlan:
        spec = manifest.handler_spec.cli_tool
        if not spec:
            raise ValueError("CLI Tool handler_spec missing in manifest.")

        target_root = "~" if scope == Scope.GLOBAL else "."

        config_dir = "~/.aiaddons/cli_tools" if scope == Scope.GLOBAL else ".agents/cli_tools"
        file_path = f"{config_dir}/{spec.binary_name}.json"

        bin_name = spec.binary_name
        ver_constraint = spec.version_constraint
        summary_text = f"{{binary_name: '{bin_name}', version_constraint: '{ver_constraint}'}}"
        desc_text = f"Record verified link for binary '{bin_name}' ({agent.name})"
        record_op = WriteFileOperation(
            description=desc_text,
            target_root=target_root,
            file_path=file_path,
            content=summary_text,
            content_summary=summary_text,
            target_path=file_path,
            overwrite=True,
            reversible=True,
        )

        rollback_info = RollbackMetadata(
            reversible=True,
            rollback_operations=[
                RollbackOperation(
                    op_type="remove_file",
                    description=f"Remove CLI tool record file '{file_path}'",
                    target_root=target_root,
                    target_path=file_path,
                    params={"file_path": file_path},
                )
            ],
            instructions=f"Delete record file '{file_path}'.",
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
            planned_operations=[record_op],
            dependencies=compatibility.dependencies,
            warnings=compatibility.warnings,
            risk_level=RiskLevel.LOW,
            reversible=True,
            rollback_info=rollback_info,
        )
