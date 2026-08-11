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
    InstallationPlan,
    InstallationTransaction,
    TransactionPhase,
)
from aiaddons.core.models.agent import AgentDetectionResult, Scope
from aiaddons.core.models.manifest import IntegrationManifest, IntegrationType
from aiaddons.integrations.base import BaseIntegrationInstaller
from aiaddons.integrations.cli_tool import CLIToolInstaller
from aiaddons.integrations.mcp import MCPInstaller
from aiaddons.integrations.plugin import PluginInstaller
from aiaddons.integrations.skill import SkillInstaller
from aiaddons.state.transaction import TransactionWALManager

if TYPE_CHECKING:
    from aiaddons.registry.registry import Registry


class InstallationEngine:
    """Core installation engine managing dry-run planning and safety verification."""

    def __init__(
        self,
        installers: list[BaseIntegrationInstaller] | None = None,
        compatibility_engine: CompatibilityEngine | None = None,
        registry: Registry | None = None,
        wal_manager: TransactionWALManager | None = None,
    ) -> None:
        self._compatibility_engine = compatibility_engine or CompatibilityEngine()
        self._registry = registry
        self._wal_manager = wal_manager
        self._installers: dict[IntegrationType, BaseIntegrationInstaller] = {}

        if installers is None:
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
            raise IncompatibleAgentError(
                f"Agent '{agent.name}' is not installed on this system."
            )

        # 2. Scope compatibility check
        if scope not in manifest.supported_scopes:
            raise UnsupportedScopeError(
                f"Add-on '{manifest.name}' does not support scope '{scope.value}'."
            )

        if scope not in agent.supported_scopes:
            raise UnsupportedScopeError(
                f"Agent '{agent.name}' does not support scope '{scope.value}'."
            )

        # 3. Comprehensive compatibility check
        compat: CompatibilityResult = self._compatibility_engine.evaluate(
            manifest=manifest,
            agent=agent,
            requested_scope=scope,
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
        active_reg = registry or self._registry
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
        compat = self._compatibility_engine.evaluate(manifest, agent, scope)

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

