"""Transactional Installation Engine for planning and validating add-on installations."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from aiaddons.core.compatibility.engine import CompatibilityEngine
from aiaddons.core.compatibility.models import CompatibilityResult
from aiaddons.core.exceptions import (
    IncompatibleAgentError,
    InstallationPlanningError,
    UnsupportedIntegrationTypeError,
    UnsupportedScopeError,
)
from aiaddons.core.installer.models import (
    BatchInstallationPlan,
    InstallationPlan,
    InstallationTransaction,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.state.transaction import TransactionWALManager

if TYPE_CHECKING:
    from aiaddons.integrations.base import BaseIntegrationInstaller
    from aiaddons.registry.registry import Registry
    from aiaddons.state.store import InstalledAddonRecord


class InstallationEngine:
    """Core installation engine managing dry-run planning and safety verification."""

    def __init__(
        self,
        installers: list[BaseIntegrationInstaller] | None = None,
        compatibility_engine: CompatibilityEngine | None = None,
        registry: Registry | None = None,
        wal_manager: TransactionWALManager | None = None,
    ) -> None:
        self._compatibility_engine = compatibility_engine or CompatibilityEngine(registry=registry)
        if registry and self._compatibility_engine.registry is None:
            self._compatibility_engine.registry = registry
        self._registry = registry
        self._wal_manager = wal_manager
        self._installers: dict[IntegrationType, BaseIntegrationInstaller] = {}

        if installers is None:
            from aiaddons.integrations.cli_tool import CLIToolInstaller
            from aiaddons.integrations.mcp import MCPInstaller
            from aiaddons.integrations.plugin import PluginInstaller
            from aiaddons.integrations.skill import SkillInstaller

            default_installers: list[BaseIntegrationInstaller] = [
                MCPInstaller(),
                SkillInstaller(),
                PluginInstaller(registry=registry),
                CLIToolInstaller(),
            ]
            for inst in default_installers:
                self.register_installer(inst)
        else:
            for inst in installers:
                self.register_installer(inst)

    def register_installer(self, installer: BaseIntegrationInstaller) -> None:
        """Register a type-specific integration installer."""
        for itype in installer.supported_types():
            self._installers[itype] = installer

    def get_installer(self, integration_type: IntegrationType) -> BaseIntegrationInstaller:
        """Retrieve registered installer for the given integration type."""
        if integration_type not in self._installers:
            raise UnsupportedIntegrationTypeError(
                f"No installer registered for integration type '{integration_type.value}'."
            )
        return self._installers[integration_type]

    def generate_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> InstallationPlan:
        """Evaluate compatibility and generate a dry-run plan without mutating host state."""
        # 1. Agent presence check
        if not agent.installed:
            raise IncompatibleAgentError(f"Agent '{agent.name}' is not installed on this system.")

        # 2. Scope compatibility check
        if scope not in manifest.supported_scopes:
            raise UnsupportedScopeError(
                f"Add-on '{manifest.name}' does not support scope '{scope.value}'."
            )

        if scope not in agent.supported_scopes:
            raise UnsupportedScopeError(
                f"Agent '{agent.name}' does not support scope '{scope.value}'."
            )

        active_reg = registry or self._registry

        # 3. Comprehensive compatibility check
        compat: CompatibilityResult = self._compatibility_engine.evaluate(
            manifest=manifest,
            agent=agent,
            requested_scope=scope,
            registry=active_reg,
        )

        if not compat.compatible:
            reasons_str = "; ".join(compat.reasons)
            raise IncompatibleAgentError(
                f"Add-on '{manifest.name}' is incompatible with agent '{agent.name}': {reasons_str}"
            )

        # 4. Lookup installer handler
        installer = self.get_installer(manifest.integration_type)

        # 5. Validate manifest against installer
        val_errors = installer.validate(manifest, agent, scope)
        if val_errors:
            error_msg = f"Installer validation failed for '{manifest.id}': " + "; ".join(val_errors)
            raise InstallationPlanningError(error_msg)

        # 6. Generate plan
        from aiaddons.integrations.plugin import PluginInstaller

        if isinstance(installer, PluginInstaller):
            plan = installer.generate_plan(
                manifest=manifest,
                agent=agent,
                scope=scope,
                compatibility=compat,
                registry=active_reg,
                compatibility_engine=self._compatibility_engine,
            )
        else:
            plan = installer.generate_plan(
                manifest=manifest,
                agent=agent,
                scope=scope,
                compatibility=compat,
            )

        # 7. Validate plan security & path traversal safety
        plan.validate_safety()

        return plan

    def generate_plans(
        self,
        manifest: IntegrationManifest,
        agents: list[AgentDetectionResult],
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> dict[str, InstallationPlan]:
        """Generate installation plans for multiple detected agents, skipping incompatible ones."""
        plans: dict[str, InstallationPlan] = {}
        for agent in agents:
            try:
                plan = self.generate_plan(manifest, agent, scope, registry=registry)
                plans[agent.agent_id] = plan
            except (IncompatibleAgentError, UnsupportedScopeError, UnsupportedIntegrationTypeError):
                continue
        return plans

    def create_transaction(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> InstallationTransaction:
        """Create and initialize a dry-run installation transaction."""
        tx_id = f"tx_{uuid.uuid4().hex[:12]}"
        active_reg = registry or self._registry
        compat = self._compatibility_engine.evaluate(manifest, agent, scope, registry=active_reg)

        tx: InstallationTransaction
        try:
            plan = self.generate_plan(manifest, agent, scope, registry=registry)
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.PLANNED,
                manifest=manifest,
                agent=agent,
                requested_scope=scope,
                compatibility_result=compat,
                plan=plan,
                is_dry_run=True,
            )
        except Exception as exc:
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.FAILED,
                manifest=manifest,
                agent=agent,
                requested_scope=scope,
                compatibility_result=compat,
                plan=None,
                is_dry_run=True,
                error_message=str(exc),
            )

        if self._wal_manager:
            self._wal_manager.write_transaction(tx)
        return tx

    def generate_batch_plan(
        self,
        manifests: list[IntegrationManifest],
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> BatchInstallationPlan:
        """Generate a combined installation plan for a batch of add-ons."""
        if not manifests:
            raise InstallationPlanningError("Cannot generate batch installation plan with zero manifests.")

        plans: list[InstallationPlan] = []
        combined_warnings: list[str] = []

        for manifest in manifests:
            plan = self.generate_plan(manifest, agent, scope, registry=registry)
            plans.append(plan)
            combined_warnings.extend(plan.warnings)

        batch_plan = BatchInstallationPlan(
            plans=plans,
            target_agent=agent.agent_id,
            target_agent_name=agent.name,
            target_scope=scope,
            warnings=combined_warnings,
        )
        batch_plan.validate_safety()
        return batch_plan

    def create_batch_transaction(
        self,
        manifests: list[IntegrationManifest],
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
    ) -> InstallationTransaction:
        """Create and initialize a dry-run batch installation transaction."""
        tx_id = f"tx_batch_{uuid.uuid4().hex[:12]}"
        active_reg = registry or self._registry
        compat_results = self._compatibility_engine.evaluate_batch(manifests, agent, scope, registry=active_reg)
        is_all_compat = all(cr.compatible for cr in compat_results)
        first_compat = compat_results[0] if compat_results else None

        tx: InstallationTransaction
        try:
            if not is_all_compat:
                incompat_reasons = []
                for cr in compat_results:
                    if not cr.compatible:
                        incompat_reasons.extend(cr.reasons)
                raise IncompatibleAgentError("; ".join(incompat_reasons))

            batch_plan = self.generate_batch_plan(manifests, agent, scope, registry=registry)
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.PLANNED,
                manifest=manifests[0] if manifests else None,
                manifests=manifests,
                agent=agent,
                requested_scope=scope,
                compatibility_result=first_compat,
                plan=batch_plan.plans[0] if batch_plan.plans else None,
                batch_plan=batch_plan,
                is_dry_run=True,
            )
        except Exception as exc:
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.FAILED,
                manifest=manifests[0] if manifests else None,
                manifests=manifests,
                agent=agent,
                requested_scope=scope,
                compatibility_result=first_compat,
                plan=None,
                batch_plan=None,
                is_dry_run=True,
                error_message=str(exc),
            )

        if self._wal_manager:
            self._wal_manager.write_transaction(tx)
        return tx

    def generate_removal_plan(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
        force: bool = False,
        active_installed_plugins: list[InstalledAddonRecord] | None = None,
    ) -> InstallationPlan:
        """Generate an inverse operation plan for safely removing an installed add-on."""
        from aiaddons.core.installer.models import (
            BaseOperation,
            RemoveDirectoryOperation,
            RemoveMcpServerOperation,
            RemovePluginReferenceOperation,
            RemoveSkillOperation,
            RiskLevel,
            RollbackMetadata,
            RollbackOperation,
        )
        from aiaddons.integrations.mcp import make_relative_config_path
        from aiaddons.integrations.skill import get_skill_target_directory

        target_root = "~" if scope == Scope.GLOBAL else "."
        planned_operations: list[BaseOperation] = []
        warnings: list[str] = []
        rollback_operations: list[RollbackOperation] = []

        if manifest.integration_type == IntegrationType.MCP:
            raw_config_path = agent.get_config_path_for_scope(scope)
            if not raw_config_path:
                raw_config_path = f".{agent.agent_id}.json"
            config_path = make_relative_config_path(raw_config_path, scope)

            mcp_op = RemoveMcpServerOperation(
                description=f"Remove MCP server '{manifest.id}' from '{config_path}'",
                target_root=target_root,
                server_name=manifest.id,
                config_path=config_path,
                target_path=config_path,
                reversible=True,
            )
            planned_operations.append(mcp_op)
            rollback_operations.append(
                RollbackOperation(
                    op_type="modify_json",
                    description=f"Restore MCP server '{manifest.id}' in '{config_path}'",
                    target_root=target_root,
                    target_path=config_path,
                    params={"json_path": f"mcpServers.{manifest.id}"},
                )
            )

        elif manifest.integration_type == IntegrationType.SKILL:
            base_dir = get_skill_target_directory(agent.agent_id, scope)
            dest_dir = f"{base_dir}/{manifest.id}"

            skill_op = RemoveSkillOperation(
                description=f"Remove deployed skill '{manifest.name}' from '{dest_dir}'",
                target_root=target_root,
                skill_name=manifest.name,
                destination_dir=dest_dir,
                target_path=dest_dir,
                reversible=True,
            )
            dir_op = RemoveDirectoryOperation(
                description=f"Remove skill directory '{dest_dir}'",
                target_root=target_root,
                directory_path=dest_dir,
                target_path=dest_dir,
                reversible=True,
            )
            planned_operations.extend([skill_op, dir_op])
            rollback_operations.append(
                RollbackOperation(
                    op_type="create_directory",
                    description=f"Restore skill directory '{dest_dir}'",
                    target_root=target_root,
                    target_path=dest_dir,
                    params={"directory_path": dest_dir},
                )
            )

        elif manifest.integration_type == IntegrationType.PLUGIN:
            dest_dir = (
                f".aiaddons/plugins/{manifest.id}"
                if scope == Scope.GLOBAL
                else f".agents/plugins/{manifest.id}"
            )
            active_reg = registry or self._registry
            if manifest.handler_spec.plugin and manifest.handler_spec.plugin.components:
                # Check shared components across other active installed plugins
                other_plugin_components: set[str] = set()
                if active_installed_plugins and active_reg:
                    for installed_plugin in active_installed_plugins:
                        if installed_plugin.addon_id != manifest.id:
                            other_manifest = active_reg.get(installed_plugin.addon_id)
                            if other_manifest and other_manifest.handler_spec.plugin:
                                other_plugin_components.update(
                                    other_manifest.handler_spec.plugin.components
                                )

                for comp_id in manifest.handler_spec.plugin.components:
                    if comp_id in other_plugin_components and not force:
                        warnings.append(
                            f"Component '{comp_id}' is shared by another active plugin "
                            "and will not be removed (use --force to remove anyway)."
                        )
                        continue

                    comp_m = active_reg.get(comp_id) if active_reg else None
                    if comp_m:
                        comp_plan = self.generate_removal_plan(
                            manifest=comp_m,
                            agent=agent,
                            scope=scope,
                            registry=active_reg,
                            force=force,
                            active_installed_plugins=active_installed_plugins,
                        )
                        planned_operations.extend(comp_plan.planned_operations)
                        warnings.extend(comp_plan.warnings)

            plugin_ref_op = RemovePluginReferenceOperation(
                description=f"Remove plugin reference '{manifest.id}'",
                target_root=target_root,
                plugin_id=manifest.id,
                config_path=f"{dest_dir}/plugin.json",
                target_path=dest_dir,
                reversible=True,
            )
            dir_op = RemoveDirectoryOperation(
                description=f"Remove plugin directory '{dest_dir}'",
                target_root=target_root,
                directory_path=dest_dir,
                target_path=dest_dir,
                reversible=True,
            )
            planned_operations.extend([plugin_ref_op, dir_op])
            rollback_operations.append(
                RollbackOperation(
                    op_type="create_directory",
                    description=f"Restore plugin directory '{dest_dir}'",
                    target_root=target_root,
                    target_path=dest_dir,
                    params={"directory_path": dest_dir},
                )
            )

        rollback_info = RollbackMetadata(
            reversible=True,
            rollback_operations=rollback_operations,
            instructions=f"Rollback removal of '{manifest.id}'.",
        )

        plan = InstallationPlan(
            addon_id=manifest.id,
            addon_name=manifest.name,
            addon_version=manifest.version,
            target_agent=agent.agent_id,
            target_agent_name=agent.name,
            target_scope=scope,
            integration_type=manifest.integration_type,
            source=manifest.source,
            planned_operations=planned_operations,
            warnings=warnings,
            risk_level=RiskLevel.LOW,
            reversible=True,
            rollback_info=rollback_info,
        )
        plan.validate_safety()
        return plan

    def create_removal_transaction(
        self,
        manifest: IntegrationManifest,
        agent: AgentDetectionResult,
        scope: Scope = Scope.WORKSPACE,
        registry: Registry | None = None,
        force: bool = False,
        active_installed_plugins: list[InstalledAddonRecord] | None = None,
    ) -> InstallationTransaction:
        """Create and initialize a dry-run removal transaction."""
        tx_id = f"tx_rem_{uuid.uuid4().hex[:12]}"
        tx: InstallationTransaction
        try:
            plan = self.generate_removal_plan(
                manifest=manifest,
                agent=agent,
                scope=scope,
                registry=registry,
                force=force,
                active_installed_plugins=active_installed_plugins,
            )
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.PLANNED,
                manifest=manifest,
                agent=agent,
                requested_scope=scope,
                compatibility_result=None,
                plan=plan,
                is_dry_run=True,
            )
        except Exception as exc:
            tx = InstallationTransaction(
                transaction_id=tx_id,
                phase=TransactionPhase.FAILED,
                manifest=manifest,
                agent=agent,
                requested_scope=scope,
                compatibility_result=None,
                plan=None,
                is_dry_run=True,
                error_message=str(exc),
            )

        if self._wal_manager:
            self._wal_manager.write_transaction(tx)
        return tx
