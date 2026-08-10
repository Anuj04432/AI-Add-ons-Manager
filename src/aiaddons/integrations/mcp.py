"""MCP (Model Context Protocol) Integration Installer."""

from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.installer.models import (
    AddMcpServerOperation,
    InstallationPlan,
    ModifyJsonOperation,
    RiskLevel,
    RollbackMetadata,
    RollbackOperation,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.base import BaseIntegrationInstaller


class MCPInstaller(BaseIntegrationInstaller):
    """Installer for Model Context Protocol (MCP) integrations."""

    def supported_types(self) -> list[IntegrationType]:
        return [IntegrationType.MCP]

    def validate(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
    ) -> list[str]:
        errors: list[str] = []
        if manifest.integration_type != IntegrationType.MCP:
            errors.append(
                f"Invalid integration type '{manifest.integration_type.value}' for MCPInstaller."
            )

        if not manifest.handler_spec.mcp:
            errors.append("Missing required 'mcp' handler_spec in manifest.")

        return errors

    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope,
        compatibility: CompatibilityResult,
    ) -> InstallationPlan:
        spec = manifest.handler_spec.mcp
        if not spec:
            raise ValueError("MCP handler_spec missing in manifest.")

        target_root = "~" if scope == Scope.GLOBAL else "."

        config_path = agent.get_config_path_for_scope(scope)
        if not config_path:
            config_path = (
                f"~/.{agent.agent_id}.json"
                if scope == Scope.GLOBAL
                else f".{agent.agent_id}.json"
            )

        env_names = [e.name for e in spec.env_vars]

        mcp_desc = (
            f"Configure MCP server '{manifest.id}' ({spec.package_name}) "
            f"for {agent.name} ({scope.value})"
        )
        mcp_op = AddMcpServerOperation(
            description=mcp_desc,
            target_root=target_root,
            server_name=manifest.id,
            runtime=spec.runtime,
            package_name=spec.package_name,
            transport=spec.transport,
            env_var_names=env_names,
            config_path=config_path,
            target_path=config_path,
            reversible=True,
            rollback=RollbackMetadata(
                reversible=True,
                rollback_operations=[
                    RollbackOperation(
                        op_type="remove_mcp_server",
                        description=f"Remove MCP server '{manifest.id}' from '{config_path}'",
                        target_root=target_root,
                        target_path=config_path,
                        params={"server_name": manifest.id},
                    )
                ],
            ),
        )

        mcp_payload = {
            "command": spec.runtime.value,
            "args": [spec.package_name],
            "env": {e.name: e.name for e in spec.env_vars},
        }

        json_desc = (
            f"Inject key 'mcpServers.{manifest.id}' into agent configuration file '{config_path}'"
        )
        modify_json_op = ModifyJsonOperation(
            description=json_desc,
            target_root=target_root,
            file_path=config_path,
            json_path=f"mcpServers.{manifest.id}",
            value=mcp_payload,
            value_summary=f"{{command: '{spec.runtime.value}', package: '{spec.package_name}'}}",
            target_path=config_path,
            reversible=True,
        )

        rollback_info = RollbackMetadata(
            reversible=True,
            rollback_operations=[
                RollbackOperation(
                    op_type="remove_json_key",
                    description=f"Remove key 'mcpServers.{manifest.id}' from '{config_path}'",
                    target_root=target_root,
                    target_path=config_path,
                    params={"json_path": f"mcpServers.{manifest.id}"},
                )
            ],
            instructions=f"Remove server key '{manifest.id}' under mcpServers in '{config_path}'.",
        )

        risk_level = RiskLevel.MEDIUM if scope == Scope.GLOBAL else RiskLevel.LOW

        return InstallationPlan(
            addon_id=manifest.id,
            addon_name=manifest.name,
            addon_version=manifest.version,
            target_agent=agent.agent_id,
            target_agent_name=agent.name,
            target_scope=scope,
            integration_type=manifest.integration_type,
            source=manifest.source,
            planned_operations=[mcp_op, modify_json_op],
            dependencies=compatibility.dependencies,
            warnings=compatibility.warnings,
            risk_level=risk_level,
            reversible=True,
            rollback_info=rollback_info,
        )
