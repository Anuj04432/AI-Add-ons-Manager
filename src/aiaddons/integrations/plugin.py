"""Plugin Integration Installer for composite add-on bundles."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationPlanningError,
)
from aiaddons.core.installer.models import (
    AddPluginReferenceOperation,
    BaseOperation,
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
from aiaddons.integrations.cli_tool import CLIToolInstaller
from aiaddons.integrations.mcp import MCPInstaller
from aiaddons.integrations.skill import SkillInstaller

if TYPE_CHECKING:
    from aiaddons.registry.registry import Registry


def resolve_plugin_components(
    manifest: IntegrationManifest,
    registry: Registry,
    agent: AgentDetectionResult,
    scope: Scope,
    compatibility_engine: CompatibilityEngine,
    visited: set[str] | None = None,
    stack: list[str] | None = None,
) -> list[IntegrationManifest]:
    """Resolve composite plugin components, checking existence and compatibility."""
    if visited is None:
        visited = set()
    if stack is None:
        stack = []

    if manifest.id in stack:
        cycle_path = " -> ".join(stack + [manifest.id])
        msg = f"Dependency cycle detected in plugin components: {cycle_path}"
        raise InstallationPlanningError(msg)

    if manifest.id in visited:
        return []

    stack.append(manifest.id)
    ordered_components: list[IntegrationManifest] = []

    spec = manifest.handler_spec.plugin
    if spec and spec.components:
        for comp_id in spec.components:
            comp_manifest = registry.get(comp_id)
            if not comp_manifest:
                msg = (
                    f"Plugin component '{comp_id}' referenced by '{manifest.id}' "
                    "not found in registry."
                )
                raise InstallationPlanningError(msg)

            compat = compatibility_engine.evaluate(comp_manifest, agent, scope)
            if not compat.compatible:
                reasons = "; ".join(compat.reasons)
                msg = (
                    f"Plugin component '{comp_id}' is incompatible with agent '{agent.name}': "
                    f"{reasons}"
                )
                raise IncompatibleAgentError(msg)

            if comp_manifest.integration_type == IntegrationType.PLUGIN:
                sub_comps = resolve_plugin_components(
                    comp_manifest, registry, agent, scope, compatibility_engine, visited, stack
                )
                for sc in sub_comps:
                    if sc.id not in [m.id for m in ordered_components]:
                        ordered_components.append(sc)

            if comp_manifest.id not in [m.id for m in ordered_components]:
                ordered_components.append(comp_manifest)

    stack.pop()
    visited.add(manifest.id)
    return ordered_components


class PluginInstaller(BaseIntegrationInstaller):
    """Installer for Composite Plugin integrations."""

    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry

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
        registry: Registry | None = None,
        compatibility_engine: CompatibilityEngine | None = None,
    ) -> InstallationPlan:
        spec = manifest.handler_spec.plugin
        if not spec:
            raise ValueError("Plugin handler_spec missing in manifest.")

        target_root = "~" if scope == Scope.GLOBAL else "."

        dest_dir = (
            f".aiaddons/plugins/{manifest.id}"
            if scope == Scope.GLOBAL
            else f".agents/plugins/{manifest.id}"
        )

        planned_operations: list[BaseOperation] = []
        active_registry = registry or self.registry

        if active_registry:
            compat_engine = compatibility_engine or CompatibilityEngine()
            comp_manifests = resolve_plugin_components(
                manifest=manifest,
                registry=active_registry,
                agent=agent,
                scope=scope,
                compatibility_engine=compat_engine,
            )

            for comp_m in comp_manifests:
                comp_installer: BaseIntegrationInstaller
                if comp_m.integration_type == IntegrationType.MCP:
                    comp_installer = MCPInstaller()
                elif comp_m.integration_type == IntegrationType.SKILL:
                    comp_installer = SkillInstaller()
                elif comp_m.integration_type == IntegrationType.CLI_TOOL:
                    comp_installer = CLIToolInstaller()
                elif comp_m.integration_type == IntegrationType.PLUGIN:
                    comp_installer = PluginInstaller(registry=active_registry)
                else:
                    continue

                comp_compat = compat_engine.evaluate(comp_m, agent, scope)
                comp_plan = comp_installer.generate_plan(comp_m, agent, scope, comp_compat)
                planned_operations.extend(comp_plan.planned_operations)

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

        planned_operations.extend([create_dir_op, add_plugin_op, modify_json_op])

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
            planned_operations=planned_operations,
            dependencies=compatibility.dependencies,
            warnings=compatibility.warnings,
            risk_level=RiskLevel.MEDIUM,
            reversible=True,
            rollback_info=rollback_info,
        )
