"""Plugin Integration Installer for composite add-on bundles."""

from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.installer.models import (
    AddPluginReferenceOperation,
    CreateDirectoryOperation,
    InstallationPlan,
    ModifyJsonOperation,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.base import BaseIntegrationInstaller


class PluginInstaller(BaseIntegrationInstaller):
    """Installer for Composite Plugin integrations."""

    def supported_types(self) -> list[IntegrationType]:
        return [IntegrationType.PLUGIN]

    def validate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
    ) -> list[str]:
        errors: list[str] = []
        if manifest.integration_type != IntegrationType.PLUGIN:
            errors.append(
                f"Invalid integration type '{manifest.integration_type.value}' for PluginInstaller."
            )

        if not manifest.handler_spec.plugin:
            errors.append("Missing required 'plugin' handler_spec in manifest.")

        return errors

    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
        compatibility: CompatibilityResult,
    ) -> InstallationPlan:
        spec = manifest.handler_spec.plugin
        if not spec:
            raise ValueError("Plugin handler_spec missing in manifest.")

        target_root = "~" if scope == Scope.GLOBAL else "."

        dest_dir = (
            f"~/.aiaddons/plugins/{manifest.id}"
            if scope == Scope.GLOBAL
            else f".agents/plugins/{manifest.id}"
        )

        create_dir_op = CreateDirectoryOperation(
            description=f"Create plugin directory '{dest_dir}' for {agent.name} ({scope.value})",
            target_root=target_root,
            directory_path=dest_dir,
            target_path=dest_dir,
            reversible=True,
        )

        plugin_desc = (
            f"Register plugin composite bundle '{manifest.id}' "
            f"with {len(spec.components)} component(s)"
        )
        add_plugin_op = AddPluginReferenceOperation(
            description=plugin_desc,
            target_root=target_root,
            plugin_id=manifest.id,
            component_ids=spec.components,
            config_path=f"{dest_dir}/plugin.json",
            target_path=dest_dir,
            reversible=True,
        )

        plugin_payload = {"id": manifest.id, "components": spec.components}
        modify_json_op = ModifyJsonOperation(
            description=f"Write plugin descriptor to '{dest_dir}/plugin.json'",
            target_root=target_root,
            file_path=f"{dest_dir}/plugin.json",
            json_path="plugin",
            value=plugin_payload,
            value_summary=f"{{id: '{manifest.id}', components: {spec.components}}}",
            target_path=f"{dest_dir}/plugin.json",
            reversible=True,
        )

        rollback_info = RollbackMetadata(
            reversible=True,
            rollback_operations=[
                RollbackOperation(
                    op_type="remove_directory",
                    description=f"Remove plugin directory '{dest_dir}'",
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
            planned_operations=[create_dir_op, add_plugin_op, modify_json_op],
            dependencies=compatibility.dependencies,
            warnings=compatibility.warnings,
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            rollback_info=rollback_info,
        )
